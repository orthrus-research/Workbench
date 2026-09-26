"""Physical custody for protected external source mutations.

Owners admit an exact source plan and inspect its resulting state. This port
holds the physical stage/replace/delete/rollback and refuses to overwrite a
later edit during rollback. It is intentionally separate from retained Core
resource cleanup: an external source tree is never Core-owned scratch.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from uuid import uuid4

from workbench_api.source_transactions import (
    SourceImage, SourceStage, SourceTransactionError,
)

from .host_filesystem import fsync_directory


def _fail(code: str, message: str) -> None:
    raise SourceTransactionError(code, message)


def _ordinary_directory(path: Path) -> os.stat_result:
    try:
        state = path.lstat()
    except OSError as exc:
        raise SourceTransactionError("path", f"cannot inspect source directory: {exc}") from exc
    if not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
        _fail("path", "source path is not an ordinary directory")
    return state


def _identity(state: os.stat_result) -> tuple[int, int]:
    return state.st_dev, state.st_ino


def _check_image(image: SourceImage | None) -> None:
    if image is None:
        return
    if (
        not isinstance(image, SourceImage)
        or image.kind not in {"file", "symlink"}
        or type(image.data) is not bytes
        or type(image.executable) is not bool
        or (image.kind == "symlink" and image.executable)
    ):
        _fail("image", "source image is invalid")


class _SourceTransaction:
    def __init__(self, root: Path, *, binding: str, check_cancelled):
        if not isinstance(root, Path) or not root.is_absolute():
            _fail("path", "protected source root must be absolute")
        for ancestor in (root, *root.parents):
            if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
                _fail("path", "protected source root traverses a redirect")
        root_state = _ordinary_directory(root)
        if type(binding) is not str or not 0 < len(binding) <= 512:
            _fail("binding", "protected source transaction binding is invalid")
        self.root = root
        self.root_identity = _identity(root_state)
        self.binding_token = sha256(binding.encode("utf-8")).hexdigest()[:20]
        self.check_cancelled = check_cancelled
        self._stages: dict[str, dict] = {}
        self._created: list[tuple[Path, tuple[int, int]]] = []

    def _target(self, relative: str, *, create_parents: bool) -> Path:
        if type(relative) is not str or not relative or "\\" in relative or ":" in relative:
            _fail("path", "protected source path is not portable")
        selected = PurePosixPath(relative)
        if (
            selected.is_absolute() or selected.as_posix() != relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))
        ):
            _fail("path", "protected source path is not normalized and relative")
        for ancestor in (self.root, *self.root.parents):
            if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
                _fail("path", "protected source root traverses a redirect")
        if _identity(_ordinary_directory(self.root)) != self.root_identity:
            _fail("path", "protected source root changed during transaction")
        parent = self.root
        for part in selected.parts[:-1]:
            parent = parent / part
            try:
                state = parent.lstat()
            except FileNotFoundError:
                if not create_parents:
                    _fail("path", "protected source parent is unavailable")
                parent.mkdir(mode=0o755)
                state = _ordinary_directory(parent)
                self._created.append((parent, _identity(state)))
                fsync_directory(parent.parent)
            if not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
                _fail("path", "protected source path traverses a redirect")
        return parent / selected.name

    @staticmethod
    def _match(path: Path, expected: SourceImage | None) -> None:
        _check_image(expected)
        try:
            visible = path.lstat()
        except FileNotFoundError:
            if expected is None:
                return
            _fail("stale", "protected source disappeared after review")
        if expected is None:
            _fail("stale", "protected source appeared after review")
        assert expected is not None
        if expected.kind == "symlink":
            if not stat.S_ISLNK(visible.st_mode):
                _fail("stale", "protected source kind changed after review")
            data = os.fsencode(os.readlink(path))
            after = path.lstat()
        else:
            if not stat.S_ISREG(visible.st_mode):
                _fail("stale", "protected source kind changed after review")
            if bool(visible.st_mode & stat.S_IXUSR) != expected.executable:
                _fail("stale", "protected source mode changed after review")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if _identity(opened) != _identity(visible) or opened.st_size != len(expected.data):
                    _fail("stale", "protected source changed before reading")
                data = os.read(descriptor, len(expected.data) + 1)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if after.st_size != len(data):
                _fail("stale", "protected source changed while reading")
        final = path.lstat()
        if (
            _identity(visible) != _identity(after)
            or _identity(after) != _identity(final)
            or visible.st_size != final.st_size
            or visible.st_mtime_ns != final.st_mtime_ns
            or data != expected.data
        ):
            _fail("stale", "protected source bytes changed after review")

    def _stage(self, path: Path, image: SourceImage) -> tuple[Path, tuple[int, int]]:
        _check_image(image)
        prefix = f".{path.name}.workbench-{self.binding_token}-"
        if image.kind == "symlink":
            temporary = path.parent / f"{prefix}{uuid4().hex}.tmp"
            identity: tuple[int, int] | None = None
            try:
                os.symlink(os.fsdecode(image.data), temporary)
                identity = _identity(temporary.lstat())
                fsync_directory(path.parent)
                return temporary, identity
            except BaseException:
                if identity is not None:
                    try:
                        if _identity(temporary.lstat()) == identity:
                            temporary.unlink()
                    except FileNotFoundError:
                        pass
                raise
        descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        identity = _identity(os.fstat(descriptor))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(image.data)
                stream.flush()
                os.fchmod(stream.fileno(), 0o755 if image.executable else 0o644)
                os.fsync(stream.fileno())
            fsync_directory(path.parent)
            return temporary, _identity(temporary.lstat())
        except BaseException:
            try:
                if _identity(temporary.lstat()) == identity:
                    temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def prepare(
        self, relative: str, *, before: SourceImage | None,
        after: SourceImage | None, create_parents: bool = False,
    ) -> SourceStage:
        self.check_cancelled()
        _check_image(before)
        _check_image(after)
        target = self._target(relative, create_parents=create_parents)
        self._match(target, before)
        parent_identity = _identity(_ordinary_directory(target.parent))
        staged, identity = (None, None) if after is None else self._stage(target, after)
        reference = SourceStage(uuid4().hex)
        self._stages[reference.token] = {
            "relative": relative, "before": before, "after": after,
            "staged": staged, "identity": identity, "committed": False,
            "restored": False, "parent_identity": parent_identity,
        }
        return reference

    def _entry(self, reference: SourceStage) -> dict:
        if not isinstance(reference, SourceStage) or reference.token not in self._stages:
            _fail("stage", "source stage does not belong to this transaction")
        return self._stages[reference.token]

    def commit(self, reference: SourceStage) -> None:
        self.check_cancelled()
        entry = self._entry(reference)
        if entry["committed"]:
            _fail("stage", "source stage was already committed")
        target = self._target(entry["relative"], create_parents=False)
        if _identity(_ordinary_directory(target.parent)) != entry["parent_identity"]:
            _fail("path", "protected source parent changed before commit")
        self._match(target, entry["before"])
        if entry["after"] is None:
            target.unlink()
        else:
            staged = entry["staged"]
            if staged is None or _identity(staged.lstat()) != entry["identity"]:
                _fail("stage", "source staging identity changed")
            self._match(staged, entry["after"])
            os.replace(staged, target)
        entry["committed"] = True
        fsync_directory(target.parent)
        self._match(target, entry["after"])

    def rollback(self, reference: SourceStage) -> None:
        entry = self._entry(reference)
        if not entry["committed"] or entry["restored"]:
            _fail("stage", "source stage is not available for rollback")
        target = self._target(entry["relative"], create_parents=False)
        if _identity(_ordinary_directory(target.parent)) != entry["parent_identity"]:
            _fail("path", "protected source parent changed before rollback")
        self._match(target, entry["after"])
        if entry["before"] is None:
            target.unlink()
        else:
            replacement, identity = self._stage(target, entry["before"])
            try:
                os.replace(replacement, target)
            finally:
                try:
                    visible = replacement.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if _identity(visible) != identity:
                        _fail("stage", "rollback staging identity changed")
                    self._match(replacement, entry["before"])
                    replacement.unlink()
                    fsync_directory(replacement.parent)
        fsync_directory(target.parent)
        self._match(target, entry["before"])
        entry["restored"] = True

    def rollback_all(self) -> None:
        for token, entry in reversed(list(self._stages.items())):
            if entry["committed"] and not entry["restored"]:
                self.rollback(SourceStage(token))

    def cleanup(self, *, remove_created_directories: bool = False) -> None:
        for entry in self._stages.values():
            staged = entry["staged"]
            if staged is None:
                continue
            if _identity(_ordinary_directory(staged.parent)) != entry["parent_identity"]:
                _fail("path", "protected source parent changed before stage cleanup")
            try:
                visible = staged.lstat()
            except FileNotFoundError:
                continue
            if _identity(visible) != entry["identity"]:
                _fail("stage", "source staging identity changed before cleanup")
            self._match(staged, entry["after"])
            staged.unlink()
            fsync_directory(staged.parent)
        if remove_created_directories:
            for directory, identity in reversed(self._created):
                try:
                    visible = directory.lstat()
                except FileNotFoundError:
                    continue
                if _identity(visible) != identity or not stat.S_ISDIR(visible.st_mode):
                    _fail("path", "created source directory changed before cleanup")
                try:
                    directory.rmdir()
                except OSError:
                    # A non-empty directory is no longer transaction-owned.
                    continue
                fsync_directory(directory.parent)


class CoreSourceTransactions:
    """Bind source mutations only for a dispatched module owner."""

    def __init__(self, *, owner_id: str, check_cancelled=lambda: None):
        if type(owner_id) is not str or not owner_id:
            _fail("owner", "source transaction owner is invalid")
        self.owner_id = owner_id
        self.check_cancelled = check_cancelled

    def open(self, root: Path, *, binding: str) -> _SourceTransaction:
        return _SourceTransaction(
            root, binding=binding, check_cancelled=self.check_cancelled,
        )


__all__ = ["CoreSourceTransactions"]
