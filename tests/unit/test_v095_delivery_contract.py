from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.app import create_app
from src.integrations.hermes_models import HermesJobStatus, HermesResult
from src.trips.service import public_job_status
from tests.factories import schema_2_cost_estimate


def _result_payload(
    *,
    schema_version: str = "2.1",
    published_variant: str | None = "normal",
    delivery_status: str | None = "NORMAL",
) -> dict:
    payload = {
        "schema_version": schema_version,
        "result_id": 501,
        "city": {"name": "重庆"},
        "request": {
            "days": 1,
            "people_count": 1,
            "preferences": [],
            "avoid": [],
        },
        "weather": {"status": "skipped_disabled", "city": "重庆", "days": []},
        "plans": [
            {
                "plan_id": "plan-1",
                "title": "行程",
                "summary": "摘要",
                "tags": [],
                "pace": {
                    "level": "MODERATE",
                    "commute_status": "WITHIN_LIMIT",
                    "total_commute_minutes": 0,
                },
                "days": [],
                "cost_estimate": schema_2_cost_estimate(),
            }
        ],
        "safe_trigger": "writer_failure",
        "review_transport_error_type": "timeout",
        "provider": "private",
    }
    if published_variant is not None:
        payload["published_variant"] = published_variant
    if delivery_status is not None:
        payload["delivery_status"] = delivery_status
    return payload


@pytest.mark.parametrize(
    ("variant", "status"),
    [
        ("normal", "NORMAL"),
        ("normal", "DEGRADED"),
        ("safe", "DEGRADED"),
    ],
)
def test_all_success_combinations_round_trip_without_internal_causes(
    variant: str,
    status: str,
) -> None:
    model = HermesResult.model_validate(
        _result_payload(
            published_variant=variant,
            delivery_status=status,
        )
    )
    payload = model.model_dump(mode="json", exclude_none=True)
    assert payload["schema_version"] == "2.1"
    assert payload["published_variant"] == variant
    assert payload["delivery_status"] == status
    assert "safe_trigger" not in payload
    assert "review_transport_error_type" not in payload
    assert "provider" not in payload


def test_historical_schema_20_defaults_to_normal_normal() -> None:
    model = HermesResult.model_validate(
        _result_payload(
            schema_version="2.0",
            published_variant=None,
            delivery_status=None,
        )
    )
    assert model.schema_version == "2.1"
    assert model.published_variant == "normal"
    assert model.delivery_status == "NORMAL"


def test_invalid_safe_normal_combination_is_rejected() -> None:
    with pytest.raises(ValidationError):
        HermesResult.model_validate(
            _result_payload(
                published_variant="safe",
                delivery_status="NORMAL",
            )
        )


def test_job_status_preserves_delivery_fields_and_public_projection() -> None:
    upstream = HermesJobStatus.model_validate(
        {
            "job_id": "job-1",
            "status": "SUCCESS",
            "result_record_id": 501,
            "published_variant": "safe",
            "delivery_status": "DEGRADED",
            "safe_trigger": "review_no_anchor",
        }
    )
    local = SimpleNamespace(
        status="SUCCESS",
        hermes_job_id="job-1",
        result_record_id=501,
        error_code=None,
        error_message=None,
    )
    payload = public_job_status(upstream, local)
    assert payload["published_variant"] == "safe"
    assert payload["delivery_status"] == "DEGRADED"
    assert "safe_trigger" not in payload


def test_openapi_requires_schema_21_delivery_fields() -> None:
    result_schema = create_app().openapi()["components"]["schemas"]["HermesResult"]
    assert {"published_variant", "delivery_status"}.issubset(result_schema["required"])
    assert result_schema["properties"]["schema_version"]["const"] == "2.1"
