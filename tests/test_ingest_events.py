import json
from pathlib import Path

import pytest

from solution.db import connect, init_schema
from solution.ingest_events import ingest_event_file
from solution.pii import phone_hash


@pytest.fixture
def event_env(tmp_path):
    connection = connect(tmp_path / "pipeline.sqlite")
    init_schema(connection)
    key = bytes(range(32))
    try:
        yield connection, key
    finally:
        connection.close()


def _event(event_id, **overrides):
    event = {
        "event_id": event_id,
        "event_type": "call.initiated",
        "workflow_id": "motor_renewal",
        "call_id": f"call-{event_id}",
        "occurred_at": "2026-10-05T13:20:21Z",
        "phone": "9876543210",
        "payload": {},
    }
    event.update(overrides)
    return event


def _write_jsonl(path: Path, lines):
    with path.open("w", encoding="utf-8", newline="") as jsonl_file:
        for line in lines:
            if isinstance(line, str):
                jsonl_file.write(line)
            else:
                jsonl_file.write(json.dumps(line, ensure_ascii=False))
            jsonl_file.write("\n")
    return path


def _ingest(connection, key, path, relpath=None):
    return ingest_event_file(
        connection,
        key,
        path,
        relpath or f"drop_1/events/{path.name}",
    )


def test_malformed_lines_are_counted_and_skipped(event_env, tmp_path):
    connection, key = event_env
    path = _write_jsonl(
        tmp_path / "events.jsonl",
        [
            "{",
            "[]",
            {},
            {"event_id": "missing-time", "event_type": "call.initiated",
             "workflow_id": "motor_renewal"},
            _event("bad-time", occurred_at="not a timestamp"),
            _event("good-event"),
            "",
            "   ",
        ],
    )

    counters = _ingest(connection, key, path)

    assert counters == {
        "events_total": 6,
        "malformed": 5,
        "duplicate_events_dropped": 0,
    }
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_duplicate_event_ids_are_dropped_within_and_across_files(
    event_env, tmp_path
):
    connection, key = event_env
    first_path = _write_jsonl(
        tmp_path / "first.jsonl",
        [
            _event("evt-1"),
            _event("evt-1", event_type="call.connected"),
            _event("evt-2"),
        ],
    )
    second_path = _write_jsonl(
        tmp_path / "second.jsonl",
        [_event("evt-2"), _event("evt-3")],
    )

    first = _ingest(connection, key, first_path)
    second = _ingest(
        connection,
        key,
        second_path,
        "drop_2/events/second.jsonl",
    )

    assert first["duplicate_events_dropped"] == 1
    assert second["duplicate_events_dropped"] == 1
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3


def test_naive_timestamp_is_interpreted_as_ist_then_stored_as_utc(
    event_env, tmp_path
):
    connection, key = event_env
    path = _write_jsonl(
        tmp_path / "events.jsonl",
        [_event("evt-naive", occurred_at="2026-10-03 10:01:00")],
    )

    _ingest(connection, key, path)
    stored = connection.execute(
        "SELECT occurred_at_utc, phone_hash FROM events WHERE event_id = ?",
        ("evt-naive",),
    ).fetchone()

    assert stored["occurred_at_utc"] == "2026-10-03T04:31:00+00:00"
    assert stored["phone_hash"] == phone_hash(key, "+919876543210")


def test_disposition_override_is_stored_without_a_phone(event_env, tmp_path):
    connection, key = event_env
    path = _write_jsonl(
        tmp_path / "events.jsonl",
        [
            _event(
                "evt-override",
                event_type="disposition.override",
                call_id="call-123",
                phone=None,
                payload={"disposition_code": "renewed"},
            )
        ],
    )

    _ingest(connection, key, path)
    stored = connection.execute(
        "SELECT event_type, call_id, phone_hash, payload_json FROM events"
    ).fetchone()

    assert stored["event_type"] == "disposition.override"
    assert stored["call_id"] == "call-123"
    assert stored["phone_hash"] is None
    assert json.loads(stored["payload_json"]) == {
        "disposition_code": "renewed"
    }


def test_reingesting_same_file_changes_nothing(event_env, tmp_path):
    connection, key = event_env
    path = _write_jsonl(tmp_path / "events.jsonl", [_event("evt-1")])

    first = _ingest(connection, key, path)
    stored_counters = connection.execute(
        "SELECT counters_json FROM ingested_files"
    ).fetchone()["counters_json"]
    repeated = _ingest(connection, key, path)

    assert first["events_total"] == 1
    assert json.loads(stored_counters) == first
    assert repeated == {
        "events_total": 0,
        "malformed": 0,
        "duplicate_events_dropped": 0,
    }
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM ingested_files"
    ).fetchone()[0] == 1
