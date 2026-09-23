#!/usr/bin/env python3
"""Import one exact Cleanroom 0.6.8-alpha configuration lifecycle capture."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Any
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[4]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(CRUCIBLE_SOURCE))
sys.path.insert(0, str(TOOLS))

from import_cleanmix_defining_loader_discovery_trace import (  # noqa: E402
    EXPECTED_CLEANMIX_SHA256,
    EXPECTED_LOADER,
    PROFILE_ID,
    CleanroomDiscoveryTraceImportError,
    _validate_candidate_lock,
    _validate_fixture_result,
    _validate_launch,
    _validate_toolchain_lock,
)
from workbench_crucible_mixins import (  # noqa: E402
    ConfigLifecycleValidationError,
    build_config_lifecycle_receipt,
    parse_config_lifecycle_receipt,
    parse_raw_config_lifecycle,
    write_config_lifecycle_receipt,
)


EXPECTED_TARGETS = {
    "org.spongepowered.asm.mixin.Mixins":
        "7d2ff2c3cb1780c2cb6273ab5426869104b6e8ac7db961033bc1156c11038d74",
    "org.spongepowered.asm.mixin.transformer.Config":
        "7ad7a697935397fff8dc8fe63012615e6b28a9ec3d9df88c2bdcfcea313ee7ca",
    "org.spongepowered.asm.mixin.transformer.MixinConfig":
        "9e0c60d18374facbc6ca6ab429792f81d1ebfb54765fdb32c3b26f6c3d623f47",
    "org.spongepowered.asm.mixin.transformer.MixinProcessor":
        "426ea93ecb32d50f5ca7d4dfcbea9a20f964c6723d6cc89f1e802902942d1b4c",
}
EXPECTED_CONFIG = "mixins.workbench_worldgen_observatory.early.json"
EXPECTED_OWNER = "workbench_worldgen_observatory"


class CleanroomConfigLifecycleImportError(ValueError):
    """Evidence does not match the exact Cleanroom candidate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanroomConfigLifecycleImportError(message)


def _read_file(path: Path, context: str) -> bytes:
    _require(not path.is_symlink(), f"{context} cannot be a symlink: {path}")
    _require(path.is_file(), f"{context} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CleanroomConfigLifecycleImportError(
            f"cannot read {context}: {exc}"
        ) from exc


def _file_record(path: Path, context: str) -> dict[str, Any]:
    encoded = _read_file(path, context)
    return {
        "label": path.name,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    }


def _local_path(uri: str) -> Path:
    source = uri
    if source.startswith("jar:"):
        source = source[4:].split("!/", 1)[0]
    parsed = urlsplit(source)
    _require(parsed.scheme == "file", f"evidence URI is not local: {uri}")
    _require(parsed.netloc in {"", "localhost"}, f"evidence URI has remote authority: {uri}")
    _require(not parsed.query and not parsed.fragment, f"evidence URI has decorations: {uri}")
    path = Path(unquote(parsed.path))
    _require(path.is_absolute(), f"evidence URI is not absolute: {uri}")
    _require(not path.is_symlink(), f"evidence artifact cannot be a symlink: {path}")
    _require(path.is_file(), f"evidence artifact is not a regular file: {path}")
    return path.resolve(strict=True)


def _artifact_record(uri: str) -> dict[str, Any]:
    path = _local_path(uri)
    encoded = _read_file(path, f"artifact {uri}")
    return {
        "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "label": path.name,
        "code_source_uri": uri,
    }


def _resource_entry(resource_url: str) -> tuple[Path, str, bytes]:
    _require(resource_url.startswith("jar:"), "config resource URL is not a JAR URL")
    parts = resource_url[4:].split("!/", 1)
    _require(len(parts) == 2 and bool(parts[1]), "config resource URL lacks an archive entry")
    path = _local_path(parts[0])
    entry = parts[1]
    _require(entry == EXPECTED_CONFIG, "config resource URL names an unexpected archive entry")
    try:
        with ZipFile(path) as archive:
            _require(entry in archive.namelist(), "config resource archive does not contain the requested entry")
            encoded = archive.read(entry)
    except (BadZipFile, OSError) as exc:
        raise CleanroomConfigLifecycleImportError(
            f"cannot verify config resource archive: {exc}"
        ) from exc
    return path, entry, encoded


