from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.audit import append_admin_audit
from src.admin.auth import AdminContext, get_current_admin, resolve_admin_context
from src.admin.projection import (
    RUNTIME_POLICY,
    STAGE_LABELS_ZH,
    ProjectionUnavailable,
    current_projection_with_name,
    projection_health,
    runtime_projection,
    trip_summary,
)
from src.admin.projection_schemas import (
    AdminGenerationPipelineResponse,
    AdminGuideReviewResponse,
    AdminTripJobDetailResponse,
    AdminTripJobListResponse,
    AdminUserTripListResponse,
    StructuredRequest,
    TraceCompleteness,
)
from src.admin.schemas import AdminErrorResponse
from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.db.models import (
    AdminTripProjection,
    AdminTripStepProjection,
    AppUser,
    UserTrip,
)
from src.db.session import get_db_session
from src.integrations.hermes import HermesBusinessError, HermesIntegrationError

logger = logging.getLogger("travel_web_api.admin_projection_routes")
router = APIRouter(prefix="/api/admin", tags=["admin"])
CURRENT_ADMIN = Depends(get_current_admin)
CURRENT_AUTH = Depends(get_current_auth)
DB_SESSION = Depends(get_db_session)

TripStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "REJECTED"]
ResultType = Literal["PLAN_READY", "NO_CANDIDATES", "NO_USABLE_ROUTE", "UNKNOWN"]
AssociationState = Literal["linked", "de-identified", "unlinked"]
StageOutcome = Literal["RUNNING", "SUCCESS", "FAILED", "TIMEOUT"]
TRIP_JOB_PAGE_LIMITS = (10, 20, 50, 100)
USER_TRIP_JOB_PAGE_LIMITS = (10, 20)

COMMON_RESPONSES = {
    401: {"model": AdminErrorResponse, "description": "Authenticated session required"},
    403: {"model": AdminErrorResponse, "description": "Administrator capability required"},
    422: {"model": AdminErrorResponse, "description": "Invalid allowlisted filter"},
    503: {"model": AdminErrorResponse, "description": "Projection is unavailable"},
}


def _request_id(request: Request) -> str:
    return request.state.correlation_id


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _projection_error(exc: ProjectionUnavailable) -> ApiError:
    return ApiError(
        503,
        "PROJECTION_UNAVAILABLE",
        "攻略运营数据暂不可用。",
        retryable=True,
    )


