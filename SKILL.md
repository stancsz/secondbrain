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
| "What decisions have I saved?" / "What are my preferences?" | `list --collection Decisions` / `list --collection Preferences` / `list --collection Facts` / `list --collection Knowledge` — the four taxonomy collections for distilled know-how. |
| Pastes many items (abstracts, links, snippets) | One `add` per item. Auto-title, auto-tag. Cross-link overlapping ones by inserting `[[wikilinks]]` into content (see Bulk capture). |
| "Link these two notes" / "these contradict" | `relate <from> <to> --type <references\|contradicts\|expands\|related>`. |
| "Show me note N" / "open the RAG note" | `show <id-or-title>`. |
| "What's connected to N?" | `related <id>`, or `traverse <id> --depth 2` to walk further. |
| "Delete N" | `delete <id>` (soft by default — recoverable). Only pass `--hard` if they insist on permanent. |
| "Export my notes" / "back up" | `export --format markdown` (Obsidian-compatible) or `json`. |
| "Brain is getting big / 脑子不够用了 / clean up / how big is my brain?" | Run `summary` first. If it recommends action, propose it; don't act unprompted on the data. |
| "I want to focus on topic X" / "give me a smaller brain for Y" | `distill --query X --output focused.db`. Non-destructive; old brain untouched unless they pass `--activate` to swap. |
| "Archive old stuff" / "move cold notes out of the way" | `archive --output archive-2026.db` (default: untouched 180d+). Destructive: copies to archive then hard-deletes from working. `merge-brain --from archive-2026.db` brings them back. |
| "/history" / "show me my past conversations" / "what did we talk about last week?" | Browse the **log files** under `~/.secondbrain/logs/` (not the brain). The `/history` slash command in `commands/history.md` automates this — list newest logs, pick one, render it readably. |

---

## Path to the CLI

`<skill_root>` is the directory that contains this `SKILL.md` file.
The CLI is always at `<skill_root>/scripts/brain_cli.py`.

Typical installed paths:
- Personal scope: `~/.claude/skills/secondbrain/scripts/brain_cli.py`
- Project scope: `.claude/skills/secondbrain/scripts/brain_cli.py`

If the env var `SECONDBRAIN_CLI` is set, use `python3 "$SECONDBRAIN_CLI"` directly.
Otherwise, resolve the path from this file's location. If you cannot determine it,
ask the user once: "Where is secondbrain installed?"

---

## Commands

Run as: `python3 <skill_root>/scripts/brain_cli.py <command> [args]`. Add `--json` to any read
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
- `add "<title>" --content-file <path> [--collection C] [--tags a,b]` — `add` with content read from a file (avoids shell escaping for long content)

Slash command (not a CLI subcommand, lives in `commands/history.md`):
- `/history` — list past conversations, then open the chosen one

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

**Knowledge taxonomy.** When saving distilled knowledge from a conversation (via the distill channel or heuristic saves), always set `--collection` to one of these four:

| Collection | What goes here | Examples |
|---|---|---|
| `Decisions` | Concrete choices made | "Using Postgres for the project", "Chose MUI over Tailwind" |
| `Preferences` | Lasting style / approach | "Always TypeScript strict mode", "Prefer tabs over spaces" |
| `Facts` | Persistent personal/project context | "Stack: Next.js + FastAPI", "Deadline: Q3 2026", "Team lead: Alice" |
| `Knowledge` | Reusable how-to, patterns, lessons | "How to deploy to staging", "Django N+1 pattern to avoid" |

Notes saved mid-session for a specific topic (a paper abstract, a design doc) may use a topic collection (`Research`, `Work`, etc.) instead — the four above are specifically for auto-distilled conversational know-how. The `(none)` bucket in `stats` is a backlog of uncategorized drawers; any drawer is still fully searchable without a collection.

**Logs are logs; the brain is clean.** Raw conversation transcripts are **not**
stored in the brain. They are archived as plain files under `~/.secondbrain/logs/`
by the capture hook. `brain.db` holds only *distilled* knowledge — titled drawers
the user/agent deliberately saved. So `search` never returns a wall of raw JSONL,
and proactive recall surfaces real notes, not transcripts. To browse what was said
in a past session, use `/history` (it reads the log files). To recall *knowledge*,
use `search` (it reads the clean brain). Keep these two separate — never `brain add`
a raw transcript.

**Proactive capture (the install-and-forget promise).** When the user installs
this skill, the expectation is that durable knowledge accumulates automatically
while raw logs are preserved separately. Three channels, all quiet by default:

1. **Log channel.** The `Stop`/`PreCompact` hook in `hooks/capture_conversation.py`
   writes the full raw transcript to `~/.secondbrain/logs/YYYY/MM/` on every
   session end. This is a *log*, not a brain entry. The user opts in via
   `install.sh` (or by merging `settings.example.json`).
2. **Distill channel (session end).** On `Stop`, the same hook may hand you a
   `block` instruction asking you to extract the conversation's durable bits into
   **clean drawers** — never the raw transcript, which is already logged. Save
   each as its own well-titled drawer using the four-collection taxonomy
   (`Decisions`, `Preferences`, `Facts`, `Knowledge`), then stop. If the session
   has nothing durable, save nothing. This fires at most once per session
   (guarded by `stop_hook_active`), and only when a **smart trigger** decides
   the session is worth distilling: the user-text must be substantive
   (≥ `SECONDBRAIN_MIN_USER_CHARS` chars across ≥ `SECONDBRAIN_MIN_TURNS` turns)
   AND either contain a marker from `capture_conversation.py:_MARKER_PATTERNS`
   or be a long session (>= `SECONDBRAIN_LONG_SESSION_TURNS` turns). When the
   trigger fires, the hook hands you up to `SECONDBRAIN_MAX_CANDIDATES`
   pre-surfaced lines (each with 1 line of preceding user context) — review
   them, save the good ones, ignore the noise. The raw log is at the path the
   hook prints, in case you need more context than the candidates.
3. **Heuristic channel (during the chat).** You should *also* save durable bits
   the moment the user signals permanence — don't wait for session end. Triggers
   that warrant an `add` (no confirmation prompt — be quick):
   - "I want to remember this for later", "save this for me", "for my notes"
   - Decisions: "let's go with X", "I'm going with X", "from now on X"
   - Preferences: "I prefer X", "I always X", "I never X"
   - Personal facts: "I'm working on X", "I live in Y", "my project is Z"
   - Project context: stack, deadlines, stakeholders, the user dictates anything
   Don't save: questions, one-off code snippets, transient requests, anything they
   might revoke next message. When in doubt, don't save — the raw log is preserved,
   so anything durable can be distilled from it later.

   The `Stop` hook's marker regex list (`capture_conversation.py:_MARKER_PATTERNS`)
   is the codification of these categories — when you add a new trigger phrase
   here, also add it to `_MARKER_PATTERNS` so the smart trigger can surface
   matching sessions at session end.

**Proactive recall (Mode 2).** If the `UserPromptSubmit` hook
(`hooks/recall_memories.py`) is installed, relevant drawers from the clean brain
are injected into your context automatically before you answer — you'll see a
"🧠 secondbrain — possibly relevant notes" block. Use those notes when they fit
and cite the 8-char id; ignore them when they don't. This is the automatic
counterpart to the on-demand recall in the intent table (you can always run
`search` yourself too).

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
