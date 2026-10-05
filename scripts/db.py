"""Shared Postgres connection helper. Reads connection info from environment
variables so the same code works locally (via .env) and inside the Airflow
container (via docker-compose environment block)."""
import os

from sqlalchemy import create_engine

DEFAULTS = {
    "AGENTOBS_DB_HOST": "localhost",
    "AGENTOBS_DB_PORT": "5434",
    "AGENTOBS_DB_NAME": "agent_obs_dw",
    "AGENTOBS_DB_USER": "agentobs_admin",
    "AGENTOBS_DB_PASSWORD": "agentobs_pass",
}


def get_engine():
    cfg = {k: os.environ.get(k, v) for k, v in DEFAULTS.items()}
    url = (
        f"postgresql+psycopg2://{cfg['AGENTOBS_DB_USER']}:{cfg['AGENTOBS_DB_PASSWORD']}"
        f"@{cfg['AGENTOBS_DB_HOST']}:{cfg['AGENTOBS_DB_PORT']}/{cfg['AGENTOBS_DB_NAME']}"
    )
    return create_engine(url)
