from __future__ import annotations

import logging
import traceback

from app.core.config import Settings
from app.core.security import redact, register_secrets


class RedactingFilter(logging.Filter):
    """Scrubs secrets from every record, including formatted tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info))
                record.exc_info = None
                record.exc_text = None
            record.msg = redact(message)
            record.args = ()
        except Exception:  # never let logging break the app
            pass
        return True


def _has_filter(target: logging.Filterer) -> bool:
    return any(isinstance(f, RedactingFilter) for f in target.filters)


def setup_logging(settings: Settings) -> None:
    """Idempotent logging setup with secret redaction on every path."""
    register_secrets(settings.secret_values())

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        root.addHandler(handler)
    for handler in root.handlers:
        if not _has_filter(handler):
            handler.addFilter(RedactingFilter())

    # uvicorn logs the request line, which contains ?code=...&state=... on the OAuth callback.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not _has_filter(logger):
            logger.addFilter(RedactingFilter())

    # Keep third-party HTTP libraries quiet (their debug logs can include headers).
    for name in ("urllib3", "requests_oauthlib", "oauthlib", "httpx", "httpcore", "pymongo"):
        logging.getLogger(name).setLevel(logging.WARNING)
