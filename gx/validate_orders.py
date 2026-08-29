#!/usr/bin/env python3
"""Great Expectations Core 1.21 flow.

Builds an Expectation Suite, ValidationDefinition and Checkpoint with
severity-aware Actions (critical -> block, warning -> quarantine).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Union

import pandas as pd
from typing_extensions import override

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
    from great_expectations.checkpoint import (
        ActionContext,
        CheckpointResult,
        UpdateDataDocsAction,
        ValidationAction,
    )
except ImportError as exc:
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc

from src.contract_validator import failed_issues, load_contract, validate_dataframe
from src.quarantine import quarantine_failures


class SeverityFileAction(ValidationAction):
    """Write a severity-aware JSON action report (block / quarantine / warn)."""

    type: Literal["severity_file_action"] = "severity_file_action"
    report_path: str

    @override
    def run(
        self,
        checkpoint_result: CheckpointResult,
        action_context: Union[ActionContext, None] = None,
    ) -> dict:
        worst = "info"
        failed = []
        rank = {"info": 0, "warning": 1, "critical": 2}
        for result in checkpoint_result.run_results.values():
            stats = getattr(result, "statistics", {}) or {}
            success = bool(getattr(result, "success", True))
            for er in getattr(result, "results", []) or []:
                expectation = getattr(er, "expectation_config", None) or {}
                kwargs = getattr(expectation, "kwargs", {}) if expectation else {}
                raw_sev = getattr(expectation, "severity", None)
                if raw_sev is not None and hasattr(raw_sev, "value"):
                    severity = str(raw_sev.value).lower()
                elif raw_sev is not None:
                    severity = str(raw_sev).split(".")[-1].lower()
                else:
                    severity = "warning"
                ok = bool(getattr(er, "success", True))
                if not ok:
                    failed.append(
                        {
                            "type": getattr(expectation, "type", None) or expectation.__class__.__name__,
                            "column": kwargs.get("column") if isinstance(kwargs, dict) else None,
                            "severity": severity,
                            "success": False,
                        }
                    )
                    if rank.get(severity, 1) > rank.get(worst, 0):
                        worst = severity
            if not success and worst == "info":
                worst = "warning"

        worst = str(worst).split(".")[-1].lower()
        action = {"critical": "block", "warning": "quarantine", "info": "warn"}.get(worst, "warn")
        if not failed:
            action = "allow"
            worst = "info"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": checkpoint_result.success,
            "worst_severity": worst,
            "action": action,
            "failed_expectations": failed,
            "blocked": action == "block",
            "quarantine": action in {"block", "quarantine"} and bool(failed),
        }
        path = Path(self.report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload


def build_orders_suite() -> gx.ExpectationSuite:
    return gx.ExpectationSuite(
        name="orders_contract_suite",
        expectations=[
            gx.expectations.ExpectColumnToExist(column="order_id", severity="critical"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="order_id", severity="critical"),
            gx.expectations.ExpectColumnValuesToBeUnique(column="order_id", severity="critical"),
            gx.expectations.ExpectColumnValuesToBeInTypeList(
                column="order_id", type_list=["int", "int64", "int32"], severity="critical"
            ),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="customer_id", severity="critical"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="amount", severity="critical"),
            gx.expectations.ExpectColumnValuesToBeBetween(
                column="amount", min_value=0, severity="critical"
            ),
            gx.expectations.ExpectColumnValuesToBeInSet(
                column="currency", value_set=["USD", "VND"], severity="critical"
            ),
            gx.expectations.ExpectColumnValuesToBeInSet(
                column="status",
                value_set=["pending", "completed", "refunded", "cancelled"],
                severity="warning",
            ),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="created_at", severity="critical"),
            gx.expectations.ExpectColumnValuesToNotBeNull(column="updated_at", severity="critical"),
            gx.expectations.ExpectTableRowCountToBeBetween(
                min_value=1, severity="critical"
            ),
        ],
    )


def run_checkpoint(df: pd.DataFrame) -> dict[str, Any]:
    context = gx.get_context()
    source_name = "orders_pandas"
    try:
        data_source = context.data_sources.get(source_name)
    except Exception:
        data_source = context.data_sources.add_pandas(source_name)
    try:
        asset = data_source.get_asset("orders_dataframe")
    except Exception:
        asset = data_source.add_dataframe_asset(name="orders_dataframe")
    try:
        batch_definition = asset.get_batch_definition("whole_orders")
    except Exception:
        batch_definition = asset.add_batch_definition_whole_dataframe("whole_orders")

    suite = build_orders_suite()
    try:
        context.suites.add(suite)
    except Exception:
        existing = context.suites.get(suite.name)
        existing.expectations = suite.expectations
        suite = existing
        suite.save()

    try:
        validation_definition = context.validation_definitions.get("orders_validation")
    except Exception:
        validation_definition = gx.ValidationDefinition(
            name="orders_validation",
            data=batch_definition,
            suite=suite,
        )
        context.validation_definitions.add(validation_definition)

    action_report = ROOT / "reports" / "gx_action_report.json"
    actions = [
        SeverityFileAction(name="severity_file", report_path=str(action_report)),
        UpdateDataDocsAction(name="update_data_docs"),
    ]
    try:
        checkpoint = context.checkpoints.get("orders_checkpoint")
    except Exception:
        checkpoint = gx.Checkpoint(
            name="orders_checkpoint",
            validation_definitions=[validation_definition],
            actions=actions,
            result_format={"result_format": "SUMMARY"},
        )
        context.checkpoints.add(checkpoint)

    result = checkpoint.run(batch_parameters={"dataframe": df})
    return {
        "success": bool(result.success),
        "action_report": str(action_report),
        "statistics": {
            name: getattr(vr, "statistics", {}) for name, vr in result.run_results.items()
        },
    }


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    gx_result = run_checkpoint(df)

    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(df, contract)
    failed = failed_issues(issues)
    quarantine = quarantine_failures(df, issues, dataset="orders")

    print("=== Great Expectations Checkpoint ===")
    print(f"success                 : {gx_result['success']}")
    print(f"action report           : {gx_result['action_report']}")
    print(f"contract failed checks  : {len(failed)}")
    print(f"pipeline action         : {quarantine['action']}")
    print(f"quarantined rows        : {quarantine['quarantined_rows']}")
    if quarantine["quarantine_path"]:
        print(f"quarantine path         : {quarantine['quarantine_path']}")
    print("GX result:", "PASS" if gx_result["success"] else "FAIL")
    raise SystemExit(0 if gx_result["success"] and not failed else 1)


if __name__ == "__main__":
    main()
