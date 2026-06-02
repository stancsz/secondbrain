#!/usr/bin/env python3
"""Stop / PreCompact hook: log the conversation, then distill it.

Two design principles drive this hook:

1. **Logs stay as logs.** The full raw transcript is written to a plain
   file under `~/.secondbrain/logs/` — never into `brain.db`. The brain is
   a clean, curated knowledge store; it should not fill up with megabytes of
   raw JSONL that nobody searches. `/history` browses these log files.

2. **The brain fills itself with *distilled* knowledge.** On `Stop`, after
   writing the log, the hook returns a `block` decision that asks the agent
   to extract the durable bits of the conversation (decisions, preferences,
   facts, reusable knowledge) into clean drawers — not the raw transcript.
   This runs exactly once per session: the `stop_hook_active` flag in the
   payload guards against a loop (the second stop, after distillation, is
   allowed through).

Claude Code sends a JSON payload on stdin, e.g.:

    {
      "session_id": "...",
      "transcript_path": "/abs/path/to/transcript.jsonl",
      "hook_event_name": "Stop",
      "stop_hook_active": false
    }

Wire it up in `~/.claude/settings.json` (or `.claude/settings.json`) on the
`Stop` (and optionally `PreCompact`) events. PreCompact only snapshots the
log — it never blocks to distill.

The script never raises; any error is logged to a sibling `.log` file and
the hook exits 0 (allowing the session to end) so it can never wedge a
conversation.

Env switches:
- `SECONDBRAIN_SKIP_CAPTURE=1`     disable the hook entirely
- `SECONDBRAIN_SKIP_DISTILL=1`     log only; don't nudge the agent to distill
- `SECONDBRAIN_LOGS_DIR=/path`     override the log directory (default
                                   ~/.secondbrain/logs)
- `SECONDBRAIN_DB=/path/brain.db`  brain the distill instruction should target
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Resolve sibling files relative to this script.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
BRAIN_CLI = REPO_DIR / "scripts" / "brain_cli.py"
LOG_FILE = SCRIPT_DIR / "capture_conversation.log"
# Allow tests / multi-brain users to redirect the DB. The CLI accepts --db PATH.
DB_OVERRIDE = os.environ.get("SECONDBRAIN_DB", "").strip()


def _logs_dir() -> Path:
    """Where raw transcripts are archived as plain files. Overridable for
    tests and for users who keep their brain somewhere non-default."""
    override = os.environ.get("SECONDBRAIN_LOGS_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".secondbrain" / "logs"


def _log(msg: str) -> None:
    """Append a timestamped line to the hook log. Never raises."""
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        # If we can't log, give up silently — the hook must not fail loudly.
        pass


def _read_hook_payload() -> dict:
    """Claude Code sends the payload on stdin as JSON. Tolerate empty
    input (some hosts may not provide one)."""
    try:
        raw = sys.stdin.read()
    except OSError as ex:
        _log(f"stdin read failed: {ex}")
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        _log(f"payload not JSON: {ex}; raw[:200]={raw[:200]!r}")
        return {}
    return data if isinstance(data, dict) else {}


def _read_transcript(path_str: str) -> str | None:
    """Read the JSONL transcript at the given path. Returns the raw text
    so we save the actual artifact (lossless); the agent can re-parse it
    later when the user runs `/history`."""
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        _log(f"transcript path missing: {p}")
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError as ex:
        _log(f"transcript read failed: {p}: {ex}")
        return None


def _extract_text(value) -> "str | None":
    """Pull the first usable text out of a message payload, whatever its
    shape. Claude Code transcripts nest user text as
    `message.content = [{"type": "text", "text": "..."}]`, but older/other
    formats use a bare string or a `{"text": "..."}` dict. Handles all of
    them, recursively, and always returns a string or None — never a list."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # A message wrapper ({"content": ...}) or a single block ({"text": ...}).
        if "content" in value:
            return _extract_text(value["content"])
        if value.get("type") in (None, "text") and isinstance(value.get("text"), str):
            return value["text"]
        return None
    if isinstance(value, list):
        for block in value:
            text = _extract_text(block)
            if text:
                return text
    return None


