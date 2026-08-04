from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.admin.projection_schemas import Freshness, ProjectionAlarm
from src.integrations.hermes_models import (
    HermesAdminArtifact,
    HermesAdminFailedDraft,
    HermesAdminTripJob,
)


class AdminMutation(BaseModel):
    reason: str = Field(min_length=2, max_length=120)
    idempotency_key: uuid.UUID

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return value.strip()


class QuotaAdjustmentRequest(AdminMutation):
    target_user_id: str = Field(min_length=1, max_length=80)
    delta: int = Field(ge=-100_000, le=100_000)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("delta")
    @classmethod
    def nonzero_delta(cls, value: int) -> int:
        if value == 0:
            raise ValueError("delta must be non-zero")
        return value

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class QuotaReversalRequest(AdminMutation):
    note: str | None = Field(default=None, max_length=500)


class InvitationBatchRequest(AdminMutation):
    name: str = Field(min_length=1, max_length=120)
    source_label: str = Field(min_length=1, max_length=120)
    count: int = Field(default=50, ge=1, le=200)
    valid_days: int = Field(default=30, ge=1, le=90)


class InvitationCodeLookup(BaseModel):
    code: str = Field(min_length=12, max_length=32)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) != 12:
            raise ValueError("code must use YT-XXXX-XXXX")
        return stripped


class AdminErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class AdminErrorResponse(BaseModel):
    ok: Literal[False]
    request_id: str | None = None
    error: AdminErrorDetail


class AdminSuccessResponse(BaseModel):
    ok: Literal[True]
    request_id: str


class AdminPrincipal(BaseModel):
    user_id: str
    role: Literal["USER", "ADMIN"]
    status: Literal["ACTIVE", "DISABLED"]


class AdminMeResponse(AdminSuccessResponse):
    user: AdminPrincipal
    product_identity: Literal["USER", "ADMIN", "OWNER"]
    capabilities: list[str]


class AdminQuotaProjection(BaseModel):
    policy: Literal["beta_lifetime"]
    limit: int
    reserved: int
    consumed: int
    remaining: int
    resets_at: datetime | None


class AdminUserProjection(BaseModel):
    user_id: str
    status: Literal["ACTIVE", "DISABLED"]
    role: Literal["USER", "ADMIN"]
    product_identity: Literal["USER", "ADMIN", "OWNER"]
    display_name: str
    masked_email: str | None
    quota: AdminQuotaProjection
    created_at: datetime
    last_trip_at: datetime | None


class AdminUserListResponse(AdminSuccessResponse):
    items: list[AdminUserProjection]
    page: int
    limit: int
    total: int


class AdminUserDetailResponse(AdminSuccessResponse):
    user: AdminUserProjection


class AdminUserEmailResponse(AdminSuccessResponse):
    user_id: str
    email: str | None


class AdminUserMutationResponse(AdminSuccessResponse):
    user_id: str
    status: Literal["ACTIVE", "DISABLED"]
    role: Literal["USER", "ADMIN"]


class AdminQuotaAdjustmentProjection(BaseModel):
    adjustment_id: str
    target_user_id: str
    delta: int
    before: int
    after: int
    reason: str
    note: str | None
    reverses_adjustment_id: str | None
    created_at: datetime


class AdminQuotaAdjustmentResponse(AdminSuccessResponse):
    adjustment: AdminQuotaAdjustmentProjection


class AdminQuotaLedgerItem(AdminQuotaAdjustmentProjection):
    actor_user_id: str | None


class AdminQuotaLedgerResponse(AdminSuccessResponse):
    user_id: str
    quota: AdminQuotaProjection
    items: list[AdminQuotaLedgerItem]
    page: int
    limit: int
    total: int


class AdminInvitationBatch(BaseModel):
    batch_id: str
    name: str
    source_label: str
    count: int
    valid_days: int
    expires_at: datetime
    disabled_at: datetime | None
    plaintext_recoverable: bool
    created_at: datetime


class AdminInvitationBatchListResponse(AdminSuccessResponse):
    items: list[AdminInvitationBatch]
    page: int
    limit: int
    total: int


class AdminInvitationBatchCreateResponse(AdminSuccessResponse):
    batch: AdminInvitationBatch
    codes_disclosed: bool
    codes: list[str]


class AdminInvitationCodeProjection(BaseModel):
    code_id: str
    sequence: str
    status: Literal["ACTIVE", "EXPIRED", "DISABLED", "EXHAUSTED"]
    redeemed_at: datetime | None


class AdminInvitationBatchDetailResponse(AdminSuccessResponse):
    batch: AdminInvitationBatch
    codes_disclosed: Literal[False]
    codes: list[AdminInvitationCodeProjection]


class AdminInvitationPlaintextCodeProjection(AdminInvitationCodeProjection):
    code: str


class AdminInvitationBatchPlaintextResponse(AdminSuccessResponse):
    batch: AdminInvitationBatch
    codes_disclosed: Literal[True]
    codes: list[AdminInvitationPlaintextCodeProjection]


