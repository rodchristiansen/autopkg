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
"""See docstring for CimianInfoCreator class"""

import os
import platform
import subprocess

from autopkglib import Processor, ProcessorError

__all__ = ["CimianInfoCreator"]


class CimianInfoCreator(Processor):
    """Extracts metadata from a Windows installer and creates a Cimian pkgsinfo
    dictionary in the autopkg environment. Does not copy files or modify the repo.

    This processor populates the 'cimian_pkginfo' environment variable with a
    complete pkgsinfo dict, which can then be consumed by CimianImporter.

    For MSI files on Windows, it extracts ProductCode, UpgradeCode, ProductName,
    and ProductVersion via PowerShell. For EXE files, it reads FileVersionInfo.
    """

    description = __doc__
    lifecycle = {"introduced": "3.0.0"}
    input_variables = {
        "pkg_path": {
            "required": True,
            "description": "Path to an installer file (MSI, EXE, or NUPKG).",
        },
        "installer_type": {
            "required": True,
            "description": "Type of installer: msi, exe, or nupkg.",
        },
        "cimian_info_name": {
            "required": False,
            "description": "Package name override. Derived from filename if not provided.",
        },
        "cimian_info_display_name": {
            "required": False,
            "description": "Display name override.",
        },
        "cimian_info_version": {
            "required": False,
            "description": "Version override. Extracted from installer if not provided.",
        },
        "cimian_info_category": {
            "required": False,
            "description": "Category for the package (e.g., 'Browsers', 'Design').",
        },
        "cimian_info_developer": {
            "required": False,
            "description": "Developer / vendor name.",
        },
        "cimian_info_description": {
            "required": False,
            "description": "Package description.",
        },
    }
    output_variables = {
        "cimian_pkginfo": {
            "description": (
                "Dictionary of Cimian pkgsinfo keys extracted from the installer. "
                "Suitable for passing to CimianImporter."
            ),
        },
        "version": {
            "description": "Version string extracted or provided.",
        },
        "cimian_info_creator_summary_result": {
            "description": "Description of interesting results.",
        },
    }

    def _extract_msi_properties(self, msi_path):
        """Extract product properties from an MSI file using PowerShell (Windows only)."""
        if platform.system() != "Windows":
            self.output(
                "MSI property extraction requires Windows. Skipping.", verbose_level=1
            )
            return {}

        ps_script = (
            "$msiPath = '{msi_path}';"
            "$installer = New-Object -ComObject WindowsInstaller.Installer;"
            "$db = $installer.OpenDatabase($msiPath, 0);"
            "$view = $db.OpenView(\"SELECT Property, Value FROM Property "
            "WHERE Property IN ('ProductCode','UpgradeCode','ProductName','ProductVersion','Manufacturer')\");"
            "$view.Execute();"
            "$row = $view.Fetch();"
            "while ($row -ne $null) {{"
            "  Write-Output \"$($row.StringData(1))=$($row.StringData(2))\";"
            "  $row = $view.Fetch();"
            "}}"
        ).format(msi_path=msi_path.replace("'", "''"))

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self.output(f"MSI extraction warning: {result.stderr}", verbose_level=1)
                return {}

            props = {}
            for line in result.stdout.strip().splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
            return props
        except Exception as err:
            self.output(f"MSI extraction failed: {err}", verbose_level=1)
            return {}

    def _extract_exe_version(self, exe_path):
        """Extract FileVersionInfo from an EXE using PowerShell (Windows only)."""
        if platform.system() != "Windows":
            self.output(
                "EXE version extraction requires Windows. Skipping.", verbose_level=1
            )
            return {}

        ps_script = (
            "$info = (Get-Item '{exe_path}').VersionInfo;"
            "Write-Output \"FileVersion=$($info.FileVersion)\";"
            "Write-Output \"ProductName=$($info.ProductName)\";"
            "Write-Output \"CompanyName=$($info.CompanyName)\";"
            "Write-Output \"FileDescription=$($info.FileDescription)\""
        ).format(exe_path=exe_path.replace("'", "''"))

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return {}

            props = {}
            for line in result.stdout.strip().splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
            return props
        except Exception:
            return {}

    def main(self) -> None:
        pkg_path = self.env["pkg_path"]
        if not os.path.isfile(pkg_path):
            raise ProcessorError(f"Installer not found at: {pkg_path}")

        installer_type = self.env["installer_type"].lower()

        # Start building pkgsinfo
        pkgsinfo = {}

        # Extract metadata based on installer type
        extracted_props = {}
        if installer_type == "msi":
            extracted_props = self._extract_msi_properties(pkg_path)
        elif installer_type == "exe":
            extracted_props = self._extract_exe_version(pkg_path)

        # Name
        name = self.env.get("cimian_info_name")
        if not name:
            if installer_type == "msi" and extracted_props.get("ProductName"):
                name = extracted_props["ProductName"]
            elif installer_type == "exe" and extracted_props.get("ProductName"):
                name = extracted_props["ProductName"]
            else:
                basename = os.path.basename(pkg_path)
                name, _ = os.path.splitext(basename)
                for suffix in ("-x64", "-x86", "-arm64", "-amd64"):
                    if suffix in name:
                        name = name[: name.index(suffix)]
                        break
        pkgsinfo["name"] = name
        pkgsinfo["display_name"] = self.env.get("cimian_info_display_name", name)

        # Version
        version = self.env.get("cimian_info_version")
        if not version:
            if installer_type == "msi" and extracted_props.get("ProductVersion"):
                version = extracted_props["ProductVersion"]
            elif installer_type == "exe" and extracted_props.get("FileVersion"):
                version = extracted_props["FileVersion"]
            else:
                version = self.env.get("version", "0.0.0")
        pkgsinfo["version"] = str(version)

        # Developer
        developer = self.env.get("cimian_info_developer")
        if not developer:
            developer = extracted_props.get(
                "Manufacturer", extracted_props.get("CompanyName", "")
            )
        if developer:
            pkgsinfo["developer"] = developer

        # Category and description
        if self.env.get("cimian_info_category"):
            pkgsinfo["category"] = self.env["cimian_info_category"]
        if self.env.get("cimian_info_description"):
            pkgsinfo["description"] = self.env["cimian_info_description"]

        # MSI-specific installs array
        if installer_type == "msi":
            installs_item = {"type": "msi"}
            if extracted_props.get("ProductCode"):
                installs_item["product_code"] = extracted_props["ProductCode"]
            if extracted_props.get("UpgradeCode"):
                installs_item["upgrade_code"] = extracted_props["UpgradeCode"]
            if len(installs_item) > 1:
                pkgsinfo["installs"] = [installs_item]

        # Set outputs
        self.env["cimian_pkginfo"] = pkgsinfo
        self.env["version"] = pkgsinfo["version"]
        self.env["cimian_info_creator_summary_result"] = {
            "summary_text": "The following Cimian pkgsinfo was created:",
            "report_fields": ["name", "version", "developer"],
            "data": {
                "name": pkgsinfo["name"],
                "version": pkgsinfo["version"],
                "developer": pkgsinfo.get("developer", ""),
            },
        }

        self.output(f"Created pkgsinfo for: {pkgsinfo['name']} {pkgsinfo['version']}")


if __name__ == "__main__":
    PROCESSOR = CimianInfoCreator()
    PROCESSOR.execute_shell()
