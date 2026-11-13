import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from solution.db import connect
from solution.pipeline import ingest_drop
from solution.schema import load_schemas


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = ROOT / "assignment-a-data" / "schemas"


def test_ingest_drop_processes_sorted_leads_before_sorted_events(
    tmp_path, monkeypatch
):
    drop = tmp_path / "drop_7"
    files = [
        drop / "leads" / "policy_feedback" / "batch.csv",
        drop / "leads" / "motor_renewal" / "z.csv",
        drop / "leads" / "motor_renewal" / "a.csv",
        drop / "events" / "z.jsonl",
        drop / "events" / "a.jsonl",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    calls = []

    def fake_ingest_lead(conn, key, schema, path, relpath):
        calls.append(("lead", schema.workflow_id, path.name, relpath))
        return {
            "rows_total": 1,
            "rejected": 0,
            "new_leads": 1,
            "merged": 0,
            "warnings": 0,
        }

    def fake_ingest_event(conn, key, path, relpath):
        calls.append(("event", path.name, relpath))
        return {
            "events_total": 1,
            "malformed": 0,
            "duplicate_events_dropped": 0,
        }

    monkeypatch.setattr("solution.pipeline.ingest_lead_file", fake_ingest_lead)
    monkeypatch.setattr("solution.pipeline.ingest_event_file", fake_ingest_event)
    conn = connect(tmp_path / "pipeline.sqlite")
    try:
        counters = ingest_drop(conn, b"key", load_schemas(SCHEMAS_DIR), drop)
    finally:
        conn.close()

    assert calls == [
        ("lead", "motor_renewal", "a.csv", "drop_7/leads/motor_renewal/a.csv"),
        ("lead", "motor_renewal", "z.csv", "drop_7/leads/motor_renewal/z.csv"),
        (
            "lead",
            "policy_feedback",
            "batch.csv",
            "drop_7/leads/policy_feedback/batch.csv",
        ),
        ("event", "a.jsonl", "drop_7/events/a.jsonl"),
        ("event", "z.jsonl", "drop_7/events/z.jsonl"),
    ]
    assert counters == {
        "rows_total": 3,
        "rejected": 0,
        "new_leads": 3,
        "merged": 0,
        "warnings": 0,
        "events_total": 2,
        "malformed": 0,
        "duplicate_events_dropped": 0,
    }


def test_ingest_drop_rejects_missing_directory(tmp_path):
    conn = connect(tmp_path / "pipeline.sqlite")
    try:
        with pytest.raises(FileNotFoundError, match="Drop directory does not exist"):
            ingest_drop(conn, b"key", {}, tmp_path / "missing")
    finally:
        conn.close()


def test_python_module_cli_ingests_and_reports(tmp_path):
    drop = tmp_path / "data" / "drop_1"
    leads_dir = drop / "leads" / "motor_renewal"
    events_dir = drop / "events"
    leads_dir.mkdir(parents=True)
    events_dir.mkdir()
    with (leads_dir / "batch_001.csv").open(
        "w", encoding="utf-8", newline=""
    ) as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["Customer Name", "Mobile No", "Policy No"])
        writer.writerow(["Asha", "9876543210", "POL-001"])
    with (events_dir / "events_0001.jsonl").open(
        "w", encoding="utf-8", newline=""
    ) as jsonl_file:
        jsonl_file.write(
            json.dumps(
                {
                    "event_id": "evt-1",
                    "event_type": "call.initiated",
                    "workflow_id": "motor_renewal",
                    "call_id": "call-1",
                    "occurred_at": "2026-10-05T13:20:21Z",
                    "phone": "9876543210",
                    "payload": {},
                }
            )
            + "\n"
        )

    db_path = tmp_path / "ledger.sqlite"
    ingest_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "solution",
            "ingest",
            str(drop),
            "--db",
            str(db_path),
            "--schemas",
            str(SCHEMAS_DIR),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ingest_result.returncode == 0, ingest_result.stderr
    assert "rows_total=1" in ingest_result.stdout
    assert "events_total=1" in ingest_result.stdout

    reports_dir = tmp_path / "reports"
    report_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "solution",
            "report",
            "--out",
            str(reports_dir),
            "--db",
            str(db_path),
            "--schemas",
            str(SCHEMAS_DIR),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert report_result.returncode == 0, report_result.stderr
    assert len(list(reports_dir.iterdir())) == 5


def test_cli_missing_drop_exits_nonzero_with_clear_message(tmp_path):
    db_path = tmp_path / "must-not-be-created.sqlite"
    missing_drop = tmp_path / "missing-drop"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "solution",
            "ingest",
            str(missing_drop),
            "--db",
            str(db_path),
            "--schemas",
            str(SCHEMAS_DIR),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Error: drop directory does not exist:" in result.stderr
    assert not db_path.exists()
