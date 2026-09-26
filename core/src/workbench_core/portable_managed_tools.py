"""Portable identity of Core's fixed Prism and Packwiz tool policy.

This lock identifies exact eligible bytes for one host. It is not an installed
receipt or a request to download, build, or trust executable bytes.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from . import tooling_provision


FORMAT = "workbench-managed-tool-policy-lock-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class PortableToolLockError(ValueError):
    """The Core managed-tool policy cannot be represented or matched exactly."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _host_key(host: Mapping[str, str], *, require_policy: bool = True) -> str:
    if type(host) is not dict or set(host) != {"os", "architecture"}:
        raise PortableToolLockError("managed tool host variant is invalid")
    key = f"{host['os']}-{host['architecture']}"
    if key not in {"linux-x64", "windows-x64"}:
        raise PortableToolLockError("managed tools have no portable policy for this host")
    if require_policy and key not in tooling_provision.ASSETS:
        raise PortableToolLockError("managed tools have no exact policy for this host")
    return key


def build_managed_tool_lock(host: Mapping[str, str]) -> dict[str, Any]:
    """Freeze the current Core acquisition policy without inspecting local paths."""

    key = _host_key(host)
    body: dict[str, Any] = {
        "format": FORMAT, "schema_version": 1,
        "host_variant": dict(host),
        "assets": {name: dict(tooling_provision.ASSETS[key][name])
                   for name in ("prism", "go", "packwiz")},
        "prism_version": tooling_provision.PRISM_VERSION,
        "go_version": tooling_provision.GO_VERSION,
        "packwiz_source": {
            "commit": tooling_provision.SOURCE_COMMIT,
            "tree": tooling_provision.SOURCE_TREE,
            "bundle_sha256": tooling_provision.SOURCE_SHA256,
            "bundle_size": tooling_provision.SOURCE_SIZE,
        },
    }
    return {**body, "lock_id": "workbench-managed-tool-policy:sha256:" + sha256(_canonical(body)).hexdigest()}


def _asset(value: object, *, name: str) -> None:
    required = ({"filename", "url", "sha256", "size", "executable", "archive"}
                if name in {"prism", "go"} else {"executable", "sha256", "size"})
    if type(value) is not dict or set(value) != required:
        raise PortableToolLockError(f"managed {name} asset has unsupported fields")
    if type(value["sha256"]) is not str or _SHA.fullmatch(value["sha256"]) is None:
        raise PortableToolLockError(f"managed {name} asset digest is invalid")
    if type(value["size"]) is not int or value["size"] <= 0:
        raise PortableToolLockError(f"managed {name} asset size is invalid")
    executable = value["executable"]
    if (type(executable) is not str or not executable
            or any(_SAFE.fullmatch(part) is None for part in executable.split("/"))):
        raise PortableToolLockError(f"managed {name} executable path is invalid")
    if name == "packwiz":
        return
    if (type(value["filename"]) is not str or _SAFE.fullmatch(value["filename"]) is None
            or type(value["archive"]) is not str
            or value["archive"] not in {"tar.gz", "zip"}):
        raise PortableToolLockError(f"managed {name} archive name or type is invalid")
    try:
        parsed = urlsplit(value["url"])
        safe_url = (parsed.scheme == "https" and bool(parsed.hostname)
                    and parsed.username is None and parsed.password is None
                    and parsed.path.endswith("/" + value["filename"])
                    and not parsed.query and not parsed.fragment)
    except (TypeError, ValueError):
        safe_url = False
    if not safe_url:
        raise PortableToolLockError(f"managed {name} archive URL is invalid")


def validate_managed_tool_lock(value: object) -> dict[str, Any]:
    """Validate the portable shape and seal without requiring local policy parity."""

    fields = {"format", "schema_version", "host_variant", "assets", "prism_version",
              "go_version", "packwiz_source", "lock_id"}
    if (type(value) is not dict or set(value) != fields or value["format"] != FORMAT
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise PortableToolLockError("managed tool lock has an unsupported version or fields")
    host = value["host_variant"]
    _host_key(host, require_policy=False)
    assets = value["assets"]
    if type(assets) is not dict or set(assets) != {"prism", "go", "packwiz"}:
        raise PortableToolLockError("managed tool asset set is incomplete")
    for name in ("prism", "go", "packwiz"):
        _asset(assets[name], name=name)
    if (type(value["prism_version"]) is not str
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value["prism_version"]) is None
            or type(value["go_version"]) is not str
            or re.fullmatch(r"go[0-9]+(?:\.[0-9]+){1,3}", value["go_version"]) is None):
        raise PortableToolLockError("managed tool version is invalid")
    source = value["packwiz_source"]
    if (type(source) is not dict
            or set(source) != {"commit", "tree", "bundle_sha256", "bundle_size"}
            or any(type(source[key]) is not str or _GIT.fullmatch(source[key]) is None
                   for key in ("commit", "tree"))
            or type(source["bundle_sha256"]) is not str
            or _SHA.fullmatch(source["bundle_sha256"]) is None
            or type(source["bundle_size"]) is not int or source["bundle_size"] <= 0):
        raise PortableToolLockError("managed Packwiz source identity is invalid")
    body = {key: item for key, item in value.items() if key != "lock_id"}
    if value["lock_id"] != "workbench-managed-tool-policy:sha256:" + sha256(_canonical(body)).hexdigest():
        raise PortableToolLockError("managed tool lock seal differs from its policy")
    return value


def matches_local_managed_tool_policy(value: Mapping[str, Any]) -> bool:
    lock = validate_managed_tool_lock(dict(value))
    try:
        return lock == build_managed_tool_lock(lock["host_variant"])
    except PortableToolLockError:
        return False


def inspect_locked_managed_tools(value: Mapping[str, Any], *, state_root: Path) -> dict[str, Any]:
    """Report retained bytes without acquiring or executing a tool."""

    lock = validate_managed_tool_lock(dict(value))
    if not matches_local_managed_tool_policy(lock):
        return {"state": "policy-drifted", "required": ["matching-managed-tool-policy"]}
    observed = tooling_provision.inspect_tools(state_root, key=_host_key(lock["host_variant"]))
    states = {name: observed["tools"][name]["state"] for name in ("prism", "packwiz")}
    return {
        "state": "ready" if all(state == "ready" for state in states.values()) else "bytes-unavailable",
        "tools": states,
        "required": [] if all(state == "ready" for state in states.values()) else ["managed-tool-bytes"],
    }


__all__ = [
    "PortableToolLockError", "build_managed_tool_lock", "inspect_locked_managed_tools",
    "matches_local_managed_tool_policy", "validate_managed_tool_lock",
]
