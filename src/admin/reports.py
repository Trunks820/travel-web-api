from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.projection import runtime_projection
from src.db.models import (
    AdminAuditLog,
    AdminTripProjection,
    AdminTripStepProjection,
    AppUser,
    Invitation,
    UserTrip,
)
from src.quota.service import quota_snapshot

TERMINAL = {"SUCCESS", "FAILED", "TIMEOUT", "REJECTED"}
PROCESSING = {"PENDING", "RUNNING"}
AUDIT_ACTIONS = {
    "ADMIN_ACCESS_DENIED",
    "ADMIN_WRITE_FAILED",
    "CREATE_INVITATION_BATCH",
    "DISABLE_INVITATION_BATCH",
    "DISABLE_INVITATION_CODE",
    "DISABLE_USER",
    "DOWNLOAD_ARTIFACT",
    "GRANT_ADMIN",
    "LOOKUP_INVITATION_CODE",
    "QUOTA_ADJUST",
    "REVEAL_USER_EMAIL",
    "RESTORE_USER",
    "QUOTA_REVERSE",
    "REVOKE_ADMIN",
    "SYSTEM_BOOTSTRAP",
    "VIEW_FAILED_DRAFT",
}
AUDIT_RESULTS = {"SUCCESS", "FAILURE"}


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "not_applicable": denominator == 0,
    }


