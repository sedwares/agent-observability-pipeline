select
    event_date,
    client_id,
    task_type,
    count(*)                                                       as calls,
    sum(case when success then 1 else 0 end)                       as successful_calls,
    sum(case when not success then 1 else 0 end)                   as failed_calls,
    round(sum(case when not success then 1 else 0 end)::numeric
        / nullif(count(*), 0), 4)                                  as error_rate,
    sum(input_tokens)                                               as total_input_tokens,
    sum(output_tokens)                                              as total_output_tokens,
    round(sum(cost_usd), 4)                                         as total_cost_usd,
    round(avg(latency_ms), 1)                                       as avg_latency_ms,
    bool_and(is_pricing_known)                                      as all_pricing_known
from {{ ref('fact_agent_runs') }}
group by 1, 2, 3
order by 1, 2, 3
