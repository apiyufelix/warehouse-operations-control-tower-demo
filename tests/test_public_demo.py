"""Tests for the public packaging and read-only reporting layer."""
from pathlib import Path
import sqlite3
import sys
import ast
from datetime import date

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from analytics import TABLES, load_tables, prepare_dashboard
from forecasting import prepare_forecast
from risk_intelligence import prepare_risk_intelligence
from productivity import prepare_productivity
from executive import prepare_executive


def test_database_includes_only_public_source_tables():
    db = PROJECT / "warehouse_ops_demo.db"
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        actual = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith('sqlite_')}
    assert actual == set(TABLES)


def test_database_immutable_connection_rejects_writes():
    db = PROJECT / "warehouse_ops_demo.db"
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        try:
            conn.execute("UPDATE orders SET priority='Standard' WHERE order_id='ORD0000001'")
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc).lower() or "read-only" in str(exc).lower()
        else:
            raise AssertionError("Immutable SQLite connection unexpectedly allowed a write")


def test_public_app_has_no_uploads_or_writers():
    source = (PROJECT / "app.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    funcs = {n.func.attr for n in ast.walk(module) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not funcs.intersection({"file_uploader", "data_editor", "form_submit_button", "sqlite3", "apply_import", "update_case", "sync_detected_cases"})
    assert "with tab_refresh:" not in source
    assert "from exception_workflow import" not in source
    assert "from data_ingestion import" not in source
    assert not (PROJECT/"data_ingestion.py").exists()
    assert not (PROJECT/"exception_workflow.py").exists()


def test_public_reporting_engines_work():
    tables = load_tables(PROJECT / "warehouse_ops_demo.db")
    ids = tables["warehouses"]["warehouse_id"].tolist()
    d0, d1 = date(2026, 10, 14), date(2026, 11, 12)
    result = prepare_dashboard(tables, ids, d0, d1)
    assert result["metrics"]["orders_received"] == 1339
    assert result["metrics"]["orders_shipped"] == 1337
    assert result["metrics"]["overdue_open_asof"] == 53
    assert len(result["exceptions"]) == 53
    assert prepare_productivity(tables, ids, d0, d1)["metrics"]
    assert prepare_forecast(tables, ids, d1, horizon=7)["totals"].shape[0] == 7
    assert prepare_risk_intelligence(tables, ids, d1, horizon_hours=48)["summary"]
    assert prepare_executive(tables, ids, d0, d1)["current"]
