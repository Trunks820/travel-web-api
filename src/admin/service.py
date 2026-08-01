from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.admin.audit import admin_subject_hash, append_admin_audit
from src.admin.auth import AdminContext
from src.admin.idempotency import (
    AdminIdempotencyConflict,
    claim_admin_idempotency,
    complete_admin_idempotency,
)
from src.config import Settings
from src.db.models import (
    AppUser,
    Invitation,
    InvitationBatch,
    InvitationRedemption,
    QuotaAdjustment,
    UserIdentity,
    UserSession,
    UserTrip,
)
from src.quota.service import quota_snapshot
from src.security.secrets import new_opaque_id


class AdminOperationError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(code)


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


async def _email(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(UserIdentity.verified_email).where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == "email_otp",
        )
    )


async def public_admin_user(
    session: AsyncSession,
    settings: Settings,
    user: AppUser,
) -> dict[str, Any]:
    email = await _email(session, user.id)
    quota = await quota_snapshot(session, user.id)
    last_trip = await session.scalar(
        select(func.max(UserTrip.created_at)).where(UserTrip.user_id == user.id)
    )
    return {
        "user_id": user.public_id,
        "status": user.status,
        "role": user.role,
        "product_identity": ("OWNER" if settings.admin_owner_user_id == user.id else user.role),
        "display_name": user.display_name,
        "masked_email": mask_email(email),
        "quota": quota.public(),
        "created_at": user.created_at,
        "last_trip_at": last_trip,
    }


