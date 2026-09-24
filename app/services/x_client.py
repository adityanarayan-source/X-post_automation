"""The single place where the official X SDK (`xdk`) is used for API operations.

Rules of this module:
  * Every X API call goes through `xdk` (e.g. `client.posts.create(...)`).
  * No direct HTTP calls to X endpoints, no Tweepy.
  * The SDK is synchronous, so calls run in a worker thread (`asyncio.to_thread`).
  * SDK/HTTP failures are translated into `XSDKError` with a safe, redacted message.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from app.core.config import Settings
from app.core.exceptions import AppError, XConfigurationError, XSDKError
from app.core.security import redact
from app.services.sdk_compat import get_field

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Names the request model for `client.posts.create(body=...)` has had in the SDK.
_CREATE_MODEL_NAMES = ("CreateRequest", "CreatePostRequest", "CreatePostsRequest")


@dataclass(frozen=True)
class CreatedPost:
    post_id: str
    text: str


def _detail_from_response(response: Any) -> str | None:
    if response is None:
        return None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            for key in ("detail", "title", "error_description", "error"):
                if payload.get(key):
                    return redact(str(payload[key]))[:300]
            errors = payload.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = errors[0].get("message")
                if message:
                    return redact(str(message))[:300]
    except Exception:  # noqa: BLE001
        pass
    try:
        text = getattr(response, "text", "") or ""
        return redact(text)[:200] or None
    except Exception:  # noqa: BLE001
        return None


def translate_sdk_error(exc: BaseException) -> AppError:
    """Map an exception raised by the SDK to one of our safe application errors."""
    if isinstance(exc, AppError):
        return exc

    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    detail = _detail_from_response(response)

    if isinstance(status, int):
        if status == 401:
            return XSDKError(
                "X rejected the access token. Re-authenticate via /api/v1/auth/x/login.",
                code="x_unauthorized", status_code=401, x_status_code=status, details=detail,
            )
        if status == 403:
            return XSDKError(
                "X refused the request (403). Common causes: duplicate content, the app lacks "
                "'Read and write' permission, or account/plan restrictions.",
                code="x_forbidden", status_code=403, x_status_code=status, details=detail,
            )
        if status == 404:
            return XSDKError(
                "The requested resource was not found on X.",
                code="x_not_found", status_code=404, x_status_code=status, details=detail,
            )
        if status == 429:
            return XSDKError(
                "X rate limit reached. Try again later.",
                code="x_rate_limited", status_code=429, x_status_code=status, details=detail,
            )
        if 400 <= status < 500:
            return XSDKError(
                f"X rejected the request ({status}).",
                code="x_bad_request", status_code=400, x_status_code=status, details=detail,
            )
        return XSDKError(
            f"X is currently unavailable ({status}).",
            code="x_unavailable", status_code=502, x_status_code=status, details=detail,
        )

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return XSDKError(
            "Could not reach the X API. Check your network connection.",
            code="x_unreachable", status_code=502,
        )

    logger.error("Unexpected error from X SDK: %s: %s", type(exc).__name__, redact(str(exc)))
    return XSDKError(
        f"Unexpected error while calling the X SDK ({type(exc).__name__}).",
        code="x_sdk_error", status_code=502,
    )


class XClient:
    """Dedicated wrapper around the official `xdk.Client`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------ internals
    def _build_sdk_client(self, access_token: str) -> Any:
        try:
            from xdk import Client  # official X SDK
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise XConfigurationError(
                "The official X SDK is not installed. Run: pip install -r requirements.txt",
                code="x_sdk_missing",
            ) from exc
        try:
            # OAuth 2.0 user-context access token.
            return Client(access_token=access_token)
        except TypeError:
            return Client(token={"access_token": access_token, "token_type": "bearer"})

    @staticmethod
    def _build_create_body(text: str) -> Any:
        """Use the SDK's request model when available, else a plain dict body."""
        try:
            models = importlib.import_module("xdk.posts.models")
        except ImportError:
            return {"text": text}
        for name in _CREATE_MODEL_NAMES:
            model = getattr(models, name, None)
            if model is not None:
                try:
                    return model(text=text)
                except Exception:  # noqa: BLE001
                    continue
        return {"text": text}

    async def _run(self, func: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(func)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise translate_sdk_error(exc) from exc

    # ------------------------------------------------------------------ operations
    async def create_post(self, access_token: str, text: str) -> CreatedPost:
        def _call() -> Any:
            sdk = self._build_sdk_client(access_token)
            return sdk.posts.create(body=self._build_create_body(text))

        response = await self._run(_call)
        data = get_field(response, "data")
        post_id = get_field(data, "id")
        if not post_id:
            raise XSDKError("X accepted the request but did not return a post id.")
        return CreatedPost(post_id=str(post_id), text=str(get_field(data, "text") or text))

    async def delete_post(self, access_token: str, post_id: str) -> bool:
        def _call() -> Any:
            sdk = self._build_sdk_client(access_token)
            return sdk.posts.delete(post_id)

        response = await self._run(_call)
        deleted = get_field(get_field(response, "data"), "deleted")
        if deleted is False:
            raise XSDKError("X reported that the post was not deleted.", code="x_delete_failed")
        return True

    async def get_me(self, access_token: str) -> dict[str, Any]:
        def _call() -> Any:
            sdk = self._build_sdk_client(access_token)
            return sdk.users.get_me()

        response = await self._run(_call)
        data = get_field(response, "data")
        user_id = get_field(data, "id")
        if not user_id:
            raise XSDKError("X did not return user information.")
        return {
            "id": str(user_id),
            "name": get_field(data, "name"),
            "username": get_field(data, "username"),
        }
