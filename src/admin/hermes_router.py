from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.audit import append_admin_audit
from src.admin.auth import AdminContext, get_current_admin
from src.admin.schemas import (
    AdminArtifactDetailResponse,
    AdminArtifactListResponse,
    AdminErrorResponse,
    AdminFailedDraftResponse,
    AdminTripJobDetailResponse,
    AdminTripJobListResponse,
)
from src.api.errors import ApiError
from src.db.session import get_db_session
from src.integrations.hermes import HermesBusinessError, HermesIntegrationError
from src.integrations.hermes_models import HermesAdminTripJob

router = APIRouter(prefix="/api/admin", tags=["admin"])
CURRENT_ADMIN = Depends(get_current_admin)
DB_SESSION = Depends(get_db_session)

TripStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "REJECTED"]
TripResultType = Literal["PLAN_READY", "NO_CANDIDATES", "NO_USABLE_ROUTE"]
DetailedReason = Literal[
    "publish_gate_failed",
    "timeout",
    "llm_error",
    "db_error",
    "workflow_error",
    "unknown",
    "cancelled",
    "city_preparing",
    "city_collection_failed",
    "city_data_insufficient",
    "city_disabled",
    "city_clarification_required",
]
ArtifactType = Literal["pdf", "share_image"]
ArtifactStatus = Literal["PENDING", "RUNNING", "READY", "FAILED", "EXPIRED"]

COMMON_READ_RESPONSES = {
    401: {"model": AdminErrorResponse, "description": "Administrator session required"},
    403: {"model": AdminErrorResponse, "description": "Administrator capability required"},
    422: {"model": AdminErrorResponse, "description": "Invalid allowlisted filter"},
    502: {"model": AdminErrorResponse, "description": "Invalid upstream contract response"},
    503: {"model": AdminErrorResponse, "description": "Hermes internal-admin unavailable"},
}

_CITY_OPERATIONAL_ERRORS = {
    "CITY_PREPARING",
    "CITY_COLLECTION_FAILED",
    "CITY_DATA_INSUFFICIENT",
    "CITY_DISABLED",
}
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _request_id(request: Request) -> str:
    return request.state.correlation_id


def _source_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat().replace("+00:00", "Z") if normalized else None


def _validate_time_range(
    time_from: datetime | None,
    time_to: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    normalized_from = _as_utc(time_from)
    normalized_to = _as_utc(time_to)
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_from > normalized_to
    ):
        raise ApiError(422, "VALIDATION_ERROR", "起始时间不能晚于结束时间。")
    return normalized_from, normalized_to


def _trip_projection(job: HermesAdminTripJob) -> dict[str, object]:
    error_code = job.safe_error.code if job.safe_error else None
    exception_kind = None
    if job.status in {"PENDING", "RUNNING"} and job.total_duration_ms >= 180_000:
        exception_kind = "SLOW"
    elif job.status in {"FAILED", "TIMEOUT"}:
        exception_kind = "TERMINAL_FAILURE"
    elif job.status == "SUCCESS" and job.result_type in {
        "NO_CANDIDATES",
        "NO_USABLE_ROUTE",
    }:
        exception_kind = "DEGRADED"
    elif error_code in _CITY_OPERATIONAL_ERRORS:
        exception_kind = "CITY_OPERATIONAL"
    payload = job.model_dump(mode="json", exclude_none=True)
    payload.update(
        {
            "slow": exception_kind == "SLOW",
            "is_exception": exception_kind is not None,
            "exception_kind": exception_kind,
        }
    )
    return payload


