#!/usr/bin/env python3
"""Tests for the conversation-capture hook.

The hook is the headline Claude Code integration ("auto-capture every
conversation"), so its title derivation must handle every transcript shape
Claude Code emits — and it must NEVER raise, per the module contract.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
import capture_conversation as cap  # noqa: E402

HOOK = HOOKS_DIR / "capture_conversation.py"


class TestExtractText(unittest.TestCase):
    """_extract_text must always return a str or None — never a list."""

    def test_bare_string(self):
        self.assertEqual(cap._extract_text("hello"), "hello")

    def test_content_block_list(self):
        # The real Claude Code format: message.content is a list of blocks.
        msg = {"role": "user", "content": [{"type": "text", "text": "design a cache"}]}
        self.assertEqual(cap._extract_text(msg), "design a cache")

    def test_dict_with_string_content(self):
        self.assertEqual(cap._extract_text({"content": "plain"}), "plain")

    def test_single_text_block(self):
        self.assertEqual(cap._extract_text({"type": "text", "text": "hi"}), "hi")

    def test_list_skips_non_text_blocks(self):
        msg = [{"type": "image"}, {"type": "text", "text": "second"}]
        self.assertEqual(cap._extract_text(msg), "second")

    def test_never_returns_list(self):
        result = cap._extract_text([{"type": "text", "text": "x"}])
        self.assertNotIsInstance(result, list)

    def test_empty_returns_none(self):
        self.assertIsNone(cap._extract_text([]))
        self.assertIsNone(cap._extract_text({}))


class TestDeriveTitle(unittest.TestCase):
    def test_title_from_content_block_format(self):
        transcript = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "Help me design a cache"}]},
        })
        title = cap._derive_title(transcript, {"session_id": "abc"})
        self.assertIn("Help me design a cache", title)

    def test_fallback_when_no_user_message(self):
        transcript = json.dumps({"type": "assistant", "message": {"content": "hi"}})
        title = cap._derive_title(transcript, {"session_id": "abcdef12345"})
        self.assertIn("abcdef12", title)  # session-id fallback


class TestHookNeverFails(unittest.TestCase):
    """The hook process must exit 0 on every input — the module contract."""

    def _run(self, stdin_text, env_extra=None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=stdin_text, capture_output=True, text=True, env=env,
        )
        return proc

    def test_empty_stdin_exits_zero(self):
        self.assertEqual(self._run("").returncode, 0)

    def test_garbage_stdin_exits_zero(self):
        self.assertEqual(self._run("not json {{{").returncode, 0)

    def test_missing_transcript_exits_zero(self):
        self.assertEqual(self._run('{"session_id":"x"}').returncode, 0)

    def test_real_format_captures_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            transcript = Path(td) / "t.jsonl"
            transcript.write_text(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": "cache design"}]},
            }) + "\n")
            db = Path(td) / "brain.db"
            payload = json.dumps({"session_id": "s1", "transcript_path": str(transcript)})
            proc = self._run(payload, {"SECONDBRAIN_DB": str(db)})
            self.assertEqual(proc.returncode, 0)
            # Verify it actually landed in the Conversations collection.
            cli = HOOKS_DIR.parent / "scripts" / "brain_cli.py"
            out = subprocess.run(
                [sys.executable, str(cli), "--db", str(db), "--json",
                 "list", "--collection", "Conversations"],
                capture_output=True, text=True,
            )
            drawers = json.loads(out.stdout)
            self.assertEqual(len(drawers), 1)
            self.assertIn("cache design", drawers[0]["title"])


if __name__ == "__main__":
    unittest.main()
