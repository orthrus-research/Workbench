"""Apply the exact Cleanroom profile policy to static Mixin archive facts.

The evaluator is deliberately explicit about coverage.  A successful archive
scan proves packaging facts only; rules requiring target annotations,
instruction-level overlap, relocation provenance, system properties, or
runtime service state remain unevaluated and force a review disposition.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from workbench_project_intelligence import (
    ArtifactInput,
    ArtifactScanError,
    build_topology_receipt,
    canonical_json_bytes,
    scan_artifact_bytes,
    validate_topology_receipt,
)
from workbench_project_intelligence.mixin_receipt import build_contract_receipt


REPORT_FORMAT = "workbench-cleanroom-mixin-doctor-report-v1"
REPORT_ID_PREFIX = "workbench-cleanroom-mixin-doctor-report:sha256:"
FINDING_ID_PREFIX = "workbench-cleanroom-mixin-doctor-finding:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_REPORT_STATES = {"accept", "review", "reject"}
_COVERAGE_STATES = {"evaluated", "partially-evaluated", "not-evaluated"}
_KNOWN_POLICY_FORMAT = "workbench-cleanroom-mixin-doctor-policy-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MIXIN_VERSION_RE = re.compile(
    r"^([0-9]{1,5})(?:\.([0-9]{1,5})(?:\.([0-9]{1,5})(?:\.([0-9]{1,5}))?)?)?"
    r"(-[a-zA-Z0-9_\-]+)?$"
)
_MIXIN_VERSION_PART_MAXIMUM = 32767
_COMPATIBILITY_LEVELS = {
    f"JAVA_{major}": major for major in range(6, 26)
}
_REQUIRED_FEATURE_STATES = {
    "INJECTORS_IN_INTERFACE_MIXINS": "runtime-compatibility-dependent",
    "UNSAFE_INJECTION": "statically-active",
}
_ENV_SELECTOR_RE = re.compile(r"^@env(?:ironment)?\(([A-Z]+)\)$")
_MULTI_RELEASE_MEMBER_RE = re.compile(
    r"^META-INF/versions/[1-9][0-9]*/(?P<logical>.+)$"
)

_STATIC_ADMISSION_CONDITIONS = {
    "config_is_not_valid_json_object",
    "declared_refmap_is_not_valid_json_object",
    "production_route_names_missing_config_resource",
}

_UNAVAILABLE_CONDITIONS = {
    "mixin_targets_forbidden_native_namespace": (
        "Mixin target annotations are outside the bounded static scanner policy."
    ),
    "no_refmap_and_minecraft_or_forge_targets_present": (
        "Target ownership is not established by the bounded static scanner."
    ),
    "only_route_is_system_property": (
        "A system-property route is process state and is absent from artifact bytes."
    ),
    "remaining_overlap_present": (
        "Target-member overlap requires annotation and target resolution."
    ),
    "same_target_instruction_has_multiple_exclusive_injections": (
        "Instruction overlap requires decoded injection points and target bytecode."
    ),
    "same_target_member_has_multiple_overwrites": (
        "Overwrite overlap requires decoded target-member annotations."
    ),
    "scanner_identifies_relocated_copy_of_forbidden_component": (
        "Relocated implementation identity requires byte or source provenance."
    ),
}


class DoctorError(RuntimeError):
    """Raised when policy evaluation cannot produce an unambiguous report."""


@dataclass(frozen=True)
class _RuleResult:
    state: str
    matches: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class _MixinVersion:
    parts: tuple[int, int, int, int] | None
    suffix: str | None
    invalid_reason: str | None
    runtime_equivalent: str


@dataclass(frozen=True)
class _TargetSelection:
    effective_phase: str | None
    first_environment_selector: str | None
    first_environment_payload: str | None
    resolution: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + _sha256(canonical_json_bytes(value))


def render_report(report: Mapping[str, Any], *, compact: bool = False) -> bytes:
    """Render a report deterministically with one trailing newline."""

    if compact:
        return canonical_json_bytes(report) + b"\n"
    return (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DoctorError(f"policy repeats JSON object key {key!r}")
        result[key] = value
    return result


def _validate_policy_mapping(policy: Mapping[str, Any]) -> None:
    if policy.get("format") != _KNOWN_POLICY_FORMAT:
        raise DoctorError("unsupported Cleanroom Mixin Doctor policy format")
    if policy.get("schema_version") != 1:
        raise DoctorError("unsupported Cleanroom Mixin Doctor policy schema version")
    if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"]:
        raise DoctorError("policy_id must be a non-empty string")
    rules = policy.get("doctor_rules")
    if not isinstance(rules, Mapping) or not rules:
        raise DoctorError("policy doctor_rules must be a non-empty object")
    seen: set[str] = set()
    for fact_name, group in sorted(rules.items()):
        if not isinstance(fact_name, str) or not isinstance(group, Mapping):
            raise DoctorError("policy doctor rule group is malformed")
        group_rules = group.get("rules")
        if not isinstance(group_rules, list):
            raise DoctorError(f"policy rule group {fact_name} has no rules array")
        for rule in group_rules:
            if not isinstance(rule, Mapping):
                raise DoctorError(
                    f"policy rule group {fact_name} contains a non-object"
                )
            match = rule.get("match")
            result = rule.get("result")
            if not isinstance(match, Mapping) or not isinstance(result, Mapping):
                raise DoctorError(f"policy rule group {fact_name} has an invalid rule")
            rule_id = rule.get("finding_id")
            condition = match.get("condition")
            rationale = rule.get("rationale")
            if (
                not isinstance(rule_id, str)
                or not isinstance(condition, str)
                or not isinstance(rationale, str)
                or not rationale
            ):
                raise DoctorError(f"policy rule group {fact_name} has an invalid rule")
            if rule_id in seen:
                raise DoctorError(f"policy repeats rule ID {rule_id}")
            if result.get("disposition") not in _REPORT_STATES:
                raise DoctorError(f"policy rule {rule_id} has an invalid disposition")
            if result.get("severity") not in {"info", "warning", "error"}:
                raise DoctorError(f"policy rule {rule_id} has an invalid severity")
            seen.add(rule_id)
    runtime_capabilities = policy.get("runtime_capabilities")
    if not isinstance(runtime_capabilities, Mapping):
        raise DoctorError("policy runtime_capabilities must be an object")
    if runtime_capabilities.get("required_feature_states") != _REQUIRED_FEATURE_STATES:
        raise DoctorError(
            "policy required feature states do not match the pinned CleanMix enum"
        )


def _load_policy(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise DoctorError(f"policy is not a regular non-symlink file: {path}")
    try:
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DoctorError(f"cannot read policy {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DoctorError("policy root must be an object")
    _validate_policy_mapping(value)
    return value, _sha256(raw)


def _mixin_version(value: str) -> _MixinVersion:
    """Mirror Sponge ``VersionNumber.parse`` and its overflow branch."""

    match = _MIXIN_VERSION_RE.fullmatch(value)
    if match is None:
        return _MixinVersion(
            parts=None,
            suffix=None,
            invalid_reason=(
                "value does not match the runtime VersionNumber grammar"
            ),
            runtime_equivalent="VersionNumber.NONE",
        )
    parts = tuple(
        int(part) if part is not None else 0 for part in match.groups()[:4]
    )
    if any(part > _MIXIN_VERSION_PART_MAXIMUM for part in parts):
        return _MixinVersion(
            parts=None,
            suffix=match.group(5),
            invalid_reason=(
                "a numeric part exceeds the runtime short maximum 32767"
            ),
            runtime_equivalent="IllegalArgumentException",
        )
    return _MixinVersion(
        parts=parts,  # type: ignore[arg-type]
        suffix=match.group(5),
        invalid_reason=None,
        runtime_equivalent="VersionNumber",
    )


def _java_trim(value: str) -> str:
    """Mirror ``String.trim`` for the ASCII declarations used by Mixin."""

    start = 0
    end = len(value)
    while start < end and ord(value[start]) <= 0x20:
        start += 1
    while end > start and ord(value[end - 1]) <= 0x20:
        end -= 1
    return value[start:end]


def _compatibility_level(value: str) -> tuple[str, int | None]:
    normalized = _java_trim(value).upper()
    return normalized, _COMPATIBILITY_LEVELS.get(normalized)


def _required_feature_id(value: str) -> str:
    """Mirror ``String.trim().toUpperCase(Locale.ROOT)`` for feature IDs."""

    return _java_trim(value).upper()


def _target_selection(value: str, known_phases: set[str]) -> _TargetSelection:
    """Mirror CleanMix ``MixinConfig.parseSelector`` selection semantics."""

    for token in re.split(r"[&| ]", value):
        token = _java_trim(token)
        selector = _ENV_SELECTOR_RE.fullmatch(token)
        if selector is None:
            continue
        payload = selector.group(1)
        if payload in known_phases:
            return _TargetSelection(
                effective_phase=payload,
                first_environment_selector=token,
                first_environment_payload=payload,
                resolution="first-environment-selector",
            )
        return _TargetSelection(
            effective_phase="DEFAULT",
            first_environment_selector=token,
            first_environment_payload=payload,
            resolution="default-from-null-phase",
        )
    if value in known_phases:
        return _TargetSelection(
            effective_phase=value,
            first_environment_selector=None,
            first_environment_payload=None,
            resolution="raw-phase",
        )
    return _TargetSelection(
        effective_phase=None,
        first_environment_selector=None,
        first_environment_payload=None,
        resolution="fallback-environment",
    )


def _cleanmix_version(value: str) -> tuple[int, int, int] | None:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _artifact_match(
    scan: Mapping[str, Any], locator: str, observed: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "artifact_label": scan["label"],
        "artifact_sha256": scan["identity"]["sha256"],
        "locator": locator,
        "observed": dict(observed),
    }


def _manifest(scan: Mapping[str, Any]) -> dict[str, str]:
    return {
        row["name"].casefold(): row["value"]
        for row in scan["manifest"]["main_attributes"]
    }


def _production_route_kinds(scan: Mapping[str, Any]) -> set[str]:
    return {
        row["kind"]
        for row in scan["registration_routes"]
        if row["kind"]
        in {
            "manifest-mixin-configs",
            "manifest-mixin-connector",
            "mixinbooter-early-loader",
            "mixinbooter-late-loader",
        }
    }


def _owner_collisions(scans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    owners: dict[str, list[dict[str, Any]]] = {}
    for scan in scans:
        for owner in scan["owner_id_inputs"]:
            owners.setdefault(owner["normalized_owner_id"], []).append(
                _artifact_match(
                    scan,
                    owner["source"],
                    {
                        "normalized_owner_id": owner["normalized_owner_id"],
                        "raw_owner_id": owner["raw_owner_id"],
                    },
                )
            )
    matches: list[dict[str, Any]] = []
    for normalized, rows in sorted(owners.items()):
        if len({row["artifact_sha256"] for row in rows}) < 2:
            continue
        matches.extend(
            {
                **row,
                "observed": {
                    **row["observed"],
                    "colliding_artifact_sha256": sorted(
                        {item["artifact_sha256"] for item in rows}
                    ),
                },
            }
            for row in rows
        )
    return matches


def _evaluate_condition(
    condition: str,
    match_spec: Mapping[str, Any],
    policy: Mapping[str, Any],
    scans: Sequence[Mapping[str, Any]],
) -> _RuleResult:
    if condition in _UNAVAILABLE_CONDITIONS:
        return _RuleResult(
            state="not-evaluated",
            limitations=(_UNAVAILABLE_CONDITIONS[condition],),
        )
    if condition in _STATIC_ADMISSION_CONDITIONS:
        return _RuleResult(
            state="evaluated",
            limitations=(
                "Successful scanner admission proves this malformed state was absent; "
                "a malformed archive fails before report publication.",
            ),
        )

    matches: list[dict[str, Any]] = []
    limitations: list[str] = []
    state = "evaluated"

    if condition == "metadata_resource_absent":
        for scan in scans:
            metadata = scan["compatibility_metadata"]
            if not metadata["present"]:
                matches.append(
                    _artifact_match(
                        scan,
                        metadata["path"],
                        {"present": False, "runtime_fallback": "0.1.0"},
                    )
                )
    elif condition == "entry_version_greater_than":
        boundary = _cleanmix_version(str(match_spec["version"]))
        if boundary is None:
            raise DoctorError("policy has an invalid version boundary")
        for scan in scans:
            for key, value in scan["compatibility_metadata"]["entries"].items():
                parsed = _cleanmix_version(value)
                if parsed is not None and parsed > boundary:
                    matches.append(
                        _artifact_match(
                            scan,
                            f"cleanmix_version_compatibility.json:{key}",
                            {"key": key, "version": value},
                        )
                    )
    elif condition == "entry_version_does_not_match":
        for scan in scans:
            for entry in scan["compatibility_metadata"]["invalid_entries"]:
                matches.append(
                    _artifact_match(
                        scan,
                        f"cleanmix_version_compatibility.json:{entry['key']}",
                        {
                            "key": entry["key"],
                            "reason": entry["reason"],
                            "value": entry["value"],
                        },
                    )
                )
    elif condition == "entry_key_has_no_scanned_mixin_or_member":
        for scan in scans:
            mixins = {
                mixin["class_name"]
                for config in scan["mixin_configs"]
                for mixin in config["mixins"]
            }
            for key, value in scan["compatibility_metadata"]["entries"].items():
                class_key, separator, _ = key.partition("::")
                if class_key not in mixins:
                    matches.append(
                        _artifact_match(
                            scan,
                            f"cleanmix_version_compatibility.json:{key}",
                            {"key": key, "version": value},
                        )
                    )
                elif separator:
                    state = "partially-evaluated"
                    limitations.append(
                        "The compatibility key names a member of a scanned Mixin "
                        "class, but V1 does not retain method/field tables needed "
                        "to prove that member exists."
                    )
    elif condition == "any_original_namespace_prefix_matches":
        prefixes = policy["forbidden_embedded_content"]["package_prefixes"]
        for scan in scans:
            paths = [row["path"] for row in scan["member_inventory"]]
            for prefix_row in prefixes:
                prefix = prefix_row["path_prefix"]
                selected = []
                for path in paths:
                    multi_release = _MULTI_RELEASE_MEMBER_RE.fullmatch(path)
                    logical = (
                        multi_release.group("logical")
                        if multi_release is not None
                        else path
                    )
                    if logical.startswith(prefix):
                        selected.append(path)
                if selected:
                    matches.append(
                        _artifact_match(
                            scan,
                            prefix,
                            {
                                "class_or_resource_count": len(selected),
                                "component": prefix_row["component"],
                                "sample_paths": selected[:10],
                            },
                        )
                    )
    elif condition == "config_resource_has_no_production_registration_route":
        for scan in scans:
            dynamic = _production_route_kinds(scan) & {
                "manifest-mixin-connector",
                "mixinbooter-early-loader",
                "mixinbooter-late-loader",
            }
            for config in scan["mixin_configs"]:
                if config["registered_via"]:
                    continue
                if dynamic:
                    state = "partially-evaluated"
                    limitations.append(
                        "A dynamic registration route is present; static bytes do not prove "
                        f"whether it registers {config['path']}."
                    )
                    continue
                matches.append(
                    _artifact_match(
                        scan,
                        config["path"],
                        {"registered_via": []},
                    )
                )
    elif condition == "min_version_is_invalid":
        for scan in scans:
            for config in scan["mixin_configs"]:
                value = config["min_version"]
                if value is None:
                    continue
                parsed = _mixin_version(value)
                if parsed.parts is None:
                    matches.append(
                        _artifact_match(
                            scan,
                            config["path"],
                            {
                                "invalid_reason": parsed.invalid_reason,
                                "min_version": value,
                                "runtime_equivalent": parsed.runtime_equivalent,
                            },
                        )
                    )
    elif condition == "min_version_greater_than":
        boundary_value = policy["runtime_capabilities"]["upstream_mixin_version"]
        boundary = _mixin_version(boundary_value)
        if boundary.parts is None:
            raise DoctorError("policy has an invalid upstream Mixin version")
        for scan in scans:
            for config in scan["mixin_configs"]:
                value = config["min_version"]
                if value is None:
                    continue
                parsed = _mixin_version(value)
                if parsed.parts is not None and parsed.parts > boundary.parts:
                    matches.append(
                        _artifact_match(
                            scan,
                            config["path"],
                            {
                                "min_version": value,
                                "runtime_version": boundary_value,
                            },
                        )
                    )
    elif condition == "required_feature_is_unknown_or_candidate_proven_unavailable":
        feature_states = policy["runtime_capabilities"]["required_feature_states"]
        if feature_states != _REQUIRED_FEATURE_STATES:
            raise DoctorError(
                "policy required feature states do not match the pinned CleanMix enum"
            )
        for scan in scans:
            for config in scan["mixin_configs"]:
                for value in config["required_features"]:
                    feature_id = _required_feature_id(value)
                    feature_state = feature_states.get(feature_id)
                    if feature_state is None or feature_state == "candidate-proven-unavailable":
                        matches.append(
                            _artifact_match(
                                scan,
                                f"{config['path']}:requiredFeatures:{value}",
                                {
                                    "declared_feature": value,
                                    "feature_state": (
                                        "unknown-feature"
                                        if feature_state is None
                                        else feature_state
                                    ),
                                    "normalized_feature_id": feature_id,
                                    "runtime_equivalent": "Feature.isActive=false",
                                },
                            )
                        )
    elif condition == "required_feature_activation_depends_on_runtime_state":
        feature_states = policy["runtime_capabilities"]["required_feature_states"]
        for scan in scans:
            for config in scan["mixin_configs"]:
                for value in config["required_features"]:
                    feature_id = _required_feature_id(value)
                    if feature_states.get(feature_id) != "runtime-compatibility-dependent":
                        continue
                    state = "partially-evaluated"
                    limitations.append(
                        "INJECTORS_IN_INTERFACE_MIXINS activation depends on the "
                        "process-wide compatibility level and the effective JRE/ASM "
                        "support observed when the configuration is initialised."
                    )
                    matches.append(
                        _artifact_match(
                            scan,
                            f"{config['path']}:requiredFeatures:{value}",
                            {
                                "declared_feature": value,
                                "feature_state": "runtime-compatibility-dependent",
                                "normalized_feature_id": feature_id,
                                "runtime_resolution": (
                                    "requires-compatibility-jre-asm-observation"
                                ),
                            },
                        )
                    )
    elif condition == "compatibility_level_is_not_enum_name":
        allowed_names = set(policy["runtime_capabilities"]["compatibility_level_names"])
        if allowed_names != set(_COMPATIBILITY_LEVELS):
            raise DoctorError(
                "policy compatibility level names do not match the pinned runtime enum"
            )
        for scan in scans:
            for config in scan["mixin_configs"]:
                value = config["compatibility_level"]
                if value is None:
                    continue
                normalized, major = _compatibility_level(value)
                if major is None:
                    matches.append(
                        _artifact_match(
                            scan,
                            config["path"],
                            {
                                "compatibility_level": value,
                                "normalized_enum_name": normalized,
                                "runtime_equivalent": "MixinInitialisationError",
                            },
                        )
                    )
    elif condition == "compatibility_level_above_mixin_max_supported":
        maximum = int(
            policy["runtime_capabilities"]["maximum_mixin_supported_compatibility"]
        )
        for scan in scans:
            for config in scan["mixin_configs"]:
                value = config["compatibility_level"]
                if value is None:
                    continue
                normalized, major = _compatibility_level(value)
                if major is not None and major > maximum:
                    matches.append(
                        _artifact_match(
                            scan,
                            config["path"],
                            {
                                "compatibility_level": value,
                                "maximum_mixin_supported_java": maximum,
                                "normalized_enum_name": normalized,
                                "runtime_support": "requires-jre-and-asm-observation",
                            },
                        )
                    )
    elif condition == "first_environment_selector_payload_equals":
        expected = str(match_spec["value"])
        known = set(policy["runtime_capabilities"]["real_phases"])
        for scan in scans:
            for config in scan["mixin_configs"]:
                value = config["target_phase"]
                if value is None:
                    continue
                selection = _target_selection(value, known)
                if selection.first_environment_payload == expected:
                    matches.append(
                        _artifact_match(
                            scan,
                            config["path"],
                            {
                                "effective_phase": selection.effective_phase,
                                "first_environment_selector": (
                                    selection.first_environment_selector
                                ),
                                "target": value,
                            },
                        )
                    )
    elif condition == "selector_resolves_unknown_or_fallback":
        known = set(policy["runtime_capabilities"]["real_phases"])
        exception = str(match_spec.get("allowlisted_environment_payload", ""))
        for scan in scans:
            for config in scan["mixin_configs"]:
                value = config["target_phase"]
                if value is None:
                    continue
                selection = _target_selection(value, known)
                if selection.first_environment_payload == exception:
                    continue
                if (
                    selection.resolution == "fallback-environment"
                    or selection.resolution == "default-from-null-phase"
                ):
                    matches.append(
                        _artifact_match(
                            scan,
                            config["path"],
                            {
                                "effective_phase": selection.effective_phase,
                                "first_environment_payload": (
                                    selection.first_environment_payload
                                ),
                                "first_environment_selector": (
                                    selection.first_environment_selector
                                ),
                                "runtime_resolution": selection.resolution,
                                "target": value,
                            },
                        )
                    )
    elif condition == "listed_mixin_class_missing":
        for scan in scans:
            for config in scan["mixin_configs"]:
                for mixin in config["mixins"]:
                    if not mixin["present"]:
                        matches.append(
                            _artifact_match(
                                scan,
                                f"{config['path']}:{mixin['declared_name']}",
                                {"class_member": mixin["class_member"], "present": False},
                            )
                        )
    elif condition == "mixin_class_listed_more_than_once_across_common_client_server":
        for scan in scans:
            for config in scan["mixin_configs"]:
                grouped: dict[str, list[str]] = {}
                for mixin in config["mixins"]:
                    grouped.setdefault(mixin["class_name"], []).append(mixin["environment"])
                for class_name, environments in sorted(grouped.items()):
                    if len(environments) > 1:
                        matches.append(
                            _artifact_match(
                                scan,
                                f"{config['path']}:{class_name}",
                                {"class_name": class_name, "environments": sorted(environments)},
                            )
                        )
    elif condition == "listed_mixin_class_not_beneath_config_package":
        for scan in scans:
            for config in scan["mixin_configs"]:
                package = config["package"]
                if package is None:
                    for mixin in config["mixins"]:
                        matches.append(
                            _artifact_match(
                                scan,
                                f"{config['path']}:{mixin['declared_name']}",
                                {
                                    "class_name": mixin["class_name"],
                                    "package": None,
                                    "runtime_resolution": "orphaned",
                                },
                            )
                        )
                    continue
                package_prefix = package if package.endswith(".") else package + "."
                for mixin in config["mixins"]:
                    if not mixin["class_name"].startswith(package_prefix):
                        matches.append(
                            _artifact_match(
                                scan,
                                f"{config['path']}:{mixin['declared_name']}",
                                {"class_name": mixin["class_name"], "package": package},
                            )
                        )
    elif condition == "collision_present":
        matches.extend(_owner_collisions(scans))
    elif condition == "declared_plugin_class_missing":
        for scan in scans:
            for config in scan["mixin_configs"]:
                plugin = config["plugin"]
                if plugin is not None and not plugin["present"]:
                    state = "partially-evaluated"
                    limitations.append(
                        "The declared plugin is absent from its owning archive, but V1 "
                        "has no explicit dependency roles or class-resolution edges."
                    )
                    matches.append(
                        _artifact_match(
                            scan,
                            f"{config['path']}:plugin",
                            {
                                "class_name": plugin["class_name"],
                                "dependency_closure": "not-observed",
                                "same_archive_present": False,
                            },
                        )
                    )
    elif condition == "declared_plugin_does_not_implement":
        expected = str(match_spec["interface"]).replace(".", "/")
        for scan in scans:
            for config in scan["mixin_configs"]:
                plugin = config["plugin"]
                if plugin is None or not plugin["present"]:
                    continue
                interfaces = plugin.get("interfaces")
                if interfaces is None:
                    state = "partially-evaluated"
                    limitations.append(
                        "The scanner did not retain the declared plugin interface table."
                    )
                elif expected not in interfaces:
                    state = "partially-evaluated"
                    limitations.append(
                        "The plugin does not directly declare IMixinConfigPlugin, but "
                        "the bounded class-header scan does not resolve superclass "
                        "inheritance and therefore cannot prove the interface absent."
                    )
                    matches.append(
                        _artifact_match(
                            scan,
                            f"{config['path']}:plugin",
                            {
                                "class_name": plugin["class_name"],
                                "directly_declared_interfaces": interfaces,
                                "expected_interface": expected,
                                "resolution": (
                                    "superclass-and-dependency-closure-required"
                                ),
                            },
                        )
                    )
    elif condition == "config_uses_mod_selector_and_declares_no_plugin":
        known = set(policy["runtime_capabilities"]["real_phases"])
        for scan in scans:
            for config in scan["mixin_configs"]:
                target = config["target_phase"]
                if (
                    target is not None
                    and _target_selection(
                        target, known
                    ).first_environment_payload == "MOD"
                    and config["plugin"] is None
                ):
                    matches.append(
                        _artifact_match(scan, config["path"], {"target": target, "plugin": None})
                    )
    elif condition == "plugin_declared":
        for scan in scans:
            for config in scan["mixin_configs"]:
                plugin = config["plugin"]
                if plugin is not None:
                    matches.append(
                        _artifact_match(
                            scan,
                            f"{config['path']}:plugin",
                            {"class_name": plugin["class_name"]},
                        )
                    )
    elif condition == "declared_refmap_resource_missing":
        for scan in scans:
            for refmap in scan["refmaps"]:
                if not refmap["present"]:
                    matches.append(
                        _artifact_match(
                            scan,
                            refmap["path"],
                            {"declared_by": refmap["declared_by"], "present": False},
                        )
                    )
    elif condition == "consumer_declares_forbidden_service_descriptor":
        forbidden = set(policy["forbidden_embedded_content"]["service_descriptors"])
        for scan in scans:
            paths = {row["path"] for row in scan["member_inventory"]}
            for path in sorted(paths & forbidden):
                matches.append(_artifact_match(scan, path, {"descriptor": path}))
    elif condition == "manifest_entry_equals":
        name = str(match_spec["name"])
        expected = str(match_spec["value"])
        for scan in scans:
            actual = _manifest(scan).get(name.casefold())
            values = [] if actual is None else [value.strip() for value in actual.split(",")]
            if expected in values:
                matches.append(
                    _artifact_match(
                        scan,
                        f"META-INF/MANIFEST.MF:{name}",
                        {"name": name, "value": expected},
                    )
                )
    elif condition == "route_uses_any_class_or_annotation":
        expected = set(match_spec["values"])
        for scan in scans:
            for route in scan["registration_routes"]:
                selected = sorted(expected & set(route["values"]))
                interface_route = route["kind"] in {
                    "mixinbooter-early-loader",
                    "mixinbooter-late-loader",
                }
                if selected or interface_route:
                    matches.append(
                        _artifact_match(
                            scan,
                            route["source"],
                            {"route_kind": route["kind"], "values": route["values"]},
                        )
                    )
    elif condition == "declared_connector_class_missing":
        for scan in scans:
            paths = {row["path"] for row in scan["member_inventory"]}
            for route in scan["registration_routes"]:
                if route["kind"] != "manifest-mixin-connector":
                    continue
                for class_name in route["values"]:
                    class_member = class_name.replace(".", "/") + ".class"
                    if class_member in paths:
                        continue
                    state = "partially-evaluated"
                    limitations.append(
                        "The declared connector is absent from its owning archive, but V1 "
                        "has no explicit dependency roles or class-resolution edges."
                    )
                    matches.append(
                        _artifact_match(
                            scan,
                            route["source"],
                            {
                                "class_member": class_member,
                                "class_name": class_name,
                                "dependency_closure": "not-observed",
                                "same_archive_present": False,
                            },
                        )
                    )
    elif condition == "declared_connector_interface_requires_dependency_closure":
        expected = str(match_spec["interface"]).replace(".", "/")
        for scan in scans:
            paths = {row["path"] for row in scan["member_inventory"]}
            for route in scan["registration_routes"]:
                if route["kind"] != "manifest-mixin-connector":
                    continue
                for class_name in route["values"]:
                    class_member = class_name.replace(".", "/") + ".class"
                    state = "partially-evaluated"
                    if class_member not in paths:
                        limitations.append(
                            "Connector interface resolution requires the missing "
                            "dependency closure."
                        )
                        continue
                    limitations.append(
                        "V1 retains the connector route and class member but not its "
                        "class-header interfaces, superclass chain, or dependency closure."
                    )
                    matches.append(
                        _artifact_match(
                            scan,
                            route["source"],
                            {
                                "class_member": class_member,
                                "class_name": class_name,
                                "expected_interface": expected,
                                "interface_resolution": "not-retained-by-v1-scanner",
                            },
                        )
                    )
    elif condition == "manifest_attribute_present":
        name = str(match_spec["name"])
        for scan in scans:
            value = _manifest(scan).get(name.casefold())
            if value is not None:
                matches.append(
                    _artifact_match(
                        scan,
                        f"META-INF/MANIFEST.MF:{name}",
                        {"name": name, "value": value},
                    )
                )
    elif condition == "no_production_registration_route":
        for scan in scans:
            if scan["mixin_configs"] and not _production_route_kinds(scan):
                matches.append(
                    _artifact_match(
                        scan,
                        "META-INF/MANIFEST.MF",
                        {"config_count": len(scan["mixin_configs"]), "production_routes": []},
                    )
                )
    else:
        raise DoctorError(f"policy condition is not implemented: {condition}")

    ordered = tuple(
        sorted(
            matches,
            key=lambda row: (
                row["artifact_sha256"],
                row["artifact_label"],
                row["locator"],
                canonical_json_bytes(row["observed"]),
            ),
        )
    )
    return _RuleResult(
        state=state,
        matches=ordered,
        limitations=tuple(sorted(set(limitations))),
    )


def _policy_rules(policy: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    result: list[tuple[str, Mapping[str, Any]]] = []
    for fact_name, group in policy["doctor_rules"].items():
        for rule in group["rules"]:
            result.append((fact_name, rule))
    return sorted(result, key=lambda item: item[1]["finding_id"])


def evaluate_scans(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    scans: Sequence[Mapping[str, Any]],
    topology_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate scanner facts after proving their exact topology binding.

    ``policy_sha256`` is the caller's SHA-256 of the exact policy file bytes.
    It cannot be reconstructed from the parsed mapping because whitespace and
    JSON member order are part of that file identity.
    """

    if not scans:
        raise DoctorError("at least one scanned artifact is required")
    _validate_policy_mapping(policy)
    try:
        validate_topology_receipt(topology_receipt)
        ordered_scans = tuple(
            sorted(
                scans,
                key=lambda row: (row["identity"]["sha256"], row["label"]),
            )
        )
        expected_receipt = build_contract_receipt(ordered_scans)
    except (ArtifactScanError, KeyError, TypeError) as exc:
        raise DoctorError(f"cannot bind supplied scans to topology receipt: {exc}") from exc
    if expected_receipt != topology_receipt:
        raise DoctorError(
            "supplied scans do not deterministically rebuild the bound topology receipt"
        )
    scans = ordered_scans
    findings: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []

    for fact_name, rule in _policy_rules(policy):
        rule_id = rule["finding_id"]
        condition = rule["match"]["condition"]
        result = _evaluate_condition(condition, rule["match"], policy, scans)
        if result.state not in _COVERAGE_STATES:
            raise DoctorError(f"rule {rule_id} produced an invalid coverage state")
        coverage.append(
            {
                "condition": condition,
                "limitations": list(result.limitations),
                "match_count": len(result.matches),
                "rule_id": rule_id,
                "scanner_fact": fact_name,
                "state": result.state,
            }
        )
        for match in result.matches:
            material = {
                "artifact_label": match["artifact_label"],
                "artifact_sha256": match["artifact_sha256"],
                "disposition": rule["result"]["disposition"],
                "locator": match["locator"],
                "observed": match["observed"],
                "rationale": rule["rationale"],
                "rule_id": rule_id,
                "scanner_fact": fact_name,
                "severity": rule["result"]["severity"],
            }
            row = dict(material)
            row["finding_id"] = _content_id(FINDING_ID_PREFIX, material)
            findings.append(row)

    findings.sort(key=lambda row: row["finding_id"])
    coverage.sort(key=lambda row: row["rule_id"])
    coverage_state = (
        "complete"
        if all(row["state"] == "evaluated" for row in coverage)
        else "partial"
    )
    matched_dispositions = {row["disposition"] for row in findings}
    if "reject" in matched_dispositions:
        disposition = "reject"
    elif "review" in matched_dispositions or coverage_state == "partial":
        disposition = "review"
    else:
        disposition = "accept"

    limitations = [
        "This report evaluates exact archive bytes and does not prove runtime service selection.",
        "Static registration does not prove configuration preparation or mixin application.",
        "Rules marked not-evaluated or partially-evaluated prevent an accept disposition.",
        "Final transformed bytes remain Foundation/Crucible runtime evidence custody.",
        "Recurrent Complex integration and presence are out of scope; this Doctor introduces no adapter.",
    ]
    material = {
        "boundaries": {
            "candidate_lock_v1_mutated": False,
            "recurrent_complex_integration_scope": (
                "out-of-scope-no-adapter-introduced"
            ),
            "runtime_application_proved": False,
            "static_scan_only": True,
        },
        "canonicalization_id": CANONICALIZATION_ID,
        "coverage": coverage,
        "findings": findings,
        "format": REPORT_FORMAT,
        "limitations": limitations,
        "policy": {
            "policy_id": policy["policy_id"],
            "sha256": policy_sha256,
        },
        "schema_version": 1,
        "summary": {
            "artifact_count": len(scans),
            "coverage_state": coverage_state,
            "disposition": disposition,
            "evaluated_rule_count": sum(row["state"] == "evaluated" for row in coverage),
            "finding_count": len(findings),
            "partial_rule_count": sum(
                row["state"] == "partially-evaluated" for row in coverage
            ),
            "reject_finding_count": sum(
                row["disposition"] == "reject" for row in findings
            ),
            "review_finding_count": sum(
                row["disposition"] == "review" for row in findings
            ),
            "rule_count": len(coverage),
            "unevaluated_rule_count": sum(
                row["state"] == "not-evaluated" for row in coverage
            ),
        },
        "topology_receipt": {
            "receipt_id": topology_receipt["receipt_id"],
            "scope_id": topology_receipt["scope"]["scope_id"],
        },
    }
    report = dict(material)
    report["report_id"] = _content_id(REPORT_ID_PREFIX, material)
    validate_bound_report(
        report,
        policy=policy,
        policy_sha256=policy_sha256,
        topology_receipt=topology_receipt,
    )
    return report


