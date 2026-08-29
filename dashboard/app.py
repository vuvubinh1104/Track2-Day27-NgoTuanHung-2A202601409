from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "latest_metrics.json"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"
GX_ACTION = ROOT / "reports" / "gx_action_report.json"
SODA = ROOT / "reports" / "soda_contract_result.json"
ELEMENTARY = ROOT / "reports" / "elementary_results.json"

st.set_page_config(page_title="Data Reliability Lab", layout="wide")
st.title("Data Reliability Game Day")
st.caption("Detect → triage → blast radius → SLO burn → mitigate.")

if not REPORT.exists():
    st.warning("Run `make baseline` first to generate reports/latest_metrics.json")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))
slo = report.get("contract_slo") or {}
fresh = report.get("freshness_slo") or {}
burn = report.get("multiwindow_burn") or {}
action = report.get("pipeline_action") or {}

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Orders rows", report["orders_rows"])
c2.metric("Freshness (min)", f"{report['freshness_minutes']:.1f}")
c3.metric("Contract failures", report["failed_contract_checks"])
c4.metric("Critical failures", report["critical_contract_failures"])
c5.metric("Pipeline action", action.get("action", "allow"))

s1, s2, s3, s4 = st.columns(4)
s1.metric("Contract SLO remaining", f"{slo.get('remaining_error_budget_fraction', 0):.0%}")
s2.metric("Contract burn rate", f"{slo.get('burn_rate', 0):.2f}x")
s3.metric("Freshness SLO remaining", f"{fresh.get('remaining_error_budget_fraction', 0):.0%}")
s4.metric("Page?", "YES" if burn.get("page") else "no", delta=burn.get("reason", ""))

st.subheader("Current signals")
st.json(
    {
        "row_count_anomaly": report.get("row_count_anomaly"),
        "amount_distribution": report.get("amount_distribution"),
        "kb_text_length_signal": report.get("kb_text_length_signal"),
        "kb_embedding_signal": report.get("kb_embedding_signal"),
        "kb_failed_checks": report.get("kb_failed_checks"),
        "contract_slo": slo,
        "multiwindow_burn": burn,
        "quarantine": report.get("quarantine"),
    }
)

history = pd.read_csv(HISTORY)
st.subheader("Historical row count")
st.line_chart(history.set_index("date")[["row_count"]])

left, right = st.columns(2)
with left:
    st.subheader("Dataset blast radius (stg_orders)")
    st.write("stg_orders -> " + " -> ".join(report.get("sample_blast_radius_from_stg_orders") or []))
    st.subheader("Column blast radius (stg_orders.amount_usd)")
    st.write(" -> ".join(report.get("column_blast_radius_amount_usd") or []))
with right:
    st.subheader("KB blast radius")
    st.write("kb_documents -> " + " -> ".join(report.get("kb_blast_radius") or []))
    st.subheader("Owners / runbooks")
    st.markdown(
        "- Orders owner: `commerce-data` — quarantine duplicates, block type/PK failures\n"
        "- KB owner: `support-ai` — refresh RAG index when freshness SLO burns\n"
        "- CEO dashboard: `fct_daily_revenue` — do not publish if pipeline action is `block`"
    )

st.subheader("Downstream health artifacts")
cols = st.columns(3)
for col, path, label in [
    (cols[0], GX_ACTION, "GX action report"),
    (cols[1], SODA, "Soda contract"),
    (cols[2], ELEMENTARY, "Elementary / dbt artifacts"),
]:
    with col:
        st.caption(label)
        if path.exists():
            st.json(json.loads(path.read_text(encoding="utf-8")))
        else:
            st.info(f"Run the matching target to produce `{path.name}`.")
