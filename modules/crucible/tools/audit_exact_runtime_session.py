#!/usr/bin/env python3
"""Audit one already-captured exact Cleanroom runtime session.

This tool is intentionally not a launcher and not a normalizer.  It verifies a
closed set of caller-pinned inputs, checks that the saved launch contract agrees
with the exact case configuration, and emits one small content-addressed
receipt.  Runtime files are read in place and are never modified.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
import tempfile
from typing import Any, Mapping


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    build_foundation_class_dump_manifest,
    canonical_json_bytes,
    canonical_json_sha256,
    locate_foundation_class_dump,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    FIXED_WORLD_SEED,
    FIXED_WORLD_SEED_SHA256,
    load_fixture_result,
)


RECEIPT_SCHEMA = "workbench.crucible.exact-runtime-session-audit.v1"
RECEIPT_PREFIX = "crucible-exact-runtime-session-audit:sha256:"
CONFIGURATION_FORMAT = "workbench-cleanroom-configuration-set-v1"
INSTALLED_MOD_SET_FORMAT = "workbench-cleanroom-installed-mod-set-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_GRADLE_SUCCESS_RE = re.compile(r"^BUILD SUCCESSFUL(?: in .+)?$")
_GRADLE_FAILURE_RE = re.compile(r"^BUILD FAILED(?: in .+)?$")
_CHILD_EXIT_RE = re.compile(r"finished with non-zero exit value ([0-9]+)")
_JAVA_OPTIONS_PREFIX = "Picked up JAVA_TOOL_OPTIONS: "

_MAX_SMALL_JSON_BYTES = 16 * 1024 * 1024
_MAX_LAUNCH_LOG_BYTES = 32 * 1024 * 1024
_MAX_FIXTURE_ARTIFACT_BYTES = 1024 * 1024 * 1024
_MAX_CONFIG_FILE_BYTES = 256 * 1024 * 1024
_MAX_RAW_BYTES = 32 * 1024 * 1024 * 1024
_MAX_RAW_LINE_BYTES = 4 * 1024 * 1024
_MAX_RAW_ROWS = 50_000_000
_HASH_CHUNK_BYTES = 1024 * 1024

_CONFIGURATION_KEYS = {"format", "case_id", "files", "runtime_settings"}
_CONFIG_FILE_KEYS = {"relative_path", "sha256"}
_RUNTIME_SETTING_KEYS = {
    "foundation_dump",
    "main_class",
    "fixture_driver_enabled",
    "fixture_expected_seed",
    "fixture_expected_seed_sha256",
    "fixture_route_order",
    "fixture_result_relative_path",
    "probe_enabled",
    "probe_capture_id",
    "probe_mode",
    "probe_output_relative_path",
}
_INSTALLED_MOD_SET_KEYS = {
    "format",
    "candidate_lock_sha256",
    "runtime_class_source_sha256",
    "installed_artifacts",
}
_INSTALLED_ARTIFACT_KEYS = {"relative_path", "sha256"}


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _FileReceipt:
    path: Path
    size: int
    sha256: str
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _RawReceipt:
    file: _FileReceipt
    row_count: int
    first_row: Mapping[str, Any]
    penultimate_row: Mapping[str, Any] | None
    last_row: Mapping[str, Any]
    stop_token_count: int
    completion_token_count: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _exact_keys(value: Any, expected: set[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == expected,
        f"{context} fields mismatch: missing={sorted(expected - actual)!r}, "
        f"unknown={sorted(actual - expected)!r}",
    )
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token}")


def _reject_float(token: str) -> None:
    raise ValueError(f"session audit JSON does not permit number {token}")


def _parse_json(encoded: bytes, *, context: str) -> Any:
    try:
        return json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse {context}: {exc}") from exc


def _absolute_path(value: str | os.PathLike[str], context: str) -> Path:
    path = Path(value)
    _require(path.is_absolute(), f"{context} must be an absolute path")
    return path


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _plain_directory(path: Path, context: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(metadata.st_mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISDIR(metadata.st_mode), f"{context} is not a directory: {path}")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise CaptureValidationError(f"cannot resolve {context} {path}: {exc}") from exc


def _hash_regular_file(
    path: Path,
    *,
    context: str,
    maximum_size: int,
) -> _FileReceipt:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISREG(before.st_mode), f"{context} is not a regular file: {path}")
    _require(
        0 < before.st_size <= maximum_size,
        f"{context} size is outside audit bounds: {path}",
    )
    expected_identity = _identity(before)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(_HASH_CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
        after = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot hash {context} {path}: {exc}") from exc
    _require(_identity(after) == expected_identity, f"{context} changed while hashing: {path}")
    return _FileReceipt(path, before.st_size, digest.hexdigest(), expected_identity)


def _require_unchanged(receipt: _FileReceipt, context: str) -> None:
    try:
        current = receipt.path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot re-inspect {context} {receipt.path}: {exc}") from exc
    _require(
        _identity(current) == receipt.identity,
        f"{context} changed during session audit: {receipt.path}",
    )


def _load_strict_json(
    path: Path,
    *,
    context: str,
    maximum_size: int = _MAX_SMALL_JSON_BYTES,
) -> tuple[Any, _FileReceipt]:
    receipt = _hash_regular_file(path, context=context, maximum_size=maximum_size)
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot read {context} {path}: {exc}") from exc
    _require_unchanged(receipt, context)
    return _parse_json(encoded, context=context), receipt


def _safe_relative_file(value: Any, *, prefix: str, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    _require("\\" not in value and "\x00" not in value, f"{context} is not portable")
    relative = PurePosixPath(value)
    _require(
        not relative.is_absolute()
        and all(part not in {"", ".", ".."} for part in relative.parts),
        f"{context} must be a normalized relative path",
    )
    _require(
        value == relative.as_posix() and value.startswith(prefix),
        f"{context} is outside {prefix.rstrip('/')}",
    )
    return value


def _child_path(server: Path, relative_path: str, context: str) -> Path:
    candidate = server.joinpath(*PurePosixPath(relative_path).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CaptureValidationError(f"cannot resolve {context} {candidate}: {exc}") from exc
    _require(server == resolved or server in resolved.parents, f"{context} escapes the server directory")
    return resolved


def _regular_inventory(directory: Path, *, prefix: str, context: str) -> set[str]:
    _plain_directory(directory, context)
    result: set[str] = set()
    try:
        entries = tuple(directory.rglob("*"))
    except OSError as exc:
        raise CaptureValidationError(f"cannot enumerate {context} {directory}: {exc}") from exc
    for entry in entries:
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise CaptureValidationError(f"cannot inspect {context} entry {entry}: {exc}") from exc
        _require(not stat.S_ISLNK(metadata.st_mode), f"{context} contains a symlink: {entry}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        _require(stat.S_ISREG(metadata.st_mode), f"{context} contains a non-file: {entry}")
        result.add(prefix + entry.relative_to(directory).as_posix())
    return result


def _verify_configuration_manifest(
    value: Any,
    *,
    case_id: str,
    server: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _exact_keys(value, _CONFIGURATION_KEYS, "configuration-set manifest")
    _require(manifest["format"] == CONFIGURATION_FORMAT, "configuration-set format mismatch")
    _require(manifest["case_id"] == case_id, "configuration-set case ID mismatch")
    files = manifest["files"]
    _require(isinstance(files, list) and bool(files), "configuration-set files must be nonempty")
    verified: list[dict[str, Any]] = []
    paths: list[str] = []
    receipts: list[_FileReceipt] = []
    for index, raw in enumerate(files):
        item = _exact_keys(raw, _CONFIG_FILE_KEYS, f"configuration file {index}")
        raw_path = item["relative_path"]
        if raw_path == "server.properties":
            relative_path = raw_path
        else:
            relative_path = _safe_relative_file(
                raw_path,
                prefix="config/",
                context=f"configuration file {index} path",
            )
        expected_sha256 = _sha256(item["sha256"], f"configuration file {relative_path}")
        source = _child_path(server, relative_path, "configuration file")
        receipt = _hash_regular_file(
            source,
            context=f"configuration file {relative_path}",
            maximum_size=_MAX_CONFIG_FILE_BYTES,
        )
        _require(receipt.sha256 == expected_sha256, f"configuration file digest mismatch: {relative_path}")
        paths.append(relative_path)
        receipts.append(receipt)
        verified.append({"relative_path": relative_path, "size_bytes": receipt.size, "sha256": receipt.sha256})
    _require(paths == sorted(set(paths)), "configuration file list is not a canonical unique path set")
    observed_paths = {"server.properties"} | _regular_inventory(
        server / "config", prefix="config/", context="server configuration directory"
    )
    _require(set(paths) == observed_paths, "configuration manifest does not exactly inventory server configuration files")

    settings = _exact_keys(manifest["runtime_settings"], _RUNTIME_SETTING_KEYS, "configuration runtime settings")
    _require(settings["foundation_dump"] is True, "Foundation class dumping was not enabled")
    _require(settings["main_class"] == "com.cleanroommc.boot.MainServer", "exact Cleanroom server main class mismatch")
    _require(settings["fixture_driver_enabled"] is True, "fixture driver was not enabled")
    _require(settings["fixture_expected_seed"] == str(FIXED_WORLD_SEED), "fixture seed mismatch")
    _require(settings["fixture_expected_seed_sha256"] == FIXED_WORLD_SEED_SHA256, "fixture seed digest mismatch")
    _require(settings["fixture_route_order"] in {"forward", "reverse"}, "fixture route order is invalid")
    _require(settings["fixture_result_relative_path"] == "fixture-result.json", "fixture result path is not exact")
    probe_enabled = settings["probe_enabled"]
    _require(isinstance(probe_enabled, bool), "probe enabled setting must be a boolean")
    optional_probe = (
        settings["probe_capture_id"],
        settings["probe_mode"],
        settings["probe_output_relative_path"],
    )
    if probe_enabled:
        _require(
            isinstance(optional_probe[0], str) and bool(optional_probe[0]),
            "enabled probe requires a capture ID",
        )
        _require(optional_probe[1] == "lossless-fixture", "enabled probe mode mismatch")
        _require(optional_probe[2] == "raw.ndjson", "enabled probe output path mismatch")
    else:
        _require(optional_probe == (None, None, None), "disabled probe must omit capture, mode, and output")
    for receipt in receipts:
        _require_unchanged(receipt, "configuration file")
    return dict(settings), verified


def _verify_installed_mod_manifest(
    value: Any,
    *,
    server: Path,
    candidate_lock_sha256: str,
    fixture_artifact_sha256: str,
) -> tuple[str, list[dict[str, Any]]]:
    manifest = _exact_keys(value, _INSTALLED_MOD_SET_KEYS, "installed mod-set manifest")
    _require(manifest["format"] == INSTALLED_MOD_SET_FORMAT, "installed mod-set format mismatch")
    _require(manifest["candidate_lock_sha256"] == candidate_lock_sha256, "installed mod-set candidate lock mismatch")
    runtime_source = _sha256(manifest["runtime_class_source_sha256"], "runtime class source")
    artifacts = manifest["installed_artifacts"]
    _require(isinstance(artifacts, list) and bool(artifacts), "installed artifacts must be nonempty")
    verified: list[dict[str, Any]] = []
    paths: list[str] = []
    receipts: list[_FileReceipt] = []
    for index, raw in enumerate(artifacts):
        item = _exact_keys(raw, _INSTALLED_ARTIFACT_KEYS, f"installed artifact {index}")
        relative_path = _safe_relative_file(
            item["relative_path"], prefix="mods/", context=f"installed artifact {index} path"
        )
        expected_sha256 = _sha256(item["sha256"], f"installed artifact {relative_path}")
        source = _child_path(server, relative_path, "installed artifact")
        receipt = _hash_regular_file(
            source,
            context=f"installed artifact {relative_path}",
            maximum_size=_MAX_FIXTURE_ARTIFACT_BYTES,
        )
        _require(receipt.sha256 == expected_sha256, f"installed artifact digest mismatch: {relative_path}")
        paths.append(relative_path)
        receipts.append(receipt)
        verified.append({"relative_path": relative_path, "size_bytes": receipt.size, "sha256": receipt.sha256})
    _require(paths == sorted(set(paths)), "installed artifact list is not a canonical unique path set")
    _require(
        fixture_artifact_sha256 in {row["sha256"] for row in verified},
        "fixture artifact is absent from the installed mod set",
    )
    observed_paths = _regular_inventory(server / "mods", prefix="mods/", context="server mods directory")
    _require(set(paths) == observed_paths, "installed mod manifest does not exactly inventory server mods")
    for receipt in receipts:
        _require_unchanged(receipt, "installed artifact")
    return runtime_source, verified


def _parse_java_properties(line: str) -> dict[str, str]:
    _require(line.startswith(_JAVA_OPTIONS_PREFIX), "invalid JAVA_TOOL_OPTIONS evidence line")
    try:
        tokens = shlex.split(line[len(_JAVA_OPTIONS_PREFIX) :], posix=True)
    except ValueError as exc:
        raise CaptureValidationError(f"cannot parse JAVA_TOOL_OPTIONS evidence: {exc}") from exc
    _require(bool(tokens), "JAVA_TOOL_OPTIONS evidence is empty")
    properties: dict[str, str] = {}
    for token in tokens:
        _require(token.startswith("-D") and "=" in token[2:], "JAVA_TOOL_OPTIONS contains a non-property token")
        key, value = token[2:].split("=", 1)
        _require(bool(key) and key not in properties, f"duplicate JAVA_TOOL_OPTIONS property {key!r}")
        properties[key] = value
    return properties


def _verify_launch_log(
    path: Path,
    *,
    expected_sha256: str,
    case: Path,
    settings: Mapping[str, Any],
    outcome: str,
) -> tuple[_FileReceipt, dict[str, Any]]:
    receipt = _hash_regular_file(path, context="launch log", maximum_size=_MAX_LAUNCH_LOG_BYTES)
    _require(receipt.sha256 == expected_sha256, "launch log digest mismatch")
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise CaptureValidationError(f"cannot read launch log {path}: {exc}") from exc
    _require_unchanged(receipt, "launch log")
    lines = text.splitlines()
    java_lines = [line for line in lines if line.startswith(_JAVA_OPTIONS_PREFIX)]
    _require(len(java_lines) == 2, "launch log must contain exactly two JAVA_TOOL_OPTIONS evidence lines")
    _require(java_lines[0] == java_lines[1], "JAVA_TOOL_OPTIONS evidence lines differ")
    properties = _parse_java_properties(java_lines[0])

    expected_properties = {
        "java.awt.headless": "true",
        "foundation.dump": "true",
        "workbench.worldgen.observatory.fixture_driver.enabled": "true",
        "workbench.worldgen.observatory.fixture_driver.order": str(settings["fixture_route_order"]),
        "workbench.worldgen.observatory.fixture_driver.expected_seed": str(settings["fixture_expected_seed"]),
        "workbench.worldgen.observatory.fixture_driver.result": str(case / "fixture-result.json"),
        "workbench.worldgen.observatory.probe.enabled": "true" if settings["probe_enabled"] else "false",
    }
    if settings["probe_enabled"]:
        expected_properties.update(
            {
                "workbench.worldgen.observatory.probe.capture_id": str(settings["probe_capture_id"]),
                "workbench.worldgen.observatory.probe.mode": str(settings["probe_mode"]),
                "workbench.worldgen.observatory.probe.output": str(case / "raw.ndjson"),
            }
        )
    _require(properties == expected_properties, "JAVA_TOOL_OPTIONS properties do not exactly match the case configuration")

    success_lines = [line for line in lines if _GRADLE_SUCCESS_RE.fullmatch(line) is not None]
    failure_lines = [line for line in lines if _GRADLE_FAILURE_RE.fullmatch(line) is not None]
    exit_matches = [int(match.group(1)) for match in _CHILD_EXIT_RE.finditer(text)]
    if outcome == "completed":
        _require(len(success_lines) == 1 and not failure_lines, "completed case lacks one clean BUILD SUCCESSFUL record")
        _require(not exit_matches, "completed case contains child non-zero-exit evidence")
        gradle = {
            "terminal_status": "BUILD SUCCESSFUL",
            "exit_code": 0,
            "exit_code_evidence": "gradle_success_terminal_record",
        }
    else:
        _require(len(failure_lines) == 1 and not success_lines, "crash case lacks one clean BUILD FAILED record")
        _require(exit_matches == [137], "crash case must contain exactly one textual child exit 137 record")
        evidence_lines = [line for line in lines if _CHILD_EXIT_RE.search(line) is not None]
        _require(len(evidence_lines) == 1, "crash child-exit evidence line count mismatch")
        gradle = {
            "terminal_status": "BUILD FAILED",
            "child_exit_code_from_text": 137,
            "child_exit_evidence_line_sha256": hashlib.sha256(
                evidence_lines[0].encode("utf-8")
            ).hexdigest(),
            "provenance": "launch_log_text_only",
        }
    return receipt, {
        "java_tool_options_occurrences": 2,
        "java_tool_options_line_sha256": hashlib.sha256(java_lines[0].encode("utf-8")).hexdigest(),
        "java_properties_sha256": canonical_json_sha256(properties),
        "gradle": gradle,
    }


def _raw_receipt(path: Path, *, expected_sha256: str) -> _RawReceipt:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect raw NDJSON {path}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"raw NDJSON must not be a symlink: {path}")
    _require(stat.S_ISREG(before.st_mode), f"raw NDJSON is not a regular file: {path}")
    _require(0 < before.st_size <= _MAX_RAW_BYTES, "raw NDJSON size is outside audit bounds")
    expected_identity = _identity(before)
    digest = hashlib.sha256()
    terminal: deque[bytes] = deque(maxlen=2)
    first: bytes | None = None
    row_count = 0
    stop_count = 0
    completion_count = 0
    try:
        with path.open("rb") as stream:
            while True:
                line = stream.readline(_MAX_RAW_LINE_BYTES + 1)
                if not line:
                    break
                _require(len(line) <= _MAX_RAW_LINE_BYTES, "raw NDJSON line exceeds audit bounds")
                _require(line.endswith(b"\n"), "raw NDJSON contains a truncated final line")
                _require(line.strip(), "raw NDJSON contains a blank line")
                row_count += 1
                _require(row_count <= _MAX_RAW_ROWS, "raw NDJSON row count exceeds audit bounds")
                if first is None:
                    first = line
                terminal.append(line)
                stop_count += line.count(b'"control":"stop"')
                completion_count += line.count(
                    b'"completion_marker":"dedicated_server_fixture_complete_v1"'
                )
                digest.update(line)
        after = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot read raw NDJSON {path}: {exc}") from exc
    _require(_identity(after) == expected_identity, "raw NDJSON changed while hashing")
    observed_sha256 = digest.hexdigest()
    _require(observed_sha256 == expected_sha256, "raw NDJSON digest mismatch")
    _require(first is not None and terminal, "raw NDJSON is empty")
    first_row = _parse_json(first, context="first raw NDJSON row")
    last_row = _parse_json(terminal[-1], context="last raw NDJSON row")
    penultimate_row = (
        _parse_json(terminal[-2], context="penultimate raw NDJSON row")
        if len(terminal) == 2
        else None
    )
    for value, label in ((first_row, "first"), (last_row, "last")):
        _require(isinstance(value, Mapping), f"{label} raw NDJSON row must be an object")
    if penultimate_row is not None:
        _require(isinstance(penultimate_row, Mapping), "penultimate raw NDJSON row must be an object")
    return _RawReceipt(
        file=_FileReceipt(path, before.st_size, observed_sha256, expected_identity),
        row_count=row_count,
        first_row=first_row,
        penultimate_row=penultimate_row,
        last_row=last_row,
        stop_token_count=stop_count,
        completion_token_count=completion_count,
    )


def _verify_raw_controls(
    raw: _RawReceipt,
    *,
    settings: Mapping[str, Any],
    outcome: str,
    expected_result_sha256: str | None,
) -> dict[str, Any]:
    capture_id = settings["probe_capture_id"]
    first = raw.first_row
    _require(first.get("record_type") == "capture_control", "raw capture does not start with control")
    _require(first.get("sequence") == 0, "raw capture does not start at sequence zero")
    _require(first.get("capture_id") == capture_id, "raw start capture ID mismatch")
    first_payload = first.get("payload")
    _require(isinstance(first_payload, Mapping), "raw start payload is missing")
    _require(first_payload.get("control") == "start", "raw capture lacks the start control")
    _require(first_payload.get("requested_mode") == settings["probe_mode"], "raw start mode mismatch")
    _require(first_payload.get("world_seed_sha256") == FIXED_WORLD_SEED_SHA256, "raw start seed mismatch")
    _require(first_payload.get("route_order") == settings["fixture_route_order"], "raw start route mismatch")

    if outcome == "completed":
        _require(raw.stop_token_count == 1, "completed raw capture must contain exactly one stop control")
        _require(raw.completion_token_count == 1, "completed raw capture must contain exactly one fixture completion marker")
        _require(raw.penultimate_row is not None, "completed raw capture lacks a fixture completion row")
        completion = raw.penultimate_row
        _require(completion.get("record_type") == "fixture_driver", "penultimate raw row is not fixture completion")
        completion_payload = completion.get("payload")
        _require(isinstance(completion_payload, Mapping), "fixture completion payload is missing")
        _require(completion_payload.get("action") == "complete", "fixture completion action mismatch")
        _require(completion_payload.get("save_state") == "flushed", "fixture completion was not flushed")
        _require(completion_payload.get("route_order") == settings["fixture_route_order"], "fixture completion route mismatch")
        _require(completion_payload.get("result_identity_sha256") == expected_result_sha256, "fixture completion result identity mismatch")
        last = raw.last_row
        _require(last.get("record_type") == "capture_control", "completed raw capture does not end with control")
        _require(last.get("capture_id") == capture_id, "raw stop capture ID mismatch")
        _require(last.get("sequence") == raw.row_count - 1, "raw terminal sequence does not match row count")
        last_payload = last.get("payload")
        _require(isinstance(last_payload, Mapping), "raw stop payload is missing")
        _require(last_payload.get("control") == "stop", "completed raw capture does not end with stop")
        _require(last_payload.get("completion_state") == "complete", "raw stop is not complete")
        _require(last_payload.get("open_span_count") == 0, "raw stop has open spans")
        _require(last_payload.get("open_write_count") == 0, "raw stop has open writes")
        terminal_state = "complete_and_stopped"
    else:
        _require(raw.stop_token_count == 0, "crash raw capture contains a stop control")
        _require(raw.completion_token_count == 0, "crash raw capture contains a fixture completion marker")
        _require(
            not (
                raw.last_row.get("record_type") == "capture_control"
                and isinstance(raw.last_row.get("payload"), Mapping)
                and raw.last_row["payload"].get("control") == "stop"
            ),
            "crash raw capture ends with a stop control",
        )
        terminal_state = "incomplete_without_stop"
    return {
        "capture_id": capture_id,
        "row_count": raw.row_count,
        "terminal_sequence": raw.last_row.get("sequence"),
        "terminal_state": terminal_state,
        "stop_control_count": raw.stop_token_count,
        "fixture_completion_marker_count": raw.completion_token_count,
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def audit_session(
    *,
    case_id: str,
    outcome: str,
    case_directory: str | os.PathLike[str],
    candidate_lock: str | os.PathLike[str],
    expected_candidate_lock_sha256: str,
    fixture_artifact: str | os.PathLike[str],
    expected_fixture_artifact_sha256: str,
    installed_mod_set_manifest: str | os.PathLike[str],
    expected_installed_mod_set_sha256: str,
    configuration_set_manifest: str | os.PathLike[str],
    expected_configuration_set_sha256: str,
    expected_launch_log_sha256: str,
    expected_raw_sha256: str | None,
    expected_result_sha256: str | None,
    expected_runtime_inventory_sha256: str | None,
    expected_semantic_result_sha256: str | None,
    output_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Verify one exact-runtime session and atomically write its receipt."""

    _require(isinstance(case_id, str) and _CASE_ID_RE.fullmatch(case_id) is not None, "invalid case ID")
    _require(outcome in {"completed", "crash"}, "outcome must be completed or crash")
    candidate_sha = _sha256(expected_candidate_lock_sha256, "expected candidate lock digest")
    fixture_sha = _sha256(expected_fixture_artifact_sha256, "expected fixture artifact digest")
    mod_set_sha = _sha256(expected_installed_mod_set_sha256, "expected installed mod-set digest")
    config_sha = _sha256(expected_configuration_set_sha256, "expected configuration-set digest")
    launch_sha = _sha256(expected_launch_log_sha256, "expected launch-log digest")
    for value, context in (
        (expected_raw_sha256, "expected raw digest"),
        (expected_result_sha256, "expected result digest"),
        (expected_runtime_inventory_sha256, "expected runtime inventory digest"),
        (expected_semantic_result_sha256, "expected semantic result digest"),
    ):
        if value is not None:
            _sha256(value, context)

    case = _plain_directory(_absolute_path(case_directory, "case directory"), "case directory")
    _require(case.name == case_id, "case directory basename does not match case ID")
    server = _plain_directory(case / "server", "case server directory")
    output = _absolute_path(output_path, "output path")
    _require(output.resolve(strict=False) not in {case, server}, "output path aliases a runtime directory")
    _require(case not in output.resolve(strict=False).parents, "audit receipt must be written outside the runtime case")

    candidate_path = _absolute_path(candidate_lock, "candidate lock path")
    candidate_value, candidate_receipt = _load_strict_json(candidate_path, context="candidate lock")
    _require(isinstance(candidate_value, Mapping), "candidate lock must be an object")
    _require(candidate_receipt.sha256 == candidate_sha, "candidate lock digest mismatch")

    fixture_path = _absolute_path(fixture_artifact, "fixture artifact path")
    fixture_receipt = _hash_regular_file(
        fixture_path, context="fixture artifact", maximum_size=_MAX_FIXTURE_ARTIFACT_BYTES
    )
    _require(fixture_receipt.sha256 == fixture_sha, "fixture artifact digest mismatch")

    mod_manifest_path = _absolute_path(installed_mod_set_manifest, "installed mod-set manifest path")
    mod_manifest, mod_manifest_receipt = _load_strict_json(
        mod_manifest_path, context="installed mod-set manifest"
    )
    mod_manifest_canonical_sha = canonical_json_sha256(mod_manifest)
    _require(mod_manifest_canonical_sha == mod_set_sha, "installed mod-set canonical digest mismatch")
    runtime_class_source_sha, installed_artifacts = _verify_installed_mod_manifest(
        mod_manifest,
        server=server,
        candidate_lock_sha256=candidate_sha,
        fixture_artifact_sha256=fixture_sha,
    )

    config_manifest_path = _absolute_path(configuration_set_manifest, "configuration-set manifest path")
    config_manifest, config_manifest_receipt = _load_strict_json(
        config_manifest_path, context="configuration-set manifest"
    )
    config_manifest_canonical_sha = canonical_json_sha256(config_manifest)
    _require(config_manifest_canonical_sha == config_sha, "configuration-set canonical digest mismatch")
    settings, configuration_files = _verify_configuration_manifest(
        config_manifest, case_id=case_id, server=server
    )

    launch_receipt, launch_evidence = _verify_launch_log(
        case / "launch.log",
        expected_sha256=launch_sha,
        case=case,
        settings=settings,
        outcome=outcome,
    )

    probe_enabled = bool(settings["probe_enabled"])
    raw_path = case / "raw.ndjson"
    raw_receipt: _RawReceipt | None = None
    raw_evidence: dict[str, Any] | None = None
    if probe_enabled:
        _require(expected_raw_sha256 is not None, "enabled probe requires an expected raw digest")
        _require(os.path.lexists(raw_path), "enabled probe raw NDJSON is absent")
        raw_receipt = _raw_receipt(raw_path, expected_sha256=expected_raw_sha256)
        raw_evidence = _verify_raw_controls(
            raw_receipt,
            settings=settings,
            outcome=outcome,
            expected_result_sha256=expected_result_sha256,
        )
    else:
        _require(expected_raw_sha256 is None, "disabled probe forbids an expected raw digest")
        _require(not os.path.lexists(raw_path), "disabled probe unexpectedly produced raw NDJSON")
        _require(outcome == "completed", "observer-off case must be completed")

    result_path = case / "fixture-result.json"
    fixture_result = None
    result_receipt: _FileReceipt | None = None
    if outcome == "completed":
        _require(expected_result_sha256 is not None, "completed case requires an expected result digest")
        _require(expected_runtime_inventory_sha256 is not None, "completed case requires an expected runtime inventory digest")
        _require(expected_semantic_result_sha256 is not None, "completed case requires an expected semantic result digest")
        _require(os.path.lexists(result_path), "completed case fixture result is absent")
        result_receipt = _hash_regular_file(
            result_path, context="fixture result", maximum_size=_MAX_SMALL_JSON_BYTES
        )
        _require(result_receipt.sha256 == expected_result_sha256, "fixture result digest mismatch")
        fixture_result = load_fixture_result(result_path)
        _require(fixture_result.artifact_sha256 == result_receipt.sha256, "fixture result admission did not bind the exact bytes")
        _require(fixture_result.route_order == settings["fixture_route_order"], "fixture result route differs from launch")
        inventory_sha = fixture_result.inventory_sha256()
        semantic_sha = fixture_result.semantic_map_sha256()
        _require(inventory_sha == expected_runtime_inventory_sha256, "runtime inventory digest mismatch")
        _require(semantic_sha == expected_semantic_result_sha256, "semantic result digest mismatch")
        available_sources = {
            row.source_sha256
            for row in fixture_result.runtime_mod_inventory
            if row.source_sha256 != "unavailable"
        }
        _require(runtime_class_source_sha in available_sources, "runtime class source is absent from admitted inventory")
        _require(
            {row["sha256"] for row in installed_artifacts} <= available_sources,
            "installed artifact source is absent from admitted runtime inventory",
        )
    else:
        _require(expected_result_sha256 is None, "crash case forbids an expected result digest")
        _require(expected_runtime_inventory_sha256 is None, "crash case forbids an expected runtime inventory digest")
        _require(expected_semantic_result_sha256 is None, "crash case forbids an expected semantic result digest")
        _require(not os.path.lexists(result_path), "crash case unexpectedly contains a fixture result")

    dump_directory = locate_foundation_class_dump(case)
    dump_manifest = build_foundation_class_dump_manifest(dump_directory)

    for receipt, context in (
        (candidate_receipt, "candidate lock"),
        (fixture_receipt, "fixture artifact"),
        (mod_manifest_receipt, "installed mod-set manifest"),
        (config_manifest_receipt, "configuration-set manifest"),
        (launch_receipt, "launch log"),
    ):
        _require_unchanged(receipt, context)
    if raw_receipt is not None:
        _require_unchanged(raw_receipt.file, "raw NDJSON")
    if result_receipt is not None:
        _require_unchanged(result_receipt, "fixture result")

    result_material = None
    if fixture_result is not None and result_receipt is not None:
        result_material = {
            "file_sha256": result_receipt.sha256,
            "canonical_document_sha256": fixture_result.canonical_document_sha256,
            "completion_state": "complete",
            "save_state": "flushed",
            "shutdown_state": "requested",
            "route_order": fixture_result.route_order,
            "route_sha256": fixture_result.route_sha256,
            "semantic_result_sha256": fixture_result.semantic_map_sha256(),
            "runtime_inventory_sha256": fixture_result.inventory_sha256(),
            "runtime_inventory_count": len(fixture_result.runtime_mod_inventory),
        }
    raw_material = None
    if raw_receipt is not None and raw_evidence is not None:
        raw_material = {
            "file_sha256": raw_receipt.file.sha256,
            "size_bytes": raw_receipt.file.size,
            **raw_evidence,
        }

    material: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "case_id": case_id,
        "outcome": outcome,
        "observer_enabled": probe_enabled,
        "candidate_lock": {
            "file_sha256": candidate_receipt.sha256,
            "canonical_sha256": canonical_json_sha256(candidate_value),
        },
        "fixture_artifact": {
            "file_sha256": fixture_receipt.sha256,
            "size_bytes": fixture_receipt.size,
        },
        "installed_mod_set": {
            "file_sha256": mod_manifest_receipt.sha256,
            "canonical_sha256": mod_manifest_canonical_sha,
            "runtime_class_source_sha256": runtime_class_source_sha,
            "verified_artifacts": installed_artifacts,
        },
        "configuration_set": {
            "file_sha256": config_manifest_receipt.sha256,
            "canonical_sha256": config_manifest_canonical_sha,
            "verified_files": configuration_files,
            "runtime_settings_sha256": canonical_json_sha256(settings),
        },
        "launch_log": {
            "file_sha256": launch_receipt.sha256,
            "size_bytes": launch_receipt.size,
            **launch_evidence,
        },
        "raw_capture": raw_material,
        "fixture_result": result_material,
        "foundation_class_dump": {
            "format": dump_manifest.format,
            "class_count": dump_manifest.class_count,
            "total_size_bytes": dump_manifest.total_size_bytes,
            "manifest_sha256": dump_manifest.manifest_sha256,
        },
    }
    receipt = {"audit_id": RECEIPT_PREFIX + canonical_json_sha256(material), **material}
    _atomic_write_json(output, receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--outcome", choices=("completed", "crash"), required=True)
    parser.add_argument("--case-directory", required=True)
    parser.add_argument("--candidate-lock", required=True)
    parser.add_argument("--expected-candidate-lock-sha256", required=True)
    parser.add_argument("--fixture-artifact", required=True)
    parser.add_argument("--expected-fixture-artifact-sha256", required=True)
    parser.add_argument("--installed-mod-set-manifest", required=True)
    parser.add_argument("--expected-installed-mod-set-sha256", required=True)
    parser.add_argument("--configuration-set-manifest", required=True)
    parser.add_argument("--expected-configuration-set-sha256", required=True)
    parser.add_argument("--expected-launch-log-sha256", required=True)
    parser.add_argument("--expected-raw-sha256")
    parser.add_argument("--expected-result-sha256")
    parser.add_argument("--expected-runtime-inventory-sha256")
    parser.add_argument("--expected-semantic-result-sha256")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = audit_session(
            case_id=arguments.case_id,
            outcome=arguments.outcome,
            case_directory=arguments.case_directory,
            candidate_lock=arguments.candidate_lock,
            expected_candidate_lock_sha256=arguments.expected_candidate_lock_sha256,
            fixture_artifact=arguments.fixture_artifact,
            expected_fixture_artifact_sha256=arguments.expected_fixture_artifact_sha256,
            installed_mod_set_manifest=arguments.installed_mod_set_manifest,
            expected_installed_mod_set_sha256=arguments.expected_installed_mod_set_sha256,
            configuration_set_manifest=arguments.configuration_set_manifest,
            expected_configuration_set_sha256=arguments.expected_configuration_set_sha256,
            expected_launch_log_sha256=arguments.expected_launch_log_sha256,
            expected_raw_sha256=arguments.expected_raw_sha256,
            expected_result_sha256=arguments.expected_result_sha256,
            expected_runtime_inventory_sha256=arguments.expected_runtime_inventory_sha256,
            expected_semantic_result_sha256=arguments.expected_semantic_result_sha256,
            output_path=arguments.output,
        )
    except CaptureValidationError as exc:
        print(f"session audit failed: {exc}", file=sys.stderr)
        return 2
    print(receipt["audit_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
