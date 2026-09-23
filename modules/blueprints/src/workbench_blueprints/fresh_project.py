"""Fresh-project bootstrap and recovery mechanics owned by Blueprints.

This additive V2 port deliberately does not reinterpret Blueprints V1 plans or
standards.  It gives profile-owned constructors an exact, race-detecting
observation for an absent, empty, or clean unborn Git destination and retains
the bootstrap state outside that destination while the existing Blueprints
application transaction writes the reviewed project bytes.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
from typing import Any, Mapping, NoReturn, Sequence, cast
from workbench_api.host_filesystem import fsync_directory

from . import application_transaction


OBSERVATION_FORMAT = "workbench-blueprints-fresh-target-observation-v2"
OBSERVATION_KIND = "workbench-blueprints-fresh-target-observation"
BOOTSTRAP_JOURNAL_FORMAT = "workbench-blueprints-fresh-bootstrap-journal-v2"
BOOTSTRAP_RECEIPT_FORMAT = "workbench-blueprints-fresh-bootstrap-receipt-v2"
BOOTSTRAP_RECEIPT_KIND = "workbench-blueprints-fresh-bootstrap-receipt"

_EXCLUDE_BLOCK = b"# Workbench fresh project V2\n/.workbench/\n"
_MAXIMUM_GIT_METADATA_BYTES = 1024 * 1024
_OBSERVATION_KEYS = {
    "format",
    "git",
    "id",
    "parent_identity",
    "schema_version",
    "state",
    "target_identity",
    "target_uri",
}


class FreshProjectError(ValueError):
    """A fresh target changed, is unsafe, or cannot be recovered exactly."""


def _fail(message: str) -> NoReturn:
    raise FreshProjectError(message)


def _absolute(value: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _ordinary_directory(path: Path, label: str) -> os.stat_result:
    try:
        state = path.lstat()
    except OSError as exc:
        raise FreshProjectError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail(f"{label} must be an ordinary non-symlink directory")
    return state


def _no_symlink_ancestors(path: Path, label: str) -> None:
    """Reject path traversal through a symlink before any owned mutation."""

    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise FreshProjectError(f"cannot inspect {label} ancestry") from exc
        if stat.S_ISLNK(state.st_mode):
            _fail(f"{label} traverses a symbolic link")


def _identity(state: os.stat_result) -> dict[str, int]:
    return {"device": state.st_dev, "inode": state.st_ino}


def _read_regular(path: Path, label: str, maximum: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise FreshProjectError(f"cannot open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            _fail(f"{label} must be a bounded ordinary file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        if (
            len(raw) > maximum
            or len(raw) != before.st_size
            or before_identity != after_identity
        ):
            _fail(f"{label} changed while being read")
        return raw
    finally:
        os.close(descriptor)


def _read_optional_regular(path: Path, label: str) -> bytes | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FreshProjectError(f"cannot inspect {label}") from exc
    return _read_regular(path, label, _MAXIMUM_GIT_METADATA_BYTES)


def _atomic_replace(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    _ordinary_directory(path.parent, "fresh-project state parent")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_new(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    _ordinary_directory(path.parent, "fresh-project state parent")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            mode,
        )
    except OSError as exc:
        raise FreshProjectError(f"cannot create retained {path.name}") from exc
    try:
        raw_offset = 0
        while raw_offset < len(raw):
            written = os.write(descriptor, raw[raw_offset:])
            if written < 1:
                raise OSError("fresh-project retained write made no progress")
            raw_offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git(target: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    for key in (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    ):
        environment.pop(key, None)
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(target), *arguments],
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise FreshProjectError("cannot inspect or initialize fresh Git target") from exc


def _git_observation(target: Path, *, allow_transaction_staging: bool = False) -> dict[str, Any]:
    git_path = target / ".git"
    git_state = _ordinary_directory(git_path, "fresh target .git")
    top = _git(target, "rev-parse", "--show-toplevel").stdout.strip()
    if _absolute(top) != target.resolve():
        _fail("fresh target must be its own Git worktree root")
    head = _git(target, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode == 0:
        _fail("fresh target Git repository already has a born HEAD")
    if head.returncode not in {1, 128}:
        _fail("fresh target Git HEAD state is unverifiable")
    index = _git(target, "ls-files", "--stage").stdout
    status = _git(
        target,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout
    if index or (status and not allow_transaction_staging):
        _fail("fresh target Git repository is not clean and unborn")
    object_format = _git(target, "rev-parse", "--show-object-format").stdout.strip()
    if object_format != "sha1":
        _fail("fresh target Git repository must use the supported sha1 object format")
    exclude = _read_optional_regular(git_path / "info/exclude", "Git exclude file")
    return {
        "exclude_sha256": None if exclude is None else sha256(exclude).hexdigest(),
        "exclude_size": None if exclude is None else len(exclude),
        "git_directory_identity": _identity(git_state),
        "head_state": "unborn",
        "object_format": "sha1",
    }


def observe_fresh_target(target: Path | str) -> dict[str, Any]:
    """Return a sealed exact observation without creating or changing the target."""

    path = _absolute(target)
    if path == Path(path.anchor):
        _fail("fresh target cannot be a filesystem root")
    parent = path.parent
    _no_symlink_ancestors(parent, "fresh target parent")
    parent_state = _ordinary_directory(parent, "fresh target parent")
    try:
        target_state = path.lstat()
    except FileNotFoundError:
        body = {
            "format": OBSERVATION_FORMAT,
            "git": None,
            "parent_identity": _identity(parent_state),
            "schema_version": 2,
            "state": "absent",
            "target_identity": None,
            "target_uri": path.as_uri(),
        }
        return application_transaction.seal(OBSERVATION_KIND, body)
    except OSError as exc:
        raise FreshProjectError("cannot inspect fresh target") from exc
    if stat.S_ISLNK(target_state.st_mode) or not stat.S_ISDIR(target_state.st_mode):
        _fail("fresh target must be absent or an ordinary directory")
    entries = sorted(item.name for item in path.iterdir())
    git: dict[str, Any] | None = None
    state = "empty-directory"
    if entries:
        if entries != [".git"]:
            _fail("fresh target directory contains bytes outside an unborn Git repository")
        git = _git_observation(path)
        state = "unborn-git"
    body = {
        "format": OBSERVATION_FORMAT,
        "git": git,
        "parent_identity": _identity(parent_state),
        "schema_version": 2,
        "state": state,
        "target_identity": _identity(target_state),
        "target_uri": path.as_uri(),
    }
    return application_transaction.seal(OBSERVATION_KIND, body)


def validate_fresh_target_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed observation and its content identity."""

    if type(value) is not dict or set(value) != _OBSERVATION_KEYS:
        _fail("fresh target observation fields changed")
    observed = dict(value)
    body = dict(observed)
    supplied = body.pop("id", None)
    if (
        body.get("format") != OBSERVATION_FORMAT
        or body.get("schema_version") != 2
        or type(body.get("schema_version")) is bool
        or body.get("state") not in {"absent", "empty-directory", "unborn-git"}
        or type(body.get("target_uri")) is not str
        or supplied != application_transaction.content_id(OBSERVATION_KIND, body)
    ):
        _fail("fresh target observation identity changed")
    for key in ("parent_identity",):
        identity = body.get(key)
        if (
            type(identity) is not dict
            or set(identity) != {"device", "inode"}
            or any(type(identity[item]) is not int or identity[item] < 0 for item in identity)
        ):
            _fail(f"fresh target {key} changed")
    target_identity = body.get("target_identity")
    if body["state"] == "absent":
        if target_identity is not None or body.get("git") is not None:
            _fail("absent fresh target carries directory or Git identity")
    else:
        if (
            type(target_identity) is not dict
            or set(target_identity) != {"device", "inode"}
            or any(
                type(target_identity[item]) is not int or target_identity[item] < 0
                for item in target_identity
            )
        ):
            _fail("fresh target directory identity changed")
        if body["state"] == "empty-directory" and body.get("git") is not None:
            _fail("empty fresh target unexpectedly carries Git identity")
    if body["state"] == "unborn-git":
        git = body.get("git")
        expected = {
            "exclude_sha256",
            "exclude_size",
            "git_directory_identity",
            "head_state",
            "object_format",
        }
        if type(git) is not dict or set(git) != expected:
            _fail("unborn Git observation fields changed")
        if git.get("head_state") != "unborn" or git.get("object_format") != "sha1":
            _fail("unborn Git identity changed")
        git_identity = git.get("git_directory_identity")
        if (
            type(git_identity) is not dict
            or set(git_identity) != {"device", "inode"}
            or any(
                type(git_identity[item]) is not int or git_identity[item] < 0
                for item in git_identity
            )
        ):
            _fail("unborn Git directory identity changed")
        digest = git.get("exclude_sha256")
        size = git.get("exclude_size")
        if (digest is None) != (size is None):
            _fail("unborn Git exclude identity is incomplete")
        if digest is not None and (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(size) is not int
            or size < 0
        ):
            _fail("unborn Git exclude identity changed")
    return cast(dict[str, Any], observed)


