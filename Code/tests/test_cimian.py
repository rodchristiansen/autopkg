#!/usr/local/autopkg/python

import os
import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory

import yaml

from autopkglib import ProcessorError
from autopkglib.CimianImporter import CimianImporter, _ordered_pkgsinfo, _sha256_hash


class TestCimianImporter(unittest.TestCase):
    """Test class for CimianImporter Processor."""

    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.cimian_repo = os.path.join(self.tmp_dir.name, "cimian_repo")
        self.pkg_path = os.path.join(self.tmp_dir.name, "TestApp-x64-1.0.0.msi")

        # Create Cimian repo structure
        os.makedirs(os.path.join(self.cimian_repo, "deployment", "pkgs"))
        os.makedirs(os.path.join(self.cimian_repo, "deployment", "pkgsinfo"))
        os.makedirs(os.path.join(self.cimian_repo, "deployment", "catalogs"))

        # Create a dummy installer
        with open(self.pkg_path, "wb") as f:
            f.write(b"dummy msi installer content for testing")

        self.good_env = {
            "CIMIAN_REPO": self.cimian_repo,
            "pkg_path": self.pkg_path,
            "installer_type": "msi",
            "catalogs": ["Development"],
            "supported_architectures": ["x64"],
            "cimian_subdirectory": "",
            "force_cimian_import": False,
            "unattended_install": True,
            "unattended_uninstall": False,
            "verbose": 0,
        }

        self.processor = CimianImporter()
        self.processor.env = deepcopy(self.good_env)

    def tearDown(self):
        self.tmp_dir.cleanup()

    # --- Helper tests ---

    def test_sha256_hash(self):
        """SHA256 hash should be computed correctly."""
        h = _sha256_hash(self.pkg_path)
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_sha256_hash_deterministic(self):
        """Same file should always produce the same hash."""
        h1 = _sha256_hash(self.pkg_path)
        h2 = _sha256_hash(self.pkg_path)
        self.assertEqual(h1, h2)

    def test_ordered_pkgsinfo_puts_name_first(self):
        """Key ordering should place name, display_name, version first."""
        data = {
            "unattended_install": True,
            "name": "Test",
            "version": "1.0",
            "catalogs": ["Development"],
            "display_name": "Test App",
        }
        ordered = _ordered_pkgsinfo(data)
        keys = list(ordered.keys())
        self.assertEqual(keys[0], "name")
        self.assertEqual(keys[1], "display_name")
        self.assertEqual(keys[2], "version")

    # --- Import tests ---

    def test_basic_import(self):
        """A basic import should copy the installer and create pkgsinfo YAML."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "TestApp",
            "display_name": "Test Application",
            "version": "1.0.0",
            "category": "Testing",
            "developer": "TestCorp",
        }
        self.processor.process()

        # Check outputs
        self.assertTrue(self.processor.env["cimian_repo_changed"])
        self.assertTrue(os.path.isfile(self.processor.env["cimian_pkg_path"]))
        self.assertTrue(os.path.isfile(self.processor.env["cimian_pkginfo_path"]))

        # Verify pkgsinfo YAML content
        with open(self.processor.env["cimian_pkginfo_path"], "r") as f:
            pkgsinfo = yaml.safe_load(f)
        self.assertEqual(pkgsinfo["name"], "TestApp")
        self.assertEqual(pkgsinfo["version"], "1.0.0")
        self.assertEqual(pkgsinfo["installer"]["type"], "msi")
        self.assertIn("hash", pkgsinfo["installer"])
        self.assertIn("size", pkgsinfo["installer"])

    def test_import_with_subdirectory(self):
        """Import with a subdirectory should create nested paths."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "Chrome",
            "version": "120.0",
        }
        self.processor.env["cimian_subdirectory"] = "apps/browsers"
        self.processor.process()

        pkginfo_path = self.processor.env["cimian_pkginfo_path"]
        pkg_path = self.processor.env["cimian_pkg_path"]
        self.assertIn("browsers", pkginfo_path)
        self.assertIn("browsers", pkg_path)
        self.assertTrue(os.path.isfile(pkginfo_path))
        self.assertTrue(os.path.isfile(pkg_path))

    def test_import_derives_name_from_filename(self):
        """If no name is provided, derive it from the installer filename."""
        self.processor.process()

        with open(self.processor.env["cimian_pkginfo_path"], "r") as f:
            pkgsinfo = yaml.safe_load(f)
        # "TestApp-x64-1.0.0.msi" -> strips -x64 suffix -> "TestApp"
        self.assertEqual(pkgsinfo["name"], "TestApp")

    def test_import_with_scripts(self):
        """Scripts should be written as literal block scalars in YAML."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "ScriptApp",
            "version": "1.0",
        }
        self.processor.env["postinstall_script"] = (
            "# Remove desktop shortcut\n"
            "Remove-Item 'C:\\Users\\Public\\Desktop\\App.lnk' -Force\n"
        )
        self.processor.env["installcheck_script"] = (
            "if (Test-Path 'C:\\Program Files\\App\\app.exe') {\n"
            "  exit 1\n"
            "}\n"
            "exit 0\n"
        )
        self.processor.process()

        with open(self.processor.env["cimian_pkginfo_path"], "r") as f:
            pkgsinfo = yaml.safe_load(f)
        self.assertIn("postinstall_script", pkgsinfo)
        self.assertIn("installcheck_script", pkgsinfo)
        self.assertIn("Remove-Item", pkgsinfo["postinstall_script"])

    def test_import_with_installer_switches(self):
        """Installer switches should be included in the installer block."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "SilentApp",
            "version": "2.0",
        }
        self.processor.env["installer_switches"] = ["quiet", "norestart"]
        self.processor.process()

        with open(self.processor.env["cimian_pkginfo_path"], "r") as f:
            pkgsinfo = yaml.safe_load(f)
        self.assertEqual(pkgsinfo["installer"]["switches"], ["quiet", "norestart"])

    # --- Duplicate detection tests ---

    def test_duplicate_by_name_version_skips(self):
        """Importing a duplicate name+version should skip without force."""
        # First import
        self.processor.env["cimian_pkginfo"] = {
            "name": "DupeApp",
            "version": "1.0",
        }
        self.processor.process()
        self.assertTrue(self.processor.env["cimian_repo_changed"])

        # Second import of same name+version
        new_processor = CimianImporter()
        new_processor.env = deepcopy(self.good_env)
        new_processor.env["cimian_pkginfo"] = {
            "name": "DupeApp",
            "version": "1.0",
        }
        new_processor.process()
        self.assertFalse(new_processor.env["cimian_repo_changed"])
        self.assertEqual(new_processor.env["cimian_pkginfo_path"], "")

    def test_duplicate_by_hash_skips(self):
        """Importing a file with the same hash should skip."""
        # First import
        self.processor.env["cimian_pkginfo"] = {
            "name": "HashApp",
            "version": "1.0",
        }
        self.processor.process()
        self.assertTrue(self.processor.env["cimian_repo_changed"])

        # Second import with different name but same file (same hash)
        new_processor = CimianImporter()
        new_processor.env = deepcopy(self.good_env)
        new_processor.env["cimian_pkginfo"] = {
            "name": "DifferentName",
            "version": "2.0",
        }
        new_processor.process()
        self.assertFalse(new_processor.env["cimian_repo_changed"])

    def test_force_import_overrides_duplicate(self):
        """force_cimian_import should import even when duplicate exists."""
        # First import
        self.processor.env["cimian_pkginfo"] = {
            "name": "ForceApp",
            "version": "1.0",
        }
        self.processor.process()
        self.assertTrue(self.processor.env["cimian_repo_changed"])

        # Second import with force
        new_processor = CimianImporter()
        new_processor.env = deepcopy(self.good_env)
        new_processor.env["cimian_pkginfo"] = {
            "name": "ForceApp",
            "version": "1.0",
        }
        new_processor.env["force_cimian_import"] = True
        new_processor.process()
        self.assertTrue(new_processor.env["cimian_repo_changed"])

    # --- Validation tests ---

    def test_invalid_installer_type_raises(self):
        """An invalid installer_type should raise ProcessorError."""
        self.processor.env["installer_type"] = "invalid"
        with self.assertRaises(ProcessorError):
            self.processor.process()

    def test_missing_installer_file_raises(self):
        """A missing pkg_path should raise ProcessorError."""
        self.processor.env["pkg_path"] = "/nonexistent/file.msi"
        with self.assertRaises(ProcessorError):
            self.processor.process()

    def test_missing_deployment_dir_raises(self):
        """A missing deployment directory should raise ProcessorError."""
        self.processor.env["CIMIAN_REPO"] = "/nonexistent/repo"
        with self.assertRaises(ProcessorError):
            self.processor.process()

    # --- Summary result tests ---

    def test_summary_result_on_import(self):
        """Summary result should be populated after successful import."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "SummaryApp",
            "version": "3.0",
        }
        self.processor.process()

        summary = self.processor.env["cimian_importer_summary_result"]
        self.assertIn("summary_text", summary)
        self.assertIn("data", summary)
        self.assertEqual(summary["data"]["name"], "SummaryApp")
        self.assertEqual(summary["data"]["version"], "3.0")

    # --- YAML output format tests ---

    def test_yaml_output_is_valid(self):
        """Generated pkgsinfo YAML should be valid and parseable."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "YamlApp",
            "version": "1.0",
            "category": "Testing",
        }
        self.processor.process()

        with open(self.processor.env["cimian_pkginfo_path"], "r") as f:
            content = f.read()
        # Should be valid YAML
        data = yaml.safe_load(content)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["name"], "YamlApp")

    def test_pkgsinfo_filename_includes_arch(self):
        """Pkgsinfo filename should include architecture when single arch."""
        self.processor.env["cimian_pkginfo"] = {
            "name": "ArchApp",
            "version": "1.0",
        }
        self.processor.env["supported_architectures"] = ["x64"]
        self.processor.process()

        pkginfo_path = self.processor.env["cimian_pkginfo_path"]
        self.assertIn("ArchApp-x64-1.0.yaml", os.path.basename(pkginfo_path))


