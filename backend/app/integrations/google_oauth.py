"""One-time Google sign-in that produces the refresh token for the test calendar.

    python -m app.integrations.google_oauth

Reads GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (a "Desktop app" OAuth client)
from backend/.env, opens Google's consent page, receives the answer on a loopback address
(http://127.0.0.1:<random port>), and prints the refresh token to put in backend/.env as
GOOGLE_OAUTH_REFRESH_TOKEN. Uses PKCE; nothing is stored by this script.
"""

import base64
import hashlib
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import get_settings
from app.integrations.google_calendar import SCOPES, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def pkce_pair() -> tuple[str, str]:
    """A code verifier and its S256 code challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def authorization_url(client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",  # Ask for a refresh token.
        "prompt": "consent",  # Always return one, even if consent was given before.
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def exchange_code(
    http: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    response = http.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
    )
    if response.status_code != 200:
        raise SystemExit(f"Google rejected the sign-in ({response.status_code}): {response.text}")
    data: dict[str, Any] = response.json()
    return data


def _wait_for_redirect(server: HTTPServer) -> dict[str, str]:
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            if "code" in query or "error" in query:
                received.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"VoxCircuit: you can close this tab and return to the terminal.")

        def log_message(self, format: str, *args: Any) -> None:
            return  # Keep the terminal quiet.

    server.RequestHandlerClass = Handler
    server.timeout = 300
    while not received:
        server.handle_request()
    return received


def main() -> None:
    settings = get_settings()
    secret = settings.google_oauth_client_secret
    if not settings.google_oauth_client_id or secret is None:
        raise SystemExit(
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in backend/.env first."
        )
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = authorization_url(settings.google_oauth_client_id, redirect_uri, challenge, state)
    print("Opening Google sign-in. If no browser opens, visit:\n\n" + url + "\n")
    webbrowser.open(url)
    try:
        answer = _wait_for_redirect(server)
    finally:
        server.server_close()
    if answer.get("state") != state:
        raise SystemExit("The sign-in response did not match this request. Try again.")
    if "error" in answer:
        raise SystemExit(f"Google sign-in failed: {answer['error']}")
    with httpx.Client(timeout=15) as http:
        tokens = exchange_code(
            http,
            client_id=settings.google_oauth_client_id,
            client_secret=secret.get_secret_value(),
            code=answer["code"],
            verifier=verifier,
            redirect_uri=redirect_uri,
        )
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise SystemExit("Google returned no refresh token. Revoke the app's access and retry.")
    print("Signed in. Add this line to backend/.env (keep it secret):\n")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={refresh_token}")


if __name__ == "__main__":
    main()
