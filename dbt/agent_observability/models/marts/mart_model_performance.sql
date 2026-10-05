-- Which model to reach for, by the numbers: cost, speed, and reliability
-- side by side, so "should this task run on Haiku or Sonnet" has a real
-- answer instead of a guess.

select
    model,
    task_type,
    count(*)                                                        as calls,
    round(avg(latency_ms), 1)                                       as avg_latency_ms,
    round(
        -- percentile_cont always returns double precision regardless of
        -- input type (same Postgres quirk as fact_agent_runs' cost_usd) -
        -- cast before round(numeric, int) will accept it
        (percentile_cont(0.95) within group (order by latency_ms))::numeric, 1
    )                                                                as p95_latency_ms,
    round(sum(case when not success then 1 else 0 end)::numeric
        / nullif(count(*), 0), 4)                                   as error_rate,
    round(avg(cost_usd), 6)                                         as avg_cost_per_call_usd,
    round(avg(input_tokens + output_tokens), 0)                     as avg_total_tokens
from {{ ref('fact_agent_runs') }}
group by 1, 2
order by model, calls desc
