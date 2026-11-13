"""Ingest and normalize workflow lead CSV files into SQLite."""

import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from solution.normalize import norm_phone, parse_date, parse_decimal, parse_enum
from solution.pii import last4, phone_hash
from solution.schema import Field, WorkflowSchema


@dataclass
class _LeadState:
    lead_id: int | None
    external_ref: str
    batch_id: str
    phone_last4: str
    attrs: dict[str, str | None]


def _parse_value(field: Field, raw: str) -> object | None:
    if field.type == "string":
        return raw.strip()
    if field.type == "phone":
        return norm_phone(raw)
    if field.type == "date":
        return parse_date(raw)
    if field.type == "decimal":
        return parse_decimal(raw)
    if field.type == "enum":
        return parse_enum(raw, field.values)
    raise ValueError(f"Unsupported field type {field.type!r} for {field.name!r}")


def _storage_value(field: Field, value: object) -> str:
    if field.type == "date" and isinstance(value, date):
        return value.isoformat()
    if field.type == "decimal" and isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, str):
        return value
    raise ValueError(f"Cannot serialize value for field {field.name!r}")


def _clean_row(
    row: list[str],
    header_map: dict[str, int],
    fields: tuple[Field, ...],
) -> tuple[dict[str, str | None], str | None, int]:
    cleaned: dict[str, str | None] = {}
    warnings = 0

    for field in fields:
        column_index = header_map.get(field.name)
        raw = (
            row[column_index].strip()
            if column_index is not None and column_index < len(row)
            else ""
        )
        if not raw:
            cleaned[field.name] = None
            if field.required:
                return cleaned, f"missing_required:{field.name}", 0
            continue

        value = _parse_value(field, raw)
        if field.type == "phone" and value is None:
            return cleaned, "invalid_phone", 0
        if value is None:
            cleaned[field.name] = None
            if not field.required:
                warnings += 1
        else:
            cleaned[field.name] = _storage_value(field, value)

    return cleaned, None, warnings


def _apply_defaults(
    attrs: dict[str, str | None],
    fields: tuple[Field, ...],
) -> None:
    for field in fields:
        if attrs.get(field.name) is None and field.default is not None:
            value = _parse_value(field, field.default)
            if value is None:
                raise ValueError(
                    f"Invalid default {field.default!r} for field {field.name!r}"
                )
            attrs[field.name] = _storage_value(field, value)


