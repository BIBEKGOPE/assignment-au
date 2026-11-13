from solution.report import effective_disposition


VALID_CODES = {"renewed", "not_interested"}


def test_override_beats_a_later_disposition_set():
    sets = [
        {
            "event_id": "set-later",
            "occurred_at": "2026-10-05T15:00:00Z",
            "code": "renewed",
        }
    ]
    overrides = [
        {
            "event_id": "override-earlier",
            "occurred_at": "2026-10-05T14:00:00Z",
            "code": "not_interested",
        }
    ]

    assert effective_disposition(sets, overrides, VALID_CODES) == "not_interested"


def test_latest_override_wins_with_event_id_tiebreak():
    sets = []
    overrides = [
        {
            "event_id": "override-a",
            "occurred_at": "2026-10-05T14:00:00Z",
            "code": "not_interested",
        },
        {
            "event_id": "override-z",
            "occurred_at": "2026-10-05T14:00:00Z",
            "code": "renewed",
        },
        {
            "event_id": "override-old",
            "occurred_at": "2026-10-05T13:00:00Z",
            "code": "not_interested",
        },
    ]

    assert effective_disposition(sets, overrides, VALID_CODES) == "renewed"


def test_unknown_disposition_codes_are_ignored():
    sets = [
        {
            "event_id": "set-valid",
            "occurred_at": "2026-10-05T13:00:00Z",
            "code": "renewed",
        },
        {
            "event_id": "set-unknown",
            "occurred_at": "2026-10-05T16:00:00Z",
            "code": "unknown",
        },
    ]
    overrides = [
        {
            "event_id": "override-unknown",
            "occurred_at": "2026-10-05T17:00:00Z",
            "code": "unknown",
        }
    ]

    assert effective_disposition(sets, overrides, VALID_CODES) == "renewed"
