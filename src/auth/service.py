from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.errors import ApiError
from src.auth.mailer import EmailDeliveryError, OtpMailer
from src.auth.schemas import SendEmailCodeRequest
from src.config import Settings
from src.db.models import (
    AppUser,
    EmailOtpChallenge,
    Invitation,
    InvitationRedemption,
    QuotaGrant,
    UserIdentity,
    UserSession,
)
from src.invitations.service import find_invitation, invitation_is_usable
from src.security.secrets import (
    hash_secret,
    new_opaque_id,
    new_otp_code,
    new_session_token,
    secret_matches,
)


@dataclass(frozen=True)
class AuthVerification:
    outcome: str
    session_token: str | None = None


def _hash(settings: Settings, raw: str, purpose: str) -> bytes:
    return hash_secret(
        raw,
        purpose=purpose,
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )


async def _enforce_send_limits(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    ip_hash: bytes,
    purpose: str,
    now: datetime,
) -> None:
    cooldown_since = now - timedelta(seconds=settings.otp_resend_seconds)
    recent = await session.scalar(
        select(func.max(EmailOtpChallenge.sent_at)).where(
            EmailOtpChallenge.email == email,
            EmailOtpChallenge.purpose == purpose,
        )
    )
    if recent is not None and recent > cooldown_since:
        raise ApiError(429, "OTP_RATE_LIMITED", "请求过于频繁，请稍后再试。", retryable=True)

    window_start = now - timedelta(seconds=settings.otp_rate_window_seconds)
    email_count = await session.scalar(
        select(func.count())
        .select_from(EmailOtpChallenge)
        .where(
            EmailOtpChallenge.email == email,
            EmailOtpChallenge.sent_at >= window_start,
        )
    )
    ip_count = await session.scalar(
        select(func.count())
        .select_from(EmailOtpChallenge)
        .where(
            EmailOtpChallenge.client_ip_hash == ip_hash,
            EmailOtpChallenge.sent_at >= window_start,
        )
    )
    global_count = await session.scalar(
        select(func.count())
        .select_from(EmailOtpChallenge)
        .where(EmailOtpChallenge.sent_at >= window_start)
    )
    if (
        int(email_count or 0) >= settings.otp_per_email_limit
        or int(ip_count or 0) >= settings.otp_per_ip_limit
        or int(global_count or 0) >= settings.otp_global_limit
    ):
        raise ApiError(429, "OTP_RATE_LIMITED", "请求过于频繁，请稍后再试。", retryable=True)


async def send_auth_code(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    mailer: OtpMailer,
    body: SendEmailCodeRequest,
    *,
    client_ip: str,
) -> str:
    now = datetime.now(UTC)
    raw_code = new_otp_code(settings.otp_code_digits)
    public_id = new_opaque_id("otp_")
    ip_hash = _hash(settings, client_ip, "client-ip")
    invitation_id = None
    async with session_factory() as session, session.begin():
        await _enforce_send_limits(
            session,
            settings,
            email=str(body.email),
            ip_hash=ip_hash,
            purpose="EMAIL_AUTH",
            now=now,
        )
        if body.mode == "register":
            invitation = await find_invitation(
                session, body.invitation_code or "", settings, for_update=False
            )
            if invitation is None or not invitation_is_usable(invitation, now):
                raise ApiError(422, "INVITATION_INVALID", "邀请码无效或已失效。")
            invitation_id = invitation.id
        challenge = EmailOtpChallenge(
            public_id=public_id,
            email=str(body.email),
            mode=body.mode,
            purpose="EMAIL_AUTH",
            invitation_id=invitation_id,
            code_hash=_hash(settings, raw_code, f"otp:{public_id}:EMAIL_AUTH"),
            client_ip_hash=ip_hash,
            attempt_count=0,
            max_attempts=settings.otp_max_attempts,
            delivery_status="PENDING",
            sent_at=now,
            expires_at=now + timedelta(seconds=settings.otp_expiry_seconds),
        )
        session.add(challenge)

    try:
        await mailer.send_otp(email=str(body.email), code=raw_code, purpose="EMAIL_AUTH")
    except EmailDeliveryError as exc:
        async with session_factory() as session, session.begin():
            await session.execute(
                update(EmailOtpChallenge)
                .where(EmailOtpChallenge.public_id == public_id)
                .values(delivery_status="FAILED")
            )
        raise ApiError(
            503,
            "EMAIL_DELIVERY_UNAVAILABLE",
            "验证码暂时无法发送，请稍后再试。",
            retryable=True,
        ) from exc

    async with session_factory() as session, session.begin():
        await session.execute(
            update(EmailOtpChallenge)
            .where(
                EmailOtpChallenge.public_id == public_id,
                EmailOtpChallenge.delivery_status == "PENDING",
            )
            .values(delivery_status="SENT")
        )
    return public_id


