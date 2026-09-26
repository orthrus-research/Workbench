"""Read-only, exact input candidates for a V3 environment share.

This record is a review prerequisite. It does not install a package, copy a
fixture, or change the unresolved inputs in an existing share/import receipt.
"""

from __future__ import annotations

from hashlib import file_digest, sha256
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from jsonschema.exceptions import SchemaError

from workbench_api import ModuleError
from workbench_api.profile_extensions import (
    ProfileExtensionError, profile_extension_identity, require_profile_extension,
)
from workbench_api.profiles import profile_status

from .durable_records import read_bounded_bytes
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _seal, validate_share,
)
from .module_cli import _snapshot_wheel


FORMAT = "workbench-environment-input-candidate-v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[a-z][a-z0-9.-]*\Z")
_PROFILE_PATH = re.compile(r"profiles/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_FIXTURE_KINDS = frozenset({
    "fixture-owner-lock", "fixture-owner-schema", "profile-preflight-tool",
})
_PORTABLE_FIXTURE_KINDS = _FIXTURE_KINDS | frozenset({
    "fixture-cleanup-init", "fixture-execution-policy", "fixture-execution-schema",
})
_FIXTURE_KIND_SETS = (_FIXTURE_KINDS, _PORTABLE_FIXTURE_KINDS)
_MAX_WHEELS = 64
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_RECORD_BYTES = 64 * 1024


def _ordinary_source(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ReconstructionError("input candidate source must be an absolute file")
    if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
           for part in (path, *path.parents)):
        raise ReconstructionError("input candidate source traverses a redirect")


def _wheel_candidate(path: Path) -> dict[str, Any]:
    _ordinary_source(path)
    try:
        with _snapshot_wheel(path) as wheel:
            with wheel.path.open("rb") as source:
                digest = file_digest(source, "sha256").hexdigest()
            size = wheel.path.stat().st_size
            if not wheel.modules and not wheel.profiles:
                raise ReconstructionError("candidate wheel declares no optional Workbench owner")
            return {
                "distribution": wheel.distribution,
                "version": wheel.version,
                "sha256": "sha256:" + digest,
                "size": size,
                "module_ids": sorted(wheel.modules),
                "profile_ids": sorted(wheel.profiles),
            }
    except (ModuleError, OSError, ValueError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"optional package wheel cannot be admitted: {exc}") from exc


def _profile_fixture_candidate(
    owner_id: str, *, platform_document_id: str, platform_document_sha256: str,
) -> dict[str, Any]:
    try:
        owners = [row for row in profile_status() if row.id == owner_id and row.state == "available"]
        if len(owners) != 1 or owners[0].profile is None or owners[0].profile.kind != "platform":
            raise ReconstructionError("selected platform fixture owner is not admitted")
        profile_source = owners[0].profile.resource("profile")
        _ordinary_source(profile_source)
        if sha256(read_bounded_bytes(profile_source, byte_limit=_MAX_SOURCE_BYTES)).hexdigest() != platform_document_sha256:
            raise ReconstructionError("fixture owner platform document differs from the V3 share")
        owner = require_profile_extension("workbench.workspace_home_fixtures", owner_id)
        identity = profile_extension_identity("workbench.workspace_home_fixtures", owner_id)
        if (identity["distribution"] != owners[0].distribution
                or identity["version"] != owners[0].version):
            raise ReconstructionError("fixture extension differs from the admitted platform owner")
        read_lock = getattr(owner, "read_owner_lock")
        validate_lock = getattr(owner, "validate_owner_lock")
        source_inputs = getattr(owner, "source_inputs")
        lock = read_lock()
        if validate_lock(lock) != lock:
            raise ReconstructionError("fixture owner did not validate its exact lock")
        declaration = lock.get("declaration_id") if type(lock) is dict else None
        declared = lock.get("declared_values") if type(lock) is dict else None
        tree_digest = declared.get("tree_digest") if type(declared) is dict else None
        fixture_identity = declared.get("identity") if type(declared) is dict else None
        if (type(declaration) is not str or _SAFE_ID.fullmatch(declaration) is None
                or type(tree_digest) is not str or _DIGEST.fullmatch(tree_digest) is None
                or type(fixture_identity) is not dict
                or fixture_identity.get("digest") != tree_digest):
            raise ReconstructionError("fixture owner lock lacks an exact source tree identity")
        inputs = source_inputs()
        kinds = {row.get("kind") for row in inputs if type(row) is dict} if type(inputs) is tuple else set()
        if (type(inputs) is not tuple or len(inputs) != len(kinds)
                or kinds not in _FIXTURE_KIND_SETS):
            raise ReconstructionError("fixture owner does not expose a complete supported source witness set")
        if kinds == _PORTABLE_FIXTURE_KINDS:
            read_policy = getattr(owner, "read_execution_policy")
            validate_policy = getattr(owner, "validate_execution_policy")
            policy = read_policy()
            if validate_policy(policy) != policy:
                raise ReconstructionError("fixture owner did not validate its execution policy")
        sources = []
        for row in inputs:
            relative = row.get("display_path")
            path = row.get("path")
            if (type(relative) is not str or _PROFILE_PATH.fullmatch(relative) is None
                    or PurePosixPath(relative).as_posix() != relative
                    or any(part in {".", ".."} for part in PurePosixPath(relative).parts)):
                raise ReconstructionError("fixture source witness has no portable profile path")
            _ordinary_source(path)
            raw = read_bounded_bytes(path, byte_limit=_MAX_SOURCE_BYTES)
            sources.append({
                "kind": row["kind"], "relative_path": relative,
                "sha256": "sha256:" + sha256(raw).hexdigest(), "size": len(raw),
            })
        if len({item["relative_path"] for item in sources}) != len(sources):
            raise ReconstructionError("fixture source witness paths are repeated")
        if (kinds == _PORTABLE_FIXTURE_KINDS
                and validate_policy(read_policy()) != policy):
            raise ReconstructionError("fixture execution policy changed during candidate inspection")
        if (sha256(read_bounded_bytes(profile_source, byte_limit=_MAX_SOURCE_BYTES)).hexdigest()
                != platform_document_sha256 or validate_lock(read_lock()) != lock
                or identity != profile_extension_identity(
                    "workbench.workspace_home_fixtures", owner_id,
                )):
            raise ReconstructionError("fixture owner changed during candidate inspection")
        return {
            "selected_platform_profile_id": platform_document_id,
            "owner_profile_id": owner_id, "owner_code": identity,
            "declaration_id": declaration, "tree_digest": tree_digest,
            "sources": sorted(sources, key=lambda row: row["relative_path"].encode("utf-8")),
        }
    except (ProfileExtensionError, OSError, ValueError, AttributeError, KeyError, TypeError,
            SchemaError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"profile fixture owner cannot prove its exact inputs: {exc}") from exc


