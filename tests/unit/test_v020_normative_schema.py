from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from src.app import create_app

FROZEN_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "travel-admin"
    / "docs"
    / "v0.2.0-operational-trace-schema.json"
)
HERMES_RESULT_URN = "urn:yuntu:travel-web-api:openapi:HermesResult"


def _frozen_schema() -> dict:
    return json.loads(FROZEN_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator(definition: str) -> Draft202012Validator:
    frozen = _frozen_schema()
    openapi = create_app().openapi()
    hermes_resource = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": HERMES_RESULT_URN,
        "components": openapi["components"],
        "allOf": [{"$ref": "#/components/schemas/HermesResult"}],
    }
    registry = Registry().with_resource(
        HERMES_RESULT_URN,
        Resource.from_contents(hermes_resource),
    )
    entrypoint = {
        "$schema": frozen["$schema"],
        "$defs": frozen["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    return Draft202012Validator(entrypoint, registry=registry)


def _freshness() -> dict:
    return {
        "data_as_of": None,
        "sync_checked_at": "2026-08-02T00:00:01Z",
        "sync_lag_seconds": 1,
        "source_high_watermark": 0,
        "applied_high_watermark": 0,
        "projection_state": "FRESH",
    }


def _job(*, guide: bool = False) -> dict:
    return {
        "job_id": "job-schema-1",
        "source": "WEB",
        "city": "重庆",
        "days": 3,
        "status": "SUCCESS" if guide else "RUNNING",
        "current_stage": "SUCCESS" if guide else "FINAL_WRITER",
        "result_type": "PLAN_READY" if guide else None,
        "result_record_id": 9001 if guide else None,
        "guide_result_state": "AVAILABLE" if guide else "NOT_APPLICABLE",
        "has_final_guide": guide,
        "safe_error": None,
        "detailed_reason": None,
        "created_at": "2026-08-02T00:00:00Z",
        "started_at": "2026-08-02T00:00:01Z",
        "finished_at": "2026-08-02T00:01:00Z" if guide else None,
        "total_duration_ms": 60_000,
        "retry_count": 0,
        "failed_draft_available": False,
        "is_slow": False,
        "timeout_settlement_anomaly": False,
        "trace_completeness": "COMPLETE",
        "association": {
            "state": "linked",
            "user_id": "usr_schema_1",
            "display_name": "示例用户",
        },
    }


def _base_response() -> dict:
    return {
        "ok": True,
        "request_id": "req-schema",
        "as_of": "2026-08-02T00:00:02Z",
        "freshness": _freshness(),
        "projection_alarm": None,
    }


@pytest.mark.parametrize(
    ("definition", "payload"),
    [
        (
            "AdminTripJobListResponse",
            {**_base_response(), "page": 1, "limit": 20, "total": 1, "items": [_job()]},
        ),
        (
            "AdminTripJobDetailResponse",
            {
                **_base_response(),
                "trip_job": _job(),
                "steps": [
                    {
                        "source_step_id": 1,
                        "stage": "FINAL_WRITER",
                        "stage_label_zh": "撰写攻略正文",
                        "status": "RUNNING",
                        "attempt": 1,
                        "publish_retry_round": 0,
                        "started_at": "2026-08-02T00:00:01Z",
                        "finished_at": None,
                        "duration_ms": None,
                    }
                ],
            },
        ),
        (
            "AdminUserTripListResponse",
            {**_base_response(), "page": 1, "limit": 10, "total": 1, "items": [_job()]},
        ),
        (
            "AdminGenerationPipelineResponse",
            {
                **_base_response(),
                "window": {
                    "from": "2026-08-01T00:00:00Z",
                    "to": "2026-08-02T00:00:00Z",
                },
                "runtime_policy": {
                    "slow_after_seconds": 90,
                    "timeout_after_seconds": 120,
                    "stale_sweep_seconds": 30,
                },
                "overview": {
                    "created_task_count": 0,
                    "terminal_task_count": 0,
                    "terminal_success_count": 0,
                    "published_guide_count": 0,
                    "no_guide_success_count": 0,
                    "terminal_failure_count": 0,
                    "backlog_task_count": 0,
                    "slow_task_count": 0,
                    "timeout_settlement_anomaly_count": 0,
                    "terminal_success_rate": None,
                    "published_guide_rate": None,
                    "excluded_trace_task_count": 0,
                },
                "nodes": [],
            },
        ),
        (
            "AdminGuideReviewResponse",
            {
                **_base_response(),
                "trip_job": _job(guide=True),
                "request": {
                    "values": {"to_city": "重庆", "days": 3},
                    "field_provenance": {
                        "to_city": "USER_SUPPLIED",
                        "days": "USER_SUPPLIED",
                    },
                },
                "request_source": "BFF_USER_TRIP",
                "final_guide": {
                    "schema_version": "1.5",
                    "result_id": 9001,
                    "city": {"name": "重庆"},
                    "request": {
                        "days": 3,
                        "people_count": 2,
                        "preferences": ["美食"],
                        "avoid": [],
                    },
                    "weather": None,
                    "plans": [
                        {
                            "plan_id": "safe",
                            "title": "安全行程",
                            "summary": "安全摘要",
                            "tags": [],
                            "pace": {
                                "level": "MODERATE",
                                "commute_status": "WITHIN_LIMIT",
                                "total_commute_minutes": 0,
                            },
                            "days": [],
                        }
                    ],
                },
                "artifacts": [],
            },
        ),
    ],
)
def test_all_public_response_defs_validate_with_hermes_urn(
    definition: str,
    payload: dict,
) -> None:
    _validator(definition).validate(payload)


def test_openapi_registers_hermes_result_and_frozen_urn_resolution() -> None:
    schema = create_app().openapi()
    assert "HermesResult" in schema["components"]["schemas"]
    assert schema["x-external-schema-resolution"] == {
        HERMES_RESULT_URN: "#/components/schemas/HermesResult"
    }
    assert _frozen_schema()["x-external-schema-resolution"] == {
        HERMES_RESULT_URN: "#/components/schemas/HermesResult"
    }


def test_guide_review_schema_rejects_duplicate_top_level_association() -> None:
    payload = {
        **_base_response(),
        "trip_job": _job(guide=True),
        "request": None,
        "request_source": "UNAVAILABLE",
        "final_guide": {
            "schema_version": "1.5",
            "result_id": 9001,
            "city": {"name": "重庆"},
            "request": {"days": 3, "people_count": 2},
            "weather": None,
            "plans": [],
        },
        "artifacts": [],
        "association": {"state": "unlinked"},
    }
    with pytest.raises(ValidationError):
        _validator("AdminGuideReviewResponse").validate(payload)
