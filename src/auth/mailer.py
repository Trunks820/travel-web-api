from __future__ import annotations

from typing import Protocol

from alibabacloud_dm20151123 import models as dm_models
from alibabacloud_dm20151123.client import Client as DirectMailSdkClient
from alibabacloud_tea_openapi import models as open_api_models

from src.config import Settings


class EmailDeliveryError(Exception):
    pass


class OtpMailer(Protocol):
    async def send_otp(self, *, email: str, code: str, purpose: str) -> None: ...


class DirectMailOtpMailer:
    def __init__(self, settings: Settings) -> None:
        config = open_api_models.Config(
            access_key_id=settings.directmail_access_key_id.get_secret_value(),
            access_key_secret=settings.directmail_access_key_secret.get_secret_value(),
            endpoint=settings.directmail_endpoint,
            region_id=settings.directmail_region,
            connect_timeout=settings.directmail_connect_timeout_ms,
            read_timeout=settings.directmail_read_timeout_ms,
        )
        self._client = DirectMailSdkClient(config)
        self._account_name = settings.directmail_account_name

    async def send_otp(self, *, email: str, code: str, purpose: str) -> None:
        action = "注销账户" if purpose == "ACCOUNT_CLOSURE" else "登录或注册"
        request = dm_models.SingleSendMailRequest(
            account_name=self._account_name,
            address_type=1,
            reply_to_address=False,
            to_address=email,
            subject=f"云途{action}验证码",
            text_body=f"你的云途验证码是：{code}。验证码将在 10 分钟后失效，请勿转发。",
            click_trace="0",
            un_subscribe_link_type="disabled",
        )
        try:
            await self._client.single_send_mail_async(request)
        except Exception as exc:
            raise EmailDeliveryError("DirectMail delivery failed") from exc
