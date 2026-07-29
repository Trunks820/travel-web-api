from __future__ import annotations

import pytest

from src.auth.mailer import DirectMailOtpMailer, EmailDeliveryError
from src.config import Settings


class FakeDirectMailSdk:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests = []

    async def single_send_mail_async(self, request):
        if self.fail:
            raise RuntimeError("provider-private-body")
        self.requests.append(request)


async def test_directmail_request_uses_frozen_sender_and_safe_fields() -> None:
    settings = Settings(
        app_env="test",
        directmail_access_key_id="test-key",
        directmail_access_key_secret="test-secret",
    )
    mailer = DirectMailOtpMailer(settings)
    fake = FakeDirectMailSdk()
    mailer._client = fake

    await mailer.send_otp(
        email="user@example.com",
        code="123456",
        purpose="EMAIL_AUTH",
    )

    request = fake.requests[0]
    assert request.account_name == "no-reply@notify.kakarot8.com"
    assert request.to_address == "user@example.com"
    assert request.address_type == 1
    assert request.reply_to_address is False
    assert request.click_trace == "0"
    assert request.un_subscribe_link_type == "disabled"


async def test_directmail_provider_failure_is_normalized() -> None:
    mailer = DirectMailOtpMailer(
        Settings(
            app_env="test",
            directmail_access_key_id="test-key",
            directmail_access_key_secret="test-secret",
        )
    )
    mailer._client = FakeDirectMailSdk(fail=True)

    with pytest.raises(EmailDeliveryError) as exc_info:
        await mailer.send_otp(
            email="user@example.com",
            code="123456",
            purpose="EMAIL_AUTH",
        )
    assert "provider-private-body" not in str(exc_info.value)
