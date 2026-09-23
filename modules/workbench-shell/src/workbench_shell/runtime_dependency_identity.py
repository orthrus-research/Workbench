"""Explicit source or installed runtime identities for service registries."""

from __future__ import annotations

from pathlib import Path
import hashlib
from importlib import metadata
import platform
import sys
import tomllib
from typing import Any, Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from workbench_api.canonical import CANONICALIZER_ID, content_id

from workbench_core.pixi_lock import (
    PixiLockError,
    bind_pixi_inputs,
    build_environment_closure_from_bound_inputs,
    parse_pixi_manifest_bytes,
)


class RuntimeDependencyIdentityV2Error(ValueError):
    """The selected runtime cannot produce an exact dependency identity."""


def _seal(
    kind: str,
    body: Mapping[str, Any],
    identity_field: str,
) -> dict[str, Any]:
    value = dict(body)
    value[identity_field] = content_id(kind, value)
    return value


def _installed_runtime_contract(repository_root: Path, error_type: type[ValueError]) -> dict[str, Any]:
    """Bind actual installed metadata, never a checkout's Pixi declaration."""

    indexed = {}
    for distribution in metadata.distributions():
        name = canonicalize_name(distribution.metadata.get("Name", ""))
        if name:
            indexed.setdefault(name, []).append(distribution)

    def required_distribution(name):
        matches = indexed.get(name, ())
        if not matches:
            raise error_type(f"installed dependency is missing: {name}")
        if len(matches) != 1:
            raise error_type(f"installed distribution metadata is duplicated: {name}")
        return matches[0]

    required = {"workbench-api", "workbench-core", "workbench-crucible", "workbench-shell"}
    for name, relative in (("workbench-api", "api"), ("workbench-core", "core"), ("workbench-crucible", "modules/crucible"), ("workbench-shell", "modules/workbench-shell")):
        owner = required_distribution(name)
        if Path(owner.locate_file("workbench_resources")).resolve() != repository_root:
            raise error_type(f"installed resource root belongs to another environment: {name}")
        manifest = repository_root / relative / "pyproject.toml"
        try:
            project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
        except (OSError, ValueError, KeyError) as exc:
            raise error_type(f"installed owner manifest is unavailable: {name}") from exc
        if manifest.is_symlink() or project.get("name") != name or project.get("version") != owner.version:
            raise error_type(f"installed owner manifest differs from native metadata: {name}")
    pending = [(name, frozenset()) for name in sorted(required)]
    selected = {}
    requested_extras = {}
    while pending:
        name, extras = pending.pop()
        previous_extras = requested_extras.get(name)
        if previous_extras is not None and extras <= previous_extras:
            continue
        requested_extras[name] = extras | (previous_extras or frozenset())
        distribution = required_distribution(name)
        requires_python = distribution.metadata.get("Requires-Python")
        declarations = sorted(distribution.requires or ())
        try:
            Version(distribution.version)
            if requires_python and Version(platform.python_version()) not in SpecifierSet(requires_python):
                raise error_type(f"installed dependency requires another Python: {name}")
            for declaration in declarations:
                requirement = Requirement(declaration)
                if requirement.marker and not any(
                    requirement.marker.evaluate({"extra": extra})
                    for extra in requested_extras[name] | {""}
                ):
                    continue
                if requirement.url:
                    raise error_type(f"installed dependency uses an unsupported direct URL: {declaration}")
                dependency = canonicalize_name(requirement.name)
                dependency_distribution = required_distribution(dependency)
                if Version(dependency_distribution.version) not in requirement.specifier:
                    raise error_type(f"installed dependency is incompatible: {declaration}")
                pending.append((dependency, frozenset(requirement.extras)))
        except (InvalidRequirement, InvalidSpecifier, InvalidVersion) as exc:
            raise error_type(f"installed dependency declaration is invalid: {name}") from exc
        bound = {}
        for filename in ("METADATA", "RECORD", "direct_url.json"):
            text = distribution.read_text(filename)
            if text is not None:
                bound[filename] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not {"METADATA", "RECORD"} <= bound.keys():
            raise error_type(f"installed dependency lacks native metadata custody: {name}")
        selected[name] = {"name": name, "version": distribution.version, "requires_python": requires_python,
                          "dependencies": declarations, "requested_extras": sorted(requested_extras[name]),
                          "metadata_sha256": bound}
    return {"execution_scope": "installed-native-runtime", "materialization_evidence": "observed-installed-distribution-metadata",
            "source_pixi_qualification": False, "release_qualified": False,
            "python": {"implementation": platform.python_implementation(), "version": platform.python_version()},
            "distributions": [selected[name] for name in sorted(selected)]}


