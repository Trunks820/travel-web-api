from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.db.models import AppUser, QuotaAdjustment, QuotaGrant, TripQuotaEntry, UserTrip
from src.security.secrets import new_opaque_id

ACTIVE_TRIP_STATUSES = ("SUBMITTING", "PENDING", "RUNNING")
TERMINAL_TRIP_STATUSES = ("SUCCESS", "FAILED", "TIMEOUT", "REJECTED")
PERIOD_TYPE = "BETA_LIFETIME"
PERIOD_KEY = "v0.1-beta"


class RequestIdConflict(Exception):
    pass


@dataclass(frozen=True)
class ActiveTripConflict(Exception):
    trip_id: str
    job_id: str | None
    status: str


class QuotaExhausted(Exception):
    pass


class TripOwnershipError(Exception):
    pass


class QuotaInvariantError(Exception):
    pass


@dataclass(frozen=True)
class QuotaSnapshot:
    limit: int
    reserved: int
    consumed: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.reserved - self.consumed, 0)

    def public(self) -> dict[str, object]:
        return {
            "policy": "beta_lifetime",
            "limit": self.limit,
            "reserved": self.reserved,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "resets_at": None,
        }


@dataclass(frozen=True)
class Reservation:
    trip: UserTrip
    created: bool
    quota: QuotaSnapshot


async def quota_snapshot(session: AsyncSession, user_id: uuid.UUID) -> QuotaSnapshot:
    granted = int(
        await session.scalar(
            select(func.coalesce(func.sum(QuotaGrant.units), 0)).where(
                QuotaGrant.user_id == user_id,
                QuotaGrant.period_type == PERIOD_TYPE,
                QuotaGrant.period_key == PERIOD_KEY,
            )
        )
        or 0
    )
    adjusted = int(
        await session.scalar(
            select(func.coalesce(func.sum(QuotaAdjustment.delta), 0)).where(
                QuotaAdjustment.target_user_id == user_id
            )
        )
        or 0
    )
    rows = (
        await session.execute(
            select(
                TripQuotaEntry.status,
                func.coalesce(func.sum(TripQuotaEntry.units), 0),
            )
            .where(
                TripQuotaEntry.user_id == user_id,
                TripQuotaEntry.period_type == PERIOD_TYPE,
                TripQuotaEntry.period_key == PERIOD_KEY,
                TripQuotaEntry.status.in_(("RESERVED", "CONSUMED")),
            )
            .group_by(TripQuotaEntry.status)
        )
    ).all()
    by_status = {status: int(units) for status, units in rows}
    return QuotaSnapshot(
        limit=granted + adjusted,
        reserved=by_status.get("RESERVED", 0),
        consumed=by_status.get("CONSUMED", 0),
    )


