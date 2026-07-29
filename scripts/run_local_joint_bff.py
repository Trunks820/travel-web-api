from __future__ import annotations

import uvicorn

from scripts.local_joint_guard import validate_local_joint_settings
from src.app import create_app
from src.config import Settings


class LocalConsoleOtpMailer:
    async def send_otp(self, *, email: str, code: str, purpose: str) -> None:
        del email
        print(  # noqa: T201
            f"LOCAL_ONLY_OTP purpose={purpose} code={code}",
            flush=True,
        )


def build_local_joint_app(settings: Settings | None = None):
    runtime_settings = settings or Settings()
    validate_local_joint_settings(runtime_settings)
    return create_app(runtime_settings, mailer=LocalConsoleOtpMailer())


def main() -> None:
    uvicorn.run(
        build_local_joint_app(),
        host="127.0.0.1",
        port=6670,
        log_level="info",
    )


if __name__ == "__main__":
    main()
