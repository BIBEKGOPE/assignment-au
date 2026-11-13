import pytest

from solution.report import compute_bucket


@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        ([], "yet_to_reach_out"),
        (
            [
                {"effective_disposition": "renewed", "telephony_failed": True},
                {"effective_disposition": None, "telephony_failed": True},
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
                {
                    "effective_disposition": None,
                    "telephony_failed": False,
                    "connected": True,
                },
            ],
            "in_progress_dialed",
        ),
    ],
)
def test_bucket_priority_and_mixed_failed_connected_calls(calls, expected):
    assert compute_bucket(calls, {"renewed"}) == expected
