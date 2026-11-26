"""Step 6: explainable, backtested synthetic order-demand forecast and staffing scenario.

No future observations are used. Reports, warehouse shifts, and all date labels are UTC.
Forecasts are not contractual promises, calibrated prediction intervals, or staffing orders.
"""
from __future__ import annotations

from datetime import date
import numpy as np
import pandas as pd

from analytics import reporting_cutoff
from productivity import PROCESSES

MODEL_NAMES = {
    'weekday': 'Recent same-weekday pattern',
    'rolling': 'Recent 28-day average',
}


def _complete_day(as_of: pd.Timestamp) -> pd.Timestamp:
    """End at last *fully observed* UTC day, excluding partial snapshot days."""
    day = as_of.normalize()
    if as_of < day + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1):
        return day - pd.Timedelta(days=1)
    return day


def _estimate(history: pd.Series, forecast_date: pd.Timestamp, method: str) -> float:
    """Simple, nonnegative, explainable estimator requiring no compiled ML libraries."""
    past = history.loc[history.index < forecast_date]
    if past.empty:
        return 0.0
    trailing = past.iloc[-28:]
    if method == 'rolling':
        return max(0.0, float(trailing.mean()))
    if method != 'weekday':
        raise ValueError('Unknown model')
    matching = past.loc[past.index.dayofweek == forecast_date.dayofweek].iloc[-8:]
    if len(matching) < 2:
        return max(0.0, float(trailing.mean()))
    weights = np.arange(1, len(matching) + 1, dtype=float)
    base = float(np.average(matching.values, weights=weights))
    # Recent-level adjustment is capped so a brief spike cannot dominate forecasts.
    if len(past) >= 56:
        older = float(past.iloc[-56:-28].mean())
        recent = float(past.iloc[-28:].mean())
        multiplier = np.clip(recent / older, 0.85, 1.15) if older > 0 else 1.0
        base *= float(multiplier)
    return max(0.0, base)


def _backtest(history: pd.Series) -> tuple[str, float, float, float, pd.DataFrame]:
    """Walk-forward validation split: 14 days for selection, 14 later days for evaluation.

    Every one-day-ahead prediction uses only observations strictly before its target.
    Reported MAE/WAPE are from days NOT used to choose the candidate method.
    """
    if len(history) < 70:
        raise ValueError('At least 70 complete calendar days of order history required')
    days = history.index[-28:]
    rows = []
    for pos, target_day in enumerate(days):
        for method in MODEL_NAMES:
            prediction = _estimate(history.loc[history.index < target_day], target_day, method)
            actual = float(history.loc[target_day])
            rows.append({'date': target_day, 'split': 'selection' if pos < 14 else 'evaluation',
                         'method': method, 'actual': actual,
                         'prediction': prediction, 'abs_error': abs(actual - prediction)})
    report = pd.DataFrame(rows)
    selection = report.loc[report['split'].eq('selection')]
    ranking = selection.groupby('method')['abs_error'].mean().to_dict()
    selected = min(MODEL_NAMES, key=lambda m: (ranking[m], list(MODEL_NAMES).index(m)))
    chosen_eval = report.loc[report['split'].eq('evaluation') & report['method'].eq(selected)]
    mae = float(chosen_eval['abs_error'].mean())
    wape = (float(chosen_eval['abs_error'].sum() / chosen_eval['actual'].sum() * 100)
            if chosen_eval['actual'].sum() else np.nan)
    band = float(chosen_eval['abs_error'].quantile(0.8))
    return selected, mae, wape, band, report


