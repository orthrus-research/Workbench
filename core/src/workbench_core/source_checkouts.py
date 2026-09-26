"""Physical custody for an exact, reviewed Git checkout acquisition.

Project owners select a remote, immutable commit and required project shape.
Core owns the sibling clone stage, bounded Git processes, create-once receipt,
no-replace checkout promotion and exact-identity failure cleanup. This is a
process-local transaction; a portable source lock and restart journal require
a separate versioned plan.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence
from uuid import uuid4

from workbench_api.source_checkouts import SourceCheckoutError

from .durable_records import (
    private_record_lock, publish_immutable_bytes, read_private_bytes,
)
from .host_filesystem import fsync_directory, secure_private_path


_OBJECT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_RECEIPT = re.compile(r"workbench-project-acquisition:sha256:([0-9a-f]{64})\Z")


def _fail(code: str, message: str) -> None:
    raise SourceCheckoutError(code, message)


def _directory(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SourceCheckoutError("path", f"cannot inspect acquisition directory: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        _fail("path", "acquisition parent is not an ordinary directory")
    return info


def _no_redirects(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            _fail("path", "acquisition path traverses a redirect")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _run_git(
    executable: str, arguments: Sequence[str], *, environment: Mapping[str, str],
    timeout_seconds: float, full_output_limit: int | None = None,
) -> tuple[int, str, str]:
    values = dict(environment)
    values["GIT_TERMINAL_PROMPT"] = "0"
    values["GIT_CONFIG_NOSYSTEM"] = "1"
    values.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    values.setdefault("LC_ALL", "C")
    values["GIT_NO_REPLACE_OBJECTS"] = "1"
    values["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(
                [executable, *arguments], check=False, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, timeout=timeout_seconds, env=values,
            )
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read((full_output_limit + 1) if full_output_limit is not None else 4096)
            if full_output_limit is not None and len(output) > full_output_limit:
                _fail("bounds", "Git verification output exceeds its bound")
            return (
                completed.returncode,
                output.decode("utf-8", "replace"),
                stderr.read(4096).decode("utf-8", "replace"),
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceCheckoutError(
            "process", f"Git acquisition command failed before completion ({type(exc).__name__})",
        ) from exc


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Promote a directory without replacing a destination created after review."""

    if os.name == "nt":
        os.rename(source, destination)  # Windows refuses an existing destination.
        return
    if sys.platform != "linux":
        _fail("unsupported", "atomic no-replace checkout publication is unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    operation = getattr(libc, "renameat2", None)
    if operation is None:
        _fail("unsupported", "atomic no-replace checkout publication is unavailable")
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    if operation(
        at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(destination),
        rename_noreplace,
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _receipt_path(state_root: Path, receipt_id: str) -> Path:
    if not isinstance(state_root, Path) or not state_root.is_absolute():
        _fail("path", "acquisition state root must be absolute")
    if type(receipt_id) is not str or (match := _RECEIPT.fullmatch(receipt_id)) is None:
        _fail("receipt", "acquisition receipt identity is invalid")
    path = state_root / "evidence" / "project-acquisition" / f"{match.group(1)}.json"
    _no_redirects(path.parent)
    missing: list[Path] = []
    cursor = path.parent
    while not cursor.exists() and not cursor.is_symlink():
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    _directory(cursor)
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
            fsync_directory(directory.parent)
        except OSError as exc:
            raise SourceCheckoutError("receipt", f"cannot create acquisition receipt store: {exc}") from exc
    for directory in (state_root, state_root / "evidence", path.parent):
        _directory(directory)
        try:
            secure_private_path(directory, directory=True)
        except OSError as exc:
            raise SourceCheckoutError("receipt", f"cannot secure acquisition receipt store: {exc}") from exc
    return path


class _CoreSourceCheckout:
    def __init__(
        self, destination: Path, staging_root: Path, *, parent_identity: tuple[int, int],
        stage_identity: tuple[int, int], observed_commit: str, observed_tree: str,
    ):
        self.destination = destination
        self.staging_root = staging_root
        self.parent_identity = parent_identity
        self.stage_identity = stage_identity
        self.observed_commit = observed_commit
        self.observed_tree = observed_tree
        self._published = False

    def _check_stage(self) -> None:
        _no_redirects(self.destination.parent)
        if _identity(_directory(self.destination.parent)) != self.parent_identity:
            _fail("path", "acquisition destination parent changed after review")
        if _identity(_directory(self.staging_root)) != self.stage_identity:
            _fail("stage", "acquisition clone stage changed identity")

    @staticmethod
    def _rollback_receipt(
        path: Path, *, expected_identity: tuple[int, int],
        expected_bytes: bytes,
    ) -> None:
        lock_path = path.with_name(f".{path.name}.record.lock")
        try:
            with private_record_lock(lock_path):
                quarantine = path.with_name(f".{path.name}.{uuid4().hex}.rollback")
                before = path.lstat()
                if (
                    not stat.S_ISREG(before.st_mode)
                    or _identity(before) != expected_identity
                    or before.st_nlink != 1
                    or read_private_bytes(path, byte_limit=len(expected_bytes)) != expected_bytes
                ):
                    _fail("recovery", "acquisition receipt changed after publication; recovery is required")
                # Move the name first, then inspect the moved inode. A later
                # replacement can never be unlinked merely because it raced
                # the preflight comparison.
                _rename_noreplace(path, quarantine)
                moved = quarantine.lstat()
                if _identity(moved) != expected_identity:
                    try:
                        _rename_noreplace(quarantine, path)
                    except OSError:
                        pass  # Retain the replacement in quarantine for recovery.
                    _fail("recovery", "acquisition receipt changed during rollback; recovery is required")
                quarantine.unlink()
                fsync_directory(path.parent)
        except SourceCheckoutError:
            raise
        except OSError as exc:
            raise SourceCheckoutError(
                "recovery", f"cannot roll back exact acquisition receipt: {exc}",
            ) from exc

    def publish(
        self, *, state_root: Path, receipt_id: str, receipt_bytes: bytes,
        byte_limit: int,
    ) -> Path:
        if self._published:
            _fail("state", "acquisition checkout was already published")
        self._check_stage()
        if self.destination.exists() or self.destination.is_symlink():
            _fail("collision", "acquisition destination appeared after review")
        if type(receipt_bytes) is not bytes or type(byte_limit) is not int or byte_limit < len(receipt_bytes):
            _fail("receipt", "acquisition receipt exceeds its byte bound")
        if not isinstance(state_root, Path) or not state_root.is_absolute():
            _fail("path", "acquisition state root must be absolute")
        if state_root == self.destination or state_root.is_relative_to(self.destination):
            _fail("path", "acquisition receipt state cannot be inside the unpublished checkout")
        path = _receipt_path(state_root, receipt_id)
        try:
            publish_immutable_bytes(path, receipt_bytes, byte_limit=byte_limit)
        except OSError as exc:
            raise SourceCheckoutError("receipt", f"cannot publish acquisition receipt: {exc}") from exc
        try:
            receipt_identity = _identity(path.lstat())
        except OSError as exc:
            raise SourceCheckoutError("recovery", f"cannot identify published acquisition receipt: {exc}") from exc
        try:
            self._check_stage()
            _rename_noreplace(self.staging_root, self.destination)
        except (OSError, SourceCheckoutError) as exc:
            self._rollback_receipt(
                path, expected_identity=receipt_identity,
                expected_bytes=receipt_bytes,
            )
            raise SourceCheckoutError("publish", f"cannot publish acquired checkout: {exc}") from exc
        self._published = True
        try:
            if _identity(_directory(self.destination)) != self.stage_identity:
                _fail("recovery", "published checkout changed identity; recovery is required")
            fsync_directory(self.destination.parent)
        except SourceCheckoutError:
            raise
        except OSError as exc:
            raise SourceCheckoutError(
                "recovery", f"acquired checkout was published but directory flush failed: {exc}",
            ) from exc
        return path

    def close(self) -> None:
        if self._published or (
            not self.staging_root.exists() and not self.staging_root.is_symlink()
        ):
            return
        if _identity(_directory(self.staging_root)) != self.stage_identity:
            _fail("stage", "acquisition stage changed identity; refusing cleanup")
        try:
            shutil.rmtree(self.staging_root)
            fsync_directory(self.staging_root.parent)
        except OSError as exc:
            raise SourceCheckoutError("cleanup", f"cannot clean exact acquisition stage: {exc}") from exc


class CoreSourceCheckouts:
    """Clone into one owned stage and expose it only for semantic validation."""

    def verify_exact(
        self, root: Path, *, git_executable: str, expected_commit: str,
        expected_tree: str, environment: Mapping[str, str],
    ) -> tuple[str, str]:
        """Reopen one clean local checkout without trusting its branch or index."""

        if (not isinstance(root, Path) or not root.is_absolute()
                or not _OBJECT.fullmatch(expected_commit)
                or not _OBJECT.fullmatch(expected_tree)
                or len(expected_commit) != len(expected_tree)):
            _fail("input", "exact checkout verification input is invalid")
        _no_redirects(root)
        root_identity = _identity(_directory(root))
        parent_identity = _identity(_directory(root.parent))
        _directory(root / ".git")
        count = 0
        def walk_error(exc: OSError) -> None:
            raise SourceCheckoutError("unavailable", "checkout cannot be completely inspected") from exc

        for parent, names, files in os.walk(root, followlinks=False, onerror=walk_error):
            for name in names + files:
                count += 1
                if count > 200_000:
                    _fail("bounds", "checkout has too many members to verify")
                member = Path(parent) / name
                try:
                    info = member.lstat()
                except OSError as exc:
                    raise SourceCheckoutError("changed", "checkout member disappeared during verification") from exc
                if (stat.S_ISLNK(info.st_mode) or getattr(member, "is_junction", lambda: False)()
                        or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
                    _fail("unsafe", "checkout contains a redirect or special member")
        for expression, expected in (("HEAD^{commit}", expected_commit), ("HEAD^{tree}", expected_tree)):
            status, output, _ = _run_git(
                git_executable, ("-C", str(root), "rev-parse", "--verify", expression),
                environment=environment, timeout_seconds=30.0,
            )
            if status or output.strip() != expected:
                _fail("identity", "checkout differs from the reviewed commit or tree")
        status, output, _ = _run_git(
            git_executable,
            ("-C", str(root), "status", "--porcelain=v1", "--untracked-files=all",
             "--ignored=matching", "--ignore-submodules=none"),
            environment=environment, timeout_seconds=60.0,
        )
        if status or output.strip():
            _fail("changed", "checkout worktree differs from the reviewed tree")
        status, output, _ = _run_git(
            git_executable, ("-C", str(root), "ls-files", "-v"),
            environment=environment, timeout_seconds=60.0,
            full_output_limit=32 * 1024 * 1024,
        )
        if status or any(not line.startswith("H ") for line in output.splitlines()):
            _fail("changed", "checkout index hides changes to the reviewed tree")
        if (_identity(_directory(root)) != root_identity
                or _identity(_directory(root.parent)) != parent_identity):
            _fail("changed", "checkout directory changed during verification")
        return expected_commit, expected_tree

    def open(
        self, destination: Path, *, git_executable: str, remote_url: str,
        checkout_branch: str, expected_commit: str,
        expected_tree: str | None, environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> _CoreSourceCheckout:
        if not isinstance(destination, Path) or not destination.is_absolute():
            _fail("path", "acquisition destination must be absolute")
        if (
            type(git_executable) is not str or not git_executable
            or type(remote_url) is not str or not remote_url or remote_url.startswith("-")
            or type(checkout_branch) is not str or _BRANCH.fullmatch(checkout_branch) is None
            or type(expected_commit) is not str or _OBJECT.fullmatch(expected_commit) is None
            or expected_tree is not None and (
                type(expected_tree) is not str or _OBJECT.fullmatch(expected_tree) is None
                or len(expected_tree) != len(expected_commit)
            )
            or not isinstance(environment, Mapping)
            or any(type(key) is not str or type(value) is not str for key, value in environment.items())
            or type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 3600
        ):
            _fail("input", "acquisition clone input is invalid")
        _no_redirects(destination.parent)
        parent_identity = _identity(_directory(destination.parent))
        if destination.exists() or destination.is_symlink():
            _fail("collision", "acquisition destination appeared after review")
        try:
            staging = Path(tempfile.mkdtemp(
                prefix=f".workbench-acquire-{destination.name}-", dir=destination.parent,
            ))
        except OSError as exc:
            raise SourceCheckoutError("stage", f"cannot stage acquisition clone: {exc}") from exc
        stage_identity = _identity(_directory(staging))
        checkout = _CoreSourceCheckout(
            destination, staging, parent_identity=parent_identity,
            stage_identity=stage_identity, observed_commit="", observed_tree="",
        )
        try:
            status, stdout, stderr = _run_git(
                git_executable,
                (
                    "clone", "--no-tags", "--single-branch", "--branch",
                    checkout_branch, "--", remote_url, str(staging),
                ),
                environment=environment, timeout_seconds=timeout_seconds,
            )
            if status:
                detail = (stderr or stdout).strip()[:4000]
                _fail("clone", "Git clone failed" + (f": {detail}" if detail else f" (exit {status})"))
            status, stdout, _ = _run_git(
                git_executable,
                ("-C", str(staging), "rev-parse", "--verify", "HEAD^{commit}"),
                environment=environment, timeout_seconds=30.0,
            )
            commit = stdout.strip()
            if status or commit != expected_commit:
                _fail("identity", "remote channel moved during acquisition; review a new exact plan")
            status, stdout, _ = _run_git(
                git_executable,
                ("-C", str(staging), "rev-parse", "--verify", "HEAD^{tree}"),
                environment=environment, timeout_seconds=30.0,
            )
            tree = stdout.strip()
            if status or _OBJECT.fullmatch(tree) is None or len(tree) != len(commit):
                _fail("identity", "acquired checkout has no verifiable Git tree")
            if expected_tree is not None and tree != expected_tree:
                _fail("tree", "acquired checkout tree differs from approved exact tree")
            checkout._check_stage()
            checkout.observed_commit = commit
            checkout.observed_tree = tree
            return checkout
        except BaseException:
            checkout.close()
            raise


__all__ = ["CoreSourceCheckouts"]
