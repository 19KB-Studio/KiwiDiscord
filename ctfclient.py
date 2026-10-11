# ctfclient.py
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


async def fetch_upcoming_events(limit: int = 100, days_back: int = 3, days_forward: int = 30) -> list[dict]:
    """
    Fetch events from CTFtime and normalize them into the shape
    ctf.db.upsert_events() expects.

    days_back widens the query window into the past so events that have
    already started (but not finished) are still returned — needed to
    detect "live" CTFs, since CTFtime's `start` param filters on the
    event's start time, not whether it's currently running. 3 days covers
    the overwhelming majority of CTFs (most run 24-72h); a CTF longer than
    that won't be picked up as "live" once its start falls outside this
    window — acceptable for the current use case but worth knowing if you
    add longer-format events later.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {
        "limit": str(limit),
        "start": str(int((now - datetime.timedelta(days=days_back)).timestamp())),
        "finish": str(int((now + datetime.timedelta(days=days_forward)).timestamp())),
    }

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(
                CTFTIME_EVENTS_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    print(f"[CTFtime] Unexpected status {resp.status}")
                    return []
                raw = await resp.json(content_type=None)
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
                "logo":          item.get("logo") or "",
                "start_time":    datetime.datetime.fromisoformat(item["start"].replace("Z", "+00:00")),
                "finish_time":   datetime.datetime.fromisoformat(item["finish"].replace("Z", "+00:00")),
            })
        except (KeyError, ValueError) as e:
            print(f"[CTFtime] Skipping malformed event: {e}")
            continue

    return events