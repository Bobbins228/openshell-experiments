"""Exercise ambiguous writes and failed HTTP error-body reads."""

import io
import urllib.error
import unittest
from unittest.mock import patch

from lib.auth import _token_from_env
from lib.errors import AgentError
from lib.fetch_gdoc import export_doc
from lib.google_api import google_api_request
from lib.push_to_gdoc import push_report
from test_push_to_gdoc import document


class BrokenBody(io.BytesIO):
    def read(self, *args):
        raise TimeoutError("body timed out")


class ErrorBodyTests(unittest.TestCase):
    def test_failed_body_read_preserves_status_classification(self):
        calls = [
            lambda: _token_from_env("id", "secret", "refresh"),
            lambda: export_doc("doc", "/unused", {}),
            lambda: google_api_request("GET", "https://example.com", {}),
        ]
        for status, retriable in ((503, True), (403, False)):
            for call in calls:
                with self.subTest(status=status, call=call):
                    error = urllib.error.HTTPError("https://example.com", status, "error", {}, BrokenBody())
                    with patch("urllib.request.urlopen", side_effect=error):
                        with self.assertRaises(AgentError) as raised:
                            call()
                    error.close()
                    self.assertEqual(raised.exception.retriable, retriable)


class PublishRetryTests(unittest.TestCase):
    report = {"date": "Nov 20, 2026", "summary": "Summary.",
              "bullets": [{"label": "Update:", "text": " Detail.\n"}]}

    def setUp(self):
        self.doc = document("Please add the current date.\n")
        self.doc["revisionId"] = "revision-1"
        self.marker = "leadership-report-" + "a" * 32

    def run_push(self, handler):
        with patch.dict("os.environ", {"LEADERSHIP_DOC_ID": "doc-id"}), \
             patch("lib.push_to_gdoc.get_auth_headers", return_value={}), \
             patch("lib.push_to_gdoc.uuid.uuid4") as identifier, \
             patch("lib.push_to_gdoc.time.sleep") as sleep, \
             patch("lib.push_to_gdoc.google_api_request", side_effect=handler):
            identifier.return_value.hex = "a" * 32
            push_report(self.report)
            return sleep

    def test_committed_write_with_lost_response_is_not_repeated(self):
        calls = []
        def api(method, url, headers, body=None):
            calls.append((method, body))
            if len(calls) == 1:
                return self.doc
            if method == "POST":
                raise AgentError("timeout", retriable=True)
            return {"revisionId": "revision-2", "namedRanges": {self.marker: {}}}
        self.run_push(api)
        self.assertEqual([method for method, _ in calls], ["GET", "POST", "GET"])
        body = calls[1][1]
        self.assertEqual(body["writeControl"]["requiredRevisionId"], "revision-1")
        self.assertEqual(body["requests"][1]["createNamedRange"]["name"], self.marker)

    def test_retry_uses_same_revision_and_marker_when_first_not_visible(self):
        posts = []
        def api(method, url, headers, body=None):
            if method == "GET":
                return self.doc
            posts.append(body)
            if len(posts) == 1:
                raise AgentError("timeout", retriable=True)
            return {}
        sleep = self.run_push(api)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0], posts[1])
        sleep.assert_called_once_with(30)

    def test_retry_conflict_reconciles_original_write(self):
        count = 0
        def api(method, url, headers, body=None):
            nonlocal count
            count += 1
            if count in (1, 3):
                return self.doc
            if method == "POST":
                raise AgentError("timeout or revision conflict", retriable=count == 2)
            return {"revisionId": "revision-2", "namedRanges": {self.marker: {}}}
        self.run_push(api)
        self.assertEqual(count, 5)

    def test_unknown_outcome_never_retries_pipeline(self):
        count = 0
        def api(method, url, headers, body=None):
            nonlocal count
            count += 1
            if count == 1:
                return self.doc
            if method == "POST":
                raise AgentError("timeout", retriable=True)
            raise AgentError("cannot read document", retriable=True)
        with self.assertRaises(AgentError) as raised:
            self.run_push(api)
        self.assertFalse(raised.exception.retriable)
        self.assertIn(self.marker, str(raised.exception))
        self.assertEqual(count, 3)

    def test_changed_revision_without_marker_stops(self):
        count = 0
        def api(method, url, headers, body=None):
            nonlocal count
            count += 1
            if count == 1:
                return self.doc
            if method == "POST":
                raise AgentError("timeout", retriable=True)
            return {"revisionId": "revision-2"}
        with self.assertRaises(AgentError) as raised:
            self.run_push(api)
        self.assertFalse(raised.exception.retriable)
        self.assertEqual(count, 3)

    def test_two_ambiguous_attempts_do_not_restart_pipeline(self):
        calls = []
        def api(method, url, headers, body=None):
            calls.append(method)
            if method == "POST":
                raise AgentError("timeout", retriable=True)
            return self.doc
        with self.assertRaises(AgentError) as raised:
            self.run_push(api)
        self.assertFalse(raised.exception.retriable)
        self.assertEqual(calls, ["GET", "POST", "GET", "POST", "GET"])

    def test_missing_revision_fails_before_write(self):
        self.doc.pop("revisionId")
        calls = []
        def api(method, url, headers, body=None):
            calls.append(method)
            return self.doc
        with self.assertRaises(AgentError) as raised:
            self.run_push(api)
        self.assertFalse(raised.exception.retriable)
        self.assertEqual(calls, ["GET"])


if __name__ == "__main__":
    unittest.main()
