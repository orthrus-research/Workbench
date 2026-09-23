"""Generic exact-byte application transactions owned by Blueprints.

Callers validate their own plan and receipt formats.  This module owns bounded
regular-file access, durable token-owned locking, exact preflight and byte
replacement, retained history, crash recovery, mode preservation, and rollback
that refuses to overwrite later edits.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Any, Callable, Mapping, NoReturn, Sequence, cast
import uuid
from workbench_api.host_filesystem import fsync_directory as _fsync_directory


MAXIMUM_OPERATION_BYTES = 4 * 1024 * 1024


class ApplicationTransactionError(ValueError):
    """An exact-byte application transaction is invalid or unsafe."""


def _fail(message: str) -> NoReturn:
    raise ApplicationTransactionError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_id(kind: str, body: Mapping[str, Any]) -> str:
    return f"{kind}:sha256:{sha256(canonical_json_bytes(dict(body))).hexdigest()}"


def seal(kind: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return {**body, "id": content_id(kind, body)}


def _relative(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} is not a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        _fail(f"{label} is not a safe normalized path")
    return path


def _absolute_path(value: Path | str) -> Path:
    """Make a path absolute without hiding a symlink at the final component."""

    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _ordinary_directory(path: Path, label: str) -> None:
    try:
        state = path.lstat()
    except OSError as exc:
        raise ApplicationTransactionError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail(f"{label} is not an ordinary directory")


def _rooted_target(
    root: Path,
    relative: PurePosixPath,
    label: str,
    *,
    create_parents: bool = False,
) -> Path:
    """Resolve one lexical descendant while rejecting symlinked ancestors."""

    _ordinary_directory(root, f"{label} root")

    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        try:
            parent_state = parent.lstat()
        except FileNotFoundError:
            if not create_parents:
                continue
            try:
                parent.mkdir(mode=0o755)
            except FileExistsError:
                pass
            try:
                parent_state = parent.lstat()
            except OSError as exc:
                raise ApplicationTransactionError(
                    f"cannot create {label} parent"
                ) from exc
        except OSError as exc:
            raise ApplicationTransactionError(
                f"cannot inspect {label} parent"
            ) from exc
        if stat.S_ISLNK(parent_state.st_mode) or not stat.S_ISDIR(
            parent_state.st_mode
        ):
            _fail(f"{label} traverses a symlink or non-directory ancestor")
    return root.joinpath(*relative.parts)


def _read_regular(path: Path, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ApplicationTransactionError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"{label} is not a regular non-symlink file")
    if before.st_size > MAXIMUM_OPERATION_BYTES:
        _fail(f"{label} exceeds its byte bound")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise ApplicationTransactionError(f"cannot read {label}") from exc
    identity = lambda row: (
        row.st_dev,
        row.st_ino,
        row.st_mode,
        row.st_size,
        row.st_mtime_ns,
    )
    if identity(before) != identity(after) or len(raw) != before.st_size:
        _fail(f"{label} changed while being read")
    return raw


def _atomic_new(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, mode)
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            _fail(f"retained qualification artifact already exists: {path}")
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _atomic_replace(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    """Replace one mutable operational record and fsync its directory."""

    _ordinary_directory(path.parent, "transaction record parent")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _read_json_record(path: Path, label: str) -> dict[str, Any]:
    raw = _read_regular(path, label)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationTransactionError(f"cannot decode {label}") from exc
    if type(value) is not dict:
        _fail(f"{label} must be one ordinary object")
    return cast(dict[str, Any], value)


def _acquire_transaction_lock(
    path: Path,
    binding: str,
) -> tuple[int, str, tuple[int, int]] | None:
    """Create one token-owned lock without following a replaced pathname."""

    _ordinary_directory(path.parent, "transaction lock parent")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        return None
    token = uuid.uuid4().hex
    state = os.fstat(descriptor)
    identity = (state.st_dev, state.st_ino)
    try:
        raw = canonical_json_bytes(
            {
                "binding": binding,
                "format": "workbench-blueprints-m2-transaction-lock-v1",
                "pid": os.getpid(),
                "token": token,
            }
        )
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written < 1:
                raise OSError("transaction lock write made no progress")
            offset += written
        os.fsync(descriptor)
        return descriptor, token, identity
    except BaseException:
        _release_transaction_lock(path, descriptor, token, identity)
        raise


def _release_transaction_lock(
    path: Path,
    descriptor: int,
    token: str,
    identity: tuple[int, int],
) -> None:
    """Remove only the same visible lock created by this transaction."""

    os.close(descriptor)
    visible: int | None = None
    remove = False
    try:
        visible = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        state = os.fstat(visible)
        if (state.st_dev, state.st_ino) != identity or state.st_size > 16 * 1024:
            return
        raw = os.read(visible, 16 * 1024 + 1)
        value = json.loads(raw.decode("utf-8", errors="strict"))
        if type(value) is dict and value.get("token") == token:
            remove = True
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    finally:
        if visible is not None:
            os.close(visible)
    if remove:
        try:
            state = path.lstat()
            if (state.st_dev, state.st_ino) == identity:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _close_transaction_lock(descriptor: int) -> None:
    """Close a held lock while deliberately preserving its recovery marker."""

    try:
        os.close(descriptor)
    except OSError:
        pass


def _mark_transaction_lock_recoverable(path: Path, binding: str) -> None:
    """Keep the workspace blocked without pretending the caller is still live."""

    _atomic_replace(
        path,
        canonical_json_bytes(
            {
                "binding": binding,
                "format": "workbench-blueprints-m2-transaction-lock-v1",
                "pid": 2_147_483_647,
                "token": uuid.uuid4().hex,
            }
        ),
    )


def _transaction_lock_record(
    path: Path,
) -> tuple[dict[str, Any], bytes, tuple[int, int]]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise ApplicationTransactionError(
            "cannot inspect the interrupted transaction lock"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024:
            _fail("interrupted transaction lock is not a bounded regular file")
        raw = os.read(descriptor, 16 * 1024 + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != before.st_size
    ):
        _fail("interrupted transaction lock changed while being read")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationTransactionError(
            "interrupted transaction lock is invalid"
        ) from exc
    if (
        type(value) is not dict
        or set(value) != {"binding", "format", "pid", "token"}
        or value.get("format") != "workbench-blueprints-m2-transaction-lock-v1"
        or type(value.get("binding")) is not str
        or type(value.get("pid")) is not int
        or type(value.get("pid")) is bool
        or value["pid"] < 1
        or type(value.get("token")) is not str
        or len(value["token"]) != 32
        or any(character not in "0123456789abcdef" for character in value["token"])
    ):
        _fail("interrupted transaction lock fields are invalid")
    return cast(dict[str, Any], value), raw, (before.st_dev, before.st_ino)


def _process_is_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Hosts without a reliable zero-signal probe fail closed.
        return True
    return True


def _quarantine_stale_transaction_lock(
    path: Path,
    state_root: Path,
    *,
    expected_binding: str,
) -> dict[str, Any] | None:
    """Move one dead, plan-bound lock into the plan's operational history."""

    if not path.exists() and not path.is_symlink():
        return None
    value, raw, identity = _transaction_lock_record(path)
    if value["binding"] != expected_binding:
        _fail("interrupted transaction lock belongs to another plan")
    if _process_is_alive(value["pid"]):
        _fail("the transaction process is still running")
    quarantine = _rooted_target(
        state_root,
        PurePosixPath("stale-locks", f"{value['token']}.json"),
        "stale transaction lock",
        create_parents=True,
    )
    retained_raw = raw + (b"" if raw.endswith(b"\n") else b"\n")
    if quarantine.exists():
        if _read_regular(quarantine, "stale transaction lock") != retained_raw:
            _fail("stale transaction lock quarantine is inconsistent")
    else:
        _atomic_new(quarantine, retained_raw)
    try:
        visible = path.lstat()
    except OSError as exc:
        raise ApplicationTransactionError(
            "interrupted transaction lock disappeared during recovery"
        ) from exc
    if (visible.st_dev, visible.st_ino) != identity:
        _fail("interrupted transaction lock was replaced during recovery")
    path.unlink()
    _fsync_directory(path.parent)
    return value


