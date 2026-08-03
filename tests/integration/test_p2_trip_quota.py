from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from src.db.models import (
    AppUser,
    QuotaGrant,
    TripQuotaEntry,
    UserIdentity,
    UserSession,
    UserTrip,
)
from src.integrations.hermes import HermesBusinessError, HermesIntegrationError
from src.quota.service import (
    ActiveTripConflict,
    reserve_trip,
    settle_trip,
)
from src.security.secrets import hash_secret, new_opaque_id, new_session_token
from src.trips.reconciliation import reconcile_bounded
from src.trips.schemas import normalized_request_hash
from tests.factories import unique_display_name_fields

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "https://kakarot8.com"}


def _sse_payloads(stream_text: str, event_name: str) -> list[dict]:
    payloads = []
    for block in stream_text.split("\n\n"):
        lines = block.splitlines()
        if f"event: {event_name}" not in lines:
            continue
        data_line = next(line for line in lines if line.startswith("data: "))
        payloads.append(json.loads(data_line.removeprefix("data: ")))
    return payloads


def _request(
    request_id: str,
    *,
    city: str = "重庆",
    extra: bool = False,
) -> dict:
    payload = {
        "trip_request": {
            "to_city": city,
            "days": 3,
            "people_count": 2,
            "preferences": ["美食", "citywalk"],
            "avoid": [],
            "notes": "",
        },
        "request_id": request_id,
    }
    if extra:
        payload.update(
            {
                "source": "browser-untrusted",
                "conversation_id": "browser-conversation",
                "email": "leak@example.com",
            }
        )
        payload["trip_request"].update(
            {
                "source": "nested-source",
                "provider_payload": {"token": "secret"},
                "identity": "browser-user",
            }
        )
    return payload


async def _seed_user(
    session_factory,
    settings,
    *,
    email: str,
    credits: int = 3,
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
            **unique_display_name_fields(),
        )
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider="email_otp",
                provider_subject=email,
                verified_email=email,
                created_at=now,
                last_login_at=now,
            )
        )
        if credits:
            session.add(
                QuotaGrant(
                    user_id=user.id,
                    period_type="BETA_LIFETIME",
                    period_key="v0.1-beta",
                    units=credits,
                    reason="INTEGRATION_TEST",
                    idempotency_key="integration-test-grant",
                    created_at=now,
                )
            )
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


def _authenticate(client: httpx.AsyncClient, settings, raw_token: str) -> None:
    client.cookies.set(settings.cookie_name, raw_token)


async def test_concurrent_last_unit_and_one_active_constraint(
    session_factory,
    test_settings,
) -> None:
    user, _token = await _seed_user(
        session_factory,
        test_settings,
        email="last-unit@example.com",
        credits=1,
    )
    request_a = _request("web-last-unit-a")["trip_request"]
    request_b = _request("web-last-unit-b", city="成都")["trip_request"]

    async def reserve(request_id: str, trip_request: dict):
        try:
            return await reserve_trip(
                session_factory,
                test_settings,
                user_id=user.id,
                client_request_id=request_id,
                request_hash=normalized_request_hash(trip_request),
                request_json=trip_request,
            )
        except Exception as exc:
            return exc

    results = await asyncio.gather(
        reserve("web-last-unit-a", request_a),
        reserve("web-last-unit-b", request_b),
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ActiveTripConflict) for result in results) == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserTrip)) == 1
        assert await session.scalar(select(func.count()).select_from(TripQuotaEntry)) == 1
        quota = await session.scalar(select(TripQuotaEntry))
        assert quota is not None and quota.status == "RESERVED"


async def test_concurrent_different_requests_return_one_active_conflict(
    app,
    hermes,
    session_factory,
    test_settings,
) -> None:
    _user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="active@example.com",
    )
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
        _authenticate(client_a, test_settings, raw_token)
        _authenticate(client_b, test_settings, raw_token)
        responses = await asyncio.gather(
            client_a.post(
                "/api/trip/async",
                headers=ORIGIN,
                json=_request("web-active-first"),
            ),
            client_b.post(
                "/api/trip/async",
                headers=ORIGIN,
                json=_request("web-active-second", city="成都"),
            ),
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "ACTIVE_TRIP_EXISTS"
    assert conflict.json()["active_trip"]["status"] in {"SUBMITTING", "PENDING"}
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserTrip)) == 1
        assert await session.scalar(select(func.count()).select_from(TripQuotaEntry)) == 1
    assert len(hermes.jobs_by_request) == 1


