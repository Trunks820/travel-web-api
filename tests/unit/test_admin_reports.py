from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.admin.reports import ratio, trip_exception
from src.db.models import AdminTripProjection


def _trip(**changes):
    now = datetime.now(UTC)
    values = {
        "job_id": "trip_test",
        "source_id": 1,
        "source_version": 1,
        "source": "WEB",
        "city": "重庆",
        "days": 3,
        "status": "RUNNING",
        "guide_result_state": "NOT_APPLICABLE",
        "created_at": now - timedelta(seconds=181),
        "started_at": now - timedelta(seconds=181),
        "retry_count": 0,
        "failed_draft_available": False,
        "trace_completeness": "COMPLETE",
        "association_state": "unlinked",
        "association_version": 1,
        "source_updated_at": now,
        "synced_at": now,
    }
    values.update(changes)
    if "created_at" in changes and "started_at" not in changes:
        values["started_at"] = changes["created_at"]
    return AdminTripProjection(**values)


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