def _validate_exact_rows(rows: list[dict[str, Any]]) -> None:
    _require(rows[-1]["payload"]["health"] == "healthy", "config lifecycle capture is not healthy")
    expected = {
        row["target_class"]: row["expected_input_sha256"]
        for row in rows[0]["payload"]["targets"]
    }
    _require(expected == EXPECTED_TARGETS, "config lifecycle exact class-byte guards drifted")
    transforms = [row["payload"] for row in rows if row["event"] == "transform_applied"]
    _require(
        all(row["defining_loader_class"] == EXPECTED_LOADER for row in transforms),
        "config lifecycle targets were not defined by the exact Foundation LaunchClassLoader",
    )

    decisions = [row["payload"] for row in rows if row["event"] == "admission_decision"]
    admitted = [row for row in decisions if row["decision"] == "admitted"]
    _require(len(admitted) == 1, "exact config lifecycle capture did not admit exactly one config")
    config = admitted[0]
    _require(config["requested_config"] == EXPECTED_CONFIG, "exact capture admitted an unexpected config")
    _require(config["source_id"] == EXPECTED_OWNER, "exact config owner is not the fixture mod")
    _require(config["required"] is True, "exact config did not preserve required=true")
    _require(config["queued_phase"] == "DEFAULT", "exact config queued in an unexpected phase")

    checks = [
        row["payload"] for row in rows
        if row["event"] == "phase_eligibility" and row["payload"]["attempt"] == config["attempt"]
    ]
    _require(
        [(row["consumption_phase"], row["eligible"]) for row in checks]
        == [("PREINIT", False), ("INIT", False), ("DEFAULT", True)],
        "exact config phase custody does not show PREINIT/INIT deferral then DEFAULT consumption",
    )
    stages = [
        (row["payload"]["stage"], row["payload"]["outcome"])
        for row in rows if row["event"] == "config_stage"
        and row["payload"]["attempt"] == config["attempt"]
    ]
    _require(
        stages == [
            ("onSelect", "started"), ("onSelect", "completed"),
            ("prepare", "started"), ("prepare", "completed"),
            ("post_initialise", "started"), ("post_initialise", "completed"),
        ],
        "exact config preparation stages are incomplete or reordered",
    )
    terminal = next(
        row["payload"] for row in rows
        if row["event"] == "terminal_state" and row["payload"]["attempt"] == config["attempt"]
    )
    _require(terminal["outcome"] == "active", "exact admitted config did not become active")


