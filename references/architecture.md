# SecondBrain — Architecture Reference (v2.1)

Read this when modifying the schema or debugging the store. Day-to-day use only
needs `SKILL.md`.

## Contents
1. Design principles
2. Data model (3 tables + FTS + pending_links)
3. FTS5 correctness (the v2 bugs and their fixes)
4. Wikilink resolution rules
5. Soft delete semantics
6. Phase 2 — MCP interface contract
7. v1 → v2 migration
8. Storage, durability, performance targets
9. What changed from v2 → v2.1

---

## 1. Design principles

1. **Flat beats hierarchy.** Knowledge is a graph, not a tree. Organization is tags + links.
2. **Capture first, organize later.** A drawer with no collection beats a note not taken.
3. **Links live in content.** Relations derived from `[[wikilinks]]` can't drift from the text. Manual relations are additive.
4. **Ship working things.** Phase 1 is fully implemented and tested. Phase 2 is an interface contract, not dead stubs.

## 2. Data model

`collection` is a plain string field on `drawers`, not a foreign key — no setup step,
collection-less capture is allowed, and `SELECT DISTINCT collection` lists them for free.
Rooms from v1 are gone; they were tags by another name.

Tables: `drawers`, `tags`, `drawer_tags` (M:N), `relations` (typed edges),
`pending_links` (unresolved wikilinks), `drawers_fts` (FTS5), `_meta`.
Full DDL is `scripts/schema.sql`.

`relations` carries `source ∈ {manual, wikilink, inferred}` with
`UNIQUE (from_id, to_id, source)` — a manual and a wikilink edge can coexist between
the same pair. `relation_type ∈ {references, contradicts, expands, related}`.
`strength` (0–1) orders results in `related` and is reserved for Phase 2 ranking.

## 3. FTS5 correctness (the v2 bugs, fixed in v2.1)

`drawers_fts` is an **external-content** FTS5 table (`content=drawers`). This has three
sharp edges that v2 got wrong:

- **Updates/deletes need the `'delete'` command, not raw DELETE.** On external-content
  tables a plain `DELETE FROM drawers_fts` corrupts the index. The triggers use
  `INSERT INTO drawers_fts(drawers_fts, rowid, title, content) VALUES('delete', ...)`.
- **A missing AFTER DELETE trigger orphans the index.** v2 had none, so hard-deletes left
  searchable ghosts. v2.1 adds `drawers_ad`.
- **FTS knows nothing about `deleted_at`.** A raw `MATCH` returns soft-deleted rows. Every
  search joins FTS `rowid → drawers.rowid` and filters `WHERE deleted_at IS NULL`. This is
  done in `SecondBrain.search`; do not query `drawers_fts` directly without that join.

Because `drawers.id` is a TEXT UUID, the FTS link is the implicit integer `rowid`, not `id`.
Joins go `drawers_fts.rowid = drawers.rowid`, then read `drawers.id` for output.

## 4. Wikilink resolution

- `[[Title]]` resolves against `drawers.title` case-insensitively, exact match first,
  most-recently-updated on ties (`idx_drawers_title_nocase`).
- **Resolution is frozen at write time.** When content is saved, each `[[link]]` resolves
  to a concrete target id stored in the relation row. Editing some *other* drawer later
  never re-points an existing link. (v2's "most-recent wins, re-evaluated forever" rule
  violated the no-drift principle; v2.1 freezes the target.)
- Unresolved links go to `pending_links` (its own indexed table, not JSON metadata — so
  resolution on `add` is one indexed lookup, preserving the <50ms add target). When a
  drawer with a matching title is later created or restored, `_resolve_pending_to` promotes
  those pending rows to real `wikilink` relations.
- Disambiguate with qualifiers: `[[Transformers (NLP)]]` vs `[[Transformers (electrical)]]`.

## 5. Soft delete

