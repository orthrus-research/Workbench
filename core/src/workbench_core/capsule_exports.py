"""Core file-resource custody for reviewed reproduction capsule bytes."""

from __future__ import annotations

from hashlib import sha256 as digest_bytes
from pathlib import Path
import re

from workbench_api.capsule_exports import CapsuleExportError
from workbench_api.durable_resources import DurableResourceError

from .storage.registered import CoreDurableResources, ResourceCatalog
from .user_config_home import default_user_config_home


_CAPSULE_ID = re.compile(r"workbench-reproduction-capsule:sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 16 * 1024 * 1024
_OWNER = "workbench-shell"


class CoreCapsuleExports:
    def __init__(
        self, *, workspace: Path, configuration_home: Path | None = None,
    ) -> None:
        if (
            not isinstance(workspace, Path) or not workspace.is_absolute()
            or not workspace.is_dir() or workspace.is_symlink()
            or (configuration_home is not None and (
                not isinstance(configuration_home, Path) or not configuration_home.is_absolute()
            ))
        ):
            raise CapsuleExportError("capsule.path", "Core capsule export host has invalid roots")
        self.workspace = workspace
        self.configuration_home = configuration_home or default_user_config_home()
        self.catalog = ResourceCatalog(self.configuration_home)

    def _target(self, target: Path) -> Path:
        if (
            not isinstance(target, Path) or not target.is_absolute()
            or target.name in {"", ".", ".."}
            or any(part in {".", ".."} for part in target.parts)
            or target.is_relative_to(self.configuration_home)
        ):
            raise CapsuleExportError("capsule.path", "capsule target is invalid or overlaps Core configuration")
        return target

    def _rows(self, target: Path) -> list[dict]:
        try:
            return [
                row for row in self.catalog.inventory()["resources"]
                if row["path"] == str(target)
            ]
        except DurableResourceError as exc:
            raise CapsuleExportError("capsule.catalog", "Core capsule catalog is unavailable or changed") from exc
        except OSError as exc:
            raise CapsuleExportError("capsule.catalog", "Core capsule catalog is unavailable") from exc

    def cataloged(self, *, target: Path) -> bool:
        return bool(self._rows(self._target(target)))

    def publish(self, *, target: Path, data: bytes, capsule_id: str) -> None:
        target = self._target(target)
        if (
            type(data) is not bytes or not 1 <= len(data) <= _MAX_BYTES
            or type(capsule_id) is not str or _CAPSULE_ID.fullmatch(capsule_id) is None
        ):
            raise CapsuleExportError("capsule.inputs", "capsule has invalid exact bytes or identity")
        if self._rows(target):
            raise CapsuleExportError("capsule.incomplete", "capsule target has an earlier Core attempt")
        if target.exists() or target.is_symlink():
            raise CapsuleExportError("capsule.exists", "capsule output already exists")
        legacy_stage = target.with_name(f".{target.name}.tmp")
        if legacy_stage.exists() or legacy_stage.is_symlink():
            raise CapsuleExportError("capsule.incomplete", "historical capsule temporary output requires review")
        try:
            host = CoreDurableResources(
                workspace=self.workspace,
                configuration_home=self.configuration_home,
                locations={"artifacts": target.parent},
                location_sources={"artifacts": "explicit-capsule-export"},
                owner_id=_OWNER,
                allow_explicit_filename=True,
            )
            reference = host.publish_bytes(
                "artifacts", target.name, data,
                requested_path=target, domain_id=capsule_id,
            )
            if (
                reference.path != target or reference.domain_id != capsule_id
                or host.read_bytes(reference.resource_id) != data
            ):
                raise CapsuleExportError("capsule.changed", "published capsule differs from reviewed bytes")
        except DurableResourceError as exc:
            raise CapsuleExportError(exc.code, str(exc)) from exc
        except OSError as exc:
            raise CapsuleExportError("capsule.publish", "Core capsule publication is unavailable") from exc

    def verify(
        self, *, target: Path, capsule_id: str, size: int, sha256: str,
    ) -> None:
        target = self._target(target)
        if (
            type(capsule_id) is not str or _CAPSULE_ID.fullmatch(capsule_id) is None
            or type(size) is not int or not 1 <= size <= _MAX_BYTES
            or type(sha256) is not str or _DIGEST.fullmatch(sha256) is None
        ):
            raise CapsuleExportError("capsule.inputs", "capsule verification inputs are invalid")
        rows = self._rows(target)
        if len(rows) != 1 or rows[0]["status"] != "committed":
            raise CapsuleExportError("capsule.incomplete", "capsule has no single committed Core resource")
        row = rows[0]
        try:
            reference = self.catalog.describe(row["resource_id"], workspace=self.workspace)
            if (
                reference.owner_id != _OWNER or reference.role != "artifacts"
                or reference.path != target or reference.domain_id != capsule_id
                or reference.bytes != size or reference.sha256 != "sha256:" + sha256
            ):
                raise CapsuleExportError("capsule.changed", "capsule Core binding differs")
            raw = self.catalog.read_bytes(reference.resource_id, workspace=self.workspace)
            if len(raw) != size or digest_bytes(raw).hexdigest() != sha256:
                raise CapsuleExportError("capsule.changed", "capsule Core bytes differ")
        except DurableResourceError as exc:
            raise CapsuleExportError("capsule.changed", "Core capsule resource cannot be reopened") from exc
        except OSError as exc:
            raise CapsuleExportError("capsule.changed", "Core capsule resource cannot be reopened") from exc


__all__ = ["CoreCapsuleExports"]
