"""Simple dependency container, built once per application instance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.database.mongodb import MongoDB
from app.database.repositories.oauth_state_repository import OAuthStateRepository
from app.database.repositories.post_repository import PostRepository
from app.database.repositories.token_repository import TokenRepository
from app.services.post_service import PostService
from app.services.token_service import TokenService
from app.services.x_auth_service import XAuthService
from app.services.x_client import XClient


@dataclass
class Container:
    settings: Settings
    mongodb: Any
    x_client: Any
    x_auth_service: Any
    token_service: Any
    post_service: Any


def build_container(settings: Settings) -> Container:
    mongodb = MongoDB()
    x_client = XClient(settings)
    x_auth_service = XAuthService(settings, OAuthStateRepository(mongodb))
    token_service = TokenService(TokenRepository(mongodb), x_auth_service, settings)
    post_service = PostService(x_client, token_service, PostRepository(mongodb))
    return Container(
        settings=settings,
        mongodb=mongodb,
        x_client=x_client,
        x_auth_service=x_auth_service,
        token_service=token_service,
        post_service=post_service,
    )
