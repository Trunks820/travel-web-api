from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("travel_web_api.errors")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(code)


def error_envelope(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def install_error_handlers(app: FastAPI) -> None:
    def sensitive_headers(request: Request) -> dict[str, str] | None:
        path = request.url.path
        if (
            path.endswith("/email")
            or path.endswith("/failed-draft")
            or path.endswith("/download")
            or path.endswith("/guide-review")
        ):
            return {"Cache-Control": "private, no-store"}
        return None

    def add_admin_request_id(request: Request, content: dict[str, Any]) -> None:
        if request.url.path.startswith("/api/admin/"):
            content["request_id"] = request.state.correlation_id

    async def audit_admin_failure(request: Request, code: str) -> None:
        if (
            not request.url.path.startswith("/api/admin/")
            or request.method not in {"POST", "PUT", "PATCH", "DELETE"}
            or not hasattr(request.state, "admin_context")
        ):
            return
        try:
            from src.admin.audit import append_admin_audit

            context = request.state.admin_context
            route = request.scope.get("route")
            target_id = getattr(route, "path", request.url.path)
            async with request.app.state.session_factory() as session, session.begin():
                await append_admin_audit(
                    session,
                    request.app.state.settings,
                    actor_user_id=context.user.id,
                    actor_identity=context.product_identity,
                    action="ADMIN_WRITE_FAILED",
                    target_type="ENDPOINT",
                    target_id=target_id,
                    result="FAILURE",
                    error_code=code,
                    request_id=request.state.correlation_id,
                    source_ip=request.client.host if request.client else "unknown",
                    idempotency_key=getattr(
                        request.state,
                        "admin_idempotency_key",
                        None,
                    ),
                )
        except Exception:
            logger.exception("admin_failure_audit_failed")

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        await audit_admin_failure(request, exc.code)
        content = error_envelope(exc.code, exc.message, exc.retryable)
        add_admin_request_id(request, content)
        content.update(exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=sensitive_headers(request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        await audit_admin_failure(request, "VALIDATION_ERROR")
        content = error_envelope("VALIDATION_ERROR", "请求参数无效。")
        add_admin_request_id(request, content)
        return JSONResponse(
            status_code=422,
            content=content,
            headers=sensitive_headers(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(
                status_code=404,
                content=error_envelope("NOT_FOUND", "请求的资源不存在。"),
                headers=sensitive_headers(request),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope("BAD_REQUEST", "请求无法处理。"),
            headers=sensitive_headers(request),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        await audit_admin_failure(request, "INTERNAL_ERROR")
        logger.error(
            "unhandled_exception",
            extra={"error_type": type(exc).__name__},
        )
        content = error_envelope("INTERNAL_ERROR", "服务暂时不可用。", True)
        add_admin_request_id(request, content)
        return JSONResponse(
            status_code=500,
            content=content,
            headers=sensitive_headers(request),
        )
