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
"""Tests for munkirepolibs YAML write path and catalog format detection."""

import os
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autopkglib.autopkgyaml import detect_munki_format, load_munki_file


class TestAutoPkgLibYamlWrite(unittest.TestCase):
    """AutoPkgLib.copy_pkginfo_to_repo picks format from file_extension."""

    def test_yaml_extension_writes_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkgsinfo"))

            from autopkglib.munkirepolibs.AutoPkgLib import AutoPkgLib

            lib = AutoPkgLib(tmpdir, "")
            pkginfo = {"name": "Test", "version": "1.0", "catalogs": ["testing"]}
            result_path = lib.copy_pkginfo_to_repo(pkginfo, file_extension="yaml")

            self.assertTrue(result_path.endswith(".yaml"))
            self.assertTrue(os.path.exists(result_path))
            self.assertEqual(detect_munki_format(result_path), "yaml")
            loaded = load_munki_file(result_path)
            self.assertEqual(loaded["name"], "Test")
            self.assertEqual(loaded["version"], "1.0")

    def test_plist_extension_writes_plist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "pkgsinfo"))

            from autopkglib.munkirepolibs.AutoPkgLib import AutoPkgLib

            lib = AutoPkgLib(tmpdir, "")
            pkginfo = {"name": "Test", "version": "1.0", "catalogs": ["testing"]}
            result_path = lib.copy_pkginfo_to_repo(pkginfo, file_extension="plist")

            self.assertTrue(result_path.endswith(".plist"))
            self.assertTrue(os.path.exists(result_path))
            with open(result_path, "rb") as f:
                loaded = plistlib.load(f)
            self.assertEqual(loaded["name"], "Test")


class TestAutoPkgLibYamlCatalog(unittest.TestCase):
    """AutoPkgLib.make_catalog_db reads catalogs in either format.

    Munki writes yaml catalogs at the same extensionless path as plist
    catalogs (see munki/munki#1261); format is detected by content
    inspection.
    """

    def test_read_yaml_catalog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalogs_path = os.path.join(tmpdir, "catalogs")
            os.makedirs(catalogs_path)
            catalog = [
                {
                    "name": "Firefox",
                    "version": "126.0",
                    "installer_item_hash": "abc123",
                    "installer_item_location": "apps/Firefox-126.0.dmg",
                },
            ]
            import yaml

            with open(
                os.path.join(catalogs_path, "all"), "w", encoding="utf-8"
            ) as f:
                yaml.dump(catalog, f)

            from autopkglib.munkirepolibs.AutoPkgLib import AutoPkgLib

            lib = AutoPkgLib(tmpdir, "")
            pkgdb = lib.make_catalog_db()
            self.assertIn("abc123", pkgdb["hashes"])
            self.assertEqual(len(pkgdb["items"]), 1)
            self.assertEqual(pkgdb["items"][0]["name"], "Firefox")

    def test_read_plist_catalog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalogs_path = os.path.join(tmpdir, "catalogs")
            os.makedirs(catalogs_path)
            catalog = [
                {
                    "name": "Chrome",
                    "version": "100.0",
                    "installer_item_hash": "def456",
                    "installer_item_location": "apps/Chrome-100.0.dmg",
                },
            ]
            with open(os.path.join(catalogs_path, "all"), "wb") as f:
                plistlib.dump(catalog, f)

            from autopkglib.munkirepolibs.AutoPkgLib import AutoPkgLib

            lib = AutoPkgLib(tmpdir, "")
            pkgdb = lib.make_catalog_db()
            self.assertIn("def456", pkgdb["hashes"])

    def test_empty_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from autopkglib.munkirepolibs.AutoPkgLib import AutoPkgLib

            lib = AutoPkgLib(tmpdir, "")
            pkgdb = lib.make_catalog_db()
            self.assertEqual(len(pkgdb["items"]), 0)


if __name__ == "__main__":
    unittest.main()
