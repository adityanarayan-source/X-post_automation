from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from app.api.deps import ContainerDep, TokenServiceDep, XAuthServiceDep, XClientDep
from app.core.exceptions import AppError, OAuthCallbackError
from app.schemas.auth import (
    CallbackResponse,
    ConnectedData,
    LoginData,
    LoginResponse,
    MeResponse,
    XUser,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth/x", tags=["X Authentication"])


@router.get(
    "/login",
    response_model=LoginResponse,
    summary="Start X OAuth 2.0 (PKCE) login",
    description=(
        "Returns the X authorization URL as JSON (default) — open it in your browser. "
        "Or open `/api/v1/auth/x/login?redirect=true` directly in the browser to be "
        "redirected to X immediately. After you approve, X redirects to the callback URL."
    ),
)
async def login(
    auth: XAuthServiceDep,
    redirect: bool = Query(False, description="Respond with a 307 redirect to X instead of JSON."),
):
    url = await auth.start_login()
    if redirect:
        return RedirectResponse(url, status_code=307)
    return LoginResponse(
        message="Open authorization_url in your browser to connect your X account.",
        data=LoginData(authorization_url=url),
    )


@router.get(
    "/callback",
    response_model=CallbackResponse,
    summary="OAuth 2.0 callback (called by X)",
    description="X redirects the browser here after authorization. Not meant to be called manually.",
)
async def callback(
    request: Request,
    container: ContainerDep,
    auth: XAuthServiceDep,
    tokens: TokenServiceDep,
    x_client: XClientDep,
    code: str | None = Query(None, description="Authorization code from X"),
    state: str | None = Query(None, description="State issued by /login"),
    error: str | None = Query(None, description="Set by X when authorization was denied"),
):
    if error:
        raise OAuthCallbackError(
            f"X authorization was not completed ({error[:60]}).", code="oauth_access_denied"
        )
    if not code or not state:
        raise OAuthCallbackError("Missing 'code' or 'state' in the callback request.")

    # Rebuild the exact redirect URL X used (some SDK code paths parse it directly).
    callback_url = f"{container.settings.x_redirect_uri}?{request.url.query}"
    bundle = await auth.complete_login(code=code, state=state, callback_url=callback_url)
    await tokens.store_tokens(bundle)

    user: XUser | None = None
    try:
        me = await x_client.get_me(bundle.access_token)
        user = XUser(**me)
    except AppError as exc:
        logger.warning("Connected, but fetching the X profile failed: %s", exc.code)

    return CallbackResponse(
        message="X account connected successfully. You can close this tab and use /docs.",
        data=ConnectedData(expires_at=bundle.expires_at, scopes=bundle.scopes, user=user),
    )


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get the authenticated X user",
    description="Uses the official X SDK (`client.users.get_me()`).",
)
async def me(tokens: TokenServiceDep, x_client: XClientDep):
    data = await tokens.with_valid_token(lambda token: x_client.get_me(token))
    return MeResponse(data=XUser(**data))
