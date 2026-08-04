from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from src.app import create_app
from src.config import Settings
from src.integrations.hermes import HermesClient, HermesIntegrationError
from src.integrations.hermes_models import HermesResult
from tests.factories import schema_2_cost_estimate

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "v0.9.4-public-schema-2.0.json"


def _result_payload(*, include_internal_fields: bool = False) -> dict:
    return {
        "schema_version": "2.0",
        "result_id": 501,
        "city": {"name": "重庆"},
        "request": {
            "days": 3,
            "people_count": 2,
            "preferences": ["美食"],
            "avoid": [],
        },
        "weather": {"status": "skipped_disabled", "city": "重庆", "days": []},
        "plans": [
            {
                "plan_id": "plan_a",
                "title": "山城慢行",
                "summary": "安全摘要",
                "tags": [],
                "pace": {
                    "level": "MODERATE",
                    "commute_status": "WITHIN_LIMIT",
                    "total_commute_minutes": 0,
                },
                "days": [],
                "cost_estimate": schema_2_cost_estimate(
                    include_internal_fields=include_internal_fields
                ),
                "provider_payload": {"secret": True},
            }
        ],
        "provider_payload": {"secret": True},
    }


def _mutated_cost(mutator) -> dict:
    payload = _result_payload()
    mutator(payload["plans"][0]["cost_estimate"])
    return payload


def test_schema_2_projection_ignores_internal_fields_without_leaking_them() -> None:
    projected = HermesResult.model_validate(
        _result_payload(include_internal_fields=True)
    ).model_dump(mode="json", exclude_none=True)
    encoded = json.dumps(projected, ensure_ascii=False)
    for forbidden in (
        "snapshot_id",
        "diagnostics",
        "observations",
        "line_items",
        "provider_payload",
        "fen_arithmetic",
        "raw_fare",
    ):
        assert forbidden not in encoded
    assert projected["plans"][0]["cost_estimate"]["completeness"] == "complete"


@pytest.mark.parametrize("completeness", ["partial", "unavailable"])
def test_nullable_cost_fields_may_be_absent_from_hermes_exclude_none_response(
    completeness: str,
) -> None:
    payload = _result_payload()
    payload["plans"][0]["cost_estimate"] = schema_2_cost_estimate(completeness=completeness)

    def drop_none(value):
        if isinstance(value, dict):
            return {key: drop_none(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [drop_none(item) for item in value]
        return value

    projected = HermesResult.model_validate(drop_none(payload))
    assert projected.plans[0].cost_estimate.completeness == completeness


@pytest.mark.parametrize(
    "payload",
    [
        {**_result_payload(), "schema_version": "1.5"},
        _mutated_cost(lambda cost: cost.pop("currency")),
        _mutated_cost(
            lambda cost: cost["scenarios"][0]["categories"][0]["range"].update(min_cny=-10)
        ),
        _mutated_cost(
            lambda cost: cost["scenarios"][0]["categories"][0]["range"].update(min_cny=101)
        ),
        _mutated_cost(
            lambda cost: cost["scenarios"][0]["categories"][0]["range"].update(min_cny=100.0)
        ),
        _mutated_cost(lambda cost: cost["scenarios"][0]["categories"].reverse()),
        _mutated_cost(lambda cost: cost["scenarios"][0].update(intercity_mode="flight")),
        _mutated_cost(
            lambda cost: cost["scenarios"][0].update(total_range={"min_cny": 500, "max_cny": 990})
        ),
        _mutated_cost(lambda cost: cost.update(completeness="partial")),
        _mutated_cost(
            lambda cost: cost["scenarios"][0]["categories"][0].update(coverage="missing")
        ),
    ],
    ids=[
        "old-schema",
        "missing-required",
        "negative-cny",
        "not-cny-10",
        "float-cny",
        "category-order",
        "scenario-mode",
        "total-reconciliation",
        "completeness",
        "coverage-nullability",
    ],
)
def test_invalid_public_cost_contract_fails_closed(payload: dict) -> None:
    with pytest.raises(ValidationError):
        HermesResult.model_validate(payload)


@pytest.mark.asyncio
async def test_invalid_hermes_cost_payload_is_a_non_retryable_protocol_error() -> None:
    payload = _mutated_cost(
        lambda cost: cost["scenarios"][0].update(total_range={"min_cny": 0, "max_cny": 0})
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = HermesClient.from_settings(
        Settings(app_env="test"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(HermesIntegrationError) as captured:
        await client.result(501, job_id="job-501", correlation_id="safe-request")
    await client.close()
    assert captured.value.category == "PROTOCOL"
    assert captured.value.retryable is False


def test_openapi_and_local_fixture_match_hermes_schema_2_public_surface() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    schema = create_app().openapi()
    components = schema["components"]["schemas"]
    assert set(components["HermesResult"]["properties"]) == set(fixture["trip_result_properties"])
    assert set(components["ResultRequest"]["properties"]) == set(fixture["request_properties"])
    assert fixture["plan_required_addition"] in components["ResultPlan"]["required"]
    assert set(components["CostEstimateSummary"]["properties"]) == set(
        fixture["cost_estimate_properties"]
    )
    assert set(components["CostScenarioSummary"]["properties"]) == set(
        fixture["scenario_properties"]
    )
    assert set(components["CostCategorySummary"]["properties"]) == set(
        fixture["category_properties"]
    )
    assert set(components["CostMoneyRange"]["properties"]) == set(fixture["money_range_properties"])
    result_response = schema["paths"]["/api/trip/results/{result_record_id}"]["get"]
    assert result_response["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HermesResult"
    }
    serialized_components = json.dumps(
        {
            key: components[key]
            for key in (
                "CostEstimateSummary",
                "CostScenarioSummary",
                "CostCategorySummary",
                "CostMoneyRange",
            )
        }
    )
    for forbidden in fixture["forbidden_cost_properties"]:
        assert forbidden not in serialized_components


def test_local_contract_fixture_is_identical_to_hermes_p4_fixture() -> None:
    hermes_fixture = Path(
        r"D:\tools\workSpace\hermes-travel\tests\fixtures\v0.9.4-public-schema-2.0.json"
    )
    if not hermes_fixture.exists():
        pytest.skip("Hermes sibling fixture is not available in this checkout")
    assert json.loads(FIXTURE_PATH.read_text(encoding="utf-8")) == json.loads(
        hermes_fixture.read_text(encoding="utf-8")
    )
