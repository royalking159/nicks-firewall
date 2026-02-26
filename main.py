# main.py
import os
import re
import json
import time
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# ----------------- CONFIG ------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))
GENERAL_CHANNEL_ID = int(os.getenv("GENERAL_CHANNEL_ID", "0"))
STAFF_CHANNEL_IDS = [int(x) for x in os.getenv("STAFF_CHANNEL_IDS", "").split(",") if x.strip().isdigit()]

if not TOKEN or MOD_LOG_CHANNEL_ID == 0 or GENERAL_CHANNEL_ID == 0:
    raise RuntimeError("DISCORD_TOKEN, MOD_LOG_CHANNEL_ID, or GENERAL_CHANNEL_ID missing in .env")

# ----------------- STORAGE FILES -----------------
WARN_FILE = "warnings.json"
MOD_ACTION_FILE = "mod_actions.json"
LOCKDOWN_FILE = "lockdowns.json"

def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return {}

def save_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ----------------- BOT SETUP -----------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ----------------- UTIL FUNCTIONS -----------------
def parse_duration(duration: str) -> int:
    """Parse strings like 1h30m, 45m, 2d into seconds"""
    total = 0
    if not duration: return 0
    for match in re.finditer(r"(\d+)([dhms])", duration.lower()):
        num, unit = match.groups()
        num = int(num)
        if unit=="d": total+=num*86400
        elif unit=="h": total+=num*3600
        elif unit=="m": total+=num*60
        elif unit=="s": total+=num
    return total

def human_readable(seconds:int) -> str:
    d,h,m,s=0,0,0,0
    d, seconds=divmod(seconds,86400)
    h, seconds=divmod(seconds,3600)
    m, s=divmod(seconds,60)
    parts=[]
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return "".join(parts) or "0s"

async def send_mod_log(embed: discord.Embed, files=None):
    ch = bot.get_channel(MOD_LOG_CHANNEL_ID)
    if ch:
        await ch.send(embed=embed, files=files or [])

# ----------------- WARNINGS & ACTIONS -----------------
def load_warnings(): return load_json(WARN_FILE)
def save_warnings(data): save_json(WARN_FILE, data)
def load_actions(): return load_json(MOD_ACTION_FILE)
def save_actions(data): save_json(MOD_ACTION_FILE, data)

def _next_id(lst):
    if not lst: return 1
    return max(int(x.get("id",0)) for x in lst)+1

def add_action(guild_id:int, action_type:str, user_id:int, reason:str, moderator):
    data = load_actions()
    g = data.setdefault(str(guild_id), {})
    a = g.setdefault(action_type, {})
    u = a.setdefault(str(user_id), [])
    action_id = _next_id(u)
    u.append({
        "id": action_id,
        "reason": reason or "No reason provided",
        "moderator_id": getattr(moderator,"id",None),
        "moderator_name": str(moderator),
        "timestamp": int(time.time())
    })
    save_actions(data)
    return action_id

def edit_action_reason(guild_id:int, action_type:str, user_id:int, number:int, new_reason:str):
    data = load_actions()
    g = data.get(str(guild_id), {})
    a = g.get(action_type, {})
    u = a.get(str(user_id), [])
    if not u: return None
    idx = 0
    if number:
        for i,item in enumerate(u):
            if int(item.get("id",i+1))==number: idx=i; break
    old = u[idx].get("reason","")
    u[idx]["reason"]=new_reason or "No reason provided"
    u[idx]["edited_at"]=int(time.time())
    save_actions(data)
    return old

# ----------------- LOCKDOWN -----------------
LOCKDOWN_LEVELS = ["mild","semi","full"]

def load_lockdowns(): return load_json(LOCKDOWN_FILE)
def save_lockdowns(data): save_json(LOCKDOWN_FILE, data)

