from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient

from app.core.config import Settings
from app.core.exceptions import DatabaseError

logger = logging.getLogger(__name__)

TOKENS = "tokens"
POSTS = "posts"
OAUTH_STATES = "oauth_states"  # short-lived PKCE/state records (TTL-indexed)


class MongoDB:
    """Thin wrapper around PyMongo's native async client."""

    def __init__(self) -> None:
        self._client: AsyncMongoClient | None = None
        self._database_name: str = ""
        self._ttl_seconds: int = 600
        self.indexes_ready: bool = False

    async def connect(self, settings: Settings) -> None:
        self._client = AsyncMongoClient(
            settings.mongodb_uri.get_secret_value(),
            serverSelectionTimeoutMS=5000,
            tz_aware=True,
        )
        self._database_name = settings.mongodb_database
        self._ttl_seconds = settings.oauth_state_ttl_seconds

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def collection(self, name: str) -> Any:
        if self._client is None:
            raise DatabaseError("MongoDB client is not initialised.")
        return self._client[self._database_name][name]

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.admin.command("ping")
            return True
        except Exception as exc:  # noqa: BLE001 - health check must never raise
            logger.warning("MongoDB ping failed: %s", type(exc).__name__)
            return False

    async def ensure_indexes(self) -> None:
        """Create indexes (idempotent)."""
        posts = self.collection(POSTS)
        await posts.create_index([("x_post_id", ASCENDING)], unique=True, name="uniq_x_post_id")
        await posts.create_index([("created_at", DESCENDING)], name="created_at_desc")
        await posts.create_index(
            [("status", ASCENDING), ("created_at", DESCENDING)], name="status_created_at"
        )

        tokens = self.collection(TOKENS)
        await tokens.create_index([("provider", ASCENDING)], unique=True, name="uniq_provider")

        states = self.collection(OAUTH_STATES)
        await states.create_index([("state", ASCENDING)], unique=True, name="uniq_state")
        await states.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=self._ttl_seconds,
            name="ttl_created_at",
        )
        self.indexes_ready = True
        logger.info("MongoDB indexes ensured for database '%s'", self._database_name)