async def list_admin_users(
    session: AsyncSession,
    settings: Settings,
    *,
    q: str | None,
    role: str | None,
    status: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    statement = select(AppUser).distinct()
    if q:
        term = f"%{q.strip()}%"
        statement = (
            statement.outerjoin(UserIdentity, UserIdentity.user_id == AppUser.id)
            .outerjoin(InvitationRedemption, InvitationRedemption.user_id == AppUser.id)
            .outerjoin(Invitation, Invitation.id == InvitationRedemption.invitation_id)
            .outerjoin(InvitationBatch, InvitationBatch.id == Invitation.batch_id)
            .where(
                or_(
                    AppUser.display_name.ilike(term),
                    AppUser.public_id.ilike(term),
                    UserIdentity.verified_email.ilike(term),
                    Invitation.source_label.ilike(term),
                    InvitationBatch.name.ilike(term),
                )
            )
        )
    if role:
        statement = statement.where(AppUser.role == role)
    if status:
        statement = statement.where(AppUser.status == status)
    count = await session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    )
    users = (
        await session.execute(
            statement.order_by(AppUser.created_at.desc(), AppUser.public_id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars()
    items = [await public_admin_user(session, settings, user) for user in users]
    return {"items": items, "page": page, "limit": limit, "total": int(count or 0)}


async def find_user_by_public_id(
    session: AsyncSession,
    public_id: str,
    *,
    for_update: bool = False,
) -> AppUser:
    statement = select(AppUser).where(AppUser.public_id == public_id)
    if for_update:
        statement = statement.with_for_update()
    user = await session.scalar(statement)
    if user is None:
        raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "用户不存在。")
    return user


def assert_target_allowed(
    context: AdminContext,
    settings: Settings,
    target: AppUser,
    *,
    role_change: bool = False,
    disabling: bool = False,
) -> None:
    if role_change and not context.is_owner:
        raise AdminOperationError(403, "OWNER_REQUIRED", "该操作仅限 OWNER。")
    if context.is_owner:
        if disabling and settings.admin_owner_user_id == target.id:
            raise AdminOperationError(409, "LAST_OWNER_PROTECTED", "最后一位 OWNER 不能停用。")
        return
    if (
        target.id == context.user.id
        or target.role == "ADMIN"
        or settings.admin_owner_user_id == target.id
    ):
        raise AdminOperationError(403, "ADMIN_FORBIDDEN", "当前管理员无权操作该用户。")


async def mutate_user(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    context: AdminContext,
    *,
    target_public_id: str,
    action: str,
    reason: str,
    idempotency_key: uuid.UUID,
    request_id: str,
    source_ip: str,
) -> dict[str, Any]:
    payload = {"target_user_id": target_public_id, "reason": reason}
    async with session_factory() as session, session.begin():
        try:
            claim = await claim_admin_idempotency(
                session,
                settings,
                actor_user_id=context.user.id,
                idempotency_key=idempotency_key,
                action=action,
                payload=payload,
            )
        except AdminIdempotencyConflict as exc:
            raise AdminOperationError(409, "IDEMPOTENCY_CONFLICT", "幂等键请求不一致。") from exc
        if claim.replay_response:
            return claim.replay_response[1]
        target = await find_user_by_public_id(session, target_public_id, for_update=True)
        role_change = action in {"GRANT_ADMIN", "REVOKE_ADMIN"}
        assert_target_allowed(
            context,
            settings,
            target,
            role_change=role_change,
            disabling=action == "DISABLE_USER",
        )
        before = {"status": target.status, "role": target.role}
        if action == "DISABLE_USER":
            target.status = "DISABLED"
        elif action == "RESTORE_USER":
            target.status = "ACTIVE"
        elif action == "GRANT_ADMIN":
            target.role = "ADMIN"
        elif action == "REVOKE_ADMIN":
            target.role = "USER"
        else:
            raise RuntimeError("unknown user mutation")
        target.updated_at = datetime.now(UTC)
        if action in {"DISABLE_USER", "GRANT_ADMIN", "REVOKE_ADMIN"}:
            await session.execute(
                update(UserSession)
                .where(
                    UserSession.user_id == target.id,
                    UserSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC), revoke_reason=action)
            )
        response = {
            "ok": True,
            "user_id": target.public_id,
            "status": target.status,
            "role": target.role,
        }
        await append_admin_audit(
            session,
            settings,
            actor_user_id=context.user.id,
            actor_identity=context.product_identity,
            action=action,
            target_type="USER",
            target_id=target.public_id,
            result="SUCCESS",
            request_id=request_id,
            source_ip=source_ip,
            reason=reason,
            idempotency_key=idempotency_key,
            before=before,
            after={"status": target.status, "role": target.role},
        )
        complete_admin_idempotency(claim, http_status=200, response_json=response)
        return response


async def create_quota_adjustment(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    context: AdminContext,
    *,
    target_public_id: str,
    delta: int,
    reason: str,
    note: str | None,
    idempotency_key: uuid.UUID,
    request_id: str,
    source_ip: str,
    reverses_public_id: str | None = None,
) -> dict[str, Any]:
    action = "QUOTA_REVERSE" if reverses_public_id else "QUOTA_ADJUST"
    payload = {
        "target_user_id": target_public_id,
        "delta": delta,
        "reason": reason,
        "note": note,
        "reverses_adjustment_id": reverses_public_id,
    }
    async with session_factory() as session, session.begin():
        try:
            claim = await claim_admin_idempotency(
                session,
                settings,
                actor_user_id=context.user.id,
                idempotency_key=idempotency_key,
                action=action,
                payload=payload,
            )
        except AdminIdempotencyConflict as exc:
            raise AdminOperationError(409, "IDEMPOTENCY_CONFLICT", "幂等键请求不一致。") from exc
        if claim.replay_response:
            return claim.replay_response[1]
        target = await find_user_by_public_id(session, target_public_id, for_update=True)
        assert_target_allowed(context, settings, target)
        before = await quota_snapshot(session, target.id)
        after = before.remaining + delta
        if after < 0:
            raise AdminOperationError(409, "QUOTA_BALANCE_INSUFFICIENT", "可用额度不足。")
        original_id = None
        if reverses_public_id:
            original = await session.scalar(
                select(QuotaAdjustment)
                .where(QuotaAdjustment.public_id == reverses_public_id)
                .with_for_update()
            )
            if original is None or original.target_user_id != target.id:
                raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "额度流水不存在。")
            exists = await session.scalar(
                select(QuotaAdjustment.id).where(
                    QuotaAdjustment.reverses_adjustment_id == original.id
                )
            )
            if exists is not None:
                raise AdminOperationError(409, "ADJUSTMENT_ALREADY_REVERSED", "该流水已冲正。")
            if delta != -original.delta:
                raise RuntimeError("reversal delta must exactly negate original")
            original_id = original.id
        adjustment = QuotaAdjustment(
            public_id=new_opaque_id("adj_"),
            target_user_id=target.id,
            actor_user_id=context.user.id,
            target_scope_hash=admin_subject_hash(target.id, settings),
            actor_scope_hash=admin_subject_hash(context.user.id, settings),
            delta=delta,
            balance_before=before.remaining,
            balance_after=after,
            reason=reason,
            note=note,
            idempotency_id=claim.record.id,
            reverses_adjustment_id=original_id,
        )
        session.add(adjustment)
        await session.flush()
        response = {
            "ok": True,
            "adjustment": {
                "adjustment_id": adjustment.public_id,
                "target_user_id": target.public_id,
                "delta": delta,
                "before": before.remaining,
                "after": after,
                "reason": reason,
                "note": note,
                "reverses_adjustment_id": reverses_public_id,
                "created_at": adjustment.created_at.isoformat(),
            },
        }
        await append_admin_audit(
            session,
            settings,
            actor_user_id=context.user.id,
            actor_identity=context.product_identity,
            action=action,
            target_type="USER",
            target_id=target.public_id,
            result="SUCCESS",
            request_id=request_id,
            source_ip=source_ip,
            reason=reason,
            idempotency_key=idempotency_key,
            before={"available_quota": before.remaining},
            after={"available_quota": after, "delta": delta},
        )
        complete_admin_idempotency(claim, http_status=201, response_json=response)
        return response


