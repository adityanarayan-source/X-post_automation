from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app import __version__
from app.api.deps import ContainerDep
from app.core.timeutils import utcnow

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness check")
async def health(container: ContainerDep) -> dict[str, Any]:
    s = container.settings
    return {
        "status": "ok",
        "app": s.app_name,
        "version": __version__,
        "environment": s.app_env,
        "time": utcnow().isoformat(),
    }


@router.get(
    "/health/detailed",
    summary="Application, MongoDB and X auth configuration check",
    responses={503: {"description": "MongoDB is unreachable"}},
)
async def health_detailed(container: ContainerDep) -> Any:
    s = container.settings

    mongo_ok = await container.mongodb.ping()
    mongodb: dict[str, Any] = {
        "status": "ok" if mongo_ok else "down",
        "database": s.mongodb_database,
    }
    if mongo_ok and not getattr(container.mongodb, "indexes_ready", True):
        try:
            await container.mongodb.ensure_indexes()
        except Exception:  # noqa: BLE001
            logger.warning("Could not ensure MongoDB indexes during health check")
    mongodb["indexes_ready"] = bool(getattr(container.mongodb, "indexes_ready", False))

    problems = container.x_auth_service.configuration_problems()
    x_auth: dict[str, Any] = {
        "status": "ok" if not problems else "misconfigured",
        "client_id_configured": bool(s.x_client_id),
        "client_secret_configured": bool(s.x_client_secret.get_secret_value()),
        "redirect_uri": s.x_redirect_uri,
        "scopes": s.scopes,
        "problems": problems,
    }
    if mongo_ok:
        try:
            x_auth["token"] = await container.token_service.status()
        except Exception:  # noqa: BLE001
            x_auth["token"] = {"stored": None, "error": "could not read token status"}

    if not mongo_ok:
        overall = "unhealthy"
    elif problems:
        overall = "degraded"
    else:
        overall = "ok"

    body = {
        "status": overall,
        "time": utcnow().isoformat(),
        "components": {
            "application": {"status": "ok", "version": __version__, "environment": s.app_env},
            "mongodb": mongodb,
            "x_auth": x_auth,
        },
    }
    return JSONResponse(
        status_code=503 if overall == "unhealthy" else 200, content=jsonable_encoder(body)
    )
