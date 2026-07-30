from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from src.app import create_app
from src.auth.dependencies import get_current_auth
from src.config import Settings
from src.db.session import get_db_session
from src.integrations.hermes import HermesClient
from src.trips import router as trip_router


class FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return None


class FakeEngine:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def connect(self):
        if not self.available:
            raise OSError("database unavailable")
        return FakeConnection()

    async def dispose(self):
        return None


class FakeHermes:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    async def readiness(self, _correlation_id: str) -> None:
        if not self.available:
            raise OSError("Hermes unavailable")

    async def close(self):
        return None


def make_app(*, db_available: bool = True, hermes_available: bool = True):
    return create_app(
        Settings(app_env="test", request_max_bytes=1024),
        engine=FakeEngine(available=db_available),
        hermes=FakeHermes(available=hermes_available),
    )


async def _allow_owned_result(*_args, **_kwargs) -> None:
    return None


async def _fake_auth():
    return SimpleNamespace()


async def _fake_db():
    yield SimpleNamespace()


def _make_artifact_app(
    monkeypatch,
    handler,
    *,
    artifact_max_bytes: int = 25 * 1024 * 1024,
):
    settings = Settings(
        app_env="test",
        hermes_internal_credential="test-internal",
        artifact_max_bytes=artifact_max_bytes,
    )
    hermes = HermesClient.from_settings(
        settings,
        transport=httpx.MockTransport(handler),
    )
    app = create_app(settings, engine=FakeEngine(), hermes=hermes)
    app.dependency_overrides[get_current_auth] = _fake_auth
    app.dependency_overrides[get_db_session] = _fake_db
    monkeypatch.setattr(trip_router, "_owned_result", _allow_owned_result)
    return app


def test_health_is_liveness_only_and_has_request_id() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "travel-web-api",
        "version": "0.1.0",
    }
    assert response.headers["x-request-id"]


def test_ready_checks_database_and_hermes() -> None:
    with TestClient(make_app()) as client:
        ready = client.get("/ready")
    assert ready.status_code == 200

    with TestClient(make_app(db_available=False)) as client:
        database_down = client.get("/ready")
    assert database_down.status_code == 503
    assert database_down.json()["error"]["code"] == "NOT_READY"

    with TestClient(make_app(hermes_available=False)) as client:
        hermes_down = client.get("/ready")
    assert hermes_down.status_code == 503
    assert hermes_down.json()["error"]["code"] == "NOT_READY"


