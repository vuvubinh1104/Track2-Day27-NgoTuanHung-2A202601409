"""Deterministic data-contract validator.

Covers schema, types, uniqueness, accepted values, ranges, freshness,
severity-aware actions (block / quarantine / warn), and KB `fields`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
DEFAULT_ACTIONS = {"critical": "block", "warning": "quarantine", "info": "warn"}
TYPE_ALIASES = {
    "integer": "integer",
    "int": "integer",
    "int64": "integer",
    "bigint": "integer",
    "number": "number",
    "float": "number",
    "double": "number",
    "numeric": "number",
    "decimal": "number",
    "string": "string",
    "str": "string",
    "varchar": "string",
    "text": "string",
    "datetime": "datetime",
    "timestamp": "datetime",
    "date": "datetime",
    "boolean": "boolean",
    "bool": "boolean",
}


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
    action: str | None = None,
) -> dict[str, Any]:
    severity = severity if severity in SEVERITY_RANK else "warning"
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "details": details,
        "action": action or DEFAULT_ACTIONS.get(severity, "warn"),
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _contract_columns(contract: dict[str, Any]) -> dict[str, Any]:
    columns = contract.get("columns")
    if columns:
        return columns
    return contract.get("fields") or {}


def _normalize_type(raw: Any) -> str | None:
    if raw is None:
        return None
    return TYPE_ALIASES.get(str(raw).strip().lower())


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _is_bool(value: Any) -> bool:
    return isinstance(value, (bool, np.bool_))


def _value_matches_type(value: Any, expected: str) -> bool:
    if _is_null(value):
        return True
    if expected == "integer":
        if _is_bool(value):
            return False
        if isinstance(value, (int, np.integer)):
            return True
        if isinstance(value, (float, np.floating)):
            return bool(np.isfinite(value) and float(value).is_integer())
        return False
    if expected == "number":
        if _is_bool(value) or isinstance(value, str):
            return False
        if isinstance(value, (int, float, np.number)):
            return bool(np.isfinite(float(value)))
        return False
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return _is_bool(value)
    if expected == "datetime":
        ts = pd.to_datetime(value, utc=True, errors="coerce")
        return not pd.isna(ts)
    return True


def _parse_as_of(contract: dict[str, Any]) -> pd.Timestamp:
    freshness = contract.get("freshness") or {}
    raw = freshness.get("as_of") or contract.get("as_of")
    if raw is not None:
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if not pd.isna(ts):
            return pd.Timestamp(ts)
    return pd.Timestamp(datetime.now(timezone.utc))


def _validate_column(series: pd.Series, column: str, rules: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    severity = rules.get("severity", "warning")
    action = rules.get("action")
    required = bool(rules.get("required", False))

    if required:
        null_count = int(series.isna().sum())
        issues.append(
            _issue(
                "not_null",
                column=column,
                severity=severity,
                passed=(null_count == 0),
                details=f"null_count={null_count}",
                action=action,
            )
        )

    expected_type = _normalize_type(rules.get("type") or rules.get("dtype") or rules.get("data_type"))
    if expected_type:
        invalid_idx = [
            i for i, value in enumerate(series.tolist()) if not _value_matches_type(value, expected_type)
        ]
        issues.append(
            _issue(
                "type",
                column=column,
                severity=severity,
                passed=(len(invalid_idx) == 0),
                details=(
                    f"invalid_type_count={len(invalid_idx)}; declared={expected_type}"
                    + (f"; sample_index={invalid_idx[:5]}" if invalid_idx else "")
                ),
                action=action,
            )
        )

    if rules.get("unique"):
        duplicate_count = int(series.duplicated(keep=False).sum())
        issues.append(
            _issue(
                "unique",
                column=column,
                severity=severity,
                passed=(duplicate_count == 0),
                details=f"duplicate_rows={duplicate_count}",
                action=action,
            )
        )

    accepted = rules.get("accepted_values")
    if accepted is not None:
        invalid_mask = series.notna() & ~series.isin(accepted)
        invalid_count = int(invalid_mask.sum())
        issues.append(
            _issue(
                "accepted_values",
                column=column,
                severity=severity,
                passed=(invalid_count == 0),
                details=f"invalid_count={invalid_count}; accepted={accepted}",
                action=action,
            )
        )

    if "min" in rules or "max" in rules:
        numeric = pd.to_numeric(series, errors="coerce")
        invalid = pd.Series(False, index=series.index)
        if "min" in rules:
            invalid |= numeric < rules["min"]
        if "max" in rules:
            invalid |= numeric > rules["max"]
        # Non-numeric values in a ranged column are range/type failures.
        invalid |= series.notna() & numeric.isna()
        invalid_count = int(invalid.fillna(False).sum())
        issues.append(
            _issue(
                "range",
                column=column,
                severity=severity,
                passed=(invalid_count == 0),
                details=f"invalid_count={invalid_count}",
                action=action,
            )
        )

    min_length = rules.get("min_length")
    max_length = rules.get("max_length")
    if min_length is not None or max_length is not None:
        lengths = series.fillna("").astype(str).map(len)
        invalid = pd.Series(False, index=series.index)
        if min_length is not None:
            invalid |= series.notna() & (lengths < int(min_length))
        if max_length is not None:
            invalid |= series.notna() & (lengths > int(max_length))
        invalid_count = int(invalid.sum())
        issues.append(
            _issue(
                "length",
                column=column,
                severity=severity,
                passed=(invalid_count == 0),
                details=f"invalid_count={invalid_count}; min_length={min_length}; max_length={max_length}",
                action=action,
            )
        )

    return issues


def _validate_freshness(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    spec = contract.get("freshness") or {}
    if not spec:
        return []
    column = spec.get("column")
    max_delay = spec.get("max_delay_minutes")
    if not column or max_delay is None:
        return []
    severity = spec.get("severity", "warning")
    action = spec.get("action")
    if column not in df.columns:
        return [
            _issue(
                "freshness",
                column=column,
                severity=severity,
                passed=False,
                details=f"freshness column missing: {column}",
                action=action,
            )
        ]

    parsed = pd.to_datetime(df[column], utc=True, errors="coerce")
    as_of = _parse_as_of(contract)
    if parsed.notna().sum() == 0:
        return [
            _issue(
                "freshness",
                column=column,
                severity=severity,
                passed=False,
                details="no_parseable_timestamps",
                action=action,
            )
        ]
    latest = parsed.max()
    delay_minutes = float((as_of - latest).total_seconds() / 60.0)
    passed = delay_minutes <= float(max_delay)
    return [
        _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=passed,
            details=(
                f"delay_minutes={delay_minutes:.1f}; max_delay_minutes={max_delay}; "
                f"latest={latest.isoformat()}; as_of={as_of.isoformat()}"
            ),
            action=action,
        )
    ]


def _validate_cross_field(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    columns = _contract_columns(contract)
    if "created_at" in df.columns and "updated_at" in df.columns:
        created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
        updated = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")
        inverted = created.notna() & updated.notna() & (updated < created)
        invalid_count = int(inverted.sum())
        severity = columns.get("updated_at", {}).get("severity", "warning")
        issues.append(
            _issue(
                "created_before_updated",
                column="updated_at",
                severity=severity,
                passed=(invalid_count == 0),
                details=f"invalid_count={invalid_count}",
            )
        )
    return issues


def validate_dataframe(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    columns = _contract_columns(contract)

    for column, rules in columns.items():
        severity = (rules or {}).get("severity", "warning")
        required = bool((rules or {}).get("required", False))
        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                    )
                )
            continue
        issues.extend(_validate_column(df[column], column, rules or {}))

    issues.extend(_validate_freshness(df, contract))
    issues.extend(_validate_cross_field(df, contract))
    return issues


def failed_issues(issues: list[dict[str, Any]], min_severity: str | None = None) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    threshold = SEVERITY_RANK.get(min_severity, 1)
    return [i for i in failed if SEVERITY_RANK.get(i.get("severity", "warning"), 1) >= threshold]


def decide_action(issues: list[dict[str, Any]]) -> dict[str, Any]:
    failed = failed_issues(issues)
    if not failed:
        return {"action": "allow", "blocked": False, "quarantine": False, "reason": "all_checks_passed"}
    worst = max(failed, key=lambda i: SEVERITY_RANK.get(i.get("severity", "warning"), 1))
    severity = worst.get("severity", "warning")
    action = DEFAULT_ACTIONS.get(severity, "warn")
    return {
        "action": action,
        "blocked": action == "block",
        "quarantine": action in {"block", "quarantine"},
        "reason": f"worst_severity={severity}; failed_checks={len(failed)}",
        "worst_severity": severity,
        "failed_checks": len(failed),
    }