def build_runtime_dependency_lock_manifest_v2(
    repository_root: Path,
    *,
    format_name: str,
    error_type: type[ValueError] = RuntimeDependencyIdentityV2Error,
) -> dict[str, Any]:
    """Build a truthful identity for the explicitly selected runtime layout.

    The projection selects every target in Pixi's ``default`` environment and
    only the runtime declarations from Core's native ``[project]``. It is not evidence for
    the environment of a packaged or native invocation. An installed resource
    root instead binds the actual installed native dependency metadata closure.
    """

    root = repository_root.resolve()
    if root.name == "workbench_resources" and root.is_relative_to(Path(sys.prefix).resolve()):
        return _seal("dependency-lock", {"canonicalizer": CANONICALIZER_ID, "format": format_name,
            "kind": "dependency-lock", "schema_version": 2,
            "installed_runtime_contract": _installed_runtime_contract(root, error_type)}, "id")

    def read_toml(path: Path, label: str) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise error_type(f"{label} is not an ordinary file: {path}")
        try:
            value = tomllib.loads(
                path.read_text(encoding="utf-8", errors="strict")
            )
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise error_type(f"{label} cannot be read: {path}") from exc
        if type(value) is not dict:
            raise error_type(f"{label} root must be a table")
        return value

    pixi_manifest_path = repository_root / "pixi.toml"
    pixi_lock_path = repository_root / "pixi.lock"
    pyproject = read_toml(repository_root / "core/pyproject.toml", "native Core manifest")
    try:
        pixi_inputs = bind_pixi_inputs(pixi_manifest_path, pixi_lock_path)
        pixi_manifest = parse_pixi_manifest_bytes(pixi_inputs.manifest_bytes)
    except PixiLockError as exc:
        raise error_type(f"source runtime inputs are invalid: {exc}") from exc

    workspace = pixi_manifest.get("workspace")
    platform_rows = (
        workspace.get("platforms") if type(workspace) is dict else None
    )
    if type(platform_rows) is not list or not platform_rows:
        raise error_type("Pixi workspace platforms must be a non-empty array")
    platform_names: list[str] = []
    for row in platform_rows:
        name = row.get("name") if type(row) is dict else None
        subdir = row.get("platform") if type(row) is dict else None
        if not all(type(item) is str and item for item in (name, subdir)):
            raise error_type("Pixi workspace platform identity is incomplete")
        platform_names.append(name)
    if len(set(platform_names)) != len(platform_names):
        raise error_type("Pixi workspace platform names must be unique")

    project = pyproject.get("project")
    requires_python = (
        project.get("requires-python") if type(project) is dict else None
    )
    dependencies = project.get("dependencies") if type(project) is dict else None
    if type(requires_python) is not str or not requires_python:
        raise error_type("project.requires-python must be a non-empty string")
    if (
        type(dependencies) is not list
        or any(type(item) is not str or not item for item in dependencies)
        or len(set(dependencies)) != len(dependencies)
    ):
        raise error_type("project.dependencies must be unique non-empty strings")

    closures: list[dict[str, Any]] = []
    try:
        for platform_name in sorted(
            platform_names, key=lambda item: item.encode("utf-8")
        ):
            closures.append(
                build_environment_closure_from_bound_inputs(
                    pixi_inputs,
                    environment="default",
                    platform=platform_name,
                )
            )
    except PixiLockError as exc:
        raise error_type(f"source runtime closure is invalid: {exc}") from exc

    return _seal(
        "dependency-lock",
        {
            "canonicalizer": CANONICALIZER_ID,
            "format": format_name,
            "kind": "dependency-lock",
            "schema_version": 2,
            "source_runtime_contract": {
                "environment": "default",
                "execution_scope": "supported-source-runtime",
                "host_platform_selection": "all-declared-targets",
                "packaged_materialization_evidence": (
                    "external-distribution-receipt-required"
                ),
                "project": {
                    "dependencies": sorted(
                        dependencies, key=lambda item: item.encode("utf-8")
                    ),
                    "requires_python": requires_python,
                },
                "target_closures": closures,
            },
        },
        "id",
    )


__all__ = [
    "RuntimeDependencyIdentityV2Error",
    "build_runtime_dependency_lock_manifest_v2",
]
