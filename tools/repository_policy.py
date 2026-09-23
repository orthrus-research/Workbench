"""Validate the declared public repository and independent release-unit map.

These records organize source custody and release ownership. They do not create
a repository, assign a version, qualify an artifact, or authorize publication.
"""

from __future__ import annotations

from component_versions import load_authority, check_projections, ComponentVersionError

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPOSITORY_PATH = Path("packaging/release/public-repository-v1.json")
PUBLIC_REPOSITORY_SCHEMA_PATH = Path(
    "packaging/release/schemas/workbench-public-repository-v1.schema.json"
)


class PublicRepositoryError(ValueError):
    """The public repository or release-unit record is absent or inconsistent."""


def _strict_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicRepositoryError(f"cannot read {label}: {path}: {exc}") from exc
    if not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise PublicRepositoryError(f"{label} is empty or has a UTF-8 BOM: {path}")

    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise PublicRepositoryError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                PublicRepositoryError(
                    f"{label} contains non-finite value {token!r}"
                )
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicRepositoryError(
            f"{label} is not strict UTF-8 JSON: {path}"
        ) from exc
    if type(value) is not dict:
        raise PublicRepositoryError(f"{label} root must be an object: {path}")
    return value


def _schema_error(error: ValidationError) -> str:
    pointer = "/" + "/".join(str(part) for part in error.absolute_path)
    return f"{pointer}: {error.message}"


def _validate_schema(
    value: Mapping[str, Any],
    *,
    root: Path,
    schema_path: Path,
    label: str,
) -> None:
    schema = _strict_json(root / schema_path, label=f"{label} schema")
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except (SchemaError, ValidationError) as exc:
        detail = _schema_error(exc) if isinstance(exc, ValidationError) else str(exc)
        raise PublicRepositoryError(f"invalid {label}: {detail}") from exc


def _safe_repository_path(
    root: Path,
    value: str,
    *,
    label: str,
    file_only: bool = False,
) -> Path:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.as_posix() != value
    ):
        raise PublicRepositoryError(
            f"{label} is not a safe repository path: {value!r}"
        )
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.exists() or (file_only and not path.is_file()):
        kind = "file" if file_only else "path"
        raise PublicRepositoryError(
            f"{label} is missing, indirect, or not a {kind}: {value}"
        )
    return path


def validate_public_repository(
    value: Mapping[str, Any],
    root: Path = ROOT,
) -> dict[str, Any]:
    """Validate the destination identity without upgrading any hosting claim."""

    root = root.resolve()
    _validate_schema(
        value,
        root=root,
        schema_path=PUBLIC_REPOSITORY_SCHEMA_PATH,
        label="public repository record",
    )
    result = deepcopy(dict(value))
    destination = result["destination"]
    full_name = (
        f"{destination['organization_login']}/{destination['repository_name']}"
    )
    if destination["full_name"] != full_name:
        raise PublicRepositoryError("public repository full name is not derived")
    base = f"https://github.com/{full_name}"
    expected_urls = {
        "html_url": base,
        "clone_url": base + ".git",
        "issues_url": base + "/issues",
        "security_advisory_url": base + "/security/advisories/new",
    }
    for field, expected in expected_urls.items():
        if destination[field] != expected:
            raise PublicRepositoryError(
                f"public repository {field} is not derived from its identity"
            )

    hosting_state = result["hosting_state"]
    claims = result["claims"]
    if hosting_state == "not-created" and any(claims.values()):
        raise PublicRepositoryError(
            "a not-created repository cannot carry a positive hosting claim"
        )
    if hosting_state == "private-staging" and (
        claims["repository_created"] is not True
        or claims["public_visibility_verified"] is not False
    ):
        raise PublicRepositoryError(
            "a private-staging repository must exist and cannot claim public visibility"
        )
    if hosting_state == "public" and (
        claims["repository_created"] is not True
        or claims["public_visibility_verified"] is not True
    ):
        raise PublicRepositoryError(
            "a public repository must exist and have verified public visibility"
        )
    if not claims["repository_created"] and any(
        claims[field]
        for field in (
            "history_pushed",
            "public_visibility_verified",
            "repository_settings_applied",
        )
    ):
        raise PublicRepositoryError(
            "hosting activity cannot precede repository creation"
        )
    history = result["history_policy"]
    _safe_repository_path(
        root,
        history["export_tool"],
        label="public export tool",
        file_only=True,
    )
    if history["existing_branches_published"] or history["existing_tags_published"]:
        raise PublicRepositoryError(
            "a clean-root export cannot publish existing refs"
        )
    return result


def load_public_repository(root: Path = ROOT) -> dict[str, Any]:
    return validate_public_repository(_strict_json(root / PUBLIC_REPOSITORY_PATH, label="public repository record"), root)


def validate_release_units(value: Mapping[str, Any], root: Path = ROOT) -> dict[str, Any]:
    expected = load_release_units(root)
    if value != expected:
        raise PublicRepositoryError("release view differs from native authorities")
    return expected


def load_release_units(root: Path = ROOT) -> dict[str, Any]:
    try:
        authority, components = load_authority(root)
        failures = check_projections(components, root)
        if failures:
            raise PublicRepositoryError("; ".join(failures))
        repository = load_public_repository(root)
        if sorted(repository["history_policy"]["tag_namespaces"]) != sorted(name + "/v" for name in components):
            raise PublicRepositoryError("tag namespaces differ from native component inventory")
        return authority
    except ComponentVersionError as exc:
        raise PublicRepositoryError(str(exc)) from exc


def public_repository_summary(root: Path = ROOT) -> dict[str, Any]:
    repository = load_public_repository(root)
    authority = load_release_units(root)
    return {"format": "workbench-public-repository-summary-v1", "schema_version": 1,
            "destination": repository["destination"]["full_name"], "hosting_state": repository["hosting_state"],
            "history_strategy": repository["history_policy"]["kind"], "claims": repository["claims"],
            "release_units": authority["components"], "release_descriptor_id": authority["release_descriptor_id"]}
