"""
Agent Observability Dashboard.

Run locally (outside Docker) with:
    pip install -r requirements.txt
    streamlit run dashboard/app.py

Expects the Postgres warehouse (docker compose up) to be reachable on
localhost:5434 with the dbt marts already built (dbt run).
"""
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.append(".")
from scripts.db import get_engine

st.set_page_config(page_title="Agent Observability", layout="wide")
st.title("Agent Observability Dashboard")
st.caption(
    "Cost, latency, and reliability across every tracked LLM/agent call. "
    "Pricing is example-rate — see the model_pricing seed before using this for real client billing."
)

engine = get_engine()

# Fixed categorical color order (never cycled) - clients get slots 1-6 of
# the validated default palette, models get slots 1-3. A bar/line chart
# only needs to clear *adjacent*-pair CVD separation (not all-pairs, which
# is reserved for scatter/small-multiples), so this order is safe for the
# grouped-bar and multi-line charts below.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


@st.cache_data(ttl=300)
def load(table: str) -> pd.DataFrame:
    return pd.read_sql(f"select * from analytics.{table}", engine)


daily_cost = load("mart_daily_cost")
client_summary = load("mart_client_cost_summary")
model_perf = load("mart_model_performance")
error_analysis = load("mart_error_analysis")

if daily_cost.empty:
    st.warning(
        "No data found in analytics.mart_daily_cost. "
        "Run `python scripts/load_events.py` then `dbt run` first."
    )
    st.stop()

daily_cost["event_date"] = pd.to_datetime(daily_cost["event_date"])
error_analysis["event_date"] = pd.to_datetime(error_analysis["event_date"])

clients = sorted(client_summary["client_id"].unique())
CLIENT_COLORS = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(clients)}
models = sorted(model_perf["model"].unique())
MODEL_COLORS = {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(models)}

# --- Sidebar filters ---
selected_clients = st.sidebar.multiselect("Clients", options=clients, default=clients)
min_date, max_date = daily_cost["event_date"].min(), daily_cost["event_date"].max()
date_range = st.sidebar.date_input("Date range", (min_date, max_date), min_value=min_date, max_value=max_date)

daily_f = daily_cost[daily_cost["client_id"].isin(selected_clients)]
if len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    daily_f = daily_f[(daily_f["event_date"] >= start) & (daily_f["event_date"] <= end)]

tab_overview, tab_clients, tab_models, tab_alerts = st.tabs(
    ["Overview", "Clients", "Models", "Alerts"]
)

# --- Overview ---
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)
    total_calls = daily_f["calls"].sum()
    total_cost = daily_f["total_cost_usd"].sum()
    error_rate = daily_f["failed_calls"].sum() / max(total_calls, 1)
    avg_latency = (daily_f["avg_latency_ms"] * daily_f["calls"]).sum() / max(total_calls, 1)
    col1.metric("Total calls", f"{total_calls:,}")
    col2.metric("Total cost", f"${total_cost:,.2f}")
    col3.metric("Error rate", f"{error_rate * 100:.1f}%")
    col4.metric("Avg latency", f"{avg_latency:,.0f} ms")

    st.subheader("Daily spend, all clients")
    daily_total = daily_f.groupby("event_date", as_index=False)["total_cost_usd"].sum()
    fig_total = px.line(
        daily_total, x="event_date", y="total_cost_usd",
        labels={"total_cost_usd": "Cost (USD)", "event_date": "Date"},
    )
    fig_total.update_traces(line_color=PALETTE[0])
    st.plotly_chart(fig_total, use_container_width=True)

    st.subheader("Daily error rate, all clients")
    daily_err = daily_f.groupby("event_date", as_index=False).agg(
        calls=("calls", "sum"), failed_calls=("failed_calls", "sum")
    )
    daily_err["error_rate"] = daily_err["failed_calls"] / daily_err["calls"]
    fig_err = px.line(
        daily_err, x="event_date", y="error_rate",
        labels={"error_rate": "Error Rate", "event_date": "Date"},
    )
    fig_err.update_traces(line_color=PALETTE[7])
    fig_err.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig_err, use_container_width=True)