def _retain_object(state_root: Path, raw: bytes) -> dict[str, Any]:
    digest = sha256(raw).hexdigest()
    path = _rooted_target(
        state_root,
        PurePosixPath("objects", digest[:2], digest[2:]),
        "history object",
        create_parents=True,
    )
    if path.exists():
        if path.is_symlink() or not path.is_file() or _read_regular(path, "history object") != raw:
            _fail("retained history object is corrupt")
    else:
        _atomic_new(path, raw)
    return {"sha256": digest, "size": len(raw)}


def _target_bytes(root: Path, row: Mapping[str, Any], prefix: str) -> bytes | None:
    relative = _relative(row["path"], "transaction target")
    target = _rooted_target(root, relative, "transaction target")
    expected_encoded = row[f"{prefix}_base64"]
    if expected_encoded is None:
        if target.exists() or target.is_symlink():
            _fail(f"transaction target expected absent: {relative}")
        return None
    expected = base64.b64decode(expected_encoded, validate=True)
    observed = _read_regular(target, f"transaction target {relative}")
    if observed != expected:
        _fail(f"transaction target differs from {prefix} bytes: {relative}")
    return observed


def _target_mode(root: Path, row: Mapping[str, Any], label: str) -> int:
    relative = _relative(row["path"], label)
    target = _rooted_target(root, relative, label)
    try:
        state = target.lstat()
    except OSError as exc:
        raise ApplicationTransactionError(
            f"cannot inspect {label} mode"
        ) from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        _fail(f"{label} mode owner is not a regular file")
    return stat.S_IMODE(state.st_mode)


