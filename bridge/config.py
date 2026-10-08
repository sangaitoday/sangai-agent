"""Environment-driven configuration for the Slack ↔ Claude Code bridge."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root: bridge/config.py → bridge/ → <repo>/
REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    """All knobs the bridge reads, resolved from the environment once at startup."""

    # --- Identity ---
    bot_name: str = "SangAI"      # the AI's display name, used in bridge replies

    # --- Slack (Socket Mode) ---
    slack_app_token: str = ""     # xapp-… — Socket Mode connection
    slack_bot_token: str = ""     # xoxb-… — Web API (chat.postMessage, status)

    # --- Claude CLI invocation ---
    claude_bin: str = "claude"
    permission_mode: str = "acceptEdits"
    add_dir: Path = REPO_ROOT     # cwd + --add-dir for `claude`
    timeout_s: float = 600.0

    # --- Session map ---
    session_map_path: Path = REPO_ROOT / "data" / "cc-sessions.json"
    continuation_window_s: float = 2 * 3600.0  # reuse a recent channel session

    # --- Access control (optional) ---
    allowed_user_ids: frozenset = field(default_factory=frozenset)

    @classmethod
    def from_env(cls) -> "Config":
        app_token = _env("SLACK_APP_TOKEN")
        bot_token = _env("SLACK_BOT_TOKEN")
        if not app_token:
            raise SystemExit("SLACK_APP_TOKEN is not set (xapp- Socket Mode token).")
        if not bot_token:
            raise SystemExit("SLACK_BOT_TOKEN is not set (xoxb- bot token).")

        add_dir = Path(_env("CC_ADD_DIR") or str(REPO_ROOT)).resolve()

        allowed = {
            u.strip()
            for u in _env("SLACK_ALLOWED_USERS").split(",")
            if u.strip()
        }

        return cls(
            bot_name=_env("BOT_NAME") or "SangAI",
            slack_app_token=app_token,
            slack_bot_token=bot_token,
            claude_bin=_env("CC_CLAUDE_BIN") or "claude",
            permission_mode=_env("CC_PERMISSION_MODE") or "acceptEdits",
            add_dir=add_dir,
            timeout_s=float(_env("CC_AGENT_TIMEOUT_S") or "600"),
            session_map_path=REPO_ROOT / "data" / "cc-sessions.json",
            continuation_window_s=float(
                _env("CC_SESSION_CONTINUATION_WINDOW_S") or str(2 * 3600)
            ),
            allowed_user_ids=frozenset(allowed),
        )
