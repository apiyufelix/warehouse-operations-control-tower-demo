"""SQLite-backed exception case management for synthetic warehouse data.

Detection uses the last available warehouse snapshot, not a live WMS feed.
Workflow timestamps use real UTC wall time, which differs from data snapshot time.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SEVERITIES = ("Critical", "High", "Medium", "Low")
STATUSES = ("New", "In Progress", "Monitoring", "Resolved")
SLA_HOURS = {"Critical": 4, "High": 8, "Medium": 24, "Low": 48}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def severity_for(overdue_hours: float, priority: str) -> str:
    """Transparent demo rules based on aging and order priority."""
    if overdue_hours >= 48 or (priority == "SameDay" and overdue_hours >= 12):
        return "Critical"
    if overdue_hours >= 24 or (priority == "Expedited" and overdue_hours >= 8):
        return "High"
    if overdue_hours >= 8 or (priority == "SameDay" and overdue_hours >= 2):
        return "Medium"
    return "Low"


@contextmanager
def _connect(path: str | Path):
    """Open a short-lived, properly closed SQLite transaction."""
    conn = sqlite3.connect(str(path), timeout=15)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        with conn:
            yield conn
    finally:
        conn.close()


def initialize_workflow(path: str | Path) -> None:
    """Add workflow tables to an existing Step 3 database, preserving its other tables."""
    with _connect(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS exception_cases (
            order_id TEXT PRIMARY KEY REFERENCES orders(order_id),
            severity TEXT NOT NULL CHECK(severity IN ('Critical','High','Medium','Low')),
            workflow_status TEXT NOT NULL DEFAULT 'New'
                CHECK(workflow_status IN ('New','In Progress','Monitoring','Resolved')),
            assigned_owner TEXT NOT NULL DEFAULT '',
            root_cause TEXT NOT NULL DEFAULT '',
            corrective_action TEXT NOT NULL DEFAULT '',
            resolution_notes TEXT NOT NULL DEFAULT '',
            first_detected_at TEXT NOT NULL,
            sla_due_at TEXT NOT NULL,
            last_updated_at TEXT NOT NULL,
            resolved_at TEXT,
            currently_overdue INTEGER NOT NULL DEFAULT 1 CHECK(currently_overdue IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS exception_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            event_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_details TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cases_status_severity
            ON exception_cases(workflow_status, severity);
        CREATE INDEX IF NOT EXISTS idx_cases_current ON exception_cases(currently_overdue);
        CREATE INDEX IF NOT EXISTS idx_events_order ON exception_events(order_id,event_at);
        """)


def sync_detected_cases(path: str | Path, overdue: pd.DataFrame) -> dict[str, int]:
    """Sync all currently overdue orders from the latest snapshot; safe to repeat.

    Existing human workflow edits are preserved. Automatically cleared overdue
    conditions are flagged but not silently marked as manually resolved.
    """
    required = {"order_id", "overdue_hours", "priority"}
    if not required.issubset(overdue.columns):
        raise ValueError(f"Missing columns: {required - set(overdue.columns)}")
    if overdue["order_id"].duplicated().any():
        raise ValueError("Duplicate order IDs in detected exceptions")
    initialize_workflow(path)
    now = utc_now()
    detected_ids = set(overdue["order_id"].astype(str))
    counts = {"new": 0, "escalated": 0, "reactivated": 0, "cleared": 0}
    with _connect(path) as conn:
        # BEGIN IMMEDIATE avoids another writer interleaving case synchronization.
        conn.execute("BEGIN IMMEDIATE")
        existing = {row["order_id"]: row for row in conn.execute("SELECT * FROM exception_cases")}
        for row in overdue.itertuples(index=False):
            oid = str(row.order_id)
            severity = severity_for(float(row.overdue_hours), str(row.priority))
            old = existing.get(oid)
            if old is None:
                deadline = (datetime.fromisoformat(now) + timedelta(hours=SLA_HOURS[severity])).isoformat(timespec="microseconds")
                conn.execute("""
                    INSERT INTO exception_cases
                    (order_id,severity,workflow_status,first_detected_at,sla_due_at,last_updated_at)
                    VALUES (?,?, 'New',?,?,?)
                """, (oid, severity, now, deadline, now))
                conn.execute("""
                    INSERT INTO exception_events(order_id,event_at,actor,event_type,event_details)
                    VALUES (?,?,'System','Detected',?)
                """, (oid, now, json.dumps({"severity": severity, "demo_sla_hours": SLA_HOURS[severity]})))
                counts["new"] += 1
            else:
                changes = {}
                if old["severity"] != severity:
                    changes["severity"] = [old["severity"], severity]
                    # Escalation keeps the original first-detection anchor and never
                    # extends an existing SLA deadline.
                    proposed = (datetime.fromisoformat(old["first_detected_at"]) +
                                timedelta(hours=SLA_HOURS[severity])).isoformat(timespec="microseconds")
                    deadline = min(old["sla_due_at"], proposed)
                    conn.execute("UPDATE exception_cases SET severity=?, sla_due_at=? WHERE order_id=?",
                                 (severity, deadline, oid))
                    counts["escalated"] += 1
                if not old["currently_overdue"]:
                    changes["currently_overdue"] = [0, 1]
                    counts["reactivated"] += 1
                if changes:
                    conn.execute("UPDATE exception_cases SET currently_overdue=1, last_updated_at=? WHERE order_id=?", (now, oid))
                    conn.execute("""
                        INSERT INTO exception_events(order_id,event_at,actor,event_type,event_details)
                        VALUES (?,?,'System','Detection updated',?)
                    """, (oid, now, json.dumps(changes)))
        for oid, old in existing.items():
            if old["currently_overdue"] and oid not in detected_ids:
                conn.execute("UPDATE exception_cases SET currently_overdue=0, last_updated_at=? WHERE order_id=?", (now, oid))
                conn.execute("""
                    INSERT INTO exception_events(order_id,event_at,actor,event_type,event_details)
                    VALUES (?,?,'System','No longer overdue',?)
                """, (oid, now, '{"currently_overdue": [1, 0]}'))
                counts["cleared"] += 1
    return counts