def _load_existing_state(
    conn: sqlite3.Connection,
    workflow_id: str,
    phone_digest: str,
    attr_fields: tuple[Field, ...],
) -> _LeadState | None:
    row = conn.execute(
        """
        SELECT id, external_ref, batch_id, phone_last4, attrs_json
        FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (workflow_id, phone_digest),
    ).fetchone()
    if row is None:
        return None

    stored_attrs = json.loads(row["attrs_json"])
    if not isinstance(stored_attrs, dict):
        raise ValueError("Stored lead attrs_json must contain a JSON object")
    attrs = {
        field.name: stored_attrs.get(field.name)
        for field in attr_fields
    }
    return _LeadState(
        lead_id=row["id"],
        external_ref=row["external_ref"],
        batch_id=row["batch_id"],
        phone_last4=row["phone_last4"],
        attrs=attrs,
    )


def _encode_attrs(attrs: dict[str, str | None]) -> str:
    return json.dumps(
        attrs,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def ingest_lead_file(
    conn: sqlite3.Connection,
    key: bytes,
    schema: WorkflowSchema,
    csv_path: str | Path,
    relpath: str,
) -> dict[str, int]:
    """Ingest one lead CSV atomically and return its row counters.

    Example: ``ingest_lead_file(conn, key, schema, "batch.csv", "batch.csv")``
    returns counters including ``rows_total``, ``new_leads``, and ``rejected``.
    """
    phone_fields = tuple(field for field in schema.fields if field.type == "phone")
    if len(phone_fields) != 1:
        raise ValueError(
            f"Workflow {schema.workflow_id!r} must have exactly one phone field"
        )
    phone_field = phone_fields[0]
    reference_field = schema.reference_field
    attr_fields = tuple(
        field
        for field in schema.fields
        if not field.required and not field.pii and not field.reference
    )
    batch_id = Path(csv_path).stem
    content = Path(csv_path).read_bytes()
    file_sha256 = hashlib.sha256(content).hexdigest()

    counters = {
        "rows_total": 0,
        "rejected": 0,
        "new_leads": 0,
        "merged": 0,
        "warnings": 0,
    }
    rejection_rows: list[tuple[str, str, int, str]] = []
    leads_by_identity: dict[tuple[str, str], _LeadState] = {}

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_file = conn.execute(
            "SELECT 1 FROM ingested_files WHERE relpath = ? AND sha256 = ?",
            (relpath, file_sha256),
        ).fetchone()
        if existing_file is not None:
            return counters

        # A changed file with the same batch stem replaces its prior rejection rows.
        conn.execute(
            "DELETE FROM lead_rejections WHERE workflow_id = ? AND batch_id = ?",
            (schema.workflow_id, batch_id),
        )

        with io.TextIOWrapper(
            io.BytesIO(content),
            encoding="utf-8-sig",
            newline="",
        ) as csv_file:
            reader = csv.reader(csv_file)
            headers = next(reader, [])
            header_map = schema.map_headers(headers)
            row_number = 0

            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                row_number += 1
                counters["rows_total"] += 1

                cleaned, reason, row_warnings = _clean_row(
                    row,
                    header_map,
                    schema.fields,
                )
                if reason is not None:
                    counters["rejected"] += 1
                    rejection_rows.append(
                        (schema.workflow_id, batch_id, row_number, reason)
                    )
                    continue

                canonical_phone = cleaned[phone_field.name]
                external_ref = cleaned[reference_field.name]
                if canonical_phone is None or external_ref is None:
                    raise ValueError("Validated lead is missing its phone or reference")

                digest = phone_hash(key, canonical_phone)
                identity = (schema.workflow_id, digest)
                state = leads_by_identity.get(identity)
                if state is None:
                    state = _load_existing_state(
                        conn,
                        schema.workflow_id,
                        digest,
                        attr_fields,
                    )
                    if state is None:
                        state = _LeadState(
                            lead_id=None,
                            external_ref=external_ref,
                            batch_id=batch_id,
                            phone_last4=last4(canonical_phone),
                            attrs={field.name: None for field in attr_fields},
                        )
                        counters["new_leads"] += 1
                    else:
                        counters["merged"] += 1
                    leads_by_identity[identity] = state
                else:
                    counters["merged"] += 1

                state.external_ref = external_ref
                for field in attr_fields:
                    value = cleaned.get(field.name)
                    if value is not None:
                        state.attrs[field.name] = value
                _apply_defaults(state.attrs, attr_fields)
                counters["warnings"] += row_warnings

        if rejection_rows:
            conn.executemany(
                """
                INSERT INTO lead_rejections (workflow_id, batch_id, row_number, reason)
                VALUES (?, ?, ?, ?)
                """,
                rejection_rows,
            )

        new_leads = []
        updated_leads = []
        for (workflow_id, digest), state in leads_by_identity.items():
            attrs_json = _encode_attrs(state.attrs)
            if state.lead_id is None:
                new_leads.append(
                    (
                        workflow_id,
                        state.external_ref,
                        state.batch_id,
                        digest,
                        state.phone_last4,
                        attrs_json,
                    )
                )
            else:
                updated_leads.append(
                    (state.external_ref, attrs_json, state.lead_id)
                )

        if new_leads:
            conn.executemany(
                """
                INSERT INTO leads (
                    workflow_id, external_ref, batch_id, phone_hash,
                    phone_last4, attrs_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                new_leads,
            )
        if updated_leads:
            conn.executemany(
                """
                UPDATE leads
                SET external_ref = ?, attrs_json = ?
                WHERE id = ?
                """,
                updated_leads,
            )

        conn.execute(
            """
            INSERT INTO ingested_files (
                kind, relpath, sha256, counters_json, ingested_at
            ) VALUES ('leads', ?, ?, ?, ?)
            """,
            (
                relpath,
                file_sha256,
                json.dumps(counters, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    return counters
