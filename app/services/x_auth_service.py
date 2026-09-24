"""OAuth 2.0 (Authorization Code + PKCE) using the official `xdk` SDK.

The SDK's `OAuth2PKCEAuth` generates the PKCE verifier/challenge and the authorization
URL, exchanges the code for tokens, and refreshes tokens. This service adds:
  * CSRF `state` handling (stored in MongoDB, single use, expires),
  * persistence of the PKCE verifier between /login and /callback,
  * normalisation of the SDK's token dict into a `TokenBundle`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import Settings
from app.core.exceptions import (
    OAuthCallbackError,
    OAuthStateError,
    TokenRefreshError,
    XConfigurationError,
    XSDKError,
)
from app.core.security import redact, register_secret
from app.core.timeutils import ensure_utc, utcnow
from app.database.repositories.oauth_state_repository import OAuthStateRepository
from app.services.sdk_compat import call_flexible

logger = logging.getLogger(__name__)

DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS = 7200  # X user access tokens last 2 hours


@dataclass
class TokenBundle:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    token_type: str
    expires_at: datetime
    scopes: list[str]


def bundle_from_token(
    token: Any,
    *,
    fallback_refresh_token: str | None = None,
    fallback_scopes: list[str] | None = None,
) -> TokenBundle:
    """Normalise the token dict returned by the SDK."""
    if token is None:
        raise ValueError("empty token response")
    if not isinstance(token, dict):
        try:
            token = dict(token)
        except (TypeError, ValueError):
            token = {
                k: getattr(token, k, None)
                for k in ("access_token", "refresh_token", "token_type", "expires_at", "expires_in", "scope")
            }

    access_token = token.get("access_token")
    if not access_token:
        raise ValueError("token response did not contain an access token")

    expires_at: datetime | None = None
    raw_at = token.get("expires_at")
    if isinstance(raw_at, datetime):
        expires_at = ensure_utc(raw_at)
    elif isinstance(raw_at, (int, float)) and raw_at > 1_000_000_000:
        expires_at = datetime.fromtimestamp(raw_at, tz=timezone.utc)
    if expires_at is None:
        try:
            expires_at = utcnow() + timedelta(seconds=int(token.get("expires_in")))
        except (TypeError, ValueError):
            expires_at = utcnow() + timedelta(seconds=DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS)

    raw_scope = token.get("scope")
    if isinstance(raw_scope, str):
        scopes = [s for s in raw_scope.replace(",", " ").split() if s]
    elif isinstance(raw_scope, (list, tuple, set)):
        scopes = [str(s) for s in raw_scope]
    else:
        scopes = list(fallback_scopes or [])

    refresh_token = token.get("refresh_token") or fallback_refresh_token

    # From now on these values are scrubbed from logs and error messages.
    register_secret(str(access_token))
    register_secret(refresh_token)

    return TokenBundle(
        access_token=str(access_token),
        refresh_token=str(refresh_token) if refresh_token else None,
        token_type=str(token.get("token_type") or "bearer"),
        expires_at=expires_at,
        scopes=scopes,
    )


def _query_param(url: str, name: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(name)
    return values[0] if values else None


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class XAuthService:
    PENDING_TTL_SECONDS = 900

    def __init__(self, settings: Settings, state_repository: OAuthStateRepository) -> None:
        self._settings = settings
        self._states = state_repository
        # In-process cache of SDK auth objects keyed by state. Mongo is the source of truth
        # for state validity; this only saves re-creating the SDK object in the common case.
        self._pending: dict[str, tuple[Any, float]] = {}

        # requests-oauthlib refuses `http://` redirect responses unless this is set. Only
        # relax it for a local development redirect URI.
        redirect = settings.x_redirect_uri.lower()
        if redirect.startswith(("http://localhost", "http://127.0.0.1")):
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    # ------------------------------------------------------------- configuration
    def configuration_problems(self) -> list[str]:
        s = self._settings
        problems: list[str] = []
        if not s.x_client_id:
            problems.append("X_CLIENT_ID is not set")
        if not s.x_client_secret.get_secret_value():
            problems.append("X_CLIENT_SECRET is not set")
        if not s.x_redirect_uri:
            problems.append("X_REDIRECT_URI is not set")
        if s.missing_required_scopes:
            problems.append("X_SCOPES is missing: " + ", ".join(s.missing_required_scopes))
        return problems

    def _require_configured(self) -> None:
        problems = self.configuration_problems()
        if problems:
            raise XConfigurationError(
                "X OAuth is not configured: " + "; ".join(problems) + ". Check your .env file.",
                details=problems,
            )

    # ------------------------------------------------------------------ SDK glue
    def _new_auth(self) -> Any:
        try:
            module = importlib.import_module("xdk.oauth2_auth")
            auth_cls = module.OAuth2PKCEAuth
        except (ImportError, AttributeError) as exc:
            raise XConfigurationError(
                "The official X SDK (xdk) with OAuth2PKCEAuth is not available. "
                "Run: pip install -r requirements.txt",
                code="x_sdk_missing",
            ) from exc
        s = self._settings
        kwargs: dict[str, Any] = {
            "client_id": s.x_client_id,
            "redirect_uri": s.x_redirect_uri,
            "scope": s.scope_string,
        }
        secret = s.x_client_secret.get_secret_value()
        if secret:
            kwargs["client_secret"] = secret
        return call_flexible(auth_cls, **kwargs)

    @staticmethod
    def _read_verifier(auth: Any) -> str | None:
        getter = getattr(auth, "get_code_verifier", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:  # noqa: BLE001
                pass
        for attr in ("code_verifier", "_code_verifier"):
            value = getattr(auth, attr, None)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _apply_verifier(auth: Any, verifier: str | None) -> None:
        if not verifier:
            return
        setter = getattr(auth, "set_pkce_parameters", None)
        if callable(setter):
            try:
                call_flexible(setter, code_verifier=verifier, code_challenge=_s256(verifier))
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            auth.code_verifier = verifier
        except Exception:  # noqa: BLE001
            pass

    def _purge_pending(self) -> None:
        cutoff = time.monotonic() - self.PENDING_TTL_SECONDS
        for key in [k for k, (_, ts) in self._pending.items() if ts < cutoff]:
            self._pending.pop(key, None)

    # ------------------------------------------------------------------ login
    async def start_login(self) -> str:
        """Create the authorization URL (state + PKCE) and remember what /callback needs."""
        self._require_configured()
        self._purge_pending()
        requested_state = secrets.token_urlsafe(32)

        def _build() -> tuple[Any, str, str | None]:
            auth = self._new_auth()
            url = call_flexible(auth.get_authorization_url, state=requested_state)
            if isinstance(url, tuple):  # some OAuth libs return (url, state)
                url = url[0]
            return auth, str(url), self._read_verifier(auth)

        try:
            auth, url, verifier = await asyncio.to_thread(_build)
        except (XConfigurationError, XSDKError):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not build X authorization URL: %s", type(exc).__name__)
            raise XSDKError(
                "Could not create the X authorization URL with the SDK.", code="x_oauth_error"
            ) from exc

        state = _query_param(url, "state") or requested_state
        await self._states.save(state, verifier)
        self._pending[state] = (auth, time.monotonic())
        logger.info("OAuth login started")
        return url

    # ---------------------------------------------------------------- callback
    async def complete_login(self, *, code: str, state: str, callback_url: str) -> TokenBundle:
        self._require_configured()
        record = await self._states.pop(state)
        cached = self._pending.pop(state, None)
        if record is None:
            raise OAuthStateError()
        created = ensure_utc(record.get("created_at"))
        if created and utcnow() - created > timedelta(seconds=self._settings.oauth_state_ttl_seconds):
            raise OAuthStateError("The OAuth state has expired. Start again at /api/v1/auth/x/login.")

        verifier = record.get("code_verifier")

        def _exchange() -> Any:
            if cached is not None:
                auth = cached[0]
            else:
                auth = self._new_auth()
                self._apply_verifier(auth, verifier)
            exchange = getattr(auth, "exchange_code", None)
            if callable(exchange):
                try:
                    return call_flexible(exchange, code=code, code_verifier=verifier)
                except TypeError:
                    pass
            fetch = getattr(auth, "fetch_token", None)
            if callable(fetch):
                return call_flexible(
                    fetch, authorization_response=callback_url, code_verifier=verifier
                )
            raise XSDKError("The installed xdk OAuth2PKCEAuth cannot exchange authorization codes.")

        try:
            token = await asyncio.to_thread(_exchange)
            return bundle_from_token(token, fallback_scopes=self._settings.scopes)
        except (XConfigurationError, XSDKError):
            raise
        except Exception as exc:  # noqa: BLE001
            safe = redact(str(exc).replace(code, "[REDACTED]"))[:300]
            logger.warning("X token exchange failed: %s", type(exc).__name__)
            raise OAuthCallbackError(
                "Exchanging the authorization code for tokens failed. Start again at "
                "/api/v1/auth/x/login.",
                code="oauth_token_exchange_failed",
                details=f"{type(exc).__name__}: {safe}",
            ) from exc

    # ----------------------------------------------------------------- refresh
    async def refresh(self, refresh_token: str) -> TokenBundle:
        self._require_configured()

        def _refresh() -> Any:
            auth = self._new_auth()
            try:  # SDK variants read the refresh token from `auth.token`
                auth.token = {
                    "access_token": "",
                    "refresh_token": refresh_token,
                    "token_type": "bearer",
                }
            except Exception:  # noqa: BLE001
                pass
            fn = getattr(auth, "refresh_token", None)
            if not callable(fn):
                raise XSDKError("The installed xdk OAuth2PKCEAuth has no refresh_token().")
            return call_flexible(fn, refresh_token=refresh_token)

        try:
            token = await asyncio.to_thread(_refresh)
            return bundle_from_token(
                token,
                fallback_refresh_token=refresh_token,
                fallback_scopes=self._settings.scopes,
            )
        except XConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("X token refresh failed: %s", type(exc).__name__)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            hint = (
                " The refresh token is invalid, expired or already used."
                if status in (400, 401)
                else ""
            )
            raise TokenRefreshError(
                "Refreshing the X access token failed." + hint
                + " Re-authenticate via /api/v1/auth/x/login.",
                details=f"{type(exc).__name__}",
            ) from exc
