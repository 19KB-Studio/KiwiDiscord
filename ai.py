import asyncio
import os

import discord
from discord.ext import commands
import aiohttp

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# openrouter/free is OpenRouter's own router: it picks a currently-live free
# model per request instead of you pinning to one specific model ID. This is
# the actual fix for the failure mode that broke $groq and $gemini -- those
# were hardcoded to specific models that got deprecated out from under them.
# Docs: https://openrouter.ai/docs/guides/routing/routers/free-router
OPENROUTER_MODEL = "openrouter/free"

# OpenRouter asks integrations to identify themselves via these headers --
# not auth, just good citizenship, same idea as CTFtime's User-Agent requirement.
OPENROUTER_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://github.com/19KB-Studio/KiwiDiscord",
    "X-Title": "kiwiBot",
}

# ── Web search grounding (Tavily) ─────────────────────────────────────────────
# Free tier: 1,000 credits/month, no card required, 1 credit per basic search.
# Docs: https://docs.tavily.com
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_MAX_RESULTS = 4
TAVILY_SNIPPET_CHARS = 300  # per-result content cap, keeps the prompt small

SYSTEM_PROMPT = """
You are an intelligent AI assistant integrated into a Discord server, with
access to live web search results provided alongside each question.

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
6. When web search results are provided below a question, prioritize them for
   anything time-sensitive -- current events, recent CVEs/vulnerabilities,
   version numbers, prices, or "latest" anything -- over your own training
   knowledge, since search results are current and your training data is not.
   If the search results don't cover the question, answer from your own
   knowledge and say so rather than guessing at what the results might contain.

Response Guidelines:
- Keep responses concise.
- Use bullet points or short paragraphs.
- Explain technical topics step-by-step.
- Use markdown formatting when helpful.

Safety Rules:
- Do not assist with harmful or illegal activities.
- Only provide educational explanations for hacking/security topics -- explain
  how a vulnerability class works or how to defend against it, not
  step-by-step exploitation instructions against real, unnamed targets.
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


# ── Web search helper ─────────────────────────────────────────────────────────

async def search_web(query: str) -> list[dict] | None:
    """
    Run a Tavily search and return up to TAVILY_MAX_RESULTS results as
    [{"title", "url", "content"}, ...]. Returns None (not []) on any failure
    or missing key so callers can distinguish "no search happened" from
    "search happened but found nothing".
    """
    tavily_key = os.getenv("TAVILY_API_KEY") or TAVILY_API_KEY
    if not tavily_key:
        return None

    payload = {
        "api_key": tavily_key,
        "query": query,
        "search_depth": "basic",
        "max_results": TAVILY_MAX_RESULTS,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                TAVILY_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[Tavily] Unexpected status {resp.status}: {body[:300]}")
                    return None
                data = await resp.json()
    except Exception as e:
        print(f"[Tavily] Search failed: {e}")
        return None

    results = []
    for item in data.get("results", [])[:TAVILY_MAX_RESULTS]:
        content = (item.get("content") or "").strip()
        if len(content) > TAVILY_SNIPPET_CHARS:
            content = content[:TAVILY_SNIPPET_CHARS].rstrip() + "…"
        results.append({
            "title":   item.get("title", "Untitled"),
            "url":     item.get("url", ""),
            "content": content,
        })
    return results


def format_search_context(results: list[dict]) -> str:
    lines = ["Live web search results:"]
    for i, r in enumerate(results, start=1):
        lines.append(f"[{i}] {r['title']}\n{r['content']}\nSource: {r['url']}")
    return "\n\n".join(lines)


# ── OpenRouter helper with retry + timeout ───────────────────────────────────

async def query_openrouter(messages: list) -> str | None:
    api_key = os.getenv("OPENROUTER_API_KEY") or OPENROUTER_API_KEY
    if not api_key:
        print("[OpenRouter] OPENROUTER_API_KEY is not set — skipping request.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/19KB-Studio/KiwiDiscord",
        "X-Title": "kiwiBot",
    }
    payload = {"model": OPENROUTER_MODEL, "messages": messages}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(
                    OPENROUTER_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    if resp.status == 429:
                        # Daily free-tier cap (50/day, 1000/day with any credit
                        # balance) -- retrying immediately won't help this one.
                        print("[OpenRouter] Rate limited — likely the daily free-tier cap.")
                        return None
                    if resp.status != 200:
                        body = await resp.text()
                        print(f"[OpenRouter] Unexpected status {resp.status}: {body[:300]}")
                    else:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            print(f"[OpenRouter] Timeout on attempt {attempt}/{MAX_RETRIES}")
        except Exception as e:
            print(f"[OpenRouter] Error on attempt {attempt}/{MAX_RETRIES}: {e}")
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    return None


async def send_chunked(destination, text: str):
    """Send a long message in 2000-char Discord chunks."""
    for i in range(0, len(text), 2000):
        await destination.send(text[i:i + 2000])


# ── Shared answer pipeline (search -> augmented prompt -> model) ─────────────

async def answer_question(server: str, channel: str, question: str) -> tuple[str | None, list[dict] | None]:
    """
    Runs the full search-augmented answer flow for one question and returns
    (answer_text, search_results). search_results is None if no search ran
    (missing key or failure) -- distinct from an empty list.
    """
    search_results = await search_web(question)

    store_chat(server, channel, question, "user")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if search_results:
        messages.append({"role": "system", "content": format_search_context(search_results)})

    messages += get_chat(server, channel)

    content = await query_openrouter(messages)
    if content is not None:
        store_chat(server, channel, content, "assistant")

    return content, search_results


def append_sources(text: str, search_results: list[dict] | None) -> str:
    if not search_results:
        return text
    sources = "\n".join(f"[{i}] <{r['url']}>" for i, r in enumerate(search_results, start=1) if r["url"])
    if not sources:
        return text
    return f"{text}\n\n**Sources:**\n{sources}"


# ── Cog ───────────────────────────────────────────────────────────────────────

class AI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── $ai ───────────────────────────────────────────────────────────────────

    @commands.command(name="ai", aliases=["ask"])
    async def ask_ai(self, ctx: commands.Context, *, question: str):
        """Ask the AI a question (with live web search). Usage: $ai <question> (or $ask)"""
        server  = str(ctx.guild.id) if ctx.guild else "dm"
        channel = str(ctx.channel.id)

        async with ctx.channel.typing():
            content, search_results = await answer_question(server, channel, question)

        if content is None:
            await ctx.send(
                "No response after retries. This is usually the free-tier "
                "daily rate limit (resets daily) or a temporary provider issue — "
                "please try again in a bit."
            )
            return

        await send_chunked(ctx, append_sources(f"**AI:**\n{content}", search_results))

    # ── $clear_context ────────────────────────────────────────────────────────

    @commands.command(name="clear_context")
    async def clear_context(self, ctx: commands.Context):
        """Clear conversation history for this channel. Usage: $clear_context"""
        server  = str(ctx.guild.id) if ctx.guild else "dm"
        channel = str(ctx.channel.id)
        if server in conversation_context and channel in conversation_context[server]:
            conversation_context[server][channel] = []
        await ctx.send("Conversation context cleared for this channel.")

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

        content_clean = message.content.replace(f"<@!{self.bot.user.id}>", "").replace(f"<@{self.bot.user.id}>", "").strip()
        if not content_clean:
            await message.channel.send(
                "Hey! How can I help? Try `$ai` with a question."
            )
            return

        async with message.channel.typing():
            response_content, search_results = await answer_question(server, channel, content_clean)

        if response_content is None:
            await message.channel.send(
                "An error occurred (timeout/rate-limit). Try again in a moment."
            )
            return

        await send_chunked(message.channel, append_sources(response_content, search_results))


async def setup(bot: commands.Bot):
    await bot.add_cog(AI(bot))
