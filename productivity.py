"""Step 5: process-level activity, staffing, and bottleneck analytics.

All timestamps are UTC, all data are synthetic. Units across process stages
are *not* additive as unique shipped units; each stage touches the same units.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from analytics import reporting_cutoff

PROCESSES = ('Picking', 'Packing', 'Dispatch')
SHIFTS = ('Shift A (00–08 UTC)', 'Shift B (08–16 UTC)', 'Shift C (16–24 UTC)')
# Illustrative reference rates only; these are NOT an employer's performance targets.
REFERENCE_UNITS_PER_HOUR = {'Picking': 6.5, 'Packing': 8.0, 'Dispatch': 10.5}


def _safe_ratio(top, bottom):
    return float(top / bottom) if bottom > 0 else None


def _summary(activity: pd.DataFrame, labor: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Aggregate stage units and direct labor independently to avoid join fanout."""
    if activity.empty:
        left = pd.DataFrame(columns=group_cols + ['units_processed', 'activities', 'avg_queue_hours', 'p90_queue_hours'])
    else:
        left = activity.groupby(group_cols, observed=True).agg(
            units_processed=('units_processed', 'sum'),
            activities=('activity_id', 'nunique'),
            avg_queue_hours=('queue_minutes', lambda x: x.mean() / 60),
            p90_queue_hours=('queue_minutes', lambda x: x.quantile(.9) / 60),
        ).reset_index()
    if labor.empty:
        right = pd.DataFrame(columns=group_cols + ['actual_labor_hours', 'planned_labor_hours'])
    else:
        right = labor.groupby(group_cols, observed=True).agg(
            actual_labor_hours=('actual_labor_hours', 'sum'),
            planned_labor_hours=('planned_labor_hours', 'sum'),
        ).reset_index()
    both = left.merge(right, on=group_cols, how='outer', validate='one_to_one')
    for c in ('units_processed', 'activities', 'actual_labor_hours', 'planned_labor_hours'):
        both[c] = pd.to_numeric(both[c], errors='coerce').fillna(0)
    both['units_per_labor_hour'] = both['units_processed'].div(
        both['actual_labor_hours'].replace(0, float('nan'))
    )
    both['reference_units_per_hour'] = both['process_name'].map(REFERENCE_UNITS_PER_HOUR)
    both['target_attainment_pct'] = 100 * both['units_per_labor_hour'] / both['reference_units_per_hour']
    both['staffing_vs_plan_pct'] = 100 * (
        both['actual_labor_hours'] - both['planned_labor_hours']
    ) / both['planned_labor_hours'].replace(0, float('nan'))
    return both


