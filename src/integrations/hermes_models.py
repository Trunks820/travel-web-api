from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HermesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HermesTripCreated(HermesModel):
    job_id: str = Field(min_length=1, max_length=160)
    status: str = "PENDING"
    current_stage: str | None = Field(default=None, max_length=120)
    queue_position: int | None = Field(default=None, ge=0)
    message: str | None = Field(default=None, max_length=500)
    cached: bool | None = None

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"PENDING", "RUNNING", "SUCCESS"}:
            raise ValueError("unsupported creation status")
        return normalized


class HermesJobStatus(HermesModel):
    ok: bool = True
    job_id: str = Field(min_length=1, max_length=160)
    status: str
    current_stage: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=500)
    queue_position: int | None = Field(default=None, ge=0)
    error_message: str | None = Field(default=None, max_length=500)
    error_code: str | None = Field(default=None, max_length=80)
    city_notice_code: str | None = Field(default=None, max_length=80)
    city_status: str | None = Field(default=None, max_length=80)
    city_batch_status: str | None = Field(default=None, max_length=80)
    city_batch_error_code: str | None = Field(default=None, max_length=80)
    plan_count: int | None = Field(default=None, ge=0)
    result_type: str | None = Field(default=None, max_length=80)
    result_record_id: int | None = Field(default=None, ge=1)
    elapsed_ms: int | None = Field(default=None, ge=0)
    queue_wait_ms: int | None = Field(default=None, ge=0)
    run_elapsed_ms: int | None = Field(default=None, ge=0)
    total_elapsed_ms: int | None = Field(default=None, ge=0)
    created_time: str | None = Field(default=None, max_length=64)
    updated_time: str | None = Field(default=None, max_length=64)

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {
            "SUBMITTING",
            "PENDING",
            "RUNNING",
            "SUCCESS",
            "FAILED",
            "TIMEOUT",
            "REJECTED",
        }:
            raise ValueError("unsupported job status")
        return normalized


class HermesAdminEnvelope(HermesModel):
    ok: Literal[True]
    contract_version: Literal["v1"]
    request_id: str = Field(min_length=1, max_length=120)


class HermesAdminSafeError(HermesModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class HermesAdminTripStep(HermesModel):
    stage: str = Field(min_length=1, max_length=120)
    status: str = Field(min_length=1, max_length=32)
    attempt: int = Field(ge=1)
    publish_retry_round: int = Field(ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class HermesAdminTripJob(HermesModel):
    job_id: str = Field(min_length=1, max_length=160)
    result_record_id: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "REJECTED"]
    current_stage: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    result_type: Literal["PLAN_READY", "NO_CANDIDATES", "NO_USABLE_ROUTE"] | None = None
    safe_error: HermesAdminSafeError | None = None
    detailed_reason: str | None = Field(default=None, max_length=80)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    failed_draft_available: bool
    steps: list[HermesAdminTripStep] | None = Field(default=None, max_length=200)


class HermesAdminTripJobList(HermesAdminEnvelope):
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    items: list[HermesAdminTripJob] = Field(max_length=100)


class HermesAdminTripJobDetail(HermesAdminEnvelope):
    trip_job: HermesAdminTripJob


class HermesProjectionJobPayload(HermesModel):
    source_id: int = Field(ge=1)
    job_id: str = Field(min_length=1, max_length=160)
    source_version: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    days: int | None = Field(default=None, ge=1, le=30)
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "REJECTED"]
    current_stage: str | None = Field(default=None, max_length=120)
    result_type: Literal["PLAN_READY", "NO_CANDIDATES", "NO_USABLE_ROUTE", "UNKNOWN"] | None
    result_record_id: int | None = Field(default=None, ge=1)
    guide_result_state: Literal["NOT_APPLICABLE", "LEGAL_NO_GUIDE", "AVAILABLE", "INCONSISTENT"]
    error_code: str | None = Field(default=None, max_length=80)
    safe_error: HermesAdminSafeError | None
    detailed_reason: str | None = Field(default=None, max_length=80)
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    retry_count: int = Field(ge=0)
    failed_draft_available: bool
    trace_completeness: Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
    source_updated_at: datetime


class HermesProjectionStepPayload(HermesModel):
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


