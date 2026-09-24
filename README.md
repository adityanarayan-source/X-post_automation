# X-post_automation

A FastAPI backend that connects to X with **OAuth 2.0 (Authorization Code + PKCE)** and publishes / manages posts using the **official X Python SDK (`xdk`)**. Post history and tokens are stored in **MongoDB**.

- Repo: https://github.com/adityanarayan-source/X-post_automation
- Every X operation (auth, refresh, create/delete post, current user) goes through `xdk`. There are **no direct HTTP calls to X endpoints** and **no Tweepy**.

## Features

| Area | Endpoint |
|---|---|
| OAuth login (returns X authorization URL) | `GET /api/v1/auth/x/login` (`?redirect=true` to redirect) |
| OAuth callback (called by X) | `GET /api/v1/auth/x/callback` |
| Current X user | `GET /api/v1/auth/x/me` |
| Publish a text post | `POST /api/v1/posts/text` |
| Post history (paginated) | `GET /api/v1/posts?page=1&page_size=20&status=published` |
| Single post | `GET /api/v1/posts/{post_id}` |
| Delete post (history kept as `deleted`) | `DELETE /api/v1/posts/{post_id}` |
| Health | `GET /health`, `GET /health/detailed` |

Swagger UI: `http://localhost:8000/docs`

## Architecture

```
app/
├── main.py                  # app factory, lifespan (Mongo connect + indexes), routers
├── api/                     # thin HTTP layer (no business logic, no SDK clients)
│   ├── auth.py  posts.py  health.py  deps.py
├── services/
│   ├── x_client.py          # the ONLY place that calls xdk (posts.create/delete, users.get_me)
│   ├── x_auth_service.py    # xdk OAuth2PKCEAuth: login URL, state, PKCE, code exchange, refresh
│   ├── token_service.py     # load -> expiry check (+buffer) -> refresh -> persist; 401 retry
│   ├── post_service.py      # publish/list/get/delete + MongoDB history
│   └── container.py         # wires services together
├── database/
│   ├── mongodb.py           # async PyMongo client + index creation
│   └── repositories/        # token_repository, post_repository, oauth_state_repository
├── schemas/                 # Pydantic request/response models
└── core/                    # config, logging (with secret redaction), exceptions, error handlers
```

Request flow for `POST /api/v1/posts/text`:

```
route -> PostService -> TokenService.with_valid_token()  (refreshes if expiring / on 401)
                     -> XClient.create_post()            (xdk: client.posts.create(...))
                     -> PostRepository.create()          (MongoDB `posts`)
```

**Extending later** (drafts, images/video, scheduling, threads, replies, analytics, multiple accounts, AI generation): add a method to `XClient`, a service beside `PostService`, and a repository. Tokens are keyed by `provider`, so multi-account support means adding an account key there. None of these are implemented now.

### MongoDB
Database `x_post_automation` (indexes are created automatically at startup):

- `tokens` – `provider, access_token, refresh_token, token_type, expires_at, scopes, created_at, updated_at`
- `posts` – `x_post_id, text, status, post_url, created_at, updated_at, error_message`
- `oauth_states` – short-lived (TTL 10 min) `state` + PKCE `code_verifier` between `/login` and `/callback`

## Setup

### 1. Requirements
Python 3.11+ and a running MongoDB.

### 2. MongoDB
Pick one:

```bash
# Docker (easiest)
docker run -d --name x-mongo -p 27017:27017 mongo:7

# macOS (Homebrew)
brew tap mongodb/brew && brew install mongodb-community && brew services start mongodb-community

# Ubuntu/Debian: follow https://www.mongodb.com/docs/manual/administration/install-on-linux/
# Windows: install MongoDB Community Server (it runs as a service), or use the Docker command above.
```
MongoDB Atlas also works: put the connection string in `MONGODB_URI`.

### 3. X Developer Portal (OAuth 2.0)
1. Go to https://developer.x.com → **Developer Portal** → create (or open) a **Project** and an **App**.
2. In the app, open **User authentication settings → Set up** and choose:
   - **App permissions:** `Read and write`
   - **Type of App:** `Web App, Automated App or Bot` (confidential client — this gives you a Client Secret)
   - **Callback URI / Redirect URL:** `http://localhost:8000/api/v1/auth/x/callback` (must match `X_REDIRECT_URI` **exactly**)
   - **Website URL:** `https://github.com/adityanarayan-source/X-post_automation`
3. Save, then open **Keys and tokens** → **OAuth 2.0 Client ID and Client Secret** → copy both (regenerate the secret if you never saved it).

> ⚠️ The "Access Token / Access Token Secret" shown on the same page are **OAuth 1.0a** credentials. They do **not** work here. `X_ACCESS_TOKEN` / `X_REFRESH_TOKEN` in `.env` are optional and, if used, must be **OAuth 2.0 user-context** tokens. The normal way to get them is the login flow below — leave them blank.

**Required scopes:** `tweet.read`, `tweet.write`, `users.read`, `offline.access` (the last one is what returns a refresh token).

### 4. Configure `.env`
`.env` is git-ignored. `.env.example` is the template (a blank `.env` is already created for you).

