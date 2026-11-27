#!/usr/bin/env python3
"""Leadership Report Agent — self-contained pipeline.

Fetches meeting notes from Google Drive, analyzes them via an LLM
(an OpenAI-compatible endpoint), generates a structured leadership
report, and pushes it to a Google Doc.

Environment variables:
    OPENAI_BASE_URL          Real OpenAI-compatible inference endpoint (required)
    OPENAI_API_KEY           API key or OpenShell provider placeholder
    MODEL_ID                 Model identifier served by the endpoint
    LEADERSHIP_DOC_ID        Google Doc ID to push the report to (required)
    GOOGLE_QUOTA_PROJECT     GCP project for API quota
    MEETING_NAME             Google Meet meeting name to search for (required)

Exit codes:
    0  Success
    1  Usage error
    2  Permanent failure (auth invalid, doc missing, bad config)
    3  Transient failure (network, server error)
"""

import json # read structured data
import os # to read computer settings
import sys
import time # wait/pause
from datetime import datetime, timezone

# imports from other files in this folder
from lib.errors import AgentError, EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT
from lib.auth import get_auth_headers
from lib.fetch_gdoc import fetch_notes
from lib.llm import analyze_notes
from lib.push_to_gdoc import push_report

# where to save the files temporarily while it works on them
NOTES_PATH = "/tmp/meeting_notes.txt"
REPORT_PATH = "/tmp/leadership_report.json"

# uses this to write what its doing with the exact time
def log(step: str, message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{step}] {message}") # ts=timestamp, step=passed in, message=passed in


def run() -> int:
    log("init", "Leadership Report Agent starting") # calls out that its starting

    try: # use try, so if it does fail it doesnt crash
        log("auth", "Checking Google credentials...")
        get_auth_headers() # try to get_auth_headers()
        log("auth", "AUTH_OK") # log the result, if it fails, stop here

        log("fetch", "Fetching meeting notes from Google Drive...")
        meeting_name = os.environ.get("MEETING_NAME") # set the meeting name by getting from os environment
        fetch_notes(NOTES_PATH, meeting_name=meeting_name) # try to fetch the notes and download them to NOTES_PATH

        with open(NOTES_PATH) as f: # open the file
            notes_text = f.read() # read it

        if not notes_text.strip(): # if its empty (strip removes whitespace)
            log("fetch", "FETCH_FAILED: Meeting notes file is empty.") # say what happened
            return EXIT_PERMANENT # treats it as a permanent problem, not an intermittent failure

        log("fetch", f"Fetched {len(notes_text)} bytes of meeting notes") # record the storage size of the file as proof that reading it worked

        log("llm", "Sending notes to LLM for analysis...")
        report = analyze_notes(notes_text) # send the "readed" (idk my brains not working T_T) notes to the LLM and ask it to analyze
        log("llm", f"Report generated: {report['date']}, {len(report['bullets'])} bullets") # give the data and number of bullet points in the report

        with open(REPORT_PATH, "w") as f: # open with write perms,
            json.dump(report, f, indent=2) # make a json dump- aka save a backup of the file temporarily to temp folder
        log("llm", f"Report JSON written to {REPORT_PATH}")

        log("push", "Pushing report to Google Doc...")
        push_report(report) # upload the file to Google Docs

    except AgentError as e: # if anything goes wrong in the 'try' block, the error will get captured and written down
        log("error", str(e))
        return EXIT_TRANSIENT if e.retriable else EXIT_PERMANENT # if its permanent, give up, if its temp, maybe try again

    log("done", "Leadership report published successfully")
    return EXIT_OK

# the supervisor
def main() -> None:
    exit_code = run() # call run()

    if exit_code == EXIT_TRANSIENT: # if theres a transient failure in run()
        log("retry", "Transient failure — retrying in 30s...")
        time.sleep(30) # wait 30s
        exit_code = run() # try again
        if exit_code != EXIT_OK: # if it fails again
            log("retry", "Retry failed. Giving up.") # give up

    sys.exit(exit_code)


if __name__ == "__main__": # if a human runs the file directly, call main()
    main()
