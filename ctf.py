import datetime

import discord
from discord.ext import commands, tasks

import db
import ctfclient as ctftime_client
import filters
import formatting

CHECK_INTERVAL_HOURS = 6
NOTIFY_WINDOW = (7, 10)  # days-out range for a first notification


class CTFTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_ctf_events.start()

    def cog_unload(self):
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

        # One connection for the whole run: upsert, two reads, one batched
        # write. This replaces what was previously up to (events × guilds)
        # separate connections.
        conn = await db.connect()
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
        await db.set_ctf_channel(ctx.guild.id, ctx.channel.id, notify_india)
        await ctx.send(
            f"✅ CTF notifications will be posted in {ctx.channel.mention}. "
            f"Offline India events: {'included' if notify_india else 'excluded'}."
        )

    @commands.command(name="ctfupcoming")
    async def ctf_upcoming(self, ctx: commands.Context):
        """List upcoming CTFs matching your filters, regardless of the notify window."""
        events = await ctftime_client.fetch_upcoming_events()
        now = datetime.datetime.now(datetime.timezone.utc)

        matches = [
            e for e in events
            if (filters.is_open_online(e) or filters.is_offline_india(e))
            and filters.days_until_start(e, now) >= 0
        ]
        matches.sort(key=lambda e: e["start_time"])

        if not matches:
            await ctx.send("No matching upcoming CTFs found right now.")
            return

        for event in matches[:5]:
            days_out = filters.days_until_start(event, now)
            await ctx.send(embed=formatting.build_event_embed(event, days_out))


async def setup(bot: commands.Bot):
    await bot.add_cog(CTFTracker(bot))