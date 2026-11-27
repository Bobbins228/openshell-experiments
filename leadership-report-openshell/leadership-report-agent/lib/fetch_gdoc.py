#!/usr/bin/env python3
"""Fetch a Google Doc as plain text.

Usage:
    python -m lib.fetch_gdoc <output_path>                  # auto-fetch latest Gemini notes
    python -m lib.fetch_gdoc <doc_id_or_url> <output_path>  # fetch specific doc

Calendar mode searches Google Drive for the most recent Gemini notes document
matching the meeting name (set via MEETING_NAME env var).

Environment variables:
    MEETING_NAME             Meeting name to search for in Google Drive (required)
    GOOGLE_QUOTA_PROJECT     GCP project for API quota

Exit codes:
    0  Success
    1  Usage error
    2  Permanent failure (bad config, missing doc, auth invalid)
    3  Transient failure (network, server error — safe to retry)

Requires:
    Local:  gcloud auth application-default login with Drive and Docs scopes
"""

import os
import re # regular expression operations
import sys
import urllib.error # contains the exception classes raised by urlib.request
import urllib.parse # break url strings down into components or combines pieces into a complete url
import urllib.request # open and reads url

from .auth import get_auth_headers # get this function from auth
from .errors import AgentError, AuthError, EXIT_USAGE, EXIT_PERMANENT, EXIT_TRANSIENT # get the errors fromm error
from .google_api import google_api_request
from .http_errors import error_body

# takes a google doc link, chops off the unecessary parts and extracts the doc ID
def extract_doc_id(input_str: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", input_str) # ignore everything till you see /d/, then capture everything after until you hit '/'
    if match:
        return match.group(1)
    return input_str

# uses doc id, place to save file, auth headers
def export_doc(doc_id: str, output_path: str, headers: dict) -> None:
    url = (
        f"https://www.googleapis.com/drive/v3/files/{doc_id}"
        f"/export?mimeType=text/plain&supportsAllDrives=true"
    )
    req = urllib.request.Request(url, headers=headers) # use the headers to request the url
    try: # safe try, handle exceptions
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read() # if it works, the content will be populated by reading the response
    except urllib.error.HTTPError as e:
        body = error_body(e)
        if e.code == 404: # url not found or no permission
            raise AgentError(f"Document {doc_id} not found or not accessible.", retriable=False) from e
        if e.code in (408, 429) or e.code >= 500:
            raise AgentError(f"Drive export error ({e.code}). Retry later.\n{body}", retriable=True) from e
        raise AgentError(f"Export failed ({e.code})\n{body}", retriable=False) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AgentError(f"Network error during export: {getattr(e, 'reason', e)}", retriable=True) from e

    with open(output_path, "wb") as f:
        f.write(content) # write the file
    print(f"FETCH_OK: {doc_id} -> {output_path} ({len(content)} bytes)") # report it was ok, and with the id and bytes as proof


def find_latest_notes_doc(headers: dict, meeting_name: str) -> str:
    """Search Drive for the most recent Gemini notes doc matching the meeting name."""
    safe_name = meeting_name.replace("\\", "\\\\").replace("'", "\\'") # sanitise name? remove '\'s and '''s in the meeting title that would mess up name parsing. add a second backslash to any backslashes and add a backslash before any apostrophes (escape them)
    query = ( # construct the search query to type out
        f"name contains '{safe_name}' "
        f"and mimeType = 'application/vnd.google-apps.document' "
        f"and trashed = false"
    )
    params = urllib.parse.urlencode({ # search parameters (query, order, size to show, fields)
        "q": query,
        "orderBy": "createdTime desc",
        "pageSize": 5,
        "fields": "files(id,name,createdTime)",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    })
    url = f"https://www.googleapis.com/drive/v3/files?{params}" # create the search url using the params

    result = google_api_request("GET", url, headers) # send a get request to the url with the auth headers
    files = result.get("files", []) # save the files from the result

    if not files: # if nothing is returned from the search
        raise AgentError(
            f"No Google Doc found matching '{meeting_name}'. "
            f"Gemini note-taking may not be enabled for this meeting.",
            retriable=False, # dont retry
        )

    doc = files[0] # select the first search result (its sorted by latest so this would be the newest)
    print(f"Found: \"{doc['name']}\" (created {doc['createdTime']})") # let everyone know what file you found
    return doc["id"] # return the id


def fetch_notes(output_path: str, meeting_name: str | None = None, doc_ref: str | None = None) -> str:
    """Fetch meeting notes to output_path. Returns the output path on success.

    Raises AuthError on auth failure. Raises AgentError on fetch failure.
    """
    headers = get_auth_headers() # collect the auth needed

    if doc_ref: # if theres a specific link given, extract the ID directly
        doc_id = extract_doc_id(doc_ref) # use the link chopper to get the ID
    else: # if theres no specific link, construct the search
        name = meeting_name or os.environ.get("MEETING_NAME", "").strip() # look for the meeting name and strip whitespace
        if not name: # if theres no name, fail
            raise AgentError(
                "MEETING_NAME environment variable is required. "
                "Set it to the Google Meet meeting name to search for.",
                retriable=False,
            )
        print(f"Searching for Gemini notes matching '{name}'...")
        doc_id = find_latest_notes_doc(headers, name) # call the search function w the meeting name and auth headers and store the output as the doc id

    export_doc(doc_id, output_path, headers) # go download the file from the id, tell it where to save it, and give it the auth
    return output_path


def main(): # if someone invokes the file directly
    if len(sys.argv) == 2:
        output = sys.argv[1]
        try:
            fetch_notes(output, doc_ref=None) # go straight to searching (no google doc link provided)
        except (AuthError, AgentError) as e:
            print(f"FETCH_FAILED: {e}", file=sys.stderr)
            sys.exit(EXIT_TRANSIENT if e.retriable else EXIT_PERMANENT)

    elif len(sys.argv) == 3:
        input_str = sys.argv[1] # string provided
        output = sys.argv[2]
        try:
            fetch_notes(output, doc_ref=input_str) # run with a google doc url provided
        except (AuthError, AgentError) as e:
            print(f"FETCH_FAILED: {e}", file=sys.stderr)
            sys.exit(EXIT_TRANSIENT if e.retriable else EXIT_PERMANENT)

    else: # they didn't give the instructions right, so provide the help menu
        print(
            f"Usage:\n"
            f"  {sys.argv[0]} <output_path>                  # auto-fetch from Drive\n"
            f"  {sys.argv[0]} <doc_id_or_url> <output_path>  # fetch specific doc",
            file=sys.stderr,
        )
        sys.exit(EXIT_USAGE)


if __name__ == "__main__":
    main()
