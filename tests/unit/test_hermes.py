from datetime import UTC, datetime

import httpx
import pytest

from src.config import Settings
from src.integrations.hermes import (
    HermesBusinessError,
    HermesClient,
    HermesIntegrationError,
)
from tests.factories import schema_2_cost_estimate


@pytest.mark.asyncio
async def test_hermes_readiness_validates_json_and_sends_internal_headers() -> None:
    seen_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = HermesClient.from_settings(
        Settings(app_env="test", hermes_internal_credential="internal-secret"),
        transport=httpx.MockTransport(handler),
    )
    await client.readiness("request-123")
    await client.close()
    assert seen_headers["x-request-id"] == "request-123"
    assert seen_headers["x-internal-credential"] == "internal-secret"


@pytest.mark.asyncio
async def test_result_preserves_transport_contract_and_drops_unknown_fields() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_version": "2.0",
                "result_id": 9,
                "city": {"name": "重庆"},
                "request": {
                    "days": 3,
                    "people_count": 1,
                    "preferences": [],
                    "avoid": [],
                },
                "weather": {"status": "skipped_disabled", "city": "重庆", "days": []},
                "plans": [
                    {
                        "plan_id": "plan_a",
                        "title": "测试行程",
                        "summary": "测试摘要",
                        "tags": [],
                        "pace": {
                            "level": "RELAXED",
                            "commute_status": "WITHIN_LIMIT",
                            "total_commute_minutes": 0,
                        },
                        "transport": {
                            "from_city": "成都",
                            "to_city": "重庆",
                            "query_date": "2026-08-05",
                            "source": "realtime",
                            "modes": [
                                {
                                    "mode": "train",
                                    "min_duration_minutes": 90,
                                    "price_range": "¥100-200",
                                    "price_source": "realtime",
                                    "daily_count": 12,
                                    "data_source": "realtime",
                                    "availability_status": "available_at_query",
                                    "availability_checked_at": None,
                                    "options": [
                                        {
                                            "type": "train",
                                            "no": "G1",
                                            "departure_time": "08:00",
                                            "arrival_time": "09:30",
                                            "duration_minutes": 90,
                                            "price": "¥150",
                                            "departure_station": "成都东",
                                            "arrival_station": "重庆北",
                                            "airline": None,
                                        }
                                    ],
                                }
                            ],
                        },
                        "days": [],
                        "cost_estimate": schema_2_cost_estimate(),
                        "provider_payload": {"secret": True},
                    }
                ],
            },
        )

    client = HermesClient.from_settings(
        Settings(app_env="test", hermes_internal_credential="internal-secret"),
        transport=httpx.MockTransport(handler),
    )
    result = await client.result(9, job_id="job-9", correlation_id="request-9")
    await client.close()

    payload = result.model_dump(exclude_none=True)
    assert set(payload["request"]) == {"days", "people_count", "preferences", "avoid"}
    assert payload["plans"][0]["transport"]["source"] == "realtime"
    assert payload["plans"][0]["transport"]["modes"][0]["options"][0]["no"] == "G1"
    assert "provider_payload" not in payload["plans"][0]


@pytest.mark.asyncio
async def test_hermes_readiness_normalizes_protocol_errors() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    client = HermesClient.from_settings(
        Settings(app_env="test"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesIntegrationError) as captured:
        await client.readiness("request-123")
    await client.close()
    assert captured.value.category == "PROTOCOL"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_create_trip_uses_trusted_identity_and_validates_response() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "job_id": "job-safe",
                "status": "PENDING",
                "provider_payload": {"secret": True},
            },
        )

    client = HermesClient.from_settings(
        Settings(app_env="test", hermes_internal_credential="internal-secret"),
        transport=httpx.MockTransport(handler),
    )
    created = await client.create_trip(
        trip_request={"to_city": "重庆", "days": 3},
        upstream_request_id="bff-trip-safe",
        conversation_id="trip-safe",
        correlation_id="correlation-safe",
    )
    await client.close()

    assert created.model_dump() == {
        "job_id": "job-safe",
        "status": "PENDING",
        "current_stage": None,
        "queue_position": None,
        "message": None,
        "cached": None,
    }
    assert seen["json"]["source"] == "travel-web-api"
    assert seen["json"]["conversation_id"] == "trip-safe"
    assert seen["json"]["user_display_name"] is None
    assert seen["headers"]["idempotency-key"] == "bff-trip-safe"
    assert seen["headers"]["x-internal-credential"] == "internal-secret"


@pytest.mark.asyncio
async def test_create_trip_protocol_error_marks_acceptance_uncertain_without_leak() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"code": "PRIVATE", "message": "credential=secret"}},
        )

    client = HermesClient.from_settings(
        Settings(app_env="test"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesIntegrationError) as captured:
        await client.create_trip(
            trip_request={"to_city": "重庆", "days": 3},
            upstream_request_id="bff-trip-safe",
            conversation_id="trip-safe",
            correlation_id="correlation-safe",
        )
    await client.close()
    assert captured.value.category == "PROTOCOL"
    assert captured.value.acceptance_uncertain is True
    assert "credential" not in str(captured.value)


@pytest.mark.asyncio
async def test_job_status_and_sse_drop_unapproved_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stream"):
            return httpx.Response(
                200,
                text=(
                    "event: complete\n"
                    'data: {"status":"SUCCESS","job_id":"job-1",'
                    '"result_record_id":9,"provider_payload":{"secret":true}}\n\n'
                ),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "job_id": "job-1",
                "status": "RUNNING",
                "current_stage": "WRITER",
                "trip_request": {"notes": "private"},
                "provider_payload": {"secret": True},
            },
        )

    client = HermesClient.from_settings(
        Settings(app_env="test"),
        transport=httpx.MockTransport(handler),
    )
    status = await client.job_status("job-1", "correlation-safe")
    events = [event async for event in client.stream_job("job-1", "correlation-safe")]
    await client.close()
    assert "trip_request" not in status.model_dump()
    assert "provider_payload" not in status.model_dump()
    assert events == [
        (
            "complete",
            {"status": "SUCCESS", "job_id": "job-1", "result_record_id": 9},
        )
    ]


