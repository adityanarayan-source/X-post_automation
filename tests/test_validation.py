import pytest

URL = "/api/v1/posts/text"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   \n\t  "},
        {"text": None},
        {"text": 123},
        {"text": "x" * 281},
        {"text": "hello\x00world"},
        {"text": "hello", "unexpected": True},
    ],
)
def test_invalid_payloads_are_rejected(client, authed, x_client, payload):
    r = client.post(URL, json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "validation_error"
    assert x_client.created == []  # X was never called


def test_text_is_trimmed_and_published(client, authed, x_client):
    r = client.post(URL, json={"text": "   Hello from tests!  \n"})
    assert r.status_code == 201
    assert x_client.created == ["Hello from tests!"]
    data = r.json()
    assert data["success"] is True
    assert data["message"] == "Post published successfully"
    assert data["data"]["status"] == "published"
    assert data["data"]["text"] == "Hello from tests!"
    assert data["data"]["post_url"].endswith(data["data"]["post_id"])


def test_exactly_280_characters_is_accepted(client, authed):
    assert client.post(URL, json={"text": "a" * 280}).status_code == 201


def test_validation_error_does_not_echo_input(client):
    r = client.post(URL, json={"text": "x" * 300})
    assert "x" * 300 not in r.text


def test_invalid_post_id_path_rejected(client):
    assert client.get("/api/v1/posts/not-a-number").status_code == 422


@pytest.mark.parametrize("params", ["page=0", "page_size=0", "page_size=101", "status=bogus"])
def test_invalid_pagination_rejected(client, params):
    assert client.get(f"/api/v1/posts?{params}").status_code == 422
