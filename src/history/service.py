from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.db.models import UserTrip
from src.history.cursor import (
    InvalidHistoryCursor,
    decode_history_cursor,
    encode_history_cursor,
)
from src.quota.service import TERMINAL_TRIP_STATUSES
from src.trips.schemas import TripRequest
from src.trips.service import safe_failure


def deidentify_trip_request(request_json: dict, *, account_closure: bool) -> dict:
    try:
        safe = TripRequest.model_validate(request_json).normalized()
    except ValueError:
        safe = {
            "to_city": str(request_json.get("to_city", ""))[:120],
            "days": max(1, min(int(request_json.get("days", 1)), 30)),
            "people_count": max(1, min(int(request_json.get("people_count", 1)), 30)),
            "preferences": [],
            "avoid": [],
            "notes": "",
        }
    safe["notes"] = ""
    if account_closure:
        safe.pop("from_city", None)
    return safe


async def archive_expired(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = 100,
) -> int:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        rows = list(
            (
                await session.scalars(
                    select(UserTrip)
                    .where(
                        UserTrip.status.in_(TERMINAL_TRIP_STATUSES),
                        UserTrip.visible_until <= now,
                        UserTrip.archived_at.is_(None),
                    )
                    .order_by(UserTrip.visible_until, UserTrip.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for row in rows:
            row.archived_at = now
            row.request_json = deidentify_trip_request(
                row.request_json,
                account_closure=False,
            )
            row.updated_at = now
    return len(rows)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _history_item(row: UserTrip) -> dict[str, object]:
    retry_request = TripRequest.model_validate(row.request_json).normalized()
    error = None
    if row.status in {"FAILED", "TIMEOUT", "REJECTED"}:
        code, message, retryable = safe_failure(row.error_code, row.error_message)
        error = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
    return {
        "trip_id": row.public_id,
        "job_id": row.hermes_job_id,
        "status": row.status,
        "city": row.city,
        "days": row.days,
        "result_record_id": row.result_record_id,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
        "expires_from_history_at": _iso(row.visible_until),
        "retry_input": {"trip_request": retry_request},
        "error": error,
    }


async def own_history(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int,
    status: str | None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    predicates = [
        UserTrip.user_id == user_id,
        UserTrip.status.in_(TERMINAL_TRIP_STATUSES),
        UserTrip.visible_until > now,
        UserTrip.archived_at.is_(None),
    ]
    if status is not None:
        predicates.append(UserTrip.status == status)
    if cursor:
        try:
            cursor_public_id = decode_history_cursor(cursor, settings)
        except InvalidHistoryCursor as exc:
            raise InvalidHistoryCursor from exc
        cursor_row = await session.scalar(
            select(UserTrip).where(
                UserTrip.user_id == user_id,
                UserTrip.public_id == cursor_public_id,
            )
        )
        if cursor_row is None:
            raise InvalidHistoryCursor
        predicates.append(
            or_(
                UserTrip.created_at < cursor_row.created_at,
                and_(
                    UserTrip.created_at == cursor_row.created_at,
                    UserTrip.id < cursor_row.id,
                ),
            )
        )
    rows = list(
        (
            await session.scalars(
                select(UserTrip)
                .where(*predicates)
                .order_by(UserTrip.created_at.desc(), UserTrip.id.desc())
                .limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_history_cursor(page[-1].public_id, settings) if has_more and page else None
    return {
        "ok": True,
        "items": [_history_item(row) for row in page],
        "next_cursor": next_cursor,
    }
