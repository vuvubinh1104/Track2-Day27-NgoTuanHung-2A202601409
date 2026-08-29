-- Singular business test: positive revenue cannot come from zero completed rows.
select *
from {{ ref('fct_daily_revenue') }}
where daily_revenue > 0
  and completed_order_rows <= 0
