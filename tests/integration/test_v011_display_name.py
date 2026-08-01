from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from src.db.models import AppUser, DisplayNameQuarantine, UserIdentity, UserSession
from src.profile import service as profile_service
from src.profile.display_names import NormalizedDisplayName, former_name_digest
from src.profile.service import (
    DisplayNameMutationError,
    create_user_with_default_display_name,
    rename_display_name,
)
from src.security.secrets import hash_secret, new_opaque_id, new_session_token
from tests.factories import unique_display_name_fields

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "https://kakarot8.com"}


async def _seed_user(
    session_factory,
    settings,
    *,
    email: str,
    display_name: str | None = None,
    display_name_normalized: str | None = None,
) -> tuple[AppUser, str]:
    now = datetime.now(UTC)
    raw_token = new_session_token()
    fields = unique_display_name_fields()
    if display_name is not None and display_name_normalized is not None:
        fields = {
            "display_name": display_name,
            "display_name_normalized": display_name_normalized,
            "display_name_changed_at": None,
        }
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
            created_at=now,
            updated_at=now,
            **fields,
        )
        session.add(user)
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


async def test_default_allocation_skips_unexpired_quarantine(
    session_factory,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    quarantined_name = "user_0000000001"
    available_name = "user_0000000002"
    candidates = iter(
        (
            NormalizedDisplayName(quarantined_name, quarantined_name),
            NormalizedDisplayName(available_name, available_name),
        )
    )
    monkeypatch.setattr(
        profile_service,
        "generate_default_display_name",
        lambda: next(candidates),
    )
    digest = former_name_digest(
        quarantined_name,
        pepper=test_settings.secret_hash_pepper.get_secret_value(),
    )
    async with session_factory() as session, session.begin():
        session.add(
            DisplayNameQuarantine(
                name_digest=digest,
                expires_at=now + timedelta(days=1),
                created_at=now,
            )
        )

    async with session_factory() as session, session.begin():
        user = await create_user_with_default_display_name(
            session,
            test_settings,
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
            created_at=now,
            updated_at=now,
        )

    assert user.display_name == available_name
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).where(AppUser.display_name_normalized == quarantined_name)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).where(AppUser.display_name_normalized == available_name)
            )
            == 1
        )


async def test_profile_contract_first_rename_exact_replay_and_cooldown(
    client,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="profile@example.com",
    )
    _authenticate(client, test_settings, raw_token)

    before = await client.get("/api/me")
    assert before.status_code == 200
    assert before.json()["user"]["display_name"] == user.display_name
    assert before.json()["user"]["display_name_change_available_at"] is None

    cross_user = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": "CrossUserName", "user_id": "usr_other"},
    )
    assert cross_user.status_code == 422
    assert cross_user.json()["error"]["code"] == "VALIDATION_ERROR"

    changed = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": " 山城Traveler_7 "},
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["user_id"] == user.public_id
    assert changed.json()["user"]["display_name"] == "山城Traveler_7"
    available_at = changed.json()["user"]["display_name_change_available_at"]
    assert available_at is not None

    replay = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": "山城Traveler_7"},
    )
    assert replay.status_code == 200
    assert replay.json()["user"]["display_name_change_available_at"] == available_at

    cooldown = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": "AnotherName"},
    )
    assert cooldown.status_code == 429
    assert cooldown.json()["error"]["code"] == "DISPLAY_NAME_CHANGE_COOLDOWN"

    reserved = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": "Admin_helper"},
    )
    assert reserved.status_code == 422
    assert reserved.json()["error"]["code"] == "DISPLAY_NAME_RESERVED"


async def test_case_only_rename_counts_without_quarantining_owned_key(
    client,
    session_factory,
    test_settings,
) -> None:
    user, raw_token = await _seed_user(
        session_factory,
        test_settings,
        email="case@example.com",
        display_name="Traveler",
        display_name_normalized="traveler",
    )
    _authenticate(client, test_settings, raw_token)
    response = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": "TRAVELER"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["display_name"] == "TRAVELER"
    async with session_factory() as session:
        refreshed = await session.get(AppUser, user.id)
        assert refreshed is not None and refreshed.display_name_changed_at is not None
        assert await session.scalar(select(func.count()).select_from(DisplayNameQuarantine)) == 0


async def test_concurrent_nfkc_claims_have_exactly_one_owner(
    session_factory,
    test_settings,
) -> None:
    first, _ = await _seed_user(
        session_factory,
        test_settings,
        email="first@example.com",
    )
    second, _ = await _seed_user(
        session_factory,
        test_settings,
        email="second@example.com",
    )
    results = await asyncio.gather(
        rename_display_name(
            session_factory,
            test_settings,
            user_id=first.id,
            requested_name="Ｆｏｏ",
        ),
        rename_display_name(
            session_factory,
            test_settings,
            user_id=second.id,
            requested_name="foo",
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    loser = next(result for result in results if isinstance(result, Exception))
    assert isinstance(loser, DisplayNameMutationError)
    assert loser.code == "DISPLAY_NAME_UNAVAILABLE"
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).where(AppUser.display_name_normalized == "foo")
            )
            == 1
        )


async def test_former_name_is_quarantined_then_claimable_after_expiry(
    session_factory,
    test_settings,
) -> None:
    first, _ = await _seed_user(
        session_factory,
        test_settings,
        email="former@example.com",
        display_name="FormerName",
        display_name_normalized="formername",
    )
    second, _ = await _seed_user(
        session_factory,
        test_settings,
        email="claimer@example.com",
        display_name="SecondName",
        display_name_normalized="secondname",
    )
    await rename_display_name(
        session_factory,
        test_settings,
        user_id=first.id,
        requested_name="NewName",
    )
    with pytest.raises(DisplayNameMutationError, match="DISPLAY_NAME_UNAVAILABLE"):
        await rename_display_name(
            session_factory,
            test_settings,
            user_id=second.id,
            requested_name="FormerName",
        )

    digest = former_name_digest(
        "formername",
        pepper=test_settings.secret_hash_pepper.get_secret_value(),
    )
    async with session_factory() as session, session.begin():
        row = await session.get(DisplayNameQuarantine, digest)
        assert row is not None
        assert timedelta(days=14, hours=23) < row.expires_at - row.created_at <= timedelta(days=15)
        await session.execute(
            update(DisplayNameQuarantine)
            .where(DisplayNameQuarantine.name_digest == digest)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    claimed = await rename_display_name(
        session_factory,
        test_settings,
        user_id=second.id,
        requested_name="FormerName",
    )
    assert claimed.display_name == "FormerName"


async def test_profile_mutation_requires_session_and_same_origin(client) -> None:
    unauthenticated = await client.patch(
        "/api/me/profile",
        headers=ORIGIN,
        json={"display_name": "SafeName"},
    )
    assert unauthenticated.status_code == 401
    missing_origin = await client.patch(
        "/api/me/profile",
        json={"display_name": "SafeName"},
    )
    assert missing_origin.status_code == 403
