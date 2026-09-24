from __future__ import annotations

from typing import Any

from app.core.timeutils import utcnow
from app.database.mongodb import OAUTH_STATES, MongoDB


class OAuthStateRepository:
    """Stores the OAuth `state` and PKCE `code_verifier` between /login and /callback."""

    def __init__(self, db: MongoDB) -> None:
        self._db = db

    @property
    def _col(self) -> Any:
        return self._db.collection(OAUTH_STATES)

    async def save(self, state: str, code_verifier: str | None) -> None:
        await self._col.insert_one(
            {"state": state, "code_verifier": code_verifier, "created_at": utcnow()}
        )

    async def pop(self, state: str) -> dict[str, Any] | None:
        """Fetch and delete atomically, so a state can only be used once."""
        return await self._col.find_one_and_delete({"state": state}, projection={"_id": 0})
