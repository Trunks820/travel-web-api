from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.admin.audit import admin_subject_hash
from src.config import Settings
from src.db.models import (
    AdminAuditLog,
    AdminIdempotency,
    AppUser,
    EmailOtpChallenge,
    InvitationBatch,
    InvitationRedemption,
    QuotaAdjustment,
    QuotaGrant,
    TripQuotaEntry,
    UserIdentity,
    UserSession,
    UserTrip,
)
from src.history.service import deidentify_trip_request
from src.quota.service import ACTIVE_TRIP_STATUSES
from src.security.secrets import secret_matches


@dataclass(frozen=True)
class ClosureResult:
    outcome: str


async def close_account(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    user_id,
    challenge_id: str,
    code: str,
) -> ClosureResult:
    now = datetime.now(UTC)
    outcome = "OTP_INVALID"
    async with session_factory() as session, session.begin():
        user = await session.scalar(select(AppUser).where(AppUser.id == user_id).with_for_update())
        if user is None:
            return ClosureResult("AUTH_REQUIRED")
        identity = await session.scalar(
            select(UserIdentity).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == "email_otp",
            )
        )
        challenge = await session.scalar(
            select(EmailOtpChallenge)
            .where(EmailOtpChallenge.public_id == challenge_id)
            .with_for_update()
        )
        if (
            identity is None
            or not identity.verified_email
            or challenge is None
            or challenge.purpose != "ACCOUNT_CLOSURE"
            or challenge.mode != "closure"
            or challenge.user_id != user_id
            or challenge.email != identity.verified_email
            or challenge.delivery_status != "SENT"
        ):
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
            purpose=f"otp:{challenge.public_id}:ACCOUNT_CLOSURE",
            pepper=settings.secret_hash_pepper.get_secret_value(),
        ):
            challenge.attempt_count += 1
            outcome = (
                "OTP_ATTEMPTS_EXCEEDED"
                if challenge.attempt_count >= challenge.max_attempts
                else "OTP_INVALID"
            )
        else:
            active = await session.scalar(
                select(UserTrip.id).where(
                    UserTrip.user_id == user_id,
                    UserTrip.status.in_(ACTIVE_TRIP_STATUSES),
                )
            )
            if active is not None:
                outcome = "ACTIVE_TRIP_IN_PROGRESS"
            else:
                subject_hash = admin_subject_hash(user.id, settings)
                public_user_id = user.public_id
                trips = list(
                    (
                        await session.scalars(
                            select(UserTrip).where(UserTrip.user_id == user_id).with_for_update()
                        )
                    ).all()
                )
                for trip in trips:
                    trip.user_id = None
                    trip.quota_entry_id = None
                    trip.identity_erased_at = now
                    trip.archived_at = now
                    trip.client_request_id = f"erased:{trip.public_id}"
                    trip.request_json = deidentify_trip_request(
                        trip.request_json,
                        account_closure=True,
                    )
                    trip.updated_at = now
                await session.flush()
                await session.execute(
                    delete(TripQuotaEntry).where(TripQuotaEntry.user_id == user_id)
                )
                await session.execute(delete(QuotaGrant).where(QuotaGrant.user_id == user_id))
                await session.execute(
                    delete(InvitationRedemption).where(InvitationRedemption.user_id == user_id)
                )
                await session.execute(delete(UserSession).where(UserSession.user_id == user_id))
                await session.execute(
                    delete(EmailOtpChallenge).where(
                        (EmailOtpChallenge.user_id == user_id)
                        | (EmailOtpChallenge.email == identity.verified_email)
                    )
                )
                await session.execute(delete(UserIdentity).where(UserIdentity.user_id == user_id))
                await session.execute(text("SET LOCAL travel_web.account_closure = 'on'"))
                await session.execute(
                    update(AdminIdempotency)
                    .where(AdminIdempotency.actor_user_id == user_id)
                    .values(actor_user_id=None)
                )
                await session.execute(
                    update(QuotaAdjustment)
                    .where(QuotaAdjustment.target_user_id == user_id)
                    .values(target_user_id=None, target_scope_hash=subject_hash)
                )
                await session.execute(
                    update(QuotaAdjustment)
                    .where(QuotaAdjustment.actor_user_id == user_id)
                    .values(actor_user_id=None, actor_scope_hash=subject_hash)
                )
                await session.execute(
                    update(InvitationBatch)
                    .where(InvitationBatch.created_by_user_id == user_id)
                    .values(created_by_user_id=None, creator_scope_hash=subject_hash)
                )
                await session.execute(
                    update(AdminAuditLog)
                    .where(AdminAuditLog.actor_user_id == user_id)
                    .values(actor_user_id=None)
                )
                await session.execute(
                    update(AdminAuditLog)
                    .where(AdminAuditLog.target_id == public_user_id)
                    .values(target_id=None)
                )
                await session.delete(user)
                outcome = "SUCCESS"
    return ClosureResult(outcome)