def prepare_forecast(tables: dict[str, pd.DataFrame], warehouse_ids: list[str],
                     end_date: date, horizon: int = 7) -> dict:
    if horizon not in (7, 14):
        raise ValueError('Forecast horizon must be 7 or 14 days')
    if not warehouse_ids:
        raise ValueError('Select a warehouse')
    as_of = reporting_cutoff(tables, end_date)
    complete_through = _complete_day(as_of)
    names = tables['warehouses'].set_index('warehouse_id')['warehouse_name'].to_dict()
    orders = tables['orders']
    future_days = pd.date_range(as_of.normalize() + pd.Timedelta(days=1), periods=horizon, freq='D', tz='UTC')
    forecasts, histories, scores, evaluations = [], [], [], []
    for wid in warehouse_ids:
        scope = orders.loc[orders['warehouse_id'].eq(wid)
                           & orders['created_at'].le(as_of)].copy()
        if scope.empty:
            continue
        first = scope['created_at'].min().normalize()
        index = pd.date_range(first, complete_through, freq='D', tz='UTC')
        counts = scope.loc[scope['created_at'] < complete_through + pd.Timedelta(days=1)].groupby(
            scope['created_at'].dt.normalize()).size().reindex(index, fill_value=0)
        counts = counts.astype(float)
        selected, mae, wape, band, evaluated = _backtest(counts)
        for day in future_days:
            pred = _estimate(counts, day, selected)
            forecasts.append({'date': day, 'warehouse_id': wid,
                              'warehouse_name': names.get(wid, wid),
                              'forecast_orders': int(round(pred)),
                              'lower_orders': max(0, int(np.floor(pred - band))),
                              'upper_orders': int(np.ceil(pred + band))})
        for day, value in counts.iloc[-28:].items():
            histories.append({'date': day, 'warehouse_id': wid,
                              'warehouse_name': names.get(wid, wid), 'actual_orders': int(value)})
        scores.append({'warehouse_id': wid, 'warehouse_name': names.get(wid, wid),
                       'selected_method': MODEL_NAMES[selected], 'mae_orders': round(mae, 2),
                       'wape_pct': round(wape, 2), 'band_abs_error_80': round(band, 2),
                       'backtest_days': int(evaluated.loc[evaluated['split'].eq('evaluation'), 'date'].nunique()),
                       'last_complete_day': complete_through})
        evaluations.append(evaluated.assign(warehouse_id=wid, warehouse_name=names.get(wid, wid)))
    if not forecasts:
        raise ValueError('No sufficient historical order records for the selected warehouses')
    frame = pd.DataFrame(forecasts)
    total = frame.groupby('date', as_index=False)[['forecast_orders', 'lower_orders', 'upper_orders']].sum()
    return {'as_of': as_of, 'last_complete_day': complete_through, 'horizon': horizon,
            'by_warehouse': frame, 'totals': total, 'history': pd.DataFrame(histories),
            'accuracy': pd.DataFrame(scores), 'backtest': pd.concat(evaluations, ignore_index=True)}


def prepare_capacity_scenario(tables: dict[str, pd.DataFrame], forecast: dict) -> pd.DataFrame:
    """Illustrative *incoming-order-equivalent* stage workload, not shipment/staffing commitments.

    Stage hours = forecast incoming orders × historical dispatched units per order ÷
                  observed stage units per direct labor hour.
    'Usual hours' uses recent actual labor hours as a proxy, NOT scheduled future shifts.
    Historical inputs stop strictly at the as-of cutoff and complete-day boundary.
    """
    as_of = forecast['as_of']
    full_end = forecast['last_complete_day']
    cutoff = full_end + pd.Timedelta(days=1)
    begin56 = cutoff - pd.Timedelta(days=56)
    begin28 = cutoff - pd.Timedelta(days=28)
    activities = tables['process_activities']
    a = activities.loc[activities['completed_at'].ge(begin56)
                       & activities['completed_at'].lt(cutoff)
                       & activities['completed_at'].le(as_of)]
    shifts = tables['labor_shifts']
    l = shifts.loc[shifts['shift_start_at'].ge(begin56)
                   & shifts['shift_end_at'].le(cutoff)
                   & shifts['shift_end_at'].le(as_of)]
    lines = []
    for wid, f in forecast['by_warehouse'].groupby('warehouse_id'):
        dispatched = a.loc[a['warehouse_id'].eq(wid) & a['process_name'].eq('Dispatch')]
        avg_units = float(dispatched['units_processed'].mean()) if not dispatched.empty else np.nan
        for process in PROCESSES:
            aa = a.loc[a['warehouse_id'].eq(wid) & a['process_name'].eq(process)]
            ll = l.loc[l['warehouse_id'].eq(wid) & l['process_name'].eq(process)]
            recent_l = ll.loc[ll['shift_start_at'].ge(begin28)]
            recorded_labor = float(ll['actual_labor_hours'].sum())
            stage_units = float(aa['units_processed'].sum())
            rate = stage_units / recorded_labor if recorded_labor > 0 else np.nan
            # Only report capacity when sufficient real (synthetic) observations exist.
            recent_day = recent_l.assign(day=recent_l['shift_start_at'].dt.normalize()).groupby('day')['actual_labor_hours'].sum()
            usual_hours = float(recent_day.sum() / 28.0) if len(recent_day) >= 21 else np.nan
            for row in f.itertuples():
                projected_units = row.forecast_orders * avg_units if np.isfinite(avg_units) else np.nan
                required = projected_units / rate if np.isfinite(rate) and rate > 0 else np.nan
                gap = required - usual_hours if np.isfinite(required) and np.isfinite(usual_hours) else np.nan
                lines.append({'date': row.date, 'warehouse_id': wid, 'warehouse_name': row.warehouse_name,
                              'process_name': process, 'forecast_orders': row.forecast_orders,
                              'assumed_units_per_order': round(avg_units, 2) if np.isfinite(avg_units) else np.nan,
                              'historical_stage_units_per_hour': round(rate, 2) if np.isfinite(rate) else np.nan,
                              'required_labor_hours': round(required, 2) if np.isfinite(required) else np.nan,
                              'usual_daily_labor_hours': round(usual_hours, 2) if np.isfinite(usual_hours) else np.nan,
                              'labor_gap_hours': round(gap, 2) if np.isfinite(gap) else np.nan})
    return pd.DataFrame(lines)


