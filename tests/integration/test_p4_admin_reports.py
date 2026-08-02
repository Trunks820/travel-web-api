from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.admin.audit import append_admin_audit
from src.db.models import (
    AdminProjectionConsumerState,
    AdminTripProjection,
    AdminTripStepProjection,
    AppUser,
    UserSession,
    UserTrip,
)
from src.security.secrets import hash_secret, new_opaque_id
from tests.factories import unique_display_name_fields

ADMIN_ORIGIN = "https://admin.kakarot8.com"


async def _admin_account(session_factory, test_settings):
    token = new_opaque_id("session_", bytes_of_entropy=32)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="ADMIN",
            **unique_display_name_fields(),
        )
        session.add(user)
        await session.flush()
        session.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_secret(
                    token,
                    purpose="session",
                    pepper=test_settings.secret_hash_pepper.get_secret_value(),
                ),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        return user, token


def _headers(test_settings, token: str):
    return {
        "Cookie": f"{test_settings.cookie_name}={token}",
        "Origin": ADMIN_ORIGIN,
    }


def _trip(
    *,
    index: int,
    now: datetime,
    user_id,
    status: str,
    city: str = "重庆",
    elapsed_ms: int = 10_000,
    result_type: str | None = None,
    error_code: str | None = None,
    detailed_reason: str | None = None,
    request_json: dict | None = None,
):
    telemetry = {
        "total_elapsed_ms": elapsed_ms,
        "stage_durations_ms": {"retrieval": elapsed_ms / 4, "writer": elapsed_ms / 2},
    }
    if result_type:
        telemetry["result_type"] = result_type
    if detailed_reason:
        telemetry["detailed_reason"] = detailed_reason
    return UserTrip(
        public_id=f"trip_report_{index}",
        user_id=user_id,
        client_request_id=f"report-request-{index}",
        request_hash=f"{index:064d}",
        request_json=request_json
        or {
            "to_city": city,
            "days": 3,
            "people_count": 2,
            "preferences": ["美食"],
            "avoid": [],
        },
        city=city,
        days=3,
        status=status,
        hermes_job_id=f"hermes-report-{index}",
        error_code=error_code,
        telemetry_json=telemetry,
        created_at=now - timedelta(minutes=index),
        finished_at=now - timedelta(minutes=index) + timedelta(milliseconds=elapsed_ms)
        if status not in {"SUBMITTING", "PENDING", "RUNNING"}
        else None,
        updated_at=now,
        visible_until=now + timedelta(days=7),
        reconciliation_attempts=0,
    )


def _projection(trip: UserTrip, *, index: int, now: datetime):
    elapsed_ms = int(trip.telemetry_json.get("total_elapsed_ms", 0))
    terminal = trip.status not in {"SUBMITTING", "PENDING", "RUNNING"}
    started_at = (
        trip.created_at if terminal else now - timedelta(milliseconds=elapsed_ms)
    )
    projection_created_at = trip.created_at if terminal else started_at
    finished_at = started_at + timedelta(milliseconds=elapsed_ms) if terminal else None
    result_type = trip.telemetry_json.get("result_type")
    projection = AdminTripProjection(
        job_id=trip.hermes_job_id,
        source_id=10_000 + index,
        source_version=1,
        source="WEB",
        city=trip.city,
        days=trip.days,
        status=trip.status,
        current_stage=None if terminal else "FINAL_WRITER",
        result_type=result_type,
        result_record_id=index if result_type == "PLAN_READY" else None,
        guide_result_state="AVAILABLE" if result_type == "PLAN_READY" else "NOT_APPLICABLE",
        error_code=trip.error_code,
        safe_error_message=None,
        detailed_reason=trip.telemetry_json.get("detailed_reason"),
        created_at=projection_created_at,
        started_at=started_at,
        finished_at=finished_at,
        retry_count=0,
        failed_draft_available=False,
        trace_completeness="COMPLETE",
        association_state="linked" if trip.user_id else "unlinked",
        association_version=1,
        identity_erased_at=None,
        user_trip_id=trip.id,
        user_id=trip.user_id,
        source_updated_at=now,
        synced_at=now,
    )
    steps = [
        AdminTripStepProjection(
            source_step_id=20_000 + index,
            job_id=trip.hermes_job_id,
            source_version=1,
            stage="FINAL_WRITER",
            status="SUCCESS" if terminal else "RUNNING",
            attempt=1,
            publish_retry_round=0,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=elapsed_ms // 2 if terminal else None,
            source_updated_at=now,
            synced_at=now,
        )
    ]
    return projection, steps


