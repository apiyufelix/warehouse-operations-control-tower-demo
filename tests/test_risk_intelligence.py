"""Time-safe synthetic risk triage, regression, and holdout checks."""
from datetime import date
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from analytics import load_tables
from risk_intelligence import prepare_risk_intelligence, _score, _fit

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def tables():
    return load_tables(ROOT / 'warehouse_ops.db')


@pytest.fixture(scope='module')
def ids(tables):
    return tables['warehouses']['warehouse_id'].tolist()


def test_current_snapshot_queue_and_breach_risk_separation(tables, ids):
    x = prepare_risk_intelligence(tables, ids, date(2026, 11, 12))
    assert x['summary']['open_in_window'] == len(x['orders'])
    assert x['summary']['already_overdue'] > 0
    assert x['summary']['upcoming'] > 0
    overdue = x['orders'].query("deadline_state == 'Already overdue'")
    upcoming = x['orders'].query("deadline_state == 'Upcoming'")
    assert overdue['risk_score_pct'].isna().all()
    assert overdue['risk_band'].eq('Already late').all()
    assert upcoming['risk_score_pct'].notna().all()
    assert upcoming['risk_score_pct'].between(0, 100).all()
    assert len(x['orders']) == x['orders']['order_id'].nunique()
    assert (x['orders']['created_at'] <= x['as_of']).all()
    assert (x['orders']['shipped_at'].isna() | (x['orders']['shipped_at'] > x['as_of'])).all()


def test_no_leakage_from_future_order_or_later_qc(tables, ids):
    day = date(2026, 8, 15)
    baseline = prepare_risk_intelligence(tables, ids, day)
    altered = dict(tables)
    added = tables['orders'].iloc[[0]].copy()
    added.loc[:, 'order_id'] = 'SYN_FUTURE_ORDER'
    added.loc[:, 'created_at'] = pd.Timestamp('2026-11-01T03:00:00Z')
    added.loc[:, 'promised_ship_at'] = pd.Timestamp('2026-11-02T03:00:00Z')
    altered['orders'] = pd.concat([tables['orders'], added], ignore_index=True)
    q = tables['quality_inspections'].copy()
    q.loc[q['inspected_at'].gt(pd.Timestamp('2026-09-01', tz='UTC')), 'defect_qty'] = 999
    altered['quality_inspections'] = q
    inv = tables['inventory'].copy()
    inv['available_qty'] = 0
    altered['inventory'] = inv
    comparison = prepare_risk_intelligence(altered, ids, day)
    pd.testing.assert_frame_equal(baseline['orders'], comparison['orders'])
    assert baseline['evaluation'] == comparison['evaluation']
    assert not baseline['is_latest_snapshot']
    assert not comparison['is_latest_snapshot']


def test_warehouse_filters_and_horizon(tables, ids):
    all_ = prepare_risk_intelligence(tables, ids, date(2026, 11, 12), 48)
    left = prepare_risk_intelligence(tables, ids[:1], date(2026, 11, 12), 48)
    assert set(left['orders']['warehouse_id']) <= set(ids[:1])
    assert len(left['orders']) <= len(all_['orders'])
    short = prepare_risk_intelligence(tables, ids, date(2026, 11, 12), 24)
    long = prepare_risk_intelligence(tables, ids, date(2026, 11, 12), 72)
    assert len(short['orders']) <= len(all_['orders']) <= len(long['orders'])
    assert short['summary']['already_overdue'] == long['summary']['already_overdue']


def test_holdout_is_out_of_time_and_counts_match(tables, ids):
    x = prepare_risk_intelligence(tables, ids, date(2026, 11, 12))
    e = x['evaluation']
    assert e['training_orders'] > 100
    assert e['evaluation_orders'] > 50
    assert e['holdout_start'] < e['holdout_end']
    assert 0 <= e['brier_score'] <= 1
    assert 0 <= e['baseline_brier'] <= 1
    assert x['calibration']['orders'].sum() == e['evaluation_orders']


def test_group_score_shrinks_to_prior_for_unseen_buckets():
    training = pd.DataFrame({
        'warehouse_priority': ['A / Standard'] * 200,
        'lead_band': ['24–48h'] * 200,
        'size_band': ['4–8 units'] * 200,
        'late_observed': [0] * 180 + [1] * 20,
    })
    model = _fit(training)
    unknown = pd.DataFrame({'warehouse_priority': ['Z / Premium'], 'lead_band': ['not seen'],
                            'size_band': ['unseen']})
    assert abs(_score(unknown, model).iloc[0] - model['prior']) < 1e-9


def test_risk_read_only_does_not_change_case_history(tables, ids):
    with sqlite3.connect(ROOT / 'warehouse_ops.db') as db:
        before = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                  for t in ('orders', 'exception_cases', 'exception_events')}
    prepare_risk_intelligence(tables, ids, date(2026, 11, 12))
    with sqlite3.connect(ROOT / 'warehouse_ops.db') as db:
        after = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                 for t in ('orders', 'exception_cases', 'exception_events')}
    assert before == after


def test_invalid_horizon_and_empty_selection(tables, ids):
    with pytest.raises(ValueError):
        prepare_risk_intelligence(tables, ids, date(2026, 11, 12), 30)
    with pytest.raises(ValueError):
        prepare_risk_intelligence(tables, [], date(2026, 11, 12), 24)
