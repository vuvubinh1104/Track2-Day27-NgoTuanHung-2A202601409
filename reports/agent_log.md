# AI Agent Decision Log

Không copy full conversation. Ghi các decision quan trọng.

## Decision 1 — Contract: type + freshness + action
- Hypothesis: Starter chỉ có not-null/unique/accepted/range nên type drift (`amount="free"`) và stale `updated_at` sẽ lọt. Hidden eval sẽ gọi `validate_orders`.
- Prompt / request to agent: Implement type/freshness/severity/action, giữ shape `{check, column, severity, passed, details}`.
- Agent proposal: `check="type"` (không coerce bằng `pd.to_numeric(errors="coerce")`), `check="freshness"` so với wall-clock/`as_of`, action `critical→block`, `warning→quarantine`, `info→warn`. KB dùng `fields` giống `columns`.
- Evidence/test: `pytest tests_public/test_contracts.py` — healthy pass; duplicate unique; BTC accepted_values; type drift; freshness 2020-01-01 fail; critical → block; quarantine writes 2 duplicate rows.
- Accept / reject / revise: **Accept**. Public healthy timestamps đổi sang “now - a few minutes” vì freshness 30 phút sẽ fail dữ liệu 2026-08-28 khi lab chạy 2026-08-29.
- Why: `pd.to_numeric(..., errors="coerce")` đúng như TODO — che type drift.

## Decision 2 — GX Suite / Checkpoint / severity Actions
- Hypothesis: Expectation rời không đủ điểm GX; cần Suite + ValidationDefinition + Checkpoint + Action theo severity.
- Prompt / request to agent: Build GX 1.21 flow với custom `ValidationAction`.
- Agent proposal: `gx/validate_orders.py` — Suite (PK unique, amount ≥ 0, currency set, types, row count), Checkpoint, `SeverityFileAction` ghi `reports/gx_action_report.json` (`block`/`quarantine`/`warn`) + `UpdateDataDocsAction`.
- Evidence/test: Healthy Checkpoint `success=true` action `allow`. Duplicate PK: `success=false`, failed `expect_column_values_to_be_unique` severity `critical`, action `block`, 6 rows quarantined.
- Accept / reject / revise: **Revise** — lần đầu `str(FailureSeverity.CRITICAL)` không normalize, Action nổ `KeyError`. Sửa lấy `.value` rồi lower-case.
- Why: Lab yêu cầu Actions theo severity, không chỉ print từng expectation.

## Decision 3 — dbt unit test vs data test; SCD fan-out
- Hypothesis: `left join` mọi `is_active=true` sẽ nhân revenue nếu customer có 2 version active. `not_null`/`unique` trên `fct_daily_revenue` vẫn pass.
- Prompt / request to agent: Smallest unit test exposing inflation; do not treat not_null as unit test.
- Agent proposal: Hai unit test (happy path 170; dual-active expect 100). Sửa model `select distinct customer_id`. Thêm generic tests (`unique order_date`, `relationships`, `not_null amount_usd`) + singular `assert_one_active_customer_version`.
- Evidence/test: `dbt build` `PASS=26` gồm `fct_daily_revenue::duplicate_active_customer_does_not_inflate_revenue`.
- Accept / reject / revise: **Accept, và sửa production model**. Lab nói “chưa sửa model” là bước học; để `make dbt` xanh và unit test khóa hành vi đúng thì phải distinct grain.
- Why: Data tests nhìn data; unit tests nhìn logic.

## Decision 4 — `auto` anomaly: MAD + weekday, không chỉ z-score
- Hypothesis: Z-score fail khi (1) seasonality weekend ~43% weekday, (2) outlier thổi std, (3) history hằng số (MAD=0).
- Prompt / request to agent: MAD + same-weekday; `auto` đọc `day_of_week` / `same_segment_history` / `known_event`.
- Agent proposal: MAD (zero-MAD = mọi lệch ≥5% là anomaly), same-weekday/weekend nếu CV history cao, relative drop 50%, EWMA giữ method riêng. Spike vs seasonal bucket nhưng khớp overall baseline → suppress (batch 600 row chạy vào Saturday).
- Evidence/test:
  - Volume 300 vs ~1000: z-score + MAD bắt.
  - MAD bắt drop khi history có outlier 5000 (z-score score thấp hơn).
  - Saturday 435 vs weekday/weekend mix: `auto` không flag; Saturday 80 flag.
  - Real `volume_drop` 150/600: `is_anomaly=True` score≈7.9. Healthy 600: `False`.
