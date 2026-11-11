import logging

from jarvis_brain.logging_setup import RedactSecrets


def test_redacts_query_tokens():
    rec = logging.LogRecord(
        "x", logging.INFO, "f", 1, '"WebSocket /ws/ui?token=%s" ok', ("abc123",), None
    )
    RedactSecrets().filter(rec)
    assert "abc123" not in rec.getMessage()
    assert "token=<redacted>" in rec.getMessage()