def build_input_candidate(
    share: Mapping[str, Any], *, wheels: Sequence[Path], profile_owner_id: str,
) -> dict[str, Any]:
    """Bind reviewed local wheel and fixture identities to an exact V3 share.

    All source paths stay local. A future acquisition must reopen these bytes
    and use a separately reviewed install/copy operation.
    """

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("input candidates require a V3 environment share")
    if (not isinstance(wheels, (tuple, list)) or not 1 <= len(wheels) <= _MAX_WHEELS
            or any(not isinstance(path, Path) for path in wheels)):
        raise ReconstructionError("select one to 64 explicit optional package wheels")
    if type(profile_owner_id) is not str or _SAFE_ID.fullmatch(profile_owner_id) is None:
        raise ReconstructionError("select an admitted platform fixture owner ID")
    packages = sorted((_wheel_candidate(path) for path in wheels), key=lambda row: row["distribution"])
    if (len({row["distribution"] for row in packages}) != len(packages)
            or len({owner for row in packages for owner in (*row["module_ids"], *row["profile_ids"])})
            != sum(len(row["module_ids"]) + len(row["profile_ids"]) for row in packages)):
        raise ReconstructionError("optional package candidates repeat an owner or distribution")
    platform = portable["lock"]["platform_profile"]
    fixture = _profile_fixture_candidate(
        profile_owner_id, platform_document_id=platform["profile_id"],
        platform_document_sha256=platform["sha256"],
    )
    body = {
        "format": FORMAT, "schema_version": 1,
        "share_id": portable["share_id"],
        "host_variant": portable["lock"]["host_variant"],
        "coverage": "reviewed-input-candidates-only",
        "packages": packages, "profile_fixture": fixture,
    }
    result = _seal(body, "workbench-environment-input-candidate", "candidate_id")
    if len(_canonical(result)) > _MAX_RECORD_BYTES:
        raise ReconstructionError("environment input candidate exceeds its byte bound")
    return result


