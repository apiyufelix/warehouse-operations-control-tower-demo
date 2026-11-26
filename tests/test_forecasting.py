"""Step 6 forecast tests. Synthetic numbers are illustrative, not production estimates."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from analytics import load_tables
from forecasting import (_backtest, _complete_day, _estimate, prepare_forecast,
                         prepare_capacity_scenario, prepare_alerts)

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def tables():
    return load_tables(ROOT / 'warehouse_ops.db')


@pytest.fixture(scope='module')
def ids(tables):
    return tables['warehouses']['warehouse_id'].tolist()


def test_forecast_shape_dates_and_nonnegative_bounds(tables, ids):
    for horizon in (7, 14):
        f = prepare_forecast(tables, ids, date(2026, 11, 12), horizon)
        assert len(f['by_warehouse']) == horizon * len(ids)
        assert len(f['totals']) == horizon
        assert f['by_warehouse']['date'].min() == pd.Timestamp('2026-11-13', tz='UTC')
        assert (f['by_warehouse']['lower_orders'] >= 0).all()
        assert (f['by_warehouse']['lower_orders'] <= f['by_warehouse']['forecast_orders']).all()
        assert (f['by_warehouse']['upper_orders'] >= f['by_warehouse']['forecast_orders']).all()
        summed = f['by_warehouse'].groupby('date')['forecast_orders'].sum().sort_index()
        pd.testing.assert_series_equal(
            summed, f['totals'].set_index('date')['forecast_orders'].sort_index(),
            check_names=False,
        )


def test_partial_day_never_enters_training(tables, ids):
    f = prepare_forecast(tables, ids, date(2026, 11, 12))
    assert f['last_complete_day'] == pd.Timestamp('2026-11-11', tz='UTC')
    assert f['history']['date'].max() <= f['last_complete_day']
    assert _complete_day(pd.Timestamp('2026-11-10 23:59:59.999999', tz='UTC')) == pd.Timestamp('2026-11-10', tz='UTC')


def test_rolling_backtest_uses_past_exclusively():
    idx = pd.date_range('2025-01-01', periods=90, tz='UTC')
    a = pd.Series([10.0] * 90, index=idx)
    a.iloc[-1] = 10_000.0
    target = a.index[-1]
    for method in ('weekday', 'rolling'):
        pred = _estimate(a, target, method)
        assert pred < 100  # target's spike cannot inform its own prediction
    choice, _, _, _, report = _backtest(a)
    assert choice in ('weekday', 'rolling')
    assert report['date'].nunique() == 28
    assert report.loc[report['date'].eq(target), 'prediction'].max() < 100


def test_historical_forecasts_ignore_future_new_orders(tables, ids):
    historical = date(2026, 9, 30)
    reference = prepare_forecast(tables, ids, historical)
    changed = dict(tables)
    future = tables['orders'].iloc[[0]].copy()
    future['order_id'] = 'FUTURE_MUST_NOT_LEAK'
    future['created_at'] = pd.Timestamp('2026-11-10', tz='UTC')
    future['promised_ship_at'] = pd.Timestamp('2026-11-12', tz='UTC')
    changed['orders'] = pd.concat([tables['orders'], future], ignore_index=True)
    observed = prepare_forecast(changed, ids, historical)
    pd.testing.assert_frame_equal(reference['by_warehouse'], observed['by_warehouse'])


def test_capacity_scenario_stops_at_report_cutoff(tables, ids):
    historical = prepare_forecast(tables, ids, date(2026, 8, 15))
    baseline = prepare_capacity_scenario(tables, historical)
    modified = dict(tables)
    a = tables['process_activities'].copy()
    after = a['completed_at'] > pd.Timestamp('2026-09-01', tz='UTC')
    a.loc[after, 'units_processed'] = 999999
    modified['process_activities'] = a
    l = tables['labor_shifts'].copy()
    after = l['shift_start_at'] > pd.Timestamp('2026-09-01', tz='UTC')
    l.loc[after, 'actual_labor_hours'] = 999999
    modified['labor_shifts'] = l
    pd.testing.assert_frame_equal(baseline, prepare_capacity_scenario(modified, historical))
    assert (baseline['required_labor_hours'].dropna() >= 0).all()


def test_alert_categories_and_historical_snapshot_guard(tables, ids):
    now = prepare_forecast(tables, ids, date(2026, 11, 12))
    alerts = prepare_alerts(tables, ids, now, prepare_capacity_scenario(tables, now))
    assert 'Overdue open order' in set(alerts['category'])
    assert 'Upcoming deadline' in set(alerts['category'])
    assert 'Inventory reorder watch' in set(alerts['category'])
    assert 'Illustrative capacity gap' in set(alerts['category'])
    old = prepare_forecast(tables, ids, date(2026, 10, 5))
    old_alerts = prepare_alerts(tables, ids, old, prepare_capacity_scenario(tables, old))
    assert 'Inventory reorder watch' not in set(old_alerts['category'])


def test_no_database_changes_when_reading_forecasts(tables, ids):
    import sqlite3
    with sqlite3.connect(ROOT / 'warehouse_ops.db') as db:
        before = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ('orders', 'exception_cases', 'exception_events')}
    f = prepare_forecast(tables, ids, date(2026, 11, 12))
    prepare_alerts(tables, ids, f, prepare_capacity_scenario(tables, f))
    with sqlite3.connect(ROOT / 'warehouse_ops.db') as db:
        after = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                 for name in ('orders', 'exception_cases', 'exception_events')}
    assert before == after


def test_short_history_is_not_silently_extrapolated():
    with pytest.raises(ValueError):
        _backtest(pd.Series([7] * 40, index=pd.date_range('2026-01-01', periods=40, tz='UTC')))


def test_validation_rejects_unsupported_horizon(tables, ids):
    with pytest.raises(ValueError):
        prepare_forecast(tables, ids, date(2026, 11, 12), horizon=45)