async def send_closure_code(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    mailer: OtpMailer,
    *,
    user_id,
    email: str,
    client_ip: str,
) -> str:
    now = datetime.now(UTC)
    raw_code = new_otp_code(settings.otp_code_digits)
    public_id = new_opaque_id("otp_")
    ip_hash = _hash(settings, client_ip, "client-ip")
    async with session_factory() as session, session.begin():
        await _enforce_send_limits(
            session,
            settings,
            email=email,
            ip_hash=ip_hash,
            purpose="ACCOUNT_CLOSURE",
            now=now,
        )
        session.add(
            EmailOtpChallenge(
                public_id=public_id,
                email=email,
                mode="closure",
                purpose="ACCOUNT_CLOSURE",
                invitation_id=None,
                user_id=user_id,
                code_hash=_hash(
                    settings,
                    raw_code,
                    f"otp:{public_id}:ACCOUNT_CLOSURE",
                ),
                client_ip_hash=ip_hash,
                attempt_count=0,
                max_attempts=settings.otp_max_attempts,
                delivery_status="PENDING",
                sent_at=now,
                expires_at=now + timedelta(seconds=settings.otp_expiry_seconds),
            )
        )
    try:
        await mailer.send_otp(
            email=email,
            code=raw_code,
            purpose="ACCOUNT_CLOSURE",
        )
    except EmailDeliveryError as exc:
        async with session_factory() as session, session.begin():
            await session.execute(
                update(EmailOtpChallenge)
                .where(EmailOtpChallenge.public_id == public_id)
                .values(delivery_status="FAILED")
            )
        raise ApiError(
            503,
            "EMAIL_DELIVERY_UNAVAILABLE",
            "验证码暂时无法发送，请稍后再试。",
            retryable=True,
        ) from exc
    async with session_factory() as session, session.begin():
        await session.execute(
            update(EmailOtpChallenge)
            .where(
                EmailOtpChallenge.public_id == public_id,
                EmailOtpChallenge.delivery_status == "PENDING",
            )
            .values(delivery_status="SENT")
        )
    return public_id


async def _new_session(
    session: AsyncSession,
    settings: Settings,
    user_id,
    now: datetime,
) -> str:
    raw_token = new_session_token()
    session.add(
        UserSession(
            user_id=user_id,
            token_hash=_hash(settings, raw_token, "session"),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=settings.session_days),
        )
    )
    await session.flush()
    return raw_token


