from __future__ import annotations

import pytest

from src.security.invitation_cipher import (
    InvitationCipherError,
    decrypt_invitation_code,
    encrypt_invitation_code,
)


def test_invitation_cipher_round_trip_binds_public_id() -> None:
    raw_code = "YT-ABCD-EFGH"
    encrypted = encrypt_invitation_code(
        raw_code,
        public_id="code_one",
        pepper="test-pepper",
    )

    assert raw_code.encode() not in encrypted
    assert (
        decrypt_invitation_code(
            encrypted,
            public_id="code_one",
            pepper="test-pepper",
        )
        == raw_code
    )

    with pytest.raises(InvitationCipherError):
        decrypt_invitation_code(
            encrypted,
            public_id="code_other",
            pepper="test-pepper",
        )


def test_invitation_cipher_rejects_tampering() -> None:
    encrypted = bytearray(
        encrypt_invitation_code(
            "YT-2345-6789",
            public_id="code_tampered",
            pepper="test-pepper",
        )
    )
    encrypted[-1] ^= 1

    with pytest.raises(InvitationCipherError):
        decrypt_invitation_code(
            bytes(encrypted),
            public_id="code_tampered",
            pepper="test-pepper",
        )