@pytest.mark.asyncio
async def test_dashboard_and_trip_generation_formulas_filters_and_durations(
    client,
    session_factory,
    test_settings,
):
    admin, token = await _admin_account(session_factory, test_settings)
    now = datetime.now(UTC)
    rows = [
        _trip(
            index=1,
            now=now,
            user_id=admin.id,
            status="SUCCESS",
            elapsed_ms=100_000,
            result_type="PLAN_READY",
        ),
        _trip(
            index=2,
            now=now,
            user_id=admin.id,
            status="SUCCESS",
            elapsed_ms=200_000,
            result_type="NO_CANDIDATES",
        ),
        _trip(
            index=3,
            now=now,
            user_id=admin.id,
            status="FAILED",
            elapsed_ms=190_000,
            error_code="CITY_DISABLED",
            detailed_reason="disabled_by_policy",
        ),
        _trip(
            index=4,
            now=now,
            user_id=admin.id,
            status="REJECTED",
            error_code="CITY_CLARIFICATION_REQUIRED",
        ),
        _trip(
            index=5,
            now=now,
            user_id=None,
            status="TIMEOUT",
            city="杭州",
            elapsed_ms=50_000,
        ),
        _trip(
            index=6,
            now=now,
            user_id=admin.id,
            status="PENDING",
            elapsed_ms=181_000,
        ),
    ]
    async with session_factory() as session, session.begin():
        session.add_all(rows)
        await session.flush()
        projections = [_projection(row, index=index, now=now) for index, row in enumerate(rows)]
        session.add_all([projection for projection, _steps in projections])
        session.add_all([step for _projection_row, steps in projections for step in steps])
        state = await session.get(AdminProjectionConsumerState, 1)
        state.applied_high_watermark = 6
        state.latest_heartbeat_watermark = 6
        state.latest_heartbeat_observed_at = now
        state.sync_checked_at = now
        state.next_expected_sequence = 7
        state.stream_state = "ACTIVE"
        state.initialization_state = "INITIALIZED"

    headers = _headers(test_settings, token)
    dashboard = await client.get("/api/admin/dashboard", headers=headers)
    assert dashboard.status_code == 200
    dashboard_body = dashboard.json()
    assert dashboard_body["trips_24h"]["terminal_success_rate"] == {
        "value": 0.4,
        "not_applicable": False,
    }
    exception_ids = {item["job_id"] for item in dashboard_body["recent_exceptions"]}
    assert "hermes-report-3" in exception_ids
    assert "hermes-report-4" not in exception_ids
    assert "hermes-report-6" in exception_ids

    report = await client.get("/api/admin/reports/trip-generation", headers=headers)
    assert report.status_code == 200
    body = report.json()
    assert body["terminal_count"] == 5
    assert body["terminal_success_rate"]["value"] == 0.4
    assert body["valid_guide_rate"]["value"] == 0.2
    assert body["no_candidates_rate"]["value"] == 0.2
    assert body["slow_tasks"] == {
        "count": 4,
        "rate": {"value": 4 / 6, "not_applicable": False},
    }
    assert body["duration_ms"]["total"] == {"p50": 100_000.0, "p95": 200_000.0}
    assert body["duration_ms"]["stages"]["FINAL_WRITER"] == {
        "p50": 50_000.0,
        "p95": 100_000.0,
    }
    assert body["detailed_reason_distribution"] == {"disabled_by_policy": 1}

    filtered = await client.get(
        "/api/admin/reports/trip-generation",
        params={"city": "重庆", "result_type": "PLAN_READY"},
        headers=headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["terminal_count"] == 1
    assert filtered.json()["valid_guide_rate"]["value"] == 1.0


@pytest.mark.asyncio
async def test_preference_privacy_and_audit_allowlisted_filters(
    client,
    session_factory,
    test_settings,
):
    admin, token = await _admin_account(session_factory, test_settings)
    now = datetime.now(UTC)
    requests = [
        {
            "to_city": "重庆",
            "days": 3,
            "people_count": 2,
            "budget": 1200,
            "preferences": ["美食", "美食"],
            "avoid": ["排队"],
            "commute_mode": "transit",
            "must_include": [{"name": "洪崖洞", "place_id": 101}],
            "accommodation": {"name": "酒店"},
            "notes": "must never be reported",
        },
        {
            "to_city": "重庆",
            "days": 4,
            "people_count": 2,
            "budget": 2500,
            "preferences": ["美食"],
            "avoid": [],
            "commute_mode": "transit",
            "must_include": [{"name": "洪崖洞", "canonical_place_id": 101}],
        },
        {
            "to_city": "重庆",
            "days": 2,
            "people_count": 1,
            "budget": 500,
            "preferences": ["夜景", "美食"],
            "avoid": [],
            "commute_mode": "walking",
            "must_include": [{"name": "洪崖洞", "place_id": 101}],
        },
        {
            "to_city": "重庆",
            "days": 2,
            "people_count": 1,
            "preferences": ["夜景"],
            "avoid": [],
            "must_include": [{"name": "私藏小店"}],
        },
    ]
    rows = [
        _trip(
            index=20 + index,
            now=now,
            user_id=admin.id if index < 3 else None,
            status="SUCCESS",
            request_json=request,
        )
        for index, request in enumerate(requests)
    ]
    async with session_factory() as session, session.begin():
        session.add_all(rows)
        await append_admin_audit(
            session,
            test_settings,
            actor_user_id=admin.id,
            actor_identity="ADMIN",
            action="QUOTA_ADJUST",
            target_type="USER",
            target_id=admin.public_id,
            result="SUCCESS",
            request_id="report-audit-success",
            source_ip="203.0.113.1",
        )
        await append_admin_audit(
            session,
            test_settings,
            actor_user_id=admin.id,
            actor_identity="ADMIN",
            action="ADMIN_WRITE_FAILED",
            target_type="ADMIN_ENDPOINT",
            target_id="/api/admin/quota-adjustments",
            result="FAILURE",
            error_code="INSUFFICIENT_QUOTA",
            request_id="report-audit-failure",
            source_ip="203.0.113.1",
        )

    headers = _headers(test_settings, token)
    response = await client.get(
        "/api/admin/reports/user-preferences",
        params={"city": "重庆"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["request_count"] == 4
    assert body["identified_distinct_user_count"] == 1
    preference = {item["value"]: item for item in body["fields"]["preferences"]}
    assert preference["美食"]["request_count"] == 3
    assert preference["OTHER"]["request_count"] == 2
    must_include = {item["value"]: item["request_count"] for item in body["fields"]["must_include"]}
    assert must_include == {"canonical:101": 3, "OTHER": 1}
    assert "notes" not in response.text
    assert "must never be reported" not in response.text

    audit = await client.get(
        "/api/admin/audit-events",
        params={"result": "FAILURE", "error_code": "INSUFFICIENT_QUOTA"},
        headers=headers,
    )
    assert audit.status_code == 200
    assert audit.json()["total"] == 1
    assert audit.json()["items"][0]["action"] == "ADMIN_WRITE_FAILED"

    rejected = await client.get(
        "/api/admin/audit-events",
        params={"action": "NOT_ALLOWLISTED"},
        headers=headers,
    )
    assert rejected.status_code == 422
