"""Command-line Step 5 verification; works without launching Streamlit."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics import load_tables  # noqa: E402
from productivity import prepare_productivity  # noqa: E402

if __name__ == '__main__':
    tables = load_tables(ROOT / 'warehouse_ops.db')
    ids = tables['warehouses']['warehouse_id'].tolist()
    report = prepare_productivity(tables, ids, date(2026, 10, 14), date(2026, 11, 12))
    print('Step 5 data verification:')
    for process in ('Picking', 'Packing', 'Dispatch'):
        row = report['by_process'].set_index('process_name').loc[process]
        print(f'  {process}: {int(row.units_processed):,} stage units, '
              f'{row.actual_labor_hours:,.1f} labor hours, '
              f'{row.units_per_labor_hour:.2f} units/labor hour')
    print(f'  Latest available UTC timestamp: {report["as_of"]}')
    print('  Source data: synthetic, not an actual WMS feed')
