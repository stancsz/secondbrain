---
name: secondbrain
description: A local, file-based knowledge graph ("second brain" / 长脑子 / 脑子不够用了) for capturing, searching, linking, and recalling notes across conversations. Use this skill whenever the user wants to SAVE something for later ("remember this", "save this article/snippet", "note that...", "记一下", "存一下", "脑子记不住"), RECALL their own knowledge ("what do I know about X?", "what have I written on Y?", "catch me up on project Z", "我之前写过 X 吗"), find GAPS ("what am I missing on X?", "我还缺什么"), or manage a personal notes/wiki/zettelkasten. Trigger it even when the user doesn't say "second brain" — any time they treat you as if you should retain or retrieve their personal notes, reach for this skill instead of answering from training data. The store is a single SQLite file with full-text search and a wikilink-driven relation graph.
---

# SecondBrain

A personal knowledge graph stored in one SQLite file at `~/.secondbrain/brain.db`.
Notes are **drawers**. They carry **tags** (flat), an optional **collection**
(a string, e.g. "Work"), and **relations** (typed edges). Relations are derived
automatically from `[[wikilinks]]` in content, and can also be added manually.

Everything runs through `scripts/brain_cli.py` (stdlib Python, no install step).
The first run creates the database and schema automatically.

## When you answer from this skill, you answer from the user's notes — not your training data. Cite drawer IDs.

---

## Intent → command (read this first)

Match what the user is doing to the right command. Do not ask for parameters the
user didn't give — infer sensible ones and state your assumption in one line.

| The user... | Do this |
|---|---|
| Pastes text/article/code and says "save this" / "remember this" | Synthesize a title, pull 2–5 tags, pick a collection if obvious, run `add`. Don't interrogate them first. |
| Asks "what do I know about X?" / "have I written about Y?" | `search X`, read results, synthesize an answer **citing drawer IDs**. |
| Asks a factual question with no personal framing | Answer normally. Do **not** force a search if they're not asking about their own notes. |
| "Catch me up on project Z" / "prep me on Z" | `list --collection Z --sort updated`, read top drawers, give a short brief, surface the most-linked ones. |
| "What am I missing on X?" | `search X`, list what exists, compare against their outline/goal, name the gaps. |
| "What haven't I touched in a while?" | `list --sort updated` (oldest end) or query the DB directly by `updated_at`. |
| Pastes many items (abstracts, links, snippets) | One `add` per item. Auto-title, auto-tag. Cross-link overlapping ones by inserting `[[wikilinks]]` into content (see Bulk capture). |
| "Link these two notes" / "these contradict" | `relate <from> <to> --type <references\|contradicts\|expands\|related>`. |
| "Show me note N" / "open the RAG note" | `show <id-or-title>`. |
| "What's connected to N?" | `related <id>`, or `traverse <id> --depth 2` to walk further. |
| "Delete N" | `delete <id>` (soft by default — recoverable). Only pass `--hard` if they insist on permanent. |
| "Export my notes" / "back up" | `export --format markdown` (Obsidian-compatible) or `json`. |
| "Brain is getting big / 脑子不够用了 / clean up / how big is my brain?" | Run `summary` first. If it recommends action, propose it; don't act unprompted on the data. |
| "I want to focus on topic X" / "give me a smaller brain for Y" | `distill --query X --output focused.db`. Non-destructive; old brain untouched unless they pass `--activate` to swap. |
| "Archive old stuff" / "move cold notes out of the way" | `archive --output archive-2026.db` (default: untouched 180d+). Destructive: copies to archive then hard-deletes from working. `merge-brain --from archive-2026.db` brings them back. |

---

## Commands

Run as: `python3 scripts/brain_cli.py <command> [args]`. Add `--json` to any read
command for structured output you can parse; omit it for human-readable text.

- `add "<title>" "<content>" [--collection C] [--tags a,b] [--source URL]`
- `search "<query>" [--collection C] [--tag T] [--limit N]`
- `show <id-or-title>`
- `update <id> [--title ...] [--content ...] [--tags a,b] [--collection C]`
- `delete <id> [--hard]`  /  `restore <id>`
- `list [--collection C] [--tag T] [--limit N] [--sort updated|created|title]`
- `collections`  /  `tags [--sort usage|alpha]`
- `relate <from> <to> --type <type> [--strength 0.0-1.0]`
- `related <id> [--source manual|wikilink|all]`  /  `traverse <id> [--depth N]`
- `export [--collection C] [--format json|markdown|csv] [--output PATH]`
- `import <path> [--merge|--replace]`  /  `stats [--collection C]`
- `summary [--cold-days 180]` — size, drawer counts (alive / cold / soft-deleted), pending links, recommendation
- `distill --output <path> [--tag T] [--collection C] [--query Q] [--since D] [--until D] [--include-related-depth N] [--activate]` — write a filtered working brain to a new file; old brain stays put unless `--activate`
- `archive --output <path> [--older-than-days N] [--before D] [--tag T] [--collection C] [--dry-run]` — move cold/filtered drawers to a new brain.db, hard-delete from working
- `merge-brain --from <path>` — bring another brain's drawers into the working brain (idempotent)

