from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.auth.schemas import SendEmailCodeRequest, VerifyEmailCodeRequest
from src.auth.service import revoke_cookie_session, send_auth_code, verify_auth_code
from src.db.models import UserIdentity, UserTrip
from src.db.session import get_db_session
from src.quota.service import ACTIVE_TRIP_STATUSES, quota_snapshot

router = APIRouter(prefix="/api")
CURRENT_AUTH = Depends(get_current_auth)
DB_SESSION = Depends(get_db_session)


def _set_session_cookie(response: Response, request: Request, raw_token: str) -> None:
    settings = request.app.state.settings
    max_age = int(timedelta(days=settings.session_days).total_seconds())
    response.set_cookie(
        key=settings.cookie_name,
        value=raw_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=request.app.state.settings.cookie_name,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _mask_email(email: str) -> str:
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


@router.post("/auth/email/send-code")
async def email_send_code(
    body: SendEmailCodeRequest,
    request: Request,
) -> dict[str, object]:
    challenge_id = await send_auth_code(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.mailer,
        body,
        client_ip=request.client.host if request.client else "unknown",
    )
    return {
        "ok": True,
        "challenge_id": challenge_id,
        "resend_after_seconds": request.app.state.settings.otp_resend_seconds,
    }


@router.post("/auth/email/verify")
async def email_verify(
    body: VerifyEmailCodeRequest,
    request: Request,
    response: Response,
) -> dict[str, bool]:
    result = await verify_auth_code(
        request.app.state.session_factory,
        request.app.state.settings,
        challenge_id=body.challenge_id,
        code=body.code,
    )
    if result.outcome == "SUCCESS" and result.session_token:
        _set_session_cookie(response, request, result.session_token)
        return {"ok": True}
    errors = {
        "REGISTRATION_REQUIRED": (409, "REGISTRATION_REQUIRED", "该邮箱需要先完成注册。"),
        "LOGIN_REQUIRED": (409, "LOGIN_REQUIRED", "该邮箱已注册，请改用登录。"),
        "INVITATION_INVALID": (422, "INVITATION_INVALID", "邀请码无效或已失效。"),
        "OTP_EXPIRED": (400, "OTP_EXPIRED", "验证码已失效。"),
        "OTP_USED": (400, "OTP_USED", "验证码已使用。"),
        "OTP_ATTEMPTS_EXCEEDED": (400, "OTP_ATTEMPTS_EXCEEDED", "验证码尝试次数过多。"),
        "AUTH_REQUIRED": (401, "AUTH_REQUIRED", "无法创建登录状态。"),
    }
    status, code, message = errors.get(result.outcome, (400, "OTP_INVALID", "验证码无效。"))
    raise ApiError(status, code, message)


@router.get("/me")
async def me(
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    email = await db.scalar(
        select(UserIdentity.verified_email).where(
            UserIdentity.user_id == auth.user.id,
            UserIdentity.provider == "email_otp",
        )
    )
    quota = await quota_snapshot(db, auth.user.id)
    active = await db.scalar(
        select(UserTrip).where(
            UserTrip.user_id == auth.user.id,
            UserTrip.status.in_(ACTIVE_TRIP_STATUSES),
        )
    )
    return {
        "ok": True,
        "user": {
            "user_id": auth.user.public_id,
            "display_name": auth.user.display_name,
            "masked_email": _mask_email(email) if email else None,
        },
        "quota": quota.public(),
        "active_trip": (
            {
                "trip_id": active.public_id,
                "job_id": active.hermes_job_id,
                "status": active.status,
            }
            if active is not None
            else None
        ),
    }


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = DB_SESSION,
) -> dict[str, bool]:
    await revoke_cookie_session(
        db,
        request.app.state.settings,
        request.cookies.get(request.app.state.settings.cookie_name),
    )
    _clear_session_cookie(response, request)
    return {"ok": True}
