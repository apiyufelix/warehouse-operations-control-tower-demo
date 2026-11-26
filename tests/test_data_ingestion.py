"""Step 9 regression tests: previews cannot change the live DB; imports are atomic."""
import csv
import io
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from analytics import load_tables, prepare_dashboard
from data_ingestion import (
    BatchValidationError, apply_import, import_history, parse_batch, preview_import,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'samples' / 'sample_nov13_refresh.zip'


@pytest.fixture
def isolated_db(tmp_path):
    dst = tmp_path / 'warehouse_ops.db'
    shutil.copy2(ROOT / 'warehouse_ops.db', dst)
    return dst


def sample_batch():
    with zipfile.ZipFile(DEMO) as f:
        return {name:f.read(name) for name in f.namelist()}


def edit_file(batch, name, mutate):
    output = dict(batch)
    contents = batch[name].decode('utf-8')
    reader = csv.DictReader(io.StringIO(contents))
    records = list(reader)
    mutate(records)
    buf = io.StringIO()
    writer = csv.DictWriter(buf,fieldnames=reader.fieldnames,lineterminator='\n')
    writer.writeheader();writer.writerows(records)
    output[name] = buf.getvalue().encode()
    return output


def count(db, name):
    with sqlite3.connect(db) as conn:
        return conn.execute(f'SELECT count(*) FROM {name}').fetchone()[0]


def test_preview_never_changes_database(isolated_db):
    before = isolated_db.read_bytes()
    result = preview_import(isolated_db, sample_batch())
    assert result['total']['inserted'] == 92
    assert result['total']['updated'] == 0
    assert isolated_db.read_bytes() == before
    assert import_history(isolated_db) == []


def test_approved_batch_updates_data_but_preserves_cases(isolated_db, tmp_path):
    before_cases = count(isolated_db, 'exception_cases')
    before_events = count(isolated_db, 'exception_events')
    before_inventory = count(isolated_db, 'inventory')
    original_orders = count(isolated_db, 'orders')
    out = apply_import(isolated_db,sample_batch(),backup_folder=tmp_path/'backups')
    assert out['total']['inserted'] == 92
    assert count(isolated_db,'orders') == original_orders + 2
    assert count(isolated_db,'inventory') == before_inventory + 80
    assert count(isolated_db, 'exception_cases') == before_cases
    assert count(isolated_db, 'exception_events') == before_events
    assert Path(out['backup_path']).exists()
    assert count(out['backup_path'],'orders') == original_orders
    assert len(import_history(isolated_db)) == 1
    tables = load_tables(isolated_db)
    ids = tables['warehouses']['warehouse_id'].tolist()
    snapshot = tables['inventory']['snapshot_at'].max()
    assert str(snapshot).startswith('2026-11-13')
    from datetime import date
    metrics = prepare_dashboard(tables,ids,date(2026,11,13),date(2026,11,13))['metrics']
    assert metrics['orders_received'] == 2
    assert metrics['orders_shipped'] == 1
    # Latest stock snapshot must not double count SKUs at/below reorder.
    current_inv = tables['inventory'].loc[tables['inventory']['snapshot_at'].eq(snapshot)]
    expected = int((current_inv['available_qty'] <= current_inv['reorder_point']).sum())
    assert metrics['skus_below_reorder'] == expected


def test_safe_replay_is_idempotent(isolated_db, tmp_path):
    files = sample_batch()
    apply_import(isolated_db,files,backup_folder=tmp_path/'backup1')
    result = preview_import(isolated_db,files)
    assert result['total']['inserted'] == 0
    assert result['total']['unchanged'] == 92
    again = apply_import(isolated_db,files,backup_folder=tmp_path/'backup2')
    assert again['total']['unchanged'] == 92
    assert count(isolated_db,'inventory') == 160
    assert len(import_history(isolated_db)) == 2


def test_invalid_quantity_rolls_back_without_side_effects(isolated_db,tmp_path):
    batch = edit_file(sample_batch(), 'shipments.csv', lambda rows: rows[0].update(units_shipped='99'))
    original = isolated_db.read_bytes()
    with pytest.raises(BatchValidationError,match='Shipment units'):
        apply_import(isolated_db,batch,backup_folder=tmp_path/'backups')
    assert isolated_db.read_bytes() == original
    assert not import_history(isolated_db)


def test_partial_inventory_snapshot_is_rejected(isolated_db):
    bad = edit_file(sample_batch(),'inventory.csv',lambda rows: rows.pop())
    with pytest.raises(BatchValidationError,match='Full inventory snapshot'):
        preview_import(isolated_db,bad)
    assert count(isolated_db,'inventory') == 80


def test_conflicting_primary_key_is_not_silently_upserted(isolated_db):
    batch = edit_file(sample_batch(),'orders.csv',lambda rows: rows[0].update(order_id='ORD0000001'))
    with pytest.raises(BatchValidationError):
        preview_import(isolated_db,batch)


def test_missing_parent_order_rejected(isolated_db):
    batch = edit_file(sample_batch(),'order_lines.csv',lambda rows: rows[0].update(order_id='MISSING_ORDER'))
    with pytest.raises(BatchValidationError,match='FOREIGN KEY'):
        preview_import(isolated_db,batch)


def test_columns_and_duplicate_keys_rejected():
    src = sample_batch()
    bad_name = {'not_supported.csv':b'col\na'}
    with pytest.raises(BatchValidationError,match='Unsupported'):
        parse_batch(bad_name)
    b = edit_file(src,'orders.csv',lambda rows: rows[1].update(order_id=rows[0]['order_id']))
    with pytest.raises(BatchValidationError,match='duplicate primary key'):
        parse_batch(b)
    b2 = dict(src)
    b2['orders.csv']=b2['orders.csv'].replace(b'customer_id,',b'bad_col,')
    with pytest.raises(BatchValidationError,match='columns must be exactly'):
        parse_batch(b2)


def test_reject_unsafe_formula_and_naive_timestamp():
    src = sample_batch()
    bad = edit_file(src,'shipments.csv',lambda rows: rows[0].update(carrier='=HYPERLINK("x")'))
    with pytest.raises(BatchValidationError,match='unsafe text'):
        parse_batch(bad)
    badtime = edit_file(src,'shipments.csv',lambda rows: rows[0].update(shipped_at='2026-11-13 11:50:00'))
    with pytest.raises(BatchValidationError,match='ISO 8601 UTC'):
        parse_batch(badtime)


def test_existing_order_status_changes_only_when_valid_shipment_added(isolated_db,tmp_path):
    with sqlite3.connect(isolated_db) as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_status='Packed' LIMIT 1").fetchone()
        line_count = conn.execute('SELECT SUM(qty_ordered) FROM order_lines WHERE order_id=?', (order[0],)).fetchone()[0]
    assert order is not None
    columns = ('order_id','warehouse_id','customer_id','created_at','promised_ship_at','priority','order_status')
    order_values = dict(zip(columns,order))
    order_values['order_status'] = 'Shipped'
    ship = {'shipment_id':'DEMO_STATUS_NEW_SHIP','order_id':order[0],'warehouse_id':order[1],
            'carrier':'Demo Carrier','shipped_at':'2026-11-12T21:30:00Z','units_shipped':line_count}
    def csv_bytes(rows):
        out=io.StringIO(); w=csv.DictWriter(out,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
        return out.getvalue().encode()
    partial = {'orders.csv':csv_bytes([order_values]),'shipments.csv':csv_bytes([ship])}
    assert preview_import(isolated_db,partial)['total']['updated'] == 1
    result=apply_import(isolated_db,partial,backup_folder=tmp_path/'backups')
    assert result['total']['updated'] == 1
    with sqlite3.connect(isolated_db) as conn:
        assert conn.execute('SELECT order_status FROM orders WHERE order_id=?',(order[0],)).fetchone()[0] == 'Shipped'


def test_status_without_ship_is_rejected(isolated_db):
    with sqlite3.connect(isolated_db) as conn:
        o=conn.execute("SELECT * FROM orders WHERE order_status='Packed' LIMIT 1").fetchone()
    d=dict(zip(('order_id','warehouse_id','customer_id','created_at','promised_ship_at','priority','order_status'),o))
    d['order_status']='Shipped'
    f=io.StringIO(); w=csv.DictWriter(f,fieldnames=d);w.writeheader();w.writerow(d)
    with pytest.raises(BatchValidationError,match='Shipped order'):
        preview_import(isolated_db,{'orders.csv':f.getvalue().encode()})
