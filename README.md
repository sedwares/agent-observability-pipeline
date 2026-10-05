# Agent Observability Pipeline

A cost, latency, and reliability warehouse for LLM/agent calls — dbt +
Airflow + Postgres on the analytics side, plus a small dependency-free
Python library (`agentobs`) that actually instruments real agent calls.
Unlike the other two projects in this portfolio, this one is meant to be
used, not just demonstrated: point `agentobs` at your own agents, and the
same warehouse and dashboard that demo this project become real tooling
for tracking what an AI agent operation is actually costing and how well
it's performing, per client.

## Why this exists

LLMOps/agent-observability is a genuinely underserved crossover skill
right now — data engineering applied to AI systems, not just AI-adjacent
data. This project is built to do double duty: a portfolio piece that
shows dbt/Airflow/warehouse-modeling skills, and an actual tool that could
sit in front of an Upwork client relationship (`mart_client_cost_summary`
is deliberately the table you'd export and hand someone directly).

## Quickstart: instrument your own agent

```python
from agentobs import AgentTracker

tracker = AgentTracker(log_path="events.jsonl", client_id="acme_corp", agent_name="research_agent")

with tracker.track(task_type="summarize_doc", model="claude-sonnet-5") as run:
    response = anthropic_client.messages.create(...)
    run.record_usage(response)   # auto-detects Anthropic or OpenAI usage shapes
```

That's it — one context manager around the call site. On success it logs
latency, token counts, and your task/client metadata as one JSON line. On
an exception, it logs the failure (error type, message, latency up to the
failure point) and **re-raises** — it never swallows an error your agent
code needs to see. See `scripts/demo_instrumentation.py` for a runnable
example (works with or without a real `ANTHROPIC_API_KEY` — falls back to
a mock client so the pattern is visible either way), and `tests/test_tracker.py`
for the offline test suite that actually exercises the Anthropic-shaped
response, the OpenAI-shaped response, explicit token overrides, and the
failure path.

**Why cost isn't computed in the library itself:** prices change, and
baking a pricing table into code you'd have to re-deploy to update is the
wrong layer. `agentobs` logs raw token counts; cost is computed downstream
in dbt from `seeds/model_pricing.csv` — updating a rate is a one-line CSV
edit and a `dbt run`, not a code change.

## Architecture

```
   Your agents, instrumented with agentobs
              │  (JSON Lines, one event per call)
              ▼
     data/events/*.jsonl  ◄──── scripts/generate_synthetic_events.py
              │                  (90 days of demo data, same schema)
              ▼
   Postgres: raw.agent_events        append-only, idempotent load
              │                      (ON CONFLICT event_id DO NOTHING)
              ▼
    ┌──────────────────────┐
    │       Airflow           │  load events → dbt run → dbt test → check_alerts
    └──────────┬─────────────┘
               ▼
   ┌────────────────────────────────────────┐
   │              dbt: analytics.*             │
   │  stg_agent_events                         │
   │  dim_client, dim_model (pricing seed)     │
   │  fact_agent_runs (cost computed here)     │
   │  mart_daily_cost, mart_client_cost_summary │
   │  mart_model_performance, mart_error_analysis│
   └──────────────────┬─────────────────────────┘
                       ▼
        Streamlit Dashboard + scripts/check_alerts.py
                (cost-spike / error-rate webhooks)
```

### The third orchestration pattern

Each project in this portfolio uses a different, deliberately-matched
ingestion pattern instead of the same template three times:

| Project | Source shape | Pattern |
|---|---|---|
| 1 — Hotel Revenue Analytics | Simulated live PMS | Daily incremental append |
| 2 — Hotel Booking Demand | Static historical extract | Full-refresh (truncate + reload) |
| 3 — this one | Many small agents logging whenever they run | **Append-only, idempotent upsert** on `event_id` |

Event-shaped data doesn't arrive as one daily batch — it trickles in from
however many agents are running. `scripts/load_events.py` can be re-run
against the same files (or new ones) at any time; `ON CONFLICT (event_id)
DO NOTHING` makes re-processing a file it's already seen a no-op, so there's
no "only load files newer than X" bookkeeping needed. The Airflow DAG runs
hourly on this pattern, closer to real-time cost monitoring than an
end-of-day report.

## Why simple event logging, not Kafka

Kafka is the right infrastructure once event volume justifies running a
broker — but for the scale an individual freelancer or small agency
actually operates at (thousands, not millions, of calls/day), it's
infrastructure to babysit for no operational benefit, and it's one more
thing a client would need running to adopt this tool. JSON Lines is what
this volume actually needs: no broker, human-readable, trivially
`grep`-able, and a real client can point their own scripts at the same
log file if they ever want to. The swap-in point if volume ever justified
it: replace `agentobs`'s `open(...).write()` with a Kafka producer, and
replace `scripts/load_events.py`'s glob with a consumer — the event schema
and everything downstream (dbt, dashboard, alerting) doesn't change.

## What the pipeline actually caught

Built with 90 days of synthetic multi-client demo data (real usage from
`agentobs` would replace/supplement this), with two anomalies deliberately
injected to prove the alerting works rather than just asserting that it
does:

- **A 3-day error-rate incident** for one client (`fernbank_saas`,
  simulating an upstream provider issue or a bad prompt-template deploy).
  `scripts/check_alerts.py --date 2026-08-19` correctly flags it: *"34.1%
  error rate (15/44 calls failed, threshold: 15%)."*
- **A single-day cost spike** for another client (`meridian_commerce`,
  simulating an unbounded loop or accidental batch re-run) — real peak
  spend of $1.28 against a $0.26 median day. `check_alerts.py --date
  2026-09-11` correctly flags it: *"$1.28 spent (threshold: $1.00/day)."*
- An ordinary day in between checks clean — zero alerts, as it should.

The $1.00/threshold is calibrated to this demo data's scale (median
per-client daily spend ~$0.26, p99 ~$0.84), **not a universal number** —
see the calibration note in `scripts/check_alerts.py`. Set it from your
own clients' real trailing spend.

