from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

from student_api import validate_orders
from src.contract_validator import decide_action
from src.quarantine import quarantine_failures

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "orders_contract.yaml"


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def healthy_df():
    return pd.DataFrame(
        [
            {
                "order_id": 1,
                "customer_id": "C1",
                "amount": 10.0,
                "currency": "USD",
                "status": "completed",
                "created_at": _ts(10),
                "updated_at": _ts(5),
            },
            {
                "order_id": 2,
                "customer_id": "C2",
                "amount": 20.0,
                "currency": "USD",
                "status": "pending",
                "created_at": _ts(9),
                "updated_at": _ts(4),
            },
        ]
    )


def failed(issues):
    return [i for i in issues if not i["passed"]]


def test_healthy_contract_passes_starter_checks():
    assert not failed(validate_orders(healthy_df(), CONTRACT))


def test_duplicate_order_id_is_detected():
    df = healthy_df()
    df.loc[1, "order_id"] = 1
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "unique" and i["column"] == "order_id" for i in issues)


def test_invalid_currency_is_detected():
    df = healthy_df()
    df.loc[0, "currency"] = "BTC"
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "accepted_values" and i["column"] == "currency" for i in issues)


def test_type_drift_on_amount_is_detected():
    df = healthy_df()
    df["amount"] = df["amount"].astype(object)
    df.loc[0, "amount"] = "free"
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "type" and i["column"] == "amount" and i["severity"] == "critical" for i in issues)


def test_stale_updated_at_fails_freshness():
    df = healthy_df()
    df["updated_at"] = "2020-01-01T00:00:00Z"
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "freshness" and i["column"] == "updated_at" for i in issues)


def test_critical_failure_blocks_pipeline():
    df = healthy_df()
    df.loc[1, "order_id"] = 1
    issues = validate_orders(df, CONTRACT)
    decision = decide_action(issues)
    assert decision["action"] == "block"
    assert decision["blocked"] is True


def test_automatic_quarantine_writes_duplicate_rows(tmp_path):
    df = healthy_df()
    df.loc[1, "order_id"] = 1
    issues = validate_orders(df, CONTRACT)
    result = quarantine_failures(df, issues, dataset="orders", out_dir=tmp_path)
    assert result["quarantined_rows"] == 2
    assert result["quarantine_path"] is not None
    assert Path(result["quarantine_path"]).exists()
