from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.admin.projection import (
    ProjectionSequenceBlocked,
    apply_projection_event,
    apply_projection_heartbeat,
    pause_projection_stream,
    repair_projection_event,
)
from src.db.models import (
    AdminProjectionConsumerState,
    AdminProjectionEvent,
    AdminTripProjection,
    AdminTripStepProjection,
    AppUser,
    UserTrip,
)
from src.quota.service import save_upstream_acceptance
from src.security.secrets import new_opaque_id
from tests.factories import unique_display_name_fields


def _event(*, sequence: int = 1, source_version: int = 1) -> dict:
    now = datetime.now(UTC)
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "TRIP_PROJECTION_COMMITTED",
        "schema_version": "1.0",
        "outbox_sequence": sequence,
        "aggregate_type": "TRIP_JOB",
        "aggregate_id": "projection-job-1",
        "aggregate_version": source_version,
        "occurred_at": now.isoformat(),
        "payload": {
            "job": {
                "source_id": 101,
                "job_id": "projection-job-1",
                "source_version": source_version,
                "source": "WEB",
                "city": "重庆",
                "days": 3,
                "status": "RUNNING",
                "current_stage": "DATA_RETRIEVAL",
                "result_type": None,
                "result_record_id": None,
                "guide_result_state": "NOT_APPLICABLE",
                "error_code": None,
                "safe_error": None,
                "detailed_reason": None,
                "created_at": (now - timedelta(seconds=30)).isoformat(),
                "started_at": (now - timedelta(seconds=20)).isoformat(),
                "finished_at": None,
                "retry_count": 0,
                "failed_draft_available": False,
                "trace_completeness": "COMPLETE",
                "source_updated_at": now.isoformat(),
            },
            "changed_steps": [
                {
                    "source_step_id": 1001,
                    "job_id": "projection-job-1",
                    "source_version": source_version,
                    "stage": "DATA_RETRIEVAL",
                    "status": "RUNNING",
                    "attempt": 1,
                    "publish_retry_round": 0,
                    "started_at": (now - timedelta(seconds=20)).isoformat(),
                    "finished_at": None,
                    "duration_ms": None,
                    "source_updated_at": now.isoformat(),
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_composite_event_dedupe_stale_rejection_and_contiguous_heartbeat(
    session_factory,
):
    event = _event()
    assert await apply_projection_event(session_factory, event) == "APPLIED"
    assert await apply_projection_event(session_factory, event) == "DUPLICATE"
    async with session_factory() as session:
        job = await session.get(AdminTripProjection, "projection-job-1")
        step = await session.get(AdminTripStepProjection, 1001)
        state = await session.get(AdminProjectionConsumerState, 1)
        assert job is not None and job.association_state == "unlinked"
        assert step is not None and step.status == "RUNNING"
        assert state.applied_high_watermark == 1
        assert state.next_expected_sequence == 2
        assert await session.get(AdminProjectionEvent, uuid.UUID(event["event_id"]))

    stale = _event(sequence=2, source_version=1)
    stale["payload"]["job"]["city"] = "不应覆盖"
    stale["payload"]["changed_steps"][0]["status"] = "SUCCESS"
    assert await apply_projection_event(session_factory, stale) == "APPLIED"
    async with session_factory() as session:
        job = await session.get(AdminTripProjection, "projection-job-1")
        step = await session.get(AdminTripStepProjection, 1001)
        assert job.city == "重庆"
        assert step.status == "RUNNING"

    observed = datetime.now(UTC)
    assert not await apply_projection_heartbeat(
        session_factory,
        {
            "event_type": "PROJECTION_HEARTBEAT",
            "schema_version": "1.0",
            "observed_at": observed.isoformat(),
            "outbox_high_watermark": 1,
        },
    )
    assert await apply_projection_heartbeat(
        session_factory,
        {
            "event_type": "PROJECTION_HEARTBEAT",
            "schema_version": "1.0",
            "observed_at": observed.isoformat(),
            "outbox_high_watermark": 2,
        },
    )
    async with session_factory() as session:
        state = await session.get(AdminProjectionConsumerState, 1)
        assert state.initialization_state == "INITIALIZED"
        assert state.latest_heartbeat_watermark == 2


@pytest.mark.asyncio
async def test_poison_pause_blocks_progress_until_same_path_repair(session_factory):
    await pause_projection_stream(session_factory)
    event = _event()
    with pytest.raises(ProjectionSequenceBlocked):
        await apply_projection_event(session_factory, event)
    assert not await apply_projection_heartbeat(
        session_factory,
        {
            "event_type": "PROJECTION_HEARTBEAT",
            "schema_version": "1.0",
            "observed_at": datetime.now(UTC).isoformat(),
            "outbox_high_watermark": 0,
        },
    )

    assert await repair_projection_event(session_factory, event) == "APPLIED"
    async with session_factory() as session:
        state = await session.get(AdminProjectionConsumerState, 1)
        assert state.stream_state == "ACTIVE"
        assert state.applied_high_watermark == 1
        assert state.next_expected_sequence == 2


@pytest.mark.asyncio
async def test_invalid_composite_exposes_no_partial_member(session_factory):
    invalid = _event()
    invalid["payload"]["changed_steps"][0]["job_id"] = "foreign-job"
    with pytest.raises(ValidationError):
        await apply_projection_event(session_factory, invalid)
    async with session_factory() as session:
        assert await session.get(AdminTripProjection, "projection-job-1") is None
        assert await session.get(AdminTripStepProjection, 1001) is None
        state = await session.get(AdminProjectionConsumerState, 1)
        assert state.applied_high_watermark == 0
        assert state.next_expected_sequence == 1


@pytest.mark.asyncio
async def test_duplicate_event_id_with_changed_payload_is_poison(session_factory):
    event = _event()
    await apply_projection_event(session_factory, event)
    changed = copy.deepcopy(event)
    changed["payload"]["job"]["city"] = "成都"
    from src.admin.projection import ProjectionPoisonError

    with pytest.raises(ProjectionPoisonError):
        await apply_projection_event(session_factory, changed)


@pytest.mark.asyncio
async def test_event_before_binding_is_corrected_by_binding_transaction(session_factory):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
            **unique_display_name_fields(),
        )
        session.add(user)
        await session.flush()
        trip = UserTrip(
            public_id=new_opaque_id("trip_"),
            user_id=user.id,
            client_request_id="projection-binding-request",
            request_hash="b" * 64,
            request_json={"to_city": "重庆", "days": 3},
            city="重庆",
            days=3,
            status="SUBMITTING",
            created_at=now,
            updated_at=now,
            visible_until=now + timedelta(days=7),
        )
        session.add(trip)
        await session.flush()
        trip_id = trip.id
        user_id = user.id

    await apply_projection_event(session_factory, _event())
    async with session_factory() as session:
        row = await session.get(AdminTripProjection, "projection-job-1")
        assert row.association_state == "unlinked"

    await save_upstream_acceptance(
        session_factory,
        trip_id=trip_id,
        job_id="projection-job-1",
        status="PENDING",
    )
    async with session_factory() as session:
        row = await session.get(AdminTripProjection, "projection-job-1")
        assert row.association_state == "linked"
        assert row.user_id == user_id
        assert row.user_trip_id == trip_id
        assert row.association_version == 2


@pytest.mark.asyncio
async def test_identity_tombstone_outranks_newer_source_event(session_factory):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        trip = UserTrip(
            public_id=new_opaque_id("trip_"),
            user_id=None,
            client_request_id="erased-projection-request",
            request_hash="c" * 64,
            request_json={"to_city": "重庆", "days": 3},
            city="重庆",
            days=3,
            status="SUCCESS",
            hermes_job_id="projection-job-1",
            created_at=now,
            updated_at=now,
            visible_until=now,
            archived_at=now,
            identity_erased_at=now,
            association_version=7,
        )
        session.add(trip)

    await apply_projection_event(session_factory, _event())
    newer = _event(sequence=2, source_version=2)
    await apply_projection_event(session_factory, newer)
    async with session_factory() as session:
        row = await session.get(AdminTripProjection, "projection-job-1")
        assert row.association_state == "de-identified"
        assert row.association_version == 7
        assert row.identity_erased_at is not None
        assert row.user_id is None
        assert row.user_trip_id is None