class HermesProjectionJobPage(HermesAdminEnvelope):
    snapshot_max_id: int = Field(ge=0)
    next_after_id: int | None = Field(default=None, ge=1)
    has_more: bool
    items: list[HermesProjectionJobPayload] = Field(max_length=1000)


class HermesProjectionStepPage(HermesAdminEnvelope):
    snapshot_max_id: int = Field(ge=0)
    next_after_id: int | None = Field(default=None, ge=1)
    has_more: bool
    items: list[HermesProjectionStepPayload] = Field(max_length=1000)


class HermesAdminFailedDraftPlan(HermesModel):
    plan_name: str = Field(max_length=300)
    summary: str = Field(max_length=10_000)
    plan_text: str = Field(min_length=1, max_length=200_000)
    used_place_names: list[str] = Field(default_factory=list, max_length=500)
    day_place_names: list[list[str]] = Field(default_factory=list, max_length=60)


class HermesAdminFailedDraft(HermesModel):
    job_id: str = Field(min_length=1, max_length=160)
    created_at: datetime
    plans: list[HermesAdminFailedDraftPlan] = Field(min_length=1, max_length=20)


class HermesAdminFailedDraftDetail(HermesAdminEnvelope):
    failed_draft: HermesAdminFailedDraft


class HermesAdminArtifact(HermesModel):
    artifact_id: str = Field(min_length=1, max_length=160)
    result_record_id: str = Field(min_length=1, max_length=160)
    artifact_type: Literal["pdf", "share_image"]
    status: Literal["PENDING", "RUNNING", "READY", "FAILED", "EXPIRED"]
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    byte_size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, max_length=128)
    text_length: int | None = Field(default=None, ge=0)
    width_px: int | None = Field(default=None, ge=0)
    height_px: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(ge=0)
    safe_error: HermesAdminSafeError | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None


class HermesAdminArtifactList(HermesAdminEnvelope):
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    items: list[HermesAdminArtifact] = Field(max_length=100)


class HermesAdminArtifactDetail(HermesAdminEnvelope):
    artifact: HermesAdminArtifact


class ResultCity(HermesModel):
    name: str = Field(max_length=120)


class ResultRequest(HermesModel):
    days: int = Field(ge=1, le=30)
    people_count: int = Field(ge=1, le=30)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    avoid: list[str] = Field(default_factory=list, max_length=20)


class ResultWeatherDay(HermesModel):
    day: int = Field(ge=1, le=30)
    date: str = Field(max_length=32)
    weather_text: str = Field(max_length=160)
    temp_min_c: float
    temp_max_c: float
    wind_text: str = Field(max_length=160)
    icon_code: str = Field(max_length=80)
    reminders: list[str] = Field(default_factory=list, max_length=20)


class ResultWeather(HermesModel):
    status: str = Field(max_length=80)
    city: str = Field(max_length=120)
    days: list[ResultWeatherDay] = Field(default_factory=list, max_length=30)


class ResultTimePreferences(HermesModel):
    daily_start: str | None = Field(default=None, max_length=16)
    daily_end: str | None = Field(default=None, max_length=16)
    rest_windows: list[ResultRestWindow] = Field(default_factory=list, max_length=30)


class ResultRestWindow(HermesModel):
    days: str = Field(max_length=80)
    start: str = Field(max_length=16)
    end: str = Field(max_length=16)


class ResultSchedule(HermesModel):
    period: Literal["morning", "afternoon", "evening", "night"]
    exact_start: str | None = Field(default=None, max_length=16)
    exact_end: str | None = Field(default=None, max_length=16)
    exact_time_source: (
        Literal[
            "reservation",
            "event",
            "transport",
            "verified_venue_rule",
        ]
        | None
    ) = None


class ResultPlace(HermesModel):
    place_id: int = Field(ge=1)
    name: str = Field(max_length=160)
    category: str = Field(max_length=80)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    role: str = Field(max_length=80)
    optional: bool
    brief: str = Field(max_length=2_000)
    stay_minutes: int | None = Field(default=None, ge=0, le=1_440)
    activity_note: str | None = Field(default=None, max_length=2_000)
    schedule: ResultSchedule | None = None


class ResultTransitStep(HermesModel):
    kind: Literal["walking", "bus", "rail", "other"]
    duration_minutes: int | None = Field(default=None, ge=0)
    distance_meters: int | None = Field(default=None, ge=0)
    line_name: str | None = Field(default=None, max_length=160)
    provider_type: str | None = Field(default=None, max_length=160)
    from_stop: str | None = Field(default=None, max_length=160)
    to_stop: str | None = Field(default=None, max_length=160)
    stop_count: int | None = Field(default=None, ge=0)


