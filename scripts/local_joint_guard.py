from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from src.config import Settings

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
EXPECTED_USER_ORIGIN = "http://localhost:3000"
EXPECTED_HERMES_PORT = 6666
HERMES_SSE_KEEPALIVE_SECONDS = 15.0
MIN_HERMES_READ_TIMEOUT_SECONDS = HERMES_SSE_KEEPALIVE_SECONDS * 3
LOCAL_CONFIRMATION_NAME = "LOCAL_JOINT_MODE"
LOCAL_CONFIRMATION_VALUE = "1"


class LocalJointGuardError(ValueError):
    pass


def validate_disposable_database_url(database_url: str) -> None:
    parsed = make_url(database_url)
    if parsed.drivername != "postgresql+asyncpg":
        raise LocalJointGuardError("local joint database must use postgresql+asyncpg")
    if parsed.host not in LOCAL_HOSTS:
        raise LocalJointGuardError("local joint database must use a loopback host")
    if not parsed.database or not parsed.database.startswith("travel_web_test"):
        raise LocalJointGuardError("local joint database name must start with travel_web_test")


def _validate_local_secret(name: str, value: str) -> None:
    if len(value) < 32 or "replace-me" in value.casefold():
        raise LocalJointGuardError(f"{name} must be a generated local secret")


def validate_local_joint_settings(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    runtime_env = os.environ if environ is None else environ
    if runtime_env.get(LOCAL_CONFIRMATION_NAME) != LOCAL_CONFIRMATION_VALUE:
        raise LocalJointGuardError(
            f"{LOCAL_CONFIRMATION_NAME}={LOCAL_CONFIRMATION_VALUE} is required"
        )
    if settings.app_env not in {"development", "test"}:
        raise LocalJointGuardError("local joint harness refuses production APP_ENV")

    validate_disposable_database_url(settings.database_url)

    if settings.user_origin.rstrip("/") != EXPECTED_USER_ORIGIN:
        raise LocalJointGuardError(f"USER_ORIGIN must be exactly {EXPECTED_USER_ORIGIN}")
    if settings.cookie_secure:
        raise LocalJointGuardError("COOKIE_SECURE must be false for loopback HTTP")

    hermes_url = urlsplit(settings.hermes_base_url)
    if (
        hermes_url.scheme != "http"
        or hermes_url.hostname not in LOCAL_HOSTS
        or hermes_url.port != EXPECTED_HERMES_PORT
    ):
        raise LocalJointGuardError(
            "HERMES_BASE_URL must be http://127.0.0.1:6666 or an equivalent loopback URL"
        )
    if settings.hermes_read_timeout_seconds < MIN_HERMES_READ_TIMEOUT_SECONDS:
        raise LocalJointGuardError(
            "HERMES_READ_TIMEOUT_SECONDS must be at least "
            f"{MIN_HERMES_READ_TIMEOUT_SECONDS:g} for the 15-second Hermes SSE keepalive"
        )

    _validate_local_secret(
        "SECRET_HASH_PEPPER",
        settings.secret_hash_pepper.get_secret_value(),
    )
    _validate_local_secret(
        "HERMES_INTERNAL_CREDENTIAL",
        settings.hermes_internal_credential.get_secret_value(),
    )
    _validate_local_secret(
        "HERMES_BFF_INTERNAL_ADMIN_CREDENTIAL",
        settings.hermes_bff_internal_admin_credential.get_secret_value(),
    )

    if (
        settings.directmail_access_key_id.get_secret_value()
        or settings.directmail_access_key_secret.get_secret_value()
    ):
        raise LocalJointGuardError(
            "local console-mail harness refuses configured DirectMail credentials"
        )
