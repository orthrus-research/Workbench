#!/usr/bin/env python3
"""Import one exact Cleanroom 0.6.8-alpha transformer-chain capture."""

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
    PROFILE_ID,
    CleanroomDiscoveryTraceImportError,
    _validate_candidate_lock,
    _validate_fixture_result,
    _validate_launch,
    _validate_toolchain_lock,
)
from workbench_crucible_mixins import (  # noqa: E402
    TransformerChainValidationError,
    build_transformer_chain_receipt,
    parse_raw_transformer_chain,
    parse_transformer_chain_receipt,
    write_transformer_chain_receipt,
)


EXPECTED_TARGETS = {
    "com.cleanroommc.cleanmix.service.CleanMixService":
        "6acbc3726fc8b5d1087ced2e21c63329666fc2fd48fda13fb0a249cb15f72c29",
    "com.cleanroommc.cleanmix.service.FoundationTransformerProvider":
        "7c44263e004ecf0647a1789bd210c42b1a148647ce39768ed7f31395d0d23185",
}
EXPECTED_PROVIDER = "com.cleanroommc.cleanmix.service.FoundationTransformerProvider"
EXPECTED_FOUNDATION_SHA256 = (
    "a9f5cf9cb54715edf22d6bcb49bae50f281f818b1731f53f22aab85a48a7b54b"
)
EXPECTED_CLEANROOM_DEV_SHA256 = (
    "5603f57eae9b970b33aa27c3cc67603a4165049540b09d4677fc3fa972b01644"
)
REQUIRED_PROVIDER_EXCLUSIONS = {
    "net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer",
    "net.minecraftforge.fml.common.asm.transformers.TerminalTransformer",
}
REQUIRED_FOUNDATION_EXCLUSIONS = {
    "org.spongepowered.asm.mixin.",
    "org.spongepowered.asm.service.",
    "org.spongepowered.asm.transformers.",
}


