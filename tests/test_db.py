import sqlite3

import pytest

from solution.db import connect, init_schema


@pytest.fixture
def db_connection(tmp_path):
    connection = connect(tmp_path / "pipeline.sqlite")
    init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_connect_enables_foreign_keys_wal_and_row_factory(db_connection):
    assert db_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db_connection.execute("SELECT 1 AS value").fetchone()["value"] == 1


def test_init_schema_is_safe_to_call_twice(db_connection):
    init_schema(db_connection)
    tables = {
        row["name"]
        for row in db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert {
        "ingested_files",
        "leads",
        "lead_rejections",
        "events",
    }.issubset(tables)


def _insert_event(db_connection):
    file_cursor = db_connection.execute(
        """
        INSERT INTO ingested_files (
            kind, relpath, sha256, counters_json, ingested_at
        ) VALUES ('events', 'drop/events.jsonl', 'digest', '{}', '2026-10-05T00:00:00Z')
        """
    )
    db_connection.execute(
        """
        INSERT INTO events (
            event_id, event_type, workflow_id, call_id, occurred_at_utc,
            phone_hash, payload_json, file_id
        ) VALUES ('evt-1', 'call.initiated', 'motor_renewal', 'call-1',
                  '2026-10-05T00:00:00Z', NULL, '{}', ?)
        """,
        (file_cursor.lastrowid,),
    )
    db_connection.commit()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET event_type = 'changed' WHERE event_id = 'evt-1'",
        "DELETE FROM events WHERE event_id = 'evt-1'",
    ],
)
def test_events_reject_update_and_delete(db_connection, statement):
    _insert_event(db_connection)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db_connection.execute(statement)
