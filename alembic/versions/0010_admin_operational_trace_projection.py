"""add Admin Control Plane v0.2 operational trace projection

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_trip",
        sa.Column("association_version", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.add_column(
        "user_trip",
        sa.Column(
            "request_field_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_user_trip_association_version_positive",
        "user_trip",
        "association_version >= 1",
    )
    op.execute(
        "UPDATE user_trip SET association_version = 2 "
        "WHERE identity_erased_at IS NOT NULL"
    )

    op.create_table(
        "admin_trip_projection",
        sa.Column("job_id", sa.String(length=160), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("city", sa.String(length=120)),
        sa.Column("days", sa.SmallInteger()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_stage", sa.String(length=120)),
        sa.Column("result_type", sa.String(length=32)),
        sa.Column("result_record_id", sa.BigInteger()),
        sa.Column("guide_result_state", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("safe_error_message", sa.String(length=500)),
        sa.Column("detailed_reason", sa.String(length=80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("failed_draft_available", sa.Boolean(), nullable=False),
        sa.Column("trace_completeness", sa.String(length=16), nullable=False),
        sa.Column("association_state", sa.String(length=24), nullable=False),
        sa.Column("association_version", sa.BigInteger(), nullable=False),
        sa.Column("identity_erased_at", sa.DateTime(timezone=True)),
        sa.Column(
            "user_trip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_trip.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
        ),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_version >= 1", name="ck_admin_trip_source_version"),
        sa.CheckConstraint("association_version >= 1", name="ck_admin_trip_assoc_version"),
        sa.CheckConstraint("retry_count >= 0", name="ck_admin_trip_retry_count"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCESS','FAILED','TIMEOUT','REJECTED')",
            name="ck_admin_trip_status",
        ),
        sa.CheckConstraint(
            "result_type IS NULL OR result_type IN "
            "('PLAN_READY','NO_CANDIDATES','NO_USABLE_ROUTE','UNKNOWN')",
            name="ck_admin_trip_result_type",
        ),
        sa.CheckConstraint(
            "guide_result_state IN "
            "('NOT_APPLICABLE','LEGAL_NO_GUIDE','AVAILABLE','INCONSISTENT')",
            name="ck_admin_trip_guide_state",
        ),
        sa.CheckConstraint(
            "trace_completeness IN ('COMPLETE','PARTIAL','UNKNOWN')",
            name="ck_admin_trip_trace_completeness",
        ),
        sa.CheckConstraint(
            "association_state IN ('linked','de-identified','unlinked')",
            name="ck_admin_trip_association_state",
        ),
        sa.CheckConstraint(
            "(association_state = 'linked' AND user_trip_id IS NOT NULL AND user_id IS NOT NULL "
            "AND identity_erased_at IS NULL) OR "
            "(association_state <> 'linked' AND user_id IS NULL)",
            name="ck_admin_trip_association_shape",
        ),
    )
    op.create_index(
        "ix_admin_trip_created_job",
        "admin_trip_projection",
        [sa.text("created_at DESC"), sa.text("job_id DESC")],
    )
    op.create_index("ix_admin_trip_status_created", "admin_trip_projection", ["status", "created_at"])
    op.create_index("ix_admin_trip_source_created", "admin_trip_projection", ["source", "created_at"])
    op.create_index(
        "ix_admin_trip_association_created",
        "admin_trip_projection",
        ["association_state", "created_at"],
    )
    op.create_index("ix_admin_trip_user_created", "admin_trip_projection", ["user_id", "created_at"])
    op.create_index("ix_admin_trip_result_record", "admin_trip_projection", ["result_record_id"])
    op.create_index("ix_admin_trip_stage_created", "admin_trip_projection", ["current_stage", "created_at"])

    op.create_table(
        "admin_trip_step_projection",
        sa.Column("source_step_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=160),
            sa.ForeignKey("admin_trip_projection.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("publish_retry_round", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_version >= 1", name="ck_admin_step_source_version"),
        sa.CheckConstraint("attempt >= 1", name="ck_admin_step_attempt"),
        sa.CheckConstraint("publish_retry_round >= 0", name="ck_admin_step_publish_retry"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_admin_step_duration"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT')",
            name="ck_admin_step_status",
        ),
    )
    op.create_index("ix_admin_step_job_order", "admin_trip_step_projection", ["job_id", "started_at", "source_step_id"])
    op.create_index("ix_admin_step_stage_start", "admin_trip_step_projection", ["stage", "started_at"])
    op.create_index("ix_admin_step_stage_status_start", "admin_trip_step_projection", ["stage", "status", "started_at"])
    op.create_index("ix_admin_step_job_stage_status", "admin_trip_step_projection", ["job_id", "stage", "status"])

    op.create_table(
        "admin_projection_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("outbox_sequence", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("payload_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "admin_projection_consumer_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("applied_high_watermark", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("latest_heartbeat_watermark", sa.BigInteger()),
        sa.Column("latest_heartbeat_observed_at", sa.DateTime(timezone=True)),
        sa.Column("sync_checked_at", sa.DateTime(timezone=True)),
        sa.Column("schema_version", sa.String(length=20), server_default="1.0", nullable=False),
        sa.Column("next_expected_sequence", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("stream_state", sa.String(length=24), server_default="ACTIVE", nullable=False),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True)),
        sa.Column("initialization_state", sa.String(length=24), server_default="UNINITIALIZED", nullable=False),
        sa.CheckConstraint("id = 1", name="ck_admin_projection_state_singleton"),
        sa.CheckConstraint("applied_high_watermark >= 0", name="ck_admin_projection_watermark"),
        sa.CheckConstraint("next_expected_sequence >= 1", name="ck_admin_projection_next_sequence"),
        sa.CheckConstraint(
            "stream_state IN ('ACTIVE','PAUSED_POISON')",
            name="ck_admin_projection_stream_state",
        ),
        sa.CheckConstraint(
            "initialization_state IN ('UNINITIALIZED','INITIALIZED')",
            name="ck_admin_projection_initialization",
        ),
    )
    op.execute(
        "INSERT INTO admin_projection_consumer_state "
        "(id, applied_high_watermark, schema_version, next_expected_sequence, "
        "stream_state, initialization_state) "
        "VALUES (1, 0, '1.0', 1, 'ACTIVE', 'UNINITIALIZED')"
    )

    op.create_table(
        "admin_projection_backfill_checkpoint",
        sa.Column("entity_type", sa.String(length=20), primary_key=True),
        sa.Column("snapshot_max_id", sa.BigInteger()),
        sa.Column("last_source_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("entity_type IN ('TRIP_JOB','TRIP_STEP')", name="ck_admin_backfill_entity"),
    )
    op.create_table(
        "admin_projection_reconciliation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extra_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stale_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("impossible_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("status IN ('RUNNING','PASSED','FAILED')", name="ck_admin_reconciliation_status"),
    )


def downgrade() -> None:
    op.drop_table("admin_projection_reconciliation")
    op.drop_table("admin_projection_backfill_checkpoint")
    op.drop_table("admin_projection_consumer_state")
    op.drop_table("admin_projection_event")
    op.drop_table("admin_trip_step_projection")
    op.drop_table("admin_trip_projection")
    op.drop_constraint("ck_user_trip_association_version_positive", "user_trip", type_="check")
    op.drop_column("user_trip", "request_field_provenance")
    op.drop_column("user_trip", "association_version")
