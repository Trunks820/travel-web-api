from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|token|secret|password|otp|invitation|email|notes|credential)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


class Redactor:
    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        self._secrets = tuple(
            sorted((value for value in secret_values if value), key=len, reverse=True)
        )

    def redact(self, value: Any, key: str | None = None) -> Any:
        if key and _SENSITIVE_KEY.search(key):
            return "[REDACTED]"
        if isinstance(value, Mapping):
            return {
                str(child_key): self.redact(child, str(child_key))
                for child_key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            redacted = _PHONE.sub("[REDACTED_PHONE]", _EMAIL.sub("[REDACTED_EMAIL]", value))
            for secret in self._secrets:
                redacted = redacted.replace(secret, "[REDACTED_SECRET]")
            return redacted
        return value


class JsonFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": self._redactor.redact(record.getMessage()),
        }
        for field in (
            "correlation_id",
            "method",
            "path",
            "status",
            "latency_ms",
            "error_type",
        ):
            if hasattr(record, field):
                payload[field] = self._redactor.redact(getattr(record, field), field)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str, secret_values: tuple[str, ...] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(Redactor(secret_values)))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
