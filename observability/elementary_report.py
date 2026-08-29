"""Elementary-style observability from dbt artifacts (no Cloud required).

Parses `dbt_project/target/run_results.json` + `manifest.json` into a compact
test/model health report. If Elementary OSS is installed via `dbt deps`, this
still works on top of the same artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.io_utils import repo_path


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_elementary_report(target_dir: str | Path | None = None) -> dict[str, Any]:
    target = Path(target_dir) if target_dir else repo_path("dbt_project", "target")
    run_results = load_json(target / "run_results.json")
    manifest = load_json(target / "manifest.json")
    nodes = manifest.get("nodes", {})
    results = []
    failed = 0
    for row in run_results.get("results", []):
        unique_id = row.get("unique_id", "")
        node = nodes.get(unique_id, {})
        status = row.get("status")
        entry = {
            "unique_id": unique_id,
            "name": node.get("name") or unique_id.split(".")[-1],
            "resource_type": node.get("resource_type") or row.get("unique_id", "").split(".")[0],
            "status": status,
            "execution_time": row.get("execution_time"),
            "message": row.get("message"),
            "failures": row.get("failures"),
            "owner": (node.get("meta") or {}).get("owner"),
        }
        results.append(entry)
        if status not in {"success", "pass", "skipped"}:
            failed += 1
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dbt_version": (run_results.get("metadata") or {}).get("dbt_version"),
        "elapsed_time": run_results.get("elapsed_time"),
        "failed_nodes": failed,
        "total_nodes": len(results),
        "healthy": failed == 0 and len(results) > 0,
        "results": results,
    }
    out = repo_path("reports", "elementary_results.json")
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
