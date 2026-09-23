"""Core-owned immutable runtime images and private disposable check storage.

Images are explicitly imported or prepared environments, never inferred from a
user's launcher. Bindings are opaque owner data; Core has no game policy.
"""

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
from uuid import uuid4
from contextlib import contextmanager

from .host_filesystem import fsync_directory, file_lease, private_path, secure_private_path
from .filesystem_paths import native_path, resolved_path


class CheckStorageError(ValueError):
    pass


class CheckExecutionBusy(CheckStorageError):
    pass


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def seal(kind, body):
    return {**body, "id": kind + ":sha256:" + sha256(canonical(body)).hexdigest()}


def safe_path(value):
    if not isinstance(value, str):
        raise CheckStorageError("expected a portable relative path")
    path = PurePosixPath(value)
    if (
        not isinstance(value, str)
        or not value
        or path.as_posix() != value
        or path.is_absolute()
        or any(p in {".", "..", ".git"} for p in path.parts)
        or any(c in value for c in "\\:\0\r\n")
    ):
        raise CheckStorageError("expected a portable relative path")
    return path


@contextmanager
def execution_lock(attempt):
    ordinary(attempt, directory=True)
    path = attempt / "execution.lock"
    if native_path(path).exists() or native_path(path).is_symlink():
        ordinary(path)
    descriptor = os.open(
        native_path(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0), 0o600
    )
    try:
        opened, observed = os.fstat(descriptor), native_path(ordinary(path)).stat()
        if (opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino)):
            raise CheckStorageError("execution lease is not an independent file")
        try:
            with file_lease(descriptor, exclusive=True):
                yield
        except BlockingIOError as exc:
            raise CheckExecutionBusy("check attempt is already executing") from exc
    finally:
        os.close(descriptor)


def execution_active(attempt):
    """Observe the Core execution lease, including work after native completion."""
    try:
        with execution_lock(attempt):
            return False
    except CheckExecutionBusy:
        return True


def ordinary(path: Path, *, directory=False):
    path = Path(os.path.abspath(path))
    for parent in (*reversed(path.parents), path):
        if native_path(parent).is_symlink() or getattr(native_path(parent), "is_junction", lambda: False)():
            raise CheckStorageError("managed storage traverses a symbolic link")
    info = native_path(path).stat()
    if not (
        stat.S_ISDIR(info.st_mode)
        if directory
        else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    ):
        raise CheckStorageError(
            "managed input is not an ordinary independent file/directory"
        )
    return path


def read_bytes(path, *, byte_limit=None):
    """Read an ordinary retained file without changing its canonical identity."""
    path = ordinary(path)
    with native_path(path).open("rb") as stream:
        raw = stream.read() if byte_limit is None else stream.read(byte_limit + 1)
    if byte_limit is not None and len(raw) > byte_limit:
        raise CheckStorageError("managed record exceeds its bound")
    return raw


def read_json(path, *, byte_limit=32 * 1024**2):
    raw = read_bytes(path, byte_limit=byte_limit)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CheckStorageError("managed record has duplicate keys")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def write_json(path, value, *, byte_limit=32 * 1024**2):
    return write_bytes(path, canonical(value) + b"\n", byte_limit=byte_limit)


def write_bytes(path, raw, *, byte_limit=32 * 1024**2):
    """Publish retained input/evidence under its owner's selected byte policy."""
    if not isinstance(raw, bytes) or (byte_limit is not None and len(raw) > byte_limit):
        raise CheckStorageError("managed file exceeds its byte bound")
    ordinary(path.parent, directory=True)
    temporary = path.parent / (".record-" + uuid4().hex)
    with native_path(temporary).open("xb") as stream:
        secure_private_path(temporary.absolute(), directory=False)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if native_path(path).exists() or native_path(path).is_symlink():
        native_path(temporary).unlink()
        raise CheckStorageError("managed record already exists")
    # Hard-link publication provides no-clobber semantics on the same filesystem.
    os.link(native_path(temporary), native_path(path))
    native_path(temporary).unlink()
    fsync_directory(path.parent)
    return path


def allocate_attempt(root, prefix):
    """Core allocates private attempt storage; domain owners supply retained contents."""
    import re
    if re.fullmatch(r"[a-z][a-z-]{0,47}", prefix) is None:
        raise CheckStorageError("invalid managed attempt prefix")
    root = initialize(root)
    attempt = root / ".workbench/check-attempts" / (prefix + "-" + uuid4().hex)
    native_path(attempt).mkdir(mode=0o700)
    secure_private_path(attempt, directory=True)
    fsync_directory(attempt.parent)
    return attempt


