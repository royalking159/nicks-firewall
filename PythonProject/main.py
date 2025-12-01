#!/usr/bin/env python3
"""
Discord bot that uses Wolfram|Alpha for answers and returns only the 'Result' pod.

Usage:
  - Mention the bot with a prompt (e.g. @Bot integrate x^2)
  - Or use: !ask <prompt>

Requirements:
  pip install -U discord.py python-dotenv requests
Place DISCORD_TOKEN and WOLFRAM_APP_ID in a .env file (never hardcode keys).
"""

import os
import asyncio
import textwrap
import requests
from dotenv import load_dotenv
from discord.ext import commands
import discord

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WOLFRAM_APP_ID = os.getenv("WOLFRAM_APP_ID")

if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN missing in environment (.env) — create a Discord bot and add token to .env")
if not WOLFRAM_APP_ID:
    raise SystemExit("WOLFRAM_APP_ID missing in environment (.env) — obtain one from Wolfram|Alpha Developer Portal")

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


def split_into_chunks(s: str, chunk_size: int = 1900):
    """Split a long string into Discord-friendly chunks."""
    for i in range(0, len(s), chunk_size):
        yield s[i:i + chunk_size]


def _call_wolfram_blocking(prompt_text: str) -> str:
    """
    Wolfram call that returns ONLY the main answer (the 'Result' pod).
    Falls back to v1/result if no Result pod is found.
    """
    try:
        base = "https://api.wolframalpha.com/v2/query"
        params = {
            "input": prompt_text,
            "appid": WOLFRAM_APP_ID,
            "output": "json",
        }
        r = requests.get(base, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return f"(error contacting Wolfram|Alpha: {e})"

    qr = data.get("queryresult", {})
    pods = qr.get("pods", [])
    success = qr.get("success", False)

    # 1. Try to find the "Result" pod only (case-insensitive)
    for pod in pods:
        title = pod.get("title", "")
        if title and title.strip().lower() == "result":
            subpods = pod.get("subpods", [])
            for sp in subpods:
                text = sp.get("plaintext")
                if text and text.strip():
                    return text.strip()

    # 2. If no "Result" pod found → fallback to Wolfram|Alpha simple result endpoint (/v1/result)
    try:
        simple = requests.get(
            "https://api.wolframalpha.com/v1/result",
            params={"i": prompt_text, "appid": WOLFRAM_APP_ID},
            timeout=8,
        )
        if simple.status_code == 200 and simple.text.strip():
            return simple.text.strip()
    except Exception:
        pass

    # 3. As a last resort, try to return the first meaningful plaintext found from any pod
    for pod in pods:
        subpods = pod.get("subpods", [])
        for sp in subpods:
            pt = sp.get("plaintext")
            if pt and pt.strip():
                return pt.strip()

    return "(No answer found.)"


async def _respond_with_wolfram(channel: discord.abc.Messageable, reply_func, prompt: str):
    """Call Wolfram (in a thread) and send the response back using reply_func."""
    async with channel.typing():
        try:
            result = await asyncio.to_thread(_call_wolfram_blocking, prompt)
        except Exception as e:
            await reply_func(f"Error calling Wolfram|Alpha API: {e}")
            return

    if not result:
        await reply_func("Wolfram|Alpha returned an empty response.")
        return

    for chunk in split_into_chunks(result):
        await channel.send(chunk)


@bot.command(name='ask')
async def ask(ctx: commands.Context, *, prompt: str = None):
    """Legacy command-style interface. Kept for backward compatibility."""
    if not prompt:
        await ctx.reply("Usage: `!ask <your question>` — you didn't provide a prompt.")
        return
    await _respond_with_wolfram(ctx.channel, ctx.reply, prompt)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    print("Ready to accept mentions or !ask <prompt>")


@bot.event
async def on_message(message: discord.Message):
    """Handle regular messages and detect mentions of the bot."""
    if message.author.bot:
        return

    if message.mentions and bot.user in message.mentions:
        content = message.content
        # remove forms of mention
        content = content.replace(f"<@{bot.user.id}>", "")
        content = content.replace(f"<@!{bot.user.id}>", "")
        content = content.lstrip()
        content = content.lstrip(',:- ')
        content = content.strip()

        if not content:
            await message.reply("Usage: mention me with a prompt, e.g. `@YourBot what's the weather?`")
            return

        await _respond_with_wolfram(message.channel, message.reply, content)
        return

    await bot.process_commands(message)


@bot.command(name='help')
async def help_cmd(ctx: commands.Context):
    help_text = textwrap.dedent(
        """
        **Simple Wolfram|Alpha Discord Bot**
        Commands:
          `@Bot <prompt>` — Mention the bot with your question (preferred).
          `!ask <prompt>` — (legacy) Ask Wolfram|Alpha a question.
          `!help` — Show this message.

        Setup notes: put DISCORD_TOKEN and WOLFRAM_APPID into a .env file.
        """
    )
    await ctx.send(help_text)


if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