class ResultCommuteLeg(HermesModel):
    from_place_id: int = Field(ge=1)
    to_place_id: int = Field(ge=1)
    mode: Literal["driving", "transit", "walking", "cycling"]
    duration_source: Literal["amap", "estimate"] | None = None
    duration_minutes: int = Field(ge=0)
    distance_meters: int = Field(ge=0)
    encoded_polyline: str | None = Field(default=None, max_length=100_000)
    transit_steps: list[ResultTransitStep] = Field(default_factory=list, max_length=100)
    transit_summary: str | None = Field(default=None, max_length=1_000)


class ResultDay(HermesModel):
    day: int = Field(ge=1, le=30)
    title: str = Field(max_length=300)
    places: list[ResultPlace] = Field(default_factory=list, max_length=100)
    commute_legs: list[ResultCommuteLeg] = Field(default_factory=list, max_length=100)
    commute_summary: str = Field(max_length=2_000)
    pace_status: Literal["WITHIN_LIMIT", "OVER_LIMIT"]
    narrative: str = Field(max_length=20_000)


class ResultPace(HermesModel):
    level: Literal["RELAXED", "MODERATE", "INTENSIVE"]
    commute_status: Literal["WITHIN_LIMIT", "OVER_LIMIT"]
    total_commute_minutes: int = Field(ge=0)


class ResultAccommodation(HermesModel):
    name: str = Field(max_length=160)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    source: Literal["user_specified", "auto_recommended"]
    reason: str | None = Field(default=None, max_length=1_000)


class ResultTransportOption(HermesModel):
    type: Literal["train", "flight"]
    no: str = Field(max_length=80)
    departure_time: str = Field(max_length=64)
    arrival_time: str = Field(max_length=64)
    duration_minutes: int = Field(ge=0)
    price: str | None = Field(default=None, max_length=80)
    departure_station: str | None = Field(default=None, max_length=160)
    arrival_station: str | None = Field(default=None, max_length=160)
    airline: str | None = Field(default=None, max_length=160)


class ResultTransportMode(HermesModel):
    mode: Literal["train", "flight"]
    min_duration_minutes: int = Field(ge=0)
    price_range: str = Field(max_length=160)
    price_source: Literal["realtime", "static_reference"]
    daily_count: int = Field(ge=0)
    data_source: Literal["realtime", "static_fallback"]
    availability_status: Literal[
        "available_at_query",
        "sold_out_at_query",
        "unknown",
    ] = "unknown"
    availability_checked_at: str | None = Field(default=None, max_length=64)
    options: list[ResultTransportOption] = Field(default_factory=list, max_length=200)


class ResultTransport(HermesModel):
    from_city: str = Field(max_length=120)
    to_city: str = Field(max_length=120)
    query_date: str | None = Field(default=None, max_length=32)
    source: Literal["realtime", "mixed", "static_fallback"]
    modes: list[ResultTransportMode] = Field(default_factory=list, max_length=10)


CostCategory = Literal[
    "intercity_transport",
    "accommodation",
    "local_transport",
    "admission",
    "meals",
]
COST_CATEGORY_ORDER: tuple[CostCategory, ...] = (
    "intercity_transport",
    "accommodation",
    "local_transport",
    "admission",
    "meals",
)
CostCompleteness = Literal["complete", "partial", "unavailable"]
CostCoverage = Literal["priced", "missing"]
CostPriceBasis = Literal["sourced", "reference", "mixed", "policy_zero"]
CostTotalScope = Literal["full_trip", "estimated_subset", "unavailable"]
CostScenarioId = Literal["train_round_trip", "flight_round_trip", "without_intercity"]
CostIntercityMode = Literal["train", "flight"]
CostAssumptionCode = Literal[
    "two_travellers_per_room",
    "itinerary_days_minus_one_nights",
    "four_travellers_per_taxi",
    "adult_full_fare",
    "two_main_meals_per_day",
]
CostExclusionCode = Literal["cycling_cost_not_included"]
MoneyCny = Annotated[int, Field(strict=True, ge=0, multiple_of=10)]


