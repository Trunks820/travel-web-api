from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from src.db.models import (
    AppUser,
    EmailOtpChallenge,
    Invitation,
    InvitationRedemption,
    QuotaGrant,
    TripQuotaEntry,
    UserIdentity,
    UserSession,
    UserTrip,
)
from src.quota.service import reserve_trip, settle_trip
from src.security.secrets import hash_secret, new_opaque_id, new_session_token
from src.trips.schemas import normalized_request_hash

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "https://kakarot8.com"}


async def _seed_user(
    session_factory,
    settings,
    *,
    email: str,
    credits: int = 10,
) -> tuple[AppUser, str]:
    now = datetime.now(UTC)
    raw_token = new_session_token()
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        invitation = Invitation(
            secret_hash=hashlib.sha256(f"invitation:{email}".encode()).digest(),
            source_label="p3-integration",
            redeemed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(invitation)
        await session.flush()
        session.add_all(
            (
                UserIdentity(
                    user_id=user.id,
                    provider="email_otp",
                    provider_subject=email,
                    verified_email=email,
                    created_at=now,
                    last_login_at=now,
                ),
                InvitationRedemption(
                    invitation_id=invitation.id,
                    user_id=user.id,
                    redeemed_at=now,
                ),
                QuotaGrant(
                    user_id=user.id,
                    period_type="BETA_LIFETIME",
                    period_key="v0.1-beta",
                    units=credits,
                    reason="INTEGRATION_TEST",
                    idempotency_key="integration-test-grant",
                    created_at=now,
                ),
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
                ),
            )
        )
    return user, raw_token


def _authenticate(client: httpx.AsyncClient, settings, raw_token: str) -> None:
    client.cookies.set(
        settings.cookie_name,
        raw_token,
        domain="kakarot8.com",
        path="/",
    )


def _trip_request(city: str, notes: str = "私人备注 phone=13800000000") -> dict:
    return {
        "from_city": "私人出发地",
        "to_city": city,
        "days": 3,
        "people_count": 2,
        "preferences": ["美食"],
        "avoid": [],
        "notes": notes,
        "source": "must-drop",
        "provider_payload": {"token": "must-drop"},
    }


async def _terminal_trip(
    session_factory,
    settings,
    *,
    user_id,
    key: str,
    city: str,
    status: str,
    created_at: datetime,
    visible_until: datetime,
    result_record_id: int | None = None,
) -> UserTrip:
    raw_request = _trip_request(city)
    request_json = {
        key: value
        for key, value in raw_request.items()
        if key not in {"source", "provider_payload"}
    }
    reserved = await reserve_trip(
        session_factory,
        settings,
        user_id=user_id,
        client_request_id=key,
        request_hash=normalized_request_hash(request_json),
        request_json=request_json,
    )
    trip = await settle_trip(
        session_factory,
        trip_id=reserved.trip.id,
        terminal_status=status,
        result_record_id=result_record_id,
        error_code="RAW_PROVIDER_STACK" if status != "SUCCESS" else None,
        error_message="traceback credential=secret" if status != "SUCCESS" else None,
        error_retryable=True if status != "SUCCESS" else None,
    )
    async with session_factory() as session, session.begin():
        await session.execute(
            update(UserTrip)
            .where(UserTrip.id == trip.id)
            .values(
                created_at=created_at,
                finished_at=created_at + timedelta(minutes=1),
                updated_at=created_at + timedelta(minutes=1),
                visible_until=visible_until,
                telemetry_json={"plan_count": 1, "total_elapsed_ms": 60_000},
            )
        )
    return trip


