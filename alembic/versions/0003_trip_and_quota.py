"""trip ownership and quota lifecycle

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_trip",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("days", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hermes_job_id", sa.String(length=160), nullable=True),
        sa.Column("result_record_id", sa.BigInteger(), nullable=True),
        sa.Column("quota_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("visible_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("identity_erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_attempts", sa.Integer(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('SUBMITTING', 'PENDING', 'RUNNING', 'SUCCESS', "
            "'FAILED', 'TIMEOUT', 'REJECTED')",
            name="ck_user_trip_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hermes_job_id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("quota_entry_id"),
        sa.UniqueConstraint("result_record_id"),
        sa.UniqueConstraint("user_id", "client_request_id", name="uq_user_trip_request"),
    )
    op.create_index("ix_user_trip_user_id", "user_trip", ["user_id"])
    op.create_index(
        "ix_user_trip_owner_created",
        "user_trip",
        ["user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_user_trip_reconcile",
        "user_trip",
        ["status", "updated_at"],
    )
    op.create_index(
        "uq_user_trip_one_active",
        "user_trip",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "user_id IS NOT NULL AND status IN ('SUBMITTING', 'PENDING', 'RUNNING')"
        ),
    )
    op.create_table(
        "trip_quota_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_type", sa.String(length=32), nullable=False),
        sa.Column("period_key", sa.String(length=64), nullable=False),
        sa.Column("units", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reserve_reason", sa.String(length=64), nullable=False),
        sa.Column("settle_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("units > 0", name="ck_trip_quota_entry_positive_units"),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'CONSUMED', 'RELEASED')",
            name="ck_trip_quota_entry_status",
        ),
        sa.ForeignKeyConstraint(["trip_id"], ["user_trip.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trip_id"),
    )
    op.create_index(
        "ix_trip_quota_owner_period_status",
        "trip_quota_entry",
        ["user_id", "period_type", "period_key", "status"],
    )
    op.create_index(
        "ix_trip_quota_status_created",
        "trip_quota_entry",
        ["status", "created_at"],
    )
    op.create_foreign_key(
        "fk_user_trip_quota_entry",
        "user_trip",
        "trip_quota_entry",
        ["quota_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_user_trip_quota_entry",
        "user_trip",
        type_="foreignkey",
    )
    op.drop_index("ix_trip_quota_status_created", table_name="trip_quota_entry")
    op.drop_index("ix_trip_quota_owner_period_status", table_name="trip_quota_entry")
    op.drop_table("trip_quota_entry")
    op.drop_index("uq_user_trip_one_active", table_name="user_trip")
    op.drop_index("ix_user_trip_reconcile", table_name="user_trip")
    op.drop_index("ix_user_trip_owner_created", table_name="user_trip")
    op.drop_index("ix_user_trip_user_id", table_name="user_trip")
    op.drop_table("user_trip")
