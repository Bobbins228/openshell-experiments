"""Regression tests for inserting reports around the document marker."""

import unittest
from unittest.mock import patch

from lib.errors import AgentError
from lib.push_to_gdoc import _utf16_length, build_requests, find_insert_index
from lib.report import validate_report


def document(text):
    """Build the paragraph indices returned by Google Docs for plain text."""
    content = []
    index = 1
    for line in text.splitlines(keepends=True):
        end = index + _utf16_length(line)
        content.append({
            "startIndex": index,
            "endIndex": end,
            "paragraph": {"elements": [{"textRun": {"content": line}}]},
        })
        index = end
    return {"body": {"content": content}}


def insert_report(doc_text, report):
    with patch("lib.push_to_gdoc.google_api_request", return_value=document(doc_text)):
        index = find_insert_index({}, "doc-id")
    validate_report(report)
    requests = build_requests(report, index)
    # Convert the API's UTF-16 position to a Python string offset.
    offset = len(doc_text.encode("utf-16-le")[: (index - 1) * 2].decode("utf-16-le"))
    return doc_text[:offset] + requests[0]["insertText"]["text"] + doc_text[offset:], requests, index


class PushReportTests(unittest.TestCase):
    def test_repeated_same_day_reports_stay_in_order(self):
        text = "Purpose 🌈\nPlease add the current date.\n"
        old = {"date": "Nov 20, 2026", "summary": "First summary.",
               "bullets": [{"label": "First:", "text": " First detail.\n"}]}
        new = {"date": "Nov 20, 2026", "summary": "Second summary.",
               "bullets": [{"label": "Second:", "text": " Second detail.\n"}]}
        text, _, _ = insert_report(text, old)
        text, _, _ = insert_report(text, new)
        self.assertIn("Please add the current date.\n\nNov 20, 2026\n\nSecond summary.", text)
        self.assertIn("Second: Second detail.\n\n\nNov 20, 2026\n\nFirst summary.", text)

    def test_formatting_ranges_follow_inserted_text_in_utf16(self):
        report = {"date": "Nov 20, 2026", "summary": "Rainbow 🌈 summary.",
                  "bullets": [{"label": "Update:", "text": " A detail.\n"}]}
        text, requests, _ = insert_report("Please add the current date.\n", report)
        encoded = text.encode("utf-16-le")
        bold = next(r["updateTextStyle"]["range"] for r in requests if "updateTextStyle" in r)
        start, end = bold["startIndex"] - 1, bold["endIndex"] - 1
        self.assertEqual(encoded[start * 2:end * 2].decode("utf-16-le"), "Update:")
        bullet = next(r["createParagraphBullets"]["range"] for r in requests if "createParagraphBullets" in r)
        start, end = bullet["startIndex"] - 1, bullet["endIndex"] - 1
        self.assertEqual(encoded[start * 2:end * 2].decode("utf-16-le"), "Update: A detail.\n")

    def test_missing_marker_fails_without_an_insert(self):
        with patch("lib.push_to_gdoc.google_api_request", return_value=document("No marker here.\n")):
            with self.assertRaises(AgentError):
                find_insert_index({}, "doc-id")


if __name__ == "__main__":
    unittest.main()
