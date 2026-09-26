"""Core custody for revisioned validation invocation results.

The validator owns the V1 document and phase meaning. Core selects its
historical directory, creates each run result once, and compares every later
revision with the exact prior bytes and file identity. Interrupted or unknown
files remain in place for review; a new process never resumes one implicitly.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import stat

from workbench_api.host_filesystem import DurableRecordError
from workbench_api.record_stores import RecordStoreReference

from .host_filesystem import (
    private_path, publish_immutable_bytes, read_private_single_link_bytes,
    replace_private_bytes,
)


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _identity(path: Path, *, directory: bool) -> tuple[int, ...]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DurableRecordError("unavailable", "validation invocation path is unavailable") from exc
    if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
            or directory and not stat.S_ISDIR(info.st_mode)
            or not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
            or not private_path(path, directory=directory)):
        raise DurableRecordError("unsafe", "validation invocation path lost private custody")
    if directory:
        return info.st_dev, info.st_ino
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


class CoreValidationInvocationRecord:
    """One process's create-once result and its guarded in-process revisions."""

    def __init__(self, store: RecordStoreReference, run_id: str, *, target: Path | None = None):
        if (
            store.family not in {"validation-invocations-v1", "validation-invocation-explicit-v1"}
            or store.owner_id != "validation"
            or not store.root.is_absolute()
            or type(run_id) is not str
            or _RUN_ID.fullmatch(run_id) is None
            or run_id in {".", ".."}
        ):
            raise DurableRecordError("policy", "validation invocation requires a selected Core store and run ID")
        self.store = store
        self.run_id = run_id
        if store.family == "validation-invocations-v1":
            if target is not None:
                raise DurableRecordError("policy", "default invocation target is selected by Core")
            self.path = store.root / f"{run_id}.json"
        else:
            if (
                not isinstance(target, Path) or not target.is_absolute()
                or target.parent != store.root or target.name in {"", ".", ".."}
                or ".." in target.parts
            ):
                raise DurableRecordError("policy", "explicit invocation target must be one exact store child")
            self.path = target
        self._store_identity = _identity(store.root, directory=True)
        self._last_bytes: bytes | None = None
        self._file_identity: tuple[int, ...] | None = None

    def _verify_store(self) -> None:
        if _identity(self.store.root, directory=True) != self._store_identity:
            raise DurableRecordError("changed", "validation invocation store changed")

    def _reject_interrupted_stage(self) -> None:
        prefix = f".{self.path.name}."
        try:
            if self.path.exists() or self.path.is_symlink():
                raise DurableRecordError(
                    "collision", "validation invocation result already exists",
                )
            if any(member.name.startswith(prefix) for member in self.store.root.iterdir()):
                raise DurableRecordError(
                    "incomplete", "interrupted validation invocation stage requires review",
                )
        except DurableRecordError:
            raise
        except OSError as exc:
            raise DurableRecordError("unavailable", "cannot inspect invocation stages") from exc

    def write(self, payload: bytes) -> None:
        if type(payload) is not bytes:
            raise DurableRecordError("bounds", "validation invocation revision must be exact bytes")
        self._verify_store()
        if self._last_bytes is None:
            self._reject_interrupted_stage()
            publish_immutable_bytes(self.path, payload, byte_limit=len(payload))
        else:
            assert self._file_identity is not None
            if _identity(self.path, directory=False) != self._file_identity:
                raise DurableRecordError("changed", "validation invocation result changed since its last revision")
            bound = max(len(payload), len(self._last_bytes))
            if read_private_single_link_bytes(self.path, byte_limit=bound) != self._last_bytes:
                raise DurableRecordError("changed", "validation invocation bytes changed since their last revision")
            replace_private_bytes(
                self.path, payload, byte_limit=bound,
                expected_sha256="sha256:" + sha256(self._last_bytes).hexdigest(),
            )
        if read_private_single_link_bytes(self.path, byte_limit=len(payload)) != payload:
            raise DurableRecordError("changed", "validation invocation revision differs after publication")
        self._verify_store()
        self._file_identity = _identity(self.path, directory=False)
        self._last_bytes = payload


__all__ = ["CoreValidationInvocationRecord"]
