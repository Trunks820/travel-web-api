from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from src.auth.service import revoke_user_sessions
from src.db.models import (
    AppUser,
    EmailOtpChallenge,
    Invitation,
    InvitationRedemption,
    QuotaGrant,
    UserIdentity,
    UserSession,
)
from src.invitations.service import create_invitation
from src.security.secrets import hash_secret, new_opaque_id
from tests.factories import unique_display_name_fields

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "https://kakarot8.com"}


async def _invite(session_factory, settings) -> tuple[Invitation, str]:
    async with session_factory() as session, session.begin():
        invitation, raw = await create_invitation(
            session,
            settings,
            source_label="integration-test",
        )
    return invitation, raw


async def _send_register(client, mailer, email: str, invitation: str) -> tuple[str, str]:
    response = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={
            "mode": "register",
            "email": email,
            "invitation_code": invitation,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["challenge_id"], mailer.messages[-1]["code"]


async def _register(client, mailer, email: str, invitation: str) -> tuple[str, str]:
    challenge_id, code = await _send_register(client, mailer, email, invitation)
    response = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={"challenge_id": challenge_id, "code": code},
    )
    assert response.status_code == 200, response.text
    return challenge_id, code


async def test_registration_is_atomic_and_persists_only_hashes(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    invitation, raw_invitation = await _invite(session_factory, test_settings)
    challenge_id, raw_code = await _register(
        client,
        mailer,
        "USER@Example.com",
        raw_invitation,
    )
    raw_session = client.cookies[test_settings.cookie_name]

    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["quota"] == {
        "policy": "beta_lifetime",
        "limit": 3,
        "reserved": 0,
        "consumed": 0,
        "remaining": 3,
        "resets_at": None,
    }
    assert me.json()["active_trip"] is None
    assert me.json()["user"]["masked_email"] == "u***@example.com"
    assert re.fullmatch(r"user_[a-z0-9]{10}", me.json()["user"]["display_name"])
    assert me.json()["user"]["display_name_change_available_at"] is None

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AppUser)) == 1
        assert await session.scalar(select(func.count()).select_from(UserIdentity)) == 1
        assert await session.scalar(select(func.count()).select_from(InvitationRedemption)) == 1
        assert await session.scalar(select(func.count()).select_from(QuotaGrant)) == 1
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 1
        challenge = await session.scalar(
            select(EmailOtpChallenge).where(EmailOtpChallenge.public_id == challenge_id)
        )
        stored_invitation = await session.get(Invitation, invitation.id)
        stored_session = await session.scalar(select(UserSession))
        assert challenge is not None and challenge.code_hash != raw_code.encode()
        assert stored_invitation is not None
        assert stored_invitation.secret_hash != raw_invitation.encode()
        assert stored_session is not None
        assert stored_session.token_hash != raw_session.encode()
        assert raw_code.encode() not in challenge.code_hash
        assert raw_invitation.encode() not in stored_invitation.secret_hash
        assert raw_session.encode() not in stored_session.token_hash


async def test_send_code_is_non_enumerating_and_mode_correction_is_post_proof(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    unknown = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "missing@example.com"},
    )
    assert unknown.status_code == 200
    missing_code = mailer.messages[-1]["code"]
    corrected = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={"challenge_id": unknown.json()["challenge_id"], "code": missing_code},
    )
    assert corrected.status_code == 409
    assert corrected.json()["error"]["code"] == "REGISTRATION_REQUIRED"

    invitation, raw_invitation = await _invite(session_factory, test_settings)
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
            **unique_display_name_fields(),
        )
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider="email_otp",
                provider_subject="known@example.com",
                verified_email="known@example.com",
            )
        )
    known = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={
            "mode": "register",
            "email": "known@example.com",
            "invitation_code": raw_invitation,
        },
    )
    assert known.status_code == 200
    assert set(unknown.json()) == set(known.json())
    correction = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={
            "challenge_id": known.json()["challenge_id"],
            "code": mailer.messages[-1]["code"],
        },
    )
    assert correction.status_code == 409
    assert correction.json()["error"]["code"] == "LOGIN_REQUIRED"
    async with session_factory() as session:
        stored_invitation = await session.get(Invitation, invitation.id)
        assert stored_invitation is not None and stored_invitation.redeemed_at is None
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 0
        assert await session.scalar(select(func.count()).select_from(QuotaGrant)) == 0


