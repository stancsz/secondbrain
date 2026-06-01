# Distill & Archive

Two operations for keeping a personal-scale brain under control. Both work
on the same schema, on the same `brain.db` file format, and round-trip via
`merge-brain`.

Read this when:
- The brain is over 100 MB and you want to make a smaller working copy.
- You want to focus on a topic and quiet everything else.
- You need to recover archived drawers (the round-trip).

Day-to-day use doesn't need this file.

---

## The two operations

| | `distill` | `archive` |
|---|---|---|
| Goal | Goal-based filtering | Cold storage |
| Typical trigger | "I want to focus on X" | "Brain is full of old stuff" |
| Effect on working brain | None (unless `--activate`) | Hard-delete after copy |
| Atomic | The copy is atomic per drawer batch | Yes — copy first, delete second; copy failure leaves the working brain untouched |
| Reversible | Just delete the new file | `merge-brain --from <archive>` |
| Adds to working brain | No | No (the opposite) |

Both operations write a fully valid `brain.db` to the output path. The
output is a real, openable, queryable brain — not a JSON dump, not a
partial export.

## Distill

`distill` creates a new `brain.db` at `--output` containing only the
drawers that match the given filters. The working brain is **not
modified** unless you pass `--activate`, which renames:

- working brain → `brain.db.bak-{TIMESTAMP}` (a point-in-time backup)
- new file → `brain.db` (the new working brain)

### Filter semantics

A drawer is included if it satisfies **all** given filter categories.
Within a category, multiple values are OR'd:

| Flag | Category | Multiple values | Example |
|---|---|---|---|
| `--tag` | Tags | OR (drawer has any of these) | `--tag ai --tag ml` |
| `--collection` | Collection | OR | `--collection Work` (single) |
| `--query` | FTS query | (single) | `--query "transformer attention"` |
| `--since` / `--until` | Date range on `updated_at` | AND (range) | `--since 2026-01-01 --until 2026-06-01` |

Plus `--include-related-depth N` which expands the seed set by N hops
along the relations graph (using the existing `traverse` CTE).

### What gets copied

| Object | Behavior |
|---|---|
| `drawers` | Rows whose id is in the seed set, **with original ids, timestamps, and metadata preserved** |
| `tags` | Only tags actually used by the copied drawers; new tag ids are minted |
| `relations` | Edges where **both** endpoints are in the copied set; original ids preserved |
| `pending_links` | Pending links from any copied drawer (target may be in or out of the set) |
| FTS index | Rebuilt by the schema triggers when the new brain is opened |
| `drawers_fts` | Triggers on the new brain re-derive everything from the copied drawers |
| Soft-deleted rows | **Excluded** from the seed set by the `deleted_at IS NULL` filter |

### What does NOT get copied

- Soft-deleted drawers
- Manual / wikilink relations where one side wasn't matched
- Tags that no copied drawer uses
- The FTS index (it's rebuilt from scratch)

### `--activate` semantics

The swap is:

1. Write the new brain to a temp path inside the same directory as the
   working brain.
2. Run `PRAGMA wal_checkpoint(TRUNCATE)` on the working connection to
   flush the WAL.
3. Close the working connection.
4. Rename the working `brain.db` → `brain.db.bak-{YYYYMMDDTHHMMSS}`.
5. Rename the new file → `brain.db`.

If step 4 or 5 fails, the new file is left in place; the user can move
it manually. The old brain is never overwritten without first being
renamed to a `.bak-*` file.

## Archive

`archive` is the destructive counterpart: it copies cold/filtered
drawers to a new archive brain, then **hard-deletes** them from the
working brain, and runs `VACUUM` to reclaim disk space.

### Default criterion

`updated_at < (now - --older-than-days)`. Default 180 days. A drawer
that was edited yesterday is never archived by the default criterion.

### Explicit filter override

If `--tag`, `--collection`, or `--before` is given, the default
age-based filter is **replaced** with the explicit filter — you can
archive a project regardless of how recently it was touched. Useful
for "I'm done with project X, archive all of it".

### Atomicity

The whole archive is two phases:

1. **Copy** — `_copy_subset_to` writes the archive brain to disk.
2. **Delete + VACUUM** — only if the copy succeeds.

If the copy fails (disk full, permission error, output file already
exists), the working brain is untouched. The output path must not
exist before the call; pass a new path or remove the existing file.

### Refusal conditions

`archive` refuses to run if:

- The target set equals every alive drawer (would leave the brain
  empty). Narrow your filter or check `--older-than-days`.
- The output path already exists. Pick a new path or remove the file.
- The output path's parent doesn't exist (we `mkdir -p` it; usually
  fine).

### Round-trip

```bash
# archive
brain archive --output ~/.secondbrain/archive-2026.db

# ...later, want one drawer back? Open the archive as a brain and find it:
sqlite3 ~/.secondbrain/archive-2026.db "SELECT id, title FROM drawers WHERE title LIKE '%foo%'"

# then bring everything back (idempotent — only the missing drawers land)
brain merge-brain --from ~/.secondbrain/archive-2026.db
```

## merge-brain

`merge-brain --from <path>` brings drawers from another brain.db into
the working brain. It is:

- **Idempotent** — drawers whose `id` already exists are skipped.
  Same for tags-by-name, relations (UNIQUE on
  `from_id+to_id+source`), drawer_tags, and pending_links.
- **Re-derives wikilinks** for the newly inserted drawers, so
  `[[X]]` references inside the new content point at the right ids in
  the merged graph.
- **Resolves pending links** for the new titles, so any pending
  `[[X]]` from before the merge now points at the imported drawer.

If you ran `archive` and a drawer in the archive referenced a drawer
that's still in the working brain via wikilink, `merge-brain` will
restore that edge (the relation has the original id, which is now
present again).

## Why this is "like agent context compression"

Agent context compression keeps the most relevant tokens and drops
the rest. `secondbrain` does the same at the drawer granularity:

- **Distill** = "keep tokens relevant to topic X"
- **Archive** = "drop tokens I haven't looked at in 6 months"
- **merge-brain** = "if a relevant token comes back into scope, restore it"

The granularity is coarser (drawer, not token), but the operating
principle is identical. The user's "new 10 MB brain again" workflow
is literally context compression: shrink the working set, keep the
old as a recoverable cold store, restore on demand.

## Performance

| Op | 50K drawers | Notes |
|---|---|---|
| `summary` | < 50 ms | Three COUNT queries, one file stat |
| `distill` (small filter, 200 matches) | < 200 ms | FTS or index lookup, copy + writes |
| `distill` (broad, 40K matches) | 1–3 s | Most of the time is writes; consider narrowing with `--include-related-depth` instead |
| `archive` (10K cold) | 2–5 s | Copy + delete + VACUUM; VACUUM is the slow part |
| `merge-brain` (10K incoming) | 3–8 s | Idempotent skip is fast (UNIQUE on insert); re-deriving wikilinks scales with the new set |

VACUUM rewrites the database file, so the wall time depends on the
working brain's size, not just the number of archived rows. A 200 MB
brain that loses 50% of its rows in an archive will still take a
couple of seconds to VACUUM.

## Future

- **Phase 2**: `distill` with semantic similarity (`--similar-to <id>`)
  using `sqlite-vec` instead of FTS.
- **Auto-suggest**: when the agent's context window is getting full,
  the skill could *propose* a distill focused on the current task
  before the user asks.
- **Scheduled archive**: cron-style `archive` with a config file
  (e.g. archive collections tagged "done" older than 90 days).
