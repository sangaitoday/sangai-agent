"""Friendly replies for non-allowlisted Slack users.

Instead of silently dropping a mention from someone who isn't on the allowlist,
post one of 20 random "still warming up" messages addressed to them. `{uid}` is
filled with the Slack user id so `<@U…>` renders as a mention (posted with
mrkdwn=True); `{name}` is the configurable AI name (Config.bot_name, env BOT_NAME).
"""
from __future__ import annotations

import random

MESSAGES = [
    "Hi <@{uid}>! 👋 I'm {name}, still warming up! I'm not fully available yet — but great things are coming. Stay tuned!",
    "Hey <@{uid}>! 😊 I'm not quite ready for everyone yet. I'm still in the works — check back soon!",
    "Hi <@{uid}>! 🚀 I'm still under development and not ready to chat just yet. Big things coming soon!",
    "Hey there <@{uid}>! ✨ I'm {name} and I'm in early access mode right now. I'll be ready for more people soon!",
    "Hi <@{uid}>! 🙌 Thanks for reaching out! I'm still being set up and not quite ready yet. Check back soon!",
    "Hey <@{uid}>! 💡 I'm {name}, still finding my feet! I'm not open to everyone just yet — stay tuned for updates!",
    "Hi <@{uid}>! 🌟 Appreciate you saying hello! I'm in private mode right now — more access coming soon!",
    "Hey <@{uid}>! 🎉 I'm still getting ready for the world! Exciting things ahead, just not quite yet!",
    "Hi <@{uid}>! 🤖 I'm {name}, still in private beta! Bear with me — great things are on the way!",
    "Hey <@{uid}>! 💬 Thanks for the message! I'm currently in early development — more soon!",
    "Hey <@{uid}>! 😅 Whoops, you've wandered into the VIP lounge! I'm not quite open yet — coming soon!",
    "Hi <@{uid}>! 🔐 Access denied... but make it cute! I'm still being built and not ready to chat. Stay tuned!",
    "Hey <@{uid}>! 🫣 Pssst... I'm still hiding while I get built! Don't tell anyone you found me!",
    "Hi <@{uid}>! 🤫 Top secret operation in progress! I'm still under development — hang tight, big things coming!",
    "Hey <@{uid}>! 🚧 Construction zone ahead! I'm still being put together — not ready for visitors just yet!",
    "Hi <@{uid}>! 🎭 Plot twist: I'm not ready for the world yet! More coming soon!",
    "Hey <@{uid}>! 🍪 I'd love to help but I'm still in the oven! Check back soon when I'm fully baked!",
    "Hi <@{uid}>! 🙈 Caught me mid-construction! Still being built — but something great is on the way!",
    "Hey <@{uid}>! 🎯 Almost ready but not quite there! I'm still in development — more soon!",
    "Hi <@{uid}>! 🚀 Not yet launched! I'm still in development mode — but the countdown has started. Stay close!",
]


def pick(user_id: str, name: str = "SangAI") -> str:
    """Return a random friendly message addressed to this user id."""
    return random.choice(MESSAGES).format(uid=user_id or "there", name=name)