# --- Clients ---
with tab_clients:
    st.subheader("Total spend by client")
    summary_f = client_summary[client_summary["client_id"].isin(selected_clients)].sort_values(
        "total_cost_usd", ascending=False
    )
    fig_cost = px.bar(
        summary_f, x="client_id", y="total_cost_usd", color="client_id",
        color_discrete_map=CLIENT_COLORS, text_auto=".2s",
        labels={"total_cost_usd": "Total Cost (USD)", "client_id": "Client"},
    )
    fig_cost.update_layout(showlegend=False)
    st.plotly_chart(fig_cost, use_container_width=True)

    st.subheader("Daily spend by client")
    fig_by_client = px.line(
        daily_f.groupby(["event_date", "client_id"], as_index=False)["total_cost_usd"].sum(),
        x="event_date", y="total_cost_usd", color="client_id",
        color_discrete_map=CLIENT_COLORS,
        labels={"total_cost_usd": "Cost (USD)", "event_date": "Date", "client_id": "Client"},
    )
    st.plotly_chart(fig_by_client, use_container_width=True)

    st.subheader("Client summary")
    st.dataframe(
        summary_f[["client_id", "total_calls", "total_cost_usd", "avg_cost_per_call_usd", "error_rate", "avg_latency_ms"]],
        use_container_width=True, hide_index=True,
    )

# --- Models ---
with tab_models:
    st.subheader("Avg cost per call by model")
    model_agg = model_perf.groupby("model", as_index=False).apply(
        lambda g: pd.Series({
            "avg_cost_per_call_usd": (g["avg_cost_per_call_usd"] * g["calls"]).sum() / g["calls"].sum(),
            "avg_latency_ms": (g["avg_latency_ms"] * g["calls"]).sum() / g["calls"].sum(),
            "calls": g["calls"].sum(),
        }),
        include_groups=False,
    )
    col_a, col_b = st.columns(2)
    with col_a:
        fig_model_cost = px.bar(
            model_agg, x="model", y="avg_cost_per_call_usd", color="model",
            color_discrete_map=MODEL_COLORS, text_auto=".4f",
            labels={"avg_cost_per_call_usd": "Avg Cost / Call (USD)", "model": "Model"},
        )
        fig_model_cost.update_layout(showlegend=False)
        st.plotly_chart(fig_model_cost, use_container_width=True)
    with col_b:
        fig_model_latency = px.bar(
            model_agg, x="model", y="avg_latency_ms", color="model",
            color_discrete_map=MODEL_COLORS, text_auto=".0f",
            labels={"avg_latency_ms": "Avg Latency (ms)", "model": "Model"},
        )
        fig_model_latency.update_layout(showlegend=False)
        st.plotly_chart(fig_model_latency, use_container_width=True)

    st.subheader("Cost, latency, and error rate by model and task type")
    st.dataframe(
        model_perf.sort_values(["model", "calls"], ascending=[True, False]),
        use_container_width=True, hide_index=True,
    )

# --- Alerts ---
with tab_alerts:
    st.subheader("Days that would trigger an alert")
    st.caption(
        "Same thresholds as scripts/check_alerts.py — defaults calibrated to this demo "
        "data's scale, not a universal number. Reset for your own clients' real spend."
    )
    cost_threshold = st.slider("Daily cost threshold (USD)", 0.1, 3.0, 1.0, 0.1)
    error_threshold = st.slider("Error rate threshold", 0.05, 0.50, 0.15, 0.05)

    flagged_cost = (
        daily_f.groupby(["event_date", "client_id"], as_index=False)["total_cost_usd"].sum()
    )
    flagged_cost = flagged_cost[flagged_cost["total_cost_usd"] > cost_threshold]

    flagged_err = daily_f.groupby(["event_date", "client_id"], as_index=False).agg(
        calls=("calls", "sum"), failed_calls=("failed_calls", "sum")
    )
    flagged_err["error_rate"] = flagged_err["failed_calls"] / flagged_err["calls"]
    flagged_err = flagged_err[(flagged_err["calls"] >= 5) & (flagged_err["error_rate"] > error_threshold)]

    col_c, col_d = st.columns(2)
    with col_c:
        st.markdown(f"**Cost alerts** ({len(flagged_cost)})")
        if flagged_cost.empty:
            st.info("No days over threshold in the selected range.")
        else:
            st.dataframe(flagged_cost.sort_values("total_cost_usd", ascending=False), use_container_width=True, hide_index=True)
    with col_d:
        st.markdown(f"**Error-rate alerts** ({len(flagged_err)})")
        if flagged_err.empty:
            st.info("No days over threshold in the selected range.")
        else:
            st.dataframe(flagged_err.sort_values("error_rate", ascending=False), use_container_width=True, hide_index=True)
