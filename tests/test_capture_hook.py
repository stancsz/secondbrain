#!/usr/bin/env python3
"""Tests for the conversation-capture hook.

New model: logs stay as logs (plain files under ~/.secondbrain/logs/), the
brain stays clean, and on Stop the hook asks the agent to distill durable
knowledge into clean drawers. The hook must never raise and never wedge a
session (always exits 0).
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

REAL_TRANSCRIPT = json.dumps({
    "type": "user",
    "message": {"role": "user", "content": [{"type": "text", "text": "design a cache"}]},
}) + "\n"


class TestExtractText(unittest.TestCase):
    """_extract_text must always return a str or None — never a list."""

    def test_bare_string(self):
        self.assertEqual(cap._extract_text("hello"), "hello")

    def test_content_block_list(self):
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
        self.assertNotIsInstance(cap._extract_text([{"type": "text", "text": "x"}]), list)

    def test_empty_returns_none(self):
        self.assertIsNone(cap._extract_text([]))
        self.assertIsNone(cap._extract_text({}))


class TestWriteLog(unittest.TestCase):
    def test_writes_plain_file_under_logs_dir(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["SECONDBRAIN_LOGS_DIR"] = td
            try:
                path = cap._write_log(REAL_TRANSCRIPT, {"session_id": "abc12345"})
            finally:
                del os.environ["SECONDBRAIN_LOGS_DIR"]
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(), REAL_TRANSCRIPT)
            # Nested under year/month and named by date + session id.
            self.assertIn("abc12345", path.name)
            self.assertTrue(str(path).startswith(td))

    def test_idempotent_across_double_stop(self):
        # Same session on the same day must reuse one file, not duplicate.
        with tempfile.TemporaryDirectory() as td:
            os.environ["SECONDBRAIN_LOGS_DIR"] = td
            try:
                p1 = cap._write_log(REAL_TRANSCRIPT, {"session_id": "sess0001"})
                p2 = cap._write_log(REAL_TRANSCRIPT + "more\n", {"session_id": "sess0001"})
            finally:
                del os.environ["SECONDBRAIN_LOGS_DIR"]
            self.assertEqual(p1, p2)
            files = list(Path(td).rglob("*.jsonl"))
            self.assertEqual(len(files), 1)


class TestDistillReason(unittest.TestCase):
    def test_reason_mentions_clean_drawers_not_transcript(self):
        reason = cap._distill_reason(Path("/tmp/logs/x.jsonl"))
        self.assertIn("distill", reason.lower())
        self.assertIn("never the raw transcript", reason.lower())
        self.assertIn("/tmp/logs/x.jsonl", reason)
        self.assertIn("add", reason)


class TestHookProcess(unittest.TestCase):
    """Subprocess-level behavior — the contract Claude Code actually sees."""

    def _run(self, payload_obj, env_extra=None, transcript=REAL_TRANSCRIPT):
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            tpath = Path(td) / "t.jsonl"
            tpath.write_text(transcript)
            payload = dict(payload_obj)
            payload.setdefault("transcript_path", str(tpath))
            env["SECONDBRAIN_LOGS_DIR"] = str(logs)
            if env_extra:
                env.update(env_extra)
            proc = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps(payload), capture_output=True, text=True, env=env,
            )
            log_files = list(logs.rglob("*.jsonl")) if logs.exists() else []
            return proc, log_files

    def test_stop_writes_log_and_blocks_to_distill(self):
        proc, logs = self._run({"hook_event_name": "Stop", "session_id": "s1"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 1)  # log written
        out = json.loads(proc.stdout)   # block decision emitted
        self.assertEqual(out["decision"], "block")
        self.assertIn("distill", out["reason"].lower())

    def test_stop_hook_active_does_not_block_again(self):
        # The second stop (after distillation) must be allowed through.
        proc, logs = self._run(
            {"hook_event_name": "Stop", "session_id": "s1", "stop_hook_active": True}
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 1)          # still logs
        self.assertEqual(proc.stdout.strip(), "")  # but no block

    def test_precompact_logs_but_never_blocks(self):
        proc, logs = self._run({"hook_event_name": "PreCompact", "session_id": "s2"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(proc.stdout.strip(), "")

    def test_skip_distill_logs_only(self):
        proc, logs = self._run(
            {"hook_event_name": "Stop", "session_id": "s3"},
            {"SECONDBRAIN_SKIP_DISTILL": "1"},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(proc.stdout.strip(), "")

    def test_skip_capture_does_nothing(self):
        proc, logs = self._run(
            {"hook_event_name": "Stop", "session_id": "s4"},
            {"SECONDBRAIN_SKIP_CAPTURE": "1"},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_never_writes_to_brain_db(self):
        # The brain must stay clean: no brain.db should be created by capture.
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            logs = Path(td) / "logs"
            db = Path(td) / "brain.db"
            tpath = Path(td) / "t.jsonl"
            tpath.write_text(REAL_TRANSCRIPT)
            env["SECONDBRAIN_LOGS_DIR"] = str(logs)
            env["SECONDBRAIN_DB"] = str(db)
            subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps({"hook_event_name": "Stop", "session_id": "s5",
                                  "transcript_path": str(tpath)}),
                capture_output=True, text=True, env=env,
            )
            self.assertFalse(db.exists(), "capture hook must not create brain.db")

    def test_exits_zero_on_garbage(self):
        env = dict(os.environ)
        for bad in ("", "}{not json", '{"hook_event_name":"Stop"}'):
            proc = subprocess.run([sys.executable, str(HOOK)],
                                  input=bad, capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
