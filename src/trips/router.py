from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.db.session import get_db_session
from src.integrations.hermes import HermesBusinessError, HermesIntegrationError
from src.integrations.hermes_models import HermesResult
from src.quota.service import (
    TripOwnershipError,
    owned_success_trip_by_result,
    owned_trip_by_job,
    record_trip_telemetry,
    settle_trip,
    update_active_status,
)
from src.trips.schemas import TripSubmitRequest
from src.trips.service import (
    ARTIFACT_EXTENSIONS,
    apply_job_status,
    public_artifact,
    public_job_status,
    safe_failure,
    submit_trip,
)

router = APIRouter(prefix="/api/trip")
CURRENT_AUTH = Depends(get_current_auth)
DB_SESSION = Depends(get_db_session)


def _not_found() -> ApiError:
    return ApiError(404, "TRIP_NOT_FOUND", "行程不存在。")


def _upstream_error(exc: Exception, *, status_timeout: bool = False) -> ApiError:
    if isinstance(exc, HermesBusinessError):
        mappings = {
            "EXPORT_RATE_LIMITED": (
                429,
                "EXPORT_RATE_LIMITED",
                "今日导出次数已达上限，请明日再试。",
            ),
            "PLACE_NOT_FOUND": (404, "PLACE_NOT_FOUND", "地点不存在。"),
            "PLACE_UNSUPPORTED": (422, "PLACE_UNSUPPORTED", "该地点暂不支持。"),
            "RESULT_CONTRACT_UNSUPPORTED": (
                422,
                "RESULT_CONTRACT_UNSUPPORTED",
                "该攻略版本暂不支持。",
            ),
        }
        status, code, message = mappings.get(
            exc.code,
            (502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。"),
        )
        return ApiError(status, code, message)
    if isinstance(exc, HermesIntegrationError) and exc.category == "UNAVAILABLE":
        if status_timeout:
            return ApiError(
                504,
                "GENERATION_STATUS_TIMEOUT",
                "暂时无法获取生成状态。",
                retryable=True,
            )
        return ApiError(
            503,
            "GENERATION_SERVICE_UNAVAILABLE",
            "生成服务暂时不可用。",
            retryable=True,
        )
    return ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。")


def _stream_interrupted_event(job_id: str) -> str:
    payload = {
        "ok": False,
        "job_id": job_id,
        "stream_state": "INTERRUPTED",
        "job_status_known": False,
        "fallback": "POLLING",
        "error": {
            "code": "GENERATION_STREAM_INTERRUPTED",
            "message": "状态流暂时中断，请改用轮询确认任务状态。",
            "retryable": True,
        },
    }
    safe_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: interrupted\ndata: {safe_json}\n\n"


@router.post("/async")
async def create_trip(
    body: TripSubmitRequest,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
) -> dict[str, object]:
    submission = await submit_trip(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.hermes,
        user_id=auth.user.id,
        body=body,
        correlation_id=request.state.correlation_id,
    )
    return {
        "ok": True,
        "trip_id": submission.trip.public_id,
        "job_id": submission.trip.hermes_job_id,
        "status": submission.trip.status,
        "quota": {
            "state": submission.quota_state,
            "remaining": submission.quota_remaining,
        },
    }


@router.get("/jobs/{job_id}")
async def job_status(
    job_id: str,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    try:
        trip = await owned_trip_by_job(db, user_id=auth.user.id, job_id=job_id)
    except TripOwnershipError as exc:
        raise _not_found() from exc
    try:
        upstream = await request.app.state.hermes.job_status(
            job_id,
            request.state.correlation_id,
        )
        local = await apply_job_status(
            request.app.state.session_factory,
            trip=trip,
            upstream=upstream,
            owner_id=auth.user.id,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc, status_timeout=True) from exc
    return public_job_status(upstream, local)


async def _owned_sse(
    request: Request,
    *,
    trip_id: uuid.UUID,
    user_id: uuid.UUID,
    job_id: str,
) -> AsyncIterator[str]:
    terminal_event_seen = False
    try:
        async for event, payload in request.app.state.hermes.stream_job(
            job_id,
            request.state.correlation_id,
        ):
            status = str(payload["status"])
            if event == "complete":
                await settle_trip(
                    request.app.state.session_factory,
                    trip_id=trip_id,
                    terminal_status="SUCCESS",
                    result_record_id=int(payload["result_record_id"]),
                    owner_id=user_id,
                )
                terminal_event_seen = True
            elif event == "failed":
                error = payload.get("error")
                raw_code = error.get("code") if isinstance(error, dict) else None
                code, message, retryable = safe_failure(raw_code)
                payload["error"] = {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                }
                await settle_trip(
                    request.app.state.session_factory,
                    trip_id=trip_id,
                    terminal_status=status,
                    error_code=code,
                    error_message=message,
                    error_retryable=retryable,
                    owner_id=user_id,
                )
                terminal_event_seen = True
            else:
                await update_active_status(
                    request.app.state.session_factory,
                    trip_id=trip_id,
                    status=status,
                    owner_id=user_id,
                )
            safe_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            yield f"event: {event}\ndata: {safe_json}\n\n"
    except HermesIntegrationError:
        if not terminal_event_seen:
            yield _stream_interrupted_event(job_id)
        return

    if not terminal_event_seen:
        yield _stream_interrupted_event(job_id)


@router.get("/jobs/{job_id}/stream")
async def job_stream(
    job_id: str,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> StreamingResponse:
    try:
        trip = await owned_trip_by_job(db, user_id=auth.user.id, job_id=job_id)
    except TripOwnershipError as exc:
        raise _not_found() from exc
    return StreamingResponse(
        _owned_sse(
            request,
            trip_id=trip.id,
            user_id=auth.user.id,
            job_id=job_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/results/{result_record_id}",
    response_model=HermesResult,
    response_model_exclude_none=True,
)
async def result(
    result_record_id: int,
    request: Request,
    job_id: Annotated[str, Query(min_length=1, max_length=160)],
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    try:
        trip = await owned_success_trip_by_result(
            db,
            user_id=auth.user.id,
            result_record_id=result_record_id,
            job_id=job_id,
        )
    except TripOwnershipError as exc:
        raise _not_found() from exc
    try:
        upstream = await request.app.state.hermes.result(
            result_record_id,
            job_id=job_id,
            correlation_id=request.state.correlation_id,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    if upstream.result_id != result_record_id:
        raise ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。")
    completeness_rank = {"complete": 0, "partial": 1, "unavailable": 2}
    cost_completeness = max(
        (plan.cost_estimate.completeness for plan in upstream.plans),
        key=completeness_rank.__getitem__,
    )
    await record_trip_telemetry(
        request.app.state.session_factory,
        trip_id=trip.id,
        telemetry={
            "result_schema_version": upstream.schema_version,
            "cost_estimate_completeness": cost_completeness,
        },
    )
    return upstream.model_dump(exclude_none=True)


async def _owned_result(
    db: AsyncSession,
    auth: AuthContext,
    result_record_id: int,
) -> None:
    try:
        await owned_success_trip_by_result(
            db,
            user_id=auth.user.id,
            result_record_id=result_record_id,
        )
    except TripOwnershipError as exc:
        raise _not_found() from exc


@router.post("/results/{result_record_id}/artifacts/{artifact_type}")
async def create_artifact(
    result_record_id: int,
    artifact_type: str,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    if artifact_type not in ARTIFACT_EXTENSIONS:
        raise ApiError(422, "ARTIFACT_TYPE_UNSUPPORTED", "暂不支持该导出类型。")
    await _owned_result(db, auth, result_record_id)
    try:
        upstream = await request.app.state.hermes.artifact(
            "POST",
            result_record_id,
            artifact_type,
            correlation_id=request.state.correlation_id,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    if upstream.result_record_id != result_record_id or upstream.artifact_type != artifact_type:
        raise ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。")
    return public_artifact(
        upstream,
        result_record_id=result_record_id,
        artifact_type=artifact_type,
    )


@router.get("/results/{result_record_id}/artifacts/{artifact_type}")
async def artifact_status(
    result_record_id: int,
    artifact_type: str,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    if artifact_type not in ARTIFACT_EXTENSIONS:
        raise ApiError(422, "ARTIFACT_TYPE_UNSUPPORTED", "暂不支持该导出类型。")
    await _owned_result(db, auth, result_record_id)
    try:
        upstream = await request.app.state.hermes.artifact(
            "GET",
            result_record_id,
            artifact_type,
            correlation_id=request.state.correlation_id,
        )
    except HermesBusinessError as exc:
        if exc.code == "EXPORT_ARTIFACT_NOT_FOUND":
            raise ApiError(
                404,
                "EXPORT_ARTIFACT_NOT_FOUND",
                "导出文件尚未创建。",
            ) from exc
        raise _upstream_error(exc) from exc
    except HermesIntegrationError as exc:
        raise _upstream_error(exc) from exc
    if upstream.result_record_id != result_record_id or upstream.artifact_type != artifact_type:
        raise ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。")
    return public_artifact(
        upstream,
        result_record_id=result_record_id,
        artifact_type=artifact_type,
    )


@router.get("/results/{result_record_id}/artifacts/{artifact_type}/download")
async def artifact_download(
    result_record_id: int,
    artifact_type: str,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> Response:
    if artifact_type not in ARTIFACT_EXTENSIONS:
        raise ApiError(422, "ARTIFACT_TYPE_UNSUPPORTED", "暂不支持该导出类型。")
    await _owned_result(db, auth, result_record_id)
    try:
        (
            content,
            content_type,
            _upstream_disposition,
        ) = await request.app.state.hermes.artifact_bytes(
            result_record_id,
            artifact_type,
            correlation_id=request.state.correlation_id,
            max_bytes=request.app.state.settings.artifact_max_bytes,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="trip-{result_record_id}.'
                f'{ARTIFACT_EXTENSIONS[artifact_type]}"'
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/places")
async def places(
    request: Request,
    city: Annotated[str, Query(min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
    _auth: AuthContext = CURRENT_AUTH,
) -> dict[str, object]:
    try:
        upstream = await request.app.state.hermes.places(
            city=city.strip(),
            limit=limit,
            correlation_id=request.state.correlation_id,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return upstream.model_dump(exclude_none=True)


@router.get("/places/{place_id}")
async def place(
    place_id: int,
    request: Request,
    _auth: AuthContext = CURRENT_AUTH,
) -> dict[str, object]:
    try:
        upstream = await request.app.state.hermes.place(
            place_id,
            correlation_id=request.state.correlation_id,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return upstream.model_dump(exclude_none=True)
