"""Orchestrate profile Blueprints against the selected installed runtime."""

from __future__ import annotations

from collections.abc import Mapping
import difflib
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any, NoReturn

from .active_instance import load_active_instance
from workbench_api import ModuleError
from workbench_api.durable_resources import DurableResourceError
from workbench_api.source_transactions import (
    SourceImage,
    SourceTransactionError,
    open_source_transaction,
)
from workbench_api.registration_attempts import (
    RegistrationImage, registration_attempts,
)
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_api.state_paths import default_suite_state_root


class RegistrationWizardError(ValueError):
    """Raised when an active-instance registration cannot be completed."""


def _fail(message: str) -> NoReturn:
    raise RegistrationWizardError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _authority_modules(suite: Path) -> tuple[Any, Any, Any]:
    for source in (
        suite / "modules/atlas/src",
        suite / "modules/blueprints/src",
    ):
        text = str(source)
        if text not in sys.path:
            sys.path.insert(0, text)
    try:
        from workbench_atlas import material_census
        from workbench_blueprints import registration_catalog
        from workbench_blueprints import registration_render
    except ImportError as exc:
        _fail(f"Workbench registration authorities are unavailable: {exc}")
    return material_census, registration_catalog, registration_render


def _state_root(suite: Path, value: Path | str | None) -> Path:
    root = (
        default_suite_state_root(suite)
        if value is None
        else Path(value).expanduser().absolute()
    )
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        _fail("Workbench state root must be a regular directory")
    return root


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"{label} must be a portable relative path")
    return path


def _active_configuration(
    suite: Path,
    configuration: WorkbenchConfiguration | None,
    config_path: Path | str | None,
) -> WorkbenchConfiguration:
    if configuration is not None and config_path is not None:
        _fail("configuration and config_path are mutually exclusive")
    try:
        return configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        _fail(f"Workbench configuration cannot be loaded: {exc}")


def _load_catalog(
    suite: Path,
    configuration: WorkbenchConfiguration,
) -> tuple[dict[str, Any], Any, Any, Path]:
    material_census, registration_catalog, registration_render = _authority_modules(suite)
    blueprints = configuration.pack_document.values.get("blueprints")
    if not isinstance(blueprints, Mapping):
        _fail("selected pack profile lacks Blueprints policy")
    assert blueprints is not None
    relative = _safe_relative(
        blueprints.get("registration_catalog"),
        "registration catalog",
    )
    profile_root = configuration.pack_document.source.path.parent.resolve()
    catalog_path = profile_root.joinpath(*relative.parts).resolve()
    if not catalog_path.is_relative_to(profile_root):
        _fail("registration catalog escapes the selected pack profile")
    try:
        catalog = registration_catalog.load_registration_catalog(catalog_path)
    except registration_catalog.RegistrationCatalogError as exc:
        _fail(f"selected registration catalog is invalid: {exc}")
    if catalog.get("profile_family_id") != configuration.pack_profile_id:
        _fail("selected registration catalog belongs to another pack profile")
    return catalog, material_census, registration_render, profile_root


