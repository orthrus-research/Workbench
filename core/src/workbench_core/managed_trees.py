"""Core staging, no-replace publication and custody for directory resources."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from datetime import datetime, timezone
import errno
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Iterator, Mapping
from uuid import uuid4

from workbench_api.managed_trees import (
    ManagedTreeError, ManagedTreeReference, ManagedTreeTarget,
)
from workbench_api.durable_resources import DurableResourceError, ResourceReference

from . import check_lifecycle, check_storage
from .durable_files import _directory as pinned_directory, read_verified
from .host_filesystem import fsync_directory, secure_private_path
from .output_routing import _WINDOWS_RESERVED
from .storage.registered import ResourceCatalog, _MAX_BYTES
from .storage.tree_catalog import (
    COMMIT_KIND, INTENT_KIND, DERIVED_INTENT_KIND, EXACT_INTENT_KIND, RESERVATION_KIND,
    _content_sha256, _store_id, inventory_members, atlas_manifest_baseline,
)
from .storage.exact_tree_inventory import (
    EXACT_INVENTORY_POLICY, exact_content_sha256, inventory_exact_members,
)


_OWNER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FILE_ID = re.compile(r"workbench-resource-v1:[0-9a-f]{32}\Z")
_TREE_ID = re.compile(r"workbench-tree-v1:[0-9a-f]{32}\Z")
_CHECK_ID = re.compile(r"workbench-check-v1:[0-9a-f]{64}\Z")
_TEMP_ID = re.compile(r"workbench-temporary-lease-v1:[0-9a-f]{32}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ensure_parent(path: Path) -> None:
    if os.name == "posix":
        descriptor = pinned_directory(path, create=True)
        os.close(descriptor)
    else:  # Windows directory publication uses native no-replace rename.
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_storage.ordinary(path, directory=True)


def _sync_members(root: Path, members: list[dict[str, object]]) -> None:
    """Flush staged payload bytes before recording a recoverable publication intent."""
    directories = [root]
    for row in members:
        selected = root / str(row["path"])
        if row["kind"] == "directory":
            directories.append(selected)
            continue
        descriptor = os.open(
            selected, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ManagedTreeError("tree.changed", "managed tree member changed before flush")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)


def _rename_no_replace(
    payload: Path, target: Path, *, parent_identity: tuple[int, int],
    payload_identity: tuple[int, int],
) -> None:
    """Use the destination filesystem's atomic no-replace directory operation."""
    check_storage.ordinary(payload, directory=True)
    check_storage.ordinary(target.parent, directory=True)
    visible_parent = target.parent.stat()
    if (visible_parent.st_dev, visible_parent.st_ino) != parent_identity:
        raise ManagedTreeError("output.changed", "managed tree destination parent changed")
    visible_payload = payload.stat()
    if (visible_payload.st_dev, visible_payload.st_ino) != payload_identity:
        raise ManagedTreeError("tree.changed", "managed tree payload changed identity")
    if os.name == "nt":  # pragma: no cover - exercised on Windows qualification
        try:
            os.rename(payload, target)
        except FileExistsError as exc:
            raise ManagedTreeError("output.exists", "managed tree destination already exists") from exc
        except OSError as exc:
            raise ManagedTreeError("output.filesystem", f"managed tree directory publication failed: {exc}") from exc
        return
    if not sys.platform.startswith("linux"):
        raise ManagedTreeError("output.filesystem", "atomic no-replace directory publication is unavailable on this host")
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ManagedTreeError("output.filesystem", "this Linux host lacks renameat2 directory publication")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    try:
        source_fd = pinned_directory(payload.parent, create=False)
    except (OSError, ValueError) as exc:
        raise ManagedTreeError("tree.changed", "managed tree staging directory changed") from exc
    try:
        try:
            target_fd = pinned_directory(target.parent, create=False)
        except (OSError, ValueError) as exc:
            raise ManagedTreeError("output.changed", "managed tree destination parent changed") from exc
        try:
            parent = os.fstat(target_fd)
            if (parent.st_dev, parent.st_ino) != parent_identity:
                raise ManagedTreeError("output.changed", "managed tree destination parent changed")
            staged = os.stat(payload.name, dir_fd=source_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(staged.st_mode)
                    or (staged.st_dev, staged.st_ino) != payload_identity):
                raise ManagedTreeError("tree.changed", "managed tree payload changed identity")
            if rename(source_fd, os.fsencode(payload.name), target_fd, os.fsencode(target.name), 1):
                code = ctypes.get_errno()
                if code in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise ManagedTreeError("output.exists", "managed tree destination already exists")
                if code in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}:
                    raise ManagedTreeError("output.filesystem", "the selected filesystem lacks atomic no-replace directory publication")
                raise ManagedTreeError("output.write", f"managed tree publication failed: {os.strerror(code)}")
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)


