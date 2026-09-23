"""Human and machine command line surfaces for Workbench."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from workbench_project_intelligence import ProjectInspectionError

from .active_instance import ActiveInstanceError, initialize_active_instance
from .blueprint_stage import (
    BlueprintStageError,
    stage_material_backed_fluid,
)
from .bootstrap import discover_suite_root, inspect_project
from .component_graph import ComponentGraphError
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    ResolvedBindings,
    WorkbenchConfiguration,
    load_workbench_configuration,
)
from workbench_core.environment_status import (
    inspect_environment_status,
    render_environment_status,
)
from .material_fluid_flow import (
    MaterialFluidFlowError,
    execute_material_fluid_trial,
    plan_material_fluid_trial,
)
from workbench_core.manual_artifacts import (
    ManualArtifactError,
    inspect_manual_artifacts,
    load_manual_artifact_profile,
    manual_artifact_profile_for_configuration,
)
from .feature_studio import (
    FeatureStudioError,
    explain_feature,
    export_feature,
    inspect_feature,
    inspect_retained_feature,
    plan_feature,
    preview_feature,
    validate_feature_result,
    verify_feature,
)
from .feature_studio_snapshot import (
    open_feature_snapshot,
    project_feature_snapshot,
    refresh_feature_snapshot_file,
    validate_feature_snapshot,
)
from workbench_core.host_adapter import HostAdapterV3Error, inspect_local_host_adapter_v3
from .runtime_bootstrap import (
    RuntimeBootstrapError,
    bootstrap_project_runtime,
)
from .runtime_diagnose import (
    RuntimeDiagnosisError,
    diagnose_project_runtime,
)
from .runtime_recipe_reload_diagnostic import (
    RuntimeRecipeDiagnosticError,
    compare_project_recipe_reload,
    diagnose_project_recipe_reload,
)
from .runtime_recipe_invalidation_diagnostic import (
    RuntimeRecipeInvalidationError,
    compare_project_recipe_invalidations,
    diagnose_project_recipe_invalidations,
)
from .runtime_worldgen_audit import (
    RuntimeWorldgenAuditError,
    audit_project_worldgen,
)
from .runtime_worldgen_fingerprint import (
    RuntimeWorldgenFingerprintError,
    attribute_runtime_worldgen_blocks,
    compare_runtime_worldgen,
    fingerprint_runtime_worldgen,
)
from workbench_core.runtime_java import JavaRuntimeError, ensure_java_runtime
from .runtime_launch import RuntimeLaunchError, launch_project_runtime
from .runtime_observe import RuntimeObserveError, observe_project_runtime
from .runtime_materialize import (
    PackwizMaterializationError,
    materialize_project_runtime,
)
from .runtime_plan import RuntimePlanError, plan_project_runtime
from .registration_wizard import (
    RegistrationWizardError,
    apply_active_registration,
    plan_active_registration,
    registration_capabilities,
)
from .stdio_host import main as serve_stdio


MAX_ANSWERS_BYTES = 2 * 1024 * 1024
MAX_SERVICE_INPUT_BYTES = 4 * 1024 * 1024


# Configuration V1 is intentionally not an open-ended environment importer.
# Each supported command names exactly the physical bindings it consumes and
# the stable operation ID used in its binding digest.
CONFIGURATION_COMMAND_BINDINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "inspect": ("workspace/inspect-v1", ()),
    "runtime-plan": ("runtime/plan-v1", ()),
    "runtime-java": ("runtime/java-v1", ("java_candidate_home",)),
}


def _require_manual_artifact_preflight(
    configuration: WorkbenchConfiguration,
    workspace: Path,
    seed_roots: Sequence[Path],
) -> None:
    """Fail before provisioning/network work with an exact recovery command."""

    profile_path = manual_artifact_profile_for_configuration(configuration)
    if profile_path is None:
        return
    profile = load_manual_artifact_profile(profile_path)
    report = inspect_manual_artifacts(
        workspace,
        profile,
        seed_roots=seed_roots,
    )
    if report["state"] == "ready":
        return
    rows = [
        row
        for row in report["artifacts"]
        if row["state"] != "ready"
    ]
    detail = "; ".join(
        f"{row['display_name']} [{row['state']}] -> "
        f"{row['download_page_url']} -> <seed-root>/{row['output_path']}"
        for row in rows
    )
    raise PackwizMaterializationError(
        "manual Packwiz file preflight is not ready; no provisioning or download "
        f"was started. {detail}. Verify with: workbench runtime preflight "
        f"{report['workspace']} --profile {report['project_id']} --seed SEED_ROOT, "
        "then rerun this command with --seed SEED_ROOT"
    )


def _configuration_manifest_record(
    configuration: WorkbenchConfiguration,
) -> dict[str, Any]:
    return {
        "path_uri": configuration.manifest.path.as_uri(),
        "suite_relative_path": configuration.manifest.relative_path,
        "sha256": configuration.manifest.sha256,
    }


def _configuration_selection_record(
    configuration: WorkbenchConfiguration,
) -> dict[str, Any]:
    return {
        "selection_digest": configuration.selection_digest,
        "pack": {
            "profile_id": configuration.pack_profile_id,
            "variant": configuration.pack_variant,
            "document": {
                "suite_relative_path": (
                    configuration.pack_document.source.relative_path
                ),
                "sha256": configuration.pack_document.source.sha256,
            },
        },
        "platform": {
            "profile_id": configuration.platform_profile_id,
            "document": {
                "suite_relative_path": (
                    configuration.platform_document.source.relative_path
                ),
                "sha256": configuration.platform_document.source.sha256,
            },
        },
    }


def _configuration_declaration_records(
    configuration: WorkbenchConfiguration,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for declaration in configuration.binding_declarations:
        source: dict[str, str] = {"kind": declaration.kind}
        if declaration.kind == "environment":
            source["variable"] = declaration.value
        else:
            source["path"] = declaration.value
        records.append({"name": declaration.name, "source": source})
    return records


def _configuration_validation_result(
    configuration: WorkbenchConfiguration,
) -> dict[str, Any]:
    """Describe strict configuration validity without reading host bindings."""

    return {
        "format": "workbench-configuration-validation-v1",
        "schema_version": 1,
        "outcome": "valid",
        "configuration_schema": configuration.schema,
        "manifest": _configuration_manifest_record(configuration),
        "selection": _configuration_selection_record(configuration),
        "binding_declarations": _configuration_declaration_records(configuration),
    }


def _configuration_resolution_result(
    configuration: WorkbenchConfiguration,
    resolved: ResolvedBindings,
    requested_command: str,
) -> dict[str, Any]:
    """Project one immutable binding snapshot for one closed command ID."""

    try:
        operation_id, consumed_names = CONFIGURATION_COMMAND_BINDINGS[
            requested_command
        ]
    except KeyError as exc:
        raise ValueError(
            f"unsupported configuration command: {requested_command}"
        ) from exc
    consumed = frozenset(consumed_names)
    bindings: list[dict[str, Any]] = []
    for entry in resolved.entries:
        source: dict[str, str] = {"kind": entry.source}
        if entry.environment_variable is not None:
            source["variable"] = entry.environment_variable
        bindings.append(
            {
                "name": entry.name,
                "consumed": entry.name in consumed,
                "state": "bound" if entry.is_bound else "unbound",
                "source": source,
                "value": entry.value,
            }
        )
    return {
        "format": "workbench-configuration-resolution-v1",
        "schema_version": 1,
        "outcome": "resolved",
        "configuration_schema": configuration.schema,
        "manifest": _configuration_manifest_record(configuration),
        "selection": _configuration_selection_record(configuration),
        "command": {
            "requested_for": requested_command,
            "operation_id": operation_id,
            "consumed_bindings": list(consumed_names),
        },
        "bindings": bindings,
        "operation_binding_digest": resolved.operation_digest(
            operation_id,
            consumed_names,
        ),
    }


def _human_configuration(result: dict[str, Any]) -> str:
    selection = result["selection"]
    pack = selection["pack"]
    platform = selection["platform"]
    lines = [
        f"Configuration: {result['outcome']} ({result['configuration_schema']})",
        (
            "Selection: "
            f"{pack['profile_id']} / {pack['variant']} -> "
            f"{platform['profile_id']}"
        ),
        f"Selection digest: {selection['selection_digest']}",
        f"Manifest SHA-256: {result['manifest']['sha256']}",
    ]
    if result["format"] == "workbench-configuration-resolution-v1":
        command = result["command"]
        lines.extend(
            (
                (
                    "Command: "
                    f"{command['requested_for']} ({command['operation_id']})"
                ),
                f"Operation binding digest: {result['operation_binding_digest']}",
            )
        )
        for binding in result["bindings"]:
            source = binding["source"]
            source_label = source["kind"]
            if "variable" in source:
                source_label += f" {source['variable']}"
            consumption = "consumed" if binding["consumed"] else "not consumed"
            value = binding["value"] if binding["value"] is not None else "<unbound>"
            lines.append(
                f"Binding {binding['name']}: {value} "
                f"({source_label}; {consumption})"
            )
    else:
        lines.append(
            "Binding declarations: "
            f"{len(result['binding_declarations'])} (host values not resolved)"
        )
    return "\n".join(lines)


def _load_service_bytes(path: Path, label: str) -> bytes:
    try:
        source = path.expanduser().resolve(strict=True)
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"{label} must be a regular file")
        with source.open("rb") as stream:
            raw = stream.read(MAX_SERVICE_INPUT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not raw or len(raw) > MAX_SERVICE_INPUT_BYTES:
        raise ValueError(f"{label} exceeds the service input byte limit")
    return raw


def _load_service_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            _load_service_bytes(path, label).decode("utf-8", errors="strict")
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot decode {label}: {exc}") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be one JSON object")
    return value


def _parser(*, feature_program: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench",
        description="Integrate Workbench projects around a real development workspace.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_manifest_argument(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--config",
            dest="config_path",
            type=Path,
            default=CONFIGURATION_PATH,
            help="current Workbench manifest, relative to --suite-root by default",
        )

    environment = subcommands.add_parser(
        "environment",
        help="inspect Workbench execution and Pixi materialization without changing it",
    )
    environment_actions = environment.add_subparsers(
        dest="environment_action",
        required=True,
    )
    environment_status = environment_actions.add_parser(
        "status",
        help="report execution provenance, identities, footprint, and repair guidance",
    )
    environment_status.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source or verified packaged suite root",
    )
    environment_status.add_argument(
        "--json",
        action="store_true",
        help="emit the complete read-only environment status record",
    )

    configuration = subcommands.add_parser(
        "config",
        help="validate or resolve the one current Workbench configuration schema",
    )
    configuration_actions = configuration.add_subparsers(
        dest="configuration_action",
        required=True,
    )

    def add_configuration_location(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--suite-root",
            type=Path,
            help="Workbench source root; discovered automatically when omitted",
        )
        command.add_argument(
            "--config",
            dest="config_path",
            type=Path,
            default=CONFIGURATION_PATH,
            help=(
                "manifest path; relative paths are resolved against --suite-root "
                "(default: workbench.toml)"
            ),
        )
        command.add_argument(
            "--json",
            action="store_true",
            help="emit the complete configuration record",
        )

    configuration_validate = configuration_actions.add_parser(
        "validate",
        help="validate selection and declarations without resolving host values",
    )
    add_configuration_location(configuration_validate)

    configuration_resolve = configuration_actions.add_parser(
        "resolve",
        help="snapshot bindings for one explicitly supported command",
    )
    configuration_resolve.add_argument(
        "--for",
        dest="configuration_command",
        required=True,
        choices=tuple(CONFIGURATION_COMMAND_BINDINGS),
        metavar="COMMAND",
        help="command whose closed binding set should be resolved",
    )
    add_configuration_location(configuration_resolve)

    host_status = subcommands.add_parser(
        "host-status",
        help="inspect host capabilities without an OS allowlist",
    )
    host_status.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; accepted for source-router consistency",
    )
    host_status.add_argument(
        "--scratch-parent",
        type=Path,
        help=(
            "existing directory in which to create and remove the disposable "
            "conformance fixture"
        ),
    )
    host_status.add_argument(
        "--json",
        action="store_true",
        help="emit the complete identity-bound receipt for the selected adapter",
    )

    service_call = subcommands.add_parser(
        "service-call",
        help="send one exact authenticated V3 request sequence to the local service",
    )
    service_call.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; accepted for source-router consistency",
    )
    service_call.add_argument("--endpoint", type=Path, required=True)
    service_call.add_argument("--credential", type=Path, required=True)
    service_call.add_argument(
        "--messages",
        type=Path,
        required=True,
        help="JSON array beginning with service/initialize",
    )
    service_call.add_argument(
        "--json",
        action="store_true",
        help="emit the complete canonical exchange receipt",
    )

    service_host = subcommands.add_parser(
        "service-host-v3",
        help=argparse.SUPPRESS,
    )
    service_host.add_argument("--suite-root", type=Path)
    service_host.add_argument("--service-root", type=Path, required=True)
    service_host.add_argument("--endpoint", type=Path, required=True)
    service_host.add_argument("--ready-file", type=Path, required=True)
    service_host.add_argument("--process-nonce", required=True)

    service_probe = subcommands.add_parser(
        "service-probe-v3",
        help=argparse.SUPPRESS,
    )
    service_probe.add_argument("--suite-root", type=Path)
    service_probe.add_argument("--endpoint", type=Path, required=True)
    service_probe.add_argument("--credential", type=Path, required=True)
    service_probe.add_argument("--json", action="store_true")

    feature_service = subcommands.add_parser(
        "feature-service",
        help=(
            "submit, resume, cancel, or reopen Feature Studio work through "
            "the installed Service V3 endpoint"
        ),
    )
    feature_service_actions = feature_service.add_subparsers(
        dest="feature_service_action",
        required=True,
    )

    def add_feature_service_connection(command: argparse.ArgumentParser) -> None:
        command.add_argument("--suite-root", type=Path)
        command.add_argument("--endpoint", type=Path, required=True)
        command.add_argument("--credential", type=Path, required=True)
        command.add_argument("--json", action="store_true")

    feature_service_submit = feature_service_actions.add_parser(
        "submit",
        help="register one exact context and submit one closed owner request",
    )
    add_feature_service_connection(feature_service_submit)
    feature_service_submit.add_argument("--context-ref", type=Path, required=True)
    feature_service_submit.add_argument(
        "--input-binding", type=Path, required=True
    )
    feature_service_submit.add_argument("--request", type=Path, required=True)

    feature_service_events = feature_service_actions.add_parser(
        "events",
        help="start or resume one lossless durable-job event subscription",
    )
    add_feature_service_connection(feature_service_events)
    for option in (
        "context-ref-id",
        "input-binding-id",
        "job-id",
        "job-submission-id",
    ):
        feature_service_events.add_argument(f"--{option}", required=True)
    feature_service_events.add_argument("--subscription-id")
    feature_service_events.add_argument("--stream-generation")
    feature_service_events.add_argument("--last-cursor", type=int, default=-1)
    feature_service_events.add_argument(
        "--maximum-events", type=int, default=64
    )

    feature_service_cancel = feature_service_actions.add_parser(
        "cancel",
        help="request cancellation against one exact durable-job event head",
    )
    add_feature_service_connection(feature_service_cancel)
    for option in (
        "context-ref-id",
        "input-binding-id",
        "job-id",
        "job-submission-id",
        "expected-event-id",
    ):
        feature_service_cancel.add_argument(f"--{option}", required=True)
    feature_service_cancel.add_argument(
        "--expected-event-ordinal", type=int, required=True
    )
    feature_service_cancel.add_argument("--reason", required=True)

    feature_service_result = feature_service_actions.add_parser(
        "result",
        help="reopen the exact owner result of one successful job",
    )
    add_feature_service_connection(feature_service_result)
    for option in ("context-ref-id", "input-binding-id", "job-id"):
        feature_service_result.add_argument(f"--{option}", required=True)

    inspect = subcommands.add_parser(
        "inspect",
        help="recognize and describe a development project",
    )
    inspect.add_argument("workspace", type=Path)
    inspect.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    inspect.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=CONFIGURATION_PATH,
        help="current Workbench manifest, relative to --suite-root by default",
    )
    inspect.add_argument(
        "--json",
        action="store_true",
        help="emit the canonical composed JSON result",
    )

    initialize = subcommands.add_parser(
        "initialize",
        help="select an installed Cleanroom instance for direct construction",
    )
    initialize.add_argument("workspace", type=Path)
    initialize.add_argument(
        "--instance",
        required=True,
        type=Path,
        help="installed Prism/MultiMC instance root or its Minecraft payload",
    )
    initialize.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    add_manifest_argument(initialize)
    initialize.add_argument(
        "--state-root",
        type=Path,
        help=(
            "Workbench state store; defaults to ignored suite .workbench in a "
            "source checkout and per-user runtime state in an installed package"
        ),
    )
    initialize.add_argument(
        "--json",
        action="store_true",
        help="emit the selected runtime identity",
    )

    register = subcommands.add_parser(
        "register",
        help="run a profile-backed wizard against the selected active instance",
    )
    register.add_argument("workspace", type=Path)
    register.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    add_manifest_argument(register)
    register.add_argument(
        "--pattern",
        help="registration pattern key; selected interactively when omitted",
    )
    register.add_argument(
        "--answers",
        type=Path,
        help="JSON answer object, or '-' for standard input",
    )
    register.add_argument(
        "--list",
        action="store_true",
        help="list investigated families and executable wizard patterns",
    )
    register.add_argument(
        "--apply",
        action="store_true",
        help="apply the previewed edits directly to the active instance",
    )
    register.add_argument(
        "--yes",
        action="store_true",
        help="confirm --apply non-interactively",
    )
    register.add_argument(
        "--state-root",
        type=Path,
        help=(
            "Workbench state store; defaults to ignored suite .workbench in a "
            "source checkout and per-user runtime state in an installed package"
        ),
    )
    register.add_argument(
        "--json",
        action="store_true",
        help="emit the catalog, plan, or application result",
    )

    blueprint_stage = subcommands.add_parser(
        "blueprint-stage",
        help=(
            "plan a material-backed fluid and stage it in a disposable "
            "pack workspace"
        ),
    )
    blueprint_stage.add_argument("workspace", type=Path)
    blueprint_stage.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    blueprint_stage.add_argument(
        "--name",
        required=True,
        help="developer-facing material name",
    )
    blueprint_stage.add_argument(
        "--color",
        required=True,
        help="six-digit lowercase hexadecimal color, for example 0x425d73",
    )
    blueprint_stage.add_argument(
        "--translation",
        help="English display name; defaults to --name",
    )
    blueprint_stage.add_argument(
        "--symbol",
        help=(
            "Groovy static-field symbol; defaults to PascalCase --name "
            "and should be supplied for pack-specific acronym casing"
        ),
    )
    blueprint_stage.add_argument(
        "--json",
        action="store_true",
        help="emit the staging result and retained receipt",
    )

    material_fluid = subcommands.add_parser(
        "material-fluid",
        help="plan or run one material-backed fluid in a disposable client",
    )
    material_fluid_actions = material_fluid.add_subparsers(
        dest="material_fluid_action",
        required=True,
    )

    def add_material_fluid_inputs(command: argparse.ArgumentParser) -> None:
        command.add_argument("workspace", type=Path)
        command.add_argument(
            "--suite-root",
            type=Path,
            help="Workbench source root; discovered automatically when omitted",
        )
        command.add_argument("--name", required=True, help="developer-facing material name")
        command.add_argument(
            "--color",
            required=True,
            help="six-digit lowercase hexadecimal color, for example 0x425d73",
        )
        command.add_argument("--translation", help="English label; defaults to --name")
        command.add_argument(
            "--symbol",
            help="Groovy static-field symbol; defaults to PascalCase --name",
        )
        command.add_argument(
            "--launcher",
            choices=("prism", "multimc"),
            default="prism",
        )

    material_fluid_plan = material_fluid_actions.add_parser(
        "plan",
        help="show the exact three-file edit and disposable execution boundary",
    )
    add_material_fluid_inputs(material_fluid_plan)
    material_fluid_plan.add_argument(
        "--json",
        action="store_true",
        help="emit the complete read-only plan",
    )

    material_fluid_run = material_fluid_actions.add_parser(
        "run",
        help="stage and cold-start one reviewed plan in a fresh projection",
    )
    add_material_fluid_inputs(material_fluid_run)
    material_fluid_run.add_argument(
        "--plan-id",
        help="exact plan ID reviewed through material-fluid plan",
    )
    material_fluid_run.add_argument(
        "--show",
        action="store_true",
        help="show or revalidate the plan without changing state",
    )
    material_fluid_run.add_argument(
        "--launcher-executable",
        required=True,
        type=Path,
        help="Prism Launcher or MultiMC executable",
    )
    material_fluid_run.add_argument(
        "--launcher-root",
        required=True,
        type=Path,
        help="launcher data root receiving one fresh disposable instance",
    )
    material_fluid_run.add_argument("--launcher-java", type=Path)
    material_fluid_run.add_argument("--launcher-java-state", type=Path)
    material_fluid_run.add_argument(
        "--launcher-profile",
        help="configured launcher profile; redacted from retained evidence",
    )
    material_fluid_run.add_argument("--packwiz", type=Path)
    material_fluid_run.add_argument(
        "--seed",
        action="append",
        type=Path,
        default=[],
        help="hash-matched Packwiz seed root; repeatable and read-only",
    )
    material_fluid_run.add_argument("--memory-mib", type=int, default=8192)
    material_fluid_run.add_argument("--offline-name", default="Workbench")
    material_fluid_run.add_argument("--launch-timeout", type=float, default=600.0)
    material_fluid_run.add_argument("--attach-timeout", type=float, default=120.0)
    material_fluid_run.add_argument(
        "--session-timeout",
        type=float,
        default=21_600.0,
    )
    material_fluid_run.add_argument(
        "--json",
        action="store_true",
        help="emit the plan with --show or the complete retained run result",
    )

    feature = subcommands.add_parser(
        "feature",
        prog=feature_program,
        help="inspect, plan, review, verify, explain, or export one Feature Studio workspace",
    )
    feature_actions = feature.add_subparsers(dest="feature_action", required=True)

    def add_feature_inputs(
        command: argparse.ArgumentParser,
        *,
        optional_source: bool = False,
    ) -> None:
        command.add_argument("workspace", type=Path, nargs="?" if optional_source else None)
        command.add_argument(
            "--suite-root",
            type=Path,
            help="Workbench source root; discovered automatically when omitted",
        )
        command.add_argument("--name", required=not optional_source, help="developer-facing material name")
        command.add_argument(
            "--color",
            required=not optional_source,
            help="six-digit lowercase hexadecimal color, for example 0x425d73",
        )
        command.add_argument("--translation", help="English label; defaults to --name")
        command.add_argument(
            "--symbol",
            help="Groovy static-field symbol; defaults to PascalCase --name",
        )
        command.add_argument(
            "--launcher",
            choices=("prism", "multimc"),
            default="prism",
        )

    feature_inspect = feature_actions.add_parser(
        "inspect",
        help="open the exact Materials & Recipes workspace projection",
    )
    add_feature_inputs(feature_inspect, optional_source=True)
    feature_inspect.add_argument(
        "--receipt",
        type=Path,
        help="select an exact retained material-flow V2 receipt instead of a new source plan",
    )
    feature_inspect.add_argument(
        "--result",
        type=Path,
        help="select an exact retained Feature Studio result; requires --snapshot",
    )
    feature_inspect.add_argument(
        "--snapshot",
        action="store_true",
        help="emit the closed read-only Feature Studio snapshot V2 for native clients",
    )
    feature_inspect.add_argument(
        "--refresh-snapshot",
        type=Path,
        help="refresh one retained snapshot through its immutable origin; requires --snapshot",
    )
    feature_inspect.add_argument("--json", action="store_true")

    for action, help_text in (
        ("plan", "produce the exact owner-bound construction plan"),
        ("explain", "show owner-backed state, limitations, and next actions"),
    ):
        command = feature_actions.add_parser(action, help=help_text)
        add_feature_inputs(command)
        command.add_argument("--json", action="store_true")

    feature_verify = feature_actions.add_parser(
        "verify",
        help="execute one reviewed plan in a fresh disposable runtime",
    )
    add_feature_inputs(feature_verify)
    feature_verify.add_argument("--plan-id", required=True)
    feature_verify.add_argument("--show", action="store_true")
    feature_verify.add_argument("--launcher-executable", required=True, type=Path)
    feature_verify.add_argument("--launcher-root", required=True, type=Path)
    feature_verify.add_argument("--launcher-java", type=Path)
    feature_verify.add_argument("--launcher-java-state", type=Path)
    feature_verify.add_argument("--launcher-profile")
    feature_verify.add_argument("--packwiz", type=Path)
    feature_verify.add_argument("--seed", action="append", type=Path, default=[])
    feature_verify.add_argument("--memory-mib", type=int, default=8192)
    feature_verify.add_argument("--offline-name", default="Workbench")
    feature_verify.add_argument("--launch-timeout", type=float, default=600.0)
    feature_verify.add_argument("--attach-timeout", type=float, default=120.0)
    feature_verify.add_argument("--session-timeout", type=float, default=21_600.0)
    feature_verify.add_argument("--state-root", type=Path)
    feature_verify.add_argument("--json", action="store_true")

    feature_export = feature_actions.add_parser(
        "export",
        help="write one exact reviewed patch bundle without modifying source",
    )
    add_feature_inputs(feature_export)
    feature_export.add_argument("--plan-id", required=True)
    feature_export.add_argument("--output", required=True, type=Path)
    feature_export.add_argument("--show", action="store_true")
    feature_export.add_argument("--json", action="store_true")

    runtime_plan = subcommands.add_parser(
        "runtime-plan",
        help="plan a disposable Cleanroom runtime without writing it",
    )
    runtime_plan.add_argument("workspace", type=Path)
    runtime_plan.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_plan.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=CONFIGURATION_PATH,
        help="current Workbench manifest, relative to --suite-root by default",
    )
    runtime_plan.add_argument(
        "--side",
        choices=("client", "server"),
        default="client",
    )
    runtime_plan.add_argument(
        "--launcher",
        choices=("prism", "multimc", "dedicated-server"),
        help="defaults to Prism for client and dedicated-server for server",
    )
    runtime_plan.add_argument(
        "--json",
        action="store_true",
        help="emit the canonical runtime plan",
    )

    runtime_bootstrap = subcommands.add_parser(
        "runtime-bootstrap",
        help="materialize a verified Cleanroom client base under managed Workbench state",
    )
    runtime_bootstrap.add_argument("workspace", type=Path)
    runtime_bootstrap.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    add_manifest_argument(runtime_bootstrap)
    runtime_bootstrap.add_argument(
        "--launcher",
        choices=("prism", "multimc"),
        default="prism",
        help="select the compatible launcher format; defaults to Prism",
    )
    runtime_bootstrap.add_argument(
        "--json",
        action="store_true",
        help="emit the bootstrap result and retained receipt",
    )

    runtime_java = subcommands.add_parser(
        "runtime-java",
        help="discover or provision the exact profile-selected Temurin runtime",
    )
    runtime_java.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_java.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=CONFIGURATION_PATH,
        help="current Workbench manifest, relative to --suite-root by default",
    )
    runtime_java.add_argument(
        "--json",
        action="store_true",
        help="emit the discovery or provision result",
    )

    runtime_materialize = subcommands.add_parser(
        "runtime-materialize",
        help=(
            "refresh a disposable Packwiz source copy and install its "
            "Cleanroom client payload"
        ),
    )
    runtime_materialize.add_argument("workspace", type=Path)
    runtime_materialize.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    add_manifest_argument(runtime_materialize)
    runtime_materialize.add_argument(
        "--launcher",
        choices=("prism", "multimc"),
        default="prism",
        help="select the compatible launcher format; defaults to Prism",
    )
    runtime_materialize.add_argument(
        "--packwiz",
        type=Path,
        help=(
            "Packwiz executable; defaults to a workspace copy and then PATH"
        ),
    )
    runtime_materialize.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help=(
            "existing Minecraft root supplying hash-matched files; "
            "repeatable"
        ),
    )
    runtime_materialize.add_argument(
        "--json",
        action="store_true",
        help="emit the materialization result and retained receipt",
    )

    runtime_launch = subcommands.add_parser(
        "runtime-launch",
        help="project and launch a populated Cleanroom client",
    )
    runtime_launch.add_argument("workspace", type=Path)
    runtime_launch.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_launch.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=CONFIGURATION_PATH,
        help="current Workbench manifest, relative to --suite-root by default",
    )
    runtime_launch.add_argument(
        "--launcher",
        choices=("prism", "multimc"),
        default="prism",
        help="select the compatible launcher adapter; defaults to Prism",
    )
    runtime_launch.add_argument(
        "--launcher-executable",
        required=True,
        type=Path,
        help="Prism Launcher or MultiMC executable",
    )
    runtime_launch.add_argument(
        "--launcher-root",
        required=True,
        type=Path,
        help="launcher data root receiving the disposable instance",
    )
    runtime_launch.add_argument(
        "--launcher-java",
        type=Path,
        help=(
            "exact launcher-host Temurin candidate; provisioned when omitted "
            "or incompatible"
        ),
    )
    runtime_launch.add_argument(
        "--launcher-java-state",
        type=Path,
        help="managed launcher-host Java store; defaults by execution host",
    )
    runtime_launch.add_argument(
        "--launcher-profile",
        help=(
            "configured launcher profile used for its ownership gate; "
            "redacted from retained evidence"
        ),
    )
    runtime_launch.add_argument(
        "--packwiz",
        type=Path,
        help="Packwiz executable forwarded to runtime materialization",
    )
    runtime_launch.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help="hash-matched Packwiz seed root; repeatable",
    )
    runtime_launch.add_argument(
        "--memory-mib",
        type=int,
        default=8192,
        help="maximum client heap in MiB; defaults to 8192",
    )
    runtime_launch.add_argument(
        "--offline-name",
        default="Workbench",
        help=(
            "offline player name used only when --launcher-profile is "
            "omitted"
        ),
    )
    runtime_launch.add_argument(
        "--compatibility-patch",
        action="append",
        default=[],
        type=Path,
        help=(
            "explicit disposable compatibility patch JSON; repeatable and "
            "never applied to the canonical materialization"
        ),
    )
    runtime_launch.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="seconds to wait for the client-loaded checkpoint",
    )
    runtime_launch.add_argument(
        "--json",
        action="store_true",
        help="emit the launch observation and retained receipt",
    )

    runtime_observe = subcommands.add_parser(
        "runtime-observe",
        help="launch, observe through client exit, and analyze final logs",
    )
    runtime_observe.add_argument("workspace", type=Path)
    runtime_observe.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_observe.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=CONFIGURATION_PATH,
        help="current Workbench manifest, relative to --suite-root by default",
    )
    runtime_observe.add_argument(
        "--launcher",
        choices=("prism", "multimc"),
        default="prism",
        help="select the compatible launcher adapter; defaults to Prism",
    )
    runtime_observe.add_argument(
        "--launcher-executable",
        required=True,
        type=Path,
        help="Prism Launcher or MultiMC executable",
    )
    runtime_observe.add_argument(
        "--launcher-root",
        required=True,
        type=Path,
        help="launcher data root receiving the disposable instance",
    )
    runtime_observe.add_argument(
        "--launcher-java",
        type=Path,
        help="exact launcher-host Temurin candidate",
    )
    runtime_observe.add_argument(
        "--launcher-java-state",
        type=Path,
        help="managed launcher-host Java store; defaults by execution host",
    )
    runtime_observe.add_argument(
        "--launcher-profile",
        help="configured launcher profile; redacted from retained evidence",
    )
    runtime_observe.add_argument(
        "--packwiz",
        type=Path,
        help="Packwiz executable forwarded to runtime materialization",
    )
    runtime_observe.add_argument(
        "--seed",
        action="append",
        default=[],
        type=Path,
        help="hash-matched Packwiz seed root; repeatable",
    )
    runtime_observe.add_argument(
        "--memory-mib",
        type=int,
        default=8192,
        help="maximum client heap in MiB; defaults to 8192",
    )
    runtime_observe.add_argument(
        "--offline-name",
        default="Workbench",
        help="offline player name when no launcher profile is supplied",
    )
    runtime_observe.add_argument(
        "--compatibility-patch",
        action="append",
        default=[],
        type=Path,
        help="identity-bound disposable compatibility patch; repeatable",
    )
    runtime_observe.add_argument(
        "--launch-timeout",
        type=float,
        default=600.0,
        help="seconds to wait for the client-loaded checkpoint",
    )
    runtime_observe.add_argument(
        "--attach-timeout",
        type=float,
        default=120.0,
        help="seconds to bind the exact projected Java process",
    )
    runtime_observe.add_argument(
        "--session-timeout",
        type=float,
        default=21600.0,
        help="maximum observed client session in seconds; defaults to 6 hours",
    )
    runtime_observe.add_argument(
        "--json",
        action="store_true",
        help="emit the session, final diagnosis, and worldgen audit",
    )

    runtime_diagnose = subcommands.add_parser(
        "runtime-diagnose",
        help="explain retained runtime evidence without changing it",
    )
    runtime_diagnose.add_argument("workspace", type=Path)
    runtime_diagnose.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    add_manifest_argument(runtime_diagnose)
    runtime_diagnose.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help="retained Workbench runtime-launch receipt",
    )
    runtime_diagnose.add_argument(
        "--artifact-root",
        action="append",
        default=[],
        type=Path,
        help=(
            "instance, Minecraft, or mods root for artifact inspection; "
            "repeatable and otherwise inferred from the receipt"
        ),
    )
    runtime_diagnose.add_argument(
        "--baseline-receipt",
        type=Path,
        help=(
            "explicit baseline runtime-launch V3 receipt for a recipe "
            "comparison; requires --recipe-invalidations or --recipe-reload"
        ),
    )
    runtime_diagnose.add_argument(
        "--recipe-invalidations",
        action="store_true",
        help=(
            "run the preview Supersymmetry recipe feature over separate "
            "Groovy postInit and GT startup/latest.log channels"
        ),
    )
    runtime_diagnose.add_argument(
        "--recipe-reload",
        action="store_true",
        help=(
            "emit the experimental profile-owned Groovy postInit/GT conflict "
            "diagnostic instead of runtime diagnosis V2"
        ),
    )
    runtime_diagnose.add_argument(
        "--json",
        action="store_true",
        help="emit the structured runtime diagnostic or comparison",
    )

    runtime_worldgen_audit = subcommands.add_parser(
        "runtime-worldgen-audit",
        help="audit retained RTG and biome-generation evidence without changing it",
    )
    runtime_worldgen_audit.add_argument("workspace", type=Path)
    runtime_worldgen_audit.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    add_manifest_argument(runtime_worldgen_audit)
    runtime_worldgen_audit.add_argument(
        "--runtime-root",
        required=True,
        type=Path,
        help="retained Minecraft or dedicated-server runtime root",
    )
    runtime_worldgen_audit.add_argument(
        "--json",
        action="store_true",
        help="emit the structured world-generation audit",
    )

    runtime_worldgen_fingerprint = subcommands.add_parser(
        "runtime-worldgen-fingerprint",
        help="fingerprint generation-relevant fields in one stopped Anvil world",
    )
    runtime_worldgen_fingerprint.add_argument("world", type=Path)
    runtime_worldgen_fingerprint.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_worldgen_fingerprint.add_argument(
        "--output",
        type=Path,
        help="new JSON evidence path; existing files are never overwritten",
    )
    runtime_worldgen_fingerprint.add_argument(
        "--json",
        action="store_true",
        help="emit the structured Atlas fingerprint",
    )

    runtime_worldgen_compare = subcommands.add_parser(
        "runtime-worldgen-compare",
        help="compare two identity-verified Atlas worldgen fingerprints",
    )
    runtime_worldgen_compare.add_argument("left", type=Path)
    runtime_worldgen_compare.add_argument("right", type=Path)
    runtime_worldgen_compare.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_worldgen_compare.add_argument(
        "--output",
        type=Path,
        help="new JSON evidence path; existing files are never overwritten",
    )
    runtime_worldgen_compare.add_argument(
        "--json",
        action="store_true",
        help="emit the structured Atlas comparison",
    )

    runtime_worldgen_block_delta = subcommands.add_parser(
        "runtime-worldgen-block-delta",
        help="attribute exact block-state changes between two stopped worlds",
    )
    runtime_worldgen_block_delta.add_argument("left_world", type=Path)
    runtime_worldgen_block_delta.add_argument("right_world", type=Path)
    runtime_worldgen_block_delta.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    runtime_worldgen_block_delta.add_argument(
        "--output",
        type=Path,
        help="new JSON evidence path; existing files are never overwritten",
    )
    runtime_worldgen_block_delta.add_argument(
        "--json",
        action="store_true",
        help="emit the structured Atlas block-delta observation",
    )

    serve = subcommands.add_parser(
        "serve",
        help="run the JSON-RPC standard-I/O host",
    )
    serve.add_argument(
        "--suite-root",
        type=Path,
        help="Workbench source root; discovered automatically when omitted",
    )
    serve.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=CONFIGURATION_PATH,
        help="current Workbench manifest, relative to --suite-root by default",
    )
    return parser


def _human_inspection(result: dict[str, Any]) -> str:
    context = result["workspace_context"]
    workspace = context["workspace"]
    project = context["project"]
    platform = context["platform"]
    pack = context["pack"]
    loaders = ", ".join(
        f"{loader['id']} {loader['version']}"
        for loader in project["loaders"]
    )
    index_state = (
        "matches pack.toml"
        if project["index"]["matches_declared_hash"]
        else "DIFFERS FROM pack.toml"
    )
    dirty_state = "dirty" if workspace["dirty"] else "clean"
    return "\n".join(
        (
            f"Project: {project['name']} {project['version']}",
            f"Workspace: {workspace['root_uri']} ({dirty_state})",
            f"Revision: {workspace['revision']}",
            (
                f"Source: Minecraft {project['minecraft_version']}; "
                f"{loaders}; {project['pack_format']}"
            ),
            (
                f"Target: {platform['profile_id']} "
                f"({platform['cleanroom_version']})"
            ),
            (
                f"Pack profile: {pack['selected_profile']} "
                f"({pack['maturity']})"
            ),
            f"Packwiz index: {index_state}",
        )
    )


def _human_host_status(result: dict[str, Any]) -> str:
    adapter = result["adapter"]
    eligibility = "ELIGIBLE" if result["core_host_eligible"] else "ATTENTION"
    lines = [
        f"Workbench core host: {eligibility}",
        f"Adapter: {adapter['adapter_id']}",
        (
            "Environment: "
            f"{adapter['system_label']} {adapter['system_release']} / "
            f"{adapter['machine_label']} / Python {adapter['python_version']}"
        ),
        f"Receipt: {result['receipt_id']}",
        "Capabilities:",
    ]
    for row in result["capabilities"]:
        lines.append(f"  - {row['id']}: {row['state'].upper()}")
        if row["state"] != "available":
            limitation = row.get("limitation")
            if limitation:
                lines.append(f"      why: {limitation}")
            next_action = row.get("next_safe_action")
            if next_action:
                lines.append(f"      next: {next_action}")
    executable = result.get("active_executable")
    if isinstance(executable, dict):
        lines.extend(
            (
                f"Executable: {executable['resolved_uri']}",
                (
                    "Executable identity: "
                    f"sha256:{executable['sha256']} "
                    f"({executable['size_bytes']} bytes)"
                ),
            )
        )
    operations = result.get("operation_states")
    if isinstance(operations, list):
        lines.append("Operations:")
        for row in operations:
            missing = row["missing_capability_ids"]
            suffix = f" (missing: {', '.join(missing)})" if missing else ""
            lines.append(f"  - {row['id']}: {row['state'].upper()}{suffix}")
    if result["missing_core_capabilities"]:
        lines.append(
            "Missing core capabilities: "
            + ", ".join(result["missing_core_capabilities"])
        )
    lines.extend(
        (
            "Release qualified: NO",
            f"Qualification boundary: {result['qualification_limitation']}",
            f"Next safe action: {result['next_safe_action']}",
        )
    )
    return "\n".join(lines)


def _human_active_instance(result: dict[str, Any]) -> str:
    selection = result["selection"]
    instance = selection["instance"]
    platform = selection["platform"]
    markers = ", ".join(
        f"{row['id']} ({row['path']})"
        for row in instance["identity"]["runtime_markers"]
    )
    return "\n".join((
        f"Active instance: {result['outcome'].upper()}",
        f"Selection: {selection['selection_id']}",
        f"Workspace: {selection['workspace']['root_uri']}",
        f"Instance: {instance['root_uri']}",
        f"Minecraft payload: {instance['payload_root_uri']}",
        (
            f"Runtime: Minecraft {platform['minecraft_version']}; "
            f"Cleanroom {platform['cleanroom_version']}"
        ),
        f"Required mods: {markers}",
        f"Selection record: {result['selection_uri']}",
    ))


def _human_registration_capabilities(result: dict[str, Any]) -> str:
    lines = [
        f"Registration catalog: {result['catalog_id']}",
        (
            "Active instance: "
            f"{result['active_instance']['instance']['payload_root_uri']}"
        ),
        "Authority precedence:",
    ]
    lines.extend(
        f"  {row['rank']}. {row['id']}: {row['description']}"
        for row in result["authority_order"]
    )
    lines.append("Executable wizard patterns:")
    lines.extend(
        (
            f"  - {row['key']} [{row['lifecycle']}]: "
            f"{row['summary']}"
        )
        for row in result["patterns"]
    )
    lines.append("Investigated registration families:")
    lines.extend(
        (
            f"  - {row['id']} [{row['active_instance_support']}; "
            f"{row['selected_authority']}]: {row['description']}"
        )
        for row in result["families"]
    )
    return "\n".join(lines)


def _human_registration_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"Registration plan: {plan['state'].upper()}",
        f"Plan: {plan['plan_id']}",
        (
            f"Pattern: {plan['pattern']['key']} "
            f"[{plan['pattern']['lifecycle']}]"
        ),
        f"Active payload: {plan['active_instance']['payload_root_uri']}",
        "Direct active-instance edits:",
    ]
    for operation in plan["operations"]:
        lines.append(f"  - {operation['path']}")
        lines.append(operation["diff"].rstrip())
    if plan["outstanding_checks"]:
        lines.append("Required follow-up checks:")
        lines.extend(f"  - {item}" for item in plan["outstanding_checks"])
    lines.append("No files were changed by this preview.")
    return "\n".join(lines)


def _human_registration_result(result: dict[str, Any]) -> str:
    plan = result["plan"]
    receipt = result["receipt"]
    lines = [
        f"Registration: {result['outcome'].upper()}",
        f"Transaction: {receipt['transaction_id']}",
        f"Pattern: {plan['pattern']['key']}",
        f"Active payload: {receipt['target']['payload_root_uri']}",
        "Updated files:",
    ]
    lines.extend(f"  - {row['path']}" for row in receipt["outputs"])
    lines.extend((
        f"Receipt: {receipt['target']['receipt_uri']}",
        "Rollback bytes were retained with the receipt.",
    ))
    if plan["outstanding_checks"]:
        lines.append("Required follow-up checks:")
        lines.extend(f"  - {item}" for item in plan["outstanding_checks"])
    return "\n".join(lines)


def _read_prompt(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    value = sys.stdin.readline()
    if value == "":
        raise RegistrationWizardError("interactive wizard input ended")
    return value.strip()


def _choose(
    label: str,
    options: list[str],
    *,
    description: str | None = None,
) -> str:
    if not options:
        raise RegistrationWizardError(f"{label} has no active-instance choices")
    candidates = list(options)
    while True:
        if description:
            sys.stderr.write(f"\n{description}\n")
            description = None
        shown = candidates if len(candidates) <= 20 else candidates[:10]
        sys.stderr.write(f"{label} ({len(candidates)} choice(s)):\n")
        for index, option in enumerate(shown, start=1):
            sys.stderr.write(f"  {index}. {option}\n")
        if len(candidates) > len(shown):
            sys.stderr.write("  ... type an exact value or a narrowing substring\n")
        raw = _read_prompt(f"{label}: ")
        if raw.isdigit() and 1 <= int(raw) <= len(shown):
            return shown[int(raw) - 1]
        exact = [option for option in candidates if option == raw]
        if exact:
            return exact[0]
        matches = [
            option for option in options
            if raw.casefold() in option.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            candidates = matches
            sys.stderr.write(f"Narrowed to {len(matches)} choice(s).\n")
        else:
            candidates = list(options)
            sys.stderr.write("That value does not match an available choice.\n")


def _json_question_example(question_type: str) -> str:
    examples = {
        "ingredient": '{"kind":"metaitem","name":"dustSulfur"}',
        "ingredient-list": (
            '[{"kind":"ore","name":"dustSulfur","amount":1}]'
        ),
        "fluid-list": '[{"name":"water","amount":1000}]',
    }
    return examples[question_type]


def _prompt_answers(
    pattern: dict[str, Any],
    runtime_options: dict[str, list[str]],
) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    sys.stderr.write(
        f"\n{pattern['label']}\n{pattern['summary']}\n"
    )
    for question in pattern["questions"]:
        question_id = question["id"]
        options = runtime_options.get(question_id)
        if options is not None:
            answers[question_id] = _choose(
                question["label"],
                options,
                description=question["description"],
            )
            continue
        question_type = question["type"]
        choices = question.get("choices")
        if isinstance(choices, list):
            answers[question_id] = _choose(
                question["label"],
                choices,
                description=question["description"],
            )
            continue
        while True:
            default = question.get("default")
            default_note = ""
            if "default" in question:
                default_note = " [default: " + json.dumps(default) + "]"
            elif not question["required"]:
                default_note = " [optional]"
            sys.stderr.write(f"\n{question['description']}\n")
            if question_type in {"ingredient", "ingredient-list", "fluid-list"}:
                sys.stderr.write(
                    f"JSON shape: {_json_question_example(question_type)}\n"
                )
            raw = _read_prompt(f"{question['label']}{default_note}: ")
            if not raw:
                if "default" in question:
                    answers[question_id] = default
                    break
                if not question["required"]:
                    break
                sys.stderr.write("A value is required.\n")
                continue
            try:
                if question_type == "integer":
                    value: Any = int(raw)
                elif question_type in {
                    "ingredient", "ingredient-list", "fluid-list"
                }:
                    value = json.loads(raw)
                else:
                    value = raw
            except (ValueError, json.JSONDecodeError):
                sys.stderr.write("The value could not be parsed; try again.\n")
                continue
            answers[question_id] = value
            break
    return answers


def _load_answers(path: Path) -> dict[str, Any]:
    try:
        if str(path) == "-":
            value = json.load(sys.stdin)
        else:
            source = path.expanduser().resolve()
            if source.is_symlink() or not source.is_file():
                raise RegistrationWizardError(
                    "wizard answers must be a regular JSON file"
                )
            if source.stat().st_size > MAX_ANSWERS_BYTES:
                raise RegistrationWizardError("wizard answers exceed the size limit")
            value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistrationWizardError(f"cannot decode wizard answers: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistrationWizardError("wizard answers must be a JSON object")
    return value


def _confirm_active_apply(payload_uri: str) -> bool:
    response = _read_prompt(
        "Apply these edits directly to the selected Minecraft payload "
        f"{payload_uri}? [y/N]: "
    )
    return response.casefold() in {"y", "yes"}


def _confirm_material_fluid_run(plan_id: str) -> bool:
    response = _read_prompt(
        "Stage this exact plan and cold-start a fresh disposable client "
        f"({plan_id})? [y/N]: "
    )
    return response.casefold() in {"y", "yes"}


def _run_registration(
    suite_root: Path,
    args: argparse.Namespace,
    *,
    configuration: WorkbenchConfiguration,
) -> tuple[dict[str, Any], bool]:
    if args.yes and not args.apply:
        raise RegistrationWizardError("--yes is valid only with --apply")
    if args.list and (args.answers is not None or args.apply or args.yes):
        raise RegistrationWizardError(
            "--list cannot be combined with answers or application options"
        )
    capabilities = registration_capabilities(
        suite_root,
        args.workspace,
        pattern_key=args.pattern,
        state_root=args.state_root,
        configuration=configuration,
    )
    if args.list:
        return capabilities, False

    interactive = args.answers is None
    if interactive and args.json:
        raise RegistrationWizardError(
            "--json requires --answers so standard output remains machine-readable"
        )
    if interactive and not sys.stdin.isatty():
        raise RegistrationWizardError(
            "interactive registration requires a terminal; use --answers JSON"
        )

    pattern_key = args.pattern
    if not interactive and pattern_key is None:
        raise RegistrationWizardError(
            "--pattern is required when wizard answers are supplied"
        )
    if pattern_key is None:
        pattern_key = _choose(
            "Registration pattern",
            [row["key"] for row in capabilities["patterns"]],
            description=(
                "Choose an executable pattern. The catalog also retains every "
                "investigated family and its current support state."
            ),
        )
        capabilities = registration_capabilities(
            suite_root,
            args.workspace,
            pattern_key=pattern_key,
            state_root=args.state_root,
            configuration=configuration,
        )
    pattern = next(
        row for row in capabilities["patterns"] if row["key"] == pattern_key
    )
    answers = (
        _prompt_answers(
            pattern,
            capabilities["runtime_options"].get(pattern_key, {}),
        )
        if interactive
        else _load_answers(args.answers)
    )
    plan = plan_active_registration(
        suite_root,
        args.workspace,
        pattern_key=pattern_key,
        answers=answers,
        state_root=args.state_root,
        configuration=configuration,
    )

    wants_prompt = interactive or (args.apply and not args.yes)
    if wants_prompt:
        if not sys.stdin.isatty():
            raise RegistrationWizardError(
                "non-interactive --apply requires --yes"
            )
        sys.stderr.write("\n" + _human_registration_plan(plan) + "\n\n")
        if not _confirm_active_apply(plan["active_instance"]["payload_root_uri"]):
            return plan, True
    elif not args.apply:
        return plan, False

    result = apply_active_registration(
        suite_root,
        args.workspace,
        pattern_key=pattern_key,
        answers=answers,
        expected_plan_id=plan["plan_id"],
        state_root=args.state_root,
        configuration=configuration,
    )
    return result, wants_prompt


def _human_runtime_plan(plan: dict[str, Any]) -> str:
    request = plan["request"]
    target = plan["target"]
    lines = [
        f"Runtime plan: {plan['state'].upper()}",
        f"Plan: {plan['plan_id']}",
        f"Project: {plan['project']['name']} {plan['project']['version']}",
        (
            f"Target: {request['side']} via {request['launcher']}; "
            f"{target['platform_profile_id']}"
        ),
        f"Fixture: {target['fixture_root_uri']}",
    ]
    blockers = plan["blockers"]
    if blockers:
        lines.append("Blockers:")
        lines.extend(
            f"  - {blocker['id']}: {blocker['reason']}"
            for blocker in blockers
        )
    warnings = plan["warnings"]
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in warnings)
    lines.append("Planned steps:")
    lines.extend(
        f"  {index}. {step['summary']}"
        for index, step in enumerate(plan["steps"], start=1)
    )
    return "\n".join(lines)


def _human_blueprint_stage(result: dict[str, Any]) -> str:
    receipt = result["receipt"]
    blueprint = receipt["blueprint"]
    target = receipt["target"]
    atlas = receipt["atlas"]
    parameters = blueprint["effective_parameters"]
    lines = [
        f"Blueprint stage: {result['outcome'].upper()}",
        f"Stage: {receipt['stage_id']}",
        (
            f"Material: {parameters['registry_name']} "
            f"(ID {parameters['material_id']})"
        ),
        (
            f"Atlas census: {atlas['observed_registration_count']} "
            "registrations; "
            f"{len(atlas['uncertainties'])} "
            "uncertainties"
        ),
        f"Disposable workspace: {target['workspace_uri']}",
        f"Receipt: {target['receipt_uri']}",
        "Staged source updates:",
        *(f"  - {row['path']}" for row in receipt["outputs"]),
        "The source checkout was not changed.",
    ]
    return "\n".join(lines)


def _human_material_fluid_plan(plan: dict[str, Any]) -> str:
    parameters = plan["blueprint"]["blueprint"]["effective_parameters"]
    lines = [
        "Material-fluid trial plan: READY",
        f"Plan: {plan['plan_id']}",
        (
            f"Material: {parameters['registry_name']} "
            f"(ID {parameters['material_id']}, color {parameters['color']})"
        ),
        f"Source: {plan['source']['workspace_uri']}",
        "Exact source-bound updates:",
    ]
    for operation in plan["operations"]:
        lines.extend((f"  - {operation['path']}", operation["diff"].rstrip()))
    lines.append("Required projection-only profile compatibility:")
    for patch in plan["execution"]["profile_compatibility"]["identity_patches"]:
        lines.extend((
            f"  - {patch['patch_id']}",
            f"    spec sha256: {patch['spec_sha256']}",
            f"    target: {patch['target_path']}!/{patch['target_entry']}",
            "    scope: disposable projection only",
        ))
    lines.extend((
        "Execution: tracked staging -> materialization -> fresh disposable "
        f"{plan['execution']['launcher']} projection -> cold-start observation",
        "Runtime assertions remain independent:",
        *(
            f"  - {key}: {value['state']}"
            for key, value in plan["assertions"].items()
        ),
        "No source, staging, launcher, or runtime state was changed.",
    ))
    return "\n".join(lines)


def _human_material_fluid_result(result: dict[str, Any]) -> str:
    receipt = result["receipt"]
    runtime = receipt["runtime"]
    lines = [
        f"Material-fluid trial: {result['outcome'].upper()}",
        f"Receipt: {receipt['receipt_id']}",
        f"Retained at: {receipt['target']['receipt_uri']}",
    ]
    stage = receipt.get("blueprint_stage")
    if isinstance(stage, dict):
        lines.extend((
            f"Staged candidate: {stage['candidate_id']}",
            f"Disposable source: {stage['workspace_uri']}",
        ))
    if runtime.get("state") == "observed":
        lines.extend((
            f"Runtime observation: {runtime.get('outcome')}",
            f"Disposable instance: {runtime.get('instance_id')}",
            f"Projection: {runtime.get('projection_uri')}",
        ))
    else:
        error = runtime.get("error", {})
        lines.append(
            f"Runtime failure: {error.get('kind', 'unknown')}: "
            f"{error.get('message', 'unknown failure')}"
        )
    compatibility = receipt.get("plan", {}).get("profile_compatibility")
    if isinstance(compatibility, list):
        lines.append("Applied reviewed projection compatibility:")
        lines.extend(
            f"  - {item.get('patch_id')} ({item.get('spec_sha256')})"
            for item in compatibility
            if isinstance(item, dict)
        )
    lines.append("Assertion states:")
    lines.extend(
        f"  - {key}: {value['state']}"
        for key, value in receipt["assertions"].items()
    )
    lines.append(
        (
            "The developer checkout was revalidated unchanged; unobserved assertions are not success."
            if result["outcome"] == "runtime-completed"
            else "This failed attempt does not claim the developer checkout remained unchanged."
        )
    )
    return "\n".join(lines)


def _human_feature_studio_result(result: dict[str, Any]) -> str:
    feature = result["feature"]
    review = result["review_binding"]
    lines = [
        f"Feature Studio — {result['workspace_label']}",
        f"Operation: {result['operation']} ({result['state']})",
        f"Result: {result['result_id']}",
        (
            f"Feature: {feature['name']} / {feature['registry_name']} "
            f"(material ID {feature['material_id']}, {feature['color']})"
        ),
        f"Workspace: {result['context']['workspace_uri']}",
        f"Plan: {result['plan']['flow_plan_id']}",
        f"Source snapshot: {review['source_snapshot_id']}",
        f"Review binding: {review['state']}",
        f"Catalog digest: {review['catalog_digest'] or 'direct-cli'}",
        f"Action digest: {review['action_digest'] or 'direct-cli'}",
        f"Review digest: {review['review_digest'] or 'direct-cli'}",
        f"Reviewed command: {review['command_id'] or 'direct-cli'}",
        f"Semantic projection: {result['semantic_equivalence']['projection_id']}",
        "Evidence: source={source}, planned={planned}, runtime={runtime}".format(
            **result["context"]["evidence_layers"]
        ),
        "Source owners:",
        *(
            f"  - {row['relative_path']} [{row['operation']}] "
            f"sha256:{row['source_sha256']}"
            for row in result["source_locations"]
        ),
        "Exact reviewed changes:",
        *(
            f"  - {row['relative_path']}\n{row['unified_diff'].rstrip()}"
            for row in result["planned_changes"]
        ),
        "Assertions:",
        *(
            f"  - {row['assertion_key']}: {row['state']}"
            for row in result["assertions"]
        ),
    ]
    if result["export"] is not None:
        lines.extend((
            f"Export: {result['export']['directory_uri']}",
            f"Patch sha256: {result['export']['patch_sha256']}",
        ))
    retained = [
        row["retained"]["uri"]
        for row in result["owner_artifacts"]
        if row["retained"] is not None
    ]
    if retained:
        lines.append("Owner receipts:")
        lines.extend(f"  - {uri}" for uri in retained)
    if result["timings"]:
        lines.append("Owner-reported operational timings:")
        lines.extend(
            f"  - {row['timing_key']}: {row['value']} {row['unit']}"
            for row in result["timings"]
        )
    lines.append("Available actions:")
    lines.extend(
        f"  - {row['action_id']}: {row['state']} ({row['risk']})"
        for row in result["actions"]
    )
    if result["explanation"]:
        lines.append("Guidance:")
        lines.extend(
            f"  - {row['title']}: {row['detail']}"
            for row in result["explanation"]
        )
    if result["limitations"]:
        lines.append("Limits:")
        lines.extend(f"  - {item}" for item in result["limitations"])
    return "\n".join(lines)


def _human_runtime_bootstrap(result: dict[str, Any]) -> str:
    receipt = result["receipt"]
    target = receipt["target"]
    platform = receipt["platform"]
    request = receipt["request"]
    lines = [
        f"Runtime bootstrap: {result['outcome'].upper()}",
        f"Bootstrap: {receipt['bootstrap_id']}",
        (
            f"Cleanroom: {platform['cleanroom_version']} "
            f"for Minecraft {platform['minecraft_version']}"
        ),
        f"Launcher format: {request['launcher']}",
        f"Instance: {target['instance_root_uri']}",
        f"Receipt: {target['receipt_uri']}",
    ]
    remaining = receipt["remaining_plan_blockers"]
    if remaining:
        lines.append("Remaining full-runtime blockers:")
        lines.extend(
            f"  - {blocker['id']}: {blocker['reason']}"
            for blocker in remaining
        )
    lines.append(
        "No launcher installation, Java runtime, or pack payload was changed."
    )
    return "\n".join(lines)


def _human_java_runtime(result: dict[str, Any]) -> str:
    lines = [
        f"Java runtime: {result['outcome'].upper()}",
        f"Source: {result['source']}",
    ]
    receipt = result.get("receipt")
    if isinstance(receipt, dict):
        probe = receipt["probe"]
        target = receipt["target"]
        lines.extend([
            f"Runtime: {probe['runtime_version']} ({probe['vendor']})",
            (
                f"Host: {receipt['host']['os']}/"
                f"{receipt['host']['architecture']}"
            ),
            f"Identity: {receipt['runtime_id']}",
            f"Java home: {target['java_home_uri']}",
            f"Receipt: {target['receipt_uri']}",
        ])
    else:
        runtime = result["runtime"]
        probe = runtime["probe"]
        lines.extend([
            f"Runtime: {probe['runtime_version']} ({probe['vendor']})",
            f"Java: {runtime['java_uri']}",
            "No managed runtime was needed.",
        ])
    return "\n".join(lines)


def _human_runtime_materialize(result: dict[str, Any]) -> str:
    receipt = result["receipt"]
    payload = receipt["payload"]
    target = receipt["target"]
    lines = [
        f"Packwiz materialization: {result['outcome'].upper()}",
        f"Materialization: {receipt['materialization_id']}",
        (
            f"Project: {receipt['project']['name']} "
            f"{receipt['project']['version']}"
        ),
        (
            f"Payload: {payload['file_count']} files, "
            f"{payload['total_bytes']} bytes"
        ),
        f"Instance: {target['instance_root_uri']}",
        f"Receipt: {target['receipt_uri']}",
    ]
    packwiz_options = receipt.get("packwiz_options")
    if isinstance(packwiz_options, dict):
        lines.append(
            "Packwiz declared defaults: "
            f"{packwiz_options.get('enabled_count')} enabled, "
            f"{packwiz_options.get('disabled_count')} disabled"
        )
    if "bootstrap_outcome" in result:
        lines.append(
            "Inputs: Cleanroom "
            f"{result['bootstrap_outcome']}; Java "
            f"{result['java_outcome']}; installer artifact "
            f"{result['installer_artifact_outcome']}"
        )
    lines.append(
        "The launcher instance is populated; Minecraft has not been launched."
    )
    return "\n".join(lines)


def _human_runtime_launch(result: dict[str, Any]) -> str:
    receipt = result["receipt"]
    launcher = receipt["launcher"]
    target = receipt["target"]
    lines = [
        f"Runtime launch: {result['outcome'].upper()}",
        f"Launch: {receipt['launch_id']}",
        (
            f"Project: {receipt['project']['name']} "
            f"{receipt['project']['version']}"
        ),
        (
            f"Launcher: {launcher['family']} "
            f"({launcher['version_output']})"
        ),
        f"Instance: {launcher['projection_uri']}",
        f"Receipt: {target['receipt_uri']}",
    ]
    checkpoint = receipt["observation"].get("checkpoint")
    if isinstance(checkpoint, dict):
        lines.append(
            f"Checkpoint: {checkpoint['id']} "
            f"({checkpoint['marker']})"
        )
    failure = receipt["observation"].get("failure_kind")
    if isinstance(failure, str):
        lines.append(f"Failure: {failure}")
    if launcher["process_state"] == "running":
        lines.append(
            "The launcher remains running; the projected instance is mutable."
        )
    return "\n".join(lines)


def _human_runtime_observation(result: dict[str, Any]) -> str:
    receipt = result["receipt"]
    launch = receipt["launch"]
    process = launch["process_observation"]
    diagnosis = result["diagnosis"]
    worldgen = result["worldgen_audit"]
    lines = [
        f"Runtime observation: {result['outcome'].upper()}",
        f"Session: {receipt['session_id']}",
        f"Instance: {launch['projection_uri']}",
        (
            f"Client process: {process['state']} "
            f"({len(process['observed_pids'])} bound PID(s))"
        ),
    ]
    if diagnosis is None:
        lines.append(
            f"Runtime diagnosis: {receipt['diagnosis']['state']} "
            f"({receipt['diagnosis'].get('reason', 'no result')})"
        )
    else:
        lines.append(
            f"Runtime diagnosis: {diagnosis['state']} "
            f"({diagnosis['diagnosis_id']})"
        )
    if worldgen is None:
        record = receipt["worldgen_audit"]
        lines.append(
            f"Worldgen audit: {record['state']} "
            f"({record.get('reason', 'no result')})"
        )
    else:
        lines.append(
            f"Worldgen audit: {worldgen['state']} "
            f"({worldgen['audit_id']})"
        )
    anvil = result.get("anvil_observations", [])
    if anvil:
        for record in anvil:
            summary = record.get("summary", {})
            world_name = record.get("world_name", "observation set")
            if "observation_id" in record:
                lines.append(
                    f"Anvil observation: {world_name} {record['state']} "
                    f"({summary.get('validated_chunk_count', 0)}/"
                    f"{summary.get('allocated_chunk_count', 0)} chunks "
                    "validated)"
                )
            else:
                lines.append(
                    f"Anvil observation: {world_name} {record['state']} "
                    f"({record.get('reason', 'no result')})"
                )
    elif process["state"] == "exited":
        lines.append("Anvil observation: no projected save worlds")
    else:
        lines.append("Anvil observation: not run (client exit was not observed)")
    for finding in (() if diagnosis is None else diagnosis.get("findings", [])):
        lines.append(
            f"Diagnosis finding: {finding['summary']} "
            f"[{finding['confidence']}]"
        )
    for finding in (() if worldgen is None else worldgen.get("findings", [])):
        lines.append(
            f"Worldgen finding: {finding['summary']} "
            f"[{finding['confidence']}]"
        )
    evidence_kind = "final capture" if process["state"] == "exited" else "snapshot"
    lines.extend([
        f"Evidence: {len(receipt['evidence'])} {evidence_kind}(s)",
        f"Receipt: {receipt['target']['receipt_uri']}",
    ])
    return "\n".join(lines)


def _human_runtime_diagnosis(result: dict[str, Any]) -> str:
    source = result["source"]
    lines = [
        f"Runtime diagnosis: {result['state'].upper()}",
        f"Diagnosis: {result['diagnosis_id']}",
        f"Launch: {source['launch_id']} ({source['launch_outcome']})",
        (
            f"Project: {source['project']['name']} "
            f"{source['project']['version']}"
        ),
    ]
    checkpoint = result.get("checkpoint")
    if isinstance(checkpoint, dict):
        lines.append(
            f"Checkpoint: {checkpoint['id']} ({checkpoint['marker']})"
        )
    failure = result.get("primary_failure")
    if isinstance(failure, dict):
        lines.append(
            "Primary failure: "
            f"{failure['category']} in {failure['config']}:"
            f"{failure['mixin_simple_name']}"
        )
    for wrapper in result["wrappers"]:
        lines.append(
            f"Wrapper: {wrapper['type']} ({wrapper['classification']}; "
            f"target artifact {wrapper['artifact_state']})"
        )
    for finding in result["findings"]:
        lines.append(
            f"Finding: {finding['summary']} "
            f"[{finding['confidence']}]"
        )
        for guidance in finding.get("guidance", []):
            lines.append(
                f"Profile guidance: {guidance['interpretation']}"
            )
            lines.extend(
                f"  - {action}"
                for action in guidance["developer_actions"]
            )
    verified = sum(
        evidence["state"] == "verified"
        for evidence in result["evidence"]
    )
    lines.append(
        f"Evidence: {verified} verified capture(s), "
        f"{len(result['artifact_observations'])} artifact observation(s)"
    )
    for root in result["artifact_roots"]:
        provenance = root.get("provenance")
        if isinstance(provenance, dict):
            lines.append(
                f"Artifact provenance: {provenance['state'].upper()} "
                f"({provenance['basis']})"
            )
    if result["log_observations"]:
        lines.append("Non-terminal log observations:")
        lines.extend(
            f"  - {item['type']}: {item['count']} occurrence(s)"
            for item in result["log_observations"]
        )
    if result["limitations"]:
        lines.append("Limitations:")
        lines.extend(
            f"  - {limitation}"
            for limitation in result["limitations"]
        )
    lines.append("No source, materialization, or launcher state was changed.")
    return "\n".join(lines)


def _human_recipe_reload_diagnostic(result: dict[str, Any]) -> str:
    summary = result["summary"]
    scope = result["scope"]
    source = result["source"]
    recommendation = result["recommendation"]
    lines = [
        f"Groovy postInit conflict diagnostic: {result['state'].upper()}",
        f"Diagnostic: {result['diagnostic_id']}",
        (
            f"Project: {source['project']['name']} "
            f"{source['project']['version']}"
        ),
        (
            f"postInit executions: {scope['post_init_execution_count']} "
            f"({scope['reload_execution_count']} reload)"
        ),
        (
            f"Complete Groovy-log GT conflicts: {summary['complete_conflict_count']} "
            f"({summary['initial_conflict_count']} initial, "
            f"{summary['reload_conflict_count']} reload)"
        ),
    ]
    if result["groups"]:
        lines.append("Largest logger/map groups:")
        for group in result["groups"][:10]:
            counts = group["counts"]
            lines.append(
                f"  - {counts['total']} · {group['script_logger']} / "
                f"{group['recipe_map']} "
                f"({counts['reload']} reload)"
            )
        if summary["group_count"] > min(10, len(result["groups"])):
            lines.append(
                "  - … "
                f"{summary['group_count'] - min(10, len(result['groups']))} "
                "additional group(s); use --json"
            )
    if summary["incomplete_sequence_count"]:
        lines.append(
            "Incomplete conflict sequences: "
            f"{summary['incomplete_sequence_count']}"
        )
    lines.extend([
        f"Recommendation: {recommendation['state'].upper()}",
        f"  {recommendation['summary']}",
    ])
    lines.extend(
        f"  - {action}" for action in recommendation["actions"]
    )
    if result["limitations"]:
        lines.append("Boundary:")
        lines.extend(
            f"  - {limitation}" for limitation in result["limitations"]
        )
    lines.append("No source, recipe, runtime, or launcher state was changed.")
    return "\n".join(lines)


def _human_recipe_conflict_comparison(result: dict[str, Any]) -> str:
    summary = result["summary"]
    recommendation = result["recommendation"]
    delta = summary["net_conflict_count_delta"]
    signed_delta = f"{delta:+d}"
    lines = [
        "Cold-start Groovy conflict delta: "
        + result["state"].replace("-", " ").upper(),
        (
            f"Candidate: {summary['candidate_complete_conflict_count']} "
            f"conflicts ({signed_delta} vs explicit baseline "
            f"{summary['baseline_complete_conflict_count']})"
        ),
        (
            "Groups: "
            f"{summary['newly_observed_group_count']} newly observed, "
            f"{summary['increased_group_count']} increased, "
            f"{summary['decreased_group_count']} decreased, "
            f"{summary['no_longer_observed_group_count']} no longer observed, "
            f"{summary['same_count_group_count']} same-count"
        ),
    ]
    actionable = [
        group
        for group in result["groups"]
        if group["classification"] != "same-count"
        or group["resolution_counts_changed"]
    ]
    if actionable:
        lines.append("Review first:")
        for group in actionable[:10]:
            lines.append(
                f"  {group['delta']:+d} {group['classification']} · "
                f"{group['script_logger']} / {group['recipe_map']} "
                f"({group['baseline_count']} → {group['candidate_count']})"
            )
        if len(actionable) > 10:
            lines.append(
                f"  … {len(actionable) - 10} additional changed group(s); "
                "use --json"
            )
    lines.extend([
        f"Next: {recommendation['summary']}",
        (
            "Scope: complete Groovy postInit GT conflict-group counts only; "
            "Java/latest.log and the effective registry are unassessed."
        ),
        (
            "Provenance: receipt roles are explicit; source revisions are "
            "not receipt-bound."
        ),
        "No source, recipe, runtime, or launcher state was changed.",
    ])
    return "\n".join(lines)


def _human_recipe_invalidation_diagnostic(result: dict[str, Any]) -> str:
    channels = result["channels"]
    groovy = channels["groovy_postinit"]
    java = channels["gt_startup_registration"]
    groovy_summary = groovy["summary"]
    java_summary = java["summary"]
    lines = [
        "Recipe-registration signals: "
        f"{result['state'].replace('-', ' ').upper()}",
        (
            "Groovy postInit: "
            f"{groovy_summary['complete_conflict_count']} conflicts in "
            f"{groovy_summary['group_count']} groups"
        ),
        (
            "GT startup/latest.log: "
            f"{java_summary['complete_signal_count']} signals in "
            f"{java_summary['group_count']} groups"
        ),
    ]
    review: list[str] = []
    for group in groovy["groups"][:5]:
        review.append(
            "  [Groovy] "
            f"{group['counts']['total']} · {group['script_logger']} / "
            f"{group['recipe_map']}"
        )
    for group in java["groups"][:5]:
        review.append(
            "  [GT startup] "
            f"{group['count']} · {group['reason_code']} · "
            f"{group['observed_owner_class']}#{group['observed_owner_method']}"
        )
    if review:
        lines.append("Review first:")
        lines.extend(review[:10])
    if groovy_summary["incomplete_sequence_count"] or java_summary[
        "incomplete_sequence_count"
    ]:
        lines.append(
            "Incomplete sequences: "
            f"Groovy {groovy_summary['incomplete_sequence_count']}, "
            f"GT startup {java_summary['incomplete_sequence_count']}"
        )
    lines.extend((
        f"Next: {result['recommendation']['summary']}",
        "Counts remain channel-specific; cross-log deduplication is unproven.",
        "Provenance: launch evidence is receipt-bound; source revision and launch-time profile bytes are not.",
        "No source, recipe, runtime, launcher, or stored baseline state was changed.",
    ))
    return "\n".join(lines)


def _human_recipe_invalidation_comparison(result: dict[str, Any]) -> str:
    state = result["state"].replace("-", " ").upper()
    lines = [f"Recipe-registration signal delta: {state}"]
    compatibility = result["compatibility"]
    if compatibility["state"] == "incomparable":
        lines.append("The selected baseline and candidate were not compared:")
        lines.extend(f"  - {item}" for item in compatibility["findings"])
    else:
        for channel_id, title, baseline_field, candidate_field in (
            (
                "groovy_postinit",
                "Groovy postInit",
                "baseline_complete_conflict_count",
                "candidate_complete_conflict_count",
            ),
            (
                "gt_startup_registration",
                "GT startup/latest.log",
                "baseline_complete_signal_count",
                "candidate_complete_signal_count",
            ),
        ):
            channel = result["channels"][channel_id]
            summary = channel["summary"]
            before = summary[baseline_field]
            after = summary[candidate_field]
            lines.append(
                f"{title}: {after} ({after - before:+d} vs explicit baseline {before})"
            )
            changed = [
                group
                for group in channel["groups"]
                if group.get("classification") != "same-count"
                or group.get("resolution_counts_changed") is True
            ]
            for group in changed[:5]:
                if channel_id == "groovy_postinit":
                    identity = (
                        f"{group['script_logger']} / {group['recipe_map']}"
                    )
                else:
                    identity = (
                        f"{group['reason_code']} · "
                        f"{group['observed_owner_class']}#"
                        f"{group['observed_owner_method']}"
                    )
                lines.append(
                    f"  {group['delta']:+d} {group['classification']} · {identity} "
                    f"({group['baseline_count']} → {group['candidate_count']})"
                )
            if len(changed) > 5:
                lines.append(
                    f"  … {len(changed) - 5} additional changed group(s); use --json"
                )
    lines.extend((
        f"Next: {result['recommendation']['summary']}",
        "Counts remain channel-specific; cross-log deduplication is unproven.",
        "Provenance: receipt roles are explicit; standalone source causality is unbound.",
        "No source, recipe, runtime, launcher, or stored baseline state was changed.",
    ))
    return "\n".join(lines)


def _human_runtime_worldgen_audit(result: dict[str, Any]) -> str:
    source = result["source"]
    registry = result["biome_registry"]
    log = result["log_observations"]
    lines = [
        f"Runtime worldgen audit: {result['state'].upper()}",
        f"Audit: {result['audit_id']}",
        (
            f"Project: {source['project']['name']} "
            f"{source['project']['version']}"
        ),
        f"Runtime: {source['runtime_root_uri']}",
        (
            f"Evidence: {len(result['evidence'])} exact file(s); "
            f"{registry['report_count']} biome report(s); "
            f"{len(log['unsupported_biomes'])} logged RTG-unsupported biome(s)"
        ),
    ]
    lines.extend(
        f"Artifact: {item['artifact_id']} ({item['state']})"
        for item in result["artifact_observations"]
    )
    lines.extend(
        f"Source alignment: {item['path']} ({item['state']})"
        for item in result["source_alignments"]
    )
    for finding in result["findings"]:
        lines.append(
            f"Finding: {finding['summary']} "
            f"[{finding['confidence']}]"
        )
        guidance = finding.get("guidance")
        if isinstance(guidance, dict):
            lines.append(f"Profile guidance: {guidance['interpretation']}")
            lines.extend(
                f"  - {action}"
                for action in guidance["developer_actions"]
            )
    if result["limitations"]:
        lines.append("Limitations:")
        lines.extend(
            f"  - {limitation}"
            for limitation in result["limitations"]
        )
    lines.append("No source, runtime, or world state was changed.")
    return "\n".join(lines)


def _human_runtime_worldgen_fingerprint(result: dict[str, Any]) -> str:
    facts = result["facts"]
    metadata = facts["metadata"]
    summary = facts["summary"]
    return "\n".join([
        f"Runtime worldgen fingerprint: {result['state'].upper()}",
        f"Fingerprint: {result['fingerprint_id']}",
        (
            f"Generator: {metadata['generator_name']} "
            f"(seed {metadata['random_seed']})"
        ),
        (
            f"Chunks: {summary['chunk_count']} across "
            f"{len(summary['dimensions'])} dimension path(s)"
        ),
        (
            f"Population: {summary['terrain_populated']} terrain-complete, "
            f"{summary['terrain_unpopulated']} terrain-incomplete; "
            f"{summary['light_populated']} light-complete, "
            f"{summary['light_unpopulated']} light-incomplete"
        ),
        "No runtime or world state was changed.",
    ])


def _human_runtime_worldgen_comparison(result: dict[str, Any]) -> str:
    summary = result["facts"]["summary"]
    equal = summary["equal_shared_chunks"]
    return "\n".join([
        f"Runtime worldgen comparison: {result['state'].upper()}",
        f"Comparison: {result['comparison_id']}",
        (
            f"Overlap: {summary['shared_chunk_count']} shared; "
            f"{summary['left_only_chunk_count']} left-only; "
            f"{summary['right_only_chunk_count']} right-only"
        ),
        (
            f"Saved worldgen output: {equal['worldgen_sha256']} equal, "
            f"{summary['shared_chunk_count'] - equal['worldgen_sha256']} different"
        ),
        (
            f"Any compared component differed in "
            f"{summary['different_shared_chunk_count']} shared chunk(s)"
        ),
        "No runtime or world state was changed.",
    ])


def _human_runtime_worldgen_block_delta(result: dict[str, Any]) -> str:
    summary = result["facts"]["summary"]
    positions = summary["changed_block_state_position_count"]
    below = summary["changed_positions_below_y_80"]
    below_percent = 0.0 if positions == 0 else (below * 100.0 / positions)
    return "\n".join([
        f"Runtime worldgen block delta: {result['state'].upper()}",
        f"Observation: {result['observation_id']}",
        (
            f"Changed blocks: {positions} position(s) across "
            f"{summary['exact_block_different_chunk_count']} chunk(s)"
        ),
        (
            f"Subsurface: {below} changed position(s) below Y=80 "
            f"({below_percent:.1f}%)"
        ),
        (
            f"Transitions: {summary['unique_transition_count']} exact legacy "
            "block-state pair(s)"
        ),
        "No runtime or world state was changed.",
    ])


def main(
    argv: Sequence[str] | None = None,
    *,
    feature_program: str | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    from workbench_core.host_services import install_local_host_services
    install_local_host_services()
    if not arguments:
        return serve_stdio()

    args = _parser(feature_program=feature_program).parse_args(arguments)
    suite_root = (
        discover_suite_root()
        if args.suite_root is None
        else args.suite_root.resolve()
    )
    if args.command == "serve":
        return serve_stdio(
            suite_root=suite_root,
            config_path=args.config_path,
        )

    previewed_registration = False
    try:
        active_configuration: WorkbenchConfiguration | None = None
        resolved_bindings: ResolvedBindings | None = None
        if args.command in {
            "config",
            "inspect",
            "initialize",
            "register",
            "runtime-plan",
            "runtime-bootstrap",
            "runtime-java",
            "runtime-materialize",
            "runtime-launch",
            "runtime-observe",
            "runtime-diagnose",
            "runtime-worldgen-audit",
        }:
            active_configuration = load_workbench_configuration(
                suite_root,
                args.config_path,
            )
            if args.command == "runtime-java":
                resolved_bindings = active_configuration.resolve_bindings(
                    names=CONFIGURATION_COMMAND_BINDINGS["runtime-java"][1],
                )
        if args.command == "config":
            assert active_configuration is not None
            if args.configuration_action == "validate":
                result = _configuration_validation_result(active_configuration)
            else:
                consumed_names = CONFIGURATION_COMMAND_BINDINGS[
                    args.configuration_command
                ][1]
                resolved_bindings = active_configuration.resolve_bindings(
                    names=consumed_names,
                )
                result = _configuration_resolution_result(
                    active_configuration,
                    resolved_bindings,
                    args.configuration_command,
                )
        elif args.command == "environment":
            result = inspect_environment_status(suite_root)
        elif args.command == "host-status":
            result = inspect_local_host_adapter_v3(
                scratch_parent=args.scratch_parent,
            )
        elif args.command == "service-call":
            # The service adapter depends on the Crucible source package. Keep
            # that optional dependency out of unrelated Shell entrypoints and
            # portable-package commands.
            from workbench_core.service.cli import invoke_service_cli_v3

            result = invoke_service_cli_v3(
                endpoint_path=args.endpoint.absolute(),
                credential_path=args.credential.absolute(),
                messages_path=args.messages.absolute(),
            )
        elif args.command == "service-host-v3":
            from .installed_service import run_installed_service_daemon_v3

            return run_installed_service_daemon_v3(
                suite_root,
                service_root=args.service_root.absolute(),
                endpoint_path=args.endpoint.absolute(),
                ready_path=args.ready_file.absolute(),
                process_nonce=args.process_nonce,
            )
        elif args.command == "service-probe-v3":
            from .installed_service import probe_installed_service_v3

            result = probe_installed_service_v3(
                suite_root,
                endpoint_path=args.endpoint.absolute(),
                credential_path=args.credential.absolute(),
            )
        elif args.command == "feature-service":
            from .feature_studio_registry import (
                build_feature_studio_registry_v3,
            )
            from .feature_studio_service import (
                FeatureStudioServiceClientV3,
            )
            from workbench_core.service.host import LocalServiceClientV3
            from workbench_api.host_filesystem import private_path

            credential_path = args.credential.expanduser().resolve(strict=True)
            if not private_path(credential_path, directory=False):
                raise ValueError(
                    "Feature Studio service credential must be an owner-private regular file"
                )
            token = credential_path.read_text(encoding="ascii")
            bundle = build_feature_studio_registry_v3(suite_root)
            endpoint_path = args.endpoint.expanduser().absolute()
            local = LocalServiceClientV3(endpoint_path, token)
            with local.connect() as connection:
                client = FeatureStudioServiceClientV3(
                    bundle.registry,
                    connection.call,
                    transport="local-endpoint",
                )
                initialized = client.initialize()
                if args.feature_service_action == "submit":
                    context_ref = _load_service_json_object(
                        args.context_ref, "Feature Studio ContextRef"
                    )
                    input_binding = _load_service_json_object(
                        args.input_binding, "Feature Studio InputBinding"
                    )
                    request = _load_service_json_object(
                        args.request, "Feature Studio owner request"
                    )
                    registration = client.register_context(
                        context_ref, input_binding
                    )
                    action_result = client.submit(
                        request,
                        context_ref_id=context_ref["id"],
                        input_binding_id=input_binding["id"],
                    )
                elif args.feature_service_action == "events":
                    supplied_resume = (
                        args.subscription_id is not None,
                        args.stream_generation is not None,
                    )
                    if supplied_resume not in {(False, False), (True, True)}:
                        raise ValueError(
                            "event resume requires both subscription and stream-generation IDs"
                        )
                    if supplied_resume == (True, True) and args.last_cursor < 0:
                        raise ValueError(
                            "event resume requires a non-negative last cursor"
                        )
                    job = {
                        "job_id": args.job_id,
                        "job_submission_id": args.job_submission_id,
                    }
                    position = (
                        None
                        if supplied_resume == (False, False)
                        else {
                            "kind": "resume",
                            "subscription_id": args.subscription_id,
                            "stream_generation": args.stream_generation,
                            "last_cursor": args.last_cursor,
                        }
                    )
                    subscription_result = client.subscribe(
                        job,
                        context_ref_id=args.context_ref_id,
                        input_binding_id=args.input_binding_id,
                        position=position,
                    )
                    subscription = subscription_result["outcome"]["value"]
                    page_result = client.event_page(
                        job,
                        subscription,
                        context_ref_id=args.context_ref_id,
                        input_binding_id=args.input_binding_id,
                        last_cursor=args.last_cursor,
                        maximum_events=args.maximum_events,
                    )
                    action_result = {
                        "subscription": subscription_result,
                        "page": page_result,
                    }
                    registration = None
                elif args.feature_service_action == "cancel":
                    action_result = client.cancel(
                        {
                            "job_id": args.job_id,
                            "job_submission_id": args.job_submission_id,
                        },
                        context_ref_id=args.context_ref_id,
                        input_binding_id=args.input_binding_id,
                        expected_event_id=args.expected_event_id,
                        expected_event_ordinal=args.expected_event_ordinal,
                        reason=args.reason,
                    )
                    registration = None
                else:
                    action_result = client.result(
                        args.job_id,
                        context_ref_id=args.context_ref_id,
                        input_binding_id=args.input_binding_id,
                    )
                    registration = None
            result = {
                "format": "workbench-feature-studio-service-cli-result-v1",
                "schema_version": 1,
                "action": args.feature_service_action,
                "registry_id": initialized["registry_id"],
                "service_instance_id": initialized["service_instance_id"],
                "registration": registration,
                "result": action_result,
            }
        elif args.command == "inspect":
            assert active_configuration is not None
            result = inspect_project(
                suite_root,
                args.workspace,
                configuration=active_configuration,
            )
        elif args.command == "initialize":
            assert active_configuration is not None
            result = initialize_active_instance(
                suite_root,
                args.workspace,
                args.instance,
                state_root=args.state_root,
                configuration=active_configuration,
            )
        elif args.command == "register":
            assert active_configuration is not None
            result, previewed_registration = _run_registration(
                suite_root,
                args,
                configuration=active_configuration,
            )
        elif args.command == "blueprint-stage":
            result = stage_material_backed_fluid(
                suite_root,
                args.workspace,
                name=args.name,
                color=args.color,
                translation=args.translation,
                symbol=args.symbol,
            )
        elif args.command == "material-fluid":
            current_plan = plan_material_fluid_trial(
                suite_root,
                args.workspace,
                name=args.name,
                color=args.color,
                translation=args.translation,
                symbol=args.symbol,
                launcher=args.launcher,
                compatibility_patches=getattr(args, "compatibility_patch", ()),
            )
            if args.material_fluid_action == "plan" or args.show:
                if (
                    args.material_fluid_action == "run"
                    and args.plan_id is not None
                    and args.plan_id != current_plan["plan_id"]
                ):
                    raise MaterialFluidFlowError(
                        "material-fluid plan changed after review; inspect the "
                        "fresh three-file diff before running"
                    )
                result = current_plan
            else:
                confirmed = False
                if args.plan_id is None:
                    if args.json or not sys.stdin.isatty():
                        raise MaterialFluidFlowError(
                            "non-interactive material-fluid run requires --plan-id"
                        )
                    print(_human_material_fluid_plan(current_plan))
                    confirmed = _confirm_material_fluid_run(current_plan["plan_id"])
                    if not confirmed:
                        print("Material-fluid run cancelled; no state was changed.")
                        return 0
                result = execute_material_fluid_trial(
                    suite_root,
                    args.workspace,
                    name=args.name,
                    color=args.color,
                    translation=args.translation,
                    symbol=args.symbol,
                    launcher=args.launcher,
                    launcher_executable=args.launcher_executable,
                    launcher_root=args.launcher_root,
                    launcher_profile=args.launcher_profile,
                    launcher_java=args.launcher_java,
                    launcher_java_state=args.launcher_java_state,
                    packwiz_executable=args.packwiz,
                    seed_roots=args.seed,
                    memory_mib=args.memory_mib,
                    offline_name=args.offline_name,
                    compatibility_patches=(),
                    timeout_seconds=args.launch_timeout,
                    attach_timeout=args.attach_timeout,
                    session_timeout=args.session_timeout,
                    expected_plan_id=args.plan_id,
                    confirmed=confirmed,
                )
        elif args.command == "feature":
            feature_common = {
                "name": args.name,
                "color": args.color,
                "translation": args.translation,
                "symbol": args.symbol,
                "launcher": args.launcher,
            }
            if args.feature_action == "inspect":
                if args.snapshot and not args.json:
                    raise FeatureStudioError(
                        "Feature Studio snapshot output requires --json"
                    )
                if args.refresh_snapshot is not None:
                    if not args.snapshot:
                        raise FeatureStudioError(
                            "Feature Studio snapshot refresh requires --snapshot"
                        )
                    if any(
                        value is not None
                        for value in (
                            args.result,
                            args.receipt,
                            args.workspace,
                            args.name,
                            args.color,
                            args.translation,
                            args.symbol,
                        )
                    ):
                        raise FeatureStudioError(
                            "Feature Studio snapshot refresh cannot be combined with another selection or source planning inputs"
                        )
                    result = refresh_feature_snapshot_file(
                        suite_root,
                        args.refresh_snapshot,
                    )
                elif args.result is not None:
                    if not args.snapshot:
                        raise FeatureStudioError(
                            "Feature Studio retained result selection requires --snapshot"
                        )
                    if any(
                        value is not None
                        for value in (
                            args.receipt,
                            args.workspace,
                            args.name,
                            args.color,
                            args.translation,
                            args.symbol,
                        )
                    ):
                        raise FeatureStudioError(
                            "Feature Studio result selection cannot be combined with another selection or source planning inputs"
                        )
                    result = open_feature_snapshot(
                        suite_root,
                        result_path=args.result,
                    )
                elif args.receipt is not None:
                    if any(
                        value is not None
                        for value in (
                            args.workspace,
                            args.name,
                            args.color,
                            args.translation,
                            args.symbol,
                        )
                    ):
                        raise FeatureStudioError(
                            "Feature Studio receipt selection cannot be combined with source planning inputs"
                        )
                    result = (
                        open_feature_snapshot(
                            suite_root,
                            receipt_path=args.receipt,
                        )
                        if args.snapshot
                        else inspect_retained_feature(suite_root, args.receipt)
                    )
                else:
                    if args.workspace is None or args.name is None or args.color is None:
                        raise FeatureStudioError(
                            "Feature Studio source inspection requires workspace, --name, and --color"
                        )
                    result = inspect_feature(
                        suite_root,
                        args.workspace,
                        **feature_common,
                    )
                    if args.snapshot:
                        result = project_feature_snapshot(
                            result,
                            suite_root=suite_root,
                        )
            elif args.feature_action == "plan":
                result = plan_feature(
                    suite_root,
                    args.workspace,
                    **feature_common,
                )
            elif args.feature_action == "explain":
                result = explain_feature(
                    suite_root,
                    args.workspace,
                    **feature_common,
                )
            elif args.show:
                result = preview_feature(
                    suite_root,
                    args.workspace,
                    operation=args.feature_action,
                    **feature_common,
                )
                if args.plan_id != result["plan"]["flow_plan_id"]:
                    raise FeatureStudioError(
                        "Feature Studio plan changed after review; inspect the fresh plan"
                    )
            elif args.feature_action == "export":
                result = export_feature(
                    suite_root,
                    args.workspace,
                    output_root=args.output,
                    expected_plan_id=args.plan_id,
                    **feature_common,
                )
            else:
                result = verify_feature(
                    suite_root,
                    args.workspace,
                    launcher_executable=args.launcher_executable,
                    launcher_root=args.launcher_root,
                    launcher_profile=args.launcher_profile,
                    launcher_java=args.launcher_java,
                    launcher_java_state=args.launcher_java_state,
                    packwiz_executable=args.packwiz,
                    seed_roots=args.seed,
                    memory_mib=args.memory_mib,
                    offline_name=args.offline_name,
                    timeout_seconds=args.launch_timeout,
                    attach_timeout=args.attach_timeout,
                    session_timeout=args.session_timeout,
                    state_root=args.state_root,
                    expected_plan_id=args.plan_id,
                    **feature_common,
                )
            if args.feature_action == "inspect" and args.snapshot:
                result = validate_feature_snapshot(result)
            else:
                result = validate_feature_result(result, suite_root=suite_root)
        elif args.command == "runtime-plan":
            assert active_configuration is not None
            result = plan_project_runtime(
                suite_root,
                args.workspace,
                side=args.side,
                launcher=args.launcher,
                configuration=active_configuration,
            )
        elif args.command == "runtime-bootstrap":
            assert active_configuration is not None
            result = bootstrap_project_runtime(
                suite_root,
                args.workspace,
                launcher=args.launcher,
                configuration=active_configuration,
            )
        elif args.command == "runtime-materialize":
            assert active_configuration is not None
            _require_manual_artifact_preflight(
                active_configuration,
                args.workspace,
                args.seed,
            )
            result = materialize_project_runtime(
                suite_root,
                args.workspace,
                launcher=args.launcher,
                packwiz_executable=args.packwiz,
                seed_roots=args.seed,
                configuration=active_configuration,
            )
        elif args.command == "runtime-launch":
            assert active_configuration is not None
            _require_manual_artifact_preflight(
                active_configuration,
                args.workspace,
                args.seed,
            )
            result = launch_project_runtime(
                suite_root,
                args.workspace,
                launcher=args.launcher,
                launcher_executable=args.launcher_executable,
                launcher_root=args.launcher_root,
                launcher_profile=args.launcher_profile,
                launcher_java=args.launcher_java,
                launcher_java_state=args.launcher_java_state,
                packwiz_executable=args.packwiz,
                seed_roots=args.seed,
                memory_mib=args.memory_mib,
                offline_name=args.offline_name,
                compatibility_patches=args.compatibility_patch,
                timeout_seconds=args.timeout,
                configuration=active_configuration,
            )
        elif args.command == "runtime-observe":
            assert active_configuration is not None
            _require_manual_artifact_preflight(
                active_configuration,
                args.workspace,
                args.seed,
            )
            result = observe_project_runtime(
                suite_root,
                args.workspace,
                launcher=args.launcher,
                launcher_executable=args.launcher_executable,
                launcher_root=args.launcher_root,
                launcher_profile=args.launcher_profile,
                launcher_java=args.launcher_java,
                launcher_java_state=args.launcher_java_state,
                packwiz_executable=args.packwiz,
                seed_roots=args.seed,
                memory_mib=args.memory_mib,
                offline_name=args.offline_name,
                compatibility_patches=args.compatibility_patch,
                timeout_seconds=args.launch_timeout,
                attach_timeout=args.attach_timeout,
                session_timeout=args.session_timeout,
                configuration=active_configuration,
            )
        elif args.command == "runtime-diagnose":
            assert active_configuration is not None
            if args.recipe_invalidations and args.recipe_reload:
                raise RuntimeRecipeInvalidationError(
                    "--recipe-invalidations cannot be combined with --recipe-reload"
                )
            if args.recipe_invalidations:
                if args.artifact_root:
                    raise RuntimeRecipeInvalidationError(
                        "--artifact-root cannot be combined with --recipe-invalidations"
                    )
                if args.baseline_receipt is not None:
                    result = compare_project_recipe_invalidations(
                        suite_root,
                        args.workspace,
                        baseline_launch_receipt=args.baseline_receipt,
                        candidate_launch_receipt=args.receipt,
                        configuration=active_configuration,
                    )
                else:
                    result = diagnose_project_recipe_invalidations(
                        suite_root,
                        args.workspace,
                        launch_receipt=args.receipt,
                        configuration=active_configuration,
                    )
            elif args.recipe_reload:
                if args.artifact_root:
                    raise RuntimeRecipeDiagnosticError(
                        "--artifact-root cannot be combined with --recipe-reload"
                    )
                if args.baseline_receipt is not None:
                    result = compare_project_recipe_reload(
                        suite_root,
                        args.workspace,
                        baseline_launch_receipt=args.baseline_receipt,
                        candidate_launch_receipt=args.receipt,
                        configuration=active_configuration,
                    )
                else:
                    result = diagnose_project_recipe_reload(
                        suite_root,
                        args.workspace,
                        launch_receipt=args.receipt,
                        configuration=active_configuration,
                    )
            else:
                if args.baseline_receipt is not None:
                    raise RuntimeRecipeDiagnosticError(
                        "--baseline-receipt requires --recipe-invalidations or --recipe-reload"
                    )
                result = diagnose_project_runtime(
                    suite_root,
                    args.workspace,
                    launch_receipt=args.receipt,
                    artifact_roots=args.artifact_root,
                    configuration=active_configuration,
                )
        elif args.command == "runtime-worldgen-audit":
            assert active_configuration is not None
            result = audit_project_worldgen(
                suite_root,
                args.workspace,
                runtime_root=args.runtime_root,
                configuration=active_configuration,
            )
        elif args.command == "runtime-worldgen-fingerprint":
            result = fingerprint_runtime_worldgen(
                suite_root,
                args.world,
                output=args.output,
            )
        elif args.command == "runtime-worldgen-compare":
            result = compare_runtime_worldgen(
                suite_root,
                args.left,
                args.right,
                output=args.output,
            )
        elif args.command == "runtime-worldgen-block-delta":
            result = attribute_runtime_worldgen_blocks(
                suite_root,
                args.left_world,
                args.right_world,
                output=args.output,
            )
        else:
            assert args.command == "runtime-java"
            assert active_configuration is not None
            assert resolved_bindings is not None
            result = ensure_java_runtime(
                suite_root,
                configuration=active_configuration,
                resolved_bindings=resolved_bindings,
            )
    except (
        ActiveInstanceError,
        BlueprintStageError,
        ComponentGraphError,
        FeatureStudioError,
        HostAdapterV3Error,
        JavaRuntimeError,
        MaterialFluidFlowError,
        PackwizMaterializationError,
        ProjectInspectionError,
        RegistrationWizardError,
        RuntimeBootstrapError,
        RuntimeDiagnosisError,
        RuntimeRecipeInvalidationError,
        RuntimeRecipeDiagnosticError,
        RuntimeWorldgenAuditError,
        RuntimeWorldgenFingerprintError,
        RuntimeLaunchError,
        RuntimeObserveError,
        RuntimePlanError,
        OSError,
        ValueError,
    ) as exc:
        print(f"workbench: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(
            result,
            sys.stdout,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    else:
        if args.command == "config":
            print(_human_configuration(result))
        elif args.command == "environment":
            print(render_environment_status(result, stream=sys.stdout))
        elif args.command == "host-status":
            print(_human_host_status(result))
        elif args.command == "service-call":
            print(
                "Service V3 exchange: "
                f"{result['request_count']} request(s), receipt {result['id']}"
            )
        elif args.command == "service-probe-v3":
            print(
                "Installed Service V3: READY "
                f"({result['service_instance_id']})"
            )
        elif args.command == "feature-service":
            action_result = result["result"]
            if args.feature_service_action == "submit":
                outcome = action_result["outcome"]
                if outcome["state"] == "accepted":
                    detail = (
                        f"job {outcome['job']['job_id']} / "
                        f"submission {outcome['job']['job_submission_id']}"
                    )
                else:
                    detail = (
                        "owner result "
                        f"{outcome['value']['owner_result_id']}"
                    )
            elif args.feature_service_action == "events":
                page = action_result["page"]["outcome"]["value"]
                detail = (
                    f"{len(page['events'])} event(s), next cursor "
                    f"{page['next_cursor']}"
                )
            elif args.feature_service_action == "cancel":
                handle = action_result["outcome"]["value"]
                detail = (
                    f"job {handle['job_id']} at {handle['mutation_state']}"
                )
            else:
                wrapper = action_result["outcome"]["value"]
                detail = f"owner result {wrapper['owner_result_id']}"
            print(
                "Feature Studio Service V3 "
                f"{args.feature_service_action}: {detail}"
            )
        elif args.command == "inspect":
            print(_human_inspection(result))
        elif args.command == "initialize":
            print(_human_active_instance(result))
        elif args.command == "register":
            if result["format"] == "workbench-registration-capabilities-v1":
                print(_human_registration_capabilities(result))
            elif result["format"] == "workbench-registration-result-v1":
                print(_human_registration_result(result))
            elif not previewed_registration:
                print(_human_registration_plan(result))
        elif args.command == "blueprint-stage":
            print(_human_blueprint_stage(result))
        elif args.command == "material-fluid":
            if result["format"] == "workbench-material-fluid-flow-plan-v2":
                print(_human_material_fluid_plan(result))
            else:
                print(_human_material_fluid_result(result))
        elif args.command == "feature":
            print(_human_feature_studio_result(result))
        elif args.command == "runtime-plan":
            print(_human_runtime_plan(result))
        elif args.command == "runtime-bootstrap":
            print(_human_runtime_bootstrap(result))
        elif args.command == "runtime-materialize":
            print(_human_runtime_materialize(result))
        elif args.command == "runtime-launch":
            print(_human_runtime_launch(result))
        elif args.command == "runtime-observe":
            print(_human_runtime_observation(result))
        elif args.command == "runtime-diagnose":
            if args.recipe_invalidations:
                if args.baseline_receipt is not None:
                    print(_human_recipe_invalidation_comparison(result))
                else:
                    print(_human_recipe_invalidation_diagnostic(result))
            elif args.recipe_reload:
                if args.baseline_receipt is not None:
                    print(_human_recipe_conflict_comparison(result))
                else:
                    print(_human_recipe_reload_diagnostic(result))
            else:
                print(_human_runtime_diagnosis(result))
        elif args.command == "runtime-worldgen-audit":
            print(_human_runtime_worldgen_audit(result))
        elif args.command == "runtime-worldgen-fingerprint":
            print(_human_runtime_worldgen_fingerprint(result))
        elif args.command == "runtime-worldgen-compare":
            print(_human_runtime_worldgen_comparison(result))
        elif args.command == "runtime-worldgen-block-delta":
            print(_human_runtime_worldgen_block_delta(result))
        else:
            print(_human_java_runtime(result))
    if (
        args.command == "runtime-launch"
        and result["outcome"] != "checkpoint-reached"
    ):
        return 1
    if (
        args.command == "runtime-observe"
        and result["outcome"] != "completed"
    ):
        return 1
    if (
        args.command == "material-fluid"
        and result["format"] == "workbench-material-fluid-flow-result-v2"
        and result["outcome"] != "runtime-completed"
    ):
        return 1
    if (
        args.command == "feature"
        and args.feature_action == "verify"
        and not args.show
    ):
        if result["state"] != "complete":
            return 1
    return 0
