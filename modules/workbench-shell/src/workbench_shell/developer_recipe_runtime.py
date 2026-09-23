"""Paired disposable runtime comparison for one reviewed recipe ADD plan."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Mapping, NoReturn, Sequence, cast
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import uuid4

from workbench_project_intelligence.git_observation import (
    GitObservationError,
    require_configured_git_executable,
)

from workbench_blueprints.reviewed_stage import stage_reviewed_feature_plan
from .developer_feature_runtime import (
    prepare_feature_runtime_attempt_parent,
)
from workbench_blueprints.profile_construction import recipe_change_authority
from .material_fluid_flow import (
    MaterialFluidFlowError,
    _preflight_explicit_java_state,
    _preflight_mutable_path,
    _preflight_runtime_state,
    captured_disposable_groovy_log,
    disposable_runtime_capture_completed,
    disposable_runtime_receipt_evidence,
    material_fluid_runtime_compatibility_policy,
    summarize_disposable_runtime,
    validate_retained_disposable_runtime,
)
from .runtime_observe import (
    RuntimeObserveError,
    observe_project_runtime,
    preflight_close_observer_launcher,
    require_runtime_processes_closed,
)
from .runtime_plan import plan_project_runtime


FORMAT = "workbench-developer-recipe-change-runtime-comparison-v2"
KIND = "workbench-developer-recipe-change-runtime-comparison"
STAGE_FORMAT = "workbench-developer-recipe-change-runtime-stage-v1"
_ID = re.compile(rf"{re.escape(KIND)}:sha256:[0-9a-f]{{64}}\Z")
_ATTEMPT_ID = re.compile(r"uuid:([0-9a-f]{32})\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_ROLES = ("baseline", "candidate")
_ORDERS = ("baseline-first", "candidate-first")
_ASSERTION_KEYS = {
    "atlas_observed_exact_addition",
    "baseline_and_candidate_share_source_tree",
    "baseline_is_unmodified_source",
    "both_client_cold_starts_observed",
    "candidate_is_exact_reviewed_delta",
    "developer_source_unchanged",
}
_LIMITATIONS = [
    "Both sides are fresh disposable clients bound to one reviewed source tree; the developer checkout is never mutated.",
    "One baseline-first or candidate-first pair does not remove execution-order, cache-warming, or launcher confounds.",
    "Dedicated-server parity is still required because the current close-observed launcher path is client-only.",
    "Exact registration and lookup change do not prove player progression, recipe execution, stable support, release, or publication.",
    "Deep progression comparison requires two capture-protocol-compatible Atlas runtime graphs and is a separate bounded analysis.",
    "Reopening is bound to the exact retained observer protocol and current decoder source digest; historical decoder dispatch is not available.",
]


class RecipeRuntimeComparisonError(ValueError):
    """One recipe comparison cannot be staged, observed, or reopened safely."""


class RecipeRuntimeLifecycleUnresolvedError(RecipeRuntimeComparisonError):
    """Raised before publication when an owned process may remain live."""


def _fail(message: str) -> NoReturn:
    raise RecipeRuntimeComparisonError(message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecipeRuntimeComparisonError(
            "recipe runtime comparison is not canonical JSON"
        ) from exc


def _seal(body: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(body)
    return {
        **material,
        "id": f"{KIND}:sha256:{sha256(_canonical_bytes(material)).hexdigest()}",
    }


def _observer(suite: Path) -> Any:
    from workbench_atlas.profile_observation import recipe_observer

    return recipe_observer("supersymmetry")


def _observer_protocol(suite: Path, observer: Any) -> dict[str, Any]:
    from workbench_api.profile_extensions import profile_extension_identity

    identity = profile_extension_identity("workbench.recipe_observers", "supersymmetry")
    if observer is not _observer(suite):
        _fail("recipe runtime observer differs from the admitted owner")
    constants = {
        "assessment_format": getattr(observer, "ASSESSMENT_FORMAT", None),
        "comparison_format": getattr(observer, "COMPARISON_FORMAT", None),
        "contract_format": getattr(observer, "CONTRACT_FORMAT", None),
        "marker_prefix": getattr(observer, "MARKER_PREFIX", None),
        "pack_profile_id": getattr(observer, "PACK_PROFILE_ID", None),
        "platform_profile_id": getattr(observer, "PLATFORM_PROFILE_ID", None),
    }
    if any(type(value) is not str or not value for value in constants.values()):
        _fail("recipe runtime observer contract is malformed")
    body = {
        **constants,
        "decoder_path": identity["module"].replace(".", "/") + ".py",
        "decoder_sha256": identity["sha256"],
        "format": "workbench-recipe-change-runtime-observer-protocol-v1",
        "marker_format": "workbench-recipe-change-runtime-marker-v1",
        "schema_version": 1,
    }
    return {
        **body,
        "id": "workbench-recipe-change-runtime-observer-protocol:sha256:"
        + sha256(_canonical_bytes(body)).hexdigest(),
    }


def _local_uri(value: Any, label: str) -> Path:
    if type(value) is not str:
        _fail(f"{label} is not a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} is not a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        _fail(f"{label} is not absolute")
    return path.resolve()


def _write_probe(
    observer: Any,
    spec: Any,
    side_root: Path,
) -> tuple[Path, dict[str, Any]]:
    overlay_path, probe, script, overlay_bytes = _expected_probe(
        observer, spec, side_root
    )
    probe_root = side_root / "observation-probe"
    probe_root.mkdir()
    script_path = probe_root / "RecipeChangeAssertion.groovy"
    for path, payload in ((script_path, script), (overlay_path, overlay_bytes)):
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return overlay_path, probe


def _expected_probe(
    observer: Any,
    spec: Any,
    side_root: Path,
) -> tuple[Path, dict[str, Any], bytes, bytes]:
    script = observer.build_recipe_change_probe(spec)
    overlay = observer.build_recipe_change_probe_overlay(spec)
    probe_root = side_root / "observation-probe"
    script_path = probe_root / "RecipeChangeAssertion.groovy"
    overlay_path = probe_root / "overlay-v1.json"
    overlay_bytes = json.dumps(
        overlay, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    return overlay_path, {
        "probe_id": spec.probe_id,
        "contract_id": spec.contract_id,
        "projection_role": spec.projection_role,
        "physical_side": spec.physical_side,
        "source_plan_id": spec.source_plan_id,
        "script_sha256": sha256(script).hexdigest(),
        "script_size": len(script),
        "script_uri": script_path.as_uri(),
        "overlay_id": overlay["patch_id"],
        "overlay_spec_sha256": sha256(overlay_bytes).hexdigest(),
        "overlay_spec_uri": overlay_path.as_uri(),
        "projection_target": overlay["target"]["path"],
        "operation": "disposable-observation-file-overlay",
    }, script, overlay_bytes


def _runtime_capture(
    custody: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    return {
        "groovy_log_sha256": custody["groovy_log_sha256"],
        "groovy_log_uri": custody["groovy_log_uri"],
        "physical_side": "client",
        "projection_role": role,
        "runtime_receipt_sha256": custody["session_receipt_sha256"],
        "runtime_receipt_size": custody["session_receipt_size"],
        "runtime_receipt_uri": custody["session_receipt_uri"],
    }


def _error_payload(exc: Exception, *, phase: str) -> dict[str, str]:
    kind = type(exc).__name__
    encoded = (str(exc) or kind).encode("utf-8")[:2000]
    return {
        "kind": kind,
        "message": encoded.decode("utf-8", "ignore") or kind,
        "phase": phase,
    }


def _retained_evidence(
    runtime_result: Mapping[str, Any],
    *,
    log_bytes: bytes | None = None,
    custody: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = {
        **disposable_runtime_receipt_evidence(runtime_result),
        "groovy_log": None,
    }
    if log_bytes is not None and custody is not None:
        evidence["groovy_log"] = {
            "sha256": custody["groovy_log_sha256"],
            "size": len(log_bytes),
            "uri": custody["groovy_log_uri"],
        }
    return evidence


def _failed_side_with_evidence(
    exc: Exception,
    *,
    phase: str,
    probe: Mapping[str, Any],
    runtime: Mapping[str, Any],
    retained_evidence: Mapping[str, Any],
    assessment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "assessment": dict(assessment) if assessment is not None else None,
        "error": _error_payload(exc, phase=phase),
        "outcome": "failed",
        "probe": dict(probe),
        "retained_evidence": dict(retained_evidence),
        "runtime": dict(runtime),
        "state": "incomplete",
    }


def _run_side(
    suite: Path,
    source_workspace: Path,
    staged_workspace: Path,
    stage: Mapping[str, Any],
    observer: Any,
    spec: Any,
    side_root: Path,
    *,
    launcher: str,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    launcher_profile: str | None,
    launcher_java: Path | str | None,
    launcher_java_state: Path | str | None,
    packwiz_executable: Path | str | None,
    seed_roots: Sequence[Path | str],
    memory_mib: int,
    offline_name: str,
    timeout_seconds: float,
    attach_timeout: float,
    session_timeout: float,
) -> dict[str, Any]:
    runtime_state = side_root / "cleanroom-runtime"
    expected = plan_project_runtime(
        suite,
        staged_workspace,
        side="client",
        launcher=launcher,
        state_root=runtime_state,
    )
    if (
        expected.get("state") != "ready"
        or expected.get("blockers") != []
        or expected.get("workspace")
        != {
            "root_uri": staged_workspace.as_uri(),
            "revision": stage["revision"],
            "dirty": False,
        }
    ):
        _fail(f"{spec.projection_role} runtime plan is not exactly ready")
    overlays, compatibility = material_fluid_runtime_compatibility_policy(suite)
    overlay_path, probe = _write_probe(observer, spec, side_root)
    try:
        result = observe_project_runtime(
            suite,
            staged_workspace,
            launcher=launcher,
            state_root=runtime_state,
            launcher_executable=launcher_executable,
            launcher_root=launcher_root,
            launcher_profile=launcher_profile,
            launcher_java=launcher_java,
            launcher_java_state=launcher_java_state,
            packwiz_executable=packwiz_executable,
            seed_roots=seed_roots,
            memory_mib=memory_mib,
            offline_name=offline_name,
            compatibility_patches=(*overlays, overlay_path),
            timeout_seconds=timeout_seconds,
            attach_timeout=attach_timeout,
            session_timeout=session_timeout,
        )
        require_runtime_processes_closed(result)
    except Exception as exc:
        raise RecipeRuntimeLifecycleUnresolvedError(
            f"{spec.projection_role} runtime lifecycle is unresolved; "
            "no comparison receipt was published"
        ) from exc
    summary, base_assertions = summarize_disposable_runtime(
        result,
        expected_evidence_root=runtime_state / "evidence/runtime",
        launcher_root=Path(launcher_root).expanduser().resolve(),
        source_workspace=source_workspace,
        staged_workspace=staged_workspace,
        expected_runtime_plan=expected,
        compatibility_policy=compatibility,
        probe=probe,
    )
    receipt_evidence = _retained_evidence(result)
    try:
        log_bytes, custody = captured_disposable_groovy_log(
            runtime_state / "evidence/runtime",
            result,
            probe,
            compatibility,
            expected,
            staged_workspace,
        )
    except Exception as exc:
        return _failed_side_with_evidence(
            exc,
            phase="capture",
            probe=probe,
            runtime=summary,
            retained_evidence=receipt_evidence,
        )
    retained_evidence = _retained_evidence(
        result, log_bytes=log_bytes, custody=custody
    )
    assessment: Mapping[str, Any] | None = None
    try:
        assessment = observer.interpret_recipe_change_observation(
            spec,
            groovy_log_bytes=log_bytes,
            capture=_runtime_capture(custody, role=spec.projection_role),
        )
        observer.validate_recipe_change_assessment(spec, assessment)
    except Exception as exc:
        return _failed_side_with_evidence(
            exc,
            phase="assertion",
            probe=probe,
            runtime=summary,
            retained_evidence=retained_evidence,
            assessment=assessment,
        )
    completed = (
        disposable_runtime_capture_completed(result)
        and base_assertions["fml_client_load"]["state"] == "observed"
        and assessment.get("state") == "observed"
    )
    return {
        "assessment": assessment,
        "error": None,
        "outcome": "runtime-observed" if completed else "runtime-mismatch",
        "probe": probe,
        "retained_evidence": retained_evidence,
        "runtime": summary,
        "state": "complete" if completed else "incomplete",
    }


def _incomplete_side(exc: Exception) -> dict[str, Any]:
    return {
        "assessment": None,
        "error": _error_payload(exc, phase="execution"),
        "outcome": "failed",
        "probe": None,
        "retained_evidence": None,
        "runtime": {"state": "failed"},
        "state": "incomplete",
    }


def _not_run_side(blocked_by: str) -> dict[str, Any]:
    if blocked_by not in _ROLES:
        _fail("recipe runtime not-run side lacks its failed predecessor")
    return {
        "assessment": None,
        "error": {
            "kind": "PriorSideFailed",
            "message": f"not run because {blocked_by} ended exceptionally",
            "phase": "blocked",
        },
        "outcome": "not-run",
        "probe": None,
        "retained_evidence": None,
        "runtime": {"state": "not-run"},
        "state": "incomplete",
    }


def _validate_side_error(value: Any) -> dict[str, str]:
    fields = {"kind", "message", "phase"}
    phases = {"assertion", "blocked", "capture", "execution"}
    if (
        type(value) is not dict
        or set(value) != fields
        or type(value.get("kind")) is not str
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", value["kind"])
        is None
        or type(value.get("message")) is not str
        or not value["message"]
        or len(value["message"].encode("utf-8")) > 2000
        or value.get("phase") not in phases
    ):
        _fail("recipe runtime side error changed")
    return dict(value)


def _expected_retained_evidence(
    custody: Mapping[str, Any],
    *,
    include_groovy_log: bool,
) -> dict[str, Any]:
    evidence = {
        **cast(dict[str, Any], custody["receipt_evidence"]),
        "groovy_log": None,
    }
    if include_groovy_log:
        capture = cast(Mapping[str, Any], custody["capture"])
        log_bytes = custody["groovy_log_bytes"]
        if type(log_bytes) is not bytes:
            _fail("recipe runtime retained Groovy log changed")
        evidence["groovy_log"] = {
            "sha256": capture["groovy_log_sha256"],
            "size": len(log_bytes),
            "uri": capture["groovy_log_uri"],
        }
    return evidence


def _preflight_output_roots(
    suite: Path,
    workspace: Path,
    state_root: Path | str,
    launcher_root: Path | str,
    launcher_java_state: Path | str | None,
) -> tuple[Path, Path, Path | None]:
    """Resolve disjoint mutable roots through the shared runtime authority."""

    state = _preflight_mutable_path(
        Path(state_root),
        protected=(workspace,),
        label="recipe runtime state",
    )
    launcher = _preflight_mutable_path(
        Path(launcher_root),
        protected=(workspace, state),
        label="recipe runtime launcher root",
    )
    java_state: Path | None = None
    if launcher_java_state is not None:
        requested_java_state = Path(launcher_java_state)
        managed_launcher_java_state = launcher / ".workbench"
        java_protected = (workspace, state)
        candidate = _preflight_mutable_path(
            requested_java_state,
            protected=java_protected,
            label="recipe runtime explicit Java state",
        )
        if candidate != managed_launcher_java_state:
            candidate = _preflight_mutable_path(
                candidate,
                protected=(*java_protected, launcher),
                label="recipe runtime explicit Java state",
            )
        java_state = candidate

    # These shared checks cover mutable descendants such as ``instances``,
    # ``artifacts``, and ``jdks`` so an existing nested link cannot bypass the
    # root-level separation above.  The exact launcher-managed .workbench Java
    # lane remains the one intentional launcher/Java overlap.
    _preflight_runtime_state(suite, launcher, protected=(workspace,))
    _preflight_explicit_java_state(
        java_state,
        protected=(workspace, state),
    )
    return state, launcher, java_state


def _stage_matches_plan(
    stage: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    candidate: bool,
) -> bool:
    expected_fields = {
        "baseline_revision",
        "format",
        "outputs",
        "plan_id",
        "revision",
        "schema_version",
        "source_tree",
        "source_workspace_uri",
        "state",
        "tracked_tree_id",
        "untracked_excluded",
        "workspace_uri",
    }
    expected = [
        {
            "ordinal": row["ordinal"],
            "path": row["path"],
            "role": row["role"],
            "sha256": row["after_sha256" if candidate else "before_sha256"],
            "size": row["after_size" if candidate else "before_size"],
        }
        for row in plan["operations"]
    ]
    source_tree = stage.get("source_tree")
    exclusions = stage.get("untracked_excluded")
    return (
        set(stage) == expected_fields
        and stage.get("format") == STAGE_FORMAT
        and stage.get("schema_version") == 1
        and stage.get("state") == "staged"
        and stage.get("plan_id") == plan["id"]
        and stage.get("source_workspace_uri") == plan["workspace_uri"]
        and stage.get("outputs") == expected
        and type(source_tree) is dict
        and set(source_tree) == {"file_count", "total_bytes", "tree_sha256"}
        and type(source_tree.get("file_count")) is int
        and type(source_tree.get("file_count")) is not bool
        and source_tree["file_count"] > 0
        and type(source_tree.get("total_bytes")) is int
        and type(source_tree.get("total_bytes")) is not bool
        and source_tree["total_bytes"] >= 0
        and type(source_tree.get("tree_sha256")) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", source_tree["tree_sha256"])
        is not None
        and type(exclusions) is dict
        and set(exclusions) == {"file_count", "paths_sha256"}
        and type(exclusions.get("file_count")) is int
        and type(exclusions.get("file_count")) is not bool
        and exclusions["file_count"] >= 0
        and type(exclusions.get("paths_sha256")) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", exclusions["paths_sha256"])
        is not None
        and type(stage.get("workspace_uri")) is str
        and type(stage.get("revision")) is str
        and _REVISION.fullmatch(stage["revision"]) is not None
        and type(stage.get("baseline_revision")) is str
        and _REVISION.fullmatch(stage["baseline_revision"]) is not None
        and type(stage.get("tracked_tree_id")) is str
        and _REVISION.fullmatch(stage["tracked_tree_id"]) is not None
        and (stage["revision"] != stage["baseline_revision"]) == candidate
    )


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} is not a portable relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail(f"{label} is not a safe normalized path")
    return relative


def _git_bytes(workspace: Path, *arguments: str) -> bytes:
    try:
        git = require_configured_git_executable()
    except GitObservationError as exc:
        raise RecipeRuntimeComparisonError(
            f"cannot reopen retained recipe runtime Git custody: {exc}"
        ) from exc
    try:
        completed = subprocess.run(
            [git, "-C", str(workspace), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecipeRuntimeComparisonError(
            "cannot reopen retained recipe runtime Git custody"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        _fail(f"retained recipe runtime Git custody failed: {detail}")
    if len(completed.stdout) > 16 * 1024 * 1024:
        _fail("retained recipe runtime Git result exceeds its byte bound")
    return completed.stdout


def _git_text(workspace: Path, *arguments: str) -> str:
    try:
        return _git_bytes(workspace, *arguments).decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise RecipeRuntimeComparisonError(
            "retained recipe runtime Git text is not UTF-8"
        ) from exc


def _read_stage_file(workspace: Path, relative: PurePosixPath) -> bytes:
    path = workspace.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
        raw = path.read_bytes()
    except OSError as exc:
        raise RecipeRuntimeComparisonError(
            f"cannot reopen retained stage output: {relative}"
        ) from exc
    if (
        path.is_symlink()
        or resolved != path.absolute()
        or not path.is_file()
        or len(raw) > 4 * 1024 * 1024
    ):
        _fail(f"retained stage output is unavailable or unsafe: {relative}")
    return raw


def _validate_stage_custody(
    stage: Mapping[str, Any],
    plan: Mapping[str, Any],
    attempt_root: Path,
    *,
    role: str,
) -> dict[str, Any]:
    candidate = role == "candidate"
    if not _stage_matches_plan(stage, plan, candidate=candidate):
        _fail(f"recipe runtime {role} stage differs from the plan")
    workspace = attempt_root / role / "workspace"
    if (
        stage.get("workspace_uri") != workspace.as_uri()
        or workspace.is_symlink()
        or not workspace.is_dir()
        or workspace.resolve() != workspace.absolute()
    ):
        _fail(f"recipe runtime {role} stage escaped its attempt")
    revision = _git_text(workspace, "rev-parse", "HEAD")
    tree = _git_text(workspace, "rev-parse", "HEAD^{tree}")
    baseline_revision = stage["baseline_revision"]
    baseline_tree = _git_text(
        workspace, "rev-parse", f"{baseline_revision}^{{tree}}"
    )
    parents = _git_text(
        workspace, "rev-list", "--parents", "-n", "1", revision
    ).split()
    baseline_subject = _git_text(
        workspace, "log", "-1", "--format=%s", baseline_revision
    )
    if (
        revision != stage["revision"]
        or tree != stage["tracked_tree_id"]
        or _git_bytes(workspace, "status", "--porcelain=v1", "-z")
        or baseline_subject
        != f"Capture Workbench source {stage['source_tree']['tree_sha256']}"
    ):
        _fail(f"recipe runtime {role} retained Git stage changed")
    if candidate:
        changed = sorted(
            token.decode("utf-8", "strict")
            for token in _git_bytes(
                workspace,
                "diff",
                "--name-only",
                "-z",
                baseline_revision,
                revision,
                "--",
            ).split(b"\0")
            if token
        )
        expected_changed = sorted(row["path"] for row in plan["operations"])
        if (
            parents != [revision, baseline_revision]
            or _git_text(workspace, "log", "-1", "--format=%s", revision)
            != f"Stage Workbench feature {plan['id']}"
            or changed != expected_changed
        ):
            _fail("recipe runtime candidate Git delta differs from the plan")
    elif (
        revision != baseline_revision
        or parents != [revision]
        or tree != baseline_tree
    ):
        _fail("recipe runtime baseline is not the retained source root commit")

    for operation in plan["operations"]:
        relative = _safe_relative(operation["path"], "recipe operation path")
        before = _git_bytes(
            workspace, "show", f"{baseline_revision}:{relative.as_posix()}"
        )
        current = _read_stage_file(workspace, relative)
        expected_current_sha = operation[
            "after_sha256" if candidate else "before_sha256"
        ]
        expected_current_size = operation[
            "after_size" if candidate else "before_size"
        ]
        if (
            len(before) != operation["before_size"]
            or sha256(before).hexdigest() != operation["before_sha256"]
            or len(current) != expected_current_size
            or sha256(current).hexdigest() != expected_current_sha
        ):
            _fail(f"recipe runtime {role} operation bytes changed")
    for dependency in plan["dependencies"]:
        relative = _safe_relative(dependency["path"], "recipe dependency path")
        current = _read_stage_file(workspace, relative)
        if (
            len(current) != dependency["size"]
            or sha256(current).hexdigest() != dependency["sha256"]
        ):
            _fail(f"recipe runtime {role} dependency bytes changed")
    return {
        "baseline_revision": baseline_revision,
        "baseline_tree": baseline_tree,
        "revision": revision,
        "tree": tree,
        "workspace": workspace,
    }


def _validate_probe_custody(
    observer: Any,
    spec: Any,
    probe: Any,
    side_root: Path,
) -> dict[str, Any]:
    overlay_path, expected, script, overlay_bytes = _expected_probe(
        observer, spec, side_root
    )
    script_path = side_root / "observation-probe/RecipeChangeAssertion.groovy"
    probe_root = script_path.parent
    if type(probe) is not dict or probe != expected:
        _fail(f"recipe runtime {spec.projection_role} probe changed")
    if probe_root.is_symlink() or not probe_root.is_dir():
        _fail("recipe runtime retained probe root is unavailable or unsafe")
    for path, expected_bytes in (
        (script_path, script),
        (overlay_path, overlay_bytes),
    ):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise RecipeRuntimeComparisonError(
                "cannot reopen retained recipe runtime probe"
            ) from exc
        if path.is_symlink() or not path.is_file() or raw != expected_bytes:
            _fail("recipe runtime retained probe bytes changed")
    return expected


def _validate_verification(value: Any, plan_id: str) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value) != {"format", "plan_id", "reason", "schema_version", "state"}
        or value.get("format")
        != "workbench-supersymmetry-recipe-change-verification-v1"
        or value.get("schema_version") != 1
        or value.get("plan_id") != plan_id
        or value.get("state") not in {"ready", "stale"}
        or value.get("state") == "ready"
        and value.get("reason") is not None
        or value.get("state") == "stale"
        and (
            type(value.get("reason")) is not str
            or not value["reason"]
            or len(value["reason"].encode("utf-8")) > 2000
        )
    ):
        _fail("recipe runtime source verification changed")
    return dict(value)


def _reopen_comparison_receipt(
    receipt_path: Path,
    record: Mapping[str, Any],
) -> None:
    if receipt_path.is_symlink() or not receipt_path.is_file():
        _fail("recipe runtime comparison receipt is unavailable or unsafe")
    try:
        raw = receipt_path.read_bytes()
        retained = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecipeRuntimeComparisonError(
            "cannot reopen recipe runtime comparison receipt"
        ) from exc
    expected = json.dumps(
        record, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if len(raw) > 8 * 1024 * 1024 or raw != expected or retained != record:
        _fail("recipe runtime comparison receipt differs from its record")


def _validate_recipe_change_runtime_comparison(
    suite_root: Path | str,
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    require_receipt: bool,
) -> dict[str, Any]:
    """Reopen one comparison through owner, Atlas, and retained-file custody."""

    suite = Path(suite_root).resolve()
    authority = recipe_change_authority("supersymmetry")
    reviewed = cast(dict[str, Any], authority.validate_recipe_change_plan(plan))
    if type(value) is not dict:
        _fail("recipe runtime comparison must be one object")
    record = dict(value)
    body = dict(record)
    supplied = body.pop("id", None)
    expected_fields = {
        "assertions",
        "attempt_id",
        "comparison",
        "format",
        "id",
        "kind",
        "limitations",
        "operation_class",
        "order",
        "observer_protocol",
        "outcome",
        "plan_id",
        "schema_version",
        "sides",
        "source",
        "stages",
        "state",
        "target",
    }
    if (
        set(record) != expected_fields
        or record.get("format") != FORMAT
        or record.get("kind") != KIND
        or record.get("schema_version") != 2
        or record.get("operation_class") != "paired-local-disposable-runtime"
        or record.get("plan_id") != reviewed["id"]
        or record.get("order") not in _ORDERS
        or record.get("limitations")
        != _LIMITATIONS
        or record.get("state") not in {"complete", "incomplete"}
        or record.get("outcome")
        not in {"runtime-comparison-completed", "runtime-comparison-mismatch", "failed"}
        or type(supplied) is not str
        or _ID.fullmatch(supplied) is None
        or supplied != _seal(body)["id"]
    ):
        _fail("recipe runtime comparison identity or shape changed")
    attempt_id = record.get("attempt_id")
    attempt_match = (
        _ATTEMPT_ID.fullmatch(attempt_id)
        if type(attempt_id) is str
        else None
    )
    if attempt_match is None:
        _fail("recipe runtime comparison attempt ID changed")
    target = record.get("target")
    if type(target) is not dict or set(target) != {"attempt_root_uri", "receipt_uri"}:
        _fail("recipe runtime comparison target changed")
    attempt_root = _local_uri(target["attempt_root_uri"], "comparison attempt")
    receipt_path = _local_uri(target["receipt_uri"], "comparison receipt")
    if (
        target["attempt_root_uri"] != attempt_root.as_uri()
        or target["receipt_uri"] != receipt_path.as_uri()
        or attempt_root.name != attempt_match.group(1)
        or attempt_root.parent.name != "attempts"
        or attempt_root.parent.parent.name != "recipe-change-comparisons"
        or attempt_root.parent.parent.parent.name != "runtime"
        or attempt_root.is_symlink()
        or not attempt_root.is_dir()
        or receipt_path != attempt_root / "receipt.json"
    ):
        _fail("recipe runtime receipt escaped its attempt")
    if require_receipt:
        _reopen_comparison_receipt(receipt_path, record)

    source = record.get("source")
    if type(source) is not dict or set(source) != {
        "error",
        "final_verification",
        "initial_verification",
        "workspace_uri",
    }:
        _fail("recipe runtime source custody changed")
    initial = _validate_verification(source["initial_verification"], reviewed["id"])
    final = _validate_verification(source["final_verification"], reviewed["id"])
    if (
        source.get("workspace_uri") != reviewed["workspace_uri"]
        or source.get("error") is not None
        or initial.get("state") != "ready"
    ):
        _fail("recipe runtime source custody differs from the reviewed plan")

    stages = record.get("stages")
    sides = record.get("sides")
    if (
        type(stages) is not dict
        or set(stages) != set(_ROLES)
        or any(type(stages[role]) is not dict for role in _ROLES)
    ):
        _fail("recipe runtime stages changed")
    if type(sides) is not dict or set(sides) != set(_ROLES):
        _fail("recipe runtime sides changed")
    stage_custody = {
        role: _validate_stage_custody(
            cast(Mapping[str, Any], stages[role]),
            reviewed,
            attempt_root,
            role=role,
        )
        for role in _ROLES
    }
    if (
        stages["baseline"].get("source_tree")
        != stages["candidate"].get("source_tree")
        or stages["baseline"].get("untracked_excluded")
        != stages["candidate"].get("untracked_excluded")
        or stage_custody["baseline"]["revision"]
        != stage_custody["candidate"]["baseline_revision"]
        or stage_custody["baseline"]["tree"]
        != stage_custody["candidate"]["baseline_tree"]
    ):
        _fail("recipe runtime sides do not share one source tree")
    contract = authority.build_recipe_observation_contract(
        reviewed, physical_side="client"
    )
    authority.validate_recipe_observation_contract(contract, reviewed)
    observer = _observer(suite)
    if record.get("observer_protocol") != _observer_protocol(
        suite, observer
    ):
        _fail("recipe runtime observer protocol changed")
    specs = {
        role: observer.derive_recipe_change_probe_spec(
            contract, projection_role=role
        )
        for role in _ROLES
    }
    compatibility_overlays, compatibility = material_fluid_runtime_compatibility_policy(
        suite
    )
    if len(compatibility_overlays) != 1:
        _fail("recipe runtime compatibility policy changed")
    execution = (
        _ROLES
        if record["order"] == "baseline-first"
        else tuple(reversed(_ROLES))
    )
    assessments: dict[str, dict[str, Any]] = {}
    side_completed: dict[str, bool] = {}
    for role in _ROLES:
        side = sides[role]
        side_fields = {
            "assessment",
            "error",
            "outcome",
            "probe",
            "retained_evidence",
            "runtime",
            "state",
        }
        if type(side) is not dict or set(side) != side_fields:
            _fail("recipe runtime side shape changed")
        assessment = side.get("assessment")
        error = side.get("error")
        if side.get("outcome") == "not-run":
            prior_role, not_run_role = execution
            side_root = attempt_root / role
            expected_not_run = _not_run_side(prior_role)
            if (
                role != not_run_role
                or sides[prior_role].get("outcome") != "failed"
                or side != expected_not_run
                or any(
                    path.exists() or path.is_symlink()
                    for path in (
                        side_root / "cleanroom-runtime",
                        side_root / "observation-probe",
                    )
                )
            ):
                _fail(f"recipe runtime {role} not-run custody changed")
            side_completed[role] = False
            continue

        if side.get("outcome") == "failed":
            retained_evidence = side.get("retained_evidence")
            validated_error = _validate_side_error(error)
            if retained_evidence is None:
                if (
                    assessment is not None
                    or validated_error["phase"] != "execution"
                    or side.get("probe") is not None
                    or side.get("runtime") != {"state": "failed"}
                    or side.get("state") != "incomplete"
                ):
                    _fail(f"recipe runtime {role} failed-side custody changed")
                side_completed[role] = False
                continue
            if (
                validated_error["phase"] not in {"capture", "assertion"}
                or type(side.get("runtime")) is not dict
                or type(retained_evidence) is not dict
                or side.get("state") != "incomplete"
            ):
                _fail(f"recipe runtime {role} diagnostic custody changed")
            probe = _validate_probe_custody(
                observer,
                specs[role],
                side.get("probe"),
                attempt_root / role,
            )
            include_groovy = validated_error["phase"] == "assertion"
            try:
                custody = validate_retained_disposable_runtime(
                    suite,
                    cast(Mapping[str, Any], side["runtime"]),
                    cast(Mapping[str, Any] | None, assessment),
                    runtime_state=attempt_root / role / "cleanroom-runtime",
                    source_workspace=_local_uri(
                        reviewed["workspace_uri"], "recipe source workspace"
                    ),
                    staged_workspace=stage_custody[role]["workspace"],
                    compatibility_policy=compatibility,
                    probe=probe,
                    attempt_root=attempt_root,
                    physical_side="client",
                    projection_role=role,
                    require_groovy_log=include_groovy,
                )
            except MaterialFluidFlowError as exc:
                raise RecipeRuntimeComparisonError(
                    f"recipe runtime {role} retained custody is invalid: {exc}"
                ) from exc
            if retained_evidence != _expected_retained_evidence(
                custody, include_groovy_log=include_groovy
            ):
                _fail(f"recipe runtime {role} retained evidence changed")
            if validated_error["phase"] == "capture":
                if assessment is not None:
                    _fail(f"recipe runtime {role} capture failure has an assessment")
                try:
                    captured_disposable_groovy_log(
                        attempt_root / role / "cleanroom-runtime/evidence/runtime",
                        custody["runtime_result"],
                        probe,
                        compatibility,
                        custody["runtime_plan"],
                        stage_custody[role]["workspace"],
                    )
                except Exception as exc:
                    if _error_payload(exc, phase="capture") != validated_error:
                        _fail(f"recipe runtime {role} capture error changed")
                else:
                    _fail(f"recipe runtime {role} capture error is no longer present")
            else:
                try:
                    expected_assessment = observer.interpret_recipe_change_observation(
                        specs[role],
                        groovy_log_bytes=custody["groovy_log_bytes"],
                        capture=_runtime_capture(custody["capture"], role=role),
                    )
                except Exception as exc:
                    if (
                        assessment is not None
                        or _error_payload(exc, phase="assertion") != validated_error
                    ):
                        _fail(f"recipe runtime {role} assertion error changed")
                else:
                    if assessment != expected_assessment:
                        _fail(f"recipe runtime {role} diagnostic assessment changed")
                    try:
                        observer.validate_recipe_change_assessment(
                            specs[role], expected_assessment
                        )
                    except Exception as exc:
                        if _error_payload(exc, phase="assertion") != validated_error:
                            _fail(f"recipe runtime {role} assertion error changed")
                    else:
                        _fail(f"recipe runtime {role} assertion error is no longer present")
            side_completed[role] = False
            continue

        if type(assessment) is not dict or type(side.get("runtime")) is not dict:
            _fail(f"recipe runtime {role} observation is malformed")
        if error is not None:
            _fail(f"recipe runtime {role} observation has an error")
        probe = _validate_probe_custody(
            observer,
            specs[role],
            side.get("probe"),
            attempt_root / role,
        )
        try:
            custody = validate_retained_disposable_runtime(
                suite,
                cast(Mapping[str, Any], side["runtime"]),
                assessment,
                runtime_state=attempt_root / role / "cleanroom-runtime",
                source_workspace=_local_uri(
                    reviewed["workspace_uri"], "recipe source workspace"
                ),
                staged_workspace=stage_custody[role]["workspace"],
                compatibility_policy=compatibility,
                probe=probe,
                attempt_root=attempt_root,
                physical_side="client",
                projection_role=role,
            )
        except MaterialFluidFlowError as exc:
            raise RecipeRuntimeComparisonError(
                f"recipe runtime {role} retained custody is invalid: {exc}"
            ) from exc
        expected_assessment = observer.interpret_recipe_change_observation(
            specs[role],
            groovy_log_bytes=custody["groovy_log_bytes"],
            capture=assessment["capture"],
        )
        validated = observer.validate_recipe_change_assessment(
            specs[role], assessment
        )
        if assessment != expected_assessment or validated != expected_assessment:
            _fail(f"recipe runtime {role} assessment differs from its Groovy log")
        assessments[role] = validated
        completed = (
            disposable_runtime_capture_completed(custody["runtime_result"])
            and custody["assertions"]["fml_client_load"]["state"] == "observed"
            and validated.get("state") == "observed"
        )
        expected_side = {
            "assessment": expected_assessment,
            "error": None,
            "outcome": "runtime-observed" if completed else "runtime-mismatch",
            "probe": probe,
            "runtime": custody["runtime_summary"],
            "state": "complete" if completed else "incomplete",
        }
        expected_side["retained_evidence"] = _expected_retained_evidence(
            custody, include_groovy_log=True
        )
        if side != expected_side:
            _fail(f"recipe runtime {role} state contradicts its receipts")
        side_completed[role] = completed
    comparison = record.get("comparison")
    if set(assessments) == set(_ROLES):
        if type(comparison) is not dict:
            _fail("recipe runtime comparison is absent despite two observations")
        expected_comparison = observer.compare_recipe_change_observations(
            specs["baseline"],
            assessments["baseline"],
            specs["candidate"],
            assessments["candidate"],
        )
        observer.validate_recipe_change_comparison(
            specs["baseline"],
            assessments["baseline"],
            specs["candidate"],
            assessments["candidate"],
            comparison,
        )
        if comparison != expected_comparison:
            _fail("recipe runtime comparison differs from Atlas interpretation")
    elif comparison is not None:
        _fail("recipe runtime comparison exists without two observations")
    assertions = record.get("assertions")
    expected_assertions = {
        "baseline_and_candidate_share_source_tree": True,
        "baseline_is_unmodified_source": True,
        "candidate_is_exact_reviewed_delta": True,
        "both_client_cold_starts_observed": all(
            side_completed.get(role) is True for role in _ROLES
        ),
        "atlas_observed_exact_addition": (
            type(comparison) is dict
            and comparison.get("state") == "observed-change"
        ),
        "developer_source_unchanged": final.get("state") == "ready",
    }
    if (
        type(assertions) is not dict
        or set(assertions) != _ASSERTION_KEYS
        or assertions != expected_assertions
    ):
        _fail("recipe runtime assertions changed")
    complete = all(expected_assertions.values())
    expected_outcome = (
        "runtime-comparison-completed"
        if complete
        else "failed"
        if any(
            sides[role]["outcome"] in {"failed", "not-run"} for role in _ROLES
        )
        else "runtime-comparison-mismatch"
    )
    if (
        record["state"] != ("complete" if complete else "incomplete")
        or record["outcome"] != expected_outcome
    ):
        _fail("recipe runtime completion contradicts its evidence")
    return record


def validate_recipe_change_runtime_comparison(
    suite_root: Path | str,
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen one retained comparison without consulting its live source."""

    return _validate_recipe_change_runtime_comparison(
        suite_root,
        value,
        plan,
        require_receipt=True,
    )


