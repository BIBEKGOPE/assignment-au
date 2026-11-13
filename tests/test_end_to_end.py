import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from solution.db import connect, init_schema
from solution.ingest_events import ingest_event_file
from solution.ingest_leads import ingest_lead_file
from solution.normalize import norm_phone
from solution.pipeline import ingest_drop
from solution.report import write_reports
from solution.schema import WorkflowSchema, load_dispositions, load_schemas


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = ROOT / "assignment-a-data" / "schemas"
REPORT_NAMES = (
    "batch_summary.csv",
    "daily_overview.csv",
    "lead_report.csv",
    "rejections.csv",
    "data_quality.json",
)
SCHEMAS = load_schemas(SCHEMAS_DIR)
DISPOSITIONS = load_dispositions(SCHEMAS_DIR / "dispositions.csv")


@pytest.fixture(scope="module", params=[42, 7], ids=["seed-42", "seed-7"])
def generated_drops(tmp_path_factory, request):
    seed = request.param
    data_dir = tmp_path_factory.mktemp(f"generated-{seed}") / "data"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "assignment-a-data" / "tools" / "generate_data.py"),
            "--seed",
            str(seed),
            "--scale",
            "small",
            "--out",
            str(data_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return seed, data_dir


def _new_database(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_schema(conn)
    return db_path, conn, bytes(range(32))


def _ingest_all(conn, key, data_dir):
    for drop in (data_dir / "drop_1", data_dir / "drop_2"):
        ingest_drop(conn, key, SCHEMAS, drop)


def _report_bytes(conn, output_dir):
    write_reports(conn, DISPOSITIONS, output_dir)
    return {
        name: (output_dir / name).read_bytes()
        for name in REPORT_NAMES
    }


def _row_counts(conn):
    tables = ("leads", "lead_rejections", "events", "ingested_files")
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }


def _valid_lead_phones(csv_path: Path, schema: WorkflowSchema):
    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        header_map = schema.map_headers(next(reader, []))
        phone_field = next(field for field in schema.fields if field.type == "phone")
        required_fields = [field for field in schema.fields if field.required]

        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            values = {}
            for field in required_fields:
                index = header_map.get(field.name)
                values[field.name] = (
                    row[index].strip() if index is not None and index < len(row) else ""
                )
            if any(not values[field.name] for field in required_fields):
                continue
            phone = norm_phone(values[phone_field.name])
            if phone is not None:
                yield phone


def _find_early_event_and_later_lead(data_dir: Path):
    first_drop = data_dir / "drop_1"
    second_drop = data_dir / "drop_2"
    first_drop_leads = set()
    later_leads = {}

    for workflow_dir in sorted((first_drop / "leads").iterdir()):
        schema = SCHEMAS[workflow_dir.name]
        for csv_path in sorted(workflow_dir.glob("*.csv")):
            first_drop_leads.update(
                (workflow_dir.name, phone)
                for phone in _valid_lead_phones(csv_path, schema)
            )

    for workflow_dir in sorted((second_drop / "leads").iterdir()):
        schema = SCHEMAS[workflow_dir.name]
        for csv_path in sorted(workflow_dir.glob("*.csv")):
            for phone in _valid_lead_phones(csv_path, schema):
                later_leads.setdefault((workflow_dir.name, phone), csv_path)

    for event_path in sorted((first_drop / "events").glob("*.jsonl")):
        with event_path.open(encoding="utf-8", newline="") as event_file:
            for line in event_file:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                phone = event.get("phone")
                canonical_phone = norm_phone(phone) if isinstance(phone, str) else None
                identity = (event.get("workflow_id"), canonical_phone)
                if (
                    event.get("event_id")
                    and canonical_phone is not None
                    and identity not in first_drop_leads
                    and identity in later_leads
                ):
                    return event, later_leads[identity], identity[0], event_path
    return None


def _copy_combined_drop(combined_dir: Path, data_dir: Path) -> None:
    for drop_name in ("drop_1", "drop_2"):
        drop = data_dir / drop_name
        for kind, pattern in (("leads", "*.csv"), ("events", "*.jsonl")):
            for source in sorted((drop / kind).rglob(pattern)):
                relative_path = source.relative_to(drop)
                destination = combined_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)


