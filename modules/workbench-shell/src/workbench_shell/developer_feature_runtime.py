"""Disposable Cleanroom execution for one reviewed developer feature plan.

The developer checkout is an input only.  Current tracked bytes are copied to
managed state, the four reviewed updates are applied to that copy, and the
existing Cleanroom runtime observes the resulting pack.  This module grants no
support, standard, release, or publication authority.
"""

from __future__ import annotations

from workbench_blueprints.reviewed_stage import (_paths_overlap, stage_reviewed_feature_plan)


from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, NoReturn, Sequence, cast
from uuid import uuid4


from .developer_feature import (
    RUN_FORMAT,
    RUN_KIND,
    material_fluid_recipe_workspace,
    validate_material_fluid_recipe_plan,
    verify_material_fluid_recipe_plan,
)
from .material_fluid_flow import (
    captured_material_fluid_groovy_log,
    material_fluid_runtime_capture_completed,
    material_fluid_runtime_compatibility_policy,
    summarize_material_fluid_runtime,
)
from .runtime_observe import observe_project_runtime
from .runtime_plan import plan_project_runtime


_RUN_ID = re.compile(
    rf"{re.escape(RUN_KIND)}:sha256:[0-9a-f]{{64}}\Z"
)
_ATTEMPT_ID = re.compile(r"uuid:[0-9a-f]{32}\Z")
_RUNTIME_LANE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_ASSERTION_STATES = frozenset(
    {"pending", "observed", "failed", "not-observed"}
)
_ASSERTION_MEANINGS = {
    "fml_client_load": "the disposable client reaches the exact FML loaded marker",
    "groovy_compilation": "the changed Groovy program compiles in the projected client",
    "material_registration": "the requested GregTech material identity is registered",
    "fluid_registration": "the material-backed Forge fluid identity is registered",
    "localization": "the requested client translation resolves to its intended label",
    "recipe_registration": "the exact reviewed machine recipe is registered once in its selected map",
}


class DeveloperFeatureRuntimeError(ValueError):
    """A reviewed feature cannot be staged or observed safely."""


