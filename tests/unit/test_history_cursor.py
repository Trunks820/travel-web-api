import pytest

from src.config import Settings
from src.history.cursor import (
    InvalidHistoryCursor,
    decode_history_cursor,
    encode_history_cursor,
)


def test_history_cursor_round_trip_and_tamper_detection() -> None:
    settings = Settings(
        app_env="test",
        secret_hash_pepper="cursor-test-pepper",
    )
    cursor = encode_history_cursor("trip_public-safe", settings)
    assert decode_history_cursor(cursor, settings) == "trip_public-safe"
    payload, signature = cursor.split(".", 1)
    tampered = f"{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    with pytest.raises(InvalidHistoryCursor):
        decode_history_cursor(tampered, settings)
    with pytest.raises(InvalidHistoryCursor):
        decode_history_cursor("not-base64", settings)
