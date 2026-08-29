-- Singular business test: at most one currently-active version per customer.
-- A second active SCD row is the failure mode that inflates fct_daily_revenue
-- if the mart join is not unique on customer_id.
select
    customer_id,
    count(*) as active_versions
from {{ ref('stg_customers') }}
where is_active = true
group by 1
having count(*) > 1
