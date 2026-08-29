# Incident Report

## Severity
P1 — CEO revenue dashboard under-reports; Support Agent can serve a stale refund policy.

## Summary

Game Day incoming batch still reports pipeline `SUCCESS` at the job layer, but two independent reliability failures are visible from evidence:

1. **Orders volume collapse** (~600 → 150 rows, ~75% drop). No schema/PK/type violation, so contract + dbt tests can stay green while `fct_daily_revenue` and the CEO dashboard under-count.
2. **Knowledge-base freshness breach** (`published_at` delay ≈ 190 minutes vs 60-minute contract). RAG/Support Agent can retrieve the previous refund policy.

Healthy baseline after `make reset`: 600 orders, 0 contract failures, freshness ≈ 5 minutes, KB freshness ≈ 10–12 minutes, row-count `auto` detector quiet, multi-window policy `within_error_budget`.

## Detection

- Signal (orders): `detect_metric(..., method="auto")` with same-weekday/MAD/relative drop. Volume-drop batch: `is_anomaly=True`, score ≈ 7.9. Z-score-only would be the wrong default on weekends because history is seasonal (~430 weekend vs ~600 weekday).
- Signal (KB): contract freshness on `published_at` (`max_delay_minutes: 60`, severity `warning`). Stale batch: `delay_minutes=190`.
- Signal (duplicates, practice): `order_id` unique check + GX Checkpoint `expect_column_values_to_be_unique` + Soda `no_duplicate_values`. Action = `block`, 6 rows quarantined.
- First observed time: incoming batch timestamp `2026-08-29` (lab clock). Latest orders `updated_at` remains fresh (~5 min); the volume failure is completeness, not lag. Latest KB `published_at` is ~3 hours behind `as_of`.

## Root Cause

Ranked hypotheses from evidence only (contracts, anomaly, lineage, SLO — not from the injector internals):

| # | Hypothesis | For | Against | Verdict |
|---|---|---|---|---|
| H1 | Partial orders ingestion / missing partition | Row count 150 vs weekday baseline ~570–650; contracts still pass; amounts look normal | Not a freshness problem on `updated_at` | **Confirmed for revenue** |
| H2 | Duplicate `order_id` fan-out inflating/breaking PK | Would fail `unique` + GX + dbt `unique_stg_orders_order_id` | Volume-drop batch has 0 contract failures | Rejected for this incident |
| H3 | Customer SCD double-active versions inflating revenue | Unit test `duplicate_active_customer_does_not_inflate_revenue` documents the failure mode | Singular test `assert_one_active_customer_version` passes on current seeds; mart join is `select distinct customer_id` | Rejected for this batch; guarded going forward |
| H4 | Stale KB publish / failed reindex | `kb_contract` freshness fails; blast radius is `kb_active_docs → rag_index → support_agent` | Document `content` length did not collapse | **Confirmed for Support Agent** |

Two root causes, not one. Revenue and RAG do not share a table; treating them as a single “pipeline bug” would delay mitigation.

## Evidence

1. **Contracts**
   - Healthy / volume-drop: 0 failed orders checks (deterministic tests cannot see a silent 75% drop).
   - Duplicate-PK practice: `unique order_id` critical, `duplicate_rows=6`, pipeline action `block`.
   - Stale KB: `freshness published_at` warning, `delay_minutes=190.0; max_delay_minutes=60`.
2. **Anomaly / distribution**
   - `auto` MAD + same-weekday + 50% relative drop flags 150 rows.
   - Amount mean-ratio/KS/PSI does not fire — leftover rows are a subset, not a currency/amount rewrite.
3. **Lineage blast radius**
   - `stg_orders` → `fct_daily_revenue` → `ceo_revenue_dashboard`
   - `stg_orders.amount_usd` → `fct_daily_revenue.daily_revenue` → `ceo_revenue_dashboard.revenue`
   - `kb_documents` → `kb_active_docs` → `rag_index` → `support_agent`
4. **SLO**
   - Worked example (lab guide): SLO 99.5%, 2 bad / 100 checks → actual 2%, allowed 0.5%, burn **4.0x**, remaining budget **0**, `breached=True`.
   - Multi-window: short=20 / long=0.5 → `page=False` (`transient_spike`); short=14.4 / long=6 → `page=True` (`sustained_fast_burn`).
