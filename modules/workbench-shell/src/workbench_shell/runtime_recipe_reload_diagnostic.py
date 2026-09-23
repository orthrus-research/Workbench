"""Experimental Shell orchestration for profile-owned recipe reload diagnosis."""

from __future__ import annotations

from hashlib import sha256
from importlib import util as importlib_util
import json
from pathlib import Path
import stat
from typing import Any, Mapping

from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .runtime_diagnose import load_verified_runtime_evidence


PROFILE_FORMAT = "workbench-runtime-recipe-reload-diagnostic-profile-v1"
CATALOG_FORMAT = (
    "workbench-pack-experimental-runtime-diagnostic-catalog-v1"
)
DIAGNOSTIC_CATALOG_PATH = Path(
    "profiles/packs/experimental-runtime-diagnostics-v1.json"
)
DIAGNOSTIC_KIND = "recipe-reload"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_OBSERVER_BYTES = 1024 * 1024


class RuntimeRecipeDiagnosticError(ValueError):
    """A verified receipt cannot support the profile recipe diagnostic."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeRecipeDiagnosticError(
            f"{label} is unavailable: {path}"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > maximum
    ):
        raise RuntimeRecipeDiagnosticError(
            f"{label} is not a bounded regular file: {path}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeRecipeDiagnosticError(
            f"{label} cannot be read: {path}"
        ) from exc
    if len(raw) != metadata.st_size:
        raise RuntimeRecipeDiagnosticError(
            f"{label} changed while it was read: {path}"
        )
    return raw


def _profile_member(
    profile_root: Path,
    value: Any,
    label: str,
    maximum: int,
) -> tuple[Path, Path, bytes]:
    if not isinstance(value, str) or not value:
        raise RuntimeRecipeDiagnosticError(f"{label} path is missing")
    relative = Path(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(character in value for character in "*?[")
    ):
        raise RuntimeRecipeDiagnosticError(
            f"{label} escapes its authority root"
        )
    candidate = profile_root / relative
    resolved = candidate.resolve()
    if not resolved.is_relative_to(profile_root):
        raise RuntimeRecipeDiagnosticError(
            f"{label} escapes its authority root"
        )
    return candidate, resolved, _regular_bytes(candidate, label, maximum)


def _profile_observer(
    suite_root: Path,
    *,
    configuration: WorkbenchConfiguration,
    expected_profile_sha256: str,
    expected_pack_profile_id: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    profile_path = configuration.pack_document.source.path
    raw_profile = configuration.pack_document.source.source_bytes
    if sha256(raw_profile).hexdigest() != expected_profile_sha256:
        raise RuntimeRecipeDiagnosticError(
            "pack profile changed after workspace inspection"
        )
    catalog_path = (suite_root / DIAGNOSTIC_CATALOG_PATH).resolve()
    raw_catalog = _regular_bytes(
        catalog_path,
        "experimental runtime diagnostic catalog",
        MAX_PROFILE_BYTES,
    )
    try:
        catalog = json.loads(raw_catalog.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeRecipeDiagnosticError(
            "experimental runtime diagnostic catalog is not valid UTF-8 JSON"
        ) from exc
    if (
        not isinstance(catalog, dict)
        or set(catalog) != {"format", "schema_version", "entries"}
        or catalog.get("format") != CATALOG_FORMAT
        or catalog.get("schema_version") != 1
        or not isinstance(catalog.get("entries"), list)
    ):
        raise RuntimeRecipeDiagnosticError(
            "experimental runtime diagnostic catalog is unsupported"
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
        raise RuntimeRecipeDiagnosticError(
            "experimental runtime diagnostic catalog contains a malformed entry"
        )
    identities = [
        (entry["pack_profile_id"], entry["diagnostic_kind"])
        for entry in entries
    ]
    if len(identities) != len(set(identities)):
        raise RuntimeRecipeDiagnosticError(
            "experimental runtime diagnostic catalog contains duplicate identities"
        )
    matches = [
        entry
        for entry in entries
        if entry["pack_profile_id"] == expected_pack_profile_id
        and entry["diagnostic_kind"] == DIAGNOSTIC_KIND
    ]
    if len(matches) != 1:
        raise RuntimeRecipeDiagnosticError(
            "selected pack has no unique recipe reload diagnostic profile"
        )
    (
        _diagnostic_profile_candidate,
        diagnostic_profile_path,
        raw_diagnostic_profile,
    ) = _profile_member(
        catalog_path.parent,
        matches[0].get("spec"),
        "recipe reload diagnostic profile",
        MAX_PROFILE_BYTES,
    )
    try:
        declaration = json.loads(raw_diagnostic_profile.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic profile is not valid UTF-8 JSON"
        ) from exc
    required = {
        "format",
        "schema_version",
        "profile_id",
        "pack_profile_id",
        "maturity",
        "report_format",
        "contract",
        "observer",
        "comparison",
    }
    if not isinstance(declaration, dict):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic profile must be an object"
        )
    profile_id = declaration.get("profile_id")
    pack_profile_id = declaration.get("pack_profile_id")
    maturity = declaration.get("maturity")
    report_format = declaration.get("report_format")
    observer_value = declaration.get("observer")
    contract_value = declaration.get("contract")
    comparison = declaration.get("comparison")
    if (
        set(declaration) != required
        or declaration.get("format") != PROFILE_FORMAT
        or declaration.get("schema_version") != 1
        or not isinstance(profile_id, str)
        or not profile_id
        or pack_profile_id != expected_pack_profile_id
        or not isinstance(maturity, str)
        or not maturity
        or not isinstance(report_format, str)
        or not report_format
        or not isinstance(comparison, dict)
        or set(comparison) != {"function", "report_format"}
        or not isinstance(comparison.get("function"), str)
        or not comparison["function"]
        or not isinstance(comparison.get("report_format"), str)
        or not comparison["report_format"]
    ):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic profile is unsupported or mismatched"
        )
    profile_root = profile_path.parent.resolve()
    observer_candidate, observer_path, raw_observer = _profile_member(
        profile_root,
        observer_value,
        "recipe reload diagnostic observer",
        MAX_OBSERVER_BYTES,
    )
    _contract_candidate, contract_path, raw_contract = _profile_member(
        profile_root,
        contract_value,
        "recipe reload diagnostic contract",
        MAX_PROFILE_BYTES,
    )
    specification = importlib_util.spec_from_file_location(
        "_workbench_profile_recipe_reload_diagnostic",
        observer_path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic observer cannot be loaded"
        )
    module = importlib_util.module_from_spec(specification)
    try:
        specification.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError, ValueError) as exc:
        raise RuntimeRecipeDiagnosticError(
            f"recipe reload diagnostic observer cannot be loaded: {exc}"
        ) from exc
    if _regular_bytes(
        observer_candidate,
        "recipe reload diagnostic observer",
        MAX_OBSERVER_BYTES,
    ) != raw_observer:
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic observer changed while it was loaded"
        )
    observer = getattr(module, "build_recipe_reload_diagnostic", None)
    comparator = getattr(module, comparison["function"], None)
    observer_error = getattr(module, "RecipeReloadDiagnosticError", ValueError)
    if (
        not callable(observer)
        or not callable(comparator)
        or not isinstance(observer_error, type)
        or not issubclass(observer_error, Exception)
    ):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic observer lacks its required interface"
        )
    binding = {
        "profile_id": profile_id,
        "pack_profile_id": pack_profile_id,
        "maturity": maturity,
        "report_format": report_format,
        "profile_uri": diagnostic_profile_path.resolve().as_uri(),
        "profile_sha256": sha256(raw_diagnostic_profile).hexdigest(),
        "profile_size": len(raw_diagnostic_profile),
        "catalog_uri": catalog_path.as_uri(),
        "catalog_sha256": sha256(raw_catalog).hexdigest(),
        "catalog_size": len(raw_catalog),
        "observer_uri": observer_path.as_uri(),
        "observer_sha256": sha256(raw_observer).hexdigest(),
        "observer_size": len(raw_observer),
        "contract_uri": contract_path.as_uri(),
        "contract_sha256": sha256(raw_contract).hexdigest(),
        "contract_size": len(raw_contract),
    }
    comparison_binding = {
        "diagnostic_profile": binding,
        "function": comparison["function"],
        "report_format": comparison["report_format"],
    }
    return (
        (observer, comparator, observer_error),
        binding,
        comparison_binding,
    )


def _validate_report(
    value: Any,
    *,
    source: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic observer returned a non-object"
        )
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
    if set(value) != required:
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic observer returned unexpected fields"
        )
    if (
        value.get("format") != profile.get("report_format")
        or value.get("schema_version") != 1
        or value.get("operation_class") != "read-only"
        or value.get("source") != source
        or value.get("profile") != profile
    ):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic observer returned an unsupported binding"
        )
    diagnostic_id = value.get("diagnostic_id")
    identity = dict(value)
    identity.pop("diagnostic_id", None)
    expected = "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()
    if diagnostic_id != expected:
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic identity does not match its content"
        )
    summary = value.get("summary")
    groups = value.get("groups")
    if not isinstance(summary, dict) or not isinstance(groups, list):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic summary is malformed"
        )
    if summary.get("emitted_group_count") != len(groups):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic group count is stale"
        )
    identities = [
        (group.get("kind"), group.get("script_logger"), group.get("recipe_map"))
        for group in groups
        if isinstance(group, dict)
    ]
    if len(identities) != len(groups) or len(identities) != len(set(identities)):
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnostic groups lack unique identities"
        )
    return value


def _diagnose_recipe_reload_receipt(
    context: Mapping[str, Any],
    launch_receipt: Path | str,
    *,
    loaded_observer: tuple[Any, Any, type[Exception]],
    profile_binding: Mapping[str, Any],
) -> dict[str, Any]:
    (
        receipt,
        receipt_identity,
        evidence,
        texts,
        limitations,
    ) = load_verified_runtime_evidence(launch_receipt)
    if receipt.get("format") != "workbench-runtime-launch-receipt-v3":
        raise RuntimeRecipeDiagnosticError(
            "recipe reload diagnosis requires a process-bound V3 launch receipt"
        )
    groovy_evidence = [
        item
        for item in evidence
        if item.get("label") == "minecraft-groovy-log"
        and item.get("state") == "verified"
    ]
    groovy_texts = [
        text for label, text in texts if label == "minecraft-groovy-log"
    ]
    if len(groovy_evidence) != 1 or len(groovy_texts) != 1:
        raise RuntimeRecipeDiagnosticError(
            "launch receipt must bind exactly one text-readable minecraft-groovy-log"
        )
    workspace_context = context["workspace_context"]
    expected_project = {
        "name": workspace_context["project"]["name"],
        "version": workspace_context["project"]["version"],
        "minecraft_version": workspace_context["project"][
            "minecraft_version"
        ],
    }
    receipt_project = receipt.get("project")
    if not isinstance(receipt_project, dict) or any(
        receipt_project.get(key) != value
        for key, value in expected_project.items()
    ):
        raise RuntimeRecipeDiagnosticError(
            "launch receipt project identity differs from the selected workspace"
        )
    observer, _comparator, observer_error = loaded_observer
    source = {
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
            "profile_family_id": workspace_context["pack"][
                "profile_family_id"
            ],
            "selected_profile": workspace_context["pack"][
                "selected_profile"
            ],
            "document_sha256": workspace_context["pack"][
                "document_sha256"
            ],
            "launch_binding": {
                "state": "unverified-current-diagnostic-context",
                "reason": (
                    "the V3 launch receipt does not bind the selected "
                    "pack-profile document"
                ),
            },
        },
        "launch_receipt": receipt_identity,
        "launch_id": receipt["launch_id"],
        "launch_outcome": receipt["outcome"],
        "process_observation": receipt["observation"]["session_exit"],
        "evidence": groovy_evidence[0],
    }
    inherited = list(limitations)
    receipt_limitations = receipt.get("limitations")
    if isinstance(receipt_limitations, list):
        inherited.extend(
            item
            for item in receipt_limitations
            if isinstance(item, str) and item
        )
    if any(
        item.get("label") == "minecraft-latest-log"
        and item.get("state") == "verified"
        for item in evidence
    ):
        inherited.append(
            "Verified minecraft-latest-log recipe-registration signals are outside this Groovy postInit conflict channel."
        )
    try:
        report = observer(
            groovy_texts[0],
            source=source,
            profile=profile_binding,
            inherited_limitations=inherited,
        )
    except observer_error as exc:
        raise RuntimeRecipeDiagnosticError(str(exc)) from exc
    return _validate_report(
        report,
        source=source,
        profile=profile_binding,
    )


def _validate_comparison(
    value: Any,
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeRecipeDiagnosticError(
            "recipe conflict comparator returned a non-object"
        )
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
    if (
        set(value) != required
        or value.get("format") != profile.get("report_format")
        or value.get("schema_version") != 1
        or value.get("operation_class") != "read-only"
        or value.get("profile") != profile
    ):
        raise RuntimeRecipeDiagnosticError(
            "recipe conflict comparator returned an unsupported binding"
        )
    identity = dict(value)
    comparison_id = identity.pop("comparison_id", None)
    if comparison_id != "sha256:" + sha256(_canonical_bytes(identity)).hexdigest():
        raise RuntimeRecipeDiagnosticError(
            "recipe conflict comparison identity does not match its content"
        )
    source = value.get("source")
    summary = value.get("summary")
    groups = value.get("groups")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("baseline"), dict)
        or not isinstance(source.get("candidate"), dict)
        or source["baseline"].get("diagnostic_id")
        != baseline.get("diagnostic_id")
        or source["candidate"].get("diagnostic_id")
        != candidate.get("diagnostic_id")
        or not isinstance(summary, dict)
        or not isinstance(groups, list)
        or summary.get("group_count") != len(groups)
    ):
        raise RuntimeRecipeDiagnosticError(
            "recipe conflict comparison source or summary is malformed"
        )
    expected_classifications = {
        "newly-observed": "newly_observed_group_count",
        "increased": "increased_group_count",
        "decreased": "decreased_group_count",
        "no-longer-observed": "no_longer_observed_group_count",
        "same-count": "same_count_group_count",
    }
    observed_counts = {key: 0 for key in expected_classifications}
    identities: set[tuple[Any, Any, Any]] = set()
    baseline_total = 0
    candidate_total = 0
    for group in groups:
        if not isinstance(group, dict):
            raise RuntimeRecipeDiagnosticError(
                "recipe conflict comparison contains a malformed group"
            )
        classification = group.get("classification")
        identity_key = (
            group.get("kind"),
            group.get("script_logger"),
            group.get("recipe_map"),
        )
        before = group.get("baseline_count")
        after = group.get("candidate_count")
        if (
            classification not in observed_counts
            or any(not isinstance(item, str) or not item for item in identity_key)
            or identity_key in identities
            or type(before) is not int
            or before < 0
            or type(after) is not int
            or after < 0
            or group.get("delta") != after - before
        ):
            raise RuntimeRecipeDiagnosticError(
                "recipe conflict comparison group is inconsistent"
            )
        identities.add(identity_key)
        observed_counts[classification] += 1
        baseline_total += before
        candidate_total += after
    if (
        any(
            summary.get(field) != observed_counts[classification]
            for classification, field in expected_classifications.items()
        )
        or summary.get("baseline_complete_conflict_count") != baseline_total
        or summary.get("candidate_complete_conflict_count") != candidate_total
        or summary.get("net_conflict_count_delta")
        != candidate_total - baseline_total
    ):
        raise RuntimeRecipeDiagnosticError(
            "recipe conflict comparison totals are inconsistent"
        )
    return value


def _loaded_profile(
    suite: Path,
    context: Mapping[str, Any],
    configuration: WorkbenchConfiguration,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    workspace_context = context["workspace_context"]
    return _profile_observer(
        suite,
        configuration=configuration,
        expected_profile_sha256=workspace_context["pack"][
            "document_sha256"
        ],
        expected_pack_profile_id=workspace_context["pack"][
            "profile_family_id"
        ],
    )


def diagnose_project_recipe_reload(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launch_receipt: Path | str,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Explain profile-recognized GT conflicts in one verified Groovy log."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeRecipeDiagnosticError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeRecipeDiagnosticError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    context = inspect_project(
        suite,
        workspace_root,
        configuration=active_configuration,
    )
    loaded, profile_binding, _comparison_profile = _loaded_profile(
        suite, context, active_configuration
    )
    return _diagnose_recipe_reload_receipt(
        context,
        launch_receipt,
        loaded_observer=loaded,
        profile_binding=profile_binding,
    )


def compare_project_recipe_reload(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    baseline_launch_receipt: Path | str,
    candidate_launch_receipt: Path | str,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compare two explicit, complete cold-start Groovy observations."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeRecipeDiagnosticError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeRecipeDiagnosticError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    context = inspect_project(
        suite,
        workspace_root,
        configuration=active_configuration,
    )
    loaded, profile_binding, comparison_profile = _loaded_profile(
        suite, context, active_configuration
    )
    baseline = _diagnose_recipe_reload_receipt(
        context,
        baseline_launch_receipt,
        loaded_observer=loaded,
        profile_binding=profile_binding,
    )
    candidate = _diagnose_recipe_reload_receipt(
        context,
        candidate_launch_receipt,
        loaded_observer=loaded,
        profile_binding=profile_binding,
    )
    _observer, comparator, observer_error = loaded
    try:
        result = comparator(
            baseline,
            candidate,
            profile=comparison_profile,
        )
    except observer_error as exc:
        raise RuntimeRecipeDiagnosticError(str(exc)) from exc
    return _validate_comparison(
        result,
        baseline=baseline,
        candidate=candidate,
        profile=comparison_profile,
    )


__all__ = [
    "RuntimeRecipeDiagnosticError",
    "compare_project_recipe_reload",
    "diagnose_project_recipe_reload",
]
