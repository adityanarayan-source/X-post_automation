"""XClient must talk to X exclusively through the (mocked) official `xdk` SDK."""
import sys
import types
from types import SimpleNamespace

import pytest

from app.core.exceptions import XSDKError
from app.services.x_client import XClient
from tests.fakes import make_settings


class FakeSDK:
    instances: list["FakeSDK"] = []

    def __init__(self, access_token=None, **kwargs):
        self.access_token = access_token
        self.calls: list[tuple] = []
        self.error: Exception | None = None
        self.posts = SimpleNamespace(create=self._create, delete=self._delete)
        self.users = SimpleNamespace(get_me=self._get_me)
        FakeSDK.instances.append(self)

    def _create(self, body=None):
        self.calls.append(("posts.create", body))
        if self.error:
            raise self.error
        text = body["text"] if isinstance(body, dict) else body.text
        return SimpleNamespace(data=SimpleNamespace(id="555", text=text))

    def _delete(self, id):
        self.calls.append(("posts.delete", id))
        return {"data": {"deleted": True}}

    def _get_me(self):
        self.calls.append(("users.get_me",))
        return SimpleNamespace(data={"id": "9", "name": "N", "username": "u"})


class HTTPError(Exception):
    def __init__(self, status):
        super().__init__("http error")
        self.response = SimpleNamespace(status_code=status, json=lambda: {"detail": "nope"}, text="")


@pytest.fixture
def fake_xdk(monkeypatch):
    FakeSDK.instances.clear()
    module = types.ModuleType("xdk")
    module.Client = FakeSDK
    monkeypatch.setitem(sys.modules, "xdk", module)
    monkeypatch.delitem(sys.modules, "xdk.posts.models", raising=False)
    return module


@pytest.fixture
def x():
    return XClient(make_settings())


async def test_create_post_calls_sdk_posts_create(fake_xdk, x):
    created = await x.create_post("tok-abc", "hello world")

    sdk = FakeSDK.instances[0]
    assert sdk.access_token == "tok-abc"
    assert sdk.calls == [("posts.create", {"text": "hello world"})]
    assert created.post_id == "555"
    assert created.text == "hello world"


async def test_create_post_uses_sdk_request_model_when_available(fake_xdk, x, monkeypatch):
    class CreateRequest:
        def __init__(self, text):
            self.text = text

    models = types.ModuleType("xdk.posts.models")
    models.CreateRequest = CreateRequest
    monkeypatch.setitem(sys.modules, "xdk.posts.models", models)

    await x.create_post("tok", "typed body")

    body = FakeSDK.instances[0].calls[0][1]
    assert isinstance(body, CreateRequest)
    assert body.text == "typed body"


async def test_delete_post_calls_sdk_posts_delete(fake_xdk, x):
    assert await x.delete_post("tok", "777") is True
    assert FakeSDK.instances[0].calls == [("posts.delete", "777")]


async def test_get_me_calls_sdk_users_get_me(fake_xdk, x):
    me = await x.get_me("tok")
    assert me == {"id": "9", "name": "N", "username": "u"}
    assert FakeSDK.instances[0].calls == [("users.get_me",)]


async def test_sdk_http_errors_are_translated(fake_xdk, x, monkeypatch):
    original_init = FakeSDK.__init__

    def failing_init(self, *a, **kw):
        original_init(self, *a, **kw)
        self.error = HTTPError(429)

    monkeypatch.setattr(FakeSDK, "__init__", failing_init)
    with pytest.raises(XSDKError) as exc:
        await x.create_post("tok", "hi")
    assert exc.value.status_code == 429
    assert exc.value.x_status_code == 429


async def test_missing_post_id_in_response_is_an_error(fake_xdk, x, monkeypatch):
    monkeypatch.setattr(FakeSDK, "_create", lambda self, body=None: SimpleNamespace(data=None))
    with pytest.raises(XSDKError):
        await x.create_post("tok", "hi")
