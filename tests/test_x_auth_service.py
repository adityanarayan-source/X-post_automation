"""OAuth flow using a mocked `xdk.oauth2_auth.OAuth2PKCEAuth`."""
import sys
import types
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.exceptions import OAuthCallbackError, OAuthStateError, TokenRefreshError
from app.services.x_auth_service import XAuthService, bundle_from_token
from tests.fakes import InMemoryOAuthStateRepository, make_settings


class FakePKCEAuth:
    instances: list["FakePKCEAuth"] = []
    exchange_error: Exception | None = None
    refresh_error: Exception | None = None

    def __init__(self, client_id=None, client_secret=None, redirect_uri=None, scope=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.code_verifier = f"verifier-{len(FakePKCEAuth.instances)}"
        self.token = None
        self.exchanged = None
        FakePKCEAuth.instances.append(self)

    def get_authorization_url(self, state=None):
        return (
            "https://x.com/i/oauth2/authorize?response_type=code"
            f"&client_id={self.client_id}&state={state}&code_challenge=abc&code_challenge_method=S256"
            f"&scope={self.scope.replace(' ', '%20')}"
        )

    def exchange_code(self, code, code_verifier=None):
        if FakePKCEAuth.exchange_error:
            raise FakePKCEAuth.exchange_error
        self.exchanged = (code, code_verifier)
        return {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "token_type": "bearer",
            "expires_in": 7200,
            "scope": "tweet.read tweet.write users.read offline.access",
        }

    def refresh_token(self, refresh_token=None):
        if FakePKCEAuth.refresh_error:
            raise FakePKCEAuth.refresh_error
        used = refresh_token or (self.token or {}).get("refresh_token")
        return {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 7200, "used": used}


@pytest.fixture(autouse=True)
def fake_oauth_module(monkeypatch):
    FakePKCEAuth.instances.clear()
    FakePKCEAuth.exchange_error = None
    FakePKCEAuth.refresh_error = None
    module = types.ModuleType("xdk.oauth2_auth")
    module.OAuth2PKCEAuth = FakePKCEAuth
    monkeypatch.setitem(sys.modules, "xdk.oauth2_auth", module)


@pytest.fixture
def states():
    return InMemoryOAuthStateRepository()


@pytest.fixture
def service(states):
    return XAuthService(make_settings(), states)


def _state_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


async def test_login_builds_url_and_persists_state_and_verifier(service, states):
    url = await service.start_login()
    state = _state_of(url)

    assert "code_challenge=" in url
    assert "client_id=test-client-id" in url
    saved = states.docs[state]
    assert saved["code_verifier"] == FakePKCEAuth.instances[0].code_verifier
    sdk = FakePKCEAuth.instances[0]
    assert sdk.scope == "tweet.read tweet.write users.read offline.access"
    assert sdk.redirect_uri == "http://localhost:8000/api/v1/auth/x/callback"


async def test_callback_exchanges_code_and_normalises_tokens(service):
    state = _state_of(await service.start_login())
    bundle = await service.complete_login(code="the-code", state=state, callback_url="http://x/cb")

    assert bundle.access_token == "at-1"
    assert bundle.refresh_token == "rt-1"
    assert "offline.access" in bundle.scopes
    assert FakePKCEAuth.instances[0].exchanged[0] == "the-code"
    assert "at-1" not in repr(bundle)  # secrets never appear in repr()


async def test_state_is_single_use(service):
    state = _state_of(await service.start_login())
    await service.complete_login(code="c", state=state, callback_url="http://x/cb")
    with pytest.raises(OAuthStateError):
        await service.complete_login(code="c", state=state, callback_url="http://x/cb")


async def test_unknown_state_is_rejected(service):
    with pytest.raises(OAuthStateError):
        await service.complete_login(code="c", state="forged", callback_url="http://x/cb")


async def test_pkce_verifier_is_restored_from_storage_after_restart(service, states):
    state = _state_of(await service.start_login())
    stored_verifier = states.docs[state]["code_verifier"]
    service._pending.clear()  # simulate a process restart between /login and /callback

    await service.complete_login(code="c", state=state, callback_url="http://x/cb")

    fresh = FakePKCEAuth.instances[-1]
    assert fresh is not FakePKCEAuth.instances[0]
    assert fresh.exchanged == ("c", stored_verifier)


async def test_exchange_failure_is_reported_without_leaking_the_code(service):
    state = _state_of(await service.start_login())
    FakePKCEAuth.exchange_error = ValueError("invalid_grant for code=SECRET-CODE-123")
    with pytest.raises(OAuthCallbackError) as exc:
        await service.complete_login(code="SECRET-CODE-123", state=state, callback_url="http://x/cb")
    assert "SECRET-CODE-123" not in str(exc.value.details)
    assert exc.value.code == "oauth_token_exchange_failed"


async def test_refresh_returns_rotated_tokens(service):
    bundle = await service.refresh("old-refresh")
    assert bundle.access_token == "at-2"
    assert bundle.refresh_token == "rt-2"


async def test_refresh_failure_requires_reauthentication(service):
    FakePKCEAuth.refresh_error = RuntimeError("invalid_grant")
    with pytest.raises(TokenRefreshError) as exc:
        await service.refresh("old-refresh")
    assert exc.value.status_code == 401
    assert "auth/x/login" in exc.value.message


async def test_login_requires_configuration(states):
    service = XAuthService(make_settings(x_client_id=""), states)
    with pytest.raises(Exception) as exc:
        await service.start_login()
    assert "X_CLIENT_ID" in str(exc.value)


def test_bundle_from_token_handles_expires_at_and_scope_list():
    import time

    bundle = bundle_from_token(
        {"access_token": "a", "expires_at": time.time() + 100, "scope": ["tweet.read"]},
        fallback_refresh_token="keep-me",
    )
    assert bundle.refresh_token == "keep-me"
    assert bundle.scopes == ["tweet.read"]
    assert bundle.expires_at.tzinfo is not None


def test_bundle_from_token_requires_access_token():
    with pytest.raises(ValueError):
        bundle_from_token({"refresh_token": "x"})
