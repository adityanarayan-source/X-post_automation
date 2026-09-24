from types import SimpleNamespace

import pytest
from pymongo.errors import PyMongoError

from app.core.exceptions import AppError, XSDKError
from app.core.security import redact, register_secret
from app.services.x_client import translate_sdk_error


class FakeHTTPError(Exception):
    """Looks like the HTTP errors raised by the SDK's HTTP layer (has .response)."""

    def __init__(self, status: int, payload: dict | None = None):
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status, json=lambda: payload or {}, text="")


# ------------------------------------------------------------- SDK error mapping
@pytest.mark.parametrize(
    "status,expected_status,expected_code",
    [
        (401, 401, "x_unauthorized"),
        (403, 403, "x_forbidden"),
        (404, 404, "x_not_found"),
        (429, 429, "x_rate_limited"),
        (400, 400, "x_bad_request"),
        (500, 502, "x_unavailable"),
        (503, 502, "x_unavailable"),
    ],
)
def test_translate_http_status_codes(status, expected_status, expected_code):
    err = translate_sdk_error(FakeHTTPError(status))
    assert isinstance(err, XSDKError)
    assert err.status_code == expected_status
    assert err.code == expected_code
    assert err.x_status_code == status


def test_translate_includes_x_detail():
    err = translate_sdk_error(FakeHTTPError(403, {"detail": "You are not allowed to create a Tweet with duplicate content."}))
    assert "duplicate content" in err.details


def test_translate_redacts_secrets_in_detail():
    register_secret("leaky-token-123456")
    err = translate_sdk_error(FakeHTTPError(400, {"detail": "bad token leaky-token-123456"}))
    assert "leaky-token-123456" not in err.details


def test_translate_network_errors():
    err = translate_sdk_error(ConnectionError("boom"))
    assert err.status_code == 502
    assert err.code == "x_unreachable"


def test_translate_unknown_errors_hides_message():
    err = translate_sdk_error(ValueError("sensitive internal detail"))
    assert err.code == "x_sdk_error"
    assert "sensitive internal detail" not in err.message


def test_translate_passes_app_errors_through():
    original = AppError("already ours")
    assert translate_sdk_error(original) is original


# ---------------------------------------------------- centralised HTTP handlers
def test_app_error_becomes_clean_json(client):
    async def route():
        raise XSDKError("X said no", code="x_forbidden", status_code=403, x_status_code=403)

    client.app.add_api_route("/__x_error", route)
    r = client.get("/__x_error")
    assert r.status_code == 403
    assert r.json() == {"success": False, "error": {"code": "x_forbidden", "message": "X said no"}}


def test_mongo_errors_are_translated_without_details(client):
    async def route():
        raise PyMongoError("mongodb://user:password@host/db failed")

    client.app.add_api_route("/__mongo_error", route)
    r = client.get("/__mongo_error")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "database_error"
    assert "password" not in r.text


def test_unexpected_errors_return_500_without_leaking(client):
    register_secret("top-secret-value-999")

    async def route():
        raise RuntimeError("exploded with top-secret-value-999")

    client.app.add_api_route("/__boom", route)
    r = client.get("/__boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert "top-secret-value-999" not in r.text
    assert "Traceback" not in r.text
    assert "details" not in body["error"]  # debug is off in tests


def test_unknown_route_uses_standard_error_format(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert r.json()["success"] is False
    assert r.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------- redaction
def test_redact_scrubs_oauth_material():
    text = "GET /cb?state=abc&code=AUTH123 Authorization: Bearer abcdefghijklmnop"
    cleaned = redact(text)
    assert "AUTH123" not in cleaned
    assert "abcdefghijklmnop" not in cleaned


def test_redact_scrubs_key_value_pairs_and_mongo_credentials():
    cleaned = redact('{"refresh_token": "r-token-1234"} mongodb+srv://bob:pw@cluster/db')
    assert "r-token-1234" not in cleaned
    assert "bob:pw" not in cleaned


def test_redact_keeps_normal_text():
    assert redact("status code: 200 ok") == "status code: 200 ok"
