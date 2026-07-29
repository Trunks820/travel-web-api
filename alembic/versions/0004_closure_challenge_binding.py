"""bind account-closure OTP challenges to the authenticated user

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_otp_challenge",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_email_otp_challenge_user",
        "email_otp_challenge",
        "app_user",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_email_otp_challenge_user_id",
        "email_otp_challenge",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_otp_challenge_user_id",
        table_name="email_otp_challenge",
    )
    op.drop_constraint(
        "fk_email_otp_challenge_user",
        "email_otp_challenge",
        type_="foreignkey",
    )
    op.drop_column("email_otp_challenge", "user_id")
