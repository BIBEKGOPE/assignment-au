"""Ingest JSONL call events into the append-only interaction ledger."""

import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from solution.normalize import norm_phone, parse_timestamp
from solution.pii import phone_hash


_EVENT_BATCH_SIZE = 1000
_INSERT_EVENT_SQL = """
    INSERT OR IGNORE INTO events (
        event_id, event_type, workflow_id, call_id, occurred_at_utc,
        phone_hash, payload_json, file_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_event_batch(
    conn: sqlite3.Connection,
    event_rows: list[tuple[str, str, str, str | None, str, str | None, str, int]],
) -> int:
    cursor = conn.executemany(_INSERT_EVENT_SQL, event_rows)
    return len(event_rows) - cursor.rowcount


def _parse_event(
    line: str,
    key: bytes,
) -> tuple[str, str, str, str | None, str, str | None, str] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(event, dict):
        return None

    event_id = event.get("event_id")
    event_type = event.get("event_type")
    workflow_id = event.get("workflow_id")
    occurred_at = event.get("occurred_at")
    call_id = event.get("call_id")
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or not isinstance(event_type, str)
        or not event_type.strip()
        or not isinstance(workflow_id, str)
        or not workflow_id.strip()
        or (call_id is not None and not isinstance(call_id, str))
        or not isinstance(occurred_at, str)
    ):
        return None

    moment = parse_timestamp(occurred_at)
    if moment is None:
        return None

    phone = event.get("phone")
    canonical_phone = norm_phone(phone) if isinstance(phone, str) else None
    digest = phone_hash(key, canonical_phone) if canonical_phone is not None else None
    occurred_at_utc = moment.astimezone(timezone.utc).isoformat()
    payload_json = json.dumps(
        event.get("payload", {}),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        event_id,
        event_type,
        workflow_id,
        call_id,
        occurred_at_utc,
        digest,
        payload_json,
    )


def ingest_event_file(
    conn: sqlite3.Connection,
    key: bytes,
    jsonl_path: str | Path,
    relpath: str,
) -> dict[str, int]:
    """Ingest one JSONL event file atomically and return its counters.

    Example: ``ingest_event_file(conn, key, "events.jsonl", "events.jsonl")``
    returns counters for total, malformed, and duplicate events.
    """
    counters = {
        "events_total": 0,
        "malformed": 0,
        "duplicate_events_dropped": 0,
    }

    with Path(jsonl_path).open("rb") as binary_file:
        digest = hashlib.sha256()
        while chunk := binary_file.read(1024 * 1024):
            digest.update(chunk)
        file_sha256 = digest.hexdigest()
        binary_file.seek(0)

        with conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_file = conn.execute(
                "SELECT 1 FROM ingested_files WHERE relpath = ? AND sha256 = ?",
                (relpath, file_sha256),
            ).fetchone()
            if existing_file is not None:
                return counters

            file_cursor = conn.execute(
                """
                INSERT INTO ingested_files (
                    kind, relpath, sha256, counters_json, ingested_at
                ) VALUES ('events', ?, ?, '{}', ?)
                """,
                (
                    relpath,
                    file_sha256,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            file_id = file_cursor.lastrowid
            if file_id is None:
                raise RuntimeError("SQLite did not return the ingested file ID")

            event_rows: list[
                tuple[str, str, str, str | None, str, str | None, str, int]
            ] = []
            with io.TextIOWrapper(
                binary_file,
                encoding="utf-8-sig",
                newline="",
            ) as event_file:
                for line in event_file:
                    if not line.strip():
                        continue
                    counters["events_total"] += 1
                    parsed = _parse_event(line, key)
                    if parsed is None:
                        counters["malformed"] += 1
                        continue
                    event_rows.append((*parsed, file_id))

                    if len(event_rows) >= _EVENT_BATCH_SIZE:
                        counters["duplicate_events_dropped"] += _insert_event_batch(
                            conn,
                            event_rows,
                        )
                        event_rows.clear()

            if event_rows:
                counters["duplicate_events_dropped"] += _insert_event_batch(
                    conn,
                    event_rows,
                )

            conn.execute(
                "UPDATE ingested_files SET counters_json = ? WHERE id = ?",
                (json.dumps(counters, sort_keys=True), file_id),
            )

    return counters
