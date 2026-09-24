from __future__ import annotations

from typing import Any

from pymongo import DESCENDING, ReturnDocument

from app.core.timeutils import utcnow
from app.database.mongodb import POSTS, MongoDB


class PostRepository:
    """Post history stored in the `posts` collection."""

    def __init__(self, db: MongoDB) -> None:
        self._db = db

    @property
    def _col(self) -> Any:
        return self._db.collection(POSTS)

    async def create(self, record: dict[str, Any]) -> None:
        # insert_one mutates its argument by adding `_id`; insert a copy.
        await self._col.insert_one(dict(record))

    async def get(self, x_post_id: str) -> dict[str, Any] | None:
        return await self._col.find_one({"x_post_id": x_post_id}, {"_id": 0})

    async def list(
        self, *, page: int, page_size: int, status: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        total = await self._col.count_documents(query)
        cursor = (
            self._col.find(query, {"_id": 0})
            .sort("created_at", DESCENDING)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)
        return items, total

    async def mark_deleted(self, x_post_id: str) -> dict[str, Any] | None:
        return await self._col.find_one_and_update(
            {"x_post_id": x_post_id},
            {
                "$set": {"status": "deleted", "updated_at": utcnow(), "error_message": None},
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )

    async def set_error(self, x_post_id: str, message: str) -> None:
        await self._col.update_one(
            {"x_post_id": x_post_id},
            {"$set": {"error_message": message, "updated_at": utcnow()}},
        )
