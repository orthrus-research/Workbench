"""Physical Git metadata custody for Blueprints fresh-project bootstrap.

Blueprints admits the target and owns the V2 recovery journal. Core records
fresh Git initialization and performs bounded historical metadata mutations.
Whole-tree disposal remains a separate custody gate.
"""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
import re
import shutil
import stat
import tempfile
from threading import Event

from workbench_api.git_bootstrap import GitBootstrapError
from workbench_api.processes import ProcessError, execute_process
from workbench_api.record_stores import open_record_store

from . import check_storage
from .durable_records import count_interrupted_create_once_stages, publish_create_once_bytes
from .host_filesystem import fsync_directory, private_path, secure_private_path


_MAXIMUM_METADATA_BYTES = 1024 * 1024
_MARKER_NAME = "workbench-fresh-project-v2.json"
_INIT_ATTEMPT_KIND = "workbench-core-fresh-git-init-attempt-v1"
_INIT_RESULT_KIND = "workbench-core-fresh-git-init-result-v1"
_INIT_ATTEMPTS = "git-init-attempts"


def _fail(code: str, message: str) -> None:
    raise GitBootstrapError(code, message)


def _check_data(data: bytes | None) -> None:
    if data is not None and (type(data) is not bytes or len(data) > _MAXIMUM_METADATA_BYTES):
        _fail("bytes", "fresh Git metadata must be bounded bytes")


def _identity(state: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        state.st_dev, state.st_ino, state.st_mode, state.st_size,
        state.st_mtime_ns, state.st_nlink,
    )


def _ordinary_directory(path: Path) -> None:
    try:
        state = path.lstat()
    except OSError as exc:
        raise GitBootstrapError("path", "cannot inspect fresh Git directory") from exc
    if not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
        _fail("path", "fresh Git directory is redirected or not ordinary")


def _device_inode(state: os.stat_result) -> tuple[int, int]:
    return state.st_dev, state.st_ino


def _init_attempt_path(state_root: Path, plan_id: str) -> Path:
    if not isinstance(state_root, Path) or not state_root.is_absolute() or type(plan_id) is not str or not plan_id:
        _fail("path", "fresh Git init needs an absolute Core state root and plan")
    digest = sha256(plan_id.encode("utf-8")).hexdigest()
    return state_root / _INIT_ATTEMPTS / f"{digest}.json"


def _init_store(state_root: Path) -> None:
    selected = open_record_store("cleanroom-fresh-bootstrap-v2", state_root)
    if selected is None or selected.root != state_root:
        _fail("unavailable", "fresh Git init requires a Core registered state root")


def _init_target(
    target: Path, *, parent_identity: tuple[int, int],
    target_identity: tuple[int, int] | None,
) -> None:
    if not isinstance(target, Path) or not target.is_absolute() or target == Path(target.anchor):
        _fail("path", "fresh Git init needs an absolute non-root target")
    for path in reversed((target.parent, *target.parent.parents)):
        if getattr(path, "is_junction", lambda: False)():
            _fail("path", "fresh Git target traverses a junction")
        _ordinary_directory(path)
    if _device_inode(target.parent.lstat()) != parent_identity:
        _fail("stale", "fresh Git target parent changed after review")
    try:
        visible = target.lstat()
    except FileNotFoundError:
        if target_identity is not None:
            _fail("stale", "fresh Git target disappeared after review")
        return
    if target_identity is None or not stat.S_ISDIR(visible.st_mode) or _device_inode(visible) != target_identity:
        _fail("stale", "fresh Git target changed after review")
    if any(target.iterdir()):
        _fail("stale", "fresh Git target is no longer empty")


def _paths(target: Path, *, exclude: bool) -> tuple[Path, Path]:
    if not isinstance(target, Path) or not target.is_absolute() or target == Path(target.anchor):
        _fail("path", "fresh Git target must be an absolute non-root path")
    for path in reversed((target, *target.parents)):
        if getattr(path, "is_junction", lambda: False)():
            _fail("path", "fresh Git target traverses a junction")
        _ordinary_directory(path)
    git = target / ".git"
    _ordinary_directory(git)
    parent = git / "info" if exclude else git
    _ordinary_directory(parent)
    return parent, parent / ("exclude" if exclude else _MARKER_NAME)


def _read(path: Path) -> tuple[bytes, os.stat_result] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GitBootstrapError("path", "cannot open fresh Git metadata") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAXIMUM_METADATA_BYTES
        ):
            _fail("path", "fresh Git metadata is not a bounded independent file")
        chunks: list[bytes] = []
        remaining = _MAXIMUM_METADATA_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        visible = path.lstat()
    except OSError as exc:
        raise GitBootstrapError("stale", "fresh Git metadata changed while reading") from exc
    if (
        len(data) != before.st_size
        or len(data) > _MAXIMUM_METADATA_BYTES
        or _identity(before) != _identity(after)
        or _identity(after) != _identity(visible)
    ):
        _fail("stale", "fresh Git metadata changed while reading")
    return data, visible


