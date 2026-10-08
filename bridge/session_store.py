"""Per-thread Claude Code session map.

Maps a stable Slack session key → a Claude Code session UUID, persisted in a small
JSON file. The first message in a thread mints a UUID (used with `claude
--session-id <uuid>`); follow-ups reuse it (`claude --resume <uuid>`), so a Slack
thread is one continuous Claude conversation.

The key is `slack:<channel>:<thread_ts>`, whose trailing field is the float ts so
`find_recent_channel_session` can parse it for the continuation-window heuristic.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional, Tuple


def build_session_key(channel: str, thread_ts: str) -> str:
    """Stable key for a Slack thread. Trailing field is the float ts."""
    return f"slack:{channel}:{thread_ts}"


class SessionStore:
    """Atomic, file-backed session_key → UUID map."""

    def __init__(self, path: Path, continuation_window_s: float) -> None:
        self.path = path
        self.continuation_window_s = continuation_window_s

    # --- persistence ---

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            print(f"session_store: could not read {self.path}: {exc}", file=sys.stderr)
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.path)

    # --- public API ---

    def resolve_session_id(self, session_key: str) -> Tuple[str, bool]:
        """Return (uuid, is_new) for this key, minting + persisting if absent."""
        data = self._load()
        sid = data.get(session_key)
        if sid:
            return sid, False
        sid = str(uuid.uuid4())
        data[session_key] = sid
        self._save(data)
        return sid, True

    def find_recent_channel_session(self, channel: str, now: float) -> Optional[str]:
        """Most recent session key in this channel within the continuation window.

        Slack `app_mention` events for thread replies sometimes omit `thread_ts`,
        which would fork a new session per reply. We compensate by reusing the
        newest existing key for the same channel if it's recent enough.
        """
        prefix = f"slack:{channel}:"
        best_ts = -1.0
        best_key: Optional[str] = None
        for key in self._load():
            if not key.startswith(prefix):
                continue
            try:
                ts = float(key[len(prefix):])
            except ValueError:
                continue
            if now - ts <= self.continuation_window_s and ts > best_ts:
                best_ts = ts
                best_key = key
        return best_key