async def test_duplicate_idempotency_conflict_and_browser_fields_are_stripped(
    app,
    hermes,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="idempotent@example.com",
    )
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
        _authenticate(client_a, test_settings, raw_token)
        _authenticate(client_b, test_settings, raw_token)
        first, duplicate = await asyncio.gather(
            client_a.post(
                "/api/trip/async",
                headers=ORIGIN,
                json=_request("web-same-request", extra=True),
            ),
            client_b.post(
                "/api/trip/async",
                headers=ORIGIN,
                json=_request("web-same-request", extra=True),
            ),
        )
        assert first.status_code == duplicate.status_code == 200
        assert first.json()["trip_id"] == duplicate.json()["trip_id"]
        assert first.json()["job_id"] == duplicate.json()["job_id"]
        conflict = await client_a.post(
            "/api/trip/async",
            headers=ORIGIN,
            json=_request("web-same-request", city="成都"),
        )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REQUEST_ID_CONFLICT"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserTrip)) == 1
        assert await session.scalar(select(func.count()).select_from(TripQuotaEntry)) == 1
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        assert trip is not None
        serialized = str(trip.request_json)
        assert "source" not in serialized
        assert "conversation" not in serialized
        assert "provider_payload" not in serialized
        assert "leak@example.com" not in serialized
    upstream = hermes.create_calls[0]
    assert upstream["source"] == "travel-web-api"
    assert upstream["conversation_id"] == first.json()["trip_id"]
    assert "identity" not in str(upstream["trip_request"])
    assert len(hermes.jobs_by_request) == 1


async def test_terminal_settlement_is_exactly_once_and_race_is_legal(
    session_factory,
    test_settings,
) -> None:
    user, _raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="settlement@example.com",
        credits=3,
    )

    async def reserve(key: str):
        request_json = _request(key)["trip_request"]
        return await reserve_trip(
            session_factory,
            test_settings,
            user_id=user.id,
            client_request_id=key,
            request_hash=normalized_request_hash(request_json),
            request_json=request_json,
        )

    success = await reserve("web-settle-success")
    await settle_trip(
        session_factory,
        trip_id=success.trip.id,
        terminal_status="SUCCESS",
        result_record_id=101,
    )
    repeated_success = await settle_trip(
        session_factory,
        trip_id=success.trip.id,
        terminal_status="SUCCESS",
        result_record_id=101,
    )
    assert repeated_success.status == "SUCCESS"

    failed = await reserve("web-settle-failed")
    for status in ("FAILED", "FAILED"):
        repeated_failed = await settle_trip(
            session_factory,
            trip_id=failed.trip.id,
            terminal_status=status,
            error_code="GENERATION_FAILED",
            error_message="生成失败，请稍后重试。",
            error_retryable=True,
        )
    assert repeated_failed.status == "FAILED"

    raced = await reserve("web-settle-race")
    race_results = await asyncio.gather(
        settle_trip(
            session_factory,
            trip_id=raced.trip.id,
            terminal_status="SUCCESS",
            result_record_id=303,
        ),
        settle_trip(
            session_factory,
            trip_id=raced.trip.id,
            terminal_status="TIMEOUT",
            error_code="GENERATION_TIMEOUT",
            error_message="生成超时，请稍后重试。",
            error_retryable=True,
        ),
    )
    assert race_results[0].status == race_results[1].status
    assert race_results[0].status in {"SUCCESS", "TIMEOUT"}

    async with session_factory() as session:
        quota_rows = list((await session.scalars(select(TripQuotaEntry))).all())
        assert len(quota_rows) == 3
        assert [row.status for row in quota_rows].count("RESERVED") == 0
        final = await session.get(UserTrip, raced.trip.id)
        assert final is not None
        if final.status == "SUCCESS":
            assert final.result_record_id == 303
        else:
            assert final.result_record_id is None