def _journal_path(state_root: Path) -> Path:
    return state_root / "fresh-bootstrap-v2.json"


def _receipt_path(state_root: Path, plan_id: str) -> Path:
    digest = sha256(plan_id.encode("utf-8")).hexdigest()
    return state_root / "bootstrap-receipts" / f"{digest}.json"


def _write_journal(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_replace(
        path,
        application_transaction.canonical_json_bytes(dict(value)) + b"\n",
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    raw = _read_regular(path, label, _MAXIMUM_GIT_METADATA_BYTES)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FreshProjectError(f"cannot decode {label}") from exc
    if type(value) is not dict:
        _fail(f"{label} must be one ordinary object")
    return cast(dict[str, Any], value)


def _validate_state_root(target: Path, state_root: Path) -> None:
    _no_symlink_ancestors(state_root.parent, "fresh-project state root")
    if state_root.is_symlink():
        _fail("fresh-project state root is a symlink")
    state_root.mkdir(parents=True, exist_ok=True)
    _ordinary_directory(state_root, "fresh-project state root")
    target_resolved_parent = target.parent.resolve()
    state_resolved = state_root.resolve()
    if state_resolved == target_resolved_parent / target.name:
        _fail("fresh-project state root cannot be the target")
    try:
        state_resolved.relative_to(target_resolved_parent / target.name)
    except ValueError:
        pass
    else:
        _fail("fresh-project state root cannot be inside the target")


def _exclude_after(before: bytes | None) -> bytes:
    raw = b"" if before is None else before
    if _EXCLUDE_BLOCK in raw:
        return raw
    if raw and not raw.endswith(b"\n"):
        raw += b"\n"
    return raw + _EXCLUDE_BLOCK


def prepare_fresh_target(
    target: Path | str,
    observation: Mapping[str, Any],
    state_root: Path | str,
    *,
    plan_id: str,
) -> dict[str, Any]:
    """Create/bootstrap the exact observed target and retain a recovery journal."""

    reviewed = validate_fresh_target_observation(observation)
    if type(plan_id) is not str or not plan_id:
        _fail("fresh-project bootstrap requires a plan identity")
    path = _absolute(target)
    state = _absolute(state_root)
    _validate_state_root(path, state)
    if observe_fresh_target(path) != reviewed:
        _fail("fresh target changed after preview")
    journal_path = _journal_path(state)
    if journal_path.exists() or journal_path.is_symlink():
        _fail("an interrupted fresh-project bootstrap must be recovered first")
    created_target = reviewed["state"] == "absent"
    created_git = reviewed["state"] != "unborn-git"
    before_exclude: bytes | None = None
    journal: dict[str, Any] = {
        "created_git": created_git,
        "created_target": created_target,
        "exclude_after_base64": None,
        "exclude_before_base64": None,
        "format": BOOTSTRAP_JOURNAL_FORMAT,
        "observation_id": reviewed["id"],
        "phase": "preparing",
        "plan_id": plan_id,
        "schema_version": 2,
        "target_uri": path.as_uri(),
    }
    _atomic_new(
        journal_path,
        application_transaction.canonical_json_bytes(journal) + b"\n",
    )
    try:
        if created_target:
            path.mkdir(mode=0o700)
        else:
            _ordinary_directory(path, "fresh target")
        if created_git:
            result = _git(path, "init", "--quiet", "--initial-branch=main")
            if result.stdout or result.stderr:
                _fail("Git bootstrap emitted unexpected output")
        git_path = path / ".git"
        _ordinary_directory(git_path, "bootstrapped .git")
        exclude_path = git_path / "info/exclude"
        before_exclude = _read_optional_regular(exclude_path, "Git exclude file")
        after_exclude = _exclude_after(before_exclude)
        if after_exclude != before_exclude:
            _atomic_replace(exclude_path, after_exclude)
        marker = {
            "format": "workbench-blueprints-fresh-bootstrap-marker-v2",
            "observation_id": reviewed["id"],
            "plan_id": plan_id,
            "schema_version": 2,
        }
        marker_path = git_path / "workbench-fresh-project-v2.json"
        if marker_path.exists() or marker_path.is_symlink():
            _fail("fresh-project bootstrap marker already exists")
        _atomic_new(
            marker_path,
            application_transaction.canonical_json_bytes(marker) + b"\n",
        )
        journal = {
            **journal,
            "exclude_after_base64": base64.b64encode(after_exclude).decode("ascii"),
            "exclude_before_base64": (
                None
                if before_exclude is None
                else base64.b64encode(before_exclude).decode("ascii")
            ),
            "phase": "bootstrapped",
        }
        _write_journal(journal_path, journal)
        return journal
    except BaseException:
        try:
            restore_fresh_target(path, state, plan_id=plan_id)
        except Exception:
            pass
        raise


def _safe_operation_path(value: Any) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail("fresh-project operation path is not portable")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] in {".git", ".workbench"}
    ):
        _fail("fresh-project operation path is unsafe")
    return path


