"""Entrypoint: `python -m bridge`.

Loads config from the environment (optionally from a sibling .env), opens the
Slack Socket Mode connection, and serves @-mentions until interrupted.
"""
from __future__ import annotations

import asyncio
import atexit
import errno
import logging
import os
import re
import signal
from pathlib import Path

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .config import REPO_ROOT, Config
from .slack_app import build_app, build_web_client

PID_PATH = REPO_ROOT / "data" / "bridge.pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM  # exists but not ours
    return True


def _acquire_singleton_lock() -> None:
    """Refuse to start if another bridge is already running.

    Two instances on the same Slack app token make Slack split Socket Mode
    events between them at random — duplicate/dropped replies. A pidfile guard
    makes a stray second `python -m bridge` fail loudly instead.
    """
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PID_PATH.exists():
        try:
            old = int(PID_PATH.read_text().strip())
        except (ValueError, OSError):
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            raise SystemExit(
                f"bridge already running (pid {old}). Refusing to start a second "
                f"instance — they would split Slack events. Stop it first, or remove "
                f"{PID_PATH} if it's stale."
            )
    PID_PATH.write_text(str(os.getpid()))
    atexit.register(_release_singleton_lock)


def _release_singleton_lock() -> None:
    try:
        if PID_PATH.exists() and PID_PATH.read_text().strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


def _parse_value(raw: str) -> str:
    """Quoted values are taken verbatim; bare values drop a trailing ` # comment`."""
    val = raw.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return re.split(r"\s+#", val, maxsplit=1)[0].strip()


def _load_dotenv(env_path: Path = REPO_ROOT / ".env") -> None:
    """Minimal .env loader (no external dep). Skips keys already in the env."""
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _parse_value(val)


async def _serve() -> None:
    config = Config.from_env()
    # One SSL-aware Web API client, shared by the app (chat.postMessage, status)
    # and the socket-mode handler (apps.connections.open + the websocket).
    web_client = build_web_client(config)
    app = build_app(config, web_client=web_client)
    handler = AsyncSocketModeHandler(app, config.slack_app_token, web_client=web_client)

    logging.info("bridge: connecting to Slack (Socket Mode) — cwd=%s", config.add_dir)

    loop = asyncio.get_event_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
        except NotImplementedError:  # pragma: no cover — non-unix
            pass

    await handler.connect_async()
    logging.info("bridge: connected — listening for @-mentions. Ctrl-C to stop.")
    await stop
    logging.info("bridge: shutting down")
    await handler.disconnect_async()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("BRIDGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _load_dotenv()
    _acquire_singleton_lock()
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    finally:
        _release_singleton_lock()


if __name__ == "__main__":
    main()
