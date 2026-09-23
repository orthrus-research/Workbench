"""Exact Mixin compiler/AP invocation-custody receipts.

The receipt binds caller-declared bytes around one compiler invocation.  It is
deliberately not a process attestation or a reproducible-build certificate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence
from zipfile import BadZipFile, LargeZipFile, ZipFile

from .mixin_topology import (
    ArtifactScanError,
    MAX_ARCHIVE_BYTES,
    _inventory_archive,
    _read_member,
    canonical_json_bytes,
)


MIXIN_BUILD_FORMAT = (
    "workbench-project-intelligence-mixin-compiler-ap-build-receipt-v1"
)
MIXIN_BUILD_POLICY_FORMAT = "workbench-mixin-compiler-ap-build-policy-v1"
MIXIN_BUILD_INPUT_FORMAT = "workbench-mixin-compiler-ap-build-input-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
MAX_BUILD_INPUT_BYTES = MAX_ARCHIVE_BYTES

_RECEIPT_ID_PREFIX = "workbench-mixin-compiler-ap-build-receipt:sha256:"
_INVOCATION_ID_PREFIX = "workbench-mixin-compiler-ap-invocation:sha256:"
_FILE_ID_PREFIX = "workbench-mixin-compiler-ap-file:sha256:"
_OPTIONS_ID_PREFIX = "workbench-mixin-compiler-ap-options:sha256:"
_PROCESSOR_ID_PREFIX = "workbench-mixin-compiler-ap-processor:sha256:"
_SERVICE_ID_PREFIX = "workbench-mixin-compiler-ap-service-entry:sha256:"
_INPUT_ID_PREFIX = "workbench-mixin-compiler-ap-input:sha256:"
_OUTPUT_ID_PREFIX = "workbench-mixin-compiler-ap-output:sha256:"
_ISSUE_ID_PREFIX = "workbench-mixin-compiler-ap-issue:sha256:"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_CLASS_NAME_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$"
)
_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

_POLICY_KEYS = {
    "cleanmix_compatibility_output_name",
    "component",
    "format",
    "minimum_processor_artifact_count",
    "minimum_runtime_artifact_count",
    "minimum_source_count",
    "policy_id",
    "processor_service_path",
    "required_output_kinds",
    "required_processor_artifact_sha256s",
    "required_processor_providers",
    "schema_version",
}

_ISSUE_KINDS = {
    "cleanmix-compatibility-output-name-mismatch",
    "compiler-executable-empty",
    "compiler-executable-missing",
    "compiler-identity-capture-empty",
    "compiler-identity-capture-missing",
    "diagnostics-capture-missing",
    "input-missing",
    "output-missing",
    "processor-artifact-count-below-policy",
    "processor-artifact-missing",
    "processor-service-entry-invalid",
    "processor-service-entry-missing",
    "required-output-kind-missing",
    "required-processor-artifact-missing",
    "required-processor-provider-missing",
    "runtime-artifact-count-below-policy",
    "runtime-artifact-empty",
    "runtime-artifact-missing",
    "runtime-identity-capture-empty",
    "runtime-identity-capture-missing",
    "source-count-below-policy",
    "source-missing",
}

_BOUNDARIES = {
    "caller_declared_single_invocation": True,
    "compiler_process_execution_attested": False,
    "exact_declared_bytes_bound": True,
    "reproducible_build_proved": False,
    "runtime_behavior_proved": False,
    "source_to_output_causality_proved": False,
}

_LIMITATIONS = [
    "The receipt records caller-declared custody for one invocation; it does not attest that the compiler process executed.",
    "Exact input and output hashes do not prove source-to-output causality or a reproducible build.",
    "Environment variables, filesystem state, network access, process scheduling, locale, and wall-clock state are not captured.",
    "Diagnostics are bound as exact bytes but are not interpreted as proof of compiler success.",
    "Refmap, mapping, and CleanMix compatibility outputs are bound as bytes; their semantic correctness is evaluated by separate authorities.",
]


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ExactBuildFileInput:
    """One caller-declared exact file slot; ``None`` records absence."""

    label: str
    logical_name: str
    data: bytes | None


@dataclass(frozen=True)
class ClassifiedBuildFileInput:
    """An exact file with a caller-declared input or output kind."""

    kind: str
    file: ExactBuildFileInput


@dataclass(frozen=True)
class MixinCompilerAPInvocationInput:
    """Exact caller-supplied custody material for one compiler invocation."""

    invocation_label: str
    compiler_executable: ExactBuildFileInput
    runtime_artifacts: Sequence[ExactBuildFileInput]
    compiler_identity_capture: ExactBuildFileInput
    runtime_identity_capture: ExactBuildFileInput
    processor_artifacts: Sequence[ExactBuildFileInput]
    compiler_options: Sequence[str]
    annotation_processor_options: Sequence[str]
    diagnostics: ExactBuildFileInput
    sources: Sequence[ExactBuildFileInput]
    inputs: Sequence[ClassifiedBuildFileInput]
    outputs: Sequence[ClassifiedBuildFileInput]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + _sha256(canonical_json_bytes(value))


def _identified(prefix: str, id_field: str, material: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(material)
    row[id_field] = _content_id(prefix, material)
    return row


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_json_object,
        parse_constant=_reject_constant,
    )


def _plain_string(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise ArtifactScanError(f"{context} is invalid")
    return value


def _logical_name(value: Any, context: str) -> str:
    value = _plain_string(value, context)
    if "\\" in value:
        raise ArtifactScanError(f"{context} is not a safe logical name")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or value.endswith("/")
        or str(path) != value
    ):
        raise ArtifactScanError(f"{context} is not a safe logical name")
    return value


def _kind(value: Any, context: str) -> str:
    if not isinstance(value, str) or _KIND_RE.fullmatch(value) is None:
        raise ArtifactScanError(f"{context} is invalid")
    return value


def _sorted_unique_strings(
    value: Any, context: str, validator: re.Pattern[str] | None = None
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ArtifactScanError(f"{context} must be a non-empty array")
    if any(
        not isinstance(item, str)
        or not item
        or (validator is not None and validator.fullmatch(item) is None)
        for item in value
    ):
        raise ArtifactScanError(f"{context} contains an invalid value")
    if value != sorted(value) or len(value) != len(set(value)):
        raise ArtifactScanError(f"{context} must be sorted and unique")
    return list(value)


def _positive_integer(value: Any, context: str) -> int:
    if type(value) is not int or value < 1:
        raise ArtifactScanError(f"{context} must be a positive integer")
    return value


def _validate_policy_object(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != _POLICY_KEYS
        or value.get("format") != MIXIN_BUILD_POLICY_FORMAT
        or value.get("schema_version") != 1
    ):
        raise ArtifactScanError("Mixin compiler/AP build policy has an unsupported V1 shape")

    policy = dict(value)
    policy["policy_id"] = _plain_string(policy.get("policy_id"), "policy_id")
    component = policy.get("component")
    if (
        not isinstance(component, dict)
        or set(component) != {"distribution", "distribution_version", "source_revision"}
        or not all(
            isinstance(component.get(field), str)
            and component[field]
            and component[field] == component[field].strip()
            and "\x00" not in component[field]
            for field in ("distribution", "distribution_version", "source_revision")
        )
        or _REVISION_RE.fullmatch(component["source_revision"]) is None
    ):
        raise ArtifactScanError("Mixin compiler/AP build policy component is invalid")
    policy["component"] = dict(component)
    policy["processor_service_path"] = _logical_name(
        policy.get("processor_service_path"), "policy processor_service_path"
    )
    policy["cleanmix_compatibility_output_name"] = _logical_name(
        policy.get("cleanmix_compatibility_output_name"),
        "policy cleanmix_compatibility_output_name",
    )
    policy["required_processor_artifact_sha256s"] = _sorted_unique_strings(
        policy.get("required_processor_artifact_sha256s"),
        "policy required_processor_artifact_sha256s",
        _SHA256_RE,
    )
    policy["required_processor_providers"] = _sorted_unique_strings(
        policy.get("required_processor_providers"),
        "policy required_processor_providers",
        _CLASS_NAME_RE,
    )
    policy["required_output_kinds"] = _sorted_unique_strings(
        policy.get("required_output_kinds"), "policy required_output_kinds", _KIND_RE
    )
    if "cleanmix-compatibility" not in policy["required_output_kinds"]:
        raise ArtifactScanError(
            "Mixin compiler/AP build policy must require cleanmix-compatibility output"
        )
    for field in (
        "minimum_processor_artifact_count",
        "minimum_runtime_artifact_count",
        "minimum_source_count",
    ):
        policy[field] = _positive_integer(policy.get(field), f"policy {field}")
    return policy


def _parse_policy(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        value = _strict_json(raw)
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise ArtifactScanError(f"Mixin compiler/AP build policy is invalid JSON: {exc}") from exc
    return _validate_policy_object(value), _sha256(raw)


def _validate_file_input(value: Any, context: str) -> ExactBuildFileInput:
    if not isinstance(value, ExactBuildFileInput):
        raise ArtifactScanError(f"{context} is not an ExactBuildFileInput")
    _plain_string(value.label, f"{context} label")
    _logical_name(value.logical_name, f"{context} logical_name")
    if value.data is not None and not isinstance(value.data, bytes):
        raise ArtifactScanError(f"{context} data must be exact bytes or None")
    if value.data is not None and len(value.data) > MAX_BUILD_INPUT_BYTES:
        raise ArtifactScanError(
            f"{context} exceeds {MAX_BUILD_INPUT_BYTES} input bytes"
        )
    return value


def _file_record(value: ExactBuildFileInput, context: str) -> dict[str, Any]:
    value = _validate_file_input(value, context)
    material: dict[str, Any] = {
        "label": value.label,
        "logical_name": value.logical_name,
        "present": value.data is not None,
        "sha256": None if value.data is None else _sha256(value.data),
        "size_bytes": None if value.data is None else len(value.data),
    }
    return _identified(_FILE_ID_PREFIX, "file_id", material)


def _options_record(
    compiler_options: Sequence[str], annotation_processor_options: Sequence[str]
) -> dict[str, Any]:
    if isinstance(compiler_options, (str, bytes)) or isinstance(
        annotation_processor_options, (str, bytes)
    ):
        raise ArtifactScanError("compiler and AP options must be ordered string arrays")
    compiler = list(compiler_options)
    processors = list(annotation_processor_options)
    for context, options in (
        ("compiler option", compiler),
        ("annotation processor option", processors),
    ):
        for index, option in enumerate(options):
            if (
                not isinstance(option, str)
                or not option
                or "\x00" in option
                or len(option) > 16_384
            ):
                raise ArtifactScanError(f"{context} {index} is invalid")
    return _identified(
        _OPTIONS_ID_PREFIX,
        "options_id",
        {
            "annotation_processor_options": processors,
            "compiler_options": compiler,
        },
    )


def _parse_service_providers(raw: bytes) -> tuple[list[str], str | None]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        return [], f"invalid UTF-8: {exc}"
    providers: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        provider = line.split("#", 1)[0].strip()
        if not provider:
            continue
        if _CLASS_NAME_RE.fullmatch(provider) is None:
            return [], f"line {line_number} has invalid provider {provider!r}"
        providers.append(provider)
    if len(providers) != len(set(providers)):
        return [], "service entry repeats a provider"
    return sorted(providers), None


def _service_entry(
    artifact: ExactBuildFileInput, service_path: str, context: str
) -> dict[str, Any]:
    if artifact.data is None:
        material = {
            "error": None,
            "parse_state": "missing",
            "path": service_path,
            "present": False,
            "providers": [],
            "sha256": None,
            "size_bytes": None,
        }
        return _identified(_SERVICE_ID_PREFIX, "service_entry_id", material)

    try:
        with ZipFile(BytesIO(artifact.data)) as archive:
            members, _, _ = _inventory_archive(archive)
            info = members.get(service_path)
            if info is None or info.is_dir():
                material = {
                    "error": None,
                    "parse_state": "missing",
                    "path": service_path,
                    "present": False,
                    "providers": [],
                    "sha256": None,
                    "size_bytes": None,
                }
            else:
                raw = _read_member(archive, info, f"{context} processor service entry")
                providers, error = _parse_service_providers(raw)
                material = {
                    "error": error,
                    "parse_state": "exact" if error is None else "invalid",
                    "path": service_path,
                    "present": True,
                    "providers": providers,
                    "sha256": _sha256(raw),
                    "size_bytes": len(raw),
                }
    except (BadZipFile, LargeZipFile, OSError, EOFError) as exc:
        raise ArtifactScanError(f"cannot inspect {context} as a processor archive: {exc}") from exc
    return _identified(_SERVICE_ID_PREFIX, "service_entry_id", material)


def _processor_record(
    artifact: ExactBuildFileInput, service_path: str, index: int
) -> dict[str, Any]:
    context = f"processor artifact {index} ({artifact.label})"
    artifact_record = _file_record(artifact, context)
    service = _service_entry(artifact, service_path, context)
    return _identified(
        _PROCESSOR_ID_PREFIX,
        "processor_id",
        {"artifact": artifact_record, "service_entry": service},
    )


def _classified_record(
    value: ClassifiedBuildFileInput, *, output: bool, index: int
) -> dict[str, Any]:
    noun = "output" if output else "input"
    if not isinstance(value, ClassifiedBuildFileInput):
        raise ArtifactScanError(f"{noun} {index} is not a ClassifiedBuildFileInput")
    kind = _kind(value.kind, f"{noun} {index} kind")
    file_record = _file_record(value.file, f"{noun} {index}")
    prefix = _OUTPUT_ID_PREFIX if output else _INPUT_ID_PREFIX
    id_field = "output_id" if output else "input_id"
    return _identified(
        prefix,
        id_field,
        {f"{noun}_kind": kind, "file": file_record},
    )


def _require_unique_file_slots(rows: Sequence[Mapping[str, Any]], context: str) -> None:
    labels = [row["label"] for row in rows]
    logical_names = [row["logical_name"] for row in rows]
    if len(labels) != len(set(labels)):
        raise ArtifactScanError(f"{context} repeats a label")
    if len(logical_names) != len(set(logical_names)):
        raise ArtifactScanError(f"{context} repeats a logical_name")


def _issue(kind: str, locator: str, expected: Any, observed: Any) -> dict[str, Any]:
    if kind not in _ISSUE_KINDS:
        raise AssertionError(f"unknown Mixin build issue kind {kind}")
    return _identified(
        _ISSUE_ID_PREFIX,
        "issue_id",
        {
            "expected": expected,
            "issue_kind": kind,
            "locator": locator,
            "observed": observed,
        },
    )


def _present(row: Mapping[str, Any]) -> bool:
    return row["present"] is True


def _derive_issues(receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    invocation = receipt["invocation"]
    policy = receipt["policy"]

    executable = invocation["compiler_executable"]
    if not _present(executable):
        issues.append(_issue("compiler-executable-missing", executable["logical_name"], True, False))
    elif executable["size_bytes"] == 0:
        issues.append(_issue("compiler-executable-empty", executable["logical_name"], ">0 bytes", 0))

    compiler_identity = invocation["compiler_identity_capture"]
    if not _present(compiler_identity):
        issues.append(_issue("compiler-identity-capture-missing", compiler_identity["logical_name"], True, False))
    elif compiler_identity["size_bytes"] == 0:
        issues.append(_issue("compiler-identity-capture-empty", compiler_identity["logical_name"], ">0 bytes", 0))

    runtime_identity = invocation["runtime_identity_capture"]
    if not _present(runtime_identity):
        issues.append(_issue("runtime-identity-capture-missing", runtime_identity["logical_name"], True, False))
    elif runtime_identity["size_bytes"] == 0:
        issues.append(_issue("runtime-identity-capture-empty", runtime_identity["logical_name"], ">0 bytes", 0))

    runtime_artifacts = invocation["runtime_artifacts"]
    for row in runtime_artifacts:
        if not _present(row):
            issues.append(_issue("runtime-artifact-missing", row["logical_name"], True, False))
        elif row["size_bytes"] == 0:
            issues.append(_issue("runtime-artifact-empty", row["logical_name"], ">0 bytes", 0))
    present_runtime = sum(_present(row) and row["size_bytes"] > 0 for row in runtime_artifacts)
    if present_runtime < policy["minimum_runtime_artifact_count"]:
        issues.append(
            _issue(
                "runtime-artifact-count-below-policy",
                "invocation.runtime_artifacts",
                policy["minimum_runtime_artifact_count"],
                present_runtime,
            )
        )

    processors = receipt["processors"]
    present_processors = 0
    observed_hashes: list[str] = []
    observed_providers: set[str] = set()
    for row in processors:
        artifact = row["artifact"]
        service = row["service_entry"]
        if not _present(artifact):
            issues.append(_issue("processor-artifact-missing", artifact["logical_name"], True, False))
            continue
        present_processors += 1
        observed_hashes.append(artifact["sha256"])
        if service["parse_state"] == "missing":
            issues.append(
                _issue(
                    "processor-service-entry-missing",
                    artifact["logical_name"],
                    policy["processor_service_path"],
                    None,
                )
            )
        elif service["parse_state"] == "invalid":
            issues.append(
                _issue(
                    "processor-service-entry-invalid",
                    artifact["logical_name"],
                    "valid UTF-8 ServiceLoader provider list",
                    service["error"],
                )
            )
        else:
            observed_providers.update(service["providers"])
    if present_processors < policy["minimum_processor_artifact_count"]:
        issues.append(
            _issue(
                "processor-artifact-count-below-policy",
                "processors",
                policy["minimum_processor_artifact_count"],
                present_processors,
            )
        )
    for required_hash in policy["required_processor_artifact_sha256s"]:
        if required_hash not in observed_hashes:
            issues.append(
                _issue(
                    "required-processor-artifact-missing",
                    "processors",
                    required_hash,
                    sorted(observed_hashes),
                )
            )
    for provider in policy["required_processor_providers"]:
        if provider not in observed_providers:
            issues.append(
                _issue(
                    "required-processor-provider-missing",
                    policy["processor_service_path"],
                    provider,
                    sorted(observed_providers),
                )
            )

    diagnostics = receipt["diagnostics"]
    if not _present(diagnostics):
        issues.append(_issue("diagnostics-capture-missing", diagnostics["logical_name"], True, False))

    sources = receipt["sources"]
    for row in sources:
        if not _present(row):
            issues.append(_issue("source-missing", row["logical_name"], True, False))
    present_sources = sum(_present(row) for row in sources)
    if present_sources < policy["minimum_source_count"]:
        issues.append(
            _issue(
                "source-count-below-policy",
                "sources",
                policy["minimum_source_count"],
                present_sources,
            )
        )

    for row in receipt["inputs"]:
        file_row = row["file"]
        if not _present(file_row):
            issues.append(_issue("input-missing", file_row["logical_name"], True, False))

    outputs = receipt["outputs"]
    for row in outputs:
        file_row = row["file"]
        if not _present(file_row):
            issues.append(_issue("output-missing", file_row["logical_name"], True, False))
        if (
            row["output_kind"] == "cleanmix-compatibility"
            and file_row["logical_name"] != policy["cleanmix_compatibility_output_name"]
        ):
            issues.append(
                _issue(
                    "cleanmix-compatibility-output-name-mismatch",
                    file_row["logical_name"],
                    policy["cleanmix_compatibility_output_name"],
                    file_row["logical_name"],
                )
            )
    for required_kind in policy["required_output_kinds"]:
        count = sum(
            row["output_kind"] == required_kind and _present(row["file"])
            for row in outputs
        )
        if count == 0:
            issues.append(
                _issue(
                    "required-output-kind-missing",
                    "outputs",
                    required_kind,
                    0,
                )
            )

    return sorted(
        issues,
        key=lambda row: canonical_json_bytes(
            {key: value for key, value in row.items() if key != "issue_id"}
        ),
    )


def _all_file_rows(receipt: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    invocation = receipt["invocation"]
    return [
        invocation["compiler_executable"],
        invocation["compiler_identity_capture"],
        invocation["runtime_identity_capture"],
        *invocation["runtime_artifacts"],
        *[row["artifact"] for row in receipt["processors"]],
        receipt["diagnostics"],
        *receipt["sources"],
        *[row["file"] for row in receipt["inputs"]],
        *[row["file"] for row in receipt["outputs"]],
    ]


def _summary(receipt: Mapping[str, Any], issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files = _all_file_rows(receipt)
    output_kinds = sorted(
        set(receipt["policy"]["required_output_kinds"])
        | {row["output_kind"] for row in receipt["outputs"]}
    )
    providers = {
        provider
        for processor in receipt["processors"]
        for provider in processor["service_entry"]["providers"]
    }
    return {
        "annotation_processor_option_count": len(
            receipt["invocation"]["options"]["annotation_processor_options"]
        ),
        "compiler_option_count": len(receipt["invocation"]["options"]["compiler_options"]),
        "custody_state": "partial" if issues else "complete",
        "declared_file_count": len(files),
        "input_count": len(receipt["inputs"]),
        "issue_count": len(issues),
        "issue_counts": {
            kind: sum(row["issue_kind"] == kind for row in issues)
            for kind in sorted(_ISSUE_KINDS)
        },
        "missing_file_count": sum(not _present(row) for row in files),
        "output_counts": {
            kind: sum(
                row["output_kind"] == kind and _present(row["file"])
                for row in receipt["outputs"]
            )
            for kind in output_kinds
        },
        "present_file_count": sum(_present(row) for row in files),
        "processor_artifact_count": len(receipt["processors"]),
        "processor_provider_count": len(providers),
        "runtime_artifact_count": len(receipt["invocation"]["runtime_artifacts"]),
        "source_count": len(receipt["sources"]),
    }


def build_mixin_compiler_ap_build_receipt(
    invocation: MixinCompilerAPInvocationInput, policy_bytes: bytes
) -> dict[str, Any]:
    """Build one content-addressed exact invocation-custody receipt."""

    if not isinstance(invocation, MixinCompilerAPInvocationInput):
        raise ArtifactScanError("invocation is not a MixinCompilerAPInvocationInput")
    if not isinstance(policy_bytes, bytes):
        raise ArtifactScanError("policy_bytes must be exact bytes")
    policy, policy_sha256 = _parse_policy(policy_bytes)
    invocation_label = _plain_string(invocation.invocation_label, "invocation_label")

    compiler_executable = _file_record(invocation.compiler_executable, "compiler executable")
    compiler_identity = _file_record(
        invocation.compiler_identity_capture, "compiler identity capture"
    )
    runtime_identity = _file_record(
        invocation.runtime_identity_capture, "runtime identity capture"
    )
    runtime_artifacts = [
        _file_record(value, f"runtime artifact {index}")
        for index, value in enumerate(invocation.runtime_artifacts)
    ]
    _require_unique_file_slots(runtime_artifacts, "runtime artifacts")
    options = _options_record(
        invocation.compiler_options, invocation.annotation_processor_options
    )
    invocation_record = _identified(
        _INVOCATION_ID_PREFIX,
        "invocation_id",
        {
            "compiler_executable": compiler_executable,
            "compiler_identity_capture": compiler_identity,
            "invocation_label": invocation_label,
            "options": options,
            "runtime_artifacts": runtime_artifacts,
            "runtime_identity_capture": runtime_identity,
        },
    )

    processor_inputs = list(invocation.processor_artifacts)
    processor_records = [
        _processor_record(value, policy["processor_service_path"], index)
        for index, value in enumerate(processor_inputs)
    ]
    _require_unique_file_slots(
        [row["artifact"] for row in processor_records], "processor artifacts"
    )
    diagnostics = _file_record(invocation.diagnostics, "diagnostics capture")
    sources = [
        _file_record(value, f"source {index}")
        for index, value in enumerate(invocation.sources)
    ]
    _require_unique_file_slots(sources, "sources")
    inputs = [
        _classified_record(value, output=False, index=index)
        for index, value in enumerate(invocation.inputs)
    ]
    outputs = [
        _classified_record(value, output=True, index=index)
        for index, value in enumerate(invocation.outputs)
    ]
    _require_unique_file_slots([row["file"] for row in inputs], "inputs")
    _require_unique_file_slots([row["file"] for row in outputs], "outputs")

    policy_binding = dict(policy)
    policy_binding["sha256"] = policy_sha256
    material: dict[str, Any] = {
        "boundaries": dict(_BOUNDARIES),
        "canonicalization_id": CANONICALIZATION_ID,
        "diagnostics": diagnostics,
        "format": MIXIN_BUILD_FORMAT,
        "inputs": inputs,
        "invocation": invocation_record,
        "issues": [],
        "limitations": list(_LIMITATIONS),
        "outputs": outputs,
        "policy": policy_binding,
        "processors": processor_records,
        "schema_version": 1,
        "sources": sources,
        "summary": {},
    }
    material["issues"] = _derive_issues(material)
    material["summary"] = _summary(material, material["issues"])
    receipt = _identified(_RECEIPT_ID_PREFIX, "receipt_id", material)
    validate_mixin_compiler_ap_build_receipt(receipt)
    return receipt


def _validate_file_record(value: Any, context: str) -> None:
    expected = {"file_id", "label", "logical_name", "present", "sha256", "size_bytes"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ArtifactScanError(f"{context} file record has an unsupported V1 shape")
    _plain_string(value.get("label"), f"{context} label")
    _logical_name(value.get("logical_name"), f"{context} logical_name")
    if type(value.get("present")) is not bool:
        raise ArtifactScanError(f"{context} presence is invalid")
    if value["present"]:
        if (
            not isinstance(value.get("sha256"), str)
            or _SHA256_RE.fullmatch(value["sha256"]) is None
            or type(value.get("size_bytes")) is not int
            or value["size_bytes"] < 0
            or value["size_bytes"] > MAX_BUILD_INPUT_BYTES
        ):
            raise ArtifactScanError(f"{context} present-file binding is invalid")
    elif value.get("sha256") is not None or value.get("size_bytes") is not None:
        raise ArtifactScanError(f"{context} absent-file binding is invalid")
    material = {key: item for key, item in value.items() if key != "file_id"}
    if value.get("file_id") != _content_id(_FILE_ID_PREFIX, material):
        raise ArtifactScanError(f"{context} file_id is invalid")


def _validate_options(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "annotation_processor_options",
        "compiler_options",
        "options_id",
    }:
        raise ArtifactScanError("Mixin compiler/AP options have an unsupported V1 shape")
    rebuilt = _options_record(
        value["compiler_options"], value["annotation_processor_options"]
    )
    if value != rebuilt:
        raise ArtifactScanError("Mixin compiler/AP options_id is invalid")


def _validate_service_entry(value: Any, policy: Mapping[str, Any], context: str) -> None:
    expected = {
        "error",
        "parse_state",
        "path",
        "present",
        "providers",
        "service_entry_id",
        "sha256",
        "size_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ArtifactScanError(f"{context} service entry has an unsupported V1 shape")
    if value.get("path") != policy["processor_service_path"]:
        raise ArtifactScanError(f"{context} service path does not match policy")
    state = value.get("parse_state")
    providers = value.get("providers")
    if not isinstance(providers, list):
        raise ArtifactScanError(f"{context} service providers are invalid")
    if state == "missing":
        if {
            "error": value.get("error"),
            "present": value.get("present"),
            "providers": providers,
            "sha256": value.get("sha256"),
            "size_bytes": value.get("size_bytes"),
        } != {
            "error": None,
            "present": False,
            "providers": [],
            "sha256": None,
            "size_bytes": None,
        }:
            raise ArtifactScanError(f"{context} missing service entry is inconsistent")
    elif state in {"exact", "invalid"}:
        if (
            value.get("present") is not True
            or not isinstance(value.get("sha256"), str)
            or _SHA256_RE.fullmatch(value["sha256"]) is None
            or type(value.get("size_bytes")) is not int
            or value["size_bytes"] < 0
        ):
            raise ArtifactScanError(f"{context} present service binding is invalid")
        if state == "exact":
            if (
                value.get("error") is not None
                or providers != sorted(providers)
                or len(providers) != len(set(providers))
                or any(
                    not isinstance(provider, str)
                    or _CLASS_NAME_RE.fullmatch(provider) is None
                    for provider in providers
                )
            ):
                raise ArtifactScanError(f"{context} exact service entry is invalid")
        elif (
            not isinstance(value.get("error"), str)
            or not value["error"]
            or providers != []
        ):
            raise ArtifactScanError(f"{context} invalid service entry is inconsistent")
    else:
        raise ArtifactScanError(f"{context} service parse_state is invalid")
    material = {key: item for key, item in value.items() if key != "service_entry_id"}
    if value.get("service_entry_id") != _content_id(_SERVICE_ID_PREFIX, material):
        raise ArtifactScanError(f"{context} service_entry_id is invalid")


def _validate_processor(value: Any, policy: Mapping[str, Any], index: int) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "artifact",
        "processor_id",
        "service_entry",
    }:
        raise ArtifactScanError(f"processor {index} has an unsupported V1 shape")
    _validate_file_record(value["artifact"], f"processor {index} artifact")
    _validate_service_entry(value["service_entry"], policy, f"processor {index}")
    material = {key: item for key, item in value.items() if key != "processor_id"}
    if value.get("processor_id") != _content_id(_PROCESSOR_ID_PREFIX, material):
        raise ArtifactScanError(f"processor {index} processor_id is invalid")


def _validate_classified(value: Any, *, output: bool, index: int) -> None:
    noun = "output" if output else "input"
    id_field = f"{noun}_id"
    kind_field = f"{noun}_kind"
    if not isinstance(value, Mapping) or set(value) != {id_field, kind_field, "file"}:
        raise ArtifactScanError(f"{noun} {index} has an unsupported V1 shape")
    _kind(value[kind_field], f"{noun} {index} kind")
    _validate_file_record(value["file"], f"{noun} {index}")
    material = {key: item for key, item in value.items() if key != id_field}
    prefix = _OUTPUT_ID_PREFIX if output else _INPUT_ID_PREFIX
    if value.get(id_field) != _content_id(prefix, material):
        raise ArtifactScanError(f"{noun} {index} {id_field} is invalid")


def _validate_issue(value: Any, index: int) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "expected",
        "issue_id",
        "issue_kind",
        "locator",
        "observed",
    }:
        raise ArtifactScanError(f"issue {index} has an unsupported V1 shape")
    if value.get("issue_kind") not in _ISSUE_KINDS:
        raise ArtifactScanError(f"issue {index} has an invalid issue_kind")
    _plain_string(value.get("locator"), f"issue {index} locator")
    try:
        canonical_json_bytes(value.get("expected"))
        canonical_json_bytes(value.get("observed"))
    except (TypeError, ValueError) as exc:
        raise ArtifactScanError(f"issue {index} contains non-canonical evidence") from exc
    material = {key: item for key, item in value.items() if key != "issue_id"}
    if value.get("issue_id") != _content_id(_ISSUE_ID_PREFIX, material):
        raise ArtifactScanError(f"issue {index} issue_id is invalid")


def validate_mixin_compiler_ap_build_receipt(receipt: Mapping[str, Any]) -> None:
    """Validate V1 shape, identities, and every policy-derived decision."""

    top_level = {
        "boundaries",
        "canonicalization_id",
        "diagnostics",
        "format",
        "inputs",
        "invocation",
        "issues",
        "limitations",
        "outputs",
        "policy",
        "processors",
        "receipt_id",
        "schema_version",
        "sources",
        "summary",
    }
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != top_level
        or receipt.get("format") != MIXIN_BUILD_FORMAT
        or receipt.get("schema_version") != 1
        or receipt.get("canonicalization_id") != CANONICALIZATION_ID
        or receipt.get("boundaries") != _BOUNDARIES
        or receipt.get("limitations") != _LIMITATIONS
    ):
        raise ArtifactScanError("Mixin compiler/AP build receipt has an unsupported V1 envelope")

    policy_binding = receipt.get("policy")
    if not isinstance(policy_binding, Mapping) or set(policy_binding) != _POLICY_KEYS | {"sha256"}:
        raise ArtifactScanError("Mixin compiler/AP build policy binding is malformed")
    if (
        not isinstance(policy_binding.get("sha256"), str)
        or _SHA256_RE.fullmatch(policy_binding["sha256"]) is None
    ):
        raise ArtifactScanError("Mixin compiler/AP build policy hash is invalid")
    _validate_policy_object(
        {key: value for key, value in policy_binding.items() if key != "sha256"}
    )

    invocation = receipt.get("invocation")
    invocation_keys = {
        "compiler_executable",
        "compiler_identity_capture",
        "invocation_id",
        "invocation_label",
        "options",
        "runtime_artifacts",
        "runtime_identity_capture",
    }
    if not isinstance(invocation, Mapping) or set(invocation) != invocation_keys:
        raise ArtifactScanError("Mixin compiler/AP invocation has an unsupported V1 shape")
    _plain_string(invocation.get("invocation_label"), "invocation_label")
    _validate_file_record(invocation["compiler_executable"], "compiler executable")
    _validate_file_record(invocation["compiler_identity_capture"], "compiler identity capture")
    _validate_file_record(invocation["runtime_identity_capture"], "runtime identity capture")
    runtime_artifacts = invocation.get("runtime_artifacts")
    if not isinstance(runtime_artifacts, list):
        raise ArtifactScanError("runtime_artifacts must be an array")
    for index, row in enumerate(runtime_artifacts):
        _validate_file_record(row, f"runtime artifact {index}")
    _require_unique_file_slots(runtime_artifacts, "runtime artifacts")
    _validate_options(invocation.get("options"))
    invocation_material = {
        key: value for key, value in invocation.items() if key != "invocation_id"
    }
    if invocation.get("invocation_id") != _content_id(_INVOCATION_ID_PREFIX, invocation_material):
        raise ArtifactScanError("Mixin compiler/AP invocation_id is invalid")

    processors = receipt.get("processors")
    sources = receipt.get("sources")
    inputs = receipt.get("inputs")
    outputs = receipt.get("outputs")
    issues = receipt.get("issues")
    if not all(isinstance(value, list) for value in (processors, sources, inputs, outputs, issues)):
        raise ArtifactScanError("Mixin compiler/AP receipt collections must be arrays")
    for index, row in enumerate(processors):
        _validate_processor(row, policy_binding, index)
    _require_unique_file_slots([row["artifact"] for row in processors], "processor artifacts")
    _validate_file_record(receipt.get("diagnostics"), "diagnostics capture")
    for index, row in enumerate(sources):
        _validate_file_record(row, f"source {index}")
    _require_unique_file_slots(sources, "sources")
    for index, row in enumerate(inputs):
        _validate_classified(row, output=False, index=index)
    for index, row in enumerate(outputs):
        _validate_classified(row, output=True, index=index)
    _require_unique_file_slots([row["file"] for row in inputs], "inputs")
    _require_unique_file_slots([row["file"] for row in outputs], "outputs")
    for index, row in enumerate(issues):
        _validate_issue(row, index)

    derived_issues = _derive_issues(receipt)
    if issues != derived_issues:
        raise ArtifactScanError("Mixin compiler/AP build issues are not reproducible")
    derived_summary = _summary(receipt, issues)
    if receipt.get("summary") != derived_summary:
        raise ArtifactScanError("Mixin compiler/AP build summary is not reproducible")

    material = {key: value for key, value in receipt.items() if key != "receipt_id"}
    if receipt.get("receipt_id") != _content_id(_RECEIPT_ID_PREFIX, material):
        raise ArtifactScanError("Mixin compiler/AP build receipt_id is invalid")


def validate_bound_mixin_compiler_ap_build_receipt(
    receipt: Mapping[str, Any],
    invocation: MixinCompilerAPInvocationInput,
    policy_bytes: bytes,
) -> None:
    """Rebuild from exact caller inputs and require byte-for-byte receipt facts."""

    validate_mixin_compiler_ap_build_receipt(receipt)
    expected = build_mixin_compiler_ap_build_receipt(invocation, policy_bytes)
    if receipt != expected:
        raise ArtifactScanError(
            "Mixin compiler/AP build receipt does not match the bound exact inputs"
        )


def render_mixin_compiler_ap_build_receipt(
    receipt: Mapping[str, Any], *, compact: bool = False
) -> bytes:
    validate_mixin_compiler_ap_build_receipt(receipt)
    if compact:
        return canonical_json_bytes(receipt) + b"\n"
    return (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
