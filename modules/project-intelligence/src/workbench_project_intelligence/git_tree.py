"""Bounded, filter-free materialization of one committed Git subtree.

The source repository is observation-only.  Committed blob objects are read
directly, without checkout, archive attributes, hooks, content filters, LFS
hydration, or a Git worktree.  The resulting temporary directory exists only
for the lifetime of the context manager.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Iterator, Mapping, Sequence
import unicodedata

from .git_observation import (
    GitObservationError,
    configured_git_executable,
    observation_environment,
    safe_git_prefix,
)


_OBJECT_FORMAT_LENGTHS = {"sha1": 40, "sha256": 64}
_LFS_VERSION_LINE = b"version https://git-lfs.github.com/spec/v1\n"
_WINDOWS_RESERVED = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_COPY_CHUNK = 1024 * 1024
_MAX_REF_CHARS = 4096
_MAX_PATH_BYTES = 4096
_MAX_COMPONENT_BYTES = 255
_MAX_CHANGED_PATHS = 100_000
_MAX_CHANGED_PATH_BYTES = 64 * 1024 * 1024


class GitTreeMaterializationError(RuntimeError):
    """A Git subtree could not be resolved or materialized safely."""


@dataclass(frozen=True, slots=True)
class GitTreeMaterialization:
    """Exact provenance for one temporary committed-subtree projection."""

    root: Path
    selected_path: Path
    repository_root: Path
    repository_relative_path: str
    selection_kind: str
    requested_ref: str
    target_tip_oid: str | None
    commit_oid: str
    tree_oid: str
    selected_tree_oid: str
    object_format: str
    file_count: int
    total_bytes: int
    manifest_sha256: str
    candidate_git_binding: Mapping[str, object]
    repository_changed_file_count: int | None
    selected_changed_file_count: int | None
    repository_changed_paths: tuple[str, ...] | None
    selected_changed_paths: tuple[str, ...] | None
    repository_dirty_paths: tuple[str, ...]
    selected_dirty_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    mode: str
    object_id: str
    path: str
    size: int


def _fail(detail: str) -> None:
    raise GitTreeMaterializationError(detail)


def _git_environment() -> dict[str, str]:
    environment = observation_environment()
    for key in (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _run(
    prefix: Sequence[str],
    repository: Path,
    arguments: Sequence[str],
    *,
    timeout: float = 30.0,
) -> bytes:
    try:
        completed = subprocess.run(
            [*prefix, "-C", os.fspath(repository), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitTreeMaterializationError(
            f"Git {' '.join(arguments)} could not be observed"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
        _fail(
            f"Git {' '.join(arguments)} failed"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


def _text(
    prefix: Sequence[str], repository: Path, arguments: Sequence[str]
) -> str:
    raw = _run(prefix, repository, arguments)
    try:
        value = raw.decode("utf-8", "strict").strip()
    except UnicodeError as exc:
        raise GitTreeMaterializationError(
            f"Git {' '.join(arguments)} returned non-UTF-8 text"
        ) from exc
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        _fail(f"Git {' '.join(arguments)} returned malformed text")
    return value


def _run_bounded_output(
    prefix: Sequence[str],
    repository: Path,
    arguments: Sequence[str],
    *,
    max_stdout_bytes: int,
    timeout: float,
) -> bytes:
    """Run Git with file-backed output, then admit only bounded stdout."""

    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        try:
            completed = subprocess.run(
                [*prefix, "-C", os.fspath(repository), *arguments],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=error,
                timeout=timeout,
                env=_git_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitTreeMaterializationError(
                f"Git {' '.join(arguments)} could not be observed"
            ) from exc
        error.seek(0)
        detail = error.read(501).decode("utf-8", "replace").strip()
        if completed.returncode:
            _fail(
                f"Git {' '.join(arguments)} failed"
                + (f": {detail[:500]}" if detail else "")
            )
        output.seek(0, os.SEEK_END)
        size = output.tell()
        if size > max_stdout_bytes:
            _fail(
                f"Git {' '.join(arguments)} exceeds the bounded output limit"
            )
        output.seek(0)
        return output.read()


def _validate_ref(value: str, option: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_REF_CHARS
        or len(value.encode("utf-8", errors="surrogatepass")) > _MAX_REF_CHARS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(f"{option} must be bounded non-control text")
    return value


def _relative(value: PurePosixPath, label: str) -> PurePosixPath:
    if not isinstance(value, PurePosixPath):
        _fail(f"{label} must be a PurePosixPath")
    rendered = value.as_posix()
    if rendered == ".":
        return value
    windows = PureWindowsPath(rendered)
    if (
        value.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or "\\" in rendered
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
        or rendered != "/".join(value.parts)
    ):
        _fail(f"{label} must be a canonical portable relative path")
    return value


def _entry_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise GitTreeMaterializationError(
            "Git tree contains a non-UTF-8 path"
        ) from exc
    if unicodedata.normalize("NFC", value) != value:
        _fail(f"Git tree path is not Unicode NFC: {value!r}")
    if (
        not value
        or len(raw) > _MAX_PATH_BYTES
        or "\x00" in value
        or "\\" in value
    ):
        _fail(f"Git tree contains an unsafe path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    parts = tuple(value.split("/"))
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in parts)
        or value != "/".join(parts)
    ):
        _fail(f"Git tree contains a noncanonical path: {value!r}")
    for part in parts:
        reserved_stem = part.split(".", 1)[0].upper()
        if (
            len(part.encode("utf-8")) > _MAX_COMPONENT_BYTES
            or
            part.casefold() == ".git"
            or part.endswith((" ", "."))
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or any(character in '<>:"|?*' for character in part)
            or reserved_stem in _WINDOWS_RESERVED
        ):
            _fail(f"Git tree path is not portable: {value!r}")
    return value


def _portable_component_key(value: str) -> str:
    """Return the cross-platform identity used for one admitted component."""

    return unicodedata.normalize("NFC", value.casefold())


def _admit_tree_path(
    path: str,
    *,
    component_spellings: dict[tuple[str, ...], str],
    directory_paths: set[tuple[str, ...]],
    file_paths: set[tuple[str, ...]],
) -> None:
    """Reject tree shapes that cannot retain one identity on every host.

    Whole-path case folding is insufficient: ``Dir/A`` and ``dir/B`` have
    different folded leaf paths while still naming the same directory on a
    case-insensitive filesystem.  Track every normalized component prefix so
    spelling collisions and file/directory prefix conflicts are independent
    of Git's traversal order.
    """

    parts = tuple(path.split("/"))
    keys = tuple(_portable_component_key(part) for part in parts)
    for index, part in enumerate(parts):
        prefix = keys[: index + 1]
        is_file = index == len(parts) - 1
        if not is_file and prefix in file_paths:
            _fail(f"Git tree path collides as both file and directory: {path}")
        if is_file and prefix in directory_paths:
            _fail(f"Git tree path collides as both file and directory: {path}")

        prior_spelling = component_spellings.get(prefix)
        if prior_spelling is not None and prior_spelling != part:
            _fail(
                "Git tree path components collide by case or normalization: "
                f"{path}"
            )
        component_spellings[prefix] = part

    file_key = keys
    if file_key in file_paths:
        _fail(f"Git tree contains a duplicate portable path: {path}")
    directory_paths.update(
        keys[:length] for length in range(1, len(keys))
    )
    file_paths.add(file_key)


def _object_id(value: str, object_format: str, label: str) -> str:
    length = _OBJECT_FORMAT_LENGTHS[object_format]
    if re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        _fail(f"Git returned an invalid {label} for {object_format}")
    return value


def _join_relative(left: PurePosixPath, right: PurePosixPath) -> PurePosixPath:
    if left.as_posix() == ".":
        return right
    if right.as_posix() == ".":
        return left
    return left / right


def _tree_entries(
    prefix: Sequence[str],
    repository: Path,
    selected_tree_oid: str,
    *,
    object_format: str,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> list[_TreeEntry]:
    raw = _run(
        prefix,
        repository,
        ("ls-tree", "-r", "-z", "-l", "--full-tree", selected_tree_oid),
        timeout=60,
    )
    entries: list[_TreeEntry] = []
    component_spellings: dict[tuple[str, ...], str] = {}
    directory_paths: set[tuple[str, ...]] = set()
    file_paths: set[tuple[str, ...]] = set()
    total_bytes = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if separator != b"\t":
            _fail("Git ls-tree returned a malformed record")
        try:
            mode_raw, kind_raw, oid_raw, size_raw = metadata.split()
            mode = mode_raw.decode("ascii", "strict")
            kind = kind_raw.decode("ascii", "strict")
            oid = oid_raw.decode("ascii", "strict")
            size_text = size_raw.decode("ascii", "strict")
        except (UnicodeError, ValueError) as exc:
            raise GitTreeMaterializationError(
                "Git ls-tree returned malformed metadata"
            ) from exc
        path = _entry_path(raw_path)
        _admit_tree_path(
            path,
            component_spellings=component_spellings,
            directory_paths=directory_paths,
            file_paths=file_paths,
        )
        if mode == "120000":
            _fail(f"Git subtree contains a symbolic link: {path}")
        if mode == "160000" or kind == "commit":
            _fail(f"Git subtree contains a submodule gitlink: {path}")
        if mode not in {"100644", "100755"} or kind != "blob":
            _fail(f"Git subtree contains an unsupported entry kind at {path}")
        oid = _object_id(oid, object_format, f"blob object ID at {path}")
        try:
            size = int(size_text)
        except ValueError as exc:
            raise GitTreeMaterializationError(
                f"Git returned an invalid blob size at {path}"
            ) from exc
        if size < 0 or size > max_file_bytes:
            _fail(f"Git blob exceeds the per-file bound at {path}")
        total_bytes += size
        if total_bytes > max_total_bytes:
            _fail("Git subtree exceeds the aggregate byte bound")
        entries.append(_TreeEntry(mode, oid, path, size))
        if len(entries) > max_files:
            _fail("Git subtree exceeds the file-count bound")
    if not entries:
        _fail("Git selected subtree contains no regular files")
    return entries


def _looks_like_lfs_pointer(prefix: bytes) -> bool:
    normalized = prefix.replace(b"\r\n", b"\n")
    return normalized.startswith(_LFS_VERSION_LINE) and b"\noid sha256:" in normalized


def _open_exclusive(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, 0o600)
    except OSError as exc:
        raise GitTreeMaterializationError(
            f"cannot create temporary Git blob projection: {path}"
        ) from exc


def _materialize_entries(
    prefix: Sequence[str],
    repository: Path,
    destination: Path,
    entries: Sequence[_TreeEntry],
    *,
    object_format: str,
) -> tuple[list[dict[str, object]], int]:
    command = [*prefix, "-C", os.fspath(repository), "cat-file", "--batch"]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
        )
    except OSError as exc:
        raise GitTreeMaterializationError("cannot start Git cat-file") from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        _fail("Git cat-file pipes are unavailable")

    manifest: list[dict[str, object]] = []
    total = 0
    try:
        for entry in entries:
            process.stdin.write(entry.object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline(512)
            if not header.endswith(b"\n"):
                _fail(f"Git cat-file returned a malformed header for {entry.path}")
            try:
                actual_oid_raw, kind_raw, size_raw = header[:-1].split()
                actual_oid = actual_oid_raw.decode("ascii", "strict")
                kind = kind_raw.decode("ascii", "strict")
                size = int(size_raw.decode("ascii", "strict"))
            except (UnicodeError, ValueError) as exc:
                raise GitTreeMaterializationError(
                    f"Git cat-file returned malformed metadata for {entry.path}"
                ) from exc
            if (
                actual_oid != entry.object_id
                or kind != "blob"
                or size != entry.size
            ):
                _fail(f"Git blob identity changed while reading {entry.path}")

            target = destination.joinpath(*PurePosixPath(entry.path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = _open_exclusive(target)
            content_sha256 = sha256()
            git_digest = hashlib.new(object_format)
            git_digest.update(f"blob {entry.size}\0".encode("ascii"))
            pointer_prefix = bytearray()
            remaining = entry.size
            try:
                with os.fdopen(descriptor, "wb") as output:
                    while remaining:
                        chunk = process.stdout.read(min(_COPY_CHUNK, remaining))
                        if not chunk:
                            _fail(f"Git blob ended early while reading {entry.path}")
                        remaining -= len(chunk)
                        output.write(chunk)
                        content_sha256.update(chunk)
                        git_digest.update(chunk)
                        if len(pointer_prefix) < 1024:
                            pointer_prefix.extend(chunk[: 1024 - len(pointer_prefix)])
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                target.unlink(missing_ok=True)
                raise
            if process.stdout.read(1) != b"\n":
                _fail(f"Git cat-file framing changed while reading {entry.path}")
            if git_digest.hexdigest() != entry.object_id:
                _fail(f"Git blob content does not match its object ID at {entry.path}")
            if _looks_like_lfs_pointer(bytes(pointer_prefix)):
                _fail(
                    f"Git subtree contains an unhydrated Git LFS pointer: {entry.path}"
                )
            target.chmod(0o755 if entry.mode == "100755" else 0o644)
            total += entry.size
            manifest.append(
                {
                    "mode": entry.mode,
                    "object_id": entry.object_id,
                    "path": entry.path,
                    "sha256": content_sha256.hexdigest(),
                    "size": entry.size,
                }
            )

        process.stdin.close()
        trailing = process.stdout.read()
        stderr = process.stderr.read()
        returncode = process.wait(timeout=30)
        if returncode or trailing or stderr:
            detail = stderr.decode("utf-8", "replace").strip()
            _fail(
                "Git cat-file did not finish cleanly"
                + (f": {detail[:500]}" if detail else "")
            )
    except BaseException:
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    return manifest, total


def _manifest_sha256(entries: Sequence[Mapping[str, object]]) -> str:
    payload = json.dumps(
        list(entries),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _validate_bounds(
    max_files: int, max_file_bytes: int, max_total_bytes: int
) -> None:
    for value, label, maximum in (
        (max_files, "max_files", 100_000),
        (max_file_bytes, "max_file_bytes", 512 * 1024 * 1024),
        (max_total_bytes, "max_total_bytes", 2 * 1024 * 1024 * 1024),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= maximum
        ):
            _fail(f"{label} must be an integer between 1 and {maximum}")


def _repository_for(source: Path, executable: str) -> Path:
    discovery_prefix = [
        executable,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
    ]
    raw = _run(
        discovery_prefix,
        source,
        ("rev-parse", "--show-toplevel"),
    )
    try:
        rendered = raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise GitTreeMaterializationError(
            "Git repository root is not UTF-8"
        ) from exc
    if rendered.endswith("\r\n"):
        rendered = rendered[:-2]
    elif rendered.endswith("\n"):
        rendered = rendered[:-1]
    else:
        _fail("Git returned a malformed repository root")
    if not rendered or any(character in rendered for character in "\x00\r\n"):
        _fail("Git returned a malformed repository root")
    repository = _windows_extended_path(Path(rendered))
    try:
        before = repository.lstat()
        resolved = _windows_extended_path(repository.resolve(strict=True))
    except OSError as exc:
        raise GitTreeMaterializationError(
            "Git repository root is unavailable"
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        _fail("Git repository root is not a non-symlink directory")
    return resolved


def _windows_extended_path(path: Path) -> Path:
    """Return an absolute native path outside Win32's legacy path limit."""

    if os.name != "nt":
        return path
    rendered = os.path.abspath(os.fspath(path)).replace("/", "\\")
    if rendered.startswith("\\\\?\\"):
        return Path(rendered)
    if rendered.startswith("\\\\"):
        rendered = "\\\\?\\UNC\\" + rendered[2:]
    else:
        rendered = "\\\\?\\" + rendered
    return Path(rendered)


