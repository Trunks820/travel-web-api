from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.db.models import AdminAuditLog, AppUser, Invitation, UserSession
from src.invitations.service import find_invitation
from src.security.secrets import hash_secret, new_opaque_id
from tests.factories import unique_display_name_fields


async def _admin(session_factory, settings):
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
                    pepper=settings.secret_hash_pepper.get_secret_value(),
                ),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        return user, token


def _headers(settings, token):
    return {
        "Cookie": f"{settings.cookie_name}={token}",
        "Origin": "https://admin.kakarot8.com",
    }


@pytest.mark.asyncio
async def test_short_code_batch_one_time_disclosure_lookup_and_disable(
    client,
    session_factory,
    test_settings,
):
    _user, token = await _admin(session_factory, test_settings)
    headers = _headers(test_settings, token)
    key = uuid.uuid4()
    body = {
        "name": "launch",
        "source_label": "ops",
        "count": 2,
        "valid_days": 30,
        "reason": "beta onboarding",
        "idempotency_key": str(key),
    }
    created = await client.post("/api/admin/invitation-batches", headers=headers, json=body)
    assert created.status_code == 201
    payload = created.json()
    assert payload["codes_disclosed"] is True
    assert len(payload["codes"]) == 2
    for code in payload["codes"]:
        assert re.fullmatch(
            r"YT-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}-"
            r"[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}",
            code,
        )
        assert not any(character in code for character in "01OIL")

    replay = await client.post("/api/admin/invitation-batches", headers=headers, json=body)
    assert replay.status_code == 201
    assert replay.json()["batch"]["batch_id"] == payload["batch"]["batch_id"]
    assert replay.json()["codes_disclosed"] is False
    assert replay.json()["codes"] == []

    detail = await client.get(
        f"/api/admin/invitation-batches/{payload['batch']['batch_id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert [item["sequence"] for item in detail.json()["codes"]] == ["#001", "#002"]
    assert payload["codes"][0] not in detail.text

    lookup = await client.post(
        "/api/admin/invitation-codes/lookup",
        headers=headers,
        json={"code": f" {payload['codes'][0].lower()} "},
    )
    assert lookup.status_code == 200
    assert lookup.headers["cache-control"] == "no-store"
    code_id = lookup.json()["code_id"]
    disabled = await client.post(
        f"/api/admin/invitation-codes/{code_id}/disable",
        headers=headers,
        json={"reason": "operator revoke", "idempotency_key": str(uuid.uuid4())},
    )
    assert disabled.status_code == 200
    lookup_after = await client.post(
        "/api/admin/invitation-codes/lookup",
        headers=headers,
        json={"code": payload["codes"][0]},
    )
    assert lookup_after.json()["status"] == "DISABLED"

    async with session_factory() as session:
        rows = (await session.execute(select(Invitation))).scalars().all()
        assert len(rows) == 2
        for row in rows:
            assert len(row.secret_hash) == 32
            assert payload["codes"][row.sequence_number - 1].encode() not in row.secret_hash
        found = await find_invitation(session, payload["codes"][1].lower(), test_settings)
        assert found is not None
        audits = (await session.execute(select(AdminAuditLog))).scalars().all()
        assert payload["codes"][0] not in str([audit.after_json for audit in audits])


@pytest.mark.asyncio
async def test_batch_creation_retries_digest_collision(
    client,
    session_factory,
    test_settings,
    monkeypatch,
):
    _user, token = await _admin(session_factory, test_settings)
    collision = "YT-2345-6789"
    replacement = "YT-ABCD-EFGH"
    async with session_factory() as session, session.begin():
        session.add(
            Invitation(
                secret_hash=hash_secret(
                    collision,
                    purpose="invitation",
                    pepper=test_settings.secret_hash_pepper.get_secret_value(),
                ),
                source_label="legacy",
                expires_at=None,
            )
        )
    generated = iter((collision, replacement))
    monkeypatch.setattr(
        "src.admin.invitations.new_short_invitation_code",
        lambda: next(generated),
    )
    response = await client.post(
        "/api/admin/invitation-batches",
        headers=_headers(test_settings, token),
        json={
            "name": "collision retry",
            "source_label": "ops",
            "count": 1,
            "valid_days": 7,
            "reason": "collision proof",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    assert response.json()["codes"] == [replacement]


@pytest.mark.asyncio
async def test_concurrent_same_key_batch_creation_has_one_batch(
    client,
    session_factory,
    test_settings,
):
    _user, token = await _admin(session_factory, test_settings)
    body = {
        "name": "concurrent",
        "source_label": "ops",
        "count": 2,
        "valid_days": 14,
        "reason": "same request retry",
        "idempotency_key": str(uuid.uuid4()),
    }
    headers = _headers(test_settings, token)
    first, second = await asyncio.gather(
        client.post("/api/admin/invitation-batches", headers=headers, json=body),
        client.post("/api/admin/invitation-batches", headers=headers, json=body),
    )
    assert first.status_code == second.status_code == 201
    payloads = (first.json(), second.json())
    assert {payload["codes_disclosed"] for payload in payloads} == {True, False}
    assert payloads[0]["batch"]["batch_id"] == payloads[1]["batch"]["batch_id"]
    async with session_factory() as session:
        assert (await session.scalar(select(func.count()).select_from(Invitation))) == 2
