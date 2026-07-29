from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.account.closure import close_account
from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.auth.router import _clear_session_cookie
from src.auth.schemas import VerifyEmailCodeRequest
from src.auth.service import send_closure_code
from src.db.models import UserIdentity
from src.db.session import get_db_session

router = APIRouter(prefix="/api/me/closure")
CURRENT_AUTH = Depends(get_current_auth)
DB_SESSION = Depends(get_db_session)


@router.post("/send-code")
async def closure_send_code(
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    email = await db.scalar(
        select(UserIdentity.verified_email).where(
            UserIdentity.user_id == auth.user.id,
            UserIdentity.provider == "email_otp",
        )
    )
    if not email:
        raise ApiError(401, "AUTH_REQUIRED", "无法验证当前账户。")
    challenge_id = await send_closure_code(
        request.app.state.session_factory,
        request.app.state.settings,
        request.app.state.mailer,
        user_id=auth.user.id,
        email=email,
        client_ip=request.client.host if request.client else "unknown",
    )
    return {
        "ok": True,
        "challenge_id": challenge_id,
        "resend_after_seconds": request.app.state.settings.otp_resend_seconds,
    }


@router.post("/confirm")
async def closure_confirm(
    body: VerifyEmailCodeRequest,
    request: Request,
    response: Response,
    auth: AuthContext = CURRENT_AUTH,
) -> dict[str, bool]:
    result = await close_account(
        request.app.state.session_factory,
        request.app.state.settings,
        user_id=auth.user.id,
        challenge_id=body.challenge_id,
        code=body.code,
    )
    if result.outcome == "SUCCESS":
        _clear_session_cookie(response, request)
        return {"ok": True}
    errors = {
        "ACTIVE_TRIP_IN_PROGRESS": (
            409,
            "ACTIVE_TRIP_IN_PROGRESS",
            "当前行程结束后才能注销账户。",
        ),
        "OTP_EXPIRED": (400, "OTP_EXPIRED", "验证码已失效。"),
        "OTP_USED": (400, "OTP_USED", "验证码已使用。"),
        "OTP_ATTEMPTS_EXCEEDED": (
            400,
            "OTP_ATTEMPTS_EXCEEDED",
            "验证码尝试次数过多。",
        ),
        "AUTH_REQUIRED": (401, "AUTH_REQUIRED", "请先登录。"),
    }
    status, code, message = errors.get(
        result.outcome,
        (400, "OTP_INVALID", "验证码无效。"),
    )
    raise ApiError(status, code, message)
