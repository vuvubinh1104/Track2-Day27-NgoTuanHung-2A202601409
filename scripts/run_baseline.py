#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.distribution import detect_distribution_shift
from observability.elementary_report import build_elementary_report
from observability.lineage import (
    extract_dbt_dataset_graph,
    get_column_downstream,
    get_downstream_assets,
    load_column_graph,
    load_graph,
)
from observability.openlineage import emit_pipeline_events
from observability.rag_metrics import detect_embedding_norm_shift, detect_text_length_shift
from observability.slo import calculate_slo, evaluate_multiwindow_burn
from src.contract_validator import decide_action, failed_issues, load_contract, validate_dataframe
from src.io_utils import load_jsonl
from src.quarantine import quarantine_failures


def _pseudo_embedding_norms(texts: list[str]) -> list[float]:
    """Deterministic content-hash proxy so we can track embedding drift without a model."""
    norms = []
    for text in texts:
        tokens = str(text).split()
        acc = 0.0
        for i, tok in enumerate(tokens):
            acc += (sum(ord(ch) for ch in tok) % 97) / 97.0
            acc += (len(tok) % 13) / 50.0
        norms.append(round(1.0 + (acc / max(len(tokens), 1) - 0.5) * 0.15, 4))
    return norms


def main() -> None:
    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")
    action = decide_action(issues)
    quarantine = quarantine_failures(orders, issues, dataset="orders")

    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    kb_df = pd.DataFrame(docs)
    kb_issues = validate_dataframe(kb_df, kb_contract)
    kb_failed = failed_issues(kb_issues)

    current_dow = datetime.now().weekday()
    same_weekday = history.loc[history["day_of_week"] == current_dow, "row_count"].tail(8).tolist()
    row_history = history["row_count"].tail(21).tolist()
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": current_dow,
            "same_segment_history": same_weekday if len(same_weekday) >= 3 else None,
        },
    )

    amount_shift = detect_distribution_shift(
        orders["amount"].tolist(),
        history["avg_amount"].tail(14).tolist(),
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0
    freshness_slo = calculate_slo(
        0.995,
        bad_events=1 if freshness_minutes > 30 else 0,
        total_events=1,
    )

    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )
    embedding_result = detect_embedding_norm_shift(
        _pseudo_embedding_norms([d["content"] for d in docs]),
        history["embedding_norm_mean"].tail(14).tolist(),
    )

    bad = 1 if critical_failed else 0
    contract_slo = calculate_slo(0.999, bad_events=bad, total_events=1)
    # Demo windows: this run is the short window; long window uses the last 6 history points
    # plus this run, so a single bad check does not page.
    long_bad = int((history["null_rate"].tail(6) > 0.02).sum()) + bad
    long_total = 6 + 1
    long_slo = calculate_slo(0.999, bad_events=long_bad, total_events=long_total)
    burn_policy = evaluate_multiwindow_burn(
        short_window_burn=contract_slo["burn_rate"],
        long_window_burn=long_slo["burn_rate"],
    )

    lineage_path = ROOT / "data" / "baseline" / "lineage_graph.json"
    dataset_graph = load_graph(lineage_path)
    column_graph = load_column_graph(lineage_path)
    blast_radius = get_downstream_assets(dataset_graph, "stg_orders")
    amount_blast = get_column_downstream(column_graph, "stg_orders.amount_usd")
    kb_blast = get_downstream_assets(dataset_graph, "kb_documents")
    dbt_graph = extract_dbt_dataset_graph(ROOT / "dbt_project" / "target" / "manifest.json")
    emit_pipeline_events(dataset_graph, column_graph)
    try:
        elementary = build_elementary_report()
    except Exception:
        elementary = {"healthy": None, "note": "dbt artifacts not built yet"}

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "pipeline_action": action,
        "quarantine": {
            "action": quarantine["action"],
            "quarantined_rows": quarantine["quarantined_rows"],
            "clean_rows": quarantine["clean_rows"],
            "path": quarantine["quarantine_path"],
        },
        "row_count_anomaly": row_result,
        "amount_distribution": amount_shift,
        "freshness_minutes": freshness_minutes,
        "freshness_slo": freshness_slo,
        "kb_failed_checks": len(kb_failed),
        "kb_issues": kb_failed,
        "kb_text_length_signal": text_result,
        "kb_embedding_signal": embedding_result,
        "contract_slo": contract_slo,
        "multiwindow_burn": burn_policy,
        "sample_blast_radius_from_stg_orders": blast_radius,
        "column_blast_radius_amount_usd": amount_blast,
        "kb_blast_radius": kb_blast,
        "dbt_manifest_nodes": len(dbt_graph),
        "elementary": {
            "healthy": elementary.get("healthy"),
            "failed_nodes": elementary.get("failed_nodes"),
            "total_nodes": elementary.get("total_nodes"),
        },
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"pipeline action          : {action['action']}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f}")
    print(f"KB contract fails        : {len(kb_failed)}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"KB embedding anomaly     : {embedding_result['is_anomaly']}")
    print(f"multiwindow page         : {burn_policy['page']} ({burn_policy['reason']})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"amount column blast      : {', '.join(amount_blast)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
