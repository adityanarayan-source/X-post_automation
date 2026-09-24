import asyncio
from datetime import timedelta

import pytest

from app.core.exceptions import AuthenticationRequiredError, TokenRefreshError, XSDKError
from app.core.timeutils import utcnow
from app.services.token_service import TokenService
from tests.fakes import make_settings


async def test_valid_token_is_returned_without_refresh(token_service, token_repo, x_auth):
    token_repo.seed(expires_in=3600)
    assert await token_service.get_valid_access_token() == "access-1"
    assert x_auth.refresh_calls == []


async def test_token_inside_refresh_buffer_is_refreshed_and_saved(token_service, token_repo, x_auth):
    token_repo.seed(expires_in=200)  # buffer is 300s -> must refresh
    token = await token_service.get_valid_access_token()

    assert token == "access-refreshed-1"
    assert x_auth.refresh_calls == ["refresh-1"]
    stored = token_repo.docs["x"]
    assert stored["access_token"] == "access-refreshed-1"
    assert stored["refresh_token"] == "refresh-rotated-1"  # rotated token persisted
    assert stored["expires_at"] > utcnow() + timedelta(hours=1)


async def test_token_just_outside_buffer_is_not_refreshed(token_service, token_repo, x_auth):
    token_repo.seed(expires_in=400)
    await token_service.get_valid_access_token()
    assert x_auth.refresh_calls == []


async def test_expired_token_without_refresh_token_requires_reauth(token_service, token_repo):
    token_repo.seed(refresh_token=None, expires_in=-10)
    with pytest.raises(AuthenticationRequiredError) as exc:
        await token_service.get_valid_access_token()
    assert exc.value.status_code == 401


async def test_no_tokens_anywhere_requires_authentication(token_service):
    with pytest.raises(AuthenticationRequiredError):
        await token_service.get_valid_access_token()


async def test_refresh_failure_raises_clear_error_and_keeps_stored_tokens(
    token_service, token_repo, x_auth
):
    token_repo.seed(expires_in=-10)
    x_auth.refresh_error = TokenRefreshError()
    with pytest.raises(TokenRefreshError) as exc:
        await token_service.get_valid_access_token()
    assert exc.value.status_code == 401
    assert "auth/x/login" in exc.value.message
    assert token_repo.docs["x"]["refresh_token"] == "refresh-1"  # untouched


async def test_bootstrap_from_env_refresh_token(token_repo, x_auth):
    settings = make_settings(x_refresh_token="env-refresh-token", x_access_token="env-access-token")
    service = TokenService(token_repo, x_auth, settings)

    token = await service.get_valid_access_token()

    assert x_auth.refresh_calls == ["env-refresh-token"]  # forced refresh on first use
    assert token == "access-refreshed-1"
    assert token_repo.docs["x"]["refresh_token"] == "refresh-rotated-1"


async def test_bootstrap_from_env_access_token_only(token_repo, x_auth):
    settings = make_settings(x_access_token="env-access-token")
    service = TokenService(token_repo, x_auth, settings)
    assert await service.get_valid_access_token() == "env-access-token"
    assert x_auth.refresh_calls == []


async def test_concurrent_requests_trigger_a_single_refresh(token_service, token_repo, x_auth):
    token_repo.seed(expires_in=-10)
    results = await asyncio.gather(*(token_service.get_valid_access_token() for _ in range(5)))
    assert len(x_auth.refresh_calls) == 1
    assert set(results) == {"access-refreshed-1"}


async def test_force_refresh_skips_when_token_already_rotated(token_service, token_repo, x_auth):
    token_repo.seed(access_token="already-new", expires_in=3600)
    token = await token_service.get_valid_access_token(force_refresh=True, rejected_token="old-token")
    assert token == "already-new"
    assert x_auth.refresh_calls == []


async def test_with_valid_token_retries_once_after_401(token_service, token_repo, x_auth):
    token_repo.seed(expires_in=3600)
    seen: list[str] = []

    async def op(token: str) -> str:
        seen.append(token)
        if len(seen) == 1:
            raise XSDKError("unauthorized", x_status_code=401)
        return "ok"

    assert await token_service.with_valid_token(op) == "ok"
    assert seen == ["access-1", "access-refreshed-1"]


async def test_with_valid_token_requires_reauth_if_401_persists(token_service, token_repo):
    token_repo.seed(expires_in=3600)

    async def op(token: str) -> str:
        raise XSDKError("unauthorized", x_status_code=401)

    with pytest.raises(AuthenticationRequiredError):
        await token_service.with_valid_token(op)


async def test_with_valid_token_does_not_retry_other_errors(token_service, token_repo, x_auth):
    token_repo.seed(expires_in=3600)

    async def op(token: str) -> str:
        raise XSDKError("rate limited", x_status_code=429, status_code=429)

    with pytest.raises(XSDKError):
        await token_service.with_valid_token(op)
    assert x_auth.refresh_calls == []


async def test_status_never_contains_token_values(token_service, token_repo):
    token_repo.seed(access_token="super-secret-access", refresh_token="super-secret-refresh")
    status = await token_service.status()
    assert status["stored"] is True
    assert "super-secret" not in str(status)