def _write_staged(
    root: Path,
    relative: PurePosixPath,
    raw: bytes,
    *,
    mode: int = 0o644,
    transaction_token: str | None = None,
) -> Path:
    path = _rooted_target(
        root,
        relative,
        "transaction target",
        create_parents=True,
    )
    token = "" if transaction_token is None else f"{transaction_token}-"
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.workbench-{token}",
        suffix=".tmp",
        dir=path.parent,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(name, mode)
    return Path(name)


def _success_receipt(
    value: Mapping[str, Any],
    history_objects: Sequence[Mapping[str, Any]],
    *,
    receipt_format: str,
    receipt_kind: str,
    receipt_content_kind: str,
    success_mutation_state: str,
) -> dict[str, Any]:
    body = {
        "authority_boundary": value["authority_boundary"],
        "diagnostic_code": None,
        "format": receipt_format,
        "history_objects": sorted(
            (dict(row) for row in history_objects),
            key=lambda row: (row["sha256"], row["size"]),
        ),
        "kind": receipt_kind,
        "mutation_state": success_mutation_state,
        "plan_id": value["id"],
        "rollback": "available-while-after-bytes-match",
        "schema_version": 1,
        "state": "applied",
    }
    return seal(receipt_content_kind, body)


def _transaction_journal(
    *,
    plan_id: str,
    workspace_uri: str,
    transaction_token: str,
    phase: str,
    attempted_ordinals: Sequence[int],
    staged_files: Sequence[Mapping[str, Any]],
    expected_receipt_id: str | None,
) -> dict[str, Any]:
    return {
        "attempted_ordinals": list(attempted_ordinals),
        "expected_receipt_id": expected_receipt_id,
        "format": "workbench-blueprints-m2-active-transaction-v2",
        "phase": phase,
        "plan_id": plan_id,
        "schema_version": 2,
        "staged_files": [dict(row) for row in staged_files],
        "transaction_token": transaction_token,
        "workspace_uri": workspace_uri,
    }


