-- Grain: one row per tracked call. Cost is computed here, joined from the
-- model_pricing seed rather than hardcoded, so updating a rate is a
-- one-line CSV edit + `dbt run`, not a code change. is_pricing_known flags
-- any model that shows up in events but has no seed row, so a new model
-- silently costing $0 in every downstream mart is visible and loud
-- instead of a silent gap.

with events as (
    select * from {{ ref('stg_agent_events') }}
),

pricing as (
    select * from {{ ref('dim_model') }}
),

joined as (
    select
        e.event_id,
        e.event_timestamp,
        e.event_date,
        e.client_id,
        e.agent_name,
        e.task_type,
        e.model,
        e.input_tokens,
        e.output_tokens,
        e.latency_ms,
        e.success,
        e.error_type,
        e.error_message,
        p.input_cost_per_1k_tokens,
        p.output_cost_per_1k_tokens,
        (p.model is not null)                              as is_pricing_known,
        -- cast to numeric throughout: input_tokens/1000 as plain integer
        -- division truncates, and dbt seed infers the pricing columns as
        -- double precision (not numeric) from the CSV, which Postgres'
        -- round(x, n) doesn't accept either way - numeric is the only
        -- path that both divides correctly and rounds cleanly
        round(
            (e.input_tokens::numeric / 1000)  * coalesce(p.input_cost_per_1k_tokens::numeric, 0)
            + (e.output_tokens::numeric / 1000) * coalesce(p.output_cost_per_1k_tokens::numeric, 0),
            6
        )                                                    as cost_usd
    from events e
    left join pricing p on e.model = p.model
)

select * from joined