class _CoreTreeStage:
    def __init__(self, host: CoreManagedTrees, *, tree_id: str, target: Path, store_root: Path,
                 staging_root: Path, reservation: dict):
        self.host = host
        self.tree_id = tree_id
        self.path = staging_root / "payload"
        self.target = target
        self.store_root = store_root
        self.staging_root = staging_root
        self.reservation = reservation
        self.renamed = False
        self.committed = False

    def publish(
        self, *, validate: Callable[[Path], object], domain_id: str | None = None,
        references: tuple[str, ...] = (), derived_members: tuple[str, ...] = (),
        derived_manifest_rule: str | None = None,
        inventory_policy: str = "portable-v1",
    ) -> ManagedTreeReference:
        if self.committed or self.renamed:
            raise ManagedTreeError("tree.state", "managed tree publication was already attempted")
        if not callable(validate):
            raise ManagedTreeError("tree.validator", "managed tree publication requires an owner validator")
        if domain_id is not None and (type(domain_id) is not str or not 0 < len(domain_id) <= 512):
            raise ManagedTreeError("tree.domain", "managed tree domain identity is invalid")
        if derived_manifest_rule is not None and (
            derived_manifest_rule != "atlas-categorical-query-index-v1"
            or self.host.owner_id != "atlas"
            or domain_id is None
            or "query-index.sqlite3" not in derived_members
        ):
            raise ManagedTreeError("tree.policy", "derived manifest rule is unsupported")
        if type(inventory_policy) is not str or inventory_policy not in {
            "portable-v1", EXACT_INVENTORY_POLICY,
        }:
            raise ManagedTreeError("tree.policy", "managed tree inventory policy is unsupported")
        if inventory_policy == EXACT_INVENTORY_POLICY and (
            type(derived_members) is not tuple or derived_members or derived_manifest_rule
        ):
            raise ManagedTreeError("tree.policy", "exact POSIX inventory does not support derived members")
        self.host.check_cancelled()
        check_storage.ordinary(self.path, directory=True)
        with self.host._references(references):
            if inventory_policy == EXACT_INVENTORY_POLICY:
                def inventory():
                    return inventory_exact_members(self.path, cancelled=self.host._cancelled)
            else:
                def inventory():
                    return inventory_members(self.path, derived_members=derived_members,
                                             cancelled=self.host._cancelled), None, None, None
            before, root_mode, files, directories = inventory()
            validate(self.path)
            self.host.check_cancelled()
            after, after_mode, after_files, after_directories = inventory()
            if (after, after_mode, after_files, after_directories) != (
                before, root_mode, files, directories,
            ):
                raise ManagedTreeError("tree.changed", "managed tree changed during owner validation")
            _sync_members(self.path, after)
            if inventory() != (after, after_mode, after_files, after_directories):
                raise ManagedTreeError("tree.changed", "managed tree changed while flushing members")
            # Source-side anchors precede the tree intent. An abrupt exit in
            # between may overretain a check, but cannot orphan published proof.
            self.host._record_check_consumers(references, self.tree_id)
            selected = check_storage.ordinary(self.path, directory=True)
            info = selected.stat()
            nonce = self.tree_id.rsplit(":", 1)[-1]
            intent_kind = (EXACT_INTENT_KIND if inventory_policy == EXACT_INVENTORY_POLICY
                           else DERIVED_INTENT_KIND if derived_manifest_rule else INTENT_KIND)
            intent_body = {
                "format": intent_kind, "tree_id": self.tree_id,
                "reservation_id": self.reservation["id"],
                "store_id": self.reservation["store_id"],
                "store_root": self.reservation["store_root"],
                "relative_path": self.reservation["relative_path"],
                "workspace": self.reservation["workspace"],
                "owner_id": self.reservation["owner_id"],
                "role": self.reservation["role"],
                "role_source": self.reservation["role_source"],
                "policy_id": self.reservation["policy_id"],
                "parent_device": self.reservation["parent_device"],
                "parent_inode": self.reservation["parent_inode"],
                "staging": self.reservation["staging"],
                "device": info.st_dev, "inode": info.st_ino,
                "content_sha256": (exact_content_sha256(before) if intent_kind == EXACT_INTENT_KIND
                                   else _content_sha256(before)),
                "domain_id": domain_id, "references": list(references),
                "prepared_at": _now(),
            }
            if intent_kind == EXACT_INTENT_KIND:
                intent_body.update({
                    "inventory_policy": EXACT_INVENTORY_POLICY,
                    "member_count": len(before), "file_count": files,
                    "directory_count": directories, "root_mode": root_mode,
                })
            else:
                intent_body["members"] = before
            if derived_manifest_rule is not None:
                intent_body["derived_manifest_rule"] = derived_manifest_rule
                intent_body["derived_manifest_base_sha256"] = atlas_manifest_baseline(
                    self.path, members=before, domain_id=domain_id,
                )
            intent = self.host.catalog.trees._write("intents", nonce, intent_kind, intent_body)
            self.host.check_cancelled()
            parent_before = check_storage.ordinary(self.target.parent, directory=True).stat()
            expected_parent = (self.reservation["parent_device"], self.reservation["parent_inode"])
            if (parent_before.st_dev, parent_before.st_ino) != expected_parent:
                raise ManagedTreeError("output.changed", "managed tree destination parent changed")
            _rename_no_replace(
                self.path, self.target, parent_identity=expected_parent,
                payload_identity=(info.st_dev, info.st_ino),
            )
            self.renamed = True
            parent_after = check_storage.ordinary(self.target.parent, directory=True).stat()
            if (parent_before.st_dev, parent_before.st_ino) != (parent_after.st_dev, parent_after.st_ino):
                raise ManagedTreeError("output.changed", "managed tree destination parent changed during publication")
            fsync_directory(self.target.parent)
            derived_status = self.host.catalog.trees._verify(intent)
            self.host.catalog.trees._write("commits", nonce, COMMIT_KIND, {
                "format": COMMIT_KIND, "tree_id": self.tree_id,
                "intent_id": intent["id"], "committed_at": _now(),
            })
            self.committed = True
            try:
                self.staging_root.rmdir()
            except OSError:
                pass
            return self.host.catalog.trees._reference(
                intent, derived_status=derived_status,
                members=before if intent_kind == EXACT_INTENT_KIND else None,
            )


