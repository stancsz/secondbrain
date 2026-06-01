# secondbrain

> A local, file-based knowledge graph for AI agents. One SQLite file, zero dependencies, full data ownership.
>
> [中文文档](./README.zh.md) · [Architecture](./references/architecture.md) · [SKILL.md](./SKILL.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org)
[![Dependencies: 0](https://img.shields.io/badge/dependencies-0-green.svg)](#installation)
[![Schema: v2.1](https://img.shields.io/badge/schema-v2.1-blueviolet.svg)](./scripts/schema.sql)

---

> You're not building a second brain. You're renting one. Every few years the rent goes up — the export becomes a Pro feature, the API terms tighten, the company gets acquired or nearly shuts down. You migrate, you lose structure, and the cycle starts again. `secondbrain` bets on the other side: one file, in your home directory, versioned in your git repo. No migration plan, because there's no vendor to migrate from.

---

## What it is

`secondbrain` is a personal knowledge store designed to be read and written by AI agents as easily as by humans. Notes are stored in a single SQLite file at `~/.secondbrain/brain.db` using the Python standard library only — no `pip install`, no `docker compose up`, no cloud account.

Notes are linked together through `[[wikilinks]]` in their content, building a knowledge graph automatically as you write. The store supports full-text search, typed relations, tags, collections, soft delete, and round-trip export to Markdown.

The `SKILL.md` in this repository makes `secondbrain` a drop-in [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills): any agent that loads the skill can save, search, and link notes in your brain during a conversation.

## Why

Most "AI memory" products store your data in a third-party cloud, behind an API, and behind a vendor that can change pricing, terms, or shut down at any time. Even local-first tools like Obsidian don't speak natively to agents — you end up with one tool for humans and a separate, paid API for AI.

`secondbrain` is a deliberately minimal alternative:

- **One file.** A SQLite database you can open with any tool, copy with `cp`, back up with `rsync`, version with `git`.
- **Standard schema.** The schema is checked in as `scripts/schema.sql` and is plain SQL — no proprietary format, no migration service.
- **Agent-native.** Every operation is a single CLI command. Agents read and write the brain with the same interface a human does.
- **Zero dependencies.** If you have Python 3.8+ and a SQLite build, you can run it.

## Features

- **Flat knowledge graph.** Drawers (notes) carry tags, an optional collection, and typed relations. No folder hierarchy to maintain.
- **`[[wikilinks]]`.** Cross-references are written in the body and resolved at write time — relations cannot drift from the text.
- **Pending links.** Forward references to not-yet-existing notes are stored in an indexed table and promoted to real relations when the target is created.
- **Full-text search.** SQLite FTS5 with soft-delete awareness. Returns sub-100ms results on 50K drawers.
- **Soft delete by default.** `delete` is reversible; `delete --hard` is permanent.
- **Typed relations.** `references`, `contradicts`, `expands`, `related` with optional strength.
- **Graph traversal.** Recursive CTE-based traverse from any drawer.
- **Import / export.** Round-trip to JSON, Markdown (Obsidian-compatible), and CSV.
- **Distill & archive.** Goal-based filter (`distill --query "X"`) writes a focused working brain without touching the old one (pass `--activate` to swap). Cold-storage (`archive --older-than-days 180`) moves untouched drawers out and VACUUMs the working brain. `merge-brain --from <archive>` brings them back.
- **Auto-capture conversations.** A `Stop` hook writes the full transcript of every conversation into `collection=Conversations`. The agent also proactively saves durable bits during a conversation when the user signals permanence.
- **`/history` slash command.** Browse past conversations in your brain, then dive into the chosen one.
- **Phase 2 (planned).** Optional vector search via `sqlite-vec` and an MCP server interface.

## Installation

```bash
git clone https://github.com/stancsz/secondbrain.git
cd secondbrain
python3 scripts/brain_cli.py stats    # first run creates ~/.secondbrain/brain.db
```

Optional, to invoke as `brain`:

```bash
ln -s "$(pwd)/scripts/brain_cli.py" /usr/local/bin/brain
# or
alias brain='python3 ~/path/to/secondbrain/scripts/brain_cli.py'
```

The only runtime requirement is Python 3.8+ with `sqlite3` (included in the standard library). The schema uses FTS5, JSON1, and recursive CTEs; these are built into the Python-bundled SQLite since 3.9, otherwise SQLite 3.41+ is required.

## Quick start

```bash
# Capture
python3 scripts/brain_cli.py add "RAG" "Retrieval-augmented generation" \
  --collection AI --tags rag,llm

# Recall
python3 scripts/brain_cli.py search "RAG"

# Link (the [[RAG]] in content auto-resolves to a references relation;
# if RAG doesn't exist yet, it goes to pending_links and resolves on first match)
python3 scripts/brain_cli.py add "Vector Search" "See [[RAG]]" --collection AI

# Traverse the graph
python3 scripts/brain_cli.py related <id>
python3 scripts/brain_cli.py traverse <id> --depth 2

# Brain health
python3 scripts/brain_cli.py summary

# Distill a focused working brain (old brain stays as a point-in-time backup)
python3 scripts/brain_cli.py distill --query "RAG" --output focused.db --activate

# Cold-store untouched drawers (180d+) and shrink the working brain
python3 scripts/brain_cli.py archive --output archive-2026.db --older-than-days 180

# Bring archived drawers back
python3 scripts/brain_cli.py merge-brain --from archive-2026.db

# Browse past conversations (also available as the /history slash command)
python3 scripts/brain_cli.py list --collection Conversations --sort updated

# Export (Obsidian-compatible)
python3 scripts/brain_cli.py export --format markdown --output brain.md
```

## Use with Claude Code

This repository is itself a Claude Code skill — `SKILL.md` defines triggers and behavior. Three ways to install:

**Project scope** (one project):

```bash
mkdir -p .claude/skills
git clone https://github.com/stancsz/secondbrain.git .claude/skills/secondbrain
```

**Personal scope** (all your projects):

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/stancsz/secondbrain.git ~/.claude/skills/secondbrain
```

**Submodule** (if you want to pin a version):

```bash
git submodule add https://github.com/stancsz/secondbrain.git .claude/skills/secondbrain
```

Once installed, the agent will catch phrases like "remember this", "what do I know about X", "catch me up on project Y", "记一下", "我之前写过 X 吗", and act on them using your brain.

### Auto-capture every conversation (optional but recommended)

If you want the brain to **remember every conversation automatically**, copy the example hook config into your own Claude Code settings:

```bash
# Personal scope: every project, every conversation
cp <repo>/settings.example.json ~/.claude/settings.json
# then edit the file and replace /path/to/secondbrain with the real path

# Or project scope: just this project
cp <repo>/settings.example.json .claude/settings.json
# then edit
```

This wires up two hooks that call `hooks/capture_conversation.py`:

- **`Stop`** — saves the full transcript of every conversation into `collection=Conversations`. Quiet, never fails the conversation, and writes a one-line entry to `hooks/capture_conversation.log` so you can audit it.
- **`PreCompact`** *(optional)* — also saves a snapshot before context compaction in long sessions. Comment this out if it feels noisy.

To disable temporarily without removing the hook:

```bash
SECONDBRAIN_SKIP_CAPTURE=1 claude
```

### `/history` slash command

The repo ships a slash command at `commands/history.md` that lets you browse past conversations. Wire it up with a symlink:

```bash
# Personal scope
mkdir -p ~/.claude/commands
ln -s <repo>/commands/history.md ~/.claude/commands/history.md
```

Then in any conversation, type `/history` — the agent lists your `collection=Conversations` drawers and opens the one you pick. You can also just say "show me my last 3 conversations" and the skill handles it the same way.

## Comparison

| Tool | Data location | Agent-readable | Lock-in | Backup | Cross-session memory | Install |
|---|---|---|---|---|---|---|
| Notion AI | Notion cloud | No | High | Vendor-controlled | No | Browser |
| ChatGPT Memory | OpenAI cloud | No | Total (black box) | Vendor-controlled | Yes (opaque) | Browser |
| Claude Projects | Anthropic cloud | No | High | Vendor-controlled | Yes (per-project) | Browser |
| mem0 | Vendor Postgres | Yes (paid API) | Medium (SDK bound) | Vendor-controlled | Yes (API) | `pip install` + key |
| Obsidian | Local `.md` | No (plugin required) | None | Manual | No (DIY) | Desktop app |
| Logseq | Local `.md` | No | None | Manual | No | Desktop app |
| Anytype | Local (P2P) | No | None | Manual sync | No | Desktop app |
| Quivr / privateGPT | Local vector DB | Via API | None | Manual | No | Docker + models |
| Apple Notes / Keep / OneNote | Vendor cloud | No | High | Vendor-controlled | No | OS-bundled |
| Evernote | Vendor cloud | No | High (historic) | Vendor-controlled | No | Desktop / web |
| **secondbrain** | **Local SQLite** | **Yes (CLI)** | **None** | **`cp` / `git push`** | **Yes (agent-native)** | **`git clone`** |

**What only `secondbrain` offers in this list:**

1. **Full data ownership.** The store is a plain SQLite file. `sqlite3 brain.db` opens it. The schema is in this repository as `scripts/schema.sql`. There is no export flow because there is no vendor to export from.
2. **Versionable.** The whole brain is one file. `git init` it, `git push` it to a private GitHub repo, get free history, diff, and disaster recovery.
3. **Agent-native.** The CLI is the API. There is no second interface for "AI mode" that you have to pay for separately.

## When to use

- You use AI agents (Claude Code, Cursor, Aider, Continue, custom) and want them to remember across sessions.
- You want a knowledge base that survives any single vendor disappearing.
- You are comfortable with a 200-line Python CLI and a SQLite file.
- You want one tool that humans and agents both drive, with the same data.

## When not to use

- You want a polished WYSIWYG note-taking app for non-technical users → use Obsidian or Notion.
- You need a team wiki with permissions and comments → use Notion or Confluence.
- You need to store millions of documents and run vector search at scale → use a dedicated vector database; `secondbrain` is for personal-scale knowledge.
- You cannot run Python locally → use a hosted note service.

## Architecture

See [`references/architecture.md`](./references/architecture.md) for:

- The data model (3 tables + FTS + `pending_links`)
- FTS5 correctness notes (the v2 bugs and their v2.1 fixes)
- Wikilink resolution rules (frozen at write time)
- Soft delete semantics
- Phase 2 MCP interface contract
- v1 → v2 migration
- Performance targets

## Backup strategy

The recommended setup is to put `~/.secondbrain/brain.db` under version control in a private GitHub repository. The database is a single file; even at 50K drawers it is typically under 100 MB, which is fine for `git push`.

For continuous backup, pair with [litestream](https://litestream.io/) to replicate the WAL stream to S3, Backblaze, or any S3-compatible object store. Schema migrations and disaster recovery are standard SQLite operations.

## Roadmap

- **v2.1 (current).** FTS5, soft delete, write-time-frozen wikilinks, `pending_links` table, recursive traverse.
- **Phase 2.** MCP server, vector search via `sqlite-vec`, automatic `inferred`-source links above a similarity threshold.
- **Ideas.** Markdown round-trip sync, Obsidian-compatible export refinements, encrypted local replicas.

## Contributing

Issues and pull requests welcome. The schema is the API — please open an issue before adding tables or columns.

## License

[MIT](./LICENSE) © 2026 secondbrain contributors
