from datetime import date
from pathlib import Path

import pytest

from analytics import load_tables, prepare_dashboard

DB = Path(__file__).resolve().parents[1] / "warehouse_ops.db"


@pytest.fixture(scope="module")
def tables():
    return load_tables(DB)


def test_latest_full_history_reconciles_step2(tables):
    ids = tables["warehouses"]["warehouse_id"].tolist()
    result = prepare_dashboard(tables, ids, date(2024, 11, 1), date(2026, 11, 12))
    metrics = result["metrics"]
    assert metrics["orders_received"] == 30642
    assert metrics["orders_shipped"] == 30298
    assert metrics["overdue_open_asof"] == 53
    assert metrics["open_backlog_asof"] == 75
    assert round(metrics["on_time_shipping_pct"], 2) == 90.95
    assert metrics["orders_due"] >= metrics["on_time_due_orders"]


def test_warehouse_filter_additive_for_event_counts(tables):
    ids = tables["warehouses"]["warehouse_id"].tolist()
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    combined = prepare_dashboard(tables, ids, start, end)["metrics"]
    one = prepare_dashboard(tables, [ids[0]], start, end)["metrics"]
    two = prepare_dashboard(tables, [ids[1]], start, end)["metrics"]
    for key in ("orders_received", "orders_shipped", "overdue_open_asof", "qc_units_inspected"):
        assert combined[key] == one[key] + two[key]


def test_no_false_historical_stock_snapshot(tables):
    ids = tables["warehouses"]["warehouse_id"].tolist()
    result = prepare_dashboard(tables, ids, date(2025, 4, 1), date(2025, 4, 30))
    assert result["snapshot_as_of"].date() == date(2026, 11, 12)
    assert result["as_of"].date() == date(2025, 4, 30)


def test_rejects_reverse_dates(tables):
    with pytest.raises(ValueError):
        prepare_dashboard(tables, ["WH_NC_01"], date(2026, 11, 12), date(2026, 1, 1))
