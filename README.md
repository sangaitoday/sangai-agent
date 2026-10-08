# sangai-agent

A small Slack ↔ [Claude Code](https://code.claude.com) bridge. @-mention the bot in
Slack and it runs the local `claude` CLI, then posts the answer back into the same
thread. Each Slack thread is one persistent Claude session, so a thread is a
continuous conversation.

- Socket Mode: no public URL or webhook, so it runs fine on a laptop.
- About 600 lines of Python (`slack_bolt`, `aiohttp`, `certifi`).
- Access control by Slack user ID; everyone else gets a friendly "not ready yet" reply.
- Runs `claude` with this repo as its working directory, so `CLAUDE.md` and
  `.claude/` are the bot's instructions.

## How it works

```
Slack @bot ──(Socket Mode)──▶ bridge (python -m bridge)
                               1. ignore bots and itself
                               2. authorize (SLACK_ALLOWED_USERS)
                               3. strip "<@BOT>" → prompt
                               4. thread_ts → session key slack:<channel>:<ts>
                               5. per-thread lock, "is thinking…" status
                               6. claude --print --session-id|--resume <uuid>
                                    --permission-mode acceptEdits --add-dir <dir>
                               7. post stdout to the thread (mrkdwn)
```

1. **Mention arrives** as an `app_mention` event over the Socket Mode websocket.
2. **Noise is dropped.** Messages from bots, including the bot's own, are ignored.
3. **Authorize.** If `SLACK_ALLOWED_USERS` is set and the sender is not in it, the
   bridge posts one of 20 random friendly replies and stops. No Claude call.
4. **Resolve the session.** The thread's `thread_ts` (or the message `ts` for a
   top-level mention) gives the session key. A continuation window reuses a recent
   session for the same channel, because Slack sometimes omits `thread_ts` on a
   reply.
5. **Serialize.** A per-session `asyncio.Lock` keeps two quick messages in one
   thread from interleaving.
6. **Show progress.** An "is thinking…" assistant status, refreshed every 5 s. If
   the status API is unavailable, the bridge adds an 👀 reaction instead.
7. **Run Claude.** The first message in a thread starts the session with
   `--session-id`; follow-ups use `--resume`. The session map is written
   atomically.
8. **Reply.** Claude's stdout is posted with `mrkdwn=True`, split into chunks under
   Slack's 40k-character limit. On failure the bridge posts a short
   `Claude Code failed: …` note, so it never goes silent.

## 1. Create the Slack app

You do this once. The app console is at <https://api.slack.com/apps>.

Slack uses two tokens, and they are not interchangeable:

| Token | Prefix | Created on | Env var |
| --- | --- | --- | --- |
| App-level token | `xapp-` | Basic Information → App-Level Tokens (scope `connections:write`) | `SLACK_APP_TOKEN` |
| Bot user OAuth token | `xoxb-` | OAuth & Permissions, after install | `SLACK_BOT_TOKEN` |

### From a manifest (recommended)

**Create New App → From a manifest**, pick the workspace, paste:

```yaml
display_information:
  name: SangAI
  description: Claude Code in Slack
features:
  bot_user:
    display_name: SangAI
    always_online: true
oauth_config:
  scopes:
    bot:
      - app_mentions:read   # receive @mentions
      - chat:write          # post replies
      - reactions:write     # the 👀 fallback status
settings:
  event_subscriptions:
    bot_events:
      - app_mention         # the only event the bridge needs
  socket_mode_enabled: true
  org_deploy_enabled: false
  token_rotation_enabled: false
```

### Or from scratch

1. **Create New App → From scratch.**
2. **OAuth & Permissions → Bot Token Scopes:** `app_mentions:read`, `chat:write`,
   `reactions:write`.
3. **Socket Mode:** enable.
4. **Event Subscriptions:** enable, and subscribe to the bot event `app_mention`.

### Then, for either path

1. **Basic Information → App-Level Tokens → Generate Token and Scopes**, add
   `connections:write`, copy the `xapp-…` value into `SLACK_APP_TOKEN`.
2. **Install to Workspace.** Copy the Bot User OAuth Token (`xoxb-…`) into
   `SLACK_BOT_TOKEN`.
3. **Invite the bot** to each channel it should answer in: `/invite @SangAI`.
4. **Find your Slack user ID** for `SLACK_ALLOWED_USERS`: profile → ⋯ → Copy member ID.

**Optional, the native "is thinking…" status.** Turn on **Agents & AI Apps**, add
the `assistant:write` bot scope, and reinstall. Without it the bridge falls back to
the 👀 reaction and everything else works the same.

After changing scopes, reinstall the app for them to take effect.

## 2. Install Claude Code

The bridge shells out to the `claude` CLI for every mention, so it must be installed
and signed in on the machine that runs the bridge.

```bash
curl -fsSL https://claude.ai/install.sh | bash     # macOS / Linux / WSL
# or: brew install --cask claude-code
# or: npm install -g @anthropic-ai/claude-code
claude --version
claude            # sign in once, interactively
```

