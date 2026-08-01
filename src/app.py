from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import text

from src.account.router import router as account_router
from src.admin.hermes_router import router as admin_hermes_router
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
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
    app.include_router(admin_hermes_router)
    return app


app = create_app()
