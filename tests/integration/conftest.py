from __future__ import annotations

import os

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app import create_app
from src.auth.mailer import EmailDeliveryError
from src.config import Settings
from src.integrations.hermes_models import (
    HermesArtifact,
    HermesJobStatus,
    HermesPlaceDetail,
    HermesPlaceList,
    HermesResult,
    HermesTripCreated,
)


class FakeHermes:
    def __init__(self) -> None:
        self.jobs_by_request: dict[str, str] = {}
        self.job_payloads: dict[str, dict] = {}
        self.create_calls: list[dict] = []
        self.status_calls: list[str] = []
        self.result_calls: list[tuple[int, str]] = []
        self.artifact_calls: list[tuple[str, int, str]] = []
        self.artifact_download_calls: list[tuple[int, str]] = []
        self.stream_calls: list[str] = []
        self.create_error: Exception | None = None
        self.status_error: Exception | None = None
        self.stream_error: Exception | None = None
        self.stream_events: dict[str, list[tuple[str, dict]]] = {}

    async def readiness(self, _correlation_id: str) -> None:
        return None

    async def create_trip(
        self,
        *,
        trip_request,
        upstream_request_id,
        conversation_id,
        correlation_id,
    ):
        del correlation_id
        if self.create_error:
            raise self.create_error
        self.create_calls.append(
            {
                "trip_request": trip_request,
                "request_id": upstream_request_id,
                "conversation_id": conversation_id,
                "source": "travel-web-api",
            }
        )
        job_id = self.jobs_by_request.setdefault(
            upstream_request_id,
            f"hermes-job-{len(self.jobs_by_request) + 1}",
        )
        self.job_payloads.setdefault(
            job_id,
            {
                "ok": True,
                "job_id": job_id,
                "status": "PENDING",
                "current_stage": "QUEUED",
            },
        )
        return HermesTripCreated(job_id=job_id, status="PENDING")

    async def job_status(self, job_id: str, _correlation_id: str):
        if self.status_error:
            raise self.status_error
        self.status_calls.append(job_id)
        return HermesJobStatus.model_validate(self.job_payloads[job_id])

    async def result(self, result_record_id: int, *, job_id: str, correlation_id: str):
        del correlation_id
        self.result_calls.append((result_record_id, job_id))
        return HermesResult.model_validate(
            {
                "schema_version": "1.5",
                "result_id": result_record_id,
                "city": {"name": "重庆"},
                "request": {
                    "to_city": "重庆",
                    "days": 3,
                    "people_count": 2,
                    "preferences": ["美食"],
                    "avoid": [],
                },
                "weather": {"status": "skipped_disabled", "city": "重庆", "days": []},
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
                        "provider_payload": {"nested_secret": True},
                    }
                ],
                "provider_payload": {"secret": "must-be-dropped"},
            }
        )

    async def artifact(
        self,
        method: str,
        result_record_id: int,
        artifact_type: str,
        *,
        correlation_id: str,
    ):
        del correlation_id
        self.artifact_calls.append((method, result_record_id, artifact_type))
        mime_type = {
            "pdf": "application/pdf",
            "share_image": "image/png",
        }[artifact_type]
        return HermesArtifact.model_validate(
            {
                "ok": True,
                "artifact_id": "artifact-safe",
                "result_record_id": result_record_id,
                "artifact_type": artifact_type,
                "status": "ready",
                "download_url": "http://internal/raw/path",
                "filename": "../../internal.bin",
                "mime_type": mime_type,
                "byte_size": 8,
                "metadata": {
                    "export_version": "1",
                    "storage_path": "/private/raw",
                },
            }
        )

    async def artifact_bytes(
        self,
        result_record_id: int,
        artifact_type: str,
        *,
        correlation_id: str,
        max_bytes: int,
    ):
        del correlation_id, max_bytes
        self.artifact_download_calls.append((result_record_id, artifact_type))
        if artifact_type == "share_image":
            return b"\x89PNG-safe", "image/png", 'attachment; filename="../../secret.png"'
        return b"%PDF-safe", "application/pdf", 'attachment; filename="../../secret.pdf"'

    async def stream_job(self, job_id: str, _correlation_id: str):
        self.stream_calls.append(job_id)
        for event, payload in self.stream_events.get(job_id, []):
            yield event, dict(payload)
        if self.stream_error:
            raise self.stream_error

    async def places(self, *, city: str, limit: int, correlation_id: str):
        del correlation_id
        return HermesPlaceList.model_validate(
            {
                "ok": True,
                "city": city,
                "places": [
                    {
                        "place_id": 1,
                        "name": "测试地点",
                        "place_type": "attraction",
                    }
                ][:limit],
            }
        )

    async def place(self, place_id: int, *, correlation_id: str):
        del correlation_id
        return HermesPlaceDetail.model_validate(
            {
                "place_id": place_id,
                "name": "测试地点",
                "place_type": "attraction",
                "top_reasons": ["值得参观"],
            }
        )

    async def close(self) -> None:
        return None


class CapturingMailer:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.fail = False

    async def send_otp(self, *, email: str, code: str, purpose: str) -> None:
        if self.fail:
            raise EmailDeliveryError("simulated delivery failure")
        self.messages.append({"email": email, "code": code, "purpose": purpose})


@pytest.fixture
async def pg_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.fail(
            "TEST_DATABASE_URL is required; PostgreSQL integration tests may not use SQLite"
        )
    parsed = make_url(database_url)
    if not parsed.database or not parsed.database.startswith("travel_web_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable travel_web_test* database")
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_database(pg_engine):
    async with pg_engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE "
                "trip_quota_entry, user_trip, "
                "email_otp_challenge, invitation_redemption, user_session, "
                "quota_grant, user_identity, invitation, app_user "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        database_url=os.environ["TEST_DATABASE_URL"],
        secret_hash_pepper="integration-test-pepper",
        hermes_internal_credential="integration-hermes-secret",
        cookie_secure=True,
        user_origin="https://kakarot8.com",
        admin_origin="https://admin.kakarot8.com",
        otp_max_attempts=2,
        otp_resend_seconds=60,
    )


@pytest.fixture
def mailer() -> CapturingMailer:
    return CapturingMailer()


@pytest.fixture
def hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def app(pg_engine, test_settings, mailer, hermes):
    return create_app(
        test_settings,
        engine=pg_engine,
        hermes=hermes,
        mailer=mailer,
    )


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://kakarot8.com",
    ) as value:
        yield value


@pytest.fixture
def session_factory(pg_engine):
    return async_sessionmaker(pg_engine, expire_on_commit=False)