def test_request_boundary_rejects_origin_content_type_and_size() -> None:
    with TestClient(make_app()) as client:
        origin = client.post(
            "/api/missing",
            json={},
            headers={"Origin": "https://evil.example"},
        )
        content_type = client.post(
            "/api/missing",
            content="body",
            headers={
                "Origin": "https://kakarot8.com",
                "Content-Type": "text/plain",
            },
        )
        too_large = client.post(
            "/api/missing",
            content=b"x" * 1025,
            headers={
                "Origin": "https://kakarot8.com",
                "Content-Type": "application/json",
            },
        )
    assert origin.status_code == 403
    assert origin.json()["error"]["code"] == "ORIGIN_REJECTED"
    assert content_type.status_code == 415
    assert content_type.json()["error"]["code"] == "JSON_REQUIRED"
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_openapi_metadata_is_the_frontend_contract_boundary() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "YunTu Travel Web API"
    assert response.json()["info"]["version"] == "0.1.0"
    paths = response.json()["paths"]
    assert "/api/me/trips" in paths
    assert "/api/me/closure/confirm" in paths
    implemented_admin_paths = {
        "/api/admin/me",
        "/api/admin/dashboard",
        "/api/admin/users",
        "/api/admin/users/{user_id}",
        "/api/admin/users/{user_id}/email",
        "/api/admin/users/{user_id}/disable",
        "/api/admin/users/{user_id}/restore",
        "/api/admin/users/{user_id}/grant-admin",
        "/api/admin/users/{user_id}/revoke-admin",
        "/api/admin/users/{user_id}/quota-ledger",
        "/api/admin/quota-adjustments",
        "/api/admin/quota-adjustments/{adjustment_id}/reverse",
        "/api/admin/trip-jobs",
        "/api/admin/trip-jobs/{job_id}",
        "/api/admin/trip-jobs/{job_id}/failed-draft",
        "/api/admin/artifacts",
        "/api/admin/artifacts/{artifact_id}",
        "/api/admin/artifacts/{artifact_id}/download",
        "/api/admin/invitation-batches",
        "/api/admin/invitation-batches/{batch_id}",
        "/api/admin/invitation-batches/{batch_id}/disable",
        "/api/admin/invitation-codes/lookup",
        "/api/admin/invitation-codes/{code_id}/disable",
        "/api/admin/reports/trip-generation",
        "/api/admin/reports/user-preferences",
        "/api/admin/audit-events",
    }
    assert implemented_admin_paths <= paths.keys()
    expected_admin_operations = {
        ("get", "/api/admin/me"),
        ("get", "/api/admin/dashboard"),
        ("get", "/api/admin/users"),
        ("get", "/api/admin/users/{user_id}"),
        ("get", "/api/admin/users/{user_id}/email"),
        ("post", "/api/admin/users/{user_id}/disable"),
        ("post", "/api/admin/users/{user_id}/restore"),
        ("post", "/api/admin/users/{user_id}/grant-admin"),
        ("post", "/api/admin/users/{user_id}/revoke-admin"),
        ("get", "/api/admin/users/{user_id}/quota-ledger"),
        ("post", "/api/admin/quota-adjustments"),
        ("post", "/api/admin/quota-adjustments/{adjustment_id}/reverse"),
        ("get", "/api/admin/trip-jobs"),
        ("get", "/api/admin/trip-jobs/{job_id}"),
        ("get", "/api/admin/trip-jobs/{job_id}/failed-draft"),
        ("get", "/api/admin/artifacts"),
        ("get", "/api/admin/artifacts/{artifact_id}"),
        ("get", "/api/admin/artifacts/{artifact_id}/download"),
        ("get", "/api/admin/invitation-batches"),
        ("post", "/api/admin/invitation-batches"),
        ("get", "/api/admin/invitation-batches/{batch_id}"),
        ("post", "/api/admin/invitation-batches/{batch_id}/disable"),
        ("post", "/api/admin/invitation-codes/lookup"),
        ("post", "/api/admin/invitation-codes/{code_id}/disable"),
        ("get", "/api/admin/reports/trip-generation"),
        ("get", "/api/admin/reports/user-preferences"),
        ("get", "/api/admin/audit-events"),
    }
    actual_admin_operations = {
        (method, path)
        for path, operations in paths.items()
        if path.startswith("/api/admin/")
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual_admin_operations == expected_admin_operations
    for method, path in expected_admin_operations:
        operation = paths[path][method]
        success_status = "201" if operation["responses"].get("201") else "200"
        content = operation["responses"][success_status]["content"]
        assert {"401", "403", "422"} <= operation["responses"].keys()
        if path.endswith("/download"):
            for media_type in ("application/pdf", "image/png"):
                assert content[media_type]["schema"] == {
                    "type": "string",
                    "format": "binary",
                }
        else:
            schema = content["application/json"]["schema"]
            assert schema["$ref"].startswith("#/components/schemas/Admin")

    mutation_operations = {
        (method, path) for method, path in expected_admin_operations if method == "post"
    }
    for method, path in mutation_operations:
        request_schema = paths[path][method]["requestBody"]["content"]["application/json"]["schema"]
        assert request_schema["$ref"].startswith("#/components/schemas/")

    trip_list = paths["/api/admin/trip-jobs"]["get"]
    assert {parameter["name"] for parameter in trip_list["parameters"]} >= {
        "time_from",
        "time_to",
        "city",
        "status",
        "result_type",
        "error_code",
        "detailed_reason",
        "page",
        "limit",
    }
    assert trip_list["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AdminTripJobListResponse"
    )
    failed_draft = paths["/api/admin/trip-jobs/{job_id}/failed-draft"]["get"]
    assert {"200", "401", "403", "404", "502", "503"} <= failed_draft["responses"].keys()
    artifact_download = paths["/api/admin/artifacts/{artifact_id}/download"]["get"]
    assert {"200", "401", "403", "404", "409", "410", "502", "503"} <= (
        artifact_download["responses"].keys()
    )
    assert not any("archive" in path for path in paths)
    assert "/api/me/trips/{trip_id}" not in paths


def test_artifact_get_not_found_contract_is_narrow(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/trip/results/2253/artifacts/share_image":
            return httpx.Response(
                404,
                json={
                    "ok": False,
                    "error": {
                        "code": "EXPORT_ARTIFACT_NOT_FOUND",
                        "message": "private upstream message",
                    },
                },
            )
        if request.url.path == "/trip/results/2254/artifacts/share_image":
            return httpx.Response(
                404,
                json={
                    "ok": False,
                    "error": {
                        "code": "UNKNOWN_PRIVATE_ERROR",
                        "message": "private upstream message",
                    },
                },
            )
        if request.url.path == "/trip/results/2255/artifacts/share_image":
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error": {
                        "code": "EXPORT_RATE_LIMITED",
                        "message": "private upstream message",
                    },
                },
            )
        if request.url.path == "/trip/results/2256/artifacts/share_image":
            return httpx.Response(
                422,
                json={
                    "ok": False,
                    "error": {
                        "code": "RESULT_CONTRACT_UNSUPPORTED",
                        "message": "private upstream message",
                    },
                },
            )
        raise AssertionError(f"unexpected upstream request: {request.method} {request.url.path}")

    app = _make_artifact_app(monkeypatch, handler)

    with TestClient(app) as client:
        missing = client.get("/api/trip/results/2253/artifacts/share_image")
        unknown = client.get("/api/trip/results/2254/artifacts/share_image")
        post_unchanged = client.post(
            "/api/trip/results/2253/artifacts/share_image",
            headers={"Origin": "https://kakarot8.com"},
        )
        rate_limited = client.post(
            "/api/trip/results/2255/artifacts/share_image",
            headers={"Origin": "https://kakarot8.com"},
        )
        contract_unsupported = client.post(
            "/api/trip/results/2256/artifacts/share_image",
            headers={"Origin": "https://kakarot8.com"},
        )
        upstream_calls = len(seen)
        unsupported = [
            client.get("/api/trip/results/2253/artifacts/archive"),
            client.post(
                "/api/trip/results/2253/artifacts/archive",
                headers={"Origin": "https://kakarot8.com"},
            ),
            client.get("/api/trip/results/2253/artifacts/archive/download"),
        ]

    assert missing.status_code == 404
    assert missing.json() == {
        "ok": False,
        "error": {
            "code": "EXPORT_ARTIFACT_NOT_FOUND",
            "message": "导出文件尚未创建。",
            "retryable": False,
        },
    }
    assert unknown.status_code == 502
    assert unknown.json()["error"]["code"] == "GENERATION_SERVICE_ERROR"
    assert "private upstream message" not in unknown.text
    assert post_unchanged.status_code == 502
    assert post_unchanged.json()["error"]["code"] == "GENERATION_SERVICE_ERROR"
    assert "private upstream message" not in post_unchanged.text
    assert rate_limited.status_code == 429
    assert rate_limited.json()["error"] == {
        "code": "EXPORT_RATE_LIMITED",
        "message": "今日导出次数已达上限，请明日再试。",
        "retryable": False,
    }
    assert "private upstream message" not in rate_limited.text
    assert contract_unsupported.status_code == 422
    assert contract_unsupported.json()["error"]["code"] == "RESULT_CONTRACT_UNSUPPORTED"
    assert "private upstream message" not in contract_unsupported.text
    assert all(response.status_code == 422 for response in unsupported)
    assert all(
        response.json()["error"]["code"] == "ARTIFACT_TYPE_UNSUPPORTED" for response in unsupported
    )
    assert len(seen) == upstream_calls


