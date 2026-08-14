import discord
from discord.ext import commands
from groq import Groq
from google import genai
import asyncio
import os

GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Clients are created lazily (on first use) instead of at import time.
# Groq(api_key=None) raises immediately, which would otherwise crash this
# entire module's import — and with it every command in this cog, including
# Gemini — whenever GROQ_API_KEY isn't set.
_groq_client = None
_gemini_client = None


def get_groq_client() -> Groq | None:
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def get_gemini_client() -> genai.Client | None:
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client

SYSTEM_PROMPT = """
You are an intelligent AI assistant integrated into a Discord server.

Your purpose is to assist users across multiple types of conversations including
technical discussions, general questions, entertainment, and updates about global
technology developments.

Core Responsibilities:
1. Answer user questions clearly and concisely.
2. Help with technical topics such as cybersecurity, programming, artificial intelligence,
   mechanical engineering, and electrical engineering.
3. Participate in general conversation when users ask casual questions.
4. Provide summaries of important global technology trends when asked.
5. Keep responses suitable for Discord chat.

Response Guidelines:
- Keep responses concise.
- Use bullet points or short paragraphs.
- Explain technical topics step-by-step.
- Use markdown formatting when helpful.

Safety Rules:
- Do not assist with harmful or illegal activities.
- Only provide educational explanations for hacking/security topics.
- Avoid misinformation.

Interaction Style:
Friendly, helpful, professional for technical topics, conversational for general chat.
"""

# Conversation memory
conversation_context: dict = {}
CONVERSATION_CONTEXT_LIMIT = 20

# ── Retry config ──────────────────────────────────────────────────────────────
MAX_RETRIES    = 2
RETRY_DELAY    = 1.5   # seconds between retries
API_TIMEOUT    = 20    # seconds before we give up on a single attempt


def store_chat(server: str, channel: str, message: str, role: str):
    if server not in conversation_context:
        conversation_context[server] = {}
    if channel not in conversation_context[server]:
        conversation_context[server][channel] = []
    cur_chat = conversation_context[server][channel]
    if len(cur_chat) >= CONVERSATION_CONTEXT_LIMIT:
        cur_chat.pop(0)
    cur_chat.append({"role": role, "content": message})


def get_chat(server: str, channel: str) -> list:
    return conversation_context.get(server, {}).get(channel, [])


# ── API helpers with retry + timeout ─────────────────────────────────────────

async def query_groq(messages: list) -> str | None:
    client = get_groq_client()
    if client is None:
        print("[Groq] GROQ_API_KEY is not set — skipping request.")
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    model="llama3-8b-8192",
                    messages=messages,
                    timeout=API_TIMEOUT,
                ),
                timeout=API_TIMEOUT + 5,   # outer asyncio guard
            )
            return response.choices[0].message.content
        except asyncio.TimeoutError:
            print(f"[Groq] Timeout on attempt {attempt}/{MAX_RETRIES}")
        except Exception as e:
            print(f"[Groq] Error on attempt {attempt}/{MAX_RETRIES}: {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return None


async def query_gemini(question: str) -> str | None:
    client = get_gemini_client()
    if client is None:
        print("[Gemini] GEMINI_API_KEY is not set — skipping request.")
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model="gemini-2.0-flash",
                    contents=question,
                ),
                timeout=API_TIMEOUT + 5,
            )
            return response.text
        except asyncio.TimeoutError:
            print(f"[Gemini] Timeout on attempt {attempt}/{MAX_RETRIES}")
        except Exception as e:
            print(f"[Gemini] Error on attempt {attempt}/{MAX_RETRIES}: {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return None


async def send_chunked(destination, text: str):
    """Send a long message in 2000-char Discord chunks."""
    for i in range(0, len(text), 2000):
        await destination.send(text[i:i + 2000])


# ── Cog ───────────────────────────────────────────────────────────────────────

class AI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── $groq ─────────────────────────────────────────────────────────────────

    @commands.command(name="groq")
    async def ask_groq(self, ctx: commands.Context, *, question: str):
        """Ask Groq (Llama 3) a question. Usage: $groq <question>"""
        server  = str(ctx.guild.id)
        channel = str(ctx.channel.id)
        store_chat(server, channel, question, "user")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + get_chat(server, channel)

        async with ctx.channel.typing():
            content = await query_groq(messages)

        if content is None:
            await ctx.send(
                "⚠️ Groq didn't respond after retries. "
                "This may be a rate-limit or a Render cold-start — please try again in a moment."
            )
            return

        store_chat(server, channel, content, "assistant")
        await send_chunked(ctx, f"🟠 **Groq (Llama 3):**\n{content}")

    # ── $gemini ───────────────────────────────────────────────────────────────

    @commands.command(name="gemini")
    async def ask_gemini(self, ctx: commands.Context, *, question: str):
        """Ask Gemini a question. Usage: $gemini <question>"""
        server  = str(ctx.guild.id)
        channel = str(ctx.channel.id)
        store_chat(server, channel, question, "user")

        async with ctx.channel.typing():
            content = await query_gemini(question)

        if content is None:
            await ctx.send(
                "⚠️ Gemini didn't respond after retries. "
                "This may be a rate-limit or a Render cold-start — please try again in a moment."
            )
            return

        store_chat(server, channel, content, "assistant")
        await send_chunked(ctx, f"🔵 **Gemini 2.0 Flash:**\n{content}")

    # ── $clear_context ────────────────────────────────────────────────────────

    @commands.command(name="clear_context")
    async def clear_context(self, ctx: commands.Context):
        """Clear conversation history for this channel. Usage: $clear_context"""
        server  = str(ctx.guild.id)
        channel = str(ctx.channel.id)
        if server in conversation_context and channel in conversation_context[server]:
            conversation_context[server][channel] = []
        await ctx.send("🧹 Conversation context cleared for this channel.")

    # ── mention handler ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user:
            return
        if not message.guild:
            return
        if self.bot.user not in message.mentions:
            return

        server  = str(message.guild.id)
        channel = str(message.channel.id)

        content_clean = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
        if not content_clean:
            await message.channel.send(
                "Hey! How can I help? Try `$groq` or `$gemini` with a question."
            )
            return

        store_chat(server, channel, content_clean, "user")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + get_chat(server, channel)

        async with message.channel.typing():
            response_content = await query_groq(messages)

        if response_content is None:
            await message.channel.send(
                "⚠️ AI error occurred (timeout/rate-limit). Try again in a moment."
            )
            return

        store_chat(server, channel, response_content, "assistant")
        await send_chunked(message.channel, response_content)


async def setup(bot: commands.Bot):
    await bot.add_cog(AI(bot)) 