def _contained_target(root, path, *, kind):
    try:
        target = resolved_path(path, strict=True)
    except (OSError, RuntimeError) as exc:
        raise CheckStorageError("toolchain link is missing, cyclic or unavailable") from exc
    if not target.is_relative_to(root):
        raise CheckStorageError(f"toolchain {kind} link escapes its selected root")
    return target


def _manifest_directory(root, path, *, contained_directory_links=False):
    if contained_directory_links:
        path = _contained_target(root, path, kind="directory")
    return ordinary(path, directory=True)


def _manifest_file(
    root, relative, *, contained_file_links=False, contained_directory_links=False
):
    path = root / relative
    parent = _manifest_directory(
        root, path.parent, contained_directory_links=contained_directory_links
    )
    path = parent / path.name
    if contained_file_links and native_path(path).is_symlink():
        path = _contained_target(root, path, kind="file")
    return ordinary(path)


def manifest_paths(root, *, exclude=(), contained_directory_links=False, cancelled=lambda: False):
    """Walk admitted paths, preserving aliases only under explicit toolchain policy.

    Aliases must resolve inside the selected root. Each directory alias is
    inventoried independently, but no walk branch may return to an ancestor.
    The default remains ordinary directories without links or junctions.
    """
    root = ordinary(root, directory=True)
    pending = [(root, root, (root,))]
    directory_count, file_count = 0, 0
    while pending:
        if cancelled():
            raise CheckStorageError("runtime inventory cancelled")
        lexical, physical, ancestors = pending.pop()
        try:
            with os.scandir(native_path(physical)) as scanned:
                entries = sorted(scanned, key=lambda entry: entry.name)
        except OSError as exc:
            raise CheckStorageError("runtime directory became unavailable while observed") from exc
        directories = []
        for entry in entries:
            if cancelled():
                raise CheckStorageError("runtime inventory cancelled")
            selected = lexical / entry.name
            relative = selected.relative_to(root).as_posix()
            if relative in exclude:
                continue
            safe_path(relative)
            try:
                directory = entry.is_dir(follow_symlinks=True)
            except OSError as exc:
                raise CheckStorageError("runtime path became unavailable while observed") from exc
            if directory:
                target = _manifest_directory(
                    root, selected, contained_directory_links=contained_directory_links
                )
                if target in ancestors:
                    raise CheckStorageError("toolchain directory link creates an ancestor cycle")
                directory_count += 1
                if directory_count > 100_000:
                    raise CheckStorageError("runtime image exceeds its directory bound")
                directories.append((selected, target, (*ancestors, target)))
            else:
                file_count += 1
                if file_count > 100_000:
                    raise CheckStorageError("runtime image exceeds its file bound")
            yield relative, directory
        pending.extend(reversed(directories))


def tree_manifest(
    root, *, exclude=(), contained_file_links=False,
    contained_directory_links=False, cancelled=lambda: False,
):
    root = ordinary(root, directory=True)
    rows, seen, total = [], set(), 0
    for relative, directory in manifest_paths(
        root, exclude=exclude, contained_directory_links=contained_directory_links,
        cancelled=cancelled,
    ):
        if directory:
            continue
        path = _manifest_file(
            root, relative, contained_file_links=contained_file_links,
            contained_directory_links=contained_directory_links,
        )
        if relative.casefold() in seen:
            raise CheckStorageError("runtime paths collide by case")
        seen.add(relative.casefold())
        before = native_path(path).stat()
        total += before.st_size
        if (
            before.st_size > 2 * 1024**3
            or total > 32 * 1024**3
            or len(rows) >= 100_000
        ):
            raise CheckStorageError("runtime image exceeds its bound")
        digest = sha256()
        observed_size = 0
        with native_path(path).open("rb") as stream:
            while chunk := stream.read(1024**2):
                if cancelled():
                    raise CheckStorageError("runtime inventory cancelled")
                observed_size += len(chunk)
                if observed_size > before.st_size:
                    raise CheckStorageError("runtime input grew while observed")
                digest.update(chunk)
        after = native_path(path).stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise CheckStorageError("runtime image changed while observed")
        rows.append(
            {
                "path": relative,
                "size": before.st_size,
                "sha256": digest.hexdigest(),
                "mode": 0o755 if before.st_mode & stat.S_IXUSR else 0o644,
            }
        )
    return sorted(rows, key=lambda row: row["path"])


