"""One reviewed material-backed-fluid edit through a disposable cold start."""

from __future__ import annotations

from workbench_api.resources import repository_root as _repository_resource_root
from workbench_api.profiles import Profile, profiles

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping, NoReturn, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname
from uuid import uuid4

from workbench_project_intelligence.git_observation import (
    GitObservationError,
    require_configured_git_executable,
)

from .blueprint_stage import (
    BlueprintStageError,
    plan_material_backed_fluid,
    stage_material_backed_fluid,
    validate_retained_blueprint_stage,
)
from .runtime_observe import (
    RuntimeObserveError,
    observe_project_runtime,
)
from .runtime_materialize import (
    packwiz_materialization_version,
    verify_packwiz_materialization_receipt_identity,
)
from .runtime_plan import plan_project_runtime
from workbench_api.state_paths import default_suite_state_root


_PLAN_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FML_CLIENT_LOADED_RE = re.compile(
    r"Forge Mod Loader has successfully loaded \d+ mods"
)


class MaterialFluidFlowError(ValueError):
    """Raised when the reviewed disposable material-fluid flow is unsafe."""


def _fail(message: str) -> NoReturn:
    raise MaterialFluidFlowError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _source_guard_snapshot(workspace: Path) -> dict[str, Any]:
    """Bind tracked drift plus every nonignored untracked source object."""

    if workspace.is_symlink() or not workspace.is_dir():
        _fail("material-fluid source workspace is unavailable or unsafe")

    def git_bytes(arguments: tuple[str, ...]) -> bytes:
        try:
            git = require_configured_git_executable()
        except GitObservationError as exc:
            _fail(f"cannot snapshot material-fluid source custody: {exc}")
        try:
            completed = subprocess.run(
                [git, "-C", str(workspace), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _fail(f"cannot snapshot material-fluid source custody: {exc}")
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            _fail(f"cannot snapshot material-fluid source custody: {detail}")
        return completed.stdout

    try:
        head = git_bytes(("rev-parse", "HEAD")).decode("ascii", "strict").strip()
    except UnicodeDecodeError as exc:
        _fail(f"material-fluid source revision is invalid: {exc}")
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        _fail("material-fluid source revision is invalid")
    tracked_diff = git_bytes(("diff", "--binary", "--no-ext-diff", "HEAD", "--"))
    if len(tracked_diff) > 16 * 1024 * 1024:
        _fail("material-fluid tracked source diff exceeds the custody limit")
    raw_paths = git_bytes(("ls-files", "-z", "--others", "--exclude-standard"))
    path_tokens = raw_paths.split(b"\0")
    if path_tokens and path_tokens[-1] == b"":
        path_tokens.pop()
    if len(path_tokens) > 10_000:
        _fail("material-fluid untracked source set exceeds the custody limit")
    untracked: list[dict[str, Any]] = []
    aggregate_bytes = 0
    for token in path_tokens:
        try:
            relative_text = token.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            _fail(f"material-fluid untracked source path is not UTF-8: {exc}")
        relative = Path(relative_text)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            _fail("material-fluid untracked source path is unsafe")
        path = workspace.joinpath(*relative.parts)
        if path.is_symlink():
            target = os.readlink(path)
            encoded_target = target.encode("utf-8")
            aggregate_bytes += len(encoded_target)
            record = {
                "kind": "symlink",
                "path": relative.as_posix(),
                "target": target,
                "target_sha256": sha256(encoded_target).hexdigest(),
            }
        elif path.is_file():
            digest = sha256()
            size = 0
            try:
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if aggregate_bytes + size > 1024 * 1024 * 1024:
                            _fail(
                                "material-fluid untracked source bytes exceed "
                                "the custody limit"
                            )
                        digest.update(chunk)
            except OSError as exc:
                _fail(f"cannot read material-fluid untracked source: {exc}")
            aggregate_bytes += size
            record = {
                "kind": "file",
                "path": relative.as_posix(),
                "sha256": digest.hexdigest(),
                "size": size,
            }
        else:
            _fail("material-fluid untracked source contains a special object")
        untracked.append(record)
    untracked.sort(key=lambda item: item["path"].encode("utf-8"))
    material = {
        "format": "workbench-material-fluid-source-snapshot-v1",
        "schema_version": 1,
        "head_revision": head,
        "tracked_diff_sha256": sha256(tracked_diff).hexdigest(),
        "tracked_diff_size": len(tracked_diff),
        "untracked": untracked,
    }
    return {
        **material,
        "snapshot_id": "sha256:" + sha256(_canonical_bytes(material)).hexdigest(),
    }


def _state_root(suite: Path, value: Path | str | None) -> Path:
    root = (
        default_suite_state_root(suite)
        if value is None
        else Path(value).expanduser().resolve()
    )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(f"cannot prepare Workbench material-fluid state: {exc}")
    if not root.is_dir() or root.is_symlink():
        _fail("Workbench material-fluid state root must be a regular directory")
    return root


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


def _reject_output_overlap(output: Path, protected: Path, label: str) -> None:
    if _paths_overlap(output, protected):
        _fail(f"{label} cannot overlap the developer or staged source workspace")


def _preflight_mutable_path(
    path: Path,
    *,
    protected: Sequence[Path],
    label: str,
) -> Path:
    candidate = path.expanduser().absolute()
    cursor = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail(f"{label} cannot traverse a symbolic link")
    resolved = candidate.resolve(strict=False)
    for workspace in protected:
        _reject_output_overlap(resolved, workspace, label)
    return resolved


def _preflight_runtime_state(
    state_root: Path,
    launcher_root: Path,
    *,
    protected: Sequence[Path],
) -> None:
    paths = (
        (state_root / "artifacts", "runtime artifact store"),
        (state_root / "evidence/runtime", "runtime evidence store"),
        (state_root / "fixtures", "runtime fixture store"),
        (state_root / "jdks", "runtime Java store"),
        (state_root / "staging/packwiz", "runtime Packwiz staging store"),
        (state_root / "cache/packwiz", "runtime Packwiz cache store"),
        (launcher_root / "instances", "launcher instance store"),
        (launcher_root / ".workbench", "launcher managed state"),
        (launcher_root / ".workbench/artifacts", "launcher artifact store"),
        (launcher_root / ".workbench/jdks", "launcher Java store"),
    )
    for path, label in paths:
        _preflight_mutable_path(path, protected=protected, label=label)


def _preflight_explicit_java_state(
    state_root: Path | None,
    *,
    protected: Sequence[Path],
) -> None:
    if state_root is None:
        return
    for path, label in (
        (state_root / "artifacts", "explicit launcher Java artifact store"),
        (state_root / "jdks", "explicit launcher Java store"),
    ):
        _preflight_mutable_path(path, protected=protected, label=label)


def _prepare_state_subdirectory(
    root: Path,
    relative_parts: tuple[str, ...],
    *,
    protected: Path,
    label: str,
) -> Path:
    cursor = root
    for part in relative_parts:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail(f"{label} cannot traverse a symbolic link")
    resolved = cursor.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        _fail(f"{label} escapes the Workbench state root")
    _reject_output_overlap(resolved, protected, label)
    try:
        cursor.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(f"cannot prepare {label}: {exc}")
    if cursor.is_symlink() or not cursor.is_dir() or cursor.resolve() != resolved:
        _fail(f"{label} is not a stable regular directory")
    return cursor


def _local_file_uri(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        _fail(f"{label} must be a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} must be a local file URI")
    return Path(url2pathname(parsed.path)).resolve()


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        _fail(f"material-fluid receipt already exists: {path}")
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        _fail(f"cannot retain material-fluid receipt: {exc}")


def _write_bytes(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        _fail(f"material-fluid observation input already exists: {path}")
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        _fail(f"cannot retain material-fluid observation input: {exc}")


def require_material_fluid_profile() -> Profile:
    """Require the installed owner before any material-fluid owner execution."""

    selected = next((profile for profile in profiles() if profile.id == "supersymmetry"), None)
    if selected is None:
        _fail("material-fluid requires the admitted and enabled Supersymmetry profile")
    return selected


def _profile_authority(role: str) -> Any:
    from workbench_api.profile_extensions import require_profile_extension

    require_material_fluid_profile()
    groups = {
        "material-fluid-observation": "workbench.material_fluid_observers",
        "material-fluid-construction": "workbench.material_fluid_policies",
    }
    return require_profile_extension(groups[role], "supersymmetry")


def _profile_observation_authority(suite: Path) -> Any:
    return _profile_authority("material-fluid-observation")


def _profile_runtime_policy_authority(suite: Path) -> Any:
    return _profile_authority("material-fluid-construction")


def material_fluid_feature_profile(
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    """Load the exact pack-owned facts that a generic product may project."""

    suite = (
        Path(suite_root).expanduser().resolve()
        if suite_root is not None
        else _repository_resource_root(__file__)
    )
    authority = _profile_runtime_policy_authority(suite)
    try:
        value = authority.material_fluid_feature_projection()
    except (AttributeError, TypeError, ValueError) as exc:
        _fail(f"cannot load the Supersymmetry material-fluid feature profile: {exc}")
    required = {
        "feature_kind",
        "format",
        "physical_side",
        "profile_family_id",
        "registry_namespace",
        "schema_version",
        "source_owners",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("format")
        != "workbench-supersymmetry-material-fluid-feature-projection-v1"
        or value.get("schema_version") != 1
        or value.get("profile_family_id") != "workbench-pack:supersymmetry"
        or value.get("feature_kind") != "material-backed-fluid"
        or re.fullmatch(r"[a-z][a-z0-9_]*", value.get("registry_namespace", ""))
        is None
        or value.get("physical_side") not in {"client", "server", "both"}
        or not isinstance(value.get("source_owners"), list)
        or len(value["source_owners"]) != 3
    ):
        _fail("Supersymmetry material-fluid feature profile is malformed")
    paths: set[str] = set()
    roles: set[str] = set()
    for row in value["source_owners"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"relative_path", "role"}
            or not isinstance(row.get("relative_path"), str)
            or not row["relative_path"]
            or Path(row["relative_path"]).is_absolute()
            or ".." in Path(row["relative_path"]).parts
            or not isinstance(row.get("role"), str)
            or not row["role"]
        ):
            _fail("Supersymmetry material-fluid source owner is malformed")
        paths.add(row["relative_path"])
        roles.add(row["role"])
    if len(paths) != 3 or len(roles) != 3:
        _fail("Supersymmetry material-fluid source owners are not unique")
    # Return detached values so a client cannot mutate the loaded authority.
    return json.loads(json.dumps(value, sort_keys=True))


def material_fluid_historical_graph_reference(
    suite_root: Path | str,
) -> dict[str, Any]:
    """Load Atlas-owned historical context without making it Shell policy."""

    suite = Path(suite_root).expanduser().resolve()
    authority = _profile_observation_authority(suite)
    try:
        value = authority.feature_studio_historical_recipe_reference()
    except (AttributeError, TypeError, ValueError) as exc:
        _fail(f"cannot load the Supersymmetry historical graph reference: {exc}")
    if (
        not isinstance(value, dict)
        or set(value)
        != {"state", "profile_id", "snapshot_id", "subjects", "limitation"}
        or value.get("state") != "historical-only"
        or not isinstance(value.get("profile_id"), str)
        or not value["profile_id"]
        or not isinstance(value.get("snapshot_id"), str)
        or not value["snapshot_id"]
        or not isinstance(value.get("subjects"), list)
        or len(value["subjects"]) != 2
        or any(not isinstance(item, str) or not item for item in value["subjects"])
        or len(set(value["subjects"])) != 2
        or not isinstance(value.get("limitation"), str)
        or not value["limitation"]
    ):
        _fail("Supersymmetry historical graph reference is malformed")
    return json.loads(json.dumps(value, sort_keys=True))


def _profile_compatibility_policy(
    suite: Path,
    requested: Sequence[Path | str],
) -> tuple[tuple[Path, ...], dict[str, Any]]:
    authority = _profile_runtime_policy_authority(suite)
    try:
        policy = authority.material_fluid_runtime_compatibility_policy(suite)
    except (OSError, TypeError, ValueError) as exc:
        _fail(f"cannot load the Supersymmetry runtime compatibility policy: {exc}")
    if (
        not isinstance(policy, dict)
        or policy.get("format")
        != "workbench-supersymmetry-material-fluid-compatibility-policy-v1"
        or policy.get("schema_version") != 1
        or policy.get("mode") != "required-exact-profile-patch-set"
        or not isinstance(policy.get("patches"), list)
        or len(policy["patches"]) != 1
        or not isinstance(policy["patches"][0], dict)
    ):
        _fail("Supersymmetry runtime compatibility policy has an invalid shape")
    authorized = tuple(
        _local_file_uri(item.get("spec_uri"), "profile compatibility patch")
        for item in policy["patches"]
    )
    requested_paths = tuple(Path(item).expanduser().resolve() for item in requested)
    if requested_paths and requested_paths != authorized:
        _fail(
            "material-fluid execution accepts only its exact profile-authorized "
            "compatibility patch set"
        )
    identity_patches = []
    for item in policy["patches"]:
        required = {
            "expected_matches",
            "find_utf8",
            "patch_id",
            "replace_utf8",
            "spec_sha256",
            "spec_uri",
            "target_entry",
            "target_entry_sha256",
            "target_path",
            "target_sha256",
        }
        if set(item) != required:
            _fail("profile compatibility patch binding has unexpected fields")
        identity_patches.append(
            {key: item[key] for key in sorted(required - {"spec_uri"})}
        )
    public_policy = {
        **policy,
        "identity_patches": identity_patches,
    }
    return authorized, public_policy


def material_fluid_runtime_compatibility_policy(
    suite_root: Path | str,
    requested: Sequence[Path | str] = (),
) -> tuple[tuple[Path, ...], dict[str, Any]]:
    """Return the profile-owned compatibility patch set for disposable runs."""

    return _profile_compatibility_policy(
        Path(suite_root).expanduser().resolve(),
        requested,
    )


def _prepare_observation_probe(
    suite: Path,
    temporary: Path,
    destination: Path,
    plan: Mapping[str, Any],
) -> tuple[Any, Path, dict[str, Any]]:
    authority = _profile_observation_authority(suite)
    parameters = plan["blueprint"]["blueprint"]["effective_parameters"]
    try:
        color_rgb = int(parameters["color"].removeprefix("0x"), 16)
        spec = authority.MaterialFluidProbeSpec(
            registry_name=parameters["registry_name"],
            symbol_name=parameters["symbol_name"],
            material_id=parameters["material_id"],
            color_rgb=color_rgb,
            translation=parameters["translation"],
            source_plan_id=plan["blueprint"]["plan_id"],
        )
        script = authority.build_material_fluid_probe(spec)
        overlay = authority.build_material_fluid_probe_overlay(spec)
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"cannot prepare the Supersymmetry observation probe: {exc}")
    probe_root = temporary / "observation-probe"
    try:
        probe_root.mkdir()
    except OSError as exc:
        _fail(f"cannot create the material-fluid observation root: {exc}")
    script_path = probe_root / "MaterialBackedFluidAssertion.groovy"
    overlay_path = probe_root / "overlay-v1.json"
    _write_bytes(script_path, script)
    _write_bytes(
        overlay_path,
        json.dumps(
            overlay,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n",
    )
    overlay_bytes = overlay_path.read_bytes()
    retained_root = destination / "observation-probe"
    return spec, overlay_path, {
        "probe_id": spec.probe_id,
        "source_plan_id": spec.source_plan_id,
        "script_sha256": sha256(script).hexdigest(),
        "script_size": len(script),
        "script_uri": (
            retained_root / "MaterialBackedFluidAssertion.groovy"
        ).as_uri(),
        "overlay_id": overlay["patch_id"],
        "overlay_spec_sha256": sha256(overlay_bytes).hexdigest(),
        "overlay_spec_uri": (retained_root / "overlay-v1.json").as_uri(),
        "projection_target": overlay["target"]["path"],
        "operation": "disposable-observation-file-overlay",
    }


def _pending_assertions() -> dict[str, dict[str, str]]:
    return {
        "fml_client_load": {
            "state": "pending",
            "meaning": "the disposable client reaches the exact FML loaded marker",
        },
        "groovy_compilation": {
            "state": "pending",
            "meaning": "the changed Groovy program compiles in the projected client",
        },
        "material_registration": {
            "state": "pending",
            "meaning": "the requested GregTech material identity is registered",
        },
        "fluid_registration": {
            "state": "pending",
            "meaning": "the material-backed Forge fluid identity is registered",
        },
        "localization": {
            "state": "pending",
            "meaning": "the requested client translation resolves to its intended label",
        },
    }


def plan_material_fluid_trial(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
    compatibility_patches: Sequence[Path | str] = (),
) -> dict[str, Any]:
    """Plan the exact edit and disposable execution boundary without writing."""

    if launcher not in {"prism", "multimc"}:
        _fail("material-fluid trial launcher must be prism or multimc")
    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    _authorized_patches, compatibility_policy = _profile_compatibility_policy(
        suite,
        compatibility_patches,
    )
    blueprint = plan_material_backed_fluid(
        suite,
        workspace,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
    )
    source_snapshot = _source_guard_snapshot(workspace)
    if blueprint.get("source", {}).get("revision") != source_snapshot[
        "head_revision"
    ]:
        _fail("material-fluid source changed while forming its reviewed plan")
    plan_material = {
        "blueprint_plan_id": blueprint["plan_id"],
        "launcher": launcher,
        "profile_compatibility": compatibility_policy["identity_patches"],
        "source_snapshot_id": source_snapshot["snapshot_id"],
        "target_policy": "fresh-unique-disposable-projection",
    }
    plan_id = "sha256:" + sha256(_canonical_bytes(plan_material)).hexdigest()
    return {
        "format": "workbench-material-fluid-flow-plan-v2",
        "schema_version": 2,
        "plan_id": plan_id,
        "state": "ready",
        "operation_class": "read-only",
        "source": blueprint["source"],
        "source_snapshot": source_snapshot,
        "blueprint": blueprint,
        "operations": blueprint["operations"],
        "execution": {
            "launcher": launcher,
            "profile_compatibility": compatibility_policy,
            "steps": [
                "revalidate the reviewed source-bound plan",
                "stage tracked source plus the exact three edits under ignored state",
                "materialize a profile-locked Cleanroom client",
                "publish and cold-start one fresh launcher projection",
                "observe process exit and retain final diagnostics",
            ],
            "target_policy": "fresh-unique-disposable-projection",
            "source_checkout_mutation": "forbidden",
        },
        "assertions": _pending_assertions(),
        "limitations": [
            "Planning changes no source, staging, launcher, or runtime state.",
            "FML load, Groovy compilation, material registration, fluid registration, "
            "and localization are independent assertion states.",
            "A generic FML client-loaded marker is not positive material, fluid, or "
            "localization evidence.",
        ],
    }


def _runtime_summary(
    runtime_result: dict[str, Any],
    *,
    expected_evidence_root: Path,
    launcher_root: Path,
    source_workspace: Path,
    staged_workspace: Path,
    expected_runtime_plan: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    probe: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    (
        session,
        launch_receipt,
        launch,
        _receipt_path,
        _receipt_digest,
        _session_path,
        _session_digest,
        run_root,
        _parent,
    ) = _validated_final_launch_receipt(runtime_result)
    target = session.get("target")
    if not isinstance(target, dict):
        _fail("runtime observation receipt lacks launch or target identity")
    if launch_receipt.get("plan_id") != expected_runtime_plan.get("plan_id"):
        _fail("runtime launch does not bind the exact staged runtime plan")
    materialization = _validate_materialization_binding(
        launch_receipt,
        expected_runtime_plan,
        staged_workspace,
    )
    _validate_compatibility_records(launch_receipt, compatibility_policy, probe)
    instance_id = launch.get("instance_id")
    if (
        not isinstance(instance_id, str)
        or not instance_id.startswith("workbench-")
    ):
        _fail("runtime observation did not use a Workbench-owned instance")
    expected_run_root = (
        expected_evidence_root.resolve()
        / str(expected_runtime_plan.get("plan_id")).removeprefix("sha256:")[:16]
        / "launches"
        / instance_id
    )
    if run_root != expected_run_root:
        _fail("runtime observation evidence is outside its exact staged plan lane")
    projection = _local_file_uri(launch.get("projection_uri"), "runtime projection")
    expected_instances = (launcher_root.resolve() / "instances").resolve()
    if projection.parent != expected_instances:
        _fail("runtime observation projection is outside the selected launcher root")
    if projection in {source_workspace.resolve(), staged_workspace.resolve()}:
        _fail("runtime observation projection aliases source or staging state")

    fml_observed = launch_receipt.get("outcome") == "checkpoint-reached"
    assertions = _pending_assertions()
    assertions["fml_client_load"] = {
        "state": "observed" if fml_observed else "not-observed",
        "meaning": assertions["fml_client_load"]["meaning"],
    }
    for key in (
        "groovy_compilation",
        "material_registration",
        "fluid_registration",
        "localization",
    ):
        assertions[key] = {
            "state": "not-observed",
            "meaning": assertions[key]["meaning"],
        }
    return ({
        "state": "observed",
        "outcome": runtime_result.get("outcome"),
        "session_id": session.get("session_id"),
        "receipt_uri": target.get("receipt_uri"),
        "final_launch_id": launch.get("final_launch_id"),
        "runtime_plan_id": expected_runtime_plan.get("plan_id"),
        **materialization,
        "instance_id": instance_id,
        "projection_uri": projection.as_uri(),
        "target_policy": "fresh-unique-disposable-projection",
    }, assertions)


def summarize_material_fluid_runtime(
    runtime_result: dict[str, Any],
    *,
    expected_evidence_root: Path,
    launcher_root: Path,
    source_workspace: Path,
    staged_workspace: Path,
    expected_runtime_plan: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    probe: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Validate and summarize a disposable material-fluid runtime result."""

    return _runtime_summary(
        runtime_result,
        expected_evidence_root=expected_evidence_root,
        launcher_root=launcher_root,
        source_workspace=source_workspace,
        staged_workspace=staged_workspace,
        expected_runtime_plan=expected_runtime_plan,
        compatibility_policy=compatibility_policy,
        probe=probe,
    )


def summarize_disposable_runtime(
    runtime_result: dict[str, Any],
    *,
    expected_evidence_root: Path,
    launcher_root: Path,
    source_workspace: Path,
    staged_workspace: Path,
    expected_runtime_plan: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    probe: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Validate custody shared by profile-owned disposable client observers.

    The returned assertion mapping retains the older material-flow vocabulary
    for compatibility.  Generic callers consume only ``fml_client_load`` and
    let their profile-owned observer define every domain assertion.
    """

    return _runtime_summary(
        runtime_result,
        expected_evidence_root=expected_evidence_root,
        launcher_root=launcher_root,
        source_workspace=source_workspace,
        staged_workspace=staged_workspace,
        expected_runtime_plan=expected_runtime_plan,
        compatibility_policy=compatibility_policy,
        probe=probe,
    )


def _runtime_capture_completed(runtime_result: Mapping[str, Any]) -> bool:
    session = runtime_result.get("receipt")
    launch_receipt = runtime_result.get("launch_receipt")
    if not isinstance(session, dict) or not isinstance(launch_receipt, dict):
        return False
    launch = session.get("launch")
    process = launch.get("process_observation") if isinstance(launch, dict) else None
    accepted_outcomes = {"completed", "analysis-incomplete"}
    return (
        runtime_result.get("outcome") in accepted_outcomes
        and session.get("outcome") == runtime_result.get("outcome")
        and launch_receipt.get("outcome") == "checkpoint-reached"
        and isinstance(process, dict)
        and process.get("state") == "exited"
    )


def material_fluid_runtime_capture_completed(
    runtime_result: Mapping[str, Any],
) -> bool:
    """Report whether the exact projected client completed its capture."""

    return _runtime_capture_completed(runtime_result)


def disposable_runtime_capture_completed(
    runtime_result: Mapping[str, Any],
) -> bool:
    """Report whether a custody-validated disposable client run completed."""

    return _runtime_capture_completed(runtime_result)


def _validate_compatibility_records(
    launch_receipt: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    probe: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = launch_receipt.get("compatibility_patches")
    projection = launch_receipt.get("projection")
    policy_patches = compatibility_policy.get("patches")
    if (
        not isinstance(records, list)
        or not isinstance(policy_patches, list)
        or len(records) != len(policy_patches) + 1
        or not all(isinstance(item, dict) for item in records)
        or not isinstance(projection, dict)
        or projection.get("compatibility_patches") != records
        or not all(isinstance(item, dict) for item in policy_patches)
    ):
        _fail("final launch does not bind the exact reviewed compatibility set")
    profile_records = records[: len(policy_patches)]
    observation_patch = records[-1]
    for policy, profile_patch in zip(policy_patches, profile_records, strict=True):
        profile_expected = {
            "entry": policy.get("target_entry"),
            "entry_sha256_before": policy.get("target_entry_sha256"),
            "find_utf8": policy.get("find_utf8"),
            "jar_sha256_before": policy.get("target_sha256"),
            "matches": policy.get("expected_matches"),
            "operation": "archive-entry-replacement",
            "patch_id": policy.get("patch_id"),
            "replace_utf8": policy.get("replace_utf8"),
            "spec_sha256": policy.get("spec_sha256"),
            "target_path": policy.get("target_path"),
        }
        if any(
            profile_patch.get(key) != value
            for key, value in profile_expected.items()
        ):
            _fail(
                "final launch profile compatibility patch differs from its "
                "reviewed policy"
            )
    probe_expected = {
        "file_sha256_after": probe.get("script_sha256"),
        "file_size_after": probe.get("script_size"),
        "operation": "file-overlay",
        "patch_id": probe.get("overlay_id"),
        "source_sha256": probe.get("script_sha256"),
        "source_size": probe.get("script_size"),
        "spec_sha256": probe.get("overlay_spec_sha256"),
        "target_path": probe.get("projection_target"),
    }
    if any(
        observation_patch.get(key) != value
        for key, value in probe_expected.items()
    ):
        _fail("final launch observation overlay differs from its reviewed probe")
    return profile_records, observation_patch


def _validate_materialization_binding(
    launch_receipt: Mapping[str, Any],
    expected_runtime_plan: Mapping[str, Any],
    staged_workspace: Path,
) -> dict[str, Any]:
    launch_projection = launch_receipt.get("projection")
    if not isinstance(launch_projection, dict):
        _fail("runtime launch lacks its materialization projection")
    source_instance = _local_file_uri(
        launch_projection.get("source_instance_uri"),
        "runtime materialization instance",
    )
    fixture_root = source_instance.parent
    if fixture_root.is_symlink() or not fixture_root.is_dir():
        _fail("runtime materialization fixture is unavailable or unsafe")
    receipt_path = fixture_root / "receipts/packwiz-materialization-v2.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        _fail("runtime materialization receipt is unavailable or unsafe")
    try:
        raw = receipt_path.read_bytes()
        receipt = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"runtime materialization receipt is not valid JSON: {exc}")
    if len(raw) > 4 * 1024 * 1024 or not isinstance(receipt, dict):
        _fail("runtime materialization receipt exceeds its byte limit")
    source = receipt.get("source_snapshot") if isinstance(receipt, dict) else None
    pack = receipt.get("refreshed_pack") if isinstance(receipt, dict) else None
    tools = receipt.get("tools") if isinstance(receipt, dict) else None
    launcher = receipt.get("launcher") if isinstance(receipt, dict) else None
    payload = receipt.get("payload") if isinstance(receipt, dict) else None
    material_target = receipt.get("target") if isinstance(receipt, dict) else None
    if (
        packwiz_materialization_version(receipt) != 2
        or receipt.get("state") != "materialized"
        or receipt.get("readiness") != "pack-payload-installed"
        or receipt.get("plan_id") != expected_runtime_plan.get("plan_id")
        or receipt.get("workspace") != expected_runtime_plan.get("workspace")
        or receipt.get("request") != expected_runtime_plan.get("request")
        or not isinstance(source, dict)
        or not isinstance(pack, dict)
        or not isinstance(tools, dict)
        or not isinstance(launcher, dict)
        or not isinstance(payload, dict)
        or not isinstance(material_target, dict)
        or material_target.get("fixture_root_uri") != fixture_root.as_uri()
        or material_target.get("receipt_uri") != receipt_path.as_uri()
        or material_target.get("instance_root_uri")
        != (fixture_root / "instance").as_uri()
        or payload.get("root_uri")
        != (fixture_root / "instance/.minecraft").as_uri()
        or launch_projection.get("source_instance_uri")
        != material_target.get("instance_root_uri")
        or launch_receipt.get("materialization_id")
        != receipt.get("materialization_id")
        or receipt.get("workspace", {}).get("root_uri")
        != staged_workspace.as_uri()
    ):
        _fail("runtime launch does not bind the exact staged materialization")
    if not verify_packwiz_materialization_receipt_identity(receipt):
        _fail("runtime materialization receipt has an invalid canonical identity")
    expected_payload = {
        key: payload.get(key)
        for key in ("tree_sha256", "file_count", "total_bytes")
    }
    projection_payload = launch_projection.get("payload")
    if (
        not isinstance(projection_payload, dict)
        or projection_payload.get("materialized") != expected_payload
    ):
        _fail("runtime launcher projection differs from the materialized payload")
    return {
        "materialization_id": receipt["materialization_id"],
        "materialization_receipt_sha256": sha256(raw).hexdigest(),
        "materialization_receipt_size": len(raw),
        "materialization_receipt_uri": receipt_path.as_uri(),
        "payload": expected_payload,
    }


def _validated_final_launch_receipt(
    runtime_result: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
    str,
    Path,
    str,
    Path,
    dict[str, Any],
]:
    launch_receipt = runtime_result.get("launch_receipt")
    session = runtime_result.get("receipt")
    if (
        not _runtime_capture_completed(runtime_result)
        or not isinstance(launch_receipt, dict)
        or not isinstance(session, dict)
    ):
        _fail("runtime observation lacks its completed final receipts")
    session_launch = session.get("launch")
    session_target = session.get("target")
    if (
        session.get("format") != "workbench-runtime-observation-session-v1"
        or session.get("schema_version") != 1
        or session.get("operation_class") != "local-mutation"
        or session.get("state")
        != ("complete" if runtime_result.get("outcome") == "completed" else "incomplete")
        or not isinstance(session_launch, dict)
        or not isinstance(session_target, dict)
    ):
        _fail("runtime observation session lacks its final launch binding")
    session_path = _local_file_uri(
        session_target.get("receipt_uri"),
        "runtime observation session receipt",
    )
    run_root = _local_file_uri(
        session_target.get("run_root_uri"),
        "runtime observation run root",
    )
    if (
        session_path != run_root / "runtime-observation-v1.json"
        or session_path.is_symlink()
        or not session_path.is_file()
        or not run_root.is_dir()
        or run_root.is_symlink()
    ):
        _fail("runtime observation session target is unavailable or unsafe")
    try:
        session_raw = session_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read the runtime observation session: {exc}")
    if len(session_raw) > 4 * 1024 * 1024:
        _fail("runtime observation session exceeds its byte limit")
    try:
        retained_session = json.loads(session_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"runtime observation session is not valid JSON: {exc}")
    if retained_session != session:
        _fail("in-memory and retained runtime observation sessions disagree")
    session_identity = dict(session)
    session_id = session_identity.pop("session_id", None)
    if (
        not isinstance(session_id, str)
        or session_id
        != "sha256:" + sha256(_canonical_bytes(session_identity)).hexdigest()
    ):
        _fail("runtime observation session has an invalid canonical identity")
    session_digest = sha256(session_raw).hexdigest()
    session_final = session_launch.get("final_receipt")
    launch_target = launch_receipt.get("target")
    launcher = launch_receipt.get("launcher")
    projection_record = launch_receipt.get("projection")
    launch_observation = launch_receipt.get("observation")
    launch_policy = launch_receipt.get("launch_policy")
    if (
        launch_receipt.get("format") != "workbench-runtime-launch-receipt-v3"
        or launch_receipt.get("schema_version") != 3
        or launch_receipt.get("state") != "observed"
        or launch_receipt.get("outcome") != "checkpoint-reached"
        or not isinstance(session_final, dict)
        or set(session_final) != {"sha256", "size", "uri"}
        or not isinstance(launch_target, dict)
        or not isinstance(launcher, dict)
        or not isinstance(projection_record, dict)
        or not isinstance(launch_observation, dict)
        or not isinstance(launch_policy, dict)
        or session_launch.get("final_launch_id") != launch_receipt.get("launch_id")
        or session_final.get("uri") != launch_target.get("receipt_uri")
        or launch_target.get("run_root_uri") != run_root.as_uri()
        or launch_target.get("parent_receipt_uri")
        != (run_root / "runtime-launch-v1.json").as_uri()
        or session_launch.get("instance_id") != launcher.get("instance_id")
        or session_launch.get("projection_uri") != launcher.get("projection_uri")
        or projection_record.get("projection_uri") != launcher.get("projection_uri")
        or launch_observation.get("session_exit")
        != session_launch.get("process_observation")
        or session.get("evidence") != launch_receipt.get("evidence")
    ):
        _fail("runtime observation session and final launch receipt disagree")
    receipt_path = _local_file_uri(
        launch_target.get("receipt_uri"),
        "final launch receipt",
    )
    if receipt_path != run_root / "runtime-launch-v3.json":
        _fail("final launch receipt is outside the bound runtime run root")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        _fail("final launch receipt is unavailable or unsafe")
    try:
        receipt_raw = receipt_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read the final launch receipt: {exc}")
    receipt_digest = sha256(receipt_raw).hexdigest()
    if (
        receipt_digest != session_final.get("sha256")
        or len(receipt_raw) != session_final.get("size")
    ):
        _fail("final launch receipt differs from the runtime session binding")
    try:
        retained_launch_receipt = json.loads(receipt_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"final launch receipt is not valid JSON: {exc}")
    if retained_launch_receipt != launch_receipt:
        _fail("in-memory and retained final launch receipts disagree")
    parent_binding = launch_receipt.get("parent_launch_receipt")
    if not isinstance(parent_binding, dict) or set(parent_binding) != {
        "launch_id",
        "sha256",
        "size",
        "uri",
    }:
        _fail("final launch receipt lacks its parent launch binding")
    if session_launch.get("parent_receipt") != {
        key: parent_binding.get(key) for key in ("sha256", "size", "uri")
    }:
        _fail("runtime session and parent launch receipt binding disagree")
    parent_path = _local_file_uri(
        parent_binding.get("uri"),
        "parent launch receipt",
    )
    if parent_path != run_root / "runtime-launch-v1.json":
        _fail("parent launch receipt is outside the bound runtime run root")
    try:
        parent_raw = parent_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read the parent launch receipt: {exc}")
    if (
        parent_path.is_symlink()
        or len(parent_raw) > 4 * 1024 * 1024
        or sha256(parent_raw).hexdigest() != parent_binding.get("sha256")
        or len(parent_raw) != parent_binding.get("size")
    ):
        _fail("parent launch receipt differs from the final launch binding")
    try:
        parent = json.loads(parent_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"parent launch receipt is not valid JSON: {exc}")
    parent_launcher = parent.get("launcher") if isinstance(parent, dict) else None
    parent_java = parent.get("java") if isinstance(parent, dict) else None
    parent_target = parent.get("target") if isinstance(parent, dict) else None
    if (
        not isinstance(parent, dict)
        or parent.get("format") != "workbench-runtime-launch-receipt-v2"
        or parent.get("schema_version") != 2
        or parent.get("state") != "observed"
        or parent.get("outcome") != "checkpoint-reached"
        or not isinstance(parent_launcher, dict)
        or not isinstance(parent_java, dict)
        or not isinstance(parent_target, dict)
        or parent.get("launch_id") != parent_binding.get("launch_id")
        or session_launch.get("parent_launch_id") != parent.get("launch_id")
        or parent_target.get("run_root_uri") != run_root.as_uri()
        or parent_target.get("receipt_uri") != parent_path.as_uri()
        or any(
            launch_receipt.get(key) != parent.get(key)
            for key in (
                "plan_id",
                "materialization_id",
                "project",
                "launcher",
                "projection",
                "compatibility_patches",
            )
        )
    ):
        _fail("parent and final launch receipts disagree")
    parent_identity = {
        "plan_id": parent.get("plan_id"),
        "materialization_id": parent.get("materialization_id"),
        "instance_id": parent_launcher.get("instance_id"),
        "started_at": parent.get("started_at"),
        "launcher": {
            "family": parent_launcher.get("family"),
            "sha256": parent_launcher.get("sha256"),
            "version_output": parent_launcher.get("version_output"),
        },
        "java_runtime_id": parent_java.get("runtime_id"),
        "projection_uri": parent_launcher.get("projection_uri"),
        "compatibility_patches": parent.get("compatibility_patches"),
    }
    if parent.get("launch_id") != "sha256:" + sha256(
        _canonical_bytes(parent_identity)
    ).hexdigest():
        _fail("parent launch receipt has an invalid canonical identity")
    evidence = launch_receipt.get("evidence")
    if not isinstance(evidence, list) or not all(
        isinstance(item, dict) for item in evidence
    ):
        _fail("final launch receipt has malformed evidence")
    evidence_labels = [item.get("label") for item in evidence]
    if (
        any(not isinstance(label, str) or not label for label in evidence_labels)
        or len(evidence_labels) != len(set(evidence_labels))
    ):
        _fail("final launch receipt has duplicate or invalid evidence labels")
    if any(item.get("label") == "minecraft-crash-report" for item in evidence):
        _fail("runtime observation captured a Minecraft crash report")
    lifecycle = session_launch.get("process_observation")
    pids = lifecycle.get("observed_pids") if isinstance(lifecycle, dict) else None
    samples = lifecycle.get("samples") if isinstance(lifecycle, dict) else None
    if (
        not isinstance(lifecycle, dict)
        or lifecycle.get("state") != "exited"
        or not isinstance(lifecycle.get("method"), str)
        or not lifecycle["method"]
        or type(samples) is not int
        or samples < 0
        or not isinstance(pids, list)
        or not pids
        or any(type(pid) is not int or pid <= 0 for pid in pids)
        or len(pids) != len(set(pids))
        or not isinstance(lifecycle.get("attached_at"), str)
        or not lifecycle["attached_at"]
        or not isinstance(lifecycle.get("observed_at"), str)
        or not lifecycle["observed_at"]
        or launch_policy.get("observation_boundary")
        != "projected-client-process-exit"
    ):
        _fail("runtime observation lacks exact projected-client process-exit evidence")
    checkpoint = launch_observation.get("checkpoint")
    parent_observation = parent.get("observation")
    parent_checkpoint = (
        parent_observation.get("checkpoint")
        if isinstance(parent_observation, dict)
        else None
    )
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != {"id", "marker", "source"}
        or checkpoint.get("id") != "fml-client-loaded"
        or checkpoint.get("source") != "minecraft-latest-log"
        or not isinstance(checkpoint.get("marker"), str)
        or _FML_CLIENT_LOADED_RE.fullmatch(checkpoint["marker"]) is None
        or parent_checkpoint != checkpoint
    ):
        _fail("runtime observation lacks its exact FML client-loaded checkpoint")
    latest_logs = [
        item
        for item in evidence
        if item.get("label") == "minecraft-latest-log"
        and item.get("state") == "captured"
    ]
    if len(latest_logs) != 1:
        _fail("runtime observation lacks one captured Minecraft latest log")
    latest = latest_logs[0]
    projection_path = _local_file_uri(
        launcher.get("projection_uri"),
        "runtime launcher projection",
    )
    expected_latest_source = projection_path / ".minecraft/logs/latest.log"
    expected_latest_capture = run_root / "final/minecraft-latest.log"
    latest_path = _local_file_uri(
        latest.get("capture_uri"),
        "captured Minecraft latest log",
    )
    if (
        latest.get("source_uri") != expected_latest_source.as_uri()
        or latest.get("capture_uri") != expected_latest_capture.as_uri()
        or latest_path != expected_latest_capture.resolve()
        or latest_path.is_symlink()
        or not latest_path.is_file()
    ):
        _fail("captured Minecraft latest log is outside final runtime evidence")
    try:
        latest_raw = latest_path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read the captured Minecraft latest log: {exc}")
    if (
        len(latest_raw) > 64 * 1024 * 1024
        or len(latest_raw) != latest.get("size")
        or sha256(latest_raw).hexdigest() != latest.get("sha256")
        or checkpoint["marker"] not in latest_raw.decode("utf-8", "replace")
    ):
        _fail("captured Minecraft latest log does not prove the FML checkpoint")
    final_identity = {
        "parent_launch_id": parent.get("launch_id"),
        "session_exit": session_launch.get("process_observation"),
        "evidence": [
            {
                key: item.get(key)
                for key in ("label", "state", "sha256", "size")
            }
            for item in evidence
        ],
    }
    if launch_receipt.get("launch_id") != "sha256:" + sha256(
        _canonical_bytes(final_identity)
    ).hexdigest():
        _fail("final launch receipt has an invalid canonical identity")
    return (
        session,
        launch_receipt,
        session_launch,
        receipt_path,
        receipt_digest,
        session_path,
        session_digest,
        run_root,
        parent,
    )


def validate_retained_material_fluid_runtime(
    receipt: Mapping[str, Any],
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    """Reopen and validate the exact retained runtime chain of one V2 success.

    This is the owner-side read boundary used by developer products.  It
    deliberately reuses the same final launch/session validator as execution
    instead of allowing a presentation client to reinterpret receipt custody.
    """

    runtime = receipt.get("runtime")
    profile_observation = receipt.get("profile_observation")
    if (
        receipt.get("format") != "workbench-material-fluid-flow-receipt-v2"
        or receipt.get("schema_version") != 2
        or receipt.get("state") != "complete"
        or receipt.get("outcome") != "runtime-completed"
        or not isinstance(runtime, dict)
        or not isinstance(profile_observation, dict)
        or profile_observation.get("state") != "observed"
    ):
        _fail("material-fluid retained runtime requires one completed V2 receipt")
    plan = receipt.get("plan")
    parameters = plan.get("effective_parameters") if isinstance(plan, dict) else None
    assertions = receipt.get("assertions")
    if (
        not isinstance(plan, dict)
        or not isinstance(parameters, dict)
        or not isinstance(assertions, dict)
        or runtime.get("state") != "observed"
        or runtime.get("target_policy") != "fresh-unique-disposable-projection"
    ):
        _fail("material-fluid completed receipt lacks its plan or assertion binding")
    try:
        suite = (
            Path(suite_root).expanduser().resolve()
            if suite_root is not None
            else _repository_resource_root(__file__)
        )
        authority = _profile_observation_authority(suite)
        spec = authority.MaterialFluidProbeSpec(
            registry_name=parameters["registry_name"],
            symbol_name=parameters["symbol_name"],
            material_id=parameters["material_id"],
            color_rgb=int(parameters["color"].removeprefix("0x"), 16),
            translation=parameters["translation"],
            source_plan_id=plan["blueprint_plan_id"],
        )
        validated_assessment = authority.validate_material_fluid_assessment(
            spec, profile_observation
        )
    except (KeyError, TypeError, ValueError) as exc:
        _fail(f"material-fluid retained profile assessment is invalid: {exc}")
    profile_assertions = validated_assessment["developer_assertions"]
    expected_assertions = _pending_assertions()
    for key, state in {
        "fml_client_load": "observed",
        **profile_assertions,
    }.items():
        expected_assertions[key]["state"] = state
    if assertions != expected_assertions:
        _fail("material-fluid flow assertions contradict the profile assessment")
    session_path = _local_file_uri(
        runtime.get("receipt_uri"), "material-fluid runtime session receipt"
    )
    if session_path.is_symlink() or not session_path.is_file():
        _fail("material-fluid runtime session receipt is unavailable or unsafe")
    try:
        session_raw = session_path.read_bytes()
        session = json.loads(session_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot reopen the material-fluid runtime session: {exc}")
    if len(session_raw) > 4 * 1024 * 1024 or not isinstance(session, dict):
        _fail("material-fluid runtime session is malformed or exceeds its bound")
    session_launch = session.get("launch")
    final_binding = (
        session_launch.get("final_receipt")
        if isinstance(session_launch, dict)
        and isinstance(session_launch.get("final_receipt"), dict)
        else None
    )
    if not isinstance(final_binding, dict):
        _fail("material-fluid runtime session lacks its final receipt binding")
    final_path = _local_file_uri(
        final_binding.get("uri"), "material-fluid final launch receipt"
    )
    if final_path.is_symlink() or not final_path.is_file():
        _fail("material-fluid final launch receipt is unavailable or unsafe")
    try:
        final_raw = final_path.read_bytes()
        final_receipt = json.loads(final_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot reopen the material-fluid final launch receipt: {exc}")
    if len(final_raw) > 4 * 1024 * 1024 or not isinstance(final_receipt, dict):
        _fail("material-fluid final launch receipt is malformed or exceeds its bound")

    (
        validated_session,
        validated_final,
        validated_launch,
        validated_final_path,
        final_digest,
        validated_session_path,
        session_digest,
        _run_root,
        _parent,
    ) = _validated_final_launch_receipt(
        {
            "outcome": session.get("outcome"),
            "receipt": session,
            "launch_receipt": final_receipt,
        }
    )
    snapshot = profile_observation.get("crucible_snapshot")
    binding = snapshot.get("binding") if isinstance(snapshot, dict) else None
    capture = binding.get("capture") if isinstance(binding, dict) else None
    if not isinstance(capture, dict):
        _fail("material-fluid profile assessment lacks runtime capture custody")
    expected_runtime = {
        "session_id": validated_session.get("session_id"),
        "final_launch_id": validated_final.get("launch_id"),
        "materialization_id": validated_final.get("materialization_id"),
        "instance_id": validated_launch.get("instance_id"),
        "projection_uri": validated_launch.get("projection_uri"),
    }
    final_projection = validated_final.get("projection")
    projected_payload = (
        final_projection.get("payload", {}).get("materialized")
        if isinstance(final_projection, dict)
        and isinstance(final_projection.get("payload"), dict)
        else None
    )
    materialization_path = _local_file_uri(
        runtime.get("materialization_receipt_uri"),
        "material-fluid materialization receipt",
    )
    if materialization_path.is_symlink() or not materialization_path.is_file():
        _fail("material-fluid materialization receipt is unavailable or unsafe")
    try:
        materialization_raw = materialization_path.read_bytes()
        materialization_receipt = json.loads(materialization_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot reopen the material-fluid materialization receipt: {exc}")
    if (
        len(materialization_raw) > 4 * 1024 * 1024
        or not isinstance(materialization_receipt, dict)
    ):
        _fail("material-fluid materialization receipt is malformed or exceeds its bound")
    materialization_payload = materialization_receipt.get("payload")
    retained_payload = (
        {
            key: materialization_payload.get(key)
            for key in ("tree_sha256", "file_count", "total_bytes")
        }
        if isinstance(materialization_payload, dict)
        else None
    )
    if (
        validated_session_path != session_path
        or validated_final_path != final_path
        or runtime.get("receipt_uri") != session_path.as_uri()
        or runtime.get("session_id") != expected_runtime["session_id"]
        or runtime.get("final_launch_id") != expected_runtime["final_launch_id"]
        or runtime.get("materialization_id")
        != expected_runtime["materialization_id"]
        or runtime.get("instance_id") != expected_runtime["instance_id"]
        or runtime.get("projection_uri") != expected_runtime["projection_uri"]
        or runtime.get("runtime_plan_id") != validated_final.get("plan_id")
        or runtime.get("outcome") != validated_session.get("outcome")
        or runtime.get("payload") != projected_payload
        or runtime.get("payload") != retained_payload
        or capture.get("session_outcome") != validated_session.get("outcome")
        or capture.get("session_receipt_uri") != session_path.as_uri()
        or capture.get("session_receipt_sha256") != session_digest
        or capture.get("session_receipt_size") != len(session_raw)
        or capture.get("launch_id") != expected_runtime["final_launch_id"]
        or capture.get("launch_receipt_uri") != final_path.as_uri()
        or capture.get("launch_receipt_sha256") != final_digest
        or capture.get("materialization_id")
        != expected_runtime["materialization_id"]
        or capture.get("materialization_receipt_uri")
        != runtime.get("materialization_receipt_uri")
        or capture.get("materialization_receipt_sha256")
        != runtime.get("materialization_receipt_sha256")
        or capture.get("materialization_receipt_size")
        != runtime.get("materialization_receipt_size")
        or capture.get("payload") != runtime.get("payload")
        or packwiz_materialization_version(materialization_receipt) != 2
        or materialization_receipt.get("state") != "materialized"
        or materialization_receipt.get("materialization_id")
        != expected_runtime["materialization_id"]
        or materialization_receipt.get("plan_id") != runtime.get("runtime_plan_id")
        or materialization_path.as_uri()
        != runtime.get("materialization_receipt_uri")
        or len(materialization_raw) != runtime.get("materialization_receipt_size")
        or sha256(materialization_raw).hexdigest()
        != runtime.get("materialization_receipt_sha256")
        or not verify_packwiz_materialization_receipt_identity(
            materialization_receipt
        )
    ):
        _fail("material-fluid receipt, session, launch, and profile custody disagree")
    return {
        "session": validated_session,
        "session_uri": session_path.as_uri(),
        "session_sha256": session_digest,
        "session_size": len(session_raw),
        "final_launch_receipt": validated_final,
        "final_launch_uri": final_path.as_uri(),
        "final_launch_sha256": final_digest,
        "final_launch_size": len(final_raw),
        "materialization_receipt": materialization_receipt,
        "materialization_uri": materialization_path.as_uri(),
        "materialization_sha256": sha256(materialization_raw).hexdigest(),
        "materialization_size": len(materialization_raw),
    }


def validate_retained_material_fluid_receipt(
    receipt: Mapping[str, Any],
    reviewed_plan: Mapping[str, Any],
    *,
    receipt_uri: str | None = None,
) -> dict[str, Any]:
    """Validate the identity, reviewed plan, and optional stage of one V2 receipt."""

    identity = {key: value for key, value in receipt.items() if key != "receipt_id"}
    target = receipt.get("target")
    expected_receipt_id = "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()
    if (
        receipt.get("format") != "workbench-material-fluid-flow-receipt-v2"
        or receipt.get("schema_version") != 2
        or receipt.get("receipt_id") != expected_receipt_id
        or not isinstance(target, Mapping)
        or not isinstance(target.get("receipt_uri"), str)
    ):
        _fail("material-fluid retained receipt identity is invalid")
    if receipt_uri is not None:
        selected_receipt = _local_file_uri(
            receipt_uri, "material-fluid retained receipt"
        )
        if target["receipt_uri"] != selected_receipt.as_uri():
            _fail("material-fluid retained receipt target is rebound")

    state = receipt.get("state")
    outcome = receipt.get("outcome")
    if not (
        (state == "complete" and outcome == "runtime-completed")
        or (
            state == "incomplete"
            and outcome in {"failed", "runtime-incomplete", "runtime-assertion-failed"}
        )
    ):
        _fail("material-fluid retained receipt state and outcome disagree")

    plan_binding = receipt.get("plan")
    source = receipt.get("source")
    stage_summary = receipt.get("blueprint_stage")
    blueprint_plan = reviewed_plan.get("blueprint")
    expected_patch_set = (
        reviewed_plan.get("execution", {})
        .get("profile_compatibility", {})
        .get("identity_patches")
        if isinstance(reviewed_plan.get("execution"), Mapping)
        else None
    )
    if (
        not isinstance(plan_binding, Mapping)
        or not isinstance(source, Mapping)
        or not isinstance(blueprint_plan, Mapping)
        or plan_binding.get("plan_id") != reviewed_plan.get("plan_id")
        or plan_binding.get("blueprint_plan_id") != blueprint_plan.get("plan_id")
        or plan_binding.get("effective_parameters")
        != blueprint_plan.get("blueprint", {}).get("effective_parameters")
        or plan_binding.get("profile_compatibility") != expected_patch_set
        or dict(source) != reviewed_plan.get("source")
    ):
        _fail("material-fluid retained receipt differs from its reviewed plan")

    stage: dict[str, Any] | None = None
    if isinstance(stage_summary, Mapping):
        try:
            stage = validate_retained_blueprint_stage(stage_summary, blueprint_plan)
        except BlueprintStageError as exc:
            _fail(f"material-fluid retained Blueprint stage is invalid: {exc}")
    elif stage_summary is not None or state == "complete":
        _fail("material-fluid retained receipt lacks its completed Blueprint stage")
    return {"blueprint_stage": stage}


def validate_retained_material_fluid_success(
    receipt: Mapping[str, Any],
    reviewed_plan: Mapping[str, Any],
    suite_root: Path | str,
    *,
    receipt_uri: str | None = None,
) -> dict[str, Any]:
    """Validate the complete Blueprint-to-runtime custody chain of a V2 success."""

    owner = validate_retained_material_fluid_receipt(
        receipt,
        reviewed_plan,
        receipt_uri=receipt_uri,
    )
    runtime = validate_retained_material_fluid_runtime(receipt, suite_root)
    return {**runtime, **owner}


def _captured_groovy_log(
    expected_evidence_root: Path,
    runtime_result: Mapping[str, Any],
    probe: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    expected_runtime_plan: Mapping[str, Any],
    staged_workspace: Path,
) -> tuple[bytes, dict[str, Any]]:
    (
        _session,
        launch_receipt,
        _session_launch,
        receipt_path,
        receipt_digest,
        session_path,
        session_digest,
        run_root,
        _parent,
    ) = (
        _validated_final_launch_receipt(runtime_result)
    )
    _validate_compatibility_records(
        launch_receipt,
        compatibility_policy,
        probe,
    )
    materialization = _validate_materialization_binding(
        launch_receipt,
        expected_runtime_plan,
        staged_workspace,
    )
    expected_run_root = (
        expected_evidence_root.resolve()
        / str(expected_runtime_plan.get("plan_id")).removeprefix("sha256:")[:16]
        / "launches"
        / str(launch_receipt.get("launcher", {}).get("instance_id"))
    )
    if run_root != expected_run_root:
        _fail("runtime observation evidence is outside its exact staged plan lane")
    matches = [
        item
        for item in launch_receipt.get("evidence", [])
        if isinstance(item, dict)
        and item.get("label") == "minecraft-groovy-log"
        and item.get("state") == "captured"
    ]
    if len(matches) != 1:
        _fail("runtime observation lacks one exact captured Groovy log")
    evidence = matches[0]
    path = _local_file_uri(evidence.get("capture_uri"), "captured Groovy log")
    projection = _local_file_uri(
        launch_receipt.get("launcher", {}).get("projection_uri"),
        "runtime launcher projection",
    )
    expected_source = projection / ".minecraft/logs/groovy.log"
    expected_capture = run_root / "final/minecraft-groovy.log"
    if (
        evidence.get("source_uri") != expected_source.as_uri()
        or evidence.get("capture_uri") != expected_capture.as_uri()
        or path != expected_capture.resolve()
        or path.is_symlink()
        or not path.is_file()
    ):
        _fail("captured Groovy log is unavailable or unsafe")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read the captured Groovy log: {exc}")
    digest = sha256(raw).hexdigest()
    if digest != evidence.get("sha256") or len(raw) != evidence.get("size"):
        _fail("captured Groovy log differs from its final launch receipt")

    return raw, {
        "launch_id": launch_receipt.get("launch_id"),
        "launch_receipt_sha256": receipt_digest,
        "launch_receipt_uri": receipt_path.as_uri(),
        "session_receipt_sha256": session_digest,
        "session_receipt_size": session_path.stat().st_size,
        "session_receipt_uri": session_path.as_uri(),
        **materialization,
        "groovy_log_sha256": digest,
        "groovy_log_uri": path.as_uri(),
        "probe_id": probe["probe_id"],
        "probe_script_sha256": probe["script_sha256"],
        "probe_overlay_id": probe["overlay_id"],
        "session_outcome": runtime_result.get("outcome"),
    }


def captured_material_fluid_groovy_log(
    expected_evidence_root: Path,
    runtime_result: Mapping[str, Any],
    probe: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    expected_runtime_plan: Mapping[str, Any],
    staged_workspace: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Return one custody-validated Groovy log and its runtime binding."""

    return _captured_groovy_log(
        expected_evidence_root,
        runtime_result,
        probe,
        compatibility_policy,
        expected_runtime_plan,
        staged_workspace,
    )


def captured_disposable_groovy_log(
    expected_evidence_root: Path,
    runtime_result: Mapping[str, Any],
    probe: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    expected_runtime_plan: Mapping[str, Any],
    staged_workspace: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Return one custody-validated Groovy log for a profile observer."""

    return _captured_groovy_log(
        expected_evidence_root,
        runtime_result,
        probe,
        compatibility_policy,
        expected_runtime_plan,
        staged_workspace,
    )


def disposable_runtime_receipt_evidence(
    runtime_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Project exact identities and links from validated final receipts."""

    (
        session,
        launch_receipt,
        _session_launch,
        launch_path,
        launch_digest,
        session_path,
        session_digest,
        _run_root,
        _parent,
    ) = _validated_final_launch_receipt(runtime_result)
    return {
        "final_launch_receipt": {
            "id": launch_receipt["launch_id"],
            "sha256": launch_digest,
            "size": launch_path.stat().st_size,
            "uri": launch_path.as_uri(),
        },
        "runtime_session_receipt": {
            "id": session["session_id"],
            "sha256": session_digest,
            "size": session_path.stat().st_size,
            "uri": session_path.as_uri(),
        },
    }


def _retained_runtime_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} is unavailable or unsafe")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot reopen {label}: {exc}")
    if len(raw) > 4 * 1024 * 1024 or not isinstance(value, dict):
        _fail(f"{label} is malformed or exceeds its byte bound")
    return raw, value


def validate_retained_disposable_runtime(
    suite_root: Path | str,
    runtime: Mapping[str, Any],
    assessment: Mapping[str, Any] | None,
    *,
    runtime_state: Path,
    source_workspace: Path,
    staged_workspace: Path,
    compatibility_policy: Mapping[str, Any],
    probe: Mapping[str, Any],
    attempt_root: Path,
    physical_side: str,
    projection_role: str,
    require_groovy_log: bool = True,
) -> dict[str, Any]:
    """Reopen one retained disposable client's complete custody chain.

    This is the generic read boundary for profile-owned observers.  It uses
    the same session, final-launch, compatibility, and materialization
    validators as the material-fluid execution path, then projects only the
    runtime capture fields that the caller's Atlas assessment is allowed to
    retain.  The original developer checkout need not still exist.
    """

    if (
        not isinstance(runtime, Mapping)
        or assessment is not None
        and not isinstance(assessment, Mapping)
        or type(require_groovy_log) is not bool
    ):
        _fail("retained disposable runtime or assessment is malformed")
    attempt = attempt_root.resolve()
    state = runtime_state.resolve()
    stage = staged_workspace.resolve()
    if (
        attempt_root.is_symlink()
        or not attempt.is_dir()
        or runtime_state.is_symlink()
        or not state.is_dir()
        or staged_workspace.is_symlink()
        or not stage.is_dir()
        or not state.is_relative_to(attempt)
        or not stage.is_relative_to(attempt)
    ):
        _fail("retained disposable runtime escaped its comparison attempt")

    session_path = _local_file_uri(
        runtime.get("receipt_uri"), "retained disposable runtime session"
    )
    _session_raw, session = _retained_runtime_json(
        session_path, "retained disposable runtime session"
    )
    session_launch = session.get("launch")
    final_binding = (
        session_launch.get("final_receipt")
        if isinstance(session_launch, dict)
        else None
    )
    if not isinstance(final_binding, dict):
        _fail("retained disposable runtime session lacks its final receipt")
    final_path = _local_file_uri(
        final_binding.get("uri"), "retained disposable final launch receipt"
    )
    _final_raw, final_receipt = _retained_runtime_json(
        final_path, "retained disposable final launch receipt"
    )
    launcher_record = final_receipt.get("launcher")
    launcher_family = (
        launcher_record.get("family")
        if isinstance(launcher_record, dict)
        else None
    )
    if launcher_family not in {"prism", "multimc"}:
        _fail("retained disposable runtime launcher family is unsupported")

    suite = Path(suite_root).resolve()
    expected_plan = plan_project_runtime(
        suite,
        stage,
        side="client",
        launcher=launcher_family,
        state_root=state,
    )
    if expected_plan.get("state") != "ready" or expected_plan.get("blockers") != []:
        _fail("retained disposable runtime plan is no longer exactly readable")

    projection = _local_file_uri(
        launcher_record.get("projection_uri"),
        "retained disposable launcher projection",
    )
    if projection.parent.name != "instances":
        _fail("retained disposable launcher projection has an invalid lane")
    runtime_result = {
        "outcome": runtime.get("outcome"),
        "receipt": session,
        "launch_receipt": final_receipt,
    }
    expected_summary, base_assertions = _runtime_summary(
        runtime_result,
        expected_evidence_root=state / "evidence/runtime",
        launcher_root=projection.parent.parent,
        source_workspace=source_workspace,
        staged_workspace=stage,
        expected_runtime_plan=expected_plan,
        compatibility_policy=compatibility_policy,
        probe=probe,
    )
    if dict(runtime) != expected_summary:
        _fail("retained disposable runtime summary contradicts its receipts")
    receipt_evidence = disposable_runtime_receipt_evidence(runtime_result)
    for reference in receipt_evidence.values():
        retained = _local_file_uri(
            reference.get("uri"), "retained disposable receipt"
        )
        if not retained.is_relative_to(attempt):
            _fail("retained disposable evidence escaped its comparison attempt")
    log_bytes: bytes | None = None
    custody: dict[str, Any] | None = None
    if require_groovy_log:
        log_bytes, custody = _captured_groovy_log(
            state / "evidence/runtime",
            runtime_result,
            probe,
            compatibility_policy,
            expected_plan,
            stage,
        )
        if assessment is not None:
            capture = assessment.get("capture")
            expected_capture = {
                "groovy_log_sha256": custody["groovy_log_sha256"],
                "groovy_log_uri": custody["groovy_log_uri"],
                "physical_side": physical_side,
                "projection_role": projection_role,
                "runtime_receipt_sha256": custody["session_receipt_sha256"],
                "runtime_receipt_size": custody["session_receipt_size"],
                "runtime_receipt_uri": custody["session_receipt_uri"],
            }
            if capture != expected_capture:
                _fail("retained disposable assessment contradicts runtime custody")
        for key in (
            "groovy_log_uri",
            "launch_receipt_uri",
            "materialization_receipt_uri",
            "session_receipt_uri",
        ):
            retained = _local_file_uri(custody.get(key), f"retained {key}")
            if not retained.is_relative_to(attempt):
                _fail("retained disposable evidence escaped its comparison attempt")
    return {
        "assertions": base_assertions,
        "capture": custody,
        "groovy_log_bytes": log_bytes,
        "receipt_evidence": receipt_evidence,
        "runtime_plan": expected_plan,
        "runtime_result": runtime_result,
        "runtime_summary": expected_summary,
    }


def _apply_profile_assertions(
    suite: Path,
    runtime_result: Mapping[str, Any],
    spec: Any,
    probe: Mapping[str, Any],
    compatibility_policy: Mapping[str, Any],
    expected_runtime_plan: Mapping[str, Any],
    staged_workspace: Path,
    expected_evidence_root: Path,
    assertions: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    authority = _profile_observation_authority(suite)
    meanings = _pending_assertions()
    try:
        log_bytes, capture = _captured_groovy_log(
            expected_evidence_root,
            runtime_result,
            probe,
            compatibility_policy,
            expected_runtime_plan,
            staged_workspace,
        )
        assessment = authority.interpret_material_fluid_observation(
            spec,
            groovy_log_bytes=log_bytes,
            capture=capture,
        )
        if not isinstance(assessment, dict):
            _fail("Atlas material-fluid assessment is not an object")
        profile_assertions = assessment.get("developer_assertions")
        expected_profile_keys = {
            "groovy_compilation",
            "material_registration",
            "fluid_registration",
            "localization",
        }
        if (
            not isinstance(profile_assertions, dict)
            or set(profile_assertions) != expected_profile_keys
            or any(
                value not in {"observed", "failed"}
                for value in profile_assertions.values()
            )
        ):
            _fail("Atlas material-fluid assessment lacks exact developer assertions")
        assertions_observed = all(
            value == "observed" for value in profile_assertions.values()
        )
        if (
            assessment.get("format")
            != "workbench-supersymmetry-material-fluid-assessment-v2"
            or assessment.get("schema_version") != 2
            or assessment.get("state") not in {"observed", "mismatch"}
            or (assessment.get("state") == "observed") != assertions_observed
        ):
            _fail("Atlas material-fluid assessment state contradicts its assertions")
    except (MaterialFluidFlowError, OSError, TypeError, ValueError) as exc:
        for key in (
            "groovy_compilation",
            "material_registration",
            "fluid_registration",
            "localization",
        ):
            assertions[key] = {
                "state": "not-observed",
                "meaning": meanings[key]["meaning"],
            }
        return ({
            "state": "inconclusive",
            "error": {"kind": type(exc).__name__, "message": str(exc)[:2000]},
            "probe": dict(probe),
        }, assertions)

    for key in sorted(expected_profile_keys):
        assertions[key] = {
            "state": profile_assertions[key],
            "meaning": meanings[key]["meaning"],
        }
    return assessment, assertions


def _stage_summary(stage_result: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    receipt = stage_result.get("receipt")
    if not isinstance(receipt, dict):
        _fail("Blueprint stage result lacks its receipt")
    target = receipt.get("target")
    blueprint = receipt.get("blueprint")
    if not isinstance(target, dict) or not isinstance(blueprint, dict):
        _fail("Blueprint stage receipt lacks target or candidate identity")
    workspace = _local_file_uri(target.get("workspace_uri"), "staged workspace")
    if not workspace.is_dir() or workspace.is_symlink():
        _fail("Blueprint staged workspace is unavailable or unsafe")
    return ({
        "state": receipt.get("state"),
        "outcome": stage_result.get("outcome"),
        "stage_id": receipt.get("stage_id"),
        "candidate_id": blueprint.get("candidate_id"),
        "revision": target.get("revision"),
        "tracked_tree_id": target.get("tracked_tree_id"),
        "workspace_uri": workspace.as_uri(),
        "receipt_uri": target.get("receipt_uri"),
    }, workspace)


def _finalize_receipt(
    temporary: Path,
    destination: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    receipt["target"] = {"receipt_uri": (destination / "receipt.json").as_uri()}
    identity = {key: value for key, value in receipt.items() if key != "receipt_id"}
    receipt["receipt_id"] = "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()
    _write_receipt(temporary / "receipt.json", receipt)
    try:
        os.replace(temporary, destination)
    except OSError as exc:
        _fail(f"cannot publish material-fluid receipt: {exc}")
    return receipt


def execute_material_fluid_trial(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    expected_plan_id: str | None = None,
    confirmed: bool = False,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    packwiz_executable: Path | str | None = None,
    seed_roots: Sequence[Path | str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    compatibility_patches: Sequence[Path | str] = (),
    timeout_seconds: float = 600.0,
    attach_timeout: float = 120.0,
    session_timeout: float = 21_600.0,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Stage, cold-start, observe, and retain one reviewed trial result."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    authorized_compatibility_patches, compatibility_policy = (
        _profile_compatibility_policy(suite, compatibility_patches)
    )
    plan = plan_material_fluid_trial(
        suite,
        workspace,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
        compatibility_patches=compatibility_patches,
    )
    if expected_plan_id is not None and _PLAN_ID_RE.fullmatch(expected_plan_id) is None:
        _fail("reviewed material-fluid plan ID must use sha256:<64 lowercase hex>")
    if expected_plan_id is None and not confirmed:
        _fail("material-fluid execution requires a reviewed plan ID or confirmation")
    if expected_plan_id is not None and expected_plan_id != plan["plan_id"]:
        _fail(
            "material-fluid plan changed after review; inspect the fresh three-file "
            "diff before running"
        )

    state_candidate = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    launcher_candidate = Path(launcher_root).expanduser().resolve()
    java_state_candidate = (
        None
        if launcher_java_state is None
        else Path(launcher_java_state).expanduser().resolve()
    )
    _reject_output_overlap(state_candidate, workspace, "material-fluid state root")
    _reject_output_overlap(launcher_candidate, workspace, "launcher root")
    if java_state_candidate is not None:
        _reject_output_overlap(
            java_state_candidate,
            workspace,
            "launcher Java state root",
        )
    _preflight_runtime_state(
        state_candidate,
        launcher_candidate,
        protected=(workspace,),
    )
    _preflight_explicit_java_state(
        java_state_candidate,
        protected=(workspace,),
    )
    local_state = _state_root(suite, state_candidate)
    attempts = _prepare_state_subdirectory(
        local_state,
        ("material-fluid", "attempts"),
        protected=workspace,
        label="material-fluid attempt root",
    )
    attempt_token = uuid4().hex
    destination = attempts / attempt_token
    if destination.exists() or destination.is_symlink():
        _fail("material-fluid attempt identity unexpectedly already exists")
    temporary = Path(tempfile.mkdtemp(prefix=".attempt-", dir=attempts))
    stage_summary: dict[str, Any] | None = None
    runtime_result: dict[str, Any] | None = None
    assertions = _pending_assertions()
    profile_observation: dict[str, Any] | None = None
    observation_probe: dict[str, Any] | None = None
    try:
        try:
            stage_result = stage_material_backed_fluid(
                suite,
                workspace,
                name=name,
                color=color,
                translation=translation,
                symbol=symbol,
                state_root=local_state,
                expected_plan_id=plan["blueprint"]["plan_id"],
            )
            stage_summary, staged_workspace = _stage_summary(stage_result)
            _reject_output_overlap(
                launcher_candidate,
                staged_workspace,
                "launcher root",
            )
            if java_state_candidate is not None:
                _reject_output_overlap(
                    java_state_candidate,
                    staged_workspace,
                    "launcher Java state root",
                )
            _preflight_runtime_state(
                local_state,
                launcher_candidate,
                protected=(workspace, staged_workspace),
            )
            _preflight_explicit_java_state(
                java_state_candidate,
                protected=(workspace, staged_workspace),
            )
            post_stage_plan = plan_material_fluid_trial(
                suite,
                workspace,
                name=name,
                color=color,
                translation=translation,
                symbol=symbol,
                launcher=launcher,
                compatibility_patches=compatibility_patches,
            )
            if post_stage_plan["plan_id"] != plan["plan_id"]:
                _fail("source workspace changed while preparing disposable staging")
            expected_runtime_plan = plan_project_runtime(
                suite,
                staged_workspace,
                side="client",
                launcher=launcher,
                state_root=local_state,
            )
            if (
                expected_runtime_plan.get("state") != "ready"
                or expected_runtime_plan.get("blockers") != []
                or not isinstance(expected_runtime_plan.get("plan_id"), str)
                or not _PLAN_ID_RE.fullmatch(expected_runtime_plan["plan_id"])
                or expected_runtime_plan.get("workspace")
                != {
                    "root_uri": staged_workspace.as_uri(),
                    "revision": stage_summary["revision"],
                    "dirty": False,
                }
            ):
                _fail("staged material-fluid runtime plan is not exactly ready")
            observation_spec, observation_overlay, observation_probe = (
                _prepare_observation_probe(
                    suite,
                    temporary,
                    destination,
                    plan,
                )
            )
            runtime_result = observe_project_runtime(
                suite,
                staged_workspace,
                launcher=launcher,
                launcher_executable=launcher_executable,
                launcher_root=launcher_candidate,
                launcher_profile=launcher_profile,
                launcher_java=launcher_java,
                launcher_java_state=java_state_candidate,
                packwiz_executable=packwiz_executable,
                seed_roots=seed_roots,
                memory_mib=memory_mib,
                offline_name=offline_name,
                compatibility_patches=(
                    *authorized_compatibility_patches,
                    observation_overlay,
                ),
                timeout_seconds=timeout_seconds,
                attach_timeout=attach_timeout,
                session_timeout=session_timeout,
                state_root=local_state,
            )
            runtime_summary, assertions = _runtime_summary(
                runtime_result,
                expected_evidence_root=local_state / "evidence/runtime",
                launcher_root=launcher_candidate,
                source_workspace=workspace,
                staged_workspace=staged_workspace,
                expected_runtime_plan=expected_runtime_plan,
                compatibility_policy=compatibility_policy,
                probe=observation_probe,
            )
            profile_observation, assertions = _apply_profile_assertions(
                suite,
                runtime_result,
                observation_spec,
                observation_probe,
                compatibility_policy,
                expected_runtime_plan,
                staged_workspace,
                local_state / "evidence/runtime",
                assertions,
            )
            final_source_plan = plan_material_fluid_trial(
                suite,
                workspace,
                name=name,
                color=color,
                translation=translation,
                symbol=symbol,
                launcher=launcher,
                compatibility_patches=compatibility_patches,
            )
            if (
                final_source_plan["plan_id"] != plan["plan_id"]
                or final_source_plan.get("source") != plan.get("source")
                or final_source_plan.get("source_snapshot")
                != plan.get("source_snapshot")
            ):
                _fail("developer source changed during the disposable runtime trial")
        except (
            BlueprintStageError,
            MaterialFluidFlowError,
            RuntimeObserveError,
            OSError,
        ) as exc:
            outcome = "failed"
            state = "incomplete"
            runtime_summary = {
                "state": "failed",
                "error": {
                    "kind": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
            }
            for value in assertions.values():
                value["state"] = "not-observed"
        else:
            runtime_completed = _runtime_capture_completed(runtime_result)
            profile_observed = (
                isinstance(profile_observation, dict)
                and profile_observation.get("state") == "observed"
            )
            all_assertions_observed = all(
                value.get("state") == "observed"
                for value in assertions.values()
            )
            outcome = (
                "runtime-completed"
                if runtime_completed
                and profile_observed
                and all_assertions_observed
                else "runtime-assertion-failed"
                if runtime_completed
                else "runtime-incomplete"
            )
            state = "complete" if outcome == "runtime-completed" else "incomplete"

        receipt = {
            "format": "workbench-material-fluid-flow-receipt-v2",
            "schema_version": 2,
            "receipt_id": "",
            "attempt_id": "uuid:" + attempt_token,
            "operation_class": "local-mutation",
            "state": state,
            "outcome": outcome,
            "plan": {
                "plan_id": plan["plan_id"],
                "blueprint_plan_id": plan["blueprint"]["plan_id"],
                "profile_compatibility": plan["execution"][
                    "profile_compatibility"
                ]["identity_patches"],
                "effective_parameters": plan["blueprint"]["blueprint"][
                    "effective_parameters"
                ],
            },
            "source": plan["source"],
            "blueprint_stage": stage_summary,
            "runtime": runtime_summary,
            "observation_probe": observation_probe,
            "profile_observation": profile_observation,
            "assertions": assertions,
            "target": {},
            "limitations": [
                (
                    "The developer checkout was revalidated unchanged after the runtime trial."
                    if outcome == "runtime-completed"
                    else "This failure receipt does not claim the developer checkout remained unchanged."
                ),
                "The runtime target is a fresh Workbench-owned launcher projection.",
                "FML load remains independent from the profile-owned Groovy, "
                "material, fluid, and localization assertion.",
                "The profile assessment applies only to this exact disposable "
                "projection, probe, final launch receipt, and captured Groovy log.",
            ],
        }
        receipt = _finalize_receipt(temporary, destination, receipt)
        temporary = destination
        return {
            "format": "workbench-material-fluid-flow-result-v2",
            "schema_version": 2,
            "outcome": outcome,
            "receipt": receipt,
            "runtime_result": runtime_result,
        }
    finally:
        if temporary.exists() and temporary != destination:
            shutil.rmtree(temporary)


__all__ = [
    "MaterialFluidFlowError",
    "captured_disposable_groovy_log",
    "captured_material_fluid_groovy_log",
    "disposable_runtime_capture_completed",
    "disposable_runtime_receipt_evidence",
    "execute_material_fluid_trial",
    "material_fluid_feature_profile",
    "material_fluid_historical_graph_reference",
    "material_fluid_runtime_capture_completed",
    "material_fluid_runtime_compatibility_policy",
    "plan_material_fluid_trial",
    "summarize_disposable_runtime",
    "summarize_material_fluid_runtime",
    "validate_retained_disposable_runtime",
    "validate_retained_material_fluid_receipt",
    "validate_retained_material_fluid_runtime",
    "validate_retained_material_fluid_success",
]
