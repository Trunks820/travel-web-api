from __future__ import annotations

import hashlib
import hmac
import secrets
import string
import unicodedata
from dataclasses import dataclass

DEFAULT_PREFIX = "user_"
DEFAULT_SUFFIX_LENGTH = 10
RENAME_COOLDOWN_DAYS = 7
FORMER_NAME_QUARANTINE_DAYS = 15

_DEFAULT_ALPHABET = string.ascii_lowercase + string.digits
_RESERVED_EXACT = frozenset(
    {
        "admin",
        "administrator",
        "owner",
        "official",
        "system",
        "support",
        "service",
        "yuntu",
        "云途",
        "管理员",
        "客服",
        "系统",
    }
)
_RESERVED_PREFIXES = ("yuntu_", "admin_", "system_", "official_", "support_", "云途")


class DisplayNameError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NormalizedDisplayName:
    presentation: str
    uniqueness_key: str


def _is_han_character(value: str) -> bool:
    name = unicodedata.name(value, "")
    return name.startswith("CJK UNIFIED IDEOGRAPH-") or name.startswith(
        "CJK COMPATIBILITY IDEOGRAPH-"
    )


def _is_allowed_character(value: str) -> bool:
    if value == "_" or value.isdigit() or _is_han_character(value):
        return True
    return unicodedata.category(value).startswith("L") and "LATIN" in unicodedata.name(value, "")


def normalize_display_name(raw_value: str) -> NormalizedDisplayName:
    presentation = unicodedata.normalize("NFKC", raw_value.strip()).strip()
    if not 2 <= len(presentation) <= 24:
        raise DisplayNameError("DISPLAY_NAME_INVALID")
    if not all(_is_allowed_character(value) for value in presentation):
        raise DisplayNameError("DISPLAY_NAME_INVALID")
    if all(value.isdigit() for value in presentation):
        raise DisplayNameError("DISPLAY_NAME_INVALID")

    uniqueness_key = presentation.casefold()
    if uniqueness_key in _RESERVED_EXACT or uniqueness_key.startswith(_RESERVED_PREFIXES):
        raise DisplayNameError("DISPLAY_NAME_RESERVED")
    return NormalizedDisplayName(presentation, uniqueness_key)


def generate_default_display_name() -> NormalizedDisplayName:
    suffix = "".join(secrets.choice(_DEFAULT_ALPHABET) for _ in range(DEFAULT_SUFFIX_LENGTH))
    return NormalizedDisplayName(DEFAULT_PREFIX + suffix, DEFAULT_PREFIX + suffix)


def former_name_digest(normalized_name: str, *, pepper: str) -> bytes:
    purpose_bound_value = b"display-name-quarantine:v1\x00" + normalized_name.encode("utf-8")
    return hmac.new(pepper.encode("utf-8"), purpose_bound_value, hashlib.sha256).digest()
