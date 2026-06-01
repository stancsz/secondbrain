---
description: Browse past conversations saved in your secondbrain. Lists Conversations-collection drawers, then dives into the chosen one.
---

# /history

Show the user a list of their saved conversations, then open the one they
pick. This is a slash command — the body below is the prompt Claude Code
sees when the user types `/history`.

## Behavior

1. **List recent conversations.** Run:

   ```bash
   python3 <repo>/scripts/brain_cli.py list --collection Conversations --sort updated --limit 30
   ```

   If the result is empty, tell the user: "No saved conversations yet.
   If you wired up the auto-capture hook in `settings.json`, every
   conversation will be saved automatically. Otherwise say
   `/save-conversation` or ask me to save this one."

2. **Present the list.** For each drawer, show:
   - Index (1-based)
   - Title (which is the date+time the conversation was saved)
   - Collection badge (`[Conversations]`)
   - Tags (e.g. `auto-capture`, topic tags)
   - Short id (8 chars)

   Use the standard `list` formatting.

3. **Ask the user to pick.** "Reply with a number, a short id, or
   `q` to cancel."

4. **Open the chosen conversation.** Run:

   ```bash
   python3 <repo>/scripts/brain_cli.py show <id>
   ```

   If the content is a raw transcript (JSONL from Claude Code's hook),
   parse it on the fly and present a readable form: a timeline of
   user messages and assistant responses, with timestamps. Don't dump
   the raw JSONL at the user.

5. **Offer follow-ups.** After showing the conversation, ask if they
   want to:
   - Search across all conversations (`brain search ... --collection Conversations`)
   - Summarize a slice
   - Distill it into a focused drawer (via `brain distill`)
   - Archive it (via `brain archive --collection Conversations`)

## Path resolution

`<repo>` is the absolute path to this secondbrain repo. Resolve it
from the slash command file's location:

```
<skill_root>/commands/history.md  →  <skill_root>/scripts/brain_cli.py
```

If you can't resolve it, ask the user where they installed the skill.

## Notes

- This command does **not** require the auto-capture hook to be wired up.
  If conversations exist (e.g. the user saved some manually), the command
  will find them. If none exist, fall back to the helpful-empty-state
  message above.
- The user can also type natural phrases like "show me my last 3
  conversations" or "what did we talk about last Tuesday?" and the
  secondbrain skill should handle those via `search` + filter on
  `collection=Conversations`. Treat this slash command as a shortcut
  for that flow.
