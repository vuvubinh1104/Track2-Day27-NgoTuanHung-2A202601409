#!/usr/bin/env python3
"""Verify the Soda Data Contract for orders.

Tries Soda Core if it is installed. Otherwise interprets the Soda YAML with
pandas so the lab stays $0 / no extra service, while keeping the contract
file in Soda's published shape.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTRACT_PATH = ROOT / "soda" / "orders.contract.yml"
REPORT_PATH = ROOT / "reports" / "soda_contract_result.json"


def _issue(check: str, column: str | None, passed: bool, details: str, severity: str = "critical") -> dict[str, Any]:
    return {
        "check": check,
        "column": column,
        "passed": passed,
        "details": details,
        "severity": severity,
        "source": "soda_contract",
    }


def verify_with_pandas(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    declared = [c["name"] for c in contract.get("columns", [])]
    missing = [c for c in declared if c not in df.columns]
    issues.append(
        _issue("schema", None, len(missing) == 0, f"missing_columns={missing}")
    )
    issues.append(_issue("rows_exist", None, len(df) > 0, f"row_count={len(df)}"))

    for col in contract.get("columns", []):
        name = col["name"]
        if name not in df.columns:
            continue
        series = df[name]
        for check in col.get("checks", []):
            kind = check.get("type")
            if kind == "no_missing_values":
                n = int(series.isna().sum())
                issues.append(_issue("no_missing_values", name, n == 0, f"null_count={n}"))
            elif kind == "no_duplicate_values":
                n = int(series.duplicated(keep=False).sum())
                issues.append(_issue("no_duplicate_values", name, n == 0, f"duplicate_rows={n}"))
            elif kind == "invalid_count":
                invalid = pd.Series(False, index=series.index)
                if "valid_values" in check:
                    invalid |= series.notna() & ~series.isin(check["valid_values"])
                if "valid_min" in check:
                    numeric = pd.to_numeric(series, errors="coerce")
                    invalid |= numeric < check["valid_min"]
                if "valid_max" in check:
                    numeric = pd.to_numeric(series, errors="coerce")
                    invalid |= numeric > check["valid_max"]
                n = int(invalid.fillna(False).sum())
                issues.append(_issue("invalid_count", name, n == 0, f"invalid_count={n}"))
            elif kind == "freshness_in_minutes":
                parsed = pd.to_datetime(series, utc=True, errors="coerce")
                latest = parsed.max()
                delay = (
                    (pd.Timestamp(datetime.now(timezone.utc)) - latest).total_seconds() / 60.0
                    if pd.notna(latest)
                    else float("inf")
                )
                limit = float(check.get("must_be_less_than", 30))
                issues.append(
                    _issue(
                        "freshness_in_minutes",
                        name,
                        delay < limit,
                        f"delay_minutes={delay:.1f}; must_be_less_than={limit}",
                        severity="warning",
                    )
                )
    return issues


def verify_with_soda_core(df: pd.DataFrame, contract_path: Path) -> list[dict[str, Any]] | None:
    try:
        from soda.scan import Scan  # type: ignore
    except Exception:
        return None
    # Soda Core scan against a pandas dataframe is optional and environment-dependent.
    try:
        scan = Scan()
        scan.set_data_source_name("orders")
        scan.add_pandas_dataframe(data_source_name="orders", dataset_name="orders", pandas_df=df)
        scan.add_sodacl_yaml_file(str(contract_path))
        scan.execute()
        results = []
        for check in scan.get_checks():
            results.append(
                _issue(
                    getattr(check, "name", "soda_check"),
                    None,
                    str(getattr(check, "outcome", "")).lower() == "pass",
                    str(check),
                )
            )
        return results
    except Exception as exc:
        return [_issue("soda_core", None, False, f"soda_core_error={exc}", severity="warning")]


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    soda_core = verify_with_soda_core(df, CONTRACT_PATH)
    pandas_issues = verify_with_pandas(df, contract)
    issues = soda_core if soda_core else pandas_issues
    failed = [i for i in issues if not i["passed"]]
    payload = {
        "engine": "soda_core" if soda_core else "soda_yaml_pandas_fallback",
        "failed": len(failed),
        "issues": issues,
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Soda contract engine : {payload['engine']}")
    print(f"Failed checks        : {payload['failed']}")
    for issue in failed:
        print(f"  - {issue['check']} {issue['column']}: {issue['details']}")
    sys.exit(1 if failed and any(i["severity"] == "critical" for i in failed) else 0)


if __name__ == "__main__":
    main()
