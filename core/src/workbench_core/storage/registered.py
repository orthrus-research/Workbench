"""Core resource catalog for exact immutable outputs in selected stores.

Intent records precede publication. A target linked before its commit record is
recoverable because its prepared temporary file retains the same inode. The
catalog indexes custody and dependencies; it never copies the owner's payload.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterator, Mapping
import unicodedata
from uuid import uuid4

from workbench_api.durable_resources import DurableResourceError, ResourceReference
from workbench_api.host_filesystem import DurableRecordError
from workbench_api.managed_trees import ManagedTreeError

from .. import check_lifecycle, check_storage
from ..durable_files import StagedFile, read_verified, unlink_prepared
from ..durable_records import (
    private_record_lock, publish_immutable_bytes, read_private_bytes,
    read_private_single_link_bytes,
)
from ..host_filesystem import file_lease, private_path, secure_private_path
from ..output_routing import _WINDOWS_RESERVED, _private_directory
from . import issuance


CATALOG_FORMAT = "workbench-resource-catalog-v1"
ROOT_FORMAT = "workbench-resource-catalog-root-v1"
ROOT_V2_FORMAT = "workbench-resource-catalog-root-v2"
ROOT_MANIFEST_NAME = "resource-catalog-root-v1.json"
ROOT_ANCHOR_NAME = ".resource-catalog-root-v1.json"
ROOT_LOCK_NAME = ".resource-catalog-root-v1.lock"
_ROOT_REQUIRED = ("reservations", "intents", "commits", "aborts", "leases", "stores")
_ROOT_KNOWN = (
    *_ROOT_REQUIRED, "trees", "working-allocations", "temporary-leases",
    "transport-trees", "reusable-projections",
)
# This preexisting lock-only directory was introduced after V1 root manifests.
# Keep those sealed manifest bytes stable while inventorying the auxiliary root.
_ROOT_AUXILIARY = ("registration-attempt-leases",)
RECORD_STORE_KIND = "workbench-record-store-v1"
RECORD_STORE_FORWARD_KIND = "workbench-record-store-forward-witness-v1"
RESERVATION_KIND = "workbench-resource-reservation-v1"
INTENT_KIND = "workbench-resource-intent-v1"
COMMIT_KIND = "workbench-resource-commit-v1"
ABORT_KIND = "workbench-resource-abort-v1"
_RESOURCE = re.compile(r"workbench-resource-v1:([0-9a-f]{32})\Z")
_CATALOG_LEASE = re.compile(r"(?:[0-9a-f]{32}\.lock|[0-9a-f]{64}\.record-store\.lock)\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_OWNER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_MAX_BYTES = 32 * 1024 * 1024


def _explicit_output_filename(name: str) -> bool:
    """Admit a bounded user-selected basename without changing default names."""

    if type(name) is not str or name in {"", ".", ".."} or name.endswith((" ", ".")):
        return False
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (
        1 <= len(encoded) <= 255
        and name.split(".", 1)[0].rstrip(" .").upper() not in _WINDOWS_RESERVED
        and all(
            char not in '/\\<>:"|?*'
            and unicodedata.category(char) not in {"Cc", "Cf"}
            for char in name
        )
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _resource_nonce(resource_id: str) -> str:
    match = _RESOURCE.fullmatch(resource_id)
    if match is None:
        raise DurableResourceError("resource.id", "invalid durable resource identity")
    return match.group(1)


def _store_id(root: Path) -> str:
    return "workbench-resource-store:sha256:" + sha256(os.fsencode(root)).hexdigest()


def _policy_id(workspace: Path, locations: Mapping[str, Path]) -> str:
    value = {"workspace": str(workspace), "locations": {key: str(value) for key, value in sorted(locations.items())}}
    return "workbench-resource-policy:sha256:" + sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sealed(kind: str, body: Mapping[str, object]) -> dict:
    return check_storage.seal(kind, dict(body))


def _read_sealed(path: Path, kind: str) -> dict:
    try:
        record = check_storage.read_json(path, byte_limit=1024 * 1024)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DurableResourceError("resource.unavailable", f"resource catalog record is unavailable: {path}") from exc
    if not isinstance(record, dict) or record != _sealed(kind, {key: value for key, value in record.items() if key != "id"}):
        raise DurableResourceError("resource.changed", f"resource catalog record changed: {path}")
    return record


class ResourceCatalog:
    def __init__(self, configuration_home: Path):
        if not configuration_home.is_absolute():
            raise DurableResourceError("resource.policy", "configuration home must be absolute")
        self.configuration_home = configuration_home
        self.root = configuration_home / "resources-v1"

    def _root_manifest(self) -> Path:
        return self.configuration_home / ROOT_MANIFEST_NAME

    def _root_anchor(self) -> Path:
        return self.root / ROOT_ANCHOR_NAME

    def _check_root_directories(self) -> os.stat_result:
        try:
            root_info = check_storage.ordinary(self.root, directory=True).stat()
            if not private_path(self.root, directory=True):
                raise ValueError("resource catalog root is not owner-private")
            for name in _ROOT_REQUIRED:
                path = self._directory(name)
                check_storage.ordinary(path, directory=True)
                if not private_path(path, directory=True):
                    raise ValueError("resource catalog namespace is not owner-private")
            for name in _ROOT_KNOWN[len(_ROOT_REQUIRED):]:
                path = self._directory(name)
                if path.exists() or path.is_symlink():
                    check_storage.ordinary(path, directory=True)
                    if not private_path(path, directory=True):
                        raise ValueError("resource catalog namespace is not owner-private")
            for name in _ROOT_AUXILIARY:
                path = self._directory(name)
                if path.exists() or path.is_symlink():
                    check_storage.ordinary(path, directory=True)
                    if not private_path(path, directory=True):
                        raise ValueError("resource catalog auxiliary lease root is not owner-private")
        except (OSError, ValueError) as exc:
            raise DurableResourceError("resource.changed", "resource catalog root or namespace is unavailable") from exc
        return root_info

    def _root_record(self, origin: str, root_info: os.stat_result, *, epoch: str | None = None) -> dict:
        kind = ROOT_V2_FORMAT if epoch is not None else ROOT_FORMAT
        body = {
            "format": kind, "schema_version": 2 if epoch is not None else 1,
            "catalog_format": CATALOG_FORMAT, "generation": 1,
            "migration_origin": origin,
            "configuration_home": str(self.configuration_home),
            "root": str(self.root),
            "root_device": root_info.st_dev, "root_inode": root_info.st_ino,
            "required_namespaces": list(_ROOT_REQUIRED),
            "known_namespaces": list(_ROOT_KNOWN),
        }
        if epoch is not None:
            body["root_epoch"] = epoch
        return _sealed(kind, body)

    def _root_identity(self, record: object, root_info: os.stat_result) -> bool:
        if not isinstance(record, dict):
            return False
        origin = record.get("migration_origin")
        if origin not in {"empty-home-first-use", "legacy-v1", "unproven-first-use"}:
            return False
        if any(type(record.get(key)) is not int for key in (
            "schema_version", "generation", "root_device", "root_inode",
        )):
            return False
        kind = record.get("format")
        epoch = record.get("root_epoch") if kind == ROOT_V2_FORMAT else None
        if kind == ROOT_V2_FORMAT:
            if origin == "legacy-v1" or not isinstance(epoch, str) or re.fullmatch(r"[0-9a-f]{32}", epoch) is None:
                return False
        elif kind != ROOT_FORMAT:
            return False
        return record == self._root_record(origin, root_info, epoch=epoch)

    def fresh_root_epoch(self) -> dict | None:
        """Return an exact new-root epoch; V1 and historical roots have none."""
        if self.verify_root() != "ready-unproven":
            return None
        outer = read_private_single_link_bytes(self._root_manifest(), byte_limit=4096)
        inner = read_private_single_link_bytes(self._root_anchor(), byte_limit=4096)
        record = json.loads(outer)
        if (outer != inner or not self._root_identity(record, self._check_root_directories())
                or outer != check_storage.canonical(record) + b"\n"):
            raise DurableResourceError("resource.changed", "fresh root epoch changed")
        return record if record["format"] == ROOT_V2_FORMAT else None

    def inspect_post_birth_issue_gap(self, *, workspace: Path) -> dict[str, object]:
        """Read the exact V2 mirror state; this does not repair or cover output."""
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise DurableResourceError("resource.policy", "select an absolute issuance workspace")
        root_record = self.fresh_root_epoch()
        if root_record is None:
            raise DurableResourceError("resource.unsupported", "issuance inspection requires a fresh V2 root")
        return issuance.inspect_gap(self, root_record, workspace)

    def inspect_workspace_issuance_epoch(self, *, workspace: Path) -> dict[str, object]:
        """Read retained workspace epoch evidence, including after home loss."""
        return issuance.inspect_workspace_epoch(self, workspace)

    def inspect_current_file_issuance(self, *, workspace: Path) -> dict[str, object]:
        """Join current V2 file rows to issues without asserting complete history."""
        if not isinstance(workspace, Path) or not workspace.is_absolute():
            raise DurableResourceError("resource.policy", "select an absolute issuance workspace")
        root_record = self.fresh_root_epoch()
        if root_record is None:
            raise DurableResourceError("resource.unsupported", "file issuance inspection requires a V2 root")
        return issuance.inspect_current_resource_join(self, root_record, workspace)

    def post_birth_coverage(
        self, resource_id: str, *, workspace: Path, owner_id: str, target: Path,
    ) -> str:
        """Verify one issued file's exact current custody without promoting cleanup."""
        _resource_nonce(resource_id)
        if (not isinstance(workspace, Path) or not workspace.is_absolute()
                or not isinstance(target, Path) or not target.is_absolute()
                or not isinstance(owner_id, str) or _OWNER.fullmatch(owner_id) is None):
            raise DurableResourceError("resource.policy", "select an exact post-birth candidate")
        root_record = self.fresh_root_epoch()
        if root_record is None:
            return "unproven"
        with issuance.verified_candidate(
            self, root_record, workspace=workspace, resource_id=resource_id,
            owner_id=owner_id, target=target,
        ) as row:
            if row is None:
                return "unproven"
            with self.lease(resource_id):
                intent = self._intent(resource_id)
                self._commit(resource_id, intent)
                if (intent["workspace"] != str(workspace) or intent["owner_id"] != owner_id
                        or self._target(intent) != target or intent["reservation_id"] != row["reservation_id"]):
                    raise DurableResourceError("resource.changed", "post-birth candidate differs from issued output")
                try:
                    info = target.lstat()
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or (info.st_dev, info.st_ino) != (intent["device"], intent["inode"])):
                        raise DurableResourceError("resource.changed", "post-birth output identity changed")
                    read_verified(target, expected_size=int(intent["bytes"]), expected_sha256=str(intent["sha256"]))
                    after = target.lstat()
                    if (after.st_dev, after.st_ino) != (intent["device"], intent["inode"]):
                        raise DurableResourceError("resource.changed", "post-birth output changed during verification")
                except OSError as exc:
                    raise DurableResourceError("resource.changed", "post-birth output is unavailable or changed") from exc
            if self.fresh_root_epoch() != root_record:
                raise DurableResourceError("resource.changed", "post-birth root epoch changed during verification")
        return "post-birth-covered"

    def verify_root(self) -> str:
        """Read-only root check; no local state proves historical completeness.

        A preexisting home without a catalog is unproven: new publication can
        proceed, but cleanup cannot infer that older resources never existed.
        """
        home = self.configuration_home
        if not home.exists() and not home.is_symlink():
            return "unproven-empty-home"
        try:
            check_storage.ordinary(home, directory=True)
            manifest = self._root_manifest()
            anchor = self._root_anchor()
            manifest_exists = manifest.exists() or manifest.is_symlink()
            root_exists = self.root.exists() or self.root.is_symlink()
            anchor_exists = anchor.exists() or anchor.is_symlink()
            if not manifest_exists and not root_exists:
                if not any(home.iterdir()):
                    return "unproven-empty-home"
                return "unproven"
            if not root_exists:
                raise DurableResourceError("resource.unavailable", "resource catalog root is missing")
            root_info = self._check_root_directories()
            if not manifest_exists and not anchor_exists:
                return "legacy"
            if not manifest_exists or not anchor_exists:
                raise DurableResourceError("resource.unavailable", "resource catalog root binding is incomplete")
            if not private_path(home, directory=True):
                raise DurableResourceError("resource.changed", "resource catalog configuration home is not owner-private")
            outer = read_private_single_link_bytes(manifest, byte_limit=4096)
            inner = read_private_single_link_bytes(anchor, byte_limit=4096)
            if outer != inner:
                raise DurableResourceError("resource.changed", "resource catalog root bindings differ")
            record = json.loads(outer)
            if not self._root_identity(record, root_info):
                raise DurableResourceError("resource.changed", "resource catalog root manifest changed")
            if outer != check_storage.canonical(record) + b"\n":
                raise DurableResourceError("resource.changed", "resource catalog root manifest is not canonical")
            return "ready-unproven"
        except DurableResourceError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise DurableResourceError("resource.changed", "resource catalog root cannot be verified") from exc

    def reconcile_interrupted_root_publication(self) -> str:
        """Finish the one known root-publication gap without adopting lost history.

        ``_ensure`` publishes the inner anchor first. If publication stops
        before the outer manifest, the sealed anchor still binds the original
        root inode. No other missing-binding shape has that crash ordering.
        This restores publication access; historical coverage stays unproven.
        """

        home = self.configuration_home
        try:
            if not home.exists() and not home.is_symlink():
                raise DurableResourceError("resource.unavailable", "resource catalog has no surviving root anchor")
            check_storage.ordinary(home, directory=True)
            if not private_path(home, directory=True):
                raise ValueError("resource catalog configuration home is not owner-private")
            with private_record_lock(home / ROOT_LOCK_NAME, wait=True):
                manifest = self._root_manifest()
                anchor = self._root_anchor()
                if manifest.exists() or manifest.is_symlink():
                    if self.verify_root() == "ready-unproven":
                        return "ready-unproven"
                    raise DurableResourceError("resource.unavailable", "resource catalog root has no interrupted publication")
                if not anchor.exists() and not anchor.is_symlink():
                    raise DurableResourceError("resource.unavailable", "resource catalog root has no surviving anchor")
                root_info = self._check_root_directories()
                raw = read_private_single_link_bytes(anchor, byte_limit=4096)
                record = json.loads(raw)
                if not self._root_identity(record, root_info) or raw != check_storage.canonical(record) + b"\n":
                    raise DurableResourceError("resource.changed", "resource catalog anchor changed")
                publish_immutable_bytes(manifest, raw, byte_limit=4096)
                if self.verify_root() != "ready-unproven":
                    raise DurableResourceError("resource.changed", "resource catalog root reconciliation failed")
                return "ready-unproven"
        except DurableResourceError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise DurableResourceError("resource.changed", "resource catalog root cannot be reconciled") from exc

    def _directory(self, name: str) -> Path:
        return self.root / name

    def _path(self, name: str, nonce: str) -> Path:
        return self._directory(name) / f"{nonce}.json"

    @property
    def trees(self):
        """Directory resources share this catalog root and workspace scope."""
        from .tree_catalog import TreeCatalog
        self.verify_root()
        return TreeCatalog(self.root)

    def _ensure(self, *, fresh_epoch: bool = False) -> None:
        initial_state = self.verify_root()
        _private_directory(self.configuration_home)
        secure_private_path(self.configuration_home, directory=True)
        with private_record_lock(self.configuration_home / ROOT_LOCK_NAME, wait=True):
            # The lock itself is the only new home entry during first install.
            if initial_state in {"unproven-empty-home", "unproven"} and not self.root.exists() and not self.root.is_symlink():
                if initial_state == "unproven-empty-home" and {path.name for path in self.configuration_home.iterdir()} != {ROOT_LOCK_NAME}:
                    raise DurableResourceError("resource.unavailable", "resource catalog first install changed")
                for path in (self.root, *(self._directory(name) for name in _ROOT_REQUIRED)):
                    _private_directory(path)
                    secure_private_path(path, directory=True)
                origin = "empty-home-first-use" if initial_state == "unproven-empty-home" else "unproven-first-use"
            else:
                state = self.verify_root()
                if state == "ready-unproven":
                    return
                if state != "legacy":
                    raise DurableResourceError("resource.unavailable", "resource catalog root cannot be initialized")
                origin = "legacy-v1"
            epoch = uuid4().hex if fresh_epoch and origin != "legacy-v1" else None
            record = self._root_record(origin, self._check_root_directories(), epoch=epoch)
            raw = check_storage.canonical(record) + b"\n"
            publish_immutable_bytes(self._root_anchor(), raw, byte_limit=4096)
            publish_immutable_bytes(self._root_manifest(), raw, byte_limit=4096)
            if self.verify_root() != "ready-unproven":
                raise DurableResourceError("resource.changed", "resource catalog root was not durably bound")

    @staticmethod
    def _record_store_registration(
        *, family: str, owner_id: str, workspace: Path, root: Path,
    ) -> tuple[str, str, dict, bytes]:
        if (
            _OWNER.fullmatch(owner_id) is None
            or _OWNER.fullmatch(family) is None
            or not workspace.is_absolute()
            or not root.is_absolute()
        ):
            raise DurableResourceError("resource.policy", "record store identity is invalid")
        identity = {
            "family": family, "owner_id": owner_id,
            "workspace": str(workspace), "root": str(root),
        }
        digest = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        store_id = f"workbench-record-store-v1:sha256:{digest}"
        body = {
            "format": RECORD_STORE_KIND, "store_id": store_id, **identity,
            "retention": "protected-until-reviewed-policy",
        }
        expected = _sealed(RECORD_STORE_KIND, body)
        return digest, store_id, expected, check_storage.canonical(expected) + b"\n"

    def register_record_store(
        self, *, family: str, owner_id: str, workspace: Path, root: Path,
    ) -> str:
        """Register one mutable namespace before its owner publishes records."""

        digest, store_id, expected, raw = self._record_store_registration(
            family=family, owner_id=owner_id, workspace=workspace, root=root,
        )
        if root.is_symlink() or not root.is_dir() or not private_path(root, directory=True):
            raise DurableResourceError("resource.unsafe", "record store is not an owner-private directory")
        self._ensure()
        path = self._directory("stores") / f"{digest}.json"
        with private_record_lock(self._directory("leases") / f"{digest}.record-store.lock", wait=True):
            publish_immutable_bytes(path, raw, byte_limit=1024 * 1024, idempotent=True)
            if _read_sealed(path, RECORD_STORE_KIND) != expected:
                raise DurableResourceError("resource.changed", "record store registration changed")
        return store_id

    def _forward_record_store_witness(
        self, *, family: str, owner_id: str, workspace: Path, root: Path,
        store_id: str, registration_id: str, root_info: os.stat_result,
        root_record: Mapping[str, object],
    ) -> tuple[Path, bytes]:
        """Bind one new registration to a pre-write, candidate-scoped V2 file."""

        digest = store_id.rsplit(":", 1)[1]
        target = workspace / ".workbench/record-store-forward-v1" / f"{digest}.json"
        if target.is_relative_to(root) or root.is_relative_to(target.parent):
            raise DurableResourceError("resource.policy", "record store overlaps its forward witness")
        witness = _sealed(RECORD_STORE_FORWARD_KIND, {
            "format": RECORD_STORE_FORWARD_KIND, "schema_version": 1,
            "configuration_home": str(self.configuration_home),
            "catalog_root_id": root_record["id"],
            "catalog_root_epoch": root_record["root_epoch"],
            "workspace": str(workspace), "family": family, "owner_id": owner_id,
            "store_id": store_id, "registration_id": registration_id,
            "root": str(root), "root_device": root_info.st_dev,
            "root_inode": root_info.st_ino,
            "historical_completeness": "unproven",
        })
        return target, check_storage.canonical(witness) + b"\n"

    def register_record_store_forward(
        self, *, family: str, owner_id: str, workspace: Path, root: Path,
    ) -> tuple[str, str]:
        """Opt in one new registration to a V2 issued witness before its row.

        This requires a newly born V2 catalog. The witness covers only this
        registration; old stores and all other families remain unproven.
        """

        digest, store_id, expected, raw = self._record_store_registration(
            family=family, owner_id=owner_id, workspace=workspace, root=root,
        )
        if root.is_symlink() or not root.is_dir() or not private_path(root, directory=True):
            raise DurableResourceError("resource.unsafe", "record store is not an owner-private directory")
        issuance.preflight_workspace(workspace)
        issuance.preflight_workspace_epoch(self, workspace)
        self._ensure(fresh_epoch=True)
        root_record = self.fresh_root_epoch()
        if root_record is None:
            raise DurableResourceError(
                "resource.unsupported", "forward registration requires a newly born V2 catalog root",
            )
        path = self._directory("stores") / f"{digest}.json"
        with private_record_lock(self._directory("leases") / f"{digest}.record-store.lock", wait=True):
            root_info = root.lstat()
            if not stat.S_ISDIR(root_info.st_mode) or not private_path(root, directory=True):
                raise DurableResourceError("resource.changed", "record store root changed before issuance")
            witness_path, witness_bytes = self._forward_record_store_witness(
                family=family, owner_id=owner_id, workspace=workspace, root=root,
                store_id=store_id, registration_id=expected["id"],
                root_info=root_info, root_record=root_record,
            )
            if path.exists() or path.is_symlink() or any(
                entry.name.startswith(f".{path.name}.") for entry in path.parent.iterdir()
            ):
                raise DurableResourceError(
                    "resource.changed", "record store already has registration or uncertain stage",
                )
            if witness_path.exists() or witness_path.is_symlink() or (
                witness_path.parent.exists() and any(
                    entry.name.startswith(f".{witness_path.name}.")
                    for entry in witness_path.parent.iterdir()
                )
            ):
                raise DurableResourceError("resource.changed", "forward witness already exists or is uncertain")
            publisher = CoreDurableResources(
                workspace=workspace, configuration_home=self.configuration_home,
                locations={"evidence": workspace / ".workbench"},
                owner_id="workbench-core", post_birth_issuance=True,
                allow_explicit_filename=True,
            )
            reference = publisher.publish_bytes(
                "evidence", witness_path.name, witness_bytes,
                requested_path=witness_path, domain_id=store_id,
            )
            if self.post_birth_coverage(
                reference.resource_id, workspace=workspace,
                owner_id="workbench-core", target=witness_path,
            ) != "post-birth-covered" or self.read_bytes(reference.resource_id) != witness_bytes:
                raise DurableResourceError("resource.changed", "forward witness did not reopen")
            after_root = root.lstat()
            if (after_root.st_dev, after_root.st_ino) != (root_info.st_dev, root_info.st_ino):
                raise DurableResourceError("resource.changed", "record store root changed after issuance")
            publish_immutable_bytes(path, raw, byte_limit=1024 * 1024)
            if _read_sealed(path, RECORD_STORE_KIND) != expected:
                raise DurableResourceError("resource.changed", "record store registration changed")
        return store_id, reference.resource_id

    def inspect_forward_record_store_registration(
        self, *, family: str, owner_id: str, workspace: Path, root: Path,
        witness_resource_id: str,
    ) -> dict[str, object]:
        """Reopen one exact current pair without inferring missing history."""

        digest, store_id, expected, raw = self._record_store_registration(
            family=family, owner_id=owner_id, workspace=workspace, root=root,
        )
        _resource_nonce(witness_resource_id)
        root_record = self.fresh_root_epoch()
        if root_record is None:
            raise DurableResourceError("resource.unsupported", "forward inspection requires a V2 catalog root")
        try:
            root_info = root.lstat()
        except OSError as exc:
            raise DurableResourceError("resource.unavailable", "record store root is unavailable") from exc
        if not stat.S_ISDIR(root_info.st_mode) or not private_path(root, directory=True):
            raise DurableResourceError("resource.changed", "record store root is unsafe")
        witness_path, witness_bytes = self._forward_record_store_witness(
            family=family, owner_id=owner_id, workspace=workspace, root=root,
            store_id=store_id, registration_id=expected["id"],
            root_info=root_info, root_record=root_record,
        )
        if self.post_birth_coverage(
            witness_resource_id, workspace=workspace,
            owner_id="workbench-core", target=witness_path,
        ) != "post-birth-covered" or self.read_bytes(witness_resource_id) != witness_bytes:
            raise DurableResourceError("resource.changed", "forward witness differs from exact registration")
        path = self._directory("stores") / f"{digest}.json"
        if any(entry.name.startswith(f".{path.name}.") for entry in path.parent.iterdir()):
            raise DurableResourceError("resource.changed", "record store registration has an uncertain stage")
        if path.exists() or path.is_symlink():
            if read_private_single_link_bytes(path, byte_limit=1024 * 1024) != raw:
                raise DurableResourceError("resource.changed", "record store registration bytes changed")
            status = "current-registration"
        else:
            status = "witnessed-registration-unavailable"
        if self.post_birth_coverage(
            witness_resource_id, workspace=workspace,
            owner_id="workbench-core", target=witness_path,
        ) != "post-birth-covered" or self.fresh_root_epoch() != root_record:
            raise DurableResourceError("resource.changed", "forward witness changed during inspection")
        after_root = root.lstat()
        if (after_root.st_dev, after_root.st_ino) != (root_info.st_dev, root_info.st_ino):
            raise DurableResourceError("resource.changed", "record store root changed during inspection")
        return {
            "format": "workbench-record-store-forward-inspection-v1",
            "workspace": str(workspace), "store_id": store_id,
            "witness_resource_id": witness_resource_id, "status": status,
            "historical_completeness": "unproven", "cleanup_authority": "none",
            "root_state": self.verify_root(),
        }

    def reconcile_interrupted_record_store_registration(
        self, *, family: str, owner_id: str, workspace: Path, root: Path,
    ) -> str:
        """Remove only a surviving post-link stage for an exact registration.

        The immutable publisher links its private stage to the final name, then
        removes that stage. An interrupted pre-link stage has no published
        binding and cannot be adopted. This does not attest catalog history.
        """

        digest, store_id, expected, raw = self._record_store_registration(
            family=family, owner_id=owner_id, workspace=workspace, root=root,
        )
        if root.is_symlink() or not root.is_dir() or not private_path(root, directory=True):
            raise DurableResourceError("resource.unsafe", "record store is not an owner-private directory")
        if self.verify_root() != "ready-unproven":
            raise DurableResourceError("resource.unavailable", "record store catalog root is not bound")
        path = self._directory("stores") / f"{digest}.json"
        prefix = f".{path.name}."
        with private_record_lock(self._directory("leases") / f"{digest}.record-store.lock", wait=True):
            # Keep every unknown entry visible to strict inventory. Python's
            # mkstemp publisher emits an eight-character suffix here.
            try:
                stages = sorted(
                    (entry for entry in path.parent.iterdir() if entry.name.startswith(prefix)),
                    key=lambda entry: entry.name,
                )
            except OSError as exc:
                raise DurableResourceError("resource.changed", "record store registration stages are unavailable") from exc
            if not stages:
                try:
                    unchanged = read_private_single_link_bytes(path, byte_limit=1024 * 1024) == raw
                except OSError as exc:
                    raise DurableResourceError("resource.unavailable", "record store registration is unavailable") from exc
                if not unchanged:
                    raise DurableResourceError("resource.changed", "record store registration bytes changed")
                if _read_sealed(path, RECORD_STORE_KIND) != expected:
                    raise DurableResourceError("resource.changed", "record store registration changed")
                return store_id
            if len(stages) != 1 or re.fullmatch(rf"\.{digest}\.json\.[a-z0-9_]{{8}}", stages[0].name) is None:
                raise DurableResourceError("resource.changed", "record store registration stage is ambiguous")
            stage = stages[0]
            try:
                published = path.lstat()
                prepared = stage.lstat()
                if (
                    not stat.S_ISREG(published.st_mode)
                    or not stat.S_ISREG(prepared.st_mode)
                    or (published.st_dev, published.st_ino) != (prepared.st_dev, prepared.st_ino)
                    or published.st_nlink != 2
                    or prepared.st_nlink != 2
                    or read_private_bytes(path, byte_limit=1024 * 1024) != raw
                    or read_private_bytes(stage, byte_limit=1024 * 1024) != raw
                ):
                    raise DurableResourceError("resource.changed", "record store registration stage changed")
                unlink_prepared(path, stage.name, (published.st_dev, published.st_ino))
            except DurableResourceError:
                raise
            except OSError as exc:
                raise DurableResourceError("resource.changed", "record store registration cannot be reconciled") from exc
            if _read_sealed(path, RECORD_STORE_KIND) != expected:
                raise DurableResourceError("resource.changed", "record store registration changed after reconciliation")
        return store_id

    def _registered_record_stores(self, workspace: Path | None) -> list[dict]:
        directory = self._directory("stores")
        if not directory.exists():
            return []
        check_storage.ordinary(directory, directory=True)
        rows = []
        for path in sorted(directory.iterdir()):
            if (
                not path.is_file()
                or not private_path(path, directory=False)
                or re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None
            ):
                raise DurableResourceError("resource.changed", "record store catalog has an invalid entry")
            try:
                raw = read_private_single_link_bytes(path, byte_limit=1024 * 1024)
            except DurableRecordError as exc:
                code = "resource.unavailable" if exc.code == "unavailable" else "resource.changed"
                raise DurableResourceError(code, "record store registration lost private custody") from exc
            try:
                row = json.loads(raw)
                sealed = (
                    isinstance(row, dict)
                    and row == _sealed(RECORD_STORE_KIND, {key: value for key, value in row.items() if key != "id"})
                    and raw == check_storage.canonical(row) + b"\n"
                )
            except (TypeError, ValueError) as exc:
                raise DurableResourceError("resource.changed", "record store registration bytes changed") from exc
            if not sealed:
                raise DurableResourceError("resource.changed", "record store registration bytes changed")
            if set(row) != {"id", "format", "store_id", "family", "owner_id", "workspace", "root", "retention"}:
                raise DurableResourceError("resource.changed", "record store registration has invalid fields")
            identity = {key: row[key] for key in ("family", "owner_id", "workspace", "root")}
            digest = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if (
                row["format"] != RECORD_STORE_KIND
                or row["store_id"] != f"workbench-record-store-v1:sha256:{digest}"
                or path.stem != digest
                or row["retention"] != "protected-until-reviewed-policy"
                or _OWNER.fullmatch(row["family"]) is None
                or _OWNER.fullmatch(row["owner_id"]) is None
                or not Path(row["workspace"]).is_absolute()
                or not Path(row["root"]).is_absolute()
            ):
                raise DurableResourceError("resource.changed", "record store registration identity changed")
            if workspace is not None and row["workspace"] != str(workspace):
                continue
            root = Path(row["root"])
            status = "available" if root.is_dir() and not root.is_symlink() and private_path(root, directory=True) else "unavailable"
            rows.append({
                "store_id": row["store_id"], "family": row["family"],
                "owner_id": row["owner_id"], "workspace": row["workspace"],
                "path": row["root"], "retention": row["retention"], "status": status,
            })
        return rows

    def _reservation(self, resource_id: str) -> dict:
        nonce = _resource_nonce(resource_id)
        record = _read_sealed(self._path("reservations", nonce), RESERVATION_KIND)
        required = {
            "id", "format", "resource_id", "store_id", "store_root",
            "relative_path", "workspace", "owner_id", "role", "role_source",
            "policy_id", "domain_id", "bytes", "sha256", "temporary",
            "references", "allocated_at",
        }
        if (
            set(record) != required
            or record["format"] != RESERVATION_KIND
            or record["resource_id"] != resource_id
            or record["role"] not in {"evidence", "artifacts"}
            or not isinstance(record["owner_id"], str)
            or _OWNER.fullmatch(record["owner_id"]) is None
            or type(record["bytes"]) is not int
            or not 0 <= record["bytes"] <= _MAX_BYTES
            or not isinstance(record["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
            or record["temporary"] != f".workbench-resource-{nonce}.pending"
            or not isinstance(record["workspace"], str)
            or not Path(record["workspace"]).is_absolute()
            or not isinstance(record["role_source"], str)
            or not isinstance(record["policy_id"], str)
            or not isinstance(record["references"], list)
            or any(not isinstance(value, str) or _RESOURCE.fullmatch(value) is None for value in record["references"])
            or len(record["references"]) != len(set(record["references"]))
        ):
            raise DurableResourceError("resource.changed", "resource reservation has invalid fields")
        self._target(record)
        return record

    def _intent(self, resource_id: str) -> dict:
        nonce = _resource_nonce(resource_id)
        record = _read_sealed(self._path("intents", nonce), INTENT_KIND)
        if record.get("resource_id") != resource_id:
            raise DurableResourceError("resource.changed", "resource intent identity changed")
        required = {
            "id", "format", "resource_id", "store_id", "store_root",
            "relative_path", "workspace", "owner_id", "role", "role_source",
            "policy_id", "domain_id", "bytes", "sha256", "temporary",
            "device", "inode", "references", "prepared_at",
            "reservation_id",
        }
        if (
            set(record) != required
            or record["format"] != INTENT_KIND
            or record["role"] not in {"evidence", "artifacts"}
            or not isinstance(record["owner_id"], str)
            or _OWNER.fullmatch(record["owner_id"]) is None
            or type(record["bytes"]) is not int
            or not 0 <= record["bytes"] <= _MAX_BYTES
            or not isinstance(record["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
            or record["temporary"] != f".workbench-resource-{nonce}.pending"
            or type(record["device"]) is not int
            or type(record["inode"]) is not int
            or not isinstance(record["workspace"], str)
            or not Path(record["workspace"]).is_absolute()
            or not isinstance(record["role_source"], str)
            or not isinstance(record["policy_id"], str)
            or not isinstance(record["references"], list)
            or any(not isinstance(value, str) or _RESOURCE.fullmatch(value) is None for value in record["references"])
            or len(record["references"]) != len(set(record["references"]))
        ):
            raise DurableResourceError("resource.changed", "resource intent has invalid fields")
        self._target(record)
        reservation = self._reservation(resource_id)
        if record["reservation_id"] != reservation["id"] or any(
            record[key] != reservation[key]
            for key in (
                "resource_id", "store_id", "store_root", "relative_path", "workspace",
                "owner_id", "role", "role_source", "policy_id", "domain_id",
                "bytes", "sha256", "temporary", "references",
            )
        ):
            raise DurableResourceError("resource.changed", "resource intent differs from its reservation")
        return record

    def _commit(self, resource_id: str, intent: Mapping[str, object]) -> dict:
        nonce = _resource_nonce(resource_id)
        record = _read_sealed(self._path("commits", nonce), COMMIT_KIND)
        if record.get("resource_id") != resource_id or record.get("intent_id") != intent.get("id"):
            raise DurableResourceError("resource.changed", "resource commit identity changed")
        if set(record) != {"id", "format", "resource_id", "intent_id", "committed_at"} or record["format"] != COMMIT_KIND:
            raise DurableResourceError("resource.changed", "resource commit has invalid fields")
        return record

    def _abort(self, resource_id: str) -> dict:
        nonce = _resource_nonce(resource_id)
        record = _read_sealed(self._path("aborts", nonce), ABORT_KIND)
        if (set(record) != {"id", "format", "resource_id", "reason", "aborted_at"}
                or record["format"] != ABORT_KIND or record["resource_id"] != resource_id
                or not isinstance(record["reason"], str)
                or not isinstance(record["aborted_at"], str)):
            raise DurableResourceError("resource.changed", "resource abort changed")
        return record

    def _write(self, name: str, nonce: str, kind: str, body: Mapping[str, object]) -> dict:
        record = _sealed(kind, body)
        check_storage.write_json(self._path(name, nonce), record, byte_limit=1024 * 1024)
        return record

    def _target(self, intent: Mapping[str, object]) -> Path:
        root = intent.get("store_root")
        relative = intent.get("relative_path")
        if not isinstance(root, str) or not isinstance(relative, str):
            raise DurableResourceError("resource.changed", "resource store path is invalid")
        base = Path(root)
        path = Path(relative)
        if not base.is_absolute() or path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
            raise DurableResourceError("resource.changed", "resource store path is unsafe")
        target = base / path
        if _store_id(base) != intent.get("store_id"):
            raise DurableResourceError("resource.changed", "resource store identity changed")
        return target

    def _reference(self, intent: Mapping[str, object]) -> ResourceReference:
        return ResourceReference(
            resource_id=str(intent["resource_id"]),
            store_id=str(intent["store_id"]),
            owner_id=str(intent["owner_id"]),
            role=str(intent["role"]),
            path=self._target(intent),
            bytes=int(intent["bytes"]),
            sha256="sha256:" + str(intent["sha256"]),
            policy_id=str(intent["policy_id"]),
            domain_id=intent.get("domain_id") if isinstance(intent.get("domain_id"), str) else None,
        )

    @contextmanager
    def lease(self, resource_id: str, *, exclusive: bool = False, create: bool = False) -> Iterator[None]:
        if self.verify_root() != "ready-unproven":
            raise DurableResourceError("resource.unavailable", "resource catalog has no rooted custody")
        nonce = _resource_nonce(resource_id)
        path = self._directory("leases") / f"{nonce}.lock"
        if not path.parent.is_dir():
            raise DurableResourceError("resource.unavailable", "resource lease directory is unavailable")
        flags = os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileNotFoundError as exc:
            raise DurableResourceError("resource.unavailable", "resource lease is unavailable") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise DurableResourceError("resource.changed", "resource lease is not an independent file")
            secure_private_path(path, directory=False)
            try:
                with file_lease(descriptor, exclusive=exclusive):
                    yield
            except BlockingIOError as exc:
                raise DurableResourceError("resource.busy", "resource is in use") from exc
        finally:
            os.close(descriptor)

    def describe(self, resource_id: str, *, workspace: Path | None = None) -> ResourceReference:
        with self.lease(resource_id):
            intent = self._intent(resource_id)
            if workspace is not None and intent["workspace"] != str(workspace):
                raise DurableResourceError("resource.scope", "resource belongs to another workspace")
            self._commit(resource_id, intent)
            return self._reference(intent)

    def read_bytes(self, resource_id: str, *, workspace: Path | None = None) -> bytes:
        with self.lease(resource_id):
            intent = self._intent(resource_id)
            if workspace is not None and intent["workspace"] != str(workspace):
                raise DurableResourceError("resource.scope", "resource belongs to another workspace")
            self._commit(resource_id, intent)
            target = self._target(intent)
            try:
                return read_verified(
                    target, expected_size=int(intent["bytes"]),
                    expected_sha256=str(intent["sha256"]),
                )
            except FileNotFoundError as exc:
                raise DurableResourceError("resource.unavailable", "retained resource is unavailable") from exc
            except OSError as exc:
                raise DurableResourceError("resource.changed", "retained resource cannot be read safely") from exc

    def _status(self, intent: Mapping[str, object]) -> str:
        resource_id = str(intent["resource_id"])
        try:
            self._commit(resource_id, intent)
        except DurableResourceError as exc:
            if exc.code != "resource.unavailable":
                return "changed"
            if self._path("aborts", _resource_nonce(resource_id)).is_file():
                return "failed"
            try:
                target = self._target(intent)
                temporary = target.parent / str(intent["temporary"])
                first, second = temporary.lstat(), target.lstat()
                if stat.S_ISREG(first.st_mode) and stat.S_ISREG(second.st_mode) and (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino) == (intent["device"], intent["inode"]):
                    return "published-uncommitted"
            except FileNotFoundError:
                return "incomplete"
            except OSError:
                return "unavailable"
            return "conflict"
        try:
            self.read_bytes(resource_id)
        except DurableResourceError as exc:
            if exc.code == "resource.changed":
                try:
                    target = self._target(intent)
                    temporary = target.parent / str(intent["temporary"])
                    first, second = temporary.lstat(), target.lstat()
                    identity = (int(intent["device"]), int(intent["inode"]))
                    if (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino) == identity:
                        read_verified(target, expected_size=int(intent["bytes"]), expected_sha256=str(intent["sha256"]), allow_linked=True)
                        return "committed-needs-reconcile"
                except (OSError, DurableResourceError):
                    pass
            return "unavailable" if exc.code == "resource.unavailable" else "changed"
        return "committed"

    def inventory(self, *, workspace: Path | None = None) -> dict:
        from ..ide_toolchain_admissions import (
            CoreIdeToolchainAdmissions, IdeToolchainAdmissionError,
        )
        from ..reusable_projections import CoreReusableProjections, ReusableProjectionError
        from ..temporary_leases import CoreTemporaryLeases, TemporaryLeaseError
        from ..transport_trees import CoreTransportTrees, TransportTreeError
        from ..working_allocations import WorkingAllocationCatalog

        root_state = self.verify_root()
        rows = []
        resource_states: dict[str, tuple[str, str]] = {}
        resource_references: list[tuple[str, list[str]]] = []
        children: dict[str, list[Path]] = {}
        if self.root.exists() or self.root.is_symlink():
            check_storage.ordinary(self.root, directory=True)
            if any(
                entry.name not in {*_ROOT_KNOWN, *_ROOT_AUXILIARY, ROOT_ANCHOR_NAME}
                for entry in self.root.iterdir()
            ):
                raise DurableResourceError("resource.changed", "resource catalog has an unknown root entry")
            auxiliary_leases = self._directory("registration-attempt-leases")
            if auxiliary_leases.exists():
                for path in auxiliary_leases.iterdir():
                    info = path.lstat()
                    if (
                        re.fullmatch(r"[0-9a-f]{64}\.lock", path.name) is None
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or not private_path(path, directory=False)
                    ):
                        raise DurableResourceError("resource.changed", "registration attempt lease catalog has an unsafe entry")
            for name in ("reservations", "intents", "commits", "aborts", "leases"):
                check_storage.ordinary(self._directory(name), directory=True)
            try:
                for path in self._directory("leases").iterdir():
                    info = path.lstat()
                    if (
                        _CATALOG_LEASE.fullmatch(path.name) is None
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or not private_path(path, directory=False)
                    ):
                        raise DurableResourceError(
                            "resource.changed", "resource catalog lease has an unknown or unsafe entry",
                        )
            except OSError as exc:
                raise DurableResourceError(
                    "resource.changed", "resource catalog leases cannot be inventoried",
                ) from exc
            # A lease can precede its reservation or record-store registration.
            # Do not adopt an orphan lease as proof that a record ever existed.
            for name in ("reservations", "intents", "commits", "aborts"):
                children[name] = sorted(self._directory(name).iterdir())
                for path in children[name]:
                    info = path.lstat()
                    if (
                        re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or not private_path(path, directory=False)
                    ):
                        raise DurableResourceError("resource.changed", "resource catalog has an unknown or unsafe record")
                    if name != "reservations" and not self._path("reservations", path.stem).is_file():
                        raise DurableResourceError("resource.changed", "resource catalog has an orphan record")
                    if name == "commits" and not self._path("intents", path.stem).is_file():
                        raise DurableResourceError("resource.changed", "resource catalog has an orphan record")

        # Validate retained child content globally before a workspace can hide it.
        # An abort may precede intent, while an absent commit is a valid crash state.
        intents = {
            path.stem: self._intent(f"workbench-resource-v1:{path.stem}")
            for path in children.get("intents", ())
        }
        for path in children.get("commits", ()):
            intent = intents.get(path.stem)
            if intent is None:
                raise DurableResourceError("resource.changed", "resource commit lost its intent")
            self._commit(f"workbench-resource-v1:{path.stem}", intent)
        for path in children.get("aborts", ()):
            self._abort(f"workbench-resource-v1:{path.stem}")
        directory = self._directory("reservations")
        if directory.exists():
            check_storage.ordinary(directory, directory=True)
            for path in sorted(directory.iterdir()):
                resource_id = f"workbench-resource-v1:{path.stem}"
                reservation = self._reservation(resource_id)
                has_intent = self._path("intents", path.stem).exists()
                record = self._intent(resource_id) if has_intent else reservation
                resource_references.append((str(record["workspace"]), record["references"]))
                if workspace is not None and record["workspace"] != str(workspace):
                    continue
                if has_intent:
                    status = self._status(record)
                else:
                    if self._path("aborts", path.stem).is_file():
                        status = "failed"
                    else:
                        target = self._target(record)
                        temporary = target.parent / str(record["temporary"])
                        status = "incomplete" if temporary.exists() or target.exists() else "allocated"
                row = {
                    "resource_id": record["resource_id"],
                    "owner_id": record["owner_id"],
                    "role": record["role"],
                    "store_id": record["store_id"],
                    "store_root": record["store_root"],
                    "path": str(self._target(record)),
                    "sha256": "sha256:" + record["sha256"],
                    "bytes": record["bytes"],
                    "policy_id": record["policy_id"],
                    "role_source": record["role_source"],
                    "references": record["references"],
                    "status": status,
                    "retention": "protected-until-reviewed-policy",
                }
                rows.append(row)
                resource_states[resource_id] = (str(record["workspace"]), status)

        def require_committed_reference(resource_id: str, owner_workspace: str) -> None:
            state = resource_states.get(resource_id)
            if state is None:
                try:
                    intent = self._intent(resource_id)
                    state = (str(intent["workspace"]), self._status(intent))
                except DurableResourceError as exc:
                    raise DurableResourceError(
                        "resource.changed", "referenced resource is unavailable or changed",
                    ) from exc
                resource_states[resource_id] = state
            if state != (owner_workspace, "committed"):
                raise DurableResourceError(
                    "resource.changed", "referenced resource is unavailable, changed, or in another workspace",
                )

        for owner_workspace, references in resource_references:
            for reference in references:
                require_committed_reference(reference, owner_workspace)

        tree_catalog = self.trees
        verified_trees: dict[str, str] = {}
        check_edges: dict[str, set[tuple[str, str]]] = {}

        def verify_tree_references(
            tree_id: str, owner_workspace: str, references: tuple[str, ...],
        ) -> None:
            for reference in references:
                if reference.startswith("workbench-resource-v1:"):
                    require_committed_reference(reference, owner_workspace)
                elif reference.startswith("workbench-tree-v1:"):
                    target_workspace = verified_trees.get(reference)
                    if target_workspace is None:
                        try:
                            with tree_catalog.lease(reference):
                                intent = tree_catalog.intent(reference)
                                tree_catalog.commit(reference, intent)
                                tree_catalog._verify(intent)
                        except (ManagedTreeError, OSError, ValueError) as exc:
                            raise DurableResourceError(
                                "resource.changed", "referenced managed tree is unavailable or changed",
                            ) from exc
                        target_workspace = str(intent["workspace"])
                        verified_trees[reference] = target_workspace
                    if target_workspace != owner_workspace:
                        raise DurableResourceError(
                            "resource.changed", "referenced managed tree belongs to another workspace",
                        )
                elif reference.startswith("workbench-temporary-lease-v1:"):
                    from ..temporary_leases import CoreTemporaryLeases, TemporaryLeaseError

                    try:
                        owner_id = str(tree_catalog.intent(tree_id)["owner_id"])
                        with CoreTemporaryLeases.reference_lease(
                            self.configuration_home, reference,
                            workspace=Path(owner_workspace), owner_id=owner_id,
                        ):
                            pass
                    except (ManagedTreeError, TemporaryLeaseError, OSError, ValueError) as exc:
                        raise DurableResourceError(
                            "resource.changed", "referenced temporary lease is unavailable or changed",
                        ) from exc
                else:
                    check_edges.setdefault(owner_workspace, set()).add((tree_id, reference))

        all_record_stores = self._registered_record_stores(None)
        record_stores = [
            store for store in all_record_stores
            if workspace is None or store["workspace"] == str(workspace)
        ]
        overlay_envelopes: list[dict[str, object]] = []
        for store in all_record_stores:
            if store["family"] != "overlay-envelope-inputs":
                continue
            from ..overlay_envelope_inputs import CoreOverlayEnvelopeInputs
            host = CoreOverlayEnvelopeInputs(
                workspace=Path(store["workspace"]), configuration_home=self.configuration_home,
                owner_id=store["owner_id"],
            )
            if store["path"] != str(host.root):
                raise DurableResourceError("resource.changed", "overlay attempt store registration changed")
            if store["status"] != "available":
                if workspace is None or store["workspace"] == str(workspace):
                    overlay_envelopes.append({"store_id": store["store_id"], "status": "store-unavailable"})
                continue
            try:
                attempts = host.inventory()
            except (OSError, ValueError, TypeError) as exc:
                raise DurableResourceError("resource.changed", "overlay attempt inventory changed") from exc
            if workspace is None or store["workspace"] == str(workspace):
                overlay_envelopes.extend(attempts)
        registered_overlay_roots = {
            store["path"] for store in all_record_stores
            if store["family"] == "overlay-envelope-inputs"
        }
        if self.configuration_home.is_dir():
            for path in self.configuration_home.iterdir():
                if re.fullmatch(r"overlay-envelope-inputs-v1-[0-9a-f]{64}", path.name) is None:
                    continue
                if str(path) in registered_overlay_roots:
                    continue
                if not private_path(path, directory=True):
                    raise DurableResourceError("resource.changed", "unregistered overlay attempt root is unsafe")
                overlay_envelopes.append({"path": str(path), "status": "unregistered-store"})
        trees = tree_catalog.inventory(
            workspace=workspace, validate_references=verify_tree_references,
        )
        for owner_workspace, edges in sorted(check_edges.items()):
            try:
                check_lifecycle.verify_tree_reference_closure(
                    Path(owner_workspace), sorted(edges), self.root,
                )
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                raise DurableResourceError(
                    "resource.changed", "managed tree check reference is unavailable or changed",
                ) from exc
        try:
            all_temporary_leases = CoreTemporaryLeases.inventory_catalog(
                self.configuration_home,
            )
        except TemporaryLeaseError as exc:
            raise DurableResourceError(
                "resource.changed", "temporary lease catalog is unavailable or changed",
            ) from exc
        temporary_leases = [
            lease for lease in all_temporary_leases
            if workspace is None or lease["workspace"] == str(workspace)
        ]
        try:
            transport_trees = CoreTransportTrees.inventory_catalog(
                self.configuration_home, workspace=workspace,
            )
        except TransportTreeError as exc:
            raise DurableResourceError(
                "resource.changed", "transport tree catalog is unavailable or changed",
            ) from exc
        try:
            reusable_projections = CoreReusableProjections.inventory_catalog(
                self.configuration_home, workspace=workspace,
            )
        except ReusableProjectionError as exc:
            raise DurableResourceError(
                "resource.changed", "reusable projection catalog is unavailable or changed",
            ) from exc
        store_by_binding = {
            (store["family"], store["owner_id"], store["workspace"], store["path"]): store
            for store in all_record_stores
        }
        for projection in reusable_projections:
            parent = store_by_binding.get((
                f"{projection['family']}.source-projections",
                projection["owner_id"], projection["workspace"],
                str(Path(projection["path"]).parent),
            ))
            # A missing row is valid for historical direct adoption, and could
            # also mean the declaration was lost. Presence proves no ordering.
            projection["parent_store_registration"] = "present" if parent else "absent"
            projection["parent_store_id"] = parent["store_id"] if parent else None
        ide_toolchain_admissions: list[dict[str, object]] = []
        ide_stores = [
            store for store in all_record_stores
            if store["family"] == "validation-ide-toolchain-admissions-v1"
        ]
        if len(ide_stores) > 1:
            raise DurableResourceError("resource.changed", "IDE admission store binding is ambiguous")
        if ide_stores or (
            self.configuration_home.name == ".ide-toolchain-core"
            and (
                (self.configuration_home / "admissions-v1").exists()
                or (self.configuration_home / "admissions-v1").is_symlink()
            )
        ):
            try:
                toolchains = CoreIdeToolchainAdmissions(
                    self.configuration_home.parent / "ide-validation-v1",
                )
                if ide_stores:
                    store = ide_stores[0]
                    if (
                        store["owner_id"] != "validation"
                        or store["workspace"] != str(toolchains.workspace)
                        or store["path"] != str(self.configuration_home / "admissions-v1")
                    ):
                        raise IdeToolchainAdmissionError("IDE admission store binding changed")
                    if store["status"] != "available":
                        ide_toolchain_admissions.append({
                            "path": store["path"], "status": "store-unavailable",
                            "retention": "protected-until-reviewed-policy",
                        })
                rows = toolchains.inventory_catalog()
                lease_by_id = {
                    lease["lease_id"]: lease for lease in all_temporary_leases
                }
                claimed_stages: set[str] = set()
                for row in rows:
                    if row["status"] == "catalog-only" and (
                        not ide_stores or row["store_id"] != ide_stores[0]["store_id"]
                    ):
                        raise IdeToolchainAdmissionError("IDE admission parent store differs")
                    row["parent_store_registration"] = "present" if ide_stores else "absent"
                    if row["status"] == "catalog-only":
                        stage_id = row["stage_lease_id"]
                        if stage_id is None:
                            row["source_stage_closure"] = "historical-unbound"
                        else:
                            stage = lease_by_id.get(stage_id)
                            stage_path = Path(stage["path"]) if stage is not None else None
                            if (
                                stage_id in claimed_stages
                                or stage is None
                                or stage["workspace"] != str(toolchains.workspace)
                                or stage["owner_id"] != "validation"
                                or stage["role"] != "ide-toolchain"
                                or stage_path.parent != toolchains.toolchain_root
                                or not stage_path.name.startswith(f"ide-{row['archive_sha256']}-")
                                or stage["status"] not in {
                                    "active-or-abandoned", "retained-unproven",
                                }
                            ):
                                raise IdeToolchainAdmissionError(
                                    "IDE admission source stage catalog link changed"
                                )
                            claimed_stages.add(stage_id)
                            row["source_stage_closure"] = (
                                "retained-catalog-only"
                                if stage["status"] == "retained-unproven"
                                else "active-incomplete"
                            )
                    if workspace is None or workspace == toolchains.workspace:
                        ide_toolchain_admissions.append(row)
                if not ide_stores and (workspace is None or workspace == toolchains.workspace):
                    ide_toolchain_admissions.append({
                        "path": str(self.configuration_home / "admissions-v1"),
                        "status": "unregistered-store",
                        "retention": "protected-until-reviewed-policy",
                    })
            except IdeToolchainAdmissionError as exc:
                raise DurableResourceError(
                    "resource.changed", "IDE admission catalog is unavailable or changed",
                ) from exc
        return {
            "format": CATALOG_FORMAT, "schema_version": 1,
            "root_state": root_state,
            "workspace": str(workspace) if workspace is not None else None,
            "resources": rows, "record_stores": record_stores,
            "trees": trees,
            "working_allocations": WorkingAllocationCatalog(self.root.parent).inventory_rows(
                workspace=workspace,
            ),
            "temporary_leases": temporary_leases,
            "transport_trees": transport_trees,
            "reusable_projections": reusable_projections,
            "ide_toolchain_admissions": ide_toolchain_admissions,
            "overlay_envelopes": overlay_envelopes,
        }

    def reconcile(self, resource_id: str) -> ResourceReference:
        """Finish only an already published prepared file; never publish anew."""

        with self.lease(resource_id, exclusive=True):
            intent = self._intent(resource_id)
            try:
                self._commit(resource_id, intent)
            except DurableResourceError as exc:
                if exc.code != "resource.unavailable":
                    raise
                target = self._target(intent)
                temporary = target.parent / str(intent["temporary"])
                try:
                    temp_info, target_info = temporary.lstat(), target.lstat()
                except OSError as unavailable:
                    raise DurableResourceError("resource.incomplete", "resource has no recoverable published file") from unavailable
                identity = (int(intent["device"]), int(intent["inode"]))
                if (
                    not stat.S_ISREG(temp_info.st_mode)
                    or not stat.S_ISREG(target_info.st_mode)
                    or (temp_info.st_dev, temp_info.st_ino) != identity
                    or (target_info.st_dev, target_info.st_ino) != identity
                ):
                    raise DurableResourceError("resource.changed", "prepared and published output identities differ")
                read_verified(target, expected_size=int(intent["bytes"]), expected_sha256=str(intent["sha256"]), allow_linked=True)
                self._write("commits", _resource_nonce(resource_id), COMMIT_KIND, {
                    "format": COMMIT_KIND, "resource_id": resource_id,
                    "intent_id": intent["id"], "committed_at": _now(),
                })
            target = self._target(intent)
            try:
                unlink_prepared(
                    target, str(intent["temporary"]),
                    (int(intent["device"]), int(intent["inode"])),
                )
            except FileNotFoundError:
                pass
            read_verified(target, expected_size=int(intent["bytes"]), expected_sha256=str(intent["sha256"]))
            return self._reference(intent)


class CoreDurableResources:
    """One admitted owner bound to a resolved Core workspace and role policy."""

    def __init__(
        self, *, workspace: Path, configuration_home: Path,
        locations: Mapping[str, Path], owner_id: str,
        policy_id: str | None = None, location_sources: Mapping[str, str] | None = None,
        check_cancelled=lambda: None,
        post_birth_issuance: bool = False,
        allow_explicit_filename: bool = False,
    ):
        if _OWNER.fullmatch(owner_id) is None:
            raise DurableResourceError("resource.policy", "invalid owner identity")
        if not workspace.is_absolute():
            raise DurableResourceError("resource.policy", "workspace must be absolute")
        self.workspace = workspace
        self.locations = dict(locations)
        self.owner_id = owner_id
        self.policy_id = policy_id or _policy_id(workspace, self.locations)
        self.location_sources = dict(location_sources or {})
        self.check_cancelled = check_cancelled
        if type(post_birth_issuance) is not bool:
            raise DurableResourceError("resource.policy", "post-birth issuance selection must be explicit")
        self.post_birth_issuance = post_birth_issuance
        if type(allow_explicit_filename) is not bool:
            raise DurableResourceError("resource.policy", "explicit filename selection must be a boolean")
        self.allow_explicit_filename = allow_explicit_filename
        self.catalog = ResourceCatalog(configuration_home)

    def _destination(self, role: str, name: str, requested_path: Path | None, nonce: str) -> tuple[Path, Path]:
        if role not in {"evidence", "artifacts"} or role not in self.locations:
            raise DurableResourceError("output.role", "unsupported durable output role")
        explicitly_named = (
            self.allow_explicit_filename
            and isinstance(requested_path, Path)
            and requested_path.is_absolute()
        )
        valid_name = (
            _explicit_output_filename(name)
            if explicitly_named
            else (
                type(name) is str
                and _NAME.fullmatch(name) is not None
                and name.split(".", 1)[0].upper() not in _WINDOWS_RESERVED
            )
        )
        if not valid_name:
            raise DurableResourceError("output.write", "output name must be one portable filename")
        if requested_path is not None and not isinstance(requested_path, Path):
            raise DurableResourceError("output.path", "requested output path must be a path")
        selected_root = Path(self.locations[role])
        if not selected_root.is_absolute() or not self.workspace.is_absolute():
            raise DurableResourceError("resource.policy", "resolved resource roots must be absolute")
        if requested_path is None or (not requested_path.is_absolute() and len(requested_path.parts) == 1):
            target = selected_root / "outputs" / self.owner_id / f"{nonce}-{name}"
        else:
            if any(part in {".", ".."} for part in requested_path.parts) or requested_path.name != name:
                raise DurableResourceError("output.path", "requested output path is unsafe")
            target = requested_path if requested_path.is_absolute() else self.workspace / requested_path
        if target.is_relative_to(selected_root):
            store_root = selected_root
        elif target.is_relative_to(self.workspace):
            store_root = self.workspace
        else:
            raise DurableResourceError("output.path", "requested output is outside the selected workspace and role root")
        return target, store_root

    def publish_bytes(
        self, role: str, name: str, data: bytes, *, requested_path: Path | None = None,
        domain_id: str | None = None, references: tuple[str, ...] = (),
    ) -> ResourceReference:
        if type(data) is not bytes or len(data) > _MAX_BYTES:
            raise DurableResourceError("output.bounds", "durable output exceeds the supported byte bound")
        if domain_id is not None and (type(domain_id) is not str or not 0 < len(domain_id) <= 512):
            raise DurableResourceError("output.domain", "invalid domain result identity")
        if (
            not isinstance(references, tuple)
            or len(references) > 128
            or any(not isinstance(value, str) or _RESOURCE.fullmatch(value) is None for value in references)
            or len(set(references)) != len(references)
        ):
            raise DurableResourceError("resource.references", "resource references must be unique")
        nonce = uuid4().hex
        resource_id = f"workbench-resource-v1:{nonce}"
        target, store_root = self._destination(role, name, requested_path, nonce)
        self.check_cancelled()
        if self.post_birth_issuance:
            issuance.preflight_workspace(self.workspace)
            issuance.preflight_workspace_epoch(self.catalog, self.workspace)
        self.catalog._ensure(fresh_epoch=self.post_birth_issuance)
        root_epoch = self.catalog.fresh_root_epoch() if self.post_birth_issuance else None
        if self.post_birth_issuance and root_epoch is None:
            raise DurableResourceError(
                "resource.unsupported", "post-birth issuance requires a newly born V2 catalog root",
            )
        with ExitStack() as stack:
            for reference in sorted(references):
                stack.enter_context(self.catalog.lease(reference))
                other = self.catalog._intent(reference)
                self.catalog._commit(reference, other)
                if other.get("workspace") != str(self.workspace):
                    raise DurableResourceError("resource.references", "referenced resource belongs to another workspace")
                read_verified(
                    self.catalog._target(other),
                    expected_size=int(other["bytes"]),
                    expected_sha256=str(other["sha256"]),
                )
            stack.enter_context(self.catalog.lease(resource_id, exclusive=True, create=True))
            reservation_body = {
                "format": RESERVATION_KIND, "resource_id": resource_id,
                "store_id": _store_id(store_root), "store_root": str(store_root),
                "relative_path": target.relative_to(store_root).as_posix(),
                "workspace": str(self.workspace), "owner_id": self.owner_id,
                "role": role, "role_source": self.location_sources.get(role, "context"),
                "policy_id": self.policy_id, "domain_id": domain_id,
                "bytes": len(data), "sha256": sha256(data).hexdigest(),
                "temporary": f".workbench-resource-{nonce}.pending",
                "references": list(references), "allocated_at": _now(),
            }
            if root_epoch is not None:
                try:
                    issuance.issue(self.catalog, root_epoch, _sealed(RESERVATION_KIND, reservation_body))
                except DurableResourceError:
                    raise
                except (OSError, ValueError) as exc:
                    raise DurableResourceError(
                        "resource.unavailable", "post-birth issuance could not be durably recorded",
                    ) from exc
            reservation = self.catalog._write("reservations", nonce, RESERVATION_KIND, reservation_body)
            stage = None
            try:
                stage = StagedFile(target, data, nonce)
                self.check_cancelled()
                intent = self.catalog._write("intents", nonce, INTENT_KIND, {
                    "format": INTENT_KIND, "resource_id": resource_id,
                    "store_id": reservation["store_id"], "store_root": reservation["store_root"],
                    "relative_path": reservation["relative_path"],
                    "workspace": reservation["workspace"], "owner_id": reservation["owner_id"],
                    "role": reservation["role"], "role_source": reservation["role_source"],
                    "policy_id": reservation["policy_id"], "domain_id": reservation["domain_id"],
                    "bytes": reservation["bytes"], "sha256": stage.digest,
                    "temporary": reservation["temporary"],
                    "device": stage.file_identity[0], "inode": stage.file_identity[1],
                    "references": reservation["references"], "prepared_at": _now(),
                    "reservation_id": reservation["id"],
                })
                self.check_cancelled()
                stage.publish()
                self.catalog._write("commits", nonce, COMMIT_KIND, {
                    "format": COMMIT_KIND, "resource_id": resource_id,
                    "intent_id": intent["id"], "committed_at": _now(),
                })
                return self.catalog._reference(intent)
            except BaseException as exc:
                if stage is None or not stage.published:
                    try:
                        self.catalog._write("aborts", nonce, ABORT_KIND, {
                            "format": ABORT_KIND, "resource_id": resource_id,
                            "reason": type(exc).__name__, "aborted_at": _now(),
                        })
                    except (OSError, ValueError):
                        pass
                if isinstance(exc, OSError):
                    raise DurableResourceError("output.write", f"cannot publish durable output: {exc}") from exc
                raise
            finally:
                if stage is not None:
                    committed = self.catalog._path("commits", nonce).is_file()
                    try:
                        stage.close(cleanup=not stage.published or committed)
                    except OSError:
                        if not committed:
                            raise
                        # The committed reference remains valid; reconcile the
                        # prepared sibling when this store is next inspected.

    def describe(self, resource_id: str) -> ResourceReference:
        return self.catalog.describe(resource_id, workspace=self.workspace)

    def read_bytes(self, resource_id: str) -> bytes:
        return self.catalog.read_bytes(resource_id, workspace=self.workspace)


__all__ = ["CoreDurableResources", "ResourceCatalog"]
