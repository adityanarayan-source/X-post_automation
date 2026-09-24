from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Standard X post limit. (X counts URLs/emoji with its own weighting, so this is a
# conservative pre-check; X remains the final authority.)
MAX_POST_LENGTH = 280

PostStatus = Literal["published", "deleted"]


class TextPostRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"text": "Testing my X post automation backend!"}]},
    )

    text: str = Field(
        ...,
        min_length=1,
        max_length=MAX_POST_LENGTH,
        description=f"Post text (1-{MAX_POST_LENGTH} characters after trimming whitespace).",
    )

    @field_validator("text", mode="before")
    @classmethod
    def _trim(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("text")
    @classmethod
    def _reject_control_characters(cls, value: str) -> str:
        for ch in value:
            if unicodedata.category(ch) == "Cc" and ch not in "\n\r\t":
                raise ValueError("text contains invalid control characters")
        return value


class PostData(BaseModel):
    post_id: str
    text: str
    status: PostStatus
    post_url: str


class PostResponse(BaseModel):
    success: bool = True
    message: str
    data: PostData


class PostRecord(BaseModel):
    x_post_id: str
    text: str
    status: PostStatus
    post_url: str
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None


class PostDetailResponse(BaseModel):
    success: bool = True
    data: PostRecord


class PostListData(BaseModel):
    items: list[PostRecord]
    page: int
    page_size: int
    total: int
    total_pages: int


class PostListResponse(BaseModel):
    success: bool = True
    data: PostListData
