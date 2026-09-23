#!/usr/bin/env python3
"""Import one exact Cleanroom 0.6.8-alpha CleanMix discovery trace."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[4]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_mixins import (  # noqa: E402
    DefiningLoaderTraceValidationError,
    build_defining_loader_trace_receipt,
    parse_defining_loader_trace_receipt,
    parse_raw_discovery_trace,
    render_defining_loader_trace_receipt,
)


PROFILE_ID = (
    "workbench-platform:cleanroom:0.6.8-alpha+mc-1.12.2+"
    "forge-14.23.5.2864+mcp-9.42-stable_39"
)
EXPECTED_CLEANROOM_REVISION = "9946eb1f17a66a72d518e5e4a92d45c62c6d33fc"
EXPECTED_CLEANMIX_SHA256 = (
    "a41daa71398e948bc09fa578ae2460be639df0fa82fccdb630b316418d5a4a36"
)
EXPECTED_MIXIN_SERVICE_CLASS_SHA256 = (
    "3e36757484a0b03ac70952b72696a8989506b84c60fb64e5154d26f53895c09b"
)
EXPECTED_BOOTSTRAP = "com.cleanroommc.cleanmix.service.CleanMixBootstrap"
EXPECTED_SERVICE = "com.cleanroommc.cleanmix.service.CleanMixService"
EXPECTED_SERVICE_NAME = "CleanMix"
EXPECTED_LOADER = "net.minecraft.launchwrapper.LaunchClassLoader"


class CleanroomDiscoveryTraceImportError(ValueError):
    """Raised when evidence does not match the exact candidate."""


class _DuplicateKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanroomDiscoveryTraceImportError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_object(encoded: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite number {token}")
            ),
            parse_float=lambda token: (_ for _ in ()).throw(
                ValueError(f"floating number {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        raise CleanroomDiscoveryTraceImportError(f"cannot parse {context}: {exc}") from exc
    _require(isinstance(value, dict), f"{context} must be an object")
    return value


def _read(path: Path, context: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CleanroomDiscoveryTraceImportError(f"cannot read {context}: {exc}") from exc


def _validate_candidate_lock(encoded: bytes) -> None:
    value = _decode_object(encoded, "candidate lock")
    _require(value.get("format") == "workbench-cleanroom-candidate-lock-v1",
             "candidate lock has wrong format")
    _require(value.get("candidate_id") == PROFILE_ID, "candidate lock has wrong identity")
    _require(value.get("minecraft", {}).get("version") == "1.12.2",
             "candidate lock has wrong Minecraft version")
    _require(value.get("forge", {}).get("version") == "14.23.5.2864",
             "candidate lock has wrong Forge version")
    _require(value.get("cleanroom", {}).get("version") == "0.6.8-alpha",
             "candidate lock has wrong Cleanroom version")
    _require(
        value.get("cleanroom", {}).get("source_revision") == EXPECTED_CLEANROOM_REVISION,
        "candidate lock has wrong Cleanroom revision",
    )


def _validate_toolchain_lock(encoded: bytes, engine_sha256: str) -> None:
    value = _decode_object(encoded, "transformer toolchain lock")
    _require(value.get("format") == "workbench-cleanroom-transformer-toolchain-lock-v1",
             "toolchain lock has wrong format")
    _require(value.get("binding", {}).get("candidate_id") == PROFILE_ID,
             "toolchain lock has wrong candidate binding")
    expectations = value.get("native_service_expectations", {})
    _require(expectations.get("bootstrap_service") == EXPECTED_BOOTSTRAP,
             "toolchain lock has wrong bootstrap service")
    _require(expectations.get("mixin_service") == EXPECTED_SERVICE,
             "toolchain lock has wrong Mixin service")
    _require(expectations.get("mixin_service_name") == EXPECTED_SERVICE_NAME,
             "toolchain lock has wrong Mixin service name")
    _require(value.get("java_runtime", {}).get("major") == 25,
             "toolchain lock does not select Java 25")
    engine_rows = [
        row for row in value.get("artifacts", [])
        if row.get("coordinate") == "com.cleanroommc:cleanmix:0.7.0"
        and row.get("role") == "mixin-engine"
    ]
    _require(len(engine_rows) == 1, "toolchain lock lacks one CleanMix engine row")
    _require(engine_rows[0].get("sha256") == EXPECTED_CLEANMIX_SHA256,
             "toolchain lock has wrong CleanMix SHA-256")
    _require(engine_sha256 == EXPECTED_CLEANMIX_SHA256,
             "measured Mixin engine does not match the toolchain lock")


def _validate_launch(encoded: bytes) -> None:
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise CleanroomDiscoveryTraceImportError("launch log is not UTF-8") from exc
    required = (
        "> Task :runServer",
        "Cleanroom version 0.6.8-alpha",
        "Initializing CleanMix...",
        "Service=CleanMix Env=SERVER",
        "Starting minecraft server version 1.12.2",
        "Stopping server",
        "BUILD SUCCESSFUL",
    )
    for marker in required:
        _require(marker in text, f"launch log lacks exact marker {marker!r}")
    _require("BUILD FAILED" not in text, "launch log records a failed Gradle build")
    _require(
        re.search(r"Java is .* version 25(?:\.|,)", text) is not None,
        "launch log does not report Java 25",
    )


def _validate_fixture_result(encoded: bytes) -> None:
    value = _decode_object(encoded, "fixture result")
    _require(value.get("schema") == "workbench.worldgen-observatory.fixture-result.v1",
             "fixture result has wrong schema")
    _require(value.get("completion_state") == "complete",
             "fixture result is not complete")
    _require(value.get("save_state") == "flushed", "fixture result is not flushed")
    _require(value.get("shutdown_state") == "requested",
             "fixture result did not request shutdown")


def _validate_exact_trace(rows: list[dict[str, Any]]) -> None:
    _require(rows[-1]["payload"]["health"] == "healthy", "trace end health is not healthy")
    transform = next(row for row in rows if row["event"] == "transform_applied")
    _require(transform["payload"]["input_sha256"] == EXPECTED_MIXIN_SERVICE_CLASS_SHA256,
             "trace did not transform exact CleanMix 0.7.0 MixinService bytes")
    _require(transform["payload"]["defining_loader_class"] == EXPECTED_LOADER,
             "trace did not observe the Foundation LaunchClassLoader definition")

    stage_starts = [row for row in rows if row["event"] == "stage_start"]
    _require([row["stage"] for row in stage_starts] == ["bootstrap", "service"],
             "trace stage order is not bootstrap then service")
    expected_bypass = {
        "bootstrap": "mixin.bootstrapService",
        "service": "mixin.service",
    }
    for row in stage_starts:
        payload = row["payload"]
        _require(payload["defining_loader_identity"] == transform["payload"]["defining_loader_identity"],
                 "stage defining-loader identity changed")
        _require(payload["bypass_property_name"] == expected_bypass[row["stage"]],
                 "trace has wrong bypass property name")
        _require(payload["bypass_property_value"] is None,
                 "exact run used a Mixin bypass property")
        _require(payload["mechanism"] == "service_loader",
                 "exact run did not use ServiceLoader")

    starts = [row for row in rows if row["event"] == "attempt_start"]
    _require([(row["stage"], row["payload"]["mechanism"]) for row in starts] == [
        ("bootstrap", "service_loader"), ("service", "service_loader")
    ], "exact run did not have one ordered attempt per discovery stage")
    bootstrap = [
        row for row in rows
        if row["stage"] == "bootstrap" and row["event"] == "provider_constructed"
    ]
    service = [
        row for row in rows
        if row["stage"] == "service" and row["event"] == "provider_constructed"
    ]
    _require(len(bootstrap) == 1 and bootstrap[0]["payload"]["provider_class"] == EXPECTED_BOOTSTRAP,
             "exact bootstrap provider was not constructed once")
    _require(len(service) == 1 and service[0]["payload"]["provider_class"] == EXPECTED_SERVICE,
             "exact Mixin service was not constructed once")
    validity = [row for row in rows if row["event"] == "validity_returned"]
    _require(len(validity) == 1 and validity[0]["payload"]["valid"] is True,
             "selected CleanMixService did not return isValid=true exactly once")
    selected = [row for row in rows if row["event"] == "provider_selected"]
    _require(len(selected) == 1 and selected[0]["payload"]["provider_class"] == EXPECTED_SERVICE,
             "exact CleanMixService was not selected once")
    names = [row["payload"]["service_name"] for row in rows
             if row["event"] == "service_name_returned"]
    _require(names == [EXPECTED_SERVICE_NAME], "CleanMix service name outcome is not exact")
    _require(not any(row["event"].endswith("failure") for row in rows),
             "exact trace contains a failure event")


def import_exact_cleanmix_discovery_trace(
    *,
    raw_trace_path: Path,
    candidate_lock_path: Path,
    toolchain_lock_path: Path,
    observer_agent_path: Path,
    mixin_engine_artifact_path: Path,
    selected_provider_artifact_path: Path,
    launch_log_path: Path,
    fixture_result_path: Path,
    launch_id: str,
) -> dict[str, Any]:
    candidate_bytes = _read(candidate_lock_path, "candidate lock")
    toolchain_bytes = _read(toolchain_lock_path, "toolchain lock")
    raw_bytes = _read(raw_trace_path, "raw trace")
    engine_bytes = _read(mixin_engine_artifact_path, "Mixin engine artifact")
    launch_bytes = _read(launch_log_path, "launch log")
    fixture_bytes = _read(fixture_result_path, "fixture result")
    _validate_candidate_lock(candidate_bytes)
    _validate_toolchain_lock(toolchain_bytes, hashlib.sha256(engine_bytes).hexdigest())
    _validate_launch(launch_bytes)
    _validate_fixture_result(fixture_bytes)
    try:
        rows = parse_raw_discovery_trace(raw_bytes)
    except DefiningLoaderTraceValidationError as exc:
        raise CleanroomDiscoveryTraceImportError(
            f"raw discovery trace failed semantic validation: {exc}"
        ) from exc
    _validate_exact_trace(rows)
    try:
        receipt = build_defining_loader_trace_receipt(
            raw_trace_path=raw_trace_path,
            candidate_lock_path=candidate_lock_path,
            toolchain_lock_path=toolchain_lock_path,
            observer_agent_path=observer_agent_path,
            mixin_engine_artifact_path=mixin_engine_artifact_path,
            selected_provider_artifact_path=selected_provider_artifact_path,
            launch_log_path=launch_log_path,
            fixture_result_path=fixture_result_path,
            launch_id=launch_id,
            profile_id=PROFILE_ID,
            side="dedicated_server",
        )
        return parse_defining_loader_trace_receipt(receipt)
    except DefiningLoaderTraceValidationError as exc:
        raise CleanroomDiscoveryTraceImportError(
            f"discovery receipt construction failed: {exc}"
        ) from exc


def _write_atomic(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
    try:
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-trace", required=True, type=Path)
    parser.add_argument("--candidate-lock", required=True, type=Path)
    parser.add_argument("--toolchain-lock", required=True, type=Path)
    parser.add_argument("--observer-agent", required=True, type=Path)
    parser.add_argument("--mixin-engine-artifact", required=True, type=Path)
    parser.add_argument("--selected-provider-artifact", required=True, type=Path)
    parser.add_argument("--launch-log", required=True, type=Path)
    parser.add_argument("--fixture-result", required=True, type=Path)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipt = import_exact_cleanmix_discovery_trace(
            raw_trace_path=arguments.raw_trace,
            candidate_lock_path=arguments.candidate_lock,
            toolchain_lock_path=arguments.toolchain_lock,
            observer_agent_path=arguments.observer_agent,
            mixin_engine_artifact_path=arguments.mixin_engine_artifact,
            selected_provider_artifact_path=arguments.selected_provider_artifact,
            launch_log_path=arguments.launch_log,
            fixture_result_path=arguments.fixture_result,
            launch_id=arguments.launch_id,
        )
        _write_atomic(arguments.output, render_defining_loader_trace_receipt(receipt))
    except (CleanroomDiscoveryTraceImportError, OSError) as exc:
        print(f"Cleanroom discovery trace import: {exc}", file=sys.stderr)
        return 2
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
