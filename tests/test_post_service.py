import pytest
from pymongo.errors import PyMongoError

from app.core.exceptions import AppError, AuthenticationRequiredError, NotFoundError, XSDKError


async def test_create_post_publishes_via_x_client_and_stores_history(
    post_service, x_client, post_repo, authed
):
    result = await post_service.create_text_post("  Hello X  ")

    assert x_client.created == ["Hello X"]
    assert x_client.tokens_used == ["access-1"]
    assert result.history_saved is True
    record = post_repo.docs[result.record["x_post_id"]]
    assert record["status"] == "published"
    assert record["text"] == "Hello X"
    assert record["post_url"].endswith(record["x_post_id"])
    assert record["error_message"] is None


async def test_create_post_requires_authentication(post_service):
    with pytest.raises(AuthenticationRequiredError):
        await post_service.create_text_post("hello")


@pytest.mark.parametrize("text", ["", "   ", "x" * 281])
async def test_create_post_rejects_invalid_text(post_service, authed, x_client, text):
    with pytest.raises(AppError) as exc:
        await post_service.create_text_post(text)
    assert exc.value.status_code == 422
    assert x_client.created == []


async def test_create_post_refreshes_and_retries_when_x_returns_401(
    post_service, x_client, token_repo, x_auth, authed
):
    x_client.create_errors = [XSDKError("unauthorized", x_status_code=401)]
    result = await post_service.create_text_post("retry me")
    assert x_client.tokens_used == ["access-1", "access-refreshed-1"]
    assert x_auth.refresh_calls == ["refresh-1"]
    assert result.record["status"] == "published"


async def test_x_errors_are_propagated_and_nothing_is_stored(post_service, x_client, post_repo, authed):
    x_client.create_errors = [XSDKError("duplicate", x_status_code=403, status_code=403)]
    with pytest.raises(XSDKError) as exc:
        await post_service.create_text_post("dup")
    assert exc.value.status_code == 403
    assert post_repo.docs == {}


async def test_post_is_still_reported_published_when_history_save_fails(
    post_service, post_repo, x_client, authed
):
    post_repo.fail_on_create = PyMongoError("db down")
    result = await post_service.create_text_post("live on X")
    assert result.history_saved is False
    assert x_client.created == ["live on X"]


async def test_list_posts_paginates_newest_first(post_service, authed):
    for i in range(5):
        await post_service.create_text_post(f"post {i}")

    page1 = await post_service.list_posts(page=1, page_size=2)
    page3 = await post_service.list_posts(page=3, page_size=2)

    assert page1["total"] == 5
    assert page1["total_pages"] == 3
    assert [p["text"] for p in page1["items"]] == ["post 4", "post 3"]
    assert [p["text"] for p in page3["items"]] == ["post 0"]


async def test_get_post_not_found(post_service):
    with pytest.raises(NotFoundError):
        await post_service.get_post("999")


async def test_delete_uses_x_client_and_keeps_history_as_deleted(
    post_service, x_client, post_repo, authed
):
    created = await post_service.create_text_post("to delete")
    post_id = created.record["x_post_id"]

    record, deleted_now = await post_service.delete_post(post_id)

    assert deleted_now is True
    assert x_client.deleted == [post_id]
    assert record["status"] == "deleted"
    assert post_repo.docs[post_id]["status"] == "deleted"  # record kept


async def test_delete_is_idempotent(post_service, x_client, authed):
    created = await post_service.create_text_post("once")
    post_id = created.record["x_post_id"]
    await post_service.delete_post(post_id)
    _, deleted_now = await post_service.delete_post(post_id)
    assert deleted_now is False
    assert x_client.deleted == [post_id]  # X called only once


async def test_delete_unknown_post_is_404(post_service, authed):
    with pytest.raises(NotFoundError):
        await post_service.delete_post("12345")


async def test_delete_failure_records_error_and_keeps_status(
    post_service, x_client, post_repo, authed
):
    created = await post_service.create_text_post("stubborn")
    post_id = created.record["x_post_id"]
    x_client.delete_errors = [XSDKError("rate limited", x_status_code=429, status_code=429)]

    with pytest.raises(XSDKError):
        await post_service.delete_post(post_id)

    assert post_repo.docs[post_id]["status"] == "published"
    assert "rate limited" in post_repo.docs[post_id]["error_message"]


async def test_delete_marks_deleted_when_post_already_gone_on_x(
    post_service, x_client, post_repo, authed
):
    created = await post_service.create_text_post("gone")
    post_id = created.record["x_post_id"]
    x_client.delete_errors = [XSDKError("not found", x_status_code=404, status_code=404)]

    record, deleted_now = await post_service.delete_post(post_id)

    assert deleted_now is True
    assert record["status"] == "deleted"
