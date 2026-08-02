from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.admin.projection_schemas import (
    Freshness,
    JobProjectionPayload,
    ProjectionAlarm,
    ProjectionHeartbeat,
    StepProjectionPayload,
    TripProjectionCommitEvent,
)
from src.db.models import (
    AdminProjectionConsumerState,
    AdminProjectionEvent,
    AdminTripProjection,
    AdminTripStepProjection,
    AppUser,
    UserTrip,
)

logger = logging.getLogger("travel_web_api.admin_projection")

STAGE_LABELS_ZH = {
    "DATA_RETRIEVAL": "检索地点数据",
    "SEMANTIC_GROUPING": "整理候选方案",
    "ROUTE_PLANNING": "规划每日路线",
    "FINAL_WRITER": "撰写攻略正文",
    "HERMES_REVIEW": "审核攻略内容",
    "REVIEW_TAXONOMY": "识别质量问题",
    "WRITER_REPAIR": "修复攻略问题",
    "REVIEW_TAXONOMY_AFTER_REPAIR": "复核修复结果",
    "PUBLISH_GATE": "执行发布校验",
    "PUBLISH_RETRY": "重新生成攻略",
    "PERSISTING": "保存最终结果",
    "INTENT_PARSER": "解析行程需求",
}
RUNTIME_POLICY = {
    "slow_after_seconds": 90,
    "timeout_after_seconds": 120,
    "stale_sweep_seconds": 30,
}


class ProjectionUnavailable(RuntimeError):
    pass


class ProjectionPoisonError(RuntimeError):
    pass


class ProjectionSequenceBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionHealth:
    as_of: datetime
    freshness: Freshness
    alarm: ProjectionAlarm | None


def calculate_sync_lag(
    *,
    observed_at: datetime,
    committed_at: datetime,
    response_as_of: datetime,
) -> tuple[float, str]:
    observed = _utc(observed_at)
    committed = _utc(committed_at)
    response_time = _utc(response_as_of)
    delivery_lag = max(0.0, (committed - observed).total_seconds())
    heartbeat_overdue = max(0.0, (response_time - observed).total_seconds() - 10.0)
    lag = max(delivery_lag, heartbeat_overdue)
    if lag <= 5:
        return lag, "FRESH"
    if lag <= 30:
        return lag, "LAGGING"
    if lag <= 300:
        return lag, "DELAYED"
    return lag, "UNAVAILABLE"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_hash(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).digest()


async def _lock_user_trip(session: AsyncSession, job_id: str) -> UserTrip | None:
    return await session.scalar(
        select(UserTrip)
        .where(UserTrip.hermes_job_id == job_id)
        .order_by(UserTrip.id)
        .with_for_update()
    )


def _association_from_locked_facts(
    trip: UserTrip | None,
    existing: AdminTripProjection | None,
) -> tuple[str, int, datetime | None, uuid.UUID | None, uuid.UUID | None]:
    if existing is not None and existing.identity_erased_at is not None:
        return (
            "de-identified",
            existing.association_version,
            existing.identity_erased_at,
            None,
            None,
        )
    if trip is None:
        return (
            "unlinked",
            existing.association_version if existing is not None else 1,
            None,
            None,
            None,
        )
    if trip.identity_erased_at is not None:
        return (
            "de-identified",
            trip.association_version,
            trip.identity_erased_at,
            None,
            None,
        )
    if trip.user_id is None:
        return "unlinked", trip.association_version, None, trip.id, None
    return "linked", trip.association_version, None, trip.id, trip.user_id


def _job_values(job: JobProjectionPayload, now: datetime) -> dict[str, Any]:
    return {
        "source_id": job.source_id,
        "source_version": job.source_version,
        "source": job.source,
        "city": job.city,
        "days": job.days,
        "status": job.status,
        "current_stage": job.current_stage,
        "result_type": job.result_type,
        "result_record_id": job.result_record_id,
        "guide_result_state": job.guide_result_state,
        "error_code": job.error_code,
        "safe_error_message": job.safe_error.message if job.safe_error else None,
        "detailed_reason": job.detailed_reason,
        "created_at": _utc(job.created_at),
        "started_at": _utc(job.started_at) if job.started_at else None,
        "finished_at": _utc(job.finished_at) if job.finished_at else None,
        "retry_count": job.retry_count,
        "failed_draft_available": job.failed_draft_available,
        "trace_completeness": job.trace_completeness,
        "source_updated_at": _utc(job.source_updated_at),
        "synced_at": now,
    }


def _step_values(step: StepProjectionPayload, now: datetime) -> dict[str, Any]:
    return {
        "job_id": step.job_id,
        "source_version": step.source_version,
        "stage": step.stage,
        "status": step.status,
        "attempt": step.attempt,
        "publish_retry_round": step.publish_retry_round,
        "started_at": _utc(step.started_at),
        "finished_at": _utc(step.finished_at) if step.finished_at else None,
        "duration_ms": step.duration_ms,
        "source_updated_at": _utc(step.source_updated_at),
        "synced_at": now,
    }