async def apply_lockdown(guild:discord.Guild, level:str, reason:str, duration:int=None):
    snapshots = load_lockdowns()
    snapshot = {"channels":{}, "timestamp":int(time.time()), "level":level, "reason":reason, "unlock_at":0}
    affected=[]
    try:
        for ch in guild.channels:
            if ch.id in STAFF_CHANNEL_IDS or ch.id==GENERAL_CHANNEL_ID:
                continue
            perms = ch.overwrites_for(guild.default_role)
            snapshot["channels"][str(ch.id)] = perms
            perms.send_messages=False
            perms.add_reactions=False
            await ch.set_permissions(guild.default_role, overwrite=perms)
            affected.append(ch.id)
        if duration:
            snapshot["unlock_at"]=int(time.time())+duration
            async def auto_unlock():
                await asyncio.sleep(duration)
                await remove_lockdown(guild)
            asyncio.create_task(auto_unlock())
        snapshots.setdefault(str(guild.id), []).append(snapshot)
        save_lockdowns(snapshots)

        embed = discord.Embed(
            title=f"🚨 Server Lockdown ({level.upper()}) 🚨",
            description=f"Reason: {reason}\n\nThe server is currently under a temporary lockdown. Staff will investigate and notify when normal operation resumes.",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        general_channel = guild.get_channel(GENERAL_CHANNEL_ID)
        if general_channel: await general_channel.send(embed=embed)
        await send_mod_log(embed)
        return affected
    except Exception as e:
        return f"⚠ Failed to apply lockdown: {e}"

async def remove_lockdown(guild:discord.Guild):
    snapshots = load_lockdowns()
    guild_snapshots = snapshots.get(str(guild.id),[])
    if not guild_snapshots: return "ℹ️ No lockdown snapshot found for this server."
    restored, failed = 0,0
    for snap in guild_snapshots:
        for cid, perms in snap["channels"].items():
            ch = guild.get_channel(int(cid))
            if ch:
                try:
                    await ch.set_permissions(guild.default_role, overwrite=perms)
                    restored+=1
                except: failed+=1
    snapshots[str(guild.id)]=[]
    save_lockdowns(snapshots)
    return f"Unlock attempted. Restored ~{restored} channels; failed ~{failed}."

# ----------------- SLASH COMMANDS -----------------
#---------------------- PING -----------------------
@tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    api_latency = round(bot.latency * 1000)

    embed = discord.Embed(
        title="🏓 Pong!",
        color=discord.Color.green()
    )
    embed.add_field(name="WebSocket Latency", value=f"{api_latency} ms", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)
#-------------------- LOCKDOWN -----------------------
@tree.command(name="lockdown", description="Lock the server")
@app_commands.describe(level="Level of lockdown", duration="Duration like 1h30m", reason="Reason for lockdown")
@app_commands.choices(level=[app_commands.Choice(name=x.upper(),value=x) for x in LOCKDOWN_LEVELS])
async def lockdown(interaction: discord.Interaction, level:app_commands.Choice[str], duration: str=None, reason:str=None):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.followup.send("❌ You lack permission to manage the server.", ephemeral=True)
    seconds = parse_duration(duration) if duration else None
    affected = await apply_lockdown(interaction.guild, level.value, reason or "No reason provided", duration=seconds)
    await interaction.followup.send(f"✅ Lockdown applied ({level.value.upper()}). Affected channels: {len(affected) if isinstance(affected,list) else 0}\n{affected if isinstance(affected,list) else ''}", ephemeral=True)

#---------------------- UNLOCK ------------------------
@tree.command(name="unlock", description="Unlock the server")
async def unlock(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.followup.send("❌ You lack permission to manage the server.", ephemeral=True)
    result = await remove_lockdown(interaction.guild)
    await interaction.followup.send(result, ephemeral=True)

#-------------------- WARN ----------------------------
@tree.command(name="warn", description="Warn a user")
@app_commands.describe(user="User to warn", reason="Reason for warning")
async def warn(interaction: discord.Interaction, user: discord.Member, reason:str=None):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    data = load_warnings()
    g = data.setdefault(str(interaction.guild.id), {})
    u = g.setdefault(str(user.id), [])
    u.append({"reason": reason or "No reason provided", "moderator_id": interaction.user.id, "timestamp": int(time.time())})
    save_warnings(data)
    embed=discord.Embed(title="User Warned", description=f"{user} warned.\nReason: {reason or 'No reason provided'}", color=discord.Color.orange())
    await interaction.response.send_message(embed=embed)
    await send_mod_log(embed)
#---------------------- KICK ----------------------------
@tree.command(name="kick", description="Kick a user")
@app_commands.describe(user="User to kick", reason="Reason for kick")
async def kick(interaction: discord.Interaction, user: discord.Member, reason:str=None):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        await user.kick(reason=reason)
        add_action(interaction.guild.id,"kick",user.id,reason or "No reason provided",interaction.user)
        embed=discord.Embed(title="User Kicked", description=f"{user} kicked.\nReason: {reason or 'No reason provided'}", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        await send_mod_log(embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to kick: {e}", ephemeral=True)
#----------------------- BAN ---------------------------
@tree.command(name="ban", description="Ban a user")
@app_commands.describe(user="User to ban", reason="Reason for ban")
async def ban(interaction: discord.Interaction, user: discord.Member, reason:str=None):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        await user.ban(reason=reason)
        add_action(interaction.guild.id,"ban",user.id,reason or "No reason provided",interaction.user)
        embed=discord.Embed(title="User Banned", description=f"{user} banned.\nReason: {reason or 'No reason provided'}", color=discord.Color.dark_red())
        await interaction.response.send_message(embed=embed)
        await send_mod_log(embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to ban: {e}", ephemeral=True)
#---------------------- MUTE ---------------------------
@tree.command(name="mute", description="Mute a user")
@app_commands.describe(user="User to mute", reason="Reason for mute")
async def mute(interaction: discord.Interaction, user: discord.Member, reason:str=None):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        mute_role = discord.utils.get(interaction.guild.roles, name="Muted")
        if not mute_role:
            mute_role = await interaction.guild.create_role(name="Muted")
            for ch in interaction.guild.channels:
                await ch.set_permissions(mute_role, speak=False, send_messages=False)
        await user.add_roles(mute_role, reason=reason)
        add_action(interaction.guild.id,"mute",user.id,reason or "No reason provided",interaction.user)
        embed=discord.Embed(title="User Muted", description=f"{user} muted.\nReason: {reason or 'No reason provided'}", color=discord.Color.dark_orange())
        await interaction.response.send_message(embed=embed)
        await send_mod_log(embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to mute: {e}", ephemeral=True)
#-------------------------- PURGE --------------------
@tree.command(name="purge", description="Purge messages")
@app_commands.describe(amount="Number of messages to delete")
async def purge(interaction: discord.Interaction, amount:int):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        embed=discord.Embed(title="Messages Purged", description=f"Deleted {len(deleted)} messages.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await send_mod_log(embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to purge: {e}", ephemeral=True)
#------------------------- EDIT REASON -----------------
@tree.command(name="editreason", description="Edit a reason for a mod action")
@app_commands.describe(user="Target user", action_type="Action type (warn/ban/kick/mute)", number="Action ID number", new_reason="New reason")
async def editreason(interaction: discord.Interaction, user:discord.Member, action_type:str, number:int, new_reason:str):
    old = edit_action_reason(interaction.guild.id, action_type, user.id, number, new_reason)
    if old is None:
        return await interaction.response.send_message("❌ Action not found.", ephemeral=True)
    await interaction.response.send_message(f"✅ Reason updated from: {old}", ephemeral=True)

# ----------------- BOT READY -----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try: await tree.sync()
    except Exception: pass

bot.run(TOKEN)