class TestCimianInfoCreator(unittest.TestCase):
    """Test class for CimianInfoCreator Processor."""

    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.pkg_path = os.path.join(self.tmp_dir.name, "TestApp-x64-2.5.0.exe")

        # Create a dummy EXE file
        with open(self.pkg_path, "wb") as f:
            f.write(b"MZ" + b"\x00" * 100)  # Minimal PE header

        self.good_env = {
            "pkg_path": self.pkg_path,
            "installer_type": "exe",
            "verbose": 0,
        }

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_derives_name_from_filename(self):
        """Should derive package name from filename, stripping arch suffix."""
        from autopkglib.CimianInfoCreator import CimianInfoCreator

        processor = CimianInfoCreator()
        processor.env = deepcopy(self.good_env)
        processor.process()

        pkginfo = processor.env["cimian_pkginfo"]
        self.assertEqual(pkginfo["name"], "TestApp")

    def test_uses_provided_name(self):
        """Should use explicitly provided name over derived one."""
        from autopkglib.CimianInfoCreator import CimianInfoCreator

        processor = CimianInfoCreator()
        processor.env = deepcopy(self.good_env)
        processor.env["cimian_info_name"] = "CustomName"
        processor.process()

        pkginfo = processor.env["cimian_pkginfo"]
        self.assertEqual(pkginfo["name"], "CustomName")

    def test_uses_provided_version(self):
        """Should use explicitly provided version."""
        from autopkglib.CimianInfoCreator import CimianInfoCreator

        processor = CimianInfoCreator()
        processor.env = deepcopy(self.good_env)
        processor.env["cimian_info_version"] = "3.14.159"
        processor.process()

        self.assertEqual(processor.env["version"], "3.14.159")
        self.assertEqual(processor.env["cimian_pkginfo"]["version"], "3.14.159")

    def test_sets_category_and_developer(self):
        """Should set category and developer when provided."""
        from autopkglib.CimianInfoCreator import CimianInfoCreator

        processor = CimianInfoCreator()
        processor.env = deepcopy(self.good_env)
        processor.env["cimian_info_category"] = "Browsers"
        processor.env["cimian_info_developer"] = "TestCorp"
        processor.process()

        pkginfo = processor.env["cimian_pkginfo"]
        self.assertEqual(pkginfo["category"], "Browsers")
        self.assertEqual(pkginfo["developer"], "TestCorp")

    def test_missing_file_raises(self):
        """Should raise ProcessorError for missing installer."""
        from autopkglib.CimianInfoCreator import CimianInfoCreator

        processor = CimianInfoCreator()
        processor.env = deepcopy(self.good_env)
        processor.env["pkg_path"] = "/does/not/exist.exe"
        with self.assertRaises(ProcessorError):
            processor.process()


