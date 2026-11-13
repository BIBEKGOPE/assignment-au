import csv
import json
from pathlib import Path

import pytest

from solution.db import connect, init_schema
from solution.pii import phone_hash
from solution.report import compute_bucket, effective_disposition, write_reports
from solution.schema import load_dispositions


DISPOSITIONS_PATH = (
    Path(__file__).resolve().parents[1]
    / "assignment-a-data"
    / "schemas"
    / "dispositions.csv"
)


@pytest.mark.parametrize(
    ("sets", "overrides", "expected"),
    [
        (
            [
                {
                    "event_id": "evt-a",
                    "occurred_at": "2026-10-05T13:20:00Z",
                    "code": "renewed",
                },
                {
                    "event_id": "evt-b",
                    "occurred_at": "2026-10-05T13:21:00Z",
                    "code": "not_interested",
                },
                {
                    "event_id": "evt-invalid",
                    "occurred_at": "2026-10-05T13:22:00Z",
                    "code": "unknown",
                },
            ],
            [],
            "not_interested",
        ),
        (
            [
                {
                    "event_id": "evt-late-set",
                    "occurred_at": "2026-10-05T20:00:00Z",
                    "code": "not_interested",
                }
            ],
            [
                {
                    "event_id": "override-a",
                    "occurred_at": "2026-10-05T19:00:00Z",
                    "code": "not_interested",
                },
                {
                    "event_id": "override-z",
                    "occurred_at": "2026-10-05T19:00:00Z",
                    "code": "renewed",
                },
            ],
            "renewed",
        ),
        (
            [
                {
                    "event_id": "evt-set",
                    "occurred_at": "2026-10-05T13:20:00Z",
                    "code": "renewed",
                }
            ],
            [
                {
                    "event_id": "override-invalid",
                    "occurred_at": "2026-10-05T14:00:00Z",
                    "code": "unknown",
                }
            ],
            "renewed",
        ),
        ([], [], None),
    ],
)
def test_effective_disposition_uses_latest_valid_override_then_set(
    sets, overrides, expected
):
    assert effective_disposition(
        sets,
        overrides,
        {"renewed", "not_interested"},
    ) == expected


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        ([], "yet_to_reach_out"),
        (
            [
                {
                    "effective_disposition": "renewed",
                    "telephony_failed": True,
                }
            ],
            "completed",
        ),
        (
            [
                {"effective_disposition": None, "telephony_failed": True},
                {"effective_disposition": None, "telephony_failed": True},
            ],
            "in_progress_telephony_failed",
        ),
        (
            [
                {"effective_disposition": None, "telephony_failed": True},
                {"effective_disposition": None, "telephony_failed": False},
            ],
            "in_progress_dialed",
        ),
    ],
)
def test_compute_bucket_checks_rules_in_priority_order(calls, expected):
    assert compute_bucket(calls, {"renewed"}) == expected


def _insert_file(conn, kind, relpath, counters):
    cursor = conn.execute(
        """
        INSERT INTO ingested_files (
            kind, relpath, sha256, counters_json, ingested_at
        ) VALUES (?, ?, ?, ?, '2026-11-13T00:00:00+00:00')
        """,
        (kind, relpath, f"sha-{relpath}", json.dumps(counters)),
    )
    return cursor.lastrowid


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.reader(csv_file))


def _insert_lead(conn, key, external_ref, batch_id, phone, lead_id):
    conn.execute(
        """
        INSERT INTO leads (
            id, workflow_id, external_ref, batch_id, phone_hash,
            phone_last4, attrs_json
        ) VALUES (?, 'motor_renewal', ?, ?, ?, ?, '{}')
        """,
        (
            lead_id,
            external_ref,
            batch_id,
            phone_hash(key, phone),
            phone[-4:],
        ),
    )
    return phone_hash(key, phone)


