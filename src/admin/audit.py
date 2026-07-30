from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.db.models import AdminAuditLog
from src.observability.logging import Redactor
from src.security.secrets import hash_secret, new_opaque_id


def audit_source_ip_hash(raw_ip: str, settings: Settings) -> bytes:
    return hash_secret(
        raw_ip,
        purpose="admin-audit-source-ip",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )


def admin_subject_hash(user_id: uuid.UUID, settings: Settings) -> bytes:
    return hash_secret(
        str(user_id),
        purpose="admin-subject",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )


def redact_audit_projection(settings: Settings, value: dict[str, Any] | None):
    if value is None:
        return None
    return Redactor(settings.redaction_secrets).redact(value)


async def append_admin_audit(
    session: AsyncSession,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID | None,
    actor_identity: str,
    action: str,
    target_type: str,
    target_id: str | None,
    result: str,
    request_id: str,
    source_ip: str,
    reason: str | None = None,
    error_code: str | None = None,
    idempotency_key: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    client: dict[str, Any] | None = None,
) -> AdminAuditLog:
    row = AdminAuditLog(
        public_id=new_opaque_id("audit_"),
        actor_user_id=actor_user_id,
        actor_identity=actor_identity,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        error_code=error_code,
        before_json=redact_audit_projection(settings, before),
        after_json=redact_audit_projection(settings, after),
        reason=reason,
        idempotency_key=idempotency_key,
        request_id=request_id,
        source_ip_hash=audit_source_ip_hash(source_ip, settings),
        client_json=redact_audit_projection(settings, client or {}) or {},
    )
    session.add(row)
    await session.flush()
    return row
