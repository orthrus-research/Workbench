#!/usr/bin/env python3
"""Import one exact Cleanroom 0.6.8-alpha Foundation definition capture."""

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
    EXPECTED_LOADER,
    PROFILE_ID,
    CleanroomDiscoveryTraceImportError,
    _validate_candidate_lock,
    _validate_fixture_result,
    _validate_launch,
    _validate_toolchain_lock,
)
from workbench_crucible_mixins import (  # noqa: E402
    FinalDefinitionValidationError,
    build_final_definition_receipt,
    parse_final_definition_receipt,
    parse_raw_final_definition,
    write_final_definition_receipt,
)
from workbench_crucible_observatory import (  # noqa: E402
    build_foundation_class_dump_manifest,
    locate_foundation_class_dump,
    verify_foundation_class_dump_entry,
)


EXPECTED_FOUNDATION_SHA256 = (
    "a9f5cf9cb54715edf22d6bcb49bae50f281f818b1731f53f22aab85a48a7b54b"
)
EXPECTED_ACTUAL_CLASS_LOADER_SHA256 = (
    "41cc84afadb4155ea5d15f5132f08b23327e6bf51edfb5615d1a6f5c6eb7706f"
)
EXPECTED_ACTUAL_CLASS_LOADER = "top.outlands.foundation.boot.ActualClassLoader"


