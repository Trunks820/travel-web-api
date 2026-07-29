from __future__ import annotations

from pathlib import Path

from alembic.config import Config

from alembic import command
from scripts.local_joint_guard import validate_local_joint_settings
from src.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def reset_database(settings: Settings) -> None:
    validate_local_joint_settings(settings)
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "alembic"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def main() -> None:
    settings = Settings()
    reset_database(settings)
    print("LOCAL_ONLY_DATABASE_RESET=complete")  # noqa: T201


if __name__ == "__main__":
    main()
