select distinct
    client_id
from {{ ref('stg_agent_events') }}