class TestCimianCatalogBuilder(unittest.TestCase):
    """Test class for CimianCatalogBuilder Processor."""

    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.cimian_repo = os.path.join(self.tmp_dir.name, "cimian_repo")
        os.makedirs(os.path.join(self.cimian_repo, "deployment", "pkgsinfo"))
        os.makedirs(os.path.join(self.cimian_repo, "deployment", "catalogs"))

        self.good_env = {
            "CIMIAN_REPO": self.cimian_repo,
            "skip_payload_check": True,
            "cimian_repo_changed": True,
            "force_catalog_rebuild": False,
            "verbose": 0,
        }

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_skips_when_no_changes(self):
        """Should skip catalog rebuild when cimian_repo_changed is False."""
        from autopkglib.CimianCatalogBuilder import CimianCatalogBuilder

        processor = CimianCatalogBuilder()
        processor.env = deepcopy(self.good_env)
        processor.env["cimian_repo_changed"] = False
        processor.process()

        self.assertFalse(processor.env["cimian_catalogs_rebuilt"])

    def test_force_rebuild_overrides_skip(self):
        """force_catalog_rebuild should trigger rebuild even without changes."""
        from autopkglib.CimianCatalogBuilder import CimianCatalogBuilder

        processor = CimianCatalogBuilder()
        processor.env = deepcopy(self.good_env)
        processor.env["cimian_repo_changed"] = False
        processor.env["force_catalog_rebuild"] = True
        # This will fail because makecatalogs isn't installed, but it should
        # attempt the rebuild (not skip)
        with self.assertRaises(ProcessorError) as ctx:
            processor.process()
        # The error should be about makecatalogs not being found, not about skipping
        self.assertIn("makecatalogs", str(ctx.exception))

    def test_missing_deployment_dir_raises(self):
        """Should raise ProcessorError for missing deployment directory."""
        from autopkglib.CimianCatalogBuilder import CimianCatalogBuilder

        processor = CimianCatalogBuilder()
        processor.env = deepcopy(self.good_env)
        processor.env["CIMIAN_REPO"] = "/nonexistent/repo"
        with self.assertRaises(ProcessorError):
            processor.process()


if __name__ == "__main__":
    unittest.main()
