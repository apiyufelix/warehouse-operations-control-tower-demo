"""Small CLI example for the first Power BI / Streamlit dashboard KPIs.
Run from project root: python scripts/calculate_kpis.py
Metric definitions use a fixed historical as-of timestamp for reproducibility.
"""
import sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
AS_OF='2026-11-12T22:00:00Z'
con=sqlite3.connect(ROOT/'warehouse_ops.db')
con.row_factory=sqlite3.Row
q="""
WITH base AS (
 SELECT o.order_id,o.order_status,o.created_at,o.promised_ship_at,s.shipped_at,
        CASE WHEN o.order_status != 'Cancelled' AND o.promised_ship_at <= :asof THEN 1 ELSE 0 END AS is_due,
        CASE WHEN s.shipped_at IS NOT NULL AND s.shipped_at <= o.promised_ship_at THEN 1 ELSE 0 END AS is_on_time
 FROM orders o LEFT JOIN shipments s ON s.order_id=o.order_id
)
SELECT
 COUNT(*) AS orders_received,
 SUM(CASE WHEN order_status='Shipped' THEN 1 ELSE 0 END) AS orders_shipped,
 SUM(CASE WHEN order_status NOT IN ('Shipped','Cancelled') THEN 1 ELSE 0 END) AS open_orders,
 SUM(CASE WHEN order_status NOT IN ('Shipped','Cancelled') AND promised_ship_at < :asof THEN 1 ELSE 0 END) AS overdue_open_orders,
 SUM(is_due) AS orders_due,
 SUM(CASE WHEN is_due=1 THEN is_on_time ELSE 0 END) AS on_time_due_orders,
 ROUND(100.0*SUM(CASE WHEN is_due=1 THEN is_on_time ELSE 0 END)/NULLIF(SUM(is_due),0),2) AS on_time_shipping_pct
FROM base;
"""
row=con.execute(q,{'asof':AS_OF}).fetchone()
for k in row.keys(): print(f'{k}: {row[k]}')
qc=con.execute('SELECT SUM(qty_inspected), SUM(defect_qty) FROM quality_inspections').fetchone()
print('qc_defect_rate_pct:',round(qc[1]/qc[0]*100,2))
con.close()