def validate_report(report: Mapping[str, Any]) -> None:
    """Recompute report/finding IDs, references, ordering, and summary counts."""

    if report.get("format") != REPORT_FORMAT or report.get("schema_version") != 1:
        raise DoctorError("Doctor report has the wrong format or schema version")
    material = {key: value for key, value in report.items() if key != "report_id"}
    if report.get("report_id") != _content_id(REPORT_ID_PREFIX, material):
        raise DoctorError("Doctor report content ID does not match")
    coverage = report.get("coverage")
    findings = report.get("findings")
    if not isinstance(coverage, list) or not isinstance(findings, list):
        raise DoctorError("Doctor report coverage/findings must be arrays")
    rule_ids = [row.get("rule_id") for row in coverage]
    if rule_ids != sorted(rule_ids) or len(rule_ids) != len(set(rule_ids)):
        raise DoctorError("Doctor report coverage is not uniquely rule-sorted")
    finding_ids = [row.get("finding_id") for row in findings]
    if finding_ids != sorted(finding_ids) or len(finding_ids) != len(set(finding_ids)):
        raise DoctorError("Doctor report findings are not uniquely ID-sorted")
    for row in findings:
        finding_material = {
            key: value for key, value in row.items() if key != "finding_id"
        }
        if row.get("finding_id") != _content_id(FINDING_ID_PREFIX, finding_material):
            raise DoctorError("Doctor finding content ID does not match")
        if row.get("rule_id") not in set(rule_ids):
            raise DoctorError("Doctor finding references an unknown rule")
    summary = report.get("summary", {})
    findings_by_rule: dict[str, int] = {}
    for row in findings:
        rule_id = row.get("rule_id")
        findings_by_rule[rule_id] = findings_by_rule.get(rule_id, 0) + 1
    for row in coverage:
        if row.get("state") not in _COVERAGE_STATES:
            raise DoctorError("Doctor report coverage has an invalid state")
        if row.get("match_count") != findings_by_rule.get(row.get("rule_id"), 0):
            raise DoctorError(
                f"Doctor report coverage match_count is wrong for {row.get('rule_id')}"
            )
    expected = {
        "rule_count": len(coverage),
        "evaluated_rule_count": sum(row.get("state") == "evaluated" for row in coverage),
        "partial_rule_count": sum(
            row.get("state") == "partially-evaluated" for row in coverage
        ),
        "unevaluated_rule_count": sum(
            row.get("state") == "not-evaluated" for row in coverage
        ),
        "finding_count": len(findings),
        "reject_finding_count": sum(
            row.get("disposition") == "reject" for row in findings
        ),
        "review_finding_count": sum(
            row.get("disposition") == "review" for row in findings
        ),
    }
    for field, value in expected.items():
        if summary.get(field) != value:
            raise DoctorError(f"Doctor report summary {field} is wrong")
    if summary.get("disposition") not in _REPORT_STATES:
        raise DoctorError("Doctor report has an invalid disposition")
    if summary.get("coverage_state") not in {"complete", "partial"}:
        raise DoctorError("Doctor report has an invalid coverage state")
    expected_coverage_state = (
        "complete"
        if all(row.get("state") == "evaluated" for row in coverage)
        else "partial"
    )
    if summary.get("coverage_state") != expected_coverage_state:
        raise DoctorError("Doctor report coverage_state is wrong")
    dispositions = {row.get("disposition") for row in findings}
    if "reject" in dispositions:
        expected_disposition = "reject"
    elif "review" in dispositions or expected_coverage_state == "partial":
        expected_disposition = "review"
    else:
        expected_disposition = "accept"
    if summary.get("disposition") != expected_disposition:
        raise DoctorError("Doctor report disposition is wrong")