def verify_bootstrapped_target(
    target: Path | str,
    state_root: Path | str,
    *,
    plan_id: str,
    operations: Sequence[Mapping[str, Any]],
) -> bool:
    """Verify bootstrap ownership while tolerating exact transaction temp files."""

    path = _absolute(target)
    state = _absolute(state_root)
    try:
        journal = _load_json(_journal_path(state), "fresh bootstrap journal")
        if (
            journal.get("format") != BOOTSTRAP_JOURNAL_FORMAT
            or journal.get("schema_version") != 2
            or journal.get("phase") != "bootstrapped"
            or journal.get("plan_id") != plan_id
            or journal.get("target_uri") != path.as_uri()
        ):
            return False
        git = _git_observation(path, allow_transaction_staging=True)
        after = base64.b64decode(journal["exclude_after_base64"], validate=True)
        if (
            git["exclude_sha256"] != sha256(after).hexdigest()
            or git["exclude_size"] != len(after)
        ):
            return False
        rows: dict[PurePosixPath, Mapping[str, Any]] = {}
        allowed_directories: set[PurePosixPath] = set()
        for row in operations:
            relative = _safe_operation_path(row.get("path"))
            if relative in rows:
                return False
            rows[relative] = row
            for parent in relative.parents:
                if parent != PurePosixPath("."):
                    allowed_directories.add(parent)
            final = path.joinpath(*relative.parts)
            if final.exists() or final.is_symlink():
                return False
        for candidate in path.rglob("*"):
            relative = PurePosixPath(candidate.relative_to(path).as_posix())
            if relative.parts[0] == ".git":
                continue
            candidate_state = candidate.lstat()
            if stat.S_ISLNK(candidate_state.st_mode):
                return False
            if stat.S_ISDIR(candidate_state.st_mode):
                if relative not in allowed_directories:
                    return False
                continue
            if not stat.S_ISREG(candidate_state.st_mode):
                return False
            owner_row = next(
                (
                    row
                    for operation_path, row in rows.items()
                    if relative.parent == operation_path.parent
                    and candidate.name.startswith(
                        f".{operation_path.name}.workbench-"
                    )
                    and candidate.name.endswith(".tmp")
                ),
                None,
            )
            if owner_row is None:
                return False
            raw = _read_regular(candidate, "transaction staged file", 4 * 1024 * 1024)
            if (
                sha256(raw).hexdigest() != owner_row.get("after_sha256")
                or len(raw) != owner_row.get("after_size")
            ):
                return False
        return True
    except (FreshProjectError, OSError, ValueError, KeyError):
        return False