class CoreManagedTrees:
    def __init__(
        self, *, workspace: Path, configuration_home: Path,
        locations: Mapping[str, Path], owner_id: str,
        policy_id: str | None = None, location_sources: Mapping[str, str] | None = None,
        check_cancelled=lambda: None,
    ):
        if not workspace.is_absolute() or not configuration_home.is_absolute():
            raise ManagedTreeError("tree.policy", "managed tree context requires absolute roots")
        if not isinstance(owner_id, str) or _OWNER.fullmatch(owner_id) is None:
            raise ManagedTreeError("tree.policy", "managed tree owner is invalid")
        self.workspace = workspace
        self.configuration_home = configuration_home
        self.locations = dict(locations)
        self.owner_id = owner_id
        self.policy_id = policy_id
        self.max_file_reference_bytes = _MAX_BYTES
        self.location_sources = dict(location_sources or {})
        self.check_cancelled = check_cancelled
        self.catalog = ResourceCatalog(configuration_home)

    def _resource_host(self):
        from .storage.registered import CoreDurableResources

        return CoreDurableResources(
            workspace=self.workspace,
            configuration_home=self.configuration_home,
            locations=self.locations,
            owner_id=self.owner_id,
            policy_id=self.policy_id,
            location_sources=self.location_sources,
            check_cancelled=self.check_cancelled,
        )

    def retain_file_reference(
        self, role: str, name: str, source: Path, *,
        sha256: str, size: int, domain_id: str | None = None,
    ) -> ResourceReference:
        """Snapshot an exact external input into the existing resource catalog."""

        if (not isinstance(source, Path) or not source.is_absolute()
                or type(sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
                or type(size) is not int or not 0 <= size <= self.max_file_reference_bytes):
            raise ManagedTreeError("tree.references", "dependency file identity is invalid")
        try:
            data = read_verified(source, expected_size=size, expected_sha256=sha256)
            return self._resource_host().publish_bytes(
                role, name, data, domain_id=domain_id,
            )
        except (DurableResourceError, OSError) as exc:
            raise ManagedTreeError("tree.references", "dependency file cannot be retained exactly") from exc

    def retain_bytes_reference(
        self, role: str, name: str, data: bytes, *,
        references: tuple[str, ...] = (), domain_id: str | None = None,
    ) -> ResourceReference:
        """Publish owner-defined dependency evidence through the resource catalog."""

        try:
            return self._resource_host().publish_bytes(
                role, name, data, references=references, domain_id=domain_id,
            )
        except (DurableResourceError, OSError) as exc:
            raise ManagedTreeError("tree.references", "dependency evidence cannot be retained") from exc

    def read_file_reference(
        self, resource_id: str,
    ) -> tuple[ResourceReference, bytes]:
        """Reopen one exact Core-issued file resource under this owner binding."""

        try:
            resource_host = self._resource_host()
            reference = resource_host.describe(resource_id)
            if reference.owner_id != self.owner_id:
                raise ManagedTreeError(
                    "tree.references", "dependency file belongs to another owner",
                )
            data = resource_host.read_bytes(resource_id)
        except (DurableResourceError, OSError) as exc:
            raise ManagedTreeError(
                "tree.references", "dependency file cannot be reopened exactly",
            ) from exc
        if (len(data) != reference.bytes
                or "sha256:" + sha256(data).hexdigest() != reference.sha256):
            raise ManagedTreeError("tree.references", "dependency file changed during reopen")
        return reference, data

    def _cancelled(self) -> bool:
        try:
            self.check_cancelled()
        except Exception:
            return True
        return False

    def _destination(self, role: str, name: str, requested_path: Path | None,
                     nonce: str) -> tuple[Path, Path, str]:
        if role not in {"evidence", "artifacts"} or role not in self.locations:
            raise ManagedTreeError("output.role", "unsupported managed tree role")
        if (type(name) is not str or _NAME.fullmatch(name) is None
                or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
            raise ManagedTreeError("output.path", "managed tree name must be one portable filename")
        selected_root = Path(self.locations[role])
        if not selected_root.is_absolute():
            raise ManagedTreeError("tree.policy", "managed tree role root must be absolute")
        if requested_path is None:
            target = selected_root / "outputs" / self.owner_id / f"{nonce}-{name}"
            return target, selected_root, self.location_sources.get(role, "context")
        if not isinstance(requested_path, Path) or requested_path.name != name:
            raise ManagedTreeError("output.path", "managed tree output path is invalid")
        if any(part in {".", ".."} for part in requested_path.parts):
            raise ManagedTreeError("output.path", "managed tree output path is unsafe")
        target = requested_path if requested_path.is_absolute() else self.workspace / requested_path
        if target.is_relative_to(selected_root):
            return target, selected_root, self.location_sources.get(role, "context")
        if target.is_relative_to(self.workspace):
            return target, self.workspace, "explicit-workspace"
        # An explicit absolute destination is user intent. Core catalogs only
        # that exact tree; the parent and unrelated siblings remain outside custody.
        if requested_path.is_absolute():
            return target, target.parent, "explicit-path"
        raise ManagedTreeError("output.path", "managed tree output is outside the selected workspace")

    @contextmanager
    def _references(self, references: tuple[str, ...]) -> Iterator[None]:
        if (not isinstance(references, tuple) or len(references) > 128
                or any(not isinstance(value, str) or not (_FILE_ID.fullmatch(value) or _TREE_ID.fullmatch(value)
                                                       or _CHECK_ID.fullmatch(value) or _TEMP_ID.fullmatch(value))
                       for value in references)
                or len(set(references)) != len(references)):
            raise ManagedTreeError("tree.references", "managed tree references must be unique Core dependencies")
        with ExitStack() as stack:
            held_resources: set[str] = set()

            def hold_resource(resource_id: str) -> None:
                if resource_id in held_resources:
                    return
                held_resources.add(resource_id)
                stack.enter_context(self.catalog.lease(resource_id))
                intent = self.catalog._intent(resource_id)
                self.catalog._commit(resource_id, intent)
                if intent["workspace"] != str(self.workspace):
                    raise ManagedTreeError("tree.references", "referenced resource belongs to another workspace")
                read_verified(
                    self.catalog._target(intent), expected_size=int(intent["bytes"]),
                    expected_sha256=str(intent["sha256"]),
                )
                for child in intent["references"]:
                    hold_resource(str(child))

            for reference in sorted(references):
                if _FILE_ID.fullmatch(reference):
                    hold_resource(reference)
                elif _TREE_ID.fullmatch(reference):
                    stack.enter_context(self.catalog.trees.lease(reference))
                    intent = self.catalog.trees.intent(reference)
                    self.catalog.trees.commit(reference, intent)
                    if intent["workspace"] != str(self.workspace):
                        raise ManagedTreeError("tree.references", "referenced tree belongs to another workspace")
                    self.catalog.trees._verify(intent)
                elif _TEMP_ID.fullmatch(reference):
                    from .temporary_leases import CoreTemporaryLeases, TemporaryLeaseError

                    try:
                        stack.enter_context(CoreTemporaryLeases.reference_lease(
                            self.configuration_home, reference,
                            workspace=self.workspace, owner_id=self.owner_id,
                        ))
                    except TemporaryLeaseError as exc:
                        raise ManagedTreeError(
                            "tree.references", "referenced temporary lease is unavailable or changed",
                        ) from exc
                else:
                    stack.enter_context(check_lifecycle.lease(self.workspace))
                    try:
                        check_lifecycle.resolve_tree_reference(self.workspace, reference)
                    except (ValueError, OSError, RuntimeError) as exc:
                        raise ManagedTreeError("tree.references", str(exc)) from exc
            yield

    def _record_check_consumers(self, references: tuple[str, ...], tree_id: str) -> None:
        for reference in references:
            if _CHECK_ID.fullmatch(reference):
                try:
                    check_lifecycle.register_tree_consumer(
                        self.workspace, reference, tree_id, self.catalog.root,
                    )
                except (ValueError, OSError, RuntimeError) as exc:
                    raise ManagedTreeError("tree.references", str(exc)) from exc

    @contextmanager
    def stage(self, role: str, name: str, *, requested_path: Path | None = None) -> Iterator[_CoreTreeStage]:
        nonce = uuid4().hex
        tree_id = f"workbench-tree-v1:{nonce}"
        target, store_root, source = self._destination(role, name, requested_path, nonce)
        self.check_cancelled()
        _ensure_parent(target.parent)
        if target.exists() or target.is_symlink():
            raise ManagedTreeError("output.exists", "managed tree destination already exists")
        parent = check_storage.ordinary(target.parent, directory=True).stat()
        self.catalog._ensure()
        self.catalog.trees.ensure()
        with self.catalog.trees.lease(tree_id, exclusive=True, create=True):
            reservation = self.catalog.trees.reserve({
                "format": RESERVATION_KIND, "tree_id": tree_id,
                "store_id": _store_id(store_root), "store_root": str(store_root),
                "relative_path": target.relative_to(store_root).as_posix(),
                "workspace": str(self.workspace), "owner_id": self.owner_id,
                "role": role, "role_source": source, "policy_id": self.policy_id,
                "parent_device": parent.st_dev, "parent_inode": parent.st_ino,
                "staging": f".workbench-tree-{nonce}.pending", "allocated_at": _now(),
            })
            staging_root = target.parent / reservation["staging"]
            stage = _CoreTreeStage(
                self, tree_id=tree_id, target=target, store_root=store_root,
                staging_root=staging_root, reservation=reservation,
            )
            try:
                staging_root.mkdir(mode=0o700)
                secure_private_path(staging_root, directory=True)
                yield stage
            except BaseException as exc:
                if not stage.renamed:
                    self.catalog.trees.abort(tree_id, type(exc).__name__)
                raise
            else:
                if not stage.committed and not stage.renamed:
                    self.catalog.trees.abort(tree_id, "unpublished")

    def describe(self, tree_id: str) -> ManagedTreeReference:
        return self.catalog.trees.describe(tree_id, workspace=self.workspace)

    def lookup_target(
        self, role: str, path: Path, *, domain_id: str | None = None,
    ) -> ManagedTreeTarget:
        """Find one exact owned target, including an interrupted reservation.

        A path is only a lookup key here. The returned tree ID is still subject
        to the catalog's exact intent/commit checks during reconciliation.
        """

        if (type(role) is not str or role not in {"evidence", "artifacts"}
                or role not in self.locations
                or not isinstance(path, Path) or not path.is_absolute()
                or any(part in {".", ".."} for part in path.parts)
                or _NAME.fullmatch(path.name) is None
                or path.name.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
            raise ManagedTreeError("tree.path", "managed tree target lookup requires one exact absolute path")
        if domain_id is not None and (type(domain_id) is not str or not 0 < len(domain_id) <= 512):
            raise ManagedTreeError("tree.domain", "managed tree target domain identity is invalid")
        for component in (path, *path.parents):
            if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
                raise ManagedTreeError("tree.path", "managed tree target traverses a redirect")
        rows = [row for row in self.catalog.trees.inventory() if row["path"] == str(path)]
        if not rows:
            raise ManagedTreeError("tree.unavailable", "managed tree target has no catalog record")
        if len(rows) != 1:
            raise ManagedTreeError("tree.ambiguous", "managed tree target has multiple catalog records")
        row = rows[0]
        if (row["workspace"] != str(self.workspace) or row["owner_id"] != self.owner_id
                or row["role"] != role):
            raise ManagedTreeError("tree.foreign", "managed tree target belongs to another binding")
        if row["status"] in {"conflict", "unavailable", "changed"}:
            raise ManagedTreeError("tree.changed", "managed tree target cannot be reopened exactly")
        tree_id = str(row["tree_id"])
        try:
            intent = self.catalog.trees.intent(tree_id)
        except ManagedTreeError as exc:
            if exc.code != "tree.unavailable":
                raise
            if domain_id is not None:
                raise ManagedTreeError(
                    "tree.unavailable", "managed tree target has no bound domain identity",
                ) from exc
            selected_domain = None
        else:
            selected_domain = intent["domain_id"]
            if domain_id is not None and selected_domain != domain_id:
                raise ManagedTreeError("tree.domain", "managed tree target has another domain identity")
        return ManagedTreeTarget(
            tree_id=tree_id, status=str(row["status"]), path=path,
            domain_id=selected_domain,
        )

    def reconcile(self, tree_id: str) -> ManagedTreeReference:
        def publish(staged: Path, target: Path, intent: Mapping[str, object]) -> None:
            self.check_cancelled()
            _rename_no_replace(
                staged, target,
                parent_identity=(intent["parent_device"], intent["parent_inode"]),
                payload_identity=(intent["device"], intent["inode"]),
            )
            fsync_directory(target.parent)

        intent = self.catalog.trees.intent(tree_id)
        with self._references(tuple(intent["references"])):
            return self.catalog.trees.reconcile(
                tree_id, workspace=self.workspace, publish_prepared=publish,
            )


__all__ = ["CoreManagedTrees"]
