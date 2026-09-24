"""In-memory fakes so tests never touch MongoDB or the real X API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.exceptions import OAuthStateError
from app.core.timeutils import utcnow
from app.services.x_auth_service import TokenBundle
from app.services.x_client import CreatedPost


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        app_env="testing",
        debug=False,
        x_client_id="test-client-id",
        x_client_secret="test-client-secret",
        x_access_token="",
        x_refresh_token="",
        x_redirect_uri="http://localhost:8000/api/v1/auth/x/callback",
        token_refresh_buffer_seconds=300,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


class InMemoryTokenRepository:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def get(self, provider: str = "x") -> dict[str, Any] | None:
        doc = self.docs.get(provider)
        return dict(doc) if doc else None

    async def upsert(
        self,
        *,
        provider: str,
        access_token: str,
        refresh_token: str | None,
        token_type: str,
        expires_at: datetime,
        scopes: list[str],
    ) -> None:
        now = utcnow()
        existing = self.docs.get(provider, {})
        self.docs[provider] = {
            "provider": provider,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": token_type,
            "expires_at": expires_at,
            "scopes": scopes,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }

    def seed(
        self,
        *,
        access_token: str = "access-1",
        refresh_token: str | None = "refresh-1",
        expires_in: int = 3600,
    ) -> None:
        now = utcnow()
        self.docs["x"] = {
            "provider": "x",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_at": now + timedelta(seconds=expires_in),
            "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"],
            "created_at": now,
            "updated_at": now,
        }


class InMemoryPostRepository:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.fail_on_create: Exception | None = None

    async def create(self, record: dict[str, Any]) -> None:
        if self.fail_on_create:
            raise self.fail_on_create
        self.docs[record["x_post_id"]] = dict(record)

    async def get(self, x_post_id: str) -> dict[str, Any] | None:
        doc = self.docs.get(x_post_id)
        return dict(doc) if doc else None

    async def list(self, *, page: int, page_size: int, status: str | None = None):
        items = sorted(self.docs.values(), key=lambda d: d["created_at"], reverse=True)
        if status:
            items = [d for d in items if d["status"] == status]
        start = (page - 1) * page_size
        return [dict(d) for d in items[start : start + page_size]], len(items)

    async def mark_deleted(self, x_post_id: str) -> dict[str, Any] | None:
        doc = self.docs.get(x_post_id)
        if not doc:
            return None
        doc.update(status="deleted", updated_at=utcnow(), error_message=None)
        return dict(doc)

    async def set_error(self, x_post_id: str, message: str) -> None:
        if x_post_id in self.docs:
            self.docs[x_post_id]["error_message"] = message


class InMemoryOAuthStateRepository:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def save(self, state: str, code_verifier: str | None) -> None:
        self.docs[state] = {"state": state, "code_verifier": code_verifier, "created_at": utcnow()}

    async def pop(self, state: str) -> dict[str, Any] | None:
        return self.docs.pop(state, None)


class FakeXClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.tokens_used: list[str] = []
        self.create_errors: list[Exception] = []
        self.delete_errors: list[Exception] = []
        self.me_result: dict[str, Any] = {"id": "42", "name": "Test User", "username": "test_user"}
        self._counter = 0

    async def create_post(self, access_token: str, text: str) -> CreatedPost:
        self.tokens_used.append(access_token)
        if self.create_errors:
            raise self.create_errors.pop(0)
        self._counter += 1
        self.created.append(text)
        return CreatedPost(post_id=str(1000 + self._counter), text=text)

    async def delete_post(self, access_token: str, post_id: str) -> bool:
        self.tokens_used.append(access_token)
        if self.delete_errors:
            raise self.delete_errors.pop(0)
        self.deleted.append(post_id)
        return True

    async def get_me(self, access_token: str) -> dict[str, Any]:
        self.tokens_used.append(access_token)
        return dict(self.me_result)


class FakeXAuthService:
    GOOD_STATE = "good-state"

    def __init__(self) -> None:
        self.refresh_calls: list[str] = []
        self.refresh_error: Exception | None = None
        self.problems: list[str] = []
        self.completed: list[dict[str, str]] = []

    def configuration_problems(self) -> list[str]:
        return list(self.problems)

    async def start_login(self) -> str:
        return "https://x.com/i/oauth2/authorize?response_type=code&state=" + self.GOOD_STATE

    async def complete_login(self, *, code: str, state: str, callback_url: str) -> TokenBundle:
        if state != self.GOOD_STATE:
            raise OAuthStateError()
        self.completed.append({"code": code, "state": state})
        return TokenBundle(
            access_token="new-access-token-value",
            refresh_token="new-refresh-token-value",
            token_type="bearer",
            expires_at=utcnow() + timedelta(hours=2),
            scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
        )

    async def refresh(self, refresh_token: str) -> TokenBundle:
        self.refresh_calls.append(refresh_token)
        await asyncio.sleep(0)  # let concurrent callers interleave
        if self.refresh_error:
            raise self.refresh_error
        n = len(self.refresh_calls)
        return TokenBundle(
            access_token=f"access-refreshed-{n}",
            refresh_token=f"refresh-rotated-{n}",
            token_type="bearer",
            expires_at=utcnow() + timedelta(hours=2),
            scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
        )


class FakeMongo:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.indexes_ready = True

    async def ping(self) -> bool:
        return self.healthy

    async def ensure_indexes(self) -> None:
        self.indexes_ready = True
