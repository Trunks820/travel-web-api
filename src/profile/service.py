from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.db.models import AppUser, DisplayNameQuarantine
from src.profile.display_names import (
    FORMER_NAME_QUARANTINE_DAYS,
    RENAME_COOLDOWN_DAYS,
    former_name_digest,
    generate_default_display_name,
    normalize_display_name,
)

_DISPLAY_NAME_UNIQUE_CONSTRAINT = "uq_app_user_display_name_normalized"


class DisplayNameMutationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DisplayNameMutation:
    public_user_id: str
    display_name: str
    changed_at: datetime | None


def change_available_at(changed_at: datetime | None) -> datetime | None:
    if changed_at is None:
        return None
    return changed_at + timedelta(days=RENAME_COOLDOWN_DAYS)


def _constraint_name(error: IntegrityError) -> str | None:
    candidates: tuple[Any, ...] = (
        error.orig,
        getattr(error.orig, "__cause__", None),
        getattr(error.orig, "__context__", None),
    )
    for candidate in candidates:
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    if _DISPLAY_NAME_UNIQUE_CONSTRAINT in str(error):
        return _DISPLAY_NAME_UNIQUE_CONSTRAINT
    return None


async def create_user_with_default_display_name(
    session: AsyncSession,
    settings: Settings,
    **user_fields: Any,
) -> AppUser:
    for _attempt in range(32):
        candidate = generate_default_display_name()
        now = datetime.now(UTC)
        digest = former_name_digest(
            candidate.uniqueness_key,
            pepper=settings.secret_hash_pepper.get_secret_value(),
        )
        unavailable = await session.scalar(
            select(
                or_(
                    exists().where(AppUser.display_name_normalized == candidate.uniqueness_key),
                    exists().where(
                        DisplayNameQuarantine.name_digest == digest,
                        DisplayNameQuarantine.expires_at > now,
                    ),
                )
            )
        )
        if unavailable:
            continue
        user = AppUser(
            display_name=candidate.presentation,
            display_name_normalized=candidate.uniqueness_key,
            display_name_changed_at=None,
            **user_fields,
        )
        try:
            async with session.begin_nested():
                session.add(user)
                await session.flush()
        except IntegrityError as error:
            if _constraint_name(error) == _DISPLAY_NAME_UNIQUE_CONSTRAINT:
                continue
            raise
        return user
    raise RuntimeError("could not allocate a unique default Display Name")


async def quarantine_former_name(
    session: AsyncSession,
    settings: Settings,
    *,
    normalized_name: str,
    now: datetime,
) -> None:
    digest = former_name_digest(
        normalized_name,
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )
    expires_at = now + timedelta(days=FORMER_NAME_QUARANTINE_DAYS)
    statement = pg_insert(DisplayNameQuarantine).values(
        name_digest=digest,
        expires_at=expires_at,
        created_at=now,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[DisplayNameQuarantine.name_digest],
            set_={"expires_at": expires_at, "created_at": now},
        )
    )


async def rename_display_name(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    user_id,
    requested_name: str,
) -> DisplayNameMutation:
    candidate = normalize_display_name(requested_name)
    now = datetime.now(UTC)
    try:
        async with session_factory() as session, session.begin():
            user = await session.scalar(
                select(AppUser).where(AppUser.id == user_id).with_for_update()
            )
            if user is None or user.status != "ACTIVE":
                raise DisplayNameMutationError("AUTH_REQUIRED")
            if candidate.presentation == user.display_name:
                return DisplayNameMutation(
                    user.public_id,
                    user.display_name,
                    user.display_name_changed_at,
                )

            available_at = change_available_at(user.display_name_changed_at)
            if available_at is not None and now < available_at:
                raise DisplayNameMutationError("DISPLAY_NAME_CHANGE_COOLDOWN")

            if candidate.uniqueness_key != user.display_name_normalized:
                owned = await session.scalar(
                    select(AppUser.id).where(
                        AppUser.display_name_normalized == candidate.uniqueness_key
                    )
                )
                digest = former_name_digest(
                    candidate.uniqueness_key,
                    pepper=settings.secret_hash_pepper.get_secret_value(),
                )
                quarantined = await session.scalar(
                    select(DisplayNameQuarantine.name_digest).where(
                        DisplayNameQuarantine.name_digest == digest,
                        DisplayNameQuarantine.expires_at > now,
                    )
                )
                if owned is not None or quarantined is not None:
                    raise DisplayNameMutationError("DISPLAY_NAME_UNAVAILABLE")
                await quarantine_former_name(
                    session,
                    settings,
                    normalized_name=user.display_name_normalized,
                    now=now,
                )

            user.display_name = candidate.presentation
            user.display_name_normalized = candidate.uniqueness_key
            user.display_name_changed_at = now
            user.updated_at = now
            await session.flush()
            mutation = DisplayNameMutation(
                user.public_id,
                user.display_name,
                user.display_name_changed_at,
            )
    except IntegrityError as error:
        if _constraint_name(error) == _DISPLAY_NAME_UNIQUE_CONSTRAINT:
            raise DisplayNameMutationError("DISPLAY_NAME_UNAVAILABLE") from error
        raise
    return mutation
