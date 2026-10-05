with source as (
    select * from {{ source('raw', 'agent_events') }}
),

cleaned as (
    select
        event_id,
        event_timestamp,
        event_timestamp::date          as event_date,
        coalesce(client_id, 'unknown') as client_id,
        coalesce(agent_name, 'unknown') as agent_name,
        task_type,
        model,
        coalesce(input_tokens, 0)      as input_tokens,
        coalesce(output_tokens, 0)     as output_tokens,
        latency_ms,
        success,
        error_type,
        error_message,
        metadata
    from source
)

select * from cleaned
