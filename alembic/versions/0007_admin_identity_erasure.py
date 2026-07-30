"""make Administrator persistence compatible with Account Closure

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("admin_idempotency", "actor_user_id", nullable=True)
    op.alter_column("quota_adjustment", "target_user_id", nullable=True)
    op.alter_column("quota_adjustment", "actor_user_id", nullable=True)
    op.alter_column("invitation_batch", "created_by_user_id", nullable=True)
    op.add_column(
        "quota_adjustment",
        sa.Column("target_scope_hash", sa.LargeBinary(length=32), nullable=True),
    )
    op.add_column(
        "quota_adjustment",
        sa.Column("actor_scope_hash", sa.LargeBinary(length=32), nullable=True),
    )
    op.add_column(
        "invitation_batch",
        sa.Column("creator_scope_hash", sa.LargeBinary(length=32), nullable=True),
    )
    op.drop_constraint(
        "ck_admin_audit_actor_identity",
        "admin_audit_log",
        type_="check",
    )
    op.create_check_constraint(
        "ck_admin_audit_actor_identity",
        "admin_audit_log",
        "actor_identity IN ('USER', 'ADMIN', 'OWNER', 'SYSTEM')",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION travel_web_reject_immutable_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            closure_mode boolean :=
                current_setting('travel_web.account_closure', true) = 'on';
        BEGIN
            IF TG_OP = 'UPDATE' AND closure_mode THEN
                IF TG_TABLE_NAME = 'quota_adjustment'
                   AND (to_jsonb(NEW) - ARRAY[
                        'actor_user_id', 'target_user_id',
                        'actor_scope_hash', 'target_scope_hash'
                   ]) = (to_jsonb(OLD) - ARRAY[
                        'actor_user_id', 'target_user_id',
                        'actor_scope_hash', 'target_scope_hash'
                   ])
                   AND (NEW.actor_user_id IS NULL OR NEW.actor_user_id = OLD.actor_user_id)
                   AND (NEW.target_user_id IS NULL OR NEW.target_user_id = OLD.target_user_id)
                THEN
                    RETURN NEW;
                END IF;
                IF TG_TABLE_NAME = 'admin_audit_log'
                   AND (to_jsonb(NEW) - ARRAY['actor_user_id', 'target_id'])
                       = (to_jsonb(OLD) - ARRAY['actor_user_id', 'target_id'])
                   AND (NEW.actor_user_id IS NULL OR NEW.actor_user_id = OLD.actor_user_id)
                   AND (NEW.target_id IS NULL OR NEW.target_id = OLD.target_id)
                THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION travel_web_reject_immutable_change()
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
    op.drop_constraint(
        "ck_admin_audit_actor_identity",
        "admin_audit_log",
        type_="check",
    )
    op.create_check_constraint(
        "ck_admin_audit_actor_identity",
        "admin_audit_log",
        "actor_identity IN ('ADMIN', 'OWNER', 'SYSTEM')",
    )
    op.drop_column("invitation_batch", "creator_scope_hash")
    op.drop_column("quota_adjustment", "actor_scope_hash")
    op.drop_column("quota_adjustment", "target_scope_hash")
    op.alter_column("invitation_batch", "created_by_user_id", nullable=False)
    op.alter_column("quota_adjustment", "actor_user_id", nullable=False)
    op.alter_column("quota_adjustment", "target_user_id", nullable=False)
    op.alter_column("admin_idempotency", "actor_user_id", nullable=False)
