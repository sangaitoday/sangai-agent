# sangai-agent — Project Invariants

This repo is a small standalone **Slack bridge** that lets people talk to Claude
Code from Slack by @-mentioning the bot, plus a portable `.claude/` directory
(agent charter and permissions) that Claude reads when it runs here.

When you run with this directory as the working tree, you may be driven by an
inbound Slack mention through the bridge. Your final reply is what the Slack
thread sees.

## Architecture

```
Slack @bot ──(Socket Mode, SLACK_APP_TOKEN)──▶ bridge (python -m bridge)
                                               │  slack_app.py: app_mention handler
                                               │  session_store.py: thread → UUID map
                                               │  claude_runner.py: claude --print
                                               │    --session-id|--resume <uuid>
                                               │    --permission-mode acceptEdits
                                               │    --add-dir <this repo>
                                               ▼
                                 posts Claude's stdout back into the same thread
                                 (chat_postMessage, mrkdwn=True)
```

The bridge maps each Slack thread to a stable Claude Code session UUID in
`data/cc-sessions.json`, so a thread is one continuous conversation. Because the
bridge runs `claude` with **this repo** as cwd, **this file is the project
instruction `claude` reads on every Slack turn**.

## Invariants

- **Output is Slack mrkdwn.** `*bold*`, `_italic_`, `<url|label>`, `•` bullets. No
  Markdown headers (`#`, `##`) or fenced code blocks for prose. The bridge posts
  your stdout verbatim with `mrkdwn=True`, so keep it tight and link out for long
  content.
- **Never stay silent.** Every path produces a reply. On failure, say what is
  missing in one short line.
- **No clarifying questions.** The bridge path is one-shot and cannot round-trip
  a question mid-turn. Make a reasonable assumption and state it inline.
- **Runtime state lives under `data/`** (`cc-sessions.json`, `bridge.pid`) and is
  gitignored. Never hand-edit `cc-sessions.json` mid-conversation; that orphans
  the matching `~/.claude/projects/.../<uuid>.jsonl` transcript.
- **Do not reveal secrets.** Never print tokens or the contents of `.env` in a
  reply.

## Bridge — operational invariants

- **Single instance only.** On startup the bridge writes its PID to
  `data/bridge.pid` and refuses to start if another instance is alive. Two Socket
  Mode clients on one app token split events at random.
- **One client per Slack app token.** If any other Socket Mode client connects
  with the same token, Slack splits events between them.
- **TLS via certifi.** `slack_app.py` builds an explicit `ssl.SSLContext` from
  `certifi` and hands it to the Slack web client (the websocket reuses it). The
  python.org macOS Python does not trust the system keychain, so removing this
  breaks the connection to `slack.com`.
- **`claude` runs with `acceptEdits`** (set `CC_PERMISSION_MODE` to change it) on
  untrusted Slack input. File edits auto-apply; other permissioned actions can
  stall a headless turn. Keep the bridge scoped to this working tree, widen
  `CC_ADD_DIR` deliberately, and set `SLACK_ALLOWED_USERS`.

## Layout

```
sangai-agent/
  CLAUDE.md                 This file
  bridge/                   Slack ↔ Claude Code bridge
    __main__.py             entrypoint: .env load, singleton lock, connect, serve
    config.py               env → Config (tokens, claude flags, cwd, timeouts)
    session_store.py        slack:<channel>:<thread_ts> → Claude session UUID map
    claude_runner.py        async `claude --print …` + result extraction
    slack_app.py            Socket Mode app, app_mention handler, threaded reply
    unauthorized.py         friendly replies for non-allowlisted users
  tests/                    unittest suite (python -m unittest)
  .claude/
    agents/SangAI.md        general-purpose assistant charter
    settings.json           tool permission allowlist
  .env.example              Slack tokens + CC_* knobs (copy to .env)
  requirements.txt          slack_bolt, aiohttp, certifi
```
