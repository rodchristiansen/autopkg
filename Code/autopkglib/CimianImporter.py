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
"""See docstring for CimianImporter class"""

import hashlib
import os
import shutil

import yaml

from autopkglib import Processor, ProcessorError

__all__ = ["CimianImporter"]

# Cimian pkgsinfo YAML key ordering for consistent output
PKGSINFO_KEY_ORDER = [
    "name",
    "display_name",
    "identifier",
    "version",
    "blocking_applications",
    "catalogs",
    "category",
    "description",
    "developer",
    "installer",
    "installs",
    "uninstaller",
    "minimum_os_version",
    "maximum_os_version",
    "supported_architectures",
    "unattended_install",
    "unattended_uninstall",
    "requires",
    "update_for",
    "preinstall_script",
    "postinstall_script",
    "preuninstall_script",
    "postuninstall_script",
    "installcheck_script",
    "uninstallcheck_script",
    "icon_name",
]


def _sha256_hash(filepath):
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ordered_pkgsinfo(pkgsinfo):
    """Return an OrderedDict-style list of tuples for YAML output,
    following Cimian's conventional key ordering."""
    ordered = {}
    for key in PKGSINFO_KEY_ORDER:
        if key in pkgsinfo:
            ordered[key] = pkgsinfo[key]
    # Append any remaining keys not in the canonical order
    for key in pkgsinfo:
        if key not in ordered:
            ordered[key] = pkgsinfo[key]
    return ordered


