"""
Append-only, idempotent load: JSONL event files -> raw.agent_events.

This is the third orchestration pattern across the three portfolio
projects, and deliberately different from the other two:
  - Project 1 (synthetic hotel): daily incremental append, new rows only.
  - Project 2 (real hotel booking data): full-refresh, static source.
  - Project 3 (this one): append-only upsert keyed on event_id.

Events arrive as JSONL files (agentobs's native output - one real log per
agent/client, plus the synthetic demo file), and the same file can be
re-loaded safely: `ON CONFLICT (event_id) DO NOTHING` makes re-running
this script a no-op for events it's already seen, which is exactly the
behavior you want for a log-tailing/cron-based ingestion pattern (no
"only load files newer than X" bookkeeping needed).

Usage:
    python scripts/load_events.py [path-or-glob ...]
    # defaults to loading every *.jsonl file under data/events/
"""
import glob
import json
import sys

import pandas as pd
from sqlalchemy import text

sys.path.append(".")
from scripts.db import get_engine

DEFAULT_GLOB = "data/events/*.jsonl"

INSERT_SQL = text("""
    INSERT INTO raw.agent_events (
        event_id, event_timestamp, client_id, agent_name, task_type, model,
        input_tokens, output_tokens, latency_ms, success, error_type, error_message, metadata
    ) VALUES (
        :event_id, :event_timestamp, :client_id, :agent_name, :task_type, :model,
        :input_tokens, :output_tokens, :latency_ms, :success, :error_type, :error_message, :metadata
    )
    ON CONFLICT (event_id) DO NOTHING
""")


def _load_file(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            rows.append(
                {
                    "event_id": e["event_id"],
                    "event_timestamp": e["timestamp"],
                    "client_id": e.get("client_id"),
                    "agent_name": e.get("agent_name"),
                    "task_type": e["task_type"],
                    "model": e["model"],
                    "input_tokens": e.get("input_tokens"),
                    "output_tokens": e.get("output_tokens"),
                    "latency_ms": e.get("latency_ms"),
                    "success": e["success"],
                    "error_type": e.get("error_type"),
                    "error_message": e.get("error_message"),
                    "metadata": json.dumps(e["metadata"]) if e.get("metadata") else None,
                }
            )
    return rows


def main(patterns: list[str]):
    engine = get_engine()

    files = []
    for pattern in patterns:
        files.extend(sorted(glob.glob(pattern)))
    if not files:
        print(f"No files matched: {patterns}")
        return

    total_seen = 0
    with engine.begin() as conn:
        before = conn.execute(text("SELECT COUNT(*) FROM raw.agent_events")).scalar()

    for path in files:
        rows = _load_file(path)
        total_seen += len(rows)
        if not rows:
            continue
        with engine.begin() as conn:
            conn.execute(INSERT_SQL, rows)
        print(f"  processed {path}: {len(rows):,} events")

    with engine.begin() as conn:
        after = conn.execute(text("SELECT COUNT(*) FROM raw.agent_events")).scalar()

    print(
        f"Done. {total_seen:,} events read across {len(files)} file(s); "
        f"{after - before:,} new rows inserted (raw.agent_events now has {after:,} total). "
        f"{total_seen - (after - before):,} were already present and skipped."
    )


if __name__ == "__main__":
    patterns = sys.argv[1:] if len(sys.argv) > 1 else [DEFAULT_GLOB]
    main(patterns)
