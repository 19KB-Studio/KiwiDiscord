"""
Neon Postgres access for the CTF tracker.

IMPORTANT: we deliberately do NOT keep a persistent connection pool alive
for the lifetime of the bot process. Neon's free tier scales compute to
zero after ~5 minutes of no active connections, and an idle pooled
connection prevents that from happening — which quietly burns CU-hours
for no reason on a bot that only needs to touch the DB a few times an
hour. Every function below opens a connection, does its work, and closes
it immediately.
"""

import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")  # Neon connection string, sslmode=require


async def connect() -> asyncpg.Connection:
    """
    Open a single short-lived connection. Caller closes it when done.

    For a batch of related operations (e.g. one scheduler run touching many
    events/guilds), open ONE connection here and reuse it for every query in
    that run, then close it. Opening/closing a fresh connection per query
    when you're about to run dozens of queries back-to-back is pure TLS
    handshake overhead for no benefit — what actually matters for Neon's
    scale-to-zero is not holding a connection open *between* runs.
    """
    url = os.getenv("DATABASE_URL") or DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return await asyncpg.connect(url)


# ── Channel config (low frequency, self-contained) ───────────────────────

async def set_ctf_channel(guild_id: int, channel_id: int, notify_offline_india: bool = True):
    conn = await connect()
    try:
        await conn.execute(
            """
            INSERT INTO ctf_channels (guild_id, channel_id, notify_offline_india, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (guild_id)
            DO UPDATE SET channel_id = $2, notify_offline_india = $3, updated_at = now()
            """,
            guild_id, channel_id, notify_offline_india,
        )
    finally:
        await conn.close()
        
async def remove_ctf_channel(guild_id: int) -> bool:
    """Delete this guild's CTF config. Returns True if a row existed."""
    conn = await connect()
    try:
        result = await conn.execute(
            "DELETE FROM ctf_channels WHERE guild_id = $1", guild_id
        )
    finally:
        await conn.close()
    # asyncpg returns a status string like "DELETE 1" or "DELETE 0"
    return int(result.split()[-1]) > 0

# ── Everything below takes an existing connection — the scheduler opens   ──
# ── one connection per run and passes it through, see ctf.py              ──

async def get_all_ctf_channels(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("SELECT guild_id, channel_id, notify_offline_india FROM ctf_channels")
    return [dict(r) for r in rows]


async def upsert_events(conn: asyncpg.Connection, events: list[dict]):
    """events: list of dicts matching the ctf_events columns."""
    if not events:
        return
    await conn.executemany(
        """
        INSERT INTO ctf_events
            (id, name, ctftime_url, event_url, format, onsite, location,
             restrictions, weight, start_time, finish_time, fetched_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
        ON CONFLICT (id) DO UPDATE SET
            name = $2, ctftime_url = $3, event_url = $4, format = $5,
            onsite = $6, location = $7, restrictions = $8, weight = $9,
            start_time = $10, finish_time = $11, fetched_at = now()
        """,
        [
            (
                e["id"], e["name"], e["ctftime_url"], e["event_url"], e["format"],
                e["onsite"], e["location"], e["restrictions"], e["weight"],
                e["start_time"], e["finish_time"],
            )
            for e in events
        ],
    )


async def get_notified_pairs(conn: asyncpg.Connection, event_ids: list[int]) -> set[tuple[int, int]]:
    """One query for every (event_id, guild_id) already notified, instead of
    one query per event per guild."""
    if not event_ids:
        return set()
    rows = await conn.fetch(
        "SELECT event_id, guild_id FROM ctf_notifications WHERE event_id = ANY($1::bigint[])",
        event_ids,
    )
    return {(r["event_id"], r["guild_id"]) for r in rows}


async def mark_notified_batch(conn: asyncpg.Connection, rows: list[tuple[int, int, int]]):
    """rows: list of (event_id, guild_id, channel_id) — one round trip for the
    whole batch instead of one insert per notification sent."""
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO ctf_notifications (event_id, guild_id, channel_id, notified_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (event_id, guild_id) DO NOTHING
        """,
        rows,
    )