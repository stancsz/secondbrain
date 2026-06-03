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


def _user_row(text: str) -> str:
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    })


# 8 user-prompt turns, ~1700 user-text chars, with one "let's go with" decision
# marker and one "remember this" marker. Long enough to clear the smart
# trigger (chars + turns) and to produce candidates.
LONG_TRANSCRIPT_WITH_MARKERS = "\n".join([
    _user_row(
        "I want to remember this for later: the secondbrain install.sh has a "
        "Windows-specific encoding bug that crashes on the first emoji print "
        "with UnicodeEncodeError on the cp1252 codec, so the install appears "
        "to succeed but the settings.json file is never actually written."
    ),
    _user_row(
        "Specifically the embedded Python heredoc prints a checkmark via "
        "print() and the Windows default stdout encoding is cp1252, which "
        "cannot encode the U+2705 character and raises mid-execution."
    ),
    _user_row(
        "Workaround for users on Windows: prepend PYTHONIOENCODING=utf-8 "
        "to the install command, or set it inside the script with an "
        "export at the top of install.sh before the heredoc runs."
    ),
    _user_row(
        "But there's also a separate silent-no-write issue on the first run "
        "where the heredoc reports success but settings.json mtime proves "
        "the json.dump never lands; running the same logic directly works."
    ),
    _user_row(
        "Let's go with the heuristic pre-extraction fix combined with a "
        "smarter trigger that skips the block for short or marker-free "
        "sessions; this is the recommendation we agreed on."
    ),
    _user_row(
        "What about the long-session override where we skip the marker "
        "check entirely for sessions above LONG_SESSION_TURNS turns? Add "
        "a test that covers both the override-on and override-off cases."
    ),
    _user_row(
        "I prefer terse error messages and the kind field for candidates "
        "should be one of decision, preference, remember, fact, wikilink, "
        "or todo so the agent can filter quickly without re-reading the log."
    ),
    _user_row(
        "From now on, all print statements in hooks should use ASCII or "
        "set UTF-8 explicitly to avoid this class of bug on Windows hosts; "
        "OK let's wrap this up, save the changes, and push a PR for review."
    ),
]) + "\n"


# 25 user-prompt turns, ~2000 user-text chars, no decision/remember/remember
# markers. With default LONG_SESSION_TURNS=20 the smart trigger should
# override and block (turns > 20); with LONG_SESSION_TURNS=999 it should
# skip the block.
def _build_no_marker_transcript(n_turns: int = 25) -> str:
    rows = []
    for i in range(n_turns):
        rows.append(_user_row(
            f"This is turn number {i + 1} of a long philosophical discussion "
            f"about knowledge graphs and how they relate to long-term memory. "
            f"There are no durable-knowledge markers in this line by design."
        ))
    return "\n".join(rows) + "\n"