def test_artifact_projection_and_download_are_type_safe(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/download"):
            artifact_type = path.split("/")[-2]
            content, content_type = {
                "pdf": (b"%PDF-safe", "application/pdf"),
                "share_image": (b"\x89PNG-safe", "image/png"),
            }[artifact_type]
            return httpx.Response(
                200,
                content=content,
                headers={
                    "content-type": content_type,
                    "content-disposition": 'attachment; filename="../../upstream.bin"',
                },
            )

        artifact_type = path.split("/")[-1]
        mime_type = {
            "pdf": "application/pdf",
            "share_image": "image/png",
        }[artifact_type]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "artifact_id": f"artifact-{artifact_type}",
                "result_record_id": 2253,
                "artifact_type": artifact_type,
                "status": "ready",
                "download_url": "http://internal/private-download",
                "filename": "../../upstream.bin",
                "mime_type": mime_type,
                "byte_size": 8,
                "metadata": {
                    "export_version": "1",
                    "storage_path": "/private/storage",
                },
            },
        )

    app = _make_artifact_app(monkeypatch, handler)
    with TestClient(app) as client:
        share_created = client.post(
            "/api/trip/results/2253/artifacts/share_image",
            headers={"Origin": "https://kakarot8.com"},
        )
        share_status = client.get("/api/trip/results/2253/artifacts/share_image")
        share_download = client.get("/api/trip/results/2253/artifacts/share_image/download")
        pdf_download = client.get("/api/trip/results/2253/artifacts/pdf/download")

    for response in (share_created, share_status):
        assert response.status_code == 200
        assert response.json()["artifact_type"] == "share_image"
        assert response.json()["download_url"] == (
            "/api/trip/results/2253/artifacts/share_image/download"
        )
        assert response.json()["filename"] == "trip-2253.png"
        assert "internal" not in response.text
        assert "storage_path" not in response.text
        assert "upstream.bin" not in response.text

    assert share_download.status_code == 200
    assert share_download.content == b"\x89PNG-safe"
    assert share_download.headers["content-type"] == "image/png"
    assert share_download.headers["content-disposition"] == ('attachment; filename="trip-2253.png"')
    assert pdf_download.status_code == 200
    assert pdf_download.content == b"%PDF-safe"
    assert pdf_download.headers["content-type"] == "application/pdf"
    assert pdf_download.headers["content-disposition"] == ('attachment; filename="trip-2253.pdf"')


def test_artifact_download_rejects_mime_mismatch_and_oversize(monkeypatch) -> None:
    max_bytes = 25 * 1024 * 1024

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/trip/results/2255/artifacts/share_image/download":
            return httpx.Response(
                200,
                content=b"x" * (max_bytes + 1),
                headers={"content-type": "image/png"},
            )
        responses = {
            "/trip/results/2253/artifacts/share_image/download": (
                b"%PDF-private",
                "application/pdf",
            ),
            "/trip/results/2254/artifacts/pdf/download": (
                b"\x89PNG-private",
                "image/png",
            ),
        }
        content, content_type = responses[request.url.path]
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": content_type},
        )

    app = _make_artifact_app(monkeypatch, handler, artifact_max_bytes=max_bytes)
    with TestClient(app) as client:
        share_wrong_mime = client.get("/api/trip/results/2253/artifacts/share_image/download")
        pdf_wrong_mime = client.get("/api/trip/results/2254/artifacts/pdf/download")
        oversized = client.get("/api/trip/results/2255/artifacts/share_image/download")

    for response in (share_wrong_mime, pdf_wrong_mime, oversized):
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "GENERATION_SERVICE_ERROR"
        assert "private" not in response.text