def _validate_time_range(
    request: Request,
    time_from: datetime | None,
    time_to: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    start = _utc(time_from)
    end = _utc(time_to)
    if start is not None and end is not None:
        if start >= end:
            raise ApiError(422, "VALIDATION_ERROR", "查询时间范围不合法。")
        maximum = timedelta(days=request.app.state.settings.admin_projection_max_range_days)
        if end - start > maximum:
            raise ApiError(422, "VALIDATION_ERROR", "查询时间范围过大。")
    return start, end


async def _health(session: AsyncSession, *, sensitive: bool = False):
    try:
        return await projection_health(session, sensitive=sensitive)
    except ProjectionUnavailable as exc:
        raise _projection_error(exc) from exc


async def _resolve_public_user_id(session: AsyncSession, public_id: str) -> Any | None:
    return await session.scalar(select(AppUser.id).where(AppUser.public_id == public_id))


def _projection_filters(
    *,
    time_from: datetime | None,
    time_to: datetime | None,
    association_state: AssociationState | None,
    internal_user_id: Any | None,
    user_filter_requested: bool,
    source: str | None,
    city: str | None,
    status: TripStatus | None,
    result_type: ResultType | None,
    error_code: str | None,
    detailed_reason: str | None,
    executed_stage: str | None,
    stage_outcome: StageOutcome | None,
    stage_started_from: datetime | None,
    stage_started_to: datetime | None,
    trace_completeness: list[TraceCompleteness] | None,
    has_final_guide: bool | None,
) -> list[Any]:
    filters: list[Any] = []
    if time_from is not None:
        filters.append(AdminTripProjection.created_at >= time_from)
    if time_to is not None:
        filters.append(AdminTripProjection.created_at < time_to)
    if association_state is not None:
        filters.append(AdminTripProjection.association_state == association_state)
    if user_filter_requested:
        filters.append(AdminTripProjection.user_id == internal_user_id)
        filters.append(AdminTripProjection.association_state == "linked")
        filters.append(AdminTripProjection.identity_erased_at.is_(None))
    if source:
        filters.append(AdminTripProjection.source == source)
    if city:
        filters.append(AdminTripProjection.city == city)
    if status:
        filters.append(AdminTripProjection.status == status)
    if result_type:
        filters.append(AdminTripProjection.result_type == result_type)
    if error_code:
        filters.append(AdminTripProjection.error_code == error_code)
    if detailed_reason:
        filters.append(AdminTripProjection.detailed_reason == detailed_reason)
    if trace_completeness:
        filters.append(AdminTripProjection.trace_completeness.in_(trace_completeness))
    if has_final_guide is not None:
        filters.append(
            AdminTripProjection.guide_result_state == "AVAILABLE"
            if has_final_guide
            else AdminTripProjection.guide_result_state != "AVAILABLE"
        )
    if executed_stage is not None:
        step_filters = [
            AdminTripStepProjection.job_id == AdminTripProjection.job_id,
            AdminTripStepProjection.stage == executed_stage,
        ]
        if stage_outcome:
            step_filters.append(AdminTripStepProjection.status == stage_outcome)
        if stage_started_from:
            step_filters.append(AdminTripStepProjection.started_at >= stage_started_from)
        if stage_started_to:
            step_filters.append(AdminTripStepProjection.started_at < stage_started_to)
        filters.append(exists(select(1).where(*step_filters)))
    return filters


def _validate_page_limit(limit: int, allowed: tuple[int, ...]) -> int:
    if limit not in allowed:
        raise ApiError(422, "VALIDATION_ERROR", "请求参数无效。")
    return limit


@router.get(
    "/trip-jobs",
    response_model=AdminTripJobListResponse,
    responses=COMMON_RESPONSES,
)
async def admin_trip_jobs(
    request: Request,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    association_state: AssociationState | None = None,
    user_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    city: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    status: TripStatus | None = None,
    result_type: ResultType | None = None,
    error_code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    detailed_reason: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    executed_stage: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    stage_outcome: StageOutcome | None = None,
    stage_started_from: datetime | None = None,
    stage_started_to: datetime | None = None,
    trace_completeness: Annotated[list[TraceCompleteness] | None, Query()] = None,
    has_final_guide: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[
        int,
        Query(ge=1, json_schema_extra={"enum": list(TRIP_JOB_PAGE_LIMITS)}),
    ] = 20,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    limit = _validate_page_limit(limit, TRIP_JOB_PAGE_LIMITS)
    start, end = _validate_time_range(request, time_from, time_to)
    stage_start, stage_end = _validate_time_range(request, stage_started_from, stage_started_to)
    if executed_stage is None and any(
        value is not None for value in (stage_outcome, stage_start, stage_end)
    ):
        raise ApiError(422, "VALIDATION_ERROR", "阶段筛选必须指定 executed_stage。")
    health = await _health(db)
    internal_user_id = await _resolve_public_user_id(db, user_id) if user_id else None
    filters = _projection_filters(
        time_from=start,
        time_to=end,
        association_state=association_state,
        internal_user_id=internal_user_id,
        user_filter_requested=user_id is not None,
        source=source.strip() if source else None,
        city=city.strip() if city else None,
        status=status,
        result_type=result_type,
        error_code=error_code.strip().upper() if error_code else None,
        detailed_reason=detailed_reason.strip().lower() if detailed_reason else None,
        executed_stage=executed_stage,
        stage_outcome=stage_outcome,
        stage_started_from=stage_start,
        stage_started_to=stage_end,
        trace_completeness=trace_completeness,
        has_final_guide=has_final_guide,
    )
    total = int(
        await db.scalar(select(func.count()).select_from(AdminTripProjection).where(*filters)) or 0
    )
    rows = (
        await db.execute(
            select(AdminTripProjection, AppUser.public_id, AppUser.display_name)
            .outerjoin(AppUser, AppUser.id == AdminTripProjection.user_id)
            .where(*filters)
            .order_by(AdminTripProjection.created_at.desc(), AdminTripProjection.job_id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    return {
        "ok": True,
        "request_id": _request_id(request),
        "as_of": health.as_of,
        "page": page,
        "limit": limit,
        "total": total,
        "items": [
            trip_summary(
                row,
                user_public_id=public_id,
                display_name=display_name,
                as_of=health.as_of,
            )
            for row, public_id, display_name in rows
        ],
        "freshness": health.freshness,
        "projection_alarm": health.alarm,
    }


@router.get(
    "/trip-jobs/{job_id}",
    response_model=AdminTripJobDetailResponse,
    responses={
        **COMMON_RESPONSES,
        404: {"model": AdminErrorResponse, "description": "Trip Attempt not found"},
    },
)
async def admin_trip_job(
    job_id: Annotated[str, Path(min_length=1, max_length=160)],
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    health = await _health(db)
    current = await current_projection_with_name(db, job_id)
    if current is None:
        raise ApiError(404, "TRIP_JOB_NOT_FOUND", "未找到攻略任务。")
    row, public_id, display_name = current
    steps = (
        await db.scalars(
            select(AdminTripStepProjection)
            .where(AdminTripStepProjection.job_id == job_id)
            .order_by(
                AdminTripStepProjection.started_at,
                AdminTripStepProjection.source_step_id,
            )
        )
    ).all()
    return {
        "ok": True,
        "request_id": _request_id(request),
        "as_of": health.as_of,
        "trip_job": trip_summary(
            row,
            user_public_id=public_id,
            display_name=display_name,
            as_of=health.as_of,
        ),
        "steps": [
            {
                "source_step_id": step.source_step_id,
                "stage": step.stage,
                "stage_label_zh": STAGE_LABELS_ZH.get(step.stage, "未知阶段"),
                "status": step.status,
                "attempt": step.attempt,
                "publish_retry_round": step.publish_retry_round,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "duration_ms": step.duration_ms,
            }
            for step in steps
        ],
        "freshness": health.freshness,
        "projection_alarm": health.alarm,
    }


@router.get(
    "/users/{user_id}/trip-jobs",
    response_model=AdminUserTripListResponse,
    responses=COMMON_RESPONSES,
)
async def admin_user_trip_jobs(
    user_id: Annotated[str, Path(min_length=1, max_length=80)],
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[
        int,
        Query(ge=1, json_schema_extra={"enum": list(USER_TRIP_JOB_PAGE_LIMITS)}),
    ] = 10,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    limit = _validate_page_limit(limit, USER_TRIP_JOB_PAGE_LIMITS)
    health = await _health(db)
    internal_user_id = await _resolve_public_user_id(db, user_id)
    filters = [
        AdminTripProjection.user_id == internal_user_id,
        AdminTripProjection.association_state == "linked",
        AdminTripProjection.identity_erased_at.is_(None),
    ]
    total = int(
        await db.scalar(select(func.count()).select_from(AdminTripProjection).where(*filters)) or 0
    )
    rows = (
        await db.execute(
            select(AdminTripProjection, AppUser.public_id, AppUser.display_name)
            .join(AppUser, AppUser.id == AdminTripProjection.user_id)
            .where(*filters)
            .order_by(AdminTripProjection.created_at.desc(), AdminTripProjection.job_id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    return {
        "ok": True,
        "request_id": _request_id(request),
        "as_of": health.as_of,
        "page": page,
        "limit": limit,
        "total": total,
        "items": [
            trip_summary(
                row,
                user_public_id=public_id,
                display_name=display_name,
                as_of=health.as_of,
            )
            for row, public_id, display_name in rows
        ],
        "freshness": health.freshness,
        "projection_alarm": health.alarm,
    }


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


@router.get(
    "/generation-pipeline",
    response_model=AdminGenerationPipelineResponse,
    responses=COMMON_RESPONSES,
)
async def admin_generation_pipeline(
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    health = await _health(db)
    window_to = health.as_of
    window_from = window_to - timedelta(hours=24)
    jobs = list(
        (
            await db.scalars(
                select(AdminTripProjection).where(
                    AdminTripProjection.created_at >= window_from,
                    AdminTripProjection.created_at < window_to,
                )
            )
        ).all()
    )
    terminal = [job for job in jobs if job.status in {"SUCCESS", "FAILED", "TIMEOUT", "REJECTED"}]
    terminal_success = [job for job in terminal if job.status == "SUCCESS"]
    published = [job for job in jobs if job.guide_result_state == "AVAILABLE"]
    no_guide = [job for job in jobs if job.guide_result_state == "LEGAL_NO_GUIDE"]
    terminal_failure = [job for job in terminal if job.status in {"FAILED", "TIMEOUT", "REJECTED"}]
    backlog_count = int(
        await db.scalar(
            select(func.count())
            .select_from(AdminTripProjection)
            .where(AdminTripProjection.status.in_(["PENDING", "RUNNING"]))
        )
        or 0
    )
    runtimes = [runtime_projection(job, health.as_of) for job in jobs]
    step_rows = (
        await db.execute(
            select(AdminTripStepProjection, AdminTripProjection.trace_completeness)
            .join(
                AdminTripProjection,
                AdminTripProjection.job_id == AdminTripStepProjection.job_id,
            )
            .where(
                AdminTripStepProjection.started_at >= window_from,
                AdminTripStepProjection.started_at < window_to,
                AdminTripStepProjection.stage != "INTENT_PARSER",
            )
        )
    ).all()
    excluded_jobs = {step.job_id for step, completeness in step_rows if completeness != "COMPLETE"}
    by_stage: dict[str, list[AdminTripStepProjection]] = {}
    excluded_by_stage: dict[str, set[str]] = {}
    for step, completeness in step_rows:
        if completeness == "COMPLETE":
            by_stage.setdefault(step.stage, []).append(step)
        else:
            excluded_by_stage.setdefault(step.stage, set()).add(step.job_id)
    preferred_order = list(STAGE_LABELS_ZH)
    stages = sorted(
        by_stage,
        key=lambda stage: (
            preferred_order.index(stage) if stage in preferred_order else len(preferred_order),
            stage,
        ),
    )
    nodes: list[dict[str, Any]] = []
    for stage in stages:
        rows = by_stage[stage]
        ended = [row for row in rows if row.status != "RUNNING"]
        successful = [row for row in rows if row.status == "SUCCESS"]
        durations = [row.duration_ms for row in successful if row.duration_ms is not None]
        nodes.append(
            {
                "stage": stage,
                "stage_label_zh": STAGE_LABELS_ZH.get(stage, "未知阶段"),
                "task_count": len({row.job_id for row in rows}),
                "execution_count": len(rows),
                "running_count": len({row.job_id for row in rows if row.status == "RUNNING"}),
                "success_count": len(successful),
                "failed_count": sum(row.status == "FAILED" for row in rows),
                "timeout_count": sum(row.status == "TIMEOUT" for row in rows),
                "retry_count": sum(row.attempt > 1 or row.publish_retry_round > 0 for row in rows),
                "excluded_trace_task_count": len(excluded_by_stage.get(stage, set())),
                "ended_count": len(ended),
                "success_rate": len(successful) / len(ended) if ended else None,
                "duration_ms": {
                    "p50": _nearest_rank(durations, 0.50),
                    "p95": _nearest_rank(durations, 0.95),
                },
            }
        )
    terminal_count = len(terminal)
    return {
        "ok": True,
        "request_id": _request_id(request),
        "window": {"from": window_from, "to": window_to},
        "as_of": health.as_of,
        "runtime_policy": RUNTIME_POLICY,
        "overview": {
            "created_task_count": len(jobs),
            "terminal_task_count": terminal_count,
            "terminal_success_count": len(terminal_success),
            "published_guide_count": len(published),
            "no_guide_success_count": len(no_guide),
            "terminal_failure_count": len(terminal_failure),
            "backlog_task_count": backlog_count,
            "slow_task_count": sum(item["is_slow"] for item in runtimes),
            "timeout_settlement_anomaly_count": sum(
                item["timeout_settlement_anomaly"] for item in runtimes
            ),
            "terminal_success_rate": (
                len(terminal_success) / terminal_count if terminal_count else None
            ),
            "published_guide_rate": len(published) / terminal_count if terminal_count else None,
            "excluded_trace_task_count": len(excluded_jobs),
        },
        "nodes": nodes,
        "freshness": health.freshness,
        "projection_alarm": health.alarm,
    }


class _AuditCommitFailed(RuntimeError):
    pass


async def _audit_guide_outcome(
    request: Request,
    admin: AdminContext,
    *,
    job_id: str,
    result: Literal["SUCCESS", "FAILURE"],
    error_code: str | None,
    result_record_id: int | None = None,
) -> None:
    if admin.is_owner:
        return
    try:
        async with request.app.state.session_factory() as session, session.begin():
            await append_admin_audit(
                session,
                request.app.state.settings,
                actor_user_id=admin.user.id,
                actor_identity=admin.product_identity,
                action="READ_GUIDE_REVIEW",
                target_type="TRIP_JOB",
                target_id=job_id,
                result=result,
                error_code=error_code,
                request_id=_request_id(request),
                source_ip=request.client.host if request.client else "unknown",
                after=(
                    {"result_record_id": result_record_id} if result_record_id is not None else None
                ),
                client={"user_agent": request.headers.get("user-agent", "")[:200]},
            )
    except Exception as exc:
        logger.exception("guide review audit commit failed")
        raise _AuditCommitFailed from exc


def _guide_error(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, HermesBusinessError):
        if exc.code in {"GUIDE_NOT_AVAILABLE", "GUIDE_RESULT_INCONSISTENT", "TRIP_JOB_NOT_FOUND"}:
            return ApiError(
                500,
                "GUIDE_RESULT_INCONSISTENT",
                "攻略结果数据不一致。",
                retryable=True,
            )
        return ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回异常。", retryable=True)
    if isinstance(exc, HermesIntegrationError) and exc.category == "UNAVAILABLE":
        return ApiError(
            503,
            "GENERATION_SERVICE_UNAVAILABLE",
            "生成服务暂不可用。",
            retryable=True,
        )
    return ApiError(502, "GENERATION_SERVICE_ERROR", "生成服务返回异常。", retryable=True)


def _request_from_bff(trip: UserTrip | None) -> StructuredRequest | None:
    if trip is None or trip.identity_erased_at is not None:
        return None
    allowed = {
        "from_city",
        "to_city",
        "start_date",
        "end_date",
        "days",
        "people_count",
        "preferences",
        "avoid",
        "notes",
        "budget",
        "must_include",
        "commute_mode",
        "daily_start",
        "daily_end",
        "rest_windows",
        "accommodation",
    }
    keys = {
        key
        for key, provenance in trip.request_field_provenance.items()
        if key in allowed and provenance == "USER_SUPPLIED" and key in trip.request_json
    }
    if not keys:
        return None
    return StructuredRequest.model_validate(
        {
            "values": {key: trip.request_json[key] for key in sorted(keys)},
            "field_provenance": {key: "USER_SUPPLIED" for key in sorted(keys)},
        }
    )


def _safe_artifacts(items: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": item.artifact_id,
            "artifact_type": item.artifact_type,
            "status": item.status,
            "filename": item.filename,
            "mime_type": item.mime_type,
            "byte_size": item.byte_size,
            "created_at": item.created_at,
            "expires_at": item.expires_at,
        }
        for item in items
    ]


@router.get(
    "/trip-jobs/{job_id}/guide-review",
    response_model=AdminGuideReviewResponse,
    responses={
        **COMMON_RESPONSES,
        404: {"model": AdminErrorResponse, "description": "Trip Attempt not found"},
        409: {"model": AdminErrorResponse, "description": "Guide is not available"},
        500: {"model": AdminErrorResponse, "description": "Guide result is inconsistent"},
        502: {"model": AdminErrorResponse, "description": "Invalid generation response"},
    },
)
async def admin_guide_review(
    job_id: Annotated[str, Path(min_length=1, max_length=160)],
    request: Request,
    response: Response,
    auth: AuthContext = CURRENT_AUTH,
) -> dict[str, Any]:
    admin = resolve_admin_context(auth, request.app.state.settings)
    audit_started = False
    audit_committed = False
    if admin.product_identity == "USER":
        error = ApiError(403, "ADMIN_FORBIDDEN", "无权执行此操作。")
        try:
            await _audit_guide_outcome(
                request,
                admin,
                job_id=job_id,
                result="FAILURE",
                error_code=error.code,
            )
        except _AuditCommitFailed:
            raise ApiError(503, "AUDIT_UNAVAILABLE", "审计服务暂不可用。", retryable=True) from None
        raise error

    try:
        async with request.app.state.session_factory() as session:
            health = await _health(session, sensitive=True)
            preliminary = await current_projection_with_name(session, job_id)
        if preliminary is None:
            raise ApiError(404, "TRIP_JOB_NOT_FOUND", "未找到攻略任务。")
        preliminary_row = preliminary[0]
        if preliminary_row.guide_result_state == "INCONSISTENT":
            raise ApiError(
                500,
                "GUIDE_RESULT_INCONSISTENT",
                "攻略结果数据不一致。",
                retryable=True,
            )
        if preliminary_row.guide_result_state != "AVAILABLE":
            raise ApiError(409, "GUIDE_NOT_AVAILABLE", "当前没有可查看的攻略。")
        result_record_id = preliminary_row.result_record_id
        if result_record_id is None:
            raise ApiError(
                500,
                "GUIDE_RESULT_INCONSISTENT",
                "攻略结果数据不一致。",
                retryable=True,
            )
        fragment = None
        if health.freshness.projection_state == "FRESH":
            try:
                fragment = await request.app.state.guide_fragment_cache.get(result_record_id)
            except Exception:
                logger.warning("guide fragment cache read failed", exc_info=True)
        authoritative = None
        if fragment is None or health.freshness.projection_state == "LAGGING":
            authoritative = await request.app.state.hermes.admin_guide_result(
                job_id,
                correlation_id=_request_id(request),
            )
            if authoritative.result_record_id != result_record_id:
                await request.app.state.guide_fragment_cache.invalidate(result_record_id)
                raise ApiError(
                    500,
                    "GUIDE_RESULT_INCONSISTENT",
                    "攻略结果数据不一致。",
                    retryable=True,
                )
            fragment = {
                "request": (
                    authoritative.request.model_dump(mode="json")
                    if authoritative.request is not None
                    else None
                ),
                "final_guide": authoritative.final_guide.model_dump(mode="json"),
            }
            artifacts = _safe_artifacts(authoritative.artifacts)
        else:
            artifact_page = await request.app.state.hermes.admin_artifacts(
                correlation_id=_request_id(request),
                params={
                    "time_from": None,
                    "time_to": None,
                    "artifact_type": None,
                    "status": None,
                    "result_record_id": result_record_id,
                    "page": 1,
                    "limit": 100,
                },
            )
            artifacts = _safe_artifacts(artifact_page.items)

        async with request.app.state.session_factory() as session, session.begin():
            trip = await session.scalar(
                select(UserTrip)
                .where(UserTrip.hermes_job_id == job_id)
                .order_by(UserTrip.id)
                .with_for_update()
            )
            current = await current_projection_with_name(session, job_id, lock=True)
            if current is None:
                raise ApiError(404, "TRIP_JOB_NOT_FOUND", "未找到攻略任务。")
            row, public_id, display_name = current
            if row.guide_result_state != "AVAILABLE" or row.result_record_id != result_record_id:
                raise ApiError(
                    500,
                    "GUIDE_RESULT_INCONSISTENT",
                    "攻略结果数据不一致。",
                    retryable=True,
                )
            bff_request = _request_from_bff(trip)
            request_payload = bff_request
            request_source = "BFF_USER_TRIP"
            if request_payload is None and fragment.get("request") is not None:
                request_payload = StructuredRequest.model_validate(fragment["request"])
                request_source = "HERMES_CANONICAL"
            if request_payload is None:
                request_source = "UNAVAILABLE"
            payload = AdminGuideReviewResponse.model_validate(
                {
                    "ok": True,
                    "request_id": _request_id(request),
                    "as_of": health.as_of,
                    "trip_job": trip_summary(
                        row,
                        user_public_id=public_id,
                        display_name=display_name,
                        as_of=health.as_of,
                    ),
                    "request": request_payload,
                    "request_source": request_source,
                    "final_guide": fragment["final_guide"],
                    "artifacts": artifacts,
                    "freshness": health.freshness,
                    "projection_alarm": None,
                }
            )
            if not admin.is_owner:
                audit_started = True
                await append_admin_audit(
                    session,
                    request.app.state.settings,
                    actor_user_id=admin.user.id,
                    actor_identity=admin.product_identity,
                    action="READ_GUIDE_REVIEW",
                    target_type="TRIP_JOB",
                    target_id=job_id,
                    result="SUCCESS",
                    error_code=None,
                    request_id=_request_id(request),
                    source_ip=request.client.host if request.client else "unknown",
                    after={"result_record_id": result_record_id},
                    client={"user_agent": request.headers.get("user-agent", "")[:200]},
                )
        audit_committed = audit_started
        if authoritative is not None:
            try:
                await request.app.state.guide_fragment_cache.set(result_record_id, fragment)
            except Exception:
                logger.warning("guide fragment cache write failed", exc_info=True)
        response.headers["Cache-Control"] = "private, no-store"
        return payload.model_dump(mode="json", by_alias=True)
    except _AuditCommitFailed:
        raise ApiError(503, "AUDIT_UNAVAILABLE", "审计服务暂不可用。", retryable=True) from None
    except Exception as exc:
        if audit_started and not audit_committed:
            logger.exception("guide review success audit failed closed")
            raise ApiError(503, "AUDIT_UNAVAILABLE", "审计服务暂不可用。", retryable=True) from None
        error = _guide_error(exc)
        if not admin.is_owner:
            try:
                await _audit_guide_outcome(
                    request,
                    admin,
                    job_id=job_id,
                    result="FAILURE",
                    error_code=error.code,
                )
            except _AuditCommitFailed:
                raise ApiError(
                    503, "AUDIT_UNAVAILABLE", "审计服务暂不可用。", retryable=True
                ) from None
        raise error from exc
