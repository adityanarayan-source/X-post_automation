from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.timeutils import utcnow
from app.database.mongodb import TOKENS, MongoDB


class TokenRepository:
    """Persists OAuth 2.0 tokens (one document per provider)."""

    def __init__(self, db: MongoDB) -> None:
        self._db = db

    @property
    def _col(self) -> Any:
        return self._db.collection(TOKENS)

    async def get(self, provider: str = "x") -> dict[str, Any] | None:
        return await self._col.find_one({"provider": provider}, {"_id": 0})

    async def upsert(
        self,
        *,
        provider: str,
        access_token: str,
        refresh_token: str | None,
        token_type: str,
        expires_at: datetime,
        scopes: list[str],
    ) -> None:
        now = utcnow()
        await self._col.update_one(
            {"provider": provider},
            {
                "$set": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_type": token_type,
                    "expires_at": expires_at,
                    "scopes": scopes,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
