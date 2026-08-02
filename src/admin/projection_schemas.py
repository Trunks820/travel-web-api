from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.integrations.hermes_models import HermesResult


class ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SafeError(ProjectionModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class LinkedAssociation(ProjectionModel):
    state: Literal["linked"]
    user_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)


class DeidentifiedAssociation(ProjectionModel):
    state: Literal["de-identified"]


class UnlinkedAssociation(ProjectionModel):
    state: Literal["unlinked"]


Association = Annotated[
    LinkedAssociation | DeidentifiedAssociation | UnlinkedAssociation,
    Field(discriminator="state"),
]
ProjectionState = Literal["FRESH", "LAGGING", "DELAYED"]
TripJobStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "REJECTED"]
ResultType = Literal["PLAN_READY", "NO_CANDIDATES", "NO_USABLE_ROUTE", "UNKNOWN"]
GuideResultState = Literal["NOT_APPLICABLE", "LEGAL_NO_GUIDE", "AVAILABLE", "INCONSISTENT"]
TraceCompleteness = Literal["COMPLETE", "PARTIAL", "UNKNOWN"]


class Freshness(ProjectionModel):
    data_as_of: datetime | None
    sync_checked_at: datetime
    sync_lag_seconds: float = Field(ge=0)
    source_high_watermark: int = Field(ge=0)
    applied_high_watermark: int = Field(ge=0)
    projection_state: ProjectionState


class ProjectionAlarm(ProjectionModel):
    code: Literal["PROJECTION_SYNC_STALLED"]
    message: str = Field(min_length=1, max_length=500)
    retryable: Literal[True]


class TripJobSummary(ProjectionModel):
    job_id: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    days: int | None = Field(default=None, ge=1, le=30)
    status: TripJobStatus
    current_stage: str | None = Field(default=None, max_length=120)
    result_type: ResultType | None
    result_record_id: int | None = Field(default=None, ge=1)
    guide_result_state: GuideResultState
    has_final_guide: bool
    safe_error: SafeError | None
    detailed_reason: str | None = Field(default=None, max_length=80)
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total_duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    failed_draft_available: bool
    is_slow: bool
    timeout_settlement_anomaly: bool
    trace_completeness: TraceCompleteness
    association: Association

    @model_validator(mode="after")
    def validate_guide_state(self) -> TripJobSummary:
        if self.guide_result_state == "AVAILABLE":
            if not (
                self.status == "SUCCESS"
                and self.result_type == "PLAN_READY"
                and self.result_record_id is not None
                and self.has_final_guide
            ):
                raise ValueError("AVAILABLE guide facts are inconsistent")
        elif self.guide_result_state == "LEGAL_NO_GUIDE":
            if not (
                self.status == "SUCCESS"
                and self.result_type in {"NO_CANDIDATES", "NO_USABLE_ROUTE"}
                and self.result_record_id is None
                and not self.has_final_guide
            ):
                raise ValueError("LEGAL_NO_GUIDE facts are inconsistent")
        elif self.guide_result_state == "NOT_APPLICABLE":
            if not (
                self.status != "SUCCESS"
                and self.result_type is None
                and self.result_record_id is None
                and not self.has_final_guide
            ):
                raise ValueError("NOT_APPLICABLE facts are inconsistent")
        elif self.has_final_guide:
            raise ValueError("only AVAILABLE may expose a final guide")
        return self


class TripStep(ProjectionModel):
    source_step_id: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=120)
    stage_label_zh: str = Field(min_length=1, max_length=120)
    status: Literal["RUNNING", "SUCCESS", "FAILED", "TIMEOUT"]
    attempt: int = Field(ge=1)
    publish_retry_round: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)


class ArtifactReference(ProjectionModel):
    artifact_id: str = Field(min_length=1, max_length=160)
    artifact_type: Literal["pdf", "share_image"]
    status: Literal["PENDING", "RUNNING", "READY", "FAILED", "EXPIRED"]
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    byte_size: int | None = Field(default=None, ge=0)
    created_at: datetime
    expires_at: datetime | None


class ProjectionSuccess(ProjectionModel):
    ok: Literal[True]
    request_id: str = Field(min_length=1, max_length=120)
    as_of: datetime
    freshness: Freshness
    projection_alarm: ProjectionAlarm | None

    @model_validator(mode="after")
    def validate_alarm_coupling(self) -> ProjectionSuccess:
        if self.freshness.projection_state == "DELAYED":
            if self.projection_alarm is None:
                raise ValueError("DELAYED requires PROJECTION_SYNC_STALLED")
        elif self.projection_alarm is not None:
            raise ValueError("FRESH/LAGGING require a null projection alarm")
        return self