async def test_concurrent_invitation_redemption_succeeds_once(
    app,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _invitation, raw_invitation = await _invite(session_factory, test_settings)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://kakarot8.com",
        ) as client_a,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://kakarot8.com",
        ) as client_b,
    ):
        challenge_a, code_a = await _send_register(
            client_a, mailer, "first@example.com", raw_invitation
        )
        challenge_b, code_b = await _send_register(
            client_b, mailer, "second@example.com", raw_invitation
        )
        responses = await asyncio.gather(
            client_a.post(
                "/api/auth/email/verify",
                headers=ORIGIN,
                json={"challenge_id": challenge_a, "code": code_a},
            ),
            client_b.post(
                "/api/auth/email/verify",
                headers=ORIGIN,
                json={"challenge_id": challenge_b, "code": code_b},
            ),
        )
    assert sorted(response.status_code for response in responses) == [200, 422]
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AppUser)) == 1
        assert await session.scalar(select(func.count()).select_from(InvitationRedemption)) == 1
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 1
        assert await session.scalar(select(func.count()).select_from(QuotaGrant)) == 1


async def test_concurrent_registration_for_same_email_is_serialized(
    app,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _invitation_a, raw_invitation_a = await _invite(session_factory, test_settings)
    _invitation_b, raw_invitation_b = await _invite(session_factory, test_settings)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://kakarot8.com",
        ) as client_a,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://kakarot8.com",
        ) as client_b,
    ):
        challenge_a, code_a = await _send_register(
            client_a,
            mailer,
            "same-email@example.com",
            raw_invitation_a,
        )
        async with session_factory() as session, session.begin():
            await session.execute(
                update(EmailOtpChallenge)
                .where(EmailOtpChallenge.public_id == challenge_a)
                .values(sent_at=datetime.now(UTC) - timedelta(minutes=2))
            )
        challenge_b, code_b = await _send_register(
            client_b,
            mailer,
            "same-email@example.com",
            raw_invitation_b,
        )
        responses = await asyncio.gather(
            client_a.post(
                "/api/auth/email/verify",
                headers=ORIGIN,
                json={"challenge_id": challenge_a, "code": code_a},
            ),
            client_b.post(
                "/api/auth/email/verify",
                headers=ORIGIN,
                json={"challenge_id": challenge_b, "code": code_b},
            ),
        )

    assert sorted(response.status_code for response in responses) == [200, 409]
    correction = next(response for response in responses if response.status_code == 409)
    assert correction.json()["error"]["code"] == "LOGIN_REQUIRED"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AppUser)) == 1
        assert await session.scalar(select(func.count()).select_from(UserIdentity)) == 1
        assert await session.scalar(select(func.count()).select_from(InvitationRedemption)) == 1
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 1
        assert await session.scalar(select(func.count()).select_from(QuotaGrant)) == 1


