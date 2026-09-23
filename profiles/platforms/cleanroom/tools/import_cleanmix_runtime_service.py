#!/usr/bin/env python3

"""Import exact Cleanroom/CleanMix 0.7 service reports for Crucible.

The importer recognizes the exact Cleanroom initialization, CleanMix service
boot, and Mixin subsystem banner emitted by the 0.7 runtime epoch.  It binds
the complete input-log bytes and the measured CleanMix container bytes to the
exact candidate transformer lock.  Log reports are not provider enumeration,
service uniqueness, Mixin-application completion, or final-byte evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[4]
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
if str(PROJECT_INTELLIGENCE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactScanError,
    validate_topology_receipt,
)


OBSERVATION_SET_FORMAT = (
    "workbench-crucible-mixin-runtime-service-observation-set-v1"
)
TOOLCHAIN_LOCK_FORMAT = "workbench-cleanroom-transformer-toolchain-lock-v1"
TOPOLOGY_RECEIPT_PREFIX = "workbench-mixin-topology-receipt:sha256:"
CLEANMIX_COORDINATE = "com.cleanroommc:cleanmix:0.7.0"
CLEANMIX_SERVICE_CLASS = "com.cleanroommc.cleanmix.service.CleanMixService"
CLEANMIX_SERVICE_NAME = "CleanMix"
CLEANMIX_SUBSYSTEM_VERSION = "0.8.7"
CLEANMIX_FILENAME = "cleanmix-0.7.0.jar"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOPOLOGY_RECEIPT_ID_RE = re.compile(
    rf"^{re.escape(TOPOLOGY_RECEIPT_PREFIX)}[0-9a-f]{{64}}$"
)
_LINE_RE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\] "
    r"\[(?P<thread>[^\]]+)/(?P<level>[A-Z]+)\] "
    r"\[(?P<logger>[^\]]+)\]: (?P<message>.*)$"
)
_INITIALIZATION_RE = re.compile(r"^Initializing (?P<service>CleanMix)\.\.\.$")
_SELECTION_RE = re.compile(
    r"^MixinService \[(?P<service>[^\]]+)\] was successfully booted in "
    r"(?P<host>\S.*)$"
)
_SUBSYSTEM_RE = re.compile(
    r"^SpongePowered MIXIN Subsystem Version=(?P<version>\S+) "
    r"Source=(?P<source>\S+) Service=(?P<service>\S+) "
    r"Env=(?P<environment>\S+)$"
)
_SOURCE_RE = re.compile(
    r"^jar:(?P<container>.+)!/"
    r"org/spongepowered/asm/mixin/MixinEnvironment\.class$"
)
_SOURCE_KINDS = ("debug", "latest")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_EXPECTED_ENVIRONMENTS = {
    "client": "CLIENT",
    "dedicated_server": "SERVER",
    "integrated_server": "CLIENT",
}
_LIMITATIONS = (
    "The candidate toolchain lock frames retrospective artifact validation; retained logs do not prove the launch was orchestrated from that lock or loaded every locked artifact.",
    "CleanMix is a reported service name, not proof of the CleanMixService implementation class or its provider artifact.",
    "CleanMix log reports do not enumerate ServiceLoader providers; provider uniqueness is not claimed.",
    "Initialization is intent, while boot and subsystem lines are reported selection evidence only.",
    "No runtime-service log observation proves a Mixin application completed or identifies Foundation-final transformed bytes.",
    "The measured cleanmix-0.7.0.jar is the MixinEnvironment code source; CleanMixService is supplied by Cleanroom and is not attributed to that artifact.",
    "The subsystem code-source URI and measured artifact must resolve to the same host-local file; its SHA-256 and size are checked against the exact candidate lock.",
)


class RuntimeServiceImportError(ValueError):
    """Raised when exact-profile service evidence cannot be bound safely."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeServiceImportError(message)


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _size(value: Any, context: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{context} must be a nonnegative integer",
    )
    return value