class AdminTripJobListResponse(ProjectionSuccess):
    page: int = Field(ge=1)
    limit: Literal[10, 20, 50, 100]
    total: int = Field(ge=0)
    items: list[TripJobSummary] = Field(max_length=100)


class AdminTripJobDetailResponse(ProjectionSuccess):
    trip_job: TripJobSummary
    steps: list[TripStep] = Field(max_length=500)


class AdminUserTripListResponse(ProjectionSuccess):
    page: int = Field(ge=1)
    limit: Literal[10, 20]
    total: int = Field(ge=0)
    items: list[TripJobSummary] = Field(max_length=20)


class RuntimePolicy(ProjectionModel):
    slow_after_seconds: Literal[90]
    timeout_after_seconds: Literal[120]
    stale_sweep_seconds: Literal[30]


class PipelineOverview(ProjectionModel):
    created_task_count: int = Field(ge=0)
    terminal_task_count: int = Field(ge=0)
    terminal_success_count: int = Field(ge=0)
    published_guide_count: int = Field(ge=0)
    no_guide_success_count: int = Field(ge=0)
    terminal_failure_count: int = Field(ge=0)
    backlog_task_count: int = Field(ge=0)
    slow_task_count: int = Field(ge=0)
    timeout_settlement_anomaly_count: int = Field(ge=0)
    terminal_success_rate: float | None = Field(default=None, ge=0, le=1)
    published_guide_rate: float | None = Field(default=None, ge=0, le=1)
    excluded_trace_task_count: int = Field(ge=0)


class DurationPercentiles(ProjectionModel):
    p50: int | None = Field(default=None, ge=0)
    p95: int | None = Field(default=None, ge=0)


class PipelineNode(ProjectionModel):
    stage: str = Field(min_length=1, max_length=120)
    stage_label_zh: str = Field(min_length=1, max_length=120)
    task_count: int = Field(ge=0)
    execution_count: int = Field(ge=0)
    running_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    excluded_trace_task_count: int = Field(ge=0)
    ended_count: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=1)
    duration_ms: DurationPercentiles


class PipelineWindow(ProjectionModel):
    from_: datetime = Field(alias="from")
    to: datetime


class AdminGenerationPipelineResponse(ProjectionSuccess):
    window: PipelineWindow
    runtime_policy: RuntimePolicy
    overview: PipelineOverview
    nodes: list[PipelineNode] = Field(max_length=30)


class MustIncludeInput(ProjectionModel):
    name: str = Field(min_length=1, max_length=120)
    place_id: int | None = Field(default=None, ge=1)


class RestWindowInput(ProjectionModel):
    start: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    label: str | None = Field(default=None, max_length=80)


