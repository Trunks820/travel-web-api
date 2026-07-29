from __future__ import annotations

import base64
import hmac

from src.config import Settings
from src.security.secrets import hash_secret


class InvalidHistoryCursor(Exception):
    pass


def encode_history_cursor(public_trip_id: str, settings: Settings) -> str:
    payload = public_trip_id.encode("utf-8")
    signature = hash_secret(
        public_trip_id,
        purpose="history-cursor",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )
    payload_part = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature_part = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload_part}.{signature_part}"


def decode_history_cursor(cursor: str, settings: Settings) -> str:
    try:
        payload_part, signature_part = cursor.split(".", 1)
        payload = base64.urlsafe_b64decode(
            (payload_part + "=" * (-len(payload_part) % 4)).encode("ascii")
        )
        signature = base64.urlsafe_b64decode(
            (signature_part + "=" * (-len(signature_part) % 4)).encode("ascii")
        )
        public_trip_id = payload.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidHistoryCursor from exc
    expected = hash_secret(
        public_trip_id,
        purpose="history-cursor",
        pepper=settings.secret_hash_pepper.get_secret_value(),
    )
    if not hmac.compare_digest(signature, expected) or not public_trip_id.startswith("trip_"):
        raise InvalidHistoryCursor
    return public_trip_id
