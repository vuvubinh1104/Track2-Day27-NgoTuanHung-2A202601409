"""Emit OpenLineage-compatible dataset/column lineage events as JSON files.

The events follow the OpenLineage 1.x run/dataset facet shape so they can be
imported into Marquez without a live OpenLineage backend.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.io_utils import repo_path


PRODUCER = "https://github.com/vinai/data-reliability-game-day"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_run_event(
    *,
    job_name: str,
    inputs: list[str],
    outputs: list[str],
    column_lineage: dict[str, list[str]] | None = None,
    namespace: str = "lab.commerce",
    event_type: str = "COMPLETE",
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    facets: dict[str, Any] = {}
    if column_lineage:
        fields = {}
        for output_col, input_cols in column_lineage.items():
            fields[output_col] = {
                "inputFields": [
                    {"namespace": namespace, "name": src.rsplit(".", 1)[0], "field": src.rsplit(".", 1)[-1]}
                    for src in input_cols
                ],
                "transformationType": "DIRECT",
            }
        facets["columnLineage"] = {"fields": fields}

    return {
        "eventType": event_type,
        "eventTime": _now(),
        "producer": PRODUCER,
        "schemaURL": SCHEMA_URL,
        "run": {"runId": run_id, "facets": {}},
        "job": {"namespace": namespace, "name": job_name, "facets": {}},
        "inputs": [{"namespace": namespace, "name": name} for name in inputs],
        "outputs": [
            {
                "namespace": namespace,
                "name": name,
                "facets": facets if column_lineage else {},
            }
            for name in outputs
        ],
    }


def emit_pipeline_events(
    dataset_graph: dict[str, list[str]],
    column_graph: dict[str, list[str]] | None = None,
    out_dir: str | Path | None = None,
) -> list[Path]:
    out_dir = Path(out_dir) if out_dir else repo_path("reports", "openlineage")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    inverse: dict[str, list[str]] = {}
    for src, children in dataset_graph.items():
        for child in children:
            inverse.setdefault(child, []).append(src)

    jobs = sorted(set(dataset_graph.keys()) | set(inverse.keys()))
    for job in jobs:
        inputs = inverse.get(job, [])
        outputs = [job]
        col_facet = None
        if column_graph:
            col_facet = {
                dst: [src]
                for src, dests in column_graph.items()
                for dst in dests
                if dst.startswith(job + ".") or src.startswith(job + ".")
            }
        event = build_run_event(
            job_name=job,
            inputs=inputs,
            outputs=outputs,
            column_lineage=col_facet,
        )
        path = out_dir / f"{job.replace('.', '_')}.json"
        path.write_text(json.dumps(event, indent=2), encoding="utf-8")
        written.append(path)
    index = {
        "produced_at": _now(),
        "events": [p.name for p in written],
        "dataset_count": len(jobs),
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return written
