"""
Thin client for the CTFtime public API.

Docs: https://ctftime.org/api/
No auth required, but CTFtime rejects requests with no/generic User-Agent
with a 403 — always send one identifying the bot.
"""

import datetime
import aiohttp

CTFTIME_EVENTS_URL = "https://ctftime.org/api/v1/events/"
HEADERS = {"User-Agent": "discord-ctf-tracker/1.0 (contact:https://github.com/LostDiscipleAkaza )"}


async def fetch_upcoming_events(limit: int = 100) -> list[dict]:
    """
    Fetch upcoming events from CTFtime and normalize them into the shape
    ctf.db.upsert_events() expects.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {
        "limit": str(limit),
        "start": str(int(now.timestamp())),
        "finish": str(int((now + datetime.timedelta(days=30)).timestamp())),
    }

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(
                CTFTIME_EVENTS_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    print(f"[CTFtime] Unexpected status {resp.status}")
                    return []
                raw = await resp.json()
        except Exception as e:
            print(f"[CTFtime] Fetch failed: {e}")
            return []

    events = []
    for item in raw:
        try:
            events.append({
                "id":            item["id"],
                "name":          item["title"],
                "ctftime_url":   f"https://ctftime.org/event/{item['id']}",
                "event_url":     item.get("url") or "",
                "format":        item.get("format", "Unknown"),
                "onsite":        bool(item.get("onsite", False)),
                "location":      item.get("location") or "",
                "restrictions":  item.get("restrictions", "Open"),
                "weight":        item.get("weight", 0.0),
                "start_time":    datetime.datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                "finish_time":   datetime.datetime.fromisoformat(item["finish"].replace("Z", "+00:00")),
            })
        except (KeyError, ValueError) as e:
            print(f"[CTFtime] Skipping malformed event: {e}")
            continue

    return events