def _remove_tree_no_symlinks(root: Path) -> None:
    state = root.lstat()
    if stat.S_ISLNK(state.st_mode):
        _fail("bootstrap cleanup refuses a symbolic link")
    if stat.S_ISREG(state.st_mode):
        root.unlink()
        return
    if not stat.S_ISDIR(state.st_mode):
        _fail("bootstrap cleanup refuses a special file")
    for child in list(root.iterdir()):
        _remove_tree_no_symlinks(child)
    root.rmdir()


def _load_bootstrap_journal(
    target: Path, state_root: Path, plan_id: str
) -> dict[str, Any]:
    journal = _load_json(_journal_path(state_root), "fresh bootstrap journal")
    expected = {
        "created_git",
        "created_target",
        "exclude_after_base64",
        "exclude_before_base64",
        "format",
        "observation_id",
        "phase",
        "plan_id",
        "schema_version",
        "target_uri",
    }
    if (
        set(journal) != expected
        or journal.get("format") != BOOTSTRAP_JOURNAL_FORMAT
        or journal.get("schema_version") != 2
        or journal.get("plan_id") != plan_id
        or journal.get("target_uri") != target.as_uri()
        or journal.get("phase") not in {"preparing", "bootstrapped"}
        or type(journal.get("created_git")) is not bool
        or type(journal.get("created_target")) is not bool
    ):
        _fail("fresh bootstrap journal fields changed")
    return journal