def _windows_display_path(path: Path) -> Path:
    """Remove an internal extended prefix from a user-facing Windows path."""

    if os.name != "nt":
        return path
    rendered = os.fspath(path)
    if rendered.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + rendered[len("\\\\?\\UNC\\") :])
    if rendered.startswith("\\\\?\\"):
        return Path(rendered[len("\\\\?\\") :])
    return path


@contextmanager
def _temporary_materialization_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="workbench-git-tree-") as temporary:
        ordinary_root = Path(temporary).resolve()
        root = _windows_extended_path(ordinary_root)
        try:
            yield root
        finally:
            # TemporaryDirectory's finalizer receives the ordinary spelling.
            # Remove via the extended spelling first so cleanup does not fall
            # back through Win32's legacy MAX_PATH boundary.
            if root != ordinary_root and ordinary_root.exists():
                shutil.rmtree(root)


def _display_git_path(path: bytes) -> str:
    """Render a Git path with unambiguous byte and control escaping."""

    rendered: list[str] = []
    for character in path.decode("utf-8", "surrogateescape"):
        number = ord(character)
        if character == "\\":
            rendered.append("\\\\")
        elif 0xDC80 <= number <= 0xDCFF:
            rendered.append(f"\\x{number - 0xDC00:02x}")
        elif number < 32 or number == 127:
            rendered.append(f"\\x{number:02x}")
        elif unicodedata.category(character).startswith("C"):
            escape = "u" if number <= 0xFFFF else "U"
            width = 4 if number <= 0xFFFF else 8
            rendered.append(f"\\{escape}{number:0{width}x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def display_git_path(path: bytes) -> str:
    """Render one raw Git path without conflating bytes or control text."""

    return _display_git_path(path)


def _partition_paths(
    paths: Sequence[bytes],
    selected_repository_path: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    rendered = tuple(_display_git_path(path) for path in paths)
    if selected_repository_path == ".":
        return rendered, rendered
    prefix_bytes = selected_repository_path.encode("utf-8") + b"/"
    selected = tuple(
        _display_git_path(path) for path in paths if path.startswith(prefix_bytes)
    )
    return rendered, selected


def _nul_paths(raw: bytes, *, label: str) -> list[bytes]:
    if raw and not raw.endswith(b"\0"):
        _fail(f"{label} returned malformed path framing")
    paths = raw[:-1].split(b"\0") if raw else []
    if any(not path for path in paths):
        _fail(f"{label} returned an empty path")
    if len(paths) > _MAX_CHANGED_PATHS:
        _fail(f"{label} exceeds the changed-path count bound")
    return paths


def _changed_file_paths(
    prefix: Sequence[str],
    repository: Path,
    baseline_commit: str,
    candidate_commit: str,
    selected_repository_path: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Observe committed PR paths without reading content or invoking filters."""

    raw = _run_bounded_output(
        prefix,
        repository,
        (
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            baseline_commit,
            candidate_commit,
            "--",
        ),
        max_stdout_bytes=_MAX_CHANGED_PATH_BYTES,
        timeout=60,
    )
    paths = _nul_paths(raw, label="Git diff")
    return _partition_paths(paths, selected_repository_path)


def _dirty_file_paths(
    status: bytes,
    selected_repository_path: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract candidate paths from bounded porcelain-v1 ``-z`` output."""

    records = _nul_paths(status, label="Git status")
    paths: list[bytes] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4 or record[2:3] != b" ":
            _fail("Git status returned a malformed porcelain record")
        state = record[:2]
        path = record[3:]
        if not path:
            _fail("Git status returned an empty candidate path")
        paths.append(path)
        index += 1
        if b"R" in state or b"C" in state:
            if index >= len(records) or not records[index]:
                _fail("Git status returned an incomplete rename/copy record")
            # Porcelain ``-z`` emits the destination first and then the
            # source.  A rename changes both names, while a copy leaves its
            # source untouched; only the destination is a dirty path for a
            # copy.  Still consume the source record so the next status entry
            # retains its framing.
            if b"R" in state:
                paths.append(records[index])
            index += 1
    if len(paths) > _MAX_CHANGED_PATHS:
        _fail("Git status exceeds the changed-path count bound")
    # A rename can mention the same path twice across status entries.
    unique_paths = tuple(dict.fromkeys(paths))
    return _partition_paths(unique_paths, selected_repository_path)


def _candidate_status(prefix: Sequence[str], repository: Path) -> bytes:
    return _run_bounded_output(
        prefix,
        repository,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ),
        max_stdout_bytes=_MAX_CHANGED_PATH_BYTES,
        timeout=60,
    )


def _single_merge_base(
    prefix: Sequence[str],
    repository: Path,
    target_tip: str,
    candidate_head: str,
    *,
    object_format: str,
    requested_ref: str,
) -> str:
    try:
        raw = _run(
            prefix,
            repository,
            ("merge-base", "--all", target_tip, candidate_head),
        )
    except GitTreeMaterializationError as exc:
        raise GitTreeMaterializationError(
            f"--pr-base {requested_ref!r} has no local merge base with candidate HEAD"
        ) from exc
    try:
        rendered = raw.decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise GitTreeMaterializationError(
            "Git merge-base returned non-ASCII object IDs"
        ) from exc
    lines = rendered.splitlines()
    if not lines:
        _fail(
            f"--pr-base {requested_ref!r} has no local merge base with candidate HEAD"
        )
    if len(lines) != 1:
        _fail(
            f"--pr-base {requested_ref!r} has multiple merge bases with candidate HEAD; "
            "an exact baseline is ambiguous"
        )
    if rendered != lines[0] + "\n":
        _fail("Git merge-base returned malformed output")
    return _object_id(
        lines[0],
        object_format,
        "PR merge-base commit object ID",
    )


@contextmanager
def _materialize_git_subtree(
    source: Path,
    ref: str,
    selected_relative: PurePosixPath,
    destination_relative: PurePosixPath,
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    merge_base_target: bool,
) -> Iterator[GitTreeMaterialization]:
    """Yield a temporary raw-blob projection of one committed subtree.

    ``selected_relative`` is relative to the explicit candidate source.  The
    destination has the same shape expected by that source: callers use ``.``
    for a direct Groovy root or ``groovy`` for a pack root.
    """

    _validate_bounds(max_files, max_file_bytes, max_total_bytes)
    ref_option = "--pr-base" if merge_base_target else "--baseline-ref"
    ref = _validate_ref(ref, ref_option)
    selected_relative = _relative(selected_relative, "selected_relative")
    destination_relative = _relative(
        destination_relative, "destination_relative"
    )
    requested = Path(source).expanduser()
    native_requested = _windows_extended_path(requested)
    try:
        before = native_requested.lstat()
        resolved_source = _windows_extended_path(
            native_requested.resolve(strict=True)
        )
    except OSError as exc:
        raise GitTreeMaterializationError(
            f"candidate source is unavailable: {requested}"
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        _fail("candidate source must be a non-symlink directory")
    display_source = _windows_display_path(resolved_source)
    if os.name == "nt" and len(os.fspath(display_source)) >= 260:
        _fail(
            "candidate source exceeds the supported Git for Windows path limit; "
            "move the checkout to a shorter path for --baseline-ref/--pr-base, "
            "or use --baseline PATH for an explicit directory comparison"
        )
    try:
        executable = configured_git_executable()
    except GitObservationError as exc:
        _fail(str(exc))
    if executable is None:
        _fail("Git is unavailable")
    repository = _repository_for(resolved_source, executable)
    display_repository = _windows_display_path(repository)
    try:
        source_relative_native = resolved_source.relative_to(repository)
    except ValueError:
        _fail("candidate source escapes its resolved Git repository")
    source_relative = PurePosixPath(source_relative_native.as_posix())
    selected_repository_path = _join_relative(
        source_relative, selected_relative
    )
    selected_repository_text = selected_repository_path.as_posix()
    if selected_repository_text != ".":
        try:
            selected_repository_bytes = selected_repository_text.encode(
                "utf-8", "strict"
            )
        except UnicodeEncodeError as exc:
            raise GitTreeMaterializationError(
                "candidate source path is not UTF-8"
            ) from exc
        _entry_path(selected_repository_bytes)

    try:
        prefix = safe_git_prefix(repository, executable=executable, timeout=10)
    except GitObservationError as exc:
        raise GitTreeMaterializationError(
            f"Git configuration cannot be observed safely: {exc}"
        ) from exc
    object_format = _text(prefix, repository, ("rev-parse", "--show-object-format"))
    if object_format not in _OBJECT_FORMAT_LENGTHS:
        _fail(f"unsupported Git object format: {object_format}")
    candidate_head = _object_id(
        _text(
            prefix,
            repository,
            ("rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"),
        ),
        object_format,
        "candidate HEAD object ID",
    )
    try:
        resolved_ref = _text(
            prefix,
            repository,
            ("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"),
        )
    except GitTreeMaterializationError as exc:
        raise GitTreeMaterializationError(
            f"{ref_option} {ref!r} does not resolve to a local commit"
        ) from exc
    resolved_ref_oid = _object_id(
        resolved_ref,
        object_format,
        "requested ref commit object ID",
    )
    target_tip_oid: str | None = None
    if merge_base_target:
        target_tip_oid = resolved_ref_oid
        commit_oid = _single_merge_base(
            prefix,
            repository,
            target_tip_oid,
            candidate_head,
            object_format=object_format,
            requested_ref=ref,
        )
    else:
        commit_oid = resolved_ref_oid
    tree_oid = _object_id(
        _text(
            prefix,
            repository,
            (
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{commit_oid}^{{tree}}",
            ),
        ),
        object_format,
        "baseline root tree object ID",
    )
    if selected_repository_text == ".":
        selected_tree_oid = tree_oid
    else:
        selected_tree_oid = _object_id(
            _text(
                prefix,
                repository,
                (
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{commit_oid}:{selected_repository_text}",
                ),
            ),
            object_format,
            "selected tree object ID",
        )
    selected_kind = _text(
        prefix, repository, ("cat-file", "-t", selected_tree_oid)
    )
    if selected_kind != "tree":
        _fail("selected Git baseline path is not a directory tree")

    repository_changed_paths: tuple[str, ...] | None = None
    selected_changed_paths: tuple[str, ...] | None = None
    if merge_base_target:
        (
            repository_changed_paths,
            selected_changed_paths,
        ) = _changed_file_paths(
            prefix,
            repository,
            commit_oid,
            candidate_head,
            selected_repository_text,
        )
    status = _candidate_status(prefix, repository)
    repository_dirty_paths, selected_dirty_paths = _dirty_file_paths(
        status,
        selected_repository_text,
    )
    candidate_binding: Mapping[str, object] = {
        "repository_root": str(display_repository),
        "revision": candidate_head,
        "dirty": bool(status),
    }
    entries = _tree_entries(
        prefix,
        repository,
        selected_tree_oid,
        object_format=object_format,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )

    with _temporary_materialization_root() as root:
        selected_path = (
            root
            if destination_relative.as_posix() == "."
            else root.joinpath(*destination_relative.parts)
        )
        if selected_path != root:
            selected_path.mkdir(parents=True, exist_ok=False)
        manifest, total_bytes = _materialize_entries(
            prefix,
            repository,
            selected_path,
            entries,
            object_format=object_format,
        )
        materialized = GitTreeMaterialization(
            root=root,
            selected_path=selected_path,
            repository_root=display_repository,
            repository_relative_path=selected_repository_text,
            selection_kind=("merge-base" if merge_base_target else "exact-ref"),
            requested_ref=ref,
            target_tip_oid=target_tip_oid,
            commit_oid=commit_oid,
            tree_oid=tree_oid,
            selected_tree_oid=selected_tree_oid,
            object_format=object_format,
            file_count=len(manifest),
            total_bytes=total_bytes,
            manifest_sha256=_manifest_sha256(manifest),
            candidate_git_binding=candidate_binding,
            repository_changed_file_count=(
                None
                if repository_changed_paths is None
                else len(repository_changed_paths)
            ),
            selected_changed_file_count=(
                None if selected_changed_paths is None else len(selected_changed_paths)
            ),
            repository_changed_paths=repository_changed_paths,
            selected_changed_paths=selected_changed_paths,
            repository_dirty_paths=repository_dirty_paths,
            selected_dirty_paths=selected_dirty_paths,
        )
        body_failed = False
        try:
            yield materialized
        except BaseException:
            body_failed = True
            raise
        finally:
            if not body_failed:
                final_head = _object_id(
                    _text(
                        prefix,
                        repository,
                        (
                            "rev-parse",
                            "--verify",
                            "--end-of-options",
                            "HEAD^{commit}",
                        ),
                    ),
                    object_format,
                    "candidate HEAD recheck object ID",
                )
                if final_head != candidate_head:
                    _fail(
                        "candidate HEAD changed during recipe review; rerun the review"
                    )
                if _candidate_status(prefix, repository) != status:
                    _fail(
                        "candidate working tree changed during recipe review; "
                        "rerun the review"
                    )


@contextmanager
def materialize_git_subtree(
    source: Path,
    ref: str,
    selected_relative: PurePosixPath,
    destination_relative: PurePosixPath,
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> Iterator[GitTreeMaterialization]:
    """Materialize the exact local commit selected by ``ref``."""

    with _materialize_git_subtree(
        source,
        ref,
        selected_relative,
        destination_relative,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        merge_base_target=False,
    ) as materialized:
        yield materialized


@contextmanager
def materialize_git_merge_base_subtree(
    source: Path,
    target_ref: str,
    selected_relative: PurePosixPath,
    destination_relative: PurePosixPath,
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> Iterator[GitTreeMaterialization]:
    """Materialize the local merge base of ``target_ref`` and candidate HEAD."""

    with _materialize_git_subtree(
        source,
        target_ref,
        selected_relative,
        destination_relative,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        merge_base_target=True,
    ) as materialized:
        yield materialized


__all__ = [
    "display_git_path",
    "GitTreeMaterialization",
    "GitTreeMaterializationError",
    "materialize_git_merge_base_subtree",
    "materialize_git_subtree",
]
