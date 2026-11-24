#!/usr/bin/env python3
"""Insert a leadership report entry at the top of a Google Doc.

Usage:
    python -m lib.push_to_gdoc <report_json_path>

report_json_path: path to a JSON file with structure:
    {
        "date": "Jun 18, 2026",
        "summary": "One-paragraph summary ...",
        "bullets": [
            {"label": "Category Name:", "text": " Detail text ...\n"},
            ...
        ]
    }

Environment variables:
    LEADERSHIP_DOC_ID        Google Doc ID to push the report to (required)

Exit codes:
    0  Success
    1  Usage error or invalid input
    2  Permanent failure (auth invalid, access denied, bad doc structure)
    3  Transient failure (network, server error — safe to retry)

Requires:
    Local:  gcloud auth application-default login with Drive and Docs scopes
"""

import json
import os
import sys
import time
import uuid

from .auth import get_auth_headers
from .errors import AgentError, AuthError, EXIT_USAGE, EXIT_PERMANENT, EXIT_TRANSIENT
from .google_api import google_api_request
from .report import validate_report


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _get_doc_id() -> str:
    doc_id = os.environ.get("LEADERSHIP_DOC_ID", "").strip() # fetch the doc id from the env vars
    if not doc_id:
        raise AgentError(
            "LEADERSHIP_DOC_ID environment variable is required. "
            "Set it to the Google Doc ID (the alphanumeric string from the doc URL).",
            retriable=False,
        )
    return doc_id


def _find_insert_index(doc: dict) -> int:
    """Find the end of the marker paragraph, before its final newline."""
    for elem in doc["body"]["content"]:
        if "paragraph" not in elem:
            continue
        text = ""
        for el in elem["paragraph"].get("elements", []):
            text += el.get("textRun", {}).get("content", "")
        if "please add the current date" in text.lower():
            # Inserting before the newline also works when this is the last
            # paragraph in the document, and never skips an existing report.
            return elem["endIndex"] - 1

    raise AgentError(
        "Could not find insertion point in document. "
        "The 'Purpose of the document' block may have been removed or changed.",
        retriable=False,
    )


def find_insert_index(headers: dict, doc_id: str) -> int:
    url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    return _find_insert_index(google_api_request("GET", url, headers))


def build_requests(report: dict, insert_index: int) -> list:
    date_line = report["date"] + "\n"
    blank = "\n"
    summary_line = report["summary"].strip() + "\n"

    # The first newline ends the marker paragraph; the second leaves a blank
    # line before the new report. The marker's original newline separates this
    # entry from whatever follows it.
    prefix = "\n\n"
    full_text = prefix + date_line + blank + summary_line
    for b in report["bullets"]:
        full_text += b["label"] + b["text"]

    reqs = [{"insertText": {"location": {"index": insert_index}, "text": full_text}}] # put the text in at the exact index

    pos = insert_index + _utf16_length(prefix)
    pos += _utf16_length(date_line) + _utf16_length(blank)

    summary_start = pos
    summary_end = pos + _utf16_length(summary_line)
    pos = summary_end # finds the start and end positions of each bit

    bold_ranges = [] # formatting V
    bullet_ranges = []
    for b in report["bullets"]:
        label = b["label"]
        text = b["text"]
        label_length = _utf16_length(label)
        text_length = _utf16_length(text)
        bold_ranges.append((pos, pos + label_length))
        bullet_ranges.append((pos, pos + label_length + text_length))
        pos += label_length + text_length

    for bs, be in bold_ranges:
        reqs.append({
            "updateTextStyle": {
                "range": {"startIndex": bs, "endIndex": be},
                "textStyle": {"bold": True},
                "fields": "bold",
            }
        })

    for bs, be in bullet_ranges:
        reqs.append({
            "createParagraphBullets": {
                "range": {"startIndex": bs, "endIndex": be},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }
        })

    reqs.append({
        "updateParagraphStyle": {
            "range": {"startIndex": summary_start, "endIndex": summary_end},
            "paragraphStyle": {
                "spaceBelow": {"magnitude": 12, "unit": "PT"},
                "indentFirstLine": {"unit": "PT"},
                "indentStart": {"unit": "PT"},
            },
            "fields": "spaceBelow,indentFirstLine,indentStart",
        }
    })

    for bs, be in bullet_ranges:
        reqs.append({
            "updateParagraphStyle": {
                "range": {"startIndex": bs, "endIndex": be},
                "paragraphStyle": {
                    "spaceAbove": {"magnitude": 12, "unit": "PT"},
                    "spaceBelow": {"magnitude": 12, "unit": "PT"},
                },
                "fields": "spaceAbove,spaceBelow",
            }
        })

    return reqs


def push_report(report: dict) -> None:
    """Validate and push a report dict to the Google Doc.

    Raises AuthError on auth failure. Raises AgentError on push failure.
    """
    doc_id = _get_doc_id() # get the doc id
    validate_report(report)
    headers = get_auth_headers() # collect the auth headers
    doc_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    doc = google_api_request("GET", doc_url, headers)
    revision = doc.get("revisionId")
    if not revision:
        raise AgentError("Google Doc has no revision ID; cannot safely publish.", retriable=False)
    insert_index = _find_insert_index(doc)
    reqs = build_requests(report, insert_index) # make the request out of the report and the position to put it

    # This marker is created atomically with the text and is invisible in the
    # document. The revision precondition prevents two in-flight attempts from
    # both committing, including when the first response is lost.
    marker = f"leadership-report-{uuid.uuid4().hex}"
    reqs.insert(1, {"createNamedRange": {
        "name": marker,
        "range": {"startIndex": insert_index + 2, "endIndex": insert_index + 2 + _utf16_length(report["date"])},
    }})
    body = {"requests": reqs, "writeControl": {"requiredRevisionId": revision}}
    url = f"{doc_url}:batchUpdate"
    for attempt in range(2):
        try:
            google_api_request("POST", url, headers, body)
            break
        except AgentError as error:
            # Even an invalid response or HTTP 5xx can follow a committed write.
            try:
                current = google_api_request("GET", doc_url, headers)
            except AgentError as check_error:
                raise AgentError(
                    f"Publish outcome unknown; inspect the Google Doc before retrying. Marker: {marker}",
                    retriable=False,
                ) from check_error
            if marker in current.get("namedRanges", {}):
                break
            if attempt == 0 and error.retriable and current.get("revisionId") == revision:
                time.sleep(30)
                continue
            raise AgentError(
                f"Publish outcome unknown or rejected; inspect the Google Doc before retrying. "
                f"Marker: {marker}. Original error: {error}", retriable=False,
            ) from error
    print(f"PUSH_OK: Report for {report['date']} added to doc {doc_id}.") # report back that it worked


def push_report_from_file(report_path: str) -> None: # for manual use, using a saved JSON file
    """Load a report JSON file and push it."""
    try:
        with open(report_path) as f:
            report = json.load(f)
    except FileNotFoundError:
        raise AgentError(f"Report file not found: {report_path}", retriable=False)
    except json.JSONDecodeError as e:
        raise AgentError(f"Invalid JSON in report file: {e}", retriable=False)

    push_report(report)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <report_json_path>", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    try:
        push_report_from_file(sys.argv[1])
    except (AuthError, AgentError) as e:
        print(f"PUSH_FAILED: {e}", file=sys.stderr)
        sys.exit(EXIT_TRANSIENT if e.retriable else EXIT_PERMANENT)


if __name__ == "__main__":
    main()
