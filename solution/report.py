"""Build deterministic CSV and JSON reports from the interaction ledger."""

import csv
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from solution.normalize import IST, parse_timestamp, to_ist
from solution.pii import mask
from solution.schema import Disposition


@dataclass
class _Call:
    workflow_id: str
    lead_id: int
    call_id: str | None
    initiated_at: datetime | None = None
    initiated_event_id: str = ""
    connected: bool = False
    telephony_failed: bool = False
    sets: list[dict[str, object]] = field(default_factory=list)
    overrides: list[dict[str, object]] = field(default_factory=list)
    effective_disposition: str | None = None


@dataclass
class _DailyMetrics:
    total_calls: int = 0
    connected_calls: int = 0
    success_leads: set[int] = field(default_factory=set)


class _DataQuality(TypedDict):
    lead_rows_total: int
    lead_rows_rejected: int
    rejections_by_reason: dict[str, int]
    duplicate_lead_rows_merged: int
    field_warnings: int
    events_total: int
    malformed_events: int
    duplicate_events_dropped: int
    orphan_events: int
    unknown_disposition_events: int


def _event_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
        if moment.tzinfo is None or moment.utcoffset() is None:
            moment = moment.replace(tzinfo=IST)
    elif isinstance(value, str):
        moment = parse_timestamp(value)
    else:
        return None
    return moment.astimezone(timezone.utc) if moment is not None else None


def effective_disposition(
    sets: Iterable[Mapping[str, object]],
    overrides: Iterable[Mapping[str, object]],
    valid_codes: set[str],
) -> str | None:
    """Return the latest valid override, or latest valid set if no override exists.

    Event mappings contain ``event_id``, ``occurred_at`` and ``code``.
    Example: ``effective_disposition([], [event], {"renewed"})`` returns the
    event's code when it is a valid, latest override.
    """

    def latest_code(
        events: Iterable[Mapping[str, object]],
    ) -> str | None:
        candidates: list[tuple[datetime, str, str]] = []
        for event in events:
            code = event.get("code")
            occurred_at = _event_time(
                event.get("occurred_at", event.get("occurred_at_utc"))
            )
            if (
                not isinstance(code, str)
                or code not in valid_codes
                or occurred_at is None
            ):
                continue
            event_id = event.get("event_id")
            candidates.append((occurred_at, str(event_id or ""), code))
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]

    latest_override = latest_code(overrides)
    if latest_override is not None:
        return latest_override
    return latest_code(sets)


def compute_bucket(
    calls: Iterable[Mapping[str, object]],
    terminal_codes: set[str],
) -> str:
    """Classify a lead from its calls, checking bucket rules in priority order.

    Each call mapping has ``effective_disposition`` and ``telephony_failed`` keys.
    Example: ``compute_bucket([], {"renewed"})`` returns ``"yet_to_reach_out"``.
    """
    call_list = list(calls)
    if not call_list:
        return "yet_to_reach_out"
    if any(
        isinstance(code := call.get("effective_disposition"), str)
        and code in terminal_codes
        for call in call_list
    ):
        return "completed"
    if all(bool(call.get("telephony_failed")) for call in call_list):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def _call_token(call_id: str | None, event_id: str) -> tuple[str, str]:
    if call_id is None:
        return ("event", event_id)
    return ("call", call_id)


def _payload_code(payload_json: str) -> str | None:
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        return None
    code = payload.get("disposition_code")
    return code if isinstance(code, str) else None


def _call_order(call: _Call) -> tuple[datetime, str]:
    if call.initiated_at is None:
        raise ValueError("Cannot sort a call without an initiation time")
    return (
        call.initiated_at,
        call.call_id or call.initiated_event_id,
    )


def _is_successful(
    call: _Call,
    dispositions: Mapping[tuple[str, str], Disposition],
) -> bool:
    if call.effective_disposition is None:
        return False
    disposition = dispositions.get(
        (call.workflow_id, call.effective_disposition)
    )
    return disposition is not None and disposition.is_success


