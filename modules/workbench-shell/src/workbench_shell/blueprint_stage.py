"""Stage one Atlas-backed Blueprint in a disposable pack workspace."""

from __future__ import annotations

from urllib.request import url2pathname

from workbench_project_intelligence.working_tree import WorkingTreeError, copy_tracked_workspace

import base64
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, NoReturn, Sequence
from urllib.parse import urlparse

from workbench_project_intelligence.git_observation import (
    GitObservationError,
    require_configured_git_executable,
)

from .bootstrap import inspect_project
from .runtime_materialize import (
    PackwizMaterializationError,
)
from workbench_api.state_paths import default_suite_state_root


EXPERIMENTAL_PATTERN_PATH = Path(
    "profiles/packs/supersymmetry/blueprints/experimental/patterns/"
    "material-backed-fluid-v1.json"
)
ATLAS_AUTHORITIES_PATH = Path(
    "modules/atlas/data/infrastructure-source-authorities-v1.json"
)
PACK_PROFILE_ID = "workbench-pack:supersymmetry"


class BlueprintStageError(ValueError):
    """Raised when a construction candidate cannot be staged safely."""


def _fail(message: str) -> NoReturn:
    raise BlueprintStageError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


def _prepare_stage_parent(local_state: Path, workspace: Path) -> Path:
    local_state = local_state.resolve()
    if _paths_overlap(local_state, workspace):
        _fail("Blueprint state root cannot overlap the source workspace")
    cursor = local_state
    for part in ("staging", "blueprints"):
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("Blueprint staging root cannot traverse a symbolic link")
    resolved = cursor.resolve(strict=False)
    if (
        not resolved.is_relative_to(local_state)
        or _paths_overlap(resolved, workspace)
    ):
        _fail("Blueprint staging root escapes state or overlaps source")
    try:
        cursor.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(f"cannot prepare Blueprint staging root: {exc}")
    if cursor.is_symlink() or not cursor.is_dir() or cursor.resolve() != resolved:
        _fail("Blueprint staging root is not a stable regular directory")
    return cursor


