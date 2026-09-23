"""Exact disposable staging of a reviewed Blueprints operation envelope.

The constructor retains validation authority. This owner copies source bytes
and applies only the already reviewed delta to a fresh disposable Git tree.
"""

from __future__ import annotations
import base64
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any, Callable, Mapping, NoReturn
from uuid import uuid4
from workbench_project_intelligence.git_observation import (
    GitObservationError,
    require_configured_git_executable,
)
from workbench_project_intelligence.working_tree import (
    WorkingTreeError,
    copy_tracked_workspace,
)

_GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_EXACT_GIT_ATTRIBUTES = b"* -text\n"


class ReviewedStageError(ValueError):
    """A reviewed operation cannot be staged safely."""


def _fail(message: str) -> NoReturn:
    raise ReviewedStageError(message)


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} must be a portable relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail(f"{label} must be a safe normalized relative path")
    return relative


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _git(root: Path, *arguments: str, environment: dict[str, str] | None = None) -> str:
    try:
        git = require_configured_git_executable()
    except GitObservationError as exc:
        raise ReviewedStageError(
            f"cannot prepare disposable feature Git workspace: {exc}"
        ) from exc
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
        raise ReviewedStageError(
            f"cannot prepare disposable feature Git workspace: {exc}"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        _fail(
            f"Git {' '.join(arguments)} failed in disposable feature staging: "
            f"{detail or 'unknown Git failure'}"
        )
    try:
        return completed.stdout.decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise ReviewedStageError("Git returned non-UTF-8 staging output") from exc


def _configure_exact_git_bytes(root: Path) -> None:
    """Disable Git's text conversion in one fresh disposable repository."""

    git_directory = root / ".git"
    info_directory = git_directory / "info"
    try:
        git_state = git_directory.lstat()
    except OSError as exc:
        raise ReviewedStageError(
            "disposable feature Git metadata is unavailable"
        ) from exc
    if stat.S_ISLNK(git_state.st_mode) or not stat.S_ISDIR(git_state.st_mode):
        _fail("disposable feature Git metadata is not an ordinary directory")
    try:
        if info_directory.exists() or info_directory.is_symlink():
            info_state = info_directory.lstat()
            if stat.S_ISLNK(info_state.st_mode) or not stat.S_ISDIR(info_state.st_mode):
                _fail("disposable feature Git info is not an ordinary directory")
        else:
            info_directory.mkdir(mode=0o700)
    except OSError as exc:
        raise ReviewedStageError(
            "cannot prepare disposable feature Git attributes"
        ) from exc

    attributes = info_directory / "attributes"
    temporary = info_directory / f".workbench-attributes-{uuid4().hex}.tmp"
    try:
        if attributes.exists() or attributes.is_symlink():
            attributes_state = attributes.lstat()
            if stat.S_ISLNK(attributes_state.st_mode) or not stat.S_ISREG(
                attributes_state.st_mode
            ):
                _fail("disposable feature Git attributes are not an ordinary file")
        with temporary.open("xb") as stream:
            stream.write(_EXACT_GIT_ATTRIBUTES)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, attributes)
        attributes_state = attributes.lstat()
        payload = attributes.read_bytes()
    except OSError as exc:
        raise ReviewedStageError(
            "cannot retain disposable feature Git attributes"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if (
        stat.S_ISLNK(attributes_state.st_mode)
        or not stat.S_ISREG(attributes_state.st_mode)
        or payload != _EXACT_GIT_ATTRIBUTES
    ):
        _fail("disposable feature Git attributes changed while written")


def _git_blob(root: Path, revision: str, relative: PurePosixPath) -> bytes:
    try:
        git = require_configured_git_executable()
    except GitObservationError as exc:
        raise ReviewedStageError(
            f"cannot verify disposable feature Git bytes: {exc}"
        ) from exc
    try:
        completed = subprocess.run(
            [
                git,
                "-C",
                str(root),
                "cat-file",
                "blob",
                f"{revision}:{relative.as_posix()}",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewedStageError("cannot verify disposable feature Git bytes") from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        _fail(
            "cannot verify disposable feature Git bytes: "
            f"{detail or 'unknown Git failure'}"
        )
    if len(completed.stdout) > 4 * 1024 * 1024:
        _fail("disposable feature Git blob exceeds its byte bound")
    return completed.stdout


def _commit(root: Path, message: str, *, initialize: bool = False) -> str:
    if initialize:
        _git(root, "init", "--quiet")
        _configure_exact_git_bytes(root)
    _git(root, "add", "--all", "--force")
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
        }
    )
    _git(
        root,
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
        environment=environment,
    )
    revision = _git(root, "rev-parse", "HEAD")
    if _GIT_REVISION.fullmatch(revision) is None:
        _fail("disposable feature workspace has an invalid revision")
    if _git(root, "status", "--porcelain=v1"):
        _fail("disposable feature workspace is dirty after commit")
    return revision


def _bounded_regular(root: Path, relative_value: Any, label: str) -> tuple[Path, bytes]:
    relative = _safe_relative(relative_value, label)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            state = current.lstat()
        except OSError as exc:
            raise ReviewedStageError(f"cannot inspect {label}") from exc
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            _fail(f"{label} traverses a symbolic link or non-directory")
    path = root.joinpath(*relative.parts)
    try:
        state = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise ReviewedStageError(f"cannot read {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        _fail(f"{label} is not a regular file")
    if len(raw) > 4 * 1024 * 1024 or len(raw) != state.st_size:
        _fail(f"{label} exceeds its byte bound or changed while read")
    return path, raw


def _decoded(row: Mapping[str, Any], prefix: str) -> bytes:
    try:
        raw = base64.b64decode(row[f"{prefix}_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewedStageError(f"feature {prefix} bytes are malformed") from exc
    if (
        type(row.get(f"{prefix}_size")) is not int
        or len(raw) != row[f"{prefix}_size"]
        or sha256(raw).hexdigest() != row.get(f"{prefix}_sha256")
    ):
        _fail(f"feature {prefix} byte identity changed")
    return raw


def stage_reviewed_feature_plan(
    reviewed: Mapping[str, Any],
    destination: Path | str,
    *,
    workspace: Path,
    verify: Callable[[], Mapping[str, Any]],
    apply_operations: bool,
    result_format: str,
) -> dict[str, Any]:
    """Create one exact baseline or candidate from a validated owner plan.

    The caller retains validation and construction authority.  This generic
    Blueprints seam only copies Git-tracked bytes, verifies the standard operation
    envelope, and optionally applies its already reviewed after-bytes.
    """

    verification = verify()
    if verification.get("state") != "ready":
        _fail(f"feature plan is stale: {verification.get('reason')}")
    workspace = workspace.resolve()
    target = Path(destination).expanduser().resolve(strict=False)
    if target.exists() or target.is_symlink() or _paths_overlap(target, workspace):
        _fail("disposable feature stage must be fresh and outside source")
    try:
        source_tree, excluded = copy_tracked_workspace(workspace, target)
    except WorkingTreeError as exc:
        raise ReviewedStageError(f"cannot copy tracked feature source: {exc}") from exc

    baseline_revision = _commit(
        target,
        f"Capture Workbench source {source_tree['tree_sha256']}",
        initialize=True,
    )
    post_copy = verify()
    if post_copy.get("state") != "ready":
        _fail("source changed while copying the disposable feature stage")

    for dependency in reviewed["dependencies"]:
        _path, raw = _bounded_regular(
            target,
            dependency["path"],
            "feature dependency",
        )
        if (
            len(raw) != dependency["size"]
            or sha256(raw).hexdigest() != dependency["sha256"]
            or _git_blob(
                target,
                baseline_revision,
                _safe_relative(dependency["path"], "feature dependency"),
            )
            != raw
        ):
            _fail("disposable feature dependency differs from the reviewed plan")

    outputs: list[dict[str, Any]] = []
    for operation in reviewed["operations"]:
        path, current = _bounded_regular(
            target,
            operation["path"],
            "feature stage target",
        )
        before = _decoded(operation, "before")
        after = _decoded(operation, "after")
        relative = _safe_relative(operation["path"], "feature stage target")
        if (
            current != before
            or _git_blob(target, baseline_revision, relative) != before
        ):
            _fail("disposable feature target differs from the reviewed plan")
        selected = before
        if apply_operations:
            try:
                with path.open("wb") as output:
                    output.write(after)
                    output.flush()
                    os.fsync(output.fileno())
            except OSError as exc:
                raise ReviewedStageError(
                    f"cannot write disposable feature target: {operation['path']}"
                ) from exc
            selected = after
        outputs.append(
            {
                "ordinal": operation["ordinal"],
                "path": operation["path"],
                "role": operation["role"],
                "sha256": sha256(selected).hexdigest(),
                "size": len(selected),
            }
        )

    stage_revision = baseline_revision
    if apply_operations:
        stage_revision = _commit(
            target,
            f"Stage Workbench feature {reviewed['id']}",
        )
    tree_id = _git(target, "rev-parse", "HEAD^{tree}")
    if _GIT_REVISION.fullmatch(tree_id) is None:
        _fail("disposable feature stage has an invalid Git tree")
    for output in outputs:
        _path, raw = _bounded_regular(
            target,
            output["path"],
            "committed feature stage target",
        )
        committed = _git_blob(
            target,
            stage_revision,
            _safe_relative(output["path"], "committed feature stage target"),
        )
        if (
            len(raw) != output["size"]
            or sha256(raw).hexdigest() != output["sha256"]
            or committed != raw
        ):
            _fail("committed feature stage target differs from the reviewed plan")
    final_source = verify()
    if final_source.get("state") != "ready":
        _fail("source changed while committing the disposable feature stage")
    return {
        "format": result_format,
        "schema_version": 1,
        "state": "staged",
        "plan_id": reviewed["id"],
        "source_workspace_uri": workspace.as_uri(),
        "workspace_uri": target.as_uri(),
        "source_tree": source_tree,
        "untracked_excluded": excluded,
        "baseline_revision": baseline_revision,
        "revision": stage_revision,
        "tracked_tree_id": tree_id,
        "outputs": outputs,
    }
