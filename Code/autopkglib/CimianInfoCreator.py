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
import xml.etree.ElementTree as ET
import zipfile

from autopkglib import Processor, ProcessorError

__all__ = ["CimianInfoCreator"]


class CimianInfoCreator(Processor):
    """Extracts metadata from a Windows installer and creates a Cimian pkgsinfo
    dictionary in the autopkg environment. Does not copy files or modify the repo.

    This processor populates the 'cimian_pkginfo' environment variable with a
    complete pkgsinfo dict, which can then be consumed by CimianImporter.

    For MSI files on Windows, it extracts ProductCode, UpgradeCode, ProductName,
    and ProductVersion via PowerShell. For EXE files, it reads FileVersionInfo.
    For MSIX/APPX files, it parses AppxManifest.xml (or AppxBundleManifest.xml
    for bundles) from the ZIP container to extract Identity.Name, Version,
    ProcessorArchitecture, Properties.DisplayName, PublisherDisplayName, and
    Description. MSIX extraction also auto-generates the installs and uninstaller
    blocks with identity_name that managedsoftwareupdate uses for detection
    (Get-AppxPackage) and uninstall (Remove-AppxProvisionedPackage).
    """

    description = __doc__
    lifecycle = {"introduced": "3.0.0"}
    input_variables = {
        "pkg_path": {
            "required": True,
            "description": "Path to an installer file (MSI, EXE, NUPKG, or MSIX).",
        },
        "installer_type": {
            "required": True,
            "description": "Type of installer: msi, exe, nupkg, or msix.",
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
        "cimian_info_icon_name": {
            "required": False,
            "description": "Icon filename for the package (e.g., 'Chrome.png').",
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
        """Extract product properties from an MSI file.

        Uses Python's built-in msilib (CPython on Windows) to read the
        Property table directly, avoiding COM subprocess issues on hosted
        CI agents.  Falls back to a PowerShell subprocess if msilib is
        unavailable.
        """
        if platform.system() != "Windows":
            self.output(
                "MSI property extraction requires Windows. Skipping.",
                verbose_level=1,
            )
            return {}

        # Preferred: use msilib (built into CPython on Windows)
        try:
            import msilib

            db = msilib.OpenDatabase(msi_path, msilib.MSIDBOPEN_READONLY)
            view = db.OpenView(
                "SELECT `Property`, `Value` FROM `Property` "
                "WHERE `Property` = 'ProductCode' "
                "OR `Property` = 'UpgradeCode' "
                "OR `Property` = 'ProductName' "
                "OR `Property` = 'ProductVersion' "
                "OR `Property` = 'Manufacturer'"
            )
            view.Execute(None)
            props = {}
            while True:
                try:
                    row = view.Fetch()
                except msilib.MSIError:
                    break
                if row is None:
                    break
                props[row.GetString(1)] = row.GetString(2)
            view.Close()
            return props
        except ImportError:
            self.output(
                "msilib unavailable, falling back to PowerShell",
                verbose_level=2,
            )
        except Exception as err:
            self.output(
                f"msilib extraction failed ({err}), falling back to PowerShell",
                verbose_level=1,
            )

        # Fallback: PowerShell COM object
        ps_script = (
            "$msiPath = '{msi_path}';"
            "$installer = New-Object -ComObject WindowsInstaller.Installer;"
            "$db = $installer.OpenDatabase($msiPath, 0);"
            "$view = $db.OpenView(\"SELECT Property, Value FROM Property "
            "WHERE Property = 'ProductCode' "
            "OR Property = 'UpgradeCode' "
            "OR Property = 'ProductName' "
            "OR Property = 'ProductVersion' "
            "OR Property = 'Manufacturer'\");"
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
                self.output(
                    f"MSI extraction warning: {result.stderr}", verbose_level=1
                )
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

    def _extract_msix_properties(self, msix_path):
        """Extract metadata from an MSIX/APPX/MSIXBUNDLE/APPXBUNDLE package.

        These are ZIP archives containing an AppxManifest.xml (single-arch)
        or AppxMetadata/AppxBundleManifest.xml (bundle). We parse Identity
        and Properties elements to extract name, version, architecture,
        display name, publisher, and description.
        """
        props = {}
        try:
            with zipfile.ZipFile(msix_path, "r") as zf:
                names = zf.namelist()

                # Bundles: check for bundle manifest first
                bundle_manifest = None
                for n in names:
                    if n.lower() == "appxmetadata/appxbundlemanifest.xml":
                        bundle_manifest = n
                        break

                if bundle_manifest:
                    with zf.open(bundle_manifest) as f:
                        tree = ET.parse(f)
                    root = tree.getroot()
                    ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
                    identity = root.find(f"{ns}Identity")
                    if identity is not None:
                        props["IdentityName"] = identity.get("Name", "")
                        props["Version"] = identity.get("Version", "")
                        props["Architecture"] = identity.get(
                            "ProcessorArchitecture", ""
                        )
                    return props

                # Single-arch: AppxManifest.xml at root
                app_manifest = None
                for n in names:
                    if n.lower() == "appxmanifest.xml":
                        app_manifest = n
                        break

                if not app_manifest:
                    return props

                with zf.open(app_manifest) as f:
                    tree = ET.parse(f)
                root = tree.getroot()
                ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""

                identity = root.find(f"{ns}Identity")
                if identity is not None:
                    props["IdentityName"] = identity.get("Name", "")
                    props["Version"] = identity.get("Version", "")
                    props["Architecture"] = identity.get(
                        "ProcessorArchitecture", ""
                    )

                properties = root.find(f"{ns}Properties")
                if properties is not None:
                    display_name_el = properties.find(f"{ns}DisplayName")
                    if display_name_el is not None and display_name_el.text:
                        props["DisplayName"] = display_name_el.text
                    pub_el = properties.find(f"{ns}PublisherDisplayName")
                    if pub_el is not None and pub_el.text:
                        props["PublisherDisplayName"] = pub_el.text
                    desc_el = properties.find(f"{ns}Description")
                    if desc_el is not None and desc_el.text:
                        props["Description"] = desc_el.text

        except Exception as err:
            self.output(f"MSIX extraction failed: {err}", verbose_level=1)

        return props

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
        elif installer_type in ("msix", "appx"):
            extracted_props = self._extract_msix_properties(pkg_path)

        # Name
        name = self.env.get("cimian_info_name")
        if not name:
            if installer_type == "msi" and extracted_props.get("ProductName"):
                name = extracted_props["ProductName"]
            elif installer_type == "exe" and extracted_props.get("ProductName"):
                name = extracted_props["ProductName"]
            elif installer_type in ("msix", "appx") and extracted_props.get(
                "DisplayName"
            ):
                name = extracted_props["DisplayName"]
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
            elif installer_type in ("msix", "appx") and extracted_props.get("Version"):
                version = extracted_props["Version"]
            else:
                version = self.env.get("version", "0.0.0")
        pkgsinfo["version"] = str(version)

        # Developer
        developer = self.env.get("cimian_info_developer")
        if not developer:
            if installer_type in ("msix", "appx"):
                developer = extracted_props.get("PublisherDisplayName", "")
            else:
                developer = extracted_props.get(
                    "Manufacturer", extracted_props.get("CompanyName", "")
                )
        if developer:
            pkgsinfo["developer"] = developer

        # Category and description
        if self.env.get("cimian_info_description"):
            pkgsinfo["description"] = self.env["cimian_info_description"]
        elif installer_type in ("msix", "appx") and extracted_props.get("Description"):
            pkgsinfo["description"] = extracted_props["Description"]
        if self.env.get("cimian_info_category"):
            pkgsinfo["category"] = self.env["cimian_info_category"]
        if self.env.get("cimian_info_icon_name"):
            pkgsinfo["icon_name"] = self.env["cimian_info_icon_name"]

        # Installer-type-specific installs array
        if installer_type == "msi":
            installs_item = {"type": "msi"}
            if extracted_props.get("ProductCode"):
                installs_item["product_code"] = extracted_props["ProductCode"]
            if extracted_props.get("UpgradeCode"):
                installs_item["upgrade_code"] = extracted_props["UpgradeCode"]
            if len(installs_item) > 1:
                pkgsinfo["installs"] = [installs_item]

        elif installer_type in ("msix", "appx"):
            identity_name = extracted_props.get("IdentityName", "")
            if identity_name:
                pkgsinfo["installs"] = [
                    {
                        "type": "msix",
                        "identity_name": identity_name,
                        "version": pkgsinfo["version"],
                    }
                ]
                pkgsinfo["uninstaller"] = [
                    {"type": "msix", "identity_name": identity_name}
                ]

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
