from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all errors that should become a clean JSON response."""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.default_message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details
        super().__init__(self.message)


class AuthenticationRequiredError(AppError):
    status_code = 401
    code = "authentication_required"
    default_message = (
        "X authentication required. Open /api/v1/auth/x/login to connect your X account."
    )


class TokenRefreshError(AppError):
    status_code = 401
    code = "token_refresh_failed"
    default_message = (
        "Refreshing the X access token failed. Re-authenticate via /api/v1/auth/x/login."
    )


class OAuthStateError(AppError):
    status_code = 400
    code = "invalid_oauth_state"
    default_message = "Unknown, expired or already-used OAuth state. Start again at /api/v1/auth/x/login."


class OAuthCallbackError(AppError):
    status_code = 400
    code = "oauth_callback_error"
    default_message = "The OAuth callback could not be processed."


class XConfigurationError(AppError):
    status_code = 503
    code = "x_not_configured"
    default_message = "X integration is not configured correctly."


class XSDKError(AppError):
    """Error raised by (or while using) the official X SDK."""

    status_code = 502
    code = "x_api_error"
    default_message = "The X API returned an error."

    def __init__(self, message: str | None = None, *, x_status_code: int | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.x_status_code = x_status_code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Resource not found."


class DatabaseError(AppError):
    status_code = 503
    code = "database_error"
    default_message = "The database is unavailable or returned an error."
