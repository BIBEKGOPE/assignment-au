import csv
import json
import sqlite3
from pathlib import Path

import pytest

from solution.db import connect, init_schema
from solution.ingest_leads import ingest_lead_file
from solution.schema import load_schemas


SCHEMAS_DIR = (
    Path(__file__).resolve().parents[1] / "assignment-a-data" / "schemas"
)
HEADERS = [
    "Customer Name",
    "Mobile No",
    "Policy No",
    "Expiry Date",
    "Premium",
    "Language",
]


@pytest.fixture
def ingestion_env(tmp_path):
    connection = connect(tmp_path / "pipeline.sqlite")
    init_schema(connection)
    schema = load_schemas(SCHEMAS_DIR)["motor_renewal"]
    try:
        yield connection, bytes(range(32)), schema
    finally:
        connection.close()


def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    return path


def _ingest(connection, key, schema, path):
    relpath = f"drop_1/leads/motor_renewal/{path.name}"
    return ingest_lead_file(connection, key, schema, path, relpath)


def test_rejections_follow_schema_order_and_have_one_reason(
    ingestion_env, tmp_path
):
    connection, key, schema = ingestion_env
    path = _write_csv(
        tmp_path / "batch_001.csv",
        [
            ["", "not a phone", "", "", "", ""],
            ["Alice", "123", "", "", "", ""],
            ["Bob", "9876543210", "", "", "", ""],
        ],
    )

    counters = _ingest(connection, key, schema, path)
    rejections = connection.execute(
        """
        SELECT row_number, reason
        FROM lead_rejections
        ORDER BY row_number
        """
    ).fetchall()

    assert [(row["row_number"], row["reason"]) for row in rejections] == [
        (1, "missing_required:customer_name"),
        (2, "invalid_phone"),
        (3, "missing_required:policy_number"),
    ]
    assert counters["rejected"] == 3
    assert counters["warnings"] == 0


def test_blank_rows_are_skipped_and_do_not_advance_row_number(
    ingestion_env, tmp_path
):
    connection, key, schema = ingestion_env
    path = _write_csv(
        tmp_path / "batch_001.csv",
        [
            ["Alice", "9876543210", "POL-1", "", "", ""],
            [" ", "", "  ", "", "", ""],
            [],
            ["Bob", "9876543211", "", "", "", ""],
        ],
    )

    counters = _ingest(connection, key, schema, path)
    rejection = connection.execute(
        "SELECT row_number, reason FROM lead_rejections"
    ).fetchone()

    assert counters["rows_total"] == 2
    assert counters["new_leads"] == 1
    assert rejection["row_number"] == 2
    assert rejection["reason"] == "missing_required:policy_number"


def test_optional_field_warnings_count_only_for_valid_rows(
    ingestion_env, tmp_path
):
    connection, key, schema = ingestion_env
    path = _write_csv(
        tmp_path / "batch_001.csv",
        [
            ["Alice", "9876543210", "POL-1", "not a date", "N/A", "xx"],
            ["", "9876543211", "POL-2", "not a date", "N/A", "xx"],
        ],
    )

    counters = _ingest(connection, key, schema, path)
    lead = connection.execute("SELECT attrs_json FROM leads").fetchone()
    attrs = json.loads(lead["attrs_json"])

    assert counters["rejected"] == 1
    assert counters["warnings"] == 3
    assert attrs == {
        "policy_expiry": None,
        "preferred_language": "en",
        "premium_amount": None,
    }


def test_duplicate_leads_keep_first_batch_and_merge_non_null_values(
    ingestion_env, tmp_path
):
    connection, key, schema = ingestion_env
    first_path = _write_csv(
        tmp_path / "batch_001.csv",
        [["Alice", "9876543210", "POL-1", "", "100", "hi"]],
    )
    second_path = _write_csv(
        tmp_path / "batch_002.csv",
        [["Alicia", "9876543210", "POL-2", "", "200", ""]],
    )

    first = _ingest(connection, key, schema, first_path)
    second = ingest_lead_file(
        connection,
        key,
        schema,
        second_path,
        "drop_2/leads/motor_renewal/batch_002.csv",
    )
    lead = connection.execute(
        """
        SELECT external_ref, batch_id, phone_last4, attrs_json
        FROM leads
        """
    ).fetchone()
    attrs = json.loads(lead["attrs_json"])

    assert first["new_leads"] == 1
    assert second["merged"] == 1
    assert second["new_leads"] == 0
    assert lead["batch_id"] == "batch_001"
    assert lead["external_ref"] == "POL-2"
    assert lead["phone_last4"] == "3210"
    assert attrs["premium_amount"] == "200.00"
    assert attrs["preferred_language"] == "hi"
    assert "customer_name" not in attrs
    assert "phone" not in attrs


def test_reingesting_same_file_changes_nothing(ingestion_env, tmp_path):
    connection, key, schema = ingestion_env
    path = _write_csv(
        tmp_path / "batch_001.csv",
        [["Alice", "9876543210", "POL-1", "", "100", ""]],
    )

    first = _ingest(connection, key, schema, path)
    before = (
        connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0],
    )
    stored_file = connection.execute(
        "SELECT kind, counters_json FROM ingested_files"
    ).fetchone()
    repeated = _ingest(connection, key, schema, path)
    after = (
        connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM ingested_files").fetchone()[0],
    )

    assert first["new_leads"] == 1
    assert stored_file["kind"] == "leads"
    assert json.loads(stored_file["counters_json"]) == first
    assert repeated == {
        "rows_total": 0,
        "rejected": 0,
        "new_leads": 0,
        "merged": 0,
        "warnings": 0,
    }
    assert after == before


def test_rows_total_equals_rejected_new_and_merged_rows(
    ingestion_env, tmp_path
):
    connection, key, schema = ingestion_env
    path = _write_csv(
        tmp_path / "batch_001.csv",
        [
            ["Alice", "9876543210", "POL-1", "", "", ""],
            ["Alicia", "9876543210", "POL-2", "", "", ""],
            ["Bob", "9876543211", "", "", "", ""],
        ],
    )

    counters = _ingest(connection, key, schema, path)

    assert counters["rows_total"] == (
        counters["rejected"] + counters["new_leads"] + counters["merged"]
    )


def test_file_ingestion_rolls_back_all_changes_on_database_failure(
    ingestion_env, tmp_path
):
    connection, key, schema = ingestion_env
    path = _write_csv(
        tmp_path / "batch_001.csv",
        [
            ["Alice", "9876543210", "POL-1", "", "", ""],
            ["Bob", "9876543211", "", "", "", ""],
        ],
    )
    connection.execute(
        """
        CREATE TRIGGER reject_ingested_file
        BEFORE INSERT ON ingested_files
        WHEN NEW.relpath = 'drop_1/leads/motor_renewal/batch_001.csv'
        BEGIN
            SELECT RAISE(ABORT, 'test transaction rollback');
        END
        """
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="test transaction rollback"):
        _ingest(connection, key, schema, path)

    assert connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM lead_rejections"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM ingested_files"
    ).fetchone()[0] == 0
