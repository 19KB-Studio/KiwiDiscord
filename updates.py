import asyncio
import calendar
import datetime
import hashlib
import json
import os

import discord
from discord.ext import commands, tasks
import aiohttp
import feedparser

import db


# Target post time: 17:30 IST = 12:00 UTC
POST_HOUR_UTC   = 12
POST_MINUTE_UTC = 0

# Admin notifications on errors — set ADMIN_USER_ID in Render env to get DMs
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
if ADMIN_USER_ID:
    try:
        ADMIN_USER_ID = int(ADMIN_USER_ID)
    except ValueError:
        print("[News] WARNING: ADMIN_USER_ID is set but not a valid integer. Notifications disabled.")
        ADMIN_USER_ID = None

# Persistence file — stores {guild_id: channel_id} as strings
NEWS_CHANNELS_FILE = "news_channels.json"

# Fallback/seed source — survives Render free-tier restarts (filesystem doesn't).
# Format: "guild_id:channel_id,guild_id:channel_id"
NEWS_CHANNELS_ENV = os.getenv("NEWS_CHANNELS")

TECH_SOURCES = [
    "https://feeds.feedburner.com/TheHackersNews",           # cybersecurity
    "https://feeds.arstechnica.com/arstechnica/technology",  # tech
    "https://techcrunch.com/feed/",                          # AI/tech startups
    "https://www.wired.com/feed/rss",                        # wired tech
    "https://venturebeat.com/feed/",                         # AI/tech
    "https://www.theverge.com/rss/index.xml",                # AI policy, big tech
    "https://feeds.feedburner.com/mitsmarterbusiness",       # MIT Technology Review
    "https://www.zdnet.com/news/rss.xml",                    # enterprise IT, security
    "https://www.engadget.com/rss.xml",                       # broad tech incl. breaking leaks/DMCA stories
    "https://www.bleepingcomputer.com/feed/",                 # dedicated infosec (breaches, CVEs, malware)
]

# How many raw entries to pull per source BEFORE freshness filtering. Kept wide
# so a source that front-loads older evergreen content doesn't crowd out fresh
# items that would otherwise sit just past a smaller slice.
RAW_FETCH_LIMIT = 20

# Drop anything older than this — prevents a slow-updating feed from serving
# the same "latest" articles run after run. Applies to both the scheduled
# digest and $news.
FRESHNESS_WINDOW_HOURS = 24

# Max articles shown per category in the digest
MAX_PER_CATEGORY = 10

# How many days to remember a headline as "already sent" for the scheduled
# daily digest. Only needs to be a bit longer than FRESHNESS_WINDOW_HOURS so
# an article can't loop back in once it's aged past the freshness cutoff and
# get pulled again as if new.
NEWS_HISTORY_DAYS = 7

CATEGORIES = {
    "ai":            ["artificial intelligence", "machine learning", "llm", "openai",
                      "anthropic", "deepmind", "generative", "chatgpt", "gemini", "gpt"],
    "cybersecurity": ["hack", "hacked", "malware", "vulnerability", "breach", "data breach",
                      "ransomware", "phishing", "zero-day", "exploit", "cve", "cyber",
                      "security flaw", "leak", "leaked"],
    "tech":          ["technology", "startup", "software", "hardware", "cloud",
                      "semiconductor", "chip", "apple", "google", "microsoft", "meta"],
}
VALID_CATEGORIES = list(CATEGORIES.keys())  # ["ai", "cybersecurity", "tech"]

# Display styling for each category's embed — colour and short label
CATEGORY_META = {
    "AI":            {"label": "AI",            "color": discord.Color(0x9B59B6)},
    "CYBERSECURITY": {"label": "Cybersecurity", "color": discord.Color(0xE74C3C)},
    "TECH":          {"label": "Tech",          "color": discord.Color(0x3498DB)},
}
DEFAULT_META = {"label": "Other", "color": discord.Color.blurple()}

# Discord's hard caps: 10 embeds/message, 6000 combined characters/message.
# Keep a safety margin below the character cap.
MAX_EMBEDS_PER_MESSAGE      = 10
MAX_EMBED_CHARS_PER_MESSAGE = 5900


# ── Persistence helpers (per-guild channel config) ─────────────────────────────

