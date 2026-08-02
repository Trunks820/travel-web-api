from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.admin.projection import calculate_sync_lag
from src.admin.projection_schemas import (
    AdminTripJobListResponse,
    TripProjectionCommitEvent,
)


@pytest.mark.parametrize(
    ("lag", "expected"),
    [
        (0, "FRESH"),
        (5, "FRESH"),
        (5.001, "LAGGING"),
        (30, "LAGGING"),
        (30.001, "DELAYED"),
        (300, "DELAYED"),
        (300.001, "UNAVAILABLE"),
    ],
)
def test_freshness_exact_inclusive_bands(lag: float, expected: str) -> None:
    observed = datetime(2026, 8, 2, tzinfo=UTC)
    calculated, state = calculate_sync_lag(
        observed_at=observed,
        committed_at=observed + timedelta(seconds=lag),
        response_as_of=observed,
    )
    assert calculated == pytest.approx(lag)
    assert state == expected


def test_freshness_uses_max_delivery_or_overdue_and_clamps_clock_skew() -> None:
    observed = datetime(2026, 8, 2, tzinfo=UTC)
    delayed, state = calculate_sync_lag(
        observed_at=observed,
        committed_at=observed + timedelta(seconds=2),
        response_as_of=observed + timedelta(seconds=31),
    )
    assert delayed == 21
    assert state == "LAGGING"
    clamped, state = calculate_sync_lag(
        observed_at=observed,
        committed_at=observed - timedelta(seconds=5),
        response_as_of=observed - timedelta(seconds=1),
    )
    assert clamped == 0
    assert state == "FRESH"


def _freshness(state: str) -> dict:
    return {
        "data_as_of": None,
        "sync_checked_at": "2026-08-02T00:00:00Z",
        "sync_lag_seconds": 0,
        "source_high_watermark": 0,
        "applied_high_watermark": 0,
        "projection_state": state,
    }


def test_projection_alarm_is_coupled_to_success_state() -> None:
    base = {
        "ok": True,
        "request_id": "req-test",
        "as_of": "2026-08-02T00:00:00Z",
        "page": 1,
        "limit": 20,
        "total": 0,
        "items": [],
    }
    AdminTripJobListResponse.model_validate(
        {**base, "freshness": _freshness("FRESH"), "projection_alarm": None}
    )
    with pytest.raises(ValidationError):
        AdminTripJobListResponse.model_validate(
            {**base, "freshness": _freshness("DELAYED"), "projection_alarm": None}
        )
    with pytest.raises(ValidationError):
        AdminTripJobListResponse.model_validate(
            {
                **base,
                "freshness": _freshness("LAGGING"),
                "projection_alarm": {
                    "code": "PROJECTION_SYNC_STALLED",
                    "message": "stalled",
                    "retryable": True,
                },
            }
        )


def test_composite_event_rejects_foreign_step() -> None:
    event = {
        "event_id": "0f4a9840-cdeb-4a98-83d5-cfa823c94053",
        "event_type": "TRIP_PROJECTION_COMMITTED",
        "schema_version": "1.0",
        "outbox_sequence": 1,
        "aggregate_type": "TRIP_JOB",
        "aggregate_id": "job-1",
        "aggregate_version": 1,
        "occurred_at": "2026-08-02T00:00:00Z",
        "payload": {
            "job": {
                "source_id": 1,
                "job_id": "job-1",
                "source_version": 1,
                "source": "WEB",
                "city": "重庆",
                "days": 3,
                "status": "PENDING",
                "current_stage": "PENDING",
                "result_type": None,
                "result_record_id": None,
                "guide_result_state": "NOT_APPLICABLE",
                "error_code": None,
                "safe_error": None,
                "detailed_reason": None,
                "created_at": "2026-08-02T00:00:00Z",
                "started_at": None,
                "finished_at": None,
                "retry_count": 0,
                "failed_draft_available": False,
                "trace_completeness": "COMPLETE",
                "source_updated_at": "2026-08-02T00:00:00Z",
            },
            "changed_steps": [
                {
                    "source_step_id": 1,
                    "job_id": "job-2",
                    "source_version": 1,
                    "stage": "DATA_RETRIEVAL",
                    "status": "RUNNING",
                    "attempt": 1,
                    "publish_retry_round": 0,
                    "started_at": "2026-08-02T00:00:00Z",
                    "finished_at": None,
                    "duration_ms": None,
                    "source_updated_at": "2026-08-02T00:00:00Z",
                }
            ],
        },
    }
    with pytest.raises(ValidationError):
        TripProjectionCommitEvent.model_validate(event)
