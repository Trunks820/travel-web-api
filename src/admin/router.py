from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.audit import append_admin_audit
from src.admin.auth import AdminContext, get_current_admin
from src.admin.invitations import (
    batch_detail,
    create_batch,
    disable_invitation_resource,
    invitation_status,
    list_batches,
    lookup_code,
)
from src.admin.reports import (
    AUDIT_ACTIONS,
    AUDIT_RESULTS,
    audit_events,
    dashboard,
    preference_report,
    trip_generation_report,
)
from src.admin.schemas import (
    AdminAuditEventListResponse,
    AdminDashboardResponse,
    AdminErrorResponse,
    AdminInvitationBatchCreateResponse,
    AdminInvitationBatchDetailResponse,
    AdminInvitationBatchListResponse,
    AdminInvitationCodeLookupResponse,
    AdminMeResponse,
    AdminMutation,
    AdminPreferenceReportResponse,
    AdminQuotaAdjustmentResponse,
    AdminQuotaLedgerResponse,
    AdminResourceDisabledResponse,
    AdminTripGenerationReportResponse,
    AdminUserDetailResponse,
    AdminUserEmailResponse,
    AdminUserListResponse,
    AdminUserMutationResponse,
    InvitationBatchRequest,
    InvitationCodeLookup,
    QuotaAdjustmentRequest,
    QuotaReversalRequest,
)
from src.admin.service import (
    AdminOperationError,
    create_quota_adjustment,
    find_user_by_public_id,
    list_admin_users,
    mutate_user,
    public_admin_user,
    quota_ledger,
    reverse_quota_adjustment,
)
from src.api.errors import ApiError
from src.db.models import InvitationBatch, UserIdentity
from src.db.session import get_db_session

ADMIN_AUTH_RESPONSES = {
    401: {"model": AdminErrorResponse, "description": "AUTHENTICATION_REQUIRED"},
    403: {
        "model": AdminErrorResponse,
        "description": "ADMIN_REQUIRED, OWNER_REQUIRED, or ADMIN_FORBIDDEN",
    },
    422: {"model": AdminErrorResponse, "description": "VALIDATION_ERROR"},
}
ADMIN_RESOURCE_RESPONSES = {
    **ADMIN_AUTH_RESPONSES,
    404: {"model": AdminErrorResponse, "description": "ADMIN_RESOURCE_NOT_FOUND"},
}
ADMIN_MUTATION_RESPONSES = {
    **ADMIN_RESOURCE_RESPONSES,
    409: {
        "model": AdminErrorResponse,
        "description": "IDEMPOTENCY_CONFLICT or operation-specific state conflict",
    },
}

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    responses=ADMIN_AUTH_RESPONSES,
)
CURRENT_ADMIN = Depends(get_current_admin)
DB_SESSION = Depends(get_db_session)


def _request_id(request: Request) -> str:
    return request.state.correlation_id


def _source_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _track_admin_idempotency(request: Request, key: uuid.UUID) -> None:
    request.state.admin_idempotency_key = key


def _raise_admin(exc: AdminOperationError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message)


@router.get("/me", response_model=AdminMeResponse)
async def admin_me(
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
) -> dict[str, object]:
    return {
        "ok": True,
        "request_id": _request_id(request),
        "user": {
            "user_id": admin.user.public_id,
            "role": admin.user.role,
            "status": admin.user.status,
        },
        "product_identity": admin.product_identity,
        "capabilities": sorted(admin.capabilities),
    }


