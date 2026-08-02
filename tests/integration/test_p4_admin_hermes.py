from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.db.models import (
    AdminAuditLog,
    AdminProjectionConsumerState,
    AdminTripProjection,
    AdminTripStepProjection,
    AppUser,
    UserSession,
)
from src.integrations.hermes import HermesBusinessError
from src.security.secrets import hash_secret, new_opaque_id
from tests.factories import unique_display_name_fields

ADMIN_ORIGIN = "https://admin.kakarot8.com"


async def _account(session_factory, settings, *, role: str):
    raw_token = new_opaque_id("session_", bytes_of_entropy=32)
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
                    raw_token,
                    purpose="session",
                    pepper=settings.secret_hash_pepper.get_secret_value(),
                ),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        return user, raw_token


def _headers(settings, token: str):
    return {
        "Cookie": f"{settings.cookie_name}={token}",
        "Origin": ADMIN_ORIGIN,
    }


@pytest.mark.asyncio
async def test_admin_trip_routes_auth_default_window_filters_and_safe_projection(
    client,
    hermes,
    session_factory,
    test_settings,
):
    _user, user_token = await _account(session_factory, test_settings, role="USER")
    _admin, admin_token = await _account(session_factory, test_settings, role="ADMIN")
    admin_headers = _headers(test_settings, admin_token)

    assert (await client.get("/api/admin/trip-jobs")).status_code == 401
    assert (
        await client.get(
            "/api/admin/trip-jobs",
            headers=_headers(test_settings, user_token),
        )
    ).status_code == 403

    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        state = await session.get(AdminProjectionConsumerState, 1)
        state.latest_heartbeat_watermark = 0
        state.latest_heartbeat_observed_at = now
        state.sync_checked_at = now
        state.initialization_state = "INITIALIZED"
        session.add(
            AdminTripProjection(
                job_id="hermes-admin-job-1",
                source_id=1,
                source_version=1,
                source="WEB",
                city="重庆",
                days=3,
                status="PENDING",
                current_stage="FINAL_WRITER",
                result_type=None,
                result_record_id=None,
                guide_result_state="NOT_APPLICABLE",
                error_code=None,
                safe_error_message=None,
                detailed_reason=None,
                created_at=now - timedelta(seconds=181),
                started_at=now - timedelta(seconds=180),
                finished_at=None,
                retry_count=0,
                failed_draft_available=False,
                trace_completeness="COMPLETE",
                association_state="unlinked",
                association_version=1,
                identity_erased_at=None,
                user_trip_id=None,
                user_id=None,
                source_updated_at=now,
                synced_at=now,
            )
        )
        session.add(
            AdminTripStepProjection(
                source_step_id=1,
                job_id="hermes-admin-job-1",
                source_version=1,
                stage="FINAL_WRITER",
                status="RUNNING",
                attempt=1,
                publish_retry_round=0,
                started_at=now - timedelta(seconds=180),
                finished_at=None,
                duration_ms=None,
                source_updated_at=now,
                synced_at=now,
            )
        )
    listing = await client.get(
        "/api/admin/trip-jobs",
        params={"city": " 重庆 ", "status": "PENDING"},
        headers=admin_headers,
    )
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["is_slow"] is True
    assert item["timeout_settlement_anomaly"] is True
    assert item["association"] == {"state": "unlinked"}
    assert hermes.admin_calls == []

    invalid_range = await client.get(
        "/api/admin/trip-jobs",
        params={
            "time_from": "2026-07-30T12:00:00Z",
            "time_to": "2026-07-29T12:00:00Z",
        },
        headers=admin_headers,
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["error"]["code"] == "VALIDATION_ERROR"

    detail = await client.get(
        "/api/admin/trip-jobs/hermes-admin-job-1",
        headers=admin_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["steps"][0]["stage"] == "FINAL_WRITER"
    assert detail.json()["steps"][0]["stage_label_zh"] == "撰写攻略正文"
    assert "metadata" not in detail.text
    assert "provider_payload" not in detail.text


@pytest.mark.asyncio
async def test_failed_draft_is_audited_no_store_and_never_enters_audit_body(
    client,
    hermes,
    session_factory,
    test_settings,
):
    _admin, token = await _account(session_factory, test_settings, role="ADMIN")
    headers = _headers(test_settings, token)

    response = await client.get(
        "/api/admin/trip-jobs/hermes-admin-job-1/failed-draft",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["failed_draft"]["publication_status"] == ("UNPUBLISHED_DIAGNOSTIC")
    assert "未发布安全正文" in response.text

    async with session_factory() as session:
        success = await session.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "VIEW_FAILED_DRAFT",
                AdminAuditLog.result == "SUCCESS",
            )
        )
        assert success is not None
        assert success.after_json == {
            "publication_status": "UNPUBLISHED_DIAGNOSTIC",
            "plan_count": 1,
        }
        assert "未发布安全正文" not in str(success.after_json)

    hermes.admin_error = HermesBusinessError("FAILED_DRAFT_NOT_FOUND")
    missing = await client.get(
        "/api/admin/trip-jobs/missing/failed-draft",
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "private, no-store"
    assert missing.json()["error"]["code"] == "FAILED_DRAFT_NOT_FOUND"
    async with session_factory() as session:
        failure = await session.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "VIEW_FAILED_DRAFT",
                AdminAuditLog.result == "FAILURE",
            )
        )
        assert failure is not None
        assert failure.error_code == "FAILED_DRAFT_NOT_FOUND"


