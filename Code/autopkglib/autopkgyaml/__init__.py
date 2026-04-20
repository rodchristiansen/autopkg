#!/usr/local/autopkg/python
#
# Copyright 2021 Brandon Friess
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
"""Helper to deal with yaml serialization for autopkg and Munki pkginfo.

Provides utilities for reading, writing, and detecting Munki data in both
plist and yaml formats. Key ordering, type normalization, and block scalar
styles are designed to match the Munki yaml fork (yamlutils.swift).
"""

import base64
import os
import plistlib
import re
from collections import OrderedDict
from datetime import datetime

import yaml


# Loader: strip the float implicit resolver so version-shaped scalars
# (e.g. `version: 10.10`, `MinimumVersion: 2.3`) load as strings,
# preserving trailing zeros that PyYAML would otherwise discard.
#
# Same approach as autopkg/autopkg#1023 (@homebysix). Defined here under
# the same name so the two PRs converge on one class: whichever lands
# first owns the definition, the other's diff becomes a no-op on rebase.
class AutoPkgYAMLLoader(yaml.SafeLoader):
    pass


AutoPkgYAMLLoader.yaml_implicit_resolvers = {
    k: [(tag, regexp) for tag, regexp in v if tag != "tag:yaml.org,2002:float"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


# Keys whose values must always be strings even if yaml parses them as
# numeric (matches Munki's stringKeys set in yamlutils.swift).
STRING_KEYS = frozenset(
    {
        "version",
        "minimum_os_version",
        "maximum_os_version",
        "minimum_munki_version",
        "minimum_update_version",
        "installer_item_version",
        "installed_version",
        "product_version",
        "CFBundleShortVersionString",
        "CFBundleVersion",
        "minosversion",
    }
)

# Script keys that should use yaml literal block scalar style (|).
SCRIPT_KEYS = frozenset(
    {
        "preinstall_script",
        "postinstall_script",
        "installcheck_script",
        "uninstallcheck_script",
        "postuninstall_script",
        "uninstall_script",
        "preuninstall_script",
        "version_script",
        "embedded_script",
    }
)

# Prose keys that should use yaml folded block scalar style (>).
PROSE_KEYS = frozenset({"description", "notes"})

_PKGINFO_HEAD_KEYS = ["name", "display_name", "version"]

_RECEIPT_HEAD_KEYS = [
    "packageid",
    "name",
    "filename",
    "installed_size",
    "version",
    "optional",
]

_INSTALLS_HEAD_KEYS = [
    "path",
    "type",
    "CFBundleIdentifier",
    "CFBundleName",
    "CFBundleShortVersionString",
    "CFBundleVersion",
    "md5checksum",
    "minosversion",
]


def autopkg_str_representer(dumper, data):
    """Makes every multiline string a block literal"""
    if len(data.splitlines()) > 1:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class AutoPkgDumper(yaml.SafeDumper):
    """Shared dumper base: force block-style (non-indentless) output."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _sorted_keys(d, head_keys):
    head = [k for k in head_keys if k in d]
    rest = sorted(k for k in d if k not in head_keys and k != "_metadata")
    tail = ["_metadata"] if "_metadata" in d else []
    return head + rest + tail


def _detect_subdict_type(d):
    if "packageid" in d:
        return _RECEIPT_HEAD_KEYS
    if "path" in d and "type" in d:
        return _INSTALLS_HEAD_KEYS
    return _PKGINFO_HEAD_KEYS


def _is_numeric_scalar(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean_float_to_str(value):
    """Matches Munki's String(format: '%.10g') behaviour."""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _normalize_yaml_types(data, parent_key=None):
    """Defence-in-depth: AutoPkgYAMLLoader keeps float-shaped scalars as
    strings; this pass catches integer-shaped scalars for STRING_KEYS
    (e.g. ``version: 10`` → ``"10"``) that the loader still coerces."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in STRING_KEYS and _is_numeric_scalar(value):
                data[key] = _clean_float_to_str(value)
            elif isinstance(value, dict):
                _normalize_yaml_types(value, key)
            elif isinstance(value, list):
                _normalize_yaml_types(value, key)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if parent_key in STRING_KEYS and _is_numeric_scalar(item):
                data[i] = _clean_float_to_str(item)
            elif isinstance(item, (dict, list)):
                _normalize_yaml_types(item, parent_key)
    return data


class _FoldedStr(str):
    """Marker for strings that should use yaml folded block style (>)."""

    pass


class _QuotedStr(str):
    """Marker for strings that must be single-quoted (version numbers etc)."""

    pass


def _folded_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")


def _quoted_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")


_NEEDS_QUOTING_RE = re.compile(
    r"""^(?:
        [-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?
        |true|false|yes|no|on|off
        |null|~
        |\d+:\d+(?::\d+)?
    )$""",
    re.VERBOSE | re.IGNORECASE,
)


def _looks_like_script(value):
    patterns = ["#!", "\nif ", "\nfor ", "\necho ", "\nprint(", "\n  ", "\n\t"]
    return any(p in value for p in patterns)


def _prepare_value(key, value):
    if value is None:
        return None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        if key in STRING_KEYS and _NEEDS_QUOTING_RE.match(value):
            return _QuotedStr(value)
        if "\n" in value or value.endswith("\n"):
            # Multiline: let autopkg_str_representer emit literal '|'.
            # Wrap prose in _FoldedStr for '>' style; scripts and generic
            # multiline text fall through to the shared str representer.
            if key in PROSE_KEYS and key not in SCRIPT_KEYS:
                if not _looks_like_script(value):
                    return _FoldedStr(value)
            return value
        if _NEEDS_QUOTING_RE.match(value):
            return _QuotedStr(value)
        return value
    return value


def _prepare_dict(d):
    head_keys = _detect_subdict_type(d)
    ordered = OrderedDict()
    for key in _sorted_keys(d, head_keys):
        value = d[key]
        # Filter None (matches Munki's behaviour). Preserve empty strings
        # as explicitly-quoted empties — matches yamlutils.swift emitting
        # `key: ''` so empty values survive round-trip.
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            ordered[key] = _QuotedStr("")
            continue
        if isinstance(value, dict):
            value = _prepare_dict(value)
        elif isinstance(value, list):
            value = _prepare_list(key, value)
        else:
            value = _prepare_value(key, value)
            if value is None:
                continue
        ordered[key] = value
    return ordered


def _prepare_list(parent_key, lst):
    result = []
    for item in lst:
        if item is None:
            continue
        if isinstance(item, dict):
            result.append(_prepare_dict(item))
        elif isinstance(item, list):
            result.append(_prepare_list(parent_key, item))
        else:
            prepared = _prepare_value(parent_key, item)
            if prepared is not None:
                result.append(prepared)
    return result


class MunkiPkginfoDumper(AutoPkgDumper):
    """Dumper for Munki pkginfo output: key ordering + block scalar styles."""

    pass


# Reuse autopkg_str_representer so multiline scripts emit literal '|'
# without needing a parallel _literal_representer.
MunkiPkginfoDumper.add_representer(str, autopkg_str_representer)
MunkiPkginfoDumper.add_representer(_FoldedStr, _folded_representer)
MunkiPkginfoDumper.add_representer(_QuotedStr, _quoted_representer)
MunkiPkginfoDumper.add_representer(
    OrderedDict,
    lambda dumper, data: dumper.represent_mapping(
        "tag:yaml.org,2002:map", data.items()
    ),
)


def dump_pkginfo_yaml(pkginfo, f):
    """Serialize a Munki pkginfo dict to yaml and write to file handle *f*."""
    prepared = _prepare_dict(pkginfo)
    yaml.dump(
        prepared,
        f,
        Dumper=MunkiPkginfoDumper,
        default_flow_style=False,
        allow_unicode=True,
        width=10000,
        indent=2,
        sort_keys=False,
    )


def dumps_pkginfo_yaml(pkginfo):
    prepared = _prepare_dict(pkginfo)
    return yaml.dump(
        prepared,
        Dumper=MunkiPkginfoDumper,
        default_flow_style=False,
        allow_unicode=True,
        width=10000,
        indent=2,
        sort_keys=False,
    )


def load_pkginfo_yaml(f):
    data = yaml.load(f, Loader=AutoPkgYAMLLoader)
    if isinstance(data, dict):
        _normalize_yaml_types(data)
    return data


def loads_pkginfo_yaml(data):
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    result = yaml.load(data, Loader=AutoPkgYAMLLoader)
    if isinstance(result, dict):
        _normalize_yaml_types(result)
    return result


def is_yaml_path(path):
    _, ext = os.path.splitext(path)
    return ext.lower() in (".yaml", ".yml")


def is_plist_path(path):
    _, ext = os.path.splitext(path)
    return ext.lower() == ".plist"


def detect_munki_format(file_path):
    """Detect whether a Munki data file is yaml or plist.

    Order: extension → content detection → default plist.
    """
    if is_yaml_path(file_path):
        return "yaml"
    if is_plist_path(file_path):
        return "plist"
    try:
        with open(file_path, "rb") as f:
            head = f.read(512)
    except OSError:
        return "plist"
    head_str = head.decode("utf-8", errors="replace").lstrip()
    if head_str.startswith("<?xml") or head_str.startswith("<plist"):
        return "plist"
    if head_str.startswith("---"):
        return "yaml"
    lines = head_str.splitlines()[:10]
    yaml_score = sum(1 for ln in lines if re.match(r"^\w[\w\s]*:", ln))
    xml_score = sum(1 for ln in lines if re.match(r"^\s*<", ln))
    if yaml_score > xml_score:
        return "yaml"
    return "plist"


def load_munki_file(file_path):
    """Load a Munki data file (pkginfo, catalog, manifest), auto-detecting format."""
    fmt = detect_munki_format(file_path)
    if fmt == "yaml":
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.load(f, Loader=AutoPkgYAMLLoader)
            if isinstance(data, dict):
                _normalize_yaml_types(data)
            return data
        except Exception:
            with open(file_path, "rb") as f:
                return plistlib.load(f)
    try:
        with open(file_path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.load(f, Loader=AutoPkgYAMLLoader)
        if isinstance(data, dict):
            _normalize_yaml_types(data)
        return data


def save_munki_file(data, file_path):
    if is_yaml_path(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            dump_pkginfo_yaml(data, f)
    else:
        with open(file_path, "wb") as f:
            plistlib.dump(data, f)


def parse_munki_data(data_bytes):
    """Parse bytes that could be either plist or yaml (e.g. makepkginfo stdout)."""
    try:
        return plistlib.loads(data_bytes)
    except Exception:
        pass
    text = data_bytes.decode("utf-8") if isinstance(data_bytes, bytes) else data_bytes
    result = yaml.load(text, Loader=AutoPkgYAMLLoader)
    if isinstance(result, dict):
        _normalize_yaml_types(result)
    return result