def prepare_alerts(tables: dict[str, pd.DataFrame], warehouse_ids: list[str],
                   forecast: dict, capacity: pd.DataFrame) -> pd.DataFrame:
    """Transparent rules only: no fabricated order-level late-shipment probabilities."""
    as_of = forecast['as_of']
    names = tables['warehouses'].set_index('warehouse_id')['warehouse_name'].to_dict()
    rows = []
    orders = tables['orders']
    shipped = tables['shipments'][['order_id', 'shipped_at']]
    joined = orders.loc[orders['warehouse_id'].isin(warehouse_ids)
                        & orders['created_at'].le(as_of)].merge(shipped, on='order_id', how='left', validate='one_to_one')
    open_orders = joined.loc[joined['order_status'].ne('Cancelled')
                             & (joined['shipped_at'].isna() | joined['shipped_at'].gt(as_of))]
    for order in open_orders.itertuples():
        hours = (order.promised_ship_at - as_of).total_seconds() / 3600
        if hours < 0:
            severity, label = ('Critical' if hours < -24 else 'High'), 'Overdue open order'
            detail = f'Promise missed by {abs(hours):.1f} hours; shipment not recorded by cutoff.'
        elif hours <= 24:
            severity, label = ('High' if hours <= 8 else 'Medium'), 'Upcoming deadline'
            detail = f'Unshipped order promised in {hours:.1f} hours (rule-based watch, not a predicted miss).'
        else:
            continue
        rows.append({'severity': severity, 'category': label, 'warehouse': names.get(order.warehouse_id, order.warehouse_id),
                     'reference': order.order_id, 'effective_date': as_of, 'explanation': detail,
                     'action': 'Review order status, inventory availability, and shipping cutoff.'})
    # Inventory is only present for the latest snapshot. Never backfill it into historical alerts.
    latest_snapshot = tables['inventory']['snapshot_at'].max()
    if as_of == latest_snapshot:
        inv = tables['inventory'].loc[tables['inventory']['snapshot_at'].eq(latest_snapshot)]
        for item in inv.loc[inv['warehouse_id'].isin(warehouse_ids)
                            & inv['available_qty'].le(inv['reorder_point'])].itertuples():
            rows.append({'severity': 'Medium', 'category': 'Inventory reorder watch',
                         'warehouse': names.get(item.warehouse_id, item.warehouse_id),
                         'reference': item.product_id, 'effective_date': as_of,
                         'explanation': f'Available {item.available_qty} units vs. reorder point {item.reorder_point}.',
                         'action': 'Check allocations and replenishment plan; no automatic purchase order.'})
    if not capacity.empty:
        for item in capacity.itertuples():
            if (not np.isfinite(item.labor_gap_hours) or
                not np.isfinite(item.usual_daily_labor_hours) or item.usual_daily_labor_hours <= 0):
                continue
            gap_pct = item.labor_gap_hours / item.usual_daily_labor_hours
            if item.labor_gap_hours <= 1 or gap_pct <= 0.15:
                continue
            severity = 'High' if gap_pct >= 0.3 else 'Medium'
            rows.append({'severity': severity, 'category': 'Illustrative capacity gap',
                         'warehouse': item.warehouse_name,
                         'reference': f'{item.process_name} · {item.date:%Y-%m-%d}',
                         'effective_date': item.date,
                         'explanation': f'Incoming-order scenario needs {item.required_labor_hours:.1f} h vs. '
                                        f'{item.usual_daily_labor_hours:.1f} h usual daily hours (+{gap_pct:.0%}).',
                         'action': 'Review actual labor roster, backlog, stage rates, and order-to-shipment timing.'})
    cols = ['severity', 'category', 'warehouse', 'reference', 'effective_date', 'explanation', 'action']
    frame = pd.DataFrame(rows, columns=cols)
    if not frame.empty:
        rank = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
        frame = frame.assign(_rank=frame['severity'].map(rank)).sort_values(
            ['_rank', 'effective_date', 'warehouse', 'reference']).drop(columns=['_rank']).reset_index(drop=True)
    return frame