- Accept / reject / revise: **Revise** — lần đầu `same_segment_history` Saturday (~250) vs batch 600 flag false positive. Thêm rule suppress segment-spike khi raw history không anomaly.
- Why: Detector phải bắt drop chưa có rule `row_count == N`, và không page weekend hợp lệ.

## Decision 5 — Multi-window burn rate (SRE workbook)
- Hypothesis: Starter `page=False` luôn; hidden test phân biệt transient vs sustained.
- Prompt / request to agent: Page khi cả hai cửa sổ burn; spike ngắn không page.
- Agent proposal: Page critical nếu short≥14.4 AND long≥6; page warning nếu short≥6 AND long≥3; short≥14.4 AND long<6 → `transient_spike` không page.
- Evidence/test: `slo_status(0.995, 2, 100)` → burn 4.0, breached. `multiwindow_burn(20, 0.5)` page False; `(14.4, 6)` page True critical.
- Accept / reject / revise: **Accept**.
- Why: Google SRE Workbook table 5-8; đúng đề “transient không page, sustained fast burn page”.

## Decision 6 — Column lineage + OpenLineage
- Hypothesis: Starter `get_column_downstream` chỉ 1 hop; hidden test transitive.
- Prompt / request to agent: BFS giống dataset lineage; emit OpenLineage JSON.
- Agent proposal: Cùng BFS; parse dbt `child_map`; ghi `reports/openlineage/*.json` (job/run/inputs/outputs + `columnLineage` facet).
- Evidence/test: `column_downstream(raw_orders.amount)` = `stg_orders.amount_usd → fct_daily_revenue.daily_revenue → ceo_revenue_dashboard.revenue`. `stg_orders` → `fct_daily_revenue`, `ceo_revenue_dashboard`.
- Accept / reject / revise: **Accept**.
- Why: CEO impact khác Support Agent impact — không đoán.

## Decision 7 — RAG embedding/token drift
- Hypothesis: Stale policy có thể không đổi length; embedding norm/shift là tín hiệu còn lại. Starter `detect_embedding_norm_shift` luôn False.
- Prompt / request to agent: Implement embedding-norm MAD/z-score trên precomputed norms (không download model).
- Agent proposal: Mean z-score + MAD + relative ≥20%. KS/PSI chỉ khi n≥8 (tránh false positive 3 điểm).
- Evidence/test: Norms ~1.0 → 0.1 flagged; 0.99–1.01 không flagged. `stale_kb` bắt bằng freshness, không bằng length (content không đổi) — đúng.
- Accept / reject / revise: **Revise** — bản đầu dùng PSI trên 3 điểm, PSI=2.25 false positive. Bỏ KS/PSI cho batch nhỏ.
- Why: Bonus RAG + giải thích stale KB không phải length collapse.

## Decision 8 — Soda contract + automatic quarantine
- Hypothesis: Bonus Soda + quarantine cần evidence bắt fault mà baseline cũ không bắt.
- Prompt / request to agent: Soda YAML + runner; quarantine CSV khi fail.
- Agent proposal: `soda/orders.contract.yml` (Soda v3 shape) + `soda/verify.py` (Soda Core nếu có, pandas fallback). `src/quarantine.py` tách row fail.
- Evidence/test: Duplicate PK — Soda `no_duplicate_values order_id duplicate_rows=6` exit 1; quarantine 6 rows. Healthy Soda 0 fail. Volume drop **không** bị Soda/contract bắt — chỉ anomaly, đúng thiết kế nhiều lớp.
- Accept / reject / revise: **Accept** pandas fallback (không bắt buộc soda-core trên classroom venv). Không `dbt deps` Elementary vì `packages.yml` làm `dbt build` fail khi chưa install; thay bằng `observability/elementary_report.py` đọc `run_results.json` (26 nodes, 0 failed).
- Why: Defense in depth: contract/GX/Soda bắt deterministic; anomaly bắt completeness; freshness bắt KB; lineage nói ai bị ảnh hưởng.