LONG_TRANSCRIPT_NO_MARKERS = _build_no_marker_transcript(25)


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
    def test_reason_mentions_distill(self):
        log_path = Path("logs") / "x.jsonl"
        reason = cap._distill_reason(log_path)
        self.assertIn("distill", reason.lower())
        self.assertIn(str(log_path), reason)

    def test_reason_without_candidates_has_no_candidates_section(self):
        reason = cap._distill_reason(Path("logs/x.jsonl"), candidates=None)
        self.assertNotIn("Candidates", reason)

    def test_reason_drops_candidates_even_when_provided(self):
        # The reason must NEVER include a Candidates section, even when the
        # hook computed some. The agent reads the log if it wants context.
        cands = [
            {"kind": "decision", "text": "going with the heuristic fix",
             "prev_line": "should we A or B?", "line_no": 42},
        ]
        reason = cap._distill_reason(Path("logs/x.jsonl"), candidates=cands)
        self.assertNotIn("Candidates", reason)
        self.assertNotIn("going with the heuristic fix", reason)
        self.assertNotIn("[decision]", reason)

    def test_reason_without_log_path_omits_log_line(self):
        reason = cap._distill_reason(None)
        self.assertNotIn("Log:", reason)

    def test_reason_with_log_path_includes_path(self):
        log_path = Path("logs/2026-06-02__abc.jsonl")
        reason = cap._distill_reason(log_path)
        self.assertIn(str(log_path), reason)

    def test_reason_is_terse(self):
        # Sanity cap: the reason itself (no candidates) must fit in a few lines.
        # If this assertion fires, the hook is going verbose again.
        reason = cap._distill_reason(Path("logs/x.jsonl"))
        self.assertLessEqual(reason.count("\n"), 5,
                             "distill reason must stay terse (<=5 newlines, no candidates)")


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
        # Use a long, marker-rich transcript so the smart trigger fires.
        proc, logs = self._run(
            {"hook_event_name": "Stop", "session_id": "s1"},
            transcript=LONG_TRANSCRIPT_WITH_MARKERS,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 1)  # log written
        out = json.loads(proc.stdout)   # block decision emitted
        self.assertEqual(out["decision"], "block")
        self.assertIn("distill", out["reason"].lower())
        # The terse reason no longer surfaces candidates in the block output.
        self.assertNotIn("Candidates", out["reason"])

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

    def test_stop_block_reason_is_one_line(self):
        # The block reason must fit in one line — verbosity is the regression
        # we keep tripping on, so pin it here. Anything with a newline is too
        # much surface area for the agent's context.
        proc, _ = self._run(
            {"hook_event_name": "Stop", "session_id": "terse"},
            transcript=LONG_TRANSCRIPT_WITH_MARKERS,
        )
        self.assertEqual(proc.returncode, 0)
        reason = json.loads(proc.stdout)["reason"]
        self.assertNotIn("\n", reason,
                         f"distill reason must be a single line, got: {reason!r}")


