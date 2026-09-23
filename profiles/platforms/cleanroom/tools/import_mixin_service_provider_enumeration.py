#!/usr/bin/env python3

"""Admit exact Java IMixinService enumeration into a Crucible receipt input.

The importer consumes identity-bearing probe evidence and an existing
runtime-service observation set. It independently remeasures implementation
code-source artifacts and validates the observed provider class against the
artifact's class member and Java service descriptor. It never derives a
provider from the Mixin subsystem code source or reported service name.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[4]
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
if str(CRUCIBLE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_mixin_custody import (  # noqa: E402
    MIXIN_SERVICE_INTERFACE,
    RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
    SERVICE_LOADER_ITERATOR_MECHANISM,
    ProviderEnumerationValidationError,
    RuntimeServiceValidationError,
    build_runtime_service_receipt,
    parse_provider_enumeration_evidence,
    render_provider_enumeration_evidence,
)


TOOLCHAIN_LOCK_FORMAT = "workbench-cleanroom-transformer-toolchain-lock-v1"
EXPECTED_CLEANMIX_SERVICE = "com.cleanroommc.cleanmix.service.CleanMixService"
SERVICE_DESCRIPTOR = "META-INF/services/" + MIXIN_SERVICE_INTERFACE
EVIDENCE_LABEL = "mixin-service-provider-enumeration-evidence-v1.json"
_BINARY_NAME_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$"
)
_LIMITATIONS = (
    "Java ServiceLoader enumeration records available provider implementations and does not prove which service Mixin selected.",
    "Provider names are reported attributes; duplicate names do not collapse distinct implementation class and artifact identities.",
    "Provider artifact attribution comes from each implementation class protection domain and never from the Mixin subsystem code source.",
    "A failed enumeration publishes no partial provider list and leaves provider uniqueness unknown.",
)


class CleanroomProviderEnumerationImportError(ValueError):
    """Raised when exact provider evidence cannot be admitted safely."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanroomProviderEnumerationImportError(message)


def _decode_json_object(data: bytes, context: str) -> dict[str, Any]:
    def reject_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CleanroomProviderEnumerationImportError(
                    f"{context} contains duplicate JSON key {key!r}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except CleanroomProviderEnumerationImportError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanroomProviderEnumerationImportError(
            f"cannot decode {context}: {exc}"
        ) from exc
    _require(isinstance(value, dict), f"{context} must be a JSON object")
    return value


