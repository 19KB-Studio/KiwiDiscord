import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from server import keep_alive
import os
import asyncio

# Load environment variables
load_dotenv()
token = os.getenv("DISCORD_TOKEN")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('discord')

# Setup bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=["$", "!"],
    intents=intents
)


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"✅ Logged in as {bot.user}")
    print(f"GROQ KEY loaded: {bool(os.getenv('GROQ_API_KEY'))}")
    print(f"GEMINI KEY loaded: {bool(os.getenv('GEMINI_API_KEY'))}")


async def load_cogs():
    cogs = ["ai", "crypto", "updates", "moderation", "ctf"]
    for cog in cogs:
        try:
            await bot.load_extension(cog)
            print(f"✅ Loaded cog: {cog}")
        except Exception as e:
            print(f"❌ Failed to load cog {cog}: {e}")


async def main():
    async with bot:
        await load_cogs()
        if not token:
            logger.error("Discord token not found.")
            raise SystemExit("Missing DISCORD_TOKEN in environment")
        await bot.start(token)


if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())