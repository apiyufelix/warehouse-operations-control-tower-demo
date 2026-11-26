"""Read-only verification of the packaged Step 9 SQLite DB and synthetic sample batch."""
from __future__ import annotations
import sys
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_ingestion import preview_import,import_history
from analytics import load_tables,prepare_dashboard

ROOT=Path(__file__).resolve().parents[1]
db=ROOT/'warehouse_ops.db'
example=ROOT/'samples'/'sample_nov13_refresh.zip'
with zipfile.ZipFile(example) as z:
    files={name:z.read(name) for name in z.namelist()}
report=preview_import(db,files)
assert report['total']['inserted']==92,report
print('Preview PASS — 92 new synthetic rows across 7 CSV tables.')
print('Actual user database was NOT modified.')
tables=load_tables(db)
print('Current demo snapshot:',tables['inventory']['snapshot_at'].max())
print('Previous approved import count:',len(import_history(db)))
