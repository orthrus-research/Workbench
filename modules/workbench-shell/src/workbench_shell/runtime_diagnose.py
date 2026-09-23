"""Read-only diagnosis of retained Workbench runtime evidence."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname
import zipfile

from workbench_core.artifact_store import sha256_file
from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .classfile import (
    ClassFileError,
    method_argument_names,
    parse_class_file,
)
from .runtime_materialize import (
    packwiz_materialization_version,
    verify_packwiz_materialization_receipt_identity,
)


DIAGNOSIS_FORMAT = "workbench-runtime-diagnosis-v2"
GUIDANCE_FORMAT = "workbench-runtime-diagnostic-guidance-v1"
LAUNCH_RECEIPT_FORMATS = {
    "workbench-runtime-launch-receipt-v1": 1,
    "workbench-runtime-launch-receipt-v2": 2,
    "workbench-runtime-launch-receipt-v3": 3,
}
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_TEXT_BYTES = 16 * 1024 * 1024
MAX_CLASS_BYTES = 16 * 1024 * 1024
MAX_GUIDANCE_BYTES = 1024 * 1024
MAX_MOD_ARCHIVES = 4096
MAX_PROVENANCE_FILES = 100_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EXCEPTION_RE = re.compile(
    r"^(?:Caused by: )?"
    r"(?P<type>[A-Za-z_$][A-Za-z0-9_.$]+): (?P<message>.*)$"
)
INJECTION_RE = re.compile(
    r"Critical injection failure: (?P<kind>.+?) method "
    r"(?P<handler>\S+) in (?P<config>[^:\s]+):(?P<mixin>\S+) "
    r"from mod (?P<mod>\S+) failed injection check, "
    r"\((?P<succeeded>\d+)/(?P<required>\d+)\) succeeded\. "
    r"Scanned (?P<scanned>\d+) target\(s\)\."
    r"(?: Using refmap (?P<refmap>\S+))?"
)
MIXIN_STACK_RE = re.compile(
    r"(?m)^\s{4}(?P<target>[A-Za-z0-9_$/]+):\s*$\n"
    r"^\s{8}(?P<mixin>[A-Za-z0-9_.$]+) "
    r"\((?P<config>[^)]+)\) \[(?P<mod>[^]]+)\]\s*$"
)
V3_SESSION_STATES = frozenset({
    "exited",
    "session-timeout",
    "attach-timeout",
    "probe-failed",
    "launch-ended-before-attach",
})


class RuntimeDiagnosisError(ValueError):
    """Raised when retained runtime evidence cannot be trusted or read."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _local_uri_path(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise RuntimeDiagnosisError(f"{label} must be a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeDiagnosisError(f"{label} must be a local file URI")
    text = url2pathname(parsed.path)
    if (
        os.name == "nt"
        and len(text) >= 3
        and text[0] == "/"
        and text[2] == ":"
    ):
        text = text[1:]
    return Path(text)


def _regular_file(path: Path, label: str, maximum: int) -> bytes:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_file():
        raise RuntimeDiagnosisError(f"{label} is not a regular file: {path}")
    try:
        size = expanded.stat().st_size
        if size > maximum:
            raise RuntimeDiagnosisError(
                f"{label} exceeds the {maximum}-byte diagnostic limit"
            )
        return expanded.read_bytes()
    except OSError as exc:
        raise RuntimeDiagnosisError(f"{label} cannot be read: {path}") from exc


