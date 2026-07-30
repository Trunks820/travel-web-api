from __future__ import annotations

import pytest

from scripts.local_joint_guard import (
    LocalJointGuardError,
    validate_disposable_database_url,
    validate_local_joint_settings,
)
from scripts.run_local_joint_bff import LocalConsoleOtpMailer
from src.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "development",
        "database_url": ("postgresql+asyncpg://postgres@127.0.0.1:55432/travel_web_test_joint"),
        "user_origin": "http://localhost:3000",
        "cookie_secure": False,
        "hermes_base_url": "http://127.0.0.1:6666",
        "hermes_internal_credential": "h" * 32,
        "hermes_bff_internal_admin_credential": "a" * 32,
        "secret_hash_pepper": "s" * 32,
        "directmail_access_key_id": "",
        "directmail_access_key_secret": "",
    }
    values.update(overrides)
    return Settings(**values)


def test_local_joint_guard_accepts_only_explicit_disposable_runtime() -> None:
    validate_local_joint_settings(
        _settings(),
        environ={"LOCAL_JOINT_MODE": "1"},
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "postgresql+asyncpg://postgres@db/travel_web_test_joint"},
        {"database_url": ("postgresql+asyncpg://postgres@127.0.0.1:55432/travel_web")},
        {"user_origin": "https://kakarot8.com"},
        {"cookie_secure": True},
        {"hermes_base_url": "http://127.0.0.1:7777"},
        {"hermes_read_timeout_seconds": 15.0},
        {"hermes_internal_credential": "short"},
        {"secret_hash_pepper": "short"},
        {"directmail_access_key_id": "must-not-be-used-by-local-harness"},
    ],
)
def test_local_joint_guard_rejects_unsafe_settings(overrides) -> None:
    with pytest.raises(LocalJointGuardError):
        validate_local_joint_settings(
            _settings(**overrides),
            environ={"LOCAL_JOINT_MODE": "1"},
        )


def test_local_joint_guard_requires_explicit_confirmation() -> None:
    with pytest.raises(LocalJointGuardError):
        validate_local_joint_settings(_settings(), environ={})


def test_local_joint_guard_refuses_production() -> None:
    settings = Settings(
        app_env="production",
        database_url=("postgresql+asyncpg://postgres@127.0.0.1:55432/travel_web_test_joint"),
        user_origin="https://kakarot8.com",
        admin_origin="https://admin.kakarot8.com",
        cookie_secure=True,
        hermes_base_url="http://127.0.0.1:6666",
        hermes_internal_credential="h" * 32,
        hermes_bff_internal_admin_credential="a" * 32,
        secret_hash_pepper="s" * 32,
        directmail_access_key_id="local-id",
        directmail_access_key_secret="local-secret",
        admin_owner_user_id="11111111-1111-1111-1111-111111111111",
    )
    with pytest.raises(LocalJointGuardError, match="production"):
        validate_local_joint_settings(
            settings,
            environ={"LOCAL_JOINT_MODE": "1"},
        )


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///travel_web_test_joint.db",
        "postgresql+asyncpg://postgres@203.0.113.10/travel_web_test_joint",
        "postgresql+asyncpg://postgres@127.0.0.1:55432/travel_agent",
    ],
)
def test_disposable_database_guard_fails_before_connection(database_url) -> None:
    with pytest.raises(LocalJointGuardError):
        validate_disposable_database_url(database_url)


@pytest.mark.asyncio
async def test_local_console_mailer_exposes_only_local_code(capsys) -> None:
    await LocalConsoleOtpMailer().send_otp(
        email="private@example.com",
        code="654321",
        purpose="EMAIL_AUTH",
    )
    output = capsys.readouterr().out
    assert output == "LOCAL_ONLY_OTP purpose=EMAIL_AUTH code=654321\n"
    assert "private@example.com" not in output
