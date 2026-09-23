"""Feature-grade orchestration for profile-owned recipe invalidation signals."""

from __future__ import annotations

from hashlib import sha256
from importlib import util as importlib_util
import json
from pathlib import Path
from typing import Any, Mapping

from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .runtime_diagnose import load_verified_runtime_evidence
from .runtime_recipe_reload_diagnostic import (
    RuntimeRecipeDiagnosticError,
    _profile_member,
    _regular_bytes,
)


CATALOG_PATH = Path("profiles/packs/runtime-diagnostics-v1.json")
CATALOG_FORMAT = "workbench-pack-runtime-diagnostic-catalog-v1"
PROFILE_FORMAT = "workbench-runtime-recipe-invalidation-diagnostic-profile-v2"
DIAGNOSTIC_KIND = "recipe-invalidations"
CHANNEL_IDS = ("groovy_postinit", "gt_startup_registration")
MAX_PROFILE_BYTES = 1024 * 1024
MAX_OBSERVER_BYTES = 1024 * 1024


class RuntimeRecipeInvalidationError(RuntimeRecipeDiagnosticError):
    """Verified evidence cannot support the preview recipe feature."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _identity(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return "sha256:" + sha256(_canonical_bytes(payload)).hexdigest()


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeRecipeInvalidationError(
            f"{label} is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeRecipeInvalidationError(f"{label} must be an object")
    return value


def _load_module(
    candidate: Path,
    resolved: Path,
    raw: bytes,
    *,
    label: str,
    module_name: str,
) -> Any:
    specification = importlib_util.spec_from_file_location(module_name, resolved)
    if specification is None or specification.loader is None:
        raise RuntimeRecipeInvalidationError(f"{label} cannot be loaded")
    module = importlib_util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError, ValueError) as exc:
        raise RuntimeRecipeInvalidationError(
            f"{label} cannot be loaded: {exc}"
        ) from exc
    if _regular_bytes(candidate, label, MAX_OBSERVER_BYTES) != raw:
        raise RuntimeRecipeInvalidationError(f"{label} changed while it was loaded")
    return module


def _member_identity(
    path: Path,
    raw: bytes,
) -> dict[str, Any]:
    return {
        "uri": path.as_uri(),
        "sha256": sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _feature_profile(
    suite_root: Path,
    *,
    configuration: WorkbenchConfiguration,
    expected_profile_sha256: str,
    expected_pack_profile_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pack_profile_path = configuration.pack_document.source.path
    raw_pack_profile = configuration.pack_document.source.source_bytes
    if sha256(raw_pack_profile).hexdigest() != expected_profile_sha256:
        raise RuntimeRecipeInvalidationError(
            "pack profile changed after workspace inspection"
        )

    catalog_path = (suite_root / CATALOG_PATH).resolve()
    raw_catalog = _regular_bytes(
        catalog_path,
        "runtime diagnostic catalog",
        MAX_PROFILE_BYTES,
    )
    catalog = _json_object(raw_catalog, "runtime diagnostic catalog")
    if (
        set(catalog) != {"format", "schema_version", "entries"}
        or catalog.get("format") != CATALOG_FORMAT
        or catalog.get("schema_version") != 1
        or not isinstance(catalog.get("entries"), list)
    ):
        raise RuntimeRecipeInvalidationError(
            "runtime diagnostic catalog is unsupported"
        )
    entries = catalog["entries"]
    if any(
        not isinstance(entry, dict)
        or set(entry) != {"diagnostic_kind", "pack_profile_id", "spec"}
        or any(
            not isinstance(entry.get(key), str) or not entry[key]
            for key in ("diagnostic_kind", "pack_profile_id", "spec")
        )
        for entry in entries
    ):
        raise RuntimeRecipeInvalidationError(
            "runtime diagnostic catalog contains a malformed entry"
        )
    identities = [
        (entry["pack_profile_id"], entry["diagnostic_kind"])
        for entry in entries
    ]
    if len(identities) != len(set(identities)):
        raise RuntimeRecipeInvalidationError(
            "runtime diagnostic catalog contains duplicate identities"
        )
    matches = [
        entry
        for entry in entries
        if entry["pack_profile_id"] == expected_pack_profile_id
        and entry["diagnostic_kind"] == DIAGNOSTIC_KIND
    ]
    if len(matches) != 1:
        raise RuntimeRecipeInvalidationError(
            "selected pack has no unique recipe invalidation feature profile"
        )
    (
        _profile_candidate,
        profile_path,
        raw_profile,
    ) = _profile_member(
        catalog_path.parent,
        matches[0]["spec"],
        "recipe invalidation feature profile",
        MAX_PROFILE_BYTES,
    )
    declaration = _json_object(raw_profile, "recipe invalidation feature profile")
    required = {
        "format",
        "schema_version",
        "profile_id",
        "pack_profile_id",
        "capability_maturity",
        "runtime_support",
        "contract",
        "diagnostic_format",
        "diagnostic_schema",
        "comparison_format",
        "comparison_schema",
        "composer",
        "channels",
    }
    composer = declaration.get("composer")
    channels = declaration.get("channels")
    if (
        set(declaration) != required
        or declaration.get("format") != PROFILE_FORMAT
        or declaration.get("schema_version") != 2
        or declaration.get("pack_profile_id") != expected_pack_profile_id
        or declaration.get("capability_maturity") != "preview"
        or declaration.get("runtime_support") != "provisional"
        or any(
            not isinstance(declaration.get(key), str) or not declaration[key]
            for key in (
                "profile_id",
                "contract",
                "diagnostic_format",
                "diagnostic_schema",
                "comparison_format",
                "comparison_schema",
            )
        )
        or not isinstance(composer, dict)
        or set(composer)
        != {"observer", "diagnostic_function", "comparison_function"}
        or any(not isinstance(value, str) or not value for value in composer.values())
        or not isinstance(channels, dict)
        or set(channels) != set(CHANNEL_IDS)
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation feature profile is unsupported or mismatched"
        )
    channel_required = {
        "evidence_label",
        "observer",
        "diagnostic_function",
        "comparison_function",
        "diagnostic_format",
        "comparison_format",
    }
    if any(
        not isinstance(value, dict)
        or set(value) != channel_required
        or any(not isinstance(item, str) or not item for item in value.values())
        for value in channels.values()
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation feature profile has a malformed channel"
        )
    evidence_labels = [channels[key]["evidence_label"] for key in CHANNEL_IDS]
    if len(evidence_labels) != len(set(evidence_labels)):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation feature channels reuse an evidence label"
        )

    profile_root = pack_profile_path.parent.resolve()
    support_members: dict[str, dict[str, Any]] = {}
    for key, label in (
        ("contract", "recipe invalidation contract"),
        ("diagnostic_schema", "recipe invalidation diagnostic schema"),
        ("comparison_schema", "recipe invalidation comparison schema"),
    ):
        _candidate, resolved, raw = _profile_member(
            profile_root,
            declaration[key],
            label,
            MAX_PROFILE_BYTES,
        )
        support_members[key] = _member_identity(resolved, raw)

    modules: dict[str, Any] = {}
    observer_bindings: dict[str, dict[str, Any]] = {}
    observer_declarations = {"composer": composer["observer"]}
    observer_declarations.update({
        channel_id: channels[channel_id]["observer"]
        for channel_id in CHANNEL_IDS
    })
    for key, relative in observer_declarations.items():
        candidate, resolved, raw = _profile_member(
            profile_root,
            relative,
            f"recipe invalidation {key} observer",
            MAX_OBSERVER_BYTES,
        )
        modules[key] = _load_module(
            candidate,
            resolved,
            raw,
            label=f"recipe invalidation {key} observer",
            module_name=f"_workbench_recipe_invalidation_{key}",
        )
        observer_bindings[key] = _member_identity(resolved, raw)

    composer_diagnostic = getattr(
        modules["composer"], composer["diagnostic_function"], None
    )
    composer_comparison = getattr(
        modules["composer"], composer["comparison_function"], None
    )
    functions: dict[str, dict[str, Any]] = {}
    for channel_id in CHANNEL_IDS:
        channel = channels[channel_id]
        diagnostic = getattr(
            modules[channel_id], channel["diagnostic_function"], None
        )
        comparison = getattr(
            modules[channel_id], channel["comparison_function"], None
        )
        if not callable(diagnostic) or not callable(comparison):
            raise RuntimeRecipeInvalidationError(
                f"{channel_id} observer lacks its declared interface"
            )
        functions[channel_id] = {
            "diagnostic": diagnostic,
            "comparison": comparison,
        }
    if not callable(composer_diagnostic) or not callable(composer_comparison):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation composer lacks its declared interface"
        )

    channel_bindings: dict[str, dict[str, Any]] = {}
    for channel_id in CHANNEL_IDS:
        channel = channels[channel_id]
        channel_bindings[channel_id] = {
            "channel_id": channel_id,
            "evidence_label": channel["evidence_label"],
            "diagnostic_function": channel["diagnostic_function"],
            "comparison_function": channel["comparison_function"],
            "diagnostic_format": channel["diagnostic_format"],
            "comparison_format": channel["comparison_format"],
            "observer_uri": observer_bindings[channel_id]["uri"],
            "observer_sha256": observer_bindings[channel_id]["sha256"],
            "observer_size": observer_bindings[channel_id]["size"],
        }
    binding: dict[str, Any] = {
        "profile_id": declaration["profile_id"],
        "pack_profile_id": declaration["pack_profile_id"],
        "capability_maturity": declaration["capability_maturity"],
        "runtime_support": declaration["runtime_support"],
        "diagnostic_format": declaration["diagnostic_format"],
        "comparison_format": declaration["comparison_format"],
        "profile_uri": profile_path.as_uri(),
        "profile_sha256": sha256(raw_profile).hexdigest(),
        "profile_size": len(raw_profile),
        "catalog_uri": catalog_path.as_uri(),
        "catalog_sha256": sha256(raw_catalog).hexdigest(),
        "catalog_size": len(raw_catalog),
        "contract": support_members["contract"],
        "diagnostic_schema": support_members["diagnostic_schema"],
        "comparison_schema": support_members["comparison_schema"],
        "composer": {
            "diagnostic_function": composer["diagnostic_function"],
            "comparison_function": composer["comparison_function"],
            "observer_uri": observer_bindings["composer"]["uri"],
            "observer_sha256": observer_bindings["composer"]["sha256"],
            "observer_size": observer_bindings["composer"]["size"],
        },
        "channels": channel_bindings,
    }
    return {
        "composer_diagnostic": composer_diagnostic,
        "composer_comparison": composer_comparison,
        "channels": functions,
    }, binding


def _channel_comparison_profile(
    channel_binding: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "diagnostic_profile": dict(channel_binding),
        "function": channel_binding["comparison_function"],
        "report_format": channel_binding["comparison_format"],
    }


def _comparison_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "diagnostic_profile": dict(profile),
        "function": profile["composer"]["comparison_function"],
        "report_format": profile["comparison_format"],
    }


def _valid_authority(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value)
        == {"classification", "owner", "normative", "atlas_publication"}
        and isinstance(value.get("classification"), str)
        and value["classification"]
        and isinstance(value.get("owner"), str)
        and value["owner"]
        and value.get("normative") is False
        and value.get("atlas_publication") is False
    )


def _valid_recommendation(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == {"state", "summary", "actions"}
        and isinstance(value.get("state"), str)
        and value["state"]
        and isinstance(value.get("summary"), str)
        and value["summary"]
        and isinstance(value.get("actions"), list)
        and all(isinstance(item, str) and item for item in value["actions"])
    )


def _valid_limitations(value: Any) -> bool:
    return bool(
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        and len(value) == len(set(value))
    )


def _channel_source(
    source: Mapping[str, Any],
    channel_id: str,
) -> dict[str, Any]:
    evidence = source.get("evidence")
    if not isinstance(evidence, Mapping) or not isinstance(
        evidence.get(channel_id), Mapping
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation source lacks its channel evidence binding"
        )
    result = dict(source)
    result["evidence"] = dict(evidence[channel_id])
    return result


def _channel_comparison_source(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    def side(report: Mapping[str, Any]) -> dict[str, Any]:
        source = report["source"]
        return {
            "diagnostic_id": report["diagnostic_id"],
            "launch_receipt": source["launch_receipt"],
            "launch_id": source["launch_id"],
            "evidence": source["evidence"],
        }

    return {
        "baseline": side(baseline),
        "candidate": side(candidate),
        "project": baseline["source"]["project"],
        "selection": "explicit-user-supplied-receipts",
        "source_causality": "unbound",
    }


def _combined_comparison_source(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    def side(report: Mapping[str, Any]) -> dict[str, Any]:
        source = report["source"]
        return {
            "diagnostic_id": report["diagnostic_id"],
            "launch_receipt": source["launch_receipt"],
            "launch_id": source["launch_id"],
            "project": source["project"],
            "process_observation": source["process_observation"],
            "evidence": source["evidence"],
        }

    return {
        "baseline": side(baseline),
        "candidate": side(candidate),
        "roles": "caller-selected-explicit-baseline-and-candidate",
        "source_causality": "unbound",
    }


def _validate_channel_report(
    value: Any,
    *,
    profile: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "format",
        "schema_version",
        "diagnostic_id",
        "operation_class",
        "authority",
        "state",
        "profile",
        "source",
        "scope",
        "summary",
        "groups",
        "frontiers",
        "recommendation",
        "limitations",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation channel returned an unexpected shape"
        )
    channel_id = profile.get("channel_id")
    allowed_states = {
        "groovy_postinit": {
            "attention",
            "inconclusive",
            "no-groovy-conflicts-observed",
        },
        "gt_startup_registration": {
            "attention",
            "inconclusive",
            "no-signals-observed",
        },
    }
    identity_fields = {
        "groovy_postinit": ("kind", "script_logger", "recipe_map"),
        "gt_startup_registration": (
            "kind",
            "reason_code",
            "observed_owner_class",
            "observed_owner_method",
        ),
    }
    summary = value.get("summary")
    groups = value.get("groups")
    frontiers = value.get("frontiers")
    identities = (
        [
            tuple(group.get(field) for field in identity_fields[channel_id])
            for group in groups
        ]
        if channel_id in identity_fields
        and isinstance(groups, list)
        and all(isinstance(group, dict) for group in groups)
        else []
    )
    if (
        value.get("format") != profile.get("diagnostic_format")
        or value.get("schema_version") != 1
        or value.get("operation_class") != "read-only"
        or value.get("profile") != profile
        or value.get("source") != source
        or value.get("diagnostic_id") != _identity(value, "diagnostic_id")
        or channel_id not in allowed_states
        or value.get("state") not in allowed_states[channel_id]
        or not _valid_authority(value.get("authority"))
        or not isinstance(value.get("scope"), dict)
        or not isinstance(summary, dict)
        or not isinstance(groups, list)
        or not all(isinstance(group, dict) and group for group in groups)
        or len(identities) != len(groups)
        or any(
            any(not isinstance(item, str) or not item for item in identity)
            for identity in identities
        )
        or len(identities) != len(set(identities))
        or type(summary.get("group_count")) is not int
        or type(summary.get("emitted_group_count")) is not int
        or summary["emitted_group_count"] != len(groups)
        or summary["group_count"] < summary["emitted_group_count"]
        or type(summary.get("groups_truncated")) is not bool
        or type(summary.get("incomplete_sequence_count")) is not int
        or summary["incomplete_sequence_count"] < 0
        or type(summary.get("emitted_frontier_count")) is not int
        or not isinstance(frontiers, list)
        or not all(isinstance(frontier, dict) and frontier for frontier in frontiers)
        or summary["emitted_frontier_count"] != len(frontiers)
        or type(summary.get("frontiers_truncated")) is not bool
        or not _valid_recommendation(value.get("recommendation"))
        or not _valid_limitations(value.get("limitations"))
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation channel returned an invalid binding or identity"
        )
    return value


def _validate_diagnostic(
    value: Any,
    *,
    source: Mapping[str, Any],
    profile: Mapping[str, Any],
    channels: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "format",
        "schema_version",
        "diagnostic_id",
        "operation_class",
        "authority",
        "state",
        "profile",
        "source",
        "channels",
        "recommendation",
        "limitations",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation composer returned an unexpected shape"
        )
    if (
        value.get("format") != profile.get("diagnostic_format")
        or value.get("schema_version") != 2
        or value.get("operation_class") != "read-only"
        or value.get("profile") != profile
        or value.get("source") != source
        or value.get("channels") != channels
        or value.get("diagnostic_id") != _identity(value, "diagnostic_id")
        or value.get("state")
        not in {
            "attention",
            "attention-incomplete",
            "inconclusive",
            "no-supported-signals-observed",
        }
        or not _valid_authority(value.get("authority"))
        or not _valid_recommendation(value.get("recommendation"))
        or not _valid_limitations(value.get("limitations"))
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation composer returned an invalid binding or identity"
        )
    for channel_id in CHANNEL_IDS:
        _validate_channel_report(
            value["channels"][channel_id],
            profile=profile["channels"][channel_id],
            source=_channel_source(source, channel_id),
        )
    return value


def _validate_channel_comparison(
    value: Any,
    *,
    profile: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "format",
        "schema_version",
        "comparison_id",
        "operation_class",
        "authority",
        "state",
        "profile",
        "source",
        "scope",
        "summary",
        "groups",
        "recommendation",
        "limitations",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation channel comparator returned an unexpected shape"
        )
    diagnostic_profile = profile.get("diagnostic_profile")
    channel_id = (
        diagnostic_profile.get("channel_id")
        if isinstance(diagnostic_profile, Mapping)
        else None
    )
    allowed_states = {
        "groovy_postinit": {
            "more-observed",
            "fewer-observed",
            "same-counts",
            "same-counts-with-resolution-count-changes",
        },
        "gt_startup_registration": {
            "more-observed",
            "fewer-observed",
            "same-counts",
        },
    }
    summary = value.get("summary")
    groups = value.get("groups")
    if (
        value.get("format") != profile.get("report_format")
        or value.get("schema_version") != 1
        or value.get("operation_class") != "read-only"
        or value.get("profile") != profile
        or value.get("source")
        != _channel_comparison_source(baseline, candidate)
        or value.get("comparison_id") != _identity(value, "comparison_id")
        or channel_id not in allowed_states
        or value.get("state") not in allowed_states[channel_id]
        or not _valid_authority(value.get("authority"))
        or not isinstance(value.get("scope"), dict)
        or not isinstance(summary, dict)
        or not isinstance(groups, list)
        or not all(isinstance(group, dict) and group for group in groups)
        or type(summary.get("group_count")) is not int
        or summary["group_count"] != len(groups)
        or not _valid_recommendation(value.get("recommendation"))
        or not _valid_limitations(value.get("limitations"))
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation channel comparator returned an invalid binding or identity"
        )
    return value


def _diagnose_receipt(
    context: Mapping[str, Any],
    launch_receipt: Path | str,
    *,
    loaded: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    receipt, receipt_identity, evidence, texts, limitations = (
        load_verified_runtime_evidence(launch_receipt)
    )
    if receipt.get("format") != "workbench-runtime-launch-receipt-v3":
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation diagnosis requires a process-bound V3 launch receipt"
        )
    if (
        receipt.get("outcome") != "checkpoint-reached"
        or not isinstance(receipt.get("observation"), dict)
        or not isinstance(receipt["observation"].get("session_exit"), dict)
        or receipt["observation"]["session_exit"].get("state") != "exited"
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation diagnosis requires a completed checkpoint launch"
        )
    records: dict[str, dict[str, Any]] = {}
    text_by_label: dict[str, str] = {}
    for channel_id in CHANNEL_IDS:
        label = profile["channels"][channel_id]["evidence_label"]
        matches = [
            item
            for item in evidence
            if item.get("label") == label and item.get("state") == "verified"
        ]
        matching_texts = [text for item_label, text in texts if item_label == label]
        if len(matches) != 1 or len(matching_texts) != 1:
            raise RuntimeRecipeInvalidationError(
                f"launch receipt must bind exactly one text-readable {label}"
            )
        records[channel_id] = matches[0]
        text_by_label[label] = matching_texts[0]

    workspace_context = context["workspace_context"]
    expected_project = {
        "name": workspace_context["project"]["name"],
        "version": workspace_context["project"]["version"],
        "minecraft_version": workspace_context["project"]["minecraft_version"],
    }
    receipt_project = receipt.get("project")
    if not isinstance(receipt_project, dict) or any(
        receipt_project.get(key) != value
        for key, value in expected_project.items()
    ):
        raise RuntimeRecipeInvalidationError(
            "launch receipt project identity differs from the selected workspace"
        )
    launcher = receipt.get("launcher")
    java = receipt.get("java")
    checkpoint = receipt["observation"].get("checkpoint")
    source: dict[str, Any] = {
        "workspace": {
            "root_uri": workspace_context["workspace"]["root_uri"],
            "revision": workspace_context["workspace"]["revision"],
            "launch_binding": {
                "state": "unverified-current-context",
                "reason": "the V3 launch receipt does not bind a workspace revision",
            },
        },
        "project": {
            **expected_project,
            "launch_binding": "matched-receipt-project-fields",
        },
        "pack_profile": {
            "profile_family_id": workspace_context["pack"]["profile_family_id"],
            "selected_profile": workspace_context["pack"]["selected_profile"],
            "document_sha256": workspace_context["pack"]["document_sha256"],
            "launch_binding": {
                "state": "unverified-current-diagnostic-context",
                "reason": (
                    "the V3 launch receipt does not bind the selected pack-profile document"
                ),
            },
        },
        "launch_receipt": receipt_identity,
        "launch_id": receipt["launch_id"],
        "launch_outcome": receipt["outcome"],
        "process_observation": receipt["observation"]["session_exit"],
        "checkpoint": checkpoint,
        "environment": {
            "java_runtime_id": java.get("runtime_id") if isinstance(java, dict) else None,
            "launcher_family": launcher.get("family") if isinstance(launcher, dict) else None,
            "launcher_host": launcher.get("host") if isinstance(launcher, dict) else None,
        },
        "evidence": {
            channel_id: records[channel_id]
            for channel_id in CHANNEL_IDS
        },
    }
    inherited = list(limitations)
    receipt_limitations = receipt.get("limitations")
    if isinstance(receipt_limitations, list):
        inherited.extend(
            item
            for item in receipt_limitations
            if isinstance(item, str) and item
        )
    channel_reports: dict[str, dict[str, Any]] = {}
    for channel_id in CHANNEL_IDS:
        channel_profile = profile["channels"][channel_id]
        label = channel_profile["evidence_label"]
        channel_source = dict(source)
        channel_source["evidence"] = records[channel_id]
        try:
            result = loaded["channels"][channel_id]["diagnostic"](
                text_by_label[label],
                source=channel_source,
                profile=channel_profile,
                inherited_limitations=inherited,
            )
        except ValueError as exc:
            raise RuntimeRecipeInvalidationError(str(exc)) from exc
        channel_reports[channel_id] = _validate_channel_report(
            result,
            profile=channel_profile,
            source=channel_source,
        )
    try:
        result = loaded["composer_diagnostic"](
            source=source,
            profile=profile,
            channels=channel_reports,
            inherited_limitations=inherited,
        )
    except ValueError as exc:
        raise RuntimeRecipeInvalidationError(str(exc)) from exc
    return _validate_diagnostic(
        result,
        source=source,
        profile=profile,
        channels=channel_reports,
    )


def _comparison_compatibility(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[str]:
    findings: list[str] = []
    baseline_source = baseline["source"]
    candidate_source = candidate["source"]
    if (
        baseline_source.get("launch_id") == candidate_source.get("launch_id")
        or baseline_source.get("launch_receipt") == candidate_source.get("launch_receipt")
    ):
        findings.append("Select two distinct launch receipts.")
    if baseline_source.get("project") != candidate_source.get("project"):
        findings.append("Baseline and candidate project identities differ.")
    baseline_checkpoint = baseline_source.get("checkpoint")
    candidate_checkpoint = candidate_source.get("checkpoint")
    for field in ("id", "source"):
        before = baseline_checkpoint.get(field) if isinstance(baseline_checkpoint, dict) else None
        after = candidate_checkpoint.get(field) if isinstance(candidate_checkpoint, dict) else None
        if before is None or after is None:
            findings.append(f"Baseline or candidate checkpoint {field} is unavailable.")
        elif before != after:
            findings.append(f"Baseline and candidate checkpoint {field} values differ.")
    baseline_environment = baseline_source.get("environment")
    candidate_environment = candidate_source.get("environment")
    for field in ("java_runtime_id", "launcher_family", "launcher_host"):
        before = baseline_environment.get(field) if isinstance(baseline_environment, dict) else None
        after = candidate_environment.get(field) if isinstance(candidate_environment, dict) else None
        if before is None or after is None:
            findings.append(
                f"Baseline or candidate {field.replace('_', ' ')} is unavailable."
            )
        elif before != after:
            findings.append(f"Baseline and candidate {field.replace('_', ' ')} values differ.")
    return sorted(set(findings))


def _validate_comparison(
    value: Any,
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    profile: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    channels: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "format",
        "schema_version",
        "comparison_id",
        "operation_class",
        "authority",
        "state",
        "profile",
        "source",
        "compatibility",
        "channels",
        "recommendation",
        "limitations",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation comparator returned an unexpected shape"
        )
    if (
        value.get("format") != profile["report_format"]
        or value.get("schema_version") != 2
        or value.get("operation_class") != "read-only"
        or value.get("profile") != profile
        or value.get("comparison_id") != _identity(value, "comparison_id")
        or value.get("source")
        != _combined_comparison_source(baseline, candidate)
        or value.get("compatibility") != compatibility
        or value.get("channels") != channels
        or value.get("state")
        not in {
            "more-observed",
            "fewer-observed",
            "same-counts",
            "same-counts-with-resolution-count-changes",
            "incomparable",
        }
        or (
            compatibility.get("state") == "incomparable"
            and value.get("state") != "incomparable"
        )
        or (
            compatibility.get("state") == "comparable"
            and value.get("state") == "incomparable"
        )
        or not _valid_authority(value.get("authority"))
        or not _valid_recommendation(value.get("recommendation"))
        or not _valid_limitations(value.get("limitations"))
    ):
        raise RuntimeRecipeInvalidationError(
            "recipe invalidation comparator returned an invalid binding or identity"
        )
    return value


def diagnose_project_recipe_invalidations(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launch_receipt: Path | str,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Explain both supported recipe-registration channels for one launch."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeRecipeInvalidationError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeRecipeInvalidationError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    context = inspect_project(
        suite,
        workspace_root,
        configuration=active_configuration,
    )
    workspace_context = context["workspace_context"]
    loaded, profile = _feature_profile(
        suite,
        configuration=active_configuration,
        expected_profile_sha256=workspace_context["pack"]["document_sha256"],
        expected_pack_profile_id=workspace_context["pack"]["profile_family_id"],
    )
    return _diagnose_receipt(
        context,
        launch_receipt,
        loaded=loaded,
        profile=profile,
    )


def compare_project_recipe_invalidations(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    baseline_launch_receipt: Path | str,
    candidate_launch_receipt: Path | str,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compare two explicit V3 observations across both supported channels."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeRecipeInvalidationError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeRecipeInvalidationError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    context = inspect_project(
        suite,
        workspace_root,
        configuration=active_configuration,
    )
    workspace_context = context["workspace_context"]
    loaded, profile = _feature_profile(
        suite,
        configuration=active_configuration,
        expected_profile_sha256=workspace_context["pack"]["document_sha256"],
        expected_pack_profile_id=workspace_context["pack"]["profile_family_id"],
    )
    baseline = _diagnose_receipt(
        context,
        baseline_launch_receipt,
        loaded=loaded,
        profile=profile,
    )
    candidate = _diagnose_receipt(
        context,
        candidate_launch_receipt,
        loaded=loaded,
        profile=profile,
    )
    findings = _comparison_compatibility(baseline, candidate)
    channel_results: dict[str, Any] = {
        channel_id: {"state": "not-compared"}
        for channel_id in CHANNEL_IDS
    }
    if not findings:
        tentative: dict[str, Any] = {}
        for channel_id in CHANNEL_IDS:
            channel_profile = profile["channels"][channel_id]
            channel_comparison_profile = _channel_comparison_profile(
                channel_profile
            )
            try:
                comparison = loaded["channels"][channel_id]["comparison"](
                    baseline["channels"][channel_id],
                    candidate["channels"][channel_id],
                    profile=channel_comparison_profile,
                )
            except ValueError as exc:
                findings.append(
                    f"{channel_id} observations are not comparable: {exc}"
                )
                break
            tentative[channel_id] = _validate_channel_comparison(
                comparison,
                profile=channel_comparison_profile,
                baseline=baseline["channels"][channel_id],
                candidate=candidate["channels"][channel_id],
            )
        if not findings:
            channel_results = tentative
    compatibility = {
        "state": "incomparable" if findings else "comparable",
        "findings": sorted(set(findings)),
    }
    comparison_profile = _comparison_profile(profile)
    try:
        result = loaded["composer_comparison"](
            baseline,
            candidate,
            profile=comparison_profile,
            compatibility=compatibility,
            channels=channel_results,
        )
    except ValueError as exc:
        raise RuntimeRecipeInvalidationError(str(exc)) from exc
    return _validate_comparison(
        result,
        baseline=baseline,
        candidate=candidate,
        profile=comparison_profile,
        compatibility=compatibility,
        channels=channel_results,
    )


__all__ = [
    "RuntimeRecipeInvalidationError",
    "compare_project_recipe_invalidations",
    "diagnose_project_recipe_invalidations",
]
