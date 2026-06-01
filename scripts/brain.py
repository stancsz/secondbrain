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

    def export(self, collection=None, fmt="json"):
        drawers = self.list(collection=collection, limit=10**9)
        if fmt == "json":
            return json.dumps(drawers, indent=2, ensure_ascii=False)
        if fmt == "markdown":
            out = []
            for d in drawers:
                fm = {"id": d["id"], "collection": d["collection"],
                      "tags": d["tags"], "sources": d["sources"]}
                out.append("---\n" + json.dumps(fm, ensure_ascii=False) +
                           f"\n---\n## {d['title']}\n\n{d['content']}\n")
            return "\n".join(out)
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

    def import_(self, path, mode="merge"):
        data = json.loads(Path(path).read_text())
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

    def close(self):
        self.con.close()