def registration_capabilities(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    pattern_key: str | None = None,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Describe all known families and ready forms for the active instance."""

    suite = Path(suite_root).resolve()
    active_configuration = _active_configuration(
        suite,
        configuration,
        config_path,
    )
    selection = load_active_instance(
        suite,
        workspace_root,
        state_root=state_root,
        configuration=active_configuration,
    )
    catalog, _material_census, registration_render, _profile_root = _load_catalog(
        suite,
        active_configuration,
    )
    patterns = catalog["patterns"]
    if pattern_key is not None:
        patterns = [row for row in patterns if row["key"] == pattern_key]
        if not patterns:
            _fail(f"unknown registration pattern: {pattern_key}")
    options: dict[str, dict[str, list[str]]] = {}
    for pattern in patterns:
        try:
            resolved = registration_render.runtime_question_options(
                catalog,
                pattern["key"],
                selection["payload_path"],
            )
        except registration_render.RegistrationRenderError as exc:
            _fail(f"cannot resolve wizard choices for {pattern['key']}: {exc}")
        if resolved:
            options[pattern["key"]] = resolved
    public_selection = {
        key: value
        for key, value in selection.items()
        if key not in {"instance_path", "payload_path"}
    }
    return {
        "format": "workbench-registration-capabilities-v1",
        "schema_version": 1,
        "catalog_id": catalog["catalog_id"],
        "profile_family_id": catalog["profile_family_id"],
        "authority_order": catalog["authority_order"],
        "observations": catalog["observations"],
        "families": catalog["families"],
        "patterns": patterns,
        "runtime_options": options,
        "active_instance": public_selection,
    }


def _public_operation(operation: dict[str, Any], payload: Path) -> dict[str, Any]:
    relative = _safe_relative(operation.get("path"), "registration operation path")
    target = payload.joinpath(*relative.parts)
    if target.is_symlink() or not target.is_file():
        _fail(f"registration operation target is not regular: {relative}")
    before = target.read_bytes()
    expected_before = operation.get("before_sha256")
    content = operation.get("content")
    expected_after = operation.get("content_sha256")
    if not isinstance(content, bytes):
        _fail(f"registration operation lacks bytes: {relative}")
    if sha256(before).hexdigest() != expected_before:
        _fail(f"registration baseline changed while planning: {relative}")
    if sha256(content).hexdigest() != expected_after:
        _fail(f"registration rendered content identity differs: {relative}")
    try:
        before_text = before.decode("utf-8").splitlines(keepends=True)
        after_text = content.decode("utf-8").splitlines(keepends=True)
    except UnicodeError as exc:
        _fail(f"registration V1 supports UTF-8 text updates only: {relative}: {exc}")
    diff = "".join(difflib.unified_diff(
        before_text,
        after_text,
        fromfile=f"a/{relative.as_posix()}",
        tofile=f"b/{relative.as_posix()}",
    ))
    if not diff:
        _fail(f"registration operation has no diff: {relative}")
    return {
        "operation": operation.get("operation"),
        "path": relative.as_posix(),
        "before_sha256": expected_before,
        "content_sha256": expected_after,
        "size": len(content),
        "diff": diff,
    }


def _render(
    suite: Path,
    workspace: Path | str,
    pattern_key: str,
    answers: dict[str, Any],
    state_root: Path | str | None,
    configuration: WorkbenchConfiguration,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path, dict[str, Any]]:
    selection = load_active_instance(
        suite,
        workspace,
        state_root=state_root,
        configuration=configuration,
    )
    catalog, material_census, registration_render, profile_root = _load_catalog(
        suite,
        configuration,
    )
    pattern = next(
        (row for row in catalog["patterns"] if row["key"] == pattern_key),
        None,
    )
    if pattern is None:
        _fail(f"unknown registration pattern: {pattern_key}")
    facts: dict[str, Any] = {}
    if pattern["renderer"] == "gregtech-material-backed-fluid-v1":
        try:
            facts["material_census"] = material_census.census_material_directory(
                selection["payload_path"]
            )
        except material_census.MaterialCensusError as exc:
            _fail(f"cannot census active-instance materials: {exc}")
    try:
        rendered = registration_render.render_registration(
            catalog,
            pattern_key,
            profile_root,
            selection["payload_path"],
            answers,
            facts=facts,
        )
    except registration_render.RegistrationRenderError as exc:
        _fail(str(exc))
    operations = rendered.get("operations")
    if not isinstance(operations, list) or not operations:
        _fail("registration pattern produced no operations")
    public = [
        _public_operation(operation, selection["payload_path"])
        for operation in operations
    ]
    public.sort(key=lambda row: row["path"])
    operations.sort(key=lambda row: row["path"])
    return rendered, operations, selection["payload_path"], {
        "selection": selection,
        "catalog": catalog,
        "public_operations": public,
    }


def _plan(
    rendered: dict[str, Any],
    payload: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    selection = context["selection"]
    catalog = context["catalog"]
    public = context["public_operations"]
    operation_identity = [
        {
            key: row[key]
            for key in ("operation", "path", "before_sha256", "content_sha256", "size")
        }
        for row in public
    ]
    plan_id = "sha256:" + sha256(_canonical_bytes({
        "selection_id": selection["selection_id"],
        "catalog_id": catalog["catalog_id"],
        "pattern": rendered["pattern"],
        "effective_answers": rendered["effective_answers"],
        "operations": operation_identity,
    })).hexdigest()
    return {
        "format": "workbench-registration-plan-v1",
        "schema_version": 1,
        "plan_id": plan_id,
        "state": "ready",
        "operation_class": "local-mutation",
        "profile_family_id": catalog["profile_family_id"],
        "catalog_id": catalog["catalog_id"],
        "active_instance": {
            "selection_id": selection["selection_id"],
            "root_uri": selection["instance"]["root_uri"],
            "payload_root_uri": payload.as_uri(),
        },
        "pattern": rendered["pattern"],
        "effective_answers": rendered["effective_answers"],
        "evidence": rendered["evidence"],
        "operations": public,
        "outstanding_checks": [
            "Relaunch the selected Cleanroom instance and require Groovy compilation to succeed.",
            *(
                ["Confirm the selected recipe map accepts the rendered item/fluid slot counts."]
                if rendered["pattern"]["renderer"] == "gregtech-machine-recipe-v1"
                else []
            ),
        ],
    }


def plan_active_registration(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    pattern_key: str,
    answers: dict[str, Any],
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Preview exact edits against the selected runtime without changing it."""

    suite = Path(suite_root).resolve()
    active_configuration = _active_configuration(
        suite,
        configuration,
        config_path,
    )
    rendered, operations, payload, context = _render(
        suite,
        workspace_root,
        pattern_key,
        answers,
        state_root,
        active_configuration,
    )
    return _plan(rendered, payload, context)


def apply_active_registration(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    pattern_key: str,
    answers: dict[str, Any],
    expected_plan_id: str | None = None,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Apply one reviewed plan and retain bytes needed to undo its edits."""

    suite = Path(suite_root).resolve()
    active_configuration = _active_configuration(
        suite,
        configuration,
        config_path,
    )
    rendered, operations, payload, context = _render(
        suite,
        workspace_root,
        pattern_key,
        answers,
        state_root,
        active_configuration,
    )
    plan = _plan(rendered, payload, context)
    if expected_plan_id is not None and plan["plan_id"] != expected_plan_id:
        _fail(
            "registration plan changed after review; inspect the new diff "
            "before applying it"
        )
    state = _state_root(suite, state_root)
    workspace = Path(workspace_root).expanduser().resolve()
    retained_images: list[RegistrationImage] = []
    source_images: list[tuple[str, SourceImage, SourceImage]] = []
    receipt_outputs: list[dict[str, Any]] = []
    for operation in operations:
        relative = _safe_relative(operation["path"], "registration operation path")
        target = payload.joinpath(*relative.parts)
        if target.is_symlink() or not target.is_file():
            _fail(f"registration target changed before apply: {relative}")
        before = target.read_bytes()
        if sha256(before).hexdigest() != operation["before_sha256"]:
            _fail(f"registration target changed before apply: {relative}")
        mode = stat.S_IMODE(target.stat().st_mode)
        retained_images.append(RegistrationImage(relative.as_posix(), before, operation["content"], mode))
        source_images.append((
            relative.as_posix(), SourceImage("file", before, mode=mode),
            SourceImage("file", operation["content"], mode=mode),
        ))
        receipt_outputs.append({
            "operation": "update", "path": relative.as_posix(),
            "before_sha256": operation["before_sha256"],
            "content_sha256": operation["content_sha256"],
            "size": len(operation["content"]),
            "backup_path": (PurePosixPath("backups") / relative).as_posix(),
        })
    try:
        with registration_attempts().open(
            state_root=state, workspace=workspace, payload=payload,
            plan_id=plan["plan_id"], selection_id=context["selection"]["selection_id"],
        ) as retained:
            receipt = {
                "format": "workbench-registration-receipt-v1", "schema_version": 1,
                "transaction_id": plan["plan_id"], "state": "prepared",
                "operation_class": "local-mutation",
                "plan": {key: value for key, value in plan.items() if key != "operations"},
                "outputs": receipt_outputs,
                "target": {
                    "instance_root_uri": context["selection"]["instance"]["root_uri"],
                    "payload_root_uri": payload.as_uri(),
                    "receipt_uri": (retained.transaction_path / "receipt.json").as_uri(),
                },
            }
            prepared_bytes = _canonical_bytes(receipt) + b"\n"
            source_transaction = None
            prepared = False
            stages = []
            attempted_count = 0
            try:
                retained.prepare(tuple(retained_images), prepared_bytes)
                prepared = True
                source_transaction = open_source_transaction(
                    payload, binding=f"registration:{plan['plan_id']}",
                    staging_token=retained.staging_token,
                )
                for ordinal, (relative, before, after) in enumerate(source_images):
                    stage = source_transaction.prepare(relative, before=before, after=after)
                    retained.record_stage(ordinal, stage.staged_relative)
                    stages.append(stage)
                for ordinal, stage in enumerate(stages):
                    retained.mark_attempted(ordinal)
                    attempted_count = ordinal + 1
                    source_transaction.commit(stage)
                if any(source_transaction.classify(stage) != "after" for stage in stages):
                    _fail("registration output changed before receipt publication")
                source_transaction.cleanup()
                receipt["state"] = "applied"
                retained.publish_applied(_canonical_bytes(receipt) + b"\n")
                retained.promote()
                return {
                    "format": "workbench-registration-result-v1", "schema_version": 1,
                    "outcome": "applied", "plan": plan, "receipt": receipt,
                }
            except Exception as exc:
                rollback_error: Exception | None = None
                if source_transaction is not None:
                    for ordinal in reversed(range(attempted_count)):
                        try:
                            relative, before, after = source_images[ordinal]
                            stage = source_transaction.attach(
                                relative, before=before, after=after,
                                staged_relative=stages[ordinal].staged_relative,
                                attempted=True,
                            )
                            observed = source_transaction.classify(stage)
                            if observed == "after":
                                source_transaction.rollback(stage)
                            elif observed != "before":
                                raise SourceTransactionError(
                                    "stale", "registration source changed after replacement attempt",
                                )
                        except Exception as rollback_exc:
                            rollback_error = rollback_error or rollback_exc
                    for stage in stages:
                        try:
                            if source_transaction.classify(stage) != "before":
                                raise SourceTransactionError(
                                    "stale", "registration source is not at its preimage after rollback",
                                )
                        except Exception as rollback_exc:
                            rollback_error = rollback_error or rollback_exc
                    try:
                        source_transaction.cleanup()
                    except Exception as cleanup_exc:
                        rollback_error = rollback_error or cleanup_exc
                if prepared:
                    try:
                        retained.restore_prepared(prepared_bytes)
                    except Exception as restore_exc:
                        rollback_error = rollback_error or restore_exc
                if rollback_error is not None:
                    try:
                        if not retained.promoted:
                            retained.promote()
                    except Exception as retention_exc:
                        rollback_error.add_note(
                            f"Registration attempt remains at {retained.path}: {retention_exc}"
                        )
                    raise RegistrationWizardError(
                        f"registration failed and rollback also failed: {rollback_error}"
                    ) from exc
                if retained.promoted:
                    _fail(
                        "registration failed after receipt promotion; source edits were rolled "
                        f"back and the retained attempt requires review: {retained.transaction_path}"
                    )
                retained.discard()
                if isinstance(exc, RegistrationWizardError):
                    raise
                _fail(f"registration transaction failed and was rolled back: {exc}")
    except (OSError, DurableResourceError, ModuleError) as exc:
        _fail(f"registration Core custody rejected the attempt: {exc}")


def inspect_active_registration_attempt(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    plan_id: str,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Inspect a retained attempt against the still-selected instance."""

    suite = Path(suite_root).resolve()
    active_configuration = _active_configuration(suite, configuration, config_path)
    selection = load_active_instance(
        suite, workspace_root, state_root=state_root,
        configuration=active_configuration,
    )
    try:
        with registration_attempts().open(
            state_root=_state_root(suite, state_root),
            workspace=Path(workspace_root).expanduser().resolve(),
            payload=selection["payload_path"], plan_id=plan_id,
            selection_id=selection["selection_id"],
        ) as attempt:
            return attempt.inspect()
    except (OSError, DurableResourceError, ModuleError) as exc:
        _fail(f"registration attempt requires review: {exc}")


def finalize_active_registration_attempt(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    plan_id: str,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Complete a proven source application interrupted before publication."""

    return _recover_active_registration_attempt(
        suite_root, workspace_root, plan_id=plan_id, state_root=state_root,
        configuration=configuration, config_path=config_path, resume=False,
    )


def resume_active_registration_attempt(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    plan_id: str,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Resume only a fully staged and ordered partial source application."""

    return _recover_active_registration_attempt(
        suite_root, workspace_root, plan_id=plan_id, state_root=state_root,
        configuration=configuration, config_path=config_path, resume=True,
    )


def _recover_active_registration_attempt(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    plan_id: str,
    state_root: Path | str | None,
    configuration: WorkbenchConfiguration | None,
    config_path: Path | str | None,
    resume: bool,
) -> dict[str, Any]:

    suite = Path(suite_root).resolve()
    active_configuration = _active_configuration(suite, configuration, config_path)
    selection = load_active_instance(
        suite, workspace_root, state_root=state_root,
        configuration=active_configuration,
    )
    try:
        with registration_attempts().open(
            state_root=_state_root(suite, state_root),
            workspace=Path(workspace_root).expanduser().resolve(),
            payload=selection["payload_path"], plan_id=plan_id,
            selection_id=selection["selection_id"],
        ) as attempt:
            inspected = attempt.resume_partial() if resume else attempt.finalize_committed()
    except (OSError, DurableResourceError, ModuleError) as exc:
        _fail(f"registration attempt requires review: {exc}")
    return {
        "format": "workbench-registration-recovery-v1",
        "schema_version": 1,
        "outcome": "applied",
        "plan_id": plan_id,
        "receipt_uri": inspected["receipt_uri"],
        "outstanding_checks": [
            "Relaunch the selected instance to check Groovy compilation and registration.",
        ],
    }


__all__ = [
    "RegistrationWizardError",
    "apply_active_registration",
    "finalize_active_registration_attempt",
    "resume_active_registration_attempt",
    "inspect_active_registration_attempt",
    "plan_active_registration",
    "registration_capabilities",
]