def restore_fresh_target(
    target: Path | str,
    state_root: Path | str,
    *,
    plan_id: str,
) -> dict[str, Any]:
    """Restore only bootstrap-owned Git metadata after project bytes roll back."""

    path = _absolute(target)
    state = _absolute(state_root)
    journal = _load_bootstrap_journal(path, state, plan_id)
    if path.exists() or path.is_symlink():
        _ordinary_directory(path, "bootstrap recovery target")
        marker_path = path / ".git/workbench-fresh-project-v2.json"
        if journal["phase"] == "preparing" and not marker_path.exists():
            entries = list(path.iterdir())
            if entries and not (
                journal["created_git"] and len(entries) == 1 and entries[0].name == ".git"
            ):
                _fail("incomplete bootstrap contains bytes without an ownership marker")
            if entries:
                _remove_tree_no_symlinks(entries[0])
            if journal["created_target"]:
                path.rmdir()
            _journal_path(state).unlink()
            return {
                "format": "workbench-blueprints-fresh-bootstrap-recovery-v2",
                "outcome": "restored",
                "plan_id": plan_id,
                "schema_version": 2,
                "target_uri": path.as_uri(),
            }
        marker = _load_json(marker_path, "fresh bootstrap marker")
        if marker != {
            "format": "workbench-blueprints-fresh-bootstrap-marker-v2",
            "observation_id": journal["observation_id"],
            "plan_id": plan_id,
            "schema_version": 2,
        }:
            _fail("fresh bootstrap marker identity changed")
        for candidate in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if candidate == path / ".git" or (path / ".git") in candidate.parents:
                continue
            candidate_state = candidate.lstat()
            if stat.S_ISLNK(candidate_state.st_mode) or not stat.S_ISDIR(candidate_state.st_mode):
                _fail("bootstrap recovery found non-owned project bytes")
            candidate.rmdir()
        if journal["created_git"]:
            _remove_tree_no_symlinks(path / ".git")
        else:
            before_encoded = journal["exclude_before_base64"]
            exclude_path = path / ".git/info/exclude"
            if before_encoded is None:
                exclude_path.unlink(missing_ok=True)
            else:
                _atomic_replace(
                    exclude_path,
                    base64.b64decode(before_encoded, validate=True),
                )
            marker_path.unlink()
        if journal["created_target"]:
            path.rmdir()
    _journal_path(state).unlink()
    return {
        "format": "workbench-blueprints-fresh-bootstrap-recovery-v2",
        "outcome": "restored",
        "plan_id": plan_id,
        "schema_version": 2,
        "target_uri": path.as_uri(),
    }