def prepare_productivity(
    tables: dict[str, pd.DataFrame], warehouse_ids: list[str],
    start_date: date, end_date: date, process_name: str = 'Picking',
    selected_shifts: list[str] | None = None,
) -> dict:
    """Compute a productivity dashboard from stage completions and labor shifts.

    Date filters apply to activity completion and labor shift start respectively.
    For the Nov 12, 2026 partial shift, synthetic labor hours are prorated to
    the frozen Nov 12 22:00 UTC snapshot in the generator.
    """
    if start_date > end_date:
        raise ValueError('Start date cannot be after end date')
    if not warehouse_ids:
        raise ValueError('Select at least one warehouse')
    if process_name not in PROCESSES:
        raise ValueError(f'Unknown process: {process_name}')
    shifts = list(SHIFTS) if selected_shifts is None else list(selected_shifts)
    if any(shift not in SHIFTS for shift in shifts):
        raise ValueError('Unknown shift selection')
    asof = reporting_cutoff(tables, end_date)
    start = pd.Timestamp(start_date).tz_localize('UTC')
    stages = tables['process_activities']
    staff = tables['labor_shifts']
    activity = stages.loc[
        stages['warehouse_id'].isin(warehouse_ids)
        & stages['completed_at'].between(start, asof)
        & stages['shift_name'].isin(shifts)
    ].copy()
    labor = staff.loc[
        staff['warehouse_id'].isin(warehouse_ids)
        & staff['shift_start_at'].between(start, asof)
        & staff['shift_name'].isin(shifts)
    ].copy()

    # Per-process summary and bottleneck cues are calculated for all three stages.
    by_process = _summary(activity, labor, ['process_name'])
    by_process['process_name'] = pd.Categorical(by_process['process_name'], categories=PROCESSES, ordered=True)
    by_process = by_process.sort_values('process_name').reset_index(drop=True)
    by_process['process_name'] = by_process['process_name'].astype(str)

    a = activity[activity['process_name'].eq(process_name)].copy()
    l = labor[labor['process_name'].eq(process_name)].copy()
    stage_units = int(a['units_processed'].sum())
    labor_hours = float(l['actual_labor_hours'].sum())
    planned_hours = float(l['planned_labor_hours'].sum())
    rate = _safe_ratio(stage_units, labor_hours)
    metrics = {
        'stage_units': stage_units,
        'completed_activities': len(a),
        'actual_labor_hours': labor_hours,
        'planned_labor_hours': planned_hours,
        'units_per_labor_hour': rate,
        'reference_units_per_hour': REFERENCE_UNITS_PER_HOUR[process_name],
        'target_attainment_pct': (100 * rate / REFERENCE_UNITS_PER_HOUR[process_name]) if rate is not None else None,
        'staffing_vs_plan_pct': (100 * (labor_hours - planned_hours) / planned_hours) if planned_hours else None,
        'avg_queue_hours': float(a['queue_minutes'].mean() / 60) if len(a) else None,
        'p90_queue_hours': float(a['queue_minutes'].quantile(.9) / 60) if len(a) else None,
    }
    by_shift = _summary(a, l, ['process_name', 'shift_name'])
    by_shift['shift_name'] = pd.Categorical(by_shift['shift_name'], categories=SHIFTS, ordered=True)
    by_shift = by_shift.sort_values('shift_name')
    by_shift['shift_name'] = by_shift['shift_name'].astype(str)

    by_warehouse_shift = _summary(a, l, ['process_name', 'warehouse_id', 'shift_name'])
    warehouse_names = tables['warehouses'][['warehouse_id', 'warehouse_name']]
    by_warehouse_shift = by_warehouse_shift.merge(
        warehouse_names, on='warehouse_id', how='left', validate='many_to_one'
    )
    by_warehouse_shift = by_warehouse_shift.sort_values(['warehouse_name', 'shift_name'])

    activity_daily = a.assign(date=a['completed_at'].dt.normalize()).groupby('date', observed=True).agg(
        units_processed=('units_processed', 'sum'),
        completed_activities=('activity_id', 'count'),
    ) if not a.empty else pd.DataFrame(columns=['units_processed', 'completed_activities'])
    labor_daily = l.assign(date=l['shift_start_at'].dt.normalize()).groupby('date', observed=True).agg(
        actual_labor_hours=('actual_labor_hours', 'sum'),
    ) if not l.empty else pd.DataFrame(columns=['actual_labor_hours'])
    days = pd.date_range(start=start.normalize(), end=asof.normalize(), freq='D', tz='UTC')
    daily = pd.DataFrame(index=days).join(activity_daily).join(labor_daily)
    daily[['units_processed', 'completed_activities', 'actual_labor_hours']] = (
        daily[['units_processed', 'completed_activities', 'actual_labor_hours']].astype(float).fillna(0)
    )
    daily['units_per_labor_hour'] = daily['units_processed'] / daily['actual_labor_hours'].replace(0, float('nan'))
    daily = daily.reset_index(names='date')

    by_hour = (a.assign(hour_utc=a['completed_at'].dt.hour)
                .groupby('hour_utc', observed=True)['units_processed'].sum()
                .reindex(range(24), fill_value=0)
                .rename('units_processed').reset_index())

    latest = a.sort_values('completed_at', ascending=False).head(100)[
        ['activity_id', 'order_id', 'warehouse_id', 'process_name', 'shift_name',
         'completed_at', 'units_processed', 'queue_minutes', 'processing_minutes']
    ].copy()
    return {
        'as_of': asof, 'selected_process': process_name,
        'metrics': metrics, 'by_process': by_process,
        'by_shift': by_shift, 'by_warehouse_shift': by_warehouse_shift,
        'daily': daily, 'hourly': by_hour, 'recent_activities': latest,
    }