async def test_history_is_owned_bounded_and_cursor_stable_under_new_inserts(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    now = datetime.now(UTC)
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="history@example.com",
    )
    other, _other_token = await _seed_user(
        session_factory,
        test_settings,
        email="history-other@example.com",
    )
    expected_ids = []
    for index, status in enumerate(("SUCCESS", "FAILED", "SUCCESS", "TIMEOUT")):
        trip = await _terminal_trip(
            session_factory,
            test_settings,
            user_id=user.id,
            key=f"web-history-{index}",
            city=f"城市{index}",
            status=status,
            created_at=now - timedelta(hours=index),
            visible_until=now + timedelta(days=6),
            result_record_id=700 + index if status == "SUCCESS" else None,
        )
        expected_ids.append(trip.public_id)
    await _terminal_trip(
        session_factory,
        test_settings,
        user_id=other.id,
        key="web-history-other",
        city="他人城市",
        status="SUCCESS",
        created_at=now + timedelta(minutes=1),
        visible_until=now + timedelta(days=6),
        result_record_id=799,
    )
    _authenticate(client, test_settings, raw_token)

    first = await client.get("/api/me/trips", params={"limit": 2})
    assert first.status_code == 200
    assert [item["trip_id"] for item in first.json()["items"]] == expected_ids[:2]
    cursor = first.json()["next_cursor"]
    assert cursor

    inserted = await _terminal_trip(
        session_factory,
        test_settings,
        user_id=user.id,
        key="web-history-new",
        city="新插入城市",
        status="SUCCESS",
        created_at=now + timedelta(minutes=2),
        visible_until=now + timedelta(days=6),
        result_record_id=800,
    )
    second = await client.get(
        "/api/me/trips",
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert [item["trip_id"] for item in second.json()["items"]] == expected_ids[2:]
    assert inserted.public_id not in {
        item["trip_id"] for item in first.json()["items"] + second.json()["items"]
    }
    serialized = str(first.json()) + str(second.json())
    assert "他人城市" not in serialized
    assert "provider_payload" not in serialized
    assert "source" not in serialized
    assert "request_id" not in serialized
    assert "traceback" not in serialized
    failed_item = next(item for item in first.json()["items"] if item["status"] == "FAILED")
    assert failed_item["error"] == {
        "code": "GENERATION_FAILED",
        "message": "生成失败，请稍后重试。",
        "retryable": True,
    }
    assert failed_item["retry_input"]["trip_request"]["notes"].startswith("私人备注")
    assert len(hermes.status_calls) == 0
    assert len(hermes.result_calls) == 0

    filtered = await client.get(
        "/api/me/trips",
        params={"status": "FAILED"},
    )
    assert filtered.status_code == 200
    assert [item["status"] for item in filtered.json()["items"]] == ["FAILED"]
    payload_part, signature_part = cursor.split(".", 1)
    tampered_cursor = (
        f"{payload_part}.{'A' if signature_part[0] != 'A' else 'B'}{signature_part[1:]}"
    )
    tampered = await client.get(
        "/api/me/trips",
        params={"cursor": tampered_cursor},
    )
    assert tampered.status_code == 422
    assert tampered.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_expired_history_archives_and_erases_free_text(
    client,
    session_factory,
    test_settings,
) -> None:
    now = datetime.now(UTC)
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="archive@example.com",
    )
    expired = await _terminal_trip(
        session_factory,
        test_settings,
        user_id=user.id,
        key="web-expired-history",
        city="重庆",
        status="SUCCESS",
        created_at=now - timedelta(days=8),
        visible_until=now - timedelta(days=1),
        result_record_id=901,
    )
    _authenticate(client, test_settings, raw_token)
    history = await client.get("/api/me/trips")
    assert history.status_code == 200
    assert history.json()["items"] == []
    async with session_factory() as session:
        row = await session.get(UserTrip, expired.id)
        assert row is not None
        assert row.archived_at is not None
        assert row.user_id == user.id
        assert row.result_record_id == 901
        assert row.request_json["to_city"] == "重庆"
        assert row.request_json["notes"] == ""


