from pathlib import Path

from solution.schema import load_dispositions, load_schemas


SCHEMAS_DIR = (
    Path(__file__).resolve().parents[1] / "assignment-a-data" / "schemas"
)


def test_load_schemas_preserves_workflows_fields_and_reference_field():
    schemas = load_schemas(SCHEMAS_DIR)

    assert set(schemas) == {"motor_renewal", "policy_feedback"}
    motor_schema = schemas["motor_renewal"]
    assert [field.name for field in motor_schema.fields] == [
        "customer_name",
        "phone",
        "policy_number",
        "policy_expiry",
        "premium_amount",
        "preferred_language",
    ]
    assert motor_schema.reference_field.name == "policy_number"
    assert motor_schema.fields[-1].default == "en"


def test_map_headers_matches_renamed_headers_and_ignores_extra_columns():
    schema = load_schemas(SCHEMAS_DIR)["motor_renewal"]
    headers = [
        "Insured Name",
        "Mobile No",
        "Policy No",
        "Expiry Date",
        "Premium",
        "Language",
        "agent_notes",
    ]

    assert schema.map_headers(headers) == {
        "customer_name": 0,
        "phone": 1,
        "policy_number": 2,
        "policy_expiry": 3,
        "premium_amount": 4,
        "preferred_language": 5,
    }


def test_map_headers_uses_first_column_matching_a_field():
    schema = load_schemas(SCHEMAS_DIR)["motor_renewal"]

    mapping = schema.map_headers(["Mobile", "Mobile No", "Policy No"])

    assert mapping["phone"] == 0


def test_load_dispositions_parses_boolean_flags():
    dispositions = load_dispositions(SCHEMAS_DIR / "dispositions.csv")

    assert dispositions[("motor_renewal", "renewed")].is_terminal is True
    assert dispositions[("motor_renewal", "renewed")].is_success is True
    assert dispositions[("motor_renewal", "payment_link_sent")].is_terminal is False
    assert dispositions[("motor_renewal", "payment_link_sent")].is_success is False