async def verify_auth_code(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    challenge_id: str,
    code: str,
) -> AuthVerification:
    now = datetime.now(UTC)
    outcome = "OTP_INVALID"
    raw_session: str | None = None
    async with session_factory() as session, session.begin():
        challenge = await session.scalar(
            select(EmailOtpChallenge)
            .where(EmailOtpChallenge.public_id == challenge_id)
            .with_for_update()
        )
        if challenge is None or challenge.purpose != "EMAIL_AUTH":
            outcome = "OTP_INVALID"
        elif challenge.delivery_status != "SENT":
            outcome = "OTP_INVALID"
        elif challenge.consumed_at is not None:
            outcome = "OTP_USED"
        elif challenge.expires_at <= now:
            outcome = "OTP_EXPIRED"
        elif challenge.attempt_count >= challenge.max_attempts:
            outcome = "OTP_ATTEMPTS_EXCEEDED"
        elif not secret_matches(
            code,
            challenge.code_hash,
            purpose=f"otp:{challenge.public_id}:EMAIL_AUTH",
            pepper=settings.secret_hash_pepper.get_secret_value(),
        ):
            challenge.attempt_count += 1
            outcome = (
                "OTP_ATTEMPTS_EXCEEDED"
                if challenge.attempt_count >= challenge.max_attempts
                else "OTP_INVALID"
            )
        else:
            challenge.consumed_at = now
            # Distinct valid challenges for the same address can be verified
            # concurrently. Serialize that identity decision so the loser
            # receives the documented LOGIN_REQUIRED correction instead of a
            # unique-constraint failure.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:email, 0))"),
                {"email": challenge.email},
            )
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == "email_otp",
                    UserIdentity.provider_subject == challenge.email,
                )
            )
            if challenge.mode == "login":
                if identity is None:
                    outcome = "REGISTRATION_REQUIRED"
                else:
                    user = await session.scalar(
                        select(AppUser).where(AppUser.id == identity.user_id).with_for_update()
                    )
                    if user is None or user.status != "ACTIVE":
                        outcome = "AUTH_REQUIRED"
                    else:
                        identity.last_login_at = now
                        raw_session = await _new_session(session, settings, user.id, now)
                        outcome = "SUCCESS"
            elif identity is not None:
                outcome = "LOGIN_REQUIRED"
            else:
                invitation = await session.scalar(
                    select(Invitation)
                    .where(Invitation.id == challenge.invitation_id)
                    .with_for_update()
                )
                if invitation is None or not invitation_is_usable(invitation, now):
                    outcome = "INVITATION_INVALID"
                else:
                    user = AppUser(
                        public_id=new_opaque_id("usr_"),
                        status="ACTIVE",
                        role="USER",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(user)
                    await session.flush()
                    session.add(
                        UserIdentity(
                            user_id=user.id,
                            provider="email_otp",
                            provider_subject=challenge.email,
                            verified_email=challenge.email,
                            created_at=now,
                            last_login_at=now,
                        )
                    )
                    invitation.redeemed_at = now
                    session.add(
                        InvitationRedemption(
                            invitation_id=invitation.id,
                            user_id=user.id,
                            redeemed_at=now,
                        )
                    )
                    session.add(
                        QuotaGrant(
                            user_id=user.id,
                            period_type="BETA_LIFETIME",
                            period_key="v0.1-beta",
                            units=settings.beta_quota_limit,
                            reason="INITIAL_REGISTRATION",
                            idempotency_key="initial-registration",
                            created_at=now,
                        )
                    )
                    raw_session = await _new_session(session, settings, user.id, now)
                    outcome = "SUCCESS"
    return AuthVerification(outcome=outcome, session_token=raw_session)


async def revoke_cookie_session(
    session: AsyncSession,
    settings: Settings,
    raw_token: str | None,
) -> None:
    if not raw_token:
        return
    token_hash = _hash(settings, raw_token, "session")
    now = datetime.now(UTC)
    await session.execute(
        update(UserSession)
        .where(UserSession.token_hash == token_hash, UserSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason="USER_LOGOUT")
    )
    await session.commit()


async def revoke_user_sessions(
    session: AsyncSession,
    *,
    user_id,
    reason: str,
) -> int:
    now = datetime.now(UTC)
    changed = await session.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason=reason)
    )
    await session.flush()
    return int(changed.rowcount or 0)
