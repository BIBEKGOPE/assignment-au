"""Coordinate ingestion of all lead and event files in a delivery drop."""

import sqlite3
from pathlib import Path
from typing import Mapping

from solution.ingest_events import ingest_event_file
from solution.ingest_leads import ingest_lead_file
from solution.schema import WorkflowSchema


_COUNTER_NAMES = (
    "rows_total",
    "rejected",
    "new_leads",
    "merged",
    "warnings",
    "events_total",
    "malformed",
    "duplicate_events_dropped",
)


def _relpath(drop: Path, file_path: Path) -> str:
    return f"{drop.name}/{file_path.relative_to(drop).as_posix()}"


def ingest_drop(
    conn: sqlite3.Connection,
    key: bytes,
    schemas: Mapping[str, WorkflowSchema],
    drop_dir: str | Path,
) -> dict[str, int]:
    """Ingest CSV leads by workflow, followed by JSONL events, in sorted order."""
    drop = Path(drop_dir)
    if not drop.is_dir():
        raise FileNotFoundError(f"Drop directory does not exist: {drop}")

    totals = {name: 0 for name in _COUNTER_NAMES}
    leads_dir = drop / "leads"
    if leads_dir.exists() and not leads_dir.is_dir():
        raise NotADirectoryError(f"Leads path is not a directory: {leads_dir}")
    if leads_dir.is_dir():
        workflow_dirs = sorted(
            (path for path in leads_dir.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
        for workflow_dir in workflow_dirs:
            schema = schemas.get(workflow_dir.name)
            if schema is None:
                raise ValueError(f"No lead schema for workflow {workflow_dir.name!r}")
            for csv_path in sorted(
                workflow_dir.glob("*.csv"),
                key=lambda path: path.name,
            ):
                counters = ingest_lead_file(
                    conn,
                    key,
                    schema,
                    csv_path,
                    _relpath(drop, csv_path),
                )
                for name, value in counters.items():
                    totals[name] += value

    events_dir = drop / "events"
    if events_dir.exists() and not events_dir.is_dir():
        raise NotADirectoryError(f"Events path is not a directory: {events_dir}")
    if events_dir.is_dir():
        for jsonl_path in sorted(
            events_dir.glob("*.jsonl"),
            key=lambda path: path.name,
        ):
            counters = ingest_event_file(
                conn,
                key,
                jsonl_path,
                _relpath(drop, jsonl_path),
            )
            for name, value in counters.items():
                totals[name] += value

    return totals