class AccommodationInput(ProjectionModel):
    name: str = Field(min_length=1, max_length=160)
    place_id: int | None = Field(default=None, ge=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class StructuredRequestValues(ProjectionModel):
    from_city: str | None = Field(default=None, max_length=120)
    to_city: str | None = Field(default=None, min_length=1, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    people_count: int | None = Field(default=None, ge=1, le=30)
    preferences: list[str] | None = Field(default=None, max_length=20)
    avoid: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
    budget: int | None = Field(default=None, ge=0, le=10_000_000)
    must_include: list[MustIncludeInput] | None = Field(default=None, max_length=5)
    commute_mode: Literal["driving", "transit", "cycling"] | None = None
    daily_start: str | None = Field(
        default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    daily_end: str | None = Field(
        default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    rest_windows: list[RestWindowInput] | None = Field(default=None, max_length=2)
    accommodation: AccommodationInput | None = None


class StructuredRequest(ProjectionModel):
    values: StructuredRequestValues
    field_provenance: dict[str, Literal["USER_SUPPLIED"]]

    @model_validator(mode="after")
    def validate_matching_keys(self) -> StructuredRequest:
        value_keys = set(self.values.model_dump(exclude_none=True))
        if not value_keys or value_keys != set(self.field_provenance):
            raise ValueError("request values and provenance keys must match")
        return self


class AdminGuideReviewResponse(ProjectionModel):
    ok: Literal[True]
    request_id: str = Field(min_length=1, max_length=120)
    as_of: datetime
    trip_job: TripJobSummary
    request: StructuredRequest | None
    request_source: Literal["BFF_USER_TRIP", "HERMES_CANONICAL", "UNAVAILABLE"]
    final_guide: HermesResult
    artifacts: list[ArtifactReference] = Field(max_length=100)
    freshness: Freshness
    projection_alarm: None = None

    @model_validator(mode="after")
    def validate_sensitive_cutoff(self) -> AdminGuideReviewResponse:
        if self.freshness.projection_state == "DELAYED":
            raise ValueError("guide review is unavailable while projection is DELAYED")
        if (self.request_source == "UNAVAILABLE") != (self.request is None):
            raise ValueError("request source and payload do not agree")
        return self


class JobProjectionPayload(ProjectionModel):
    source_id: int = Field(ge=1)
    job_id: str = Field(min_length=1, max_length=160)
    source_version: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    days: int | None = Field(default=None, ge=1, le=30)
    status: TripJobStatus
    current_stage: str | None = Field(default=None, max_length=120)
    result_type: ResultType | None
    result_record_id: int | None = Field(default=None, ge=1)
    guide_result_state: GuideResultState
    error_code: str | None = Field(default=None, max_length=80)
    safe_error: SafeError | None
    detailed_reason: str | None = Field(default=None, max_length=80)
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    retry_count: int = Field(ge=0)
    failed_draft_available: bool
    trace_completeness: TraceCompleteness
    source_updated_at: datetime

    @model_validator(mode="after")
    def validate_safe_error(self) -> JobProjectionPayload:
        if (self.error_code is None) != (self.safe_error is None):
            raise ValueError("safe_error must be present exactly with error_code")
        if self.safe_error is not None and self.safe_error.code != self.error_code:
            raise ValueError("safe_error code must match error_code")
        if self.guide_result_state == "AVAILABLE" and not (
            self.status == "SUCCESS"
            and self.result_type == "PLAN_READY"
            and self.result_record_id is not None
        ):
            raise ValueError("AVAILABLE projection facts are inconsistent")
        if self.guide_result_state == "LEGAL_NO_GUIDE" and not (
            self.status == "SUCCESS"
            and self.result_type in {"NO_CANDIDATES", "NO_USABLE_ROUTE"}
            and self.result_record_id is None
        ):
            raise ValueError("LEGAL_NO_GUIDE projection facts are inconsistent")
        if self.guide_result_state == "NOT_APPLICABLE" and not (
            self.status != "SUCCESS"
            and self.result_type is None
            and self.result_record_id is None
        ):
            raise ValueError("NOT_APPLICABLE projection facts are inconsistent")
        return self


class StepProjectionPayload(ProjectionModel):
    source_step_id: int = Field(ge=1)
    job_id: str = Field(min_length=1, max_length=160)
    source_version: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=120)
    status: Literal["RUNNING", "SUCCESS", "FAILED", "TIMEOUT"]
    attempt: int = Field(ge=1)
    publish_retry_round: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None = Field(default=None, ge=0)
    source_updated_at: datetime


class TripProjectionCommitPayload(ProjectionModel):
    job: JobProjectionPayload
    changed_steps: list[StepProjectionPayload] = Field(max_length=500)


class TripProjectionCommitEvent(ProjectionModel):
    event_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    event_type: Literal["TRIP_PROJECTION_COMMITTED"]
    schema_version: Literal["1.0"]
    outbox_sequence: int = Field(ge=1)
    aggregate_type: Literal["TRIP_JOB"]
    aggregate_id: str = Field(min_length=1, max_length=160)
    aggregate_version: int = Field(ge=1)
    occurred_at: datetime
    payload: TripProjectionCommitPayload

    @model_validator(mode="after")
    def validate_composite_identity(self) -> TripProjectionCommitEvent:
        job = self.payload.job
        if self.aggregate_id != job.job_id or self.aggregate_version != job.source_version:
            raise ValueError("aggregate and job identities must match")
        if any(step.job_id != job.job_id for step in self.payload.changed_steps):
            raise ValueError("every changed step must belong to the aggregate job")
        return self


class ProjectionHeartbeat(ProjectionModel):
    event_type: Literal["PROJECTION_HEARTBEAT"]
    schema_version: Literal["1.0"]
    observed_at: datetime
    outbox_high_watermark: int = Field(ge=0)


class SnapshotEnvelope(ProjectionModel):
    ok: Literal[True]
    contract_version: Literal["v1"]
    request_id: str = Field(min_length=1, max_length=120)
    snapshot_max_id: int = Field(ge=0)
    next_after_id: int | None = Field(default=None, ge=1)
    has_more: bool


class TripJobSnapshotPage(SnapshotEnvelope):
    items: list[JobProjectionPayload] = Field(max_length=1000)


class TripStepSnapshotPage(SnapshotEnvelope):
    items: list[StepProjectionPayload] = Field(max_length=1000)


class InternalGuideResultResponse(ProjectionModel):
    ok: Literal[True]
    contract_version: Literal["v1"]
    request_id: str = Field(min_length=1, max_length=120)
    job_id: str = Field(min_length=1, max_length=160)
    guide_result_state: Literal["AVAILABLE"]
    result_type: Literal["PLAN_READY"]
    result_record_id: int = Field(ge=1)
    request: StructuredRequest | None
    final_guide: HermesResult
    artifacts: list[ArtifactReference] = Field(max_length=100)
