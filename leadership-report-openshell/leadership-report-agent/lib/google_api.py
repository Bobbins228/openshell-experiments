"""Shared HTTP client for Google APIs."""

import http.client
import json
import os
import urllib.error
import urllib.request

from .errors import AgentError
from .http_errors import error_body

# make requests to Google API, method= GET/POST, url=web address, headers=auth, body=contents of the message/package, defaults to None
def google_api_request(method: str, url: str, headers: dict, body=None) -> dict:
    """Make an HTTP request to a Google API and return the parsed JSON response.

    Raises AgentError on HTTP or network failure.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = {**headers} # copy the auth headers
    if data is not None:
        req_headers["Content-Type"] = "application/json" # give it the Content-Type of application/json
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method) # construct the request
    try:
        timeout = float(os.environ.get("GOOGLE_REQUEST_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise AgentError("GOOGLE_REQUEST_TIMEOUT_SECONDS must be a number.", retriable=False) from exc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_response = resp.read()
        try:
            result = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentError("Google API returned invalid JSON.", retriable=False) from exc
        if not isinstance(result, dict):
            raise AgentError("Google API returned an unexpected JSON value.", retriable=False)
        return result
    except urllib.error.HTTPError as e: # handle exceptions
        err_body = error_body(e)
        if e.code == 401: # auth headers were rejected
            raise AgentError(
                f"Authentication rejected by API (401). Token may be expired.",
                retriable=False,
            ) from e
        if e.code == 403: # auth headers were valid, but didnt have access to this specifically
            raise AgentError(
                f"Access denied (403). Check scopes or permissions.\n{err_body}",
                retriable=False,
            ) from e
        if e.code == 404: # resource not found
            raise AgentError(
                f"Resource not found (404).\n{err_body}",
                retriable=False,
            ) from e
        if e.code in (408, 429) or e.code >= 500:
            raise AgentError(
                f"Google API transient error ({e.code}). Retry later.\n{err_body}",
                retriable=True,
            ) from e
        raise AgentError(f"HTTP {e.code}\n{err_body}", retriable=False) from e
    except (OSError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", e)
        raise AgentError(f"Network error: {reason}", retriable=True) from e
