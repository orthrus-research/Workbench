#!/usr/bin/env python3
"""Import one exact Cleanroom 0.6.8-alpha selected-service component capture."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Any
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[4]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(CRUCIBLE_SOURCE))
sys.path.insert(0, str(TOOLS))

from import_cleanmix_defining_loader_discovery_trace import (  # noqa: E402
    EXPECTED_CLEANMIX_SHA256,
    EXPECTED_LOADER,
    EXPECTED_MIXIN_SERVICE_CLASS_SHA256,
    PROFILE_ID,
    CleanroomDiscoveryTraceImportError,
    _validate_candidate_lock,
    _validate_fixture_result,
    _validate_launch,
    _validate_toolchain_lock,
)
from workbench_crucible_mixins import (  # noqa: E402
    ServiceComponentsValidationError,
    build_service_components_receipt,
    parse_raw_service_components,
    parse_service_components_receipt,
    write_service_components_receipt,
)


EXPECTED_COMPONENT_CLASSES = {
    "audit_trail": "org.spongepowered.asm.service.mojang.MixinAuditTrailRecorder",
    "bytecode_provider": "com.cleanroommc.cleanmix.service.FoundationBytecodeProvider",
    "class_provider": "com.cleanroommc.cleanmix.service.FoundationClassProvider",
    "class_tracker": "com.cleanroommc.cleanmix.service.FoundationClassTracker",
    "logger": "org.spongepowered.asm.service.mojang.Log4j2AuditingAdapter",
    "service": "com.cleanroommc.cleanmix.service.CleanMixService",
    "service_classloader": "net.minecraft.launchwrapper.LaunchClassLoader",
    "transformer_provider": "com.cleanroommc.cleanmix.service.FoundationTransformerProvider",
}


class CleanroomServiceComponentsImportError(ValueError):
    """Evidence does not match the exact Cleanroom candidate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanroomServiceComponentsImportError(message)


def _read_file(path: Path, context: str) -> bytes:
    _require(not path.is_symlink(), f"{context} cannot be a symlink: {path}")
    _require(path.is_file(), f"{context} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CleanroomServiceComponentsImportError(
            f"cannot read {context}: {exc}"
        ) from exc


def _file_record(path: Path, context: str) -> dict[str, Any]:
    encoded = _read_file(path, context)
    return {
        "label": path.name,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    }


def _artifact_path(code_source_uri: str) -> Path:
    source = code_source_uri
    if source.startswith("jar:"):
        source = source[4:]
        source = source.split("!/", 1)[0]
    parsed = urlsplit(source)
    _require(parsed.scheme == "file", f"component code source is not local file evidence: {code_source_uri}")
    _require(
        parsed.netloc in {"", "localhost"},
        f"component code source has a remote authority: {code_source_uri}",
    )
    _require(not parsed.query and not parsed.fragment, f"component code source has URL decorations: {code_source_uri}")
    path = Path(unquote(parsed.path))
    _require(path.is_absolute(), f"component code source is not absolute: {code_source_uri}")
    _require(not path.is_symlink(), f"component code source cannot be a symlink: {path}")
    _require(path.is_file(), f"component code source is not a regular artifact: {path}")
    return path.resolve(strict=True)


def _artifact_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uris = {
        row["payload"]["code_source_uri"]
        for row in rows
        if row["event"] == "component_observed"
    }
    uris.update(
        row["payload"]["target_code_source_uri"]
        for row in rows
        if row["event"] == "transform_applied"
    )
    records: list[dict[str, Any]] = []
    for uri in sorted(uris):
        path = _artifact_path(uri)
        encoded = _read_file(path, f"component artifact {uri}")
        records.append(
            {
                "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
                "size_bytes": len(encoded),
                "label": path.name,
                "code_source_uri": uri,
            }
        )
    return records


