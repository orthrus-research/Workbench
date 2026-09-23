"""Optional owner extensions admitted through explicit profile distributions.

An extension cannot claim another profile's identity. Broken, duplicated or
disabled contributions are reported and omitted without affecting other owners.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
import inspect
import json
from pathlib import Path
import re
from types import ModuleType
from typing import Any

from .profiles import profile_status


def _distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class ProfileExtension:
    profile_id: str
    state: str
    value: Any = None
    reason: str = ""


class ProfileExtensionError(ValueError):
    """An operation cannot use its selected native profile contribution."""


_CODE_IDENTITIES: dict[ModuleType, tuple[Path, str, int, str]] = {}


def _code_identity(value: Any) -> tuple[ModuleType, Path, str, int, str]:
    module = value if isinstance(value, ModuleType) else inspect.getmodule(value)
    source = getattr(module, "__file__", None)
    if module is None or not isinstance(source, str):
        raise ProfileExtensionError(
            "profile extension must have packaged Python source"
        )
    path = Path(source)
    if path.is_symlink() or not path.is_file() or path.suffix != ".py":
        raise ProfileExtensionError(
            "profile extension source is not a regular Python file"
        )
    size = path.stat().st_size
    if size > 2 * 1024 * 1024:
        raise ProfileExtensionError("profile extension source exceeds its byte bound")
    raw = path.read_bytes()
    if len(raw) != size:
        raise ProfileExtensionError("profile extension source changed while read")
    # Bind sibling policy/helpers and packaged resources too. A version string
    # and Python alone miss edited runtime observers loaded as package data.
    sources = []
    for source_path in sorted(path.parent.rglob("*")):
        relative = source_path.relative_to(path.parent)
        if "__pycache__" in relative.parts or source_path.suffix in {".pyc", ".pyo"}:
            continue
        if source_path.is_dir() and not source_path.is_symlink():
            continue
        if source_path.is_symlink() or not source_path.is_file():
            raise ProfileExtensionError(
                "profile package source contains a symbolic link"
            )
        if source_path.stat().st_size > 2 * 1024 * 1024 or len(sources) >= 256:
            raise ProfileExtensionError("profile package source exceeds its bound")
        sources.append(
            (
                source_path.relative_to(path.parent).as_posix(),
                sha256(source_path.read_bytes()).hexdigest(),
            )
        )
    package_digest = sha256(
        json.dumps(sources, separators=(",", ":")).encode()
    ).hexdigest()
    identity = (path.resolve(), sha256(raw).hexdigest(), size, package_digest)
    previous = _CODE_IDENTITIES.setdefault(module, identity)
    if identity != previous:
        raise ProfileExtensionError(
            "loaded profile extension source changed; restart the host"
        )
    return module, *identity


def require_profile_extension(group: str, profile_id: str) -> Any:
    """Resolve one API-1 contribution; never guess another profile or path.

    Domain owners additionally validate their own callable contracts. Admission
    is repeated on every request, including for already imported providers.
    """
    rows = [row for row in profile_extensions(group) if row.profile_id == profile_id]
    if len(rows) != 1 or rows[0].state != "available":
        reason = (
            rows[0].reason if len(rows) == 1 else "missing or duplicate contribution"
        )
        raise ProfileExtensionError(f"{profile_id} {group} unavailable: {reason}")
    value = rows[0].value
    if (
        type(getattr(value, "PROFILE_API_VERSION", None)) is not int
        or value.PROFILE_API_VERSION != 1
    ):
        raise ProfileExtensionError(
            f"{profile_id} {group} has an incompatible profile API"
        )
    _code_identity(value)
    return value


def profile_extension_identity(group: str, profile_id: str) -> dict[str, Any]:
    """Bind retained work to the actual native owner bytes, not just a version."""
    value = require_profile_extension(group, profile_id)
    module, _path, digest, size, package_digest = _code_identity(value)
    statuses = [
        row
        for row in profile_status()
        if row.id == profile_id and row.state == "available"
    ]
    if len(statuses) != 1:
        raise ProfileExtensionError(
            "profile admission changed during identity observation"
        )
    distribution = metadata.distribution(statuses[0].distribution)
    return {
        "profile_id": profile_id,
        "group": group,
        "module": module.__name__,
        "distribution": distribution.metadata["Name"],
        "version": distribution.version,
        "api_version": 1,
        "sha256": digest,
        "size": size,
        "package_source_sha256": package_digest,
    }


def installed_profile_code_identity(profile_id: str) -> dict[str, Any] | None:
    """Installed bytes for service identity, independent of profile enable state.

    This is diagnostic identity only, never admission or execution authority.
    Missing, duplicate or broken optional packages do not disable host control.
    The registration factory is not called.
    """
    entries = [
        entry
        for entry in metadata.entry_points(group="workbench.profiles")
        if entry.name == profile_id
    ]
    if len(entries) != 1 or entries[0].dist is None:
        return None
    entry = entries[0]
    try:
        module, _path, digest, size, package_digest = _code_identity(entry.load())
    except (Exception, SystemExit):
        return None
    return {
        "profile_id": profile_id,
        "module": module.__name__,
        "distribution": entry.dist.metadata["Name"],
        "version": entry.dist.version,
        "sha256": digest,
        "size": size,
        "package_source_sha256": package_digest,
    }


def profile_extensions(group: str) -> tuple[ProfileExtension, ...]:
    """Load extension objects only from admitted, enabled profile owners.

    Objects are not invoked here: each consumer validates its narrow contract.
    Extension failures never imply approval for another owner or fallback.
    """
    admitted = {
        status.id: status for status in profile_status() if status.state == "available"
    }
    entries = tuple(metadata.entry_points(group=group))
    counts = Counter(entry.name for entry in entries)
    result = []
    for entry in sorted(entries, key=lambda item: item.name):
        try:
            status = admitted.get(entry.name)
            if status is None:
                raise ValueError("owning profile is not admitted and enabled")
            if counts[entry.name] != 1:
                raise ValueError("extension identity is duplicated")
            if entry.dist is None or _distribution_name(
                entry.dist.metadata["Name"]
            ) != _distribution_name(status.distribution):
                raise ValueError("extension distribution does not own its profile")
            result.append(ProfileExtension(entry.name, "available", entry.load()))
        except (Exception, SystemExit) as exc:
            result.append(ProfileExtension(entry.name, "unavailable", reason=str(exc)))
    return tuple(result)


def event_classifiers() -> tuple[Any, ...]:
    """Return the explicit classifier callbacks for currently admitted owners."""
    return tuple(
        extension.value
        for extension in profile_extensions("workbench.event_classifiers")
        if extension.state == "available" and callable(extension.value)
    )