async def _apply_job_snapshot(
    session: AsyncSession,
    job: JobProjectionPayload,
    *,
    now: datetime,
) -> AdminTripProjection:
    # The UserTrip lock is deliberately acquired before the projection lock.
    trip = await _lock_user_trip(session, job.job_id)
    projection = await session.scalar(
        select(AdminTripProjection)
        .where(AdminTripProjection.job_id == job.job_id)
        .with_for_update()
    )
    association = _association_from_locked_facts(trip, projection)
    if projection is None:
        projection = AdminTripProjection(
            job_id=job.job_id,
            **_job_values(job, now),
            association_state=association[0],
            association_version=association[1],
            identity_erased_at=association[2],
            user_trip_id=association[3],
            user_id=association[4],
        )
        session.add(projection)
        await session.flush()
        return projection

    if job.source_version > projection.source_version:
        for field_name, value in _job_values(job, now).items():
            setattr(projection, field_name, value)
    projection.association_state = association[0]
    projection.association_version = max(projection.association_version, association[1])
    projection.identity_erased_at = projection.identity_erased_at or association[2]
    if projection.identity_erased_at is not None:
        projection.association_state = "de-identified"
        projection.user_trip_id = None
        projection.user_id = None
    else:
        projection.user_trip_id = association[3]
        projection.user_id = association[4]
    projection.synced_at = now
    await session.flush()
    return projection


async def _apply_step_snapshot(
    session: AsyncSession,
    step: StepProjectionPayload,
    *,
    now: datetime,
) -> None:
    row = await session.scalar(
        select(AdminTripStepProjection)
        .where(AdminTripStepProjection.source_step_id == step.source_step_id)
        .with_for_update()
    )
    if row is None:
        session.add(
            AdminTripStepProjection(
                source_step_id=step.source_step_id,
                **_step_values(step, now),
            )
        )
    elif step.source_version > row.source_version:
        for field_name, value in _step_values(step, now).items():
            setattr(row, field_name, value)


async def apply_projection_event(
    session_factory: async_sessionmaker[AsyncSession],
    raw_event: dict[str, Any],
    *,
    repair_paused_head: bool = False,
) -> str:
    event = TripProjectionCommitEvent.model_validate(raw_event)
    payload_hash = _canonical_hash(raw_event)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        duplicate = await session.get(AdminProjectionEvent, uuid.UUID(event.event_id))
        if duplicate is not None:
            if duplicate.payload_hash != payload_hash:
                raise ProjectionPoisonError("duplicate event_id has a different payload")
            return "DUPLICATE"
        state = await session.scalar(
            select(AdminProjectionConsumerState)
            .where(AdminProjectionConsumerState.id == 1)
            .with_for_update()
        )
        if state is None:
            raise ProjectionUnavailable("projection consumer state is missing")
        if state.stream_state != "ACTIVE" and not (
            repair_paused_head and state.stream_state == "PAUSED_POISON"
        ):
            raise ProjectionSequenceBlocked("projection stream is paused")
        if event.outbox_sequence != state.next_expected_sequence:
            raise ProjectionSequenceBlocked(
                f"expected sequence {state.next_expected_sequence}, got {event.outbox_sequence}"
            )

        await _apply_job_snapshot(session, event.payload.job, now=now)
        for step in sorted(event.payload.changed_steps, key=lambda item: item.source_step_id):
            await _apply_step_snapshot(session, step, now=now)
        session.add(
            AdminProjectionEvent(
                event_id=uuid.UUID(event.event_id),
                outbox_sequence=event.outbox_sequence,
                payload_hash=payload_hash,
                applied_at=now,
            )
        )
        state.applied_high_watermark = event.outbox_sequence
        state.next_expected_sequence = event.outbox_sequence + 1
        if repair_paused_head:
            state.stream_state = "ACTIVE"
        await session.flush()
    return "APPLIED"


async def repair_projection_event(
    session_factory: async_sessionmaker[AsyncSession],
    raw_event: dict[str, Any],
) -> str:
    """Apply the repaired DLQ head and resume only in that same transaction."""

    return await apply_projection_event(
        session_factory,
        raw_event,
        repair_paused_head=True,
    )


async def apply_projection_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    raw_heartbeat: dict[str, Any],
) -> bool:
    heartbeat = ProjectionHeartbeat.model_validate(raw_heartbeat)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        state = await session.scalar(
            select(AdminProjectionConsumerState)
            .where(AdminProjectionConsumerState.id == 1)
            .with_for_update()
        )
        if state is None or state.stream_state != "ACTIVE":
            return False
        if heartbeat.outbox_high_watermark != state.applied_high_watermark:
            return False
        state.latest_heartbeat_watermark = heartbeat.outbox_high_watermark
        state.latest_heartbeat_observed_at = _utc(heartbeat.observed_at)
        state.sync_checked_at = now
        state.initialization_state = "INITIALIZED"
        await session.flush()
    return True


