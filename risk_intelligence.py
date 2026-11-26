"""Step 7: explainable shipment-delay risk and evidence-based triage.

EDUCATIONAL / SYNTHETIC DATA ONLY. No causal inference or calibrated probability claims.
Time-aware targets use promised shipment deadlines no later than the as-of cutoff.
No future shipments are consulted when predicting historical snapshots.
No scikit-learn dependency: avoids PyArrow/Windows DLL issues for this project.
"""
from __future__ import annotations

from datetime import date
import numpy as np
import pandas as pd

from analytics import reporting_cutoff

PRIOR_STRENGTH = 35.0
RISK_BANDS = ('High', 'Elevated', 'Moderate', 'Low')


def _features(tables: dict[str, pd.DataFrame], warehouse_ids: list[str], asof: pd.Timestamp) -> pd.DataFrame:
    orders = tables['orders']
    rows = orders.loc[orders['warehouse_id'].isin(warehouse_ids)
                      & orders['created_at'].le(asof)
                      & orders['order_status'].ne('Cancelled')].copy()
    shipments = tables['shipments'][['order_id', 'shipped_at']]
    rows = rows.merge(shipments, on='order_id', how='left', validate='one_to_one')
    sizes = tables['order_lines'].groupby('order_id')['qty_ordered'].sum().rename('units_ordered')
    rows = rows.join(sizes, on='order_id')
    rows['units_ordered'] = rows['units_ordered'].fillna(0).astype(int)
    rows['promise_lead_hours'] = ((rows['promised_ship_at'] - rows['created_at']).dt.total_seconds() / 3600).clip(lower=0)
    lead = rows['promise_lead_hours']
    rows['lead_band'] = np.select([lead <= 12, lead <= 24, lead <= 48],
                                  ['0–12h', '12–24h', '24–48h'], default='>48h')
    units = rows['units_ordered']
    rows['size_band'] = np.select([units <= 3, units <= 8], ['1–3 units', '4–8 units'], default='9+ units')
    rows['warehouse_priority'] = rows['warehouse_id'].astype(str) + ' / ' + rows['priority'].astype(str)
    # The label is available only once the promised deadline has passed as of the chosen cutoff.
    rows['late_observed'] = (rows['shipped_at'].isna() | (rows['shipped_at'] > rows['promised_ship_at'])).astype(int)
    return rows


def _fit(history: pd.DataFrame) -> dict:
    if len(history) < 150:
        raise ValueError('At least 150 completed-promise historical orders are required')
    prior = float(history['late_observed'].mean())
    cohorts = {}
    for feature in ('warehouse_priority', 'lead_band', 'size_band'):
        stats = history.groupby(feature)['late_observed'].agg(['sum', 'count'])
        cohorts[feature] = {str(key): (int(r['sum']), int(r['count'])) for key, r in stats.iterrows()}
    return {'prior': prior, 'cohorts': cohorts, 'training_count': len(history)}


def _score(frame: pd.DataFrame, fitted: dict) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float, index=frame.index)
    base = fitted['prior']
    rates = []
    for feature in ('warehouse_priority', 'lead_band', 'size_band'):
        cohort = fitted['cohorts'][feature]
        n_late = frame[feature].astype(str).map(lambda x: cohort.get(x, (0, 0))[0]).astype(float)
        n_total = frame[feature].astype(str).map(lambda x: cohort.get(x, (0, 0))[1]).astype(float)
        rates.append((n_late + PRIOR_STRENGTH * base) / (n_total + PRIOR_STRENGTH))
    # Smoothed historical cohort mix; predictive *score*, not calibrated probabilities.
    score = .45 * rates[0] + .35 * rates[1] + .20 * rates[2]
    return pd.Series(score.clip(lower=0, upper=1), index=frame.index)


def _risk_band(score: float) -> str:
    if score >= .17:
        return 'High'
    if score >= .11:
        return 'Elevated'
    if score >= .075:
        return 'Moderate'
    return 'Low'


