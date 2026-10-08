"""Unit tests for the bridge. Run: python -m unittest discover -s tests -v"""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import __main__ as entry  # noqa: E402
from bridge import claude_runner, unauthorized  # noqa: E402
from bridge.config import Config  # noqa: E402
from bridge.session_store import SessionStore, build_session_key  # noqa: E402
from bridge.slack_app import MAX_MESSAGE_LENGTH, _chunk, _strip_mention  # noqa: E402


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data" / "cc-sessions.json"
        self.store = SessionStore(self.path, continuation_window_s=100.0)

    def test_first_resolve_mints_then_reuses(self) -> None:
        key = build_session_key("C1", "1700000000.000100")
        sid, is_new = self.store.resolve_session_id(key)
        self.assertTrue(is_new)
        again, is_new_again = self.store.resolve_session_id(key)
        self.assertFalse(is_new_again)
        self.assertEqual(sid, again)

    def test_distinct_threads_get_distinct_sessions(self) -> None:
        a, _ = self.store.resolve_session_id(build_session_key("C1", "1.0"))
        b, _ = self.store.resolve_session_id(build_session_key("C1", "2.0"))
        self.assertNotEqual(a, b)

    def test_corrupt_map_is_treated_as_empty(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        _, is_new = self.store.resolve_session_id("slack:C1:1.0")
        self.assertTrue(is_new)

    def test_save_leaves_no_temp_file(self) -> None:
        self.store.resolve_session_id("slack:C1:1.0")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["cc-sessions.json"])

    def test_recent_channel_session_picks_newest_in_window(self) -> None:
        self.store.resolve_session_id(build_session_key("C1", "1000.0"))
        self.store.resolve_session_id(build_session_key("C1", "1050.0"))
        self.store.resolve_session_id(build_session_key("C2", "1090.0"))
        found = self.store.find_recent_channel_session("C1", now=1100.0)
        self.assertEqual(found, "slack:C1:1050.0")

    def test_recent_channel_session_ignores_stale_and_other_channels(self) -> None:
        self.store.resolve_session_id(build_session_key("C1", "1000.0"))
        self.assertIsNone(self.store.find_recent_channel_session("C1", now=5000.0))
        self.assertIsNone(self.store.find_recent_channel_session("C9", now=1001.0))

    def test_recent_channel_session_skips_unparseable_keys(self) -> None:
        self.store.resolve_session_id("slack:C1:not-a-number")
        self.assertIsNone(self.store.find_recent_channel_session("C1", now=1.0))


class SlackHelpersTest(unittest.TestCase):
    def test_strip_mention_removes_only_the_first(self) -> None:
        self.assertEqual(_strip_mention("<@U123ABC> hello <@U999>"), "hello <@U999>")

    def test_strip_mention_handles_empty(self) -> None:
        self.assertEqual(_strip_mention(""), "")
        self.assertEqual(_strip_mention("<@U123ABC>"), "")

    def test_chunk_short_text_is_one_piece(self) -> None:
        self.assertEqual(_chunk("hi"), ["hi"])

    def test_chunk_splits_under_the_limit_and_loses_nothing(self) -> None:
        text = "x" * (MAX_MESSAGE_LENGTH * 2 + 5)
        parts = _chunk(text)
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= MAX_MESSAGE_LENGTH for p in parts))
        self.assertEqual("".join(parts), text)


class ExtractResultTextTest(unittest.TestCase):
    def test_plain_text_passes_through(self) -> None:
        self.assertEqual(claude_runner.extract_result_text("  hello \n"), "hello")

    def test_empty(self) -> None:
        self.assertEqual(claude_runner.extract_result_text("   "), "")

    def test_json_result_field_is_unwrapped(self) -> None:
        self.assertEqual(claude_runner.extract_result_text('{"result": "the answer"}'), "the answer")

    def test_json_without_known_field_is_returned_as_is(self) -> None:
        raw = '{"other": 1}'
        self.assertEqual(claude_runner.extract_result_text(raw), raw)

    def test_malformed_json_is_returned_as_is(self) -> None:
        self.assertEqual(claude_runner.extract_result_text("{oops}"), "{oops}")


class UnauthorizedTest(unittest.TestCase):
    def test_every_message_formats_with_user_and_name(self) -> None:
        for template in unauthorized.MESSAGES:
            rendered = template.format(uid="U42", name="Bot")
            self.assertIn("<@U42>", rendered)
            self.assertNotIn("{", rendered)

    def test_there_are_twenty(self) -> None:
        self.assertEqual(len(unauthorized.MESSAGES), 20)

    def test_pick_addresses_the_user(self) -> None:
        self.assertIn("<@U42>", unauthorized.pick("U42", "Bot"))

    def test_pick_without_user_falls_back(self) -> None:
        self.assertIn("<@there>", unauthorized.pick("", "Bot"))


