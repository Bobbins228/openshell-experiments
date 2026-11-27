"""Helpers for handling HTTP error responses."""

import http.client


def error_body(error) -> str:
    """Return a diagnostic body when available, without masking the status."""
    try:
        return error.read(500).decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
        return "[error response body unavailable]"
