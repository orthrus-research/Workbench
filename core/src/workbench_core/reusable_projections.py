"""Adopt and reopen an existing, fixed-path reusable source projection.

The source manifest is owner supplied. Core records the directory identity and
checks its exact source bytes at every open; generated build state may change.
Core also publishes new source-only projections; generated build state remains
mutable under the same fixed path after publication.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Iterator

from workbench_api.reusable_projections import ReusableProjectionError, ReusableProjectionReference

from . import check_storage
from .durable_records import (
    private_record_lock, publish_immutable_bytes, read_bounded_bytes, read_private_bytes,
    read_private_single_link_bytes,
)
from .host_filesystem import file_lease, fsync_directory, private_path, secure_private_path
from .output_routing import _private_directory
from .source_checkouts import _rename_noreplace
from .storage.registered import ResourceCatalog


KIND = "workbench-reusable-projection-v1"
_ID = re.compile(r"workbench-reusable-projection-v1:([0-9a-f]{64})\Z")
_NAME = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_SOURCE_FILES = 2048
_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 100_000
_RECORD_LIMIT = 4 * 1024 * 1024
_CATALOG_CHILDREN = frozenset({"records", "leases", "publication-leases"})
_RECORD_NAME = re.compile(r"([0-9a-f]{64})\.json\Z")
_RECORD_STAGE_NAME = re.compile(r"\.([0-9a-f]{64})\.json\.[a-z0-9_]{8}\Z")
_LEASE_NAME = re.compile(r"[0-9a-f]{64}\.lock\Z")


def _fail(code: str, message: str) -> None:
    raise ReusableProjectionError(f"projection.{code}", message)


def _ordinary_directory(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReusableProjectionError("projection.unavailable", f"projection directory is unavailable: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or getattr(path, "is_junction", lambda: False)():
        _fail("unsafe", f"projection contains a redirect or non-directory: {path}")
    return info


@contextmanager
def _read_existing_lease(path: Path) -> Iterator[None]:
    """Share an existing projection lease without creating or repairing it."""

    descriptor = -1
    try:
        CoreReusableProjections._inventory_directory(path.parent)
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != 0 or not private_path(path, directory=False)):
            _fail("changed", "projection inspection lease is unsafe")
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size != 0
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            _fail("changed", "projection inspection lease changed before acquisition")
        lease = file_lease(descriptor, exclusive=False)
        lease.__enter__()
    except ReusableProjectionError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except BlockingIOError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ReusableProjectionError("projection.busy", "projection is held by a writer") from exc
    except FileNotFoundError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ReusableProjectionError("projection.unavailable", "projection inspection lease is absent") from exc
    except (OSError, ValueError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ReusableProjectionError("projection.changed", "projection inspection lease is unavailable or changed") from exc
    try:
        yield
    finally:
        try:
            try:
                after = path.lstat()
                if (not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
                        or after.st_size != 0
                        or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)):
                    _fail("changed", "projection inspection lease changed during readback")
            except OSError as exc:
                raise ReusableProjectionError(
                    "projection.changed", "projection inspection lease changed during readback",
                ) from exc
        finally:
            try:
                lease.__exit__(None, None, None)
            finally:
                os.close(descriptor)


def _relative(value: Path) -> str:
    if not isinstance(value, Path) or value.is_absolute() or not value.parts or any(
        part in {"", ".", ".."} for part in value.parts
    ) or str(value) != value.as_posix() or any(
        character in str(value) for character in "\\:\0\r\n"
    ):
        _fail("path", "projection member must have a normalized relative path")
    return value.as_posix()


def _source_rows(rows: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    if not isinstance(rows, tuple) or not rows or len(rows) > _MAX_SOURCE_FILES:
        _fail("source", "projection source manifest has invalid bounds")
    clean: list[dict[str, object]] = []
    seen: set[str] = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
            _fail("source", "projection source row is invalid")
        path = _relative(Path(row["path"])) if isinstance(row["path"], str) else ""
        if (not path or path in seen or not isinstance(row["sha256"], str)
                or row["path"] != path
                or _SHA.fullmatch(row["sha256"]) is None
                or type(row["size"]) is not int or not 0 <= row["size"] <= _MAX_SOURCE_BYTES):
            _fail("source", "projection source row is invalid")
        seen.add(path)
        total += row["size"]
        if total > _MAX_SOURCE_BYTES:
            _fail("bounds", "projection declared source bytes exceed 16 MiB")
        clean.append({"path": path, "sha256": row["sha256"], "size": row["size"]})
    return sorted(clean, key=lambda row: str(row["path"]).encode("utf-8"))


def _generated(parts: tuple[str, ...]) -> list[str]:
    if not isinstance(parts, tuple) or len(parts) > 16 or len(set(parts)) != len(parts):
        _fail("source", "generated directory names are invalid")
    for part in parts:
        if not isinstance(part, str) or part in {"", ".", ".."} or "/" in part or "\\" in part:
            _fail("source", "generated directory name is invalid")
    return sorted(parts)


def _suffixes(parts: tuple[str, ...]) -> list[str]:
    if not isinstance(parts, tuple) or len(parts) > 16 or len(set(parts)) != len(parts):
        _fail("source", "generated suffixes are invalid")
    for part in parts:
        if not isinstance(part, str) or re.fullmatch(r"\.[a-z][a-z0-9]{0,15}", part) is None:
            _fail("source", "generated suffix is invalid")
    return sorted(parts)


def _generated_roots(roots: tuple[Path, ...]) -> list[str]:
    if not isinstance(roots, tuple) or len(roots) > 8:
        _fail("source", "generated root paths are invalid")
    selected = [_relative(root) for root in roots]
    if len(set(selected)) != len(selected):
        _fail("source", "generated root paths are repeated")
    return sorted(selected)


def _scan_members(
    root: Path, project: Path, expected: dict[str, dict[str, object]],
    generated: list[str], generated_suffixes: list[str], generated_roots: list[str],
) -> None:
    """Verify one complete source/member pass; safe to repeat after owner validation."""
    seen: set[str] = set()
    count = 0
    # os.walk with followlinks=False still yields symlink entries; inspect each
    # with lstat before treating generated state as admissible.
    def walk_error(exc: OSError) -> None:
        raise ReusableProjectionError("projection.unavailable", "projection cannot be completely inspected") from exc

    for directory, names, files in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        for name in names + files:
            count += 1
            if count > _MAX_MEMBERS:
                _fail("bounds", "projection has too many members")
            member = parent / name
            try:
                info = member.lstat()
            except OSError as exc:
                raise ReusableProjectionError("projection.changed", "projection changed during inspection") from exc
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                _fail("unsafe", f"projection has a redirect or special member: {member}")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                _fail("unsafe", f"projection has a hard-linked member: {member}")
            if not member.is_relative_to(project):
                if project.is_relative_to(member):
                    continue
                allowed = False
                for relative_root in generated_roots:
                    generated_root = root / relative_root
                    if member.is_relative_to(generated_root):
                        allowed = True
                        if member == generated_root and not stat.S_ISDIR(info.st_mode):
                            _fail("unsafe", f"generated root is not a directory: {member}")
                        break
                    if generated_root.is_relative_to(member):
                        allowed = stat.S_ISDIR(info.st_mode)
                        break
                if not allowed:
                    _fail("source", f"projection has an unexpected member: {member}")
                continue
            relative = member.relative_to(project)
            generated_member = any(part in generated for part in relative.parts)
            if generated_member:
                # A generated *file* may not stand in for an admitted directory.
                if len(relative.parts) == 1 and not stat.S_ISDIR(info.st_mode):
                    _fail("unsafe", f"generated member is not a directory: {member}")
                continue
            if stat.S_ISDIR(info.st_mode):
                if not any(Path(source).is_relative_to(relative) for source in expected):
                    _fail("source", f"projection has an undeclared directory: {member}")
                continue
            if member.suffix in generated_suffixes:
                continue
            row = expected.get(relative.as_posix())
            if row is None:
                _fail("source", f"projection has an undeclared source: {member}")
            if info.st_nlink != 1 or info.st_size != row["size"]:
                _fail("changed", f"projection source changed: {member}")
            try:
                raw = read_bounded_bytes(member, byte_limit=int(row["size"]))
            except (OSError, ValueError) as exc:
                raise ReusableProjectionError("projection.changed", f"projection source changed: {member}") from exc
            if "sha256:" + sha256(raw).hexdigest() != row["sha256"]:
                _fail("changed", f"projection source changed: {member}")
            seen.add(relative.as_posix())
    if seen != set(expected):
        _fail("source", "projection source files are incomplete")


def _scan(
    root: Path, project: Path, rows: list[dict[str, object]], generated: list[str],
    generated_suffixes: list[str],
    validate: Callable[[Path], object], generated_roots: list[str] | None = None,
) -> tuple[os.stat_result, os.stat_result]:
    # The state root can be outside the checkout. Reject redirection through all
    # ancestors, and require the adopted root itself to enforce private mode.
    if not root.is_absolute() or ".." in root.parts:
        _fail("path", "projection root must be absolute and normalized")
    for ancestor in (root, *root.parents):
        _ordinary_directory(ancestor)
    parent_info = _ordinary_directory(root.parent)
    root_info = _ordinary_directory(root)
    if not private_path(root, directory=True) or not private_path(root.parent, directory=True):
        _fail("unsafe", "projection cannot enforce owner-private access; on WSL use a Linux filesystem")
    cursor = root
    for part in project.relative_to(root).parts:
        cursor /= part
        _ordinary_directory(cursor)
    expected = {str(row["path"]): row for row in rows}
    declared_roots = [] if generated_roots is None else generated_roots
    _scan_members(root, project, expected, generated, generated_suffixes, declared_roots)
    validate(project)
    _scan_members(root, project, expected, generated, generated_suffixes, declared_roots)
    observed_root = _ordinary_directory(root)
    observed_parent = _ordinary_directory(root.parent)
    if (observed_root.st_dev, observed_root.st_ino) != (root_info.st_dev, root_info.st_ino):
        _fail("changed", "projection root changed during inspection")
    if (observed_parent.st_dev, observed_parent.st_ino) != (parent_info.st_dev, parent_info.st_ino):
        _fail("changed", "projection parent changed during inspection")
    return root_info, parent_info


class CoreReusableProjections:
    """Exact historical adoption and repeated verified reopen for one owner."""

    def __init__(self, *, workspace: Path, configuration_home: Path, owner_id: str):
        if not workspace.is_absolute() or _NAME.fullmatch(owner_id) is None:
            _fail("policy", "projection host needs an absolute workspace and owner")
        self.workspace = workspace
        self.owner_id = owner_id
        self.catalog = ResourceCatalog(configuration_home)
        self.root = self.catalog.root / "reusable-projections"

    def _ensure(self) -> None:
        self.catalog._ensure()
        _private_directory(self.root)
        _private_directory(self.root / "records")
        _private_directory(self.root / "leases")
        _private_directory(self.root / "publication-leases")

    def _record_path(self, projection_id: str) -> Path:
        match = _ID.fullmatch(projection_id) if isinstance(projection_id, str) else None
        if match is None:
            _fail("id", "projection ID is invalid")
        return self.root / "records" / f"{match.group(1)}.json"

    @staticmethod
    def _record_bytes(path: Path) -> bytes:
        """Accept only the publisher's exact hard-exit second link."""

        visible = path.lstat()
        if visible.st_nlink == 1:
            return read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT)
        if visible.st_nlink != 2:
            raise ValueError("projection record has an unknown hard link")
        nonce = path.stem
        stages = [
            stage for stage in path.parent.iterdir()
            if (match := _RECORD_STAGE_NAME.fullmatch(stage.name)) is not None
            and match.group(1) == nonce
        ]
        if len(stages) != 1:
            raise ValueError("projection record has an unknown hard link")
        stage = stages[0]
        staged = stage.lstat()
        if (not stat.S_ISREG(visible.st_mode) or not stat.S_ISREG(staged.st_mode)
                or not private_path(stage, directory=False)
                or (visible.st_dev, visible.st_ino, visible.st_size, visible.st_nlink)
                != (staged.st_dev, staged.st_ino, staged.st_size, staged.st_nlink)):
            raise ValueError("projection record stage changed")
        raw = read_private_bytes(path, byte_limit=_RECORD_LIMIT)
        after = path.lstat()
        staged_after = stage.lstat()
        def identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
            return (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, info.st_mode, info.st_nlink,
            )
        if identity(visible) != identity(after) or identity(staged) != identity(staged_after):
            raise ValueError("projection record stage changed while reading")
        return raw

    def _record(self, projection_id: str) -> dict[str, object]:
        try:
            row = json.loads(self._record_bytes(self._record_path(projection_id)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ReusableProjectionError("projection.unavailable", "projection record is unavailable") from exc
        old_fields = {
                    "id", "format", "family", "path", "source_digest", "project_relative",
                    "source_files", "generated_parts", "generated_suffixes", "owner_id",
                    "workspace", "projection_id", "device", "inode", "parent_device",
                    "parent_inode", "retention",
                }
        if (not isinstance(row, dict) or set(row) not in (old_fields, old_fields | {"generated_roots"})
                or row != check_storage.seal(KIND, {k: v for k, v in row.items() if k != "id"})
                or row.get("format") != KIND or row.get("projection_id") != projection_id
                or row.get("owner_id") != self.owner_id or row.get("workspace") != str(self.workspace)
                or row.get("retention") != "protected-until-reviewed-policy"
                or any(type(row.get(key)) is not int for key in (
                    "device", "inode", "parent_device", "parent_inode",
                ))):
            _fail("changed", "projection record changed")
        binding_keys = (
            "family", "path", "source_digest", "project_relative", "source_files",
            "generated_parts", "generated_suffixes", "owner_id", "workspace",
        )
        binding = {key: row[key] for key in binding_keys}
        if "generated_roots" in row:
            binding["generated_roots"] = row["generated_roots"]
        if projection_id != "workbench-reusable-projection-v1:" + sha256(check_storage.canonical(binding)).hexdigest():
            _fail("changed", "projection record identity changed")
        return row

    @staticmethod
    def _inventory_directory(path: Path) -> Path:
        try:
            directory = check_storage.ordinary(path, directory=True)
        except (OSError, ValueError) as exc:
            raise ReusableProjectionError("projection.changed", "projection catalog directory changed") from exc
        if not private_path(directory, directory=True):
            _fail("changed", "projection catalog directory lost private custody")
        return directory

    @classmethod
    def inventory_catalog(cls, configuration_home: Path, *, workspace: Path | None = None) -> list[dict[str, object]]:
        """Close current record bindings without owner validation or adoption."""

        root = ResourceCatalog(configuration_home).root / "reusable-projections"
        if not root.exists() and not root.is_symlink():
            return []
        try:
            if {entry.name for entry in cls._inventory_directory(root).iterdir()} != _CATALOG_CHILDREN:
                _fail("changed", "projection catalog has an unknown or missing child")
            children = {
                name: sorted(cls._inventory_directory(root / name).iterdir())
                for name in sorted(_CATALOG_CHILDREN)
            }
            for name in ("leases", "publication-leases"):
                for path in children[name]:
                    if _LEASE_NAME.fullmatch(path.name) is None:
                        _fail("changed", "projection catalog has an invalid lease")
                    # Both locks can precede a record during publication.
                    read_private_single_link_bytes(path, byte_limit=0)
            records: list[tuple[str, Path]] = []
            for path in children["records"]:
                match = _RECORD_NAME.fullmatch(path.name)
                if match is None:
                    stage_match = _RECORD_STAGE_NAME.fullmatch(path.name)
                    if stage_match is None:
                        _fail("changed", "projection catalog has an invalid record name")
                    info = path.lstat()
                    if (not stat.S_ISREG(info.st_mode) or info.st_size > _RECORD_LIMIT
                            or info.st_nlink not in (1, 2)
                            or not private_path(path, directory=False)):
                        _fail("changed", "projection catalog has an unsafe record stage")
                    if info.st_nlink == 2:
                        final = path.parent / f"{stage_match.group(1)}.json"
                        published = final.lstat()
                        if (not stat.S_ISREG(published.st_mode)
                                or (info.st_dev, info.st_ino, info.st_nlink)
                                != (published.st_dev, published.st_ino, published.st_nlink)):
                            _fail("changed", "projection record stage lost its published link")
                    continue
                records.append((match.group(1), path))
            rows = []
            for nonce, path in records:
                raw = json.loads(cls._record_bytes(path))
                if (not isinstance(raw, dict) or not isinstance(raw.get("workspace"), str)
                        or not isinstance(raw.get("owner_id"), str)):
                    _fail("changed", "projection catalog record changed")
                host = cls(
                    workspace=Path(raw["workspace"]), configuration_home=configuration_home,
                    owner_id=raw["owner_id"],
                )
                projection_id = f"workbench-reusable-projection-v1:{nonce}"
                row = host._record(projection_id)
                if raw != row:
                    _fail("changed", "projection record changed during inventory")
                if (not isinstance(row["family"], str) or _NAME.fullmatch(row["family"]) is None
                        or not isinstance(row["source_digest"], str)
                        or _SHA.fullmatch(row["source_digest"]) is None
                        or not isinstance(row["path"], str)
                        or not isinstance(row["project_relative"], str)
                        or not isinstance(row["source_files"], list)
                        or not isinstance(row["generated_parts"], list)
                        or not isinstance(row["generated_suffixes"], list)
                        or ("generated_roots" in row and not isinstance(row["generated_roots"], list))):
                    _fail("changed", "projection catalog binding changed")
                projection = Path(row["path"])
                if (not projection.is_absolute() or ".." in projection.parts
                        or str(projection) != row["path"]
                        or projection.name != row["source_digest"].removeprefix("sha256:")
                        or projection.parent.name != row["family"]
                        or projection.parent.parent.name != "source-projections"
                        or _relative(Path(row["project_relative"])) != row["project_relative"]
                        or _source_rows(tuple(row["source_files"])) != row["source_files"]
                        or _generated(tuple(row["generated_parts"])) != row["generated_parts"]
                        or _suffixes(tuple(row["generated_suffixes"])) != row["generated_suffixes"]):
                    _fail("changed", "projection catalog binding changed")
                generated = set(row["generated_parts"])
                suffixes = set(row["generated_suffixes"])
                if any(
                    generated.intersection(Path(source["path"]).parts)
                    or Path(source["path"]).suffix in suffixes
                    for source in row["source_files"]
                ):
                    _fail("changed", "projection source overlaps generated state")
                if "generated_roots" in row:
                    roots = tuple(Path(value) for value in row["generated_roots"])
                    if _generated_roots(roots) != row["generated_roots"]:
                        _fail("changed", "projection generated roots changed")
                    project = projection / row["project_relative"]
                    if any(
                        (projection / root).is_relative_to(project)
                        or project.is_relative_to(projection / root)
                        for root in roots
                    ):
                        _fail("changed", "projection generated root overlaps project")
                if workspace is None or row["workspace"] == str(workspace):
                    rows.append({
                        "projection_id": projection_id, "workspace": row["workspace"],
                        "owner_id": row["owner_id"], "family": row["family"],
                        "path": row["path"], "status": "catalog-only",
                    })
            return rows
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, ReusableProjectionError):
                raise
            raise ReusableProjectionError("projection.changed", "projection catalog changed") from exc

    def ensure(
        self, family: str, path: Path, *, source_root: Path,
        source_digest: str, project_relative: Path,
        source_files: tuple[dict[str, object], ...],
        generated_parts: tuple[str, ...], generated_suffixes: tuple[str, ...],
        validate: Callable[[Path], object], generated_roots: tuple[Path, ...] = (),
    ) -> ReusableProjectionReference:
        """Publish exact source into a private fixed path, or adopt a valid winner.

        A deterministic stage survives interruption. A later caller resumes it
        only after a complete Core scan and owner validation. An incomplete
        stage remains under a registered retained root for explicit recovery.
        """

        if (not isinstance(family, str) or _NAME.fullmatch(family) is None
                or not isinstance(source_digest, str)
                or not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
                or path.name != source_digest.removeprefix("sha256:")
                or path.parent.name != family or path.parent.parent.name != "source-projections"
                or _SHA.fullmatch(source_digest) is None):
            _fail("path", "new projection path does not match its source identity")
        relative = _relative(project_relative)
        rows = _source_rows(source_files)
        generated = _generated(generated_parts)
        suffixes = _suffixes(generated_suffixes)
        roots = _generated_roots(generated_roots)
        if any((path / relative).is_relative_to(path / root)
               or (path / root).is_relative_to(path / relative) for root in roots):
            _fail("source", "generated root overlaps declared project")
        if any(set(Path(str(row["path"])).parts).intersection(generated) for row in rows):
            _fail("source", "declared source overlaps generated build state")
        if any(Path(str(row["path"])).suffix in suffixes for row in rows):
            _fail("source", "declared source overlaps generated build suffix")
        if (not isinstance(source_root, Path) or not source_root.is_absolute()
                or ".." in source_root.parts):
            _fail("source", "projection source root must be absolute")
        for ancestor in (source_root, *source_root.parents):
            _ordinary_directory(ancestor)
        self._ensure()
        _private_directory(path.parent)
        try:
            secure_private_path(path.parent, directory=True)
            self.catalog.register_record_store(
                family=f"{family}.source-projections", owner_id=self.owner_id,
                workspace=self.workspace, root=path.parent,
            )
        except (OSError, ValueError) as exc:
            raise ReusableProjectionError(
                "projection.unsafe", "projection namespace cannot retain private Core custody",
            ) from exc
        stage = path.with_name(f".{path.name}.stage")
        lease = self.root / "publication-leases" / f"{path.name}.lock"
        with private_record_lock(lease, wait=True):
            if not path.exists() and not path.is_symlink():
                if stage.exists() or stage.is_symlink():
                    try:
                        stage_info, parent_info = _scan(
                            stage, stage / relative, rows, [], [], validate, [],
                        )
                    except Exception as exc:
                        raise ReusableProjectionError(
                            "projection.recovery", f"interrupted projection stage requires review: {stage}",
                        ) from exc
                else:
                    stage.mkdir(mode=0o700)
                    secure_private_path(stage, directory=True)
                    project = stage / relative
                    project.mkdir(parents=True, mode=0o700)
                    for row in rows:
                        member = Path(str(row["path"]))
                        source = source_root / member
                        for ancestor in (source, *source.parents):
                            if ancestor == source_root.parent:
                                break
                            if ancestor != source:
                                _ordinary_directory(ancestor)
                        try:
                            raw = read_bounded_bytes(source, byte_limit=int(row["size"]))
                        except (OSError, ValueError) as exc:
                            raise ReusableProjectionError(
                                "projection.changed", f"declared source changed: {member}",
                            ) from exc
                        if len(raw) != row["size"] or "sha256:" + sha256(raw).hexdigest() != row["sha256"]:
                            _fail("changed", f"declared source changed: {member}")
                        target = project / member
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        descriptor = os.open(
                            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                            0o600,
                        )
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                    stage_info, parent_info = _scan(
                        stage, project, rows, [], [], validate, [],
                    )
                    for directory, _, _ in os.walk(stage, topdown=False):
                        fsync_directory(Path(directory))
                    fsync_directory(path.parent)
                if not private_path(path.parent, directory=True):
                    _fail("unsafe", "projection parent lost private custody before publication")
                verified_stage, verified_parent = _scan(
                    stage, stage / relative, rows, [], [], validate, [],
                )
                if (
                    (stage_info.st_dev, stage_info.st_ino) != (
                        verified_stage.st_dev, verified_stage.st_ino,
                    )
                    or (parent_info.st_dev, parent_info.st_ino) != (
                        verified_parent.st_dev, verified_parent.st_ino,
                    )
                ):
                    _fail("changed", "projection stage changed before publication")
                try:
                    _rename_noreplace(stage, path)
                except OSError as exc:
                    if not path.exists() and not path.is_symlink():
                        raise ReusableProjectionError(
                            "projection.publish", "projection cannot be published without replacement",
                        ) from exc
                else:
                    published = _ordinary_directory(path)
                    if (published.st_dev, published.st_ino) != (
                        stage_info.st_dev, stage_info.st_ino,
                    ):
                        _fail("changed", "published projection changed identity")
                    fsync_directory(path.parent)
        return self.adopt(
            family, path, source_digest=source_digest,
            project_relative=project_relative, source_files=source_files,
            generated_parts=generated_parts, generated_suffixes=generated_suffixes,
            validate=validate, generated_roots=generated_roots,
        )

    def adopt(
        self, family: str, path: Path, *, source_digest: str,
        project_relative: Path, source_files: tuple[dict[str, object], ...],
        generated_parts: tuple[str, ...], generated_suffixes: tuple[str, ...],
        validate: Callable[[Path], object], generated_roots: tuple[Path, ...] = (),
    ) -> ReusableProjectionReference:
        if not isinstance(family, str) or _NAME.fullmatch(family) is None:
            _fail("path", "projection family is invalid")
        if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
            _fail("path", "projection path must be absolute and normalized")
        if (path.name != source_digest.removeprefix("sha256:")
                or path.parent.name != family or path.parent.parent.name != "source-projections"
                or _SHA.fullmatch(source_digest) is None):
            _fail("path", "projection path does not match its source identity")
        relative = _relative(project_relative)
        rows = _source_rows(source_files)
        generated = _generated(generated_parts)
        suffixes = _suffixes(generated_suffixes)
        roots = _generated_roots(generated_roots)
        if any((path / relative).is_relative_to(path / project_relative)
               or (path / project_relative).is_relative_to(path / relative)
               for relative in roots):
            _fail("source", "generated root overlaps declared project")
        if any(set(Path(str(row["path"])).parts).intersection(generated) for row in rows):
            _fail("source", "declared source overlaps generated build state")
        if any(Path(str(row["path"])).suffix in suffixes for row in rows):
            _fail("source", "declared source overlaps generated build suffix")
        binding = {
            "family": family, "path": str(path), "source_digest": source_digest,
            "project_relative": relative, "source_files": rows,
            "generated_parts": generated, "generated_suffixes": suffixes,
            "owner_id": self.owner_id,
            "workspace": str(self.workspace),
        }
        if roots:
            binding["generated_roots"] = roots
        nonce = sha256(check_storage.canonical(binding)).hexdigest()
        projection_id = f"workbench-reusable-projection-v1:{nonce}"
        self._ensure()
        with private_record_lock(self.root / "leases" / f"{nonce}.lock", wait=True):
            root_info, parent_info = _scan(path, path / relative, rows, generated, suffixes, validate, roots)
            body = {
                **binding, "format": KIND, "projection_id": projection_id,
                "device": root_info.st_dev, "inode": root_info.st_ino,
                "parent_device": parent_info.st_dev, "parent_inode": parent_info.st_ino,
                "retention": "protected-until-reviewed-policy",
            }
            record = check_storage.seal(KIND, body)
            try:
                publish_immutable_bytes(
                    self._record_path(projection_id), check_storage.canonical(record) + b"\n",
                    byte_limit=_RECORD_LIMIT, idempotent=True,
                )
            except ValueError as exc:
                raise ReusableProjectionError("projection.changed", "projection adoption conflicts with existing custody") from exc
        return ReusableProjectionReference(projection_id, path, path / relative, source_digest, self.owner_id, self.workspace)

    @contextmanager
    def open(
        self, projection_id: str, *, validate: Callable[[Path], object],
    ) -> Iterator[ReusableProjectionReference]:
        self._ensure()
        path = self._record_path(projection_id)
        nonce = path.stem
        with private_record_lock(self.root / "leases" / f"{nonce}.lock", wait=True):
            row = self._record(projection_id)
            root = Path(str(row["path"]))
            project = root / str(row["project_relative"])
            rows = _source_rows(tuple(row["source_files"]))
            generated = _generated(tuple(row["generated_parts"]))
            suffixes = _suffixes(tuple(row["generated_suffixes"]))
            roots = _generated_roots(tuple(Path(value) for value in row.get("generated_roots", ())))
            root_info, parent_info = _scan(root, project, rows, generated, suffixes, validate, roots)
            if ((root_info.st_dev, root_info.st_ino) != (row["device"], row["inode"])
                    or (parent_info.st_dev, parent_info.st_ino) != (row["parent_device"], row["parent_inode"])):
                _fail("changed", "projection directory was replaced after adoption")
            yield ReusableProjectionReference(
                projection_id, root, project, str(row["source_digest"]), self.owner_id, self.workspace,
            )

    def inspect_current(
        self, projection_id: str, *, validate: Callable[[Path], object],
    ) -> dict[str, object]:
        """Read one retained projection with its owner, without Core publication.

        The supplied owner validator must be read-only. This is a present-day
        candidate check, not historical coverage or cleanup authorization.
        """

        if self.catalog.verify_root() != "ready-unproven":
            _fail("unavailable", "projection catalog root is not currently bound")
        path = self._record_path(projection_id)
        with _read_existing_lease(self.root / "leases" / f"{path.stem}.lock"):
            matches = [
                entry for entry in self.inventory_catalog(
                    self.catalog.configuration_home, workspace=self.workspace,
                ) if entry["projection_id"] == projection_id
            ]
            if len(matches) != 1 or matches[0]["owner_id"] != self.owner_id:
                _fail("changed", "projection is absent from the current catalog")
            row = self._record(projection_id)
            record_before = path.lstat()
            root = Path(str(row["path"]))
            project = root / str(row["project_relative"])
            rows = _source_rows(tuple(row["source_files"]))
            generated = _generated(tuple(row["generated_parts"]))
            suffixes = _suffixes(tuple(row["generated_suffixes"]))
            roots = _generated_roots(tuple(Path(value) for value in row.get("generated_roots", ())))
            root_info, parent_info = _scan(root, project, rows, generated, suffixes, validate, roots)
            if ((root_info.st_dev, root_info.st_ino) != (row["device"], row["inode"])
                    or (parent_info.st_dev, parent_info.st_ino) != (
                        row["parent_device"], row["parent_inode"],
                    )):
                _fail("changed", "projection directory was replaced after adoption")
            if self._record(projection_id) != row:
                _fail("changed", "projection record changed during inspection")
            record_after = path.lstat()
            if (
                record_before.st_dev, record_before.st_ino, record_before.st_size,
                record_before.st_mtime_ns, record_before.st_ctime_ns,
            ) != (
                record_after.st_dev, record_after.st_ino, record_after.st_size,
                record_after.st_mtime_ns, record_after.st_ctime_ns,
            ):
                _fail("changed", "projection record changed during inspection")
            if self.catalog.verify_root() != "ready-unproven":
                _fail("changed", "projection catalog root changed during inspection")
            return {
                "projection_id": projection_id,
                "workspace": str(self.workspace), "owner_id": self.owner_id,
                "path": str(root), "status": "current-owner-validated",
                "physical_source": "verified", "generated_state": "unqualified",
                "historical_completeness": "unproven", "cleanup_authority": "none",
            }


__all__ = ["CoreReusableProjections"]