def _authority_modules(suite: Path) -> tuple[Any, Any, Any]:
    for source in (
        suite / "modules/atlas/src",
        suite / "modules/blueprints/src",
    ):
        source_text = str(source)
        if source_text not in sys.path:
            sys.path.insert(0, source_text)
    try:
        from workbench_atlas import material_census
        from workbench_blueprints import convention_patch, planner
    except ImportError as exc:
        _fail(f"Workbench construction authorities are unavailable: {exc}")
    return material_census, convention_patch, planner


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _groovy_symbol(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _blocking_material_uncertainties(
    census: dict[str, Any],
    pattern: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return only uncertain builders that can occupy this allocation domain."""

    uncertainties = census.get("uncertainties")
    allocation = pattern.get("allocation")
    if not isinstance(uncertainties, list) or not isinstance(allocation, dict):
        _fail("Atlas material census or Blueprint allocation is malformed")
    owner = allocation.get("owner_path")
    minimum = allocation.get("minimum")
    maximum = allocation.get("maximum")
    if (
        not isinstance(owner, str)
        or type(minimum) is not int
        or type(maximum) is not int
    ):
        _fail("Blueprint material allocation is malformed")
    blocking: list[dict[str, Any]] = []
    for uncertainty in uncertainties:
        if not isinstance(uncertainty, dict):
            _fail("Atlas material census uncertainty is malformed")
        material_id = uncertainty.get("material_id")
        if uncertainty.get("path") == owner or (
            type(material_id) is int and minimum <= material_id <= maximum
        ):
            blocking.append(uncertainty)
    return blocking


def _git_executable() -> str:
    try:
        return require_configured_git_executable()
    except GitObservationError as exc:
        _fail(f"cannot prepare disposable Blueprint Git workspace: {exc}")


def _run_git(
    root: Path,
    arguments: Sequence[str],
    *,
    environment: dict[str, str] | None = None,
) -> bytes:
    git = _git_executable()
    try:
        completed = subprocess.run(
            [git, "-C", str(root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(f"cannot prepare disposable Blueprint Git workspace: {exc}")
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        _fail(
            f"Git {' '.join(arguments)} failed in Blueprint staging: "
            f"{detail or 'unknown Git failure'}"
        )
    return completed.stdout


def _commit_stage(
    workspace: Path,
    message: str,
    *,
    initialize: bool,
) -> str:
    if initialize:
        _run_git(workspace, ("init", "--quiet"))
    _run_git(workspace, ("add", "--all", "--force"))
    environment = os.environ.copy()
    fixed_date = "2000-01-01T00:00:00+0000"
    environment.update({
        "GIT_AUTHOR_DATE": fixed_date,
        "GIT_COMMITTER_DATE": fixed_date,
    })
    _run_git(
        workspace,
        (
            "-c",
            "user.name=Workbench",
            "-c",
            "user.email=workbench@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            message,
        ),
        environment=environment,
    )
    revision = _run_git(workspace, ("rev-parse", "HEAD")).decode(
        "ascii", "strict"
    ).strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        _fail("disposable Blueprint workspace has an invalid revision")
    if _run_git(workspace, ("status", "--porcelain=v1", "-z")):
        _fail("disposable Blueprint workspace is dirty after staging")
    return revision


def _safe_output_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("sealed Blueprint output path is not portable")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"sealed Blueprint output path is unsafe: {value}")
    return path


def _apply_sealed_operations(
    workspace: Path,
    sealed: dict[str, Any],
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for operation in sealed.get("operations", []):
        if (
            not isinstance(operation, dict)
            or operation.get("operation") not in {"create", "update"}
        ):
            _fail("Blueprint staging accepts create and update operations only")
        relative = _safe_output_path(operation.get("path"))
        content_raw = operation.get("content_base64")
        expected_digest = operation.get("content_sha256")
        if not isinstance(content_raw, str) or not isinstance(
            expected_digest, str
        ):
            _fail("sealed Blueprint output lacks content identity")
        try:
            content = base64.b64decode(content_raw, validate=True)
        except (ValueError, TypeError) as exc:
            _fail(f"sealed Blueprint output is not valid base64: {exc}")
        digest = sha256(content).hexdigest()
        if digest != expected_digest:
            _fail("sealed Blueprint output content digest differs")
        target = workspace.joinpath(*relative.parts)
        kind = operation["operation"]
        if kind == "create" and (target.exists() or target.is_symlink()):
            _fail(
                "sealed Blueprint create output already exists: "
                f"{relative.as_posix()}"
            )
        if kind == "update" and (
            not target.is_file() or target.is_symlink()
        ):
            _fail(
                "sealed Blueprint update output does not name a regular file: "
                f"{relative.as_posix()}"
            )
        cursor = workspace
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                _fail(
                    "sealed Blueprint output parent contains a symbolic link: "
                    f"{relative.as_posix()}"
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(
            parent.is_symlink()
            for parent in target.parents
            if parent != workspace and workspace in parent.parents
        ):
            _fail(
                "sealed Blueprint output parent contains a symbolic link: "
                f"{relative.as_posix()}"
            )
        before_digest = operation.get("before_sha256")
        if kind == "update":
            current_digest = sha256(target.read_bytes()).hexdigest()
            if not isinstance(before_digest, str) or current_digest != before_digest:
                _fail(
                    "sealed Blueprint update baseline differs: "
                    f"{relative.as_posix()}"
                )
        temporary_name: str | None = None
        try:
            if kind == "create":
                with target.open("xb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                target.chmod(0o644)
            else:
                mode = target.stat().st_mode & 0o777
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    delete=False,
                ) as output:
                    temporary_name = output.name
                    os.fchmod(output.fileno(), mode)
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_name, target)
                temporary_name = None
        except OSError as exc:
            _fail(f"cannot stage Blueprint output {relative}: {exc}")
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
        outputs.append({
            "operation": kind,
            "path": relative.as_posix(),
            "before_sha256": before_digest,
            "sha256": digest,
            "size": len(content),
        })
    outputs.sort(key=lambda row: row["path"])
    if not outputs:
        _fail("Blueprint candidate contains no stageable outputs")
    return outputs


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(
                value,
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        _fail(f"cannot retain Blueprint staging receipt: {exc}")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        _fail("existing Blueprint stage lacks a regular receipt")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"existing Blueprint staging receipt is invalid: {exc}")
    if not isinstance(value, dict):
        _fail("existing Blueprint staging receipt is not an object")
    return value


def _verify_existing_stage(
    stage_root: Path,
    candidate_id: str,
    target_state_id: str,
    reviewed_plan_id: str,
    source_workspace_uri: str,
    source_revision: str,
    expected_stage_revision: str,
    expected_tree_id: str,
    expected_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    receipt = _load_json(stage_root / "receipt.json")
    workspace = stage_root / "workspace"
    if not workspace.is_dir() or workspace.is_symlink():
        _fail("existing Blueprint stage lacks a regular workspace")
    blueprint = receipt.get("blueprint")
    source = receipt.get("source")
    target = receipt.get("target")
    if (
        receipt.get("format") != "workbench-blueprint-stage-receipt-v2"
        or receipt.get("schema_version") != 2
        or receipt.get("state") != "staged"
        or not isinstance(blueprint, dict)
        or blueprint.get("candidate_id") != candidate_id
        or blueprint.get("target_state_id") != target_state_id
        or blueprint.get("reviewed_plan_id") != reviewed_plan_id
        or not isinstance(source, dict)
        or source.get("workspace_uri") != source_workspace_uri
        or source.get("revision") != source_revision
        or not isinstance(target, dict)
        or target.get("workspace_uri") != workspace.as_uri()
        or target.get("receipt_uri") != (stage_root / "receipt.json").as_uri()
        or target.get("revision") != expected_stage_revision
        or target.get("tracked_tree_id") != expected_tree_id
        or receipt.get("outputs") != expected_outputs
    ):
        _fail("existing Blueprint stage belongs to a different candidate")
    revision = _run_git(workspace, ("rev-parse", "HEAD")).decode(
        "ascii", "strict"
    ).strip()
    if revision != expected_stage_revision:
        _fail("existing Blueprint stage revision differs from the reviewed candidate")
    tree_id = _run_git(workspace, ("rev-parse", "HEAD^{tree}")).decode(
        "ascii", "strict"
    ).strip()
    if tree_id != expected_tree_id:
        _fail("existing Blueprint stage tracked tree differs from the reviewed candidate")
    if _run_git(workspace, ("status", "--porcelain=v1", "-z")):
        _fail("existing Blueprint stage has drifted")
    outputs = expected_outputs
    if not isinstance(outputs, list):
        _fail("existing Blueprint stage receipt lacks outputs")
    for row in outputs:
        if not isinstance(row, dict):
            _fail("existing Blueprint stage output receipt is malformed")
        relative = _safe_output_path(row.get("path"))
        path = workspace.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            _fail(f"existing Blueprint stage output is missing: {relative}")
        content = path.read_bytes()
        if (
            sha256(content).hexdigest() != row.get("sha256")
            or len(content) != row.get("size")
        ):
            _fail(f"existing Blueprint stage output has drifted: {relative}")
    return receipt


def _local_retained_path(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        _fail(f"{label} URI is unavailable")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} must be a local retained file")
    unresolved = Path(url2pathname(parsed.path))
    if unresolved.is_symlink():
        _fail(f"{label} is a symbolic link")
    try:
        return unresolved.resolve(strict=True)
    except OSError as exc:
        _fail(f"{label} is unavailable: {exc}")


def validate_retained_blueprint_stage(
    stage_summary: Mapping[str, Any],
    reviewed_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen one V2 stage and prove its exact reviewed source/tree custody."""

    receipt_path = _local_retained_path(
        stage_summary.get("receipt_uri"), "Blueprint stage receipt"
    )
    if not receipt_path.is_file():
        _fail("Blueprint stage receipt is not a regular file")
    try:
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"Blueprint stage receipt is invalid: {exc}")
    if len(raw) > 4 * 1024 * 1024 or not isinstance(receipt, dict):
        _fail("Blueprint stage receipt is malformed or exceeds its bound")

    source = receipt.get("source")
    source_snapshot = source.get("snapshot") if isinstance(source, dict) else None
    blueprint = receipt.get("blueprint")
    target = receipt.get("target")
    reviewed_source = reviewed_plan.get("source")
    reviewed_blueprint = reviewed_plan.get("blueprint")
    reviewed_operations = reviewed_plan.get("operations")
    if (
        receipt.get("format") != "workbench-blueprint-stage-receipt-v2"
        or receipt.get("schema_version") != 2
        or receipt.get("state") != "staged"
        or not isinstance(source, dict)
        or not isinstance(source_snapshot, dict)
        or not isinstance(blueprint, dict)
        or not isinstance(target, dict)
        or not isinstance(reviewed_source, Mapping)
        or not isinstance(reviewed_blueprint, Mapping)
        or not isinstance(reviewed_operations, list)
    ):
        _fail("Blueprint stage receipt lacks its V2 owner bindings")

    expected_outputs: list[dict[str, Any]] = []
    for row in reviewed_operations:
        if not isinstance(row, Mapping):
            _fail("reviewed Blueprint operation is malformed")
        expected_outputs.append(
            {
                "operation": row.get("operation"),
                "path": row.get("path"),
                "before_sha256": row.get("before_sha256"),
                "sha256": row.get("content_sha256"),
                "size": row.get("size"),
            }
        )
    expected_outputs.sort(key=lambda row: str(row["path"]).encode("utf-8"))

    source_tree_sha256 = source_snapshot.get("tree_sha256")
    candidate_material = {
        "plan_id": blueprint.get("plan_id"),
        "source_tree_sha256": source_tree_sha256,
    }
    expected_candidate_id = "blueprints-convention-candidate:sha256:" + sha256(
        _canonical_bytes(candidate_material)
    ).hexdigest()
    stage_material = {
        "candidate_id": expected_candidate_id,
        "census_id": receipt.get("atlas", {}).get("census_id")
        if isinstance(receipt.get("atlas"), dict)
        else None,
        "reviewed_plan_id": reviewed_plan.get("plan_id"),
        "source_revision": source.get("revision"),
        "source_tree_sha256": source_tree_sha256,
        "source_workspace_uri": source.get("workspace_uri"),
        "stage_revision": target.get("revision"),
        "stage_tree_id": target.get("tracked_tree_id"),
    }
    expected_stage_id = "sha256:" + sha256(
        _canonical_bytes(stage_material)
    ).hexdigest()
    workspace = receipt_path.parent / "workspace"
    reviewed_untracked = reviewed_source.get("untracked_excluded")
    retained_untracked = source.get("untracked_excluded")
    reviewed_paths_digest = (
        reviewed_untracked.get("paths_sha256")
        if isinstance(reviewed_untracked, Mapping)
        else None
    )
    if isinstance(reviewed_paths_digest, str):
        reviewed_paths_digest = reviewed_paths_digest.removeprefix("sha256:")
    retained_paths_digest = (
        retained_untracked.get("paths_sha256")
        if isinstance(retained_untracked, Mapping)
        else None
    )
    if isinstance(retained_paths_digest, str):
        retained_paths_digest = retained_paths_digest.removeprefix("sha256:")

    expected_summary = {
        "state": "staged",
        "outcome": stage_summary.get("outcome"),
        "stage_id": expected_stage_id,
        "candidate_id": expected_candidate_id,
        "revision": target.get("revision"),
        "tracked_tree_id": target.get("tracked_tree_id"),
        "workspace_uri": workspace.as_uri(),
        "receipt_uri": receipt_path.as_uri(),
    }
    if (
        stage_summary.get("outcome") not in {"staged", "reused"}
        or dict(stage_summary) != expected_summary
        or receipt.get("stage_id") != expected_stage_id
        or blueprint.get("candidate_id") != expected_candidate_id
        or blueprint.get("reviewed_plan_id") != reviewed_plan.get("plan_id")
        or blueprint.get("pattern_id") != reviewed_blueprint.get("pattern_id")
        or blueprint.get("pattern_key") != reviewed_blueprint.get("pattern_key")
        or blueprint.get("pattern_version") != reviewed_blueprint.get("pattern_version")
        or blueprint.get("effective_parameters")
        != reviewed_blueprint.get("effective_parameters")
        or blueprint.get("target_state_id") != source.get("target_state_id")
        or source.get("workspace_uri") != reviewed_source.get("workspace_uri")
        or source.get("revision") != reviewed_source.get("revision")
        or not isinstance(reviewed_untracked, Mapping)
        or not isinstance(retained_untracked, Mapping)
        or retained_untracked.get("file_count")
        != reviewed_untracked.get("file_count")
        or retained_paths_digest != reviewed_paths_digest
        or receipt.get("outputs") != expected_outputs
        or target.get("workspace_uri") != workspace.as_uri()
        or target.get("receipt_uri") != receipt_path.as_uri()
    ):
        _fail("Blueprint stage receipt differs from its reviewed plan or summary")

    if not workspace.is_dir() or workspace.is_symlink():
        _fail("Blueprint staged workspace is unavailable or unsafe")
    revision = _run_git(workspace, ("rev-parse", "HEAD")).decode(
        "ascii", "strict"
    ).strip()
    tree_id = _run_git(workspace, ("rev-parse", "HEAD^{tree}")).decode(
        "ascii", "strict"
    ).strip()
    baseline_revision = source.get("staged_baseline_revision")
    parents = _run_git(
        workspace, ("rev-list", "--parents", "-n", "1", revision)
    ).decode("ascii", "strict").strip().split()
    baseline_subject = _run_git(
        workspace, ("log", "-1", "--format=%s", str(baseline_revision))
    ).decode("utf-8", "strict").rstrip("\n")
    stage_subject = _run_git(
        workspace, ("log", "-1", "--format=%s", revision)
    ).decode("utf-8", "strict").rstrip("\n")
    if (
        revision != target.get("revision")
        or tree_id != target.get("tracked_tree_id")
        or _run_git(workspace, ("status", "--porcelain=v1", "-z"))
        or re.fullmatch(r"[0-9a-f]{40}", str(baseline_revision)) is None
        or parents != [revision, baseline_revision]
        or baseline_subject != f"Capture Workbench source {source_tree_sha256}"
        or stage_subject != f"Stage Workbench Blueprint {expected_candidate_id}"
    ):
        _fail("Blueprint staged workspace differs from its retained tree")
    for row in expected_outputs:
        relative = _safe_output_path(row["path"])
        output = workspace.joinpath(*relative.parts)
        if output.is_symlink() or not output.is_file():
            _fail(f"Blueprint staged output is unavailable: {relative}")
        content = output.read_bytes()
        if (
            len(content) != row["size"]
            or sha256(content).hexdigest() != row["sha256"]
        ):
            _fail(f"Blueprint staged output differs: {relative}")
    return {
        "receipt": receipt,
        "receipt_uri": receipt_path.as_uri(),
        "receipt_sha256": sha256(raw).hexdigest(),
        "receipt_size": len(raw),
        "workspace_uri": workspace.as_uri(),
        "stage_id": expected_stage_id,
        "candidate_id": expected_candidate_id,
        "tracked_tree_id": tree_id,
    }


def _public_plan_operation(
    workspace: Path,
    operation: dict[str, Any],
) -> dict[str, Any]:
    relative = _safe_output_path(operation.get("path"))
    target = workspace.joinpath(*relative.parts)
    if target.is_symlink() or not target.is_file():
        _fail(f"Blueprint plan target is not a regular file: {relative}")
    before = target.read_bytes()
    after = operation.get("content")
    if not isinstance(after, bytes):
        _fail(f"Blueprint plan output lacks bytes: {relative}")
    before_digest = sha256(before).hexdigest()
    after_digest = sha256(after).hexdigest()
    if (
        before_digest != operation.get("before_sha256")
        or after_digest != operation.get("content_sha256")
    ):
        _fail(f"Blueprint plan output identity differs: {relative}")
    try:
        before_lines = before.decode("utf-8").splitlines(keepends=True)
        after_lines = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeError as exc:
        _fail(f"Blueprint plan V1 supports UTF-8 updates only: {relative}: {exc}")
    diff = "".join(difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{relative.as_posix()}",
        tofile=f"b/{relative.as_posix()}",
    ))
    if not diff:
        _fail(f"Blueprint plan output has no diff: {relative}")
    return {
        "operation": operation.get("operation"),
        "path": relative.as_posix(),
        "before_sha256": before_digest,
        "content_sha256": after_digest,
        "size": len(after),
        "diff": diff,
    }