5. **GX / Soda**
   - Duplicate PK: GX Checkpoint `success=false`, action `block`; Soda contract `no_duplicate_values order_id` fails.
   - Healthy: GX `success=true` action `allow`; Soda 0 failed checks.

## Blast Radius

```text
raw_orders
 -> stg_orders
    -> fct_daily_revenue
       -> ceo_revenue_dashboard          # CEO sees revenue drop

raw_customers
 -> stg_customers
    -> fct_daily_revenue                 # not the failing grain this time

kb_documents
 -> kb_active_docs
    -> rag_index
       -> support_agent                  # old refund policy
```

Column path for amount: `raw_orders.amount` → `stg_orders.amount_usd` → `fct_daily_revenue.daily_revenue` → `ceo_revenue_dashboard.revenue`.

OpenLineage run events: `reports/openlineage/*.json`.

## Mitigation

1. **Orders PK/type/critical contract fail** → `action=block`. Do not load `stg_orders` / `fct_daily_revenue`. Duplicate rows land in `data/quarantine/`.
2. **Volume drop (no contract fail)** → do not publish CEO dashboard. Page only if multi-window burn confirms a sustained hole, not a one-run blip. Re-ingest the missing partition / late files.
3. **KB freshness warning** → quarantine the stale KB batch, keep last-known-good index, re-publish current policy docs, rebuild `rag_index`.
4. **SCD inflation (prevented)** → mart uses unique `customer_id`; singular test fails the dim if two versions are active.

## Recovery

```bash
make reset          # re-anchor incoming timestamps, restore 600-row healthy batch
make baseline       # contracts + anomaly + SLO + lineage
make dbt            # 26/26 including 2 unit tests
make gx             # Checkpoint PASS, action allow
make soda           # 0 failed checks
pytest tests_public -q
```

## Verification

- [x] Contract healthy (0 failed orders checks, action `allow`)
- [x] dbt tests healthy (`PASS=26`, including `duplicate_active_customer_does_not_inflate_revenue`)
- [x] anomaly returned to expected range (`row_count_anomaly=False` on 600 rows)
- [x] SLO healthy / budget understood (this run burn 0x; 2/100 at 99.5% = 4x, breached)
- [x] downstream output verified (blast radius lists dashboard + support agent; GX/Soda green)

## Prevention / Action Items

| Action | Owner | Deadline | Why |
|---|---|---|---|
| Keep `auto` detector (MAD + same-weekday + relative drop); do not rely on z-score alone | data-rel | immediate | Weekend traffic is ~43% of weekdays; z-score false-positives or misses subset drops when history has outliers |
| Enforce KB freshness contract (60 min) in the RAG publish job | support-ai | immediate | Stale `published_at` is how an old refund policy reaches Support Agent without a content-length change |
| Block on critical contract (PK/type); quarantine warning freshness | commerce-data | immediate | Duplicate PK must never land in `fct_daily_revenue` |
| Keep dbt unit test for dual-active SCD + `select distinct customer_id` | analytics-eng | this week | `not_null`/`unique` on the mart would still pass after a fan-out join |
| Page only on multi-window burn (14.4× & 6× or 6× & 3×) | SRE | this week | A 5-minute spike should not wake on-call |
| Emit OpenLineage + column blast radius on every baseline run | data-rel | this week | CEO vs Support impact is obvious from graph, not from the job log |

## Phase 0 — healthy system (for the lab write-up)

- **Critical datasets:** `orders`, `customers` (SCD), `kb_documents`.
- **Downstream consumers:** `fct_daily_revenue` / CEO dashboard; `rag_index` / Support Agent.
- **Trust metrics:** critical contract pass rate, row-count vs same-weekday baseline, freshness minutes, remaining error budget, multi-window page flag.

## Why `not_null` / `unique` are not dbt unit tests

Generic data tests inspect the **materialised relation** (this batch of data). They cannot see that `left join` of two active customer versions doubled `sum(amount)`. A **unit test** mocks `stg_orders` (one completed order, $100) and `stg_customers` (two `is_active=true` rows) and asserts `daily_revenue=100`. That is transformation correctness, not data presence.
