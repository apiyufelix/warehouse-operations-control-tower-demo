"""Quick Step 7 verification without Streamlit and without PyArrow."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analytics import load_tables
from risk_intelligence import prepare_risk_intelligence

if __name__ == '__main__':
    tables = load_tables(ROOT / 'warehouse_ops.db')
    ids = tables['warehouses']['warehouse_id'].tolist()
    day = tables['inventory']['snapshot_at'].max().date()
    x = prepare_risk_intelligence(tables, ids, day, 48)
    print('\nSTEP 7 — SYNTHETIC SHIPMENT-DELAY RISK & TRIAGE')
    print(f'As-of snapshot (UTC): {x["as_of"]}')
    print('Summary:', x['summary'])
    print('Holdout:', x['evaluation'])
    print('\nSample order action queue:')
    print(x['orders'][['order_id', 'warehouse_name', 'deadline_state',
                       'risk_score_pct', 'action_tier', 'suggested_action']].head(10).to_string(index=False))
    print('\nRead-only analysis completed. No workflow records were changed.')
