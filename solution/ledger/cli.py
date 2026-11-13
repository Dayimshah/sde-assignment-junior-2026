"""Command line: `python -m ledger ingest <drop_dir>` and `python -m ledger report --out <dir>`."""
import argparse
import sys

from .ingest import ingest_drop
from .reports import write_reports
from .store import IngestError, Store


def build_parser():
    parser = argparse.ArgumentParser(prog="ledger", description="Interaction ledger and reporting pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest one drop folder (drop_1, drop_2, ...)")
    ingest.add_argument("drop_dir")
    ingest.add_argument("--db", default="ledger.db", help="SQLite database file (default: ledger.db)")

    report = sub.add_parser("report", help="write the five report files")
    report.add_argument("--out", required=True, help="output folder")
    report.add_argument("--db", default="ledger.db", help="SQLite database file (default: ledger.db)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = Store(args.db)
    try:
        if args.command == "ingest":
            summary = ingest_drop(store, args.drop_dir)
            print(f"ingested {args.drop_dir}: {summary['lead_files']} lead files, "
                  f"{summary['event_files']} event files, {summary['skipped_files']} already ingested")
        else:
            write_reports(store, args.out)
            print(f"wrote reports to {args.out}")
    except (IngestError, FileNotFoundError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0
