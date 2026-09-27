"""Exact managed-attempt binding for Core's existing capture workspace tools."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Callable

from workbench_api.capture_workspaces import CaptureWorkspaceHostError
from workbench_api.managed_attempts import ManagedAttemptReference, managed_attempts

from . import capture_workspace, check_storage
from .filesystem_paths import native_path
from .managed_attempts import CoreManagedAttempts


class _ExecutionWorkspace:
    def __init__(self, attempts: CoreManagedAttempts, reference: ManagedAttemptReference):
        self._attempts = attempts
        self._reference = reference
        self._attempt_path()

    def _attempt_path(self) -> Path:
        return self._attempts._verified_path(self._reference)

    def _root(self) -> Path:
        # Reopen the exact cataloged attempt on every operation. The caller
        # cannot retarget this capability to another directory or attempt.
        return check_storage.ordinary(self._attempt_path() / "execution", directory=True)

    def copy_runtime(
        self, rows: list[dict], *, cancelled: Callable[[], bool] = lambda: False,
    ) -> Path:
        attempt = self._attempt_path()
        target = attempt / "execution"
        # Preserve V1 member modes, cancellation and partial-copy behavior by
        # delegating to the same Core copier at the fixed attempt-relative URI.
        check_storage.copy_manifest(attempt / "runtime", target, rows, cancelled=cancelled)
        return target

    def read_optional(self, relative: str) -> bytes | None:
        root = self._root()
        target = root.joinpath(*capture_workspace._parts(relative))
        return check_storage.read_bytes(target) if native_path(target).exists() else None

    def replace_file(
        self, relative: str, raw: bytes, *, expected_sha256: str | None = None,
    ) -> dict:
        return capture_workspace.replace_file(
            self._root(), relative, raw, expected_sha256=expected_sha256,
        )

    def inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]:
        return capture_workspace.inventory(self._root(), cancelled=cancelled)


class _PreparedWorkspace:
    def __init__(self, attempts: CoreManagedAttempts, reference: ManagedAttemptReference):
        self._attempts = attempts
        self._reference = reference
        self._attempt_path()

    def _attempt_path(self) -> Path:
        return self._attempts._verified_path(self._reference)

    def _root(self) -> Path:
        # Preparation and native admission both reopen the fixed runtime child.
        return check_storage.ordinary(self._attempt_path() / "runtime", directory=True)

    def materialize_inputs(
        self, *, runtime_root: Path, runtime_files: list[dict],
        java_home: Path, java_files: list[dict],
        source_files: dict[str, bytes], source_rows: list[dict],
        source_roots: list[str], runtime_exclude: list[str] | tuple[str, ...] = (),
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict:
        # Input locations and root policy belong to the caller; the verified
        # attempt fixes both retained destinations inside Core materialize.
        return capture_workspace.materialize(
            self._attempt_path(), runtime_root=runtime_root, runtime_files=runtime_files,
            java_home=java_home, java_files=java_files, source_files=source_files,
            source_rows=source_rows, source_roots=source_roots,
            runtime_exclude=runtime_exclude, cancelled=cancelled,
        )

    def create_from_build(self, relative: str, artifact: dict) -> dict:
        # The selected builder writes a single artifact in this attempt's
        # observer-build directory. Compare directory identity rather than
        # spelling: Windows Java may use an exact DOS alias for the attempt.
        if (not isinstance(artifact, dict) or set(artifact) != {"path", "size", "sha256"}
                or not isinstance(artifact["path"], str)
                or type(artifact["size"]) is not int or artifact["size"] < 0
                or not isinstance(artifact["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]) is None):
            raise capture_workspace.CaptureWorkspaceError("observer artifact identity is invalid")
        path = Path(artifact["path"])
        if (not path.is_absolute() or ".." in path.parts
                or artifact["path"] != str(Path(os.path.abspath(artifact["path"])))):
            raise capture_workspace.CaptureWorkspaceError("observer artifact path is invalid")
        build_root = check_storage.ordinary(self._attempt_path() / "observer-build", directory=True)
        source_parent = check_storage.ordinary(path.parent, directory=True)
        if not os.path.samefile(native_path(source_parent), native_path(build_root)):
            raise capture_workspace.CaptureWorkspaceError("observer artifact is outside this attempt's build")
        directory_identity = lambda item: (item.st_dev, item.st_ino)
        source_parent_before = directory_identity(native_path(source_parent).stat())
        build_root_before = directory_identity(native_path(build_root).stat())
        source = check_storage.ordinary(path)
        before = native_path(source).stat()
        raw = check_storage.read_bytes(path)
        after = native_path(check_storage.ordinary(path)).stat()
        file_identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_size,
            item.st_mtime_ns, item.st_ctime_ns,
        )
        if file_identity(before) != file_identity(after):
            raise capture_workspace.CaptureWorkspaceError("observer artifact changed during read")
        source_parent_after = check_storage.ordinary(path.parent, directory=True)
        build_root_after = check_storage.ordinary(self._attempt_path() / "observer-build", directory=True)
        if (directory_identity(native_path(source_parent_after).stat()) != source_parent_before
                or directory_identity(native_path(build_root_after).stat()) != build_root_before
                or not os.path.samefile(native_path(source_parent_after), native_path(build_root_after))):
            raise capture_workspace.CaptureWorkspaceError("observer build directory changed during read")
        if len(raw) != artifact["size"] or sha256(raw).hexdigest() != artifact["sha256"]:
            raise capture_workspace.CaptureWorkspaceError("observer artifact changed before publication")
        # The observer path was excluded from selected runtime inputs. Preserve
        # V1 create-only publication, modes and uncertain temporary files.
        return capture_workspace.replace_file(self._root(), relative, raw)

    def inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]:
        return capture_workspace.inventory(self._root(), cancelled=cancelled)

    def java_inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]:
        return capture_workspace.inventory(self._attempt_path() / "java", cancelled=cancelled)


class CoreCaptureWorkspaces:
    def execution(self, attempt: ManagedAttemptReference) -> _ExecutionWorkspace:
        selected = managed_attempts()
        if not isinstance(selected, CoreManagedAttempts):
            raise CaptureWorkspaceHostError("capture execution needs a Core managed-attempt host")
        return _ExecutionWorkspace(selected, attempt)

    def prepared(self, attempt: ManagedAttemptReference) -> _PreparedWorkspace:
        selected = managed_attempts()
        if not isinstance(selected, CoreManagedAttempts):
            raise CaptureWorkspaceHostError("capture preparation needs a Core managed-attempt host")
        return _PreparedWorkspace(selected, attempt)


HOST = CoreCaptureWorkspaces()


__all__ = ["CoreCaptureWorkspaces", "HOST"]
