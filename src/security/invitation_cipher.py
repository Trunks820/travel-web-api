from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENVELOPE_VERSION = 1
_NONCE_BYTES = 12
_KEY_CONTEXT = b"yuntu:invitation-code-encryption:v1"


class InvitationCipherError(ValueError):
    pass


def _key(pepper: str) -> bytes:
    return hmac.digest(pepper.encode("utf-8"), _KEY_CONTEXT, hashlib.sha256)


def _aad(public_id: str) -> bytes:
    return f"yuntu:invitation-code:{public_id}".encode()


def encrypt_invitation_code(raw_code: str, *, public_id: str, pepper: str) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(_key(pepper)).encrypt(
        nonce,
        raw_code.encode("utf-8"),
        _aad(public_id),
    )
    return bytes([_ENVELOPE_VERSION]) + nonce + ciphertext


def decrypt_invitation_code(envelope: bytes, *, public_id: str, pepper: str) -> str:
    if len(envelope) <= 1 + _NONCE_BYTES or envelope[0] != _ENVELOPE_VERSION:
        raise InvitationCipherError("unsupported invitation ciphertext envelope")
    nonce = envelope[1 : 1 + _NONCE_BYTES]
    ciphertext = envelope[1 + _NONCE_BYTES :]
    try:
        plaintext = AESGCM(_key(pepper)).decrypt(nonce, ciphertext, _aad(public_id))
    except InvalidTag as exc:
        raise InvitationCipherError("invitation ciphertext authentication failed") from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvitationCipherError("invitation ciphertext is not UTF-8") from exc
