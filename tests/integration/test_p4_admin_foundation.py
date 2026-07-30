from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from src.admin.audit import append_admin_audit
from src.admin.bootstrap import OwnerBootstrapRejected, bootstrap_configured_owner
from src.admin.idempotency import (
    AdminIdempotencyConflict,
    claim_admin_idempotency,
    complete_admin_idempotency,
)
from src.db.models import (
    AdminAuditLog,
    AdminIdempotency,
    AppUser,
    Invitation,
    QuotaAdjustment,
    UserIdentity,
    UserSession,
)
from src.security.secrets import hash_secret, new_opaque_id


async def _user(session_factory, *, role: str = "ADMIN") -> AppUser:
    async with session_factory() as session, session.begin():
        user = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role=role,
        )
        session.add(user)
        await session.flush()
        return user


@pytest.mark.asyncio
async def test_admin_idempotency_replays_and_conflicts(session_factory, test_settings):
    actor = await _user(session_factory)
    key = uuid.uuid4()
    async with session_factory() as session, session.begin():
        claim = await claim_admin_idempotency(
            session,
            test_settings,
            actor_user_id=actor.id,
            idempotency_key=key,
            action="TEST_ACTION",
            payload={"value": 1},
        )
        assert claim.created is True
        complete_admin_idempotency(
            claim,
            http_status=200,
            response_json={"ok": True, "value": 1},
        )

    async with session_factory() as session, session.begin():
        replay = await claim_admin_idempotency(
            session,
            test_settings,
            actor_user_id=actor.id,
            idempotency_key=key,
            action="TEST_ACTION",
            payload={"value": 1},
        )
        assert replay.created is False
        assert replay.replay_response == (200, {"ok": True, "value": 1})

    async with session_factory() as session, session.begin():
        with pytest.raises(AdminIdempotencyConflict):
            await claim_admin_idempotency(
                session,
                test_settings,
                actor_user_id=actor.id,
                idempotency_key=key,
                action="TEST_ACTION",
                payload={"value": 2},
            )


@pytest.mark.asyncio
async def test_concurrent_admin_idempotency_commits_one_winner(session_factory, test_settings):
    actor = await _user(session_factory)
    key = uuid.uuid4()
    first_claimed = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first():
        async with session_factory() as session, session.begin():
            claim = await claim_admin_idempotency(
                session,
                test_settings,
                actor_user_id=actor.id,
                idempotency_key=key,
                action="CONCURRENT",
                payload={"value": 1},
            )
            complete_admin_idempotency(
                claim,
                http_status=201,
                response_json={"ok": True},
            )
            first_claimed.set()
            await second_started.wait()
            await release_first.wait()
            return claim.created

    async def second():
        await first_claimed.wait()
        second_started.set()
        async with session_factory() as session, session.begin():
            claim = await claim_admin_idempotency(
                session,
                test_settings,
                actor_user_id=actor.id,
                idempotency_key=key,
                action="CONCURRENT",
                payload={"value": 1},
            )
            return claim.created, claim.replay_response

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await second_started.wait()
    await asyncio.sleep(0.05)
    release_first.set()
    assert await first_task is True
    assert await second_task == (False, (201, {"ok": True}))

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(AdminIdempotency).where(AdminIdempotency.idempotency_key == key)
            )
        ).scalars()
        assert len(list(rows)) == 1


@pytest.mark.asyncio
async def test_audit_and_adjustment_tables_are_database_append_only(
    session_factory,
    test_settings,
):
    actor = await _user(session_factory)
    key = uuid.uuid4()
    async with session_factory() as session, session.begin():
        claim = await claim_admin_idempotency(
            session,
            test_settings,
            actor_user_id=actor.id,
            idempotency_key=key,
            action="QUOTA_ADJUST",
            payload={"delta": 1},
        )
        adjustment = QuotaAdjustment(
            public_id=new_opaque_id("adj_"),
            target_user_id=actor.id,
            actor_user_id=actor.id,
            delta=1,
            balance_before=0,
            balance_after=1,
            reason="TEST",
            idempotency_id=claim.record.id,
        )
        session.add(adjustment)
        await append_admin_audit(
            session,
            test_settings,
            actor_user_id=actor.id,
            actor_identity="OWNER",
            action="QUOTA_ADJUST",
            target_type="USER",
            target_id=actor.public_id,
            result="SUCCESS",
            request_id="request-test",
            source_ip="203.0.113.9",
            idempotency_key=key,
            before={"email": "secret@example.com", "balance": 0},
            after={"notes": "draft", "balance": 1},
        )
        complete_admin_idempotency(
            claim,
            http_status=201,
            response_json={"ok": True, "adjustment_id": adjustment.public_id},
        )

    async with session_factory() as session:
        audit = await session.scalar(select(AdminAuditLog))
        assert audit is not None
        assert audit.before_json == {"email": "[REDACTED]", "balance": 0}
        assert audit.after_json == {"notes": "[REDACTED]", "balance": 1}
        assert len(audit.source_ip_hash) == 32

    for statement in (
        update(QuotaAdjustment).values(note="mutated"),
        delete(QuotaAdjustment),
        update(AdminAuditLog).values(reason="mutated"),
        delete(AdminAuditLog),
    ):
        async with session_factory() as session, session.begin():
            with pytest.raises(DBAPIError):
                await session.execute(statement)