async def test_closure_active_conflict_changes_no_state_and_code_can_be_retried(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="active-closure@example.com",
    )
    request_json = {
        key: value
        for key, value in _trip_request("重庆").items()
        if key not in {"source", "provider_payload"}
    }
    active = await reserve_trip(
        session_factory,
        test_settings,
        user_id=user.id,
        client_request_id="web-active-closure",
        request_hash=normalized_request_hash(request_json),
        request_json=request_json,
    )
    _authenticate(client, test_settings, raw_token)
    sent = await client.post("/api/me/closure/send-code", headers=ORIGIN)
    assert sent.status_code == 200
    challenge_id = sent.json()["challenge_id"]
    code = mailer.messages[-1]["code"]
    async with session_factory() as session:
        before = {
            "users": await session.scalar(select(func.count()).select_from(AppUser)),
            "identities": await session.scalar(select(func.count()).select_from(UserIdentity)),
            "sessions": await session.scalar(select(func.count()).select_from(UserSession)),
            "trips": await session.scalar(select(func.count()).select_from(UserTrip)),
            "quota": await session.scalar(select(func.count()).select_from(TripQuotaEntry)),
        }
    blocked = await client.post(
        "/api/me/closure/confirm",
        headers=ORIGIN,
        json={"challenge_id": challenge_id, "code": code},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ACTIVE_TRIP_IN_PROGRESS"
    async with session_factory() as session:
        after = {
            "users": await session.scalar(select(func.count()).select_from(AppUser)),
            "identities": await session.scalar(select(func.count()).select_from(UserIdentity)),
            "sessions": await session.scalar(select(func.count()).select_from(UserSession)),
            "trips": await session.scalar(select(func.count()).select_from(UserTrip)),
            "quota": await session.scalar(select(func.count()).select_from(TripQuotaEntry)),
        }
        challenge = await session.scalar(
            select(EmailOtpChallenge).where(EmailOtpChallenge.public_id == challenge_id)
        )
        assert challenge is not None and challenge.consumed_at is None
        assert challenge.attempt_count == 0
    assert after == before

    await settle_trip(
        session_factory,
        trip_id=active.trip.id,
        terminal_status="FAILED",
        error_code="GENERATION_FAILED",
        error_message="生成失败，请稍后重试。",
        error_retryable=True,
    )
    closed = await client.post(
        "/api/me/closure/confirm",
        headers=ORIGIN,
        json={"challenge_id": challenge_id, "code": code},
    )
    assert closed.status_code == 200


async def test_closure_deletes_identity_and_keeps_only_deidentified_archive(
    app,
    mailer,
    hermes,
    session_factory,
    test_settings,
) -> None:
    now = datetime.now(UTC)
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="closure@example.com",
    )
    success = await _terminal_trip(
        session_factory,
        test_settings,
        user_id=user.id,
        key="web-close-success",
        city="重庆",
        status="SUCCESS",
        created_at=now,
        visible_until=now + timedelta(days=7),
        result_record_id=1001,
    )
    failed = await _terminal_trip(
        session_factory,
        test_settings,
        user_id=user.id,
        key="web-close-failed",
        city="成都",
        status="FAILED",
        created_at=now - timedelta(hours=1),
        visible_until=now + timedelta(days=7),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://kakarot8.com",
    ) as closing_client:
        _authenticate(closing_client, test_settings, raw_token)
        sent = await closing_client.post(
            "/api/me/closure/send-code",
            headers=ORIGIN,
        )
        challenge_id = sent.json()["challenge_id"]
        code = mailer.messages[-1]["code"]
        closed = await closing_client.post(
            "/api/me/closure/confirm",
            headers=ORIGIN,
            json={"challenge_id": challenge_id, "code": code},
        )
        assert closed.status_code == 200
        assert test_settings.cookie_name not in closing_client.cookies

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AppUser)) == 0
        assert await session.scalar(select(func.count()).select_from(UserIdentity)) == 0
        assert await session.scalar(select(func.count()).select_from(UserSession)) == 0
        assert await session.scalar(select(func.count()).select_from(QuotaGrant)) == 0
        assert await session.scalar(select(func.count()).select_from(InvitationRedemption)) == 0
        assert await session.scalar(select(func.count()).select_from(EmailOtpChallenge)) == 0
        assert await session.scalar(select(func.count()).select_from(TripQuotaEntry)) == 0
        rows = list(
            (await session.scalars(select(UserTrip).order_by(UserTrip.created_at.desc()))).all()
        )
        assert {row.id for row in rows} == {success.id, failed.id}
        for row in rows:
            assert row.user_id is None
            assert row.quota_entry_id is None
            assert row.identity_erased_at is not None
            assert row.archived_at is not None
            assert row.client_request_id == f"erased:{row.public_id}"
            assert row.request_json["notes"] == ""
            assert "from_city" not in row.request_json
            assert "13800000000" not in str(row.request_json)
            assert row.telemetry_json == {"plan_count": 1, "total_elapsed_ms": 60_000}
        success_row = next(row for row in rows if row.id == success.id)
        assert success_row.result_record_id == 1001
    assert len(hermes.create_calls) == 0
    assert len(hermes.result_calls) == 0

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://kakarot8.com",
    ) as replay_client:
        _authenticate(replay_client, test_settings, raw_token)
        replay = await replay_client.get("/api/me")
        assert replay.status_code == 401


async def test_auth_challenge_cannot_confirm_closure_and_no_delete_route_exists(
    client,
    mailer,
    session_factory,
    test_settings,
) -> None:
    _user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="wrong-purpose@example.com",
    )
    _authenticate(client, test_settings, raw_token)
    auth_code = await client.post(
        "/api/auth/email/send-code",
        headers=ORIGIN,
        json={"mode": "login", "email": "wrong-purpose@example.com"},
    )
    rejected = await client.post(
        "/api/me/closure/confirm",
        headers=ORIGIN,
        json={
            "challenge_id": auth_code.json()["challenge_id"],
            "code": mailer.messages[-1]["code"],
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "OTP_INVALID"
    assert (await client.get("/api/me")).status_code == 200
    assert (await client.delete("/api/me/trips/anything", headers=ORIGIN)).status_code == 404
