"""Tests for curl response handling in URLGetter."""

import subprocess
import unittest
from unittest.mock import patch

from autopkglib import ProcessorError
from autopkglib.URLGetter import URLGetter


class TestURLGetter(unittest.TestCase):
    def test_binary_curl_error_preserves_diagnostic(self):
        processor = URLGetter()
        failure = subprocess.CalledProcessError(
            6,
            ["curl", "https://example.com"],
            stderr=b"curl: (6) Could not resolve host: example.com\n",
        )

        with patch("subprocess.run", side_effect=failure):
            with self.assertRaises(ProcessorError) as raised:
                processor.download("https://example.com")

        self.assertEqual(
            str(raised.exception),
            "curl: (6) Could not resolve host: example.com",
        )
        self.assertIs(raised.exception.__cause__, failure)

    def test_text_curl_error_preserves_diagnostic(self):
        processor = URLGetter()
        failure = subprocess.CalledProcessError(
            22,
            ["curl", "https://example.com"],
            stderr="curl: (22) HTTP 404\n",
        )

        with patch("subprocess.run", side_effect=failure):
            with self.assertRaises(ProcessorError) as raised:
                processor.download_with_curl(["curl", "https://example.com"])

        self.assertEqual(str(raised.exception), "curl: (22) HTTP 404")
