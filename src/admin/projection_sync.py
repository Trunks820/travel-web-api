from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.admin.projection import _apply_job_snapshot, _apply_step_snapshot
from src.admin.projection_schemas import JobProjectionPayload, StepProjectionPayload
from src.db.models import (
    AdminProjectionBackfillCheckpoint,
    AdminProjectionConsumerState,
    AdminProjectionReconciliation,
    AdminTripProjection,
    AdminTripStepProjection,
)
from src.integrations.hermes import HermesClient


def _page_params(checkpoint: AdminProjectionBackfillCheckpoint, batch_size: int) -> dict[str, int]:
    params = {"limit": batch_size}
    if checkpoint.last_source_id > 0:
        if checkpoint.snapshot_max_id is None:
            raise RuntimeError("backfill checkpoint lost its frozen snapshot maximum")
        params.update(
            {
                "after_id": checkpoint.last_source_id,
                "snapshot_max_id": checkpoint.snapshot_max_id,
            }
        )
    return params


async def _checkpoint(
    session: AsyncSession,
    entity_type: str,
) -> AdminProjectionBackfillCheckpoint:
    row = await session.scalar(
        select(AdminProjectionBackfillCheckpoint)
        .where(AdminProjectionBackfillCheckpoint.entity_type == entity_type)
        .with_for_update()
    )
    if row is None:
        row = AdminProjectionBackfillCheckpoint(entity_type=entity_type, last_source_id=0)
        session.add(row)
        await session.flush()
    return row


async def run_projection_backfill(
    session_factory: async_sessionmaker[AsyncSession],
    hermes: HermesClient,
    *,
    correlation_id: str,
    batch_size: int = 500,
) -> dict[str, int]:
    if not 1 <= batch_size <= 1000:
        raise ValueError("backfill batch_size must be between 1 and 1000")
    counts = {"TRIP_JOB": 0, "TRIP_STEP": 0}
    for entity_type, method in (
        ("TRIP_JOB", hermes.admin_projection_trip_jobs),
        ("TRIP_STEP", hermes.admin_projection_trip_steps),
    ):
        while True:
            async with session_factory() as session:
                checkpoint = await session.get(AdminProjectionBackfillCheckpoint, entity_type)
                if checkpoint is not None and checkpoint.completed_at is not None:
                    break
                params = (
                    _page_params(checkpoint, batch_size)
                    if checkpoint is not None
                    else {"limit": batch_size}
                )
            page = await method(correlation_id=correlation_id, params=params)
            if page.has_more and (not page.items or page.next_after_id is None):
                raise RuntimeError("invalid non-terminal projection snapshot page")
            if not page.has_more and page.next_after_id is not None:
                raise RuntimeError("terminal projection snapshot page has a cursor")
            if page.items:
                final_source_id = (
                    page.items[-1].source_id
                    if entity_type == "TRIP_JOB"
                    else page.items[-1].source_step_id
                )
                if page.has_more and page.next_after_id != final_source_id:
                    raise RuntimeError("projection snapshot cursor does not match final item")
            now = datetime.now(UTC)
            async with session_factory() as session, session.begin():
                checkpoint = await _checkpoint(session, entity_type)
                if checkpoint.snapshot_max_id not in {None, page.snapshot_max_id}:
                    raise RuntimeError("projection snapshot maximum changed during backfill")
                checkpoint.snapshot_max_id = page.snapshot_max_id
                if entity_type == "TRIP_JOB":
                    for raw in page.items:
                        item = JobProjectionPayload.model_validate(raw.model_dump(mode="json"))
                        await _apply_job_snapshot(session, item, now=now)
                else:
                    for raw in page.items:
                        item = StepProjectionPayload.model_validate(raw.model_dump(mode="json"))
                        await _apply_step_snapshot(session, item, now=now)
                counts[entity_type] += len(page.items)
                if page.has_more:
                    checkpoint.last_source_id = int(page.next_after_id or 0)
                else:
                    checkpoint.completed_at = now
                    checkpoint.last_source_id = page.snapshot_max_id
                checkpoint.updated_at = now
            if not page.has_more:
                break
    return counts