class AdminInvitationCodeLookupResponse(AdminSuccessResponse):
    code_id: str
    batch_id: str | None
    sequence: str | None
    status: Literal["ACTIVE", "EXPIRED", "DISABLED", "EXHAUSTED"]


class AdminResourceDisabledResponse(AdminSuccessResponse):
    resource_id: str
    status: Literal["DISABLED"]


class AdminRatio(BaseModel):
    value: float | None
    not_applicable: bool


class AdminDashboardUsers(BaseModel):
    total: int
    active: int
    disabled: int
    zero_quota: int
    new_7d: int


class AdminDashboardTrips(BaseModel):
    SUCCESS: int
    FAILED: int
    TIMEOUT: int
    REJECTED: int
    PENDING: int
    RUNNING: int
    processing: int
    terminal_success_rate: AdminRatio


class AdminDashboardInvitations(BaseModel):
    active_unused: int
    expiring_7d: int
    disabled: int


class AdminDashboardException(BaseModel):
    job_id: str | None
    status: str
    city: str | None
    error_code: str | None
    slow: bool


class AdminDashboardResponse(AdminSuccessResponse):
    as_of: datetime
    users: AdminDashboardUsers
    trips_24h: AdminDashboardTrips
    invitations: AdminDashboardInvitations
    recent_exceptions: list[AdminDashboardException]
    freshness: Freshness
    projection_alarm: ProjectionAlarm | None


class AdminTripReportFilters(BaseModel):
    city: str | None
    time_from: datetime | None
    time_to: datetime | None
    status: str | None
    error_code: str | None
    result_type: str | None
    detailed_reason: str | None


class AdminTripTrendPoint(BaseModel):
    date: date
    terminal_count: int
    status_distribution: dict[str, int]


class AdminDurationSummary(BaseModel):
    p50: float | None
    p95: float | None


class AdminDurationBreakdown(BaseModel):
    total: AdminDurationSummary
    stages: dict[str, AdminDurationSummary]


class AdminSlowSummary(BaseModel):
    count: int
    rate: AdminRatio


class AdminTripGenerationReportResponse(AdminSuccessResponse):
    as_of: datetime
    filters: AdminTripReportFilters
    terminal_count: int
    terminal_trend: list[AdminTripTrendPoint]
    status_distribution: dict[str, int]
    terminal_success_rate: AdminRatio
    valid_guide_rate: AdminRatio
    no_candidates_rate: AdminRatio
    no_usable_route_rate: AdminRatio
    duration_ms: AdminDurationBreakdown
    slow_tasks: AdminSlowSummary
    freshness: Freshness
    projection_alarm: ProjectionAlarm | None
    error_distribution: dict[str, int]
    detailed_reason_distribution: dict[str, int]
    result_type_distribution: dict[str, int]


class AdminPreferenceFilters(BaseModel):
    city: str | None
    time_from: datetime | None
    time_to: datetime | None


class AdminPreferenceValue(BaseModel):
    value: str
    request_count: int
    request_share: AdminRatio


class AdminPreferenceReportResponse(AdminSuccessResponse):
    as_of: datetime
    filters: AdminPreferenceFilters
    request_count: int
    identified_distinct_user_count: int
    fields: dict[str, list[AdminPreferenceValue]]


class AdminAuditEvent(BaseModel):
    audit_id: str
    actor_user_id: str | None
    actor_identity: str
    action: str
    target_type: str
    target_id: str | None
    result: Literal["SUCCESS", "FAILURE"]
    error_code: str | None
    reason: str | None
    request_id: str | None
    created_at: datetime


class AdminAuditFilters(BaseModel):
    action: str | None
    result: Literal["SUCCESS", "FAILURE"] | None
    error_code: str | None
    time_from: datetime | None
    time_to: datetime | None


class AdminAuditEventListResponse(AdminSuccessResponse):
    items: list[AdminAuditEvent]
    page: int
    limit: int
    total: int
    filters: AdminAuditFilters


class AdminTripJobProjection(HermesAdminTripJob):
    slow: bool
    is_exception: bool
    exception_kind: Literal["SLOW", "TERMINAL_FAILURE", "DEGRADED", "CITY_OPERATIONAL"] | None = (
        None
    )


class AdminTripJobListResponse(BaseModel):
    ok: Literal[True]
    request_id: str
    page: int
    limit: int
    total: int
    items: list[AdminTripJobProjection]


class AdminTripJobDetailResponse(BaseModel):
    ok: Literal[True]
    request_id: str
    trip_job: AdminTripJobProjection


class AdminFailedDraftProjection(HermesAdminFailedDraft):
    publication_status: Literal["UNPUBLISHED_DIAGNOSTIC"]


class AdminFailedDraftResponse(BaseModel):
    ok: Literal[True]
    request_id: str
    failed_draft: AdminFailedDraftProjection


class AdminArtifactListResponse(BaseModel):
    ok: Literal[True]
    request_id: str
    page: int
    limit: int
    total: int
    items: list[HermesAdminArtifact]


class AdminArtifactDetailResponse(BaseModel):
    ok: Literal[True]
    request_id: str
    artifact: HermesAdminArtifact