def _matches(path: Path, expected: bytes | None) -> os.stat_result | None:
    _check_data(expected)
    observed = _read(path)
    if observed is None:
        if expected is None:
            return None
        _fail("stale", "fresh Git metadata disappeared after review")
    if expected is None or observed[0] != expected:
        _fail("stale", "fresh Git metadata bytes changed after review")
    return observed[1]


def _refuse_interrupted_stages(path: Path) -> None:
    prefix = f".{path.name}.workbench-git-"
    for candidate in path.parent.iterdir():
        if candidate.name.startswith(prefix) and candidate.name.endswith(".tmp"):
            _fail("stage", "interrupted fresh Git metadata stage requires recovery review")


def _create(path: Path, data: bytes) -> None:
    _check_data(data)
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise GitBootstrapError("stale", "fresh Git metadata already exists or cannot be created") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
        _matches(path, data)
    except BaseException:
        # A visible partial file is retained for journaled recovery review.
        raise


def _replace(path: Path, *, before: bytes | None, after: bytes) -> None:
    _check_data(before)
    _check_data(after)
    observed = _matches(path, before)
    if before == after:
        return
    if before is None:
        _create(path, after)
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.workbench-git-", suffix=".tmp", dir=path.parent,
    )
    staged = Path(name)
    stage_state = os.fstat(descriptor)
    stage_identity = stage_state.st_dev, stage_state.st_ino
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
        visible = _matches(path, before)
        if observed is None or visible is None or _identity(observed) != _identity(visible):
            _fail("stale", "fresh Git metadata identity changed before replacement")
        os.replace(staged, path)
        fsync_directory(path.parent)
        _matches(path, after)
    finally:
        # A stage from this live call is safe to remove by its exact path.
        try:
            visible_stage = staged.lstat()
        except FileNotFoundError:
            pass
        else:
            if (visible_stage.st_dev, visible_stage.st_ino) == stage_identity:
                staged.unlink()


def _remove(path: Path, *, expected: bytes) -> None:
    first = _matches(path, expected)
    second = _matches(path, expected)
    if first is None or second is None or _identity(first) != _identity(second):
        _fail("stale", "fresh Git metadata identity changed before removal")
    path.unlink()
    fsync_directory(path.parent)