@pytest.mark.parametrize("terminal_status", ["FAILED", "TIMEOUT", "REJECTED"])
async def test_each_failure_terminal_releases_once(
    session_factory,
    test_settings,
    terminal_status,
) -> None:
    user, _token = await _seed_user(
        session_factory,
        test_settings,
        email=f"{terminal_status.lower()}@example.com",
        credits=1,
    )
    request_json = _request(f"web-{terminal_status.lower()}")["trip_request"]
    reserved = await reserve_trip(
        session_factory,
        test_settings,
        user_id=user.id,
        client_request_id=f"web-{terminal_status.lower()}",
        request_hash=normalized_request_hash(request_json),
        request_json=request_json,
    )
    await settle_trip(
        session_factory,
        trip_id=reserved.trip.id,
        terminal_status=terminal_status,
        error_code="GENERATION_FAILED",
        error_message="生成失败，请稍后重试。",
        error_retryable=True,
    )
    await settle_trip(
        session_factory,
        trip_id=reserved.trip.id,
        terminal_status=terminal_status,
        error_code="GENERATION_FAILED",
        error_message="生成失败，请稍后重试。",
        error_retryable=True,
    )
    async with session_factory() as session:
        quota = await session.scalar(select(TripQuotaEntry))
        assert quota is not None and quota.status == "RELEASED"
        assert quota.settle_reason == terminal_status


