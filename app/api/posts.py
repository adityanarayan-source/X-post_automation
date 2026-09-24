from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from app.api.deps import PostServiceDep
from app.schemas.post import (
    PostData,
    PostDetailResponse,
    PostListData,
    PostListResponse,
    PostRecord,
    PostResponse,
    TextPostRequest,
)

router = APIRouter(prefix="/api/v1/posts", tags=["Posts"])

PostIdPath = Annotated[
    str,
    Path(description="X post (tweet) id", pattern=r"^\d{1,25}$", examples=["1900000000000000000"]),
]


def _to_data(record: dict) -> PostData:
    return PostData(
        post_id=record["x_post_id"],
        text=record["text"],
        status=record["status"],
        post_url=record["post_url"],
    )


@router.post(
    "/text",
    response_model=PostResponse,
    status_code=201,
    summary="Publish a text post to X",
    description="Publishes via the official X SDK (`client.posts.create`) and stores the post in MongoDB.",
)
async def create_text_post(payload: TextPostRequest, service: PostServiceDep) -> PostResponse:
    result = await service.create_text_post(payload.text)
    message = "Post published successfully"
    if not result.history_saved:
        message += " (warning: it could not be saved to the history database)"
    return PostResponse(message=message, data=_to_data(result.record))


@router.get("", response_model=PostListResponse, summary="Post history (paginated)")
async def list_posts(
    service: PostServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Literal["published", "deleted"] | None = Query(None, description="Filter by status"),
) -> PostListResponse:
    data = await service.list_posts(page=page, page_size=page_size, status=status)
    return PostListResponse(
        data=PostListData(
            items=[PostRecord(**item) for item in data["items"]],
            page=data["page"],
            page_size=data["page_size"],
            total=data["total"],
            total_pages=data["total_pages"],
        )
    )


@router.get("/{post_id}", response_model=PostDetailResponse, summary="Get one post from history")
async def get_post(post_id: PostIdPath, service: PostServiceDep) -> PostDetailResponse:
    return PostDetailResponse(data=PostRecord(**await service.get_post(post_id)))


@router.delete(
    "/{post_id}",
    response_model=PostResponse,
    summary="Delete a post on X (history record is kept as 'deleted')",
)
async def delete_post(post_id: PostIdPath, service: PostServiceDep) -> PostResponse:
    record, deleted_now = await service.delete_post(post_id)
    message = "Post deleted successfully" if deleted_now else "Post was already deleted"
    return PostResponse(message=message, data=_to_data(record))
