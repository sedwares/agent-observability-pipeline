{#
    A minimal stand-in for dbt_utils.accepted_range, written by hand so
    this project has zero external dbt package dependencies - one less
    thing that can fail to install offline or in a locked-down client
    environment (see project 2's README for what happens when dbt_utils
    can't be fetched). Fails the test for any row outside [min_value, max_value].
#}
{% test accepted_range(model, column_name, min_value=none, max_value=none) %}

select *
from {{ model }}
where
    {{ column_name }} is not null
    {% if min_value is not none %}
    and {{ column_name }} < {{ min_value }}
    {% endif %}
    {% if max_value is not none %}
    and {{ column_name }} > {{ max_value }}
    {% endif %}

{% endtest %}
