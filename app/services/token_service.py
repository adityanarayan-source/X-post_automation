"""Token lifecycle: load -> check expiry (with buffer) -> refresh -> persist."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, TypeVar

from app.core.config import Settings
from app.core.exceptions import AuthenticationRequiredError, XSDKError
from app.core.timeutils import ensure_utc, utcnow
from app.database.repositories.token_repository import TokenRepository
from app.services.x_auth_service import (
    DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
    TokenBundle,
    XAuthService,
)
from app.core.security import register_secret

logger = logging.getLogger(__name__)
T = TypeVar("T")

PROVIDER = "x"


class TokenService:
    def __init__(
        self,
        repository: TokenRepository,
        auth_service: XAuthService,
        settings: Settings,
    ) -> None:
        self._repo = repository
        self._auth = auth_service
        self._settings = settings
        # X refresh tokens are single-use (they rotate), so refreshes must never overlap.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ storage
    async def store_tokens(self, bundle: TokenBundle) -> None:
        await self._repo.upsert(
            provider=PROVIDER,
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            token_type=bundle.token_type,
            expires_at=bundle.expires_at,
            scopes=bundle.scopes,
        )

    async def _load_or_bootstrap(self) -> dict[str, Any]:
        doc = await self._repo.get(PROVIDER)
        if doc:
            return doc

        access = self._settings.x_access_token.get_secret_value()
        refresh = self._settings.x_refresh_token.get_secret_value()
        if not access and not refresh:
            raise AuthenticationRequiredError()

        register_secret(access)
        register_secret(refresh)
        now = utcnow()
        # With a refresh token we force a refresh on first use (real expiry is unknown).
        expires_at = now if refresh else now + timedelta(seconds=DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS)
        await self._repo.upsert(
            provider=PROVIDER,
            access_token=access,
            refresh_token=refresh or None,
            token_type="bearer",
            expires_at=expires_at,
            scopes=self._settings.scopes,
        )
        logger.info("Seeded X tokens from environment into MongoDB")
        return {
            "provider": PROVIDER,
            "access_token": access,
            "refresh_token": refresh or None,
            "token_type": "bearer",
            "expires_at": expires_at,
            "scopes": self._settings.scopes,
        }

    # ------------------------------------------------------------------- expiry
    def _needs_refresh(self, doc: dict[str, Any]) -> bool:
        expires_at = ensure_utc(doc.get("expires_at"))
        if expires_at is None or not doc.get("access_token"):
            return True
        buffer = timedelta(seconds=self._settings.token_refresh_buffer_seconds)
        return expires_at - buffer <= utcnow()

    # ------------------------------------------------------------------ public
    async def get_valid_access_token(
        self, *, force_refresh: bool = False, rejected_token: str | None = None
    ) -> str:
        """Return a usable access token, refreshing (and persisting) it when required."""
        doc = await self._load_or_bootstrap()
        if not force_refresh and not self._needs_refresh(doc):
            return doc["access_token"]

        async with self._lock:
            # Another request may have refreshed while we waited for the lock.
            doc = await self._load_or_bootstrap()
            if force_refresh:
                already_rotated = (
                    rejected_token is not None
                    and doc.get("access_token") != rejected_token
                    and not self._needs_refresh(doc)
                )
                if already_rotated:
                    return doc["access_token"]
            elif not self._needs_refresh(doc):
                return doc["access_token"]
            return await self._refresh(doc)

    async def _refresh(self, doc: dict[str, Any]) -> str:
        refresh_token = doc.get("refresh_token")
        if not refresh_token:
            raise AuthenticationRequiredError(
                "The X access token expired and no refresh token is stored. "
                "Re-authenticate via /api/v1/auth/x/login."
            )
        bundle = await self._auth.refresh(refresh_token)  # raises TokenRefreshError
        await self.store_tokens(bundle)
        logger.info("X access token refreshed")
        return bundle.access_token

    async def with_valid_token(self, operation: Callable[[str], Awaitable[T]]) -> T:
        """Run `operation(access_token)`; on a 401 from X refresh once and retry once."""
        token = await self.get_valid_access_token()
        try:
            return await operation(token)
        except XSDKError as exc:
            if exc.x_status_code != 401:
                raise
        logger.info("X rejected the access token; forcing a refresh and retrying once")
        token = await self.get_valid_access_token(force_refresh=True, rejected_token=token)
        try:
            return await operation(token)
        except XSDKError as exc:
            if exc.x_status_code == 401:
                raise AuthenticationRequiredError(
                    "X keeps rejecting the stored credentials. "
                    "Re-authenticate via /api/v1/auth/x/login."
                ) from exc
            raise

    async def status(self) -> dict[str, Any]:
        """Non-sensitive token status for health checks."""
        doc = await self._repo.get(PROVIDER)
        if not doc:
            return {"stored": False}
        expires_at = ensure_utc(doc.get("expires_at"))
        return {
            "stored": True,
            "has_refresh_token": bool(doc.get("refresh_token")),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "expired": bool(expires_at and expires_at <= utcnow()),
            "scopes": doc.get("scopes", []),
        }
