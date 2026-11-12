#!/usr/bin/env python3
"""
Read-only check that Ohm's bot can export a channel with DiscordChatExporter.

  python training/check_discord_access.py <CHANNEL_ID>

Uses DISCORD_TOKEN from the environment or the repo's .env. It logs in, checks the
bot's permissions in the channel, reads the last 20 messages and reports counts only.
It never prints message text and never sends anything.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import discord


def load_token() -> str | None:
    if os.getenv("DISCORD_TOKEN"):
        return os.environ["DISCORD_TOKEN"]
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "DISCORD_TOKEN":
                return val.strip().strip('"').strip("'")
    return None


async def check(token: str, channel_id: int) -> int:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    result = {"code": 1}

    @client.event
    async def on_ready():
        try:
            print(f"logged in as {client.user}")
            try:
                ch = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            except discord.Forbidden:
                print("FAIL  bot can't see this channel (missing View Channel, or not in the server)")
                return
            except discord.NotFound:
                print("FAIL  no channel with that ID (check Copy Channel ID)")
                return
            print(f"channel: #{ch.name} in {ch.guild.name}")
            perms = ch.permissions_for(ch.guild.me)
            ok = True
            for name in ("view_channel", "read_message_history"):
                has = getattr(perms, name)
                ok &= has
                print(f"{'ok  ' if has else 'FAIL'}  {name}")
            if not ok:
                return
            total = with_text = 0
            async for m in ch.history(limit=20):
                if m.author.bot:
                    continue
                total += 1
                with_text += bool(m.content or m.attachments or m.stickers)
            print(f"read {total} recent human messages, {with_text} with content")
            if total and with_text == 0:
                print("FAIL  messages come back empty: turn on Message Content Intent "
                      "(Developer Portal → Bot → Privileged Gateway Intents)")
                return
            print("ready to export: use DISCORD_TOKEN with DiscordChatExporter "
                  f"(-c {channel_id} -f Json)")
            result["code"] = 0
        finally:
            await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure:
        print("FAIL  token rejected (check DISCORD_TOKEN)")
    except discord.PrivilegedIntentsRequired:
        print("FAIL  Message Content Intent is off in the Developer Portal")
    return result["code"]


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print(__doc__.strip())
        return 2
    token = load_token()
    if not token:
        print("no DISCORD_TOKEN in the environment or .env")
        return 2
    return asyncio.run(check(token, int(sys.argv[1])))


if __name__ == "__main__":
    sys.exit(main())
