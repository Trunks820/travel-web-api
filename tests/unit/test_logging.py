import json
import logging

from src.observability.logging import JsonFormatter, Redactor


def test_redactor_removes_nested_secrets_and_personal_data() -> None:
    redactor = Redactor(("configured-secret",))
    result = redactor.redact(
        {
            "cookie": "raw-cookie",
            "nested": {"otp": "123456"},
            "message": "alice@example.com 13800138000 configured-secret",
        }
    )
    assert result == {
        "cookie": "[REDACTED]",
        "nested": {"otp": "[REDACTED]"},
        "message": "[REDACTED_EMAIL] [REDACTED_PHONE] [REDACTED_SECRET]",
    }


def test_json_formatter_redacts_message_content() -> None:
    formatter = JsonFormatter(Redactor(("configured-secret",)))
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "alice@example.com configured-secret",
        (),
        None,
    )
    payload = json.loads(formatter.format(record))
    assert "alice@example.com" not in payload["message"]
    assert "configured-secret" not in payload["message"]