def _validate_exact_components(rows: list[dict[str, Any]]) -> None:
    _require(rows[-1]["payload"]["health"] == "healthy", "component capture is not healthy")
    transforms = [row for row in rows if row["event"] == "transform_applied"]
    _require(len(transforms) == 1, "component capture did not apply one exact substitution")
    transform = transforms[0]["payload"]
    _require(
        transform["input_sha256"] == EXPECTED_MIXIN_SERVICE_CLASS_SHA256,
        "component capture did not transform exact CleanMix 0.7.0 MixinService bytes",
    )
    _require(
        transform["defining_loader_class"] == EXPECTED_LOADER,
        "component capture did not observe the Foundation LaunchClassLoader definition",
    )
    components = {
        row["payload"]["role"]: row["payload"]
        for row in rows
        if row["event"] == "component_observed"
    }
    actual_classes = {
        role: row["implementation_class"] for role, row in components.items()
    }
    _require(
        actual_classes == EXPECTED_COMPONENT_CLASSES,
        "selected component classes do not match the exact CleanMix service: "
        + repr(actual_classes),
    )
    _require(
        components["service"]["reported_name"] == "CleanMix",
        "selected CleanMix service did not report its exact name",
    )
    _require(
        components["service"]["implementation_loader_identity"]
        == transform["defining_loader_identity"],
        "selected service and transformed MixinService used different defining loaders",
    )
    _require(
        components["service_classloader"]["object_identity"]
        == transform["defining_loader_identity"],
        "selected service did not expose the transformed MixinService defining loader",
    )


def import_exact_cleanmix_service_components(
    *,
    raw_trace_path: Path,
    candidate_lock_path: Path,
    toolchain_lock_path: Path,
    observer_agent_path: Path,
    launch_log_path: Path,
    fixture_result_path: Path,
    launch_id: str,
) -> dict[str, Any]:
    candidate_bytes = _read_file(candidate_lock_path, "candidate lock")
    toolchain_bytes = _read_file(toolchain_lock_path, "toolchain lock")
    raw_bytes = _read_file(raw_trace_path, "raw component trace")
    launch_bytes = _read_file(launch_log_path, "launch log")
    fixture_bytes = _read_file(fixture_result_path, "fixture result")
    _read_file(observer_agent_path, "observer agent")
    try:
        _validate_candidate_lock(candidate_bytes)
        _validate_launch(launch_bytes)
        _validate_fixture_result(fixture_bytes)
    except CleanroomDiscoveryTraceImportError as exc:
        raise CleanroomServiceComponentsImportError(str(exc)) from exc
    try:
        rows = parse_raw_service_components(raw_bytes)
    except ServiceComponentsValidationError as exc:
        raise CleanroomServiceComponentsImportError(
            f"raw component capture failed semantic validation: {exc}"
        ) from exc
    _validate_exact_components(rows)
    artifacts = _artifact_records(rows)
    artifact_hashes = {row["artifact_sha256"] for row in artifacts}
    _require(
        EXPECTED_CLEANMIX_SHA256 in artifact_hashes,
        "measured component artifacts do not contain exact CleanMix 0.7.0",
    )
    try:
        _validate_toolchain_lock(toolchain_bytes, EXPECTED_CLEANMIX_SHA256)
    except CleanroomDiscoveryTraceImportError as exc:
        raise CleanroomServiceComponentsImportError(str(exc)) from exc

    candidate_record = _file_record(candidate_lock_path, "candidate lock")
    toolchain_record = _file_record(toolchain_lock_path, "toolchain lock")
    try:
        receipt = build_service_components_receipt(
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
                "raw_trace": _file_record(raw_trace_path, "raw component trace"),
                "toolchain_lock": toolchain_record,
            },
            artifacts=artifacts,
            raw_events=rows,
            limitations=(
                "This exact producer is bound to Cleanroom 0.6.8-alpha with embedded CleanMix 0.7.0.",
                "The audit-trail object was read from its existing field during shutdown; the observer did not invoke the lazy accessor.",
            ),
        )
        return parse_service_components_receipt(receipt)
    except ServiceComponentsValidationError as exc:
        raise CleanroomServiceComponentsImportError(
            f"component receipt construction failed: {exc}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-trace", required=True, type=Path)
    parser.add_argument("--candidate-lock", required=True, type=Path)
    parser.add_argument("--toolchain-lock", required=True, type=Path)
    parser.add_argument("--observer-agent", required=True, type=Path)
    parser.add_argument("--launch-log", required=True, type=Path)
    parser.add_argument("--fixture-result", required=True, type=Path)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipt = import_exact_cleanmix_service_components(
            raw_trace_path=arguments.raw_trace,
            candidate_lock_path=arguments.candidate_lock,
            toolchain_lock_path=arguments.toolchain_lock,
            observer_agent_path=arguments.observer_agent,
            launch_log_path=arguments.launch_log,
            fixture_result_path=arguments.fixture_result,
            launch_id=arguments.launch_id,
        )
        write_service_components_receipt(arguments.output, receipt)
    except (CleanroomServiceComponentsImportError, OSError) as exc:
        print(f"Cleanroom service components import: {exc}", file=sys.stderr)
        return 2
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