def test_ingest_drop_twice_is_idempotent(generated_drops, tmp_path):
    _, data_dir = generated_drops
    _, conn, key = _new_database(tmp_path)
    drop = data_dir / "drop_1"

    ingest_drop(conn, key, SCHEMAS, drop)
    counts_before = _row_counts(conn)
    first_reports = _report_bytes(conn, tmp_path / "reports")
    second_counters = ingest_drop(conn, key, SCHEMAS, drop)
    counts_after = _row_counts(conn)
    second_reports = _report_bytes(conn, tmp_path / "reports")
    conn.close()

    assert counts_after == counts_before
    assert all(value == 0 for value in second_counters.values())
    assert second_reports == first_reports


def test_incremental_drops_match_combined_drop(generated_drops, tmp_path):
    _, data_dir = generated_drops
    _, incremental_conn, key = _new_database(tmp_path / "incremental")
    ingest_drop(incremental_conn, key, SCHEMAS, data_dir / "drop_1")
    ingest_drop(incremental_conn, key, SCHEMAS, data_dir / "drop_2")
    incremental_reports = _report_bytes(
        incremental_conn,
        tmp_path / "incremental-reports",
    )
    incremental_conn.close()

    combined_dir = tmp_path / "combined"
    _copy_combined_drop(combined_dir, data_dir)
    _, combined_conn, combined_key = _new_database(tmp_path / "combined-db")
    ingest_drop(combined_conn, combined_key, SCHEMAS, combined_dir)
    combined_reports = _report_bytes(combined_conn, tmp_path / "combined-reports")
    combined_conn.close()

    assert combined_reports == incremental_reports


def test_orphan_event_links_after_later_lead_ingestion(generated_drops, tmp_path):
    _, data_dir = generated_drops
    candidate = _find_early_event_and_later_lead(data_dir)
    assert candidate is not None, "generated data should contain an event before its lead"
    event, lead_csv, workflow_id, source_event_file = candidate
    schema = SCHEMAS[workflow_id]
    _, conn, key = _new_database(tmp_path)

    early_event_path = tmp_path / "early-event.jsonl"
    early_event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    ingest_event_file(conn, key, early_event_path, "drop_1/events/early-event.jsonl")
    before = json.loads(_report_bytes(conn, tmp_path / "before")["data_quality.json"])

    lead_drop = data_dir / "drop_2"
    relpath = f"{lead_drop.name}/{lead_csv.relative_to(lead_drop).as_posix()}"
    ingest_lead_file(conn, key, schema, lead_csv, relpath)
    after = json.loads(_report_bytes(conn, tmp_path / "after")["data_quality.json"])
    conn.close()

    assert source_event_file.parent.name == "events"
    assert before["orphan_events"] == 1
    assert after["orphan_events"] == 0


def test_phone_digits_are_not_stored_plaintext(generated_drops, tmp_path):
    _, data_dir = generated_drops
    db_path, conn, key = _new_database(tmp_path)
    _ingest_all(conn, key, data_dir)

    phones = set()
    for drop_name in ("drop_1", "drop_2"):
        leads_dir = data_dir / drop_name / "leads"
        for workflow_dir in sorted(leads_dir.iterdir()):
            schema = SCHEMAS[workflow_dir.name]
            for csv_path in sorted(workflow_dir.glob("*.csv")):
                phones.update(_valid_lead_phones(csv_path, schema))
        events_dir = data_dir / drop_name / "events"
        for event_path in sorted(events_dir.glob("*.jsonl")):
            with event_path.open(encoding="utf-8", newline="") as event_file:
                for line in event_file:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    phone = event.get("phone")
                    if isinstance(phone, str):
                        canonical_phone = norm_phone(phone)
                        if canonical_phone is not None:
                            phones.add(canonical_phone)

    conn.close()
    database_files = [
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
        Path(f"{db_path}-journal"),
    ]
    database_bytes = b"".join(
        path.read_bytes() for path in database_files if path.exists()
    )

    for phone in phones:
        assert phone[-10:].encode("ascii") not in database_bytes
