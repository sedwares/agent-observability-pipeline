"""
Synthetic agent-run event generator.

Produces ~90 days of realistic multi-client agent activity in the same
JSONL schema agentobs.AgentTracker writes for real calls — so the loader
and every downstream dbt model treat real and synthetic events identically.
This exists purely to give the warehouse and dashboard enough history to
be demo-able; a freelancer's own real usage (via agentobs) is what would
actually feed this in production.

Includes two deliberately injected patterns so the alerting/anomaly parts
of the pipeline have something real to catch:
  - A multi-day error-rate spike for one client (simulates an upstream
    provider incident or a bad prompt-template deploy).
  - A single-day cost spike for one client (simulates an unbounded loop
    or an accidental batch re-run).

Usage:
    python scripts/generate_synthetic_events.py
"""
import json
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone

OUT_PATH = "data/events/synthetic_events.jsonl"
DAYS_OF_HISTORY = 90

CLIENTS = [
    # (client_id, display industry, relative call-volume weight)
    ("northgate_realty", "real_estate", 1.0),
    ("brightpath_legal", "legal_services", 0.8),
    ("meridian_commerce", "ecommerce", 1.4),
    ("harborview_health_admin", "healthcare_admin", 0.6),
    ("solstice_marketing", "marketing_agency", 1.1),
    ("fernbank_saas", "saas_support", 1.6),
]

TASK_TYPES = [
    # (task_type, avg_input_tokens, avg_output_tokens, base_failure_rate)
    ("summarize_doc", 1400, 320, 0.02),
    ("classify_ticket", 300, 40, 0.015),
    ("extract_data", 900, 180, 0.03),
    ("draft_email", 500, 260, 0.02),
    ("research_query", 2200, 650, 0.04),
    ("code_review", 1800, 450, 0.025),
    ("customer_support_reply", 600, 200, 0.02),
]

MODELS = [
    # (model, relative usage weight, latency_ms mean, latency_ms stdev)
    ("claude-haiku-4-5", 0.45, 650, 180),
    ("claude-sonnet-5", 0.40, 1400, 400),
    ("claude-opus-5-5", 0.15, 3200, 900),
]

ERROR_TYPES = ["RateLimitError", "TimeoutError", "APIConnectionError", "OverloadedError"]

random.seed(42)  # reproducible demo data


def _pick_client():
    return random.choices(CLIENTS, weights=[c[2] for c in CLIENTS], k=1)[0]


def _pick_task():
    return random.choice(TASK_TYPES)


def _pick_model():
    return random.choices(MODELS, weights=[m[1] for m in MODELS], k=1)[0]


def generate_events_for_day(day: date, incident_client: str, incident_days: set, spike_day: date | None, spike_client: str):
    events = []

    # Business grows over the window: more calls per day in recent history.
    days_ago = (date.today() - day).days
    growth_factor = 1.0 + (DAYS_OF_HISTORY - days_ago) / DAYS_OF_HISTORY * 0.8
    weekday_factor = 0.55 if day.weekday() >= 5 else 1.0
    n_calls = max(5, int(random.gauss(140, 25) * growth_factor * weekday_factor))

    for _ in range(n_calls):
        client_id, industry, _ = _pick_client()
        task_type, avg_in, avg_out, base_fail_rate = _pick_task()
        model, _, lat_mean, lat_std = _pick_model()

        fail_rate = base_fail_rate
        if client_id == incident_client and day in incident_days:
            fail_rate = 0.35  # simulated incident: error rate spikes hard

        input_tokens = max(10, int(random.gauss(avg_in, avg_in * 0.3)))
        output_tokens = max(5, int(random.gauss(avg_out, avg_out * 0.35)))

        if spike_day and day == spike_day and client_id == spike_client:
            # simulated cost spike: an unbounded loop firing way more
            # large-output calls than normal for one client on one day
            output_tokens = int(output_tokens * random.uniform(4, 7))

        success = random.random() > fail_rate
        latency_ms = max(50, round(random.gauss(lat_mean, lat_std), 1))
        if not success:
            latency_ms = max(latency_ms, lat_mean * random.uniform(0.8, 1.5))  # failures often still cost time

        ts = datetime.combine(day, time(0, 0), tzinfo=timezone.utc) + timedelta(
            seconds=random.uniform(0, 86399)
        )

        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": ts.isoformat(),
            "client_id": client_id,
            "agent_name": f"{industry}_agent",
            "task_type": task_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens if success else int(output_tokens * random.uniform(0, 0.4)),
            "latency_ms": latency_ms,
            "success": success,
            "error_type": None if success else random.choice(ERROR_TYPES),
            "error_message": None if success else "simulated failure for demo data",
            "metadata": {"industry": industry},
        }
        events.append(event)

    return events


def main():
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=DAYS_OF_HISTORY - 1)

    incident_client = "fernbank_saas"
    incident_start = start_date + timedelta(days=int(DAYS_OF_HISTORY * 0.55))
    incident_days = {incident_start + timedelta(days=i) for i in range(3)}

    spike_client = "meridian_commerce"
    spike_day = start_date + timedelta(days=int(DAYS_OF_HISTORY * 0.8))

    all_events = []
    d = start_date
    while d <= end_date:
        all_events.extend(
            generate_events_for_day(d, incident_client, incident_days, spike_day, spike_client)
        )
        d += timedelta(days=1)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for e in all_events:
            f.write(json.dumps(e) + "\n")

    print(f"Generated {len(all_events):,} synthetic events across {DAYS_OF_HISTORY} days -> {OUT_PATH}")
    print(f"  Injected error-rate incident: {incident_client}, {sorted(incident_days)}")
    print(f"  Injected cost spike: {spike_client}, {spike_day}")


if __name__ == "__main__":
    main()