class CostMoneyRange(HermesModel):
    min_cny: MoneyCny
    max_cny: MoneyCny

    @model_validator(mode="after")
    def validate_order(self) -> CostMoneyRange:
        if self.min_cny > self.max_cny:
            raise ValueError("min_cny must be <= max_cny")
        return self


class CostCategorySummary(HermesModel):
    category: CostCategory
    coverage: CostCoverage
    range: CostMoneyRange | None = None
    price_basis: CostPriceBasis | None = None
    basis_label: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_coverage(self) -> CostCategorySummary:
        if self.coverage == "priced":
            if self.range is None or self.price_basis is None:
                raise ValueError("priced category requires range and price_basis")
        elif self.range is not None or self.price_basis is not None:
            raise ValueError("missing category cannot carry monetary fields")
        return self


class CostScenarioSummary(HermesModel):
    scenario_id: CostScenarioId
    intercity_mode: CostIntercityMode | None = None
    label: str = Field(min_length=1, max_length=500)
    total_scope: CostTotalScope
    total_range: CostMoneyRange | None = None
    categories: list[CostCategorySummary] = Field(min_length=5, max_length=5)
    missing_categories: list[CostCategory] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_scenario(self) -> CostScenarioSummary:
        expected_mode = {
            "train_round_trip": "train",
            "flight_round_trip": "flight",
            "without_intercity": None,
        }[self.scenario_id]
        if self.intercity_mode != expected_mode:
            raise ValueError("scenario id and intercity mode disagree")
        if tuple(item.category for item in self.categories) != COST_CATEGORY_ORDER:
            raise ValueError("cost categories must use the frozen order")
        missing = [item.category for item in self.categories if item.coverage == "missing"]
        if missing != self.missing_categories:
            raise ValueError("missing_categories must match category coverage")
        priced = [item for item in self.categories if item.coverage == "priced"]
        expected_scope: CostTotalScope = (
            "full_trip" if not missing else "estimated_subset" if priced else "unavailable"
        )
        if self.total_scope != expected_scope:
            raise ValueError("total_scope disagrees with category coverage")
        if expected_scope == "unavailable":
            if self.total_range is not None:
                raise ValueError("unavailable scenario cannot carry total_range")
        else:
            if self.total_range is None:
                raise ValueError("priced scenario requires total_range")
            visible_min = sum(item.range.min_cny for item in priced if item.range)
            visible_max = sum(item.range.max_cny for item in priced if item.range)
            if self.total_range.min_cny != visible_min or self.total_range.max_cny != visible_max:
                raise ValueError("cost total must reconcile to category ranges")
        if self.scenario_id == "without_intercity" and (
            self.categories[0].category != "intercity_transport"
            or self.categories[0].coverage != "missing"
        ):
            raise ValueError("without_intercity must report missing intercity")
        return self


class CostAssumptionSummary(HermesModel):
    code: CostAssumptionCode
    label: str = Field(min_length=1, max_length=500)


class CostExclusionSummary(HermesModel):
    code: CostExclusionCode
    label: str = Field(min_length=1, max_length=500)


class CostEstimateSummary(HermesModel):
    snapshot_version: Literal["1"]
    completeness: CostCompleteness
    currency: Literal["CNY"]
    estimated_at: datetime
    scenarios: list[CostScenarioSummary] = Field(min_length=1, max_length=3)
    assumptions: list[CostAssumptionSummary] = Field(default_factory=list, max_length=20)
    exclusions: list[CostExclusionSummary] = Field(default_factory=list, max_length=20)
    notice: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_summary(self) -> CostEstimateSummary:
        if self.estimated_at.tzinfo is None or self.estimated_at.utcoffset() != timedelta(0):
            raise ValueError("estimated_at must use UTC")
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("scenario ids must be unique")
        if "without_intercity" in scenario_ids and scenario_ids != ["without_intercity"]:
            raise ValueError("without_intercity must be the sole scenario")
        state_by_scope: dict[CostTotalScope, CostCompleteness] = {
            "full_trip": "complete",
            "estimated_subset": "partial",
            "unavailable": "unavailable",
        }
        rank = {"complete": 0, "partial": 1, "unavailable": 2}
        worst = max(
            (state_by_scope[item.total_scope] for item in self.scenarios),
            key=rank.__getitem__,
        )
        if self.completeness != worst:
            raise ValueError("completeness must equal the worst scenario")
        return self


