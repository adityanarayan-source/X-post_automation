from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app import __version__
from app.api import auth, health, posts
from app.core.config import Settings, get_settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging import setup_logging
from app.services.container import build_container

logger = logging.getLogger(__name__)

TAGS = [
    {"name": "Health", "description": "Liveness and dependency checks."},
    {"name": "X Authentication", "description": "OAuth 2.0 (PKCE) login and the current X user."},
    {"name": "Posts", "description": "Create, list, read and delete posts (official X SDK)."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = app.state.container
    await container.mongodb.connect(container.settings)
    try:
        await container.mongodb.ensure_indexes()
    except Exception as exc:  # noqa: BLE001 - the app still starts; /health/detailed reports it
        logger.error(
            "MongoDB is not reachable at startup (%s). Start MongoDB; indexes are retried "
            "by /health/detailed.",
            type(exc).__name__,
        )
    problems = container.x_auth_service.configuration_problems()
    if problems:
        logger.warning("X OAuth configuration incomplete: %s", "; ".join(problems))
    logger.info("%s v%s started (%s)", container.settings.app_name, __version__, container.settings.app_env)
    yield
    await container.mongodb.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Backend for publishing and managing X posts via **OAuth 2.0 (PKCE)** and the "
            "official **X Python SDK (`xdk`)**.\n\n"
            "**Quick start:** 1) `GET /api/v1/auth/x/login`, open the returned URL in a browser "
            "and approve → 2) `POST /api/v1/posts/text`."
        ),
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.container = build_container(settings)
    register_exception_handlers(app, settings)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(posts.router)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app


app = create_app()
