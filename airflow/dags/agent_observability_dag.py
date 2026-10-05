"""
Agent Observability warehouse - orchestration DAG.

Pattern: append-only incremental. Every run picks up whatever new JSONL
event lines exist under data/events/ (real agentobs logs and/or the
synthetic demo file), loads them idempotently (ON CONFLICT DO NOTHING on
event_id - see scripts/load_events.py), rebuilds the dbt marts, tests
them, then checks yesterday's numbers against cost/error-rate thresholds.

This is the third distinct orchestration pattern across the three
portfolio projects - deliberately: project 1 is daily-incremental against
a live-shaped source, project 2 is full-refresh against a static extract,
this one is append-only/idempotent against a log stream, which is the
right shape for "many small agent processes writing events whenever they
run," not a single daily batch job.

Schedule: hourly, since this is meant to represent near-real-time cost
monitoring for an actual freelance agent operation, not an end-of-day
batch report.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

DBT_PROJECT_DIR = "/opt/airflow/dbt/agent_observability"
DBT_PROFILES_DIR = "/opt/airflow/dbt"

default_args = {
    "owner": "sedar",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="agent_observability_pipeline",
    description="Append-only ingest of agent event logs -> dbt transform + test -> cost/error alerting",
    default_args=default_args,
    schedule_interval="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["agentobs", "dbt", "llmops", "alerting"],
) as dag:

    load_events = BashOperator(
        task_id="load_events",
        bash_command="cd /opt/airflow && python scripts/load_events.py",
    )

    dbt_seed = BashOperator(
        task_id="dbt_seed",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt seed --profiles-dir {DBT_PROFILES_DIR}",
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt run --profiles-dir {DBT_PROFILES_DIR}",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"cd {DBT_PROJECT_DIR} && dbt test --profiles-dir {DBT_PROFILES_DIR}",
    )

    check_alerts = BashOperator(
        task_id="check_alerts",
        bash_command="cd /opt/airflow && python scripts/check_alerts.py",
    )

    load_events >> dbt_seed >> dbt_run >> dbt_test >> check_alerts
