def test_login_returns_authorization_url(client):
    r = client.get("/api/v1/auth/x/login")
    assert r.status_code == 200
    assert r.json()["data"]["authorization_url"].startswith("https://x.com/i/oauth2/authorize")


def test_login_can_redirect(client):
    r = client.get("/api/v1/auth/x/login?redirect=true", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://x.com/i/oauth2/authorize")


def test_callback_with_valid_state_stores_tokens_without_leaking_them(client, token_repo, x_auth):
    r = client.get("/api/v1/auth/x/callback?code=the-auth-code&state=good-state")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["connected"] is True
    assert body["data"]["user"]["username"] == "test_user"

    assert token_repo.docs["x"]["access_token"] == "new-access-token-value"
    assert token_repo.docs["x"]["refresh_token"] == "new-refresh-token-value"
    for secret in ("new-access-token-value", "new-refresh-token-value", "the-auth-code"):
        assert secret not in r.text


def test_callback_rejects_unknown_state(client, token_repo):
    r = client.get("/api/v1/auth/x/callback?code=abc&state=forged")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_oauth_state"
    assert token_repo.docs == {}


def test_callback_requires_code_and_state(client):
    r = client.get("/api/v1/auth/x/callback")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "oauth_callback_error"


def test_callback_handles_access_denied(client):
    r = client.get("/api/v1/auth/x/callback?error=access_denied&state=good-state")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "oauth_access_denied"


def test_me_requires_authentication(client):
    r = client.get("/api/v1/auth/x/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_required"
    assert "auth/x/login" in r.json()["error"]["message"]


def test_me_returns_current_user(client, authed):
    r = client.get("/api/v1/auth/x/me")
    assert r.status_code == 200
    assert r.json()["data"] == {"id": "42", "name": "Test User", "username": "test_user"}


def test_refresh_failure_is_reported_as_reauthentication_required(client, token_repo, x_auth):
    from app.core.exceptions import TokenRefreshError

    token_repo.seed(expires_in=-10)
    x_auth.refresh_error = TokenRefreshError()
    r = client.get("/api/v1/auth/x/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "token_refresh_failed"


def test_posting_without_authentication_is_401(client):
    r = client.post("/api/v1/posts/text", json={"text": "hi"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_required"


def test_post_history_endpoints(client, authed):
    created = client.post("/api/v1/posts/text", json={"text": "history test"}).json()["data"]
    post_id = created["post_id"]

    listing = client.get("/api/v1/posts?page=1&page_size=10").json()["data"]
    assert listing["total"] == 1
    assert listing["items"][0]["x_post_id"] == post_id

    single = client.get(f"/api/v1/posts/{post_id}")
    assert single.status_code == 200
    assert single.json()["data"]["text"] == "history test"

    deleted = client.delete(f"/api/v1/posts/{post_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["status"] == "deleted"

    still_there = client.get(f"/api/v1/posts/{post_id}").json()["data"]
    assert still_there["status"] == "deleted"

    assert client.get("/api/v1/posts/999999").status_code == 404
