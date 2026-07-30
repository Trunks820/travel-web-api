from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from src.config import Settings
from src.integrations.hermes_models import (
    HermesAdminArtifactDetail,
    HermesAdminArtifactList,
    HermesAdminFailedDraftDetail,
    HermesAdminTripJobDetail,
    HermesAdminTripJobList,
    HermesArtifact,
    HermesJobStatus,
    HermesPlaceDetail,
    HermesPlaceList,
    HermesResult,
    HermesTripCreated,
    validate_sse_payload,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
ARTIFACT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "share_image": "image/png",
}
ADMIN_ARTIFACT_CONTENT_TYPES = frozenset(ARTIFACT_CONTENT_TYPES.values())
ADMIN_ARTIFACT_DOWNLOAD_ERRORS = frozenset(
    {
        "ARTIFACT_NOT_FOUND",
        "ARTIFACT_NOT_READY",
        "ARTIFACT_FILE_MISSING",
        "ARTIFACT_EXPIRED",
    }
)


class HermesHealth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool


class HermesIntegrationError(Exception):
    def __init__(
        self,
        category: str,
        *,
        retryable: bool,
        acceptance_uncertain: bool = False,
    ) -> None:
        self.category = category
        self.retryable = retryable
        self.acceptance_uncertain = acceptance_uncertain
        super().__init__(category)


