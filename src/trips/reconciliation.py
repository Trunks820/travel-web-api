from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.db.models import UserTrip
from src.integrations.hermes import HermesClient, HermesIntegrationError
from src.quota.service import save_upstream_acceptance
from src.trips.service import apply_job_status


@dataclass(frozen=True)
class ReconciliationResult:
    claimed: int
    recovered: int
    unresolved: int


async def reconcile_bounded(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    hermes: HermesClient,
    *,
    correlation_id: str,
) -> ReconciliationResult:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        trips = list(
            (
                await session.scalars(
                    select(UserTrip)
                    .where(
                        UserTrip.status.in_(("SUBMITTING", "PENDING", "RUNNING")),
                        UserTrip.reconciliation_attempts < settings.reconciliation_max_attempts,
                    )
                    .order_by(UserTrip.updated_at, UserTrip.id)
                    .limit(settings.reconciliation_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        snapshots = [
            {
                "id": trip.id,
                "public_id": trip.public_id,
                "request_json": dict(trip.request_json),
                "hermes_job_id": trip.hermes_job_id,
            }
            for trip in trips
        ]
        for trip in trips:
            trip.reconciliation_attempts += 1
            trip.last_reconciled_at = now
            trip.updated_at = now

    recovered = 0
    unresolved = 0
    for snapshot in snapshots:
        try:
            if snapshot["hermes_job_id"] is None:
                created = await hermes.create_trip(
                    trip_request=snapshot["request_json"],
                    upstream_request_id=f"bff-{snapshot['public_id']}",
                    conversation_id=str(snapshot["public_id"]),
                    correlation_id=correlation_id,
                )
                await save_upstream_acceptance(
                    session_factory,
                    trip_id=snapshot["id"],
                    job_id=created.job_id,
                    status=created.status,
                )
            else:
                async with session_factory() as session:
                    trip = await session.get(UserTrip, snapshot["id"])
                if trip is None:
                    unresolved += 1
                    continue
                upstream = await hermes.job_status(
                    str(snapshot["hermes_job_id"]),
                    correlation_id,
                )
                await apply_job_status(
                    session_factory,
                    trip=trip,
                    upstream=upstream,
                    owner_id=None,
                )
            recovered += 1
        except HermesIntegrationError:
            unresolved += 1
    return ReconciliationResult(
        claimed=len(snapshots),
        recovered=recovered,
        unresolved=unresolved,
    )