def _represent_str(dumper, data):
    """Use literal block scalar for multi-line strings (scripts)."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class CimianImporter(Processor):
    """Imports a Windows installer (MSI/EXE/NUPKG) into a Cimian deployment repository."""

    description = __doc__
    lifecycle = {"introduced": "3.0.0"}
    input_variables = {
        "CIMIAN_REPO": {
            "required": True,
            "description": (
                "Path to the root of a Cimian deployment repository. "
                "The repo should contain deployment/pkgs/ and deployment/pkgsinfo/ directories."
            ),
        },
        "pkg_path": {
            "required": True,
            "description": "Path to an installer file (MSI, EXE, or NUPKG) to import.",
        },
        "cimian_pkginfo": {
            "required": False,
            "description": (
                "Dictionary of pkgsinfo keys to set or override in the generated metadata. "
                "Common keys: name, display_name, version, category, developer, description."
            ),
        },
        "cimian_subdirectory": {
            "required": False,
            "description": (
                "The subdirectory under deployment/pkgs/ and deployment/pkgsinfo/ "
                "where the item will be placed. E.g., 'apps/browsers'."
            ),
            "default": "",
        },
        "installer_type": {
            "required": True,
            "description": "Type of installer: msi, exe, or nupkg.",
        },
        "installer_switches": {
            "required": False,
            "description": (
                "List of command-line switches for silent installation. "
                "E.g., ['quiet', 'norestart'] or ['/S', '/NCRC']."
            ),
        },
        "supported_architectures": {
            "required": False,
            "description": "List of supported architectures. E.g., ['x64'] or ['x64', 'arm64'].",
            "default": ["x64"],
        },
        "catalogs": {
            "required": False,
            "description": "List of catalogs to assign. Defaults to ['Development'].",
            "default": ["Development"],
        },
        "installs_items": {
            "required": False,
            "description": (
                "List of dicts describing installed artifacts for verification. "
                "Each dict should have 'type' (msi or file), and for MSI: "
                "'product_code'/'upgrade_code', for file: 'path' and optional 'md5checksum'."
            ),
        },
        "blocking_applications": {
            "required": False,
            "description": "List of application names that should be closed before installation.",
        },
        "requires": {
            "required": False,
            "description": "List of package names that this package depends on.",
        },
        "update_for": {
            "required": False,
            "description": "List of package names that this package is an update for.",
        },
        "minimum_os_version": {
            "required": False,
            "description": "Minimum Windows version required (e.g., '10.0.19041').",
        },
        "maximum_os_version": {
            "required": False,
            "description": "Maximum Windows version supported.",
        },
        "installcheck_script": {
            "required": False,
            "description": (
                "PowerShell script to check if installation is needed. "
                "Exit 0 = install needed, exit 1 = skip."
            ),
        },
        "uninstallcheck_script": {
            "required": False,
            "description": (
                "PowerShell script to check if uninstallation is needed. "
                "Exit 0 = uninstall needed, exit 1 = skip."
            ),
        },
        "preinstall_script": {
            "required": False,
            "description": "PowerShell script to run before installation.",
        },
        "postinstall_script": {
            "required": False,
            "description": "PowerShell script to run after installation.",
        },
        "preuninstall_script": {
            "required": False,
            "description": "PowerShell script to run before uninstallation.",
        },
        "postuninstall_script": {
            "required": False,
            "description": "PowerShell script to run after uninstallation.",
        },
        "uninstaller_path": {
            "required": False,
            "description": "Path to a separate uninstaller executable, if applicable.",
        },
        "force_cimian_import": {
            "required": False,
            "description": "If True, import even if a matching package already exists in the repo.",
            "default": False,
        },
        "unattended_install": {
            "required": False,
            "description": "Whether the installer can run without user interaction.",
            "default": True,
        },
        "unattended_uninstall": {
            "required": False,
            "description": "Whether the uninstaller can run without user interaction.",
            "default": False,
        },
    }
    output_variables = {
        "cimian_pkginfo_path": {
            "description": (
                "The path where the pkgsinfo YAML was written. "
                "Empty if item was not imported."
            ),
        },
        "cimian_pkg_path": {
            "description": (
                "The path where the installer was copied in the repo. "
                "Empty if item was not imported."
            ),
        },
        "cimian_repo_changed": {
            "description": "True if a new item was imported into the repo.",
        },
        "cimian_importer_summary_result": {
            "description": "Description of interesting results.",
        },
    }

    def _find_existing_pkgsinfo(self, pkgsinfo_dir, name, version, architectures=None):
        """Scan pkgsinfo directory for an existing entry matching name, version,
        and (optionally) supported_architectures."""
        if not os.path.isdir(pkgsinfo_dir):
            return None
        for root, _dirs, files in os.walk(pkgsinfo_dir):
            for fname in files:
                if not fname.endswith((".yaml", ".yml")):
                    continue
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if not isinstance(data, dict):
                        continue
                    if data.get("name") != name:
                        continue
                    if str(data.get("version")) != str(version):
                        continue
                    # Architecture-aware matching: if the incoming item
                    # specifies architectures, only match existing pkgsinfo
                    # with the same set of architectures.
                    if architectures:
                        existing_archs = sorted(
                            data.get("supported_architectures") or []
                        )
                        if sorted(architectures) != existing_archs:
                            continue
                    return filepath
                except Exception:
                    continue
        return None

    def _find_existing_by_hash(self, pkgsinfo_dir, installer_hash):
        """Scan pkgsinfo directory for an existing entry matching installer hash."""
        if not os.path.isdir(pkgsinfo_dir):
            return None
        for root, _dirs, files in os.walk(pkgsinfo_dir):
            for fname in files:
                if not fname.endswith((".yaml", ".yml")):
                    continue
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if (
                        isinstance(data, dict)
                        and isinstance(data.get("installer"), dict)
                        and data["installer"].get("hash") == installer_hash
                    ):
                        return filepath
                except Exception:
                    continue
        return None

    def _build_pkgsinfo(self, installer_hash, installer_size):
        """Build a Cimian pkgsinfo dictionary from environment variables."""
        # Start with recipe-provided overrides
        pkgsinfo = dict(self.env.get("cimian_pkginfo", {}))

        # Derive name from filename if not provided
        if "name" not in pkgsinfo:
            basename = os.path.basename(self.env["pkg_path"])
            name, _ = os.path.splitext(basename)
            # Strip architecture and version suffixes for cleaner name
            for suffix in ("-x64", "-x86", "-arm64", "-amd64"):
                if suffix in name:
                    name = name[: name.index(suffix)]
                    break
            pkgsinfo["name"] = name

        if "display_name" not in pkgsinfo:
            pkgsinfo["display_name"] = pkgsinfo["name"]

        if "version" not in pkgsinfo:
            pkgsinfo["version"] = self.env.get("version", "0.0.0")

        # Catalogs
        pkgsinfo["catalogs"] = self.env.get("catalogs", ["Development"])

        # Supported architectures
        pkgsinfo["supported_architectures"] = self.env.get(
            "supported_architectures", ["x64"]
        )

        # Installer block
        subdirectory = self.env.get("cimian_subdirectory", "")
        pkg_basename = os.path.basename(self.env["pkg_path"])
        if subdirectory:
            location = f"\\{subdirectory.replace('/', os.sep)}\\{pkg_basename}"
        else:
            location = f"\\{pkg_basename}"
        # Normalize to backslash paths (Cimian convention)
        location = location.replace("/", "\\")

        installer = {
            "type": self.env["installer_type"],
            "location": location,
            "hash": installer_hash,
            "size": installer_size,
        }
        if self.env.get("installer_switches"):
            installer["switches"] = self.env["installer_switches"]

        pkgsinfo["installer"] = installer

        # Unattended flags
        pkgsinfo["unattended_install"] = self.env.get("unattended_install", True)
        pkgsinfo["unattended_uninstall"] = self.env.get("unattended_uninstall", False)

        # Optional fields from env
        optional_list_fields = [
            "blocking_applications",
            "requires",
            "update_for",
            "installs_items",
        ]
        for field in optional_list_fields:
            if self.env.get(field):
                # installs_items maps to "installs" in pkgsinfo
                key = "installs" if field == "installs_items" else field
                pkgsinfo[key] = self.env[field]

        optional_string_fields = [
            "minimum_os_version",
            "maximum_os_version",
            "installcheck_script",
            "uninstallcheck_script",
            "preinstall_script",
            "postinstall_script",
            "preuninstall_script",
            "postuninstall_script",
        ]
        for field in optional_string_fields:
            if self.env.get(field):
                pkgsinfo[field] = self.env[field]

        # Uninstaller block
        if self.env.get("uninstaller_path") and os.path.isfile(
            self.env["uninstaller_path"]
        ):
            uninst_hash = _sha256_hash(self.env["uninstaller_path"])
            uninst_size = os.path.getsize(self.env["uninstaller_path"])
            uninst_basename = os.path.basename(self.env["uninstaller_path"])
            if subdirectory:
                uninst_location = (
                    f"\\{subdirectory.replace('/', os.sep)}\\{uninst_basename}"
                )
            else:
                uninst_location = f"\\{uninst_basename}"
            uninst_location = uninst_location.replace("/", "\\")
            pkgsinfo["uninstaller"] = {
                "type": os.path.splitext(uninst_basename)[1].lstrip("."),
                "location": uninst_location,
                "hash": uninst_hash,
                "size": uninst_size,
            }

        return pkgsinfo

    def _write_pkgsinfo_yaml(self, pkgsinfo, output_path):
        """Write pkgsinfo dict to a YAML file with Cimian-style formatting."""
        ordered = _ordered_pkgsinfo(pkgsinfo)

        dumper = yaml.SafeDumper
        dumper.add_representer(str, _represent_str)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(
                ordered,
                f,
                Dumper=dumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

    def main(self) -> None:
        # Validate installer_type
        valid_types = ("msi", "exe", "nupkg", "msix", "pkg")
        installer_type = self.env["installer_type"].lower()
        if installer_type not in valid_types:
            raise ProcessorError(
                f"installer_type '{installer_type}' not valid. "
                f"Must be one of: {', '.join(valid_types)}"
            )

        pkg_path = self.env["pkg_path"]
        if not os.path.isfile(pkg_path):
            raise ProcessorError(f"Installer not found at: {pkg_path}")

        cimian_repo = self.env["CIMIAN_REPO"]
        deployment_dir = os.path.join(cimian_repo, "deployment")
        pkgsinfo_dir = os.path.join(deployment_dir, "pkgsinfo")
        pkgs_dir = os.path.join(deployment_dir, "pkgs")

        if not os.path.isdir(deployment_dir):
            raise ProcessorError(
                f"Cimian deployment directory not found: {deployment_dir}"
            )

        # Clear any pre-existing summary
        if "cimian_importer_summary_result" in self.env:
            del self.env["cimian_importer_summary_result"]

        self.output(f"Cimian repo: {cimian_repo}")

        # Compute hash and size
        installer_hash = _sha256_hash(pkg_path)
        installer_size = os.path.getsize(pkg_path)
        self.output(f"Installer hash: {installer_hash}", verbose_level=2)
        self.output(f"Installer size: {installer_size}", verbose_level=2)

        # Build pkgsinfo
        pkgsinfo = self._build_pkgsinfo(installer_hash, installer_size)
        name = pkgsinfo["name"]
        version = str(pkgsinfo["version"])

        # Check for duplicates
        archs = pkgsinfo.get("supported_architectures", [])
        if not self.env.get("force_cimian_import"):
            existing = self._find_existing_pkgsinfo(
                pkgsinfo_dir, name, version, architectures=archs
            )
            if not existing:
                existing = self._find_existing_by_hash(pkgsinfo_dir, installer_hash)

            if existing:
                self.output(
                    f"Item {name} {version} already exists in the Cimian repo "
                    f"at {existing}."
                )
                self.env["cimian_pkginfo_path"] = ""
                self.env["cimian_pkg_path"] = ""
                self.env["cimian_repo_changed"] = False
                return

        # Determine target paths
        subdirectory = self.env.get("cimian_subdirectory", "")
        pkg_basename = os.path.basename(pkg_path)

        if subdirectory:
            pkg_dest_dir = os.path.join(pkgs_dir, subdirectory.replace("/", os.sep))
            pkginfo_dest_dir = os.path.join(
                pkgsinfo_dir, subdirectory.replace("/", os.sep)
            )
        else:
            pkg_dest_dir = pkgs_dir
            pkginfo_dest_dir = pkgsinfo_dir

        # Construct filename: Name-version.yaml (or Name-arch-version.yaml)
        if archs and len(archs) == 1:
            pkgsinfo_filename = f"{name}-{archs[0]}-{version}.yaml"
        else:
            pkgsinfo_filename = f"{name}-{version}.yaml"

        pkg_dest = os.path.join(pkg_dest_dir, pkg_basename)
        pkgsinfo_dest = os.path.join(pkginfo_dest_dir, pkgsinfo_filename)

        # Copy installer to repo
        os.makedirs(pkg_dest_dir, exist_ok=True)
        shutil.copy2(pkg_path, pkg_dest)
        self.output(f"Copied installer to: {pkg_dest}")

        # Copy uninstaller if provided
        if self.env.get("uninstaller_path") and os.path.isfile(
            self.env["uninstaller_path"]
        ):
            uninst_dest = os.path.join(
                pkg_dest_dir, os.path.basename(self.env["uninstaller_path"])
            )
            shutil.copy2(self.env["uninstaller_path"], uninst_dest)
            self.output(f"Copied uninstaller to: {uninst_dest}")

        # Write pkgsinfo YAML
        self._write_pkgsinfo_yaml(pkgsinfo, pkgsinfo_dest)
        self.output(f"Wrote pkgsinfo to: {pkgsinfo_dest}")

        # Set output variables
        self.env["cimian_pkginfo_path"] = pkgsinfo_dest
        self.env["cimian_pkg_path"] = pkg_dest
        self.env["cimian_repo_changed"] = True
        self.env["cimian_importer_summary_result"] = {
            "summary_text": "The following new items were imported into Cimian:",
            "report_fields": [
                "name",
                "version",
                "catalogs",
                "supported_architectures",
                "pkginfo_path",
                "pkg_path",
            ],
            "data": {
                "name": name,
                "version": version,
                "catalogs": ", ".join(pkgsinfo.get("catalogs", [])),
                "supported_architectures": archs,
                "pkginfo_path": os.path.relpath(pkgsinfo_dest, deployment_dir),
                "pkg_path": os.path.relpath(pkg_dest, deployment_dir),
            },
        }


if __name__ == "__main__":
    PROCESSOR = CimianImporter()
    PROCESSOR.execute_shell()