def plan_material_backed_fluid(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Return an exact source-bound three-file plan without writing state."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    inspection = inspect_project(suite, workspace)
    context = inspection.get("workspace_context")
    pack = context.get("pack") if isinstance(context, dict) else None
    if (
        not isinstance(pack, dict)
        or pack.get("profile_family_id") != PACK_PROFILE_ID
        or "construct" not in pack.get("permitted_operations", [])
    ):
        _fail(
            "material-backed-fluid planning requires a constructible "
            "Supersymmetry workspace"
        )
    workspace_context = context.get("workspace")
    source_revision = (
        workspace_context.get("revision")
        if isinstance(workspace_context, dict)
        else None
    )
    if not isinstance(source_revision, str):
        _fail("Supersymmetry workspace inspection lacks a source revision")

    material_census, convention_patch, _planner = _authority_modules(suite)
    git = _git_executable()
    pattern = convention_patch.load_pattern(suite / EXPERIMENTAL_PATTERN_PATH)
    census = material_census.census_material_builders(
        workspace,
        git_executable=git,
    )
    resolved_queries = material_census.resolve_standard_queries(
        workspace,
        pattern["atlas"]["queries"],
        authority_registry_path=suite / ATLAS_AUTHORITIES_PATH,
        git_executable=git,
    )
    convention_patch.verify_atlas_evidence(pattern, resolved_queries)
    baseline_evidence_sha256 = sha256(
        _canonical_bytes(resolved_queries)
    ).hexdigest()

    if _blocking_material_uncertainties(census, pattern):
        _fail(
            "Blueprint plan is blocked: Atlas material census is uncertain "
            "inside the selected allocation domain"
        )
    material_id_collisions = [
        row
        for row in census.get("collisions", [])
        if isinstance(row, dict) and row.get("kind") == "material-id"
    ]
    if material_id_collisions:
        _fail("Blueprint plan is blocked: current material IDs collide")

    registry_name = _slug(name)
    if any(
        row.get("registry_name") == registry_name
        for row in census.get("observations", [])
        if isinstance(row, dict)
    ):
        _fail("Blueprint plan is blocked: requested registry name already exists")
    effective = {
        "color": color,
        "material_id": convention_patch.allocate_first_free(
            pattern, census["occupied_values"]
        ),
        "name": name,
        "registry_name": registry_name,
        "symbol_name": _groovy_symbol(name) if symbol is None else symbol,
        "translation": name if translation is None else translation,
    }
    first_render = convention_patch.render_updates(pattern, workspace, effective)
    second_render = convention_patch.render_updates(pattern, workspace, effective)
    if first_render != second_render:
        _fail("Blueprint convention patch rendered nondeterministically")
    operations = sorted(
        (_public_plan_operation(workspace, row) for row in first_render),
        key=lambda row: row["path"],
    )
    expected_paths = sorted(row["path"] for row in pattern["edits"])
    if [row["path"] for row in operations] != expected_paths:
        _fail("material-backed-fluid plan does not contain its exact three owners")

    tracked_diff = _run_git(
        workspace,
        ("diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"),
    )
    untracked = sorted(
        os.fsdecode(item)
        for item in _run_git(
            workspace,
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ).split(b"\0")
        if item
    )
    source_binding = {
        "revision": source_revision,
        "tracked_diff_sha256": sha256(tracked_diff).hexdigest(),
        "tracked_diff_size": len(tracked_diff),
    }
    operation_identity = [
        {
            key: row[key]
            for key in (
                "operation",
                "path",
                "before_sha256",
                "content_sha256",
                "size",
            )
        }
        for row in operations
    ]
    plan_material = {
        "profile_family_id": PACK_PROFILE_ID,
        "source": source_binding,
        "pattern_sha256": pattern["pattern_sha256"],
        "baseline_evidence_sha256": baseline_evidence_sha256,
        "census_id": census["census_id"],
        "effective_parameters": effective,
        "operations": operation_identity,
    }
    plan_id = "sha256:" + sha256(_canonical_bytes(plan_material)).hexdigest()
    return {
        "format": "workbench-material-backed-fluid-plan-v1",
        "schema_version": 1,
        "plan_id": plan_id,
        "state": "ready",
        "operation_class": "read-only",
        "profile_family_id": PACK_PROFILE_ID,
        "source": {
            **source_binding,
            "workspace_uri": workspace.as_uri(),
            "untracked_excluded": {
                "file_count": len(untracked),
                "paths_sha256": sha256(_canonical_bytes(untracked)).hexdigest(),
            },
        },
        "atlas": {
            "baseline_id": pattern["atlas"]["baseline_id"],
            "baseline_evidence_sha256": baseline_evidence_sha256,
            "census_id": census["census_id"],
            "observed_registration_count": len(census["observations"]),
            "occupied_id_count": len(census["occupied_values"]),
            "collision_count": len(census["collisions"]),
            "nonblocking_uncertainty_count": len(census["uncertainties"]),
        },
        "blueprint": {
            "pattern_id": "blueprints-convention-pattern:sha256:"
            + pattern["pattern_sha256"],
            "pattern_key": pattern["pattern_key"],
            "pattern_version": pattern["version"],
            "effective_parameters": effective,
        },
        "operations": operations,
        "outstanding_assertions": [
            "Groovy compilation in the exact projected Cleanroom client",
            "registered GregTech material identity",
            "registered Forge fluid identity",
            "resolved client English localization",
        ],
        "limitations": [
            "No source, staging, launcher, or runtime state was changed.",
            "Untracked source files are excluded from later disposable staging.",
            "This source plan is not positive runtime registration evidence.",
        ],
    }


def stage_material_backed_fluid(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
    state_root: Path | str | None = None,
    expected_plan_id: str | None = None,
) -> dict[str, Any]:
    """Plan and stage one material-backed fluid without editing its source."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    reviewed_plan = plan_material_backed_fluid(
        suite,
        workspace,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
    )
    if (
        expected_plan_id is not None
        and reviewed_plan["plan_id"] != expected_plan_id
    ):
        _fail(
            "material-backed-fluid plan changed after review; inspect the "
            "fresh three-file diff before staging"
        )
    inspection = inspect_project(suite, workspace)
    context = inspection.get("workspace_context")
    pack = context.get("pack") if isinstance(context, dict) else None
    if (
        not isinstance(pack, dict)
        or pack.get("profile_family_id") != PACK_PROFILE_ID
        or "construct" not in pack.get("permitted_operations", [])
    ):
        _fail(
            "material-backed-fluid staging requires a constructible "
            "Supersymmetry workspace"
        )

    workspace_context = context.get("workspace")
    source_revision = (
        workspace_context.get("revision")
        if isinstance(workspace_context, dict)
        else None
    )
    if not isinstance(source_revision, str):
        _fail("Supersymmetry workspace inspection lacks a source revision")

    material_census, convention_patch, planner = _authority_modules(suite)
    git = _git_executable()
    pattern = convention_patch.load_pattern(suite / EXPERIMENTAL_PATTERN_PATH)
    local_state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    stage_parent = _prepare_stage_parent(local_state, workspace)
    temporary = Path(tempfile.mkdtemp(prefix=".plan-", dir=stage_parent))
    try:
        source_census = material_census.census_material_builders(
            workspace,
            git_executable=git,
        )
        resolved_queries = material_census.resolve_standard_queries(
            workspace,
            pattern["atlas"]["queries"],
            authority_registry_path=suite / ATLAS_AUTHORITIES_PATH,
            git_executable=git,
        )
        convention_patch.verify_atlas_evidence(pattern, resolved_queries)
        baseline_evidence_sha256 = sha256(
            _canonical_bytes(resolved_queries)
        ).hexdigest()
        staged_workspace = temporary / "workspace"
        try:
            source, exclusions = copy_tracked_workspace(
                workspace,
                staged_workspace,
            )
        except WorkingTreeError as exc:
            _fail(f"cannot copy Blueprint source snapshot: {exc}")
        current_revision = _run_git(
            workspace, ("rev-parse", "HEAD")
        ).decode("ascii", "strict").strip()
        if current_revision != source_revision:
            _fail("source workspace revision changed while staging")
        baseline_revision = _commit_stage(
            staged_workspace,
            f"Capture Workbench source {source['tree_sha256']}",
            initialize=True,
        )
        staged_census = material_census.census_material_builders(
            staged_workspace,
            git_executable=git,
        )
        census_fields = (
            "files",
            "observations",
            "occupied_values",
            "registry_names",
            "collisions",
            "uncertainties",
        )
        if any(
            staged_census[field] != source_census[field]
            for field in census_fields
        ):
            _fail("material census changed while copying tracked source bytes")
        census = source_census
        target_manifest = planner.capture_target_state(
            staged_workspace,
            "pack",
            git_executable=git,
        )

        registry_name = _slug(name)
        symbol_name = _groovy_symbol(name) if symbol is None else symbol
        matching = [
            row
            for row in census["observations"]
            if row["registry_name"] == registry_name
        ]
        if _blocking_material_uncertainties(census, pattern):
            _fail(
                "Blueprint plan is blocked: Atlas material census is uncertain "
                "inside the selected allocation domain"
            )
        material_id_collisions = [
            row
            for row in census["collisions"]
            if row["kind"] == "material-id"
        ]
        if material_id_collisions:
            _fail("Blueprint plan is blocked: current material IDs collide")
        if matching:
            _fail(
                "Blueprint plan is blocked: requested registry name already exists"
            )
        material_id = convention_patch.allocate_first_free(
            pattern, census["occupied_values"]
        )
        effective = {
            "color": color,
            "material_id": material_id,
            "name": name,
            "registry_name": registry_name,
            "symbol_name": symbol_name,
            "translation": name if translation is None else translation,
        }
        first_render = convention_patch.render_updates(
            pattern, staged_workspace, effective
        )
        second_render = convention_patch.render_updates(
            pattern, staged_workspace, effective
        )
        if first_render != second_render:
            _fail("Blueprint convention patch rendered nondeterministically")
        sealed_operations = [
            {
                "ordinal": ordinal,
                "operation": row["operation"],
                "path": row["path"],
                "before_sha256": row["before_sha256"],
                "content_sha256": row["content_sha256"],
                "content_base64": base64.b64encode(row["content"]).decode(
                    "ascii"
                ),
            }
            for ordinal, row in enumerate(first_render)
        ]
        operation_identity = [
            {
                key: row[key]
                for key in (
                    "ordinal",
                    "operation",
                    "path",
                    "before_sha256",
                    "content_sha256",
                )
            }
            for row in sealed_operations
        ]
        plan_id = "blueprints-convention-plan:sha256:" + sha256(
            _canonical_bytes({
                "pattern_sha256": pattern["pattern_sha256"],
                "baseline_evidence_sha256": baseline_evidence_sha256,
                "target_state_id": target_manifest["target_state_id"],
                "effective_parameters": effective,
                "operations": operation_identity,
            })
        ).hexdigest()
        candidate_id = "blueprints-convention-candidate:sha256:" + sha256(
            _canonical_bytes({
                "plan_id": plan_id,
                "source_tree_sha256": source["tree_sha256"],
            })
        ).hexdigest()
        sealed = {"operations": sealed_operations}

        candidate_digest = candidate_id.removeprefix(
            "blueprints-convention-candidate:sha256:"
        )
        if re.fullmatch(r"[0-9a-f]{64}", candidate_digest) is None:
            _fail("Blueprint candidate has an invalid content identity")
        stage_binding_digest = sha256(
            _canonical_bytes({
                "candidate_id": candidate_id,
                "reviewed_plan_id": reviewed_plan["plan_id"],
                "source_revision": source_revision,
                "source_workspace_uri": workspace.as_uri(),
            })
        ).hexdigest()
        stage_root = stage_parent / stage_binding_digest
        outputs = _apply_sealed_operations(staged_workspace, sealed)
        stage_revision = _commit_stage(
            staged_workspace,
            f"Stage Workbench Blueprint {candidate_id}",
            initialize=False,
        )
        stage_tree_id = _run_git(
            staged_workspace, ("rev-parse", "HEAD^{tree}")
        ).decode("ascii", "strict").strip()
        if re.fullmatch(r"[0-9a-f]{40}", stage_tree_id) is None:
            _fail("disposable Blueprint workspace has an invalid tracked tree")
        if stage_root.exists() or stage_root.is_symlink():
            if not stage_root.is_dir() or stage_root.is_symlink():
                _fail(
                    "existing Blueprint stage target is not a regular "
                    "directory"
                )
            receipt = _verify_existing_stage(
                stage_root,
                candidate_id,
                target_manifest["target_state_id"],
                reviewed_plan["plan_id"],
                workspace.as_uri(),
                source_revision,
                stage_revision,
                stage_tree_id,
                outputs,
            )
            return {
                "format": "workbench-blueprint-stage-result-v2",
                "schema_version": 2,
                "outcome": "reused",
                "receipt": receipt,
            }

        receipt = {
            "format": "workbench-blueprint-stage-receipt-v2",
            "schema_version": 2,
            "stage_id": "sha256:" + sha256(_canonical_bytes({
                "candidate_id": candidate_id,
                "census_id": census["census_id"],
                "reviewed_plan_id": reviewed_plan["plan_id"],
                "source_revision": source_revision,
                "source_tree_sha256": source["tree_sha256"],
                "source_workspace_uri": workspace.as_uri(),
                "stage_revision": stage_revision,
                "stage_tree_id": stage_tree_id,
            })).hexdigest(),
            "operation_class": "local-mutation",
            "state": "staged",
            "source": {
                "workspace_uri": workspace.as_uri(),
                "revision": source_revision,
                "staged_baseline_revision": baseline_revision,
                "target_state_id": target_manifest["target_state_id"],
                "snapshot": source,
                "untracked_excluded": exclusions,
            },
            "atlas": {
                "baseline_id": pattern["atlas"]["baseline_id"],
                "baseline_evidence_sha256": baseline_evidence_sha256,
                "census_id": census["census_id"],
                "observed_registration_count": len(census["observations"]),
                "occupied_id_count": len(census["occupied_values"]),
                "collision_count": len(census["collisions"]),
                "uncertainties": census["uncertainties"],
            },
            "blueprint": {
                "reviewed_plan_id": reviewed_plan["plan_id"],
                "candidate_id": candidate_id,
                "plan_id": plan_id,
                "pattern_id": (
                    "blueprints-convention-pattern:sha256:"
                    + pattern["pattern_sha256"]
                ),
                "pattern_key": pattern["pattern_key"],
                "pattern_version": pattern["version"],
                "target_state_id": target_manifest["target_state_id"],
                "effective_parameters": effective,
            },
            "outputs": outputs,
            "target": {
                "workspace_uri": (stage_root / "workspace").as_uri(),
                "receipt_uri": (stage_root / "receipt.json").as_uri(),
                "revision": stage_revision,
                "tracked_tree_id": stage_tree_id,
            },
            "next": {
                "command": "runtime-materialize",
                "workspace_uri": (stage_root / "workspace").as_uri(),
            },
            "limitations": [
                "The source checkout was not changed.",
                "Untracked source files were excluded from disposable staging.",
                "The updated aggregate Groovy and localization have not been "
                "loaded by Cleanroom.",
                *(
                    [
                        "Atlas observed pre-existing registry-name collisions; "
                        "the requested registry name is not among them."
                    ]
                    if census["collisions"]
                    else []
                ),
            ],
        }
        _write_json(temporary / "receipt.json", receipt)
        os.replace(temporary, stage_root)
        return {
            "format": "workbench-blueprint-stage-result-v2",
            "schema_version": 2,
            "outcome": "staged",
            "receipt": receipt,
        }
    except BlueprintStageError:
        raise
    except (
        OSError,
        ValueError,
        planner.PlannerDiagnostic,
    ) as exc:
        _fail(f"cannot stage Atlas-backed Blueprint: {exc}")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


__all__ = [
    "BlueprintStageError",
    "plan_material_backed_fluid",
    "stage_material_backed_fluid",
    "validate_retained_blueprint_stage",
]