async def pause_projection_stream(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        state = await session.scalar(
            select(AdminProjectionConsumerState)
            .where(AdminProjectionConsumerState.id == 1)
            .with_for_update()
        )
        if state is not None:
            state.stream_state = "PAUSED_POISON"


async def projection_health(
    session: AsyncSession,
    *,
    as_of: datetime | None = None,
    sensitive: bool = False,
) -> ProjectionHealth:
    response_time = _utc(as_of or datetime.now(UTC))
    state = await session.get(AdminProjectionConsumerState, 1)
    if (
        state is None
        or state.initialization_state != "INITIALIZED"
        or state.latest_heartbeat_observed_at is None
        or state.sync_checked_at is None
        or state.latest_heartbeat_watermark is None
    ):
        raise ProjectionUnavailable("projection has not initialized")
    observed = _utc(state.latest_heartbeat_observed_at)
    committed = _utc(state.sync_checked_at)
    lag, projection_state = calculate_sync_lag(
        observed_at=observed,
        committed_at=committed,
        response_as_of=response_time,
    )
    if projection_state == "UNAVAILABLE":
        raise ProjectionUnavailable("projection lag is unavailable")
    if sensitive and projection_state == "DELAYED":
        raise ProjectionUnavailable("sensitive reads require FRESH or LAGGING")
    data_as_of = await session.scalar(select(func.max(AdminTripProjection.source_updated_at)))
    freshness = Freshness(
        data_as_of=data_as_of,
        sync_checked_at=committed,
        sync_lag_seconds=lag,
        source_high_watermark=state.latest_heartbeat_watermark,
        applied_high_watermark=state.applied_high_watermark,
        projection_state=projection_state,
    )
    alarm = None
    if projection_state == "DELAYED":
        alarm = ProjectionAlarm(
            code="PROJECTION_SYNC_STALLED",
            message="攻略运营数据同步已停滞。",
            retryable=True,
        )
    return ProjectionHealth(as_of=response_time, freshness=freshness, alarm=alarm)


def association_payload(
    row: AdminTripProjection,
    user_public_id: str | None,
    display_name: str | None,
) -> dict[str, str]:
    if (
        row.association_state == "linked"
        and row.identity_erased_at is None
        and row.user_id is not None
        and user_public_id
        and display_name
    ):
        return {
            "state": "linked",
            "user_id": user_public_id,
            "display_name": display_name,
        }
    if row.association_state == "de-identified" or row.identity_erased_at is not None:
        return {"state": "de-identified"}
    return {"state": "unlinked"}


def runtime_projection(row: AdminTripProjection, as_of: datetime) -> dict[str, Any]:
    terminal = row.status in {"SUCCESS", "FAILED", "TIMEOUT", "REJECTED"}
    end = (row.finished_at or row.source_updated_at) if terminal else as_of
    end = _utc(end)
    created = _utc(row.created_at)
    anchor = _utc(row.started_at or row.created_at)
    total_ms = max(0, int((end - created).total_seconds() * 1000))
    elapsed_seconds = max(0.0, (end - anchor).total_seconds())
    return {
        "total_duration_ms": total_ms,
        "is_slow": elapsed_seconds >= RUNTIME_POLICY["slow_after_seconds"],
        "timeout_settlement_anomaly": (
            row.status in {"PENDING", "RUNNING"}
            and elapsed_seconds
            >= RUNTIME_POLICY["timeout_after_seconds"]
            + RUNTIME_POLICY["stale_sweep_seconds"]
        ),
    }


def trip_summary(
    row: AdminTripProjection,
    *,
    user_public_id: str | None,
    display_name: str | None,
    as_of: datetime,
) -> dict[str, Any]:
    safe_error = None
    if row.error_code is not None and row.safe_error_message is not None:
        safe_error = {"code": row.error_code, "message": row.safe_error_message}
    return {
        "job_id": row.job_id,
        "source": row.source,
        "city": row.city,
        "days": row.days,
        "status": row.status,
        "current_stage": row.current_stage,
        "result_type": row.result_type,
        "result_record_id": row.result_record_id,
        "guide_result_state": row.guide_result_state,
        "has_final_guide": row.guide_result_state == "AVAILABLE",
        "safe_error": safe_error,
        "detailed_reason": row.detailed_reason,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        **runtime_projection(row, as_of),
        "retry_count": row.retry_count,
        "failed_draft_available": row.failed_draft_available,
        "trace_completeness": row.trace_completeness,
        "association": association_payload(row, user_public_id, display_name),
    }


async def current_projection_with_name(
    session: AsyncSession,
    job_id: str,
    *,
    lock: bool = False,
) -> tuple[AdminTripProjection, str | None, str | None] | None:
    statement = (
        select(AdminTripProjection, AppUser.public_id, AppUser.display_name)
        .outerjoin(AppUser, AppUser.id == AdminTripProjection.user_id)
        .where(AdminTripProjection.job_id == job_id)
    )
    if lock:
        statement = statement.with_for_update(of=AdminTripProjection)
    result = (await session.execute(statement)).one_or_none()
    if result is None:
        return None
    return result[0], result[1], result[2]
