"""Materialize exact selected inputs in a caller-owned Core attempt.

Profiles choose source roots and exclusions. This module has no game, launcher,
capture-observer, or process policy; failures retain the attempted workspace.
"""

from hashlib import sha256
import os
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from . import check_storage
from .filesystem_paths import native_path


CaptureWorkspaceError = check_storage.CheckStorageError
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)", re.I)


def materialized_mode(relative, declared_mode, *, windows=None):
    """Project a declared mode onto the host's ordinary-file manifest mode.

    Windows stat exposes executability from the filename suffix, not the Git
    executable bit. The saved declaration remains unchanged; this result only
    describes the materialized file. ``windows`` permits explicit host checks.
    """
    _parts(relative)
    if type(declared_mode) is not int or declared_mode not in (0o644, 0o755, 0o100644, 0o100755):
        raise CaptureWorkspaceError("materialized mode requires an ordinary declared file mode")
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return 0o755 if Path(relative).suffix.casefold() in {".exe", ".com", ".bat", ".cmd"} else 0o644
    return declared_mode & 0o777


def _cancelled(callback):
    if callback():
        raise CaptureWorkspaceError("capture workspace cancelled; partial files retained")


def _parts(value, *, excluded_git=False):
    if excluded_git and value == ".git":
        return (".git",)
    path = check_storage.safe_path(value)
    for part in path.parts:
        if (
            part.casefold() == ".git"
            or part.endswith((".", " "))
            or _RESERVED.match(part)
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
        ):
            raise CaptureWorkspaceError("capture paths must be portable Windows filenames")
    return path.parts


class _Paths:
    def __init__(self):
        self.spellings = {}
        self.files = set()
        self.directories = set()

    def add(self, value, *, directory=False, excluded_git=False):
        parts = _parts(value, excluded_git=excluded_git)
        keys = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
        for index, part in enumerate(parts):
            prefix = keys[:index + 1]
            if prefix in self.spellings and self.spellings[prefix] != part:
                raise CaptureWorkspaceError("capture path components collide by case or normalization")
            self.spellings[prefix] = part
            if index < len(parts) - 1 and prefix in self.files:
                raise CaptureWorkspaceError("capture file and directory paths collide")
        if keys in self.files or (not directory and keys in self.directories):
            raise CaptureWorkspaceError("capture file and directory paths collide")
        self.directories.update(keys[:index] for index in range(1, len(keys)))
        (self.directories if directory else self.files).add(keys)


