from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.db.models import AdminIdempotency
from src.security.secrets import hash_secret


class AdminIdempotencyConflict(Exception):
    pass


class AdminIdempotencyInProgress(Exception):
    pass


@dataclass(frozen=True)
class AdminIdempotencyClaim:
    record: AdminIdempotency
    created: bool

    @property
    def replay_response(self) -> tuple[int, dict[str, Any]] | None:
        if self.record.state != "SUCCEEDED":
            return None
        if self.record.http_status is None or self.record.response_json is None:
            raise RuntimeError("successful idempotency record has no replay result")
        return self.record.http_status, self.record.response_json


def canonical_admin_request_hash(action: str, payload: dict[str, Any]) -> bytes:
    canonical = json.dumps(
        {"action": action, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).digest()


def admin_actor_scope_hash(actor_user_id: uuid.UUID, settings: Settings) -> bytes:
    return hash_secret(
        str(actor_user_id),
        purpose="admin-idempotency-actor",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )


async def claim_admin_idempotency(
    session: AsyncSession,
    settings: Settings,
    *,
    actor_user_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    action: str,
    payload: dict[str, Any],
) -> AdminIdempotencyClaim:
    actor_scope_hash = admin_actor_scope_hash(actor_user_id, settings)
    request_hash = canonical_admin_request_hash(action, payload)
    record_id = uuid.uuid4()
    inserted = await session.execute(
        insert(AdminIdempotency)
        .values(
            id=record_id,
            actor_user_id=actor_user_id,
            actor_scope_hash=actor_scope_hash,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state="IN_PROGRESS",
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_admin_idempotency_actor_key")
    )
    record = await session.scalar(
        select(AdminIdempotency)
        .where(
            AdminIdempotency.actor_scope_hash == actor_scope_hash,
            AdminIdempotency.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if record is None:
        raise RuntimeError("idempotency claim disappeared")
    if record.request_hash != request_hash:
        raise AdminIdempotencyConflict
    created = inserted.rowcount == 1
    if not created and record.state == "IN_PROGRESS":
        raise AdminIdempotencyInProgress
    return AdminIdempotencyClaim(record=record, created=created)


def complete_admin_idempotency(
    claim: AdminIdempotencyClaim,
    *,
    http_status: int,
    response_json: dict[str, Any],
) -> None:
    if not claim.created:
        raise RuntimeError("only the winning request can complete idempotency")
    claim.record.state = "SUCCEEDED"
    claim.record.http_status = http_status
    claim.record.response_json = response_json
    claim.record.completed_at = datetime.now(UTC)