def _holdout(rows: pd.DataFrame, asof: pd.Timestamp) -> tuple[dict, pd.DataFrame]:
    """An earlier chronological cut separates fitting and holdout scoring.

    Evaluation labels are from deadlines already reached by asof; no post-asof events
    are used. For the baseline, use train's unconditional late rate.
    """
    split = asof - pd.Timedelta(days=28)
    train = rows.loc[rows['promised_ship_at'].le(split)]
    evaluation = rows.loc[rows['promised_ship_at'].gt(split) & rows['promised_ship_at'].le(asof)].copy()
    model = _fit(train)
    if len(evaluation) < 30:
        raise ValueError('At least 30 mature deadline outcomes required for evaluation')
    evaluation['risk_score'] = _score(evaluation, model)
    evaluation['baseline'] = model['prior']
    y = evaluation['late_observed'].astype(float)
    brier = float(np.mean((evaluation['risk_score'] - y) ** 2))
    base_brier = float(np.mean((evaluation['baseline'] - y) ** 2))
    bins = pd.cut(evaluation['risk_score'], bins=[-.001, .075, .11, .17, 1.001],
                  labels=['Low', 'Moderate', 'Elevated', 'High'])
    calibration = evaluation.assign(score_band=bins).groupby('score_band', observed=True).agg(
        orders=('order_id', 'size'), average_score=('risk_score', 'mean'), actual_late_rate=('late_observed', 'mean')
    ).reset_index()
    calibration['average_score'] *= 100
    calibration['actual_late_rate'] *= 100
    metrics = {
        'evaluation_orders': int(len(evaluation)),
        'evaluation_days': 28,
        'evaluation_late_pct': round(float(y.mean() * 100), 2),
        'training_late_pct': round(model['prior'] * 100, 2),
        'brier_score': round(brier, 4),
        'baseline_brier': round(base_brier, 4),
        'baseline_better': bool(base_brier <= brier),
        'training_orders': int(len(train)),
        'holdout_start': split,
        'holdout_end': asof,
    }
    return metrics, calibration


def _signals_and_action(
        r: pd.Series, asof: pd.Timestamp, prior: float,
        defects: set[str], inventory_flags: dict[str, list[str]] | None,
        stage_by_order: dict[str, str] | None,
        large_threshold: float,
) -> tuple[str, str]:
    """Observations to investigate, not established causal root causes."""
    signals = []
    if r['promised_ship_at'] < asof:
        signals.append('Past promised shipment deadline')
    elif (r['promised_ship_at'] - asof).total_seconds() <= 8 * 3600:
        signals.append('Deadline within 8 hours')
    if r['promise_lead_hours'] <= 24:
        signals.append('Tight original promise (24h or less)')
    if r['units_ordered'] >= large_threshold:
        signals.append('High-unit-count order')
    if r['order_id'] in defects:
        signals.append('QC defect recorded before cutoff')
    flagged_skus = (inventory_flags or {}).get(r['order_id'], [])
    if flagged_skus:
        signals.append('Latest snapshot: SKU availability requires review (' + ', '.join(flagged_skus[:3]) + ')')
    if stage_by_order and r['order_id'] in stage_by_order:
        signals.append('Current source status: ' + stage_by_order[r['order_id']])
    if r['risk_score'] > prior * 1.15:
        signals.append('Historically higher late-shipment cohort score')
    # Pick an actionable order, while never claiming that a signal proves cause.
    if r['promised_ship_at'] < asof:
        action = 'Escalate missed promise; confirm recovery ETA and update exception owner.'
    elif flagged_skus:
        action = 'Verify physical and allocated SKU stock; coordinate replenishment if required.'
    elif r['order_id'] in defects:
        action = 'Review QC disposition and any rework hold; confirm release readiness.'
    elif (r['promised_ship_at'] - asof).total_seconds() <= 8 * 3600:
        action = 'Check pick/pack completion and dispatch capacity before the deadline.'
    elif r['units_ordered'] >= large_threshold:
        action = 'Confirm picking capacity, staging readiness, and packing handoff.'
    else:
        action = 'Review allocation, current processing stage, and shipment readiness.'
    return '; '.join(signals) if signals else 'No specific documented driver', action


