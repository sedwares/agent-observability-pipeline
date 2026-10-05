-- The table you'd actually hand a client: total spend, call volume,
-- reliability, and average cost per call, one row per client, over the
-- full window of data currently loaded.

select
    client_id,
    count(*)                                                        as total_calls,
    round(sum(cost_usd), 2)                                         as total_cost_usd,
    round(sum(cost_usd) / nullif(count(*), 0), 4)                   as avg_cost_per_call_usd,
    round(sum(case when not success then 1 else 0 end)::numeric
        / nullif(count(*), 0), 4)                                   as error_rate,
    round(avg(latency_ms), 1)                                       as avg_latency_ms,
    min(event_date)                                                 as first_seen_date,
    max(event_date)                                                 as last_seen_date,
    count(distinct task_type)                                       as distinct_task_types
from {{ ref('fact_agent_runs') }}
group by 1
order by total_cost_usd desc
