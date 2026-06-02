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

Smart-trigger knobs (the Stop hook skips the distill block when the session
is too short or has no decision/remember markers — the raw log is still
saved in either case):
- `SECONDBRAIN_MIN_USER_CHARS=1500`     min user-text chars to consider distilling
- `SECONDBRAIN_MIN_TURNS=4`             min user-prompt turns to consider distilling
- `SECONDBRAIN_LONG_SESSION_TURNS=20`   above this, the marker check is skipped
                                        (long sessions get distilled regardless)
- `SECONDBRAIN_MAX_CANDIDATES=10`       max candidate lines surfaced to the agent
                                        (the agent filters these into drawers)
"""

from __future__ import annotations

import json
import os
import re
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

# Smart-trigger thresholds. All overridable via env. See the module docstring
# for semantics. The marker list is the codification of the trigger phrases
# documented in SKILL.md — keep them in sync.
MIN_USER_CHARS = int(os.environ.get("SECONDBRAIN_MIN_USER_CHARS", "1500"))
MIN_TURNS = int(os.environ.get("SECONDBRAIN_MIN_TURNS", "4"))
LONG_SESSION_TURNS = int(os.environ.get("SECONDBRAIN_LONG_SESSION_TURNS", "20"))
MAX_CANDIDATES = int(os.environ.get("SECONDBRAIN_MAX_CANDIDATES", "10"))


# Heuristic marker vocabulary. The agent and the SKILL.md prose use the same
# category names. One regex per kind. `re.IGNORECASE` is set on all patterns
# that mix case; literal patterns (TODO, wikilinks) don't need it.
_MARKER_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("decision", re.compile(
        r"\b(decided|decision|going with|let'?s go with|we will do|we'?ll do)\b",
        re.IGNORECASE,
    )),
    ("preference", re.compile(
        r"\b(I prefer|I always|I never|from now on)\b",
        re.IGNORECASE,
    )),
    ("remember", re.compile(
        r"\b(remember this|save this|note that|记一下|存一下|记住)\b",
        re.IGNORECASE,
    )),
    ("fact", re.compile(
        r"\b(I'?m working on|my project is|my name is|I live in)\b",
        re.IGNORECASE,
    )),
    ("wikilink", re.compile(r"\[\[.+?\]\]")),
    ("todo", re.compile(r"\b(TODO|FIXME|XXX)\b")),
]


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


def _is_user_prompt_row(row: dict) -> bool:
    """A `type=="user"` row that actually carries the user's voice.

    Tool-result rows are also typed "user" but have no `role` field; we
    exclude them so the smart trigger and candidate extractor only see what
    the user actually said (not the agent's own tool output, the hook's own
    JSON, etc.)."""
    if not isinstance(row, dict):
        return False
    msg = row.get("message")
    role = (msg or {}).get("role") or row.get("role")
    return role == "user"


def _user_turns_and_text(transcript_text: str) -> tuple[int, list[str]]:
    """Walk the JSONL and return (turn_count, [per_turn_text, ...]).

    Only rows where the user is actually speaking are counted. Tool-result
    rows and assistant rows are skipped — this is critical because the
    smart trigger and the marker regex both rely on the result being free
    of agent/hook noise."""
    turns: list[str] = []
    for line in transcript_text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _is_user_prompt_row(row):
            continue
        text = _extract_text(row.get("message") or row.get("content"))
        if text:
            turns.append(text.strip())
    return len(turns), turns


def _should_distill(transcript_text: str) -> tuple[bool, str]:
    """Decide whether the Stop hook should block to ask the agent to distill.

    Returns (should_block, reason_for_log). The raw log is always written
    upstream; this only gates the block decision.

    Rule: block when the session is substantive (chars + turns above the
    thresholds) AND either a durable-knowledge marker is present OR the
    session is long enough that the user clearly cares (the long-session
    override catches philosophical sessions where markers never appear)."""
    turns, texts = _user_turns_and_text(transcript_text)
    chars = sum(len(t) for t in texts)
    if chars < MIN_USER_CHARS:
        return False, f"user_text too short ({chars} < {MIN_USER_CHARS} chars)"
    if turns < MIN_TURNS:
        return False, f"too few user turns ({turns} < {MIN_TURNS})"
    joined = "\n".join(texts)
    marker_ok = any(p.search(joined) for _, p in _MARKER_PATTERNS)
    if not marker_ok and turns <= LONG_SESSION_TURNS:
        return False, (
            f"no durable-knowledge marker and turns={turns} <= {LONG_SESSION_TURNS}"
        )
    return True, "ok"


def _extract_candidates(transcript_text: str) -> list[dict]:
    """Surface up to MAX_CANDIDATES user lines that look like durable knowledge.

    Returns a list of dicts: {"kind", "text", "prev_line", "line_no"}.
    `prev_line` is the prior user line (1 line of context) so the agent
    doesn't have to re-read the log to interpret "going with X". Lines
    are deduped by (kind, text) and capped at MAX_CANDIDATES. One marker
    per line — the first matching kind wins."""
    # First pass: collect (file_line_no, text) for each user-prompt row.
    user_rows: list[tuple[int, str]] = []
    for file_line_no, line in enumerate(transcript_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _is_user_prompt_row(row):
            continue
        text = _extract_text(row.get("message") or row.get("content"))
        if text:
            user_rows.append((file_line_no, text.strip()))

    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []
    prev_user_line = ""
    for file_line_no, text in user_rows:
        for kind, pattern in _MARKER_PATTERNS:
            if pattern.search(text):
                key = (kind, text)
                if key not in seen:
                    seen.add(key)
                    candidates.append({
                        "kind": kind,
                        "text": text,
                        "prev_line": prev_user_line,
                        "line_no": file_line_no,
                    })
                    if len(candidates) >= MAX_CANDIDATES:
                        return candidates
                break  # one marker per line
        prev_user_line = text
    return candidates


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


def _distill_reason(
    log_path: "Path | None",
    candidates: list[dict] | None = None,
) -> str:
    """The instruction handed back to the agent on Stop. It asks for clean,
    distilled drawers — not the raw transcript, which is already on disk.

    When `candidates` is non-empty, prepends a "Candidates" section so the
    agent reviews a small filtered list rather than re-reading the full
    transcript. This is the speed/quality win of the smart trigger."""
    cli = str(BRAIN_CLI)
    db_flag = f" --db {DB_OVERRIDE}" if DB_OVERRIDE else ""
    where = f"\nThe full raw transcript is already saved as a log at:\n  {log_path}\n" if log_path else ""

    candidates_block = ""
    if candidates:
        lines = [
            "I've already done a heuristic pass and surfaced these candidate "
            "lines that look like durable knowledge. Review them, save the "
            "good ones as drawers, and ignore the noise. The full raw log "
            "is at the path above if you need more context.\n",
            "## Candidates\n",
        ]
        for i, c in enumerate(candidates, 1):
            prev = f"\n   prev: \"{c['prev_line']}\"" if c["prev_line"] else ""
            lines.append(
                f"{i}. [{c['kind']}] \"{c['text']}\"  (line {c['line_no']}){prev}"
            )
        candidates_block = "\n".join(lines) + "\n\n"

    return (
        "Before you finish, distill this conversation into the clean brain."
        f"{where}\n"
        f"{candidates_block}"
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
        should_block, why = _should_distill(transcript_text)
        if should_block:
            cands = _extract_candidates(transcript_text)
            decision = {"decision": "block",
                        "reason": _distill_reason(log_path, cands)}
            print(json.dumps(decision))
            _log(f"requested distillation ({len(cands)} candidates): blocked stop once")
        else:
            _log(f"skipped distill: {why}")

    return 0  # never fail the hook


if __name__ == "__main__":
    # Last line of defense: whatever happens, the Stop hook exits 0 so the
    # conversation always ends cleanly (the module contract).
    try:
        sys.exit(main())
    except Exception as ex:  # noqa: BLE001
        _log(f"unhandled error, exiting 0 anyway: {ex!r}")
        sys.exit(0)
