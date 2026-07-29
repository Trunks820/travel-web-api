from __future__ import annotations

import hashlib
import hmac
import secrets


def new_opaque_id(prefix: str, *, bytes_of_entropy: int = 18) -> str:
    return f"{prefix}{secrets.token_urlsafe(bytes_of_entropy)}"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_otp_code(digits: int) -> str:
    lower = 10 ** (digits - 1)
    return str(lower + secrets.randbelow(9 * lower))


def hash_secret(raw: str, *, purpose: str, pepper: str) -> bytes:
    key = pepper.encode("utf-8")
    message = f"{purpose}\0{raw}".encode()
    return hmac.new(key, message, hashlib.sha256).digest()


def secret_matches(raw: str, expected: bytes, *, purpose: str, pepper: str) -> bool:
    actual = hash_secret(raw, purpose=purpose, pepper=pepper)
    return hmac.compare_digest(actual, expected)