def get_cases(path: str | Path) -> pd.DataFrame:
    initialize_workflow(path)
    with _connect(path) as conn:
        frame = pd.read_sql_query("SELECT * FROM exception_cases ORDER BY currently_overdue DESC, first_detected_at", conn)
    return frame


def get_case_events(path: str | Path, order_id: str) -> pd.DataFrame:
    with _connect(path) as conn:
        return pd.read_sql_query("""
            SELECT event_at AS 'Event time (UTC)', actor AS Actor,
                   event_type AS Event, event_details AS Details
            FROM exception_events WHERE order_id=? ORDER BY event_id DESC
        """, conn, params=(order_id,))


def update_case(
    path: str | Path, order_id: str, *, status: str, assigned_owner: str,
    root_cause: str, corrective_action: str, resolution_notes: str,
    expected_updated_at: str, actor: str = "Dashboard user",
) -> bool:
    """Save a case edit and audit trail atomically; reject stale or invalid changes.

    Returns False when no fields changed. Raises ValueError for stale editing.
    """
    if status not in STATUSES:
        raise ValueError("Invalid workflow status")
    fields = {
        "workflow_status": status,
        "assigned_owner": assigned_owner.strip(),
        "root_cause": root_cause.strip(),
        "corrective_action": corrective_action.strip(),
        "resolution_notes": resolution_notes.strip(),
    }
    for name, val in fields.items():
        if len(val) > 2000:
            raise ValueError(f"{name} cannot exceed 2000 characters")
    if status == "Resolved" and (not fields["root_cause"] or not fields["corrective_action"]):
        raise ValueError("Record the root cause and corrective action before resolving a case")
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM exception_cases WHERE order_id=?", (order_id,)).fetchone()
        if old is None:
            raise ValueError("Exception not found")
        if old["last_updated_at"] != expected_updated_at:
            raise ValueError("This case was changed by another session. Refresh and try again")
        changes = {key: [old[key], value] for key, value in fields.items() if old[key] != value}
        if not changes:
            return False
        now = utc_now()
        resolved_at = old["resolved_at"]
        if status == "Resolved" and old["workflow_status"] != "Resolved":
            resolved_at = now
        elif status != "Resolved":
            resolved_at = None
        conn.execute("""
            UPDATE exception_cases SET workflow_status=?, assigned_owner=?, root_cause=?,
            corrective_action=?, resolution_notes=?, last_updated_at=?, resolved_at=?
            WHERE order_id=?
        """, (fields["workflow_status"], fields["assigned_owner"], fields["root_cause"],
              fields["corrective_action"], fields["resolution_notes"], now, resolved_at, order_id))
        conn.execute("""
            INSERT INTO exception_events(order_id,event_at,actor,event_type,event_details)
            VALUES (?,?,?,'Case updated',?)
        """, (order_id, now, actor, json.dumps(changes)))
    return True