class CoreGitBootstrap:
    """Core physical mutations for one owner-reviewed fresh Git target."""

    def initialize_repository(
        self, target: Path, state_root: Path, *, plan_id: str,
        observation_id: str, parent_identity: tuple[int, int],
        target_identity: tuple[int, int] | None,
    ) -> None:
        """Record intent before Core creates a target or starts Git.

        An interrupted attempt is retained for review. This result does not
        authorize recursive deletion: process absence and full tree ownership
        are separate proofs.
        """

        if (type(observation_id) is not str or not observation_id
                or type(parent_identity) is not tuple or len(parent_identity) != 2
                or any(type(value) is not int or value < 0 for value in parent_identity)
                or (target_identity is not None and (
                    type(target_identity) is not tuple or len(target_identity) != 2
                    or any(type(value) is not int or value < 0 for value in target_identity)
                ))):
            _fail("path", "fresh Git init observation identity is invalid")
        attempt = _init_attempt_path(state_root, plan_id)
        if target == state_root or target in state_root.parents or state_root in target.parents:
            _fail("path", "fresh Git init state root overlaps the target")
        _init_store(state_root)
        _init_target(
            target, parent_identity=parent_identity, target_identity=target_identity,
        )
        secure_private_path(attempt.parent, directory=True)
        if count_interrupted_create_once_stages(attempt):
            _fail("stage", "interrupted fresh Git init intent requires review")
        intent = check_storage.seal(_INIT_ATTEMPT_KIND, {
            "format": _INIT_ATTEMPT_KIND,
            "plan_id": plan_id,
            "observation_id": observation_id,
            "target": str(target),
            "parent_identity": list(parent_identity),
            "target_identity": None if target_identity is None else list(target_identity),
            "created_target": target_identity is None,
        })
        publish_create_once_bytes(
            attempt, check_storage.canonical(intent) + b"\n",
            byte_limit=_MAXIMUM_METADATA_BYTES,
        )
        if target_identity is None:
            target.mkdir(mode=0o700)
        _ordinary_directory(target)
        target_state = target.lstat()
        if target_identity is not None and _device_inode(target_state) != target_identity:
            _fail("stale", "fresh Git target changed before initialization")
        if any(target.iterdir()):
            _fail("stale", "fresh Git target changed before initialization")
        executable = shutil.which("git")
        if executable is None or not Path(executable).is_absolute():
            _fail("unavailable", "fresh Git initializer is unavailable")
        environment = dict(os.environ)
        for key in (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_WORK_TREE",
        ):
            environment.pop(key, None)
        environment.update({"GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
        try:
            result = execute_process(
                [executable, "-C", str(target), "init", "--quiet", "--initial-branch=main"],
                cwd=target, stdin=b"", environment=environment, cancelled=Event(),
                timeout_seconds=40, output_limit=_MAXIMUM_METADATA_BYTES,
            )
        except ProcessError as exc:
            raise GitBootstrapError("process", "fresh Git initialization did not close cleanly") from exc
        if result.exit_code != 0 or result.stdout or result.stderr:
            _fail("process", "fresh Git initialization failed or emitted unexpected output")
        if _device_inode(target.lstat()) != _device_inode(target_state):
            _fail("stale", "fresh Git target changed during initialization")
        git_path = target / ".git"
        _ordinary_directory(git_path)
        initialized = check_storage.seal(_INIT_RESULT_KIND, {
            "format": _INIT_RESULT_KIND,
            "attempt_id": intent["id"],
            "target_identity": list(_device_inode(target_state)),
            "git_identity": list(_device_inode(git_path.lstat())),
            "process_exit_code": result.exit_code,
            "disposal": "retained-process-absence-unproven",
        })
        destination = attempt.with_name(f"{attempt.stem}.result.json")
        publish_create_once_bytes(
            destination, check_storage.canonical(initialized) + b"\n",
            byte_limit=_MAXIMUM_METADATA_BYTES,
        )

    def has_init_attempt(self, state_root: Path, *, plan_id: str) -> bool:
        """Refuse deletion whenever a new Core init attempt may have run."""

        path = _init_attempt_path(state_root, plan_id)
        directory = path.parent
        if directory.is_symlink():
            return True
        if not directory.exists():
            return False
        if not directory.is_dir() or not private_path(directory, directory=True):
            return True
        try:
            children = {child.name: child for child in directory.iterdir()}
        except OSError:
            return True
        # An empty namespace can mean an erased current attempt. A populated
        # namespace permits an older journal only when every child proves it
        # belongs to a different plan; a shared state root is caller-selected.
        if not children or path.name in children or f"{path.stem}.result.json" in children:
            return True
        attempts: dict[str, dict[str, object]] = {}
        results: dict[str, dict[str, object]] = {}
        try:
            for name, child in children.items():
                result = name.endswith(".result.json")
                digest = name[:-12] if result else name[:-5] if name.endswith(".json") else ""
                if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    return True
                record = check_storage.read_json(child, byte_limit=_MAXIMUM_METADATA_BYTES)
                if type(record) is not dict or type(record.get("id")) is not str:
                    return True
                kind = _INIT_RESULT_KIND if result else _INIT_ATTEMPT_KIND
                body = {key: value for key, value in record.items() if key != "id"}
                if record.get("format") != kind or record["id"] != check_storage.seal(kind, body)["id"]:
                    return True
                if result:
                    results[digest] = record
                else:
                    other_plan = record.get("plan_id")
                    if type(other_plan) is not str or sha256(other_plan.encode("utf-8")).hexdigest() != digest:
                        return True
                    attempts[digest] = record
            if any(
                digest not in attempts or result.get("attempt_id") != attempts[digest]["id"]
                for digest, result in results.items()
            ):
                return True
        except (OSError, ValueError, TypeError, OverflowError):
            return True
        return False

    def replace_exclude(self, target: Path, *, before: bytes | None, after: bytes) -> None:
        _, path = _paths(target, exclude=True)
        _refuse_interrupted_stages(path)
        _replace(path, before=before, after=after)

    def restore_exclude(self, target: Path, *, before: bytes | None, after: bytes) -> None:
        _, path = _paths(target, exclude=True)
        _refuse_interrupted_stages(path)
        _check_data(before)
        _check_data(after)
        current = _read(path)
        if (None if current is None else current[0]) == before:
            return
        if current is None or current[0] != after:
            _fail("stale", "fresh Git exclude bytes changed before recovery")
        if before is None:
            _remove(path, expected=after)
        else:
            _replace(path, before=after, after=before)

    def create_marker(self, target: Path, data: bytes) -> None:
        _, path = _paths(target, exclude=False)
        _create(path, data)

    def remove_marker(self, target: Path, *, expected: bytes) -> None:
        _, path = _paths(target, exclude=False)
        _remove(path, expected=expected)


HOST = CoreGitBootstrap()


__all__ = ["CoreGitBootstrap", "HOST"]
