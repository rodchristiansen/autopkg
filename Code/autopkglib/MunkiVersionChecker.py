#!/usr/local/autopkg/python
#
# Copyright 2026 Rod Christiansen
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
"""See docstring for MunkiVersionChecker class"""

import glob
import os

from autopkglib import Processor, ProcessorError
from autopkglib.autopkgyaml import load_munki_file

__all__ = ["MunkiVersionChecker"]


class MunkiVersionChecker(Processor):
    """Checks if a version of a package already exists in the Munki repo's
    pkgsinfo directory. If the version is found, sets stop_processing_recipe
    to True to skip downloading and importing.

    Place this processor AFTER a version info provider (such as
    SparkleUpdateInfoProvider or GitHubReleasesInfoProvider) and BEFORE
    URLDownloader to skip unnecessary downloads when the version already
    exists in the repo.

    Can also be placed AFTER URLDownloader to skip MunkiImporter when the
    download has not changed (download_changed is False)."""

    description = __doc__
    lifecycle = {"introduced": "3.0.0"}
    input_variables = {
        "MUNKI_REPO": {
            "required": True,
            "description": "Path to the Munki repo.",
        },
        "version": {
            "required": False,
            "description": (
                "Version to check against the repo. Typically set by a "
                "previous processor such as SparkleUpdateInfoProvider or "
                "GitHubReleasesInfoProvider. If not set, the processor "
                "falls back to checking download_changed."
            ),
        },
        "NAME": {
            "required": True,
            "description": "Name of the software package.",
        },
        "MUNKI_REPO_SUBDIR": {
            "required": False,
            "description": (
                "Subdirectory within pkgsinfo to search. "
                "If not set, searches all of pkgsinfo."
            ),
        },
        "MUNKI_PKGINFO_FILE_EXTENSION": {
            "required": False,
            "description": (
                "File extension for pkgsinfo files. Defaults to 'plist'."
            ),
            "default": "plist",
        },
        "pkginfo": {
            "required": False,
            "description": (
                "A pkginfo dict. If present and contains a 'name' key, "
                "that value is used for matching instead of NAME."
            ),
        },
        "download_changed": {
            "required": False,
            "description": (
                "Set by URLDownloader. When False, the download did not "
                "change and further processing can be stopped."
            ),
        },
    }
    output_variables = {
        "stop_processing_recipe": {
            "description": (
                "Boolean. Set to True if the version already exists in "
                "the repo or the download has not changed."
            )
        },
        "version_check_matched": {
            "description": "Boolean. True if a matching version was found."
        },
    }

    def _get_pkg_name(self):
        """Determine the package name to match against pkgsinfo files."""
        pkginfo = self.env.get("pkginfo")
        if isinstance(pkginfo, dict) and pkginfo.get("name"):
            return pkginfo["name"]
        return self.env["NAME"]

    def _get_architectures(self):
        """Architectures the recipe's pkginfo is limited to, or an empty set."""
        pkginfo = self.env.get("pkginfo")
        if not isinstance(pkginfo, dict):
            return set()
        archs = set()
        for arch in pkginfo.get("supported_architectures") or []:
            # A recipe's pkginfo can say "%ARCH%"; resolve it from the env
            arch = str(arch)
            if arch.startswith("%") and arch.endswith("%"):
                arch = str(self.env.get(arch.strip("%"), ""))
            if arch:
                archs.add(arch)
        return archs

    def _check_version_exists(self, name, version):
        """Check if a pkgsinfo file for this name and version exists
        in the Munki repo. When the recipe's pkginfo names
        supported_architectures, only a pkgsinfo for one of those
        architectures counts, so the arm64 and x86_64 builds of one
        release do not skip each other."""
        munki_repo = self.env["MUNKI_REPO"]
        repo_subdir = self.env.get("MUNKI_REPO_SUBDIR", "")
        file_ext = self.env.get(
            "MUNKI_PKGINFO_FILE_EXTENSION", "plist"
        ).strip(".")
        archs = self._get_architectures()

        pkgsinfo_dir = os.path.join(munki_repo, "pkgsinfo")
        if repo_subdir:
            search_dir = os.path.join(pkgsinfo_dir, repo_subdir)
        else:
            search_dir = pkgsinfo_dir

        if not os.path.isdir(search_dir):
            return False

        # Quick check: exact filename match. The filename carries no
        # architecture, so this shortcut only applies to recipes that
        # are not limited to particular architectures.
        if not archs:
            for ext in [file_ext] + [
                e for e in ("yaml", "yml", "plist") if e != file_ext
            ]:
                candidate = f"{name}-{version}.{ext}"
                if os.path.exists(os.path.join(search_dir, candidate)):
                    self.output(f"Found existing pkgsinfo: {candidate}")
                    return True

        # Scan files with a matching name prefix and parse them to
        # verify the version field (and architecture, when limited)
        pattern = os.path.join(search_dir, f"{name}-*")
        for filepath in glob.glob(pattern):
            if os.path.basename(filepath).startswith("."):
                continue
            try:
                info = load_munki_file(filepath)
                if (
                    isinstance(info, dict)
                    and info.get("name") == name
                    and str(info.get("version", "")) == str(version)
                ):
                    existing_archs = set(
                        info.get("supported_architectures") or []
                    )
                    if archs and existing_archs and not (archs & existing_archs):
                        continue
                    self.output(
                        f"Found matching version in: "
                        f"{os.path.basename(filepath)}"
                    )
                    return True
            except Exception:
                continue

        return False

        # Quick check: exact filename match with configured extension
        expected_name = f"{name}-{version}.{file_ext}"
        expected_path = os.path.join(search_dir, expected_name)
        if os.path.exists(expected_path):
            self.output(f"Found existing pkgsinfo: {expected_name}")
            return True

        # Check with alternative extensions
        for ext in ("yaml", "yml", "plist"):
            if ext == file_ext:
                continue
            alt_name = f"{name}-{version}.{ext}"
            alt_path = os.path.join(search_dir, alt_name)
            if os.path.exists(alt_path):
                self.output(f"Found existing pkgsinfo: {alt_name}")
                return True

        # If no exact filename match, scan files with matching name prefix
        # and parse them to verify the version field
        pattern = os.path.join(search_dir, f"{name}-*")
        for filepath in glob.glob(pattern):
            if os.path.basename(filepath).startswith("."):
                continue
            try:
                info = load_munki_file(filepath)
                if (
                    isinstance(info, dict)
                    and info.get("name") == name
                    and str(info.get("version", "")) == str(version)
                ):
                    self.output(
                        f"Found matching version in: "
                        f"{os.path.basename(filepath)}"
                    )
                    return True
            except Exception:
                continue

        return False

    def main(self):
        version = self.env.get("version")
        name = self._get_pkg_name()

        # If version is available, check if it already exists in repo
        if version:
            if self._check_version_exists(name, version):
                self.output(
                    f"{name} version {version} already exists in "
                    f"Munki repo. Skipping."
                )
                self.env["stop_processing_recipe"] = True
                self.env["version_check_matched"] = True
                return

        # If URLDownloader already ran and download did not change,
        # no need to continue processing
        if "download_changed" in self.env:
            if not self.env["download_changed"]:
                self.output(
                    "Download unchanged. Skipping further processing."
                )
                self.env["stop_processing_recipe"] = True
                self.env["version_check_matched"] = True
                return

        self.output(
            f"No existing match found for {name}"
            + (f" version {version}" if version else "")
            + ". Continuing."
        )
        self.env["stop_processing_recipe"] = False
        self.env["version_check_matched"] = False


if __name__ == "__main__":
    PROCESSOR = MunkiVersionChecker()
    PROCESSOR.execute_shell()