@router.get("/users", response_model=AdminUserListResponse)
async def admin_users(
    request: Request,
    q: Annotated[str | None, Query(min_length=2, max_length=120)] = None,
    role: Annotated[str | None, Query(pattern="^(USER|ADMIN)$")] = None,
    status: Annotated[str | None, Query(pattern="^(ACTIVE|DISABLED)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    result = await list_admin_users(
        db,
        request.app.state.settings,
        q=q,
        role=role,
        status=status,
        page=page,
        limit=limit,
    )
    return {"ok": True, "request_id": _request_id(request), **result}


@router.get(
    "/users/{user_id}",
    response_model=AdminUserDetailResponse,
    responses=ADMIN_RESOURCE_RESPONSES,
)
async def admin_user_detail(
    user_id: str,
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    try:
        user = await find_user_by_public_id(db, user_id)
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {
        "ok": True,
        "request_id": _request_id(request),
        "user": await public_admin_user(db, request.app.state.settings, user),
    }


@router.get(
    "/users/{user_id}/email",
    response_model=AdminUserEmailResponse,
    responses=ADMIN_RESOURCE_RESPONSES,
)
async def admin_user_email(
    user_id: str,
    request: Request,
    response: Response,
    admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    try:
        user = await find_user_by_public_id(db, user_id)
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    email = await db.scalar(
        select(UserIdentity.verified_email).where(
            UserIdentity.user_id == user.id,
            UserIdentity.provider == "email_otp",
        )
    )
    await append_admin_audit(
        db,
        request.app.state.settings,
        actor_user_id=admin.user.id,
        actor_identity=admin.product_identity,
        action="REVEAL_USER_EMAIL",
        target_type="USER",
        target_id=user.public_id,
        result="SUCCESS",
        request_id=_request_id(request),
        source_ip=_source_ip(request),
        after={"email_revealed": email is not None},
    )
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {
        "ok": True,
        "request_id": _request_id(request),
        "user_id": user.public_id,
        "email": email,
    }


async def _user_mutation(
    user_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext,
    action: str,
) -> dict[str, object]:
    try:
        result = await mutate_user(
            request.app.state.session_factory,
            request.app.state.settings,
            admin,
            target_public_id=user_id,
            action=action,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
            source_ip=_source_ip(request),
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"request_id": _request_id(request), **result}


@router.post(
    "/users/{user_id}/disable",
    response_model=AdminUserMutationResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def disable_user(
    user_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    return await _user_mutation(user_id, body, request, admin, "DISABLE_USER")


@router.post(
    "/users/{user_id}/restore",
    response_model=AdminUserMutationResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def restore_user(
    user_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    return await _user_mutation(user_id, body, request, admin, "RESTORE_USER")


@router.post(
    "/users/{user_id}/grant-admin",
    response_model=AdminUserMutationResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def grant_admin(
    user_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    return await _user_mutation(user_id, body, request, admin, "GRANT_ADMIN")


@router.post(
    "/users/{user_id}/revoke-admin",
    response_model=AdminUserMutationResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def revoke_admin(
    user_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    return await _user_mutation(user_id, body, request, admin, "REVOKE_ADMIN")


@router.get(
    "/users/{user_id}/quota-ledger",
    response_model=AdminQuotaLedgerResponse,
    responses=ADMIN_RESOURCE_RESPONSES,
)
async def admin_quota_ledger(
    user_id: str,
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    try:
        result = await quota_ledger(
            db,
            request.app.state.settings,
            target_public_id=user_id,
            page=page,
            limit=limit,
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"ok": True, "request_id": _request_id(request), **result}


@router.post(
    "/quota-adjustments",
    status_code=201,
    response_model=AdminQuotaAdjustmentResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def adjust_quota(
    body: QuotaAdjustmentRequest,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    try:
        result = await create_quota_adjustment(
            request.app.state.session_factory,
            request.app.state.settings,
            admin,
            target_public_id=body.target_user_id,
            delta=body.delta,
            reason=body.reason,
            note=body.note,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
            source_ip=_source_ip(request),
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"request_id": _request_id(request), **result}


@router.post(
    "/quota-adjustments/{adjustment_id}/reverse",
    status_code=201,
    response_model=AdminQuotaAdjustmentResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def reverse_adjustment(
    adjustment_id: str,
    body: QuotaReversalRequest,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    try:
        result = await reverse_quota_adjustment(
            request.app.state.session_factory,
            request.app.state.settings,
            admin,
            adjustment_public_id=adjustment_id,
            reason=body.reason,
            note=body.note,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
            source_ip=_source_ip(request),
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"request_id": _request_id(request), **result}


@router.get("/invitation-batches", response_model=AdminInvitationBatchListResponse)
async def invitation_batches(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    return {
        "ok": True,
        "request_id": _request_id(request),
        **(await list_batches(db, page=page, limit=limit)),
    }


@router.post(
    "/invitation-batches",
    status_code=201,
    response_model=AdminInvitationBatchCreateResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def create_invitation_batch(
    body: InvitationBatchRequest,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    try:
        result = await create_batch(
            request.app.state.session_factory,
            request.app.state.settings,
            admin,
            name=body.name,
            source_label=body.source_label,
            count=body.count,
            valid_days=body.valid_days,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
            source_ip=_source_ip(request),
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"request_id": _request_id(request), **result}


@router.get(
    "/invitation-batches/{batch_id}",
    response_model=AdminInvitationBatchDetailResponse,
    responses=ADMIN_RESOURCE_RESPONSES,
)
async def invitation_batch(
    batch_id: str,
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    try:
        result = await batch_detail(db, batch_id)
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"ok": True, "request_id": _request_id(request), **result}


@router.post(
    "/invitation-batches/{batch_id}/disable",
    response_model=AdminResourceDisabledResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def disable_invitation_batch(
    batch_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    try:
        result = await disable_invitation_resource(
            request.app.state.session_factory,
            request.app.state.settings,
            admin,
            resource_type="INVITATION_BATCH",
            public_id=batch_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
            source_ip=_source_ip(request),
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"request_id": _request_id(request), **result}


@router.post(
    "/invitation-codes/lookup",
    response_model=AdminInvitationCodeLookupResponse,
    responses=ADMIN_RESOURCE_RESPONSES,
)
async def invitation_code_lookup(
    body: InvitationCodeLookup,
    request: Request,
    response: Response,
    admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    try:
        row = await lookup_code(db, request.app.state.settings, body.code)
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    await append_admin_audit(
        db,
        request.app.state.settings,
        actor_user_id=admin.user.id,
        actor_identity=admin.product_identity,
        action="LOOKUP_INVITATION_CODE",
        target_type="INVITATION_CODE",
        target_id=row.public_id,
        result="SUCCESS",
        request_id=_request_id(request),
        source_ip=_source_ip(request),
    )
    await db.commit()
    batch = await db.get(InvitationBatch, row.batch_id) if row.batch_id else None
    response.headers["Cache-Control"] = "no-store"
    return {
        "ok": True,
        "request_id": _request_id(request),
        "code_id": row.public_id,
        "batch_id": batch.public_id if batch else None,
        "sequence": f"#{row.sequence_number:03d}" if row.sequence_number else None,
        "status": invitation_status(row, datetime.now(UTC)),
    }


@router.post(
    "/invitation-codes/{code_id}/disable",
    response_model=AdminResourceDisabledResponse,
    responses=ADMIN_MUTATION_RESPONSES,
)
async def disable_invitation_code(
    code_id: str,
    body: AdminMutation,
    request: Request,
    admin: AdminContext = CURRENT_ADMIN,
):
    _track_admin_idempotency(request, body.idempotency_key)
    try:
        result = await disable_invitation_resource(
            request.app.state.session_factory,
            request.app.state.settings,
            admin,
            resource_type="INVITATION_CODE",
            public_id=code_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
            request_id=_request_id(request),
            source_ip=_source_ip(request),
        )
    except AdminOperationError as exc:
        raise _raise_admin(exc) from exc
    return {"request_id": _request_id(request), **result}


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def admin_dashboard(
    request: Request,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    return {"ok": True, "request_id": _request_id(request), **(await dashboard(db))}


@router.get(
    "/reports/trip-generation",
    response_model=AdminTripGenerationReportResponse,
)
async def admin_trip_generation_report(
    request: Request,
    city: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    status: Annotated[
        str | None,
        Query(pattern="^(SUBMITTING|PENDING|RUNNING|SUCCESS|FAILED|TIMEOUT|REJECTED)$"),
    ] = None,
    error_code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    result_type: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    detailed_reason: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    return {
        "ok": True,
        "request_id": _request_id(request),
        **(
            await trip_generation_report(
                db,
                city=city,
                time_from=time_from,
                time_to=time_to,
                status_filter=status,
                error_code=error_code,
                result_type=result_type,
                detailed_reason=detailed_reason,
            )
        ),
    }


@router.get(
    "/reports/user-preferences",
    response_model=AdminPreferenceReportResponse,
)
async def admin_preference_report(
    request: Request,
    city: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    return {
        "ok": True,
        "request_id": _request_id(request),
        **(
            await preference_report(
                db,
                city=city,
                time_from=time_from,
                time_to=time_to,
            )
        ),
    }


@router.get("/audit-events", response_model=AdminAuditEventListResponse)
async def admin_audit_events(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    action: Annotated[
        str | None,
        Query(pattern=f"^({'|'.join(sorted(AUDIT_ACTIONS))})$"),
    ] = None,
    result: Annotated[
        str | None,
        Query(pattern=f"^({'|'.join(sorted(AUDIT_RESULTS))})$"),
    ] = None,
    error_code: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    _admin: AdminContext = CURRENT_ADMIN,
    db: AsyncSession = DB_SESSION,
):
    return {
        "ok": True,
        "request_id": _request_id(request),
        **(
            await audit_events(
                db,
                page=page,
                limit=limit,
                action=action,
                result=result,
                error_code=error_code,
                time_from=time_from,
                time_to=time_to,
            )
        ),
    }
