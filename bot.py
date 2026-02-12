import asyncio
import json
import logging
from datetime import datetime

import discord
from discord import app_commands

import data_models
import chat
import goal_management  # noqa: F401
import recent_messages

logging.basicConfig(
    level=logging.CRITICAL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("ibis.bot").setLevel(logging.INFO)
logging.getLogger("ibis.chat").setLevel(logging.INFO)
logging.getLogger("ibis.chat.judges").setLevel(logging.INFO)
logger = logging.getLogger("ibis.bot")

with open("keyring.json", "r", encoding="utf-8") as f:
    keyring = json.load(f)

BOT_TOKEN = keyring["discord_token"]
GUILD = discord.Object(id=keyring["guild_id"])
data_models.initialize_connection(keyring["db_url"])
chat.initialize_connection(keyring)

client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)


def clip_reply_text(text):
    return text if len(text) <= 1900 else text[:1900] + "…"


def save_context(context, server_key):
    discord_id = context.discord_id
    with data_models.Session() as session:
        user = session.get(data_models.User, discord_id)
        user.update_profile(context.user["profile"])
        user.conversation_summary = context.user["conversation_summary"]
        gm = session.get(data_models.GlobalMemory, server_key)
        if not gm:
            gm = data_models.GlobalMemory(key=server_key, content=context.global_memory)
            session.add(gm)
        else:
            gm.content = context.global_memory
        session.commit()


async def build_context(discord_user, text, message, server_key):
    is_dm = message is None or message.guild is None
    bot_user_id = client.user.id
    with data_models.Session() as session:
        user = session.get(data_models.User, discord_user.id)
        user_dict = user.to_jsonable()

        clean_text = await recent_messages.replace_mentions(message, bot_user_id) if message else text
        if is_dm:
            global_memory = ""
        else:
            gm = session.get(data_models.GlobalMemory, server_key)
            if not gm:
                gm = data_models.GlobalMemory(key=server_key, content="")
                session.add(gm)
                session.commit()
            global_memory = gm.content

    context = chat.ConversationContext(
        current_time=datetime.now().isoformat(timespec="minutes"),
        user=user_dict,
        discord_username=discord_user.display_name,
        input_text=clean_text,
        discord_id=discord_user.id,
        global_memory=global_memory,
    )
    if message:
        context.recent_messages = await recent_messages.collect_recent_messages(
            message,
            bot_user_id,
            history_limit=10,
            reply_chain_limit=5,
        )
    if is_dm:
        context.global_memory = ""
    return context, server_key


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if client.user and client.user in message.mentions:
        content = message.content
        if not content:
            return
        async with message.channel.typing():
            data_models.User.ensure_user(message.author)
            server_key = str(message.guild.id) if message.guild else "dm_global"
            context, server_key = await build_context(
                message.author,
                content,
                message,
                server_key,
            )
            reply_text = await asyncio.to_thread(context.chat)
            await asyncio.to_thread(save_context, context, server_key)
            await message.reply(clip_reply_text(reply_text), mention_author=False)


@tree.command(
    name="update",
    description="Describe updates; the bot will add/complete goals or tasks",
    guild=GUILD,
)
@app_commands.describe(text="Describe what changed or what to add")
async def update_cmd(interaction: discord.Interaction, text: str):
    await interaction.response.defer()

    data_models.User.ensure_user(interaction.user)
    server_key = str(interaction.guild.id) if interaction.guild else "dm_global"
    context, server_key = await build_context(
        interaction.user,
        text,
        None,
        server_key,
    )
    reply_text = await asyncio.to_thread(context.chat)
    await asyncio.to_thread(save_context, context, server_key)
    await interaction.followup.send(clip_reply_text(reply_text))


@client.event
async def on_ready():
    await tree.sync(guild=GUILD)
    await client.change_presence(activity=discord.Game("Roar of thunder, hear my uwu!"))


client.run(BOT_TOKEN)