def copy_manifest(
    source, destination, rows, *, contained_file_links=False,
    contained_directory_links=False, cancelled=lambda: False,
):
    ordinary(source, directory=True)
    ordinary(destination.parent, directory=True)
    native_path(destination).mkdir(mode=0o700)
    for row in rows:
        if cancelled():
            raise CheckStorageError("managed copy cancelled; partial files retained")
        path = _manifest_file(
            source, safe_path(row["path"]), contained_file_links=contained_file_links,
            contained_directory_links=contained_directory_links,
        )
        target = destination / row["path"]
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        digest, count = sha256(), 0
        with native_path(path).open("rb") as stream, native_path(target).open("xb") as output:
            while chunk := stream.read(1024**2):
                if cancelled():
                    raise CheckStorageError(
                        "managed copy cancelled; partial files retained"
                    )
                count += len(chunk)
                if count > row["size"]:
                    raise CheckStorageError("runtime input grew during copy")
                digest.update(chunk)
                output.write(chunk)
        native_path(target).chmod(row["mode"] & 0o777)
        if count != row["size"] or digest.hexdigest() != row["sha256"]:
            raise CheckStorageError("runtime input changed during copy")


def initialize(root):
    root = Path(os.path.abspath(root))
    for path in (*reversed(root.parents), root):
        if native_path(path).is_symlink() or getattr(native_path(path), "is_junction", lambda: False)():
            raise CheckStorageError("check state traverses a link")
    existed = native_path(root).exists()
    native_path(root).mkdir(mode=0o700, parents=True, exist_ok=True)
    if not existed:
        secure_private_path(root, directory=True)
    if not private_path(root, directory=True):
        raise CheckStorageError(
            "check storage must be owned by this user and owner-private"
        )
    for relative in (
        ".workbench",
        ".workbench/tmp",
        ".workbench/check-images",
        ".workbench/check-attempts",
    ):
        path = root / relative
        if native_path(path).is_symlink() or getattr(native_path(path), "is_junction", lambda: False)():
            raise CheckStorageError("check state traverses a link")
        existed = native_path(path).exists()
        native_path(path).mkdir(mode=0o700, exist_ok=True)
        if not existed:
            secure_private_path(path, directory=True)
        if not private_path(path, directory=True):
            raise CheckStorageError("check storage child is not owner-private")
    return root


def import_image(
    root,
    source,
    executable,
    arguments,
    binding,
    *,
    excluded_roots,
    toolchain_root=None,
    launch_input=None,
    _managed_source=False,
    cancelled=lambda: False,
):
    root = initialize(root)
    source = ordinary(source, directory=True)
    if (
        source.is_relative_to(root)
        and not (_managed_source and source.is_relative_to(root / ".workbench/tmp"))
    ) or root.is_relative_to(source):
        raise CheckStorageError("runtime image input overlaps its managed store")
    executable = ordinary(executable)
    with native_path(executable).open("rb") as stream:
        header = stream.read(4)
    if not os.access(executable, os.X_OK) or header != b"\x7fELF":
        raise CheckStorageError(
            "select a native Linux executable, not a shell script or launcher bridge"
        )
    if (
        not isinstance(arguments, list)
        or not arguments
        or len(arguments) > 256
        or any(
            not isinstance(x, str) or len(x) > 65536 or any(c in x for c in "\0\r\n")
            for x in arguments
        )
    ):
        raise CheckStorageError("runtime requires bounded exact arguments")
    if sum(len(x.encode()) for x in arguments) > 1024 * 1024:
        raise CheckStorageError("runtime argument array exceeds its byte bound")
    rows = tree_manifest(source, exclude=excluded_roots)
    if launch_input is not None:
        safe_path(launch_input)
        record = next((row for row in rows if row["path"] == launch_input), None)
        if record is None or record["size"] > 1024 * 1024:
            raise CheckStorageError(
                "runtime launch input must be in its bounded image manifest"
            )
    executable_record = {
        "path": str(executable),
        "sha256": sha256(native_path(executable).read_bytes()).hexdigest(),
    }
    toolchain = None
    if toolchain_root is not None:
        toolchain_root = ordinary(toolchain_root, directory=True)
        if (
            not executable.is_relative_to(toolchain_root)
            or len(toolchain_root.parts) < 3
            or root.is_relative_to(toolchain_root)
            or toolchain_root.is_relative_to(root)
        ):
            raise CheckStorageError(
                "select one independent bounded executable toolchain root"
            )
        toolchain = {
            "executable": executable.relative_to(toolchain_root).as_posix(),
            "files": tree_manifest(toolchain_root, contained_file_links=True),
        }
        executable_record["path"] = toolchain["executable"]
    body = {
        "format": "workbench-runtime-image-v2",
        "binding": binding,
        "executable": executable_record,
        "arguments": arguments,
        "files": rows,
        "toolchain": toolchain,
        "launch_input": launch_input,
    }
    image = seal("runtime-image", body)
    target = root / ".workbench/check-images" / image["id"].split(":")[-1]
    if native_path(target).exists():
        return load_image(root, image["id"])
    staging = root / ".workbench/tmp" / ("image-" + uuid4().hex)
    native_path(staging).mkdir(mode=0o700)
    copy_manifest(source, staging / "payload", rows, cancelled=cancelled)
    if toolchain is not None:
        copy_manifest(
            toolchain_root,
            staging / "toolchain",
            toolchain["files"],
            contained_file_links=True,
            cancelled=cancelled,
        )
    write_json(staging / "image.json", image)
    if tree_manifest(source, exclude=excluded_roots) != rows:
        raise CheckStorageError(
            "runtime image changed during import; partial copy retained"
        )
    if cancelled():
        raise CheckStorageError("image publication cancelled; partial copy retained")
    os.rename(native_path(staging), native_path(target))
    fsync_directory(target.parent)
    return image