def _write_csv(
    path: Path,
    header: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file, delimiter=",", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _data_quality(
    conn: sqlite3.Connection,
    orphan_events: int,
    unknown_disposition_events: int,
) -> _DataQuality:
    quality: _DataQuality = {
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "rejections_by_reason": {},
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "orphan_events": orphan_events,
        "unknown_disposition_events": unknown_disposition_events,
    }

    for row in conn.execute(
        "SELECT kind, counters_json FROM ingested_files"
    ):
        counters = json.loads(row["counters_json"])
        if row["kind"] == "leads":
            quality["lead_rows_total"] += counters.get("rows_total", 0)
            quality["lead_rows_rejected"] += counters.get("rejected", 0)
            quality["duplicate_lead_rows_merged"] += counters.get("merged", 0)
            quality["field_warnings"] += counters.get("warnings", 0)
        elif row["kind"] == "events":
            quality["events_total"] += counters.get("events_total", 0)
            quality["malformed_events"] += counters.get("malformed", 0)
            quality["duplicate_events_dropped"] += counters.get(
                "duplicate_events_dropped", 0
            )

    quality["rejections_by_reason"] = {
        row["reason"]: row["count"]
        for row in conn.execute(
            """
            SELECT reason, COUNT(*) AS count
            FROM lead_rejections
            GROUP BY reason
            ORDER BY reason
            """
        )
    }
    return quality


def write_reports(
    conn: sqlite3.Connection,
    dispositions: Mapping[tuple[str, str], Disposition],
    out_dir: str | Path,
) -> None:
    """Write all five reports deterministically from the current database state."""
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    lead_rows = conn.execute(
        """
        SELECT id, workflow_id, external_ref, batch_id, phone_hash, phone_last4
        FROM leads
        ORDER BY workflow_id, external_ref
        """
    ).fetchall()
    lead_by_identity = {
        (row["workflow_id"], row["phone_hash"]): row["id"]
        for row in lead_rows
    }
    lead_by_id = {row["id"]: row for row in lead_rows}

    call_id_to_lead: dict[tuple[str, str], int] = {}
    for row in conn.execute(
        """
        SELECT workflow_id, call_id, phone_hash
        FROM events
        WHERE event_type != 'disposition.override'
          AND call_id IS NOT NULL
          AND phone_hash IS NOT NULL
        ORDER BY occurred_at_utc, event_id
        """
    ):
        lead_id = lead_by_identity.get((row["workflow_id"], row["phone_hash"]))
        if lead_id is not None:
            call_id_to_lead.setdefault((row["workflow_id"], row["call_id"]), lead_id)

    valid_codes_by_workflow: dict[str, set[str]] = defaultdict(set)
    terminal_codes_by_workflow: dict[str, set[str]] = defaultdict(set)
    for (workflow_id, code), disposition in dispositions.items():
        valid_codes_by_workflow[workflow_id].add(code)
        if disposition.is_terminal:
            terminal_codes_by_workflow[workflow_id].add(code)

    orphan_events = 0
    unknown_disposition_events = 0
    calls: dict[tuple[str, int, str, str], _Call] = {}

    for row in conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, occurred_at_utc,
               phone_hash, payload_json
        FROM events
        ORDER BY occurred_at_utc, event_id
        """
    ):
        event_type = row["event_type"]
        workflow_id = row["workflow_id"]
        call_id = row["call_id"]
        if event_type == "disposition.override":
            lead_id = (
                call_id_to_lead.get((workflow_id, call_id))
                if call_id is not None
                else None
            )
        else:
            lead_id = lead_by_identity.get((workflow_id, row["phone_hash"]))

        if lead_id is None:
            orphan_events += 1
            continue

        code = None
        if event_type.startswith("disposition."):
            code = _payload_code(row["payload_json"])
            if code is None or (workflow_id, code) not in dispositions:
                unknown_disposition_events += 1

        if event_type not in {
            "call.initiated",
            "call.connected",
            "call.telephony_failed",
            "disposition.set",
            "disposition.override",
        }:
            continue
        if call_id is None and event_type != "call.initiated":
            continue

        call_token = _call_token(call_id, row["event_id"])
        call_key = (workflow_id, lead_id, call_token[0], call_token[1])
        call = calls.get(call_key)
        if call is None:
            call = _Call(
                workflow_id=workflow_id,
                lead_id=lead_id,
                call_id=call_id,
            )
            calls[call_key] = call

        occurred_at = _event_time(row["occurred_at_utc"])
        if occurred_at is None:
            raise ValueError(f"Invalid stored timestamp for event {row['event_id']!r}")

        if event_type == "call.initiated":
            if (
                call.initiated_at is None
                or (occurred_at, row["event_id"])
                < (call.initiated_at, call.initiated_event_id)
            ):
                call.initiated_at = occurred_at
                call.initiated_event_id = row["event_id"]
        elif event_type == "call.connected":
            call.connected = True
        elif event_type == "call.telephony_failed":
            call.telephony_failed = True
        elif event_type in {"disposition.set", "disposition.override"}:
            disposition_event = {
                "event_id": row["event_id"],
                "occurred_at": occurred_at,
                "code": code,
            }
            if event_type == "disposition.override":
                call.overrides.append(disposition_event)
            else:
                call.sets.append(disposition_event)

    calls_by_lead: dict[int, list[_Call]] = defaultdict(list)
    for call in calls.values():
        if call.initiated_at is None:
            continue
        call.effective_disposition = effective_disposition(
            call.sets,
            call.overrides,
            valid_codes_by_workflow[call.workflow_id],
        )
        calls_by_lead[call.lead_id].append(call)

    lead_buckets: dict[int, str] = {}
    for lead_id, lead in lead_by_id.items():
        lead_calls = calls_by_lead[lead_id]
        lead_buckets[lead_id] = compute_bucket(
            [
                {
                    "effective_disposition": call.effective_disposition,
                    "telephony_failed": call.telephony_failed,
                }
                for call in lead_calls
            ],
            terminal_codes_by_workflow[lead["workflow_id"]],
        )

    batch_counts: dict[tuple[str, str], dict[str, int]] = {}
    bucket_names = (
        "yet_to_reach_out",
        "in_progress_telephony_failed",
        "in_progress_dialed",
        "completed",
    )
    for lead in lead_rows:
        batch_key = (lead["workflow_id"], lead["batch_id"])
        counts = batch_counts.setdefault(
            batch_key,
            {"total_leads": 0, **{bucket: 0 for bucket in bucket_names}},
        )
        counts["total_leads"] += 1
        counts[lead_buckets[lead["id"]]] += 1

    _write_csv(
        output_dir / "batch_summary.csv",
        (
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ),
        (
            (workflow_id, batch_id, *(counts[name] for name in (
                "total_leads",
                "yet_to_reach_out",
                "in_progress_telephony_failed",
                "in_progress_dialed",
                "completed",
            )))
            for (workflow_id, batch_id), counts in sorted(batch_counts.items())
        ),
    )

    daily: dict[tuple[str, str], _DailyMetrics] = {}
    for call in calls.values():
        if call.initiated_at is None:
            continue
        local_date = to_ist(call.initiated_at).date().isoformat()
        daily_key = (call.workflow_id, local_date)
        metrics = daily.setdefault(daily_key, _DailyMetrics())
        metrics.total_calls += 1
        metrics.connected_calls += int(call.connected)
        disposition = (
            dispositions.get((call.workflow_id, call.effective_disposition))
            if call.effective_disposition is not None
            else None
        )
        if disposition is not None and disposition.is_success:
            metrics.success_leads.add(call.lead_id)

    daily_rows = []
    for (workflow_id, local_date), metrics in sorted(daily.items()):
        total_calls = metrics.total_calls
        connected_calls = metrics.connected_calls
        daily_rows.append(
            (
                workflow_id,
                local_date,
                total_calls,
                connected_calls,
                f"{connected_calls / total_calls:.4f}",
                len(metrics.success_leads),
            )
        )
    _write_csv(
        output_dir / "daily_overview.csv",
        (
            "workflow_id",
            "date",
            "total_calls",
            "connected_calls",
            "connectivity_rate",
            "success_leads",
        ),
        daily_rows,
    )

    lead_report_rows = []
    for lead in sorted(
        lead_rows,
        key=lambda row: (row["workflow_id"], row["external_ref"]),
    ):
        lead_calls = calls_by_lead[lead["id"]]
        ordered_calls = sorted(lead_calls, key=_call_order, reverse=True)
        last_call = ordered_calls[0] if ordered_calls else None
        current_call = next(
            (
                call
                for call in ordered_calls
                if call.effective_disposition is not None
            ),
            None,
        )
        ever_success = any(_is_successful(call, dispositions) for call in lead_calls)
        lead_report_rows.append(
            (
                lead["workflow_id"],
                lead["external_ref"],
                lead["batch_id"],
                mask(lead["phone_last4"]),
                lead_buckets[lead["id"]],
                len(lead_calls),
                sum(call.connected for call in lead_calls),
                (
                    to_ist(last_call.initiated_at).isoformat()
                    if last_call is not None and last_call.initiated_at is not None
                    else ""
                ),
                current_call.effective_disposition if current_call else "",
                str(ever_success).lower(),
            )
        )
    _write_csv(
        output_dir / "lead_report.csv",
        (
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
        ),
        lead_report_rows,
    )

    rejections = conn.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM lead_rejections
        ORDER BY workflow_id, batch_id, row_number
        """
    )
    _write_csv(
        output_dir / "rejections.csv",
        ("workflow_id", "batch_id", "row_number", "reason"),
        (
            (
                row["workflow_id"],
                row["batch_id"],
                row["row_number"],
                row["reason"],
            )
            for row in rejections
        ),
    )

    quality = _data_quality(conn, orphan_events, unknown_disposition_events)
    (output_dir / "data_quality.json").write_text(
        json.dumps(quality, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
