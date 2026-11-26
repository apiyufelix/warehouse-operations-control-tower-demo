"""Create a SQLite database and import the synthetic data.
Run from project root: python scripts/load_database.py
"""
import csv
import sqlite3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "warehouse_ops.db"
if DB.exists():
    DB.unlink()
conn = sqlite3.connect(DB)
try:
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    for name in ("warehouses", "products", "orders", "order_lines", "shipments", "quality_inspections", "inventory", "process_activities", "labor_shifts"):
        with (ROOT / "data" / f"{name}.csv").open(newline="", encoding="utf-8") as f:
            records = list(csv.DictReader(f))
        if not records:
            continue
        cols = list(records[0])
        sql = f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        conn.executemany(sql, (tuple(rec[c] for c in cols) for rec in records))
        print(f"Imported {name}: {len(records):,}")
    bad_fk = list(conn.execute("PRAGMA foreign_key_check"))
    if bad_fk:
        raise RuntimeError(f"Foreign key validation failed: {bad_fk[:3]}")
    # Cross-table integrity checks that foreign keys alone don't cover.
    assert conn.execute("SELECT COUNT(*) FROM shipments s JOIN orders o ON s.order_id=o.order_id WHERE o.order_status!='Shipped' OR o.warehouse_id != s.warehouse_id").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM orders o WHERE o.order_status='Shipped' AND NOT EXISTS (SELECT 1 FROM shipments s WHERE s.order_id=o.order_id)").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM shipments s JOIN (SELECT order_id, SUM(qty_ordered) total FROM order_lines GROUP BY order_id) x ON s.order_id=x.order_id WHERE s.units_shipped!=x.total").fetchone()[0] == 0
    conn.commit()
    print('Database validated:', DB)
finally:
    conn.close()
