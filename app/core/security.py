"""Secret redaction helpers.

Everything that leaves the process (logs, error responses) can be passed through
`redact()` so that tokens, authorization codes, client secrets and database
credentials are never exposed, even if they end up inside an exception message.
"""
from __future__ import annotations

import re
import threading

REDACTED = "[REDACTED]"

_registered: set[str] = set()
_lock = threading.Lock()

_KEY_VALUE = re.compile(
    r"""(?ix)
    \b(access_token|refresh_token|client_secret|code_verifier|authorization_code|id_token|password)\b
    (["']?\s*[:=]\s*["']?)
    ([^\s"'&,;}\]]+)
    """
)
_QUERY_PARAM = re.compile(r"(?i)([?&](?:code|state|code_verifier|access_token|refresh_token)=)[^\s&\"']+")
_AUTH_HEADER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9\-._~+/=]{8,}")
_MONGO_CREDENTIALS = re.compile(r"(?i)(mongodb(?:\+srv)?://)[^/@\s]+@")


def register_secret(value: str | None) -> None:
    """Remember an exact secret value so it is scrubbed wherever it appears."""
    if value and len(value) >= 6:
        with _lock:
            _registered.add(value)


def register_secrets(values: list[str]) -> None:
    for v in values:
        register_secret(v)


def redact(text: str | None) -> str:
    if not text:
        return text or ""
    with _lock:
        known = sorted(_registered, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, REDACTED)
    text = _MONGO_CREDENTIALS.sub(rf"\1{REDACTED}@", text)
    text = _KEY_VALUE.sub(rf"\1\2{REDACTED}", text)
    text = _QUERY_PARAM.sub(rf"\1{REDACTED}", text)
    text = _AUTH_HEADER.sub(rf"\1 {REDACTED}", text)
    return text
