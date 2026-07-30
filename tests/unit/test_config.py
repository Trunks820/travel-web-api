import pytest
from pydantic import ValidationError

from src.config import Settings


def test_production_rejects_placeholder_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production")


def test_production_requires_secure_https_cookie_origins() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://user:strong@db/travel_web",
            hermes_internal_credential="strong-hermes-secret",
            hermes_bff_internal_admin_credential="strong-hermes-admin-secret",
            secret_hash_pepper="strong-hash-pepper",
            directmail_access_key_id="access-key-id",
            directmail_access_key_secret="access-key-secret",
            cookie_secure=False,
        )