class ResultPlan(HermesModel):
    plan_id: str = Field(max_length=160)
    title: str = Field(max_length=300)
    summary: str = Field(max_length=3_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    pace: ResultPace
    accommodation: ResultAccommodation | None = None
    transport: ResultTransport | None = None
    days: list[ResultDay] = Field(default_factory=list, max_length=30)
    cost_estimate: CostEstimateSummary


class ResultMustInclude(HermesModel):
    name: str = Field(max_length=160)
    status: Literal[
        "scheduled",
        "not_scheduled",
        "recorded_candidate",
        "recorded_unmatched",
        "cross_city",
    ]
    place_id: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=1_000)
    matched_city: str | None = Field(default=None, max_length=120)
    avoid_conflict: bool | None = None


class HermesResult(HermesModel):
    schema_version: Literal["2.0"]
    result_id: int = Field(ge=1)
    city: ResultCity
    request: ResultRequest
    weather: ResultWeather
    time_preferences: ResultTimePreferences | None = None
    plans: list[ResultPlan] = Field(min_length=1, max_length=20)
    must_include: list[ResultMustInclude] | None = Field(default=None, max_length=20)


class HermesStructuredRequest(HermesModel):
    values: dict[str, Any]
    field_provenance: dict[str, Literal["USER_SUPPLIED"]]


class HermesInternalGuideResult(HermesAdminEnvelope):
    job_id: str = Field(min_length=1, max_length=160)
    guide_result_state: Literal["AVAILABLE"]
    result_type: Literal["PLAN_READY"]
    result_record_id: int = Field(ge=1)
    request: HermesStructuredRequest | None
    final_guide: HermesResult
    artifacts: list[HermesAdminArtifact] = Field(max_length=100)


class HermesArtifactError(HermesModel):
    code: str = Field(max_length=80)
    message: str = Field(max_length=500)


class HermesArtifact(HermesModel):
    ok: bool
    artifact_id: str | None = Field(default=None, max_length=160)
    result_record_id: int = Field(ge=1)
    artifact_type: Literal["pdf", "share_image"]
    status: str | None = Field(default=None, max_length=32)
    download_url: str | None = Field(default=None, max_length=1_000)
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    byte_size: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    width_px: int | None = Field(default=None, ge=0)
    height_px: int | None = Field(default=None, ge=0)
    expires_time: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: HermesArtifactError | None = None


class HermesPlaceListItem(HermesModel):
    place_id: int = Field(ge=1)
    name: str = Field(max_length=160)
    place_type: str = Field(max_length=80)
    district: str = Field(default="", max_length=160)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    summary: str = Field(default="", max_length=500)
    mention_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)


class HermesPlaceList(HermesModel):
    ok: bool = True
    city: str = Field(max_length=120)
    places: list[HermesPlaceListItem] = Field(default_factory=list, max_length=50)


class HermesPlaceDetail(HermesPlaceListItem):
    top_reasons: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class HermesSseError(HermesModel):
    code: str = Field(max_length=80)
    message: str = Field(max_length=500)
    retryable: bool = False


class HermesSsePayload(HermesModel):
    status: str
    job_id: str | None = Field(default=None, max_length=160)
    current_stage: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=500)
    queue_position: int | None = Field(default=None, ge=0)
    result_record_id: int | None = Field(default=None, ge=1)
    plan_count: int | None = Field(default=None, ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0)
    error: HermesSseError | None = None

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {
            "PENDING",
            "RUNNING",
            "SUCCESS",
            "FAILED",
            "TIMEOUT",
            "REJECTED",
        }:
            raise ValueError("unsupported SSE status")
        return normalized


def validate_sse_payload(
    event: str,
    payload: dict[str, Any],
) -> tuple[Literal["progress", "complete", "failed"], HermesSsePayload]:
    if event not in {"progress", "complete", "failed"}:
        raise ValueError("unsupported SSE event")
    model = HermesSsePayload.model_validate(payload)
    if event == "progress" and model.status not in {"PENDING", "RUNNING"}:
        raise ValueError("invalid progress status")
    if event == "complete" and (model.status != "SUCCESS" or model.result_record_id is None):
        raise ValueError("invalid complete event")
    if event == "failed" and model.status not in {"FAILED", "TIMEOUT", "REJECTED"}:
        raise ValueError("invalid failed event")
    return event, model