| Variable | Required | Value |
|---|---|---|
| `X_CLIENT_ID` | ✅ | OAuth 2.0 Client ID from the portal |
| `X_CLIENT_SECRET` | ✅ | OAuth 2.0 Client Secret from the portal |
| `X_ACCESS_TOKEN` / `X_REFRESH_TOKEN` | optional | Leave blank. Only for bootstrapping with existing OAuth 2.0 tokens (they are copied into MongoDB on first use) |
| `X_REDIRECT_URI` | ✅ | `http://localhost:8000/api/v1/auth/x/callback` (same as the portal) |
| `X_SCOPES` | ✅ | `tweet.read,tweet.write,users.read,offline.access` |
| `MONGODB_URI` / `MONGODB_DATABASE` | ✅ | default `mongodb://localhost:27017` / `x_post_automation` |
| `TOKEN_REFRESH_BUFFER_SECONDS` | – | Refresh this many seconds before expiry (default `300`) |
| `APP_ENV`, `DEBUG`, `HOST`, `PORT` | – | Defaults are fine for local use. `APP_ENV=production` disables `/docs` and debug details |

### 5. Install & run
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/verify_xdk.py       # checks the installed xdk exposes what this project uses

uvicorn app.main:app --reload      # or:  python run.py
```
Tests (no real X calls, no MongoDB needed): `pytest`

## Using Swagger (`/docs`)
1. **Health** → `GET /health/detailed` → expect MongoDB `ok` and X auth `ok`.
2. **X Authentication** → `GET /api/v1/auth/x/login` → Execute → copy `data.authorization_url` into your browser.
   (Shortcut: open `http://localhost:8000/api/v1/auth/x/login?redirect=true` directly in the browser.)
3. Approve on X. You land on the callback, which shows `"X account connected successfully"`. Tokens are now in MongoDB and refresh automatically. You only need to do this once (again only if a refresh fails).
4. `GET /api/v1/auth/x/me` → shows your X account.
5. **Posts** → `POST /api/v1/posts/text` with `{"text": "Testing my X post automation backend!"}` → the post is published on X and saved in MongoDB.
6. `GET /api/v1/posts`, `GET /api/v1/posts/{post_id}`, `DELETE /api/v1/posts/{post_id}`.

## Using Postman
Import `docs/postman_collection.json`. The `baseUrl` variable defaults to `http://localhost:8000`. Do the X login step in a **browser** (Postman can't complete X's consent screen); after that all other requests work. *Create text post* stores the new `post_id` in a collection variable for *Get single post* and *Delete post*.

## Error format
```json
{ "success": false, "error": { "code": "authentication_required", "message": "...", "details": null } }
```
Common codes: `validation_error` (422), `authentication_required` / `token_refresh_failed` / `x_unauthorized` (401 → re-run login), `x_forbidden` (403), `x_rate_limited` (429), `x_api_error`/`x_unavailable` (502), `database_error` (503), `internal_error` (500).

## Troubleshooting
- **`/health/detailed` → mongodb `down`** – MongoDB isn't running/reachable; check `MONGODB_URI`.
- **401 `authentication_required`** – no tokens stored yet: run the login flow.
- **401 `token_refresh_failed`** – the refresh token was revoked/used/expired (X refresh tokens rotate and are single-use). Run the login flow again.
- **X shows a redirect/callback error** – the callback URL in the portal must equal `X_REDIRECT_URI` character for character (`localhost` vs `127.0.0.1` matters). Use `http://localhost:8000/...` in the browser too.
- **403 `x_forbidden`** – duplicate text (X rejects identical posts), app permission not `Read and write` (change it in the portal, then log in again), or your X API plan/credits don't allow posting.
- **429** – rate limit; wait and retry.
- **`x_sdk_error` / `TypeError` / `AttributeError` from the SDK** – the installed `xdk` version differs from what this code expects. Run `python scripts/verify_xdk.py`; the SDK glue is isolated in `app/services/x_client.py` and `app/services/x_auth_service.py`.
- **Callback works in browser but `localhost` doesn't connect** – some systems resolve `localhost` to IPv6 first; set `HOST=0.0.0.0` (or run `uvicorn app.main:app --host ::`) for local testing.
- **Post length** – 280 characters are enforced locally; X's own counting (links = 23 chars, some emoji = 2) is the final authority.

## Security notes
- All credentials come from `.env` (git-ignored). Nothing secret is in source, README, or `.env.example`.
- Tokens, the client secret, authorization codes and DB credentials are never returned by the API and are scrubbed from logs (including uvicorn access logs containing `?code=`) and error messages.
- OAuth `state` is random, single-use and expires (10 min); PKCE is handled by the SDK.
- Stack traces are never returned to clients; in production (`APP_ENV=production`) `/docs` is disabled and no error details are exposed.
- Tokens are stored **unencrypted** in MongoDB (as specified). Restrict MongoDB access; consider field-level encryption before deploying anywhere shared.
- The API itself has **no user authentication**: anyone who can reach it can post to the connected account. Keep it bound to `127.0.0.1` (default) or add auth before exposing it.
- The `OAUTHLIB_INSECURE_TRANSPORT` flag is set automatically **only** when `X_REDIRECT_URI` is an `http://localhost` URL (needed for local development). Use HTTPS redirect URIs in production.
