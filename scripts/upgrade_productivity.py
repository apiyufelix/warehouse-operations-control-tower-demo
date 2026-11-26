"""Non-destructive Step 4→Step 5 migration.

Preserves existing order/exception case records and their audit history.
From the Step 5 project folder: python scripts/upgrade_productivity.py
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DDL_START = '-- Step 5: Synthetic process activity and direct labor time records.'
TABLES = ('process_activities', 'labor_shifts')


def migrate(database: str | Path) -> dict[str, int]:
    db = Path(database)
    if not db.is_file():
        raise FileNotFoundError(f'Database not found: {db}')
    sql = (ROOT / 'schema.sql').read_text(encoding='utf-8')
    if DDL_START not in sql:
        raise RuntimeError('Missing Step 5 schema')
    counts = {}
    with sqlite3.connect(db) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exception_cases'").fetchone():
            raise RuntimeError('This is not a valid Step 4 database (missing exception_cases)')
        before = {
            'exception_cases': conn.execute('SELECT COUNT(*) FROM exception_cases').fetchone()[0],
            'exception_events': conn.execute('SELECT COUNT(*) FROM exception_events').fetchone()[0],
        }
        conn.executescript(sql.split(DDL_START, 1)[1])
        for table in TABLES:
            with (ROOT / 'data' / f'{table}.csv').open(newline='', encoding='utf-8') as f:
                records = list(csv.DictReader(f))
            columns = list(records[0])
            stmt = f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
            conn.executemany(stmt, [[record[c] for c in columns] for record in records])
            found = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if found != len(records):
                raise ValueError(f'{table} row count mismatch: database {found}, CSV {len(records)}')
            counts[table] = found
        fk = conn.execute('PRAGMA foreign_key_check').fetchall()
        if fk:
            raise RuntimeError(f'Foreign key errors: {fk[:3]}')
        for table, original in before.items():
            current = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if current != original:
                raise RuntimeError(f'Migration unexpectedly changed {table}')
        conn.commit()
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(ROOT / 'warehouse_ops.db'), help='Existing Step 4/5 database path')
    args = parser.parse_args()
    print('Upgraded without resetting exception cases:', migrate(args.db))