def image_path(root, identity):
    import re

    if (
        not isinstance(identity, str)
        or re.fullmatch(r"runtime-image:sha256:[0-9a-f]{64}", identity) is None
    ):
        raise CheckStorageError("select an exact runtime image identity")
    return Path(root) / ".workbench/check-images" / identity.split(":")[-1]


def load_image(root, identity):
    target = image_path(root, identity)
    value = read_json(target / "image.json")
    body = {key: item for key, item in value.items() if key != "id"}
    if (
        value.get("format") != "workbench-runtime-image-v2"
        or "launch_input" not in value
        or seal("runtime-image", body) != value
        or value["id"] != identity
        or tree_manifest(target / "payload") != value["files"]
    ):
        raise CheckStorageError("runtime image bytes or identity changed")
    if value["launch_input"] is not None:
        relative = safe_path(value["launch_input"])
        record = next(
            (row for row in value["files"] if row["path"] == str(relative)), None
        )
        if record is None or record["size"] > 1024 * 1024:
            raise CheckStorageError("runtime launch input is not a bounded image file")
    executable = image_executable(root, value)
    if sha256(native_path(executable).read_bytes()).hexdigest() != value["executable"]["sha256"]:
        raise CheckStorageError("runtime executable changed; import a new image")
    return value


def image_executable(root, image):
    target = image_path(root, image["id"])
    if image.get("toolchain") is not None:
        if tree_manifest(target / "toolchain") != image["toolchain"]["files"]:
            raise CheckStorageError("runtime toolchain bytes changed")
        return ordinary(
            target / "toolchain" / safe_path(image["toolchain"]["executable"])
        )
    return ordinary(Path(image["executable"]["path"]))


def image_summaries(root):
    parent = root / ".workbench/check-images"
    if not native_path(parent).exists():
        return []
    ordinary(parent, directory=True)
    values = []
    for entry in sorted(native_path(parent).iterdir()):
        path = parent / entry.name
        value = read_json(path / "image.json")
        if value.get("format") != "workbench-runtime-image-v2":
            continue
        values.append(
            {key: value[key] for key in ("id", "binding", "executable", "arguments")}
        )
        if len(values) > 1000:
            raise CheckStorageError("runtime image catalog exceeds its bound")
    return values


def cleanup_projection(root, projection):
    """Use the existing recoverable manager, never recursive deletion."""
    from .storage import manager
    ordinary(projection, directory=True)
    inventory = manager.inventory_storage(root)
    rows = [row for row in inventory["items"] if row["path"] == str(projection)]
    if len(rows) != 1:
        raise CheckStorageError(
            "exact check projection is not one managed inventory item"
        )
    return manager.execute_cleanup(
        root, manager.plan_cleanup(root, selector=rows[0]["item_id"])
    )


def provision_projection(
    root,
    identity,
    image,
    *,
    candidate_files,
    candidate_rows,
    source_roots,
    overlay_root,
    overlay_rows,
):
    """Materialize an image plus exact caller-selected source and instrumentation."""
    import re

    if re.fullmatch(r"check-[0-9a-f]{32}", identity) is None:
        raise CheckStorageError("invalid managed check projection identity")
    projection = root / ".workbench/tmp" / identity
    copy_manifest(image_path(root, image["id"]) / "payload", projection, image["files"])
    for row in candidate_rows:
        relative = safe_path(row["path"])
        if relative.parts[0] not in source_roots:
            continue
        target = projection / relative
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        with native_path(target).open("xb") as stream:
            stream.write(candidate_files[row["path"]])
        native_path(target).chmod(row["mode"] & 0o777)
    for row in overlay_rows:
        target = projection / safe_path(row["path"])
        if target.parts[len(projection.parts)] not in source_roots:
            raise CheckStorageError("instrumentation escapes the declared source roots")
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        if native_path(target).exists():
            ordinary(target)
        raw = read_bytes(overlay_root / row["path"])
        if len(raw) != row["size"] or sha256(raw).hexdigest() != row["sha256"]:
            raise CheckStorageError("instrumentation changed during provisioning")
        native_path(target).write_bytes(raw)
    return projection