def _upstream_error(exc: Exception) -> ApiError:
    if isinstance(exc, HermesBusinessError):
        mappings = {
            "TRIP_JOB_NOT_FOUND": (404, "TRIP_JOB_NOT_FOUND", "任务不存在。"),
            "FAILED_DRAFT_NOT_FOUND": (
                404,
                "FAILED_DRAFT_NOT_FOUND",
                "失败草稿不存在。",
            ),
            "ARTIFACT_NOT_FOUND": (404, "ARTIFACT_NOT_FOUND", "文件不存在。"),
            "ARTIFACT_NOT_READY": (409, "ARTIFACT_NOT_READY", "文件尚未就绪。"),
            "ARTIFACT_FILE_MISSING": (
                409,
                "ARTIFACT_FILE_MISSING",
                "文件记录存在，但二进制已缺失。",
            ),
            "ARTIFACT_EXPIRED": (410, "ARTIFACT_EXPIRED", "文件已过期。"),
        }
        status, code, message = mappings.get(
            exc.code,
            (502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。"),
        )
        return ApiError(status, code, message)
    if isinstance(exc, HermesIntegrationError) and exc.category == "UNAVAILABLE":
        return ApiError(
            503,
            "GENERATION_SERVICE_UNAVAILABLE",
            "生成服务暂时不可用。",
            retryable=True,
        )
    return ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回了无效响应。")


def _audit_error_code(exc: Exception) -> str:
    return _upstream_error(exc).code


async def _audit_sensitive_read(
    db: AsyncSession,
    request: Request,
    admin: AdminContext,
    *,
    action: str,
    target_type: str,
    target_id: str,
    result: str,
    error_code: str | None = None,
    after: dict[str, object] | None = None,
) -> None:
    await append_admin_audit(
        db,
        request.app.state.settings,
        actor_user_id=admin.user.id,
        actor_identity=admin.product_identity,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        error_code=error_code,
        request_id=_request_id(request),
        source_ip=_source_ip(request),
        after=after,
        client={"user_agent": request.headers.get("user-agent", "")[:200]},
    )
    await db.commit()


@router.get(
    "/trip-jobs",
    response_model=AdminTripJobListResponse,
    responses=COMMON_READ_RESPONSES,
)
async def admin_trip_jobs(
    request: Request,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    city: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    status: TripStatus | None = None,
    result_type: TripResultType | None = None,
    error_code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    detailed_reason: DetailedReason | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _admin: AdminContext = CURRENT_ADMIN,
) -> dict[str, object]:
    normalized_from, normalized_to = _validate_time_range(time_from, time_to)
    if normalized_from is None:
        normalized_from = datetime.now(UTC) - timedelta(days=7)
    try:
        upstream = await request.app.state.hermes.admin_trip_jobs(
            correlation_id=_request_id(request),
            params={
                "time_from": _iso(normalized_from),
                "time_to": _iso(normalized_to),
                "city": city.strip() if city else None,
                "status": status,
                "result_type": result_type,
                "error_code": error_code.strip().upper() if error_code else None,
                "detailed_reason": detailed_reason,
                "page": page,
                "limit": limit,
            },
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return {
        "ok": True,
        "request_id": _request_id(request),
        "page": upstream.page,
        "limit": upstream.limit,
        "total": upstream.total,
        "items": [_trip_projection(item) for item in upstream.items],
    }


@router.get(
    "/trip-jobs/{job_id}",
    response_model=AdminTripJobDetailResponse,
    responses={
        **COMMON_READ_RESPONSES,
        404: {"model": AdminErrorResponse, "description": "Trip job not found"},
    },
)
async def admin_trip_job(
    job_id: Annotated[str, Path(min_length=1, max_length=160)],
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
) -> dict[str, object]:
    try:
        upstream = await request.app.state.hermes.admin_trip_job(
            job_id,
            correlation_id=_request_id(request),
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return {
        "ok": True,
        "request_id": _request_id(request),
        "trip_job": _trip_projection(upstream.trip_job),
    }


@router.get(
    "/trip-jobs/{job_id}/failed-draft",
    response_model=AdminFailedDraftResponse,
    responses={
        **COMMON_READ_RESPONSES,
        404: {"model": AdminErrorResponse, "description": "Failed draft not found"},
    },
)
async def admin_failed_draft(
    job_id: Annotated[str, Path(min_length=1, max_length=160)],
    request: Request,
    response: Response,
    admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    try:
        upstream = await request.app.state.hermes.admin_failed_draft(
            job_id,
            correlation_id=_request_id(request),
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        await _audit_sensitive_read(
            db,
            request,
            admin,
            action="VIEW_FAILED_DRAFT",
            target_type="TRIP_JOB",
            target_id=job_id,
            result="FAILURE",
            error_code=_audit_error_code(exc),
        )
        raise _upstream_error(exc) from exc
    await _audit_sensitive_read(
        db,
        request,
        admin,
        action="VIEW_FAILED_DRAFT",
        target_type="TRIP_JOB",
        target_id=job_id,
        result="SUCCESS",
        after={
            "publication_status": "UNPUBLISHED_DIAGNOSTIC",
            "plan_count": len(upstream.failed_draft.plans),
        },
    )
    response.headers["Cache-Control"] = "private, no-store"
    draft = upstream.failed_draft.model_dump(mode="json")
    draft["publication_status"] = "UNPUBLISHED_DIAGNOSTIC"
    return {
        "ok": True,
        "request_id": _request_id(request),
        "failed_draft": draft,
    }


@router.get(
    "/artifacts",
    response_model=AdminArtifactListResponse,
    responses=COMMON_READ_RESPONSES,
)
async def admin_artifacts(
    request: Request,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    artifact_type: ArtifactType | None = None,
    status: ArtifactStatus | None = None,
    result_record_id: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _admin: AdminContext = CURRENT_ADMIN,
) -> dict[str, object]:
    normalized_from, normalized_to = _validate_time_range(time_from, time_to)
    try:
        upstream = await request.app.state.hermes.admin_artifacts(
            correlation_id=_request_id(request),
            params={
                "time_from": _iso(normalized_from),
                "time_to": _iso(normalized_to),
                "artifact_type": artifact_type,
                "status": status,
                "result_record_id": result_record_id,
                "page": page,
                "limit": limit,
            },
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return {
        "ok": True,
        "request_id": _request_id(request),
        "page": upstream.page,
        "limit": upstream.limit,
        "total": upstream.total,
        "items": [item.model_dump(mode="json", exclude_none=True) for item in upstream.items],
    }


@router.get(
    "/artifacts/{artifact_id}",
    response_model=AdminArtifactDetailResponse,
    responses={
        **COMMON_READ_RESPONSES,
        404: {"model": AdminErrorResponse, "description": "Artifact not found"},
    },
)
async def admin_artifact(
    artifact_id: Annotated[str, Path(min_length=1, max_length=160)],
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
) -> dict[str, object]:
    try:
        upstream = await request.app.state.hermes.admin_artifact(
            artifact_id,
            correlation_id=_request_id(request),
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        raise _upstream_error(exc) from exc
    return {
        "ok": True,
        "request_id": _request_id(request),
        "artifact": upstream.artifact.model_dump(mode="json", exclude_none=True),
    }


@router.get(
    "/artifacts/{artifact_id}/download",
    response_class=Response,
    responses={
        **COMMON_READ_RESPONSES,
        200: {
            "description": "Existing READY Artifact bytes",
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"},
                },
                "image/png": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        },
        404: {"model": AdminErrorResponse, "description": "Artifact not found"},
        409: {
            "model": AdminErrorResponse,
            "description": "Artifact not ready or file missing",
        },
        410: {"model": AdminErrorResponse, "description": "Artifact expired"},
    },
)
async def admin_artifact_download(
    artifact_id: Annotated[str, Path(min_length=1, max_length=160)],
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> Response:
    try:
        content, content_type = await request.app.state.hermes.admin_artifact_bytes(
            artifact_id,
            correlation_id=_request_id(request),
            max_bytes=request.app.state.settings.artifact_max_bytes,
        )
    except (HermesBusinessError, HermesIntegrationError) as exc:
        await _audit_sensitive_read(
            db,
            request,
            admin,
            action="DOWNLOAD_ARTIFACT",
            target_type="ARTIFACT",
            target_id=artifact_id,
            result="FAILURE",
            error_code=_audit_error_code(exc),
        )
        raise _upstream_error(exc) from exc
    await _audit_sensitive_read(
        db,
        request,
        admin,
        action="DOWNLOAD_ARTIFACT",
        target_type="ARTIFACT",
        target_id=artifact_id,
        result="SUCCESS",
        after={"byte_size": len(content), "mime_type": content_type},
    )
    extension = "pdf" if content_type == "application/pdf" else "png"
    safe_id = _SAFE_FILENAME.sub("-", artifact_id).strip("._-")[:80] or "artifact"
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": (f'attachment; filename="artifact-{safe_id}.{extension}"'),
            "Cache-Control": "private, no-store",
            "Content-Length": str(len(content)),
        },
    )