class ConfigTest(unittest.TestCase):
    def _env(self, **extra: str) -> dict:
        base = {"SLACK_APP_TOKEN": "xapp-1", "SLACK_BOT_TOKEN": "xoxb-1"}
        base.update(extra)
        return base

    def test_defaults(self) -> None:
        with mock.patch.dict(os.environ, self._env(), clear=True):
            cfg = Config.from_env()
        self.assertEqual(cfg.bot_name, "SangAI")
        self.assertEqual(cfg.claude_bin, "claude")
        self.assertEqual(cfg.permission_mode, "acceptEdits")
        self.assertEqual(cfg.timeout_s, 600.0)
        self.assertEqual(cfg.allowed_user_ids, frozenset())

    def test_overrides_and_allowlist_parsing(self) -> None:
        env = self._env(
            BOT_NAME="Helper",
            CC_PERMISSION_MODE="plan",
            CC_AGENT_TIMEOUT_S="30",
            SLACK_ALLOWED_USERS=" U1, U2 ,,U3 ",
        )
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        self.assertEqual(cfg.bot_name, "Helper")
        self.assertEqual(cfg.permission_mode, "plan")
        self.assertEqual(cfg.timeout_s, 30.0)
        self.assertEqual(cfg.allowed_user_ids, frozenset({"U1", "U2", "U3"}))

    def test_missing_app_token_exits(self) -> None:
        with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-1"}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                Config.from_env()
        self.assertIn("SLACK_APP_TOKEN", str(ctx.exception))

    def test_missing_bot_token_exits(self) -> None:
        with mock.patch.dict(os.environ, {"SLACK_APP_TOKEN": "xapp-1"}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                Config.from_env()
        self.assertIn("SLACK_BOT_TOKEN", str(ctx.exception))


class DotenvTest(unittest.TestCase):
    def _load(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text)
            with mock.patch.dict(os.environ, {}, clear=True):
                entry._load_dotenv(path)
                return dict(os.environ)

    def test_plain_and_quoted_values(self) -> None:
        env = self._load('A=1\nB="two words"\nC=\'x\'\n')
        self.assertEqual((env["A"], env["B"], env["C"]), ("1", "two words", "x"))

    def test_inline_comment_is_stripped(self) -> None:
        env = self._load("BOT_NAME=SangAI     # display name\nTOKEN=xapp-1#frag\n")
        self.assertEqual(env["BOT_NAME"], "SangAI")
        self.assertEqual(env["TOKEN"], "xapp-1#frag")

    def test_hash_inside_quotes_is_kept(self) -> None:
        self.assertEqual(self._load('A="x # y"\n')["A"], "x # y")

    def test_comment_lines_and_existing_env_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# c\nKEEP=file\nNEW=1\n")
            with mock.patch.dict(os.environ, {"KEEP": "shell"}, clear=True):
                entry._load_dotenv(path)
                self.assertEqual((os.environ["KEEP"], os.environ["NEW"]), ("shell", "1"))

    def test_missing_file_is_fine(self) -> None:
        entry._load_dotenv(Path("/nonexistent/.env"))


class ClaudeRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _fake_claude(self, body: str) -> Path:
        script = self.dir / "fake-claude"
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def _config(self, claude_bin: str, timeout_s: float = 10.0) -> Config:
        return Config(claude_bin=claude_bin, add_dir=self.dir, timeout_s=timeout_s)

    def _run(self, *args, **kwargs):
        return asyncio.run(claude_runner.run(*args, **kwargs))

    def test_new_session_uses_session_id_and_pipes_prompt(self) -> None:
        fake = self._fake_claude('echo "args: $@"; cat\n')
        ok, out, _ = self._run("uuid-1", True, "the prompt", self._config(str(fake)))
        self.assertTrue(ok)
        self.assertIn("--print", out)
        self.assertIn("--session-id uuid-1", out)
        self.assertNotIn("--resume", out)
        self.assertIn("--permission-mode acceptEdits", out)
        self.assertIn(f"--add-dir {self.dir.resolve()}", out)
        self.assertTrue(out.endswith("the prompt"))

    def test_follow_up_uses_resume(self) -> None:
        fake = self._fake_claude('echo "args: $@"\n')
        ok, out, _ = self._run("uuid-2", False, "x", self._config(str(fake)))
        self.assertTrue(ok)
        self.assertIn("--resume uuid-2", out)
        self.assertNotIn("--session-id", out)

    def test_nonzero_exit_reports_failure_with_stderr(self) -> None:
        fake = self._fake_claude('echo boom >&2; exit 3\n')
        ok, _, err = self._run("u", True, "x", self._config(str(fake)))
        self.assertFalse(ok)
        self.assertEqual(err, "boom")

    def test_missing_binary(self) -> None:
        ok, _, err = self._run("u", True, "x", self._config(str(self.dir / "nope")))
        self.assertFalse(ok)
        self.assertIn("claude binary not found", err)

    def test_timeout_kills_the_process(self) -> None:
        fake = self._fake_claude("sleep 5\n")
        ok, _, err = self._run("u", True, "x", self._config(str(fake), timeout_s=0.2))
        self.assertFalse(ok)
        self.assertIn("timed out", err)


if __name__ == "__main__":
    unittest.main()
