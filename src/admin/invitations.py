from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.admin.audit import admin_subject_hash, append_admin_audit
from src.admin.auth import AdminContext
from src.admin.idempotency import (
    AdminIdempotencyConflict,
    claim_admin_idempotency,
    complete_admin_idempotency,
)
from src.admin.service import AdminOperationError
from src.config import Settings
from src.db.models import Invitation, InvitationBatch
from src.invitations.service import (
    new_short_invitation_code,
    normalize_invitation_code,
)
from src.security.secrets import hash_secret, new_opaque_id


def invitation_status(row: Invitation, now: datetime) -> str:
    if row.redeemed_at is not None:
        return "EXHAUSTED"
    if row.disabled_at is not None:
        return "DISABLED"
    if row.expires_at is not None and row.expires_at <= now:
        return "EXPIRED"
    return "ACTIVE"


def batch_public(row: InvitationBatch) -> dict[str, Any]:
    return {
        "batch_id": row.public_id,
        "name": row.name,
        "source_label": row.source_label,
        "count": row.code_count,
        "valid_days": row.valid_days,
        "expires_at": row.expires_at.isoformat(),
        "disabled_at": row.disabled_at.isoformat() if row.disabled_at else None,
        "created_at": row.created_at.isoformat(),
    }


async def create_batch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    admin: AdminContext,
    *,
    name: str,
    source_label: str,
    count: int,
    valid_days: int,
    reason: str,
    idempotency_key: uuid.UUID,
    request_id: str,
    source_ip: str,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "source_label": source_label,
        "count": count,
        "valid_days": valid_days,
        "reason": reason,
    }
    async with session_factory() as session, session.begin():
        try:
            claim = await claim_admin_idempotency(
                session,
                settings,
                actor_user_id=admin.user.id,
                idempotency_key=idempotency_key,
                action="CREATE_INVITATION_BATCH",
                payload=payload,
            )
        except AdminIdempotencyConflict as exc:
            raise AdminOperationError(409, "IDEMPOTENCY_CONFLICT", "幂等键请求不一致。") from exc
        if claim.replay_response:
            return claim.replay_response[1]
        now = datetime.now(UTC)
        batch = InvitationBatch(
            public_id=new_opaque_id("batch_"),
            name=name.strip(),
            source_label=source_label.strip(),
            code_count=count,
            valid_days=valid_days,
            expires_at=now + timedelta(days=valid_days),
            created_by_user_id=admin.user.id,
            creator_scope_hash=admin_subject_hash(admin.user.id, settings),
            created_at=now,
        )
        session.add(batch)
        await session.flush()
        raw_codes: list[str] = []
        digests: set[bytes] = set()
        for sequence in range(1, count + 1):
            while True:
                raw = new_short_invitation_code()
                digest = hash_secret(
                    raw,
                    purpose="invitation",
                    pepper=settings.secret_hash_pepper.get_secret_value(),
                )
                exists = await session.scalar(
                    select(Invitation.id).where(Invitation.secret_hash == digest)
                )
                if digest not in digests and exists is None:
                    break
            digests.add(digest)
            raw_codes.append(raw)
            session.add(
                Invitation(
                    public_id=new_opaque_id("code_"),
                    batch_id=batch.id,
                    sequence_number=sequence,
                    secret_hash=digest,
                    source_label=batch.source_label,
                    expires_at=batch.expires_at,
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.flush()
        replay = {
            "ok": True,
            "batch": batch_public(batch),
            "codes_disclosed": False,
            "codes": [],
        }
        first = {**replay, "codes_disclosed": True, "codes": raw_codes}
        await append_admin_audit(
            session,
            settings,
            actor_user_id=admin.user.id,
            actor_identity=admin.product_identity,
            action="CREATE_INVITATION_BATCH",
            target_type="INVITATION_BATCH",
            target_id=batch.public_id,
            result="SUCCESS",
            request_id=request_id,
            source_ip=source_ip,
            reason=reason,
            idempotency_key=idempotency_key,
            after={"count": count, "valid_days": valid_days},
        )
        complete_admin_idempotency(claim, http_status=201, response_json=replay)
        return first


async def list_batches(session: AsyncSession, *, page: int, limit: int) -> dict[str, Any]:
    total = int(await session.scalar(select(func.count()).select_from(InvitationBatch)) or 0)
    rows = (
        await session.execute(
            select(InvitationBatch)
            .order_by(InvitationBatch.created_at.desc(), InvitationBatch.public_id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars()
    return {
        "items": [batch_public(row) for row in rows],
        "page": page,
        "limit": limit,
        "total": total,
    }


async def batch_detail(session: AsyncSession, batch_id: str) -> dict[str, Any]:
    batch = await session.scalar(
        select(InvitationBatch).where(InvitationBatch.public_id == batch_id)
    )
    if batch is None:
        raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "邀请码批次不存在。")
    rows = (
        await session.execute(
            select(Invitation)
            .where(Invitation.batch_id == batch.id)
            .order_by(Invitation.sequence_number)
        )
    ).scalars()
    now = datetime.now(UTC)
    return {
        "batch": batch_public(batch),
        "codes_disclosed": False,
        "codes": [
            {
                "code_id": row.public_id,
                "sequence": f"#{row.sequence_number:03d}",
                "status": invitation_status(row, now),
                "redeemed_at": row.redeemed_at,
            }
            for row in rows
        ],
    }


async def lookup_code(session: AsyncSession, settings: Settings, raw_code: str) -> Invitation:
    normalized = normalize_invitation_code(raw_code)
    digest = hash_secret(
        normalized,
        purpose="invitation",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )
    row = await session.scalar(select(Invitation).where(Invitation.secret_hash == digest))
    if row is None or row.public_id is None:
        raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "邀请码不存在。")
    return row


async def disable_invitation_resource(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    admin: AdminContext,
    *,
    resource_type: str,
    public_id: str,
    reason: str,
    idempotency_key: uuid.UUID,
    request_id: str,
    source_ip: str,
) -> dict[str, Any]:
    action = f"DISABLE_{resource_type}"
    async with session_factory() as session, session.begin():
        try:
            claim = await claim_admin_idempotency(
                session,
                settings,
                actor_user_id=admin.user.id,
                idempotency_key=idempotency_key,
                action=action,
                payload={"public_id": public_id, "reason": reason},
            )
        except AdminIdempotencyConflict as exc:
            raise AdminOperationError(409, "IDEMPOTENCY_CONFLICT", "幂等键请求不一致。") from exc
        if claim.replay_response:
            return claim.replay_response[1]
        now = datetime.now(UTC)
        if resource_type == "INVITATION_BATCH":
            row = await session.scalar(
                select(InvitationBatch)
                .where(InvitationBatch.public_id == public_id)
                .with_for_update()
            )
            if row is None:
                raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "批次不存在。")
            row.disabled_at = row.disabled_at or now
            invitations = (
                await session.execute(
                    select(Invitation).where(Invitation.batch_id == row.id).with_for_update()
                )
            ).scalars()
            for invitation in invitations:
                if invitation.redeemed_at is None:
                    invitation.disabled_at = invitation.disabled_at or now
        else:
            row = await session.scalar(
                select(Invitation).where(Invitation.public_id == public_id).with_for_update()
            )
            if row is None:
                raise AdminOperationError(404, "ADMIN_RESOURCE_NOT_FOUND", "邀请码不存在。")
            if row.redeemed_at is None:
                row.disabled_at = row.disabled_at or now
        response = {"ok": True, "resource_id": public_id, "status": "DISABLED"}
        await append_admin_audit(
            session,
            settings,
            actor_user_id=admin.user.id,
            actor_identity=admin.product_identity,
            action=action,
            target_type=resource_type,
            target_id=public_id,
            result="SUCCESS",
            request_id=request_id,
            source_ip=source_ip,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        complete_admin_idempotency(claim, http_status=200, response_json=response)
        return response