async def test_owned_job_sse_result_artifact_places_and_cross_user_404(
    app,
    hermes,
    session_factory,
    test_settings,
) -> None:
    owner, owner_token = await _seed_user(
        session_factory,
        test_settings,
        email="owner@example.com",
    )
    _other, other_token = await _seed_user(
        session_factory,
        test_settings,
        email="other@example.com",
    )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://kakarot8.com",
        ) as owner_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://kakarot8.com",
        ) as other_client,
    ):
        _authenticate(owner_client, test_settings, owner_token)
        _authenticate(other_client, test_settings, other_token)
        created = await owner_client.post(
            "/api/trip/async",
            headers=ORIGIN,
            json=_request("web-owned-proxy"),
        )
        job_id = created.json()["job_id"]
        hermes.job_payloads[job_id] = {
            "ok": True,
            "job_id": job_id,
            "status": "SUCCESS",
            "result_record_id": 501,
            "plan_count": 1,
            "provider_payload": {"secret": True},
        }
        polled = await owner_client.get(f"/api/trip/jobs/{job_id}")
        assert polled.status_code == 200
        assert polled.json()["status"] == "SUCCESS"
        assert "provider_payload" not in polled.text

        hermes.stream_events[job_id] = [
            (
                "complete",
                {
                    "status": "SUCCESS",
                    "job_id": job_id,
                    "result_record_id": 501,
                },
            )
        ]
        streamed = await owner_client.get(f"/api/trip/jobs/{job_id}/stream")
        assert streamed.status_code == 200
        assert "event: complete" in streamed.text
        assert "event: interrupted" not in streamed.text

        result = await owner_client.get(
            "/api/trip/results/501",
            params={"job_id": job_id},
        )
        assert result.status_code == 200
        result_payload = result.json()
        assert result_payload["result_id"] == 501
        assert result_payload["request"]["from_city"] == "成都"
        assert result_payload["plans"][0]["transport"]["source"] == "realtime"
        assert result_payload["plans"][0]["transport"]["modes"][0]["mode"] == "train"
        assert result_payload["plans"][0]["transport"]["modes"][0]["options"][0]["no"] == "G1"
        assert "provider_payload" not in result.text

        pdf_created = await owner_client.post(
            "/api/trip/results/501/artifacts/pdf",
            headers=ORIGIN,
        )
        pdf_status = await owner_client.get("/api/trip/results/501/artifacts/pdf")
        pdf_download = await owner_client.get("/api/trip/results/501/artifacts/pdf/download")
        assert pdf_created.status_code == pdf_status.status_code == 200
        assert pdf_created.json()["download_url"].startswith("/api/")
        assert pdf_created.json()["filename"] == "trip-501.pdf"
        assert "internal" not in pdf_created.text
        assert "storage_path" not in pdf_created.text
        assert pdf_download.status_code == 200
        assert pdf_download.content == b"%PDF-safe"
        assert pdf_download.headers["content-type"] == "application/pdf"
        assert pdf_download.headers["content-disposition"] == (
            'attachment; filename="trip-501.pdf"'
        )

        share_created = await owner_client.post(
            "/api/trip/results/501/artifacts/share_image",
            headers=ORIGIN,
        )
        share_status = await owner_client.get("/api/trip/results/501/artifacts/share_image")
        share_download = await owner_client.get(
            "/api/trip/results/501/artifacts/share_image/download"
        )
        assert share_created.status_code == share_status.status_code == 200
        assert share_created.json()["artifact_type"] == "share_image"
        assert share_created.json()["download_url"] == (
            "/api/trip/results/501/artifacts/share_image/download"
        )
        assert share_created.json()["filename"] == "trip-501.png"
        assert share_status.json()["status"] == "ready"
        assert share_status.json()["filename"] == "trip-501.png"
        assert "internal" not in share_created.text
        assert "storage_path" not in share_created.text
        assert share_download.status_code == 200
        assert share_download.content == b"\x89PNG-safe"
        assert share_download.headers["content-type"] == "image/png"
        assert share_download.headers["content-disposition"] == (
            'attachment; filename="trip-501.png"'
        )

        places = await owner_client.get(
            "/api/trip/places",
            params={"city": "重庆", "limit": 12},
        )
        detail = await owner_client.get("/api/trip/places/1")
        assert places.status_code == detail.status_code == 200

        calls_before = (
            len(hermes.status_calls),
            len(hermes.stream_calls),
            len(hermes.result_calls),
            len(hermes.artifact_calls),
            len(hermes.artifact_download_calls),
        )
        denials = [
            await other_client.get(f"/api/trip/jobs/{job_id}"),
            await other_client.get(f"/api/trip/jobs/{job_id}/stream"),
            await other_client.get(
                "/api/trip/results/501",
                params={"job_id": job_id},
            ),
            await other_client.get("/api/trip/results/501/artifacts/pdf"),
            await other_client.post(
                "/api/trip/results/501/artifacts/pdf",
                headers=ORIGIN,
            ),
            await other_client.get("/api/trip/results/501/artifacts/pdf/download"),
            await other_client.get("/api/trip/results/501/artifacts/share_image"),
            await other_client.post(
                "/api/trip/results/501/artifacts/share_image",
                headers=ORIGIN,
            ),
            await other_client.get("/api/trip/results/501/artifacts/share_image/download"),
            await owner_client.get("/api/trip/results/999999/artifacts/share_image"),
        ]
        assert all(response.status_code == 404 for response in denials)
        assert all(response.json()["error"]["code"] == "TRIP_NOT_FOUND" for response in denials)
        assert calls_before == (
            len(hermes.status_calls),
            len(hermes.stream_calls),
            len(hermes.result_calls),
            len(hermes.artifact_calls),
            len(hermes.artifact_download_calls),
        )

    async with session_factory() as session:
        owner_trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == owner.id))
        assert owner_trip is not None and owner_trip.status == "SUCCESS"
        assert owner_trip.telemetry_json["plan_count"] == 1
        assert owner_trip.telemetry_json["result_schema_version"] == "1.5"
        quota = await session.get(TripQuotaEntry, owner_trip.quota_entry_id)
        assert quota is not None and quota.status == "CONSUMED"


async def test_uncertain_submit_is_reconciled_without_releasing(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="uncertain@example.com",
        credits=1,
    )
    _authenticate(client, test_settings, raw_token)
    hermes.create_error = HermesIntegrationError(
        "UNAVAILABLE",
        retryable=True,
        acceptance_uncertain=True,
    )
    response = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-uncertain"),
    )
    assert response.status_code == 503
    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "SUBMITTING"
        assert quota is not None and quota.status == "RESERVED"

    hermes.create_error = None
    result = await reconcile_bounded(
        session_factory,
        test_settings,
        hermes,
        correlation_id="reconcile-test",
    )
    assert result.claimed == result.recovered == 1
    assert result.unresolved == 0
    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "PENDING"
        assert trip.hermes_job_id is not None
        assert quota is not None and quota.status == "RESERVED"