def validate_input_candidate(share: Mapping[str, Any], value: object) -> dict[str, Any]:
    """Admit a transported candidate without treating its source bytes as present."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3 or type(value) is not dict:
        raise ReconstructionError("environment input candidate requires a V3 share")
    candidate = value
    if (set(candidate) != {"format", "schema_version", "share_id", "host_variant",
                           "coverage", "packages", "profile_fixture", "candidate_id"}
            or candidate["format"] != FORMAT or type(candidate["schema_version"]) is not int
            or candidate["schema_version"] != 1
            or candidate["share_id"] != portable["share_id"]
            or candidate["host_variant"] != portable["lock"]["host_variant"]
            or candidate["coverage"] != "reviewed-input-candidates-only"
            or type(candidate["packages"]) is not list
            or not 1 <= len(candidate["packages"]) <= _MAX_WHEELS
            or type(candidate["profile_fixture"]) is not dict
            or candidate["profile_fixture"].get("selected_platform_profile_id")
            != portable["lock"]["platform_profile"]["profile_id"]
            or len(_canonical(candidate)) > _MAX_RECORD_BYTES):
        raise ReconstructionError("environment input candidate has unsupported fields or binding")
    for package in candidate["packages"]:
        if (type(package) is not dict
                or set(package) != {"distribution", "version", "sha256", "size",
                                    "module_ids", "profile_ids"}
                or type(package["distribution"]) is not str
                or _SAFE_ID.fullmatch(package["distribution"]) is None
                or type(package["version"]) is not str or not package["version"]
                or type(package["sha256"]) is not str or _DIGEST.fullmatch(package["sha256"]) is None
                or type(package["size"]) is not int or not 0 < package["size"] <= 256 * 1024 * 1024
                or any(type(package[key]) is not list or any(
                    type(item) is not str or _SAFE_ID.fullmatch(item) is None
                    for item in package[key]) for key in ("module_ids", "profile_ids"))
                or not package["module_ids"] and not package["profile_ids"]):
            raise ReconstructionError("environment package candidate is invalid")
    owners = [owner for row in candidate["packages"]
              for owner in (*row["module_ids"], *row["profile_ids"])]
    if (candidate["packages"] != sorted(candidate["packages"], key=lambda row: row["distribution"])
            or len({row["distribution"] for row in candidate["packages"]}) != len(candidate["packages"])
            or len(set(owners)) != len(owners)
            or any(row[key] != sorted(set(row[key])) for row in candidate["packages"]
                   for key in ("module_ids", "profile_ids"))):
        raise ReconstructionError("environment package candidates are not sorted")
    fixture = candidate["profile_fixture"]
    if (set(fixture) != {"selected_platform_profile_id", "owner_profile_id", "owner_code",
                         "declaration_id", "tree_digest", "sources"}
            or type(fixture["owner_profile_id"]) is not str
            or _SAFE_ID.fullmatch(fixture["owner_profile_id"]) is None
            or type(fixture["declaration_id"]) is not str
            or _SAFE_ID.fullmatch(fixture["declaration_id"]) is None
            or type(fixture["tree_digest"]) is not str
            or _DIGEST.fullmatch(fixture["tree_digest"]) is None
            or type(fixture["owner_code"]) is not dict
            or type(fixture["sources"]) is not list
            or len(fixture["sources"]) not in {len(kinds) for kinds in _FIXTURE_KIND_SETS}):
        raise ReconstructionError("environment fixture candidate is invalid")
    owner_code = fixture["owner_code"]
    if (set(owner_code) != {"profile_id", "group", "module", "distribution", "version",
                           "api_version", "sha256", "size", "package_source_sha256"}
            or owner_code["profile_id"] != fixture["owner_profile_id"]
            or owner_code["group"] != "workbench.workspace_home_fixtures"
            or any(type(owner_code[key]) is not str or not owner_code[key]
                   for key in ("module", "distribution", "version"))
            or type(owner_code["api_version"]) is not int or owner_code["api_version"] != 1
            or any(type(owner_code[key]) is not str
                   or re.fullmatch(r"[0-9a-f]{64}", owner_code[key]) is None
                   for key in ("sha256", "package_source_sha256"))
            or type(owner_code["size"]) is not int
            or not 0 < owner_code["size"] <= _MAX_SOURCE_BYTES):
        raise ReconstructionError("environment fixture owner code identity is invalid")
    for source in fixture["sources"]:
        if (type(source) is not dict
                or set(source) != {"kind", "relative_path", "sha256", "size"}
                or type(source["kind"]) is not str or source["kind"] not in _PORTABLE_FIXTURE_KINDS
                or type(source["relative_path"]) is not str
                or _PROFILE_PATH.fullmatch(source["relative_path"]) is None
                or PurePosixPath(source["relative_path"]).as_posix() != source["relative_path"]
                or any(part in {".", ".."} for part in PurePosixPath(source["relative_path"]).parts)
                or type(source["sha256"]) is not str or _DIGEST.fullmatch(source["sha256"]) is None
                or type(source["size"]) is not int or not 0 <= source["size"] <= _MAX_SOURCE_BYTES):
            raise ReconstructionError("environment fixture source candidate is invalid")
    if ({row["kind"] for row in fixture["sources"]} not in _FIXTURE_KIND_SETS
            or len({row["relative_path"] for row in fixture["sources"]}) != len(fixture["sources"])
            or fixture["sources"] != sorted(fixture["sources"], key=lambda row: row["relative_path"].encode("utf-8"))
            or candidate["candidate_id"] != _seal(
                {key: item for key, item in candidate.items() if key != "candidate_id"},
                "workbench-environment-input-candidate", "candidate_id",
            )["candidate_id"]):
        raise ReconstructionError("environment input candidate identity changed")
    return candidate


def recheck_input_candidate(
    share: Mapping[str, Any], candidate: Mapping[str, Any], *, wheels: Sequence[Path],
    profile_owner_id: str,
) -> dict[str, Any]:
    """Prove that the reviewed candidate still describes the selected local inputs."""

    reviewed = validate_input_candidate(share, dict(candidate))
    if build_input_candidate(share, wheels=wheels, profile_owner_id=profile_owner_id) != reviewed:
        raise ReconstructionError("reviewed environment input candidate changed")
    return reviewed


__all__ = [
    "FORMAT", "build_input_candidate", "recheck_input_candidate", "validate_input_candidate",
]