def _below(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def _aligned(path, roots):
    """An excluded/source root must not acquire a differently spelled alias."""
    # Filesystem relatives and admitted row paths are already canonical. An
    # explicitly excluded .git name is skipped before ordinary row admission.
    parts = tuple(path.split("/"))
    keys = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
    for root in roots:
        root_parts = _parts(root, excluded_git=True)
        root_keys = tuple(unicodedata.normalize("NFC", part).casefold() for part in root_parts)
        for index in range(min(len(keys), len(root_keys))):
            if keys[index] != root_keys[index]:
                break
            if parts[index] != root_parts[index]:
                raise CaptureWorkspaceError("capture root components collide by case or normalization")


def _exclusions(values, *, excluded_git=False):
    result = tuple(sorted(set(values)))
    paths = _Paths()
    for value in result:
        paths.add(value, directory=True, excluded_git=excluded_git)
    return result


def _rows(rows, *, source=False):
    paths, result = _Paths(), []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "mode", "size", "sha256"}
            or type(row["size"]) is not int
            or row["size"] < 0
            or type(row["mode"]) is not int
            or row["mode"] not in ((0o100644, 0o100755) if source else (0o644, 0o755))
            or not isinstance(row["sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
        ):
            raise CaptureWorkspaceError("capture requires exact ordinary file manifest rows")
        paths.add(row["path"])
        result.append({**row, "mode": row["mode"] & 0o777})
    return sorted(result, key=lambda row: row["path"])


def inventory(
    root, *, exclude=(), contained_file_links=False,
    contained_directory_links=False, cancelled=lambda: False,
):
    """Return Core manifest rows after checking the selected portable path tree."""
    _cancelled(cancelled)
    root = check_storage.ordinary(Path(root), directory=True)
    excluded = _exclusions(exclude, excluded_git=True)
    paths = _Paths()
    for relative, directory in check_storage.manifest_paths(
        root, exclude=excluded, contained_directory_links=contained_directory_links,
        cancelled=cancelled,
    ):
        _aligned(relative, excluded)
        paths.add(relative, directory=directory)
    rows = check_storage.tree_manifest(
        root, exclude=excluded, contained_file_links=contained_file_links,
        contained_directory_links=contained_directory_links, cancelled=cancelled,
    )
    _cancelled(cancelled)
    return _rows(rows)


def materialize(
    attempt, *, runtime_root, runtime_files, java_home, java_files,
    source_files, source_rows, source_roots, runtime_exclude=(),
    cancelled=lambda: False,
):
    """Clone reviewed runtime/JVM tables and replace complete source roots.

    ``source_rows`` are saved-candidate V1 rows (Git file modes), and
    ``source_files`` maps their relative names to retained bytes. Only rows
    inside ``source_roots`` enter the runtime. The selected runtime inventory
    must exclude every source root and all caller-declared ``runtime_exclude``
    paths. Original and retained inventories are verified before returning.
    """
    _cancelled(cancelled)
    attempt = check_storage.ordinary(Path(attempt), directory=True)
    runtime_root = check_storage.ordinary(Path(runtime_root), directory=True)
    java_home = check_storage.ordinary(Path(java_home), directory=True)
    for selected in (runtime_root, java_home):
        if attempt.is_relative_to(selected) or selected.is_relative_to(attempt):
            raise CaptureWorkspaceError("capture attempt overlaps selected runtime or toolchain")
    roots = _exclusions(source_roots)
    if not roots:
        raise CaptureWorkspaceError("capture source roots must be explicit")
    excludes = _exclusions((*roots, *runtime_exclude), excluded_git=True)
    runtime_rows, java_rows = _rows(runtime_files), _rows(java_files)
    if any(_below(row["path"], excludes) for row in runtime_rows):
        raise CaptureWorkspaceError("runtime manifest includes an excluded source or ephemeral path")
    captured_rows = _rows(source_rows, source=True)
    for row in captured_rows:
        _aligned(row["path"], roots)
    selected_source = [
        {**row, "mode": materialized_mode(row["path"], row["mode"])}
        for row in captured_rows if _below(row["path"], roots)
    ]
    if {name for name in source_files if _below(name, roots)} != {row["path"] for row in selected_source}:
        raise CaptureWorkspaceError("captured source bytes and declared rows differ")
    for row in selected_source:
        _cancelled(cancelled)
        raw = source_files[row["path"]]
        if not isinstance(raw, bytes) or len(raw) != row["size"] or sha256(raw).hexdigest() != row["sha256"]:
            raise CaptureWorkspaceError("captured source bytes differ from their declared row")
    expected_runtime = _rows([*runtime_rows, *selected_source])

    def verify_originals():
        if inventory(runtime_root, exclude=excludes, cancelled=cancelled) != runtime_rows:
            raise CaptureWorkspaceError("selected runtime changed from its reviewed inventory")
        if inventory(java_home, contained_file_links=True, contained_directory_links=True, cancelled=cancelled) != java_rows:
            raise CaptureWorkspaceError("selected toolchain changed from its reviewed inventory")

    verify_originals()
    runtime = attempt / "runtime"
    toolchain = attempt / "java"
    check_storage.copy_manifest(runtime_root, runtime, runtime_rows, cancelled=cancelled)
    check_storage.copy_manifest(
        java_home, toolchain, java_rows, contained_file_links=True,
        contained_directory_links=True, cancelled=cancelled,
    )
    for row in selected_source:
        _cancelled(cancelled)
        destination = runtime / row["path"]
        native_path(destination.parent).mkdir(parents=True, exist_ok=True)
        check_storage.write_bytes(destination, source_files[row["path"]], byte_limit=None)
        native_path(destination).chmod(row["mode"])
    if inventory(runtime, cancelled=cancelled) != expected_runtime:
        raise CaptureWorkspaceError("retained runtime differs from its declared inputs")
    if inventory(toolchain, cancelled=cancelled) != java_rows:
        raise CaptureWorkspaceError("retained toolchain differs from its declared inputs")
    verify_originals()
    _cancelled(cancelled)
    return {
        "runtime": str(runtime), "java_home": str(toolchain),
        "runtime_files": expected_runtime, "java_files": java_rows,
    }


def replace_file(root, relative, raw, *, expected_sha256=None):
    """Publish a caller-declared override only over the exact reviewed bytes.

    The caller owns this execution projection. An omitted expected digest
    permits creation only. Failed publication retains its temporary file;
    successful replacement preserves the reviewed file's executable mode.
    """
    root = check_storage.ordinary(Path(root), directory=True)
    parts = _parts(relative)
    if not isinstance(raw, bytes) or (
        expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
        )
    ):
        raise CaptureWorkspaceError("override requires bytes and an exact expected digest")
    parent = root
    for index, part in enumerate(parts):
        key = unicodedata.normalize("NFC", part).casefold()
        if any(
            child.name != part and unicodedata.normalize("NFC", child.name).casefold() == key
            for child in native_path(parent).iterdir()
        ):
            raise CaptureWorkspaceError("override path collides by case or normalization")
        if index < len(parts) - 1:
            parent = parent / part
            native_path(parent).mkdir(mode=0o700, exist_ok=True)
            check_storage.ordinary(parent, directory=True)
    destination = parent / parts[-1]

    def observe_existing():
        try:
            check_storage.ordinary(destination)
            before = native_path(destination).stat()
            digest = sha256(native_path(destination).read_bytes()).hexdigest()
            after = native_path(destination).stat()
        except OSError as exc:
            raise CaptureWorkspaceError("override target is unavailable for its expected bytes") from exc
        token = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if token(before) != token(after) or digest != expected_sha256:
            raise CaptureWorkspaceError("override target differs from its expected bytes")
        return token(after), 0o755 if after.st_mode & 0o100 else 0o644

    if expected_sha256 is None:
        if os.path.lexists(native_path(destination)):
            raise CaptureWorkspaceError("override creation requires an absent target")
        identity, mode = None, materialized_mode(relative, 0o644)
    else:
        identity, mode = observe_existing()
    temporary = parent / (".capture-write-" + uuid4().hex)
    try:
        with native_path(temporary).open("xb") as stream:
            native_path(temporary).chmod(mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if expected_sha256 is None:
            # No-clobber atomic creation on the destination filesystem.
            os.link(native_path(temporary), native_path(destination))
            native_path(temporary).unlink()
        else:
            if observe_existing()[0] != identity:
                raise CaptureWorkspaceError("override target custody changed during publication")
            os.replace(native_path(temporary), native_path(destination))
        check_storage.fsync_directory(parent)
    except OSError as exc:
        raise CaptureWorkspaceError("override publication failed; temporary files retained") from exc
    return {"path": relative, "size": len(raw), "sha256": sha256(raw).hexdigest(), "mode": mode}
