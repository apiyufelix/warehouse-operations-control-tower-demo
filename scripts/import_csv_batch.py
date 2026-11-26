"""Optional terminal workflow for Step 9 CSV files in a folder, no network access.

Validate without writing:
  python scripts/import_csv_batch.py --folder samples/example_unpacked
Approve only after review:
  python scripts/import_csv_batch.py --folder samples/example_unpacked --apply --confirm
"""
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from data_ingestion import preview_import,apply_import,BatchValidationError


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',required=True,type=Path,help='Directory of supported named CSV files')
    parser.add_argument('--db',default=ROOT/'warehouse_ops.db',type=Path)
    parser.add_argument('--source',default='Manual terminal CSV import')
    parser.add_argument('--apply',action='store_true',help='Write to DB after validation')
    parser.add_argument('--confirm',action='store_true',help='Second opt-in for writing, required with --apply')
    args=parser.parse_args()
    try:
        sources={path.name:path.read_bytes() for path in args.folder.glob('*.csv')}
        report=preview_import(args.db,sources)
        print('Preview:',report['total'])
        for table,counts in report['counts'].items(): print(' -',table,counts)
        for warning in report['warnings']: print('WARNING:',warning)
        if args.apply:
            if not args.confirm:
                parser.error('To apply changes pass BOTH --apply and --confirm')
            result=apply_import(args.db,sources,source_label=args.source)
            print('Applied as run',result['run_id'],'with backup',result['backup_path'])
        else:
            print('Read-only preview. Nothing was saved. To approve: --apply --confirm')
    except (BatchValidationError, OSError) as exc:
        parser.exit(1,f'Validation failed: {exc}\n')

if __name__=='__main__': run()
