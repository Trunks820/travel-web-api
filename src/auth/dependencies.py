from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import ApiError
from src.db.models import AppUser, UserSession
from src.db.session import get_db_session
from src.security.secrets import hash_secret

DB_SESSION = Depends(get_db_session)


@dataclass(frozen=True)
class AuthContext:
    user: AppUser
    session: UserSession


async def get_current_auth(
    request: Request,
    db: AsyncSession = DB_SESSION,
) -> AuthContext:
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.cookie_name)
    if not raw_token:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。")
    token_hash = hash_secret(
        raw_token,
        purpose="session",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )
    row = (
        await db.execute(
            select(UserSession, AppUser)
            .join(AppUser, AppUser.id == UserSession.user_id)
            .where(UserSession.token_hash == token_hash)
        )
    ).one_or_none()
    if row is None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。")
    user_session, user = row
    now = datetime.now(UTC)
    if user_session.revoked_at is not None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。")
    if user_session.expires_at <= now:
        raise ApiError(401, "SESSION_EXPIRED", "登录状态已过期。")
    if user.status != "ACTIVE":
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。")
    if user_session.last_seen_at <= now - timedelta(
        seconds=settings.session_last_seen_write_seconds
    ):
        user_session.last_seen_at = now
        await db.commit()
    return AuthContext(user=user, session=user_session)
