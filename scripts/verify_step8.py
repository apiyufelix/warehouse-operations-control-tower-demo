"""Read-only CLI spot check for the Step 8 executive dashboard (no Streamlit required)."""
from datetime import timedelta
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analytics import load_tables
from exception_workflow import get_cases
from executive import prepare_executive, export_brief_html

if __name__ == '__main__':
    tables = load_tables(ROOT / 'warehouse_ops.db')
    ids = tables['warehouses']['warehouse_id'].tolist()
    end = tables['inventory']['snapshot_at'].max().date()
    start = end - timedelta(days=29)
    result = prepare_executive(tables, ids, start, end, workflow_cases=get_cases(ROOT / 'warehouse_ops.db'))
    print('STEP 8 — EXECUTIVE BRIEF (SYNTHETIC DATA)')
    print('Period:', start, 'through', end, 'UTC; as-of:', result['as_of'])
    print('Prior period:',result['prior_start'],'through',result['prior_end'])
    print('KPI comparison:')
    print(result['comparison'].to_string(index=False))
    print('\nManagement review priorities:')
    print(result['priorities'].to_string(index=False))
    print('\nPrint-ready HTML bytes:',len(export_brief_html(result,['All selected warehouses'])))
    print('\nVerified read-only. No files or workflow cases were modified.')
