from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.container import Container
from app.services.post_service import PostService
from app.services.token_service import TokenService
from tests.fakes import (
    FakeMongo,
    FakeXAuthService,
    FakeXClient,
    InMemoryPostRepository,
    InMemoryTokenRepository,
    make_settings,
)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def token_repo():
    return InMemoryTokenRepository()


@pytest.fixture
def post_repo():
    return InMemoryPostRepository()


@pytest.fixture
def x_client():
    return FakeXClient()


@pytest.fixture
def x_auth():
    return FakeXAuthService()


@pytest.fixture
def mongo():
    return FakeMongo()


@pytest.fixture
def token_service(token_repo, x_auth, settings):
    return TokenService(token_repo, x_auth, settings)


@pytest.fixture
def post_service(x_client, token_service, post_repo):
    return PostService(x_client, token_service, post_repo)


@pytest.fixture
def container(settings, mongo, x_client, x_auth, token_service, post_service):
    return Container(
        settings=settings,
        mongodb=mongo,
        x_client=x_client,
        x_auth_service=x_auth,
        token_service=token_service,
        post_service=post_service,
    )


@pytest.fixture
def client(settings, container):
    """HTTP client against the real app, with fakes injected (lifespan is not run)."""
    app = create_app(settings)
    app.state.container = container
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def authed(token_repo):
    """A valid, unexpired stored token."""
    token_repo.seed(access_token="access-1", refresh_token="refresh-1", expires_in=3600)
    return token_repo