def _insert_event(
    conn,
    file_id,
    event_id,
    event_type,
    call_id,
    occurred_at_utc,
    phone_digest,
    payload=None,
):
    conn.execute(
        """
        INSERT INTO events (
            event_id, event_type, workflow_id, call_id, occurred_at_utc,
            phone_hash, payload_json, file_id
        ) VALUES (?, ?, 'motor_renewal', ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            call_id,
            occurred_at_utc,
            phone_digest,
            json.dumps(payload or {}),
            file_id,
        ),
    )


def _seed_report_database(conn, key):
    _insert_file(
        conn,
        "leads",
        "drop_1/leads.csv",
        {
            "rows_total": 6,
            "rejected": 1,
            "new_leads": 4,
            "merged": 1,
            "warnings": 2,
        },
    )
    event_file_id = _insert_file(
        conn,
        "events",
        "drop_1/events.jsonl",
        {
            "events_total": 14,
            "malformed": 1,
            "duplicate_events_dropped": 1,
        },
    )
    phone_hashes = [
        _insert_lead(conn, key, "POL-001", "batch_001", "+919876543210", 1),
        _insert_lead(conn, key, "POL-002", "batch_002", "+919876543211", 2),
        _insert_lead(conn, key, "POL-003", "batch_001", "+919876543212", 3),
        _insert_lead(conn, key, "POL-004", "batch_003", "+919876543213", 4),
    ]
    lead1_hash, lead2_hash, _, lead4_hash = phone_hashes

    _insert_event(
        conn, event_file_id, "e01", "call.initiated", "call-1",
        "2026-10-05T13:20:21+00:00", lead1_hash,
    )
    _insert_event(
        conn, event_file_id, "e02", "call.connected", "call-1",
        "2026-10-05T13:21:00+00:00", lead1_hash,
    )
    _insert_event(
        conn, event_file_id, "e03", "disposition.set", "call-1",
        "2026-10-05T13:22:00+00:00", lead1_hash,
        {"disposition_code": "renewed"},
    )
    _insert_event(
        conn, event_file_id, "e04", "disposition.override", "call-1",
        "2026-10-05T13:23:00+00:00", None,
        {"disposition_code": "not_interested"},
    )
    _insert_event(
        conn, event_file_id, "e05", "call.initiated", "call-2",
        "2026-10-06T04:00:00+00:00", lead1_hash,
    )
    _insert_event(
        conn, event_file_id, "e06", "disposition.set", "call-2",
        "2026-10-06T04:05:00+00:00", lead1_hash,
        {"disposition_code": "renewed"},
    )
    _insert_event(
        conn, event_file_id, "e07", "call.initiated", "call-3",
        "2026-10-05T13:00:00+00:00", lead2_hash,
    )
    _insert_event(
        conn, event_file_id, "e08", "call.telephony_failed", "call-3",
        "2026-10-05T13:01:00+00:00", lead2_hash,
    )
    _insert_event(
        conn, event_file_id, "e09", "disposition.set", "call-3",
        "2026-10-05T13:02:00+00:00", lead2_hash,
        {"disposition_code": "not_in_catalogue"},
    )
    _insert_event(
        conn, event_file_id, "e10", "call.initiated", "call-4",
        "2026-10-05T16:00:00+00:00", lead4_hash,
    )
    _insert_event(
        conn, event_file_id, "e11", "call.connected", "call-4",
        "2026-10-05T16:01:00+00:00", lead4_hash,
    )
    _insert_event(
        conn, event_file_id, "e12", "disposition.override", "missing-call",
        "2026-10-05T16:02:00+00:00", None,
        {"disposition_code": "not_in_catalogue"},
    )
    conn.execute(
        """
        INSERT INTO lead_rejections (
            workflow_id, batch_id, row_number, reason
        ) VALUES ('motor_renewal', 'batch_001', 5, 'missing_required:phone')
        """
    )
    conn.commit()


def test_write_reports_obeys_contract_and_is_byte_identical(
    tmp_path,
):
    database_path = tmp_path / "report.sqlite"
    conn = connect(database_path)
    init_schema(conn)
    key = bytes(range(32))
    _seed_report_database(conn, key)
    dispositions = load_dispositions(DISPOSITIONS_PATH)
    out_dir = tmp_path / "reports"

    write_reports(conn, dispositions, out_dir)
    first_run = {
        path.name: path.read_bytes()
        for path in sorted(out_dir.iterdir())
    }
    write_reports(conn, dispositions, out_dir)
    second_run = {
        path.name: path.read_bytes()
        for path in sorted(out_dir.iterdir())
    }
    conn.close()

    assert first_run == second_run
    assert set(first_run) == {
        "batch_summary.csv",
        "daily_overview.csv",
        "lead_report.csv",
        "rejections.csv",
        "data_quality.json",
    }

    batch_rows = _read_csv(out_dir / "batch_summary.csv")
    assert batch_rows == [
        [
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ],
        ["motor_renewal", "batch_001", "2", "1", "0", "0", "1"],
        ["motor_renewal", "batch_002", "1", "0", "1", "0", "0"],
        ["motor_renewal", "batch_003", "1", "0", "0", "1", "0"],
    ]
    daily_rows = _read_csv(out_dir / "daily_overview.csv")
    assert daily_rows[0] == [
        "workflow_id",
        "date",
        "total_calls",
        "connected_calls",
        "connectivity_rate",
        "success_leads",
    ]
    assert daily_rows[1:] == [
        ["motor_renewal", "2026-10-05", "3", "2", "0.6667", "0"],
        ["motor_renewal", "2026-10-06", "1", "0", "0.0000", "1"],
    ]
    lead_rows = _read_csv(out_dir / "lead_report.csv")
    assert lead_rows[0] == [
        "workflow_id",
        "external_ref",
        "batch_id",
        "phone_masked",
        "bucket",
        "total_calls",
        "connected_calls",
        "last_call_at",
        "current_disposition",
        "ever_success",
    ]
    assert lead_rows[1:] == [
        [
            "motor_renewal", "POL-001", "batch_001", "+91******3210",
            "completed", "2", "1", "2026-10-06T09:30:00+05:30",
            "renewed", "true",
        ],
        [
            "motor_renewal", "POL-002", "batch_002", "+91******3211",
            "in_progress_telephony_failed", "1", "0",
            "2026-10-05T18:30:00+05:30", "", "false",
        ],
        [
            "motor_renewal", "POL-003", "batch_001", "+91******3212",
            "yet_to_reach_out", "0", "0", "", "", "false",
        ],
        [
            "motor_renewal", "POL-004", "batch_003", "+91******3213",
            "in_progress_dialed", "1", "1", "2026-10-05T21:30:00+05:30",
            "", "false",
        ],
    ]
    rejection_rows = _read_csv(out_dir / "rejections.csv")
    assert rejection_rows == [
        ["workflow_id", "batch_id", "row_number", "reason"],
        ["motor_renewal", "batch_001", "5", "missing_required:phone"],
    ]
    quality = json.loads((out_dir / "data_quality.json").read_text())
    assert quality == {
        "lead_rows_total": 6,
        "lead_rows_rejected": 1,
        "rejections_by_reason": {"missing_required:phone": 1},
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 2,
        "events_total": 14,
        "malformed_events": 1,
        "duplicate_events_dropped": 1,
        "orphan_events": 1,
        "unknown_disposition_events": 1,
    }
