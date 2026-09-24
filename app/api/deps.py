"""FastAPI dependencies. Route files never build SDK clients themselves."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.services.container import Container
from app.services.post_service import PostService
from app.services.token_service import TokenService
from app.services.x_auth_service import XAuthService
from app.services.x_client import XClient


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_post_service(container: ContainerDep) -> PostService:
    return container.post_service


def get_token_service(container: ContainerDep) -> TokenService:
    return container.token_service


def get_x_auth_service(container: ContainerDep) -> XAuthService:
    return container.x_auth_service


def get_x_client(container: ContainerDep) -> XClient:
    return container.x_client


PostServiceDep = Annotated[PostService, Depends(get_post_service)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]
XAuthServiceDep = Annotated[XAuthService, Depends(get_x_auth_service)]
XClientDep = Annotated[XClient, Depends(get_x_client)]
