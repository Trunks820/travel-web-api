from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://travel_web_api:replace-me@127.0.0.1:5432/travel_web"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_seconds: float = 10.0

    hermes_base_url: str = "http://127.0.0.1:6666"
    hermes_internal_credential: SecretStr = SecretStr("replace-me")
    hermes_bff_internal_admin_credential: SecretStr = SecretStr("replace-me")
    hermes_connect_timeout_seconds: float = 3.0
    hermes_read_timeout_seconds: float = 90.0
    hermes_write_timeout_seconds: float = 10.0
    hermes_pool_timeout_seconds: float = 5.0

    secret_hash_pepper: SecretStr = SecretStr("replace-me")
    directmail_access_key_id: SecretStr = SecretStr("")
    directmail_access_key_secret: SecretStr = SecretStr("")
    directmail_account_name: str = "no-reply@notify.kakarot8.com"
    directmail_region: str = "cn-hangzhou"
    directmail_endpoint: str = "dm.aliyuncs.com"
    directmail_connect_timeout_ms: int = 3_000
    directmail_read_timeout_ms: int = 10_000

    cookie_name: str = "yuntu_session"
    cookie_secure: bool = True
    session_days: int = 7
    session_last_seen_write_seconds: int = 3_600
    user_origin: str = "https://kakarot8.com"
    admin_origin: str = "https://admin.kakarot8.com"
    admin_owner_user_id: uuid.UUID | None = None
    request_max_bytes: int = 65_536

    otp_code_digits: int = 6
    otp_expiry_seconds: int = 600
    otp_max_attempts: int = 5
    otp_resend_seconds: int = 60
    otp_rate_window_seconds: int = 3_600
    otp_per_email_limit: int = 5
    otp_per_ip_limit: int = 20
    otp_global_limit: int = 1_000

    beta_quota_limit: int = 3
    trip_history_days: int = 7
    reconciliation_batch_size: int = 50
    reconciliation_max_attempts: int = 5
    artifact_max_bytes: int = 25 * 1024 * 1024

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset({self.user_origin.rstrip("/"), self.admin_origin.rstrip("/")})

    @property
    def redaction_secrets(self) -> tuple[str, ...]:
        values = (
            self.hermes_internal_credential.get_secret_value(),
            self.hermes_bff_internal_admin_credential.get_secret_value(),
            self.secret_hash_pepper.get_secret_value(),
            self.directmail_access_key_id.get_secret_value(),
            self.directmail_access_key_secret.get_secret_value(),
        )
        return tuple(value for value in values if value and value != "replace-me")

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> Settings:
        if self.db_pool_size < 1 or self.db_max_overflow < 0:
            raise ValueError("database pool bounds are invalid")
        if self.request_max_bytes < 1024:
            raise ValueError("REQUEST_MAX_BYTES is too small")
        if self.session_days != 7 or self.trip_history_days != 7:
            raise ValueError("v0.1 session and history policy is fixed at seven days")
        if self.otp_code_digits < 6 or self.otp_max_attempts < 1:
            raise ValueError("OTP security bounds are invalid")
        if (
            min(
                self.otp_expiry_seconds,
                self.otp_resend_seconds,
                self.otp_rate_window_seconds,
                self.otp_per_email_limit,
                self.otp_per_ip_limit,
                self.otp_global_limit,
            )
            < 1
        ):
            raise ValueError("OTP expiry and rate bounds must be positive")
        if self.app_env != "production":
            return self
        required = {
            "DATABASE_URL": self.database_url,
            "HERMES_INTERNAL_CREDENTIAL": self.hermes_internal_credential.get_secret_value(),
            "HERMES_BFF_INTERNAL_ADMIN_CREDENTIAL": (
                self.hermes_bff_internal_admin_credential.get_secret_value()
            ),
            "SECRET_HASH_PEPPER": self.secret_hash_pepper.get_secret_value(),
            "DIRECTMAIL_ACCESS_KEY_ID": self.directmail_access_key_id.get_secret_value(),
            "DIRECTMAIL_ACCESS_KEY_SECRET": self.directmail_access_key_secret.get_secret_value(),
            "ADMIN_OWNER_USER_ID": str(self.admin_owner_user_id or ""),
        }
        unsafe = [
            name
            for name, value in required.items()
            if not value or "replace-me" in value.casefold()
        ]
        if unsafe:
            raise ValueError(f"unsafe or missing production settings: {', '.join(sorted(unsafe))}")
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if not self.user_origin.startswith("https://") or not self.admin_origin.startswith(
            "https://"
        ):
            raise ValueError("production origins must use HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
