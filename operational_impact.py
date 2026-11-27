"""Step 17: read-only, deterministic labor transfer impact estimates.

All volumes are *stage-work* units, never distinct orders or shipments.
Uses the exact transfer plan, inputs, and productivity assumptions from
labor_balancing.analyze_labor_balance(). Incoming staff start after a modeled
handoff/setup delay; existing staff work throughout the remaining horizon.
No ML, live labor system, customer SLA, or measured productivity change implied.
"""
from __future__ import annotations

from math import isfinite

import pandas as pd


def _finite_nonnegative(value: float, title: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{title} must be a finite non-negative number') from exc
    if not isfinite(number) or number < 0:
        raise ValueError(f'{title} must be a finite non-negative number')
    return number


def _clearance_hours(units: float, existing_rate: float, incoming_rate: float,
                     setup_hours: float) -> float | None:
    """Piecewise clearance time, with all incoming transfers active after setup."""
    if units <= 0:
        return 0.0
    if existing_rate <= 0 and incoming_rate <= 0:
        return None
    before_handoff = existing_rate * setup_hours
    if units <= before_handoff and existing_rate > 0:
        return units / existing_rate
    eventual_rate = existing_rate + incoming_rate
    if eventual_rate <= 0:
        return None
    return setup_hours + max(0.0, units - before_handoff) / eventual_rate


def _modeled_coverage(units: float, staff: int, incoming: int, rate_per_person: float,
                      horizon: float, setup_hours: float) -> tuple[float, float, float | None]:
    """Returns gross stage capacity, work covered, time to complete that stage."""
    active_before_setup = staff - incoming
    if active_before_setup < 0:
        raise ValueError('Incoming transfers exceed destination staff')
    stage_capacity = rate_per_person * (
        active_before_setup * horizon + incoming * max(0.0, horizon - setup_hours)
    )
    covered = min(units, stage_capacity)
    clear_hours = _clearance_hours(units, active_before_setup * rate_per_person,
                                   incoming * rate_per_person, setup_hours)
    return stage_capacity, covered, clear_hours


def prepare_operational_impact(analysis: dict, *, transfer_setup_minutes: float = 15.0) -> dict:
    """Translate suggested reallocation into interpretable stage-capacity impacts.

    - 'Additional stage work covered' is a scenario capacity estimate within
      remaining shift time, not incremental *actual* processed units.
    - 'At-risk stages' counts department queues with modeled work left at the
      horizon, not predicted late orders.
    - 'Load ratio' = workload / available stage capacity; >100% means overload.
    - Marginal move gains are allocated sequentially to each destination in the
      recommender's existing order. They rank only the recommended moves; they
      are not an optimal allocation search.
    """
    minutes = _finite_nonnegative(transfer_setup_minutes, 'transfer_setup_minutes')
    if minutes > 240:
        raise ValueError('transfer_setup_minutes must not exceed 240')
    if not isinstance(analysis, dict) or not {'departments', 'transfers', 'parameters'}.issubset(analysis):
        raise ValueError('A complete labor balancing analysis is required')
    params = analysis['parameters']
    horizon = _finite_nonnegative(params['hours_remaining'], 'hours_remaining')
    fraction = _finite_nonnegative(params['target_utilization'], 'target_utilization')
    buffer = _finite_nonnegative(params['workload_buffer'], 'workload_buffer')
    if horizon <= 0 or not (0 < fraction <= 1) or buffer > 2:
        raise ValueError('Invalid labor scenario assumptions')
    setup_hours = minutes / 60
    departments = analysis['departments'].copy()
    transfers = analysis['transfers'].copy()
    required = {'warehouse_id', 'shift_name', 'process_name', 'warehouse_name',
                'staff_on_duty', 'staff_after', 'remaining_work_units', 'units_per_labor_hour'}
    if departments.empty or not required.issubset(departments.columns):
        raise ValueError('Missing operational department data')
    keys = ['warehouse_id', 'shift_name', 'process_name']
    if departments.duplicated(keys).any():
        raise ValueError('Duplicate warehouse/shift/process in scenario')
    for field in ('staff_on_duty', 'staff_after', 'remaining_work_units', 'units_per_labor_hour'):
        values = pd.to_numeric(departments[field], errors='coerce')
        if values.isna().any() or not values.map(isfinite).all() or (values < 0).any():
            raise ValueError(f'Invalid department {field}')
    if (departments['units_per_labor_hour'] <= 0).any():
        raise ValueError('Stage rates must be positive')
    if not transfers.empty:
        expected_move_fields = {'warehouse_id', 'shift_name', 'from_department',
                                'to_department', 'employees_to_transfer'}
        if not expected_move_fields.issubset(transfers.columns):
            raise ValueError('Missing transfer plan fields')
        counts = pd.to_numeric(transfers['employees_to_transfer'], errors='coerce')
        if counts.isna().any() or not counts.map(isfinite).all() or (counts <= 0).any() or (counts % 1 != 0).any():
            raise ValueError('Transfer amounts must be positive integers')
    incoming_counts: dict[tuple, int] = {}
    net_changes: dict[tuple, int] = {}
    for move in transfers.itertuples(index=False):
        donor = (move.warehouse_id, move.shift_name, move.from_department)
        recipient = (move.warehouse_id, move.shift_name, move.to_department)
        if donor == recipient:
            raise ValueError('Transfers require distinct departments')
        n = int(move.employees_to_transfer)
        incoming_counts[recipient] = incoming_counts.get(recipient, 0) + n
        net_changes[donor] = net_changes.get(donor, 0) - n
        net_changes[recipient] = net_changes.get(recipient, 0) + n
    known = set(map(tuple, departments[keys].itertuples(index=False, name=None)))
    if not set(net_changes).issubset(known):
        raise ValueError('Transfer references an unknown warehouse, shift, or process')
    for row in departments.itertuples(index=False):
        k = (row.warehouse_id, row.shift_name, row.process_name)
        if row.staff_after - row.staff_on_duty != net_changes.get(k, 0):
            raise ValueError('Transfer plan does not match after-staffing')

    output: list[dict] = []
    for row in departments.itertuples(index=False):
        key = (row.warehouse_id, row.shift_name, row.process_name)
        incoming = incoming_counts.get(key, 0)
        units = float(row.remaining_work_units)
        per_person = float(row.units_per_labor_hour) * fraction / (1 + buffer)
        before_capacity, before_covered, before_clear = _modeled_coverage(
            units, int(row.staff_on_duty), 0, per_person, horizon, 0
        )
        after_capacity, after_covered, after_clear = _modeled_coverage(
            units, int(row.staff_after), incoming, per_person, horizon, setup_hours
        )
        output.append({
            'warehouse_id': row.warehouse_id, 'warehouse_name': row.warehouse_name,
            'shift_name': row.shift_name, 'process_name': row.process_name,
            'staff_before': int(row.staff_on_duty), 'staff_after': int(row.staff_after),
            'incoming_transfers': incoming, 'remaining_stage_units': units,
            'capacity_before': before_capacity, 'capacity_after': after_capacity,
            'stage_work_covered_before': before_covered, 'stage_work_covered_after': after_covered,
            'stage_work_uncovered_before': max(0., units - before_covered),
            'stage_work_uncovered_after': max(0., units - after_covered),
            'additional_stage_work_covered': after_covered - before_covered,
            'capacity_load_pct_before': 100 * units / before_capacity if before_capacity else None,
            'capacity_load_pct_after': 100 * units / after_capacity if after_capacity else None,
            'estimated_clear_hours_before': before_clear,
            'estimated_clear_hours_after': after_clear,
            'stage_at_risk_before': before_covered + 1e-9 < units,
            'stage_at_risk_after': after_covered + 1e-9 < units,
        })
    stages = pd.DataFrame(output).sort_values(['warehouse_name', 'shift_name', 'process_name']).reset_index(drop=True)

    move_rows = []
    staff_so_far: dict[tuple, int] = {}
    incoming_so_far: dict[tuple, int] = {}
    index = {tuple(r[k] for k in keys): r for _, r in stages.iterrows()}
    for move in transfers.itertuples(index=False):
        key = (move.warehouse_id, move.shift_name, move.to_department)
        receiver = index[key]
        n = int(move.employees_to_transfer)
        already = incoming_so_far.get(key, 0)
        existing_after_baseline = int(receiver['staff_before'])
        now_staff = existing_after_baseline + already
        unit_rate = float(departments.loc[
            (departments['warehouse_id'] == key[0]) & (departments['shift_name'] == key[1])
            & (departments['process_name'] == key[2]), 'units_per_labor_hour'
        ].iloc[0]) * fraction / (1 + buffer)
        before = _modeled_coverage(float(receiver['remaining_stage_units']), now_staff, already,
                                   unit_rate, horizon, setup_hours)[1]
        after = _modeled_coverage(float(receiver['remaining_stage_units']), now_staff + n, already + n,
                                  unit_rate, horizon, setup_hours)[1]
        benefit = max(0., after - before)
        incoming_so_far[key] = already + n
        move_rows.append({
            'warehouse_name': move.warehouse_name, 'warehouse_id': move.warehouse_id,
            'shift_name': move.shift_name, 'from_department': move.from_department,
            'to_department': move.to_department, 'employees_to_transfer': n,
            'marginal_stage_work_gain': benefit, 'gain_per_transferred_employee': benefit / n,
        })
    ranked = pd.DataFrame(move_rows, columns=[
        'warehouse_name', 'warehouse_id', 'shift_name', 'from_department', 'to_department',
        'employees_to_transfer', 'marginal_stage_work_gain', 'gain_per_transferred_employee',
    ])
    if not ranked.empty:
        ranked = ranked.sort_values(['gain_per_transferred_employee', 'warehouse_name',
                                     'shift_name', 'to_department'], ascending=[False, True, True, True]).reset_index(drop=True)
        ranked.insert(0, 'priority_rank', range(1, len(ranked) + 1))

    before = float(stages['stage_work_covered_before'].sum())
    after = float(stages['stage_work_covered_after'].sum())
    needed = float(stages['remaining_stage_units'].sum())
    summary = {
        'stage_work_units': needed,
        'stage_work_covered_before': before, 'stage_work_covered_after': after,
        'additional_stage_work_covered': after - before,
        'covered_pct_before': 100 * before / needed if needed else None,
        'covered_pct_after': 100 * after / needed if needed else None,
        'uncovered_stage_work_before': float(stages['stage_work_uncovered_before'].sum()),
        'uncovered_stage_work_after': float(stages['stage_work_uncovered_after'].sum()),
        'at_risk_stages_before': int(stages['stage_at_risk_before'].sum()),
        'at_risk_stages_after': int(stages['stage_at_risk_after'].sum()),
        'total_transferred_employees': int(transfers['employees_to_transfer'].sum()) if not transfers.empty else 0,
        'transfer_setup_minutes': minutes,
    }
    if summary['additional_stage_work_covered'] < -1e-6:
        # Should not happen for the original safe-donor algorithm, but do not
        # conceal regressions if the transfer engine is changed in the future.
        raise ValueError('Transfers reduce modeled covered stage work')
    if not ranked.empty and ranked['marginal_stage_work_gain'].sum() > summary['additional_stage_work_covered'] + 1e-6:
        raise ValueError('Transfer benefit accounting is inconsistent')
    return {'stages': stages, 'transfers_ranked': ranked, 'metrics': summary}
