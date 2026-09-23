"""V1 stage-bound runtime snapshot contract and builders."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any, Mapping, Sequence


SNAPSHOT_FORMAT = "workbench-crucible-stage-snapshot-v1"
SNAPSHOT_SCHEMA_VERSION = 1


class StageSnapshotError(ValueError):
    """A value cannot truthfully represent a Crucible stage snapshot."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def registry_observation(
    *,
    stage: str,
    semantic_descriptor: Mapping[str, Any],
    registry_state: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor = _semantic_descriptor(semantic_descriptor)
    value: dict[str, Any] = {
        "runtime_record_id": "",
        "record_kind": "registry-observation",
        "semantic_descriptor": descriptor,
        "evidence_state": "runtime-observed",
        "stage": stage,
        "registry_state": dict(registry_state),
        "provenance": {"authority": "Crucible", **dict(provenance)},
    }
    value["runtime_record_id"] = _content_id(
        "workbench-crucible-registry-observation:sha256:",
        {key: item for key, item in value.items() if key != "runtime_record_id"},
    )
    return value


def effect_observation(
    *,
    stage: str,
    semantic_descriptor: Mapping[str, Any],
    operation: str,
    effect_state: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor = _semantic_descriptor(semantic_descriptor)
    value: dict[str, Any] = {
        "runtime_record_id": "",
        "record_kind": "effect-observation",
        "semantic_descriptor": descriptor,
        "evidence_state": "runtime-observed",
        "stage": stage,
        "operation": operation,
        "effect_state": dict(effect_state),
        "provenance": {"authority": "Crucible", **dict(provenance)},
    }
    value["runtime_record_id"] = _content_id(
        "workbench-crucible-effect-observation:sha256:",
        {key: item for key, item in value.items() if key != "runtime_record_id"},
    )
    return value


def build_stage_snapshot(
    *,
    pack_profile_id: str,
    platform_profile_id: str,
    stage: str,
    registry: Sequence[Mapping[str, Any]],
    effects: Sequence[Mapping[str, Any]],
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal observations from one exact lifecycle stage."""

    value: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": "",
        "authority": {
            "owner": "Crucible",
            "claim": "observed registry and effect state at one lifecycle stage",
            "source_authority": "none",
            "playability_authority": "none",
        },
        "binding": {
            "pack_profile_id": pack_profile_id,
            "platform_profile_id": platform_profile_id,
            "stage": stage,
            "capture": dict(capture),
        },
        "registry": [dict(row) for row in registry],
        "effects": [dict(row) for row in effects],
        "summary": {
            "registry_records": len(registry),
            "effect_records": len(effects),
            "effects_by_operation": dict(
                sorted(Counter(row["operation"] for row in effects).items())
            ),
        },
        "limitations": [
            "The snapshot establishes only what the named Crucible capture observed at the bound stage.",
            "Atlas may derive conclusions from this snapshot but cannot rewrite it into Atlas-owned runtime truth.",
        ],
    }
    value["snapshot_id"] = _snapshot_identity(value)
    return validate_stage_snapshot(value)


def validate_stage_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StageSnapshotError("stage snapshot must be an object")
    required = {
        "format",
        "schema_version",
        "snapshot_id",
        "authority",
        "binding",
        "registry",
        "effects",
        "summary",
        "limitations",
    }
    if set(value) != required:
        raise StageSnapshotError("stage snapshot has unexpected keys")
    if value["format"] != SNAPSHOT_FORMAT or value["schema_version"] != 1:
        raise StageSnapshotError("unsupported stage snapshot format")
    if value["snapshot_id"] != _snapshot_identity(value):
        raise StageSnapshotError("stage snapshot identity does not match content")
    authority = value["authority"]
    if not isinstance(authority, Mapping) or authority.get("owner") != "Crucible":
        raise StageSnapshotError("runtime snapshot authority must remain Crucible")
    if authority.get("playability_authority") != "none":
        raise StageSnapshotError("runtime snapshot cannot claim playability authority")
    binding = value["binding"]
    if not isinstance(binding, Mapping) or not isinstance(binding.get("stage"), str):
        raise StageSnapshotError("runtime snapshot stage binding is missing")
    stage = binding["stage"]
    record_ids: list[str] = []
    for collection, kind in (("registry", "registry-observation"), ("effects", "effect-observation")):
        rows = value[collection]
        if not isinstance(rows, list):
            raise StageSnapshotError(f"snapshot {collection} must be a list")
        for row in rows:
            _validate_record(row, kind=kind, stage=stage)
            record_ids.append(row["runtime_record_id"])
    if len(record_ids) != len(set(record_ids)):
        raise StageSnapshotError("runtime observation identities are duplicate")
    summary = value["summary"]
    if not isinstance(summary, Mapping):
        raise StageSnapshotError("stage snapshot summary is malformed")
    if summary.get("registry_records") != len(value["registry"]):
        raise StageSnapshotError("stage snapshot registry count is stale")
    if summary.get("effect_records") != len(value["effects"]):
        raise StageSnapshotError("stage snapshot effect count is stale")
    return dict(value)


def _validate_record(value: Any, *, kind: str, stage: str) -> None:
    if not isinstance(value, Mapping):
        raise StageSnapshotError("runtime observation must be an object")
    common = {
        "runtime_record_id",
        "record_kind",
        "semantic_descriptor",
        "evidence_state",
        "stage",
        "provenance",
    }
    extra = {"registry_state"} if kind == "registry-observation" else {"operation", "effect_state"}
    if set(value) != common | extra:
        raise StageSnapshotError("runtime observation has unexpected keys")
    if value.get("record_kind") != kind or value.get("evidence_state") != "runtime-observed":
        raise StageSnapshotError("runtime observation kind or evidence state is invalid")
    if value.get("stage") != stage:
        raise StageSnapshotError("runtime observation escapes the snapshot stage")
    _semantic_descriptor(value["semantic_descriptor"])
    provenance = value.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("authority") != "Crucible":
        raise StageSnapshotError("runtime observation provenance lost Crucible authority")
    prefix = (
        "workbench-crucible-registry-observation:sha256:"
        if kind == "registry-observation"
        else "workbench-crucible-effect-observation:sha256:"
    )
    payload = {key: item for key, item in value.items() if key != "runtime_record_id"}
    if value.get("runtime_record_id") != _content_id(prefix, payload):
        raise StageSnapshotError("runtime observation identity does not match content")


def _semantic_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"domain", "kind", "key"}:
        raise StageSnapshotError("semantic descriptor must contain domain, kind, and key")
    if not isinstance(value["domain"], str) or not value["domain"]:
        raise StageSnapshotError("semantic descriptor domain is missing")
    if not isinstance(value["kind"], str) or not value["kind"]:
        raise StageSnapshotError("semantic descriptor kind is missing")
    if not isinstance(value["key"], Mapping) or not value["key"]:
        raise StageSnapshotError("semantic descriptor key is missing")
    return {"domain": value["domain"], "kind": value["kind"], "key": dict(value["key"])}


def _snapshot_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("snapshot_id", None)
    return _content_id("workbench-crucible-stage-snapshot:sha256:", payload)