def _validate_v3_receipt(receipt: dict[str, Any]) -> None:
    parent = receipt.get("parent_launch_receipt")
    observation = receipt.get("observation")
    launch_policy = receipt.get("launch_policy")
    target = receipt.get("target")
    if (
        not isinstance(parent, dict)
        or SHA256_ID_RE.fullmatch(str(parent.get("launch_id"))) is None
        or SHA256_RE.fullmatch(str(parent.get("sha256"))) is None
        or type(parent.get("size")) is not int
        or parent["size"] < 0
        or not isinstance(observation, dict)
        or not isinstance(launch_policy, dict)
        or not isinstance(target, dict)
    ):
        raise RuntimeDiagnosisError(
            "V3 launch receipt lacks its parent or observation identity"
        )
    parent_path = _local_uri_path(
        parent.get("uri"),
        "V3 parent launch receipt",
    )
    target_parent = _local_uri_path(
        target.get("parent_receipt_uri"),
        "V3 target parent launch receipt",
    )
    if parent_path.absolute() != target_parent.absolute():
        raise RuntimeDiagnosisError(
            "V3 parent launch receipt locations do not match"
        )
    parent_raw = _regular_file(
        parent_path,
        "V3 parent launch receipt",
        MAX_RECEIPT_BYTES,
    )
    if (
        len(parent_raw) != parent["size"]
        or sha256(parent_raw).hexdigest() != parent["sha256"]
    ):
        raise RuntimeDiagnosisError(
            "V3 parent launch receipt does not match its retained identity"
        )
    try:
        parent_receipt = json.loads(parent_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeDiagnosisError(
            "V3 parent launch receipt is not valid UTF-8 JSON"
        ) from exc
    if (
        not isinstance(parent_receipt, dict)
        or parent_receipt.get("format")
        not in {
            "workbench-runtime-launch-receipt-v1",
            "workbench-runtime-launch-receipt-v2",
        }
        or parent_receipt.get("schema_version")
        != LAUNCH_RECEIPT_FORMATS[parent_receipt.get("format")]
        or parent_receipt.get("launch_id") != parent["launch_id"]
    ):
        raise RuntimeDiagnosisError(
            "V3 parent launch receipt has an unsupported identity"
        )
    if receipt.get("project") != parent_receipt.get("project"):
        raise RuntimeDiagnosisError(
            "V3 project identity differs from its bound parent receipt"
        )
    lifecycle = observation.get("session_exit")
    if not isinstance(lifecycle, dict):
        raise RuntimeDiagnosisError(
            "V3 launch receipt lacks a session-exit observation"
        )
    state = lifecycle.get("state")
    pids = lifecycle.get("observed_pids")
    samples = lifecycle.get("samples")
    attached_at = lifecycle.get("attached_at")
    if (
        state not in V3_SESSION_STATES
        or not isinstance(lifecycle.get("method"), str)
        or not lifecycle["method"]
        or type(samples) is not int
        or samples < 0
        or not isinstance(pids, list)
        or any(type(pid) is not int or pid <= 0 for pid in pids)
        or len(pids) != len(set(pids))
        or (
            attached_at is not None
            and (not isinstance(attached_at, str) or not attached_at)
        )
        or not isinstance(lifecycle.get("observed_at"), str)
        or not lifecycle["observed_at"]
    ):
        raise RuntimeDiagnosisError(
            "V3 session-exit observation is incomplete"
        )
    boundary = launch_policy.get("observation_boundary")
    if state == "exited":
        if (
            not pids
            or not isinstance(attached_at, str)
            or not attached_at
            or boundary != "projected-client-process-exit"
        ):
            raise RuntimeDiagnosisError(
                "V3 exited session lacks exact process-exit binding"
            )
    elif boundary != "incomplete-process-observation":
        raise RuntimeDiagnosisError(
            "V3 incomplete session is mislabeled as process-exit evidence"
        )
    if receipt.get("outcome") == "checkpoint-reached" and not any(
        isinstance(item, dict)
        and item.get("label") == "minecraft-latest-log"
        and item.get("state") == "captured"
        for item in receipt["evidence"]
    ):
        raise RuntimeDiagnosisError(
            "V3 checkpoint receipt lacks a retained Minecraft latest log"
        )
    if any(not isinstance(item, dict) for item in receipt["evidence"]):
        raise RuntimeDiagnosisError(
            "V3 launch evidence entries must be objects"
        )
    identity = {
        "parent_launch_id": parent["launch_id"],
        "session_exit": lifecycle,
        "evidence": [
            {
                key: item.get(key)
                for key in ("label", "state", "sha256", "size")
            }
            for item in receipt["evidence"]
        ],
    }
    expected_launch_id = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    if receipt.get("launch_id") != expected_launch_id:
        raise RuntimeDiagnosisError(
            "V3 launch ID does not bind its lifecycle and evidence"
        )


def _load_receipt(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = _regular_file(path, "launch receipt", MAX_RECEIPT_BYTES)
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeDiagnosisError("launch receipt is not valid UTF-8 JSON") from exc
    if not isinstance(receipt, dict):
        raise RuntimeDiagnosisError("launch receipt must be an object")
    expected_schema = LAUNCH_RECEIPT_FORMATS.get(receipt.get("format"))
    if (
        expected_schema is None
        or receipt.get("schema_version") != expected_schema
        or SHA256_ID_RE.fullmatch(str(receipt.get("launch_id"))) is None
        or receipt.get("outcome")
        not in {"checkpoint-reached", "failed", "timed-out"}
        or not isinstance(receipt.get("evidence"), list)
    ):
        raise RuntimeDiagnosisError(
            "launch receipt has an unsupported or incomplete identity"
        )
    if expected_schema == 3:
        _validate_v3_receipt(receipt)
    resolved = path.expanduser().resolve()
    return receipt, {
        "uri": resolved.as_uri(),
        "sha256": sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _verified_evidence(
    receipt: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], list[str]]:
    records: list[dict[str, Any]] = []
    texts: list[tuple[str, str]] = []
    limitations: list[str] = []
    for index, item in enumerate(receipt["evidence"]):
        if not isinstance(item, dict):
            raise RuntimeDiagnosisError(
                f"launch evidence entry {index} must be an object"
            )
        label = item.get("label")
        state = item.get("state")
        if not isinstance(label, str) or not label:
            raise RuntimeDiagnosisError(
                f"launch evidence entry {index} lacks a label"
            )
        if state != "captured":
            records.append({
                "label": label,
                "state": "unavailable",
                "reason": str(item.get("reason", "not captured")),
            })
            continue
        expected_sha = item.get("sha256")
        expected_size = item.get("size")
        if (
            not isinstance(expected_sha, str)
            or SHA256_RE.fullmatch(expected_sha) is None
            or type(expected_size) is not int
            or expected_size < 0
        ):
            raise RuntimeDiagnosisError(
                f"captured evidence {label!r} lacks an exact identity"
            )
        path = _local_uri_path(
            item.get("capture_uri"),
            f"captured evidence {label!r}",
        )
        if path.is_symlink():
            raise RuntimeDiagnosisError(
                f"captured evidence {label!r} is a symbolic link"
            )
        resolved_path = path.expanduser().resolve()
        capture_uri = resolved_path.as_uri()
        if not resolved_path.is_file():
            records.append({
                "label": label,
                "state": "unavailable",
                "capture_uri": capture_uri,
                "expected_sha256": expected_sha,
                "expected_size": expected_size,
            })
            limitations.append(
                f"Captured evidence {label!r} is no longer available."
            )
            continue
        raw_text_evidence: bytes | None = None
        if expected_size <= MAX_EVIDENCE_TEXT_BYTES:
            raw_text_evidence = _regular_file(
                resolved_path,
                f"captured evidence {label!r}",
                MAX_EVIDENCE_TEXT_BYTES,
            )
            actual_sha = sha256(raw_text_evidence).hexdigest()
            actual_size = len(raw_text_evidence)
        else:
            actual_sha, actual_size = sha256_file(resolved_path)
        if actual_size != expected_size or actual_sha != expected_sha:
            raise RuntimeDiagnosisError(
                f"captured evidence {label!r} does not match its receipt"
            )
        record = {
            "label": label,
            "state": "verified",
            "capture_uri": capture_uri,
            "sha256": actual_sha,
            "size": actual_size,
        }
        records.append(record)
        if raw_text_evidence is None:
            limitations.append(
                f"Verified evidence {label!r} exceeds the text-analysis limit."
            )
            continue
        try:
            text = raw_text_evidence.decode("utf-8", errors="replace")
        except UnicodeError as exc:
            raise RuntimeDiagnosisError(
                f"verified evidence {label!r} cannot be decoded"
            ) from exc
        texts.append((label, text))
    return records, texts, limitations


def load_verified_runtime_evidence(
    launch_receipt: Path | str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[tuple[str, str]],
    list[str],
]:
    """Verify one retained receipt and return its bounded text evidence."""

    receipt, receipt_identity = _load_receipt(Path(launch_receipt))
    evidence, texts, limitations = _verified_evidence(receipt)
    return receipt, receipt_identity, evidence, texts, limitations


def _exception_chain(text: str) -> list[dict[str, Any]]:
    result = []
    for line in text.splitlines():
        match = EXCEPTION_RE.match(line.rstrip("\r"))
        if match is None:
            continue
        exception_type = match.group("type")
        simple = exception_type.rsplit(".", 1)[-1]
        if not any(
            marker in simple
            for marker in ("Exception", "Error", "Throwable", "Crash")
        ):
            continue
        result.append({
            "depth": len(result),
            "type": exception_type,
            "message": match.group("message"),
        })
    return result


def _injection_failure(text: str) -> dict[str, Any] | None:
    match = INJECTION_RE.search(text)
    if match is None:
        return None
    stack_match = MIXIN_STACK_RE.search(text)
    target_class = None
    mixin_class = None
    if stack_match is not None:
        target_class = stack_match.group("target")
        mixin_class = stack_match.group("mixin")
    return {
        "category": "mixin-injection",
        "kind": match.group("kind"),
        "handler": match.group("handler"),
        "config": match.group("config"),
        "mixin_simple_name": match.group("mixin"),
        "mixin_class": mixin_class,
        "owner_mod": match.group("mod"),
        "target_class": target_class,
        "succeeded": int(match.group("succeeded")),
        "required": int(match.group("required")),
        "scanned_targets": int(match.group("scanned")),
        "refmap": match.group("refmap"),
    }


def _select_failure_text(
    outcome: str,
    texts: list[tuple[str, str]],
) -> tuple[str | None, str]:
    if outcome != "failed":
        return None, ""
    priority = {
        "minecraft-crash-report": 0,
        "minecraft-latest-log": 1,
    }
    ordered = sorted(
        texts,
        key=lambda item: (priority.get(item[0], 2), item[0]),
    )
    for label, text in ordered:
        if label == "minecraft-crash-report":
            return label, text
    for label, text in ordered:
        if _injection_failure(text) is not None:
            return label, text
    return None, ""


def _log_observations(
    outcome: str,
    texts: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    if outcome == "failed":
        return []
    latest = next(
        (
            text
            for label, text in texts
            if label == "minecraft-latest-log"
        ),
        None,
    )
    if latest is None:
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for exception in _exception_chain(latest):
        exception_type = exception["type"]
        group = grouped.setdefault(exception_type, {
            "type": exception_type,
            "count": 0,
            "classification": "non-terminal-log-observation",
            "evidence_label": "minecraft-latest-log",
            "sample_messages": [],
        })
        group["count"] += 1
        message = exception["message"][:500]
        if (
            message not in group["sample_messages"]
            and len(group["sample_messages"]) < 3
        ):
            group["sample_messages"].append(message)
    return [grouped[key] for key in sorted(grouped)]


def _candidate_root_uris(receipt: dict[str, Any]) -> list[tuple[str, Any]]:
    launcher = receipt.get("launcher")
    projection = receipt.get("projection")
    candidates: list[tuple[str, Any]] = []
    if isinstance(launcher, dict):
        candidates.append(("launcher-projection", launcher.get("projection_uri")))
    if isinstance(projection, dict):
        candidates.extend((
            ("projection", projection.get("projection_uri")),
            ("materialized-source", projection.get("source_instance_uri")),
        ))
    return candidates


def _artifact_layout(
    path: Path,
) -> tuple[Path, Path, Path | None] | None:
    if path.is_symlink() or not path.is_dir():
        return None
    if path.name == "mods":
        mods = path
        minecraft = path.parent
        instance = (
            minecraft.parent if minecraft.name == ".minecraft" else None
        )
    elif (path / ".minecraft/mods").is_dir():
        instance = path
        minecraft = path / ".minecraft"
        mods = minecraft / "mods"
    elif (path / "mods").is_dir():
        minecraft = path
        mods = path / "mods"
        instance = path.parent if path.name == ".minecraft" else None
    else:
        return None
    if mods.is_symlink() or not mods.is_dir():
        return None
    return mods.resolve(), minecraft.resolve(), (
        instance.resolve() if instance is not None else None
    )


def _tree_identity(
    root: Path,
) -> tuple[dict[str, Any], dict[str, tuple[str, int]]]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeDiagnosisError(
            "materialized payload is not a regular directory"
        )
    entries: list[dict[str, Any]] = []
    hashes: dict[str, tuple[str, int]] = {}
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeDiagnosisError(
                f"materialized payload contains a symbolic link: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeDiagnosisError(
                f"materialized payload contains a special file: {relative}"
            )
        if len(entries) >= MAX_PROVENANCE_FILES:
            raise RuntimeDiagnosisError(
                "materialized payload exceeds the provenance file limit"
            )
        digest, size = sha256_file(path)
        entries.append({
            "mode": stat.S_IMODE(path.stat().st_mode),
            "path": relative,
            "sha256": digest,
            "size": size,
        })
        hashes[relative] = (digest, size)
    return {
        "tree_sha256": "sha256:" + sha256(
            _canonical_bytes(entries)
        ).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
    }, hashes


def _payload_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    summary = {
        key: value.get(key)
        for key in ("tree_sha256", "file_count", "total_bytes")
    }
    if (
        SHA256_ID_RE.fullmatch(str(summary["tree_sha256"])) is None
        or type(summary["file_count"]) is not int
        or summary["file_count"] < 0
        or type(summary["total_bytes"]) is not int
        or summary["total_bytes"] < 0
    ):
        return None
    return summary


def _unverified_provenance(
    reason: str,
) -> tuple[dict[str, Any], dict[str, tuple[str, int]]]:
    return {
        "state": "unverified",
        "basis": "current-local-artifacts",
        "reason": reason,
    }, {}


def _materialized_source_provenance(
    launch_receipt: dict[str, Any],
    instance: Path | None,
    minecraft: Path,
) -> tuple[dict[str, Any], dict[str, tuple[str, int]]]:
    if instance is None:
        return _unverified_provenance(
            "the artifact root is not a recognizable launcher instance"
        )
    projection = launch_receipt.get("projection")
    if not isinstance(projection, dict):
        return _unverified_provenance(
            "the launch receipt lacks projection identity"
        )
    try:
        source_instance = _local_uri_path(
            projection.get("source_instance_uri"),
            "materialized source instance",
        ).resolve()
    except RuntimeDiagnosisError as exc:
        return _unverified_provenance(str(exc))
    if source_instance != instance:
        return _unverified_provenance(
            "the artifact root is not the launch receipt's source instance"
        )
    expected = _payload_summary(
        projection.get("payload", {}).get("materialized")
        if isinstance(projection.get("payload"), dict)
        else None
    )
    if expected is None:
        return _unverified_provenance(
            "the launch receipt lacks materialized payload identity"
        )

    receipt_root = instance.parent / "receipts"
    try:
        if receipt_root.is_symlink():
            raise RuntimeDiagnosisError(
                "materialization receipt directory cannot be a symbolic link"
            )
        receipt_path = receipt_root / "packwiz-materialization-v2.json"
        raw = _regular_file(
            receipt_path,
            "materialization receipt",
            MAX_RECEIPT_BYTES,
        )
        materialization = json.loads(raw.decode("utf-8"))
    except (
        RuntimeDiagnosisError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        return _unverified_provenance(
            f"the materialization receipt cannot be trusted: {exc}"
        )
    if not isinstance(materialization, dict):
        return _unverified_provenance(
            "the materialization receipt is not an object"
        )
    recorded = _payload_summary(materialization.get("payload"))
    target = materialization.get("target")
    try:
        target_instance = _local_uri_path(
            target.get("instance_root_uri") if isinstance(target, dict) else None,
            "materialization target instance",
        ).resolve()
        payload_root = _local_uri_path(
            materialization.get("payload", {}).get("root_uri")
            if isinstance(materialization.get("payload"), dict)
            else None,
            "materialization payload root",
        ).resolve()
        target_receipt = _local_uri_path(
            target.get("receipt_uri") if isinstance(target, dict) else None,
            "materialization receipt target",
        ).resolve()
    except RuntimeDiagnosisError as exc:
        return _unverified_provenance(str(exc))
    binding_errors = []
    materialization_version = packwiz_materialization_version(materialization)
    if (
        materialization_version != 2
        or materialization.get("state") != "materialized"
    ):
        binding_errors.append("unsupported materialization receipt")
    elif not verify_packwiz_materialization_receipt_identity(materialization):
        binding_errors.append("materialization receipt identity is invalid")
    launch_materialization_id = launch_receipt.get("materialization_id")
    if SHA256_ID_RE.fullmatch(str(launch_materialization_id)) is None:
        binding_errors.append("launch materialization ID is missing")
    elif (
        materialization.get("materialization_id")
        != launch_materialization_id
    ):
        binding_errors.append("materialization ID differs from launch")
    if (
        target_instance != instance
        or payload_root != minecraft
        or target_receipt != receipt_path.resolve()
    ):
        binding_errors.append("materialization target path differs")
    if recorded != expected:
        binding_errors.append("materialization payload identity differs")
    if binding_errors:
        return {
            "state": "drifted",
            "basis": "materialization-receipt-and-tree",
            "reason": "; ".join(binding_errors),
            "receipt_uri": receipt_path.resolve().as_uri(),
            "receipt_sha256": sha256(raw).hexdigest(),
            "expected_payload": expected,
            "recorded_payload": recorded,
        }, {}
    try:
        observed, hashes = _tree_identity(minecraft)
    except RuntimeDiagnosisError as exc:
        return {
            "state": "drifted",
            "basis": "materialization-receipt-and-tree",
            "reason": str(exc),
            "receipt_uri": receipt_path.resolve().as_uri(),
            "receipt_sha256": sha256(raw).hexdigest(),
            "expected_payload": expected,
        }, {}
    if observed != expected:
        return {
            "state": "drifted",
            "basis": "materialization-receipt-and-tree",
            "reason": "current payload tree differs from the launch receipt",
            "receipt_uri": receipt_path.resolve().as_uri(),
            "receipt_sha256": sha256(raw).hexdigest(),
            "expected_payload": expected,
            "observed_payload": observed,
        }, hashes
    compatibility_patches = (
        projection.get("compatibility_patches")
        or launch_receipt.get("compatibility_patches")
    )
    if compatibility_patches:
        return {
            "state": "unverified",
            "basis": "materialization-receipt-and-tree",
            "reason": (
                "the launch applied compatibility patches after source "
                "materialization"
            ),
            "receipt_uri": receipt_path.resolve().as_uri(),
            "receipt_sha256": sha256(raw).hexdigest(),
            "expected_payload": expected,
            "observed_payload": observed,
        }, hashes
    return {
        "state": "verified",
        "basis": "materialization-receipt-and-tree",
        "materialization_id": materialization["materialization_id"],
        "receipt_uri": receipt_path.resolve().as_uri(),
        "receipt_sha256": sha256(raw).hexdigest(),
        "expected_payload": expected,
        "observed_payload": observed,
    }, hashes


def _artifact_roots(
    receipt: dict[str, Any],
    explicit_roots: Sequence[Path | str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    limitations: list[str] = []
    seen: set[Path] = set()
    if explicit_roots:
        candidates = [
            ("explicit", Path(root).expanduser())
            for root in explicit_roots
        ]
    else:
        candidates = []
        for source, uri in _candidate_root_uris(receipt):
            if uri is None:
                continue
            try:
                candidates.append((source, _local_uri_path(uri, source)))
            except RuntimeDiagnosisError as exc:
                limitations.append(str(exc))

    for source, candidate in candidates:
        layout = _artifact_layout(candidate)
        if layout is None:
            records.append({
                "source": source,
                "state": "unavailable",
                "uri": candidate.absolute().as_uri(),
            })
            continue
        mods, minecraft, instance = layout
        if mods in seen:
            continue
        seen.add(mods)
        provenance, hashes = _materialized_source_provenance(
            receipt,
            instance,
            minecraft,
        )
        record = {
            "source": source,
            "state": "available",
            "mods_uri": mods.as_uri(),
            "provenance": provenance,
        }
        records.append(record)
        roots.append({
            "mods": mods,
            "minecraft": minecraft,
            "source": source,
            "provenance": provenance,
            "hashes": hashes,
        })
        if provenance["state"] != "verified":
            limitations.append(
                f"Artifact root {source!r} is not launch-bound: "
                f"{provenance['reason']}."
            )
        if not explicit_roots:
            break
    if not roots:
        limitations.append(
            "No retained or explicit mods directory is available for "
            "artifact inspection."
        )
    return records, roots, limitations


def _entry_record(
    jar: Path,
    jar_sha256: str,
    jar_size: int,
    role: str,
    entry_name: str,
    payload: bytes,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "role": role,
        "artifact_uri": jar.resolve().as_uri(),
        "artifact_sha256": jar_sha256,
        "artifact_size": jar_size,
        "entry": entry_name,
        "entry_sha256": sha256(payload).hexdigest(),
        "entry_size": len(payload),
    }
    if entry_name.endswith(".class"):
        try:
            parsed = parse_class_file(payload)
            record["class"] = {
                "name": parsed["class_name"],
                "major_version": parsed["major_version"],
            }
        except (ClassFileError, RecursionError) as exc:
            record["class_metadata_error"] = str(exc)
    return record


def _scan_artifacts(
    artifact_roots: Sequence[dict[str, Any]],
    failure: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, list[bytes]], list[str]]:
    if failure is None:
        return [], {}, []
    target_class = failure.get("target_class")
    mixin_class = failure.get("mixin_class")
    config = failure.get("config")
    requested: dict[str, str] = {}
    if isinstance(target_class, str) and target_class:
        requested["target-class"] = target_class.replace(".", "/") + ".class"
    if isinstance(mixin_class, str) and mixin_class:
        requested["mixin-class"] = mixin_class.replace(".", "/") + ".class"
    if isinstance(config, str) and config:
        requested["mixin-config"] = config
    if not requested:
        return [], {}, []

    archive_roots = sorted(
        (
            (path, root)
            for root in artifact_roots
            for path in root["mods"].iterdir()
            if path.name.casefold().endswith(".jar")
        ),
        key=lambda item: item[0].as_posix().casefold(),
    )
    if len(archive_roots) > MAX_MOD_ARCHIVES:
        raise RuntimeDiagnosisError(
            f"mods roots contain more than {MAX_MOD_ARCHIVES} JARs"
        )
    observations: list[dict[str, Any]] = []
    payloads: dict[str, list[bytes]] = {}
    limitations: list[str] = []
    for jar, root in archive_roots:
        if jar.is_symlink() or not jar.is_file():
            limitations.append(
                f"Skipped non-regular mod archive {jar.name!r}."
            )
            continue
        try:
            with zipfile.ZipFile(jar) as archive:
                names = archive.namelist()
                hits = [
                    (role, entry)
                    for role, entry in requested.items()
                    if entry in names
                ]
                if not hits:
                    continue
                relative = jar.relative_to(root["minecraft"]).as_posix()
                cached_identity = root["hashes"].get(relative)
                if cached_identity is None:
                    jar_sha, jar_size = sha256_file(jar)
                else:
                    jar_sha, jar_size = cached_identity
                for role, entry in hits:
                    if names.count(entry) != 1:
                        limitations.append(
                            f"Skipped duplicate archive entry {entry!r} in "
                            f"{jar.name!r}."
                        )
                        continue
                    info = archive.getinfo(entry)
                    if info.file_size > MAX_CLASS_BYTES:
                        limitations.append(
                            f"Skipped oversized archive entry {entry!r} in "
                            f"{jar.name!r}."
                        )
                        continue
                    payload = archive.read(info)
                    observation = _entry_record(
                        jar,
                        jar_sha,
                        jar_size,
                        role,
                        entry,
                        payload,
                    )
                    observation["root_source"] = root["source"]
                    observation["launch_binding"] = root["provenance"][
                        "state"
                    ]
                    observations.append(observation)
                    payloads.setdefault(role, []).append(payload)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            limitations.append(
                f"Could not inspect mod archive {jar.name!r}: "
                f"{type(exc).__name__}."
            )
    observations.sort(key=lambda item: (
        item["role"],
        item["artifact_uri"],
        item["entry"],
    ))
    return observations, payloads, limitations


def _unique_payload(
    payloads: dict[str, list[bytes]],
    role: str,
) -> bytes | None:
    unique = {
        sha256(payload).hexdigest(): payload
        for payload in payloads.get(role, [])
    }
    return next(iter(unique.values())) if len(unique) == 1 else None


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _handler_parts(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, None
    separator = value.find("(")
    if separator <= 0:
        return value, None
    return value[:separator], value[separator:]


def _mixin_discriminator_facts(
    failure: dict[str, Any],
    payloads: dict[str, list[bytes]],
) -> dict[str, Any] | None:
    mixin_payload = _unique_payload(payloads, "mixin-class")
    target_payload = _unique_payload(payloads, "target-class")
    if mixin_payload is None or target_payload is None:
        return None
    try:
        mixin = parse_class_file(mixin_payload)
        target = parse_class_file(target_payload)
    except (ClassFileError, RecursionError):
        return None

    handler_name, handler_descriptor = _handler_parts(failure.get("handler"))
    handlers = [
        method
        for method in mixin["methods"]
        if method["name"] == handler_name
        and (
            handler_descriptor is None
            or method["descriptor"] == handler_descriptor
        )
    ]
    annotations = [
        annotation
        for method in handlers
        for annotation in method["annotations"]
        if annotation["descriptor"].endswith(
            "/mixin/injection/ModifyVariable;"
        )
    ]
    if len(annotations) != 1:
        return None
    values = annotations[0]["values"]
    requested_names = _string_values(values.get("name"))
    selectors = _string_values(values.get("method"))
    selector_names = {
        selector.split("(", 1)[0]
        for selector in selectors
        if selector
    }
    target_methods = [
        method
        for method in target["methods"]
        if method["name"] in selector_names
    ]
    candidates = []
    complete_names = True
    for method in target_methods:
        try:
            argument_names = method_argument_names(method)
        except ClassFileError:
            continue
        if any(name is None for name in argument_names):
            complete_names = False
        candidates.append({
            "name": method["name"],
            "descriptor": method["descriptor"],
            "argument_names": argument_names,
        })
    available_names = sorted({
        name
        for candidate in candidates
        for name in candidate["argument_names"]
        if isinstance(name, str)
    })
    mismatch = bool(
        requested_names
        and candidates
        and complete_names
        and not set(requested_names).intersection(available_names)
    )
    return {
        "annotation": annotations[0]["descriptor"],
        "method_selectors": selectors,
        "requested_names": requested_names,
        "args_only": bool(values.get("argsOnly", 0)),
        "require": values.get("require"),
        "target_methods": candidates,
        "available_argument_names": available_names,
        "named_discriminator_mismatch": mismatch,
    }


def _observation_entry_hash(
    observations: Iterable[dict[str, Any]],
    role: str,
) -> str | None:
    hashes = {
        item["entry_sha256"]
        for item in observations
        if item.get("role") == role
    }
    return next(iter(hashes)) if len(hashes) == 1 else None


def _artifact_binding_state(
    observations: Iterable[dict[str, Any]],
) -> str:
    required_roles = {"target-class", "mixin-class"}
    selected = [
        item
        for item in observations
        if item.get("role") in required_roles
    ]
    if {item.get("role") for item in selected} != required_roles:
        return "unverified"
    states = {item.get("launch_binding") for item in selected}
    if states == {"verified"}:
        return "verified"
    if "drifted" in states:
        return "drifted"
    return "unverified"


def _guidance_fingerprint(
    context: dict[str, Any],
    failure: dict[str, Any],
    observations: list[dict[str, Any]],
    discriminator: dict[str, Any] | None,
    category: str,
) -> dict[str, Any]:
    project = context["workspace_context"]["project"]
    fingerprint: dict[str, Any] = {
        "category": category,
        "project_name": project.get("name"),
        "project_version": project.get("version"),
        "mixin_config": failure.get("config"),
        "mixin_class": failure.get("mixin_class"),
        "target_class": failure.get("target_class"),
        "target_entry_sha256": _observation_entry_hash(
            observations, "target-class"
        ),
        "mixin_entry_sha256": _observation_entry_hash(
            observations, "mixin-class"
        ),
    }
    if discriminator is not None:
        fingerprint["requested_names"] = discriminator["requested_names"]
        fingerprint["available_argument_names"] = discriminator[
            "available_argument_names"
        ]
    return fingerprint


def _load_guidance(
    configuration: WorkbenchConfiguration,
    fingerprint: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_path = configuration.pack_document.source.path
    profile = configuration.pack_document.values
    declarations = profile.get("diagnostic_patterns", [])
    if not isinstance(declarations, (list, tuple)):
        raise RuntimeDiagnosisError("diagnostic_patterns must be a list")
    result = []
    profile_root = profile_path.parent.resolve()
    for declaration in declarations:
        if not isinstance(declaration, Mapping):
            raise RuntimeDiagnosisError(
                "diagnostic pattern declaration must be an object"
            )
        maturity = declaration.get("maturity")
        if not isinstance(maturity, str) or not maturity:
            raise RuntimeDiagnosisError(
                "diagnostic pattern declaration lacks maturity"
            )
        spec_value = declaration.get("spec")
        if not isinstance(spec_value, str) or not spec_value:
            raise RuntimeDiagnosisError(
                "diagnostic pattern declaration lacks spec"
            )
        spec_path = profile_path.parent / spec_value
        resolved = spec_path.resolve()
        try:
            resolved.relative_to(profile_root)
        except ValueError as exc:
            raise RuntimeDiagnosisError(
                "diagnostic pattern escapes its pack profile"
            ) from exc
        spec_raw = _regular_file(
            spec_path,
            "diagnostic guidance",
            MAX_GUIDANCE_BYTES,
        )
        try:
            spec = json.loads(spec_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeDiagnosisError(
                "diagnostic guidance is not valid UTF-8 JSON"
            ) from exc
        related = (
            spec.get("related_experiments", [])
            if isinstance(spec, dict)
            else None
        )
        if (
            not isinstance(spec, dict)
            or spec.get("format") != GUIDANCE_FORMAT
            or spec.get("schema_version") != 1
            or not isinstance(spec.get("pattern_id"), str)
            or not isinstance(spec.get("match"), dict)
            or not spec["match"]
            or not all(
                isinstance(key, str) and key
                for key in spec["match"]
            )
            or not isinstance(spec.get("interpretation"), str)
            or not isinstance(spec.get("developer_actions"), list)
            or not all(
                isinstance(action, str) and action
                for action in spec["developer_actions"]
            )
            or not isinstance(related, list)
            or not all(
                isinstance(experiment, str) and experiment
                for experiment in related
            )
            or declaration.get("pattern_id") != spec.get("pattern_id")
        ):
            raise RuntimeDiagnosisError(
                "diagnostic guidance has an unsupported shape"
            )
        if all(
            key in fingerprint and fingerprint[key] == value
            for key, value in spec["match"].items()
        ):
            result.append({
                "pattern_id": spec["pattern_id"],
                "maturity": maturity,
                "spec_sha256": sha256(spec_raw).hexdigest(),
                "interpretation": spec["interpretation"],
                "developer_actions": list(spec["developer_actions"]),
                "related_experiments": list(related),
            })
    return result


def _runtime_summary(receipt: dict[str, Any], text: str) -> dict[str, Any]:
    java = receipt.get("java")
    result: dict[str, Any] = {"components": []}
    if isinstance(java, dict):
        probe = java.get("probe")
        result["java"] = {
            "runtime_id": java.get("runtime_id"),
            "runtime_version": (
                probe.get("runtime_version")
                if isinstance(probe, dict)
                else None
            ),
            "vendor": (
                probe.get("vendor") if isinstance(probe, dict) else None
            ),
        }
    fml = re.search(
        r"(?m)^\s*FML: .*?Cleanroom (?P<cleanroom>\S+) "
        r"(?P<count>\d+) mods loaded, (?P<active>\d+) mods active",
        text,
    )
    if fml is not None:
        result["components"].append({
            "id": "cleanroom",
            "version": fml.group("cleanroom"),
        })
        result["mod_count"] = {
            "loaded": int(fml.group("count")),
            "active": int(fml.group("active")),
        }
    else:
        cleanroom = re.search(
            r"/cleanroom/(?P<version>[^/\s]+)/cleanroom-[^/\s]+\.jar",
            text,
        )
        if cleanroom is not None:
            result["components"].append({
                "id": "cleanroom",
                "version": cleanroom.group("version"),
            })
        loaded = re.search(
            r"Forge Mod Loader has successfully loaded (?P<count>\d+) mods",
            text,
        )
        if loaded is not None:
            result["mod_count"] = {
                "loaded": int(loaded.group("count")),
            }
    return result


def _checkpoint_record(receipt: dict[str, Any]) -> dict[str, str] | None:
    observation = receipt.get("observation")
    value = (
        observation.get("checkpoint")
        if isinstance(observation, dict)
        else None
    )
    if not isinstance(value, dict):
        return None
    required = ("id", "marker", "source")
    if any(
        not isinstance(value.get(key), str) or not value[key]
        for key in required
    ):
        return None
    return {key: value[key] for key in required}


def _wrappers(
    chain: list[dict[str, Any]],
    failure: dict[str, Any] | None,
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if failure is None or not any(
        item.get("role") == "target-class" for item in observations
    ):
        return []
    target = failure.get("target_class")
    if not isinstance(target, str):
        return []
    binding = _artifact_binding_state(observations)
    forms = {target, target.replace("/", ".")}
    result = []
    for item in chain:
        simple = item["type"].rsplit(".", 1)[-1]
        if simple not in {"NoClassDefFoundError", "ClassNotFoundException"}:
            continue
        if not any(form in item["message"] for form in forms):
            continue
        result.append({
            "type": item["type"],
            "subject": target,
            "classification": "transformation-wrapper",
            "artifact_state": (
                "present-launch-bound"
                if binding == "verified"
                else "present-unverified"
            ),
            "launch_binding": binding,
            "reason": (
                "The target class exists in an inspected mod archive and a "
                "deeper Mixin transformation error prevented definition."
            ),
        })
    return result


def diagnose_project_runtime(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launch_receipt: Path | str,
    artifact_roots: Sequence[Path | str] = (),
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Diagnose one retained launch without changing any runtime state."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeDiagnosisError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeDiagnosisError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    context = inspect_project(
        suite,
        workspace_root,
        configuration=active_configuration,
    )
    (
        receipt,
        receipt_identity,
        evidence,
        texts,
        limitations,
    ) = load_verified_runtime_evidence(launch_receipt)
    receipt_limitations = receipt.get("limitations", [])
    if isinstance(receipt_limitations, list):
        limitations.extend(
            limitation
            for limitation in receipt_limitations
            if isinstance(limitation, str) and limitation
        )
    evidence_label, failure_text = _select_failure_text(
        receipt["outcome"],
        texts,
    )
    chain = _exception_chain(failure_text)
    failure = _injection_failure(failure_text)
    log_observations = _log_observations(receipt["outcome"], texts)
    if failure is None:
        root_records: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        payloads: dict[str, list[bytes]] = {}
    else:
        root_records, roots, root_limits = _artifact_roots(
            receipt,
            artifact_roots,
        )
        limitations.extend(root_limits)
        observations, payloads, artifact_limits = _scan_artifacts(
            roots,
            failure,
        )
        limitations.extend(artifact_limits)
    discriminator = (
        _mixin_discriminator_facts(failure, payloads)
        if failure is not None
        else None
    )
    artifact_binding = _artifact_binding_state(observations)

    finding: dict[str, Any] | None = None
    category = "mixin-injection"
    if failure is not None:
        finding = {
            "finding_id": "workbench:runtime:mixin-injection-failure",
            "category": category,
            "severity": "blocking",
            "confidence": "observed",
            "summary": (
                "A required Mixin injection failed before the target class "
                "could be defined."
            ),
            "facts": {
                "handler": failure["handler"],
                "succeeded": failure["succeeded"],
                "required": failure["required"],
                "scanned_targets": failure["scanned_targets"],
            },
            "artifact_binding": {
                "state": artifact_binding,
                "basis": (
                    "launch-bound-artifacts"
                    if artifact_binding == "verified"
                    else "current-local-artifacts"
                ),
            },
        }
        if (
            discriminator is not None
            and discriminator["named_discriminator_mismatch"]
        ):
            category = "mixin-local-variable-discriminator"
            finding.update({
                "finding_id": (
                    "workbench:runtime:"
                    "mixin-local-variable-discriminator-mismatch"
                ),
                "category": category,
                "confidence": (
                    "confirmed"
                    if artifact_binding == "verified"
                    else "observed"
                ),
                "summary": (
                    "A required @ModifyVariable names a local variable that "
                    "is absent from the inspected target method arguments."
                ),
                "facts": discriminator,
            })
        fingerprint = _guidance_fingerprint(
            context,
            failure,
            observations,
            discriminator,
            category,
        )
        guidance = (
            _load_guidance(active_configuration, fingerprint)
            if artifact_binding == "verified"
            else []
        )
        if guidance:
            finding["guidance"] = guidance

    checkpoint = _checkpoint_record(receipt)
    if finding is not None:
        state = "blocked"
    elif receipt["outcome"] == "checkpoint-reached" and checkpoint is not None:
        state = "checkpoint-reached"
    else:
        state = "inconclusive"
    if receipt["outcome"] == "checkpoint-reached" and checkpoint is None:
        limitations.append(
            "The launch claims checkpoint-reached without checkpoint identity."
        )
    if not texts:
        limitations.append(
            "No verified text evidence was available for causal analysis."
        )
    if failure is not None and not observations:
        limitations.append(
            "The failure was parsed, but its target artifacts were not found."
        )
    if (
        discriminator is not None
        and discriminator["named_discriminator_mismatch"]
        and artifact_binding != "verified"
    ):
        limitations.append(
            "The named-variable mismatch is observed in current local "
            "artifacts but is not bound to the launched payload."
        )

    runtime_text = next(
        (
            text
            for label, text in texts
            if label == "minecraft-latest-log"
        ),
        failure_text,
    )

    workspace_context = context["workspace_context"]
    report: dict[str, Any] = {
        "format": DIAGNOSIS_FORMAT,
        "schema_version": 2,
        "operation_class": "read-only",
        "authority": {
            "classification": "integration-observation",
            "normative": False,
            "atlas_publication": False,
            "sentinel_policy_finding": False,
        },
        "state": state,
        "checkpoint": checkpoint,
        "source": {
            "workspace": {
                "root_uri": workspace_context["workspace"]["root_uri"],
                "revision": workspace_context["workspace"]["revision"],
            },
            "project": {
                "name": workspace_context["project"]["name"],
                "version": workspace_context["project"]["version"],
                "minecraft_version": workspace_context["project"][
                    "minecraft_version"
                ],
            },
            "pack_profile": {
                "profile_family_id": workspace_context["pack"][
                    "profile_family_id"
                ],
                "selected_profile": workspace_context["pack"][
                    "selected_profile"
                ],
                "document_sha256": workspace_context["pack"][
                    "document_sha256"
                ],
            },
            "launch_receipt": receipt_identity,
            "launch_id": receipt["launch_id"],
            "launch_outcome": receipt["outcome"],
        },
        "runtime": _runtime_summary(receipt, runtime_text),
        "evidence": evidence,
        "analysis_evidence_label": evidence_label,
        "exception_chain": chain,
        "log_observations": log_observations,
        "primary_failure": failure,
        "wrappers": _wrappers(chain, failure, observations),
        "artifact_roots": root_records,
        "artifact_observations": observations,
        "findings": [] if finding is None else [finding],
        "limitations": sorted(set(limitations)),
    }
    report["diagnosis_id"] = "sha256:" + sha256(
        _canonical_bytes(report)
    ).hexdigest()
    return report
