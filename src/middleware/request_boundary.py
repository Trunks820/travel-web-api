from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.errors import error_envelope
from src.config import Settings

logger = logging.getLogger("travel_web_api.request")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _header_map(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").casefold(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


class RequestBoundaryMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        headers = _header_map(scope)
        supplied_request_id = headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _SAFE_REQUEST_ID.fullmatch(supplied_request_id)
            else str(uuid.uuid4())
        )
        scope.setdefault("state", {})["correlation_id"] = request_id
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        boundary_error = self._validate_boundary(path, method, headers)
        if boundary_error is not None:
            status, code, message = boundary_error
            response = JSONResponse(
                status_code=status,
                content=error_envelope(code, message),
                headers={"X-Request-ID": request_id},
            )
            await response(scope, receive, send)
            return

        response_status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                mutable_headers = list(message.get("headers", []))
                mutable_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = mutable_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request_complete",
                extra={
                    "correlation_id": request_id,
                    "method": method,
                    "path": path,
                    "status": response_status,
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )

    def _validate_boundary(
        self,
        path: str,
        method: str,
        headers: dict[str, str],
    ) -> tuple[int, str, str] | None:
        content_length = headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > self.settings.request_max_bytes
            except ValueError:
                too_large = True
            if too_large:
                return 413, "REQUEST_TOO_LARGE", "请求内容过大。"

        if not path.startswith("/api/") or method not in _MUTATING_METHODS:
            return None
        origin = headers.get("origin", "").rstrip("/")
        if origin not in self.settings.allowed_origins:
            return 403, "ORIGIN_REJECTED", "请求来源无效。"

        has_body = int(headers.get("content-length", "0") or "0") > 0
        if method in {"POST", "PUT", "PATCH"} and has_body:
            media_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if media_type != "application/json":
                return 415, "JSON_REQUIRED", "请求必须使用 application/json。"
        return None
