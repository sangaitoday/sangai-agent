"""Slack Socket Mode app: @-mention → Claude Code → threaded reply."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
import time
from typing import Dict, List, Optional

import certifi
from slack_bolt.app.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from . import claude_runner, unauthorized
from .config import Config
from .session_store import SessionStore, build_session_key

log = logging.getLogger("bridge.slack")

# Slack hard-caps a message at 40k chars; stay under with headroom.
MAX_MESSAGE_LENGTH = 39000
# Mention token at the start of app_mention text, e.g. "<@U123ABC> hello".
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


def _strip_mention(text: str) -> str:
    """Drop the leading bot mention; collapse leftover whitespace."""
    return _MENTION_RE.sub("", text or "", count=1).strip()


def _chunk(text: str, size: int = MAX_MESSAGE_LENGTH) -> List[str]:
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]


def build_web_client(config: Config) -> AsyncWebClient:
    """Bot-token Web API client with an explicit certifi CA bundle.

    The python.org macOS Python doesn't trust the system keychain, so aiohttp's
    default SSL context can't verify slack.com. We build the context from certifi
    and attach it to the client — slack's socket-mode client reuses
    `web_client.ssl` for the websocket too (slack_sdk aiohttp client, line ~382).
    """
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    return AsyncWebClient(token=config.slack_bot_token, ssl=ssl_ctx)


def build_app(config: Config, web_client: Optional[AsyncWebClient] = None) -> AsyncApp:
    # Prevent slack_bolt from switching into multi-workspace OAuth mode when these
    # happen to be present in the environment.
    os.environ.pop("SLACK_CLIENT_ID", None)
    os.environ.pop("SLACK_CLIENT_SECRET", None)

    app = AsyncApp(client=web_client or build_web_client(config))
    store = SessionStore(config.session_map_path, config.continuation_window_s)
    locks: Dict[str, asyncio.Lock] = {}

    async def _set_status(client, channel: str, thread_ts: str, status: str) -> bool:
        try:
            await client.assistant_threads_setStatus(
                channel_id=channel, thread_ts=thread_ts, status=status
            )
            return True
        except Exception:
            return False

    async def _keep_alive(client, channel: str, thread_ts: str) -> None:
        """Refresh the thinking status every 5 s until cancelled."""
        while True:
            await asyncio.sleep(5)
            await _set_status(client, channel, thread_ts, "is thinking...")

    @app.event("app_mention")
    async def handle_mention(event, client, logger):  # noqa: ANN001
        # 1. Ignore other bots (incl. our own messages) outright.
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        user = event.get("user", "")

        # 2. Authorization: only allowlisted users reach Claude. Everyone else
        #    gets a random friendly "not ready yet" reply.
        if config.allowed_user_ids and user not in config.allowed_user_ids:
            log.info("unauthorized mention from user %s — sending friendly reply", user)
            try:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=unauthorized.pick(user, config.bot_name),
                    mrkdwn=True,
                )
            except Exception as exc:
                log.warning("failed to send unauthorized reply: %s", exc)
            return

        # 3. Extract the prompt.
        user_text = _strip_mention(event.get("text", ""))
        if not user_text:
            return

        # 4. Resolve thread + session key.
        session_key = (
            store.find_recent_channel_session(channel, time.time())
            or build_session_key(channel, thread_ts)
        )

        # 5. Serialize messages within the same session/thread.
        lock = locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            await _run_and_reply(client, channel, thread_ts, session_key, user_text)

    async def _run_and_reply(client, channel, thread_ts, session_key, user_text):
        # Show "thinking…", with a keep-alive; fall back to an eyes reaction.
        status_shown = await _set_status(client, channel, thread_ts, "is thinking...")
        keep_alive: Optional[asyncio.Task] = None
        reacted = False
        if status_shown:
            keep_alive = asyncio.create_task(_keep_alive(client, channel, thread_ts))
        else:
            try:
                await client.reactions_add(channel=channel, timestamp=thread_ts, name="eyes")
                reacted = True
            except Exception:
                pass

        session_id, is_new = store.resolve_session_id(session_key)
        log.info(
            "claude %s session_key=%r session_id=%s",
            "new" if is_new else "resume", session_key, session_id,
        )
        ok, stdout, stderr = await claude_runner.run(session_id, is_new, user_text, config)

        if keep_alive is not None:
            keep_alive.cancel()
            try:
                await keep_alive
            except asyncio.CancelledError:
                pass

        async def _clear():
            if status_shown:
                await _set_status(client, channel, thread_ts, "")
            if reacted:
                try:
                    await client.reactions_remove(
                        channel=channel, timestamp=thread_ts, name="eyes"
                    )
                except Exception:
                    pass

        # Never stay silent: post the answer, or a short failure note.
        if not ok:
            tail = (stderr or stdout or "unknown error")[-1500:]
            answer = f"Claude Code failed: {tail}"
            log.error("claude failed: %s", tail)
        else:
            answer = claude_runner.extract_result_text(stdout) or "Claude returned an empty response."

        await _clear()
        for i, chunk in enumerate(_chunk(answer)):
            try:
                await client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts, text=chunk, mrkdwn=True
                )
            except Exception as exc:
                log.error("chat_postMessage failed (chunk %d): %s", i, exc)
                break
        log.info("replied session_key=%r (%d in, %d out)",
                 session_key, len(user_text), len(answer))

    @app.event("message")
    async def _ignore_message(event, logger):  # noqa: ANN001
        # Silence "unhandled event" warnings; we only act on explicit mentions.
        return

    return app
