from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app import create_app
from src.auth.mailer import EmailDeliveryError
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
        self.admin_calls: list[tuple[str, object]] = []
        self.admin_error: Exception | None = None
        self.admin_download_error: Exception | None = None
        now = datetime.now(UTC)
        self.admin_job_item = {
            "job_id": "hermes-admin-job-1",
            "result_record_id": "9001",
            "status": "FAILED",
            "current_stage": "FAILED",
            "city": "重庆",
            "result_type": None,
            "safe_error": {
                "code": "PUBLISH_GATE_FAILED",
                "message": "攻略未通过发布校验",
            },
            "detailed_reason": "publish_gate_failed",
            "created_at": now - timedelta(seconds=181),
            "started_at": now - timedelta(seconds=180),
            "finished_at": now,
            "total_duration_ms": 181_000,
            "retry_count": 1,
            "failed_draft_available": True,
            "steps": [
                {
                    "stage": "FINAL_WRITER",
                    "status": "SUCCESS",
                    "attempt": 1,
                    "publish_retry_round": 0,
                    "started_at": now - timedelta(seconds=2),
                    "finished_at": now - timedelta(seconds=1),
                    "duration_ms": 1_000,
                }
            ],
        }
        self.admin_failed_draft_payload = {
            "job_id": "hermes-admin-job-1",
            "created_at": now,
            "plans": [
                {
                    "plan_name": "诊断草稿",
                    "summary": "安全摘要",
                    "plan_text": "未发布安全正文",
                    "used_place_names": ["地点A"],
                    "day_place_names": [["地点A"]],
                }
            ],
        }
        self.admin_artifact_item = {
            "artifact_id": "artifact-admin-1",
            "result_record_id": "9001",
            "artifact_type": "pdf",
            "status": "READY",
            "filename": "重庆攻略.pdf",
            "mime_type": "application/pdf",
            "byte_size": 9,
            "sha256": "abc",
            "text_length": 10,
            "width_px": None,
            "height_px": None,
            "page_count": 1,
            "attempt_count": 1,
            "safe_error": None,
            "created_at": now,
            "started_at": now,
            "finished_at": now,
            "expires_at": now + timedelta(days=1),
        }
        self.admin_download = (b"%PDF-safe", "application/pdf")

    async def readiness(self, _correlation_id: str) -> None:
        return None

    def _raise_admin_error(self) -> None:
        if self.admin_error:
            raise self.admin_error

    async def admin_trip_jobs(self, *, correlation_id: str, params: dict):
        self._raise_admin_error()
        self.admin_calls.append(("trip_jobs", dict(params)))
        return HermesAdminTripJobList.model_validate(
            {
                "ok": True,
                "contract_version": "v1",
                "request_id": correlation_id,
                "page": params["page"],
                "limit": params["limit"],
                "total": 1,
                "items": [self.admin_job_item],
            }
        )

    async def admin_trip_job(self, job_id: str, *, correlation_id: str):
        self._raise_admin_error()
        self.admin_calls.append(("trip_job", job_id))
        return HermesAdminTripJobDetail.model_validate(
            {
                "ok": True,
                "contract_version": "v1",
                "request_id": correlation_id,
                "trip_job": {**self.admin_job_item, "job_id": job_id},
            }
        )

    async def admin_failed_draft(self, job_id: str, *, correlation_id: str):
        self._raise_admin_error()
        self.admin_calls.append(("failed_draft", job_id))
        return HermesAdminFailedDraftDetail.model_validate(
            {
                "ok": True,
                "contract_version": "v1",
                "request_id": correlation_id,
                "failed_draft": {
                    **self.admin_failed_draft_payload,
                    "job_id": job_id,
                },
            }
        )

    async def admin_artifacts(self, *, correlation_id: str, params: dict):
        self._raise_admin_error()
        self.admin_calls.append(("artifacts", dict(params)))
        return HermesAdminArtifactList.model_validate(
            {
                "ok": True,
                "contract_version": "v1",
                "request_id": correlation_id,
                "page": params["page"],
                "limit": params["limit"],
                "total": 1,
                "items": [self.admin_artifact_item],
            }
        )

    async def admin_artifact(self, artifact_id: str, *, correlation_id: str):
        self._raise_admin_error()
        self.admin_calls.append(("artifact", artifact_id))
        return HermesAdminArtifactDetail.model_validate(
            {
                "ok": True,
                "contract_version": "v1",
                "request_id": correlation_id,
                "artifact": {
                    **self.admin_artifact_item,
                    "artifact_id": artifact_id,
                },
            }
        )

    async def admin_artifact_bytes(
        self,
        artifact_id: str,
        *,
        correlation_id: str,
        max_bytes: int,
    ):
        del correlation_id, max_bytes
        self.admin_calls.append(("artifact_download", artifact_id))
        if self.admin_download_error:
            raise self.admin_download_error
        return self.admin_download

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
                    "from_city": "成都",
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
                                    "availability_checked_at": "2026-08-03T10:00:00+08:00",
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
                "admin_projection_reconciliation, "
                "admin_projection_backfill_checkpoint, admin_projection_event, "
                "admin_trip_step_projection, admin_trip_projection, "
                "display_name_quarantine, admin_audit_log, quota_adjustment, "
                "admin_idempotency, "
                "trip_quota_entry, user_trip, "
                "email_otp_challenge, invitation_redemption, user_session, "
                "quota_grant, user_identity, invitation, invitation_batch, app_user "
                "RESTART IDENTITY CASCADE"
            )
        )
        await connection.execute(
            text(
                "UPDATE admin_projection_consumer_state SET "
                "applied_high_watermark = 0, latest_heartbeat_watermark = NULL, "
                "latest_heartbeat_observed_at = NULL, sync_checked_at = NULL, "
                "schema_version = '1.0', next_expected_sequence = 1, "
                "stream_state = 'ACTIVE', last_reconciliation_at = NULL, "
                "initialization_state = 'UNINITIALIZED' WHERE id = 1"
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
        hermes_bff_internal_admin_credential="integration-admin-hermes-secret",
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
