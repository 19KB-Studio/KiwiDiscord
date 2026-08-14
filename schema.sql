-- Run once against your Neon database (psql "$DATABASE_URL" -f schema.sql)

CREATE TABLE IF NOT EXISTS ctf_events (
    id              BIGINT PRIMARY KEY,        -- CTFtime event id
    name            TEXT NOT NULL,
    ctftime_url     TEXT,
    event_url       TEXT,
    format          TEXT,                      -- "Jeopardy" / "Attack-Defense" / "Mixed"
    onsite          BOOLEAN NOT NULL,
    location        TEXT,                      -- only meaningful when onsite = true
    restrictions    TEXT,                      -- CTFtime's "Open" / "Academic" / "Invitation only" etc.
    weight          REAL,
    start_time      TIMESTAMPTZ NOT NULL,
    finish_time     TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (event, guild) once a notification has been sent — dedup key.
CREATE TABLE IF NOT EXISTS ctf_notifications (
    event_id        BIGINT NOT NULL REFERENCES ctf_events(id),
    guild_id        BIGINT NOT NULL,
    channel_id      BIGINT NOT NULL,
    notified_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, guild_id)
);

-- Per-guild config: where to post, and whether they want offline-India events too.
CREATE TABLE IF NOT EXISTS ctf_channels (
    guild_id                BIGINT PRIMARY KEY,
    channel_id              BIGINT NOT NULL,
    notify_offline_india    BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ctf_events_start_time ON ctf_events (start_time);