async def test_otp_expiry_attempt_limit_reuse_and_wrong_purpose(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _invitation, raw_invitation = await _invite(session_factory, test_settings)
    challenge_id, raw_code = await _send_register(
        client, mailer, "attempts@example.com", raw_invitation
    )
    for expected in ("OTP_INVALID", "OTP_ATTEMPTS_EXCEEDED"):
        response = await client.post(
            "/api/auth/email/verify",
            headers=ORIGIN,
            json={"challenge_id": challenge_id, "code": "000000"},
        )
        assert response.json()["error"]["code"] == expected
    blocked = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={"challenge_id": challenge_id, "code": raw_code},
    )
    assert blocked.json()["error"]["code"] == "OTP_ATTEMPTS_EXCEEDED"

    _invitation_2, raw_invitation_2 = await _invite(session_factory, test_settings)
    expiring_id, expiring_code = await _send_register(
        client, mailer, "expired@example.com", raw_invitation_2
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            update(EmailOtpChallenge)
            .where(EmailOtpChallenge.public_id == expiring_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    expired = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={"challenge_id": expiring_id, "code": expiring_code},
    )
    assert expired.json()["error"]["code"] == "OTP_EXPIRED"

    wrong_id = new_opaque_id("otp_")
    wrong_code = "123456"
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        session.add(
            EmailOtpChallenge(
                public_id=wrong_id,
                email="closure@example.com",
                mode="closure",
                purpose="ACCOUNT_CLOSURE",
                code_hash=hash_secret(
                    wrong_code,
                    purpose=f"otp:{wrong_id}:ACCOUNT_CLOSURE",
                    pepper=test_settings.secret_hash_pepper.get_secret_value(),
                ),
                client_ip_hash=b"x" * 32,
                attempt_count=0,
                max_attempts=2,
                delivery_status="SENT",
                sent_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
    wrong_purpose = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={"challenge_id": wrong_id, "code": wrong_code},
    )
    assert wrong_purpose.json()["error"]["code"] == "OTP_INVALID"


async def test_session_negative_paths_logout_replay_and_origin(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _invitation, raw_invitation = await _invite(session_factory, test_settings)
    challenge_id, code = await _register(client, mailer, "session@example.com", raw_invitation)
    reused = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={"challenge_id": challenge_id, "code": code},
    )
    assert reused.json()["error"]["code"] == "OTP_USED"

    forged = httpx.Cookies()
    forged.set(test_settings.cookie_name, "forged", domain="kakarot8.com", path="/")
    original_cookies = client.cookies
    client.cookies = forged
    assert (await client.get("/api/me")).status_code == 401
    client.cookies = original_cookies

    no_origin = await client.post("/api/auth/logout")
    assert no_origin.status_code == 403
    bad_origin = await client.post(
        "/api/auth/logout",
        headers={"Origin": "https://evil.example"},
    )
    assert bad_origin.status_code == 403

    first = await client.post("/api/auth/logout", headers=ORIGIN)
    second = await client.post("/api/auth/logout", headers=ORIGIN)
    assert first.status_code == second.status_code == 200
    assert (await client.get("/api/me")).status_code == 401

    async with session_factory() as session:
        stored = await session.scalar(select(UserSession))
        assert stored is not None and stored.revoked_at is not None


async def test_expired_and_disabled_user_sessions_fail_closed(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _invitation, raw_invitation = await _invite(session_factory, test_settings)
    await _register(client, mailer, "disabled@example.com", raw_invitation)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    expired = await client.get("/api/me")
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "SESSION_EXPIRED"

    async with session_factory() as session, session.begin():
        await session.execute(
            update(UserSession).values(expires_at=datetime.now(UTC) + timedelta(days=7))
        )
        await session.execute(update(AppUser).values(status="DISABLED"))
    disabled = await client.get("/api/me")
    assert disabled.status_code == 401
    assert disabled.json()["error"]["code"] == "AUTH_REQUIRED"

    async with session_factory() as session, session.begin():
        await session.execute(
            update(EmailOtpChallenge).values(sent_at=datetime.now(UTC) - timedelta(minutes=2))
        )
    login = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "disabled@example.com"},
    )
    assert login.status_code == 200
    rejected = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={
            "challenge_id": login.json()["challenge_id"],
            "code": mailer.messages[-1]["code"],
        },
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "AUTH_REQUIRED"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 1


async def test_returning_login_issues_new_secure_host_only_session(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _invitation, raw_invitation = await _invite(session_factory, test_settings)
    await _register(client, mailer, "returning@example.com", raw_invitation)
    first_token = client.cookies[test_settings.cookie_name]
    await client.post("/api/auth/logout", headers=ORIGIN)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(EmailOtpChallenge).values(sent_at=datetime.now(UTC) - timedelta(minutes=2))
        )

    send = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "returning@example.com"},
    )
    assert send.status_code == 200
    verified = await client.post(
        "/api/auth/email/verify",
        headers=ORIGIN,
        json={
            "challenge_id": send.json()["challenge_id"],
            "code": mailer.messages[-1]["code"],
        },
    )
    assert verified.status_code == 200
    assert client.cookies[test_settings.cookie_name] != first_token
    set_cookie = verified.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "domain=" not in set_cookie
    assert (await client.get("/api/me")).status_code == 200


async def test_delivery_failure_is_safe_and_challenge_is_unusable(
    client,
    mailer,
    session_factory,
) -> None:
    mailer.fail = True
    response = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "delivery@example.com"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "EMAIL_DELIVERY_UNAVAILABLE",
            "message": "验证码暂时无法发送，请稍后再试。",
            "retryable": True,
        },
    }
    assert "simulated" not in response.text
    async with session_factory() as session:
        challenge = await session.scalar(select(EmailOtpChallenge))
        assert challenge is not None and challenge.delivery_status == "FAILED"


async def test_rate_limits_and_capability_session_revocation(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    test_settings.otp_per_ip_limit = 1
    first = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "limit-one@example.com"},
    )
    second = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "limit-two@example.com"},
    )
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "OTP_RATE_LIMITED"

    test_settings.otp_per_ip_limit = 20
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
            **unique_display_name_fields(),
        )
        session.add(user)
        await session.flush()
        session.add(
            UserSession(
                user_id=user.id,
                token_hash=b"r" * 32,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        changed = await revoke_user_sessions(
            session,
            user_id=user.id,
            reason="ROLE_CHANGED",
        )
        assert changed == 1
    async with session_factory() as session:
        stored = await session.scalar(select(UserSession).where(UserSession.user_id == user.id))
        assert stored is not None
        assert stored.revoked_at is not None
        assert stored.revoke_reason == "ROLE_CHANGED"