def trip_exception(row: AdminTripProjection, now: datetime) -> bool:
    if row.status in {"FAILED", "TIMEOUT"}:
        return True
    if runtime_projection(row, now)["is_slow"]:
        return True
    if row.status == "SUCCESS" and row.result_type in {
        "NO_CANDIDATES",
        "NO_USABLE_ROUTE",
    }:
        return True
    if row.error_code == "CITY_CLARIFICATION_REQUIRED":
        return False
    return row.error_code in {
        "CITY_PREPARING",
        "CITY_COLLECTION_FAILED",
        "CITY_DATA_INSUFFICIENT",
        "CITY_DISABLED",
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def duration_summary(values: list[float]) -> dict[str, float | None]:
    return {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}


async def dashboard(session: AsyncSession, *, as_of: datetime) -> dict[str, Any]:
    now = as_of
    users = (await session.execute(select(AppUser))).scalars().all()
    trips = (
        (
            await session.execute(
                select(AdminTripProjection).where(
                    AdminTripProjection.created_at >= now - timedelta(hours=24)
                )
            )
        )
        .scalars()
        .all()
    )
    status = Counter(row.status for row in trips)
    terminal_count = sum(status[value] for value in TERMINAL)
    zero_quota = 0
    for user in users:
        if (await quota_snapshot(session, user.id)).remaining == 0:
            zero_quota += 1
    invitations = (await session.execute(select(Invitation))).scalars().all()
    active = sum(
        row.disabled_at is None
        and row.redeemed_at is None
        and (row.expires_at is None or row.expires_at > now)
        for row in invitations
    )
    expiring = sum(
        row.disabled_at is None
        and row.redeemed_at is None
        and row.expires_at is not None
        and now < row.expires_at <= now + timedelta(days=7)
        for row in invitations
    )
    exceptions = sorted(
        (row for row in trips if trip_exception(row, now)),
        key=lambda row: row.created_at,
        reverse=True,
    )
    return {
        "as_of": now.isoformat(),
        "users": {
            "total": len(users),
            "active": sum(row.status == "ACTIVE" for row in users),
            "disabled": sum(row.status == "DISABLED" for row in users),
            "zero_quota": zero_quota,
            "new_7d": sum(row.created_at >= now - timedelta(days=7) for row in users),
        },
        "trips_24h": {
            **{name: status[name] for name in (*sorted(TERMINAL), "PENDING", "RUNNING")},
            "processing": sum(status[value] for value in PROCESSING),
            "terminal_success_rate": ratio(status["SUCCESS"], terminal_count),
        },
        "invitations": {
            "active_unused": active,
            "expiring_7d": expiring,
            "disabled": sum(row.disabled_at is not None for row in invitations),
        },
        "recent_exceptions": [
            {
                "job_id": row.job_id,
                "status": row.status,
                "city": row.city,
                "error_code": row.error_code,
                "slow": runtime_projection(row, now)["is_slow"],
            }
            for row in exceptions[:10]
        ],
    }


async def trip_generation_report(
    session: AsyncSession,
    *,
    city: str | None,
    time_from: datetime | None,
    time_to: datetime | None,
    status_filter: str | None,
    error_code: str | None,
    result_type: str | None,
    detailed_reason: str | None,
    as_of: datetime,
) -> dict[str, Any]:
    now = as_of
    statement = select(AdminTripProjection)
    if city:
        statement = statement.where(AdminTripProjection.city == city)
    if time_from:
        statement = statement.where(AdminTripProjection.created_at >= time_from)
    if time_to:
        statement = statement.where(AdminTripProjection.created_at < time_to)
    if status_filter:
        statement = statement.where(AdminTripProjection.status == status_filter)
    if error_code:
        statement = statement.where(AdminTripProjection.error_code == error_code)
    if result_type:
        statement = statement.where(AdminTripProjection.result_type == result_type)
    if detailed_reason:
        statement = statement.where(AdminTripProjection.detailed_reason == detailed_reason)
    rows = (await session.execute(statement)).scalars().all()
    terminal = [row for row in rows if row.status in TERMINAL]
    status = Counter(row.status for row in terminal)
    types = Counter(row.result_type for row in terminal if row.result_type)
    valid_guides = sum(row.guide_result_state == "AVAILABLE" for row in terminal)
    elapsed = [float(runtime_projection(row, now)["total_duration_ms"]) for row in rows]
    stages: dict[str, list[float]] = defaultdict(list)
    job_ids = [row.job_id for row in rows if row.trace_completeness == "COMPLETE"]
    if job_ids:
        step_rows = (
            await session.scalars(
                select(AdminTripStepProjection).where(
                    AdminTripStepProjection.job_id.in_(job_ids),
                    AdminTripStepProjection.duration_ms.is_not(None),
                )
            )
        ).all()
        for step in step_rows:
            stages[step.stage].append(float(step.duration_ms or 0))
    slow = sum(runtime_projection(row, now)["is_slow"] for row in rows)
    trend: dict[str, Counter[str]] = defaultdict(Counter)
    for row in terminal:
        trend[row.created_at.date().isoformat()][row.status] += 1
    return {
        "as_of": now.isoformat(),
        "filters": {
            "city": city,
            "time_from": time_from.isoformat() if time_from else None,
            "time_to": time_to.isoformat() if time_to else None,
            "status": status_filter,
            "error_code": error_code,
            "result_type": result_type,
            "detailed_reason": detailed_reason,
        },
        "terminal_count": len(terminal),
        "terminal_trend": [
            {
                "date": day,
                "terminal_count": sum(counts.values()),
                "status_distribution": dict(counts),
            }
            for day, counts in sorted(trend.items())
        ],
        "status_distribution": dict(status),
        "terminal_success_rate": ratio(status["SUCCESS"], len(terminal)),
        "valid_guide_rate": ratio(valid_guides, len(terminal)),
        "no_candidates_rate": ratio(types["NO_CANDIDATES"], len(terminal)),
        "no_usable_route_rate": ratio(types["NO_USABLE_ROUTE"], len(terminal)),
        "duration_ms": {
            "total": duration_summary(elapsed),
            "stages": {name: duration_summary(values) for name, values in sorted(stages.items())},
        },
        "slow_tasks": {"count": slow, "rate": ratio(slow, len(rows))},
        "error_distribution": dict(Counter(row.error_code for row in rows if row.error_code)),
        "detailed_reason_distribution": dict(
            Counter(row.detailed_reason for row in rows if row.detailed_reason)
        ),
        "result_type_distribution": dict(types),
    }


def _budget_band(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    if value < 1_000:
        return "UNDER_1000"
    if value < 3_000:
        return "1000_2999"
    if value < 8_000:
        return "3000_7999"
    return "8000_PLUS"


def _must_include_key(item: object) -> str | None:
    if isinstance(item, dict):
        canonical_id = item.get("canonical_place_id") or item.get("place_id")
        if canonical_id not in (None, ""):
            return f"canonical:{canonical_id}"
        name = str(item.get("name") or "").strip()
        return f"name:{name}" if name else None
    name = str(item).strip()
    return f"name:{name}" if name else None


async def preference_report(
    session: AsyncSession,
    *,
    city: str | None,
    time_from: datetime | None,
    time_to: datetime | None,
) -> dict[str, Any]:
    statement = select(UserTrip.user_id, UserTrip.request_json)
    if city:
        statement = statement.where(UserTrip.city == city)
    if time_from:
        statement = statement.where(UserTrip.created_at >= time_from)
    if time_to:
        statement = statement.where(UserTrip.created_at < time_to)
    rows = (await session.execute(statement)).all()
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    users = {user_id for user_id, _request in rows if user_id is not None}
    scalar_fields = ("to_city", "days", "people_count", "pace", "commute_mode")
    multi_fields = ("preferences", "avoid", "time_preferences")
    for _user_id, request in rows:
        for field in scalar_fields:
            if request.get(field) not in (None, ""):
                counters[field][str(request[field])] += 1
        if (band := _budget_band(request.get("budget"))) is not None:
            counters["budget_band"][band] += 1
        for field in multi_fields:
            values = request.get(field) or []
            if isinstance(values, list):
                for value in {str(item) for item in values}:
                    counters[field][value] += 1
            elif isinstance(values, dict):
                for key, value in values.items():
                    if value not in (None, "", [], {}):
                        counters[field][str(key)] += 1
        must_include = request.get("must_include") or []
        if isinstance(must_include, list):
            values = {
                value for item in must_include if (value := _must_include_key(item)) is not None
            }
            for value in values:
                counters["must_include"][value] += 1
        counters["accommodation_filled"]["yes" if request.get("accommodation") else "no"] += 1
    total = len(rows)
    result: dict[str, Any] = {}
    privacy_bounded_fields = {"preferences", "avoid", "time_preferences", "must_include"}
    for field, counts in counters.items():
        normalized = Counter()
        for value, count in counts.items():
            normalized[value if count >= 3 or field not in privacy_bounded_fields else "OTHER"] += (
                count
            )
        result[field] = [
            {"value": value, "request_count": count, "request_share": ratio(count, total)}
            for value, count in normalized.most_common()
        ]
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "filters": {
            "city": city,
            "time_from": time_from.isoformat() if time_from else None,
            "time_to": time_to.isoformat() if time_to else None,
        },
        "request_count": total,
        "identified_distinct_user_count": len(users),
        "fields": result,
    }


def _audit_filters(
    statement: Select[Any],
    *,
    action: str | None,
    result: str | None,
    error_code: str | None,
    time_from: datetime | None,
    time_to: datetime | None,
) -> Select[Any]:
    if action:
        statement = statement.where(AdminAuditLog.action == action)
    if result:
        statement = statement.where(AdminAuditLog.result == result)
    if error_code:
        statement = statement.where(AdminAuditLog.error_code == error_code)
    if time_from:
        statement = statement.where(AdminAuditLog.created_at >= time_from)
    if time_to:
        statement = statement.where(AdminAuditLog.created_at < time_to)
    return statement


async def audit_events(
    session: AsyncSession,
    *,
    page: int,
    limit: int,
    action: str | None,
    result: str | None,
    error_code: str | None,
    time_from: datetime | None,
    time_to: datetime | None,
) -> dict[str, Any]:
    total_statement = _audit_filters(
        select(func.count()).select_from(AdminAuditLog),
        action=action,
        result=result,
        error_code=error_code,
        time_from=time_from,
        time_to=time_to,
    )
    total = int(await session.scalar(total_statement) or 0)
    statement = _audit_filters(
        select(AdminAuditLog),
        action=action,
        result=result,
        error_code=error_code,
        time_from=time_from,
        time_to=time_to,
    )
    rows = (
        (
            await session.execute(
                statement.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    actor_ids = {row.actor_user_id for row in rows if row.actor_user_id is not None}
    actor_public_ids: dict[Any, str] = {}
    if actor_ids:
        actor_public_ids = dict(
            (
                await session.execute(
                    select(AppUser.id, AppUser.public_id).where(AppUser.id.in_(actor_ids))
                )
            ).all()
        )
    return {
        "items": [
            {
                "audit_id": row.public_id,
                "actor_user_id": actor_public_ids.get(row.actor_user_id),
                "actor_identity": row.actor_identity,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "result": row.result,
                "error_code": row.error_code,
                "reason": row.reason,
                "request_id": row.request_id,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "page": page,
        "limit": limit,
        "total": total,
        "filters": {
            "action": action,
            "result": result,
            "error_code": error_code,
            "time_from": time_from.isoformat() if time_from else None,
            "time_to": time_to.isoformat() if time_to else None,
        },
    }