def _derive_title(transcript_text: str, payload: dict) -> str:
    """Build a human-readable title. Falls back to a timestamp + id if
    the transcript doesn't have a clean first-user-message."""
    first_user_msg = None
    for line in transcript_text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "user" or row.get("role") == "user":
            msg = row.get("message") or row.get("content")
            first_user_msg = _extract_text(msg)
            if first_user_msg:
                break

    if first_user_msg:
        first_user_msg = " ".join(first_user_msg.split())
        if len(first_user_msg) > 60:
            first_user_msg = first_user_msg[:57] + "..."
        return f"{datetime.now().strftime('%Y-%m-%d %H:%M')} — {first_user_msg}"

    # Fallback: timestamp + session id
    sid = payload.get("session_id", "")
    sid_short = sid[:8] if sid else "unknown"
    return f"Conversation {datetime.now().strftime('%Y-%m-%d %H:%M')} ({sid_short})"


def _write_log(transcript_text: str, payload: dict) -> "Path | None":
    """Archive the raw transcript as a plain file under the logs dir.

    The filename is keyed by date + session id so that the second `Stop`
    of a session (the one after distillation) overwrites the same file
    instead of creating a duplicate. Returns the path, or None on failure."""
    sid = (payload.get("session_id") or "").strip()
    sid_short = sid[:8] if sid else "unknown"
    now = datetime.now()
    target_dir = _logs_dir() / now.strftime("%Y") / now.strftime("%m")
    # Date + session id only (no seconds) → idempotent across the double-stop.
    fname = f"{now.strftime('%Y-%m-%d')}__{sid_short}.jsonl"
    path = target_dir / fname
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(transcript_text, encoding="utf-8")
        return path
    except OSError as ex:
        _log(f"log write failed: {path}: {ex}")
        return None


def _distill_reason(log_path: "Path | None") -> str:
    """The instruction handed back to the agent on Stop. It asks for clean,
    distilled drawers — not the raw transcript, which is already on disk."""
    cli = str(BRAIN_CLI)
    db_flag = f" --db {DB_OVERRIDE}" if DB_OVERRIDE else ""
    where = f"\nThe full raw transcript is already saved as a log at:\n  {log_path}\n" if log_path else ""
    return (
        "Before you finish, distill this conversation into the clean brain."
        f"{where}\n"
        "Extract ONLY durable, reusable knowledge and save each item as its "
        "own clean drawer (never the raw transcript):\n"
        "  • decisions made   ('we decided X', 'going with Y')\n"
        "  • preferences      ('I prefer X', 'always/never Y')\n"
        "  • personal/project facts (stack, deadlines, people, context)\n"
        "  • reusable knowledge worth recalling in a later session\n\n"
        f"Save each with:\n  python3 {cli}{db_flag} add \"<title>\" \"<content>\" "
        "[--collection C] [--tags a,b]\n\n"
        "Write a clear title and a self-contained 1–3 sentence body that will "
        "still make sense months from now without this chat around it. "
        "Add [[wikilinks]] to related notes where natural.\n\n"
        "If this conversation has nothing durable (a quick question, throwaway "
        "debugging, small talk), save NOTHING and just stop. Quality over "
        "quantity — the raw log is preserved either way."
    )


def main() -> int:
    # Bail-out switch
    if os.environ.get("SECONDBRAIN_SKIP_CAPTURE") == "1":
        return 0

    payload = _read_hook_payload()
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    transcript_text = _read_transcript(transcript_path)
    if not transcript_text:
        # Nothing to log (no transcript or empty); don't log noise.
        return 0

    # 1. Logs stay as logs — archive the raw transcript to disk.
    log_path = _write_log(transcript_text, payload)
    if log_path:
        _log(f"logged: {log_path}  ({len(transcript_text)} chars)  event={event!r}")

    # 2. Auto-distill at session end. Only on Stop, only once per session
    #    (stop_hook_active guards the loop), and only if not disabled.
    distill_on = (
        event == "Stop"
        and os.environ.get("SECONDBRAIN_SKIP_DISTILL") != "1"
        and not payload.get("stop_hook_active")
    )
    if distill_on:
        decision = {"decision": "block", "reason": _distill_reason(log_path)}
        print(json.dumps(decision))
        _log("requested distillation (blocked stop once)")

    return 0  # never fail the hook


if __name__ == "__main__":
    # Last line of defense: whatever happens, the Stop hook exits 0 so the
    # conversation always ends cleanly (the module contract).
    try:
        sys.exit(main())
    except Exception as ex:  # noqa: BLE001
        _log(f"unhandled error, exiting 0 anyway: {ex!r}")
        sys.exit(0)