def _write_transaction_journal(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_replace(path, canonical_json_bytes(dict(value)) + b"\n")


def _best_effort_transaction_phase(
    path: Path,
    value: Mapping[str, Any],
    phase: str,
) -> dict[str, Any]:
    updated = {**dict(value), "phase": phase}
    try:
        _write_transaction_journal(path, updated)
    except BaseException:
        # The last durable attempted-ordinal record is still sufficient for
        # recovery.  Never skip byte restoration because a phase label failed.
        pass
    return updated


def _validated_transaction_journal(
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    workspace_uri: str,
) -> dict[str, Any]:
    journal = dict(value)
    if (
        set(journal)
        != {
            "attempted_ordinals",
            "expected_receipt_id",
            "format",
            "phase",
            "plan_id",
            "schema_version",
            "staged_files",
            "transaction_token",
            "workspace_uri",
        }
        or journal.get("format")
        != "workbench-blueprints-m2-active-transaction-v2"
        or type(journal.get("schema_version")) is not int
        or journal.get("schema_version") != 2
        or journal.get("plan_id") != plan["id"]
        or journal.get("workspace_uri") != workspace_uri
        or journal.get("phase")
        not in {
            "prepared",
            "applying",
            "applied",
            "recovering",
            "rolling-back",
            "review-required",
        }
        or type(journal.get("transaction_token")) is not str
        or len(journal["transaction_token"]) != 32
        or any(
            character not in "0123456789abcdef"
            for character in journal["transaction_token"]
        )
    ):
        _fail("interrupted transaction journal identity changed")
    attempted = journal.get("attempted_ordinals")
    if (
        type(attempted) is not list
        or any(type(row) is not int for row in attempted)
        or attempted != list(range(len(attempted)))
        or len(attempted) > len(plan["operations"])
    ):
        _fail("interrupted transaction attempt order changed")
    expected_receipt_id = journal.get("expected_receipt_id")
    if expected_receipt_id is not None and (
        type(expected_receipt_id) is not str
        or ":sha256:" not in expected_receipt_id
        or len(expected_receipt_id.rsplit(":sha256:", 1)[-1]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_receipt_id.rsplit(":sha256:", 1)[-1]
        )
    ):
        _fail("interrupted transaction receipt identity changed")
    staged_files = journal.get("staged_files")
    if type(staged_files) is not list or len(staged_files) > len(plan["operations"]):
        _fail("interrupted transaction staged-file list changed")
    observed_ordinals: list[int] = []
    for row in staged_files:
        if type(row) is not dict or set(row) != {"ordinal", "path"}:
            _fail("interrupted transaction staged-file entry changed")
        ordinal = row.get("ordinal")
        if type(ordinal) is not int or type(ordinal) is bool:
            _fail("interrupted transaction staged-file ordinal changed")
        if ordinal < 0 or ordinal >= len(plan["operations"]):
            _fail("interrupted transaction staged-file ordinal is out of range")
        relative = _relative(row.get("path"), "interrupted staged file")
        target_relative = _relative(
            plan["operations"][ordinal]["path"], "interrupted transaction target"
        )
        if relative.parent != target_relative.parent:
            _fail("interrupted staged file left its transaction target directory")
        expected_prefix = (
            f".{target_relative.name}.workbench-{journal['transaction_token']}-"
        )
        if not relative.name.startswith(expected_prefix) or not relative.name.endswith(
            ".tmp"
        ):
            _fail("interrupted staged file name is not transaction-owned")
        observed_ordinals.append(ordinal)
    if observed_ordinals != list(range(len(observed_ordinals))):
        _fail("interrupted staged-file order changed")
    return journal


def _cleanup_transaction_staged_files(
    root: Path,
    journal: Mapping[str, Any],
) -> None:
    for row in journal["staged_files"]:
        relative = _relative(row["path"], "interrupted staged file")
        path = _rooted_target(root, relative, "interrupted staged file")
        try:
            state = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if stat.S_ISREG(state.st_mode) and not stat.S_ISLNK(state.st_mode):
            path.unlink(missing_ok=True)


def _cleanup_token_staged_files(
    root: Path,
    plan: Mapping[str, Any],
    transaction_token: str,
) -> None:
    """Remove only temp names carrying one interrupted transaction token."""

    for row in plan["operations"]:
        relative = _relative(row["path"], "transaction target")
        target = _rooted_target(root, relative, "transaction target")
        prefix = f".{target.name}.workbench-{transaction_token}-"
        try:
            candidates = list(target.parent.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            if not candidate.name.startswith(prefix) or not candidate.name.endswith(
                ".tmp"
            ):
                continue
            try:
                state = candidate.lstat()
            except OSError:
                continue
            if stat.S_ISREG(state.st_mode) and not stat.S_ISLNK(state.st_mode):
                candidate.unlink(missing_ok=True)


def apply_application_transaction(
    workspace: Path | str,
    value: Mapping[str, Any],
    state_root: Path | str,
    *,
    receipt_format: str,
    receipt_kind: str,
    receipt_content_kind: str,
    success_mutation_state: str,
    fail_after_ordinal: int | None = None,
    after_preflight: Callable[[Path], None] | None = None,
    source_preflight: Callable[[Path, Mapping[str, Any]], bool] | None = None,
    transaction_lock: Path | str | None = None,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Apply one already owner-validated plan with shared transaction mechanics.

    The caller owns the plan and receipt formats.  This helper owns only the
    atomic write, exact revalidation, retained history, lock, and immediate
    partial-failure rollback mechanics shared by disposable and admitted
    direct-checkout application.
    """

    root = _absolute_path(workspace)
    state = _absolute_path(state_root)
    value = dict(value)
    _ordinary_directory(root, "disposable workspace")
    if state.is_symlink():
        _fail("transaction state root is a symlink")
    root_resolved = root.resolve()
    state_resolved = state.resolve()
    if (
        state_resolved == root_resolved
        or state_resolved.is_relative_to(root_resolved)
        or root_resolved.is_relative_to(state_resolved)
    ):
        _fail("transaction state cannot overlap the disposable workspace")
    state.mkdir(parents=True, exist_ok=True)
    _ordinary_directory(state, "transaction state root")
    lock_path = (
        state / "active-transaction.lock"
        if transaction_lock is None
        else _absolute_path(transaction_lock)
    )
    held_lock = _acquire_transaction_lock(lock_path, value["id"])
    if held_lock is None:
        body = {
            "authority_boundary": value["authority_boundary"],
            "diagnostic_code": "BLUEPRINTS_M2_TRANSACTION_LOCKED",
            "format": receipt_format,
            "kind": receipt_kind,
            "mutation_state": "not-started",
            "plan_id": value["id"],
            "rollback": "not-needed",
            "schema_version": 1,
            "state": "rejected",
        }
        return seal(receipt_content_kind, body)
    lock_descriptor, lock_token, lock_identity = held_lock
    journal_path = state / "active-transaction.json"
    prepared_receipt_path = state / "prepared-receipt.json"
    staged: list[Path] = []
    staged_files: list[dict[str, Any]] = []
    attempted: list[Mapping[str, Any]] = []
    history_objects: list[dict[str, Any]] = []
    journal: dict[str, Any] | None = None
    own_journal = False
    preserve_recovery = False
    try:
        if (
            journal_path.exists()
            or journal_path.is_symlink()
            or prepared_receipt_path.exists()
            or prepared_receipt_path.is_symlink()
        ):
            _fail("an interrupted transaction must be recovered before applying")
        journal = _transaction_journal(
            plan_id=value["id"],
            workspace_uri=root_resolved.as_uri(),
            transaction_token=lock_token,
            phase="prepared",
            attempted_ordinals=[],
            staged_files=[],
            expected_receipt_id=None,
        )
        _atomic_new(journal_path, canonical_json_bytes(journal) + b"\n")
        _fsync_directory(state)
        own_journal = True
        if source_preflight is None:
            _fail("application transaction requires owner source preflight")
        source_matches = source_preflight(root, value)
        if not source_matches:
            body = {
                "authority_boundary": value["authority_boundary"],
                "diagnostic_code": "BLUEPRINTS_M2_STALE_PLAN",
                "format": receipt_format,
                "kind": receipt_kind,
                "mutation_state": "not-started",
                "plan_id": value["id"],
                "rollback": "not-needed",
                "schema_version": 1,
                "state": "rejected",
            }
            return seal(receipt_content_kind, body)
        for row in value["operations"]:
            before = _target_bytes(root, row, "before")
            mode = 0o644 if before is None else _target_mode(
                root,
                row,
                "transaction target",
            )
            if before is not None:
                history_objects.append(_retain_object(state, before))
            after = base64.b64decode(row["after_base64"], validate=True)
            history_objects.append(_retain_object(state, after))
            relative = _relative(row["path"], "transaction target")
            temporary = _write_staged(
                root,
                relative,
                after,
                mode=mode,
                transaction_token=lock_token,
            )
            staged.append(temporary)
            staged_files.append(
                {
                    "ordinal": row["ordinal"],
                    "path": temporary.resolve().relative_to(root_resolved).as_posix(),
                }
            )
            journal = _transaction_journal(
                plan_id=value["id"],
                workspace_uri=root_resolved.as_uri(),
                transaction_token=lock_token,
                phase="prepared",
                attempted_ordinals=[],
                staged_files=staged_files,
                expected_receipt_id=None,
            )
            _write_transaction_journal(journal_path, journal)
        receipt = _success_receipt(
            value,
            history_objects,
            receipt_format=receipt_format,
            receipt_kind=receipt_kind,
            receipt_content_kind=receipt_content_kind,
            success_mutation_state=success_mutation_state,
        )
        _atomic_new(
            prepared_receipt_path,
            canonical_json_bytes(receipt) + b"\n",
        )
        _fsync_directory(state)
        journal = _transaction_journal(
            plan_id=value["id"],
            workspace_uri=root_resolved.as_uri(),
            transaction_token=lock_token,
            phase="prepared",
            attempted_ordinals=[],
            staged_files=staged_files,
            expected_receipt_id=receipt["id"],
        )
        _write_transaction_journal(journal_path, journal)
        if after_preflight is not None:
            after_preflight(root)
        if source_preflight is not None and not source_preflight(root, value):
            body = {
                "authority_boundary": value["authority_boundary"],
                "diagnostic_code": "BLUEPRINTS_M2_STALE_PLAN",
                "format": receipt_format,
                "kind": receipt_kind,
                "mutation_state": "not-started",
                "plan_id": value["id"],
                "rollback": "not-needed",
                "schema_version": 1,
                "state": "rejected",
            }
            return seal(receipt_content_kind, body)
        try:
            for row, temporary in zip(value["operations"], staged, strict=True):
                _target_bytes(root, row, "before")
                relative = _relative(row["path"], "transaction target")
                target = _rooted_target(
                    root,
                    relative,
                    "transaction target",
                    create_parents=True,
                )
                if row["before_base64"] is not None:
                    os.chmod(
                        temporary,
                        _target_mode(root, row, "transaction target"),
                    )
                attempted.append(row)
                journal = _transaction_journal(
                    plan_id=value["id"],
                    workspace_uri=root_resolved.as_uri(),
                    transaction_token=lock_token,
                    phase="applying",
                    attempted_ordinals=[item["ordinal"] for item in attempted],
                    staged_files=staged_files,
                    expected_receipt_id=receipt["id"],
                )
                # Persist ownership before replacement so recovery never
                # guesses whether a path was transaction-owned.
                _write_transaction_journal(journal_path, journal)
                os.replace(temporary, target)
                _fsync_directory(target.parent)
                _target_bytes(root, row, "after")
                if fail_after_ordinal == row["ordinal"]:
                    raise OSError("injected bounded partial failure")
            for row in value["operations"]:
                _target_bytes(root, row, "after")
            journal = _transaction_journal(
                plan_id=value["id"],
                workspace_uri=root_resolved.as_uri(),
                transaction_token=lock_token,
                phase="applied",
                attempted_ordinals=[item["ordinal"] for item in attempted],
                staged_files=staged_files,
                expected_receipt_id=receipt["id"],
            )
            _write_transaction_journal(journal_path, journal)
            if commit_receipt is not None:
                commit_receipt(receipt)
        except BaseException as exc:
            rollback_state = "succeeded"
            if journal is not None:
                journal = _best_effort_transaction_phase(
                    journal_path,
                    journal,
                    "rolling-back",
                )
            for row in reversed(attempted):
                try:
                    try:
                        _target_bytes(root, row, "after")
                    except ApplicationTransactionError:
                        try:
                            _target_bytes(root, row, "before")
                        except ApplicationTransactionError:
                            rollback_state = "blocked-by-later-edit"
                        continue
                    relative = _relative(row["path"], "rollback target")
                    target = _rooted_target(
                        root,
                        relative,
                        "rollback target",
                        create_parents=True,
                    )
                    before_encoded = row["before_base64"]
                    if before_encoded is None:
                        target.unlink()
                    else:
                        before = base64.b64decode(before_encoded, validate=True)
                        replacement = _write_staged(
                            root,
                            relative,
                            before,
                            mode=_target_mode(root, row, "rollback target"),
                            transaction_token=lock_token,
                        )
                        os.replace(replacement, target)
                    _fsync_directory(target.parent)
                    _target_bytes(root, row, "before")
                except Exception:
                    rollback_state = "blocked-by-later-edit"
            if rollback_state != "succeeded":
                preserve_recovery = True
                if journal is not None:
                    journal = _best_effort_transaction_phase(
                        journal_path,
                        journal,
                        "review-required",
                    )
            if not isinstance(exc, Exception):
                raise
            body = {
                "authority_boundary": value["authority_boundary"],
                "diagnostic_code": (
                    "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
                    if rollback_state == "succeeded"
                    else "BLUEPRINTS_M2_ROLLBACK_REQUIRES_REVIEW"
                ),
                "failure_kind": type(exc).__name__,
                "format": receipt_format,
                "history_objects": sorted(
                    history_objects,
                    key=lambda row: (row["sha256"], row["size"]),
                ),
                "kind": receipt_kind,
                "mutation_state": "restored" if rollback_state == "succeeded" else "indeterminate",
                "plan_id": value["id"],
                "rollback": rollback_state,
                "schema_version": 1,
                "state": "rejected",
            }
            return seal(receipt_content_kind, body)
        return receipt
    finally:
        if own_journal and not preserve_recovery:
            for temporary in staged:
                temporary.unlink(missing_ok=True)
            prepared_receipt_path.unlink(missing_ok=True)
            journal_path.unlink(missing_ok=True)
            _fsync_directory(state)
        if preserve_recovery:
            _close_transaction_lock(lock_descriptor)
            _mark_transaction_lock_recoverable(lock_path, value["id"])
        else:
            _release_transaction_lock(
                lock_path,
                lock_descriptor,
                lock_token,
                lock_identity,
            )


def recover_application_transaction(
    workspace: Path | str,
    value: Mapping[str, Any],
    state_root: Path | str,
    *,
    receipt_format: str,
    receipt_kind: str,
    receipt_content_kind: str,
    success_mutation_state: str,
    transaction_lock: Path | str | None = None,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Recover one interrupted application without guessing path ownership.

    The journal records an attempted ordinal before every atomic replacement.
    Recovery therefore either finalizes an exact all-after receipt, restores
    only exact-after attempted rows, or performs no writes when any attempted
    path contains bytes owned by somebody else.
    """

    root = _absolute_path(workspace)
    state = _absolute_path(state_root)
    plan = dict(value)
    _ordinary_directory(root, "recovery workspace")
    _ordinary_directory(state, "recovery transaction state")
    root_resolved = root.resolve()
    state_resolved = state.resolve()
    if (
        state_resolved == root_resolved
        or state_resolved.is_relative_to(root_resolved)
        or root_resolved.is_relative_to(state_resolved)
    ):
        _fail("recovery transaction state cannot overlap the workspace")
    lock_path = (
        state / "active-transaction.lock"
        if transaction_lock is None
        else _absolute_path(transaction_lock)
    )
    stale = _quarantine_stale_transaction_lock(
        lock_path,
        state,
        expected_binding=plan["id"],
    )
    held_lock = _acquire_transaction_lock(lock_path, plan["id"])
    if held_lock is None:
        _fail("another transaction acquired the workspace during recovery")
    lock_descriptor, lock_token, lock_identity = held_lock
    journal_path = state / "active-transaction.json"
    prepared_receipt_path = state / "prepared-receipt.json"
    preserve_recovery = False
    journal: dict[str, Any] | None = None
    try:
        if not journal_path.exists() or journal_path.is_symlink():
            if stale is None:
                _fail("no interrupted transaction is available to recover")
            _cleanup_token_staged_files(root, plan, stale["token"])
            prepared_receipt_path.unlink(missing_ok=True)
            _fsync_directory(state)
            return {
                "application_receipt": None,
                "attempted_ordinals": [],
                "diagnostic_code": "BLUEPRINTS_M2_INTERRUPTED_BEFORE_MUTATION",
                "outcome": "restored",
                "workspace_mutated": False,
            }
        journal = _validated_transaction_journal(
            _read_json_record(journal_path, "interrupted transaction journal"),
            plan=plan,
            workspace_uri=root_resolved.as_uri(),
        )
        journal = {
            **journal,
            "phase": "recovering",
        }
        _write_transaction_journal(journal_path, journal)

        expected_history = [
            {
                "sha256": row[f"{prefix}_sha256"],
                "size": row[f"{prefix}_size"],
            }
            for row in plan["operations"]
            for prefix in ("before", "after")
            if row[f"{prefix}_base64"] is not None
        ]
        expected_receipt = _success_receipt(
            plan,
            expected_history,
            receipt_format=receipt_format,
            receipt_kind=receipt_kind,
            receipt_content_kind=receipt_content_kind,
            success_mutation_state=success_mutation_state,
        )
        prepared_receipt: dict[str, Any] | None = None
        if prepared_receipt_path.exists() and not prepared_receipt_path.is_symlink():
            candidate = _read_json_record(
                prepared_receipt_path,
                "prepared transaction receipt",
            )
            if (
                candidate == expected_receipt
                and journal.get("expected_receipt_id") == expected_receipt["id"]
            ):
                prepared_receipt = candidate

        attempted_ordinals = cast(list[int], journal["attempted_ordinals"])
        classifications: dict[int, str] = {}
        for ordinal in attempted_ordinals:
            row = plan["operations"][ordinal]
            try:
                _target_bytes(root, row, "after")
                classifications[ordinal] = "after"
                continue
            except ApplicationTransactionError:
                pass
            try:
                _target_bytes(root, row, "before")
                classifications[ordinal] = "before"
            except ApplicationTransactionError:
                classifications[ordinal] = "other"

        if any(value == "other" for value in classifications.values()):
            preserve_recovery = True
            journal = _best_effort_transaction_phase(
                journal_path,
                journal,
                "review-required",
            )
            return {
                "application_receipt": None,
                "attempted_ordinals": attempted_ordinals,
                "diagnostic_code": "BLUEPRINTS_M2_RECOVERY_REQUIRES_REVIEW",
                "outcome": "review-required",
                "workspace_mutated": False,
            }

        all_ordinals = list(range(len(plan["operations"])))
        if (
            attempted_ordinals == all_ordinals
            and all(classifications[row] == "after" for row in attempted_ordinals)
            and prepared_receipt is not None
        ):
            try:
                _verify_retained_history(state, prepared_receipt)
                if commit_receipt is not None:
                    commit_receipt(prepared_receipt)
            except Exception:
                preserve_recovery = True
                journal = _best_effort_transaction_phase(
                    journal_path,
                    journal,
                    "review-required",
                )
                raise
            _cleanup_transaction_staged_files(root, journal)
            prepared_receipt_path.unlink(missing_ok=True)
            journal_path.unlink(missing_ok=True)
            _fsync_directory(state)
            return {
                "application_receipt": prepared_receipt,
                "attempted_ordinals": attempted_ordinals,
                "diagnostic_code": None,
                "outcome": "applied",
                "workspace_mutated": False,
            }

        mutated = False
        try:
            journal = {**journal, "phase": "rolling-back"}
            _write_transaction_journal(journal_path, journal)
            for ordinal in reversed(attempted_ordinals):
                if classifications[ordinal] != "after":
                    continue
                row = plan["operations"][ordinal]
                relative = _relative(row["path"], "recovery target")
                target = _rooted_target(
                    root,
                    relative,
                    "recovery target",
                    create_parents=True,
                )
                before_encoded = row["before_base64"]
                if before_encoded is None:
                    target.unlink()
                else:
                    before = base64.b64decode(before_encoded, validate=True)
                    replacement = _write_staged(
                        root,
                        relative,
                        before,
                        mode=_target_mode(root, row, "recovery target"),
                        transaction_token=lock_token,
                    )
                    os.replace(replacement, target)
                _fsync_directory(target.parent)
                _target_bytes(root, row, "before")
                mutated = True
            for ordinal in attempted_ordinals:
                _target_bytes(root, plan["operations"][ordinal], "before")
        except Exception:
            preserve_recovery = True
            journal = _best_effort_transaction_phase(
                journal_path,
                journal,
                "review-required",
            )
            raise
        _cleanup_transaction_staged_files(root, journal)
        prepared_receipt_path.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
        _fsync_directory(state)
        return {
            "application_receipt": None,
            "attempted_ordinals": attempted_ordinals,
            "diagnostic_code": "BLUEPRINTS_M2_INTERRUPTED_TRANSACTION_RESTORED",
            "outcome": "restored",
            "workspace_mutated": mutated,
        }
    finally:
        if preserve_recovery:
            _close_transaction_lock(lock_descriptor)
            _mark_transaction_lock_recoverable(lock_path, plan["id"])
        else:
            _release_transaction_lock(
                lock_path,
                lock_descriptor,
                lock_token,
                lock_identity,
            )


def validate_applied_application_receipt(
    receipt: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    receipt_format: str,
    receipt_kind: str,
    receipt_content_kind: str,
    success_mutation_state: str,
) -> dict[str, Any]:
    if type(receipt) is not dict:
        _fail("rollback requires one exact application receipt")
    value = dict(receipt)
    body = dict(value)
    supplied = body.pop("id", None)
    expected_fields = {
        "authority_boundary",
        "diagnostic_code",
        "format",
        "history_objects",
        "id",
        "kind",
        "mutation_state",
        "plan_id",
        "rollback",
        "schema_version",
        "state",
    }
    if (
        set(value) != expected_fields
        or supplied
        != content_id(receipt_content_kind, body)
        or value.get("format") != receipt_format
        or value.get("kind") != receipt_kind
        or value.get("schema_version") != 1
        or value.get("plan_id") != plan["id"]
        or value.get("authority_boundary") != plan["authority_boundary"]
        or value.get("diagnostic_code") is not None
        or value.get("mutation_state") != success_mutation_state
        or value.get("rollback") != "available-while-after-bytes-match"
        or value.get("state") != "applied"
    ):
        _fail("rollback requires the exact successful application receipt")

    expected_history: list[dict[str, Any]] = []
    for row in plan["operations"]:
        for prefix in ("before", "after"):
            if row[f"{prefix}_base64"] is None:
                continue
            expected_history.append(
                {
                    "sha256": row[f"{prefix}_sha256"],
                    "size": row[f"{prefix}_size"],
                }
            )
    expected_history.sort(key=lambda row: (row["sha256"], row["size"]))
    if value.get("history_objects") != expected_history:
        _fail("application receipt history differs from the rollback plan")
    return value


def _verify_retained_history(
    state_root: Path,
    receipt: Mapping[str, Any],
) -> None:
    for row in receipt["history_objects"]:
        relative = PurePosixPath(
            "objects",
            row["sha256"][:2],
            row["sha256"][2:],
        )
        path = _rooted_target(state_root, relative, "rollback history object")
        raw = _read_regular(path, "rollback history object")
        if len(raw) != row["size"] or sha256(raw).hexdigest() != row["sha256"]:
            _fail("rollback history object identity changed")


def rollback_application_transaction(
    workspace: Path | str,
    value: Mapping[str, Any],
    state_root: Path | str,
    *,
    applied: Mapping[str, Any],
    rollback_format: str,
    rollback_kind: str,
    rollback_content_kind: str,
    transaction_lock: Path | str | None = None,
) -> dict[str, Any]:
    """Restore an owner-validated transaction while preserving later edits."""

    root = _absolute_path(workspace)
    state = _absolute_path(state_root)
    value = dict(value)
    applied = dict(applied)
    _ordinary_directory(root, "disposable workspace")
    if state.is_symlink():
        _fail("rollback transaction state is a symlink")
    root_resolved = root.resolve()
    state_resolved = state.resolve()
    if (
        state_resolved == root_resolved
        or state_resolved.is_relative_to(root_resolved)
        or root_resolved.is_relative_to(state_resolved)
    ):
        _fail("transaction state cannot overlap the disposable workspace")
    _ordinary_directory(state, "rollback transaction state")

    lock_path = (
        state / "active-transaction.lock"
        if transaction_lock is None
        else _absolute_path(transaction_lock)
    )
    held_lock = _acquire_transaction_lock(lock_path, applied["id"])
    if held_lock is None:
        body = {
            "diagnostic_code": "BLUEPRINTS_M2_TRANSACTION_LOCKED",
            "format": rollback_format,
            "kind": rollback_kind,
            "plan_id": value["id"],
            "schema_version": 1,
            "state": "rejected",
            "workspace_mutated": False,
        }
        return seal(rollback_content_kind, body)
    lock_descriptor, lock_token, lock_identity = held_lock

    try:
        _verify_retained_history(state, applied)
        try:
            for row in value["operations"]:
                _target_bytes(root, row, "after")
        except ApplicationTransactionError:
            body = {
                "diagnostic_code": "BLUEPRINTS_M2_LATER_EDIT_PRESERVED",
                "format": rollback_format,
                "kind": rollback_kind,
                "plan_id": value["id"],
                "schema_version": 1,
                "state": "rejected",
                "workspace_mutated": False,
            }
            return seal(rollback_content_kind, body)
        for row in reversed(value["operations"]):
            relative = _relative(row["path"], "rollback target")
            target = _rooted_target(
                root,
                relative,
                "rollback target",
                create_parents=True,
            )
            before_encoded = row["before_base64"]
            if before_encoded is None:
                target.unlink()
            else:
                before = base64.b64decode(before_encoded, validate=True)
                replacement = _write_staged(
                    root,
                    relative,
                    before,
                    mode=_target_mode(root, row, "rollback target"),
                )
                os.replace(replacement, target)
            _target_bytes(root, row, "before")
        body = {
            "diagnostic_code": None,
            "format": rollback_format,
            "kind": rollback_kind,
            "plan_id": value["id"],
            "schema_version": 1,
            "state": "restored",
            "workspace_mutated": True,
        }
        return seal(rollback_content_kind, body)
    finally:
        _release_transaction_lock(
            lock_path,
            lock_descriptor,
            lock_token,
            lock_identity,
        )


__all__ = [
    "ApplicationTransactionError",
    "apply_application_transaction",
    "recover_application_transaction",
    "rollback_application_transaction",
    "validate_applied_application_receipt",
]