def _decode_json_object(data: bytes, context: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeServiceImportError(
                    f"{context} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        text = data.decode("utf-8")
        value = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except RuntimeServiceImportError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeServiceImportError(f"cannot decode {context}: {exc}") from exc
    _require(isinstance(value, dict), f"{context} must be a JSON object")
    return value


def _locked_cleanmix(lock_bytes: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    lock = _decode_json_object(lock_bytes, "transformer toolchain lock")
    _require(
        lock.get("format") == TOOLCHAIN_LOCK_FORMAT,
        "transformer toolchain lock format is not exact V1",
    )
    _require(lock.get("schema_version") == 1, "toolchain lock schema version is not 1")

    binding = lock.get("binding")
    _require(isinstance(binding, dict), "toolchain lock binding must be an object")
    _text(binding.get("candidate_id"), "toolchain lock candidate_id")

    expectations = lock.get("native_service_expectations")
    _require(
        isinstance(expectations, dict),
        "toolchain lock native service expectations must be an object",
    )
    _require(
        expectations.get("mixin_service") == CLEANMIX_SERVICE_CLASS,
        "toolchain lock does not select the exact CleanMix service class",
    )
    _require(
        expectations.get("mixin_service_name") == CLEANMIX_SERVICE_NAME,
        "toolchain lock does not select the exact CleanMix service name",
    )

    versions = lock.get("platform_versions")
    _require(isinstance(versions, dict), "toolchain lock versions must be an object")
    _require(
        versions.get("cleanmix") == "0.7.0",
        "toolchain lock is outside the exact CleanMix 0.7.0 profile",
    )
    _require(
        versions.get("cleanmix_upstream_sponge_mixin")
        == CLEANMIX_SUBSYSTEM_VERSION,
        "toolchain lock has an unexpected Mixin subsystem version",
    )

    artifacts = lock.get("artifacts")
    _require(isinstance(artifacts, list), "toolchain lock artifacts must be an array")
    matches = [
        row
        for row in artifacts
        if isinstance(row, dict) and row.get("coordinate") == CLEANMIX_COORDINATE
    ]
    _require(
        len(matches) == 1,
        "toolchain lock must contain exactly one CleanMix 0.7.0 artifact",
    )
    artifact = matches[0]
    _require(artifact.get("role") == "mixin-engine", "CleanMix lock role is not mixin-engine")
    _require(
        artifact.get("filename") == CLEANMIX_FILENAME,
        "CleanMix lock filename is not exact",
    )
    _sha256(artifact.get("sha256"), "locked CleanMix artifact sha256")
    _size(artifact.get("size"), "locked CleanMix artifact size")
    return lock, artifact


def _source_container_path(source: str) -> Path:
    """Return a host-local file path for the exact Mixin code-source URI."""

    match = _SOURCE_RE.fullmatch(source)
    _require(match is not None, "Mixin subsystem source is not the exact class code source")
    container = match.group("container")
    parsed = urlsplit(container)
    _require(parsed.scheme == "file", "Mixin subsystem source container is not a file URI")
    _require(
        parsed.query == "" and parsed.fragment == "",
        "Mixin subsystem source container has query or fragment data",
    )
    _require(
        parsed.netloc in {"", "localhost"},
        "Mixin subsystem source is not a host-local file URI",
    )
    decoded = unquote(parsed.path)
    _require("\x00" not in decoded, "Mixin subsystem source contains a NUL byte")
    if re.match(r"^/[A-Za-z]:/", decoded):
        _require(
            os.name == "nt",
            "Mixin subsystem source is not host-local on this operating system",
        )
        decoded = decoded[1:]
    return Path(decoded)


def _validated_topology_binding(receipt_bytes: bytes) -> tuple[str, str]:
    receipt = _decode_json_object(receipt_bytes, "component topology receipt")
    try:
        validate_topology_receipt(receipt)
    except ArtifactScanError as exc:
        raise RuntimeServiceImportError(
            f"component topology receipt failed semantic validation: {exc}"
        ) from exc
    receipt_id = receipt.get("receipt_id")
    _require(
        isinstance(receipt_id, str)
        and _TOPOLOGY_RECEIPT_ID_RE.fullmatch(receipt_id) is not None,
        "component topology receipt ID is not exact V1",
    )
    return receipt_id, hashlib.sha256(receipt_bytes).hexdigest()


def _source_filename(source: str) -> str:
    match = _SOURCE_RE.fullmatch(source)
    _require(match is not None, "Mixin subsystem source is not the exact class code source")
    container = match.group("container")
    parsed = urlsplit(container)
    _require(parsed.scheme == "file", "Mixin subsystem source container is not a file URI")
    path = unquote(parsed.path).replace("\\", "/")
    filename = path.rsplit("/", 1)[-1]
    _require(filename == CLEANMIX_FILENAME, "Mixin subsystem source filename is not locked CleanMix")
    return filename


def _normalize_session(
    session: Mapping[str, Any],
    *,
    lock: Mapping[str, Any],
    lock_sha256: str,
    topology_receipt_id: str,
    topology_receipt_sha256: str,
) -> dict[str, Any]:
    expected = {
        "session_id",
        "launch_id",
        "profile_id",
        "side",
    }
    _require(set(session) == expected, "session surface is not exactly V1")
    side = session["side"]
    _require(side in _SIDES, "session.side is unsupported")
    binding = lock["binding"]
    profile_id = _text(session["profile_id"], "session.profile_id")
    _require(
        profile_id == binding["candidate_id"],
        "session.profile_id does not match the transformer toolchain lock",
    )
    return {
        "session_id": _text(session["session_id"], "session.session_id"),
        "launch_id": _text(session["launch_id"], "session.launch_id"),
        "profile_id": profile_id,
        "side": side,
        "candidate_toolchain_lock_sha256": lock_sha256,
        "component_topology_receipt_id": topology_receipt_id,
        "component_topology_receipt_sha256": topology_receipt_sha256,
    }


def _observation_common(
    *,
    sequence: int,
    kind: str,
    service_name: str,
    evidence_sha256: str,
    line_number: int,
    match: re.Match[str],
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "kind": kind,
        "service_name": service_name,
        "evidence_sha256": evidence_sha256,
        "source_record": f"line:{line_number}",
        "logger": match.group("logger"),
        "level": match.group("level"),
        "thread": match.group("thread"),
        "wallclock": match.group("time"),
    }


def _parse_log(
    data: bytes,
    *,
    source_kind: str,
    sequence_start: int,
    artifact_sha256: str,
    artifact_path: Path | None,
    side: str,
) -> list[dict[str, Any]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeServiceImportError(
            f"{source_kind}.log is not UTF-8: {exc}"
        ) from exc
    _require("\x00" not in text, f"{source_kind}.log contains a NUL byte")
    evidence_sha256 = hashlib.sha256(data).hexdigest()
    observations: list[dict[str, Any]] = []

    for line_number, line in enumerate(text.splitlines(), 1):
        line_match = _LINE_RE.fullmatch(line)
        if line_match is None:
            if any(
                marker in line
                for marker in (
                    "Initializing CleanMix",
                    "MixinService [CleanMix]",
                    "SpongePowered MIXIN Subsystem",
                )
            ):
                raise RuntimeServiceImportError(
                    f"malformed CleanMix service report at {source_kind}.log line {line_number}"
                )
            continue

        logger = line_match.group("logger")
        level = line_match.group("level")
        message = line_match.group("message")
        initialization = _INITIALIZATION_RE.fullmatch(message)
        selection = _SELECTION_RE.fullmatch(message)
        subsystem = _SUBSYSTEM_RE.fullmatch(message)

        if initialization is not None:
            _require(
                logger == "Cleanroom" and level == "INFO",
                f"unexpected CleanMix initialization logger or level at {source_kind}.log line {line_number}",
            )
            observations.append(
                _observation_common(
                    sequence=sequence_start + len(observations),
                    kind="initialization_report",
                    service_name=initialization.group("service"),
                    evidence_sha256=evidence_sha256,
                    line_number=line_number,
                    match=line_match,
                )
            )
            continue

        if selection is not None:
            _require(
                logger == "CleanMix" and level == "DEBUG",
                f"unexpected CleanMix boot logger or level at {source_kind}.log line {line_number}",
            )
            _require(
                selection.group("service") == CLEANMIX_SERVICE_NAME,
                f"reported Mixin service does not match the exact lock at {source_kind}.log line {line_number}",
            )
            observations.append(
                _observation_common(
                    sequence=sequence_start + len(observations),
                    kind="selection_report",
                    service_name=selection.group("service"),
                    evidence_sha256=evidence_sha256,
                    line_number=line_number,
                    match=line_match,
                )
            )
            continue

        if subsystem is not None:
            _require(
                logger == "CleanMix" and level == "INFO",
                f"unexpected Mixin subsystem logger or level at {source_kind}.log line {line_number}",
            )
            _require(
                subsystem.group("service") == CLEANMIX_SERVICE_NAME,
                f"reported subsystem service does not match the exact lock at {source_kind}.log line {line_number}",
            )
            _require(
                subsystem.group("version") == CLEANMIX_SUBSYSTEM_VERSION,
                f"reported subsystem version does not match the exact lock at {source_kind}.log line {line_number}",
            )
            environment = subsystem.group("environment")
            _require(
                environment == _EXPECTED_ENVIRONMENTS[side],
                f"reported subsystem environment does not match session.side at {source_kind}.log line {line_number}",
            )
            source = subsystem.group("source")
            _source_filename(source)
            source_path = _source_container_path(source)
            _require(
                artifact_path is not None,
                "measured CleanMix artifact path is required for exact source binding",
            )
            try:
                source_metadata = source_path.lstat()
            except OSError as exc:
                raise RuntimeServiceImportError(
                    f"reported CleanMix source is unavailable at {source_kind}.log line {line_number}: {exc}"
                ) from exc
            _require(
                not stat.S_ISLNK(source_metadata.st_mode)
                and stat.S_ISREG(source_metadata.st_mode),
                f"reported CleanMix source is not a regular non-symlink file at {source_kind}.log line {line_number}",
            )
            try:
                same_file = os.path.samefile(source_path, artifact_path)
            except OSError as exc:
                raise RuntimeServiceImportError(
                    f"cannot compare reported CleanMix source at {source_kind}.log line {line_number}: {exc}"
                ) from exc
            _require(
                same_file,
                f"reported CleanMix source is not the measured artifact at {source_kind}.log line {line_number}",
            )
            observation = _observation_common(
                sequence=sequence_start + len(observations),
                kind="subsystem_report",
                service_name=subsystem.group("service"),
                evidence_sha256=evidence_sha256,
                line_number=line_number,
                match=line_match,
            )
            observation.update(
                {
                    "subsystem_version": subsystem.group("version"),
                    "environment": environment,
                    "source_artifact_sha256": artifact_sha256,
                }
            )
            observations.append(observation)
            continue

        if (
            message.startswith("Initializing CleanMix")
            or message.startswith("MixinService [")
            or message.startswith("SpongePowered MIXIN Subsystem")
        ):
            raise RuntimeServiceImportError(
                f"unknown exact-profile CleanMix service report at {source_kind}.log line {line_number}"
            )

    kinds = {row["kind"] for row in observations}
    _require(
        "subsystem_report" in kinds,
        f"{source_kind}.log has no exact CleanMix subsystem report",
    )
    if source_kind == "debug":
        _require(
            "selection_report" in kinds,
            "debug.log has no exact CleanMix boot selection report",
        )
    return observations


def build_observation_set(
    *,
    logs: Mapping[str, bytes],
    cleanmix_artifact_bytes: bytes,
    cleanmix_artifact_path: Path,
    toolchain_lock_bytes: bytes,
    component_topology_receipt_bytes: bytes,
    session: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact-profile normalized input for the generic assembler."""

    _require(bool(logs), "at least one complete debug/latest log is required")
    _require(
        set(logs) <= set(_SOURCE_KINDS),
        f"unsupported log kinds: {sorted(set(logs) - set(_SOURCE_KINDS))}",
    )
    _require(
        all(isinstance(data, bytes) for data in logs.values()),
        "all log inputs must be bytes",
    )
    _require(
        isinstance(cleanmix_artifact_bytes, bytes),
        "CleanMix artifact input must be bytes",
    )
    _require(isinstance(toolchain_lock_bytes, bytes), "toolchain lock input must be bytes")
    _require(
        isinstance(component_topology_receipt_bytes, bytes),
        "component topology receipt input must be bytes",
    )

    lock, locked_artifact = _locked_cleanmix(toolchain_lock_bytes)
    lock_sha256 = hashlib.sha256(toolchain_lock_bytes).hexdigest()
    artifact_sha256 = hashlib.sha256(cleanmix_artifact_bytes).hexdigest()
    _require(
        artifact_sha256 == locked_artifact["sha256"],
        "measured CleanMix artifact SHA-256 does not match the toolchain lock",
    )
    _require(
        len(cleanmix_artifact_bytes) == locked_artifact["size"],
        "measured CleanMix artifact size does not match the toolchain lock",
    )
    _require(
        isinstance(cleanmix_artifact_path, Path),
        "measured CleanMix artifact path is required",
    )
    _require(
        cleanmix_artifact_path.name == locked_artifact["filename"],
        "measured CleanMix artifact filename does not match the toolchain lock",
    )
    try:
        artifact_metadata = cleanmix_artifact_path.lstat()
    except OSError as exc:
        raise RuntimeServiceImportError(
            f"cannot inspect measured CleanMix artifact: {exc}"
        ) from exc
    _require(
        not stat.S_ISLNK(artifact_metadata.st_mode)
        and stat.S_ISREG(artifact_metadata.st_mode),
        "measured CleanMix artifact must be a regular non-symlink file",
    )
    try:
        path_artifact_bytes = cleanmix_artifact_path.read_bytes()
    except OSError as exc:
        raise RuntimeServiceImportError(
            f"cannot reread measured CleanMix artifact: {exc}"
        ) from exc
    _require(
        path_artifact_bytes == cleanmix_artifact_bytes,
        "measured CleanMix artifact bytes do not match its bound path",
    )
    topology_receipt_id, topology_receipt_sha256 = _validated_topology_binding(
        component_topology_receipt_bytes
    )

    normalized_session = _normalize_session(
        session,
        lock=lock,
        lock_sha256=lock_sha256,
        topology_receipt_id=topology_receipt_id,
        topology_receipt_sha256=topology_receipt_sha256,
    )
    evidence: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    for source_kind in _SOURCE_KINDS:
        if source_kind not in logs:
            continue
        data = logs[source_kind]
        source_sha256 = hashlib.sha256(data).hexdigest()
        _require(
            source_sha256 not in seen_evidence,
            "debug.log and latest.log inputs have identical content identities",
        )
        seen_evidence.add(source_sha256)
        evidence.append(
            {
                "source_sha256": source_sha256,
                "size_bytes": len(data),
                "kind": "runtime_log",
                "label": f"{source_kind}.log",
            }
        )
        observations.extend(
            _parse_log(
                data,
                source_kind=source_kind,
                sequence_start=len(observations) + 1,
                artifact_sha256=artifact_sha256,
                artifact_path=cleanmix_artifact_path,
                side=normalized_session["side"],
            )
        )

    return {
        "format": OBSERVATION_SET_FORMAT,
        "session": normalized_session,
        "artifacts": [
            {
                "artifact_sha256": artifact_sha256,
                "size_bytes": len(cleanmix_artifact_bytes),
                "role": "mixin_runtime",
                "label": CLEANMIX_COORDINATE,
            }
        ],
        "evidence": evidence,
        "observations": observations,
        "provider_enumeration": {
            "state": "not_enumerated",
            "mechanism": None,
            "providers": [],
            "evidence_sha256": None,
            "evidence_id": None,
            "source_record": None,
            "failure": None,
        },
        "limitations": sorted(_LIMITATIONS),
    }


def _read_regular_file(path: Path, context: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeServiceImportError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(metadata.st_mode), f"{context} cannot be a symlink: {path}")
    _require(stat.S_ISREG(metadata.st_mode), f"{context} must be a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RuntimeServiceImportError(f"cannot read {context} {path}: {exc}") from exc


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise RuntimeServiceImportError(f"output cannot be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".cleanmix-runtime-service-",
        suffix=".json.tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import exact Cleanroom/CleanMix 0.7 service log reports for Crucible."
        )
    )
    parser.add_argument("--debug-log", type=Path)
    parser.add_argument("--latest-log", type=Path)
    parser.add_argument("--cleanmix-artifact", required=True, type=Path)
    parser.add_argument("--toolchain-lock", required=True, type=Path)
    parser.add_argument("--component-topology-receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument(
        "--side",
        required=True,
        choices=("client", "dedicated_server", "integrated_server"),
    )
    args = parser.parse_args()
    if args.debug_log is None and args.latest_log is None:
        parser.error("at least one of --debug-log or --latest-log is required")

    logs: dict[str, bytes] = {}
    if args.debug_log is not None:
        logs["debug"] = _read_regular_file(args.debug_log, "debug log")
    if args.latest_log is not None:
        logs["latest"] = _read_regular_file(args.latest_log, "latest log")
    artifact_bytes = _read_regular_file(args.cleanmix_artifact, "CleanMix artifact")
    lock_bytes = _read_regular_file(args.toolchain_lock, "toolchain lock")
    topology_receipt_bytes = _read_regular_file(
        args.component_topology_receipt,
        "component topology receipt",
    )
    observation_set = build_observation_set(
        logs=logs,
        cleanmix_artifact_bytes=artifact_bytes,
        cleanmix_artifact_path=args.cleanmix_artifact.resolve(),
        toolchain_lock_bytes=lock_bytes,
        component_topology_receipt_bytes=topology_receipt_bytes,
        session={
            "session_id": args.session_id,
            "launch_id": args.launch_id,
            "profile_id": args.profile_id,
            "side": args.side,
        },
    )
    _atomic_write(args.output, observation_set)
    print(len(observation_set["observations"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeServiceImportError as exc:
        print(f"CleanMix runtime service import failed: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
