"""Admit a retained Cleanroom fixture execution policy without launching it.

The profile selects the inputs. Core reopens their managed snapshot and checks
the bounded command contract before any future execution can use it. This
review does not acquire Gradle, select a Java installation, or run the fixture.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .environment_fixture_import import _read_source, reopen_fixture_import
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import ReconstructionError, _seal, validate_share


FORMAT = "workbench-environment-fixture-execution-policy-review-v1"
_POLICY_FORMAT = "workbench-cleanroom-fixture-execution-policy-v1"
_POLICY_PATH = "profiles/platforms/cleanroom/policies/generic-mod-fixture-execution-v1.json"
_SCHEMA_PATH = "profiles/platforms/cleanroom/schemas/workbench-cleanroom-fixture-execution-policy-v1.schema.json"
_CLEANUP_PATH = "profiles/platforms/cleanroom/tools/clean_generic_mod_fixture.gradle"
_WITNESS_PATHS = {
    "fixture-execution-policy": _POLICY_PATH,
    "fixture-execution-schema": _SCHEMA_PATH,
    "fixture-cleanup-init": _CLEANUP_PATH,
}
_ARGV = [
    "{gradle_bin}", "--no-daemon", "-p", "{project}",
    "--project-cache-dir", "{project_cache}", "--init-script", "{cleanup_init}",
    "clean", "check", "workbenchCleanFixtureLocalCache",
]
_ENVIRONMENT = {
    "JAVA_HOME": "{java_home}", "PATH_PREPEND": "{java_home}/bin",
    "GRADLE_USER_HOME": "{gradle_home}", "TZ": "UTC",
}
_PATHS = {
    "project_relative_to_state": "source-projections/cleanroom/{fixture_digest_hex}/profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop",
    "project_cache_relative_to_state": "gradle-project-cache/generic-mod-daily-loop",
    "gradle_home_relative_to_state": "gradle-home/generic-mod-daily-loop",
    "generated_root_relative_to_projection_digest": ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop",
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GRADLE_VERSION = re.compile(r"[0-9][A-Za-z0-9.~-]*\Z")
_MAX_WITNESS_BYTES = 2 * 1024 * 1024


def _witness(tree: Path, row: Mapping[str, Any]) -> bytes:
    relative = PurePosixPath(row["relative_path"])
    raw = _read_source(tree.joinpath(*relative.parts), limit=_MAX_WITNESS_BYTES)
    if len(raw) != row["size"] or "sha256:" + sha256(raw).hexdigest() != row["sha256"]:
        raise ReconstructionError("retained fixture execution witness changed")
    return raw


def _object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ReconstructionError(f"retained {label} is not JSON: {exc}") from exc
    if type(value) is not dict:
        raise ReconstructionError(f"retained {label} is not an object")
    return value


def _policy_contract(
    policy: dict[str, Any], fixture: Mapping[str, Any], cleanup: Mapping[str, Any],
) -> None:
    if (set(policy) != {"format", "schema_version", "scope", "fixture", "gradle", "java",
                        "cleanup_init", "paths", "argv_template", "environment", "capture", "restart"}
            or policy["format"] != _POLICY_FORMAT or policy["schema_version"] != 1
            or policy["scope"] != "portable-input-policy-only"
            or policy["fixture"] != {
                "declaration_id": fixture["declaration_id"],
                "tree_digest": fixture["tree_digest"],
            }
            or policy["cleanup_init"] != {
                "relative_path": cleanup["relative_path"],
                "sha256": cleanup["sha256"], "size": cleanup["size"],
            }
            or policy["java"] != {
                "required_major": 25,
                "selection": "managed-profile-or-elected-local-java-25",
            }
            or policy["paths"] != _PATHS or policy["argv_template"] != _ARGV
            or policy["environment"] != _ENVIRONMENT
            or policy["restart"] != "retain-prepared-attempt-and-refuse-automatic-rerun-after-launch"):
        raise ReconstructionError("retained fixture execution policy differs from the V1 contract")
    gradle = policy["gradle"]
    if (type(gradle) is not dict
            or set(gradle) != {"selection", "version", "archive_root", "archive_url", "archive_sha256", "archive_size"}
            or gradle["selection"] != "profile-selected-unqualified"
            or type(gradle["version"]) is not str
            or _GRADLE_VERSION.fullmatch(gradle["version"]) is None
            or gradle["archive_root"] != f"gradle-{gradle['version']}"
            or gradle["archive_url"] != f"https://services.gradle.org/distributions/gradle-{gradle['version']}-bin.zip"
            or type(gradle["archive_sha256"]) is not str
            or _DIGEST.fullmatch(gradle["archive_sha256"]) is None
            or type(gradle["archive_size"]) is not int
            or not 0 < gradle["archive_size"] <= 256 * 1024 * 1024):
        raise ReconstructionError("retained fixture Gradle archive policy is invalid")
    capture = policy["capture"]
    if (type(capture) is not dict
            or set(capture) != {"maximum_bytes_per_stream", "timeout_seconds", "require_process_group_closure"}
            or capture["maximum_bytes_per_stream"] != 4 * 1024 * 1024
            or type(capture["timeout_seconds"]) is not int
            or not 1 <= capture["timeout_seconds"] <= 21600
            or capture["require_process_group_closure"] is not True):
        raise ReconstructionError("retained fixture process custody policy is invalid")


def review_fixture_execution_policy(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Reopen six exact witnesses in Core custody and return a review-only seal."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    fixture = reviewed["profile_fixture"]
    if fixture["owner_profile_id"] != "cleanroom":
        raise ReconstructionError("portable fixture execution policy needs the Cleanroom owner")
    by_kind = {row["kind"]: row for row in fixture["sources"]}
    if (set(_WITNESS_PATHS) - set(by_kind)
            or any(by_kind[kind]["relative_path"] != path
                   for kind, path in _WITNESS_PATHS.items())):
        raise ReconstructionError("fixture candidate lacks the exact portable execution witnesses")
    receipt = reopen_fixture_import(
        suite_root, portable, reviewed, workspace=workspace,
        result_resource_id=fixture_result_resource_id, environment=environment,
    )
    tree = Path(receipt["tree_path"])
    policy_raw = _witness(tree, by_kind["fixture-execution-policy"])
    schema_raw = _witness(tree, by_kind["fixture-execution-schema"])
    _witness(tree, by_kind["fixture-cleanup-init"])
    policy = _object(policy_raw, "fixture execution policy")
    schema = _object(schema_raw, "fixture execution schema")
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(policy))
    except (SchemaError, ValueError, TypeError) as exc:
        raise ReconstructionError(f"fixture execution schema is invalid: {exc}") from exc
    if errors:
        raise ReconstructionError("retained fixture execution policy differs from its schema")
    _policy_contract(policy, fixture, by_kind["fixture-cleanup-init"])
    return _seal({
        "format": FORMAT, "schema_version": 1, "share_id": portable["share_id"],
        "candidate_id": reviewed["candidate_id"],
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_tree_id": receipt["tree_id"],
        "fixture_tree_content_sha256": receipt["tree_content_sha256"],
        "policy_witness": {key: {"sha256": by_kind[key]["sha256"], "size": by_kind[key]["size"]}
                           for key in sorted(_WITNESS_PATHS)},
        "policy": policy,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "remaining_prerequisites": [
            "exact-gradle-archive-acquisition-and-admission",
            "elected-java-25-binding", "profile-fixture-execution",
            "fixture-artifact-admission", "combined-environment-reconstruction",
        ],
        "state": "review-only-unqualified",
    }, "workbench-environment-fixture-execution-policy-review", "review_id")


def reopen_fixture_execution_policy(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str, expected_review_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recompute a saved review ID from retained inputs without original sources."""

    review = review_fixture_execution_policy(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id, environment=environment,
    )
    if type(expected_review_id) is not str or review["review_id"] != expected_review_id:
        raise ReconstructionError("fixture execution policy review changed after retention")
    return review


__all__ = ["review_fixture_execution_policy", "reopen_fixture_execution_policy"]
