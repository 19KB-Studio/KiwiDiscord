import discord
from discord.ext import commands
import asyncio


# ── Active roleplay sessions {channel_id: {"theme": str, "players": set}} ──────
roleplay_sessions: dict[int, dict] = {}


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════════
    # NICKNAME MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(name="nick")
    @commands.has_permissions(manage_nicknames=True)
    async def set_nick(self, ctx: commands.Context, member: discord.Member, *, nickname: str):
        """
        Set a member's nickname.
        Usage: $nick @user <nickname>
        """
        try:
            await member.edit(nick=nickname)
            await ctx.send(f"✅ Nickname for {member.mention} set to **{nickname}**.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to change that member's nickname.")

    @commands.command(name="resetnick")
    @commands.has_permissions(manage_nicknames=True)
    async def reset_nick(self, ctx: commands.Context, member: discord.Member):
        """
        Reset a member's nickname to their username.
        Usage: $resetnick @user
        """
        try:
            await member.edit(nick=None)
            await ctx.send(f"✅ Nickname for {member.mention} has been reset.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to reset that member's nickname.")

    @commands.command(name="mynick")
    async def my_nick(self, ctx: commands.Context, *, nickname: str):
        """
        Set your own nickname.
        Usage: $mynick <nickname>
        """
        try:
            await ctx.author.edit(nick=nickname)
            await ctx.send(f"✅ Your nickname has been set to **{nickname}**.")
        except discord.Forbidden:
            await ctx.send("❌ I can't change your nickname (you may be the server owner).")

    # ══════════════════════════════════════════════════════════════════════════
    # CHANNEL MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int = 10):
        """
        Delete the last N messages (max 100).
        Usage: $purge [amount]
        """
        amount = min(max(amount, 1), 100)
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 to include command msg
        msg = await ctx.send(f"🗑️ Deleted **{len(deleted) - 1}** messages.")
        await asyncio.sleep(3)
        await msg.delete()

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: int = 0):
        """
        Set the current channel's slowmode delay (0 disables it).
        Usage: $slowmode [seconds]   (0–21600, Discord's max)
        """
        seconds = min(max(seconds, 0), 21600)
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("🐇 Slowmode disabled.")
        else:
            await ctx.send(f"🐢 Slowmode set to **{seconds}s**.")

    @commands.command(name="createchannel")
    @commands.has_permissions(manage_channels=True)
    async def create_channel(self, ctx: commands.Context, kind: str, *, name: str):
        """
        Create a text or voice channel.
        Usage: $createchannel text|voice <name>
        """
        kind = kind.lower()
        if kind not in ("text", "voice"):
            await ctx.send("❌ Usage: `$createchannel text|voice <name>`")
            return

        try:
            if kind == "text":
                channel = await ctx.guild.create_text_channel(name)
            else:
                channel = await ctx.guild.create_voice_channel(name)
            await ctx.send(f"✅ Created {kind} channel {channel.mention if kind == 'text' else f'**{channel.name}**'}.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to create channels.")

    @commands.command(name="deletechannel")
    @commands.has_permissions(manage_channels=True)
    async def delete_channel(self, ctx: commands.Context, channel: discord.abc.GuildChannel = None):
        """
        Delete a channel (with confirmation). Defaults to the current channel.
        Usage: $deletechannel [#channel]
        """
        target = channel or ctx.channel

        await ctx.send(
            f"⚠️ Delete **#{target.name}**? Reply `yes` within 15 seconds to confirm."
        )

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == ctx.author.id
                and m.channel.id == ctx.channel.id
                and m.content.lower() == "yes"
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=15)
        except asyncio.TimeoutError:
            await ctx.send("❌ Deletion cancelled (no confirmation received).")
            return

        try:
            await target.delete()
            if target.id != ctx.channel.id:
                await ctx.send(f"✅ Deleted **#{target.name}**.")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to delete that channel.")


    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context):
        """
        Lock the current channel so only staff can send messages.
        Usage: $lock
        """
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send("🔒 Channel locked.")

    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context):
        """
        Unlock the current channel.
        Usage: $unlock
        """
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = True
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ctx.send("🔓 Channel unlocked.")


    # ══════════════════════════════════════════════════════════════════════════
    # ROLEPLAY
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(name="rp")
    async def roleplay_start(self, ctx: commands.Context, *, theme: str = "free-form"):
        """
        Start a roleplay session in this channel.
        Usage: $rp [theme]
        """
        cid = ctx.channel.id
        if cid in roleplay_sessions:
            await ctx.send("⚔️ A roleplay session is already active here. Use `$rpend` to stop it.")
            return
        roleplay_sessions[cid] = {"theme": theme, "players": {ctx.author.id}}
        await ctx.send(
            f"🎭 **Roleplay started!** Theme: *{theme}*\n"
            f"Use `$rpjoin` to join, `$rpsay <text>` to speak in character, and `$rpend` to end."
        )

    @commands.command(name="rpjoin")
    async def roleplay_join(self, ctx: commands.Context):
        """Join the active roleplay session. Usage: $rpjoin"""
        cid = ctx.channel.id
        if cid not in roleplay_sessions:
            await ctx.send("❌ No active roleplay session. Start one with `$rp [theme]`.")
            return
        roleplay_sessions[cid]["players"].add(ctx.author.id)
        await ctx.send(f"✅ {ctx.author.display_name} joined the roleplay!")

    @commands.command(name="rpsay")
    async def roleplay_say(self, ctx: commands.Context, *, message: str):
        """
        Speak in character during an active roleplay.
        Usage: $rpsay <message>
        """
        cid = ctx.channel.id
        if cid not in roleplay_sessions:
            await ctx.send("❌ No active roleplay session here.")
            return
        session = roleplay_sessions[cid]
        if ctx.author.id not in session["players"]:
            await ctx.send("❌ You haven't joined this session. Use `$rpjoin` first.")
            return
        # Delete the command message for immersion
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
        embed = discord.Embed(
            description=f"*{message}*",
            color=discord.Color.purple()
        )
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Theme: {session['theme']}")
        await ctx.channel.send(embed=embed)

    @commands.command(name="rpend")
    async def roleplay_end(self, ctx: commands.Context):
        """End the active roleplay session. Usage: $rpend"""
        cid = ctx.channel.id
        if cid not in roleplay_sessions:
            await ctx.send("❌ No active roleplay session here.")
            return
        session = roleplay_sessions.pop(cid)
        players = len(session["players"])
        await ctx.send(
            f"🎭 Roleplay session ended. Theme was: *{session['theme']}* | Players: **{players}**"
        )

    @commands.command(name="rpstatus")
    async def roleplay_status(self, ctx: commands.Context):
        """Check the current roleplay session status. Usage: $rpstatus"""
        cid = ctx.channel.id
        if cid not in roleplay_sessions:
            await ctx.send("❌ No active roleplay session in this channel.")
            return
        session = roleplay_sessions[cid]
        player_mentions = []
        for uid in session["players"]:
            member = ctx.guild.get_member(uid)
            player_mentions.append(member.display_name if member else f"<unknown:{uid}>")
        await ctx.send(
            f"🎭 **Active Roleplay**\n"
            f"Theme: *{session['theme']}*\n"
            f"Players ({len(player_mentions)}): {', '.join(player_mentions)}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ERROR HANDLING
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument. Check `$help {ctx.command.name}` for usage.")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ Member not found.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Invalid argument provided.")
        # Let other errors propagate normally
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