def load_news_channels() -> dict[str, int]:
    if not os.path.exists(NEWS_CHANNELS_FILE):
        return {}
    try:
        with open(NEWS_CHANNELS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[News] Failed to load {NEWS_CHANNELS_FILE}: {e}")
        return {}


def save_news_channels(data: dict[str, int]) -> None:
    try:
        with open(NEWS_CHANNELS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[News] Failed to save {NEWS_CHANNELS_FILE}: {e}")


def parse_env_channels(env_value: str | None) -> dict[str, int]:
    """Parse 'guild_id:channel_id,guild_id:channel_id' into a dict."""
    if not env_value:
        return {}
    result = {}
    for pair in env_value.replace(" ", "").split(","):
        if not pair:
            continue
        try:
            guild_id, channel_id = pair.split(":")
            result[guild_id] = int(channel_id)
        except ValueError:
            print(f"[News] Ignoring invalid NEWS_CHANNELS entry: {pair!r}")
    return result


def channels_to_env_string(data: dict[str, int]) -> str:
    return ",".join(f"{gid}:{cid}" for gid, cid in data.items())


# ── Cross-day dedup (Neon) ──────────────────────────────────────────────────────
# Only used for the scheduled daily digest — see news_cmd for why $news is
# intentionally excluded from this filter.

def _title_hash(title: str) -> str:
    return hashlib.md5(title.strip().lower().encode("utf-8")).hexdigest()


async def filter_unsent(items: list[dict]) -> list[dict]:
    """Drop items whose title was already posted in a recent digest. If the DB
    isn't configured or the check fails, fail open and return items unchanged —
    a missing dedup check is better than a broken digest."""
    if not db.DATABASE_URL or not items:
        return items

    hashes = [_title_hash(i["title"]) for i in items]
    try:
        conn = await db.connect()
    except RuntimeError:
        return items

    try:
        rows = await conn.fetch(
            "SELECT title_hash FROM news_sent WHERE title_hash = ANY($1::text[])",
            hashes,
        )
        already_sent = {r["title_hash"] for r in rows}
    except Exception as e:
        print(f"[News] Failed to check news_sent, showing unfiltered: {e}")
        return items
    finally:
        await conn.close()

    return [item for item, h in zip(items, hashes) if h not in already_sent]


async def mark_sent(items: list[dict]) -> None:
    """Record headlines actually shown in a digest so they aren't repeated.
    Also prunes entries older than NEWS_HISTORY_DAYS on the same connection."""
    if not db.DATABASE_URL or not items:
        return

    rows = [(_title_hash(i["title"]),) for i in items]
    try:
        conn = await db.connect()
    except RuntimeError:
        return

    try:
        await conn.executemany(
            """
            INSERT INTO news_sent (title_hash, first_seen)
            VALUES ($1, now())
            ON CONFLICT (title_hash) DO NOTHING
            """,
            rows,
        )
        await conn.execute(
            "DELETE FROM news_sent WHERE first_seen < now() - interval '%s days'" % NEWS_HISTORY_DAYS
        )
    except Exception as e:
        print(f"[News] Failed to update news_sent: {e}")
    finally:
        await conn.close()


# ── RSS helpers ───────────────────────────────────────────────────────────────

def categorize(title: str, summary: str) -> str:
    combined = (title + " " + summary).lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in combined for kw in keywords):
            return cat.upper()
    return "TECH"


def _entry_published_dt(entry) -> datetime.datetime | None:
    """Best-effort publish time for an entry, normalized to UTC. Returns None
    if the feed doesn't supply one — callers should treat that as unknown
    freshness, not as stale."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    try:
        return datetime.datetime.fromtimestamp(calendar.timegm(struct), tz=datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


async def fetch_rss(url: str, session: aiohttp.ClientSession) -> list[dict]:
    """Fetch and parse one RSS feed, returning up to RAW_FETCH_LIMIT items that
    fall within FRESHNESS_WINDOW_HOURS. Entries with no parseable date are kept
    (can't tell if they're stale, so don't punish them for missing metadata)."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            text = await resp.text()
    except Exception as e:
        print(f"[News] Failed to fetch {url}: {e}")
        return []

    feed  = feedparser.parse(text)
    items = []
    now   = datetime.datetime.now(datetime.timezone.utc)
    skipped_stale = 0

    for entry in feed.entries[:RAW_FETCH_LIMIT]:
        title   = entry.get("title", "").strip()
        link    = entry.get("link", "")
        summary = entry.get("summary", "")

        if not title or not link:
            continue

        published_dt = _entry_published_dt(entry)
        if published_dt is not None:
            age_hours = (now - published_dt).total_seconds() / 3600
            if age_hours > FRESHNESS_WINDOW_HOURS:
                skipped_stale += 1
                continue

        items.append({
            "title":        title,
            "link":         link,
            "category":     categorize(title, summary),
            "published_dt": published_dt,
        })

    print(f"[News] {url} → {len(items)} items ({skipped_stale} skipped as stale)")
    return items


async def get_news_data(apply_history_filter: bool = False) -> dict[str, list[dict]]:
    """
    Fetch ALL sources concurrently, dedupe within this run, optionally drop
    anything already shown in a previous digest (apply_history_filter — used
    by the scheduled daily post, not $news), group by category, and sort each
    category newest-first.
    """
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[fetch_rss(url, session) for url in TECH_SOURCES],
            return_exceptions=True,
        )

    all_items: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
        else:
            print(f"[News] Source raised exception: {r}")

    # Deduplicate by title (case-insensitive) — within this run only.
    seen         = set()
    unique_items = []
    for item in all_items:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    if apply_history_filter:
        unique_items = await filter_unsent(unique_items)

    # Group by category
    grouped: dict[str, list] = {}
    for item in unique_items:
        grouped.setdefault(item["category"], []).append(item)

    # Newest first within each category. Items with no known publish time sort
    # last rather than being dropped.
    epoch_min = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    for items in grouped.values():
        items.sort(key=lambda i: i["published_dt"] or epoch_min, reverse=True)

    return grouped


# ── Digest formatting (Discord embeds) ─────────────────────────────────────────

def clean_title(title: str, limit: int = 110) -> str:
    """Strip brackets that would break markdown link syntax and cap the length."""
    title = title.replace("[", "(").replace("]", ")").strip()
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def today_label() -> str:
    """Human-readable UTC date for digest headers."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%A, %B %d, %Y")


def build_digest_embeds(
    grouped: dict[str, list],
    only_category: str | None = None,
) -> list[discord.Embed]:
    """
    Turn {category: [items]} into one colour-coded embed per category, in a fixed
    AI → Cybersecurity → Tech order. Pass only_category ('ai', 'cybersecurity', 'tech')
    to build just that category's card. Always returns at least one embed.
    """
    embeds: list[discord.Embed] = []
    total = 0

    for cat in (c.upper() for c in CATEGORIES):
        items = grouped.get(cat)
        if not items:
            continue
        if only_category and cat != only_category.upper():
            continue

        meta  = CATEGORY_META.get(cat, DEFAULT_META)
        shown = items[:MAX_PER_CATEGORY]
        total += len(items)

        headlines = "\n".join(
            f"**{i}.** [{clean_title(item['title'])}]({item['link']})"
            for i, item in enumerate(shown, start=1)
        )

        embeds.append(discord.Embed(
            title=meta["label"],
            description=headlines,
            color=meta["color"],
        ))

    if not embeds:
        message = (f"No **{only_category}** articles found this time — try again later."
                   if only_category else
                   "Couldn't fetch any articles this time. Try again later.")
        return [discord.Embed(title="No news right now", description=message,
                              color=discord.Color.orange())]

    footer = (f"{total} headline{'s' if total != 1 else ''} found"
              if only_category else
              f"{total} headlines tracked across {len(TECH_SOURCES)} sources")
    embeds[-1].set_footer(text=footer)
    embeds[-1].timestamp = datetime.datetime.now(datetime.timezone.utc)

    return embeds


def shown_items_from_grouped(grouped: dict[str, list]) -> list[dict]:
    """The subset of items that actually made it into build_digest_embeds
    (i.e. respecting the MAX_PER_CATEGORY cap) — this, not the full fetched
    pool, is what should get marked as sent."""
    return [item for items in grouped.values() for item in items[:MAX_PER_CATEGORY]]


async def send_digest(send, embeds: list[discord.Embed], content: str | None = None) -> None:
    """
    Send embeds using as few messages as possible, only splitting into additional
    messages if Discord's per-message limits (10 embeds / ~6000 chars) would be
    exceeded. `send` is a bound method like channel.send or ctx.send.
    """
    batches: list[list[discord.Embed]] = []
    current: list[discord.Embed] = []
    current_chars = 0

    for embed in embeds:
        embed_chars = len(embed)
        if current and (len(current) >= MAX_EMBEDS_PER_MESSAGE
                         or current_chars + embed_chars > MAX_EMBED_CHARS_PER_MESSAGE):
            batches.append(current)
            current, current_chars = [], 0
        current.append(embed)
        current_chars += embed_chars
    if current:
        batches.append(current)

    for i, batch in enumerate(batches):
        await send(content=content if i == 0 else None, embeds=batch)
        if i < len(batches) - 1:
            await asyncio.sleep(1)


# ── Cog ───────────────────────────────────────────────────────────────────────

class Updates(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Env var is the durable source of truth (survives Render restarts).
        # JSON file fills in anything added via commands during this session.
        env_channels  = parse_env_channels(NEWS_CHANNELS_ENV)
        file_channels = load_news_channels()
        self.news_channels: dict[str, int] = {**file_channels, **env_channels}

        self.last_sent_date: dict[str, datetime.date] = {}
        
        # Track last error notification per guild to avoid spam DMs.
        # {guild_id_str: datetime} — only send a new DM if last one was 5+ mins ago.
        self.last_error_notified: dict[str, datetime.datetime] = {}
        
        self.daily_news.start()

    def cog_unload(self):
        self.daily_news.cancel()

    async def notify_admin(self, guild_id: str, error_msg: str) -> None:
        """
        Send the admin a DM about an error in daily_news for a specific guild.
        Includes cooldown (5 mins) per guild to avoid spam if errors repeat.
        """
        if not ADMIN_USER_ID:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        last_notified = self.last_error_notified.get(guild_id)

        # Skip if we notified about this guild in the last 5 minutes
        if last_notified and (now - last_notified).total_seconds() < 300:
            return

        self.last_error_notified[guild_id] = now

        try:
            admin_user = await self.bot.fetch_user(ADMIN_USER_ID)
            await admin_user.send(
                f"**[KiwiBot News Error]**\n"
                f"Guild ID: `{guild_id}`\n"
                f"Time: <t:{int(now.timestamp())}:F>\n\n"
                f"{error_msg}"
            )
            print(f"[News]  Admin notified of error in guild {guild_id}")
        except discord.Forbidden:
            print(f"[News]  Can't DM admin {ADMIN_USER_ID} — they may have DMs disabled")
        except discord.NotFound:
            print(f"[News]  Admin user {ADMIN_USER_ID} not found")
        except Exception as e:
            print(f"[News]  Failed to notify admin: {type(e).__name__}: {e}")

    # ── Scheduled daily post ──────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def daily_news(self):
        if not self.news_channels:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        if now.hour != POST_HOUR_UTC or now.minute != POST_MINUTE_UTC:
            return

        grouped = await get_news_data(apply_history_filter=True)
        embeds  = build_digest_embeds(grouped)
        header  = f"**Daily Tech Digest** · {today_label()}"

        for guild_id_str, channel_id in list(self.news_channels.items()):
            if self.last_sent_date.get(guild_id_str) == now.date():
                continue

            channel = self.bot.get_channel(channel_id)
            if not channel:
                print(f"[News] Channel {channel_id} not found for guild {guild_id_str}. Skipping.")
                continue

            self.last_sent_date[guild_id_str] = now.date()
            print(f"[News] Sending digest to guild {guild_id_str} at {now.strftime('%H:%M UTC')}")

            try:
                await send_digest(channel.send, embeds, content=header)
            except discord.Forbidden:
                error_msg = (
                    f"**Missing Permissions**\n"
                    f"Channel: <#{channel_id}>\n\n"
                    f"Kiwi doesn't have permission to post messages. "
                    f"Check the channel permissions and ensure Kiwi has `Send Messages` enabled."
                )
                await self.notify_admin(guild_id_str, error_msg)
                print(f"[News] Missing permissions to post in {channel_id} (guild {guild_id_str})")
            except discord.HTTPException as e:
                # Covers 429 (rate limit), 500+ (server errors), network timeouts, etc.
                error_msg = (
                    f"**HTTP {e.status} Error**\n"
                    f"Channel: <#{channel_id}>\n\n"
                    f"```\n{str(e)[:200]}\n```\n\n"
                )
                if e.status == 429:
                    error_msg += (
                        " **Discord Rate Limit Hit**\n"
                        "This guild's digest failed due to a 429 rate limit. "
                        "This usually means Render's shared IP was flagged. Consider upgrading to paid Render or contacting Discord support."
                    )
                else:
                    error_msg += f"Discord API returned status {e.status}. Check server status or try again later."
                await self.notify_admin(guild_id_str, error_msg)
                print(f"[News]  HTTP {e.status} error posting to guild {guild_id_str}: {e}")
            except Exception as e:
                # Covers asyncio errors, timeout errors, or anything else unexpected
                error_msg = (
                    f"**Unexpected Error**\n"
                    f"Channel: <#{channel_id}>\n"
                    f"Error Type: `{type(e).__name__}`\n\n"
                    f"```\n{str(e)[:200]}\n```"
                )
                await self.notify_admin(guild_id_str, error_msg)
                print(f"[News]  Error sending to guild {guild_id_str}: {type(e).__name__}: {e}")

        # Mark whatever actually appeared in the digest as sent, once — not
        # per guild, since it's the same content everywhere. Marked even if
        # some guild sends failed — the dedup is about "what the digest tried
        # to share", not "what every guild successfully received".
        await mark_sent(shown_items_from_grouped(grouped))

    @daily_news.before_loop
    async def before_daily_news(self):
        await self.bot.wait_until_ready()

    # ── $news ─────────────────────────────────────────────────────────────────

    @commands.command(name="news")
    async def news_cmd(self, ctx: commands.Context, category: str = "all"):
        """
        Fetch the latest tech news.
        Usage: $news [ai | cybersecurity | tech | all]
        """
        category = category.lower()

        if category != "all" and category not in VALID_CATEGORIES:
            options = ", ".join(f"`{c}`" for c in VALID_CATEGORIES)
            await ctx.send(embed=discord.Embed(
                description=f"Unknown category `{category}`. Try `all`, {options}.",
                color=discord.Color.red(),
            ))
            return

        # Deliberately NOT using apply_history_filter here — $news is
        # on-demand, so it always shows the freshest matching headlines
        # regardless of what the scheduled digest already posted. Only the
        # scheduled daily post dedupes against history.
        async with ctx.channel.typing():
            grouped = await get_news_data()

        embeds = build_digest_embeds(grouped, only_category=None if category == "all" else category)

        if category == "all":
            header = f"**Daily Tech Digest** · {today_label()}"
        else:
            header = f"**Latest {category.upper()} headlines**"

        await send_digest(ctx.send, embeds, content=header)

    # ── $setnewschannel ───────────────────────────────────────────────────────

    @commands.command(name="setnewschannel")
    @commands.has_permissions(administrator=True)
    async def set_news_channel(self, ctx: commands.Context):
        """Set this channel as the daily news channel for this server. Usage: $setnewschannel"""
        guild_id_str = str(ctx.guild.id)
        self.news_channels[guild_id_str] = ctx.channel.id
        save_news_channels(self.news_channels)

        env_str = channels_to_env_string(self.news_channels)
        await ctx.send(embed=discord.Embed(
            description=f"Daily news will be posted in {ctx.channel.mention} at **17:30 IST** daily.",
            color=discord.Color.green(),
        ))
        try:
            await ctx.author.send(
                f"**Render env var update needed** (sent privately):\n"
                f"Set `NEWS_CHANNELS` to:\n```\n{env_str}\n```"
            )
        except discord.Forbidden:
            await ctx.send("Couldn't DM you the Render config — please enable DMs from server members.")

    # ── $removenewschannel ────────────────────────────────────────────────────

    @commands.command(name="removenewschannel")
    @commands.has_permissions(administrator=True)
    async def remove_news_channel(self, ctx: commands.Context):
        """Stop daily news posts for this server only. Usage: $removenewschannel"""
        guild_id_str = str(ctx.guild.id)
        if guild_id_str not in self.news_channels:
            await ctx.send(embed=discord.Embed(
                description="No news channel is set for this server.",
                color=discord.Color.red(),
            ))
            return
        del self.news_channels[guild_id_str]
        self.last_sent_date.pop(guild_id_str, None)
        save_news_channels(self.news_channels)

        env_str = channels_to_env_string(self.news_channels)
        await ctx.send(embed=discord.Embed(
            description="Daily news disabled for this server. Use `$setnewschannel` to re-enable.",
            color=discord.Color.dark_grey(),
        ))
        try:
            if env_str:
                await ctx.author.send(
                    f"**Render env var update needed** (sent privately):\n"
                    f"Update `NEWS_CHANNELS` to:\n```\n{env_str}\n```"
                )
            else:
                await ctx.author.send(
                    "**Render env var update needed** (sent privately):\n"
                    "Remove the `NEWS_CHANNELS` env variable on Render — no servers left."
                )
        except discord.Forbidden:
            await ctx.send("Couldn't DM you the Render config — please enable DMs from server members.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Updates(bot))