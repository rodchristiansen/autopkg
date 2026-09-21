#!/usr/local/autopkg/python
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from email.message import Message
from unittest.mock import patch

from autopkglib.URLDownloaderPython import URLDownloaderPython

# autopkglib re-exports each processor class under its module's own name,
# so patch the module object rather than the dotted path.
PROCESSOR_MODULE = sys.modules["autopkglib.URLDownloaderPython"]

PAYLOAD = b"chunked-transfer-payload" * 100


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, data: bytes, headers: dict):
        super().__init__(data)
        message = Message()
        for key, value in headers.items():
            message[key] = value
        self.headers = message

    def info(self) -> Message:
        return self.headers


class TestURLDownloaderPythonHeaders(unittest.TestCase):
    """A response without Content-Length must still produce a file.

    A chunked transfer carries no Content-Length. Recording the headers used
    to be strict about it, and the failure discarded a download that had
    already streamed to disk while still reporting success, so the next
    processor in the recipe failed on a file that was never there.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.processor = URLDownloaderPython()
        self.processor.env = {
            "url": "https://example.com/Example.pkg",
            "filename": "Example.pkg",
            "RECIPE_CACHE_DIR": self.temp_dir,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_with_headers(self, headers: dict):
        with patch.object(
            PROCESSOR_MODULE, "urlopen", return_value=FakeResponse(PAYLOAD, headers)
        ):
            self.processor.main()
        return self.processor.env["pathname"]

    def test_download_without_content_length_is_kept(self):
        pathname = self.run_with_headers(
            {
                "Transfer-Encoding": "chunked",
                "ETag": 'W/"6ab12470-a6013c"',
                "Last-Modified": "Mon, 21 Sep 2026 12:34:56 GMT",
            }
        )

        self.assertTrue(self.processor.env["download_changed"])
        self.assertTrue(os.path.isfile(pathname))
        with open(pathname, "rb") as downloaded:
            self.assertEqual(downloaded.read(), PAYLOAD)

    def test_streamed_size_recorded_when_content_length_absent(self):
        pathname = self.run_with_headers(
            {"Transfer-Encoding": "chunked", "ETag": 'W/"6ab12470-a6013c"'}
        )

        with open(pathname + ".info.json") as info_json:
            metadata = json.load(info_json)
        self.assertEqual(metadata["http_headers"]["Content-Length"], len(PAYLOAD))
        self.assertEqual(metadata["http_headers"]["ETag"], 'W/"6ab12470-a6013c"')
        self.assertIsNone(metadata["http_headers"]["Last-Modified"])

    def test_unchanged_chunked_download_is_not_refetched(self):
        headers = {"Transfer-Encoding": "chunked", "ETag": 'W/"6ab12470-a6013c"'}
        pathname = self.run_with_headers(headers)
        first_mtime = os.path.getmtime(pathname)

        self.run_with_headers(headers)

        self.assertFalse(self.processor.env["download_changed"])
        self.assertTrue(os.path.isfile(pathname))
        self.assertEqual(os.path.getmtime(pathname), first_mtime)


if __name__ == "__main__":
    unittest.main()
