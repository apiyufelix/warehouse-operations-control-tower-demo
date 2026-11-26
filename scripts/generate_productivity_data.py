"""Deterministic, additive *synthetic* process and labor data for Step 5.

Does not rewrite original orders, shipments, QC, or Step 4 exception history.
Model: UTC 00–08 / 08–16 / 16–24 demo shifts, not an actual employer schedule.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
RNG = random.Random(20261113)
PROCESSES = ('Picking', 'Packing', 'Dispatch')
SHIFTS = ('Shift A (00–08 UTC)', 'Shift B (08–16 UTC)', 'Shift C (16–24 UTC)')


def read_csv(filename):
    with (DATA / filename).open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def utc(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def fmt(t):
    return t.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_csv(filename, rows, fields):
    with (DATA / filename).open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f'{filename}: {len(rows):,} rows')


def shift_name(timestamp):
    return SHIFTS[timestamp.hour // 8]


def make_activities():
    orders = {row['order_id']: row for row in read_csv('orders.csv')}
    activities = []
    for ship in read_csv('shipments.csv'):
        order = orders[ship['order_id']]
        created, shipped = utc(order['created_at']), utc(ship['shipped_at'])
        cycle_min = (shipped - created).total_seconds() / 60
        assert cycle_min > 0, ship['order_id']
        qty = int(ship['units_shipped'])
        # Event durations are modeled from unit count with substantial natural variance;
        # durations are compressed when necessary to fit the actual shipping cycle.
        work_minutes = [
            max(1.0, (4 + qty * 1.05) * RNG.uniform(.72, 1.33)),
            max(1.0, (3 + qty * .82) * RNG.uniform(.75, 1.4)),
            max(1.0, (2 + qty * .51) * RNG.uniform(.7, 1.45)),
        ]
        if sum(work_minutes) > cycle_min * .65:
            factor = cycle_min * .65 / sum(work_minutes)
            work_minutes = [v * factor for v in work_minutes]
        idle = cycle_min - sum(work_minutes)
        # Three queue waits: order-receipt→pick, pick→pack, pack→dispatch.
        # The distribution varies between orders and produces candidate bottlenecks.
        base = [2.2, 1.7, 1.1] if order['warehouse_id'] == 'WH_NC_01' else [1.8, 2.1, 1.2]
        if shipped.month in (11, 12):
            base[0] *= 1.35
        weights = [RNG.gammavariate(b, 1) for b in base]
        waits = [idle * weight / sum(weights) for weight in weights]
        cursor = created
        for i, proc in enumerate(PROCESSES):
            cursor += timedelta(minutes=waits[i])
            started = cursor
            cursor += timedelta(minutes=work_minutes[i])
            completed = cursor
            if i == 2:
                completed = shipped  # Correct float rounding residual on the last step.
            activities.append({
                'activity_id': f'ACT{len(activities)+1:08d}',
                'order_id': ship['order_id'],
                'warehouse_id': ship['warehouse_id'],
                'process_name': proc,
                'started_at': fmt(started),
                'completed_at': fmt(completed),
                'shift_name': shift_name(completed),
                'units_processed': qty,
                'queue_minutes': f'{waits[i]:.3f}',
                'processing_minutes': f'{(completed-started).total_seconds()/60:.3f}',
            })
    fields = ('activity_id', 'order_id', 'warehouse_id', 'process_name', 'started_at',
              'completed_at', 'shift_name', 'units_processed', 'queue_minutes', 'processing_minutes')
    write_csv('process_activities.csv', activities, fields)
    return activities


def make_labor():
    orders = read_csv('orders.csv')
    inventory = read_csv('inventory.csv')
    snapshot = max(utc(row['snapshot_at']) for row in inventory)
    first = min(utc(row['created_at']) for row in orders).date()
    day = first
    rows = []
    # Planned hours per 8-hour shift are independent of realized shipment volumes.
    # Small demo warehouses: fractional hours reflect staff shared across processes.
    baseline = {
        'WH_NC_01': {'Picking': 8.0, 'Packing': 6.5, 'Dispatch': 5.1},
        'WH_NC_02': {'Picking': 5.0, 'Packing': 4.1, 'Dispatch': 3.1},
    }
    while day <= snapshot.date():
        peak = 1.16 if day.month in (11, 12) else 1.0
        weekday = 1.06 if day.weekday() < 5 else .86
        for wh, process_hours in baseline.items():
            for index, shift in enumerate(SHIFTS):
                shift_start = datetime.combine(day, time(index * 8), tzinfo=timezone.utc)
                shift_end = min(shift_start + timedelta(hours=8), snapshot)
                fraction = (shift_end - shift_start).total_seconds() / (8 * 3600)
                if fraction <= 0:
                    continue
                for process in PROCESSES:
                    planned = process_hours[process] * peak * weekday * fraction
                    actual = planned * RNG.uniform(.78, 1.21)
                    rows.append({
                        'labor_id': f'LAB{len(rows)+1:08d}',
                        'warehouse_id': wh,
                        'process_name': process,
                        'shift_name': shift,
                        'shift_start_at': fmt(shift_start),
                        'shift_end_at': fmt(shift_end),
                        'planned_labor_hours': f'{planned:.3f}',
                        'actual_labor_hours': f'{actual:.3f}',
                    })
        day += timedelta(days=1)
    fields = ('labor_id', 'warehouse_id', 'process_name', 'shift_name',
              'shift_start_at', 'shift_end_at', 'planned_labor_hours', 'actual_labor_hours')
    write_csv('labor_shifts.csv', rows, fields)


if __name__ == '__main__':
    make_activities()
    make_labor()
    print('Generated synthetic data with UTC shift windows and no change to Step 4 records.')
