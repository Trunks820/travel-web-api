"""use table-specific immutable-row erasure triggers

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TRIGGER trg_quota_adjustment_immutable ON quota_adjustment")
    op.execute("DROP TRIGGER trg_admin_audit_log_immutable ON admin_audit_log")
    op.execute(
        """
        CREATE FUNCTION travel_web_quota_adjustment_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND current_setting('travel_web.account_closure', true) = 'on'
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
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION travel_web_admin_audit_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND current_setting('travel_web.account_closure', true) = 'on'
               AND (to_jsonb(NEW) - ARRAY['actor_user_id', 'target_id'])
                   = (to_jsonb(OLD) - ARRAY['actor_user_id', 'target_id'])
               AND (NEW.actor_user_id IS NULL OR NEW.actor_user_id = OLD.actor_user_id)
               AND (NEW.target_id IS NULL OR NEW.target_id = OLD.target_id)
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_quota_adjustment_immutable
        BEFORE UPDATE OR DELETE ON quota_adjustment
        FOR EACH ROW EXECUTE FUNCTION travel_web_quota_adjustment_immutable()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_audit_log_immutable
        BEFORE UPDATE OR DELETE ON admin_audit_log
        FOR EACH ROW EXECUTE FUNCTION travel_web_admin_audit_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_admin_audit_log_immutable ON admin_audit_log")
    op.execute("DROP TRIGGER trg_quota_adjustment_immutable ON quota_adjustment")
    op.execute("DROP FUNCTION travel_web_admin_audit_immutable()")
    op.execute("DROP FUNCTION travel_web_quota_adjustment_immutable()")
    op.execute(
        """
        CREATE TRIGGER trg_quota_adjustment_immutable
        BEFORE UPDATE OR DELETE ON quota_adjustment
        FOR EACH ROW EXECUTE FUNCTION travel_web_reject_immutable_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_audit_log_immutable
        BEFORE UPDATE OR DELETE ON admin_audit_log
        FOR EACH ROW EXECUTE FUNCTION travel_web_reject_immutable_change()
        """
    )