def _read_regular_file(path: Path, context: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CleanroomProviderEnumerationImportError(
            f"cannot inspect {context} {path}: {exc}"
        ) from exc
    _require(not stat.S_ISLNK(metadata.st_mode), f"{context} cannot be a symlink: {path}")
    _require(stat.S_ISREG(metadata.st_mode), f"{context} must be a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CleanroomProviderEnumerationImportError(
            f"cannot read {context} {path}: {exc}"
        ) from exc


def _validate_base(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "base observation set must be an object")
    expected = {
        "format",
        "session",
        "artifacts",
        "evidence",
        "observations",
        "provider_enumeration",
        "limitations",
    }
    _require(set(value) == expected, "base observation-set surface is not exactly V1")
    _require(
        value["format"] == RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
        "base observation-set format is not V1",
    )
    try:
        receipt = build_runtime_service_receipt(
            session=value["session"],
            artifacts=value["artifacts"],
            evidence=value["evidence"],
            observations=value["observations"],
            provider_enumeration=value["provider_enumeration"],
            limitations=value["limitations"],
        )
    except RuntimeServiceValidationError as exc:
        raise CleanroomProviderEnumerationImportError(
            f"base observation set failed semantic validation: {exc}"
        ) from exc
    _require(
        receipt["provider_enumeration"]["state"] == "not_enumerated",
        "base observation set already contains provider enumeration evidence",
    )
    return receipt


def _validate_lock(lock_bytes: bytes, session: Mapping[str, Any]) -> None:
    lock = _decode_json_object(lock_bytes, "transformer toolchain lock")
    _require(lock.get("format") == TOOLCHAIN_LOCK_FORMAT, "toolchain lock format is not V1")
    _require(lock.get("schema_version") == 1, "toolchain lock schema_version is not 1")
    _require(
        hashlib.sha256(lock_bytes).hexdigest()
        == session["candidate_toolchain_lock_sha256"],
        "toolchain lock bytes do not match the bound session hash",
    )
    binding = lock.get("binding")
    _require(isinstance(binding, dict), "toolchain lock binding must be an object")
    _require(
        binding.get("candidate_id") == session["profile_id"],
        "toolchain lock candidate does not match session.profile_id",
    )
    expectations = lock.get("native_service_expectations")
    _require(
        isinstance(expectations, dict)
        and expectations.get("mixin_service") == EXPECTED_CLEANMIX_SERVICE,
        "toolchain lock does not declare the exact CleanMix service implementation",
    )


def _path_from_file_uri(value: str, context: str) -> Path:
    parsed = urlsplit(value)
    _require(parsed.scheme == "file", f"{context} is not a file URI")
    _require(
        parsed.netloc in {"", "localhost"},
        f"{context} is not a host-local file URI",
    )
    _require(
        parsed.query == "" and parsed.fragment == "",
        f"{context} has query or fragment data",
    )
    decoded = unquote(parsed.path)
    _require("\x00" not in decoded, f"{context} contains a NUL byte")
    _require(
        re.match(r"^/[A-Za-z]:/", decoded) is None,
        f"{context} is not local to this operating system",
    )
    return Path(decoded)


def _service_descriptor_entries(data: bytes, context: str) -> set[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CleanroomProviderEnumerationImportError(
            f"{context} is not UTF-8: {exc}"
        ) from exc
    _require("\x00" not in text, f"{context} contains a NUL byte")
    entries: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        _require(
            _BINARY_NAME_RE.fullmatch(value) is not None,
            f"{context} line {line_number} is not a Java binary name",
        )
        entries.add(value)
    return entries


def _validate_provider_members(
    artifact_bytes: bytes,
    *,
    provider_classes: set[str],
    label: str,
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(artifact_bytes)) as archive:
            infos = archive.infolist()
            names = [row.filename for row in infos]
            _require(
                names.count(SERVICE_DESCRIPTOR) == 1,
                f"{label} must contain exactly one {SERVICE_DESCRIPTOR}",
            )
            descriptor = _service_descriptor_entries(
                archive.read(SERVICE_DESCRIPTOR),
                f"{label}:{SERVICE_DESCRIPTOR}",
            )
            for service_class in sorted(provider_classes):
                member = service_class.replace(".", "/") + ".class"
                _require(
                    names.count(member) == 1,
                    f"{label} does not contain exactly one observed provider class {member}",
                )
                _require(
                    service_class in descriptor,
                    f"{label} service descriptor does not list observed provider {service_class}",
                )
    except zipfile.BadZipFile as exc:
        raise CleanroomProviderEnumerationImportError(
            f"provider artifact {label} is not a valid ZIP/JAR: {exc}"
        ) from exc


def _measure_provider_artifacts(
    paths: Sequence[Path],
    providers: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    expected_hashes = {str(row["provider_artifact_sha256"]) for row in providers}
    _require(
        len(paths) == len(expected_hashes),
        "provider artifact inputs must exactly cover distinct observed source artifacts",
    )
    measured: dict[str, dict[str, Any]] = {}
    for path in paths:
        data = _read_regular_file(path, "provider artifact")
        digest = hashlib.sha256(data).hexdigest()
        _require(digest not in measured, "provider artifact inputs repeat an exact identity")
        measured[digest] = {
            "path": path.resolve(),
            "bytes": data,
            "size": len(data),
            "label": path.name,
        }
    _require(
        set(measured) == expected_hashes,
        "provider artifact SHA-256 set does not match probe evidence",
    )

    classes_by_artifact: dict[str, set[str]] = {}
    for index, row in enumerate(providers):
        digest = str(row["provider_artifact_sha256"])
        record = measured[digest]
        _require(
            row["provider_artifact_size_bytes"] == record["size"],
            f"providers[{index}] source artifact size does not match measured bytes",
        )
        source_path = _path_from_file_uri(
            str(row["source_uri"]), f"providers[{index}].source_uri"
        )
        try:
            source_metadata = source_path.lstat()
        except OSError as exc:
            raise CleanroomProviderEnumerationImportError(
                f"cannot inspect providers[{index}] source URI: {exc}"
            ) from exc
        _require(
            not stat.S_ISLNK(source_metadata.st_mode)
            and stat.S_ISREG(source_metadata.st_mode),
            f"providers[{index}] source URI is not a regular non-symlink file",
        )
        try:
            same_file = os.path.samefile(source_path, record["path"])
        except OSError as exc:
            raise CleanroomProviderEnumerationImportError(
                f"cannot compare providers[{index}] source URI: {exc}"
            ) from exc
        _require(
            same_file,
            f"providers[{index}] source URI does not name its measured provider artifact",
        )
        service_class = str(row["service_class"])
        _require(
            _BINARY_NAME_RE.fullmatch(service_class) is not None,
            f"providers[{index}].service_class is not a Java binary name",
        )
        classes_by_artifact.setdefault(digest, set()).add(service_class)

    for digest, classes in classes_by_artifact.items():
        record = measured[digest]
        _validate_provider_members(
            record["bytes"], provider_classes=classes, label=record["label"]
        )
    return measured


def import_provider_enumeration(
    *,
    base_observation_set: Mapping[str, Any],
    provider_evidence_bytes: bytes,
    provider_artifact_paths: Sequence[Path],
    toolchain_lock_bytes: bytes,
) -> dict[str, Any]:
    """Merge one exact probe document into a generic observation set."""

    base = _validate_base(base_observation_set)
    _validate_lock(toolchain_lock_bytes, base["session"])
    raw = _decode_json_object(provider_evidence_bytes, "provider enumeration evidence")
    try:
        provider_evidence = parse_provider_enumeration_evidence(raw)
    except ProviderEnumerationValidationError as exc:
        raise CleanroomProviderEnumerationImportError(
            f"provider enumeration evidence failed semantic validation: {exc}"
        ) from exc
    _require(
        provider_evidence_bytes == render_provider_enumeration_evidence(provider_evidence),
        "provider enumeration evidence bytes are not canonical V1 output",
    )
    _require(
        provider_evidence["session"] == base["session"],
        "provider enumeration session does not match the base runtime receipt",
    )
    _require(
        provider_evidence["probe"]["mechanism"]
        == SERVICE_LOADER_ITERATOR_MECHANISM,
        "provider enumeration mechanism is not exact V1",
    )

    if provider_evidence["state"] == "enumerated":
        measured = _measure_provider_artifacts(
            provider_artifact_paths,
            provider_evidence["providers"],
        )
    else:
        _require(
            not provider_artifact_paths,
            "failed enumeration cannot accept provider artifact inputs",
        )
        measured = {}

    artifacts = list(base["artifacts"])
    existing_artifacts = {row["artifact_sha256"]: row for row in artifacts}
    for digest in sorted(measured):
        _require(
            digest not in existing_artifacts,
            "provider artifact identity already has a non-provider role in the base receipt",
        )
        record = measured[digest]
        artifacts.append(
            {
                "artifact_sha256": digest,
                "size_bytes": record["size"],
                "role": "service_provider",
                "label": record["label"],
            }
        )

    evidence_sha = hashlib.sha256(provider_evidence_bytes).hexdigest()
    evidence = list(base["evidence"])
    _require(
        evidence_sha not in {row["source_sha256"] for row in evidence},
        "provider enumeration evidence duplicates an existing evidence identity",
    )
    evidence.append(
        {
            "source_sha256": evidence_sha,
            "size_bytes": len(provider_evidence_bytes),
            "kind": "service_loader_enumeration",
            "label": EVIDENCE_LABEL,
        }
    )
    evidence_id = provider_evidence["evidence_id"]
    enumeration = {
        "state": provider_evidence["state"],
        "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
        "providers": [
            {
                "service_class": row["service_class"],
                "service_name": row["service_name"],
                "provider_artifact_sha256": row["provider_artifact_sha256"],
            }
            for row in provider_evidence["providers"]
        ],
        "evidence_sha256": evidence_sha,
        "evidence_id": evidence_id,
        "source_record": evidence_id,
        "failure": provider_evidence["failure"],
    }
    limitations = sorted(set(base["limitations"]) | set(_LIMITATIONS))
    admitted = build_runtime_service_receipt(
        session=base["session"],
        artifacts=artifacts,
        evidence=evidence,
        observations=base["observations"],
        provider_enumeration=enumeration,
        limitations=limitations,
    )
    return {
        "format": RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
        "session": admitted["session"],
        "artifacts": admitted["artifacts"],
        "evidence": admitted["evidence"],
        "observations": admitted["observations"],
        "provider_enumeration": admitted["provider_enumeration"],
        "limitations": admitted["limitations"],
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise CleanroomProviderEnumerationImportError(
            f"output cannot be a symlink: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".mixin-provider-enumeration-import-",
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
        description="Import exact Java IMixinService provider enumeration evidence."
    )
    parser.add_argument("--base-observation-set", required=True, type=Path)
    parser.add_argument("--probe-evidence", required=True, type=Path)
    parser.add_argument("--toolchain-lock", required=True, type=Path)
    parser.add_argument("--provider-artifact", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    base = _decode_json_object(
        _read_regular_file(args.base_observation_set, "base observation set"),
        "base observation set",
    )
    value = import_provider_enumeration(
        base_observation_set=base,
        provider_evidence_bytes=_read_regular_file(
            args.probe_evidence, "provider enumeration evidence"
        ),
        provider_artifact_paths=args.provider_artifact,
        toolchain_lock_bytes=_read_regular_file(
            args.toolchain_lock, "transformer toolchain lock"
        ),
    )
    _atomic_write(args.output, value)
    print(value["provider_enumeration"]["evidence_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CleanroomProviderEnumerationImportError as exc:
        print(f"Mixin provider enumeration import failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