async def _all_source_snapshots(
    hermes: HermesClient,
    *,
    correlation_id: str,
    entity_type: str,
) -> dict[int, dict[str, Any]]:
    method = (
        hermes.admin_projection_trip_jobs
        if entity_type == "TRIP_JOB"
        else hermes.admin_projection_trip_steps
    )
    after_id = 0
    snapshot_max_id: int | None = None
    items: dict[int, dict[str, Any]] = {}
    while True:
        params: dict[str, int] = {"limit": 1000}
        if after_id:
            params["after_id"] = after_id
            params["snapshot_max_id"] = int(snapshot_max_id or 0)
        page = await method(correlation_id=correlation_id, params=params)
        snapshot_max_id = snapshot_max_id if snapshot_max_id is not None else page.snapshot_max_id
        if page.snapshot_max_id != snapshot_max_id:
            raise RuntimeError("reconciliation snapshot maximum changed")
        for model in page.items:
            payload = model.model_dump(mode="json")
            key = payload["source_id" if entity_type == "TRIP_JOB" else "source_step_id"]
            items[int(key)] = payload
        if not page.has_more:
            return items
        if page.next_after_id is None:
            raise RuntimeError("reconciliation page omitted its continuation cursor")
        after_id = page.next_after_id


async def reconcile_projection(
    session_factory: async_sessionmaker[AsyncSession],
    hermes: HermesClient,
    *,
    correlation_id: str,
    repair: bool = True,
) -> dict[str, int]:
    started_at = datetime.now(UTC)
    run_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            AdminProjectionReconciliation(
                id=run_id,
                started_at=started_at,
                status="RUNNING",
            )
        )
    source_jobs = await _all_source_snapshots(
        hermes,
        correlation_id=correlation_id,
        entity_type="TRIP_JOB",
    )
    source_steps = await _all_source_snapshots(
        hermes,
        correlation_id=correlation_id,
        entity_type="TRIP_STEP",
    )
    async with session_factory() as session:
        local_jobs = {
            row.source_id: row
            for row in (await session.scalars(select(AdminTripProjection))).all()
        }
        local_steps = {
            row.source_step_id: row
            for row in (await session.scalars(select(AdminTripStepProjection))).all()
        }
    missing_job_ids = sorted(set(source_jobs) - set(local_jobs))
    missing_step_ids = sorted(set(source_steps) - set(local_steps))
    extra_job_ids = sorted(set(local_jobs) - set(source_jobs))
    extra_step_ids = sorted(set(local_steps) - set(source_steps))
    stale_job_ids = sorted(
        source_id
        for source_id in set(source_jobs) & set(local_jobs)
        if int(source_jobs[source_id]["source_version"]) != local_jobs[source_id].source_version
    )
    stale_step_ids = sorted(
        source_id
        for source_id in set(source_steps) & set(local_steps)
        if int(source_steps[source_id]["source_version"]) != local_steps[source_id].source_version
    )
    impossible_job_ids = sorted(
        row.source_id
        for row in local_jobs.values()
        if (
            row.identity_erased_at is not None
            and (row.association_state != "de-identified" or row.user_id is not None)
        )
        or (
            row.association_state == "linked"
            and (row.user_id is None or row.user_trip_id is None)
        )
    )
    if repair and (missing_job_ids or stale_job_ids or missing_step_ids or stale_step_ids):
        now = datetime.now(UTC)
        async with session_factory() as session, session.begin():
            for source_id in missing_job_ids + stale_job_ids:
                await _apply_job_snapshot(
                    session,
                    JobProjectionPayload.model_validate(source_jobs[source_id]),
                    now=now,
                )
            for source_id in missing_step_ids + stale_step_ids:
                await _apply_step_snapshot(
                    session,
                    StepProjectionPayload.model_validate(source_steps[source_id]),
                    now=now,
                )
    result = {
        "missing_count": len(missing_job_ids) + len(missing_step_ids),
        "extra_count": len(extra_job_ids) + len(extra_step_ids),
        "stale_count": len(stale_job_ids) + len(stale_step_ids),
        "impossible_count": len(impossible_job_ids),
    }
    finished_at = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        run = await session.get(AdminProjectionReconciliation, run_id)
        if run is None:
            raise RuntimeError("reconciliation run disappeared")
        run.finished_at = finished_at
        run.status = "PASSED" if not any(result.values()) else "FAILED"
        run.missing_count = result["missing_count"]
        run.extra_count = result["extra_count"]
        run.stale_count = result["stale_count"]
        run.impossible_count = result["impossible_count"]
        run.summary_json = {
            "missing_job_source_ids": missing_job_ids[:100],
            "missing_step_source_ids": missing_step_ids[:100],
            "extra_job_source_ids": extra_job_ids[:100],
            "extra_step_source_ids": extra_step_ids[:100],
            "stale_job_source_ids": stale_job_ids[:100],
            "stale_step_source_ids": stale_step_ids[:100],
            "impossible_job_source_ids": impossible_job_ids[:100],
            "repair_attempted": repair,
        }
        state = await session.get(AdminProjectionConsumerState, 1)
        if state is not None:
            state.last_reconciliation_at = finished_at
    return result
