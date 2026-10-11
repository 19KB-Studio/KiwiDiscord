import asyncio
import logging
import os
import random
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from server import keep_alive

# Load environment variables
load_dotenv()
token = os.getenv("DISCORD_TOKEN")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('discord')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


def create_bot() -> commands.Bot:
    proxy = os.getenv("DISCORD_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
    if proxy:
        logger.info("Using proxy for Discord connection")
        print("Using proxy for Discord connection")

    b = commands.Bot(
        command_prefix=["$", "!"],
        intents=intents,
        proxy=proxy
    )

    @b.event
    async def on_ready():
        logger.info(f"Logged in as {b.user} (ID: {b.user.id})")
        print(f"Logged in as {b.user}")
        print(f"OPENROUTER KEY loaded: {bool(os.getenv('OPENROUTER_API_KEY'))}")
        print(f"TAVILY KEY loaded: {bool(os.getenv('TAVILY_API_KEY'))}")

    return b


async def load_cogs(b: commands.Bot):
    cogs = ["ai", "crypto", "updates", "moderation", "ctf"]
    for cog in cogs:
        try:
            await b.load_extension(cog)
            print(f"Loaded cog: {cog}")
        except Exception as e:
            print(f"Failed to load cog {cog}: {e}")


async def main():
    if not token:
        logger.error("Discord token not found.")
        raise SystemExit("Missing DISCORD_TOKEN in environment")

    backoff = 20
    max_backoff = 300

    while True:
        b = create_bot()
        try:
            async with b:
                await load_cogs(b)
                await b.start(token)
                break
        except discord.HTTPException as e:
            if e.status == 429:
                jitter = random.uniform(2, 10)
                wait_time = backoff + jitter
                logger.warning(
                    f"[Rate Limit] Discord/Cloudflare 429 encountered (shared IP on Render). "
                    f"Waiting {wait_time:.1f}s before retrying..."
                )
                print(
                    f"[Rate Limit] 429 Too Many Requests (Cloudflare Error 1015). "
                    f"Retrying in {wait_time:.1f}s..."
                )
                await asyncio.sleep(wait_time)
                backoff = min(backoff * 2, max_backoff)
            else:
                logger.error(f"HTTPException while starting bot: {e}")
                raise
        except (discord.ConnectionClosed, aiohttp.ClientConnectorError) as e:
            logger.warning(f"Connection error: {e}. Retrying in 15s...")
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise


if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