async def test_known_preacceptance_failure_releases_and_public_error_is_redacted(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="preaccept@example.com",
        credits=1,
    )
    _authenticate(client, test_settings, raw_token)
    hermes.create_error = HermesIntegrationError(
        "UNAVAILABLE",
        retryable=True,
        acceptance_uncertain=False,
    )
    response = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-preaccept"),
    )
    assert response.status_code == 503
    assert "credential" not in response.text.casefold()
    assert "upstream" not in response.text.casefold()
    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "FAILED"
        assert quota is not None and quota.status == "RELEASED"


async def test_me_restores_active_trip_and_real_quota(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    _user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="me@example.com",
    )
    _authenticate(client, test_settings, raw_token)
    created = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-me-active"),
    )
    assert created.status_code == 200
    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["active_trip"] == {
        "trip_id": created.json()["trip_id"],
        "job_id": created.json()["job_id"],
        "status": "PENDING",
    }
    assert me.json()["quota"] == {
        "policy": "beta_lifetime",
        "limit": 3,
        "reserved": 1,
        "consumed": 0,
        "remaining": 2,
        "resets_at": None,
    }
    assert len(hermes.jobs_by_request) == 1


async def test_quota_exhaustion_and_city_rejection_create_no_charge(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    _empty_user, empty_token = await _seed_user(
        session_factory,
        test_settings,
        email="empty@example.com",
        credits=0,
    )
    _authenticate(client, test_settings, empty_token)
    exhausted = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-no-quota"),
    )
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "QUOTA_EXHAUSTED"
    assert len(hermes.create_calls) == 0
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserTrip)) == 0
        assert await session.scalar(select(func.count()).select_from(TripQuotaEntry)) == 0


async def test_city_unsupported_releases_reserved_unit(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="city-reject@example.com",
        credits=1,
    )
    _authenticate(client, test_settings, raw_token)
    hermes.create_error = HermesBusinessError("CITY_NOT_SUPPORTED")
    response = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-city-reject"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CITY_NOT_SUPPORTED"
    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "REJECTED"
        assert quota is not None and quota.status == "RELEASED"


async def test_complete_sse_event_consumes_without_interrupted(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="sse-success@example.com",
        credits=1,
    )
    _authenticate(client, test_settings, raw_token)
    created = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-sse-success"),
    )
    job_id = created.json()["job_id"]
    hermes.stream_events[job_id] = [
        (
            "complete",
            {
                "status": "SUCCESS",
                "job_id": job_id,
                "result_record_id": 901,
            },
        )
    ]

    streamed = await client.get(f"/api/trip/jobs/{job_id}/stream")

    assert streamed.status_code == 200
    assert streamed.text.count("event: complete") == 1
    assert "event: interrupted" not in streamed.text
    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "SUCCESS"
        assert trip.result_record_id == 901
        assert quota is not None and quota.status == "CONSUMED"
        assert quota.settle_reason == "SUCCESS"


async def test_failed_sse_event_is_allowlisted_and_releases_once(
    client,
    hermes,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="sse-failure@example.com",
        credits=1,
    )
    _authenticate(client, test_settings, raw_token)
    created = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request("web-sse-failure"),
    )
    job_id = created.json()["job_id"]
    hermes.stream_events[job_id] = [
        (
            "failed",
            {
                "status": "FAILED",
                "job_id": job_id,
                "error": {
                    "code": "RAW_PROVIDER_STACK",
                    "message": "credential=super-secret traceback",
                },
            },
        )
    ]
    first = await client.get(f"/api/trip/jobs/{job_id}/stream")
    second = await client.get(f"/api/trip/jobs/{job_id}/stream")
    assert first.status_code == second.status_code == 200
    assert "GENERATION_FAILED" in first.text
    assert "super-secret" not in first.text
    assert "RAW_PROVIDER_STACK" not in first.text
    assert "event: interrupted" not in first.text
    assert "event: interrupted" not in second.text
    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "FAILED"
        assert quota is not None and quota.status == "RELEASED"