class HermesBusinessError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class HermesClient:
    def __init__(
        self,
        *,
        base_url: str,
        credential: str,
        admin_credential: str,
        timeout: httpx.Timeout,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credential = credential
        self._admin_credential = admin_credential
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> HermesClient:
        return cls(
            base_url=settings.hermes_base_url,
            credential=settings.hermes_internal_credential.get_secret_value(),
            admin_credential=(settings.hermes_bff_internal_admin_credential.get_secret_value()),
            timeout=httpx.Timeout(
                connect=settings.hermes_connect_timeout_seconds,
                read=settings.hermes_read_timeout_seconds,
                write=settings.hermes_write_timeout_seconds,
                pool=settings.hermes_pool_timeout_seconds,
            ),
            transport=transport,
        )

    def headers(self, correlation_id: str) -> dict[str, str]:
        return {
            "X-Request-ID": correlation_id,
            "X-Internal-Credential": self._credential,
        }

    def admin_headers(self, correlation_id: str) -> dict[str, str]:
        return {
            "X-Request-ID": correlation_id,
            "X-Internal-Credential": self._admin_credential,
        }

    async def readiness(self, correlation_id: str) -> None:
        try:
            response = await self._client.get(
                "/health",
                headers=self.headers(correlation_id),
            )
            response.raise_for_status()
            HermesHealth.model_validate(response.json())
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HermesIntegrationError("UNAVAILABLE", retryable=True) from exc
        except (httpx.HTTPStatusError, ValueError, ValidationError) as exc:
            raise HermesIntegrationError("PROTOCOL", retryable=False) from exc

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        correlation_id: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        acceptance_possible: bool = False,
        allowed_business_errors: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        headers = self.headers(correlation_id)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self._client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise HermesIntegrationError("PROTOCOL", retryable=False)
            return payload
        except (HermesIntegrationError, HermesBusinessError):
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise HermesIntegrationError("UNAVAILABLE", retryable=True) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.NetworkError) as exc:
            raise HermesIntegrationError(
                "UNAVAILABLE",
                retryable=True,
                acceptance_uncertain=acceptance_possible,
            ) from exc
        except httpx.HTTPStatusError as exc:
            code = _safe_error_code(exc.response)
            if code in allowed_business_errors:
                raise HermesBusinessError(code) from exc
            raise HermesIntegrationError(
                "PROTOCOL",
                retryable=exc.response.status_code >= 500,
                acceptance_uncertain=acceptance_possible,
            ) from exc
        except ValueError as exc:
            raise HermesIntegrationError(
                "PROTOCOL",
                retryable=False,
                acceptance_uncertain=acceptance_possible,
            ) from exc

    async def _request_model(
        self,
        model_type: type[ModelT],
        method: str,
        path: str,
        *,
        correlation_id: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        acceptance_possible: bool = False,
        allowed_business_errors: frozenset[str] = frozenset(),
    ) -> ModelT:
        payload = await self.request_json(
            method,
            path,
            correlation_id=correlation_id,
            json_body=json_body,
            params=params,
            idempotency_key=idempotency_key,
            acceptance_possible=acceptance_possible,
            allowed_business_errors=allowed_business_errors,
        )
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise HermesIntegrationError(
                "PROTOCOL",
                retryable=False,
                acceptance_uncertain=acceptance_possible,
            ) from exc

    async def _request_admin_model(
        self,
        model_type: type[ModelT],
        path: str,
        *,
        correlation_id: str,
        params: dict[str, Any] | None = None,
        allowed_business_errors: frozenset[str] = frozenset(),
    ) -> ModelT:
        try:
            response = await self._client.get(
                path,
                headers=self.admin_headers(correlation_id),
                params={key: value for key, value in (params or {}).items() if value is not None},
            )
            self._validate_admin_response_request_id(response, correlation_id)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise HermesIntegrationError("PROTOCOL", retryable=False)
            model = model_type.model_validate(payload)
            if getattr(model, "request_id", None) != correlation_id:
                raise HermesIntegrationError("PROTOCOL", retryable=False)
            return model
        except (HermesIntegrationError, HermesBusinessError):
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HermesIntegrationError("UNAVAILABLE", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            code = _safe_error_code(exc.response)
            if code in allowed_business_errors:
                raise HermesBusinessError(code) from exc
            raise HermesIntegrationError(
                "UNAVAILABLE" if exc.response.status_code >= 500 else "PROTOCOL",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise HermesIntegrationError("PROTOCOL", retryable=False) from exc

    @staticmethod
    def _validate_admin_response_request_id(
        response: httpx.Response,
        correlation_id: str,
    ) -> None:
        if response.headers.get("X-Request-ID") != correlation_id:
            raise HermesIntegrationError("PROTOCOL", retryable=False)

    async def admin_trip_jobs(
        self,
        *,
        correlation_id: str,
        params: dict[str, Any],
    ) -> HermesAdminTripJobList:
        return await self._request_admin_model(
            HermesAdminTripJobList,
            "/internal/v1/admin/trip-jobs",
            correlation_id=correlation_id,
            params=params,
        )

    async def admin_trip_job(
        self,
        job_id: str,
        *,
        correlation_id: str,
    ) -> HermesAdminTripJobDetail:
        return await self._request_admin_model(
            HermesAdminTripJobDetail,
            f"/internal/v1/admin/trip-jobs/{quote(job_id, safe='')}",
            correlation_id=correlation_id,
            allowed_business_errors=frozenset({"TRIP_JOB_NOT_FOUND"}),
        )

    async def admin_failed_draft(
        self,
        job_id: str,
        *,
        correlation_id: str,
    ) -> HermesAdminFailedDraftDetail:
        return await self._request_admin_model(
            HermesAdminFailedDraftDetail,
            f"/internal/v1/admin/trip-jobs/{quote(job_id, safe='')}/failed-draft",
            correlation_id=correlation_id,
            allowed_business_errors=frozenset({"TRIP_JOB_NOT_FOUND", "FAILED_DRAFT_NOT_FOUND"}),
        )

    async def admin_artifacts(
        self,
        *,
        correlation_id: str,
        params: dict[str, Any],
    ) -> HermesAdminArtifactList:
        return await self._request_admin_model(
            HermesAdminArtifactList,
            "/internal/v1/admin/artifacts",
            correlation_id=correlation_id,
            params=params,
        )

    async def admin_artifact(
        self,
        artifact_id: str,
        *,
        correlation_id: str,
    ) -> HermesAdminArtifactDetail:
        return await self._request_admin_model(
            HermesAdminArtifactDetail,
            f"/internal/v1/admin/artifacts/{quote(artifact_id, safe='')}",
            correlation_id=correlation_id,
            allowed_business_errors=frozenset({"ARTIFACT_NOT_FOUND"}),
        )

    async def admin_artifact_bytes(
        self,
        artifact_id: str,
        *,
        correlation_id: str,
        max_bytes: int,
    ) -> tuple[bytes, str]:
        try:
            response = await self._client.get(
                f"/internal/v1/admin/artifacts/{quote(artifact_id, safe='')}/download",
                headers=self.admin_headers(correlation_id),
            )
            self._validate_admin_response_request_id(response, correlation_id)
            response.raise_for_status()
        except (HermesIntegrationError, HermesBusinessError):
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HermesIntegrationError("UNAVAILABLE", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            code = _safe_error_code(exc.response)
            if code in ADMIN_ARTIFACT_DOWNLOAD_ERRORS:
                raise HermesBusinessError(code) from exc
            raise HermesIntegrationError(
                "UNAVAILABLE" if exc.response.status_code >= 500 else "PROTOCOL",
                retryable=exc.response.status_code >= 500,
            ) from exc

        content = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
        raw_length = response.headers.get("content-length")
        disposition = response.headers.get("content-disposition")
        try:
            declared_length = int(raw_length or "")
        except ValueError as exc:
            raise HermesIntegrationError("PROTOCOL", retryable=False) from exc
        if (
            content_type not in ADMIN_ARTIFACT_CONTENT_TYPES
            or declared_length != len(content)
            or len(content) > max_bytes
            or not disposition
        ):
            raise HermesIntegrationError("PROTOCOL", retryable=False)
        return content, content_type

    async def create_trip(
        self,
        *,
        trip_request: dict[str, Any],
        upstream_request_id: str,
        conversation_id: str,
        correlation_id: str,
    ) -> HermesTripCreated:
        return await self._request_model(
            HermesTripCreated,
            "POST",
            "/trip/async",
            correlation_id=correlation_id,
            json_body={
                "trip_request": trip_request,
                "request_id": upstream_request_id,
                "source": "travel-web-api",
                "conversation_id": conversation_id,
                "user_display_name": None,
            },
            idempotency_key=upstream_request_id,
            acceptance_possible=True,
            allowed_business_errors=frozenset({"CITY_NOT_SUPPORTED"}),
        )

    async def job_status(self, job_id: str, correlation_id: str) -> HermesJobStatus:
        return await self._request_model(
            HermesJobStatus,
            "GET",
            f"/trip/jobs/{job_id}",
            correlation_id=correlation_id,
        )

    async def result(
        self,
        result_record_id: int,
        *,
        job_id: str,
        correlation_id: str,
    ) -> HermesResult:
        return await self._request_model(
            HermesResult,
            "GET",
            f"/trip/results/{result_record_id}",
            correlation_id=correlation_id,
            params={"job_id": job_id},
        )

    async def artifact(
        self,
        method: str,
        result_record_id: int,
        artifact_type: str,
        *,
        correlation_id: str,
    ) -> HermesArtifact:
        if method == "GET":
            allowed_business_errors = frozenset({"EXPORT_ARTIFACT_NOT_FOUND"})
        elif method == "POST":
            allowed_business_errors = frozenset(
                {
                    "EXPORT_RATE_LIMITED",
                    "RESULT_CONTRACT_UNSUPPORTED",
                }
            )
        else:
            allowed_business_errors = frozenset()
        return await self._request_model(
            HermesArtifact,
            method,
            f"/trip/results/{result_record_id}/artifacts/{artifact_type}",
            correlation_id=correlation_id,
            allowed_business_errors=allowed_business_errors,
        )

    async def places(
        self,
        *,
        city: str,
        limit: int,
        correlation_id: str,
    ) -> HermesPlaceList:
        return await self._request_model(
            HermesPlaceList,
            "GET",
            "/trip/places",
            correlation_id=correlation_id,
            params={"city": city, "limit": limit},
        )

    async def place(self, place_id: int, *, correlation_id: str) -> HermesPlaceDetail:
        return await self._request_model(
            HermesPlaceDetail,
            "GET",
            f"/trip/places/{place_id}",
            correlation_id=correlation_id,
            allowed_business_errors=frozenset(
                {
                    "PLACE_NOT_FOUND",
                    "PLACE_UNSUPPORTED",
                }
            ),
        )

    async def artifact_bytes(
        self,
        result_record_id: int,
        artifact_type: str,
        *,
        correlation_id: str,
        max_bytes: int,
    ) -> tuple[bytes, str, str | None]:
        expected_content_type = ARTIFACT_CONTENT_TYPES.get(artifact_type)
        if expected_content_type is None:
            raise HermesIntegrationError("PROTOCOL", retryable=False)
        try:
            response = await self._client.get(
                f"/trip/results/{result_record_id}/artifacts/{artifact_type}/download",
                headers=self.headers(correlation_id),
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HermesIntegrationError("UNAVAILABLE", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise HermesIntegrationError("PROTOCOL", retryable=False) from exc
        content = response.content
        content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
        if len(content) > max_bytes or content_type != expected_content_type:
            raise HermesIntegrationError("PROTOCOL", retryable=False)
        disposition = response.headers.get("content-disposition")
        return content, content_type, disposition

    async def stream_job(
        self,
        job_id: str,
        correlation_id: str,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        try:
            async with self._client.stream(
                "GET",
                f"/trip/jobs/{job_id}/stream",
                headers=self.headers(correlation_id),
            ) as response:
                response.raise_for_status()
                event = "message"
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif not line and data_lines:
                        payload = json.loads("\n".join(data_lines))
                        if not isinstance(payload, dict):
                            raise ValueError("SSE data must be an object")
                        safe_event, model = validate_sse_payload(event, payload)
                        yield safe_event, model.model_dump(exclude_none=True)
                        event = "message"
                        data_lines = []
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise HermesIntegrationError("PROTOCOL", retryable=False) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise HermesIntegrationError("UNAVAILABLE", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise HermesIntegrationError("PROTOCOL", retryable=False) from exc

    async def close(self) -> None:
        await self._client.aclose()


def _safe_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    nested = payload.get("error")
    code = nested.get("code") if isinstance(nested, dict) else None
    return code if isinstance(code, str) and len(code) <= 80 else None
