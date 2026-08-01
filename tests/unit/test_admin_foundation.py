from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.admin.audit import audit_source_ip_hash, redact_audit_projection
from src.admin.auth import ADMIN_CAPABILITIES, OWNER_CAPABILITIES, get_current_admin
from src.admin.idempotency import (
    admin_actor_scope_hash,
    canonical_admin_request_hash,
)
from src.api.errors import ApiError
from src.auth.dependencies import AuthContext
from src.config import Settings
from src.db.models import AppUser, UserSession


def _auth(user_id: uuid.UUID, *, role: str = "USER") -> AuthContext:
    now = datetime.now(UTC)
    return AuthContext(
        user=AppUser(
            id=user_id,
            public_id=f"usr_{user_id.hex}",
            status="ACTIVE",
            role=role,
            display_name="test_admin",
            display_name_normalized="test_admin",
            display_name_changed_at=None,
        ),
        session=UserSession(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=b"x" * 32,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=7),
        ),
    )


@pytest.mark.asyncio
async def test_owner_identity_is_configured_user_id_not_email():
    owner_id = uuid.uuid4()
    settings = Settings(admin_owner_user_id=owner_id)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))

    owner = await get_current_admin(request, _auth(owner_id))
    assert owner.product_identity == "OWNER"
    assert owner.capabilities == OWNER_CAPABILITIES

    admin = await get_current_admin(request, _auth(uuid.uuid4(), role="ADMIN"))
    assert admin.product_identity == "ADMIN"
    assert admin.capabilities == ADMIN_CAPABILITIES

    with pytest.raises(ApiError) as caught:
        await get_current_admin(request, _auth(uuid.uuid4()))
    assert caught.value.status_code == 403
    assert caught.value.code == "ADMIN_REQUIRED"


def test_admin_hashes_are_stable_scoped_and_canonical():
    actor = uuid.uuid4()
    settings = Settings(secret_hash_pepper="unit-test-pepper")
    assert admin_actor_scope_hash(actor, settings) == admin_actor_scope_hash(actor, settings)
    assert admin_actor_scope_hash(actor, settings) != admin_actor_scope_hash(uuid.uuid4(), settings)
    assert canonical_admin_request_hash("TEST", {"b": 2, "a": 1}) == (
        canonical_admin_request_hash("TEST", {"a": 1, "b": 2})
    )
    assert canonical_admin_request_hash("TEST", {"a": 1}) != canonical_admin_request_hash(
        "TEST", {"a": 2}
    )


def test_audit_projection_and_ip_are_irreversibly_redacted():
    settings = Settings(secret_hash_pepper="unit-test-pepper")
    projected = redact_audit_projection(
        settings,
        {
            "email": "person@example.com",
            "notes": "private draft",
            "safe": "contact person@example.com",
        },
    )
    assert projected == {
        "email": "[REDACTED]",
        "notes": "[REDACTED]",
        "safe": "contact [REDACTED_EMAIL]",
    }
    digest = audit_source_ip_hash("203.0.113.4", settings)
    assert len(digest) == 32
    assert b"203.0.113.4" not in digest
