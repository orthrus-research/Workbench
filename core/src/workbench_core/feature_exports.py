"""Core publication and exact reopening of Feature Studio export pairs.

The V1 owner receipt stays byte-identical. A V2 result may additionally name
the transport tree that holds its two files, making later paired verification
mandatory for newly cataloged exports.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import sys

from workbench_api.feature_exports import FeatureExportError
from workbench_api.transport_trees import TransportTreeError

from .durable_files import _directory as pinned_directory
from .transport_trees import CoreTransportTrees, MAX_TOTAL_BYTES
from .user_config_home import default_user_config_home


_RECEIPT_ID = re.compile(r"feature-studio-export-receipt:sha256:[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_OWNER = "workbench-shell.feature-studio"
_MAX_RECEIPT = 1024 * 1024
_MAX_PATCH = 128 * 1024 * 1024


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns)


def _read_pair(
    root: Path, *, patch_sha256: str, patch_size: int,
    receipt_sha256: str, receipt_size: int,
) -> None:
    """Pin the directory and both files while checking their exact pair."""

    try:
        directory = pinned_directory(root, create=False)
        try:
            before = os.fstat(directory)
            if not stat.S_ISDIR(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o700:
                raise FeatureExportError("feature-export.changed", "export directory mode changed")
            if set(os.listdir(directory)) != {"feature.patch", "receipt.json"}:
                raise FeatureExportError("feature-export.changed", "export pair has missing or extra members")
            for name, expected_digest, expected_size in (
                ("feature.patch", patch_sha256, patch_size),
                ("receipt.json", receipt_sha256, receipt_size),
            ):
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    opened = os.fstat(descriptor)
                    visible = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                            or stat.S_IMODE(opened.st_mode) != 0o644
                            or _identity(opened) != _identity(visible)
                            or opened.st_size != expected_size):
                        raise FeatureExportError("feature-export.changed", "export member identity changed")
                    digest = sha256()
                    size = 0
                    while chunk := os.read(descriptor, 1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        if size > expected_size:
                            raise FeatureExportError("feature-export.changed", "export member grew while reading")
                    after = os.fstat(descriptor)
                    visible_after = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if (size != expected_size or digest.hexdigest() != expected_digest
                            or _identity(after) != _identity(opened)
                            or _identity(visible_after) != _identity(opened)):
                        raise FeatureExportError("feature-export.changed", "export member bytes changed")
                finally:
                    os.close(descriptor)
            if (set(os.listdir(directory)) != {"feature.patch", "receipt.json"}
                    or _identity(os.fstat(directory)) != _identity(before)):
                raise FeatureExportError("feature-export.changed", "export pair changed during verification")
        finally:
            os.close(directory)
    except FeatureExportError:
        raise
    except (OSError, ValueError) as exc:
        raise FeatureExportError("feature-export.changed", "export pair is unavailable or redirecting") from exc


def _write_member(directory: int, name: str, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(name, flags, 0o644, dir_fd=directory)
    try:
        os.fchmod(descriptor, 0o644)
        remaining = memoryview(value)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise FeatureExportError("feature-export.write", "export write made no progress")
            remaining = remaining[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CoreFeatureExports:
    def __init__(self, *, configuration_home: Path | None = None):
        self.configuration_home = configuration_home

    def _host(self, workspace: Path) -> CoreTransportTrees:
        return CoreTransportTrees(
            workspace=workspace,
            configuration_home=self.configuration_home or default_user_config_home(),
            owner_id=_OWNER,
        )

    def cataloged(self, *, workspace: Path, target: Path) -> bool:
        if not isinstance(workspace, Path) or not workspace.is_absolute() or not isinstance(target, Path) or not target.is_absolute():
            raise FeatureExportError("feature-export.path", "export catalog lookup paths are invalid")
        try:
            return any(row["path"] == target for row in self._host(workspace).inventory())
        except TransportTreeError as exc:
            raise FeatureExportError("feature-export.changed", "Core export catalog cannot be read") from exc

    @staticmethod
    def _inputs(
        *, workspace: Path, target: Path, patch_sha256: str, patch_size: int,
        receipt_sha256: str, receipt_size: int, receipt_id: str,
    ) -> None:
        if sys.platform != "linux" or os.name != "posix":
            raise FeatureExportError("feature-export.filesystem", "Core export pairs require Linux or WSL on a POSIX filesystem")
        if (not isinstance(workspace, Path) or not workspace.is_absolute()
                or not isinstance(target, Path) or not target.is_absolute()
                or target.name in {"", ".", ".."}
                or any(part in {".", ".."} for part in target.parts)
                or workspace == target or workspace in target.parents or target in workspace.parents):
            raise FeatureExportError("feature-export.path", "export target and workspace are invalid")
        if (type(patch_size) is not int or not 1 <= patch_size <= _MAX_PATCH
                or type(receipt_size) is not int or not 1 <= receipt_size <= _MAX_RECEIPT
                or patch_size + receipt_size > MAX_TOTAL_BYTES
                or type(patch_sha256) is not str or _SHA.fullmatch(patch_sha256) is None
                or type(receipt_sha256) is not str or _SHA.fullmatch(receipt_sha256) is None
                or type(receipt_id) is not str or _RECEIPT_ID.fullmatch(receipt_id) is None):
            raise FeatureExportError("feature-export.inputs", "export pair has invalid exact inputs")
        try:
            for path in (workspace, target.parent):
                descriptor = pinned_directory(path, create=False)
                os.close(descriptor)
        except (OSError, ValueError) as exc:
            raise FeatureExportError("feature-export.path", "export workspace or parent is unavailable or redirecting") from exc

    def verify(
        self, *, workspace: Path, target: Path, tree_id: str,
        patch_sha256: str, patch_size: int,
        receipt_sha256: str, receipt_size: int, receipt_id: str,
    ) -> None:
        self._inputs(
            workspace=workspace, target=target, patch_sha256=patch_sha256,
            patch_size=patch_size, receipt_sha256=receipt_sha256,
            receipt_size=receipt_size, receipt_id=receipt_id,
        )
        host = self._host(workspace)
        try:
            reference = host.describe(tree_id)
            if (reference.workspace != workspace or reference.owner_id != _OWNER
                    or reference.path != target
                    or reference.domain_id != f"feature-studio-export:{receipt_id}"
                    or reference.file_count != 2 or reference.directory_count != 0
                    or reference.total_bytes != patch_size + receipt_size):
                raise FeatureExportError("feature-export.changed", "export tree identity differs from its result")
            _read_pair(
                target, patch_sha256=patch_sha256, patch_size=patch_size,
                receipt_sha256=receipt_sha256, receipt_size=receipt_size,
            )
            if host.describe(tree_id) != reference:
                raise FeatureExportError("feature-export.changed", "export tree changed during reopening")
        except TransportTreeError as exc:
            raise FeatureExportError("feature-export.changed", "Core export tree cannot be reopened") from exc

    def publish(
        self, *, workspace: Path, target: Path, patch: bytes,
        receipt: bytes, receipt_id: str,
    ) -> str:
        if type(patch) is not bytes or type(receipt) is not bytes:
            raise FeatureExportError("feature-export.inputs", "export contents must be exact bytes")
        expected = {
            "workspace": workspace, "target": target,
            "patch_sha256": sha256(patch).hexdigest(), "patch_size": len(patch),
            "receipt_sha256": sha256(receipt).hexdigest(), "receipt_size": len(receipt),
            "receipt_id": receipt_id,
        }
        self._inputs(**expected)
        host = self._host(workspace)
        try:
            matches = [row for row in host.inventory() if row["path"] == target]
            if matches:
                if len(matches) != 1 or matches[0]["status"] not in {"committed", "prepared-incomplete"}:
                    raise FeatureExportError("feature-export.incomplete", "export target has an ambiguous earlier attempt")
                row = matches[0]
                tree_id = str(row["tree_id"])
                if row["status"] == "prepared-incomplete":
                    intent = host._intent(tree_id)
                    if intent["domain_id"] != f"feature-studio-export:{receipt_id}":
                        raise FeatureExportError("feature-export.changed", "prepared export belongs to another review")
                    payload = target if target.exists() or target.is_symlink() else Path(row["staging"])
                    _read_pair(
                        payload, patch_sha256=expected["patch_sha256"],
                        patch_size=expected["patch_size"],
                        receipt_sha256=expected["receipt_sha256"],
                        receipt_size=expected["receipt_size"],
                    )
                    host.reconcile(tree_id)
                self.verify(tree_id=tree_id, **expected)
                return tree_id
            if target.exists() or target.is_symlink():
                raise FeatureExportError("output.exists", "export destination already exists")
            with host.stage(target) as stage:
                stage.path.mkdir(mode=0o700)
                directory = pinned_directory(stage.path, create=False)
                try:
                    _write_member(directory, "feature.patch", patch)
                    _write_member(directory, "receipt.json", receipt)
                    os.fsync(directory)
                finally:
                    os.close(directory)
                reference = stage.publish(
                    validate=lambda path: _read_pair(
                        path, patch_sha256=expected["patch_sha256"],
                        patch_size=expected["patch_size"],
                        receipt_sha256=expected["receipt_sha256"],
                        receipt_size=expected["receipt_size"],
                    ),
                    domain_id=f"feature-studio-export:{receipt_id}",
                )
            self.verify(tree_id=reference.tree_id, **expected)
            return reference.tree_id
        except FeatureExportError:
            raise
        except TransportTreeError as exc:
            raise FeatureExportError(exc.code, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise FeatureExportError("feature-export.incomplete", "Core export attempt was interrupted") from exc


HOST = CoreFeatureExports()


__all__ = ["CoreFeatureExports", "HOST"]