@pytest.mark.asyncio
async def test_legacy_invitation_remains_insertable_after_admin_migration(
    session_factory,
    test_settings,
):
    async with session_factory() as session, session.begin():
        legacy = Invitation(
            secret_hash=hash_secret(
                "inv_legacy",
                purpose="invitation",
                pepper=test_settings.secret_hash_pepper.get_secret_value(),
            ),
            source_label="legacy",
            expires_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(legacy)
    async with session_factory() as session:
        row = await session.scalar(select(Invitation))
        assert row is not None
        assert row.batch_id is None
        assert row.public_id is None
        assert row.sequence_number is None


@pytest.mark.asyncio
async def test_admin_schema_constraints_are_present(session_factory):
    async with session_factory() as session:
        trigger_count = await session.scalar(
            text(
                """
                SELECT count(*)
                FROM pg_trigger
                WHERE tgname IN (
                    'trg_quota_adjustment_immutable',
                    'trg_admin_audit_log_immutable',
                    'trg_admin_idempotency_no_delete'
                )
                AND NOT tgisinternal
                """
            )
        )
        assert trigger_count == 3


@pytest.mark.asyncio
async def test_controlled_owner_bootstrap_requires_verified_configured_user_and_revokes_sessions(
    session_factory,
    test_settings,
):
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        owner = AppUser(
            public_id=new_opaque_id("usr_"),
            status="ACTIVE",
            role="USER",
        )
        session.add(owner)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=owner.id,
                provider="email_otp",
                provider_subject="owner@example.com",
                verified_email="owner@example.com",
            )
        )
        active_session = UserSession(
            user_id=owner.id,
            token_hash=b"b" * 32,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=7),
        )
        session.add(active_session)
        await session.flush()
        owner_id = owner.id
        owner_public_id = owner.public_id
        session_id = active_session.id

    settings = test_settings.model_copy(update={"admin_owner_user_id": owner_id})
    async with session_factory() as session, session.begin():
        bootstrapped = await bootstrap_configured_owner(
            session,
            settings,
            target_user_id=owner_id,
            request_id="bootstrap-request",
            source_ip="127.0.0.1",
        )
        assert bootstrapped.role == "ADMIN"

    async with session_factory() as session:
        revoked = await session.get(UserSession, session_id)
        audit = await session.scalar(
            select(AdminAuditLog).where(AdminAuditLog.action == "SYSTEM_BOOTSTRAP")
        )
        assert revoked is not None
        assert revoked.revoked_at is not None
        assert revoked.revoke_reason == "SYSTEM_BOOTSTRAP"
        assert audit is not None
        assert audit.actor_identity == "SYSTEM"
        assert audit.target_id == owner_public_id

    async with session_factory() as session, session.begin():
        replay = await bootstrap_configured_owner(
            session,
            settings,
            target_user_id=owner_id,
            request_id="bootstrap-replay",
            source_ip="127.0.0.1",
        )
        assert replay.role == "ADMIN"
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AdminAuditLog)
            .where(AdminAuditLog.action == "SYSTEM_BOOTSTRAP")
        )
        assert count == 1

    async with session_factory() as session, session.begin():
        with pytest.raises(OwnerBootstrapRejected):
            await bootstrap_configured_owner(
                session,
                settings,
                target_user_id=uuid.uuid4(),
                request_id="wrong-owner",
                source_ip="127.0.0.1",
            )