def _fail(message: str) -> NoReturn:
    raise DeveloperFeatureRuntimeError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _seal(body: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(body)
    return {
        **material,
        "id": f"{RUN_KIND}:sha256:{sha256(_canonical_bytes(material)).hexdigest()}",
    }






def prepare_feature_runtime_attempt_parent(
    state_root: Path | str,
    workspace: Path,
    *,
    lane: str,
) -> Path:
    """Prepare one ignored, no-link runtime-attempt lane outside source."""

    if _RUNTIME_LANE.fullmatch(lane) is None:
        _fail("feature runtime lane is not a safe bounded name")
    root = Path(os.path.abspath(os.fspath(Path(state_root).expanduser())))
    if root.is_symlink() or _paths_overlap(root, workspace):
        _fail("feature runtime state cannot be a link or overlap source")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DeveloperFeatureRuntimeError(
            f"cannot prepare feature runtime state: {exc}"
        ) from exc
    if root.is_symlink() or not root.is_dir():
        _fail("feature runtime state is not an ordinary directory")
    current = root
    for part in ("runtime", lane, "attempts"):
        current = current / part
        if current.is_symlink():
            _fail("feature runtime state traverses a symbolic link")
        try:
            current.mkdir(exist_ok=True)
        except OSError as exc:
            raise DeveloperFeatureRuntimeError(
                f"cannot prepare feature runtime attempt state: {exc}"
            ) from exc
        if current.is_symlink() or not current.is_dir():
            _fail("feature runtime attempt state is not an ordinary directory")
    return current


def _prepare_attempt_parent(state_root: Path | str, workspace: Path) -> Path:
    return prepare_feature_runtime_attempt_parent(
        state_root,
        workspace,
        lane="material-fluid-recipe",
    )
















def stage_material_fluid_recipe_plan(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    destination: Path | str,
) -> dict[str, Any]:
    """Copy tracked bytes and apply one validated plan only to that copy."""

    suite = Path(suite_root).resolve()
    reviewed = validate_material_fluid_recipe_plan(plan)
    return stage_reviewed_feature_plan(
        reviewed,
        destination,
        workspace=material_fluid_recipe_workspace(reviewed),
        verify=lambda: verify_material_fluid_recipe_plan(suite, reviewed),
        apply_operations=True,
        result_format="workbench-developer-material-fluid-recipe-stage-v1",
    )


def _profile_authority(suite: Path) -> Any:
    from workbench_api.profile_extensions import require_profile_extension
    return require_profile_extension("workbench.material_recipe_observers", "supersymmetry")


def _prepare_probe(
    suite: Path,
    plan: Mapping[str, Any],
    temporary: Path,
    destination: Path,
) -> tuple[Any, Path, dict[str, Any]]:
    authority = _profile_authority(suite)
    request = plan["request"]
    try:
        spec = authority.MaterialFluidRecipeProbeSpec(
            registry_name=request["registry_name"],
            symbol_name=request["symbol"],
            material_id=request["material_id"],
            color_rgb=int(request["color"].removeprefix("0x"), 16),
            translation=request["translation"],
            recipe_map_alias=request["recipe_map"],
            recipe_map_registry_name=request["recipe_map_registry_name"],
            input_fluid=request["input_fluid"],
            input_amount=request["input_amount"],
            output_amount=request["output_amount"],
            duration=request["duration"],
            voltage_tier=request["voltage_tier"],
            source_plan_id=plan["id"],
        )
        script = authority.build_material_fluid_recipe_probe(spec)
        overlay = authority.build_material_fluid_recipe_probe_overlay(spec)
    except (AttributeError, TypeError, ValueError) as exc:
        raise DeveloperFeatureRuntimeError(
            f"cannot prepare the Supersymmetry recipe observation: {exc}"
        ) from exc
    probe_root = temporary / "observation-probe"
    probe_root.mkdir()
    script_path = probe_root / "MaterialFluidRecipeAssertion.groovy"
    overlay_path = probe_root / "overlay-v1.json"
    script_path.write_bytes(script)
    overlay_bytes = json.dumps(
        overlay,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    overlay_path.write_bytes(overlay_bytes)
    retained = destination / "observation-probe"
    return spec, overlay_path, {
        "probe_id": spec.probe_id,
        "source_plan_id": plan["id"],
        "script_sha256": sha256(script).hexdigest(),
        "script_size": len(script),
        "script_uri": (retained / script_path.name).as_uri(),
        "overlay_id": overlay["patch_id"],
        "overlay_spec_sha256": sha256(overlay_bytes).hexdigest(),
        "overlay_spec_uri": (retained / overlay_path.name).as_uri(),
        "projection_target": overlay["target"]["path"],
        "operation": "disposable-observation-file-overlay",
    }


def _pending_assertions() -> dict[str, dict[str, str]]:
    return {
        name: {"state": "pending", "meaning": meaning}
        for name, meaning in _ASSERTION_MEANINGS.items()
    }


def validate_material_fluid_recipe_run(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_material_fluid_recipe_plan(plan)
    if type(value) is not dict:
        _fail("feature runtime receipt must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        set(receipt)
        != {
            "assertions",
            "attempt_id",
            "format",
            "id",
            "kind",
            "limitations",
            "observation_probe",
            "operation_class",
            "outcome",
            "plan_id",
            "profile_observation",
            "runtime",
            "schema_version",
            "source",
            "stage",
            "state",
            "target",
        }
        or receipt.get("format") != RUN_FORMAT
        or receipt.get("kind") != RUN_KIND
        or receipt.get("schema_version") != 1
        or receipt.get("operation_class") != "local-disposable-runtime"
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state") not in {"complete", "incomplete"}
        or receipt.get("outcome")
        not in {"runtime-completed", "runtime-assertion-failed", "runtime-incomplete", "failed"}
        or type(supplied) is not str
        or _RUN_ID.fullmatch(supplied) is None
        or supplied != _seal(body)["id"]
        or type(receipt.get("attempt_id")) is not str
        or _ATTEMPT_ID.fullmatch(receipt["attempt_id"]) is None
    ):
        _fail("feature runtime receipt identity or shape changed")
    assertions = receipt.get("assertions")
    if type(assertions) is not dict or set(assertions) != set(_ASSERTION_MEANINGS):
        _fail("feature runtime assertion set changed")
    for name, assertion in assertions.items():
        if (
            type(assertion) is not dict
            or set(assertion) != {"meaning", "state"}
            or assertion.get("meaning") != _ASSERTION_MEANINGS[name]
            or assertion.get("state") not in _ASSERTION_STATES
        ):
            _fail("feature runtime assertion changed")
    all_observed = all(
        assertion["state"] == "observed" for assertion in assertions.values()
    )
    if (receipt["state"] == "complete") != (
        receipt["outcome"] == "runtime-completed" and all_observed
    ):
        _fail("feature runtime completion contradicts its assertions")
    target = receipt.get("target")
    source = receipt.get("source")
    if (
        type(target) is not dict
        or set(target) != {"attempt_root_uri", "receipt_uri"}
        or not all(type(item) is str and item.startswith("file:") for item in target.values())
        or type(source) is not dict
        or source.get("workspace_uri") != reviewed["workspace_uri"]
        or source.get("plan_id") != reviewed["id"]
        or type(receipt.get("limitations")) is not list
        or not receipt["limitations"]
    ):
        _fail("feature runtime source or target binding changed")
    return cast(dict[str, Any], receipt)


def run_material_fluid_recipe(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    consent_plan_id: str,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    launcher: str = "prism",
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    packwiz_executable: Path | str | None = None,
    seed_roots: Sequence[Path | str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    timeout_seconds: float = 600.0,
    attach_timeout: float = 120.0,
    session_timeout: float = 21_600.0,
) -> dict[str, Any]:
    """Stage, launch, observe, and retain one exact reviewed recipe plan."""

    suite = Path(suite_root).resolve()
    reviewed = validate_material_fluid_recipe_plan(plan)
    if consent_plan_id != reviewed["id"]:
        _fail("run requires explicit consent to the exact reviewed plan ID")
    if launcher not in {"prism", "multimc"}:
        _fail("feature runtime launcher must be prism or multimc")
    workspace = material_fluid_recipe_workspace(reviewed)
    launcher_path = Path(launcher_root).expanduser().resolve()
    state = Path(state_root).expanduser().resolve(strict=False)
    if _paths_overlap(launcher_path, workspace) or _paths_overlap(state, workspace):
        _fail("feature runtime outputs cannot overlap the developer checkout")
    parent = _prepare_attempt_parent(state, workspace)
    token = uuid4().hex
    destination = parent / token
    if destination.exists() or destination.is_symlink():
        _fail("feature runtime attempt identity unexpectedly exists")
    try:
        destination.mkdir(mode=0o700)
    except OSError as exc:
        raise DeveloperFeatureRuntimeError(
            f"cannot create feature runtime attempt: {exc}"
        ) from exc
    runtime_state = state / "cleanroom-runtime"
    assertions = _pending_assertions()
    stage: dict[str, Any] | None = None
    runtime_result: dict[str, Any] | None = None
    runtime_summary: dict[str, Any] = {"state": "not-run"}
    profile_observation: dict[str, Any] | None = None
    observation_probe: dict[str, Any] | None = None
    initial_verification = verify_material_fluid_recipe_plan(suite, reviewed)
    error: Exception | None = None
    try:
        if initial_verification.get("state") != "ready":
            _fail(f"feature plan is stale: {initial_verification.get('reason')}")
        authorized_patches, compatibility_policy = (
            material_fluid_runtime_compatibility_policy(suite)
        )
        staged_workspace = destination / "workspace"
        stage = stage_material_fluid_recipe_plan(
            suite,
            reviewed,
            staged_workspace,
        )
        expected_runtime_plan = plan_project_runtime(
            suite,
            staged_workspace,
            side="client",
            launcher=launcher,
            state_root=runtime_state,
        )
        if (
            expected_runtime_plan.get("state") != "ready"
            or expected_runtime_plan.get("blockers") != []
            or expected_runtime_plan.get("workspace")
            != {
                "root_uri": staged_workspace.as_uri(),
                "revision": stage["revision"],
                "dirty": False,
            }
        ):
            _fail("staged feature runtime plan is not exactly ready")
        observation_spec, observation_overlay, observation_probe = _prepare_probe(
            suite,
            reviewed,
            destination,
            destination,
        )
        runtime_result = observe_project_runtime(
            suite,
            staged_workspace,
            launcher=launcher,
            launcher_executable=launcher_executable,
            launcher_root=launcher_path,
            launcher_profile=launcher_profile,
            launcher_java=launcher_java,
            launcher_java_state=launcher_java_state,
            packwiz_executable=packwiz_executable,
            seed_roots=seed_roots,
            memory_mib=memory_mib,
            offline_name=offline_name,
            compatibility_patches=(*authorized_patches, observation_overlay),
            timeout_seconds=timeout_seconds,
            attach_timeout=attach_timeout,
            session_timeout=session_timeout,
            state_root=runtime_state,
        )
        runtime_summary, base_assertions = summarize_material_fluid_runtime(
            runtime_result,
            expected_evidence_root=runtime_state / "evidence/runtime",
            launcher_root=launcher_path,
            source_workspace=workspace,
            staged_workspace=staged_workspace,
            expected_runtime_plan=expected_runtime_plan,
            compatibility_policy=compatibility_policy,
            probe=observation_probe,
        )
        assertions.update(base_assertions)
        authority = _profile_authority(suite)
        log_bytes, capture = captured_material_fluid_groovy_log(
            runtime_state / "evidence/runtime",
            runtime_result,
            observation_probe,
            compatibility_policy,
            expected_runtime_plan,
            staged_workspace,
        )
        profile_observation = authority.interpret_material_fluid_recipe_observation(
            observation_spec,
            groovy_log_bytes=log_bytes,
            capture=capture,
        )
        profile_assertions = profile_observation.get("developer_assertions")
        expected_profile = set(_ASSERTION_MEANINGS) - {"fml_client_load"}
        if (
            type(profile_assertions) is not dict
            or set(profile_assertions) != expected_profile
            or any(value not in {"observed", "failed"} for value in profile_assertions.values())
        ):
            _fail("profile recipe assessment lacks exact developer assertions")
        for name, value in profile_assertions.items():
            assertions[name] = {
                "state": value,
                "meaning": _ASSERTION_MEANINGS[name],
            }
    except Exception as exc:  # Retain every ordinary failure as an incomplete run.
        error = exc
        runtime_summary = {
            "state": "failed",
            "error": {"kind": type(exc).__name__, "message": str(exc)[:2000]},
        }
        for assertion in assertions.values():
            if assertion["state"] == "pending":
                assertion["state"] = "not-observed"

    final_verification = verify_material_fluid_recipe_plan(suite, reviewed)
    runtime_completed = (
        error is None
        and runtime_result is not None
        and material_fluid_runtime_capture_completed(runtime_result)
    )
    profile_observed = (
        error is None
        and type(profile_observation) is dict
        and profile_observation.get("state") == "observed"
    )
    all_observed = all(
        assertion["state"] == "observed" for assertion in assertions.values()
    )
    source_unchanged = final_verification.get("state") == "ready"
    if runtime_completed and profile_observed and all_observed and source_unchanged:
        outcome = "runtime-completed"
        result_state = "complete"
    elif error is not None:
        outcome = "failed"
        result_state = "incomplete"
    elif runtime_completed:
        outcome = "runtime-assertion-failed"
        result_state = "incomplete"
    else:
        outcome = "runtime-incomplete"
        result_state = "incomplete"

    body = {
        "assertions": assertions,
        "attempt_id": "uuid:" + token,
        "format": RUN_FORMAT,
        "kind": RUN_KIND,
        "limitations": [
            "The developer checkout is an input only; all planned edits were applied to a disposable tracked-file copy.",
            "This observation applies only to the exact staged projection and retained runtime capture.",
            "The run does not grant tested-profile support, stable-standard admission, release, or publication authority.",
        ],
        "observation_probe": observation_probe,
        "operation_class": "local-disposable-runtime",
        "outcome": outcome,
        "plan_id": reviewed["id"],
        "profile_observation": profile_observation,
        "runtime": runtime_summary,
        "schema_version": 1,
        "source": {
            "plan_id": reviewed["id"],
            "workspace_uri": reviewed["workspace_uri"],
            "initial_verification": initial_verification,
            "final_verification": final_verification,
        },
        "stage": stage,
        "state": result_state,
        "target": {
            "attempt_root_uri": destination.as_uri(),
            "receipt_uri": (destination / "receipt.json").as_uri(),
        },
    }
    receipt = validate_material_fluid_recipe_run(_seal(body), reviewed)
    receipt_path = destination / "receipt.json"
    temporary_receipt = destination / ".receipt.json.tmp"
    try:
        with temporary_receipt.open("xb") as output:
            output.write(
                json.dumps(
                    receipt,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_receipt, receipt_path)
    except OSError as exc:
        raise DeveloperFeatureRuntimeError(
            f"cannot publish feature runtime attempt: {exc}"
        ) from exc
    finally:
        temporary_receipt.unlink(missing_ok=True)
    return receipt


__all__ = [
    "DeveloperFeatureRuntimeError",
    "prepare_feature_runtime_attempt_parent",
    "run_material_fluid_recipe",
    "stage_material_fluid_recipe_plan",
    "validate_material_fluid_recipe_run",
]
