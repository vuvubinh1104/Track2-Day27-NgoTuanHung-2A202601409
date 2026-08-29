"""Automatic quarantine for rows that fail contract checks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.contract_validator import (
    _normalize_type,
    _value_matches_type,
    decide_action,
    failed_issues,
)
from src.io_utils import repo_path

ROW_LEVEL_CHECKS = {
    "not_null",
    "unique",
    "accepted_values",
    "range",
    "type",
    "length",
    "created_before_updated",
}


def row_failure_mask(df: pd.DataFrame, issues: list[dict[str, Any]]) -> pd.Series:
    mask = pd.Series(False, index=df.index)
    for issue in failed_issues(issues):
        check = issue.get("check")
        column = issue.get("column")
        if check not in ROW_LEVEL_CHECKS or not column or column not in df.columns:
            continue
        series = df[column]
        if check == "not_null":
            mask |= series.isna()
        elif check == "unique":
            mask |= series.duplicated(keep=False)
        elif check == "range":
            numeric = pd.to_numeric(series, errors="coerce")
            mask |= series.notna() & numeric.isna()
            details = issue.get("details") or ""
            # Conservative: if a min/max was violated, drop non-finite or out-of-range later
            # via numeric comparison if we can parse the contract from details. Fallback:
            if "invalid_count" in details:
                mask |= numeric < 0
        elif check == "length":
            lengths = series.fillna("").astype(str).map(len)
            mask |= series.notna() & (lengths < 1)
        elif check == "created_before_updated" and "created_at" in df.columns:
            created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
            updated = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")
            mask |= created.notna() & updated.notna() & (updated < created)
        elif check == "type":
            expected = None
            details = issue.get("details") or ""
            if "declared=" in details:
                expected = _normalize_type(details.split("declared=")[-1].split(";")[0])
            if expected:
                mask |= pd.Series(
                    [not _value_matches_type(v, expected) for v in series.tolist()],
                    index=df.index,
                )
    return mask


def quarantine_failures(
    df: pd.DataFrame,
    issues: list[dict[str, Any]],
    *,
    dataset: str = "orders",
    out_dir: str | Path | None = None,
    bad_mask: pd.Series | None = None,
) -> dict[str, Any]:
    """Persist failing rows and return the clean remainder plus action metadata."""
    decision = decide_action(issues)
    out_dir = Path(out_dir) if out_dir else repo_path("data", "quarantine")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if bad_mask is None:
        bad_mask = row_failure_mask(df, issues)

    quarantined = df.loc[bad_mask].copy() if bool(bad_mask.any()) else df.iloc[0:0].copy()
    clean = df.loc[~bad_mask].copy() if bool(bad_mask.any()) else df.copy()

    # Dataset-level failures (freshness) quarantine the whole batch when action requires it.
    dataset_level = any(
        (not i.get("passed")) and i.get("check") in {"freshness", "required_column"}
        for i in issues
    )
    if decision["quarantine"] and dataset_level and len(quarantined) == 0:
        quarantined = df.copy()
        clean = df.iloc[0:0].copy()

    path = None
    if decision["quarantine"] and len(quarantined) > 0:
        path = out_dir / f"{dataset}_{stamp}.csv"
        quarantined.to_csv(path, index=False)
        manifest = out_dir / f"{dataset}_{stamp}_issues.json"
        manifest.write_text(
            json.dumps(
                {
                    "dataset": dataset,
                    "action": decision["action"],
                    "failed": [i for i in issues if not i.get("passed", False)],
                    "quarantined_rows": int(len(quarantined)),
                    "clean_rows": int(len(clean)),
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    return {
        **decision,
        "quarantined_rows": int(len(quarantined)),
        "clean_rows": int(len(clean)),
        "quarantine_path": str(path) if path else None,
        "clean_df": clean,
        "quarantined_df": quarantined,
    }
