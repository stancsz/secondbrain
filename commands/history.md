---
description: Browse past conversation logs saved by second-brain. Lists raw transcript log files under ~/.secondbrain/logs/, then opens the one you pick in a readable form.
---

# /history

Show the user a list of their saved conversation **logs**, then open the one
they pick. This is a slash command — the body below is the prompt Claude Code
sees when the user types `/history`.

> Conversation logs are plain files, not brain drawers. The brain (`brain.db`)
> holds only distilled knowledge; the full raw transcripts live on disk under
> `~/.secondbrain/logs/YYYY/MM/`. This command browses those files.

## Behavior

1. **List recent logs.** The logs live under `~/.secondbrain/logs/` (override:
   `$SECONDBRAIN_LOGS_DIR`), nested by year/month, named
   `YYYY-MM-DD__<session8>.jsonl`. List the most recent ~30, newest first:

   ```bash
   ls -1t "${SECONDBRAIN_LOGS_DIR:-$HOME/.second-brain/logs}"/*/*/*.jsonl 2>/dev/null | head -30
   ```

   If there are none, tell the user: "No conversation logs yet. If you wired up
   the capture hook in `settings.json`, every session is logged automatically
   under `~/.secondbrain/logs/`. Otherwise this session isn't being recorded."

2. **Present the list.** For each log file, show:
   - Index (1-based)
   - The date (from the filename)
   - A one-line topic, derived by reading the first user message in the file
     (parse the JSONL; the first `type:"user"` row's text)
   - The filename (so the user can pick by it)

3. **Ask the user to pick.** "Reply with a number, a filename, or `q` to cancel."

4. **Open the chosen log.** Read the file and present a readable timeline of
   user messages and assistant responses with timestamps. Do **not** dump the
   raw JSONL at the user.

5. **Offer follow-ups.** After showing the conversation, ask if they want to:
   - **Distill it into the brain** — extract durable decisions/facts/preferences
     as clean drawers with `brain add ...`. (The Stop hook used to nudge
     this automatically; now it's a manual step you can run any time.)
   - Search across logs (`grep -l "<term>" ~/.secondbrain/logs/*/*/*.jsonl`).
   - Summarize a slice.

## Notes

- The brain and the logs are deliberately separate. Searching your **knowledge**
  ("what do I know about X") goes through `brain search` and never touches these
  raw logs. Browsing **what was said in a past session** is what `/history` is for.
- Natural phrases like "show me my last 3 conversations" or "what did we talk
  about last Tuesday?" should route through this same log-browsing flow.
- Distillation is how knowledge gets from a log into the brain. Run it
  explicitly with `brain add ...` from any context (here, or mid-session).
