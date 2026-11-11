from __future__ import annotations

import logging
import re
import sys

# Browsers can't set WebSocket headers, so the kiosk passes its token in the query string;
# uvicorn logs request lines verbatim. Redact anything that looks like a credential.
_SECRET_RE = re.compile(r"(?i)((?:token|key|secret|password|code)=)[^&\s\"']+")


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = _SECRET_RE.sub(r"\1<redacted>", msg)
        if redacted != msg:
            record.msg, record.args = redacted, None
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    )
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
