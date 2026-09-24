"""Verify that the installed official X SDK (`xdk`) exposes what this project uses.

Run after `pip install -r requirements.txt`:

    python scripts/verify_xdk.py

Makes NO network calls. Exit code 0 = everything needed was found.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import sys

problems: list[str] = []


def sig(obj) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(signature unavailable)"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'OK' if ok else 'MISSING'}] {label} {detail}".rstrip())
    if not ok:
        problems.append(label)


try:
    print("xdk version:", importlib.metadata.version("xdk"))
except importlib.metadata.PackageNotFoundError:
    print("xdk is NOT installed. Run: pip install -r requirements.txt")
    sys.exit(2)

# --- Client -------------------------------------------------------------------------
xdk = importlib.import_module("xdk")
Client = getattr(xdk, "Client", None)
check("xdk.Client", Client is not None, sig(Client) if Client else "")
if Client is not None:
    try:
        client = Client(access_token="dummy-token-for-introspection")
        check("Client(access_token=...)", True)
    except TypeError as exc:
        client = None
        check("Client(access_token=...)", False, f"-> {exc}")
    if client is not None:
        posts = getattr(client, "posts", None)
        users = getattr(client, "users", None)
        check("client.posts.create", hasattr(posts, "create"), sig(getattr(posts, "create", None)))
        check("client.posts.delete", hasattr(posts, "delete"), sig(getattr(posts, "delete", None)))
        check("client.users.get_me", hasattr(users, "get_me"), sig(getattr(users, "get_me", None)))

# --- Request model for posts.create -------------------------------------------------------
try:
    models = importlib.import_module("xdk.posts.models")
    found = [n for n in ("CreateRequest", "CreatePostRequest", "CreatePostsRequest") if hasattr(models, n)]
    print(f"[INFO] xdk.posts.models create-request model(s): {found or 'none (dict body will be used)'}")
except ImportError:
    print("[INFO] xdk.posts.models not importable (dict body will be used)")

# --- OAuth 2.0 PKCE ----------------------------------------------------------------------
try:
    oauth = importlib.import_module("xdk.oauth2_auth")
    cls = getattr(oauth, "OAuth2PKCEAuth", None)
    check("xdk.oauth2_auth.OAuth2PKCEAuth", cls is not None, sig(cls) if cls else "")
    if cls is not None:
        for name in ("get_authorization_url", "exchange_code", "fetch_token", "refresh_token"):
            attr = getattr(cls, name, None)
            print(f"[{'OK' if attr else 'INFO'}] OAuth2PKCEAuth.{name} {sig(attr) if attr else '(not present)'}")
        check(
            "OAuth2PKCEAuth can exchange a code",
            hasattr(cls, "exchange_code") or hasattr(cls, "fetch_token"),
        )
        check("OAuth2PKCEAuth.refresh_token", hasattr(cls, "refresh_token"))
except ImportError as exc:
    check("xdk.oauth2_auth module", False, f"-> {exc}")

print()
if problems:
    print("PROBLEMS:", ", ".join(problems))
    print("Adjust app/services/x_client.py / x_auth_service.py to the signatures printed above.")
    sys.exit(1)
print("All required xdk entry points are present.")
