from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.errors import ApiError
from src.config import Settings
from src.db.models import UserTrip
from src.integrations.hermes import (
    HermesBusinessError,
    HermesClient,
    HermesIntegrationError,
)
from src.integrations.hermes_models import HermesJobStatus, HermesTripCreated
from src.quota.service import (
    ActiveTripConflict,
    QuotaExhausted,
    RequestIdConflict,
    Reservation,
    TripOwnershipError,
    mark_upstream_uncertain,
    quota_snapshot,
    record_trip_telemetry,
    reserve_trip,
    save_upstream_acceptance,
    settle_trip,
    update_active_status,
)
from src.trips.schemas import TripSubmitRequest, normalized_request_hash

SAFE_FAILURES: dict[str, tuple[str, str, bool]] = {
    "CITY_NOT_SUPPORTED": ("CITY_NOT_SUPPORTED", "暂不支持该城市。", False),
    "NO_FEASIBLE_PLAN": ("NO_FEASIBLE_PLAN", "未能生成满足条件的行程，请调整条件后重试。", True),
    "CONTENT_REJECTED": ("CONTENT_REJECTED", "本次请求无法生成行程。", False),
    "GENERATION_TIMEOUT": ("GENERATION_TIMEOUT", "生成超时，请稍后重试。", True),
    "GENERATION_FAILED": ("GENERATION_FAILED", "生成失败，请稍后重试。", True),
    "WRITER_CAPACITY_BUSY": (
        "SERVICE_UNAVAILABLE",
        "服务暂时不可用，请稍后再试",
        True,
    ),
    "WRITER_ENDPOINTS_UNAVAILABLE": (
        "SERVICE_UNAVAILABLE",
        "服务暂时不可用，请稍后再试",
        True,
    ),
}
ARTIFACT_EXTENSIONS = {
    "pdf": "pdf",
    "share_image": "png",
}


@dataclass(frozen=True)
class Submission:
    trip: UserTrip
    quota_remaining: int
    quota_state: str


def upstream_request_id(trip: UserTrip) -> str:
    return f"bff-{trip.public_id}"


def safe_failure(
    raw_code: str | None,
    raw_message: str | None = None,
) -> tuple[str, str, bool]:
    del raw_message
    return SAFE_FAILURES.get(
        (raw_code or "").upper(),
        ("GENERATION_FAILED", "生成失败，请稍后重试。", True),
    )


async def _submit_reserved(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    hermes: HermesClient,
    reservation: Reservation,
    *,
    correlation_id: str,
) -> UserTrip:
    trip = reservation.trip
    try:
        created: HermesTripCreated = await hermes.create_trip(
            trip_request=trip.request_json,
            upstream_request_id=upstream_request_id(trip),
            conversation_id=trip.public_id,
            correlation_id=correlation_id,
        )
    except HermesBusinessError as exc:
        if exc.code == "CITY_NOT_SUPPORTED":
            await settle_trip(
                session_factory,
                trip_id=trip.id,
                terminal_status="REJECTED",
                error_code="CITY_NOT_SUPPORTED",
                error_message="暂不支持该城市。",
                error_retryable=False,
            )
            raise ApiError(422, "CITY_NOT_SUPPORTED", "暂不支持该城市。") from exc
        raise ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。") from exc
    except HermesIntegrationError as exc:
        if exc.acceptance_uncertain:
            await mark_upstream_uncertain(session_factory, trip.id)
        else:
            await settle_trip(
                session_factory,
                trip_id=trip.id,
                terminal_status="FAILED",
                error_code="GENERATION_SERVICE_UNAVAILABLE",
                error_message="生成服务暂时不可用，请稍后重试。",
                error_retryable=True,
            )
        code = (
            "GENERATION_SERVICE_UNAVAILABLE"
            if exc.category == "UNAVAILABLE"
            else "GENERATION_SERVICE_ERROR"
        )
        status = 503 if exc.category == "UNAVAILABLE" else 502
        message = (
            "生成服务暂时不可用，请稍后重试。" if status == 503 else "生成服务返回了无效响应。"
        )
        raise ApiError(status, code, message, retryable=exc.retryable) from exc

    if not created.job_id:
        await mark_upstream_uncertain(session_factory, trip.id)
        raise ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。")
    accepted = await save_upstream_acceptance(
        session_factory,
        trip_id=trip.id,
        job_id=created.job_id,
        status=created.status,
    )
    await record_trip_telemetry(
        session_factory,
        trip_id=trip.id,
        telemetry={
            "current_stage": created.current_stage,
        },
    )
    return accepted


