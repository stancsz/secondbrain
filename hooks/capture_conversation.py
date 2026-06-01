#!/usr/bin/env python3
"""Stop hook: capture the just-finished conversation into the brain.

Claude Code fires a `Stop` hook at the end of every session. The hook
receives a JSON payload on stdin with at least:

    {
      "session_id": "...",
      "transcript_path": "/abs/path/to/transcript.jsonl",
      "hook_event_name": "Stop",
      "last_assistant_message": "..."
    }

We read the transcript file, derive a title (date + first user message
or session id), and add it to the brain as a `Conversations`-collection
drawer. Tags are added for easy filtering.

Wire it up in `~/.claude/settings.json` (or `.claude/settings.json`):

    {
      "hooks": {
        "Stop": [
          {
            "matcher": "*",
            "hooks": [
              {
                "type": "command",
                "command": "python3 /path/to/secondbrain/hooks/capture_conversation.py"
              }
            ]
          }
        ]
      }
    }

The script never raises; any error is logged to a sibling `.log` file
and the hook returns 0 so the conversation still ends cleanly.

To disable temporarily, set the env var `SECONDBRAIN_SKIP_CAPTURE=1`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Resolve sibling files relative to this script.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
BRAIN_CLI = REPO_DIR / "scripts" / "brain_cli.py"
LOG_FILE = SCRIPT_DIR / "capture_conversation.log"
# Allow tests / multi-brain users to redirect the DB. The CLI accepts --db PATH.
DB_OVERRIDE = os.environ.get("SECONDBRAIN_DB", "").strip()


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
            if isinstance(msg, str):
                first_user_msg = msg
            elif isinstance(msg, dict):
                first_user_msg = msg.get("content") or msg.get("text")
            elif isinstance(msg, list):
                # Walk the content blocks to find the first text part.
                for block in msg:
                    if isinstance(block, dict) and block.get("type") == "text":
                        first_user_msg = block.get("text")
                        break
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


def _add_to_brain(title: str, content: str) -> tuple[bool, str]:
    """Call brain_cli.py add with --content-file to avoid shell escaping.
    Returns (ok, stderr_text)."""
    if not BRAIN_CLI.exists():
        return False, f"brain_cli.py not found at {BRAIN_CLI}"

    # Use a temp file because the content can be megabytes of JSONL.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(content)
        tmp.close()
        cmd = [
            sys.executable, str(BRAIN_CLI),
            "add", title,
            "--content-file", tmp.name,
            "--collection", "Conversations",
            "--tags", "auto-capture",
        ]
        if DB_OVERRIDE:
            cmd[2:2] = ["--db", DB_OVERRIDE]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:400]
    return True, ""


def main() -> int:
    # Bail-out switches
    if os.environ.get("SECONDBRAIN_SKIP_CAPTURE") == "1":
        return 0

    payload = _read_hook_payload()
    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    transcript_text = _read_transcript(transcript_path)
    if not transcript_text:
        # Nothing to save (no transcript or empty); don't log noise.
        return 0

    title = _derive_title(transcript_text, payload)
    ok, err = _add_to_brain(title, transcript_text)
    if ok:
        _log(f"captured: {title!r}  ({len(transcript_text)} chars)")
    else:
        _log(f"capture FAILED: {title!r}  err={err!r}")
    return 0  # never fail the hook


if __name__ == "__main__":
    sys.exit(main())
