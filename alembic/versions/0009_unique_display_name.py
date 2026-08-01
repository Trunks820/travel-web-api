"""add globally unique mutable Display Names

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import secrets
import string
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = string.ascii_lowercase + string.digits
_RESERVED_EXACT = frozenset(
    {
        "admin",
        "administrator",
        "owner",
        "official",
        "system",
        "support",
        "service",
        "yuntu",
        "云途",
        "管理员",
        "客服",
        "系统",
    }
)
_RESERVED_PREFIXES = ("yuntu_", "admin_", "system_", "official_", "support_", "云途")


def _is_han_character(value: str) -> bool:
    name = unicodedata.name(value, "")
    return name.startswith("CJK UNIFIED IDEOGRAPH-") or name.startswith(
        "CJK COMPATIBILITY IDEOGRAPH-"
    )


def _normalize_existing(raw_value: str | None) -> tuple[str, str] | None:
    if raw_value is None:
        return None
    presentation = unicodedata.normalize("NFKC", raw_value.strip()).strip()
    if not 2 <= len(presentation) <= 24:
        return None
    for value in presentation:
        allowed = (
            value == "_"
            or value.isdigit()
            or _is_han_character(value)
            or (
                unicodedata.category(value).startswith("L")
                and "LATIN" in unicodedata.name(value, "")
            )
        )
        if not allowed:
            return None
    if all(value.isdigit() for value in presentation):
        return None
    normalized = presentation.casefold()
    if normalized in _RESERVED_EXACT or normalized.startswith(_RESERVED_PREFIXES):
        return None
    return presentation, normalized


def _new_default(used: set[str]) -> tuple[str, str]:
    while True:
        value = "user_" + "".join(secrets.choice(_ALPHABET) for _ in range(10))
        if value not in used:
            return value, value


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("display_name_normalized", sa.String(length=96), nullable=True),
    )
    op.add_column(
        "app_user",
        sa.Column("display_name_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "display_name_quarantine",
        sa.Column("name_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name_digest"),
    )
    op.create_index(
        "ix_display_name_quarantine_expires_at",
        "display_name_quarantine",
        ["expires_at"],
    )

    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE app_user IN SHARE ROW EXCLUSIVE MODE"))
    users = bind.execute(
        sa.text("SELECT id, display_name FROM app_user ORDER BY created_at, id")
    ).mappings()
    used: set[str] = set()
    for user in users:
        candidate = _normalize_existing(user["display_name"])
        if candidate is None or candidate[1] in used:
            candidate = _new_default(used)
        presentation, normalized = candidate
        used.add(normalized)
        bind.execute(
            sa.text(
                "UPDATE app_user "
                "SET display_name = :presentation, display_name_normalized = :normalized "
                "WHERE id = :user_id"
            ),
            {
                "presentation": presentation,
                "normalized": normalized,
                "user_id": user["id"],
            },
        )

    op.alter_column(
        "app_user",
        "display_name",
        existing_type=sa.String(length=120),
        type_=sa.String(length=24),
        nullable=False,
        postgresql_using="display_name::varchar(24)",
    )
    op.alter_column(
        "app_user",
        "display_name_normalized",
        existing_type=sa.String(length=96),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_app_user_display_name_normalized",
        "app_user",
        ["display_name_normalized"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_app_user_display_name_normalized",
        "app_user",
        type_="unique",
    )
    op.alter_column(
        "app_user",
        "display_name",
        existing_type=sa.String(length=24),
        type_=sa.String(length=120),
        nullable=True,
    )
    op.drop_index(
        "ix_display_name_quarantine_expires_at",
        table_name="display_name_quarantine",
    )
    op.drop_table("display_name_quarantine")
    op.drop_column("app_user", "display_name_changed_at")
    op.drop_column("app_user", "display_name_normalized")
