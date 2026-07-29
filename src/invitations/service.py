from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.db.models import Invitation
from src.security.secrets import hash_secret, new_opaque_id


def invitation_is_usable(invitation: Invitation, now: datetime) -> bool:
    return (
        invitation.disabled_at is None
        and invitation.redeemed_at is None
        and (invitation.expires_at is None or invitation.expires_at > now)
    )


async def find_invitation(
    session: AsyncSession,
    raw_secret: str,
    settings: Settings,
    *,
    for_update: bool = False,
) -> Invitation | None:
    secret_hash = hash_secret(
        raw_secret,
        purpose="invitation",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )
    statement = select(Invitation).where(Invitation.secret_hash == secret_hash)
    if for_update:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def create_invitation(
    session: AsyncSession,
    settings: Settings,
    *,
    source_label: str,
    expires_at: datetime | None = None,
) -> tuple[Invitation, str]:
    raw_secret = new_opaque_id("inv_", bytes_of_entropy=24)
    invitation = Invitation(
        secret_hash=hash_secret(
            raw_secret,
            purpose="invitation",
            pepper=settings.secret_hash_pepper.get_secret_value(),
        ),
        source_label=source_label,
        expires_at=expires_at,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(invitation)
    await session.flush()
    return invitation, raw_secret
