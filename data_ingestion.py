"""Step 9: controlled, transactional CSV ingestion for the synthetic control tower.

No WMS connection is established. Uploads are manually provided, UTF-8 CSV files.
Previews execute against an isolated SQLite in-memory copy, NEVER the live DB.
Final imports create a SQLite backup and record auditable changes atomically.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

# Reference dimensions are fixed for this pilot. Import dimension changes in a later version.
IMPORT_ORDER = (
    'orders', 'order_lines', 'shipments', 'quality_inspections', 'inventory',
    'process_activities', 'labor_shifts',
)
MAX_CSV_BYTES = 4 * 1024 * 1024
MAX_RECORDS_PER_CSV = 12000

COLUMNS = {
    'orders': ('order_id','warehouse_id','customer_id','created_at','promised_ship_at','priority','order_status'),
    'order_lines': ('order_line_id','order_id','product_id','qty_ordered','unit_price'),
    'shipments': ('shipment_id','order_id','warehouse_id','carrier','shipped_at','units_shipped'),
    'quality_inspections': ('inspection_id','order_id','order_line_id','warehouse_id','inspected_at','qty_inspected','defect_qty','inspection_result','issue_type','resolution_status'),
    'inventory': ('warehouse_id','product_id','snapshot_at','on_hand_qty','allocated_qty','available_qty','reorder_point'),
    'process_activities': ('activity_id','order_id','warehouse_id','process_name','started_at','completed_at','shift_name','units_processed','queue_minutes','processing_minutes'),
    'labor_shifts': ('labor_id','warehouse_id','process_name','shift_name','shift_start_at','shift_end_at','planned_labor_hours','actual_labor_hours'),
}
INT_COLS = {'qty_ordered','units_shipped','qty_inspected','defect_qty','on_hand_qty','allocated_qty',
            'available_qty','reorder_point','units_processed'}
FLOAT_COLS = {'unit_price','queue_minutes','processing_minutes','planned_labor_hours','actual_labor_hours'}
DATE_COLS = {'created_at','promised_ship_at','shipped_at','inspected_at','snapshot_at','started_at',
             'completed_at','shift_start_at','shift_end_at'}
ID_COLS = {'order_id','order_line_id','shipment_id','inspection_id','activity_id','labor_id',
           'warehouse_id','product_id','customer_id'}
ALLOWED = {
    'priority': {'Standard','Expedited','SameDay'},
    'order_status': {'Shipped','Cancelled','Picking','Packed','Exception Hold'},
    'inspection_result': {'Pass','Fail'},
    'resolution_status': {'Not Required','Open','Closed'},
    'process_name': {'Picking','Packing','Dispatch'},
    'shift_name': {'Shift A (00–08 UTC)','Shift B (08–16 UTC)','Shift C (16–24 UTC)'},
}
PK = {'orders':('order_id',), 'order_lines':('order_line_id',), 'shipments':('shipment_id',),
      'quality_inspections':('inspection_id',),
      'inventory':('warehouse_id','product_id','snapshot_at'),
      'process_activities':('activity_id',), 'labor_shifts':('labor_id',)}

CREATE_AUDIT = (
    "CREATE TABLE IF NOT EXISTS data_import_runs ("
    "run_id INTEGER PRIMARY KEY AUTOINCREMENT, imported_at TEXT NOT NULL, "
    "batch_hash TEXT NOT NULL, source_label TEXT NOT NULL, inserted_total INTEGER NOT NULL, "
    "unchanged_total INTEGER NOT NULL, updated_total INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS data_import_details ("
    "run_id INTEGER NOT NULL REFERENCES data_import_runs(run_id), "
    "table_name TEXT NOT NULL, received_rows INTEGER NOT NULL, inserted_rows INTEGER NOT NULL, "
    "unchanged_rows INTEGER NOT NULL, updated_rows INTEGER NOT NULL, "
    "PRIMARY KEY(run_id,table_name))",
)

class BatchValidationError(ValueError):
    """Validation rejection, with full transaction rollback."""


def _canonical_time(value: str) -> str:
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', value):
        raise BatchValidationError('Timestamp must be ISO 8601 UTC, e.g. 2026-11-13T22:00:00Z')
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise BatchValidationError(f'Invalid timestamp: {value}') from exc
    return parsed.isoformat(timespec='seconds').replace('+00:00','Z') if not parsed.microsecond else parsed.isoformat(timespec='microseconds').replace('+00:00','Z')


def _normalize(table: str, record: dict, line: int) -> dict:
    output = {}
    for key in COLUMNS[table]:
        raw = record[key]
        if raw is None or not str(raw).strip():
            raise BatchValidationError(f'{table}.csv row {line}: {key} cannot be blank')
        value = str(raw).strip()
        if len(value) > 200:
            raise BatchValidationError(f'{table}.csv row {line}: {key} is too long')
        if key in INT_COLS:
            if not re.fullmatch(r'\d+', value):
                raise BatchValidationError(f'{table}.csv row {line}: {key} must be a nonnegative integer')
            val = int(value)
        elif key in FLOAT_COLS:
            try:
                val = float(value)
            except ValueError as exc:
                raise BatchValidationError(f'{table}.csv row {line}: {key} must be numeric') from exc
            import math
            if not math.isfinite(val) or val < 0:
                raise BatchValidationError(f'{table}.csv row {line}: {key} must be finite and nonnegative')
        elif key in DATE_COLS:
            try:
                val = _canonical_time(value)
            except BatchValidationError as exc:
                raise BatchValidationError(f'{table}.csv row {line}: {key}: {exc}') from exc
        else:
            # Avoid script/formula-like untrusted text being persisted for later CSV exports.
            if any(ord(c) < 32 for c in value) or value[0] in ('=', '+', '@', '-'):
                raise BatchValidationError(f'{table}.csv row {line}: unsafe text in {key}')
            if key in ID_COLS and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
                raise BatchValidationError(f'{table}.csv row {line}: {key} requires letters, numbers, _ or -')
            if key in ALLOWED and value not in ALLOWED[key]:
                raise BatchValidationError(f'{table}.csv row {line}: unsupported {key}: {value}')
            val = value
        output[key] = val
    if table == 'inventory' and output['available_qty'] != output['on_hand_qty'] - output['allocated_qty']:
        raise BatchValidationError(f'{table}.csv row {line}: available_qty must equal on_hand_qty - allocated_qty')
    if table == 'orders' and output['promised_ship_at'] < output['created_at']:
        raise BatchValidationError(f'{table}.csv row {line}: promise cannot precede order creation')
    if table == 'quality_inspections':
        if output['defect_qty'] > output['qty_inspected']:
            raise BatchValidationError(f'{table}.csv row {line}: defects exceed inspected units')
        if (output['defect_qty'] > 0) != (output['inspection_result'] == 'Fail'):
            raise BatchValidationError(f'{table}.csv row {line}: inspection_result must match defect quantity')
    if table in ('process_activities','labor_shifts'):
        start = 'started_at' if table == 'process_activities' else 'shift_start_at'
        end = 'completed_at' if table == 'process_activities' else 'shift_end_at'
        if output[end] < output[start]:
            raise BatchValidationError(f'{table}.csv row {line}: end cannot precede start')
    # Essential positive-unit constraints are also present in SQLite schema.
    for name in ('qty_ordered','units_shipped','qty_inspected','units_processed'):
        if name in output and output[name] <= 0:
            raise BatchValidationError(f'{table}.csv row {line}: {name} must be positive')
    return output


def parse_batch(files: Mapping[str, bytes]) -> dict[str, list[dict]]:
    """Parse uploaded name->bytes mappings, never accept path names as SQL identifiers."""
    if not files:
        raise BatchValidationError('Choose at least one CSV file')
    unknown = set(files) - {name + '.csv' for name in IMPORT_ORDER}
    if unknown:
        raise BatchValidationError(f'Unsupported CSV filename(s): {", ".join(sorted(unknown))}')
    out = {}
    for table in IMPORT_ORDER:
        name = table + '.csv'
        if name not in files:
            continue
        blob = files[name]
        if len(blob) > MAX_CSV_BYTES:
            raise BatchValidationError(f'{name} exceeds the 4 MB upload limit')
        try:
            content = blob.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise BatchValidationError(f'{name}: must be UTF-8 text') from exc
        reader = csv.DictReader(io.StringIO(content, newline=''))
        fields = reader.fieldnames
        if fields is None or len(set(fields)) != len(fields) or set(fields) != set(COLUMNS[table]):
            raise BatchValidationError(f'{name}: columns must be exactly: {", ".join(COLUMNS[table])}')
        rows = []
        keys = set()
        for n, row in enumerate(reader, 2):
            if n > MAX_RECORDS_PER_CSV + 1:
                raise BatchValidationError(f'{name}: maximum {MAX_RECORDS_PER_CSV:,} rows per import')
            if None in row:
                raise BatchValidationError(f'{name} row {n}: too many columns')
            clean = _normalize(table, row, n)
            pk = tuple(clean[col] for col in PK[table])
            if pk in keys:
                raise BatchValidationError(f'{name}: duplicate primary key within upload at row {n}: {pk}')
            keys.add(pk)
            rows.append(clean)
        if not rows:
            raise BatchValidationError(f'{name}: at least one data row is required')
        out[table] = rows
    return out


def batch_digest(files: Mapping[str,bytes]) -> str:
    hasher = hashlib.sha256()
    for filename in sorted(files):
        hasher.update(filename.encode('utf-8'))
        hasher.update(b'\x00')
        hasher.update(files[filename])
        hasher.update(b'\x00')
    return hasher.hexdigest()


def _check_inventory_batch(conn: sqlite3.Connection, rows: list[dict]) -> None:
    dates = {r['snapshot_at'] for r in rows}
    if len(dates) != 1:
        raise BatchValidationError('inventory.csv must contain one consistent snapshot_at UTC timestamp')
    new_at = next(iter(dates))
    old_at = conn.execute('SELECT MAX(snapshot_at) FROM inventory').fetchone()[0]
    if old_at is not None and new_at < old_at:
        raise BatchValidationError('Inventory snapshot is older than the most recent stored snapshot')
    # An identical current snapshot can be replayed safely; edits to existing
    # inventory records are rejected by immutable-key comparison below.
    existing = {tuple(r) for r in conn.execute('SELECT warehouse_id, product_id FROM inventory WHERE snapshot_at=?', (old_at,))} if old_at else set()
    supplied = {(r['warehouse_id'],r['product_id']) for r in rows}
    if existing and supplied != existing:
        missing = len(existing-supplied)
        extra = len(supplied-existing)
        raise BatchValidationError(f'Full inventory snapshot required: {missing} SKU/warehouse pairs missing, {extra} unexpected. Avoid partial snapshots.')


def _assert_integrity(conn: sqlite3.Connection) -> None:
    fk = conn.execute('PRAGMA foreign_key_check').fetchall()
    if fk:
        raise BatchValidationError(f'Foreign-key mismatch: {fk[0]}')
    checks = [
        ('Shipped order must have exactly one shipment', """SELECT count(*) FROM orders o WHERE o.order_status='Shipped' AND NOT EXISTS (SELECT 1 FROM shipments s WHERE s.order_id=o.order_id)"""),
        ('Shipment must match Shipped status and warehouse', """SELECT count(*) FROM shipments s JOIN orders o ON o.order_id=s.order_id WHERE o.order_status!='Shipped' OR o.warehouse_id!=s.warehouse_id"""),
        ('Shipment units must equal ordered units', """SELECT count(*) FROM shipments s LEFT JOIN (SELECT order_id,SUM(qty_ordered) total FROM order_lines GROUP BY order_id) l ON l.order_id=s.order_id WHERE l.total IS NULL OR s.units_shipped != l.total"""),
        ('All orders require order lines', """SELECT count(*) FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_lines l WHERE l.order_id=o.order_id)"""),
        ('Inspection warehouse/order line association must match', """SELECT count(*) FROM quality_inspections q JOIN order_lines l ON l.order_line_id=q.order_line_id JOIN orders o ON o.order_id=q.order_id WHERE l.order_id!=q.order_id OR o.warehouse_id!=q.warehouse_id"""),
        ('Process activity warehouse must match order', """SELECT count(*) FROM process_activities p JOIN orders o ON o.order_id=p.order_id WHERE p.warehouse_id!=o.warehouse_id"""),
        ('Process activity units must match shipped units', """SELECT count(*) FROM process_activities p LEFT JOIN shipments s ON s.order_id=p.order_id WHERE s.shipment_id IS NULL OR s.units_shipped!=p.units_processed"""),
    ]
    for message, sql in checks:
        n = conn.execute(sql).fetchone()[0]
        if n:
            raise BatchValidationError(f'{message}: {n} problem record(s)')


def _apply_rows(conn: sqlite3.Connection, batch: dict[str,list[dict]]) -> tuple[dict, list[str]]:
    stats = {}
    warnings = []
    if 'inventory' in batch:
        _check_inventory_batch(conn, batch['inventory'])
    for table in IMPORT_ORDER:
        if table not in batch:
            continue
        incoming = batch[table]
        counts = {'received': len(incoming), 'inserted': 0, 'unchanged': 0, 'updated': 0}
        keys = PK[table]
        for row in incoming:
            query = ' AND '.join(f'{col}=?' for col in keys)
            existing = conn.execute(f'SELECT * FROM {table} WHERE {query}', [row[col] for col in keys]).fetchone()
            if existing is not None:
                previous = dict(existing)
                changed = {k for k in COLUMNS[table] if previous[k] != row[k]}
                if not changed:
                    counts['unchanged'] += 1
                    continue
                if table == 'orders' and changed == {'order_status'}:
                    old = previous['order_status']
                    new = row['order_status']
                    transitions = {
                        'Picking': {'Packed','Exception Hold','Shipped','Cancelled'},
                        'Packed': {'Exception Hold','Shipped','Cancelled'},
                        'Exception Hold': {'Picking','Packed','Shipped','Cancelled'},
                    }
                    if new not in transitions.get(old, set()):
                        raise BatchValidationError(f'Invalid order state transition {row["order_id"]}: {old} → {new}')
                    conn.execute('UPDATE orders SET order_status=? WHERE order_id=?', (new, row['order_id']))
                    counts['updated'] += 1
                else:
                    raise BatchValidationError(f'{table}: existing record {tuple(row[k] for k in keys)} conflicts with uploaded values; corrections are not supported')
            else:
                columns = COLUMNS[table]
                placeholders = ','.join('?' for _ in columns)
                try:
                    conn.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({placeholders})',
                                 [row[col] for col in columns])
                except sqlite3.IntegrityError as exc:
                    raise BatchValidationError(f'{table}: rejected row {tuple(row[k] for k in keys)}: {exc}') from exc
                counts['inserted'] += 1
        stats[table] = counts
    _assert_integrity(conn)
    latest = conn.execute('SELECT MAX(snapshot_at) FROM inventory').fetchone()[0]
    after_latest = conn.execute('SELECT COUNT(*) FROM orders WHERE created_at> ?', (latest,)).fetchone()[0]
    if after_latest:
        warnings.append(f'{after_latest} order(s) occur after latest inventory snapshot {latest}. Update inventory to advance the dashboard reporting cutoff.')
    if ('shipments' in batch or 'orders' in batch) and 'process_activities' not in batch:
        warnings.append('New shipments lack new picking/packing/dispatch events unless separately imported; productivity rates may lag shipment volume.')
    if 'process_activities' in batch and 'labor_shifts' not in batch:
        warnings.append('Activity rows were imported without matching new labor hours. Review units-per-labor-hour trends.')
    return stats, warnings


def _sum_stats(stats: dict) -> dict:
    return {field: sum(row[field] for row in stats.values()) for field in ('received','inserted','unchanged','updated')}


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.execute('PRAGMA foreign_keys=ON')
    conn.row_factory = sqlite3.Row
    return conn


def preview_import(database: str | Path, files: Mapping[str,bytes]) -> dict:
    """Returns validated counts based on an isolated backup, with zero live DB writes."""
    batch = parse_batch(files)
    source = _connect(database)
    memory = _connect(':memory:')
    try:
        source.backup(memory)
        memory.execute('BEGIN')
        stats, warnings = _apply_rows(memory, batch)
        memory.rollback()
        return {'counts':stats, 'total':_sum_stats(stats), 'warnings':warnings, 'hash':batch_digest(files)}
    finally:
        memory.close()
        source.close()


def import_history(database: str | Path, limit: int = 15) -> list[dict]:
    with _connect(database) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_import_runs'").fetchone():
            return []
        rows = conn.execute('SELECT * FROM data_import_runs ORDER BY run_id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]


def apply_import(database: str | Path, files: Mapping[str,bytes], source_label: str='Manual CSV upload',
                 backup_folder: str | Path | None = None) -> dict:
    """Make a pre-write backup, then validate/apply all tables in a single SQL transaction.

    On *any* error the data and audit transaction is rolled back. The backup is
    retained even after a failed apply to provide an independent recovery point.
    """
    batch = parse_batch(files)
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not 1 <= len(source_label.strip()) <= 100:
        raise BatchValidationError('Source label must have 1 to 100 characters')
    # Create a consistent snapshot with SQLite's backup API, never shutil.copy a live db.
    folder = Path(backup_folder) if backup_folder else path.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    backup_path = folder / f'warehouse_ops_before_import_{stamp}.db'
    with _connect(path) as src, _connect(backup_path) as target:
        src.backup(target)
    conn = _connect(path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        for statement in CREATE_AUDIT:
            conn.execute(statement)
        stats, warnings = _apply_rows(conn, batch)
        totals = _sum_stats(stats)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        cursor = conn.execute(
            'INSERT INTO data_import_runs (imported_at,batch_hash,source_label,inserted_total,unchanged_total,updated_total) VALUES (?,?,?,?,?,?)',
            (now, batch_digest(files), source_label.strip(),totals['inserted'],totals['unchanged'],totals['updated'])
        )
        run_id = cursor.lastrowid
        conn.executemany(
            'INSERT INTO data_import_details (run_id,table_name,received_rows,inserted_rows,unchanged_rows,updated_rows) VALUES (?,?,?,?,?,?)',
            [(run_id,t,r['received'],r['inserted'],r['unchanged'],r['updated']) for t,r in stats.items()]
        )
        conn.commit()
        return {'run_id':run_id, 'counts':stats,'total':totals,'warnings':warnings,
                'hash':batch_digest(files),'backup_path':str(backup_path)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
