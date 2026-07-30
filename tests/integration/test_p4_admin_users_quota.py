from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.db.models import (
    AdminAuditLog,
    AppUser,
    QuotaAdjustment,
    QuotaGrant,
    UserIdentity,
    UserSession,
)
from src.security.secrets import hash_secret, new_opaque_id

ADMIN_ORIGIN = "https://admin.kakarot8.com"


async def _account(
    session_factory,
    test_settings,
    *,
    role: str = "USER",
    status: str = "ACTIVE",
    email: str | None = None,
):
    raw_token = new_opaque_id("session_", bytes_of_entropy=32)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status=status,
            role=role,
        )
        session.add(user)
        await session.flush()
        if email:
            session.add(
                UserIdentity(
                    user_id=user.id,
                    provider="email_otp",
                    provider_subject=email,
                    verified_email=email,
                )
            )
        session.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_secret(
                    raw_token,
                    purpose="session",
                    pepper=test_settings.secret_hash_pepper.get_secret_value(),
                ),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        await session.flush()
        return user, raw_token


def _headers(test_settings, token: str):
    return {
        "Cookie": f"{test_settings.cookie_name}={token}",
        "Origin": ADMIN_ORIGIN,
    }


@pytest.mark.asyncio
async def test_admin_me_visitor_user_admin_owner_matrix(
    client,
    session_factory,
    test_settings,
):
    ordinary, ordinary_token = await _account(session_factory, test_settings)
    admin, admin_token = await _account(session_factory, test_settings, role="ADMIN")
    owner, owner_token = await _account(session_factory, test_settings)
    test_settings.admin_owner_user_id = owner.id

    assert (await client.get("/api/admin/me")).status_code == 401
    denied = await client.get(
        "/api/admin/me",
        headers=_headers(test_settings, ordinary_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_REQUIRED"
    assert "request_id" in denied.json()
    async with session_factory() as session:
        denial_audit = await session.scalar(
            select(AdminAuditLog).where(AdminAuditLog.action == "ADMIN_ACCESS_DENIED")
        )
        assert denial_audit is not None
        assert denial_audit.actor_identity == "USER"
        assert denial_audit.error_code == "ADMIN_REQUIRED"

    admin_response = await client.get(
        "/api/admin/me",
        headers=_headers(test_settings, admin_token),
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["product_identity"] == "ADMIN"
    assert "role.manage" not in admin_response.json()["capabilities"]

    owner_response = await client.get(
        "/api/admin/me",
        headers=_headers(test_settings, owner_token),
    )
    assert owner_response.status_code == 200
    assert owner_response.json()["product_identity"] == "OWNER"
    assert "role.manage" in owner_response.json()["capabilities"]
    assert ordinary.id != admin.id


@pytest.mark.asyncio
async def test_owner_user_role_disable_restore_email_and_idempotency(
    client,
    session_factory,
    test_settings,
):
    owner, owner_token = await _account(
        session_factory,
        test_settings,
        email="owner@example.com",
    )
    target, target_token = await _account(
        session_factory,
        test_settings,
        email="target@example.com",
    )
    test_settings.admin_owner_user_id = owner.id
    headers = _headers(test_settings, owner_token)

    listing = await client.get("/api/admin/users?q=target", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["masked_email"] == "t***@example.com"
    assert "target@example.com" not in listing.text

    email = await client.get(f"/api/admin/users/{target.public_id}/email", headers=headers)
    assert email.status_code == 200
    assert email.json()["email"] == "target@example.com"
    assert email.headers["cache-control"] == "no-store"

    grant = await client.post(
        f"/api/admin/users/{target.public_id}/grant-admin",
        headers=headers,
        json={"reason": "promote operator", "idempotency_key": str(uuid.uuid4())},
    )
    assert grant.status_code == 200
    assert grant.json()["role"] == "ADMIN"
    assert (
        await client.get("/api/me", headers=_headers(test_settings, target_token))
    ).status_code == 401

    revoke_key = uuid.uuid4()
    body = {"reason": "operator rotation", "idempotency_key": str(revoke_key)}
    revoke = await client.post(
        f"/api/admin/users/{target.public_id}/revoke-admin",
        headers=headers,
        json=body,
    )
    assert revoke.status_code == 200
    assert revoke.json()["role"] == "USER"
    replay = await client.post(
        f"/api/admin/users/{target.public_id}/revoke-admin",
        headers=headers,
        json=body,
    )
    assert replay.status_code == 200
    conflict = await client.post(
        f"/api/admin/users/{target.public_id}/revoke-admin",
        headers=headers,
        json={"reason": "different request", "idempotency_key": str(revoke_key)},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    disable = await client.post(
        f"/api/admin/users/{target.public_id}/disable",
        headers=headers,
        json={"reason": "abuse response", "idempotency_key": str(uuid.uuid4())},
    )
    assert disable.status_code == 200
    assert disable.json()["status"] == "DISABLED"
    restore = await client.post(
        f"/api/admin/users/{target.public_id}/restore",
        headers=headers,
        json={"reason": "review complete", "idempotency_key": str(uuid.uuid4())},
    )
    assert restore.status_code == 200
    assert restore.json()["status"] == "ACTIVE"
    assert (
        await client.get("/api/me", headers=_headers(test_settings, target_token))
    ).status_code == 401

    owner_disable = await client.post(
        f"/api/admin/users/{owner.public_id}/disable",
        headers=headers,
        json={"reason": "unsafe self action", "idempotency_key": str(uuid.uuid4())},
    )
    assert owner_disable.status_code == 409
    assert owner_disable.json()["error"]["code"] == "LAST_OWNER_PROTECTED"

    async with session_factory() as session:
        reveal = await session.scalar(
            select(AdminAuditLog).where(AdminAuditLog.action == "REVEAL_USER_EMAIL")
        )
        assert reveal is not None
        assert "target@example.com" not in str(reveal.after_json)


@pytest.mark.asyncio
async def test_admin_cannot_operate_admin_or_owner(
    client,
    session_factory,
    test_settings,
):
    owner, _ = await _account(session_factory, test_settings)
    actor, actor_token = await _account(session_factory, test_settings, role="ADMIN")
    other_admin, _ = await _account(session_factory, test_settings, role="ADMIN")
    user, _ = await _account(session_factory, test_settings)
    test_settings.admin_owner_user_id = owner.id
    headers = _headers(test_settings, actor_token)

    for target in (actor, owner, other_admin):
        response = await client.post(
            f"/api/admin/users/{target.public_id}/disable",
            headers=headers,
            json={"reason": "forbidden target", "idempotency_key": str(uuid.uuid4())},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ADMIN_FORBIDDEN"

    allowed = await client.post(
        f"/api/admin/users/{user.public_id}/disable",
        headers=headers,
        json={"reason": "ordinary user", "idempotency_key": str(uuid.uuid4())},
    )
    assert allowed.status_code == 200
    role_denied = await client.post(
        f"/api/admin/users/{user.public_id}/grant-admin",
        headers=headers,
        json={"reason": "not owner", "idempotency_key": str(uuid.uuid4())},
    )
    assert role_denied.status_code == 403
    assert role_denied.json()["error"]["code"] == "OWNER_REQUIRED"


@pytest.mark.asyncio
async def test_signed_quota_adjustment_insufficient_reversal_and_replay(
    client,
    session_factory,
    test_settings,
):
    owner, owner_token = await _account(session_factory, test_settings)
    target, _ = await _account(session_factory, test_settings)
    test_settings.admin_owner_user_id = owner.id
    async with session_factory() as session, session.begin():
        session.add(
            QuotaGrant(
                user_id=target.id,
                period_type="BETA_LIFETIME",
                period_key="v0.1-beta",
                units=3,
                reason="INITIAL_BETA",
                idempotency_key=f"initial:{target.id}",
            )
        )
    headers = _headers(test_settings, owner_token)

    deduction_key = uuid.uuid4()
    deduction_body = {
        "target_user_id": target.public_id,
        "delta": -2,
        "reason": "manual correction",
        "note": "approved",
        "idempotency_key": str(deduction_key),
    }
    deduction = await client.post(
        "/api/admin/quota-adjustments",
        headers=headers,
        json=deduction_body,
    )
    assert deduction.status_code == 201
    assert deduction.json()["adjustment"]["before"] == 3
    assert deduction.json()["adjustment"]["after"] == 1
    replay = await client.post(
        "/api/admin/quota-adjustments",
        headers=headers,
        json=deduction_body,
    )
    assert replay.status_code == 201
    assert (
        replay.json()["adjustment"]["adjustment_id"]
        == (deduction.json()["adjustment"]["adjustment_id"])
    )

    insufficient_key = uuid.uuid4()
    insufficient = await client.post(
        "/api/admin/quota-adjustments",
        headers=headers,
        json={
            "target_user_id": target.public_id,
            "delta": -2,
            "reason": "too much",
            "idempotency_key": str(insufficient_key),
        },
    )
    assert insufficient.status_code == 409
    assert insufficient.json()["error"]["code"] == "QUOTA_BALANCE_INSUFFICIENT"
    async with session_factory() as session:
        failed_write = await session.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "ADMIN_WRITE_FAILED",
                AdminAuditLog.error_code == "QUOTA_BALANCE_INSUFFICIENT",
            )
        )
        assert failed_write is not None
        assert failed_write.result == "FAILURE"
        assert failed_write.idempotency_key == insufficient_key

    reverse = await client.post(
        f"/api/admin/quota-adjustments/{deduction.json()['adjustment']['adjustment_id']}/reverse",
        headers=headers,
        json={"reason": "undo correction", "idempotency_key": str(uuid.uuid4())},
    )
    assert reverse.status_code == 201
    assert reverse.json()["adjustment"]["delta"] == 2
    assert reverse.json()["adjustment"]["after"] == 3
    second_reverse = await client.post(
        f"/api/admin/quota-adjustments/{deduction.json()['adjustment']['adjustment_id']}/reverse",
        headers=headers,
        json={"reason": "repeat undo", "idempotency_key": str(uuid.uuid4())},
    )
    assert second_reverse.status_code == 409
    assert second_reverse.json()["error"]["code"] == "ADJUSTMENT_ALREADY_REVERSED"

    disabled = await client.post(
        f"/api/admin/users/{target.public_id}/disable",
        headers=headers,
        json={"reason": "support hold", "idempotency_key": str(uuid.uuid4())},
    )
    assert disabled.status_code == 200
    disabled_adjustment = await client.post(
        "/api/admin/quota-adjustments",
        headers=headers,
        json={
            "target_user_id": target.public_id,
            "delta": 1,
            "reason": "credit disabled account",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert disabled_adjustment.status_code == 201
    assert disabled_adjustment.json()["adjustment"]["after"] == 4

    missing = await client.post(
        "/api/admin/quota-adjustments",
        headers=headers,
        json={
            "target_user_id": "usr_closed_or_missing",
            "delta": 1,
            "reason": "must not create identity",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ADMIN_RESOURCE_NOT_FOUND"

    ledger = await client.get(
        f"/api/admin/users/{target.public_id}/quota-ledger",
        headers=headers,
    )
    assert ledger.status_code == 200
    assert ledger.json()["quota"]["remaining"] == 4
    assert len(ledger.json()["items"]) == 3
    reversal_item = next(item for item in ledger.json()["items"] if item["delta"] == 2)
    assert (
        reversal_item["reverses_adjustment_id"] == deduction.json()["adjustment"]["adjustment_id"]
    )
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(QuotaAdjustment)) == 3
