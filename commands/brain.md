---
name: brain
description: Quick access to SecondBrain — add, search, show, list, or summary without remembering the CLI shape.
---

# /brain — SecondBrain shortcut

Wraps the most common `brain_cli.py` actions so the user can do them in
natural language without remembering subcommand names or flags. The CLI
itself stays the source of truth — this just translates intent.

## Forms

Match the user's phrasing to the right subcommand. If they say:

- `/brain add "Title" "Content" [tags]` → save a drawer.
  - Infer 2–5 tags, pick a collection if obvious (use the four-collection
    taxonomy `Decisions` / `Preferences` / `Facts` / `Knowledge` only if
    the content is clearly distilled conversational know-how; otherwise
    use a topic collection or no collection).
  - Command: `brain add "<title>" "<content>" [--collection C] [--tags a,b]`.
- `/brain search "query"` → find notes.
  - Command: `brain search "<query>" [--collection C] [--tag T] [--limit N]`.
  - Synthesize a short answer citing 8-char drawer ids. Don't dump raw results.
- `/brain show <id-or-title>` → open one drawer.
  - Command: `brain show <id-or-title>`. If title is ambiguous, list matches
    and ask which.
- `/brain list [--collection X]` → browse.
  - Command: `brain list [--collection C] [--tag T] [--limit N] [--sort updated|created|title]`.
- `/brain summary` → brain health.
  - Command: `brain summary`. Propose action if it recommends, never act
    unprompted on `archive` or `distill --activate`.

## Output rules

- For each action, run the matching `brain_cli.py` subcommand. Add `--json`
  only if you need to parse the output; omit it for human-readable text.
- Be quiet: one short line per save, no echo of the full CLI invocation.
- No-op: silent unless the user explicitly asked for confirmation.

## Vague phrasing

If the user is loose ("catch me up on Braid", "what do I know about RAG?"),
interpret as the most-likely action: `list --collection Braid --sort updated`
for prep, `search RAG` for recall. Read the top 5, give a 3-paragraph brief,
surface the most-linked drawer (`related` / `traverse`).

## Examples

- `/brain add "Attention Is All You Need" "<abstract>" --collection Research --tags ml,transformers,paper`
- `/brain search second-brain`
- `/brain list --collection Braid --sort updated`
- `/brain summary`

## Other slash commands

- `/history` (in `commands/history.md`) — browse raw conversation logs at `~/.secondbrain/logs/`.
- `/brain` (this file) — operate on the brain itself.