---

## Behavior contracts (handle these the same way every time)

**Capture without friction.** When saving, never block on a missing collection or
tag. A drawer with no collection is fully searchable and linkable. Capture now,
let the user organize later. The `(none)` collection count in `stats` is their backlog.

**Wikilinks build the graph for free.** Any `[[Another Title]]` in content becomes a
`references` relation on save. When you write a note that relates to an existing one,
weave the link into the prose: `...similar to [[RAG Overview]]...`. Resolution is
case-insensitive, exact-match-first. Targets that don't exist yet become *pending* and
auto-link the moment a matching drawer is created — so forward references are safe.

**Suggest links, don't silently inject them (single add).** After a normal `add`,
run a `search` against the new content and *offer* `[[wikilink]]` edits the user can
accept. Exception: **Bulk capture** mode — when the user pastes a batch to ingest, you
*may* insert cross-links between items in that batch directly, then tell them what you linked.

**Empty search → don't give up.** If `search` returns nothing, broaden the query
(drop a word, try a synonym) and retry once before telling the user it isn't in their
notes. Never claim something is absent after a single narrow query.

**Ambiguous `show` → list, don't guess.** If a title matches multiple drawers, the CLI
returns all matches with short IDs. Surface them and ask which, using the 8-char id.

**Soft delete is the default and is reversible.** `delete` sets a timestamp; the drawer
vanishes from every query but `restore <id>` brings it back with its links intact. Only
use `--hard` (permanent, cascades to relations) when the user explicitly wants it gone forever.

**Citing.** When you answer a knowledge question from the brain, reference the drawers you
used by their short id, e.g. "(per drawer `be452d8b`)", so the user can `show` them.

**Proactive brain health.** Don't wait for the user to complain about size — at the start
of a long session, or when the user asks "how big is my brain / 脑子够用吗", run `summary`
and surface any recommendation. If `summary` returns a recommendation, *propose* the
action in one line ("You have 32K cold drawers — want me to archive them?") — never run
`archive` or `distill --activate` unprompted, since both are destructive (or at least
rearrange the canonical file).

**Distill is non-destructive by default.** `distill` writes a new brain.db and leaves
the working brain untouched. The user can inspect the new file, then re-run with
`--activate` to make it the new working brain (which renames the old to `.bak-TIMESTAMP`).
Always show the user where the new file is, and only suggest `--activate` after they
confirm.

**Archive is destructive; merge-brain is its undo.** `archive` *hard-deletes* from the
working brain after copying the cold drawers out. There is no `--soft` flag. If the user
might want them back, suggest running with `--dry-run` first to see what would be moved,
or remind them that `merge-brain --from <archive>` is the round-trip.

---

## Examples

**Save from conversation**
> User pastes a paper abstract: "save this"
Run: `add "Attention Is All You Need" "<abstract>" --collection Research --tags ml,transformers,paper --source <url>`
Then: search "attention" to find related drawers, offer to add `[[wikilinks]]`.

**Recall**
> "What do I know about RAG?"
Run: `search "RAG"` → read the hits → "You have three notes on this. The core one (`eb8dc573`) covers retrieval-augmented generation and links to [[Vector Search]]..."

**Prep**
> "Catch me up on the Braid project."
Run: `list --collection Braid --sort updated` → read top 5 → 3-paragraph brief, lead with the most-linked drawer (`related`/`traverse` to find it).

---

## Architecture & guarantees

See `references/architecture.md` for the data model, the FTS5 correctness notes
(external-content triggers, soft-delete filtering), wikilink resolution rules, the
Phase 2 MCP interface contract, the v1→v2 migration, and performance targets. Read it
only when modifying the schema or debugging the store — day-to-day use needs only this file.

For the distill / archive / merge-brain operations, see
`references/distill-archive.md` — it covers the filter semantics, what gets
copied, atomicity guarantees, the `--activate` swap, and the connection to
agent context compression.

Key guarantees the implementation enforces and you can rely on:
- Soft-deleted drawers never appear in `search`, `list`, `related`, or `traverse`.
- Hard delete cascades to relations/tags/pending links and cleans the FTS index.
- Editing a drawer's content re-derives its wikilink relations but **never** touches
  manual relations.
- The store is plain SQLite — `sqlite3 ~/.secondbrain/brain.db` works for ad-hoc queries.