def finalize_fresh_target(
    target: Path | str,
    state_root: Path | str,
    *,
    plan_id: str,
) -> dict[str, Any]:
    """Seal and retain a receipt after the profile transaction is applied."""

    path = _absolute(target)
    state = _absolute(state_root)
    journal = _load_bootstrap_journal(path, state, plan_id)
    marker_path = path / ".git/workbench-fresh-project-v2.json"
    marker = _load_json(marker_path, "fresh bootstrap marker")
    if marker.get("plan_id") != plan_id or marker.get("observation_id") != journal["observation_id"]:
        _fail("fresh bootstrap marker identity changed")
    marker_path.unlink()
    body = {
        "created_git": journal["created_git"],
        "created_target": journal["created_target"],
        "format": BOOTSTRAP_RECEIPT_FORMAT,
        "kind": BOOTSTRAP_RECEIPT_KIND,
        "observation_id": journal["observation_id"],
        "plan_id": plan_id,
        "schema_version": 2,
        "state": "applied",
        "target_uri": path.as_uri(),
    }
    receipt = application_transaction.seal(BOOTSTRAP_RECEIPT_KIND, body)
    receipts = state / "bootstrap-receipts"
    receipts.mkdir(exist_ok=True)
    _ordinary_directory(receipts, "bootstrap receipt directory")
    destination = _receipt_path(state, plan_id)
    if destination.exists() or destination.is_symlink():
        if _load_json(destination, "bootstrap receipt") != receipt:
            _fail("retained bootstrap receipt identity changed")
    else:
        _atomic_new(
            destination,
            application_transaction.canonical_json_bytes(receipt) + b"\n",
        )
    _journal_path(state).unlink()
    return receipt


def load_retained_bootstrap_receipt(
    state_root: Path | str, *, plan_id: str
) -> dict[str, Any]:
    """Load the exact retained bootstrap receipt for a completed plan."""

    state = _absolute(state_root)
    value = _load_json(_receipt_path(state, plan_id), "bootstrap receipt")
    body = dict(value)
    supplied = body.pop("id", None)
    if (
        value.get("format") != BOOTSTRAP_RECEIPT_FORMAT
        or value.get("kind") != BOOTSTRAP_RECEIPT_KIND
        or value.get("schema_version") != 2
        or value.get("plan_id") != plan_id
        or supplied != application_transaction.content_id(BOOTSTRAP_RECEIPT_KIND, body)
    ):
        _fail("retained bootstrap receipt identity changed")
    return value
