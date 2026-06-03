#!/usr/bin/env python3
"""UserPromptSubmit hook: proactively recall relevant notes (Mode 2).

Claude Code fires a `UserPromptSubmit` hook every time the user submits a
prompt, *before* the model sees it. The hook receives a JSON payload on
stdin:

    {
      "session_id": "...",
      "transcript_path": "/abs/path/to/transcript.jsonl",
      "cwd": "/abs/path",
      "hook_event_name": "UserPromptSubmit",
      "prompt": "what did we decide about the checkout timeout?"
    }

We turn the prompt into a safe full-text query, search the brain, and print
a short block of the most relevant drawers to stdout. For UserPromptSubmit
hooks, stdout (with exit code 0) is injected into the model's context — so
Claude sees the user's own notes *before* it answers, without the user
having to ask "what do I know about X". This is the proactive counterpart
to the reactive recall the skill already does on demand.

Wire it up in `~/.claude/settings.json` (or `.claude/settings.json`):

    {
      "hooks": {
        "UserPromptSubmit": [
          {
            "matcher": "*",
            "hooks": [
              {
                "type": "command",
                "command": "python3 /path/to/second-brain/hooks/recall_memories.py"
              }
            ]
          }
        ]
      }
    }

Design rules:
- **Never block.** Recall is an enhancement; on any error the hook prints
  nothing and exits 0 so the prompt goes through untouched.
- **Never dump transcripts.** Raw conversation logs live on disk
  (`~/.secondbrain/logs/`), not in the brain, so the brain is clean by
  construction. As belt-and-suspenders we also skip any legacy
  `Conversations` collection, in case an older brain still has one.
- **Stay small.** Top few drawers, short snippets — the stdout cap is 10k
  chars and this runs on *every* prompt, so it has to be fast and quiet.

Env switches:
- `SECONDBRAIN_SKIP_RECALL=1`     disable proactive recall for the session
- `SECONDBRAIN_DB=/path/brain.db` use a non-default brain
- `SECONDBRAIN_RECALL_LIMIT=N`    max drawers to surface (default 3)
- `SECONDBRAIN_RECALL_COLLECTION` restrict recall to one collection
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
LOG_FILE = SCRIPT_DIR / "recall_memories.log"

# Make the SecondBrain class importable (it lives in ../scripts).
sys.path.insert(0, str(REPO_DIR / "scripts"))

# Collections that should never be surfaced proactively (raw transcripts).
EXCLUDED_COLLECTIONS = {"Conversations"}

# A small stopword list so the FTS query carries signal, not filler.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "of",
    "to", "in", "on", "at", "by", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "doing", "have", "has", "had", "i", "you", "we",
    "they", "it", "this", "that", "these", "those", "my", "your", "our",
    "me", "us", "what", "which", "who", "whom", "whose", "when", "where",
    "why", "how", "can", "could", "should", "would", "will", "shall", "may",
    "might", "must", "with", "about", "from", "into", "as", "so", "not",
    "no", "yes", "please", "tell", "show", "give", "get", "let", "go",
    "want", "need", "know", "think", "make", "help", "any", "some", "all",
    # Conversational filler — prompts made only of these carry no recall signal.
    "ok", "okay", "thanks", "thank", "thx", "hi", "hello", "hey", "yo",
    "cool", "nice", "great", "sure", "yeah", "yep", "nope", "lol", "pls",
}

# Token = run of letters/digits/underscore; also keeps CJK characters whole.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")


def _log(msg: str) -> None:
    """Append a timestamped line to the hook log. Never raises."""
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        pass


def _read_payload() -> dict:
    """Read the JSON payload Claude Code sends on stdin. Tolerate anything."""
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _build_fts_query(prompt: str, max_terms: int = 10) -> "str | None":
    """Turn a free-form prompt into a safe FTS5 MATCH query.

    Raw natural language breaks FTS5 (quotes, parens, `-`, `AND`/`OR`/`NOT`,
    `:` column filters all have meaning). We extract meaningful tokens, drop
    stopwords and 1-2 char noise, double-quote each token so FTS treats it
    as a literal, and OR them together for broad recall. Returns None when
    nothing useful survives (e.g. "ok thanks")."""
    tokens = _TOKEN_RE.findall((prompt or "").lower())
    seen: set = set()
    kept: list = []
    for tok in tokens:
        # Keep CJK runs regardless of length; for latin tokens require len>=3.
        is_cjk = bool(re.match(r"[一-鿿]", tok))
        if not is_cjk and (len(tok) < 3 or tok in STOPWORDS):
            continue
        if tok in seen:
            continue
        seen.add(tok)
        kept.append(tok)
        if len(kept) >= max_terms:
            break
    if not kept:
        return None
    # Double-quote each token → FTS5 treats it as a literal, never an operator.
    return " OR ".join(f'"{t}"' for t in kept)


def _recall(prompt: str, db_path: "str | None", limit: int,
            only_collection: "str | None" = None) -> list:
    """Search the brain for drawers relevant to the prompt. Returns a list of
    drawer dicts (already excludes soft-deleted via SecondBrain.search)."""
    query = _build_fts_query(prompt)
    if not query:
        return []
    try:
        from brain import SecondBrain
    except Exception as ex:  # noqa: BLE001
        _log(f"could not import SecondBrain: {ex!r}")
        return []

    b = None
    try:
        b = SecondBrain(db_path) if db_path else SecondBrain()
        # Over-fetch so we can drop excluded collections and still fill `limit`.
        raw = b.search(query, collection=only_collection, limit=limit * 4)
    except Exception as ex:  # noqa: BLE001
        _log(f"search failed for query {query!r}: {ex!r}")
        return []
    finally:
        if b is not None:
            try:
                b.close()
            except Exception:  # noqa: BLE001
                pass

    results = []
    for d in raw:
        if only_collection is None and d.get("collection") in EXCLUDED_COLLECTIONS:
            continue
        results.append(d)
        if len(results) >= limit:
            break
    return results


def _snippet(text: str, n: int = 100) -> str:
    text = " ".join((text or "").split())
    return text[:n] + ("…" if len(text) > n else "")


def _format_context(drawers: list) -> str:
    """Render the recalled drawers as a compact context block for the model.

    Kept terse on purpose: this prints on every prompt, so it must not feel
    like noise. Just the bullets — the SKILL.md tells the agent how to use
    them and when to stay quiet about them."""
    lines = ["🧠 second-brain — possibly relevant notes (cite 8-char id, ignore if irrelevant):"]
    for d in drawers:
        coll = f" [{d['collection']}]" if d.get("collection") else ""
        lines.append(f"- \"{d['title']}\"{coll} ({d['id'][:8]})")
        snip = _snippet(d.get("content", ""))
        if snip:
            lines.append(f"  {snip}")
    return "\n".join(lines)


def main() -> int:
    if os.environ.get("SECONDBRAIN_SKIP_RECALL") == "1":
        return 0

    payload = _read_payload()
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str) or not prompt.strip():
        return 0

    db_path = os.environ.get("SECONDBRAIN_DB", "").strip() or None
    try:
        limit = int(os.environ.get("SECONDBRAIN_RECALL_LIMIT", "3"))
    except ValueError:
        limit = 3
    limit = max(1, min(limit, 15))
    only_collection = os.environ.get("SECONDBRAIN_RECALL_COLLECTION", "").strip() or None

    drawers = _recall(prompt, db_path, limit, only_collection)
    if not drawers:
        # Nothing relevant — stay silent, let the prompt through untouched.
        return 0

    print(_format_context(drawers))
    _log(f"recalled {len(drawers)} drawer(s) for prompt[:60]={prompt[:60]!r}")
    return 0


if __name__ == "__main__":
    # The hook must never block a prompt — swallow everything and exit 0.
    try:
        sys.exit(main())
    except Exception as ex:  # noqa: BLE001
        _log(f"unhandled error, exiting 0 anyway: {ex!r}")
        sys.exit(0)