def validate_bound_report(
    report: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    topology_receipt: Mapping[str, Any],
    scans: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Validate bindings, optionally requiring deterministic scan-backed output.

    Without ``scans`` this closes internal identities and external policy,
    topology, rule, and artifact references only. Supplying the exact scans
    additionally rebuilds the topology and reruns every condition, which is
    required to prove match counts, zero-match claims, coverage states, and
    the resulting report content.
    """

    validate_report(report)
    _validate_policy_mapping(policy)
    try:
        validate_topology_receipt(topology_receipt)
    except ArtifactScanError as exc:
        raise DoctorError(f"bound topology receipt is invalid: {exc}") from exc

    report_policy = report.get("policy")
    if not isinstance(report_policy, Mapping):
        raise DoctorError("Doctor report policy binding must be an object")
    if not isinstance(policy_sha256, str) or _SHA256_RE.fullmatch(policy_sha256) is None:
        raise DoctorError("bound policy SHA-256 must be 64 lowercase hexadecimal digits")
    if report_policy.get("policy_id") != policy.get("policy_id"):
        raise DoctorError("Doctor report policy ID does not match the bound policy")
    if report_policy.get("sha256") != policy_sha256:
        raise DoctorError("Doctor report policy SHA-256 does not match the bound policy")

    expected_rules = {
        rule["finding_id"]: (fact_name, rule)
        for fact_name, rule in _policy_rules(policy)
    }
    coverage = report["coverage"]
    coverage_rule_ids = [row["rule_id"] for row in coverage]
    expected_rule_ids = sorted(expected_rules)
    if coverage_rule_ids != expected_rule_ids:
        missing = sorted(set(expected_rule_ids) - set(coverage_rule_ids))
        unexpected = sorted(set(coverage_rule_ids) - set(expected_rule_ids))
        raise DoctorError(
            "Doctor report coverage does not exactly match the bound policy "
            f"(missing={missing}, unexpected={unexpected})"
        )
    for row in coverage:
        fact_name, rule = expected_rules[row["rule_id"]]
        if row.get("scanner_fact") != fact_name:
            raise DoctorError(
                f"Doctor coverage scanner_fact does not match policy rule {row['rule_id']}"
            )
        if row.get("condition") != rule["match"]["condition"]:
            raise DoctorError(
                f"Doctor coverage condition does not match policy rule {row['rule_id']}"
            )

    for row in report["findings"]:
        fact_name, rule = expected_rules[row["rule_id"]]
        expected_semantics = {
            "disposition": rule["result"]["disposition"],
            "rationale": rule["rationale"],
            "scanner_fact": fact_name,
            "severity": rule["result"]["severity"],
        }
        for field, expected in expected_semantics.items():
            if row.get(field) != expected:
                raise DoctorError(
                    f"Doctor finding {field} does not match policy rule {row['rule_id']}"
                )

    report_topology = report.get("topology_receipt")
    if not isinstance(report_topology, Mapping):
        raise DoctorError("Doctor report topology binding must be an object")
    if report_topology.get("receipt_id") != topology_receipt.get("receipt_id"):
        raise DoctorError("Doctor report topology receipt ID does not match")
    receipt_scope = topology_receipt.get("scope")
    if not isinstance(receipt_scope, Mapping):
        raise DoctorError("bound topology receipt scope must be an object")
    if report_topology.get("scope_id") != receipt_scope.get("scope_id"):
        raise DoctorError("Doctor report topology scope ID does not match")

    receipt_artifacts = topology_receipt.get("artifacts")
    if not isinstance(receipt_artifacts, list):
        raise DoctorError("bound topology receipt artifacts must be an array")
    if report["summary"].get("artifact_count") != len(receipt_artifacts):
        raise DoctorError("Doctor report artifact_count does not match topology receipt")
    artifact_identities = {
        (row.get("sha256"), row.get("file_name")) for row in receipt_artifacts
    }
    for row in report["findings"]:
        identity = (row.get("artifact_sha256"), row.get("artifact_label"))
        if identity not in artifact_identities:
            raise DoctorError(
                "Doctor finding artifact identity does not exist in topology receipt"
            )

    if scans is not None:
        expected = evaluate_scans(
            policy=policy,
            policy_sha256=policy_sha256,
            scans=scans,
            topology_receipt=topology_receipt,
        )
        if report != expected:
            raise DoctorError(
                "Doctor report does not match deterministic evaluation of the bound scans"
            )


def inspect_artifact_paths(
    paths: Iterable[Path | str], *, policy_path: Path | str
) -> dict[str, Any]:
    """Read exact archive bytes, scan them, and evaluate the bound policy."""

    policy, policy_sha256 = _load_policy(Path(policy_path))
    inputs: list[ArtifactInput] = []
    scans: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise DoctorError(
                f"artifact path is not a regular non-symlink file: {path}"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DoctorError(f"cannot read artifact {path}: {exc}") from exc
        item = ArtifactInput(label=path.name, data=data)
        inputs.append(item)
        try:
            scans.append(scan_artifact_bytes(data, label=item.label))
        except ArtifactScanError as exc:
            raise DoctorError(f"artifact scan rejected {path}: {exc}") from exc
    if not inputs:
        raise DoctorError("at least one artifact is required")
    scans.sort(key=lambda row: (row["identity"]["sha256"], row["label"]))
    try:
        receipt = build_topology_receipt(inputs)
    except ArtifactScanError as exc:
        raise DoctorError(f"cannot bind topology receipt: {exc}") from exc
    return evaluate_scans(
        policy=policy,
        policy_sha256=policy_sha256,
        scans=scans,
        topology_receipt=receipt,
    )