@pytest.mark.asyncio
async def test_artifact_routes_ready_expired_file_missing_download_audit(
    client,
    hermes,
    session_factory,
    test_settings,
):
    _admin, token = await _account(session_factory, test_settings, role="ADMIN")
    headers = _headers(test_settings, token)

    listing = await client.get(
        "/api/admin/artifacts",
        params={"artifact_type": "pdf", "status": "READY"},
        headers=headers,
    )
    assert listing.status_code == 200
    artifact = listing.json()["items"][0]
    assert artifact["status"] == "READY"
    assert "storage_key" not in listing.text
    assert "storage_backend" not in listing.text

    detail = await client.get(
        "/api/admin/artifacts/artifact-admin-1",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["artifact"]["result_record_id"] == "9001"

    download = await client.get(
        "/api/admin/artifacts/artifact-admin-1/download",
        headers=headers,
    )
    assert download.status_code == 200
    assert download.content == b"%PDF-safe"
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-length"] == "9"
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["content-disposition"] == (
        'attachment; filename="artifact-artifact-admin-1.pdf"'
    )
    async with session_factory() as session:
        success = await session.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "DOWNLOAD_ARTIFACT",
                AdminAuditLog.result == "SUCCESS",
            )
        )
        assert success is not None
        assert success.after_json == {
            "byte_size": 9,
            "mime_type": "application/pdf",
        }
        assert "%PDF-safe" not in str(success.after_json)

    hermes.admin_download_error = HermesBusinessError("ARTIFACT_FILE_MISSING")
    missing = await client.get(
        "/api/admin/artifacts/artifact-admin-1/download",
        headers=headers,
    )
    assert missing.status_code == 409
    assert missing.headers["cache-control"] == "private, no-store"
    assert missing.json()["error"]["code"] == "ARTIFACT_FILE_MISSING"

    hermes.admin_download_error = HermesBusinessError("ARTIFACT_EXPIRED")
    expired = await client.get(
        "/api/admin/artifacts/artifact-admin-1/download",
        headers=headers,
    )
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "ARTIFACT_EXPIRED"
    async with session_factory() as session:
        failures = (
            (
                await session.execute(
                    select(AdminAuditLog)
                    .where(
                        AdminAuditLog.action == "DOWNLOAD_ARTIFACT",
                        AdminAuditLog.result == "FAILURE",
                    )
                    .order_by(AdminAuditLog.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [row.error_code for row in failures] == [
            "ARTIFACT_FILE_MISSING",
            "ARTIFACT_EXPIRED",
        ]
