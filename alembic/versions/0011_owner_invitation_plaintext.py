"""store encrypted invitation plaintext for OWNER-only reads

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invitation_batch",
        sa.Column(
            "plaintext_recoverable",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "invitation",
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invitation", "encrypted_secret")
    op.drop_column("invitation_batch", "plaintext_recoverable")