def import_exact_cleanmix_config_lifecycle(
    *,
    raw_trace_path: Path,
    candidate_lock_path: Path,
    toolchain_lock_path: Path,
    observer_agent_path: Path,
    launch_log_path: Path,
    fixture_result_path: Path,
    source_config_path: Path,
    launch_id: str,
) -> dict[str, Any]:
    candidate_bytes = _read_file(candidate_lock_path, "candidate lock")
    toolchain_bytes = _read_file(toolchain_lock_path, "toolchain lock")
    raw_bytes = _read_file(raw_trace_path, "raw lifecycle trace")
    launch_bytes = _read_file(launch_log_path, "launch log")
    fixture_bytes = _read_file(fixture_result_path, "fixture result")
    source_config_bytes = _read_file(source_config_path, "source mixin config")
    _read_file(observer_agent_path, "observer agent")
    try:
        _validate_candidate_lock(candidate_bytes)
        _validate_toolchain_lock(toolchain_bytes, EXPECTED_CLEANMIX_SHA256)
        _validate_launch(launch_bytes)
        _validate_fixture_result(fixture_bytes)
    except CleanroomDiscoveryTraceImportError as exc:
        raise CleanroomConfigLifecycleImportError(str(exc)) from exc
    try:
        rows = parse_raw_config_lifecycle(raw_bytes)
    except ConfigLifecycleValidationError as exc:
        raise CleanroomConfigLifecycleImportError(
            f"raw config lifecycle capture failed semantic validation: {exc}"
        ) from exc
    _validate_exact_rows(rows)

    artifact_uris = {
        row["payload"]["target_code_source_uri"]
        for row in rows if row["event"] == "transform_applied"
    }
    artifacts = [_artifact_record(uri) for uri in sorted(artifact_uris)]
    _require(
        {row["artifact_sha256"] for row in artifacts} == {EXPECTED_CLEANMIX_SHA256},
        "instrumented config lifecycle targets are not exact CleanMix 0.7.0",
    )

    resource_evidence: list[dict[str, Any]] = []
    resource_uris: set[str] = set()
    admitted_attempts = {
        row["payload"]["attempt"]
        for row in rows
        if row["event"] == "admission_decision"
        and row["payload"]["decision"] == "admitted"
    }
    for row in rows:
        if row["event"] != "config_create_started":
            continue
        payload = row["payload"]
        resource_url = payload["resolved_resource_url"]
        if resource_url is None or payload["attempt"] not in admitted_attempts:
            continue
        path, _, entry_bytes = _resource_entry(resource_url)
        _require(
            entry_bytes == source_config_bytes
            or (
                entry_bytes.rstrip(b"\n") == source_config_bytes.rstrip(b"\n")
                and entry_bytes.endswith(b"}")
                and source_config_bytes.endswith(b"}\n")
            ),
            "installed config resource differs from the bound fixture source beyond the production JAR's terminal-newline normalization",
        )
        artifact_bytes = _read_file(path, "config owner artifact")
        resource_evidence.append(
            {
                "attempt": payload["attempt"],
                "resource_url": resource_url,
                "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "resource_entry_sha256": hashlib.sha256(entry_bytes).hexdigest(),
            }
        )
        source_description = payload["source_description"]
        _require(source_description is not None, "resolved config resource lacks owner description")
        resource_uris.add(source_description)
    _require(bool(resource_evidence), "exact capture lacks a verified config resource")
    artifacts.extend(_artifact_record(uri) for uri in sorted(resource_uris))

    candidate_record = _file_record(candidate_lock_path, "candidate lock")
    toolchain_record = _file_record(toolchain_lock_path, "toolchain lock")
    try:
        receipt = build_config_lifecycle_receipt(
            session={
                "capture_id": rows[0]["capture_id"],
                "launch_id": launch_id,
                "profile_id": PROFILE_ID,
                "side": "dedicated_server",
                "candidate_lock_sha256": candidate_record["sha256"],
                "toolchain_lock_sha256": toolchain_record["sha256"],
            },
            inputs={
                "agent_artifact": _file_record(observer_agent_path, "observer agent"),
                "candidate_lock": candidate_record,
                "fixture_result": _file_record(fixture_result_path, "fixture result"),
                "launch_log": _file_record(launch_log_path, "launch log"),
                "raw_trace": _file_record(raw_trace_path, "raw lifecycle trace"),
                "toolchain_lock": toolchain_record,
            },
            artifacts=artifacts,
            resource_evidence=resource_evidence,
            raw_events=rows,
            limitations=(
                "This exact producer is bound to Cleanroom 0.6.8-alpha with embedded CleanMix 0.7.0.",
                "The retained vertical slice exercises one DEFAULT config on a dedicated server; other phases, sides, and failure outcomes remain future matrix rows.",
                "The production JAR normalizes away the source config's terminal newline; the importer admits only that exact byte difference and hashes the installed archive entry itself.",
            ),
        )
        return parse_config_lifecycle_receipt(receipt)
    except ConfigLifecycleValidationError as exc:
        raise CleanroomConfigLifecycleImportError(
            f"config lifecycle receipt construction failed: {exc}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-trace", required=True, type=Path)
    parser.add_argument("--candidate-lock", required=True, type=Path)
    parser.add_argument("--toolchain-lock", required=True, type=Path)
    parser.add_argument("--observer-agent", required=True, type=Path)
    parser.add_argument("--launch-log", required=True, type=Path)
    parser.add_argument("--fixture-result", required=True, type=Path)
    parser.add_argument("--source-config", required=True, type=Path)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipt = import_exact_cleanmix_config_lifecycle(
            raw_trace_path=arguments.raw_trace,
            candidate_lock_path=arguments.candidate_lock,
            toolchain_lock_path=arguments.toolchain_lock,
            observer_agent_path=arguments.observer_agent,
            launch_log_path=arguments.launch_log,
            fixture_result_path=arguments.fixture_result,
            source_config_path=arguments.source_config,
            launch_id=arguments.launch_id,
        )
        write_config_lifecycle_receipt(arguments.output, receipt)
    except (CleanroomConfigLifecycleImportError, OSError) as exc:
        print(f"Cleanroom config lifecycle import: {exc}", file=sys.stderr)
        return 2
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
