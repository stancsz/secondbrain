#!/usr/bin/env python3
"""SecondBrain v2.1 — local knowledge graph for AI agents.

One file, stdlib only (sqlite3 + json + uuid + re). No external deps for Phase 1.
Every /brain-* command maps to a method here. The CLI wrapper is brain_cli.py.

Design decisions worth knowing:
- Soft-deleted drawers are excluded from EVERY read path. There is one helper,
  _alive(), and all queries go through views/filters that use it.
- Wikilinks resolve at WRITE time and the resolved target id is frozen into the
  relation row. Editing some *other* drawer later never silently re-points an
  existing link. (This fixes the v2 "most recently updated wins, forever" drift.)
- Unresolved [[links]] go to the pending_links table. When a drawer is created or
  retitled, we resolve any pending links pointing at its title in one indexed query.
"""

import json
import re
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path.home() / ".secondbrain" / "brain.db"
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
VALID_REL_TYPES = {"references", "contradicts", "expands", "related"}


def _uuid() -> str:
    return uuid.uuid4().hex


class SecondBrain:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.db_path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def _ensure_schema(self):
        schema = (Path(__file__).parent / "schema.sql").read_text()
        self.con.executescript(schema)
        self.con.commit()

    # -- internal helpers ---------------------------------------------------

    def _row_to_drawer(self, row) -> dict:
        d = dict(row)
        d["sources"] = json.loads(d.get("sources") or "[]")
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["tags"] = self._tags_for(d["id"])
        return d

    def _tags_for(self, drawer_id: str) -> list:
        rows = self.con.execute(
            "SELECT t.name FROM tags t JOIN drawer_tags dt ON dt.tag_id = t.id "
            "WHERE dt.drawer_id = ? ORDER BY t.name",
            (drawer_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _resolve_title(self, title: str) -> str | None:
        """Resolve a [[title]] to a drawer id. Exact (case-insensitive) match,
        most-recently-updated on ambiguity. Returns None if no live match."""
        row = self.con.execute(
            "SELECT id FROM drawers WHERE title = ? COLLATE NOCASE "
            "AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1",
            (title.strip(),),
        ).fetchone()
        return row["id"] if row else None

    def _upsert_tag(self, name: str) -> str:
        name = name.strip()
        row = self.con.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        tid = _uuid()
        self.con.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tid, name))
        return tid

    def _set_tags(self, drawer_id: str, tags: list):
        self.con.execute("DELETE FROM drawer_tags WHERE drawer_id = ?", (drawer_id,))
        for name in tags or []:
            if not name.strip():
                continue
            tid = self._upsert_tag(name)
            self.con.execute(
                "INSERT OR IGNORE INTO drawer_tags (drawer_id, tag_id) VALUES (?, ?)",
                (drawer_id, tid),
            )

    def _sync_wikilinks(self, drawer_id: str, content: str):
        """Re-derive wikilink relations for one drawer from its content.
        Deletes only this drawer's source='wikilink' edges, never manual ones.
        Unresolved targets land in pending_links."""
        self.con.execute(
            "DELETE FROM relations WHERE from_id = ? AND source = 'wikilink'",
            (drawer_id,),
        )
        self.con.execute("DELETE FROM pending_links WHERE from_id = ?", (drawer_id,))
        seen = set()
        for raw in WIKILINK_RE.findall(content or ""):
            title = raw.strip()
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            target = self._resolve_title(title)
            if target and target != drawer_id:
                self.con.execute(
                    "INSERT OR IGNORE INTO relations "
                    "(id, from_id, to_id, relation_type, strength, source) "
                    "VALUES (?, ?, ?, 'references', 0.5, 'wikilink')",
                    (_uuid(), drawer_id, target),
                )
            elif not target:
                self.con.execute(
                    "INSERT OR IGNORE INTO pending_links (id, from_id, target_title) "
                    "VALUES (?, ?, ?)",
                    (_uuid(), drawer_id, title),
                )

    def _resolve_pending_to(self, drawer_id: str, title: str):
        """A drawer named `title` now exists (id=drawer_id). Convert any pending
        links pointing at this title into real wikilink relations."""
        rows = self.con.execute(
            "SELECT id, from_id FROM pending_links WHERE target_title = ? COLLATE NOCASE",
            (title.strip(),),
        ).fetchall()
        for r in rows:
            if r["from_id"] == drawer_id:
                continue
            self.con.execute(
                "INSERT OR IGNORE INTO relations "
                "(id, from_id, to_id, relation_type, strength, source) "
                "VALUES (?, ?, ?, 'references', 0.5, 'wikilink')",
                (_uuid(), r["from_id"], drawer_id),
            )
            self.con.execute("DELETE FROM pending_links WHERE id = ?", (r["id"],))

    # -- CRUD ----------------------------------------------------------------

    def add(self, title, content, collection=None, tags=None, sources=None):
        did = _uuid()
        self.con.execute(
            "INSERT INTO drawers (id, title, content, collection, sources) "
            "VALUES (?, ?, ?, ?, ?)",
            (did, title, content, collection, json.dumps(sources or [])),
        )
        self._set_tags(did, tags or [])
        self._sync_wikilinks(did, content)
        self._resolve_pending_to(did, title)
        self.con.commit()
        return self.get(did)

    def get(self, drawer_id):
        row = self.con.execute(
            "SELECT * FROM drawers WHERE id = ? AND deleted_at IS NULL", (drawer_id,)
        ).fetchone()
        return self._row_to_drawer(row) if row else None

    def get_by_title(self, needle):
        """Substring match on title, live drawers only, most recent first."""
        rows = self.con.execute(
            "SELECT * FROM drawers WHERE title LIKE ? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT 10",
            (f"%{needle}%",),
        ).fetchall()
        return [self._row_to_drawer(r) for r in rows]

    def update(self, drawer_id, title=None, content=None, tags=None,
               collection=None, sources=None):
        cur = self.get(drawer_id)
        if not cur:
            return None
        new_title = title if title is not None else cur["title"]
        new_content = content if content is not None else cur["content"]
        new_collection = collection if collection is not None else cur["collection"]
        new_sources = sources if sources is not None else cur["sources"]
        self.con.execute(
            "UPDATE drawers SET title=?, content=?, collection=?, sources=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_title, new_content, new_collection, json.dumps(new_sources), drawer_id),
        )
        if tags is not None:
            self._set_tags(drawer_id, tags)
        if content is not None:
            self._sync_wikilinks(drawer_id, new_content)
        if title is not None and title != cur["title"]:
            self._resolve_pending_to(drawer_id, new_title)
        self.con.commit()
        return self.get(drawer_id)

    def delete(self, drawer_id, hard=False):
        if hard:
            # FK ON DELETE CASCADE cleans relations/tags/pending; AD trigger fixes FTS.
            n = self.con.execute("DELETE FROM drawers WHERE id=?", (drawer_id,)).rowcount
        else:
            n = self.con.execute(
                "UPDATE drawers SET deleted_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND deleted_at IS NULL",
                (drawer_id,),
            ).rowcount
        self.con.commit()
        return n > 0

    def restore(self, drawer_id):
        n = self.con.execute(
            "UPDATE drawers SET deleted_at=NULL WHERE id=? AND deleted_at IS NOT NULL",
            (drawer_id,),
        ).rowcount
        if n:
            d = self.con.execute(
                "SELECT title, content FROM drawers WHERE id=?", (drawer_id,)
            ).fetchone()
            self._sync_wikilinks(drawer_id, d["content"])
            self._resolve_pending_to(drawer_id, d["title"])
            self.con.commit()
        return n > 0

    # -- search & list -------------------------------------------------------

    def search(self, query, collection=None, tag=None, limit=10):
        # Join FTS rowid back to drawers.rowid, then filter soft-deleted.
        sql = [
            "SELECT d.* FROM drawers_fts f",
            "JOIN drawers d ON d.rowid = f.rowid",
            "WHERE drawers_fts MATCH ? AND d.deleted_at IS NULL",
        ]
        params = [query]
        if collection is not None:
            sql.append("AND d.collection = ?")
            params.append(collection)
        if tag is not None:
            sql.append(
                "AND d.id IN (SELECT dt.drawer_id FROM drawer_tags dt "
                "JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)"
            )
            params.append(tag)
        sql.append("ORDER BY rank LIMIT ?")
        params.append(limit)
        rows = self.con.execute(" ".join(sql), params).fetchall()
        return [self._row_to_drawer(r) for r in rows]

    def list(self, collection=None, tag=None, limit=20, offset=0, sort="updated"):
        order = {"updated": "updated_at DESC", "created": "created_at DESC",
                 "title": "title COLLATE NOCASE ASC"}.get(sort, "updated_at DESC")
        sql = ["SELECT d.* FROM drawers d WHERE d.deleted_at IS NULL"]
        params = []
        if collection is not None:
            sql.append("AND d.collection = ?")
            params.append(collection)
        if tag is not None:
            sql.append(
                "AND d.id IN (SELECT dt.drawer_id FROM drawer_tags dt "
                "JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)"
            )
            params.append(tag)
        sql.append(f"ORDER BY {order} LIMIT ? OFFSET ?")
        params += [limit, offset]
        rows = self.con.execute(" ".join(sql), params).fetchall()
        return [self._row_to_drawer(r) for r in rows]

    def collections(self):
        rows = self.con.execute(
            "SELECT COALESCE(collection, '(none)') AS name, COUNT(*) AS n "
            "FROM drawers WHERE deleted_at IS NULL GROUP BY collection "
            "ORDER BY (collection IS NULL), n DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def tags(self, sort="usage", limit=None):
        order = "n DESC, t.name" if sort == "usage" else "t.name COLLATE NOCASE"
        sql = (
            "SELECT t.name, t.color, COUNT(dt.drawer_id) AS n FROM tags t "
            "LEFT JOIN drawer_tags dt ON dt.tag_id = t.id "
            "LEFT JOIN drawers d ON d.id = dt.drawer_id AND d.deleted_at IS NULL "
            f"GROUP BY t.id ORDER BY {order}"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.con.execute(sql).fetchall()]

    # -- graph ---------------------------------------------------------------

    def relate(self, from_id, to_id, relation_type="related", strength=0.5):
        if relation_type not in VALID_REL_TYPES:
            raise ValueError(f"relation_type must be one of {sorted(VALID_REL_TYPES)}")
        if not self.get(from_id) or not self.get(to_id):
            raise ValueError("both drawers must exist and be live")
        rid = _uuid()
        self.con.execute(
            "INSERT OR IGNORE INTO relations "
            "(id, from_id, to_id, relation_type, strength, source) "
            "VALUES (?, ?, ?, ?, ?, 'manual')",
            (rid, from_id, to_id, relation_type, strength),
        )
        self.con.commit()
        return rid

    def related(self, drawer_id, limit=20, source="all"):
        src_filter = "" if source == "all" else "AND r.source = :src"
        # Both directions; exclude edges touching soft-deleted drawers.
        rows = self.con.execute(
            f"""
            SELECT r.relation_type, r.strength, r.source, d.id, d.title, d.collection,
                   CASE WHEN r.from_id = :id THEN 'out' ELSE 'in' END AS dir
            FROM relations r
            JOIN drawers d ON d.id = CASE WHEN r.from_id = :id THEN r.to_id ELSE r.from_id END
            WHERE (r.from_id = :id OR r.to_id = :id)
              AND d.deleted_at IS NULL {src_filter}
            ORDER BY r.strength DESC LIMIT :lim
            """,
            {"id": drawer_id, "src": source, "lim": limit},
        ).fetchall()
        return [dict(r) for r in rows]

    def traverse(self, drawer_id, depth=2, limit=20):
        rows = self.con.execute(
            """
            WITH RECURSIVE walk(id, hop) AS (
                SELECT :id, 0
                UNION
                SELECT CASE WHEN r.from_id = w.id THEN r.to_id ELSE r.from_id END, w.hop + 1
                FROM relations r JOIN walk w
                  ON (r.from_id = w.id OR r.to_id = w.id)
                WHERE w.hop < :depth
            )
            SELECT DISTINCT d.id, d.title, d.collection, MIN(w.hop) AS hop
            FROM walk w JOIN drawers d ON d.id = w.id
            WHERE d.deleted_at IS NULL AND w.id != :id
            GROUP BY d.id ORDER BY hop, d.title LIMIT :lim
            """,
            {"id": drawer_id, "depth": depth, "lim": limit},
        ).fetchall()
        return [dict(r) for r in rows]

    # -- data ----------------------------------------------------------------

    def stats(self, collection=None):
        where = "WHERE deleted_at IS NULL"
        params = []
        if collection is not None:
            where += " AND collection = ?"
            params.append(collection)
        total = self.con.execute(
            f"SELECT COUNT(*) c FROM drawers {where}", params
        ).fetchone()["c"]
        uncolld = self.con.execute(
            "SELECT COUNT(*) c FROM drawers WHERE deleted_at IS NULL AND collection IS NULL"
        ).fetchone()["c"]
        softdel = self.con.execute(
            "SELECT COUNT(*) c FROM drawers WHERE deleted_at IS NOT NULL"
        ).fetchone()["c"]
        rels = self.con.execute(
            "SELECT source, COUNT(*) c FROM relations GROUP BY source"
        ).fetchall()
        pending = self.con.execute("SELECT COUNT(*) c FROM pending_links").fetchone()["c"]
        return {
            "drawers": total,
            "uncollected": uncolld,
            "soft_deleted": softdel,
            "relations": {r["source"]: r["c"] for r in rels},
            "pending_links": pending,
            "tags": self.tags(sort="usage", limit=5),
            "collections": self.collections(),
        }

    # -- markdown helpers ----------------------------------------------------

    @staticmethod
    def _yaml_scalar(v: str) -> str:
        """Serialize a scalar to YAML; JSON-quotes strings with special chars."""
        if not v:
            return '""'
        if re.match(r'^[A-Za-z0-9 _\-\.]+$', v) and not v[0].isspace() and not v[-1].isspace():
            return v
        return json.dumps(v, ensure_ascii=False)

    @staticmethod
    def _yaml_frontmatter(fields: dict) -> str:
        """Render a dict as a YAML frontmatter block (--- ... ---)."""
        lines = ["---"]
        for k, v in fields.items():
            if v is None:
                continue
            if isinstance(v, list):
                if v:
                    lines.append(f"{k}:")
                    for item in v:
                        lines.append(f"  - {SecondBrain._yaml_scalar(str(item))}")
                else:
                    lines.append(f"{k}: []")
            else:
                lines.append(f"{k}: {SecondBrain._yaml_scalar(str(v))}")
        lines.append("---")
        return "\n".join(lines)

    @staticmethod
    def _safe_filename(title: str) -> str:
        """Convert a note title to a filesystem-safe filename stem (no extension)."""
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
        safe = safe.strip('. ')
        return safe[:200] or "untitled"

    def _drawer_to_md(self, d: dict) -> str:
        """Render one drawer as a Markdown document with YAML frontmatter."""
        fm = self._yaml_frontmatter({
            "id": d["id"],
            "title": d["title"],
            "collection": d.get("collection"),
            "tags": d.get("tags", []),
            "sources": d.get("sources", []),
            "created_at": d.get("created_at", ""),
            "updated_at": d.get("updated_at", ""),
        })
        return f"{fm}\n\n# {d['title']}\n\n{d['content']}\n"

    # -- export / import -----------------------------------------------------

    def export(self, collection=None, fmt="json"):
        drawers = self.list(collection=collection, limit=10**9)
        if fmt == "json":
            return json.dumps(drawers, indent=2, ensure_ascii=False)
        if fmt == "markdown":
            return "\n".join(self._drawer_to_md(d) for d in drawers)
        if fmt == "csv":
            import csv, io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "title", "collection", "tags", "content"])
            for d in drawers:
                w.writerow([d["id"], d["title"], d["collection"] or "",
                            ";".join(d["tags"]), d["content"]])
            return buf.getvalue()
        raise ValueError("format must be json|markdown|csv")

    def export_vault(self, output_dir, collection=None) -> dict:
        """Write one Markdown file per drawer into output_dir (Obsidian-compatible vault).
        Filenames are derived from titles; duplicates get a short-id suffix.
        Returns {"drawers": N, "path": str(output_dir)}."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        drawers = self.list(collection=collection, limit=10**9)
        seen: dict = {}
        written = 0
        for d in drawers:
            stem = self._safe_filename(d["title"])
            if stem in seen:
                stem = f"{stem}_{d['id'][:8]}"
            seen[stem] = True
            (output_dir / f"{stem}.md").write_text(
                self._drawer_to_md(d), encoding="utf-8"
            )
            written += 1
        return {"drawers": written, "path": str(output_dir)}

    @staticmethod
    def _parse_md_note(text: str) -> "dict | None":
        """Parse a single Markdown note (YAML frontmatter + # heading + body).
        Returns a raw drawer dict or None if the text is not a valid note."""
        text = text.strip()
        if not text or not text.startswith("---"):
            return None
        lines = text.split("\n")
        fm_lines, i = [], 1
        while i < len(lines) and lines[i].rstrip() != "---":
            fm_lines.append(lines[i])
            i += 1
        if i >= len(lines):
            return None  # no closing ---
        rest_lines = lines[i + 1:]

        # Minimal YAML parser for the known frontmatter format.
        fm: dict = {}
        cur_list: "str | None" = None
        for line in fm_lines:
            if not line.strip():
                continue
            if line.startswith("  - ") and cur_list is not None:
                raw = line[4:].strip()
                val = json.loads(raw) if raw.startswith('"') else raw
                fm[cur_list].append(val)
            elif ": " in line or line.rstrip().endswith(":"):
                cur_list = None
                stripped = line.rstrip()
                if stripped.endswith(": []"):
                    key = stripped[:-4].strip()
                    fm[key] = []
                elif stripped.endswith(":"):
                    key = stripped[:-1].strip()
                    fm[key] = []
                    cur_list = key
                else:
                    key, _, raw_val = line.partition(": ")
                    key = key.strip()
                    raw_val = raw_val.strip()
                    val = json.loads(raw_val) if raw_val.startswith('"') else raw_val
                    fm[key] = val

        # Extract title from # heading; body is everything after it.
        title = fm.get("title") or ""
        body_start = 0
        for j, bl in enumerate(rest_lines):
            if not bl.strip():
                continue
            if bl.startswith("# "):
                if not title:
                    title = bl[2:].strip()
                body_start = j + 1
            break

        content = "\n".join(rest_lines[body_start:]).strip()
        if not title:
            return None

        return {
            "id": fm.get("id") or _uuid(),
            "title": title,
            "content": content,
            "collection": fm.get("collection") or None,
            "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
            "sources": fm.get("sources", []) if isinstance(fm.get("sources"), list) else [],
            "metadata": {},
        }

    def _import_drawers(self, data: list, mode: str) -> dict:
        """Core import loop: insert a list of raw drawer dicts."""
        added = skipped = 0
        for d in data:
            exists = self.con.execute(
                "SELECT 1 FROM drawers WHERE id=?", (d["id"],)
            ).fetchone()
            if exists and mode == "merge":
                skipped += 1
                continue
            if exists and mode == "replace":
                self.con.execute("DELETE FROM drawers WHERE id=?", (d["id"],))
            self.con.execute(
                "INSERT INTO drawers (id, title, content, collection, sources, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (d["id"], d["title"], d["content"], d.get("collection"),
                 json.dumps(d.get("sources", [])), json.dumps(d.get("metadata", {}))),
            )
            self._set_tags(d["id"], d.get("tags", []))
            added += 1
        # Re-derive all wikilinks after the full set exists (resolves cross-refs).
        for d in self.list(limit=10**9):
            self._sync_wikilinks(d["id"], d["content"])
            self._resolve_pending_to(d["id"], d["title"])
        self.con.commit()
        return {"added": added, "skipped": skipped}

    def _import_vault(self, vault_dir: Path, mode: str) -> dict:
        """Import all .md files from a vault directory."""
        drawers = []
        for f in sorted(vault_dir.glob("*.md")):
            d = self._parse_md_note(f.read_text(encoding="utf-8"))
            if d:
                drawers.append(d)
        return self._import_drawers(drawers, mode)

    def import_(self, path, mode="merge"):
        path = Path(path)
        if path.is_dir():
            return self._import_vault(path, mode)
        if path.suffix.lower() in (".md", ".markdown"):
            d = self._parse_md_note(path.read_text(encoding="utf-8"))
            return self._import_drawers([d] if d else [], mode)
        data = json.loads(path.read_text())
        return self._import_drawers(data, mode)

    def close(self):
        if getattr(self, "_closed", False):
            return
        self.con.close()
        self._closed = True

    # -- lifecycle / size ---------------------------------------------------

    def checkpoint(self):
        """Flush the WAL to the main DB file. Idempotent. Safe to call repeatedly.
        Use this before renaming the db file on disk so the WAL/SHM don't follow."""
        try:
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass

    def checkpoint_and_close(self):
        self.checkpoint()
        self.close()

    # -- summary / distill / archive ----------------------------------------

    def _humanize_bytes(self, n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def _count_alive(self) -> int:
        return self.con.execute(
            "SELECT COUNT(*) c FROM drawers WHERE deleted_at IS NULL"
        ).fetchone()["c"]

    def _build_filter(self, tags=None, collection=None, query=None,
                      since=None, until=None, alias="d"):
        """Build a WHERE clause + params that selects drawers matching all
        the given filters. Within a category (multiple tags) it's IN/OR.
        Across categories it's AND."""
        a = alias
        clauses = [f"{a}.deleted_at IS NULL"]
        params = []
        if tags:
            placeholders = ",".join("?" * len(tags))
            clauses.append(
                f"{a}.id IN (SELECT dt.drawer_id FROM drawer_tags dt "
                f"JOIN tags t ON t.id = dt.tag_id WHERE t.name IN ({placeholders}))"
            )
            params.extend(tags)
        if collection:
            clauses.append(f"{a}.collection = ?")
            params.append(collection)
        if query:
            clauses.append(
                f"{a}.rowid IN (SELECT rowid FROM drawers_fts WHERE drawers_fts MATCH ?)"
            )
            params.append(query)
        if since:
            clauses.append(f"{a}.updated_at >= ?")
            params.append(since)
        if until:
            clauses.append(f"{a}.updated_at <= ?")
            params.append(until)
        return " AND ".join(clauses), params

    def _copy_subset_to(self, output_path, drawer_ids) -> dict:
        """Copy a set of drawer_ids (and their tags/relations/pending_links)
        into a fresh brain.db at output_path. Returns counts."""
        output_path = Path(output_path)
        if output_path.exists():
            raise FileExistsError(
                f"{output_path} already exists; remove it or pick another path"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Open fresh brain — _ensure_schema creates all tables.
        out = SecondBrain(output_path)

        if not drawer_ids:
            out.close()
            return {"drawers": 0, "tags": 0, "relations": 0, "pending_links": 0,
                    "path": str(output_path)}

        placeholders = ",".join("?" * len(drawer_ids))
        ids = list(drawer_ids)

        # Drawers (preserve original ids, timestamps, metadata)
        drawer_rows = self.con.execute(
            f"SELECT * FROM drawers WHERE id IN ({placeholders})", ids
        ).fetchall()
        for d in drawer_rows:
            out.con.execute(
                "INSERT INTO drawers (id, title, content, collection, sources, "
                "created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (d["id"], d["title"], d["content"], d["collection"], d["sources"],
                 d["created_at"], d["updated_at"], d["metadata"]),
            )

        # Tags: only those actually used by the subset; mint new tag ids.
        tag_rows = self.con.execute(
            f"SELECT dt.drawer_id, t.name FROM drawer_tags dt "
            f"JOIN tags t ON t.id = dt.tag_id "
            f"WHERE dt.drawer_id IN ({placeholders})", ids
        ).fetchall()
        tag_name_to_id = {}
        for tn in sorted({r["name"] for r in tag_rows}):
            tid = _uuid()
            out.con.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tid, tn))
            tag_name_to_id[tn] = tid
        for r in tag_rows:
            out.con.execute(
                "INSERT INTO drawer_tags (drawer_id, tag_id) VALUES (?, ?)",
                (r["drawer_id"], tag_name_to_id[r["name"]]),
            )

        # Relations: edges where BOTH endpoints are in the subset.
        rel_rows = self.con.execute(
            f"SELECT * FROM relations WHERE from_id IN ({placeholders}) "
            f"AND to_id IN ({placeholders})", ids + ids
        ).fetchall()
        for r in rel_rows:
            out.con.execute(
                "INSERT INTO relations (id, from_id, to_id, relation_type, "
                "strength, source) VALUES (?, ?, ?, ?, ?, ?)",
                (r["id"], r["from_id"], r["to_id"], r["relation_type"],
                 r["strength"], r["source"]),
            )

        # Pending links: from a drawer in the subset (target may or may not be).
        pend_rows = self.con.execute(
            f"SELECT * FROM pending_links WHERE from_id IN ({placeholders})", ids
        ).fetchall()
        for r in pend_rows:
            out.con.execute(
                "INSERT INTO pending_links (id, from_id, target_title) "
                "VALUES (?, ?, ?)",
                (r["id"], r["from_id"], r["target_title"]),
            )

        out.con.commit()
        out.close()
        return {
            "drawers": len(drawer_rows),
            "tags": len(tag_name_to_id),
            "relations": len(rel_rows),
            "pending_links": len(pend_rows),
            "path": str(output_path),
        }

    def summary(self, cold_threshold_days: int = 180) -> dict:
        """Brain health snapshot: size, drawer counts (alive / cold / soft-del),
        relation counts, pending links, and a one-line recommendation.

        Cold = alive drawers whose updated_at is older than cold_threshold_days."""
        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        alive = self._count_alive()
        cold = self.con.execute(
            "SELECT COUNT(*) c FROM drawers WHERE deleted_at IS NULL "
            "AND updated_at < datetime('now', ?)",
            (f"-{int(cold_threshold_days)} days",),
        ).fetchone()["c"]
        soft = self.con.execute(
            "SELECT COUNT(*) c FROM drawers WHERE deleted_at IS NOT NULL"
        ).fetchone()["c"]
        rels = {r["source"]: r["c"] for r in self.con.execute(
            "SELECT source, COUNT(*) c FROM relations GROUP BY source"
        ).fetchall()}
        pending = self.con.execute(
            "SELECT COUNT(*) c FROM pending_links"
        ).fetchone()["c"]

        size_mb = size_bytes / 1024 / 1024
        rec = None
        if alive and (cold / alive) > 0.5:
            rec = "archive"
        if size_mb > 100:
            rec = rec or "archive"
        if alive and (cold / alive) > 0.7 and alive > 5000:
            rec = "archive-then-distill"

        return {
            "db_path": str(self.db_path),
            "size_bytes": size_bytes,
            "size_human": self._humanize_bytes(size_bytes),
            "drawers": {
                "alive": alive,
                "cold": cold,
                "soft_deleted": soft,
                "cold_threshold_days": cold_threshold_days,
            },
            "relations": rels,
            "pending_links": pending,
            "recommendation": rec,
        }

    def distill(self, output_path, tags=None, collection=None, query=None,
                since=None, until=None, include_related_depth: int = 0,
                min_strength: float = 0.0) -> dict:
        """Create a new brain.db at output_path containing only the drawers
        that match the given filters. NON-DESTRUCTIVE: the working brain is
        not modified. The CLI's --activate flag handles swapping.

        Filters AND across categories (a drawer must satisfy all given filters)
        and OR within a category (any tag, any collection). Returns counts.
        """
        if not any([tags, collection, query, since, until]):
            raise ValueError(
                "distill needs at least one filter: --tag / --collection / "
                "--query / --since / --until"
            )
        where, params = self._build_filter(tags, collection, query, since, until)
        seed_ids = {r["id"] for r in self.con.execute(
            f"SELECT id FROM drawers d WHERE {where}", params
        ).fetchall()}

        # Optional N-hop expansion around the seeds.
        if include_related_depth > 0 and seed_ids:
            for sid in list(seed_ids):
                for row in self.traverse(sid, depth=include_related_depth,
                                         limit=10**6):
                    seed_ids.add(row["id"])

        if not seed_ids:
            return {"drawers": 0, "tags": 0, "relations": 0,
                    "pending_links": 0, "path": str(Path(output_path)),
                    "note": "no drawers matched the filter"}

        return self._copy_subset_to(output_path, seed_ids)

    def archive(self, output_path, older_than_days: int = 180,
                before_date: str = None, tags=None, collection=None,
                dry_run: bool = False) -> dict:
        """Move cold drawers to output_path (a new brain.db) and hard-delete
        them from the working brain. The whole operation is atomic — if the
        copy fails, nothing is deleted.

        Default criterion: updated_at < (now - older_than_days).
        If before_date is given, use that instead.
        If tags/collection are given, archive matching drawers regardless of age.
        """
        if tags or collection or before_date is not None:
            where, params = self._build_filter(tags, collection,
                                               since=None, until=before_date)
            target_ids = {r["id"] for r in self.con.execute(
                f"SELECT id FROM drawers d WHERE {where}", params
            ).fetchall()}
            criterion = "explicit filter"
        else:
            target_ids = {r["id"] for r in self.con.execute(
                "SELECT id FROM drawers WHERE deleted_at IS NULL "
                "AND updated_at < datetime('now', ?)",
                (f"-{int(older_than_days)} days",),
            ).fetchall()}
            criterion = f"untouched {older_than_days}+ days"

        alive_before = self._count_alive()
        if not target_ids:
            return {
                "archived": 0,
                "would_archive": 0,
                "remaining": alive_before,
                "would_remain": alive_before,
                "criterion": criterion,
                "path": str(Path(output_path)),
                "dry_run": dry_run,
            }
        if target_ids == {r["id"] for r in self.con.execute(
                "SELECT id FROM drawers WHERE deleted_at IS NULL").fetchall()}:
            raise ValueError(
                "archive would remove every alive drawer — refusing. "
                "Pass a narrower filter or check your --older-than-days."
            )

        if dry_run:
            return {
                "would_archive": len(target_ids),
                "would_remain": alive_before - len(target_ids),
                "criterion": criterion,
                "path": str(Path(output_path)),
                "dry_run": True,
            }

        # Atomic: copy first, then delete. If the copy fails the working
        # brain is untouched.
        copy_stats = self._copy_subset_to(output_path, target_ids)

        placeholders = ",".join("?" * len(target_ids))
        self.con.execute(
            f"DELETE FROM drawers WHERE id IN ({placeholders})", list(target_ids)
        )
        self.con.commit()
        self.checkpoint()  # flush WAL so renames are clean
        self.con.execute("VACUUM")  # reclaim space

        size_after = self.db_path.stat().st_size
        return {
            "archived": copy_stats["drawers"],
            "archived_relations": copy_stats["relations"],
            "remaining": self._count_alive(),
            "criterion": criterion,
            "path": str(Path(output_path)),
            "size_remaining_bytes": size_after,
            "size_remaining_human": self._humanize_bytes(size_after),
        }

    def merge_brain(self, source_path) -> dict:
        """Bring drawers from another brain.db into this one. Idempotent:
        drawers whose id already exists are skipped (relations, tags and
        pending_links are likewise skipped via UNIQUE constraints)."""
        source_path = Path(source_path)
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        # Source is read-only; never use the same DB_PATH global.
        src = SecondBrain(source_path)

        # Build id-set of already-present drawers for fast skip.
        existing = {r["id"] for r in self.con.execute("SELECT id FROM drawers").fetchall()}
        existing_tag_names = {r["name"] for r in self.con.execute("SELECT name FROM tags").fetchall()}

        # Tags: copy any missing tag names (mint new ids).
        src_tag_rows = src.con.execute("SELECT id, name FROM tags").fetchall()
        src_tag_id_to_name = {r["id"]: r["name"] for r in src_tag_rows}
        name_to_new_id = {}
        for name in {r["name"] for r in src_tag_rows}:
            if name not in existing_tag_names:
                tid = _uuid()
                self.con.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tid, name))
                name_to_new_id[name] = tid
                existing_tag_names.add(name)
        # Map source tag id -> our tag id (either pre-existing or newly minted).
        # For tags that already existed by name, find our id.
        src_tag_id_to_our_id = {}
        for src_tid, name in src_tag_id_to_name.items():
            if name in name_to_new_id:
                src_tag_id_to_our_id[src_tid] = name_to_new_id[name]
            else:
                src_tag_id_to_our_id[src_tid] = self.con.execute(
                    "SELECT id FROM tags WHERE name = ?", (name,)
                ).fetchone()["id"]

        # Drawers: skip those we already have.
        src_drawer_rows = src.con.execute("SELECT * FROM drawers").fetchall()
        added_drawers = 0
        skipped_drawers = 0
        newly_added_ids = []
        for d in src_drawer_rows:
            if d["id"] in existing:
                skipped_drawers += 1
                continue
            self.con.execute(
                "INSERT INTO drawers (id, title, content, collection, sources, "
                "created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (d["id"], d["title"], d["content"], d["collection"], d["sources"],
                 d["created_at"], d["updated_at"], d["metadata"]),
            )
            existing.add(d["id"])
            added_drawers += 1
            newly_added_ids.append(d["id"])

        # Drawer_tags: re-bind source tag ids to our tag ids.
        src_dt_rows = src.con.execute("SELECT drawer_id, tag_id FROM drawer_tags").fetchall()
        added_tags_links = 0
        for r in src_dt_rows:
            if r["drawer_id"] not in existing:
                continue
            our_tag_id = src_tag_id_to_our_id.get(r["tag_id"])
            if our_tag_id is None:
                continue
            self.con.execute(
                "INSERT OR IGNORE INTO drawer_tags (drawer_id, tag_id) "
                "VALUES (?, ?)",
                (r["drawer_id"], our_tag_id),
            )
            added_tags_links += 1

        # Relations: only those touching drawers we now have.
        src_rel_rows = src.con.execute(
            "SELECT * FROM relations"
        ).fetchall()
        added_rels = 0
        for r in src_rel_rows:
            if r["from_id"] not in existing or r["to_id"] not in existing:
                continue
            self.con.execute(
                "INSERT OR IGNORE INTO relations "
                "(id, from_id, to_id, relation_type, strength, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["id"], r["from_id"], r["to_id"], r["relation_type"],
                 r["strength"], r["source"]),
            )
            added_rels += 1

        # Pending links: only from drawers we now have.
        src_pend_rows = src.con.execute(
            "SELECT * FROM pending_links"
        ).fetchall()
        added_pend = 0
        for r in src_pend_rows:
            if r["from_id"] not in existing:
                continue
            self.con.execute(
                "INSERT OR IGNORE INTO pending_links "
                "(id, from_id, target_title) VALUES (?, ?, ?)",
                (r["id"], r["from_id"], r["target_title"]),
            )
            added_pend += 1

        # Re-derive wikilinks for newly added drawers so cross-refs into
        # the existing brain resolve correctly.
        for did in newly_added_ids:
            d = self.con.execute(
                "SELECT content FROM drawers WHERE id=?", (did,)
            ).fetchone()
            if d:
                self._sync_wikilinks(did, d["content"])
        # And resolve any pending links pointing at the new titles.
        for did in newly_added_ids:
            d = self.con.execute(
                "SELECT title FROM drawers WHERE id=?", (did,)
            ).fetchone()
            if d:
                self._resolve_pending_to(did, d["title"])

        self.con.commit()
        src.close()
        return {
            "drawers_added": added_drawers,
            "drawers_skipped": skipped_drawers,
            "tag_links_added": added_tags_links,
            "relations_added": added_rels,
            "pending_links_added": added_pend,
            "source_path": str(source_path),
        }