class FoundationFinalDefinitionImportError(ValueError):
    """Evidence does not match the exact Cleanroom/Foundation candidate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FoundationFinalDefinitionImportError(message)


def _read_file(path: Path, context: str) -> bytes:
    _require(not path.is_symlink(), f"{context} cannot be a symlink: {path}")
    _require(path.is_file(), f"{context} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FoundationFinalDefinitionImportError(
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
        source = source[4:].split("!/", 1)[0]
    parsed = urlsplit(source)
    _require(parsed.scheme == "file", f"definition code source is not local file evidence: {code_source_uri}")
    _require(parsed.netloc in {"", "localhost"}, f"definition code source has a remote authority: {code_source_uri}")
    _require(not parsed.query and not parsed.fragment, f"definition code source has URL decorations: {code_source_uri}")
    path = Path(unquote(parsed.path))
    _require(path.is_absolute(), f"definition code source is not absolute: {code_source_uri}")
    _require(not path.is_symlink(), f"definition code source cannot be a symlink: {path}")
    _require(path.is_file(), f"definition code source is not a regular artifact: {path}")
    return path.resolve(strict=True)


def _artifact_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uris = {
        row["payload"]["target_code_source_uri"]
        for row in rows
        if row["event"] == "transform_applied"
    }
    uris.update(
        row["payload"]["code_source_uri"]
        for row in rows
        if row["event"] == "final_definition"
    )
    records = []
    for uri in sorted(uris):
        path = _artifact_path(uri)
        encoded = _read_file(path, f"definition artifact {uri}")
        records.append(
            {
                "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
                "size_bytes": len(encoded),
                "label": path.name,
                "code_source_uri": uri,
            }
        )
    return records


def _validate_exact_rows(
    rows: list[dict[str, Any]], dump_directory: Path
) -> None:
    _require(rows[-1]["payload"]["health"] == "healthy", "final-definition capture is not healthy")
    installed = next(row["payload"] for row in rows if row["event"] == "transformer_installed")
    _require(installed["target_class"].replace("/", ".") == EXPECTED_ACTUAL_CLASS_LOADER, "final-definition observer targeted an unexpected class")
    _require(installed["expected_input_sha256"] == EXPECTED_ACTUAL_CLASS_LOADER_SHA256, "ActualClassLoader byte guard drifted")
    applied = [row["payload"] for row in rows if row["event"] == "transform_applied"]
    _require(len(applied) == 1, "ActualClassLoader was not instrumented exactly once")
    _require(applied[0]["input_sha256"] == EXPECTED_ACTUAL_CLASS_LOADER_SHA256, "ActualClassLoader observed input drifted")
    definitions = [row["payload"] for row in rows if row["event"] == "final_definition"]
    _require(bool(definitions), "capture contains no successful final definition")
    for definition in definitions:
        _require(definition["defining_loader_class"] == EXPECTED_LOADER, f"{definition['target_class']} used an unexpected defining loader")
        verified = verify_foundation_class_dump_entry(
            dump_directory, definition["target_class"]
        )
        _require(verified.relative_path == definition["dump_relative_path"], f"{definition['target_class']} dump path drifted")
        _require(verified.size == definition["final_bytecode_size"], f"{definition['target_class']} final size drifted")
        _require(verified.sha256 == definition["final_bytecode_sha256"], f"{definition['target_class']} final bytes drifted after observation")


def import_exact_foundation_final_definitions(
    *,
    raw_trace_path: Path,
    candidate_lock_path: Path,
    toolchain_lock_path: Path,
    observer_agent_path: Path,
    launch_log_path: Path,
    fixture_result_path: Path,
    case_directory: Path,
    launch_id: str,
) -> dict[str, Any]:
    candidate_bytes = _read_file(candidate_lock_path, "candidate lock")
    toolchain_bytes = _read_file(toolchain_lock_path, "toolchain lock")
    raw_bytes = _read_file(raw_trace_path, "raw final-definition trace")
    launch_bytes = _read_file(launch_log_path, "launch log")
    fixture_bytes = _read_file(fixture_result_path, "fixture result")
    _read_file(observer_agent_path, "observer agent")
    try:
        _validate_candidate_lock(candidate_bytes)
        _validate_toolchain_lock(toolchain_bytes, "a41daa71398e948bc09fa578ae2460be639df0fa82fccdb630b316418d5a4a36")
        _validate_launch(launch_bytes)
        _validate_fixture_result(fixture_bytes)
    except CleanroomDiscoveryTraceImportError as exc:
        raise FoundationFinalDefinitionImportError(str(exc)) from exc
    try:
        rows = parse_raw_final_definition(raw_bytes)
    except FinalDefinitionValidationError as exc:
        raise FoundationFinalDefinitionImportError(
            f"raw final-definition capture failed semantic validation: {exc}"
        ) from exc

    try:
        dump_directory = locate_foundation_class_dump(case_directory)
        manifest = build_foundation_class_dump_manifest(dump_directory)
        _validate_exact_rows(rows, dump_directory)
    except ValueError as exc:
        raise FoundationFinalDefinitionImportError(
            f"Foundation dump validation failed: {exc}"
        ) from exc
    artifacts = _artifact_records(rows)
    artifact_hashes = {row["artifact_sha256"] for row in artifacts}
    _require(EXPECTED_FOUNDATION_SHA256 in artifact_hashes, "definition capture is not bound to Foundation 0.19.11")

    candidate_record = _file_record(candidate_lock_path, "candidate lock")
    toolchain_record = _file_record(toolchain_lock_path, "toolchain lock")
    try:
        receipt = build_final_definition_receipt(
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
                "raw_trace": _file_record(raw_trace_path, "raw final-definition trace"),
                "toolchain_lock": toolchain_record,
            },
            artifacts=artifacts,
            foundation_manifest={
                "format": manifest.format,
                "manifest_sha256": manifest.manifest_sha256,
                "class_count": manifest.class_count,
                "total_size_bytes": manifest.total_size_bytes,
                "foundation_artifact_sha256": EXPECTED_FOUNDATION_SHA256,
            },
            raw_events=rows,
            limitations=(
                "This exact producer is bound to Cleanroom 0.6.8-alpha and Foundation 0.19.11.",
                "The retained vertical slice is one dedicated-server launch and only the explicitly requested target set.",
            ),
        )
        return parse_final_definition_receipt(receipt)
    except FinalDefinitionValidationError as exc:
        raise FoundationFinalDefinitionImportError(
            f"final-definition receipt construction failed: {exc}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-trace", required=True, type=Path)
    parser.add_argument("--candidate-lock", required=True, type=Path)
    parser.add_argument("--toolchain-lock", required=True, type=Path)
    parser.add_argument("--observer-agent", required=True, type=Path)
    parser.add_argument("--launch-log", required=True, type=Path)
    parser.add_argument("--fixture-result", required=True, type=Path)
    parser.add_argument("--case-directory", required=True, type=Path)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipt = import_exact_foundation_final_definitions(
            raw_trace_path=arguments.raw_trace,
            candidate_lock_path=arguments.candidate_lock,
            toolchain_lock_path=arguments.toolchain_lock,
            observer_agent_path=arguments.observer_agent,
            launch_log_path=arguments.launch_log,
            fixture_result_path=arguments.fixture_result,
            case_directory=arguments.case_directory,
            launch_id=arguments.launch_id,
        )
        write_final_definition_receipt(arguments.output, receipt)
    except (FoundationFinalDefinitionImportError, OSError) as exc:
        print(f"Foundation final-definition import: {exc}", file=sys.stderr)
        return 2
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
