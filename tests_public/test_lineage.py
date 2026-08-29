from pathlib import Path

from student_api import column_downstream, downstream_assets
from observability.lineage import load_column_graph, load_graph

ROOT = Path(__file__).resolve().parents[1]


def test_transitive_downstream_assets():
    graph = {
        "raw_orders": ["stg_orders"],
        "stg_orders": ["revenue"],
        "revenue": ["dashboard"],
    }
    assert downstream_assets(graph, "raw_orders") == ["stg_orders", "revenue", "dashboard"]


def test_stg_orders_blast_radius_from_lab_graph():
    graph = load_graph(ROOT / "data" / "baseline" / "lineage_graph.json")
    downstream = downstream_assets(graph, "stg_orders")
    assert "fct_daily_revenue" in downstream
    assert "ceo_revenue_dashboard" in downstream


def test_transitive_column_lineage():
    graph = {
        "raw_orders.amount": ["stg_orders.amount_usd"],
        "stg_orders.amount_usd": ["fct_daily_revenue.daily_revenue"],
        "fct_daily_revenue.daily_revenue": ["ceo_revenue_dashboard.revenue"],
    }
    assert column_downstream(graph, "raw_orders.amount") == [
        "stg_orders.amount_usd",
        "fct_daily_revenue.daily_revenue",
        "ceo_revenue_dashboard.revenue",
    ]


def test_lab_column_graph_is_transitive():
    graph = load_column_graph(ROOT / "data" / "baseline" / "lineage_graph.json")
    downstream = column_downstream(graph, "stg_orders.amount_usd")
    assert "fct_daily_revenue.daily_revenue" in downstream
    assert "ceo_revenue_dashboard.revenue" in downstream