@pytest.mark.parametrize(
    ("terminal_status", "quota_status", "result_record_id"),
    [
        ("SUCCESS", "CONSUMED", 801),
        ("FAILED", "RELEASED", None),
    ],
)
@pytest.mark.parametrize("stream_end", ["clean_eof", "transport_exception"])
async def test_sse_non_terminal_end_stays_active_until_polling(
    client,
    hermes,
    session_factory,
    test_settings,
    terminal_status,
    quota_status,
    result_record_id,
    stream_end,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email=f"sse-{stream_end}-{terminal_status.lower()}@example.com",
        credits=1,
    )
    _authenticate(client, test_settings, raw_token)
    created = await client.post(
        "/api/trip/async",
        headers=ORIGIN,
        json=_request(f"web-sse-{stream_end}-{terminal_status.lower()}"),
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    hermes.stream_events[job_id] = [
        (
            "progress",
            {
                "status": "RUNNING",
                "job_id": job_id,
                "current_stage": "WRITER",
            },
        )
    ]
    if stream_end == "transport_exception":
        hermes.stream_error = HermesIntegrationError("UNAVAILABLE", retryable=True)
    interrupted = await client.get(f"/api/trip/jobs/{job_id}/stream")
    assert interrupted.status_code == 200
    assert "event: progress" in interrupted.text
    assert interrupted.text.count("event: interrupted") == 1
    assert _sse_payloads(interrupted.text, "interrupted") == [
        {
            "ok": False,
            "job_id": job_id,
            "stream_state": "INTERRUPTED",
            "job_status_known": False,
            "fallback": "POLLING",
            "error": {
                "code": "GENERATION_STREAM_INTERRUPTED",
                "message": "状态流暂时中断，请改用轮询确认任务状态。",
                "retryable": True,
            },
        }
    ]
    assert "status" not in _sse_payloads(interrupted.text, "interrupted")[0]
    assert "event: failed" not in interrupted.text
    assert '"status":"FAILED"' not in interrupted.text
    assert '"status":"TIMEOUT"' not in interrupted.text
    assert '"status":"REJECTED"' not in interrupted.text

    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == "RUNNING"
        assert quota is not None and quota.status == "RESERVED"
        assert quota.settle_reason is None
        assert quota.settled_at is None

    me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["active_trip"] == {
        "trip_id": created.json()["trip_id"],
        "job_id": job_id,
        "status": "RUNNING",
    }
    assert me.json()["quota"]["reserved"] == 1
    assert me.json()["quota"]["consumed"] == 0

    hermes.stream_error = None
    hermes.job_payloads[job_id] = {
        "ok": True,
        "job_id": job_id,
        "status": terminal_status,
        "result_record_id": result_record_id,
        "error_code": "RAW_PROVIDER_STACK" if terminal_status == "FAILED" else None,
    }
    first_poll = await client.get(f"/api/trip/jobs/{job_id}")
    assert first_poll.status_code == 200
    assert first_poll.json()["status"] == terminal_status
    async with session_factory() as session:
        quota_after_first_poll = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert quota_after_first_poll is not None
        first_settled_at = quota_after_first_poll.settled_at
        assert first_settled_at is not None

    second_poll = await client.get(f"/api/trip/jobs/{job_id}")
    assert second_poll.status_code == 200
    assert second_poll.json()["status"] == terminal_status

    async with session_factory() as session:
        trip = await session.scalar(select(UserTrip).where(UserTrip.user_id == user.id))
        quota = await session.scalar(
            select(TripQuotaEntry).where(TripQuotaEntry.user_id == user.id)
        )
        assert trip is not None and trip.status == terminal_status
        assert quota is not None and quota.status == quota_status
        assert quota.settled_at == first_settled_at
        assert await session.scalar(select(func.count()).select_from(TripQuotaEntry)) == 1

    settled_me = await client.get("/api/me")
    assert settled_me.status_code == 200
    assert settled_me.json()["active_trip"] is None
    assert settled_me.json()["quota"]["reserved"] == 0
    assert settled_me.json()["quota"]["consumed"] == (1 if terminal_status == "SUCCESS" else 0)
