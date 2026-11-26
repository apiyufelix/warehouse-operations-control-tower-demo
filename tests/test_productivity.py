from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from analytics import load_tables
from productivity import PROCESSES, SHIFTS, prepare_productivity
from scripts.upgrade_productivity import migrate

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'warehouse_ops.db'


@pytest.fixture(scope='module')
def data():
    return load_tables(DB)


def full_report(data, **kwargs):
    ids = data['warehouses']['warehouse_id'].tolist()
    return prepare_productivity(data, ids, date(2024, 11, 1), date(2026, 11, 12), **kwargs)


def test_each_process_matches_unique_shipped_units_without_stage_double_count(data):
    expected_units = int(data['shipments']['units_shipped'].sum())
    expected_events = len(data['shipments'])
    report = full_report(data)
    for process in PROCESSES:
        stage = report['by_process'].set_index('process_name').loc[process]
        assert int(stage['units_processed']) == expected_units
        assert int(stage['activities']) == expected_events
    assert report['metrics']['stage_units'] == expected_units
    assert len(data['process_activities']) == expected_events * 3
    assert not data['process_activities'].duplicated(['order_id', 'process_name']).any()


def test_all_stages_follow_order_and_shipment_timestamps(data):
    a = data['process_activities'].sort_values(['order_id', 'started_at'])
    assert (a['completed_at'] >= a['started_at']).all()
    a = a.merge(data['orders'][['order_id', 'created_at']], on='order_id', validate='many_to_one')
    assert (a['started_at'] >= a['created_at']).all()
    last = a.loc[a['process_name'].eq('Dispatch')]
    last = last.merge(data['shipments'][['order_id', 'shipped_at']], on='order_id', validate='one_to_one')
    assert (last['completed_at'] == last['shipped_at']).all()
    steps = a.groupby('order_id')['process_name'].agg(tuple)
    assert steps.map(lambda x: x == PROCESSES).all()


def test_process_and_shift_totals_reconcile_to_selected_process_metrics(data):
    report = prepare_productivity(
        data, ['WH_NC_01'], date(2026, 10, 14), date(2026, 11, 12),
        process_name='Packing', selected_shifts=[SHIFTS[0], SHIFTS[2]],
    )
    m = report['metrics']
    assert m['stage_units'] == report['by_shift']['units_processed'].sum()
    assert abs(m['actual_labor_hours'] - report['by_shift']['actual_labor_hours'].sum()) < 1e-8
    assert m['stage_units'] == int(report['daily']['units_processed'].sum())
    assert m['stage_units'] == int(report['hourly']['units_processed'].sum())
    assert m['stage_units'] == int(report['by_warehouse_shift']['units_processed'].sum())


def test_warehouses_additive_for_stage_units_and_labor_hours(data):
    dates = (date(2026, 10, 14), date(2026, 11, 12))
    a = prepare_productivity(data, ['WH_NC_01'], *dates)
    b = prepare_productivity(data, ['WH_NC_02'], *dates)
    combined = prepare_productivity(data, ['WH_NC_01', 'WH_NC_02'], *dates)
    assert a['metrics']['stage_units'] + b['metrics']['stage_units'] == combined['metrics']['stage_units']
    assert abs(a['metrics']['actual_labor_hours'] + b['metrics']['actual_labor_hours'] - combined['metrics']['actual_labor_hours']) < 1e-6


def test_historical_cutoff_never_includes_future_activity(data):
    cutoff = date(2025, 2, 14)
    p = prepare_productivity(data, ['WH_NC_01'], date(2025, 2, 1), cutoff)
    assert p['as_of'].date() == cutoff
    assert not p['recent_activities'].empty
    assert p['recent_activities']['completed_at'].max().date() <= cutoff


def test_no_data_when_selected_shifts_empty(data):
    p = prepare_productivity(data, ['WH_NC_01'], date(2026, 10, 14), date(2026, 11, 12), selected_shifts=[])
    assert p['metrics']['stage_units'] == 0
    assert p['metrics']['actual_labor_hours'] == 0
    assert p['metrics']['units_per_labor_hour'] is None


def test_unknown_process_is_rejected(data):
    with pytest.raises(ValueError):
        prepare_productivity(data, ['WH_NC_01'], date(2026, 11, 1), date(2026, 11, 12), process_name='Shipping2')


def test_non_destructive_migration_preserves_cases_and_events(tmp_path):
    copied = tmp_path / 'warehouse_ops.db'
    shutil.copyfile(DB, copied)
    with sqlite3.connect(copied) as conn:
        oid = conn.execute('SELECT order_id FROM orders LIMIT 1').fetchone()[0]
        stamp = '2026-11-12T22:00:00Z'
        conn.execute('''INSERT INTO exception_cases
            (order_id,severity,workflow_status,assigned_owner,root_cause,corrective_action,
             resolution_notes,first_detected_at,sla_due_at,last_updated_at,currently_overdue)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
             (oid,'Low','In Progress','Step 4 demo owner','demo','demo','demo',stamp,stamp,stamp,1))
        conn.execute('INSERT INTO exception_events (order_id,event_at,actor,event_type,event_details) VALUES (?,?,?,?,?)',
                     (oid,stamp,'tester','test','retain this audit record'))
        before = (conn.execute('SELECT COUNT(*) FROM exception_cases').fetchone()[0],
                  conn.execute('SELECT COUNT(*) FROM exception_events').fetchone()[0])
    for _ in range(2):
        counts = migrate(copied)
        assert counts['process_activities'] == 90894
        assert counts['labor_shifts'] == 13356
    with sqlite3.connect(copied) as conn:
        after = (conn.execute('SELECT COUNT(*) FROM exception_cases').fetchone()[0],
                 conn.execute('SELECT COUNT(*) FROM exception_events').fetchone()[0])
        assert after == before
        assert conn.execute('SELECT assigned_owner FROM exception_cases WHERE order_id=?',(oid,)).fetchone()[0] == 'Step 4 demo owner'