def run_recipe_change_runtime_comparison(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    consent_plan_id: str,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    launcher: str = "prism",
    order: str = "baseline-first",
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
    """Run one unchanged and one reviewed candidate disposable client."""

    suite = Path(suite_root).resolve()
    authority = recipe_change_authority("supersymmetry")
    reviewed = cast(dict[str, Any], authority.validate_recipe_change_plan(plan))
    if consent_plan_id != reviewed["id"]:
        _fail("runtime comparison requires consent to the exact reviewed plan ID")
    if launcher not in {"prism", "multimc"} or order not in _ORDERS:
        _fail("runtime comparison launcher or order is unsupported")
    initial = authority.verify_recipe_change_plan(suite, reviewed)
    if initial.get("state") != "ready":
        _fail(f"recipe plan is stale: {initial.get('reason')}")
    workspace = authority.recipe_change_workspace(reviewed)
    state, launcher_path, java_state = _preflight_output_roots(
        suite,
        workspace,
        state_root,
        launcher_root,
        launcher_java_state,
    )
    try:
        preflight_close_observer_launcher(
            launcher_executable,
            launcher_path,
            launcher,
        )
    except RuntimeObserveError as exc:
        raise RecipeRuntimeComparisonError(str(exc)) from exc
    parent = prepare_feature_runtime_attempt_parent(
        state, workspace, lane="recipe-change-comparisons"
    )
    token = uuid4().hex
    destination = parent / token
    destination.mkdir(mode=0o700)
    contract = authority.build_recipe_observation_contract(
        reviewed, physical_side="client"
    )
    authority.validate_recipe_observation_contract(contract, reviewed)
    observer = _observer(suite)
    specs = {
        role: observer.derive_recipe_change_probe_spec(
            contract, projection_role=role
        )
        for role in _ROLES
    }
    verify = lambda: authority.verify_recipe_change_plan(suite, reviewed)
    stages: dict[str, Any] = {}
    sides: dict[str, Any] = {
        role: _incomplete_side(RuntimeError("not run")) for role in _ROLES
    }
    comparison: dict[str, Any] | None = None
    for role in _ROLES:
        side_root = destination / role
        side_root.mkdir()
        stages[role] = stage_reviewed_feature_plan(
            reviewed,
            side_root / "workspace",
            workspace=workspace,
            verify=verify,
            apply_operations=role == "candidate",
            result_format=STAGE_FORMAT,
        )
    stage_custody = {
        role: _validate_stage_custody(
            stages[role], reviewed, destination, role=role
        )
        for role in _ROLES
    }
    if (
        stages["baseline"]["source_tree"] != stages["candidate"]["source_tree"]
        or stages["baseline"]["untracked_excluded"]
        != stages["candidate"]["untracked_excluded"]
        or stage_custody["baseline"]["revision"]
        != stage_custody["candidate"]["baseline_revision"]
        or stage_custody["baseline"]["tree"]
        != stage_custody["candidate"]["baseline_tree"]
    ):
        _fail("source tree changed between baseline and candidate staging")
    compatibility_overlays, _compatibility = (
        material_fluid_runtime_compatibility_policy(suite)
    )
    if len(compatibility_overlays) != 1:
        _fail("recipe runtime compatibility policy changed")
    execution = _ROLES if order == "baseline-first" else tuple(reversed(_ROLES))
    for ordinal, role in enumerate(execution):
        try:
            sides[role] = _run_side(
                suite,
                workspace,
                destination / role / "workspace",
                stages[role],
                observer,
                specs[role],
                destination / role,
                launcher=launcher,
                launcher_executable=launcher_executable,
                launcher_root=launcher_path,
                launcher_profile=launcher_profile,
                launcher_java=launcher_java,
                launcher_java_state=java_state,
                packwiz_executable=packwiz_executable,
                seed_roots=seed_roots,
                memory_mib=memory_mib,
                offline_name=offline_name,
                timeout_seconds=timeout_seconds,
                attach_timeout=attach_timeout,
                session_timeout=session_timeout,
            )
        except RecipeRuntimeLifecycleUnresolvedError:
            raise
        except Exception as exc:  # no unknown observer lifecycle crossed this boundary
            sides[role] = _incomplete_side(exc)
            for later_role in execution[ordinal + 1 :]:
                sides[later_role] = _not_run_side(role)
            break
    if all(
        sides[role]["assessment"] is not None
        and sides[role]["outcome"] != "failed"
        for role in _ROLES
    ):
        comparison = observer.compare_recipe_change_observations(
            specs["baseline"],
            sides["baseline"]["assessment"],
            specs["candidate"],
            sides["candidate"]["assessment"],
        )

    final = authority.verify_recipe_change_plan(suite, reviewed)
    assertions = {
        "baseline_and_candidate_share_source_tree": (
            set(stages) == set(_ROLES)
            and stages["baseline"].get("source_tree")
            == stages["candidate"].get("source_tree")
        ),
        "baseline_is_unmodified_source": (
            "baseline" in stages
            and stages["baseline"].get("revision")
            == stages["baseline"].get("baseline_revision")
        ),
        "candidate_is_exact_reviewed_delta": (
            "candidate" in stages
            and _stage_matches_plan(stages["candidate"], reviewed, candidate=True)
        ),
        "both_client_cold_starts_observed": all(
            sides[role].get("state") == "complete" for role in _ROLES
        ),
        "atlas_observed_exact_addition": (
            comparison is not None and comparison.get("state") == "observed-change"
        ),
        "developer_source_unchanged": final.get("state") == "ready",
    }
    complete = comparison is not None and all(assertions.values())
    outcome = (
        "runtime-comparison-completed"
        if complete
        else "failed"
        if any(
            side["outcome"] in {"failed", "not-run"} for side in sides.values()
        )
        else "runtime-comparison-mismatch"
    )
    body = {
        "assertions": assertions,
        "attempt_id": "uuid:" + token,
        "comparison": comparison,
        "format": FORMAT,
        "kind": KIND,
        "limitations": list(_LIMITATIONS),
        "observer_protocol": _observer_protocol(suite, observer),
        "operation_class": "paired-local-disposable-runtime",
        "order": order,
        "outcome": outcome,
        "plan_id": reviewed["id"],
        "schema_version": 2,
        "sides": sides,
        "source": {
            "workspace_uri": reviewed["workspace_uri"],
            "initial_verification": initial,
            "final_verification": final,
            "error": None,
        },
        "stages": stages,
        "state": "complete" if complete else "incomplete",
        "target": {
            "attempt_root_uri": destination.as_uri(),
            "receipt_uri": (destination / "receipt.json").as_uri(),
        },
    }
    record = _seal(body)
    _validate_recipe_change_runtime_comparison(
        suite, record, reviewed, require_receipt=False
    )
    receipt = destination / "receipt.json"
    temporary = destination / ".receipt.json.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt)
    except OSError as exc:
        raise RecipeRuntimeComparisonError(
            f"cannot publish recipe runtime comparison: {exc}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return validate_recipe_change_runtime_comparison(suite, record, reviewed)


__all__ = [
    "FORMAT",
    "KIND",
    "RecipeRuntimeComparisonError",
    "RecipeRuntimeLifecycleUnresolvedError",
    "run_recipe_change_runtime_comparison",
    "validate_recipe_change_runtime_comparison",
]
