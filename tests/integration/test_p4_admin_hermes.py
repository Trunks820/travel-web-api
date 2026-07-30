from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.db.models import AdminAuditLog, AppUser, UserSession
from src.integrations.hermes import HermesBusinessError
from src.security.secrets import hash_secret, new_opaque_id

ADMIN_ORIGIN = "https://admin.kakarot8.com"


async def _account(session_factory, settings, *, role: str):
    raw_token = new_opaque_id("session_", bytes_of_entropy=32)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role=role,
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

    hermes.admin_job_item.update(
        {
            "status": "PENDING",
            "current_stage": "FINAL_WRITER",
            "result_record_id": None,
            "safe_error": None,
            "detailed_reason": None,
            "finished_at": None,
            "failed_draft_available": False,
        }
    )
    listing = await client.get(
        "/api/admin/trip-jobs",
        params={"city": " 重庆 ", "status": "PENDING"},
        headers=admin_headers,
    )
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["slow"] is True
    assert item["is_exception"] is True
    assert item["exception_kind"] == "SLOW"
    call = hermes.admin_calls[-1]
    assert call[0] == "trip_jobs"
    assert call[1]["city"] == "重庆"
    assert call[1]["status"] == "PENDING"
    assert call[1]["time_from"] is not None

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
    trip_job = detail.json()["trip_job"]
    assert trip_job["steps"][0]["stage"] == "FINAL_WRITER"
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
