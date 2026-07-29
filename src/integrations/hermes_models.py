from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ResultPlan(HermesModel):
    plan_id: str = Field(max_length=160)
    title: str = Field(max_length=300)
    summary: str = Field(max_length=3_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    pace: ResultPace
    accommodation: ResultAccommodation | None = None
    days: list[ResultDay] = Field(default_factory=list, max_length=30)


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


class ResultCommuteModeReport(HermesModel):
    requested: Literal["driving", "transit", "walking", "cycling"]
    effective: Literal["driving", "transit", "walking", "cycling"]
    effective_reason: Literal["requested", "feature_disabled"]
    degraded_leg_count: int = Field(default=0, ge=0)
    rationalized_leg_count: int = Field(default=0, ge=0)
    duration_source: Literal["amap", "mixed", "estimated"] = "amap"


class HermesResult(HermesModel):
    schema_version: str = Field(max_length=20)
    result_id: int = Field(ge=1)
    city: ResultCity
    request: ResultRequest
    weather: ResultWeather | None
    time_preferences: ResultTimePreferences | None = None
    plans: list[ResultPlan] = Field(max_length=20)
    must_include: list[ResultMustInclude] | None = Field(default=None, max_length=20)
    commute_mode_report: ResultCommuteModeReport | None = None


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
