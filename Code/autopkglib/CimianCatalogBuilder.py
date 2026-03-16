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
"""See docstring for CimianCatalogBuilder class"""

import os
import shutil
import subprocess

from autopkglib import Processor, ProcessorError

__all__ = ["CimianCatalogBuilder"]


class CimianCatalogBuilder(Processor):
    """Rebuilds Cimian catalogs by running the makecatalogs tool.

    This processor shells out to Cimian's `makecatalogs` executable to regenerate
    catalog YAML files (All.yaml, Production.yaml, etc.) from the pkgsinfo directory.
    It should typically be run as the final step in a Cimian recipe, after CimianImporter.
    """

    description = __doc__
    lifecycle = {"introduced": "3.0.0"}
    input_variables = {
        "CIMIAN_REPO": {
            "required": True,
            "description": (
                "Path to the root of a Cimian deployment repository. "
                "The deployment/ subdirectory must contain pkgsinfo/ and catalogs/."
            ),
        },
        "makecatalogs_path": {
            "required": False,
            "description": (
                "Path to the Cimian makecatalogs executable. "
                "If not provided, searches PATH for 'makecatalogs' or 'makecatalogs.exe'."
            ),
        },
        "skip_payload_check": {
            "required": False,
            "description": (
                "If True, passes --skip_payload_check to makecatalogs "
                "to skip verifying that installer files exist on disk."
            ),
            "default": True,
        },
        "cimian_repo_changed": {
            "required": False,
            "description": (
                "Set by CimianImporter. If False, catalog rebuild is skipped."
            ),
        },
        "force_catalog_rebuild": {
            "required": False,
            "description": "If True, rebuild catalogs even if cimian_repo_changed is False.",
            "default": False,
        },
    }
    output_variables = {
        "cimian_catalogs_rebuilt": {
            "description": "True if catalogs were successfully rebuilt.",
        },
        "cimian_catalog_builder_summary_result": {
            "description": "Description of interesting results.",
        },
    }

    def _find_makecatalogs(self):
        """Locate the makecatalogs executable."""
        # Explicit path
        if self.env.get("makecatalogs_path"):
            path = self.env["makecatalogs_path"]
            if os.path.isfile(path):
                return path
            raise ProcessorError(f"makecatalogs not found at specified path: {path}")

        # Search PATH
        exe = shutil.which("makecatalogs") or shutil.which("makecatalogs.exe")
        if exe:
            return exe

        # Common install locations on Windows
        common_paths = [
            os.path.join(
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                "Cimian",
                "makecatalogs.exe",
            ),
            os.path.join(
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                "CimianTools",
                "makecatalogs.exe",
            ),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                return path

        raise ProcessorError(
            "makecatalogs executable not found. "
            "Set makecatalogs_path or ensure it is on PATH."
        )

    def main(self) -> None:
        # Skip if nothing changed (unless forced)
        if not self.env.get("cimian_repo_changed") and not self.env.get(
            "force_catalog_rebuild"
        ):
            self.output("No repo changes detected. Skipping catalog rebuild.")
            self.env["cimian_catalogs_rebuilt"] = False
            return

        cimian_repo = self.env["CIMIAN_REPO"]
        deployment_dir = os.path.join(cimian_repo, "deployment")
        if not os.path.isdir(deployment_dir):
            raise ProcessorError(
                f"Cimian deployment directory not found: {deployment_dir}"
            )

        makecatalogs = self._find_makecatalogs()
        self.output(f"Using makecatalogs: {makecatalogs}")

        args = [makecatalogs, "--repo_path", deployment_dir, "--silent"]
        if self.env.get("skip_payload_check"):
            args.append("--skip_payload_check")

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise ProcessorError("makecatalogs timed out after 120 seconds.")
        except OSError as err:
            raise ProcessorError(
                f"makecatalogs execution failed: {err.strerror}"
            )

        if stdout:
            for line in stdout.strip().splitlines():
                self.output(line, verbose_level=2)
        if stderr:
            for line in stderr.strip().splitlines():
                self.output(f"WARNING: {line}", verbose_level=1)

        if proc.returncode != 0:
            raise ProcessorError(
                f"makecatalogs failed with return code {proc.returncode}: {stderr}"
            )

        self.output("Cimian catalogs rebuilt successfully.")
        self.env["cimian_catalogs_rebuilt"] = True
        self.env["cimian_catalog_builder_summary_result"] = {
            "summary_text": "Cimian catalogs were rebuilt:",
            "report_fields": ["repo_path"],
            "data": {
                "repo_path": deployment_dir,
            },
        }


if __name__ == "__main__":
    PROCESSOR = CimianCatalogBuilder()
    PROCESSOR.execute_shell()
