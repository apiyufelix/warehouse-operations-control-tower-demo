"""Produce a small, fully synthetic Nov 13 CSV refresh batch for demonstrations."""
from __future__ import annotations
import csv
import sqlite3
import zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_ingestion import COLUMNS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'samples'
OUT.mkdir(exist_ok=True)
with sqlite3.connect(ROOT / 'warehouse_ops.db') as conn:
    conn.row_factory = sqlite3.Row
    inv = [dict(r) for r in conn.execute('SELECT * FROM inventory ORDER BY warehouse_id,product_id')]
    wh = sorted(set(row['warehouse_id'] for row in inv))
    assert len(wh) == 2
    products = {w: next(row['product_id'] for row in inv if row['warehouse_id']==w) for w in wh}
    prices = {r['product_id']:r['unit_price'] for r in conn.execute('SELECT product_id,unit_price FROM products')}

# Order 1 is shipped, Order 2 intentionally remains packed past its due time.
orders = [
 {'order_id':'DEMO_REFRESH_0001','warehouse_id':wh[0],'customer_id':'DEMO_CUST_001',
  'created_at':'2026-11-13T09:00:00Z','promised_ship_at':'2026-11-13T18:00:00Z',
  'priority':'Standard','order_status':'Shipped'},
 {'order_id':'DEMO_REFRESH_0002','warehouse_id':wh[1],'customer_id':'DEMO_CUST_002',
  'created_at':'2026-11-13T14:00:00Z','promised_ship_at':'2026-11-13T19:00:00Z',
  'priority':'SameDay','order_status':'Packed'},
]
lines = [
 {'order_line_id':'DEMO_LINE_0001','order_id':'DEMO_REFRESH_0001','product_id':products[wh[0]],
  'qty_ordered':3,'unit_price':prices[products[wh[0]]]},
 {'order_line_id':'DEMO_LINE_0002','order_id':'DEMO_REFRESH_0002','product_id':products[wh[1]],
  'qty_ordered':4,'unit_price':prices[products[wh[1]]]},
]
shipments = [{'shipment_id':'DEMO_SHIP_0001','order_id':'DEMO_REFRESH_0001','warehouse_id':wh[0],
              'carrier':'Demo Carrier','shipped_at':'2026-11-13T11:50:00Z','units_shipped':3}]
qc = [{'inspection_id':'DEMO_QC_0001','order_id':'DEMO_REFRESH_0001',
        'order_line_id':'DEMO_LINE_0001','warehouse_id':wh[0],
        'inspected_at':'2026-11-13T11:30:00Z','qty_inspected':3,'defect_qty':0,
        'inspection_result':'Pass','issue_type':'None','resolution_status':'Not Required'}]
for row in inv:
    row['snapshot_at'] = '2026-11-13T22:00:00Z'
    if row['warehouse_id']==wh[0] and row['product_id']==products[wh[0]]:
        row['on_hand_qty'] -= 3
        row['available_qty'] -= 3
activity = []
for i,(process,start,end,queue,processing) in enumerate([
    ('Picking','2026-11-13T10:00:00Z','2026-11-13T10:20:00Z',5,20),
    ('Packing','2026-11-13T10:30:00Z','2026-11-13T10:50:00Z',10,20),
    ('Dispatch','2026-11-13T11:35:00Z','2026-11-13T11:45:00Z',45,10),
],1):
    activity.append({'activity_id':f'DEMO_ACT_000{i}', 'order_id':'DEMO_REFRESH_0001',
                     'warehouse_id':wh[0],'process_name':process,
                     'started_at':start, 'completed_at':end,
                     'shift_name':'Shift B (08–16 UTC)', 'units_processed':3,
                     'queue_minutes':queue, 'processing_minutes':processing})
labor = []
for i,process in enumerate(('Picking','Packing','Dispatch'),1):
    labor.append({'labor_id':f'DEMO_LABOR_000{i}','warehouse_id':wh[0],
                  'process_name':process,'shift_name':'Shift B (08–16 UTC)',
                  'shift_start_at':'2026-11-13T08:00:00Z','shift_end_at':'2026-11-13T16:00:00Z',
                  'planned_labor_hours':2.0,'actual_labor_hours':2.25})
all_tables = {'orders':orders,'order_lines':lines,'shipments':shipments,'quality_inspections':qc,
              'inventory':inv,'process_activities':activity,'labor_shifts':labor}
zip_path = OUT / 'sample_nov13_refresh.zip'
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as bundle:
    for name,rows in all_tables.items():
        buffer = __import__('io').StringIO()
        writer = csv.DictWriter(buffer,fieldnames=COLUMNS[name],lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)
        bundle.writestr(f'{name}.csv',buffer.getvalue())
print(zip_path,zip_path.stat().st_size)
