import discord


def build_event_embed(event: dict, days_out: int) -> discord.Embed:
    kind = "🌐 Online" if not event["onsite"] else f"📍 Onsite — {event['location']}"

    embed = discord.Embed(
        title=f"🚩 {event['name']}",
        url=event["ctftime_url"],
        description=f"Starts in **{days_out} day{'s' if days_out != 1 else ''}**",
        color=discord.Color.green(),
    )
    embed.add_field(name="Format", value=event["format"] or "Unknown", inline=True)
    embed.add_field(name="Type", value=kind, inline=True)
    embed.add_field(name="Weight", value=str(event["weight"]), inline=True)
    embed.add_field(
        name="Starts",
        value=f"<t:{int(event['start_time'].timestamp())}:F>",
        inline=False,
    )
    if event.get("event_url"):
        embed.add_field(name="Event site", value=event["event_url"], inline=False)
    embed.set_footer(text=f"CTFtime restrictions: {event.get('restrictions', 'Open')}")

    return embed