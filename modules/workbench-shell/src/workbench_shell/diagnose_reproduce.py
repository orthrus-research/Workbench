"""Evidence-bounded diagnosis and deterministic reproduction capsules.

This module composes the existing live-console owner port.  It does not infer
causality, hydrate dependencies, or execute archive-provided shell commands.
Raw console bytes remain owned by the retained session; diagnosis stores exact
navigation ranges and capsules carry only reviewed canonical projections.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Callable, Iterable, Mapping
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

from jsonschema import Draft202012Validator, FormatChecker

from workbench_core.sessions import (
    SessionError,
    live_console_execution_reference,
    read_live_console_owner_artifacts,
    resolve_live_console_owner_reference,
)


DIAGNOSIS_FORMAT = "workbench-diagnosis-v1"
CAPSULE_FORMAT = "workbench-reproduction-capsule-manifest-v1"
REPLAY_FORMAT = "workbench-reproduction-capsule-replay-v1"
MAX_CAPSULE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 32
_ACTION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,255}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|api[-_]?key|access[-_]?token|client[-_]?secret|authorization)\s*[:=]"
)
_SECRET_KEYS = frozenset(
    {
        "apikey",
        "apitoken",
        "accesstoken",
        "authorization",
        "authorizationheader",
        "clientsecret",
        "credential",
        "credentials",
        "password",
        "passwd",
        "refreshtoken",
        "secret",
        "token",
    }
)
_REQUIRED_EXCLUSIONS = frozenset(
    {"credentials", "protected-binaries", "personal-worlds"}
)
_SCHEMA_ROOT = _module_resource_root(__file__, 'workbench-shell') / "schemas"


class DiagnoseReproduceV2Error(RuntimeError):
    """Diagnosis or capsule custody failed closed."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise DiagnoseReproduceV2Error("record is not canonical JSON data") from exc