async def submit_trip(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    hermes: HermesClient,
    *,
    user_id: uuid.UUID,
    body: TripSubmitRequest,
    correlation_id: str,
) -> Submission:
    normalized = body.trip_request.normalized()
    supplied_fields = {
        field_name: "USER_SUPPLIED"
        for field_name in body.trip_request.model_fields_set
        if field_name in normalized
    }
    request_hash = normalized_request_hash(normalized)
    try:
        reservation = await reserve_trip(
            session_factory,
            settings,
            user_id=user_id,
            client_request_id=body.request_id,
            request_hash=request_hash,
            request_json=normalized,
            request_field_provenance=supplied_fields,
        )
    except RequestIdConflict as exc:
        raise ApiError(409, "REQUEST_ID_CONFLICT", "request_id 已用于其他请求。") from exc
    except ActiveTripConflict as exc:
        raise ApiError(
            409,
            "ACTIVE_TRIP_EXISTS",
            "当前已有进行中的行程。",
            details={
                "active_trip": {
                    "trip_id": exc.trip_id,
                    "job_id": exc.job_id,
                    "status": exc.status,
                }
            },
        ) from exc
    except QuotaExhausted as exc:
        raise ApiError(429, "QUOTA_EXHAUSTED", "生成额度已用完。") from exc
    except TripOwnershipError as exc:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。") from exc

    trip = reservation.trip
    if trip.hermes_job_id is None and trip.status == "SUBMITTING":
        trip = await _submit_reserved(
            session_factory,
            settings,
            hermes,
            reservation,
            correlation_id=correlation_id,
        )
    async with session_factory() as session:
        current_quota = await quota_snapshot(session, user_id)
    quota_state = {
        "SUCCESS": "CONSUMED",
        "FAILED": "RELEASED",
        "TIMEOUT": "RELEASED",
        "REJECTED": "RELEASED",
    }.get(trip.status, "RESERVED")
    return Submission(
        trip=trip,
        quota_remaining=current_quota.remaining,
        quota_state=quota_state,
    )


async def apply_job_status(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trip: UserTrip,
    upstream: HermesJobStatus,
    owner_id: uuid.UUID | None,
) -> UserTrip:
    if upstream.job_id != trip.hermes_job_id:
        raise HermesIntegrationError("PROTOCOL", retryable=False)
    await record_trip_telemetry(
        session_factory,
        trip_id=trip.id,
        telemetry={
            "current_stage": upstream.current_stage,
            "plan_count": upstream.plan_count,
            "elapsed_ms": upstream.elapsed_ms,
            "queue_wait_ms": upstream.queue_wait_ms,
            "run_elapsed_ms": upstream.run_elapsed_ms,
            "total_elapsed_ms": upstream.total_elapsed_ms,
            "result_type": upstream.result_type,
        },
    )
    if upstream.status in {"SUBMITTING", "PENDING", "RUNNING"}:
        return await update_active_status(
            session_factory,
            trip_id=trip.id,
            status=upstream.status,
            owner_id=owner_id,
        )
    code, message, retryable = safe_failure(
        upstream.error_code,
        upstream.error_message,
    )
    if upstream.status == "SUCCESS":
        if upstream.result_record_id is None:
            raise HermesIntegrationError("PROTOCOL", retryable=False)
        return await settle_trip(
            session_factory,
            trip_id=trip.id,
            terminal_status="SUCCESS",
            result_record_id=upstream.result_record_id,
            owner_id=owner_id,
        )
    return await settle_trip(
        session_factory,
        trip_id=trip.id,
        terminal_status=upstream.status,
        error_code=code,
        error_message=message,
        error_retryable=retryable,
        owner_id=owner_id,
    )


def public_job_status(upstream: HermesJobStatus, local: UserTrip) -> dict[str, Any]:
    payload = upstream.model_dump(exclude_none=True)
    payload.pop("message", None)
    payload["ok"] = True
    payload["status"] = local.status
    payload["job_id"] = local.hermes_job_id
    payload["result_record_id"] = local.result_record_id
    if local.status in {"FAILED", "TIMEOUT", "REJECTED"}:
        payload["error_code"] = local.error_code
        payload["error_message"] = local.error_message
    else:
        payload["error_code"] = None
        payload["error_message"] = None
    return payload


def public_artifact(model, *, result_record_id: int, artifact_type: str) -> dict[str, Any]:
    payload = model.model_dump(exclude_none=True)
    payload["result_record_id"] = result_record_id
    payload["artifact_type"] = artifact_type
    payload.pop("download_url", None)
    payload.pop("filename", None)
    payload["metadata"] = {
        key: value
        for key, value in payload.get("metadata", {}).items()
        if key in {"export_version", "source_schema_version"}
        and isinstance(value, (str, int, float, bool))
    }
    if payload.get("status") == "ready":
        payload["download_url"] = (
            f"/api/trip/results/{result_record_id}/artifacts/{artifact_type}/download"
        )
        payload["filename"] = f"trip-{result_record_id}.{ARTIFACT_EXTENSIONS[artifact_type]}"
    return payload
