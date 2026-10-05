-- Error trend by client and day - the table the alerting check (see
-- scripts/check_alerts.py) queries, and the one that makes an incident
-- like "client X's error rate jumped to 35% for three days" visible in a
-- chart rather than buried in an aggregate.

select
    event_date,
    client_id,
    error_type,
    count(*) as occurrences
from {{ ref('fact_agent_runs') }}
where not success
group by 1, 2, 3
order by 1, 2
