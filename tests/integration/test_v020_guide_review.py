from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.admin import projection_router
from src.db.models import (
    AdminAuditLog,
    AdminProjectionConsumerState,
    AdminTripProjection,
    AppUser,
    UserSession,
    UserTrip,
)
from src.integrations.hermes_models import HermesInternalGuideResult
from src.security.secrets import hash_secret, new_opaque_id
from tests.factories import unique_display_name_fields

ADMIN_ORIGIN = "https://admin.kakarot8.com"


async def _account(session_factory, settings, *, role: str):
    token = new_opaque_id("session_", bytes_of_entropy=32)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role=role,
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
                    pepper=settings.secret_hash_pepper.get_secret_value(),
                ),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
    return user, token


def _headers(settings, token: str) -> dict[str, str]:
    return {
        "Cookie": f"{settings.cookie_name}={token}",
        "Origin": ADMIN_ORIGIN,
    }


async def _seed_available_guide(session_factory, user: AppUser) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        trip = UserTrip(
            public_id=new_opaque_id("trip_"),
            user_id=user.id,
            client_request_id="guide-review-request-1",
            request_hash="a" * 64,
            request_json={"to_city": "重庆", "days": 3, "people_count": 2},
            request_field_provenance={
                "to_city": "USER_SUPPLIED",
                "days": "USER_SUPPLIED",
                "people_count": "USER_SUPPLIED",
            },
            city="重庆",
            days=3,
            status="SUCCESS",
            hermes_job_id="guide-job-1",
            result_record_id=9001,
            created_at=now - timedelta(minutes=3),
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
            updated_at=now,
            visible_until=now + timedelta(days=7),
        )
        session.add(trip)
        await session.flush()
        session.add(
            AdminTripProjection(
                job_id="guide-job-1",
                source_id=201,
                source_version=3,
                source="WEB",
                city="重庆",
                days=3,
                status="SUCCESS",
                current_stage="SUCCESS",
                result_type="PLAN_READY",
                result_record_id=9001,
                guide_result_state="AVAILABLE",
                error_code=None,
                safe_error_message=None,
                detailed_reason=None,
                created_at=trip.created_at,
                started_at=trip.started_at,
                finished_at=trip.finished_at,
                retry_count=0,
                failed_draft_available=False,
                trace_completeness="COMPLETE",
                association_state="linked",
                association_version=trip.association_version,
                identity_erased_at=None,
                user_trip_id=trip.id,
                user_id=user.id,
                source_updated_at=now,
                synced_at=now,
            )
        )
        state = await session.get(AdminProjectionConsumerState, 1)
        state.latest_heartbeat_watermark = 3
        state.applied_high_watermark = 3
        state.next_expected_sequence = 4
        state.latest_heartbeat_observed_at = now
        state.sync_checked_at = now
        state.initialization_state = "INITIALIZED"


async def _install_guide_result(hermes) -> None:
    final_guide = await hermes.result(9001, job_id="guide-job-1", correlation_id="fixture")

    async def admin_guide_result(job_id: str, *, correlation_id: str):
        return HermesInternalGuideResult.model_validate(
            {
                "ok": True,
                "contract_version": "v1",
                "request_id": correlation_id,
                "job_id": job_id,
                "guide_result_state": "AVAILABLE",
                "result_type": "PLAN_READY",
                "result_record_id": 9001,
                "request": {
                    "values": {"to_city": "重庆", "days": 3},
                    "field_provenance": {
                        "to_city": "USER_SUPPLIED",
                        "days": "USER_SUPPLIED",
                    },
                },
                "final_guide": final_guide.model_dump(mode="json"),
                "artifacts": [hermes.admin_artifact_item],
            }
        )

    hermes.admin_guide_result = admin_guide_result


@pytest.mark.asyncio
async def test_guide_review_admin_exactly_one_owner_zero_and_user_failure_audit(
    client,
    hermes,
    session_factory,
    test_settings,
):
    admin, admin_token = await _account(session_factory, test_settings, role="ADMIN")
    owner, owner_token = await _account(session_factory, test_settings, role="ADMIN")
    _user, user_token = await _account(session_factory, test_settings, role="USER")
    await _seed_available_guide(session_factory, admin)
    await _install_guide_result(hermes)

    success = await client.get(
        "/api/admin/trip-jobs/guide-job-1/guide-review",
        headers=_headers(test_settings, admin_token),
    )
    assert success.status_code == 200
    assert success.headers["cache-control"] == "private, no-store"
    assert success.json()["request_source"] == "BFF_USER_TRIP"
    assert success.json()["trip_job"]["association"] == {
        "state": "linked",
        "user_id": admin.public_id,
        "display_name": admin.display_name,
    }
    assert "association" not in {
        key for key in success.json() if key != "trip_job"
    }

    denied = await client.get(
        "/api/admin/trip-jobs/guide-job-1/guide-review",
        headers=_headers(test_settings, user_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_FORBIDDEN"

    test_settings.admin_owner_user_id = owner.id
    owner_read = await client.get(
        "/api/admin/trip-jobs/guide-job-1/guide-review",
        headers=_headers(test_settings, owner_token),
    )
    assert owner_read.status_code == 200

    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(AdminAuditLog)
                    .where(AdminAuditLog.action == "READ_GUIDE_REVIEW")
                    .order_by(AdminAuditLog.created_at)
                )
            ).all()
        )
        assert [(row.actor_identity, row.result) for row in rows] == [
            ("ADMIN", "SUCCESS"),
            ("USER", "FAILURE"),
        ]
        assert all("重庆" not in str(row.after_json) for row in rows)


@pytest.mark.asyncio
async def test_guide_review_audit_failure_fails_closed(
    client,
    hermes,
    session_factory,
    test_settings,
    monkeypatch,
):
    admin, token = await _account(session_factory, test_settings, role="ADMIN")
    await _seed_available_guide(session_factory, admin)
    await _install_guide_result(hermes)

    async def fail_audit(*_args, **_kwargs):
        raise OSError("audit storage unavailable")

    monkeypatch.setattr(projection_router, "append_admin_audit", fail_audit)
    response = await client.get(
        "/api/admin/trip-jobs/guide-job-1/guide-review",
        headers=_headers(test_settings, token),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
    assert "final_guide" not in response.text
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(AdminAuditLog).where(
                AdminAuditLog.action == "READ_GUIDE_REVIEW"
            )
        )
        assert count == 0