If your organization uses API-key auth, export `ANTHROPIC_API_KEY` in the bridge's
environment instead. Full reference: <https://code.claude.com/docs/en/setup>.

Test the headless path before starting the bridge. This is exactly what it runs:

```bash
echo "say hi in 3 words" | claude --print
```

If that fails, fix auth first. Otherwise every mention gets a `Claude Code failed: …`
reply.

## 3. Configure

```bash
cp .env.example .env
```

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `SLACK_APP_TOKEN` | yes | | App-level token (`xapp-…`) |
| `SLACK_BOT_TOKEN` | yes | | Bot token (`xoxb-…`) |
| `SLACK_ALLOWED_USERS` | no | empty = everyone | Comma-separated Slack user IDs allowed to reach Claude |
| `BOT_NAME` | no | `SangAI` | Display name used in the friendly replies |
| `CC_PERMISSION_MODE` | no | `acceptEdits` | Claude permission mode (`acceptEdits`, `default`, `plan`) |
| `CC_ADD_DIR` | no | this repo | Working directory and `--add-dir` for `claude` |
| `CC_CLAUDE_BIN` | no | `claude` | Path to the `claude` CLI |
| `CC_AGENT_TIMEOUT_S` | no | `600` | Max seconds per Claude turn |
| `CC_SESSION_CONTINUATION_WINDOW_S` | no | `7200` | How long a channel's session is reused |
| `BRIDGE_LOG_LEVEL` | no | `INFO` | Python log level |

`.env` is gitignored.

## 4. Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m bridge
```

On success the log shows `bridge: connected — listening for @-mentions`. Then, in
Slack: `@SangAI what repo am I in?` returns a threaded reply, and follow-ups in that
thread continue the same session.

Stop it with Ctrl-C, or `kill "$(cat data/bridge.pid)"` if it runs in the background.

**Single instance only.** On startup the bridge writes `data/bridge.pid` and refuses
to start a second copy. Two Socket Mode clients on one app token make Slack split
events between them at random. If a crash leaves a stale pidfile, delete it and retry.

## 5. State

| File | What |
| --- | --- |
| `data/cc-sessions.json` | `slack:<channel>:<thread_ts>` → Claude session UUID |
| `data/bridge.pid` | Singleton-lock pidfile |

Claude's transcripts live under `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`.
Because the bridge resumes by UUID, you can continue a Slack thread locally with
`claude --resume <uuid>`. Everything under `data/` is gitignored.

## 6. Security

The bridge feeds Slack messages to an agent that can edit files, so treat it as
remote code execution for whoever can mention it.

- Set `SLACK_ALLOWED_USERS`. Empty means everyone in the workspace.
- `acceptEdits` auto-applies file edits. `.claude/settings.json` ships a narrow
  allowlist; widen it deliberately.
- Keep `CC_ADD_DIR` pointed at a directory you are happy for the bot to modify.
- Never commit `.env`. Anyone with the Slack tokens can impersonate the bot.

## 7. Files

| File | Responsibility |
| --- | --- |
| `bridge/__main__.py` | Entrypoint: load `.env`, singleton lock, connect, serve |
| `bridge/config.py` | `Config.from_env()`: tokens, flags, cwd, timeouts, allowlist, bot name |
| `bridge/slack_app.py` | Socket Mode app, `app_mention` handler, status, threaded reply |
| `bridge/session_store.py` | Session key ↔ UUID map (atomic writes), continuation window |
| `bridge/claude_runner.py` | Async `claude --print …` and answer extraction |
| `bridge/unauthorized.py` | 20 friendly replies for non-allowlisted users |

The bridge is deliberately thin: every mention goes straight to Claude, with no
prefix routing, approval flow or slash commands.

## 8. Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 9. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `CERTIFICATE_VERIFY_FAILED` connecting to slack.com | The python.org macOS Python does not trust the system keychain. The bridge builds its SSL context from `certifi`; make sure it is installed. |
| Duplicate or missing replies | More than one client on the same app token. Stop the extras with `kill "$(cat data/bridge.pid)"`. |
| `bridge already running (pid N)` | The singleton lock. Another instance is alive, or `data/bridge.pid` is stale after a crash. |
| The bot ignores a mention | It is not in the channel (`/invite @SangAI`), the sender is not in `SLACK_ALLOWED_USERS`, or `app_mention` is not subscribed. |
| `Claude Code failed: …` reply | The `claude` CLI errored: auth, permission mode or timeout. With `acceptEdits`, an action that needs approval cannot be answered in a headless turn, so allowlist it in `.claude/settings.json`. |
| Replies look unformatted | Slack renders mrkdwn, not Markdown. `CLAUDE.md` tells Claude to use `*bold*`, `<url\|label>` and `•` bullets. |

## License

MIT, see [LICENSE](LICENSE).
