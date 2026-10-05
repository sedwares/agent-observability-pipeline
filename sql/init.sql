-- Raw landing schema for agent observability events. Loaded from JSONL
-- (agentobs's native output format) rather than a live stream connector -
-- see README for why that's the right call at this project's scale, and
-- how to swap in Kafka if/when volume justifies it.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS raw.agent_events (
    event_id        TEXT PRIMARY KEY,   -- UUID from agentobs; the natural idempotency key
    event_timestamp TIMESTAMPTZ NOT NULL,
    client_id       TEXT,
    agent_name      TEXT,
    task_type       TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      NUMERIC,
    success         BOOLEAN NOT NULL,
    error_type      TEXT,
    error_message   TEXT,
    metadata        JSONB,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_events_timestamp ON raw.agent_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_events_client ON raw.agent_events(client_id);
