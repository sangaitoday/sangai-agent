"""Standalone Slack ↔ Claude Code bridge.

A self-contained async process that connects to Slack via Socket Mode, delegates
each @-mention to the local `claude` CLI (with a stable per-thread session UUID),
and posts the answer back to the originating Slack thread.

Fully self-contained — no dependency on any other repo or service; everything it
needs lives here.
"""

__all__ = ["config", "session_store", "claude_runner", "slack_app"]
