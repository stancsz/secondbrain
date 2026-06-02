#!/usr/bin/env python3
"""Tests for the proactive-recall hook (Mode 2).

The hook runs on every UserPromptSubmit, so it must be fast, must never
block a prompt (always exit 0), must turn arbitrary prose into a safe FTS
query, and must never surface raw conversation transcripts.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
import recall_memories as recall  # noqa: E402
from brain import SecondBrain  # noqa: E402

HOOK = HOOKS_DIR / "recall_memories.py"


class TestBuildFtsQuery(unittest.TestCase):
    def test_strips_punctuation_and_operators(self):
        # Raw prompt has chars that would break FTS5 MATCH; result must be safe.
        q = recall._build_fts_query("What's the deal with the checkout-service? (broken!)")
        self.assertIsNotNone(q)
        self.assertNotIn("(", q)
        self.assertNotIn("?", q)
        self.assertNotIn("-", q)
        # Every term is double-quoted (literal) and OR-joined.
        self.assertIn('"checkout"', q)
        self.assertIn(" OR ", q)

    def test_drops_stopwords_and_short_tokens(self):
        q = recall._build_fts_query("what is the to a in on of")
        self.assertIsNone(q)  # nothing meaningful survives

    def test_low_signal_prompt_returns_none(self):
        self.assertIsNone(recall._build_fts_query("ok thanks!"))
        self.assertIsNone(recall._build_fts_query(""))

    def test_dedupes_terms(self):
        q = recall._build_fts_query("redis redis redis caching")
        self.assertEqual(q.count('"redis"'), 1)

    def test_caps_term_count(self):
        prompt = " ".join(f"term{i}" for i in range(50))
        q = recall._build_fts_query(prompt, max_terms=10)
        self.assertEqual(q.count(" OR ") + 1, 10)

    def test_resulting_query_is_valid_fts(self):
        # The produced query must actually run against FTS5 without error.
        with tempfile.TemporaryDirectory() as td:
            b = SecondBrain(Path(td) / "b.db")
            b.add("Checkout timeout", "raised gateway timeout to 30s")
            q = recall._build_fts_query("why is checkout timing out?? (urgent)")
            hits = b.search(q, limit=5)  # must not raise
            self.assertTrue(any("Checkout" in h["title"] for h in hits))
            b.close()


class TestRecall(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.db = str(Path(self.td) / "brain.db")
        b = SecondBrain(self.db)
        b.add("Checkout service timeout fix",
              "raised API gateway timeout to 30s after the incident",
              collection="Engineering", tags=["checkout", "incident"])
        b.add("Redis caching layer",
              "session keys TTL 300s, invalidate on logout",
              collection="Engineering", tags=["redis"])
        # A raw transcript in the Conversations collection — must be excluded.
        b.add("2026-01-01 chat",
              '{"type":"user","message":"checkout incident postmortem"}',
              collection="Conversations", tags=["auto-capture"])
        b.close()

    def test_recalls_relevant_drawer(self):
        out = recall._recall("checkout timeout problem", self.db, limit=5)
        titles = [d["title"] for d in out]
        self.assertIn("Checkout service timeout fix", titles)

    def test_excludes_conversations_collection(self):
        # "incident" appears in both the Engineering note and the transcript;
        # the transcript must never be surfaced.
        out = recall._recall("incident postmortem", self.db, limit=5)
        colls = {d.get("collection") for d in out}
        self.assertNotIn("Conversations", colls)

    def test_irrelevant_prompt_returns_empty(self):
        self.assertEqual(recall._recall("haiku about the ocean", self.db, limit=5), [])

    def test_low_signal_prompt_returns_empty(self):
        self.assertEqual(recall._recall("ok thanks", self.db, limit=5), [])

    def test_limit_is_honored(self):
        out = recall._recall("engineering redis checkout incident session", self.db, limit=1)
        self.assertLessEqual(len(out), 1)

    def test_only_collection_can_include_conversations(self):
        # If explicitly scoped to Conversations, the exclusion is bypassed.
        out = recall._recall("checkout", self.db, limit=5, only_collection="Conversations")
        self.assertTrue(all(d["collection"] == "Conversations" for d in out))


class TestHookNeverBlocks(unittest.TestCase):
    """Subprocess-level guarantee: every input exits 0."""

    def _run(self, stdin_text, env_extra=None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=stdin_text, capture_output=True, text=True, env=env,
        )

    def test_empty_stdin(self):
        self.assertEqual(self._run("").returncode, 0)

    def test_garbage_stdin(self):
        self.assertEqual(self._run("}{not json").returncode, 0)

    def test_missing_prompt(self):
        self.assertEqual(self._run('{"hook_event_name":"UserPromptSubmit"}').returncode, 0)

    def test_skip_switch_silences_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "b.db")
            b = SecondBrain(db); b.add("Checkout timeout", "gateway 30s"); b.close()
            proc = self._run('{"prompt":"checkout timeout"}',
                             {"SECONDBRAIN_DB": db, "SECONDBRAIN_SKIP_RECALL": "1"})
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), "")

    def test_relevant_prompt_injects_context(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "b.db")
            b = SecondBrain(db)
            b.add("Checkout timeout fix", "raised gateway timeout to 30s",
                  collection="Engineering")
            b.close()
            proc = self._run('{"prompt":"why does checkout keep timing out?"}',
                             {"SECONDBRAIN_DB": db})
            self.assertEqual(proc.returncode, 0)
            self.assertIn("secondbrain", proc.stdout)
            self.assertIn("Checkout timeout fix", proc.stdout)


if __name__ == "__main__":
    unittest.main()