async def reserve_trip(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    user_id: uuid.UUID,
    client_request_id: str,
    request_hash: str,
    request_json: dict[str, object],
) -> Reservation:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = await session.scalar(select(AppUser).where(AppUser.id == user_id).with_for_update())
        if user is None or user.status != "ACTIVE":
            raise TripOwnershipError

        existing = await session.scalar(
            select(UserTrip).where(
                UserTrip.user_id == user_id,
                UserTrip.client_request_id == client_request_id,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise RequestIdConflict
            return Reservation(
                trip=existing,
                created=False,
                quota=await quota_snapshot(session, user_id),
            )

        active = await session.scalar(
            select(UserTrip).where(
                UserTrip.user_id == user_id,
                UserTrip.status.in_(ACTIVE_TRIP_STATUSES),
            )
        )
        if active is not None:
            raise ActiveTripConflict(
                trip_id=active.public_id,
                job_id=active.hermes_job_id,
                status=active.status,
            )

        before = await quota_snapshot(session, user_id)
        if before.remaining < 1:
            raise QuotaExhausted

        trip_id = uuid.uuid4()
        quota_id = uuid.uuid4()
        trip = UserTrip(
            id=trip_id,
            public_id=new_opaque_id("trip_"),
            user_id=user_id,
            client_request_id=client_request_id,
            request_hash=request_hash,
            request_json=request_json,
            city=str(request_json["to_city"]),
            days=int(request_json["days"]),
            status="SUBMITTING",
            quota_entry_id=None,
            created_at=now,
            updated_at=now,
            visible_until=now + timedelta(days=settings.trip_history_days),
            reconciliation_attempts=0,
        )
        quota_entry = TripQuotaEntry(
            id=quota_id,
            user_id=user_id,
            trip_id=trip_id,
            period_type=PERIOD_TYPE,
            period_key=PERIOD_KEY,
            units=1,
            status="RESERVED",
            reserve_reason="TRIP_SUBMISSION",
            created_at=now,
            updated_at=now,
        )
        session.add(trip)
        await session.flush()
        session.add(quota_entry)
        await session.flush()
        trip.quota_entry_id = quota_id
        await session.flush()
        return Reservation(
            trip=trip,
            created=True,
            quota=QuotaSnapshot(
                limit=before.limit,
                reserved=before.reserved + 1,
                consumed=before.consumed,
            ),
        )


async def owned_trip_by_job(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    job_id: str,
) -> UserTrip:
    trip = await session.scalar(
        select(UserTrip).where(
            UserTrip.user_id == user_id,
            UserTrip.hermes_job_id == job_id,
        )
    )
    if trip is None:
        raise TripOwnershipError
    return trip


async def owned_success_trip_by_result(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    result_record_id: int,
    job_id: str | None = None,
) -> UserTrip:
    predicates = (
        UserTrip.user_id == user_id,
        UserTrip.result_record_id == result_record_id,
        UserTrip.status == "SUCCESS",
    )
    statement = select(UserTrip).where(*predicates)
    if job_id is not None:
        statement = statement.where(UserTrip.hermes_job_id == job_id)
    trip = await session.scalar(statement)
    if trip is None:
        raise TripOwnershipError
    return trip


async def save_upstream_acceptance(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trip_id: uuid.UUID,
    job_id: str,
    status: str,
) -> UserTrip:
    now = datetime.now(UTC)
    normalized = status.upper()
    if normalized not in {"PENDING", "RUNNING"}:
        normalized = "PENDING"
    async with session_factory() as session, session.begin():
        trip = await session.scalar(
            select(UserTrip).where(UserTrip.id == trip_id).with_for_update()
        )
        if trip is None:
            raise QuotaInvariantError("trip disappeared after upstream acceptance")
        if trip.hermes_job_id is not None and trip.hermes_job_id != job_id:
            raise QuotaInvariantError("one trip resolved to different upstream jobs")
        if trip.status in TERMINAL_TRIP_STATUSES:
            return trip
        trip.hermes_job_id = job_id
        trip.status = normalized
        trip.started_at = trip.started_at or (now if normalized == "RUNNING" else None)
        trip.updated_at = now
        await session.flush()
        return trip


async def mark_upstream_uncertain(
    session_factory: async_sessionmaker[AsyncSession],
    trip_id: uuid.UUID,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(UserTrip)
            .where(
                UserTrip.id == trip_id,
                UserTrip.status == "SUBMITTING",
            )
            .values(
                reconciliation_attempts=UserTrip.reconciliation_attempts + 1,
                last_reconciled_at=now,
                updated_at=now,
            )
        )


async def record_trip_telemetry(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trip_id: uuid.UUID,
    telemetry: dict[str, object],
) -> None:
    safe = {
        key: value
        for key, value in telemetry.items()
        if key
        in {
            "current_stage",
            "plan_count",
            "elapsed_ms",
            "queue_wait_ms",
            "run_elapsed_ms",
            "total_elapsed_ms",
            "result_type",
            "result_schema_version",
        }
        and isinstance(value, (str, int, float, bool))
    }
    if not safe:
        return
    async with session_factory() as session, session.begin():
        trip = await session.scalar(
            select(UserTrip).where(UserTrip.id == trip_id).with_for_update()
        )
        if trip is None:
            raise QuotaInvariantError("trip disappeared while recording telemetry")
        trip.telemetry_json = {**(trip.telemetry_json or {}), **safe}
        trip.updated_at = datetime.now(UTC)


async def settle_trip(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trip_id: uuid.UUID,
    terminal_status: str,
    result_record_id: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_retryable: bool | None = None,
    owner_id: uuid.UUID | None = None,
) -> UserTrip:
    terminal_status = terminal_status.upper()
    if terminal_status not in TERMINAL_TRIP_STATUSES:
        raise ValueError("terminal status required")
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        statement = select(UserTrip).where(UserTrip.id == trip_id).with_for_update()
        if owner_id is not None:
            statement = statement.where(UserTrip.user_id == owner_id)
        trip = await session.scalar(statement)
        if trip is None:
            raise TripOwnershipError
        if trip.status in TERMINAL_TRIP_STATUSES:
            return trip

        quota_status = "CONSUMED" if terminal_status == "SUCCESS" else "RELEASED"
        changed = await session.execute(
            update(TripQuotaEntry)
            .where(
                TripQuotaEntry.id == trip.quota_entry_id,
                TripQuotaEntry.status == "RESERVED",
            )
            .values(
                status=quota_status,
                settle_reason=terminal_status,
                settled_at=now,
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            quota = await session.get(TripQuotaEntry, trip.quota_entry_id)
            if quota is None or quota.status != quota_status:
                raise QuotaInvariantError("quota entry cannot follow terminal trip")

        trip.status = terminal_status
        trip.result_record_id = result_record_id if terminal_status == "SUCCESS" else None
        trip.error_code = error_code if terminal_status != "SUCCESS" else None
        trip.error_message = error_message if terminal_status != "SUCCESS" else None
        trip.error_retryable = error_retryable if terminal_status != "SUCCESS" else None
        trip.finished_at = now
        trip.updated_at = now
        await session.flush()
        return trip


async def update_active_status(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trip_id: uuid.UUID,
    status: str,
    owner_id: uuid.UUID | None = None,
) -> UserTrip:
    status = status.upper()
    if status not in ACTIVE_TRIP_STATUSES:
        raise ValueError("active status required")
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        statement = select(UserTrip).where(UserTrip.id == trip_id).with_for_update()
        if owner_id is not None:
            statement = statement.where(UserTrip.user_id == owner_id)
        trip = await session.scalar(statement)
        if trip is None:
            raise TripOwnershipError
        if trip.status not in TERMINAL_TRIP_STATUSES:
            rank = {"SUBMITTING": 0, "PENDING": 1, "RUNNING": 2}
            if rank[status] >= rank[trip.status]:
                trip.status = status
                trip.started_at = trip.started_at or (now if status == "RUNNING" else None)
                trip.updated_at = now
        await session.flush()
        return trip
