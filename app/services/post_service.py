from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from pymongo.errors import PyMongoError

from app.core.exceptions import AppError, DatabaseError, NotFoundError, XSDKError
from app.core.timeutils import utcnow
from app.database.repositories.post_repository import PostRepository
from app.schemas.post import MAX_POST_LENGTH
from app.services.token_service import TokenService
from app.services.x_client import XClient

logger = logging.getLogger(__name__)


def build_post_url(post_id: str) -> str:
    # Works without knowing the username.
    return f"https://x.com/i/web/status/{post_id}"


@dataclass
class CreateResult:
    record: dict[str, Any]
    history_saved: bool


class PostService:
    def __init__(
        self, x_client: XClient, token_service: TokenService, repository: PostRepository
    ) -> None:
        self._x = x_client
        self._tokens = token_service
        self._repo = repository

    async def create_text_post(self, text: str) -> CreateResult:
        text = (text or "").strip()
        if not text:
            raise AppError("Post text must not be empty.", status_code=422, code="invalid_post")
        if len(text) > MAX_POST_LENGTH:
            raise AppError(
                f"Post text must be at most {MAX_POST_LENGTH} characters.",
                status_code=422,
                code="invalid_post",
            )

        created = await self._tokens.with_valid_token(
            lambda token: self._x.create_post(token, text)
        )

        now = utcnow()
        record = {
            "x_post_id": created.post_id,
            "text": created.text,
            "status": "published",
            "post_url": build_post_url(created.post_id),
            "created_at": now,
            "updated_at": now,
            "error_message": None,
        }
        try:
            await self._repo.create(record)
            saved = True
        except (PyMongoError, DatabaseError):
            # The post IS live on X; do not report failure just because history failed.
            logger.exception("Post %s published but could not be saved to MongoDB", created.post_id)
            saved = False
        return CreateResult(record=record, history_saved=saved)

    async def list_posts(
        self, *, page: int, page_size: int, status: str | None = None
    ) -> dict[str, Any]:
        items, total = await self._repo.list(page=page, page_size=page_size, status=status)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if total else 0,
        }

    async def get_post(self, x_post_id: str) -> dict[str, Any]:
        record = await self._repo.get(x_post_id)
        if record is None:
            raise NotFoundError(f"Post {x_post_id} was not found in the history.")
        return record

    async def delete_post(self, x_post_id: str) -> tuple[dict[str, Any], bool]:
        """Delete on X via the SDK, keep the history record with status 'deleted'.

        Returns (record, deleted_now).
        """
        record = await self.get_post(x_post_id)
        if record.get("status") == "deleted":
            return record, False

        try:
            await self._tokens.with_valid_token(
                lambda token: self._x.delete_post(token, x_post_id)
            )
        except XSDKError as exc:
            if exc.x_status_code == 404:
                logger.info("Post %s no longer exists on X; marking it deleted", x_post_id)
            else:
                try:
                    await self._repo.set_error(x_post_id, exc.message)
                except (PyMongoError, DatabaseError):
                    logger.exception("Could not record delete error for post %s", x_post_id)
                raise

        updated = await self._repo.mark_deleted(x_post_id)
        return (updated or {**record, "status": "deleted"}), True
