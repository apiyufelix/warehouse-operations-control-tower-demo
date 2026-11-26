"""Read-only cross-trained labor-balancing scenario engine.

This is a what-if scenario, not a live staffing feed. The input headcounts and
remaining stage workloads are synthetic/manual; labor_shifts records only hours.
All transfers remain inside the same warehouse and shift. No employee is moved
below their minimum safe coverage or computed workload requirement.
"""
from __future__ import annotations

from math import ceil, isfinite
from typing import Mapping

import pandas as pd

from productivity import PROCESSES, REFERENCE_UNITS_PER_HOUR, SHIFTS

SCENARIO_FIELDS = (
    'warehouse_id', 'warehouse_name', 'shift_name', 'process_name',
    'staff_on_duty', 'remaining_work_units', 'minimum_staff',
)


def _whole_number(value, field: str) -> int:
    if pd.isna(value) or not isfinite(float(value)) or float(value) != int(float(value)) or float(value) < 0:
        raise ValueError(f'{field} must be a non-negative whole number')
    return int(value)


def analyze_labor_balance(
    scenario: pd.DataFrame,
    *,
    hours_remaining: float = 6.0,
    target_utilization: float = 0.85,
    workload_buffer: float = 0.10,
    rates: Mapping[str, float] | None = None,
) -> dict:
    """Recommend whole-person transfers from surplus to deficit groups.

    Required staff = max(min_staff, ceil((units*(1+buffer)/rate)
                   / (hours_remaining*target_utilization))).
    Each independently modeled stage uses its own rate and queue volume.
    The covered-work figures sum stage work, not distinct fulfilled units.
    """
    missing = set(SCENARIO_FIELDS) - set(scenario.columns)
    if missing:
        raise ValueError(f'Missing staffing scenario columns: {sorted(missing)}')
    if scenario.empty:
        raise ValueError('Staffing scenario must include at least one department')
    for title, value, low, high in (
        ('hours_remaining', hours_remaining, 0, 24),
        ('target_utilization', target_utilization, 0, 1),
        ('workload_buffer', workload_buffer, 0, 2),
    ):
        if not isfinite(float(value)):
            raise ValueError(f'Invalid {title}: {value}')
        if title == 'workload_buffer':
            valid = low <= float(value) <= high
        else:
            valid = low < float(value) <= high
        if not valid:
            raise ValueError(f'Invalid {title}: {value}')
    rates = dict(REFERENCE_UNITS_PER_HOUR if rates is None else rates)
    for name in PROCESSES:
        if name not in rates or not isfinite(float(rates[name])) or float(rates[name]) <= 0:
            raise ValueError(f'{name} reference rate must be positive')
    if scenario[['warehouse_id', 'warehouse_name', 'shift_name', 'process_name']].isna().any().any():
        raise ValueError('Warehouse, shift, and process identifiers are required')
    if not scenario['process_name'].isin(PROCESSES).all():
        raise ValueError('Scenario has an unknown department')
    if not scenario['shift_name'].isin(SHIFTS).all():
        raise ValueError('Scenario has an unknown shift')
    if scenario.duplicated(['warehouse_id', 'shift_name', 'process_name']).any():
        raise ValueError('Duplicate department in warehouse/shift')
    if scenario.groupby('warehouse_id')['warehouse_name'].nunique().max() > 1:
        raise ValueError('Conflicting warehouse name for a warehouse ID')

    result = scenario[list(SCENARIO_FIELDS)].copy()
    for column in ('staff_on_duty', 'remaining_work_units', 'minimum_staff'):
        result[column] = result[column].map(lambda x: _whole_number(x, column))
    result['units_per_labor_hour'] = result['process_name'].map(rates).astype(float)
    result['staff_needed'] = result.apply(
        lambda r: max(r['minimum_staff'], ceil(
            r['remaining_work_units'] * (1 + workload_buffer) /
            (r['units_per_labor_hour'] * hours_remaining * target_utilization)
        )), axis=1,
    )
    result['surplus_before'] = (result['staff_on_duty'] - result['staff_needed']).clip(lower=0)
    result['shortage_before'] = (result['staff_needed'] - result['staff_on_duty']).clip(lower=0)
    result['staff_after'] = result['staff_on_duty'].copy()

    moves: list[dict] = []
    # Transfers are always in the same warehouse AND shift; prioritize largest
    # shortfall first, then largest available surplus. Deterministic ties.
    for (warehouse_id, shift_name), group in result.groupby(['warehouse_id', 'shift_name'], sort=True):
        donors = group[group['surplus_before'] > 0].sort_values(
            ['surplus_before', 'process_name'], ascending=[False, True]
        )
        receivers = group[group['shortage_before'] > 0].sort_values(
            ['shortage_before', 'process_name'], ascending=[False, True]
        )
        for receiver_i, receiver in receivers.iterrows():
            remaining = int(receiver['shortage_before'])
            for donor_i, donor in donors.iterrows():
                donor_room = int(result.at[donor_i, 'staff_after'] - result.at[donor_i, 'staff_needed'])
                to_move = min(remaining, max(0, donor_room))
                if to_move == 0:
                    continue
                result.at[donor_i, 'staff_after'] -= to_move
                result.at[receiver_i, 'staff_after'] += to_move
                moves.append({
                    'warehouse_id': warehouse_id,
                    'warehouse_name': str(receiver['warehouse_name']),
                    'shift_name': shift_name,
                    'from_department': str(donor['process_name']),
                    'to_department': str(receiver['process_name']),
                    'employees_to_transfer': to_move,
                    'reason': f"{receiver['process_name']} is short {int(receiver['shortage_before'])} employee(s); "
                              f"{donor['process_name']} has excess coverage",
                })
                remaining -= to_move
                if remaining == 0:
                    break

    result['surplus_after'] = (result['staff_after'] - result['staff_needed']).clip(lower=0)
    result['shortage_after'] = (result['staff_needed'] - result['staff_after']).clip(lower=0)
    # A modeled stage can complete at most its demand volume. Count stage units,
    # not unique orders / distinct shipped units across picking-packing-dispatch.
    for suffix, column in [('before', 'staff_on_duty'), ('after', 'staff_after')]:
        available_units = (
            result[column] * result['units_per_labor_hour'] *
            hours_remaining * target_utilization / (1 + workload_buffer)
        )
        result[f'work_units_covered_{suffix}'] = result['remaining_work_units'].clip(upper=available_units)
        result[f'work_units_at_risk_{suffix}'] = (
            result['remaining_work_units'] - result[f'work_units_covered_{suffix}']
        ).clip(lower=0)

    transfers = pd.DataFrame(moves, columns=[
        'warehouse_id', 'warehouse_name', 'shift_name', 'from_department',
        'to_department', 'employees_to_transfer', 'reason',
    ])
    result = result.sort_values(['warehouse_name', 'shift_name', 'process_name']).reset_index(drop=True)
    summary = (
        result.groupby(['warehouse_id', 'warehouse_name', 'shift_name'], as_index=False)
        .agg(staff_on_duty=('staff_on_duty', 'sum'),
             required_staff=('staff_needed', 'sum'),
             surplus_before=('surplus_before', 'sum'),
             shortage_before=('shortage_before', 'sum'),
             surplus_after=('surplus_after', 'sum'),
             shortage_after=('shortage_after', 'sum'),
             work_units_at_risk_before=('work_units_at_risk_before', 'sum'),
             work_units_at_risk_after=('work_units_at_risk_after', 'sum'))
    )
    moved = (
        transfers.groupby(['warehouse_id', 'shift_name'])['employees_to_transfer'].sum()
        if not transfers.empty else pd.Series(dtype=int)
    )
    summary['transfers_recommended'] = [
        int(moved.get((r.warehouse_id, r.shift_name), 0)) for r in summary.itertuples(index=False)
    ]
    summary['stage_work_risk_reduction'] = (
        summary['work_units_at_risk_before'] - summary['work_units_at_risk_after']
    ).clip(lower=0)
    summary['alert'] = summary.apply(
        lambda r: (
            'TRANSFER RECOMMENDED' if r['transfers_recommended'] else
            'STAFFING SHORTAGE — NO DONOR' if r['shortage_after'] else
            'UNALLOCATED STAFF — NO SHORTAGE' if r['surplus_after'] else
            'BALANCED'
        ), axis=1,
    )
    return {
        'departments': result,
        'transfers': transfers,
        'summary': summary,
        'parameters': dict(
            hours_remaining=float(hours_remaining),
            target_utilization=float(target_utilization),
            workload_buffer=float(workload_buffer),
            reference_rates=rates,
        ),
    }