class TestSmartTrigger(unittest.TestCase):
    """The smart trigger decides when to block the Stop to distill.

    Each test runs the hook as a subprocess so we exercise the same code
    path Claude Code actually invokes."""

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

    def test_short_transcript_skips_block_but_writes_log(self):
        proc, logs = self._run({"hook_event_name": "Stop", "session_id": "t1"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")  # no block
        self.assertEqual(len(logs), 1)             # log still written

    def test_no_markers_default_long_session_skips_block(self):
        # 15 turns of no-marker text, with default LONG_SESSION_TURNS=20
        # → still under the long-session override → no block.
        proc, logs = self._run(
            {"hook_event_name": "Stop", "session_id": "t2"},
            transcript=_build_no_marker_transcript(15),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(len(logs), 1)

    def test_long_session_blocks_without_marker(self):
        # 25 turns no marker, default LONG_SESSION_TURNS=20 → override → block.
        proc, logs = self._run(
            {"hook_event_name": "Stop", "session_id": "t3"},
            transcript=LONG_TRANSCRIPT_NO_MARKERS,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(logs), 1)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "block")
        # No markers → no Candidates section in the prompt.
        self.assertNotIn("Candidates", out["reason"])

    def test_long_session_override_can_be_disabled(self):
        # Same transcript, but with LONG_SESSION_TURNS=999 → override off → no block.
        proc, _ = self._run(
            {"hook_event_name": "Stop", "session_id": "t4"},
            env_extra={"SECONDBRAIN_LONG_SESSION_TURNS": "999"},
            transcript=LONG_TRANSCRIPT_NO_MARKERS,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_precompact_never_blocks_even_with_long_marker_rich_transcript(self):
        # PreCompact must NEVER block, regardless of trigger conditions.
        proc, logs = self._run(
            {"hook_event_name": "PreCompact", "session_id": "t5"},
            transcript=LONG_TRANSCRIPT_WITH_MARKERS,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(len(logs), 1)

    def test_skip_distill_env_short_circuits_before_smart_trigger(self):
        # SECONDBRAIN_SKIP_DISTILL=1 must win over a long marker-rich transcript.
        proc, _ = self._run(
            {"hook_event_name": "Stop", "session_id": "t6"},
            env_extra={"SECONDBRAIN_SKIP_DISTILL": "1"},
            transcript=LONG_TRANSCRIPT_WITH_MARKERS,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


class TestCandidates(unittest.TestCase):
    """The candidate extractor surfaces pre-filtered lines for the agent."""

    def test_user_text_only_excludes_assistant_and_tool_rows(self):
        # Mix in assistant rows containing marker words; they must not appear.
        mixed = (
            json.dumps({"type": "assistant",
                        "message": {"role": "assistant",
                                    "content": [{"type": "text",
                                                 "text": "decided: use the heuristic"}]}}) + "\n"
            + _user_row("remember this: the install.sh bug on Windows is real") + "\n"
            + json.dumps({"type": "user",
                        "message": {"content": [{"type": "tool_result",
                                                 "tool_use_id": "x",
                                                 "content": "TODO: re-run install"}]}}) + "\n"
            + _user_row("this is a normal follow-up turn with enough words "
                        "to push the count above the threshold for testing") + "\n"
        )
        cands = cap._extract_candidates(mixed)
        kinds = [c["kind"] for c in cands]
        texts = [c["text"] for c in cands]
        # The "decided" came from the assistant row → must not appear.
        self.assertNotIn("decided: use the heuristic", texts)
        # The "TODO" came from a tool_result row → must not appear.
        self.assertNotIn("TODO: re-run install", texts)
        # The "remember" came from a real user row → must appear.
        self.assertIn("remember this: the install.sh bug on Windows is real", texts)

    def test_candidate_has_prev_line(self):
        # First user row: prev_line is "". Second user row: prev_line is the first.
        rows = [
            _user_row("should we A or B?"),
            _user_row("let's go with A"),
        ]
        cands = cap._extract_candidates("\n".join(rows) + "\n")
        matched = [c for c in cands if c["text"] == "let's go with A"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["prev_line"], "should we A or B?")
        self.assertEqual(matched[0]["kind"], "decision")

    def test_first_candidate_has_empty_prev_line(self):
        # When the matching line is the very first user row, prev_line is "".
        rows = [_user_row("going with the heuristic fix is the right call")]
        cands = cap._extract_candidates("\n".join(rows) + "\n")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["prev_line"], "")

    def test_dedup_by_kind_and_text(self):
        # Same line repeated 5 times → only one candidate.
        rows = [_user_row("let's go with the heuristic fix")] * 5
        cands = cap._extract_candidates("\n".join(rows) + "\n")
        self.assertEqual(len(cands), 1)

    def test_cap_at_max_candidates(self):
        # 30 unique matching lines → cap kicks in at MAX_CANDIDATES.
        rows = [
            _user_row(f"let's go with option number {i} for the test")
            for i in range(30)
        ]
        cands = cap._extract_candidates("\n".join(rows) + "\n")
        self.assertEqual(len(cands), cap.MAX_CANDIDATES)

    def test_one_marker_per_line(self):
        # A line that matches both "decision" and "preference" only emits one.
        rows = [_user_row("I prefer going with the heuristic fix")]
        cands = cap._extract_candidates("\n".join(rows) + "\n")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], "decision")  # first match wins

    def test_should_distill_short_transcript(self):
        ok, why = cap._should_distill(REAL_TRANSCRIPT)
        self.assertFalse(ok)
        self.assertIn("user_text too short", why)

    def test_should_distill_no_markers_under_long_threshold(self):
        ok, why = cap._should_distill(_build_no_marker_transcript(10))
        self.assertFalse(ok)
        self.assertIn("no durable-knowledge marker", why)

    def test_should_distill_markers_present(self):
        ok, why = cap._should_distill(LONG_TRANSCRIPT_WITH_MARKERS)
        self.assertTrue(ok)
        self.assertEqual(why, "ok")

    def test_should_distill_long_session_overrides_marker(self):
        ok, why = cap._should_distill(LONG_TRANSCRIPT_NO_MARKERS)
        self.assertTrue(ok)
        self.assertEqual(why, "ok")

    def test_tool_result_row_with_user_role_is_excluded(self):
        # Regression: real Claude Code transcripts wrap tool_result blocks in
        # role="user" messages. If we only check role, the entire file content
        # returned by Read/Bash leaks in as a "user prompt" and the candidate
        # list fills with multi-page file dumps. The filter must reject any
        # row whose content contains a tool_result/tool_use block.
        tool_result_row = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "x",
                "content": "decided to save this. TODO fix. [[wikilink]] " * 100,
            }]},
        })
        real_user_row = _user_row(
            "remember this: the real durable knowledge from the session"
        )
        mixed = tool_result_row + "\n" + real_user_row + "\n"
        cands = cap._extract_candidates(mixed)
        texts = [c["text"] for c in cands]
        # Tool-result content must not appear.
        self.assertFalse(any("[[wikilink]]" in t for t in texts),
                         f"tool_result text leaked into candidates: {texts}")
        # Real user line still surfaces.
        self.assertTrue(any("real durable knowledge" in t for t in texts))

    def test_candidate_text_is_clipped(self):
        # A 5000-char pasted line must not produce a 5000-char candidate.
        long_text = "let's go with " + ("X" * 5000)
        rows = _user_row(long_text)
        cands = cap._extract_candidates(rows + "\n")
        self.assertEqual(len(cands), 1)
        self.assertLessEqual(len(cands[0]["text"]), cap.CANDIDATE_TEXT_MAX,
                             "candidate text must respect CANDIDATE_TEXT_MAX")


