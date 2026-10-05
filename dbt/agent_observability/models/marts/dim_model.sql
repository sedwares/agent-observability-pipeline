select
    model,
    provider,
    input_cost_per_1k_tokens,
    output_cost_per_1k_tokens,
    notes
from {{ ref('model_pricing') }}