## Data model

| Layer | Models | Purpose |
|---|---|---|
| Staging | `stg_agent_events` | Clean/typed events, nulls coalesced to 'unknown' rather than dropped |
| Marts | `dim_client`, `dim_model` | Conformed dimensions; `dim_model` is the pricing seed |
| Marts | `fact_agent_runs` | One row per call, cost computed from the pricing seed, `is_pricing_known` flags gaps |
| Marts | `mart_daily_cost` | Calls, cost, error rate, latency by day/client/task |
| Marts | `mart_client_cost_summary` | **The table you'd hand a client** — spend, volume, reliability, one row per client |
| Marts | `mart_model_performance` | Cost, latency (avg + p95), error rate by model and task — "which model for this task" |
| Marts | `mart_error_analysis` | Failure counts by day/client/error type — feeds the alert check |

## Design choices worth knowing about

- **Zero external dbt packages.** No `dbt_utils` — this project doesn't
  need a date spine (event timestamps are used directly), and the one
  range-check test is a 15-line hand-written macro
  (`macros/test_accepted_range.sql`). One less thing that can fail to
  install in a locked-down client environment. (Projects 1 and 2 in this
  portfolio originally depended on `dbt_utils` and hit exactly that
  failure while being built — see their READMEs.)
- **Schema naming.** `dbt_project.yml` deliberately has no `+schema`
  overrides. dbt's default `generate_schema_name` macro *concatenates* the
  profile's target schema with any custom `+schema` config — `analytics` +
  `schema: marts` lands tables in `analytics_marts`, not `analytics`. Every
  query here assumes the plain `analytics` schema, so the override is left
  out entirely. (Found this the hard way while verifying project 2 — see
  its README for the full story — and got it right here from the start.)
- **Pricing is example data.** `seeds/model_pricing.csv` is clearly
  labeled — verify current rates at your provider's pricing page before
  using this for real client billing. `is_pricing_known` on
  `fact_agent_runs` (tested with `severity: warn`) flags any model that
  shows up in events but is missing from the seed, so a new model silently
  costing $0 is visible, not silent.
- **Failures are logged, not swallowed.** `agentobs.track()` re-raises
  every exception after logging it — this library observes your agents,
  it doesn't change their error-handling behavior.

## Tech stack

Postgres 16 · dbt-postgres 1.8 · Apache Airflow 2.9 · Streamlit · Plotly ·
Docker Compose · zero-dependency Python for `agentobs` itself

## Setup

### 1. Try the instrumentation library (no infra required)

```bash
pip install -e .
python -m pytest tests/test_tracker.py -v   # or: python tests/test_tracker.py
python scripts/demo_instrumentation.py       # logs to data/events/demo_run.jsonl
```

### 2. Generate demo data and start the infrastructure