class CleanroomTransformerChainImportError(ValueError):
    """Evidence does not match the exact Cleanroom candidate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanroomTransformerChainImportError(message)


def _read_file(path: Path, context: str) -> bytes:
    _require(not path.is_symlink(), f"{context} cannot be a symlink: {path}")
    _require(path.is_file(), f"{context} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CleanroomTransformerChainImportError(
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
    _require(parsed.scheme == "file", f"chain code source is not local file evidence: {code_source_uri}")
    _require(parsed.netloc in {"", "localhost"}, f"chain code source has a remote authority: {code_source_uri}")
    _require(not parsed.query and not parsed.fragment, f"chain code source has URL decorations: {code_source_uri}")
    path = Path(unquote(parsed.path))
    _require(path.is_absolute(), f"chain code source is not absolute: {code_source_uri}")
    _require(not path.is_symlink(), f"chain code source cannot be a symlink: {path}")
    _require(path.is_file(), f"chain code source is not a regular artifact: {path}")
    return path.resolve(strict=True)


def _artifact_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uris: set[str] = set()
    for row in rows:
        payload = row["payload"]
        if row["event"] == "transform_applied":
            uris.add(payload["target_code_source_uri"])
        elif row["event"] == "chain_epoch":
            uris.add(payload["provider_code_source_uri"])
            uris.add(payload["foundation_code_source_uri"])
            for chain_name in ("live_chain", "delegated_chain"):
                uris.update(item["code_source_uri"] for item in payload[chain_name])
    _require(None not in uris, "chain capture contains an unmeasured code source")
    records: list[dict[str, Any]] = []
    for uri in sorted(uris):
        path = _artifact_path(uri)
        encoded = _read_file(path, f"chain artifact {uri}")
        records.append({
            "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
            "label": path.name,
            "code_source_uri": uri,
        })
    return records


def _delegated_is_ordered_subsequence(
    live: list[dict[str, Any]], delegated: list[dict[str, Any]]
) -> bool:
    live_names = [row["reported_name"] for row in live]
    next_index = 0
    for delegated_row in delegated:
        name = delegated_row["reported_name"]
        try:
            matched = live_names.index(name, next_index)
        except ValueError:
            return False
        next_index = matched + 1
    return True


def _validate_exact_rows(rows: list[dict[str, Any]]) -> None:
    _require(rows[-1]["payload"]["health"] == "healthy", "transformer-chain capture is not healthy")
    expected = {
        row["target_class"]: row["expected_input_sha256"]
        for row in rows[0]["payload"]["targets"]
    }
    _require(expected == EXPECTED_TARGETS, "transformer-chain exact class-byte guards drifted")
    transforms = [row["payload"] for row in rows if row["event"] == "transform_applied"]
    _require(len(transforms) == 2, "transformer-chain capture did not weave both exact ownership seams")
    _require(all(row["defining_loader_class"] == EXPECTED_LOADER for row in transforms), "transformer-chain targets were not defined by the exact Foundation LaunchClassLoader")

    epochs = [row["payload"] for row in rows if row["event"] == "chain_epoch"]
    _require(bool(epochs), "transformer-chain capture contains no rebuild epoch")
    for epoch in epochs:
        _require(epoch["provider_class"] == EXPECTED_PROVIDER, "epoch used an unexpected transformer provider")
        _require(epoch["provider_loader_class"] == EXPECTED_LOADER, "epoch provider used an unexpected defining loader")
        _require(REQUIRED_PROVIDER_EXCLUSIONS <= set(epoch["provider_exclusions"]), "epoch omits an exact re-entrant provider exclusion")
        _require(REQUIRED_FOUNDATION_EXCLUSIONS <= set(epoch["foundation_transformer_exclusions"]), "epoch omits an exact Foundation transformer exclusion")
        _require(_delegated_is_ordered_subsequence(epoch["live_chain"], epoch["delegated_chain"]), "delegated transformer chain is not an ordered live-chain subsequence")
        _require(all(item["delegation_excluded"] is False for item in epoch["delegated_chain"]), "delegated chain retained an excluded transformer")

    reasons = {
        row["payload"]["reason_code"]
        for row in rows if row["event"] == "refresh_requested"
    }
    _require(
        "initial_cache_miss" in reasons or "live_chain_size_changed" in reasons,
        "capture lacks an initial cache or live-chain rebuild reason",
    )
    _require(
        "transformer_exclusion_added" in reasons
        or "transformer_exclusion_reasserted" in reasons,
        "capture lacks a transformer-exclusion invalidation reason",
    )
    _require(
        "cleanmix_service_refresh" in reasons,
        "capture lacks the CleanMix service refresh reason",
    )


def import_exact_cleanmix_transformer_chain(
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
    raw_bytes = _read_file(raw_trace_path, "raw transformer-chain trace")
    launch_bytes = _read_file(launch_log_path, "launch log")
    fixture_bytes = _read_file(fixture_result_path, "fixture result")
    _read_file(observer_agent_path, "observer agent")
    try:
        _validate_candidate_lock(candidate_bytes)
        _validate_toolchain_lock(toolchain_bytes, EXPECTED_CLEANMIX_SHA256)
        _validate_launch(launch_bytes)
        _validate_fixture_result(fixture_bytes)
    except CleanroomDiscoveryTraceImportError as exc:
        raise CleanroomTransformerChainImportError(str(exc)) from exc
    try:
        rows = parse_raw_transformer_chain(raw_bytes)
    except TransformerChainValidationError as exc:
        raise CleanroomTransformerChainImportError(
            f"raw transformer-chain capture failed semantic validation: {exc}"
        ) from exc
    _validate_exact_rows(rows)
    artifacts = _artifact_records(rows)
    artifact_hashes = {row["artifact_sha256"] for row in artifacts}
    _require(EXPECTED_CLEANROOM_DEV_SHA256 in artifact_hashes, "epochs are not bound to the exact transformed Cleanroom candidate artifact")
    _require(EXPECTED_FOUNDATION_SHA256 in artifact_hashes, "epochs are not bound to exact Foundation 0.19.11")

    candidate_record = _file_record(candidate_lock_path, "candidate lock")
    toolchain_record = _file_record(toolchain_lock_path, "toolchain lock")
    try:
        receipt = build_transformer_chain_receipt(
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
                "raw_trace": _file_record(raw_trace_path, "raw transformer-chain trace"),
                "toolchain_lock": toolchain_record,
            },
            artifacts=artifacts,
            raw_events=rows,
            limitations=(
                "This exact producer is bound to Cleanroom 0.6.8-alpha, CleanMix 0.7.0, and Foundation 0.19.11.",
                "The retained vertical slice is one dedicated-server launch; client, integrated-server, live-size mutation, and failure paths remain future matrix rows.",
            ),
        )
        return parse_transformer_chain_receipt(receipt)
    except TransformerChainValidationError as exc:
        raise CleanroomTransformerChainImportError(
            f"transformer-chain receipt construction failed: {exc}"
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
        receipt = import_exact_cleanmix_transformer_chain(
            raw_trace_path=arguments.raw_trace,
            candidate_lock_path=arguments.candidate_lock,
            toolchain_lock_path=arguments.toolchain_lock,
            observer_agent_path=arguments.observer_agent,
            launch_log_path=arguments.launch_log,
            fixture_result_path=arguments.fixture_result,
            launch_id=arguments.launch_id,
        )
        write_transformer_chain_receipt(arguments.output, receipt)
    except (CleanroomTransformerChainImportError, OSError) as exc:
        print(f"Cleanroom transformer-chain import: {exc}", file=sys.stderr)
        return 2
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
