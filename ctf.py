# ctf.py
import datetime

import discord
from discord.ext import commands, tasks

import db
import ctfclient as ctftime_client
import filters
import formatting

CHECK_INTERVAL_HOURS = 6
NOTIFY_WINDOW = (7, 10)  # days-out range for a first notification
UPCOMING_WINDOW_DAYS = 10  # for $ctfupcoming: 0 (starts later today) through this many days out
MAX_EMBEDS_PER_MESSAGE = 10  # Discord hard cap per message


class CTFTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if db.DATABASE_URL:
            self.check_ctf_events.start()
        else:
            print(
                "[CTF] DATABASE_URL is not set — CTF tracker background checks "
                "are disabled. $setctfchannel and $ctfupcoming that touch the "
                "DB will also fail until it's configured."
            )

    def cog_unload(self):
        if self.check_ctf_events.is_running():
            self.check_ctf_events.cancel()

    # ── Scheduled check ───────────────────────────────────────────────────

    @tasks.loop(hours=CHECK_INTERVAL_HOURS)
    async def check_ctf_events(self):
        events = await ctftime_client.fetch_upcoming_events()
        if not events:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        candidates = [
            e for e in events
            if NOTIFY_WINDOW[0] <= filters.days_until_start(e, now) <= NOTIFY_WINDOW[1]
            and (filters.is_open_online(e) or filters.is_offline_india(e))
        ]

        try:
            conn = await db.connect()
        except RuntimeError as e:
            print(f"[CTF] Skipping this run — {e}")
            return

        try:
            await db.upsert_events(conn, events)

            if not candidates:
                return

            channels = await db.get_all_ctf_channels(conn)
            if not channels:
                return

            candidate_ids = [e["id"] for e in candidates]
            notified_pairs = await db.get_notified_pairs(conn, candidate_ids)

            to_send = []  # (event, days_out, channel, cfg)
            for event in candidates:
                days_out = filters.days_until_start(event, now)
                online_ok = filters.is_open_online(event)
                india_ok = filters.is_offline_india(event)

                for cfg in channels:
                    if india_ok and not online_ok and not cfg["notify_offline_india"]:
                        continue  # guild opted out of offline-India events
                    if (event["id"], cfg["guild_id"]) in notified_pairs:
                        continue
                    channel = self.bot.get_channel(cfg["channel_id"])
                    if not channel:
                        print(f"[CTF] Channel {cfg['channel_id']} not found for guild {cfg['guild_id']}")
                        continue
                    to_send.append((event, days_out, channel, cfg))

            sent_rows = []  # (event_id, guild_id, channel_id) for the ones that actually sent
            for event, days_out, channel, cfg in to_send:
                embed = formatting.build_event_embed(event, days_out)
                try:
                    await channel.send(embed=embed)
                    sent_rows.append((event["id"], cfg["guild_id"], cfg["channel_id"]))
                except discord.Forbidden:
                    print(f"[CTF] Missing permission to post in channel {cfg['channel_id']}")

            await db.mark_notified_batch(conn, sent_rows)
        finally:
            await conn.close()

    @check_ctf_events.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ── Commands ──────────────────────────────────────────────────────────

    @commands.command(name="setctfchannel")
    @commands.has_permissions(administrator=True)
    async def set_ctf_channel(self, ctx: commands.Context, offline_india: str = "yes"):
        """
        Set this channel for CTF notifications.
        Usage: $setctfchannel [yes|no]   (yes/no = also notify offline India events)
        """
        notify_india = offline_india.lower() not in ("no", "false", "off")
        try:
            await db.set_ctf_channel(ctx.guild.id, ctx.channel.id, notify_india)
        except RuntimeError:
            await ctx.send("❌ CTF tracker isn't configured yet (`DATABASE_URL` is not set). Ask the bot owner to set it up.")
            return
        await ctx.send(
            f"✅ CTF notifications will be posted in {ctx.channel.mention}. "
            f"Offline India events: {'included' if notify_india else 'excluded'}."
        )

    @commands.command(name="ctfupcoming")
    async def ctf_upcoming(self, ctx: commands.Context):
        """
        List live and upcoming CTFs matching your filters: open-to-anyone
        online events, plus offline events happening in India. "Upcoming"
        covers the next 10 days; live events (already started, not yet
        finished) are shown first regardless of when they started.
        """
        events = await ctftime_client.fetch_upcoming_events()
        now = datetime.datetime.now(datetime.timezone.utc)

        live = []
        upcoming = []  # (event, days_out)
        for e in events:
            if not (filters.is_open_online(e) or filters.is_offline_india(e)):
                continue
            if filters.is_live(e, now):
                live.append(e)
                continue
            days_out = filters.days_until_start(e, now)
            if 0 <= days_out <= UPCOMING_WINDOW_DAYS:
                upcoming.append((e, days_out))

        if not live and not upcoming:
            await ctx.send(f"No live CTFs and nothing matching in the next {UPCOMING_WINDOW_DAYS} days right now.")
            return

        live.sort(key=lambda e: e["finish_time"])
        upcoming.sort(key=lambda pair: pair[0]["start_time"])

        embeds = [formatting.build_event_embed(e, 0, live=True) for e in live]
        embeds += [formatting.build_event_embed(e, days_out) for e, days_out in upcoming]

        for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
            await ctx.send(embeds=embeds[i:i + MAX_EMBEDS_PER_MESSAGE])


async def setup(bot: commands.Bot):
    await bot.add_cog(CTFTracker(bot))
