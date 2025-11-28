# bot.py
import os
import asyncio
import logging
from g4f.client import AsyncClient
import discord
from discord.ext import commands
from discord import app_commands

logging.basicConfig(level=logging.INFO)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # put your bot token in env var

if not DISCORD_TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN environment variable before running.")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Utility to call g4f ---
async def ask_g4f(question: str, model: str = "gpt-4o", timeout: int = 60) -> str:
    """
    Ask g4f AsyncClient a question and return the assistant text.
    Creates and closes a client per call to be safe.
    """
    client = AsyncClient()
    try:
        # call the same API shape you posted
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": question}],
            ),
            timeout=timeout,
        )
        # attempt to extract text safely
        try:
            return response.choices[0].message.content
        except Exception:
            # fallback to string convert if structure differs
            return str(response)
    finally:
        # close client if it supports aclose()
        aclose = getattr(client, "aclose", None)
        if aclose:
            await aclose()


# --- Sync app commands on ready ---
@bot.event
async def on_ready():
    # sync slash commands to Discord (will create/update them)
    try:
        await bot.tree.sync()
        logging.info("Slash commands synced.")
    except Exception as e:
        logging.exception("Failed to sync slash commands: %s", e)
    logging.info(f"Logged in as {bot.user} (id: {bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # --- Reply when the bot is mentioned ---
    if bot.user in message.mentions:
        # Remove the mention text and keep the actual question
        content = message.content.replace(f"<@{bot.user.id}>", "").strip()

        # If they only tagged the bot but wrote nothing
        if content == "":
            await message.reply("Hi! Ask me something 🙂")
            return

        # Call the model
        reply = await ask_g4f(content)
        await message.reply(reply)
        return

    # Make sure commands still work (!ask, etc.)
    await bot.process_commands(message)


# --- Slash command ---
@bot.tree.command(name="ask", description="Ask the model a question")
@app_commands.describe(question="What you want to ask the model")
async def slash_ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer()  # defer the response (shows "thinking...")
    try:
        answer = await ask_g4f(question)
        # Discord messages are limited; trim if necessary
        if not answer:
            answer = "_(no answer returned)_"
        if len(answer) > 1900:
            # send a short preview and attach remainder as a file
            preview = answer[:1900] + "\n\n(remaining output attached as .txt)"
            await interaction.followup.send(preview)
            # attach remainder
            remainder = answer[1900:]
            await interaction.followup.send(file=discord.File(fp=discord.File(io.BytesIO(remainder.encode()), filename="remainder.txt").fp, filename="remainder.txt"))
        else:
            await interaction.followup.send(answer)
    except Exception as e:
        await interaction.followup.send(f"Error calling model: {e}")


# --- Prefix command (text command) ---
@bot.command(name="ask")
async def text_ask(ctx: commands.Context, *, question: str):
    """Use: !ask What is quantum computing?"""
    # give feedback so user knows bot is working
    message = await ctx.reply("Thinking... ⏳")
    try:
        answer = await ask_g4f(question)
        if not answer:
            answer = "_(no answer returned)_"
        # edit the "Thinking..." message with the answer
        await message.edit(content=answer)
    except Exception as e:
        await message.edit(content=f"Error calling model: {e}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