# A real Claude Code Stop-hook payload that triggered the original
# tool-result-leak bug in production on 2026-06-02: the user asked a
# single substantive question, then Claude used Read/Bash tools whose
# results contained marker words ("decision", "TODO", "[[wikilink]]")
# inside multi-KB file dumps. The buggy filter saw every tool_result
# row as a user prompt and surfaced whole files as `[decision]` candidates
# with prev_line = "(Bash completed with no output)".
#
# The shape below mirrors what was on disk: msg.role == "user" with
# content = [{"type": "tool_result", "content": "<file dump>"}].
def _tool_result_row(payload_text: str, tool_use_id: str = "toolu_x") -> str:
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": payload_text,
        }]},
    })


def _assistant_tool_use_row(tool_use_id: str = "toolu_x") -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{
            "type": "tool_use", "id": tool_use_id, "name": "Read",
            "input": {"file_path": "SKILL.md"},
        }]},
    })


# Multi-KB simulated file dump that contains every marker word the hook
# regex looks for, exactly as a Read of SKILL.md would.
LEAKY_FILE_DUMP = (
    "Trigger that warrants an `add`: decision, decided, going with, "
    "let's go with, I prefer, I always, I never, from now on, remember "
    "this, save this, note that, I'm working on, my project is, "
    "[[wikilink]] [[Another Title]] TODO FIXME XXX. " * 60
)


