"""travel-admin owner, idempotency, audit, quota, and invitation foundation

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitation_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source_label", sa.String(length=120), nullable=False),
        sa.Column("code_count", sa.SmallInteger(), nullable=False),
        sa.Column("valid_days", sa.SmallInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code_count BETWEEN 1 AND 200",
            name="ck_invitation_batch_code_count",
        ),
        sa.CheckConstraint(
            "valid_days BETWEEN 1 AND 90",
            name="ck_invitation_batch_valid_days",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_invitation_batch_created_by_user_id",
        "invitation_batch",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_invitation_batch_expires_at",
        "invitation_batch",
        ["expires_at"],
    )

    op.add_column("invitation", sa.Column("public_id", sa.String(length=80), nullable=True))
    op.add_column(
        "invitation",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "invitation",
        sa.Column("sequence_number", sa.SmallInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_invitation_batch_id",
        "invitation",
        "invitation_batch",
        ["batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_invitation_public_id",
        "invitation",
        ["public_id"],
    )
    op.create_unique_constraint(
        "uq_invitation_batch_sequence",
        "invitation",
        ["batch_id", "sequence_number"],
    )
    op.create_check_constraint(
        "ck_invitation_sequence_positive",
        "invitation",
        "sequence_number IS NULL OR sequence_number > 0",
    )
    op.create_index("ix_invitation_batch_id", "invitation", ["batch_id"])

    op.create_table(
        "admin_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_scope_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('IN_PROGRESS', 'SUCCEEDED')",
            name="ck_admin_idempotency_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_scope_hash",
            "idempotency_key",
            name="uq_admin_idempotency_actor_key",
        ),
    )

    op.create_table(
        "quota_adjustment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_before", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("idempotency_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "reverses_adjustment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("delta <> 0", name="ck_quota_adjustment_nonzero"),
        sa.CheckConstraint(
            "balance_before >= 0 AND balance_after >= 0",
            name="ck_quota_adjustment_nonnegative_balance",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_id"],
            ["admin_idempotency.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_adjustment_id"],
            ["quota_adjustment.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint(
            "reverses_adjustment_id",
            name="uq_quota_adjustment_reversal",
        ),
    )
    op.create_index(
        "ix_quota_adjustment_actor_user_id",
        "quota_adjustment",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_quota_adjustment_target_user_id",
        "quota_adjustment",
        ["target_user_id"],
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_identity", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=160), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=False),
        sa.Column("source_ip_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "client_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_identity IN ('ADMIN', 'OWNER', 'SYSTEM')",
            name="ck_admin_audit_actor_identity",
        ),
        sa.CheckConstraint(
            "result IN ('SUCCESS', 'FAILURE')",
            name="ck_admin_audit_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_admin_audit_action_created",
        "admin_audit_log",
        ["action", "created_at"],
    )
    op.create_index(
        "ix_admin_audit_created_id",
        "admin_audit_log",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_admin_audit_log_actor_user_id",
        "admin_audit_log",
        ["actor_user_id"],
    )

    op.execute(
        """
        CREATE FUNCTION travel_web_reject_immutable_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in ("quota_adjustment", "admin_audit_log"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION travel_web_reject_immutable_change()
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_admin_idempotency_no_delete
        BEFORE DELETE ON admin_idempotency
        FOR EACH ROW EXECUTE FUNCTION travel_web_reject_immutable_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_admin_idempotency_no_delete ON admin_idempotency")
    op.execute("DROP TRIGGER trg_admin_audit_log_immutable ON admin_audit_log")
    op.execute("DROP TRIGGER trg_quota_adjustment_immutable ON quota_adjustment")
    op.execute("DROP FUNCTION travel_web_reject_immutable_change()")

    op.drop_index("ix_admin_audit_log_actor_user_id", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_created_id", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_action_created", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_index("ix_quota_adjustment_target_user_id", table_name="quota_adjustment")
    op.drop_index("ix_quota_adjustment_actor_user_id", table_name="quota_adjustment")
    op.drop_table("quota_adjustment")
    op.drop_table("admin_idempotency")

    op.drop_index("ix_invitation_batch_id", table_name="invitation")
    op.drop_constraint("ck_invitation_sequence_positive", "invitation", type_="check")
    op.drop_constraint("uq_invitation_batch_sequence", "invitation", type_="unique")
    op.drop_constraint("uq_invitation_public_id", "invitation", type_="unique")
    op.drop_constraint("fk_invitation_batch_id", "invitation", type_="foreignkey")
    op.drop_column("invitation", "sequence_number")
    op.drop_column("invitation", "batch_id")
    op.drop_column("invitation", "public_id")

    op.drop_index(
        "ix_invitation_batch_expires_at",
        table_name="invitation_batch",
    )
    op.drop_index(
        "ix_invitation_batch_created_by_user_id",
        table_name="invitation_batch",
    )
    op.drop_table("invitation_batch")
