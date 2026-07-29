from __future__ import annotations

import asyncio

from scripts.local_joint_guard import validate_local_joint_settings
from src.config import Settings
from src.db.session import build_engine, build_session_factory
from src.invitations.service import create_invitation


async def seed_one_invitation(settings: Settings) -> str:
    validate_local_joint_settings(settings)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session, session.begin():
            _invitation, raw_secret = await create_invitation(
                session,
                settings,
                source_label="j0.5-local-joint",
            )
        return raw_secret
    finally:
        await engine.dispose()


def main() -> None:
    raw_secret = asyncio.run(seed_one_invitation(Settings()))
    print(f"LOCAL_ONLY_INVITATION={raw_secret}")  # noqa: T201


if __name__ == "__main__":
    main()