class TestStopHookProductionShape(unittest.TestCase):
    """End-to-end regression for the 2026-06-02 tool-result-leak bug.

    The transcript shape, marker leak, and verbosity blow-up that prompted
    this test all came from a real Stop-hook payload. If any of these asserts
    fire again, the hook is regressing the user's '≤1 line per event' rule."""

    def _build_real_shape_transcript(self) -> str:
        # 8 real user prompts (~1700 chars) interleaved with assistant
        # tool_use + user tool_result rows whose content is a multi-KB dump
        # containing decision/TODO/wikilink markers.
        rows = []
        rows.append(_user_row(
            "I want to remember this for later: the secondbrain install.sh has "
            "a Windows-specific encoding bug. The fix is to set "
            "PYTHONIOENCODING=utf-8 in install.sh before the heredoc runs."
        ))
        rows.append(_assistant_tool_use_row("t1"))
        rows.append(_tool_result_row(LEAKY_FILE_DUMP, "t1"))
        rows.append(_user_row(
            "Specifically the embedded Python heredoc prints a checkmark via "
            "print() and the Windows default stdout encoding is cp1252, which "
            "cannot encode the U+2705 character and raises mid-execution."
        ))
        rows.append(_assistant_tool_use_row("t2"))
        rows.append(_tool_result_row(LEAKY_FILE_DUMP, "t2"))
        rows.append(_user_row(
            "Let's go with the heuristic pre-extraction fix combined with a "
            "smarter trigger that skips the block for short or marker-free "
            "sessions; this is the recommendation we agreed on."
        ))
        rows.append(_assistant_tool_use_row("t3"))
        rows.append(_tool_result_row(LEAKY_FILE_DUMP, "t3"))
        rows.append(_user_row(
            "I prefer terse error messages and the kind field for candidates "
            "should be one of decision, preference, remember, fact, wikilink, "
            "or todo so the agent can filter quickly without re-reading the log."
        ))
        rows.append(_user_row(
            "From now on, all print statements in hooks should use ASCII or "
            "set UTF-8 explicitly to avoid this class of bug on Windows; "
            "OK let's wrap this up, save the changes, and push a PR."
        ))
        rows.append(_user_row(
            "What about the long-session override where we skip the marker "
            "check entirely for sessions above LONG_SESSION_TURNS turns? Add "
            "a test that covers both the override-on and override-off cases, "
            "and make sure the chars and turns gates still pass independently "
            "so we don't accidentally weaken the smart trigger on real sessions."
        ))
        rows.append(_user_row(
            "Workaround for users on Windows: prepend PYTHONIOENCODING=utf-8 "
            "to the install command, or set it inside the script directly."
        ))
        rows.append(_user_row(
            "But there's also a separate silent-no-write issue on the first "
            "run where the heredoc reports success but settings.json mtime "
            "proves the json.dump never lands; running the same logic "
            "directly outside the heredoc works fine, so it's something "
            "about the way the embedded interpreter handles stdout flushing."
        ))
        return "\n".join(rows) + "\n"

    def _run_hook(self, transcript: str):
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            tpath = Path(td) / "t.jsonl"
            tpath.write_text(transcript, encoding="utf-8")
            env["SECONDBRAIN_LOGS_DIR"] = str(logs)
            proc = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps({
                    "hook_event_name": "Stop", "session_id": "leakbug",
                    "transcript_path": str(tpath),
                }),
                capture_output=True, text=True, env=env,
            )
            return proc

    def test_no_file_dump_leaks_into_reason(self):
        proc = self._run_hook(self._build_real_shape_transcript())
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "block")
        reason = out["reason"]
        # The 60x-repeated marker phrase from the file dump must never appear
        # in the candidate list. If it does, the tool_result filter regressed.
        leaked = LEAKY_FILE_DUMP[:50]
        self.assertNotIn(leaked, reason,
                         "tool_result content leaked into the distill reason")

    def test_reason_size_is_bounded(self):
        # Even with 13 user turns + 3 multi-KB tool results, the reason the
        # hook sends back must stay terse (≤1 line per event rule). 4 KB is
        # the budget; without the fix, the original bug emitted ~30 KB.
        proc = self._run_hook(self._build_real_shape_transcript())
        self.assertEqual(proc.returncode, 0)
        reason = json.loads(proc.stdout)["reason"]
        self.assertLessEqual(len(reason), 4096,
                             f"distill reason ballooned to {len(reason)} chars "
                             "(budget 4096); the verbosity rule has regressed")

    def test_candidates_function_still_filters_tool_results(self):
        # The candidates function continues to exist and stays correct; the
        # terse reason just no longer surfaces its output. Run the function
        # directly to assert the leak stays fixed.
        cands = cap._extract_candidates(self._build_real_shape_transcript())
        texts = [c["text"] for c in cands]
        # Real user lines surface.
        self.assertTrue(any("remember this for later" in t for t in texts),
                        f"real user text missing from candidates: {texts}")
        # Tool_result file dumps do not.
        self.assertFalse(any("(Bash completed" in (c.get("prev_line", "") or "")
                             for c in cands),
                           f"tool_result prev_line leaked: {[c.get('prev_line') for c in cands]}")
        # The 60x-repeated marker phrase from the file dump is bounded.
        for c in cands:
            self.assertLessEqual(len(c["text"]), cap.CANDIDATE_TEXT_MAX)


if __name__ == "__main__":
    unittest.main()
