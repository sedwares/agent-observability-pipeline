"""
Threshold-based alerting: daily cost spikes and error-rate spikes, per
client, posted to a webhook (Slack-compatible incoming-webhook JSON, or
any endpoint that accepts {"text": "..."}).

No webhook configured? It still runs and prints what it would have sent -
so this is safe to run in a demo, in CI, or by a client who hasn't set up
Slack yet, without silently doing nothing and without crashing.

Thresholds (override via environment or CLI flags):
    ALERT_DAILY_COST_THRESHOLD_USD   default: 1.00   (per client, per day)
    ALERT_ERROR_RATE_THRESHOLD       default: 0.15   (15%)
    ALERT_WEBHOOK_URL                 unset = dry-run (print only)

The $1.00 default is calibrated to this project's demo-scale synthetic
data (median per-client daily spend ~$0.26, p99 ~$0.84 - see README), NOT
a universal number. Set it from YOUR OWN clients' actual trailing-30-day
spend (a p99 or a manually agreed budget per client), or every alert
either never fires or fires constantly.

Usage:
    python scripts/check_alerts.py                  # checks the most recent day in the data
    python scripts/check_alerts.py --date 2026-09-11 # checks a specific day
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from sqlalchemy import text

sys.path.append(".")
from scripts.db import get_engine

DEFAULT_COST_THRESHOLD = float(os.environ.get("ALERT_DAILY_COST_THRESHOLD_USD", "1.00"))
DEFAULT_ERROR_RATE_THRESHOLD = float(os.environ.get("ALERT_ERROR_RATE_THRESHOLD", "0.15"))
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL")


def _post_webhook(message: str) -> None:
    if not WEBHOOK_URL:
        print("  [dry-run: ALERT_WEBHOOK_URL not set] would have posted:")
        print(f"    {message}")
        return
    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  posted alert (HTTP {resp.status})")
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        # Alerting infra failing shouldn't take down the pipeline run -
        # log it loudly and move on, same reasoning as a caught exception
        # anywhere else in a monitoring path.
        print(f"  WARNING: failed to post alert to webhook: {exc}")


def check_cost_spikes(engine, check_date, threshold):
    query = text("""
        select client_id, sum(total_cost_usd) as day_cost
        from analytics.mart_daily_cost
        where event_date = :check_date
        group by client_id
        having sum(total_cost_usd) > :threshold
        order by day_cost desc
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"check_date": check_date, "threshold": threshold}).fetchall()

    for client_id, day_cost in rows:
        msg = (
            f":rotating_light: *Cost alert* — `{client_id}` spent "
            f"${day_cost:.2f} on {check_date} (threshold: ${threshold:.2f}/day)"
        )
        print(msg)
        _post_webhook(msg)
    return len(rows)


def check_error_rate_spikes(engine, check_date, threshold):
    query = text("""
        select client_id, sum(calls) as calls, sum(failed_calls) as failed_calls,
               sum(failed_calls)::numeric / nullif(sum(calls), 0) as error_rate
        from analytics.mart_daily_cost
        where event_date = :check_date
        group by client_id
        having sum(calls) >= 5  -- ignore near-zero-volume noise
           and sum(failed_calls)::numeric / nullif(sum(calls), 0) > :threshold
        order by error_rate desc
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"check_date": check_date, "threshold": threshold}).fetchall()

    for client_id, calls, failed_calls, error_rate in rows:
        msg = (
            f":warning: *Error rate alert* — `{client_id}` had a "
            f"{error_rate * 100:.1f}% error rate on {check_date} "
            f"({failed_calls}/{calls} calls failed, threshold: {threshold * 100:.0f}%)"
        )
        print(msg)
        _post_webhook(msg)
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to the most recent date in mart_daily_cost")
    parser.add_argument("--cost-threshold", type=float, default=DEFAULT_COST_THRESHOLD)
    parser.add_argument("--error-rate-threshold", type=float, default=DEFAULT_ERROR_RATE_THRESHOLD)
    args = parser.parse_args()

    engine = get_engine()

    if args.date:
        check_date = args.date
    else:
        with engine.connect() as conn:
            check_date = conn.execute(text("select max(event_date) from analytics.mart_daily_cost")).scalar()
        if check_date is None:
            print("No data in analytics.mart_daily_cost yet — run the dbt models first.")
            return

    print(f"Checking {check_date} against cost > ${args.cost_threshold:.2f}/day "
          f"and error rate > {args.error_rate_threshold * 100:.0f}%...")

    n_cost = check_cost_spikes(engine, check_date, args.cost_threshold)
    n_error = check_error_rate_spikes(engine, check_date, args.error_rate_threshold)

    total = n_cost + n_error
    print(f"Done. {total} alert(s) fired ({n_cost} cost, {n_error} error-rate).")


if __name__ == "__main__":
    main()
