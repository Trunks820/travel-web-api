from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.audit import append_admin_audit
from src.config import Settings
from src.db.models import AdminAuditLog, AppUser, UserIdentity, UserSession


class OwnerBootstrapRejected(Exception):
    pass


async def bootstrap_configured_owner(
    session: AsyncSession,
    settings: Settings,
    *,
    target_user_id: uuid.UUID,
    request_id: str,
    source_ip: str,
) -> AppUser:
    if settings.admin_owner_user_id is None or target_user_id != settings.admin_owner_user_id:
        raise OwnerBootstrapRejected("target is not the configured owner")

    user = await session.scalar(
        select(AppUser).where(AppUser.id == target_user_id).with_for_update()
    )
    if user is None or user.status != "ACTIVE":
        raise OwnerBootstrapRejected("configured owner must be an active existing user")
    verified_identity = await session.scalar(
        select(UserIdentity.id).where(
            UserIdentity.user_id == target_user_id,
            UserIdentity.provider == "email_otp",
            UserIdentity.verified_email.is_not(None),
        )
    )
    if verified_identity is None:
        raise OwnerBootstrapRejected("configured owner must have a verified email identity")

    existing = await session.scalar(
        select(AdminAuditLog.id).where(
            AdminAuditLog.action == "SYSTEM_BOOTSTRAP",
            AdminAuditLog.target_type == "USER",
            AdminAuditLog.target_id == user.public_id,
            AdminAuditLog.result == "SUCCESS",
        )
    )
    if existing is not None:
        return user

    before = {"role": user.role, "status": user.status}
    user.role = "ADMIN"
    user.updated_at = datetime.now(UTC)
    await session.execute(
        update(UserSession)
        .where(
            UserSession.user_id == target_user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(
            revoked_at=datetime.now(UTC),
            revoke_reason="SYSTEM_BOOTSTRAP",
        )
    )
    await append_admin_audit(
        session,
        settings,
        actor_user_id=None,
        actor_identity="SYSTEM",
        action="SYSTEM_BOOTSTRAP",
        target_type="USER",
        target_id=user.public_id,
        result="SUCCESS",
        request_id=request_id,
        source_ip=source_ip,
        reason="INITIAL_OWNER_BOOTSTRAP",
        before=before,
        after={"role": "ADMIN", "status": user.status, "product_identity": "OWNER"},
    )
    await session.flush()
    return user
