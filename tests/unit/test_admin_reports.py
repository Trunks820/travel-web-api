from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.admin.reports import ratio, trip_exception
from src.db.models import UserTrip


def _trip(**changes):
    now = datetime.now(UTC)
    values = {
        "public_id": "trip_test",
        "client_request_id": "request",
        "request_hash": "x" * 64,
        "request_json": {"to_city": "重庆", "days": 3},
        "city": "重庆",
        "days": 3,
        "status": "RUNNING",
        "telemetry_json": {},
        "created_at": now - timedelta(seconds=181),
        "updated_at": now,
        "visible_until": now + timedelta(days=7),
        "reconciliation_attempts": 0,
    }
    values.update(changes)
    return UserTrip(**values)


def test_report_zero_denominator_is_not_applicable():
    assert ratio(0, 0) == {"value": None, "not_applicable": True}
    assert ratio(1, 2) == {"value": 0.5, "not_applicable": False}


def test_trip_exception_contract():
    now = datetime.now(UTC)
    assert trip_exception(_trip(), now) is True
    assert trip_exception(_trip(status="FAILED"), now) is True
    assert (
        trip_exception(
            _trip(
                status="REJECTED",
                error_code="CITY_CLARIFICATION_REQUIRED",
                created_at=now,
            ),
            now,
        )
        is False
    )
    assert (
        trip_exception(
            _trip(status="REJECTED", error_code="CITY_DISABLED", created_at=now),
            now,
        )
        is True
    )
