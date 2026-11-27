"""Shared Google authentication.

In OpenShell sandboxes: uses the gateway-injected GCP provider placeholder.

Outside OpenShell: refreshes an OAuth token from environment variables, then
falls back to `gcloud auth application-default print-access-token`.

Environment variables:
    GCP_DRIVE_DOCS_ACCESS_TOKEN
                             Gateway-injected Drive/Docs access-token placeholder
    GOOGLE_CLIENT_ID         OAuth2 client ID
    GOOGLE_CLIENT_SECRET     OAuth2 client secret
    GOOGLE_REFRESH_TOKEN     OAuth2 refresh token
    GOOGLE_QUOTA_PROJECT     GCP project for API quota
"""

import json
import os
import subprocess # for running side programs
import sys
import urllib.error # urlib = requests over internet
import urllib.parse
import urllib.request

from .errors import AuthError, EXIT_PERMANENT, EXIT_TRANSIENT # from __init__
from .http_errors import error_body

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REQUEST_TIMEOUT_SECONDS = 30


def get_token() -> str:
    """Return a valid Google OAuth2 access token."""
    provider_token = os.environ.get("GCP_DRIVE_DOCS_ACCESS_TOKEN")
    if provider_token: # OpenShell already refreshed this token for the sandbox
        return provider_token

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if client_id and client_secret and refresh_token: # needs all three
        return _token_from_env(client_id, client_secret, refresh_token) # create a token from them
    return _token_from_gcloud() # if even one is missing, fall back to token from gcloud


def get_auth_headers() -> dict:
    """Return headers for authenticated API calls (token + quota project if set)."""
    headers = {"Authorization": f"Bearer {get_token()}"}
    quota_project = os.environ.get("GOOGLE_QUOTA_PROJECT")
    if quota_project: # if theres a quota project, tell google who to charge money to
        headers["x-goog-user-project"] = quota_project
    return headers # return a header- attached to every request sent so Google knows whos requesting


def _token_from_env(client_id: str, client_secret: str, refresh_token: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode() # create a token from the env

    req = urllib.request.Request(TOKEN_ENDPOINT, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    }) # request a token

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw_response = resp.read()
        try:
            result = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthError("Token endpoint returned invalid JSON.", retriable=False) from exc
        if not isinstance(result, dict):
            raise AuthError("Token endpoint returned an unexpected JSON value.", retriable=False)
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise AuthError("Token endpoint response did not include an access token.", retriable=False)
        return result["access_token"]
    except urllib.error.HTTPError as e: # handle all the different ways google might say no
        # translate each one into a readable error message
        body = error_body(e)
        if e.code == 400 and "invalid_grant" in body: # 400=expired
            raise AuthError(
                f"Refresh token expired or revoked. Re-authenticate and update the secret.\n{body}",
                retriable=False,
            ) from e
        if e.code == 401: # 401=passwords dont match
            raise AuthError(
                f"Invalid client credentials (client_id/client_secret mismatch).\n{body}",
                retriable=False,
            ) from e
        if e.code in (408, 429) or e.code >= 500:
            raise AuthError(
                f"Google token endpoint transient error ({e.code}). Retry later.\n{body}",
                retriable=True,
            ) from e
        raise AuthError(f"Token refresh failed ({e.code}): {body}", retriable=False) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AuthError(
            f"Network error reaching token endpoint: {getattr(e, 'reason', e)}",
            retriable=True, # if its a network error, you can try again
        ) from e


def _token_from_gcloud() -> str: # fallback- uses the gcloud badge the human already has
    try: # try to get the gcloud auth token
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return result.stdout.strip() # return the error with whitespace stripped
    except FileNotFoundError: # if gcloud isnt installed, fail and dont retry
        raise AuthError(
            "gcloud CLI not found. Configure the OpenShell provider or install gcloud for local testing.",
            retriable=False,
        )
    except subprocess.TimeoutExpired: # if it takes more than 30s to respond, fail and retry
        raise AuthError("gcloud auth timed out after 30s.", retriable=True)
    except subprocess.CalledProcessError as e: # if gcloud auth failed, fail and don't retry
        raise AuthError(f"gcloud auth failed: {e.stderr.strip()}", retriable=False) from e


if __name__ == "__main__": # quick test for the user- if a human runs the file, try to quickly get a badge, and print AUTH_OK or prints an error and shuts down
    try:
        headers = get_auth_headers()
        print("AUTH_OK")
        print(f"Token length: {len(headers['Authorization'])} chars") # print the token length as proof it exists
        if "x-goog-user-project" in headers:
            print(f"Quota project: {headers['x-goog-user-project']}") # print the quota project if there is one
    except AuthError as e:
        print(f"AUTH_FAILED: {e}", file=sys.stderr)
        sys.exit(EXIT_PERMANENT if not e.retriable else EXIT_TRANSIENT)