def _contains_sensitive_data(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if (
                normalized in _SECRET_KEYS
                and item is not None
                and item != ""
                and item is not False
            ):
                return True
            if _contains_sensitive_data(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_data(item) for item in value)
    return isinstance(value, str) and _SECRET_RE.search(value) is not None


def _content_id(prefix: str, value: Mapping[str, Any], field: str) -> str:
    projection = dict(value)
    projection.pop(field, None)
    return prefix + hashlib.sha256(_canonical_bytes(projection)).hexdigest()


def _validate_schema(name: str, value: Any, label: str) -> None:
    path = _SCHEMA_ROOT / name
    try:
        raw = path.read_bytes()
        if not 1 <= len(raw) <= 2 * 1024 * 1024:
            raise ValueError("schema size is outside its owner bound")
        schema = json.loads(raw.decode("utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(value),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise DiagnoseReproduceV2Error(f"{label} schema is unavailable") from exc
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise DiagnoseReproduceV2Error(
            f"{label} violates its owner schema at {location}: {error.message}"
        )


def _stable_owner_ref(value: Mapping[str, Any]) -> dict[str, str]:
    fields = (
        "owner_id",
        "record_id",
        "record_kind",
        "uri",
        "digest",
        "last_verified_state",
    )
    result: dict[str, str] = {}
    for field in fields:
        item = value.get(field)
        if not isinstance(item, str) or not item:
            raise DiagnoseReproduceV2Error(
                f"live-console owner reference lacks stable {field}"
            )
        result[field] = item
    if not _DIGEST_RE.fullmatch(result["digest"]):
        raise DiagnoseReproduceV2Error("live-console owner digest is invalid")
    return result


def _event_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "event_id",
        "sequence",
        "kind",
        "severity",
        "subsystem",
        "message",
        "stream",
        "artifact",
        "byte_start",
        "byte_end",
        "boundary",
    }
    if set(row) != required:
        raise DiagnoseReproduceV2Error("live-console event projection drifted")
    return {
        "event_id": row["event_id"],
        "sequence": row["sequence"],
        "kind": row["kind"],
        "severity": row["severity"],
        "subsystem": row["subsystem"],
        "message": row["message"],
        "raw_range": {
            "stream": row["stream"],
            "artifact": row["artifact"],
            "byte_start": row["byte_start"],
            "byte_end": row["byte_end"],
            "boundary": row["boundary"],
        },
    }


def _claim(row: Mapping[str, Any], state: str) -> dict[str, Any]:
    result = dict(row)
    result["claim_state"] = state
    result["owner_id"] = "workbench-shell"
    return result


def _owner_classification(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exact owner-returned semantic classification.

    The generic diagnosis owner may retain these observations, but it does not
    reinterpret them.  V1 currently admits only the Cleanroom Dev Loop stage
    record whose receipt is independently validated by its owner port.
    """

    fields = {
        "classification_id",
        "claim_state",
        "owner_id",
        "owner_record_id",
        "owner_record_kind",
        "owner_record_uri",
        "owner_record_digest",
        "stage",
        "state",
        "required_markers",
        "observed_markers",
        "effective_exit_code",
        "cleanup_contained",
        "artifact_digest",
        "detail",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DiagnoseReproduceV2Error(
            "owner semantic classification is not a closed record"
        )
    result = dict(value)
    if (
        result["classification_id"] != "cleanroom-dev-loop-stage"
        or result["claim_state"] != "observed"
        or result["owner_id"] != "workbench-shell"
        or result["owner_record_kind"]
        != "workbench-cleanroom-dev-loop-receipt"
        or result["stage"] not in {"build", "client", "server"}
        or result["state"] not in {"not-run", "passed", "failed"}
    ):
        raise DiagnoseReproduceV2Error(
            "owner semantic classification authority changed"
        )
    for field in ("owner_record_id", "owner_record_uri"):
        item = result[field]
        if not isinstance(item, str) or not item or len(item) > 8192:
            raise DiagnoseReproduceV2Error(
                f"owner semantic classification {field} is invalid"
            )
    if not _DIGEST_RE.fullmatch(result["owner_record_digest"]):
        raise DiagnoseReproduceV2Error(
            "owner semantic classification receipt digest is invalid"
        )
    for field in ("required_markers", "observed_markers"):
        items = result[field]
        if (
            not isinstance(items, list)
            or len(items) > 32
            or any(not isinstance(item, str) or not item or len(item) > 256 for item in items)
            or len(items) != len(set(items))
        ):
            raise DiagnoseReproduceV2Error(
                f"owner semantic classification {field} is invalid"
            )
    exit_code = result["effective_exit_code"]
    if exit_code is not None and (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not 0 <= exit_code <= 255
    ):
        raise DiagnoseReproduceV2Error(
            "owner semantic classification exit code is invalid"
        )
    if result["cleanup_contained"] is not None and not isinstance(
        result["cleanup_contained"], bool
    ):
        raise DiagnoseReproduceV2Error(
            "owner semantic classification cleanup state is invalid"
        )
    artifact = result["artifact_digest"]
    if artifact is not None and (
        not isinstance(artifact, str) or not _DIGEST_RE.fullmatch(artifact)
    ):
        raise DiagnoseReproduceV2Error(
            "owner semantic classification artifact digest is invalid"
        )
    detail = result["detail"]
    if not isinstance(detail, str) or not detail or len(detail) > 8192:
        raise DiagnoseReproduceV2Error(
            "owner semantic classification detail is invalid"
        )
    return result


def _next_experiment(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "action_id",
        "arguments",
        "context_digest",
        "mutation",
    }:
        raise DiagnoseReproduceV2Error("next experiment is not a closed action")
    result = dict(value)
    if (
        not isinstance(result["action_id"], str)
        or not _ACTION_RE.fullmatch(result["action_id"])
        or not isinstance(result["arguments"], Mapping)
        or len(result["arguments"]) > 64
        or not isinstance(result["context_digest"], str)
        or not _DIGEST_RE.fullmatch(result["context_digest"])
        or result["mutation"] not in {"read-only", "isolated-target-only"}
    ):
        raise DiagnoseReproduceV2Error("next experiment is invalid")
    result["arguments"] = dict(result["arguments"])
    if len(_canonical_bytes(result)) > 256 * 1024 or _contains_sensitive_data(result):
        raise DiagnoseReproduceV2Error(
            "next experiment contains sensitive or unbounded data"
        )
    return result


def diagnose_live_console(
    root: Path,
    owner_ref: Mapping[str, Any],
    *,
    work_session_id: str | None = None,
    owner_classifications: Iterable[Mapping[str, Any]] = (),
    owner_next_experiments: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Diagnose one exact retained console owner without causal invention."""

    if work_session_id is not None and (
        not isinstance(work_session_id, str)
        or not 1 <= len(work_session_id) <= 160
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", work_session_id)
    ):
        raise DiagnoseReproduceV2Error("Work Session identity is invalid")

    try:
        resolved = resolve_live_console_owner_reference(root, owner_ref)
        execution = live_console_execution_reference(root, owner_ref)
        rows: list[dict[str, Any]] = []
        after_sequence = -1
        while True:
            page = read_live_console_owner_artifacts(
                root,
                owner_ref,
                after_sequence=after_sequence,
                limit=200,
            )
            rows.extend(_event_projection(row) for row in page["events"])
            if not page["has_more"]:
                current_digest = page["current_owner_digest"]
                break
            next_after = page["next_after_sequence"]
            if not isinstance(next_after, int) or next_after <= after_sequence:
                raise DiagnoseReproduceV2Error(
                    "live-console event pagination did not advance"
                )
            after_sequence = next_after
    except SessionError as exc:
        raise DiagnoseReproduceV2Error(f"live-console evidence is invalid: {exc}") from exc
    if not rows:
        raise DiagnoseReproduceV2Error("live-console owner has no retained events")

    classifications = [_owner_classification(row) for row in owner_classifications]
    if len(classifications) > 256:
        raise DiagnoseReproduceV2Error(
            "owner semantic classifications exceed their bound"
        )
    owner_actions = [_next_experiment(row) for row in owner_next_experiments]
    if len(owner_actions) > 127:
        raise DiagnoseReproduceV2Error("owner next experiments exceed their bound")
    failures = [row for row in rows if row["severity"] in {"error", "fatal"}]
    shutdown = [
        row
        for row in rows
        if any(token in row["message"].casefold() for token in ("shutdown", "stopping", "cleanup"))
    ]
    shutdown_ids = {row["event_id"] for row in shutdown}
    primary = failures[:1]
    wrappers = [row for row in failures[1:] if row["event_id"] not in shutdown_ids]
    contributing = [
        row
        for row in rows
        if row["event_id"] not in shutdown_ids
        and row not in failures
        and row["severity"] == "warning"
    ]
    command = execution["command"]
    fingerprint_basis = {
        "command_id": command["command_id"],
        "owner_state": resolved["last_verified_state"],
        "owner_classifications": [
            {
                "classification_id": row["classification_id"],
                "stage": row["stage"],
                "state": row["state"],
                "required_markers": row["required_markers"],
                "observed_markers": row["observed_markers"],
                "effective_exit_code": row["effective_exit_code"],
                "cleanup_contained": row["cleanup_contained"],
                "artifact_digest": row["artifact_digest"],
            }
            for row in classifications
        ],
        "primary": [
            {
                "kind": row["kind"],
                "severity": row["severity"],
                "subsystem": row["subsystem"],
                "message": row["message"],
            }
            for row in primary
        ],
    }
    fingerprint = "sha256:" + hashlib.sha256(
        _canonical_bytes(fingerprint_basis)
    ).hexdigest()
    first_event = primary[0] if primary else rows[0]
    stable_original = _stable_owner_ref(owner_ref)
    stable_current = _stable_owner_ref(resolved)
    stable_current["digest"] = current_digest
    generic_failed = (
        resolved["last_verified_state"] in {"failed", "incomplete", "cancelled"}
        or bool(failures)
    )
    semantic_failed = any(row["state"] == "failed" for row in classifications)
    diagnosis: dict[str, Any] = {
        "format": DIAGNOSIS_FORMAT,
        "schema_version": 1,
        "diagnosis_id": "pending",
        "work_session_id": work_session_id,
        "outcome": (
            "failed"
            if semantic_failed or (not classifications and generic_failed)
            else "inconclusive"
        ),
        "target_owner_ref": stable_original,
        "current_owner_ref": stable_current,
        "command": {
            "command_id": command["command_id"],
            "intent": command["intent"],
            "shell": command["shell"],
        },
        "timeline": rows,
        "observed_failures": [_claim(row, "observed") for row in primary],
        "wrappers": [_claim(row, "observed") for row in wrappers],
        "secondary_failures": [],
        "shutdown_noise": [_claim(row, "observed") for row in shutdown],
        "contributing_conditions": [
            _claim(row, "observed") for row in contributing
        ],
        "classifications": classifications,
        "unknowns": [
            {
                "id": "causal-root-unresolved",
                "claim_state": "unknown",
                "owner_id": "workbench-shell",
                "detail": (
                    "No owner-qualified classifier establishes a causal root; "
                    "the earliest retained failure remains an observation."
                ),
            }
        ],
        "next_experiments": [
            {
                "action_id": "workbench.diagnose.inspect-raw",
                "arguments": {
                    "owner_record_id": stable_original["record_id"],
                    "owner_digest": stable_original["digest"],
                    "event_id": first_event["event_id"],
                },
                "context_digest": current_digest,
                "mutation": "read-only",
            }
        ] + owner_actions,
        "fingerprint": fingerprint,
        "limitations": [
            "The earliest observed failure is not asserted to be the root cause.",
            "No profile-owned classifier was applied by this generic projection.",
            "Raw bytes remain in the retained live-console owner record.",
        ],
    }
    diagnosis["diagnosis_id"] = _content_id(
        "workbench-diagnosis:sha256:", diagnosis, "diagnosis_id"
    )
    return diagnosis


def _validate_diagnosis(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DiagnoseReproduceV2Error("diagnosis is not an object")
    result = dict(value)
    _validate_schema(
        "workbench-diagnosis-v1.schema.json",
        result,
        "diagnosis",
    )
    if result.get("format") != DIAGNOSIS_FORMAT or result.get("schema_version") != 1:
        raise DiagnoseReproduceV2Error("diagnosis format is unsupported")
    expected = _content_id(
        "workbench-diagnosis:sha256:", result, "diagnosis_id"
    )
    if result.get("diagnosis_id") != expected:
        raise DiagnoseReproduceV2Error("diagnosis identity differs")
    fingerprint = result.get("fingerprint")
    if not isinstance(fingerprint, str) or not _DIGEST_RE.fullmatch(fingerprint):
        raise DiagnoseReproduceV2Error("diagnosis fingerprint is invalid")
    return result


def validate_diagnosis(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and defensively copy one canonical diagnosis owner record."""

    return _validate_diagnosis(value)


def _validate_replay_action(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "action_id",
        "arguments",
        "mutation",
    }:
        raise DiagnoseReproduceV2Error("replay action is not a closed typed action")
    action_id = value.get("action_id")
    arguments = value.get("arguments")
    mutation = value.get("mutation")
    if not isinstance(action_id, str) or not _ACTION_RE.fullmatch(action_id):
        raise DiagnoseReproduceV2Error("replay action ID is invalid")
    if not isinstance(arguments, Mapping):
        raise DiagnoseReproduceV2Error("replay action arguments are invalid")
    if mutation not in {"read-only", "isolated-target-only"}:
        raise DiagnoseReproduceV2Error("replay action mutation budget is invalid")
    result = {
        "action_id": action_id,
        "arguments": dict(arguments),
        "mutation": mutation,
    }
    raw = _canonical_bytes(result)
    if len(raw) > 256 * 1024 or _contains_sensitive_data(result):
        raise DiagnoseReproduceV2Error("replay action contains sensitive or unbounded data")
    return result


def _validate_privacy_review(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"approved", "excluded"}:
        raise DiagnoseReproduceV2Error("capsule privacy review is invalid")
    excluded = value.get("excluded")
    if value.get("approved") is not True or not isinstance(excluded, list):
        raise DiagnoseReproduceV2Error("capsule privacy review is not approved")
    if len(excluded) != len(set(excluded)) or not _REQUIRED_EXCLUSIONS.issubset(excluded):
        raise DiagnoseReproduceV2Error(
            "capsule privacy review lacks required exclusions"
        )
    return {"approved": True, "excluded": sorted(excluded)}


def validate_reproduction_capsule_manifest(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed, content-addressed capsule manifest record."""

    if not isinstance(value, Mapping):
        raise DiagnoseReproduceV2Error("capsule manifest is not an object")
    result = dict(value)
    _validate_schema(
        "workbench-reproduction-capsule-manifest-v1.schema.json",
        result,
        "capsule manifest",
    )
    expected = _content_id(
        "workbench-reproduction-capsule:sha256:",
        result,
        "capsule_id",
    )
    if result.get("capsule_id") != expected:
        raise DiagnoseReproduceV2Error("capsule identity differs")
    _validate_replay_action(result["replay_action"])
    _validate_privacy_review(result["privacy_review"])
    return result


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def create_reproduction_capsule(
    diagnosis: Mapping[str, Any],
    output: Path,
    *,
    replay_action: Mapping[str, Any],
    privacy_review: Mapping[str, Any],
) -> dict[str, Any]:
    """Write one deterministic reviewed capsule without dependency binaries."""

    diagnosis_record = _validate_diagnosis(diagnosis)
    action = _validate_replay_action(replay_action)
    review = _validate_privacy_review(privacy_review)
    diagnosis_raw = _canonical_bytes(diagnosis_record)
    if _contains_sensitive_data(diagnosis_record):
        raise DiagnoseReproduceV2Error("diagnosis contains secret-shaped text")
    manifest: dict[str, Any] = {
        "format": CAPSULE_FORMAT,
        "schema_version": 1,
        "capsule_id": "pending",
        "diagnosis_id": diagnosis_record["diagnosis_id"],
        "expected_fingerprint": diagnosis_record["fingerprint"],
        "replay_action": action,
        "privacy_review": review,
        "members": [
            {
                "path": "diagnosis.json",
                "size": len(diagnosis_raw),
                "sha256": "sha256:" + hashlib.sha256(diagnosis_raw).hexdigest(),
            }
        ],
        "limitations": [
            "The capsule contains projections and descriptors, not protected runtime binaries.",
            "Replay requires an independently allowlisted typed action executor.",
        ],
    }
    manifest["capsule_id"] = _content_id(
        "workbench-reproduction-capsule:sha256:", manifest, "capsule_id"
    )
    manifest_raw = _canonical_bytes(manifest)
    destination = output.absolute()
    if destination.exists() or destination.is_symlink():
        raise DiagnoseReproduceV2Error("capsule output already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise DiagnoseReproduceV2Error("capsule temporary output already exists")
    try:
        with ZipFile(temporary, "x", compression=ZIP_STORED, allowZip64=False) as archive:
            archive.writestr(_zip_info("diagnosis.json"), diagnosis_raw)
            archive.writestr(_zip_info("manifest.json"), manifest_raw)
        descriptor = os.open(
            temporary,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        directory_descriptor = os.open(
            destination.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except (OSError, BadZipFile) as exc:
        temporary.unlink(missing_ok=True)
        raise DiagnoseReproduceV2Error("capsule could not be published atomically") from exc
    return {
        "format": "workbench-reproduction-capsule-result-v1",
        "capsule_id": manifest["capsule_id"],
        "diagnosis_id": manifest["diagnosis_id"],
        "path": str(destination),
        "size": destination.stat().st_size,
        "sha256": "sha256:" + hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise DiagnoseReproduceV2Error(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict) or _canonical_bytes(value) != raw:
        raise DiagnoseReproduceV2Error(f"{label} is not canonical JSON")
    return value


def _safe_member_name(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) != 1
    ):
        raise DiagnoseReproduceV2Error("capsule contains an unsafe member path")
    return value


def inspect_reproduction_capsule(path: Path) -> dict[str, Any]:
    """Verify a capsule without hydration, execution, or extraction."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DiagnoseReproduceV2Error("capsule does not exist") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= MAX_CAPSULE_BYTES
    ):
        raise DiagnoseReproduceV2Error("capsule file identity is unsafe")
    try:
        with ZipFile(path, "r", allowZip64=False) as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_MEMBERS:
                raise DiagnoseReproduceV2Error("capsule member count is invalid")
            names = [_safe_member_name(info.filename) for info in infos]
            if names != sorted(names) or len(names) != len(set(names)):
                raise DiagnoseReproduceV2Error("capsule members are not canonical")
            if set(names) != {"diagnosis.json", "manifest.json"}:
                raise DiagnoseReproduceV2Error("capsule membership differs")
            raw_members: dict[str, bytes] = {}
            for info in infos:
                mode = info.external_attr >> 16
                if (
                    info.compress_type != ZIP_STORED
                    or info.file_size > MAX_MEMBER_BYTES
                    or info.compress_size != info.file_size
                    or stat.S_ISLNK(mode)
                    or (mode and not stat.S_ISREG(mode))
                ):
                    raise DiagnoseReproduceV2Error("capsule member identity is unsafe")
                raw_members[info.filename] = archive.read(info)
    except (OSError, BadZipFile, RuntimeError) as exc:
        if isinstance(exc, DiagnoseReproduceV2Error):
            raise
        raise DiagnoseReproduceV2Error("capsule archive is invalid") from exc
    manifest = _strict_json(raw_members["manifest.json"], "capsule manifest")
    diagnosis = _strict_json(raw_members["diagnosis.json"], "capsule diagnosis")
    manifest = validate_reproduction_capsule_manifest(manifest)
    diagnosis = _validate_diagnosis(diagnosis)
    if manifest.get("diagnosis_id") != diagnosis["diagnosis_id"]:
        raise DiagnoseReproduceV2Error("capsule diagnosis identity differs")
    rows = manifest.get("members")
    expected_row = {
        "path": "diagnosis.json",
        "size": len(raw_members["diagnosis.json"]),
        "sha256": "sha256:"
        + hashlib.sha256(raw_members["diagnosis.json"]).hexdigest(),
    }
    if rows != [expected_row]:
        raise DiagnoseReproduceV2Error("capsule member digest differs")
    action = _validate_replay_action(manifest.get("replay_action"))
    _validate_privacy_review(manifest.get("privacy_review"))
    if manifest.get("expected_fingerprint") != diagnosis["fingerprint"]:
        raise DiagnoseReproduceV2Error("capsule expected fingerprint differs")
    return {
        "format": "workbench-reproduction-capsule-inspection-v1",
        "capsule_id": manifest["capsule_id"],
        "diagnosis_id": diagnosis["diagnosis_id"],
        "fingerprint": diagnosis["fingerprint"],
        "member_count": len(raw_members),
        "replay_action": action,
        "privacy_review": manifest["privacy_review"],
        "limitations": list(manifest.get("limitations", [])),
    }


def replay_reproduction_capsule(
    path: Path,
    *,
    allowed_action_ids: Iterable[str],
    execute: Callable[[dict[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Replay one verified typed action through a caller-owned executor."""

    inspected = inspect_reproduction_capsule(path)
    action = inspected["replay_action"]
    allowed = set(allowed_action_ids)
    if action["action_id"] not in allowed:
        raise DiagnoseReproduceV2Error("capsule replay action is not allowlisted")
    observed = execute(dict(action))
    if not isinstance(observed, Mapping):
        raise DiagnoseReproduceV2Error("capsule replay executor returned no record")
    outcome = observed.get("outcome")
    if outcome not in {
        "matching-failure",
        "divergent-failure",
        "unexpected-success",
        "blocked-hydration",
        "cancelled",
        "incomplete",
    }:
        raise DiagnoseReproduceV2Error("capsule replay outcome is invalid")
    fingerprint = observed.get("fingerprint")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or not _DIGEST_RE.fullmatch(fingerprint)
    ):
        raise DiagnoseReproduceV2Error("capsule replay fingerprint is invalid")
    return {
        "format": REPLAY_FORMAT,
        "schema_version": 1,
        "capsule_id": inspected["capsule_id"],
        "diagnosis_id": inspected["diagnosis_id"],
        "action_id": action["action_id"],
        "outcome": outcome,
        "expected_fingerprint": inspected["fingerprint"],
        "observed_fingerprint": fingerprint,
        "matched": outcome == "matching-failure"
        and fingerprint == inspected["fingerprint"],
    }


__all__ = [
    "CAPSULE_FORMAT",
    "DIAGNOSIS_FORMAT",
    "DiagnoseReproduceV2Error",
    "create_reproduction_capsule",
    "diagnose_live_console",
    "inspect_reproduction_capsule",
    "replay_reproduction_capsule",
    "validate_diagnosis",
    "validate_reproduction_capsule_manifest",
]
