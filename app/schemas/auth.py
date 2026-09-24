from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class XUser(BaseModel):
    id: str
    name: str | None = None
    username: str | None = None


class LoginData(BaseModel):
    authorization_url: str = Field(
        ..., description="Open this URL in a browser to authorize the app on X."
    )


class LoginResponse(BaseModel):
    success: bool = True
    message: str
    data: LoginData


class ConnectedData(BaseModel):
    connected: bool = True
    expires_at: datetime
    scopes: list[str]
    user: XUser | None = None


class CallbackResponse(BaseModel):
    success: bool = True
    message: str
    data: ConnectedData


class MeResponse(BaseModel):
    success: bool = True
    data: XUser
