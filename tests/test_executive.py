"""Step 8 regression checks: performance rollup, snapshot isolation, and export."""
from datetime import date, timedelta
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from analytics import load_tables, prepare_dashboard
from exception_workflow import get_cases
from executive import export_brief_html, prepare_executive
from productivity import prepare_productivity

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "warehouse_ops.db"


@pytest.fixture(scope="module")
def tables():
    return load_tables(DB)


@pytest.fixture(scope="module")
def ids(tables):
    return tables["warehouses"]["warehouse_id"].tolist()


def test_summary_matches_source_definitions(tables, ids):
    start, end = date(2026, 10, 14), date(2026, 11, 12)
    briefing = prepare_executive(tables, ids, start, end)
    source = prepare_dashboard(tables, ids, start, end)["metrics"]
    assert briefing["current"] == source
    assert briefing["picking_rate"] == prepare_productivity(tables, ids, start, end)["metrics"]["units_per_labor_hour"]
    assert briefing["forecast7"] == int(briefing["forecast_daily"]["forecast_orders"].sum())
    assert len(briefing["forecast_daily"]) == 7


def test_equal_length_prior_window_and_changes(tables, ids):
    start, end = date(2026, 10, 14), date(2026, 11, 12)
    briefing = prepare_executive(tables, ids, start, end)
    assert briefing["prior_start"] == date(2026, 9, 14)
    assert briefing["prior_end"] == date(2026, 10, 13)
    assert briefing["previous"] == prepare_dashboard(tables, ids, date(2026, 9, 14), date(2026, 10, 13))["metrics"]
    row = briefing["comparison"].set_index("KPI").loc["Orders shipped"]
    assert row["Change"] == row["Current"] - row["Prior"]
    assert row["Change unit"] == "count"


def test_no_fabricated_prior_data_at_beginning(tables, ids):
    report = prepare_executive(tables, ids, date(2024, 11, 1), date(2024, 11, 5))
    assert report["prior_start"] is None
    assert report["previous"] is None
    assert report["comparison"]["Change"].isna().all()
    assert report["forecast7"] is None  # fewer than 70 days; no invented forecast


def test_warehouse_counts_add_up_and_do_not_triple_count_stage_units(tables, ids):
    report = prepare_executive(tables, ids, date(2026, 10, 14), date(2026, 11, 12))
    wh = report["warehouses"]
    assert int(wh["Received"].sum()) == report["current"]["orders_received"]
    assert int(wh["Shipped"].sum()) == report["current"]["orders_shipped"]
    assert int(wh["Overdue open"].sum()) == report["current"]["overdue_open_asof"]
    assert int(wh["Backlog open"].sum()) == report["current"]["open_backlog_asof"]
    assert set(report["processes"]["Process"]) == {"Picking", "Packing", "Dispatch"}
    assert report["picking_rate"] == pytest.approx(report["processes"].set_index("Process").loc["Picking", "Units/labor h"])


def test_historical_date_suppresses_current_workflow_and_latest_stock_watch(tables, ids):
    cases = get_cases(DB)
    report = prepare_executive(tables, ids, date(2025, 6, 1), date(2025, 6, 30), workflow_cases=cases)
    assert report["cases_summary"] is None
    assert not report["priorities"]["Management review"].eq("Check replenishment exposure").any()
    assert report["as_of"].date() == date(2025, 6, 30)
    assert report["inventory_snapshot_at"].date() == date(2026, 11, 12)


def test_latest_case_count_and_no_db_modification(tables, ids):
    with sqlite3.connect(DB) as db:
        prior = (db.execute("SELECT COUNT(*) FROM exception_cases").fetchone()[0],
                 db.execute("SELECT COUNT(*) FROM exception_events").fetchone()[0])
    report = prepare_executive(tables, ids, date(2026, 10, 14), date(2026, 11, 12), workflow_cases=get_cases(DB))
    assert report["cases_summary"]["tracked_active"] == 53
    with sqlite3.connect(DB) as db:
        after = (db.execute("SELECT COUNT(*) FROM exception_cases").fetchone()[0],
                 db.execute("SELECT COUNT(*) FROM exception_events").fetchone()[0])
    assert prior == after


def test_references_adjust_priorities_without_changing_raw_metrics(tables, ids):
    args = (tables, ids, date(2026, 10, 14), date(2026, 11, 12))
    base = prepare_executive(*args)
    relaxed = prepare_executive(*args, targets={"on_time_shipping_pct": 75,
                                                  "qc_defect_rate_pct": 10,
                                                  "picking_units_per_hour": 1})
    assert relaxed["current"] == base["current"]
    assert not relaxed["priorities"]["Management review"].eq("Investigate on-time performance").any()
    assert not relaxed["priorities"]["Management review"].eq("Review quality defects").any()
    assert not relaxed["priorities"]["Management review"].eq("Review picking throughput").any()


def test_html_printable_escape(tables, ids):
    report = prepare_executive(tables, ids, date(2026, 10, 14), date(2026, 11, 12))
    exported = export_brief_html(report, ["<script>bad()</script>"]).decode("utf-8")
    assert "&lt;script&gt;" in exported
    assert "<script>" not in exported
    assert "SYNTHETIC DATA ONLY" in exported
    assert "Executive Brief" in exported


def test_invalid_dates_or_targets_rejected(tables, ids):
    with pytest.raises(ValueError):
        prepare_executive(tables, ids, date(2026, 11, 12), date(2026, 11, 1))
    with pytest.raises(ValueError):
        prepare_executive(tables, ids, date(2026, 11, 1), date(2026, 11, 12), targets={
            "on_time_shipping_pct": 101, "qc_defect_rate_pct": 1, "picking_units_per_hour": 5})