```bash
python scripts/generate_synthetic_events.py   # writes data/events/synthetic_events.jsonl
docker compose up -d
```

Postgres on `localhost:5434`, Airflow UI at `http://localhost:8082`
(offset from projects 1 and 2 so all three can run side by side). Get the
Airflow admin password with `docker compose logs airflow | grep password`.

### 3. Build the warehouse

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/load_events.py

cd dbt/agent_observability
dbt seed --profiles-dir ..
dbt run --profiles-dir ..
dbt test --profiles-dir ..
cd ../..
```

**Troubleshooting:** if `dbt --version` reports something calling itself
"dbt-oss" or flags the postgres adapter as experimental, your environment
resolved a pre-release `dbt-core` 2.x ("Fusion") instead of the classic
engine this was built against. Fix with `pip install "dbt-core<2.0"
dbt-postgres==1.8.2` (already pinned in `requirements.txt`).

### 4. Check the alerting

```bash
python scripts/check_alerts.py                     # checks the most recent day
python scripts/check_alerts.py --date 2026-09-11    # the injected cost spike
python scripts/check_alerts.py --date 2026-08-19    # the injected error-rate incident
```

Set `ALERT_WEBHOOK_URL` in `.env` to a Slack incoming-webhook URL to post
real alerts; leave it unset and alerts print to stdout instead (safe
default, nothing fails silently either way).

### 5. Turn on the pipeline in Airflow

Un-pause `agent_observability_pipeline` — runs hourly: load events → dbt
seed/run/test → check alerts.

### 6. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

## Verification note

Every model was actually built and tested against a live Postgres 16
instance with real (synthetic) event data while writing this — `dbt run`
and `dbt test` both passing (8 models, 20 tests), and `check_alerts.py`
run against the exact injected incident/spike dates to confirm it catches
them and stays quiet otherwise. Two real bugs were caught and fixed in the
process, not just written and assumed correct:

1. `round(double precision, integer)` doesn't exist in Postgres —
   `input_tokens / 1000.0` and `percentile_cont()` both produce `double
   precision`, and `round(x, n)` only accepts `numeric`. Fixed with
   explicit casts in `fact_agent_runs.sql` and `mart_model_performance.sql`,
   plus declaring the seed's pricing columns as `numeric(10,6)` at load
   time in `dbt_project.yml` rather than leaving dbt to infer `double
   precision` from the CSV.
2. The alert cost threshold's first default ($5.00/day) never fired
   against the injected spike, because it was set without checking the
   actual scale of the data — the real median per-client day is ~$0.26.
   Recalibrated to $1.00 after checking the distribution directly
   (`p50`/`p90`/`p99` via a `percentile_cont` query), and documented as
   demo-scale, not universal, in `check_alerts.py`.

## Project structure

```
agent-observability-pipeline/
├── agentobs/                    # the instrumentation library
│   ├── __init__.py
│   └── tracker.py
├── tests/test_tracker.py         # offline unit tests, no network/API key needed
├── pyproject.toml                # pip install -e .
├── docker-compose.yml            # Postgres (5434) + Airflow (8082)
├── data/events/
│   └── synthetic_events.jsonl    # 90 days of demo data (checked in)
├── sql/init.sql                  # raw schema DDL
├── scripts/
│   ├── demo_instrumentation.py   # real-or-mocked Anthropic call example
│   ├── generate_synthetic_events.py
│   ├── load_events.py            # append-only, idempotent load
│   ├── check_alerts.py           # cost-spike / error-rate webhook alerts
│   └── db.py
├── dbt/
│   ├── profiles.yml
│   └── agent_observability/
│       ├── dbt_project.yml
│       ├── seeds/model_pricing.csv
│       ├── macros/test_accepted_range.sql
│       └── models/{staging,marts}/
├── airflow/dags/agent_observability_dag.py
└── dashboard/app.py               # Streamlit + Plotly
```

## Possible extensions

- Swap the JSONL sink for a Kafka producer once volume justifies it (see
  "why simple event logging, not Kafka" above for exactly where that
  swap-in point is)
- Add a per-client monthly invoice export from `mart_client_cost_summary`
- Track prompt-template versions in `metadata` and add a mart comparing
  cost/latency/error-rate across versions — A/B testing for prompts, for free
- Point `agentobs` at a second provider (OpenAI, a local model server) and
  add `mart_model_performance` comparisons across providers, not just
  within Anthropic's lineup