`delete` sets `deleted_at`; the drawer disappears from search, list, related, traverse,
collections, and stats-counts, but `restore` brings it back and re-derives its wikilinks.
`delete --hard` removes the row; `ON DELETE CASCADE` cleans `relations`, `drawer_tags`,
and `pending_links`, and the `drawers_ad` trigger cleans FTS.

## 6. Phase 2 — MCP interface contract

Phase 2 implements exactly these signatures against the Phase 1 schema. Only addition is
the `embeddings` table.

```python
search(query, collection=None, tag=None, limit=10) -> SearchResult
search_semantic(query, limit=10) -> SearchResult          # needs embeddings
add(title, content, collection=None, tags=None, sources=None) -> Drawer
get(id) -> Drawer | None
update(id, title=None, content=None, tags=None, collection=None) -> Drawer
delete(id, hard=False) -> bool
restore(id) -> bool
list(collection=None, tag=None, limit=20, offset=0) -> list[Drawer]
collections() -> list[CollectionSummary]
tags(sort='usage') -> list[TagSummary]
related(id, limit=5, source='all') -> list[Relation]
traverse(id, depth=2, limit=20) -> Graph
relate(from_id, to_id, relation_type, strength=0.5) -> Relation
export(collection=None, format='json') -> str
stats(collection=None) -> Stats
```

```sql
CREATE TABLE IF NOT EXISTS embeddings (
    id         TEXT PRIMARY KEY,
    drawer_id  TEXT NOT NULL UNIQUE,
    vector     BLOB NOT NULL,             -- float32[], 384-dim (all-MiniLM-L6-v2)
    model      TEXT DEFAULT 'all-MiniLM-L6-v2',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (drawer_id) REFERENCES drawers(id) ON DELETE CASCADE
);
UPDATE _meta SET value='2', updated_at=CURRENT_TIMESTAMP WHERE key='schema_version';
```

Semantic search uses `sqlite-vec` ANN over `embeddings.vector`. Auto-linking inserts
`source='inferred'` relations above cosine similarity 0.82. The `ON DELETE CASCADE` on
`embeddings.drawer_id` is the one improvement over the v2 contract (v2 omitted it, orphaning
vectors on hard delete).

## 7. v1 → v2 migration

```
memories.*  → drawers.*            (all fields preserved)
wing.name   → drawer.collection    (string copy, FK dropped)
room name   → tag                  ("AI-Projects" room → "AI-Projects" tag)
memory_tags → drawer_tags
relations   → relations            (source set to 'manual')
wings/rooms tables dropped
```
After migration set `_meta.schema_version = '1'` (base schema). Phase 2 bumps it to `'2'`.
This is the consistent versioning v2 got wrong (it claimed `1` after a "v2" migration).

## 8. Storage, durability, performance

| Property | Value |
|---|---|
| Path | `~/.secondbrain/brain.db` |
| SQLite | 3.41+ (FTS5, json1, recursive CTE) |
| Journal | WAL (concurrent reads safe) |
| Backup | Litestream → S3 (optional) |
| Soft delete | `deleted_at`; `restore` to undo |

| Operation | Target |
|---|---|
| FTS search (50K drawers) | < 100 ms |
| Add (incl. wikilink parse + pending lookup) | < 50 ms |
| List by collection | < 20 ms (partial index) |
| Traverse depth 2 | < 200 ms (recursive CTE) |
| Full export (50K) | < 5 s |

## 9. What changed v2 → v2.1

- FTS triggers rewritten to the external-content `'delete'` form; added AFTER DELETE trigger.
- All read paths filter soft-deleted rows (search no longer leaks deleted drawers).
- Wikilink target resolution frozen at write time (no silent re-pointing).
- `pending_links` promoted from JSON metadata to an indexed table.
- `pending_links` / cross-references resolve on create **and** restore.
- Ambiguous `show` returns all matches instead of guessing.
- Empty-search retry guidance added for the agent.
- `embeddings.drawer_id` gains `ON DELETE CASCADE`.
- Consistent `schema_version` (base = 1, Phase 2 = 2).
- Single-add suggests links; only bulk capture auto-inserts them (the v2 contradiction resolved).
