-- Grain is one row per order_date.
-- Join active customers at unique customer_id grain so a Type-2 SCD with two
-- simultaneously-active versions cannot fan-out and inflate revenue.
-- `not_null` / `unique` on the result would still pass after inflation; the
-- unit test in unit_tests.yml is what locks the transformation math.

with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
active_customers as (
    select distinct customer_id
    from {{ ref('stg_customers') }}
    where is_active = true
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
