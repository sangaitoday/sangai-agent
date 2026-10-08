"""Run the local `claude` CLI asynchronously and extract its answer."""
from __future__ import annotations

import asyncio
import json
from typing import Tuple

from .config import Config


async def run(
    session_id: str,
    is_new: bool,
    prompt: str,
    config: Config,
) -> Tuple[bool, str, str]:
    """Invoke `claude --print …` with the prompt on stdin.

    Returns (ok, stdout, stderr). First message in a thread creates the session
    with --session-id; follow-ups --resume it.
    """
    session_flag = ["--session-id", session_id] if is_new else ["--resume", session_id]
    argv = [
        config.claude_bin,
        "--print",
        *session_flag,
        "--permission-mode", config.permission_mode,
        "--add-dir", str(config.add_dir),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(config.add_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return False, "", f"claude binary not found ({config.claude_bin!r}): {exc}"
    except Exception as exc:  # pragma: no cover — defensive
        return False, "", f"failed to spawn claude: {exc}"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")),
            timeout=config.timeout_s,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return False, "", f"claude timed out after {config.timeout_s}s"

    stdout = (stdout_b or b"").decode("utf-8", "replace").strip()
    stderr = (stderr_b or b"").decode("utf-8", "replace").strip()

    if proc.returncode != 0:
        return False, stdout, stderr or f"non-zero exit ({proc.returncode})"
    return True, stdout, stderr


def extract_result_text(stdout: str) -> str:
    """Return the answer text.

    We don't pass --output-format json, so stdout is usually plain text — return
    it as-is. If a JSON object slips through, pull out result/text/content.
    """
    stripped = stdout.strip()
    if not stripped:
        return ""
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return stripped
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(payload, dict):
        for key in ("result", "text", "content"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return stripped