@pytest.mark.parametrize("transport_error", [httpx.ReadTimeout, httpx.ReadError])
@pytest.mark.asyncio
async def test_sse_timeout_and_network_errors_are_normalized(transport_error) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise transport_error("stream interrupted", request=request)

    client = HermesClient.from_settings(
        Settings(app_env="test"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesIntegrationError) as captured:
        _events = [event async for event in client.stream_job("job-1", "correlation-safe")]
    await client.close()

    assert captured.value.category == "UNAVAILABLE"
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_place_business_error_is_allowlisted_without_upstream_message() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "PLACE_NOT_FOUND",
                    "message": "private upstream detail",
                }
            },
        )

    client = HermesClient.from_settings(
        Settings(app_env="test"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesBusinessError) as captured:
        await client.place(404, correlation_id="correlation-safe")
    await client.close()
    assert captured.value.code == "PLACE_NOT_FOUND"
    assert "private upstream detail" not in str(captured.value)


def _admin_trip_item() -> dict:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "job_id": "job-admin-1",
        "result_record_id": "42",
        "status": "FAILED",
        "current_stage": "FAILED",
        "city": "重庆",
        "result_type": None,
        "safe_error": {
            "code": "PUBLISH_GATE_FAILED",
            "message": "攻略未通过发布校验",
        },
        "detailed_reason": "publish_gate_failed",
        "created_at": now,
        "started_at": now,
        "finished_at": now,
        "total_duration_ms": 200_000,
        "retry_count": 1,
        "failed_draft_available": True,
        "provider_payload": {"secret": True},
    }


@pytest.mark.asyncio
async def test_internal_admin_list_uses_independent_credential_and_safe_model() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        request_id = request.headers["X-Request-ID"]
        return httpx.Response(
            200,
            headers={"X-Request-ID": request_id},
            json={
                "ok": True,
                "contract_version": "v1",
                "request_id": request_id,
                "page": 1,
                "limit": 20,
                "total": 1,
                "items": [_admin_trip_item()],
            },
        )

    client = HermesClient.from_settings(
        Settings(
            app_env="test",
            hermes_internal_credential="ordinary-internal",
            hermes_bff_internal_admin_credential="admin-internal",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await client.admin_trip_jobs(
        correlation_id="admin-request-1",
        params={"page": 1, "limit": 20, "city": "重庆", "status": None},
    )
    await client.close()

    assert seen["path"] == "/internal/v1/admin/trip-jobs"
    assert seen["headers"]["x-internal-credential"] == "admin-internal"
    assert seen["headers"]["x-internal-credential"] != "ordinary-internal"
    assert seen["params"] == {"page": "1", "limit": "20", "city": "重庆"}
    assert result.request_id == "admin-request-1"
    assert "provider_payload" not in result.items[0].model_dump()


@pytest.mark.asyncio
async def test_internal_admin_requires_matching_request_id_and_v1_envelope() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Request-ID": "different-request"},
            json={
                "ok": True,
                "contract_version": "v2",
                "request_id": request.headers["X-Request-ID"],
                "page": 1,
                "limit": 20,
                "total": 0,
                "items": [],
            },
        )

    client = HermesClient.from_settings(
        Settings(
            app_env="test",
            hermes_bff_internal_admin_credential="admin-internal",
        ),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(HermesIntegrationError) as captured:
        await client.admin_trip_jobs(
            correlation_id="admin-request-1",
            params={"page": 1, "limit": 20},
        )
    await client.close()
    assert captured.value.category == "PROTOCOL"
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_internal_admin_download_validates_bytes_and_allowlists_file_missing() -> None:
    mode = "success"

    async def handler(request: httpx.Request) -> httpx.Response:
        request_id = request.headers["X-Request-ID"]
        if mode == "missing":
            return httpx.Response(
                409,
                headers={"X-Request-ID": request_id},
                json={
                    "ok": False,
                    "contract_version": "v1",
                    "request_id": request_id,
                    "error": {
                        "code": "ARTIFACT_FILE_MISSING",
                        "message": "private upstream copy",
                    },
                },
            )
        return httpx.Response(
            200,
            headers={
                "X-Request-ID": request_id,
                "Content-Type": "application/pdf",
                "Content-Length": "9",
                "Content-Disposition": 'attachment; filename="safe.pdf"',
            },
            content=b"%PDF-safe",
        )

    client = HermesClient.from_settings(
        Settings(
            app_env="test",
            hermes_bff_internal_admin_credential="admin-internal",
        ),
        transport=httpx.MockTransport(handler),
    )
    content, content_type = await client.admin_artifact_bytes(
        "artifact-1",
        correlation_id="admin-request-1",
        max_bytes=100,
    )
    assert content == b"%PDF-safe"
    assert content_type == "application/pdf"

    mode = "missing"
    with pytest.raises(HermesBusinessError) as captured:
        await client.admin_artifact_bytes(
            "artifact-1",
            correlation_id="admin-request-2",
            max_bytes=100,
        )
    await client.close()
    assert captured.value.code == "ARTIFACT_FILE_MISSING"
    assert "private upstream copy" not in str(captured.value)
