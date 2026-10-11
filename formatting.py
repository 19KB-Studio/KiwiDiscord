# formatting.py
import discord


def build_event_embed(event: dict, days_out: int, live: bool = False) -> discord.Embed:
    kind = "Online" if not event["onsite"] else f"Onsite — {event['location']}"

    if live:
        status = f"**LIVE NOW** — ends <t:{int(event['finish_time'].timestamp())}:R>"
        color = discord.Color.red()
    else:
        status = f"Starts in **{days_out} day{'s' if days_out != 1 else ''}**"
        color = discord.Color.green()

    embed = discord.Embed(
        title=event["name"],
        url=event["ctftime_url"],
        description=status,
        color=color,
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
    if event.get("logo"):
        embed.set_thumbnail(url=event["logo"])
    embed.set_footer(text=f"CTFtime restrictions: {event.get('restrictions', 'Open')}")

    return embed