from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_app_user_status"),
        CheckConstraint("role IN ('USER', 'ADMIN')", name="ck_app_user_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="USER")
    display_name: Mapped[str] = mapped_column(String(24), nullable=False)
    display_name_normalized: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)

    display_name_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class DisplayNameQuarantine(Base):
    __tablename__ = "display_name_quarantine"

    name_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserIdentity(Base):
    __tablename__ = "user_identity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_identity_provider_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(320), nullable=False)
    verified_email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[AppUser] = relationship(back_populates="identities")


class InvitationBatch(Base):
    __tablename__ = "invitation_batch"
    __table_args__ = (
        CheckConstraint("code_count BETWEEN 1 AND 200", name="ck_invitation_batch_code_count"),
        CheckConstraint("valid_days BETWEEN 1 AND 90", name="ck_invitation_batch_valid_days"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_label: Mapped[str] = mapped_column(String(120), nullable=False)
    code_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    valid_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    creator_scope_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Invitation(Base):
    __tablename__ = "invitation"
    __table_args__ = (
        UniqueConstraint("batch_id", "sequence_number", name="uq_invitation_batch_sequence"),
        CheckConstraint(
            "sequence_number IS NULL OR sequence_number > 0",
            name="ck_invitation_sequence_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invitation_batch.id", ondelete="RESTRICT"), index=True
    )
    sequence_number: Mapped[int | None] = mapped_column(SmallInteger)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    source_label: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EmailOtpChallenge(Base):
    __tablename__ = "email_otp_challenge"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('login', 'register', 'closure')", name="ck_email_otp_challenge_mode"
        ),
        CheckConstraint(
            "purpose IN ('EMAIL_AUTH', 'ACCOUNT_CLOSURE')",
            name="ck_email_otp_challenge_purpose",
        ),
        CheckConstraint(
            "delivery_status IN ('PENDING', 'SENT', 'FAILED')",
            name="ck_email_otp_challenge_delivery_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_email_otp_challenge_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_email_otp_challenge_max_attempts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invitation.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    client_ip_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvitationRedemption(Base):
    __tablename__ = "invitation_redemption"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invitation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invitation.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserSession(Base):
    __tablename__ = "user_session"
    __table_args__ = (
        Index("ix_user_session_user_expires", "user_id", "expires_at"),
        Index("ix_user_session_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[AppUser] = relationship(back_populates="sessions")


class QuotaGrant(Base):
    __tablename__ = "quota_grant"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_quota_grant_user_idempotency"),
        CheckConstraint("units > 0", name="ck_quota_grant_positive_units"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminIdempotency(Base):
    __tablename__ = "admin_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "actor_scope_hash",
            "idempotency_key",
            name="uq_admin_idempotency_actor_key",
        ),
        CheckConstraint(
            "state IN ('IN_PROGRESS', 'SUCCEEDED')",
            name="ck_admin_idempotency_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_scope_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_PROGRESS")
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuotaAdjustment(Base):
    __tablename__ = "quota_adjustment"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_quota_adjustment_nonzero"),
        CheckConstraint(
            "balance_before >= 0 AND balance_after >= 0",
            name="ck_quota_adjustment_nonnegative_balance",
        ),
        UniqueConstraint(
            "reverses_adjustment_id",
            name="uq_quota_adjustment_reversal",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    target_scope_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    actor_scope_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_before: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    idempotency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_idempotency.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    reverses_adjustment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quota_adjustment.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"
    __table_args__ = (
        CheckConstraint(
            "actor_identity IN ('USER', 'ADMIN', 'OWNER', 'SYSTEM')",
            name="ck_admin_audit_actor_identity",
        ),
        CheckConstraint(
            "result IN ('SUCCESS', 'FAILURE')",
            name="ck_admin_audit_result",
        ),
        Index("ix_admin_audit_created_id", "created_at", "id"),
        Index("ix_admin_audit_action_created", "action", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_identity: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(160))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column()
    request_id: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ip_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    client_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserTrip(Base):
    """P2/P3 model declared early so `/api/me` can expose an active projection."""

    __tablename__ = "user_trip"
    __table_args__ = (
        UniqueConstraint("user_id", "client_request_id", name="uq_user_trip_request"),
        CheckConstraint(
            "status IN ('SUBMITTING', 'PENDING', 'RUNNING', 'SUCCESS', "
            "'FAILED', 'TIMEOUT', 'REJECTED')",
            name="ck_user_trip_status",
        ),
        Index("ix_user_trip_owner_created", "user_id", "created_at", "id"),
        Index("ix_user_trip_reconcile", "status", "updated_at"),
        Index(
            "uq_user_trip_one_active",
            "user_id",
            unique=True,
            postgresql_where=text(
                "user_id IS NOT NULL AND status IN ('SUBMITTING', 'PENDING', 'RUNNING')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    hermes_job_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    result_record_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    quota_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "trip_quota_entry.id",
            name="fk_user_trip_quota_entry",
            ondelete="SET NULL",
            use_alter=True,
        ),
        unique=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    telemetry_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    visible_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identity_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    association_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    request_field_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    reconciliation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminTripProjection(Base):
    __tablename__ = "admin_trip_projection"
    __table_args__ = (
        Index("ix_admin_trip_created_job", text("created_at DESC"), text("job_id DESC")),
        Index("ix_admin_trip_status_created", "status", "created_at"),
        Index("ix_admin_trip_source_created", "source", "created_at"),
        Index("ix_admin_trip_association_created", "association_state", "created_at"),
        Index("ix_admin_trip_user_created", "user_id", "created_at"),
        Index("ix_admin_trip_result_record", "result_record_id"),
        Index("ix_admin_trip_stage_created", "current_stage", "created_at"),
    )

    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    days: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(120))
    result_type: Mapped[str | None] = mapped_column(String(32))
    result_record_id: Mapped[int | None] = mapped_column(BigInteger)
    guide_result_state: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    safe_error_message: Mapped[str | None] = mapped_column(String(500))
    detailed_reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_draft_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trace_completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    association_state: Mapped[str] = mapped_column(String(24), nullable=False)
    association_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    identity_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_trip_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_trip.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdminTripStepProjection(Base):
    __tablename__ = "admin_trip_step_projection"
    __table_args__ = (
        Index("ix_admin_step_job_order", "job_id", "started_at", "source_step_id"),
        Index("ix_admin_step_stage_start", "stage", "started_at"),
        Index("ix_admin_step_stage_status_start", "stage", "status", "started_at"),
        Index("ix_admin_step_job_stage_status", "job_id", "stage", "status"),
    )

    source_step_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("admin_trip_projection.job_id", ondelete="CASCADE"), nullable=False
    )
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stage: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    publish_retry_round: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdminProjectionEvent(Base):
    __tablename__ = "admin_projection_event"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    outbox_sequence: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminProjectionConsumerState(Base):
    __tablename__ = "admin_projection_consumer_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    applied_high_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latest_heartbeat_watermark: Mapped[int | None] = mapped_column(BigInteger)
    latest_heartbeat_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    sync_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    next_expected_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    stream_state: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initialization_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="UNINITIALIZED"
    )


class AdminProjectionBackfillCheckpoint(Base):
    __tablename__ = "admin_projection_backfill_checkpoint"

    entity_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    snapshot_max_id: Mapped[int | None] = mapped_column(BigInteger)
    last_source_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminProjectionReconciliation(Base):
    __tablename__ = "admin_projection_reconciliation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impossible_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class TripQuotaEntry(Base):
    __tablename__ = "trip_quota_entry"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RESERVED', 'CONSUMED', 'RELEASED')",
            name="ck_trip_quota_entry_status",
        ),
        CheckConstraint("units > 0", name="ck_trip_quota_entry_positive_units"),
        Index(
            "ix_trip_quota_owner_period_status",
            "user_id",
            "period_type",
            "period_key",
            "status",
        ),
        Index("ix_trip_quota_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_trip.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    period_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reserve_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    settle_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