def prepare_labor_comparison(analysis: dict) -> dict:
    """Compare modeled staffing and stage-work coverage before vs after transfers.

    This is a descriptive what-if comparison using the *same* coverage
    assumptions as analyze_labor_balance. It does not predict actual worker
    productivity, cost savings, throughput, on-time shipping, or unique orders.
    
    The units across stages and shifts are summed *stage-work* units, not unique
    physical units, because a unit might appear at picking, packing, dispatch.
    """
    rows = analysis['departments'].copy()
    needed = {
        'process_name', 'staff_on_duty', 'staff_after', 'staff_needed',
        'remaining_work_units', 'shortage_before', 'shortage_after',
        'work_units_covered_before', 'work_units_covered_after',
        'work_units_at_risk_before', 'work_units_at_risk_after',
    }
    if not needed.issubset(rows.columns):
        raise ValueError(f'Missing comparison columns: {sorted(needed - set(rows.columns))}')
    if rows.empty:
        raise ValueError('No modeled departments to compare')

    rows['staff_change'] = rows['staff_after'] - rows['staff_on_duty']
    rows['stage_work_coverage_change'] = (
        rows['work_units_covered_after'] - rows['work_units_covered_before']
    )
    for period in ('before', 'after'):
        work = rows['remaining_work_units'].astype(float)
        percent = rows[f'work_units_covered_{period}'] / work.where(work > 0) * 100
        rows[f'coverage_pct_{period}'] = percent.clip(lower=0, upper=100)

    demand = float(rows['remaining_work_units'].sum())
    covered_before = float(rows['work_units_covered_before'].sum())
    covered_after = float(rows['work_units_covered_after'].sum())
    gap_before = float(rows['work_units_at_risk_before'].sum())
    gap_after = float(rows['work_units_at_risk_after'].sum())
    headcount_before = int(rows['staff_on_duty'].sum())
    headcount_after = int(rows['staff_after'].sum())
    shortage_before = int(rows['shortage_before'].sum())
    shortage_after = int(rows['shortage_after'].sum())
    if headcount_before != headcount_after:
        raise ValueError('A transfer-only scenario must preserve total headcount')
    if covered_after < covered_before - 1e-6 or gap_after > gap_before + 1e-6:
        raise ValueError('Transfer scenario must not reduce modeled stage-work coverage')

    metrics = {
        'total_stage_work_units': demand,
        'stage_work_covered_before': covered_before,
        'stage_work_covered_after': covered_after,
        'stage_work_at_risk_before': gap_before,
        'stage_work_at_risk_after': gap_after,
        'stage_work_gap_reduced': max(0., gap_before - gap_after),
        'coverage_pct_before': (100 * covered_before / demand) if demand > 0 else None,
        'coverage_pct_after': (100 * covered_after / demand) if demand > 0 else None,
        'headcount_before': headcount_before,
        'headcount_after': headcount_after,
        'shortage_before': shortage_before,
        'shortage_after': shortage_after,
    }
    return {'departments': rows, 'metrics': metrics}