def prepare_risk_intelligence(
    tables: dict[str, pd.DataFrame], warehouse_ids: list[str], end_date: date,
    horizon_hours: int = 48,
) -> dict:
    """Return per-order triage, holdout diagnostics, and aggregate evidence.

    Historical views do NOT consult latest inventory or current statuses. Cases with
    an already missed deadline are displayed as breaches, not predicted probabilities.
    """
    if not warehouse_ids:
        raise ValueError('At least one warehouse is required')
    if horizon_hours not in (24, 48, 72):
        raise ValueError('Horizon must be 24, 48, or 72 hours')
    asof = reporting_cutoff(tables, end_date)
    source = _features(tables, warehouse_ids, asof)
    mature = source.loc[source['promised_ship_at'].le(asof)]
    model = _fit(mature)
    evaluation, calibration = _holdout(source, asof)
    active = source.loc[(source['shipped_at'].isna() | source['shipped_at'].gt(asof))
                        & (source['promised_ship_at'] <= asof + pd.Timedelta(hours=horizon_hours))].copy()
    active['risk_score'] = _score(active, model)
    active['deadline_state'] = np.where(active['promised_ship_at'] < asof, 'Already overdue', 'Upcoming')
    active['risk_band'] = active['risk_score'].apply(_risk_band)
    active.loc[active['deadline_state'].eq('Already overdue'), 'risk_band'] = 'Already late'
    active['hours_to_deadline'] = ((active['promised_ship_at'] - asof).dt.total_seconds() / 3600).round(1)
    # The numeric score is not meaningful as probability after a deadline is breached.
    active['risk_score_pct'] = (100 * active['risk_score']).round(1)
    active.loc[active['deadline_state'].eq('Already overdue'), 'risk_score_pct'] = np.nan
    names = tables['warehouses'].set_index('warehouse_id')['warehouse_name'].to_dict()
    active['warehouse_name'] = active['warehouse_id'].map(names)
    qc = tables['quality_inspections']
    defects = set(qc.loc[qc['defect_qty'].gt(0) & qc['inspected_at'].le(asof), 'order_id'])
    is_latest = asof == tables['inventory']['snapshot_at'].max()
    inventory_flags = {}
    statuses = None
    if is_latest and not active.empty:
        inv = tables['inventory'].loc[
            tables['inventory']['snapshot_at'].eq(tables['inventory']['snapshot_at'].max()),
            ['warehouse_id', 'product_id', 'available_qty', 'reorder_point']
        ]
        lines = tables['order_lines'].loc[tables['order_lines']['order_id'].isin(active['order_id'])]
        lines = lines.merge(active[['order_id', 'warehouse_id']], on='order_id', how='left', validate='many_to_one')
        lines = lines.merge(inv, on=['warehouse_id', 'product_id'], how='left', validate='many_to_one')
        review = lines.loc[lines['available_qty'].notna() &
                           ((lines['available_qty'] < lines['qty_ordered']) |
                            (lines['available_qty'] <= lines['reorder_point']))]
        inventory_flags = review.groupby('order_id')['product_id'].apply(lambda s: list(dict.fromkeys(s.tolist()))).to_dict()
        statuses = active.set_index('order_id')['order_status'].to_dict()
    large_threshold = float(mature['units_ordered'].quantile(.8))
    if len(active):
        triage = active.apply(
            lambda r: _signals_and_action(r, asof, model['prior'], defects,
                                           inventory_flags if is_latest else None,
                                           statuses if is_latest else None,
                                           large_threshold), axis=1,
        )
        active[['review_signals', 'suggested_action']] = pd.DataFrame(triage.tolist(), index=active.index)
    else:
        active['review_signals'] = pd.Series(dtype=str)
        active['suggested_action'] = pd.Series(dtype=str)
    active['action_tier'] = np.select(
        [active['deadline_state'].eq('Already overdue'),
         active['hours_to_deadline'].le(8),
         active['risk_band'].isin(['High', 'Elevated'])],
        ['Escalate', 'Urgent review', 'Priority review'], default='Monitor',
    )
    active['rank'] = active['action_tier'].map({'Escalate': 0, 'Urgent review': 1,
                                               'Priority review': 2, 'Monitor': 3})
    active = active.sort_values(['rank', 'hours_to_deadline', 'risk_score'],
                                ascending=[True, True, False]).reset_index(drop=True)
    summary = {
        'open_in_window': len(active),
        'already_overdue': int(active['deadline_state'].eq('Already overdue').sum()),
        'upcoming': int(active['deadline_state'].eq('Upcoming').sum()),
        'urgent_or_escalate': int(active['action_tier'].isin(['Escalate', 'Urgent review']).sum()),
        'high_scored_upcoming': int((active['deadline_state'].eq('Upcoming') &
                                    active['risk_band'].isin(['High', 'Elevated'])).sum()),
    }
    diagnostic_counts = (
        pd.Series([s.strip() for text in active['review_signals']
                   for s in str(text).split(';') if s.strip()])
        .value_counts().head(10).rename_axis('signal').reset_index(name='orders')
    ) if len(active) else pd.DataFrame(columns=['signal', 'orders'])
    return {'as_of': asof, 'is_latest_snapshot': is_latest, 'summary': summary,
            'orders': active, 'diagnostic_counts': diagnostic_counts,
            'evaluation': evaluation, 'calibration': calibration,
            'model': {'prior_late_pct': round(100 * model['prior'], 2),
                      'training_orders': model['training_count'],
                      'large_order_threshold': large_threshold},
            'horizon_hours': horizon_hours}
