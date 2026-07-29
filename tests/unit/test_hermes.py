import httpx
import pytest

from src.config import Settings
from src.integrations.hermes import (
    HermesBusinessError,
    HermesClient,
    HermesIntegrationError,
)


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
