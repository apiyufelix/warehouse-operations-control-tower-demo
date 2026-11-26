"""Run with python -m pytest tests -q from project root."""
from datetime import date
from pathlib import Path
import shutil
import sqlite3

import pytest

from analytics import load_tables, prepare_dashboard
from exception_workflow import (
    SEVERITIES, get_case_events, get_cases, severity_for,
    sync_detected_cases, update_case,
)

SOURCE = Path(__file__).resolve().parents[1] / "warehouse_ops.db"


@pytest.fixture
def db(tmp_path):
    target = tmp_path / "warehouse_ops.db"
    shutil.copy2(SOURCE, target)
    # Ensure tests never depend on any cases bundled in a pre-populated database.
    with sqlite3.connect(target) as conn:
        conn.execute("DROP TABLE IF EXISTS exception_events")
        conn.execute("DROP TABLE IF EXISTS exception_cases")
    return target


@pytest.fixture
def detected(db):
    tables = load_tables(db)
    ids = tables["warehouses"]["warehouse_id"].tolist()
    return prepare_dashboard(tables, ids, date(2026, 11, 1), date(2026, 11, 12))["exceptions"]


def test_severity_rules():
    assert severity_for(49, "Standard") == "Critical"
    assert severity_for(13, "SameDay") == "Critical"
    assert severity_for(25, "Standard") == "High"
    assert severity_for(10, "Expedited") == "High"
    assert severity_for(12, "Standard") == "Medium"
    assert severity_for(2, "Standard") == "Low"


def test_first_sync_and_idempotency(db, detected):
    first = sync_detected_cases(db, detected)
    assert first["new"] == len(detected) == 53
    second = sync_detected_cases(db, detected)
    assert second == {"new": 0, "escalated": 0, "reactivated": 0, "cleared": 0}
    cases = get_cases(db)
    assert len(cases) == 53
    assert set(cases["severity"]).issubset(set(SEVERITIES))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM exception_events").fetchone()[0] == 53


def test_status_notes_and_audit_are_saved(db, detected):
    sync_detected_cases(db, detected)
    case = get_cases(db).iloc[0]
    oid = case["order_id"]
    assert update_case(
        db, oid, status="In Progress", assigned_owner="QC Team",
        root_cause="Incorrect carton label", corrective_action="Reprint label",
        resolution_notes="Awaiting recheck", expected_updated_at=case["last_updated_at"]
    )
    new = get_cases(db).set_index("order_id").loc[oid]
    assert new["workflow_status"] == "In Progress"
    assert new["assigned_owner"] == "QC Team"
    assert len(get_case_events(db, oid)) == 2
    with pytest.raises(ValueError, match="changed by another session"):
        update_case(db, oid, status="Monitoring", assigned_owner="QC Team",
                    root_cause="Incorrect carton label", corrective_action="Reprint label",
                    resolution_notes="Awaiting recheck", expected_updated_at=case["last_updated_at"])


def test_resolution_requires_documentation_and_preserves_source_condition(db, detected):
    sync_detected_cases(db, detected)
    case = get_cases(db).iloc[0]
    oid = case["order_id"]
    with pytest.raises(ValueError, match="root cause and corrective action"):
        update_case(db, oid, status="Resolved", assigned_owner="Ops", root_cause="",
                    corrective_action="", resolution_notes="", expected_updated_at=case["last_updated_at"])
    assert update_case(db, oid, status="Resolved", assigned_owner="Ops",
                       root_cause="Warehouse late", corrective_action="Expedite release",
                       resolution_notes="Verified", expected_updated_at=case["last_updated_at"])
    sync_detected_cases(db, detected)
    new = get_cases(db).set_index("order_id").loc[oid]
    assert new["workflow_status"] == "Resolved"
    assert new["currently_overdue"] == 1   # order data hasn't changed
    assert new["resolved_at"] is not None


def test_auto_clear_flag_preserves_audit_and_all_warehouse_cases(db, detected):
    sync_detected_cases(db, detected)
    one = detected.iloc[0]["order_id"]
    reduced = detected.loc[detected["order_id"].ne(one)]
    changed = sync_detected_cases(db, reduced)
    assert changed["cleared"] == 1
    assert get_cases(db).set_index("order_id").loc[one]["currently_overdue"] == 0
    assert len(get_case_events(db, one)) == 2
    restored = sync_detected_cases(db, detected)
    assert restored["reactivated"] == 1
    assert get_cases(db).set_index("order_id").loc[one]["currently_overdue"] == 1
