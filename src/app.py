from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text

from src.account.router import router as account_router
from src.admin.guide_cache import GuideFragmentCache
from src.admin.hermes_router import router as admin_hermes_router
from src.admin.projection_consumer import ProjectionConsumer
from src.admin.projection_router import router as admin_projection_router
from src.admin.projection_sync import run_projection_backfill
from src.admin.router import router as admin_router
from src.api.errors import ApiError, install_error_handlers
from src.auth.mailer import DirectMailOtpMailer
from src.auth.router import router as auth_router
from src.config import Settings, get_settings
from src.db.session import build_engine, build_session_factory
from src.history.router import router as history_router
from src.integrations.hermes import HermesClient
from src.middleware.request_boundary import RequestBoundaryMiddleware
from src.observability.logging import configure_logging
from src.profile.router import router as profile_router
from src.trips.router import router as trips_router


def create_app(
    settings: Settings | None = None,
    *,
    engine=None,
    hermes=None,
    mailer=None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.redaction_secrets)
    runtime_engine = engine or build_engine(settings)
    runtime_hermes = hermes or HermesClient.from_settings(settings)
    runtime_mailer = mailer or DirectMailOtpMailer(settings)
    runtime_cache = GuideFragmentCache.from_settings(settings)
    runtime_consumer = None
    consumer_task = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal runtime_consumer, consumer_task
        if settings.projection_backfill_on_start:
            await run_projection_backfill(
                _app.state.session_factory,
                runtime_hermes,
                correlation_id="projection-startup-backfill",
            )
        if settings.projection_consumer_enabled:
            runtime_consumer = ProjectionConsumer(_app.state.session_factory, settings)
            consumer_task = asyncio.create_task(runtime_consumer.run())
        try:
            yield
        finally:
            if runtime_consumer is not None:
                await runtime_consumer.stop()
            if consumer_task is not None:
                consumer_task.cancel()
                await asyncio.gather(consumer_task, return_exceptions=True)
            await runtime_cache.close()
            await runtime_hermes.close()
            await runtime_engine.dispose()

    app = FastAPI(
        title="YunTu Travel Web API",
        version=settings.app_version,
        description="Private same-origin BFF for YunTu hosted-product clients.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = runtime_engine
    app.state.session_factory = build_session_factory(runtime_engine)
    app.state.hermes = runtime_hermes
    app.state.mailer = runtime_mailer
    app.state.guide_fragment_cache = runtime_cache
    app.add_middleware(RequestBoundaryMiddleware, settings=settings)
    install_error_handlers(app)

    system_router = APIRouter()

    @system_router.get("/health", include_in_schema=False)
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "travel-web-api",
            "version": settings.app_version,
        }

    @system_router.get("/ready", include_in_schema=False)
    async def ready(request: Request) -> dict[str, bool]:
        try:
            async with request.app.state.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            await request.app.state.hermes.readiness(request.state.correlation_id)
        except Exception as exc:
            raise ApiError(
                503,
                "NOT_READY",
                "服务依赖尚未就绪。",
                retryable=True,
            ) from exc
        return {"ok": True}

    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(trips_router)
    app.include_router(history_router)
    app.include_router(account_router)
    app.include_router(profile_router)
    app.include_router(admin_router)
    app.include_router(admin_projection_router)
    app.include_router(admin_hermes_router)

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        if "HermesResult" not in schema.get("components", {}).get("schemas", {}):
            raise RuntimeError("HermesResult must be registered in OpenAPI components")
        schema["x-external-schema-resolution"] = {
            "urn:yuntu:travel-web-api:openapi:HermesResult": (
                "#/components/schemas/HermesResult"
            )
        }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app


app = create_app()
