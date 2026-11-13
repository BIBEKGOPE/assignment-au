"""Load workflow lead schemas and disposition definitions for the data pipeline."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from solution.normalize import norm_header


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool
    pii: bool = False
    reference: bool = False
    aliases: tuple[str, ...] = ()
    values: tuple[str, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class WorkflowSchema:
    workflow_id: str
    fields: tuple[Field, ...]

    @property
    def reference_field(self) -> Field:
        """Return the workflow's reference field.

        Example: ``schema.reference_field.name`` returns ``"policy_number"``.
        """
        reference_fields = [field for field in self.fields if field.reference]
        if len(reference_fields) != 1:
            raise ValueError(
                f"Workflow {self.workflow_id!r} must have exactly one reference field"
            )
        return reference_fields[0]

    def map_headers(self, headers: list[str]) -> dict[str, int]:
        """Map recognized field names to their first matching CSV column.

        Example: ``schema.map_headers(["Insured Name"])`` maps the customer-name field
        to column index ``0``.
        """
        normalized_headers = [norm_header(header) for header in headers]
        mapping: dict[str, int] = {}

        for field in self.fields:
            accepted_headers = {
                norm_header(field.name),
                *(norm_header(alias) for alias in field.aliases),
            }
            for index, header in enumerate(normalized_headers):
                if header in accepted_headers:
                    mapping[field.name] = index
                    break

        return mapping


@dataclass(frozen=True)
class Disposition:
    is_terminal: bool
    is_success: bool


def load_schemas(schemas_dir: str | Path) -> dict[str, WorkflowSchema]:
    """Load every workflow JSON schema from a directory.

    Example: ``load_schemas("assignment-a-data/schemas")`` returns schemas keyed by
    workflow ID.
    """
    schemas: dict[str, WorkflowSchema] = {}
    for schema_path in sorted(Path(schemas_dir).glob("*.json")):
        with schema_path.open(encoding="utf-8") as schema_file:
            schema_data = json.load(schema_file)

        fields = tuple(
            Field(
                name=field_data["name"],
                type=field_data["type"],
                required=field_data["required"],
                pii=field_data.get("pii", False),
                reference=field_data.get("reference", False),
                aliases=tuple(field_data.get("aliases", ())),
                values=tuple(field_data.get("values", ())),
                default=field_data.get("default"),
            )
            for field_data in schema_data["fields"]
        )
        workflow_schema = WorkflowSchema(
            workflow_id=schema_data["workflow_id"],
            fields=fields,
        )
        schemas[workflow_schema.workflow_id] = workflow_schema

    return schemas


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"Expected 'true' or 'false', got {value!r}")


def load_dispositions(csv_path: str | Path) -> dict[tuple[str, str], Disposition]:
    """Load disposition definitions keyed by workflow and code.

    Example: ``load_dispositions("dispositions.csv")[("motor_renewal", "renewed")]``
    returns a terminal, successful disposition.
    """
    dispositions: dict[tuple[str, str], Disposition] = {}
    with Path(csv_path).open(encoding="utf-8", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            key = (row["workflow_id"], row["code"])
            dispositions[key] = Disposition(
                is_terminal=_parse_bool(row["is_terminal"]),
                is_success=_parse_bool(row["is_success"]),
            )
    return dispositions
