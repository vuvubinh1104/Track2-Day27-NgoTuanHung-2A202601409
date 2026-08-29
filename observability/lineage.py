from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


def load_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["dataset_lineage"] if "dataset_lineage" in payload else payload


def load_column_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if "column_lineage" in payload:
        return payload["column_lineage"]
    return payload


def _bfs_downstream(graph: dict[str, list[str]], start: str) -> list[str]:
    seen = {start}
    q: deque[str] = deque([start])
    out: list[str] = []
    while q:
        node = q.popleft()
        for child in graph.get(node, []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                q.append(child)
    return out


def get_downstream_assets(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return transitive downstream assets in BFS order, excluding start."""
    return _bfs_downstream(graph, start)


def get_column_downstream(
    column_graph: dict[str, list[str]], start_column: str
) -> list[str]:
    """Transitive column-level blast radius in BFS order, excluding start."""
    return _bfs_downstream(column_graph, start_column)


def extract_dbt_dataset_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Map each dbt node unique_id to the nodes that depend on it."""
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    graph: dict[str, list[str]] = {}
    child_map = manifest.get("child_map", {})
    for parent, children in child_map.items():
        graph[parent] = list(children)
    return graph


def extract_dbt_column_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Best-effort column lineage from a dbt manifest.

    dbt Core does not always emit full column lineage. When `columns` exist on
    parent/child nodes we connect identically named columns; otherwise we fall
    back to dataset edges suffixed with `.*`.
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    nodes = manifest.get("nodes", {})
    child_map = manifest.get("child_map", {})
    graph: dict[str, list[str]] = {}

    def _add(src: str, dst: str) -> None:
        graph.setdefault(src, [])
        if dst not in graph[src]:
            graph[src].append(dst)

    for parent, children in child_map.items():
        parent_node = nodes.get(parent, {})
        parent_cols = list((parent_node.get("columns") or {}).keys())
        parent_name = parent_node.get("alias") or parent_node.get("name") or parent
        for child in children:
            child_node = nodes.get(child, {})
            child_cols = list((child_node.get("columns") or {}).keys())
            child_name = child_node.get("alias") or child_node.get("name") or child
            if parent_cols and child_cols:
                child_set = {c.lower(): c for c in child_cols}
                for col in parent_cols:
                    dest = child_set.get(col.lower())
                    if dest:
                        _add(f"{parent_name}.{col}", f"{child_name}.{dest}")
            else:
                _add(f"{parent_name}.*", f"{child_name}.*")
    return graph


def blast_radius_report(
    dataset_graph: dict[str, list[str]],
    column_graph: dict[str, list[str]],
    start_dataset: str,
    start_column: str | None = None,
) -> dict[str, Any]:
    datasets = get_downstream_assets(dataset_graph, start_dataset)
    columns = get_column_downstream(column_graph, start_column) if start_column else []
    return {
        "start": start_dataset,
        "start_column": start_column,
        "downstream_datasets": datasets,
        "downstream_columns": columns,
        "impacted_count": len(datasets) + len(columns),
    }
