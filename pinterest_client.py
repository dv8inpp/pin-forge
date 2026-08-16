"""
Pinterest API v5 client.

Handles the OAuth2 authorization-code flow (so you log in once through
Pinterest's consent screen and the app stores/refreshes the token itself),
plus the two calls PinForge actually needs: list boards, and create a pin.

Docs: https://developers.pinterest.com/docs/getting-started/authentication/
"""
import os
import base64
import requests
from requests_oauthlib import OAuth2Session

AUTH_BASE_URL = "https://www.pinterest.com/oauth/"


def _token_url() -> str:
    """Same -sandbox-in-the-hostname pattern as the data endpoints, but
    for the token exchange/refresh URL specifically. This matters more
    than it might look: a token minted against the production token URL
    is NOT valid against api-sandbox.pinterest.com's data endpoints even
    though it looks like a normal, working Bearer token (Pinterest just
    flatly rejects it with a 401 there) -- the token itself has to be
    minted through the sandbox token endpoint to work in Sandbox at all."""
    if os.environ.get("PINTEREST_USE_SANDBOX", "").lower() in ("1", "true", "yes"):
        return "https://api-sandbox.pinterest.com/v5/oauth/token"
    return "https://api.pinterest.com/v5/oauth/token"


def _api_base() -> str:
    """Pinterest requires Trial-access apps to hit a separate Sandbox host
    for most calls -- set PINTEREST_USE_SANDBOX=1 in .env while you're on
    Trial access; flip it off (or remove it) once you're approved for
    Standard access and your app can create real, publicly-visible pins."""
    if os.environ.get("PINTEREST_USE_SANDBOX", "").lower() in ("1", "true", "yes"):
        return "https://api-sandbox.pinterest.com/v5"
    return "https://api.pinterest.com/v5"

SCOPES = ["boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read"]


def get_authorization_url():
    client_id = os.environ["PINTEREST_APP_ID"]
    redirect_uri = os.environ["PINTEREST_REDIRECT_URI"]
    oauth = OAuth2Session(client_id, redirect_uri=redirect_uri, scope=SCOPES)
    auth_url, state = oauth.authorization_url(AUTH_BASE_URL)
    return auth_url, state


def exchange_code_for_token(authorization_response_url: str, state: str) -> dict:
    client_id = os.environ["PINTEREST_APP_ID"]
    client_secret = os.environ["PINTEREST_APP_SECRET"]
    redirect_uri = os.environ["PINTEREST_REDIRECT_URI"]

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    oauth = OAuth2Session(client_id, redirect_uri=redirect_uri, state=state, scope=SCOPES)
    token = oauth.fetch_token(
        _token_url(),
        authorization_response=authorization_response_url,
        headers={"Authorization": f"Basic {basic}"},
        include_client_id=True,
    )
    return token


def refresh_token(token: dict) -> dict:
    client_id = os.environ["PINTEREST_APP_ID"]
    client_secret = os.environ["PINTEREST_APP_SECRET"]
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    resp = requests.post(
        _token_url(),
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    new_token = resp.json()
    # Pinterest may omit refresh_token on refresh responses; keep the old one.
    new_token.setdefault("refresh_token", token.get("refresh_token"))
    return new_token


def _authed_headers(token: dict) -> dict:
    return {"Authorization": f"Bearer {token['access_token']}"}


def list_boards(token: dict) -> list[dict]:
    resp = requests.get(f"{_api_base()}/boards", headers=_authed_headers(token),
                         params={"page_size": 100}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("items", [])


def create_pin(token: dict, board_id: str, title: str, description: str,
                link: str, image_path: str) -> dict:
    """
    Uploads a local image file and creates the pin in one call, using
    Pinterest's base64 media_source option (simplest path for a
    self-hosted app with no public image URL required).
    """
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "board_id": board_id,
        "title": title[:100],
        "description": description[:800],
        "link": link,
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/png",
            "data": image_b64,
        },
    }
    resp = requests.post(
        f"{_api_base()}/pins",
        headers={**_authed_headers(token), "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()