async def reverse_quota_adjustment(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    context: AdminContext,
    *,
    adjustment_public_id: str,
    reason: str,
    note: str | None,
    idempotency_key: uuid.UUID,
    request_id: str,
    source_ip: str,
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await session.scalar(
            select(QuotaAdjustment).where(QuotaAdjustment.public_id == adjustment_public_id)
        )
        if row is None:
            raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "额度流水不存在。")
        target = await session.get(AppUser, row.target_user_id)
        if target is None:
            raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "用户不存在。")
        target_public_id = target.public_id
        delta = -row.delta
    return await create_quota_adjustment(
        session_factory,
        settings,
        context,
        target_public_id=target_public_id,
        delta=delta,
        reason=reason,
        note=note,
        idempotency_key=idempotency_key,
        request_id=request_id,
        source_ip=source_ip,
        reverses_public_id=adjustment_public_id,
    )


async def quota_ledger(
    session: AsyncSession,
    settings: Settings,
    *,
    target_public_id: str,
    page: int,
    limit: int,
) -> dict[str, Any]:
    target = await find_user_by_public_id(session, target_public_id)
    statement = select(QuotaAdjustment).where(QuotaAdjustment.target_user_id == target.id)
    total = int(
        await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        or 0
    )
    rows = (
        (
            await session.execute(
                statement.order_by(
                    QuotaAdjustment.created_at.desc(),
                    QuotaAdjustment.public_id.desc(),
                )
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    reversed_ids = {
        row.reverses_adjustment_id for row in rows if row.reverses_adjustment_id is not None
    }
    reversed_public_ids: dict[uuid.UUID, str] = {}
    if reversed_ids:
        reversed_public_ids = dict(
            (
                await session.execute(
                    select(QuotaAdjustment.id, QuotaAdjustment.public_id).where(
                        QuotaAdjustment.id.in_(reversed_ids)
                    )
                )
            ).all()
        )
    snapshot = await quota_snapshot(session, target.id)
    items = [
        {
            "adjustment_id": row.public_id,
            "delta": row.delta,
            "before": row.balance_before,
            "after": row.balance_after,
            "reason": row.reason,
            "note": row.note,
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "target_user_id": target.public_id,
            "reverses_adjustment_id": (
                reversed_public_ids.get(row.reverses_adjustment_id)
                if row.reverses_adjustment_id
                else None
            ),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return {
        "user_id": target.public_id,
        "quota": snapshot.public(),
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
    }
