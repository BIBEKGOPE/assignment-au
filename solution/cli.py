"""Command-line entry points for ingesting drops and writing reports."""

import argparse
import sys
from pathlib import Path

from solution.db import connect, init_schema
from solution.pii import get_key
from solution.pipeline import ingest_drop
from solution.report import write_reports
from solution.schema import load_dispositions, load_schemas


_DEFAULT_DB = "ledger.db"
_DEFAULT_SCHEMAS = "assignment-a-data/schemas"
_INGEST_SUMMARY_ORDER = (
    "rows_total",
    "rejected",
    "new_leads",
    "merged",
    "warnings",
    "events_total",
    "malformed",
    "duplicate_events_dropped",
)


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for the pipeline."""
    parser = argparse.ArgumentParser(description="Assignment A data pipeline")
    parser.add_argument("--db", default=_DEFAULT_DB, help="SQLite database path")
    parser.add_argument(
        "--schemas",
        default=_DEFAULT_SCHEMAS,
        help="Directory containing workflow schemas and dispositions.csv",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="ingest one delivery drop")
    ingest_parser.add_argument("drop_dir", help="path to the delivery drop")
    ingest_parser.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        help="SQLite database path",
    )
    ingest_parser.add_argument(
        "--schemas",
        default=argparse.SUPPRESS,
        help="directory containing workflow schemas",
    )

    report_parser = subparsers.add_parser("report", help="write all report files")
    report_parser.add_argument("--out", required=True, help="report output directory")
    report_parser.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        help="SQLite database path",
    )
    report_parser.add_argument(
        "--schemas",
        default=argparse.SUPPRESS,
        help="directory containing workflow schemas",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a pipeline command and return its process exit status."""
    args = build_parser().parse_args(argv)
    if args.command == "ingest" and not Path(args.drop_dir).is_dir():
        print(
            f"Error: drop directory does not exist: {args.drop_dir}",
            file=sys.stderr,
        )
        return 2

    conn = connect(args.db)
    try:
        init_schema(conn)
        if args.command == "ingest":
            schemas = load_schemas(args.schemas)
            key = get_key(args.db)
            try:
                counters = ingest_drop(conn, key, schemas, args.drop_dir)
            except FileNotFoundError as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
            summary = " ".join(
                f"{name}={counters[name]}" for name in _INGEST_SUMMARY_ORDER
            )
            print(f"Ingest complete: {summary}")
        else:
            dispositions = load_dispositions(
                Path(args.schemas) / "dispositions.csv"
            )
            write_reports(conn, dispositions, args.out)
            print(f"Reports written to {args.out}")
    finally:
        conn.close()
    return 0
