"""Authority-preserving Workspace Home V2 composition and local adoption.

Home V2 embeds the identity-bearing Workspace Home V1 projection verbatim,
then adds revision-bound jobs, owner-record references, freshness, and an
ignored-local adoption record.
It consumes Work Session summaries and the product capability catalog through validator
ports; it never reads their storage or reinterprets their owner state.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence, TextIO

from workbench_api.profile_extensions import (
    ProfileExtensionError,
    require_profile_extension,
)
from workbench_api.host_filesystem import (
    HostFilesystemError,
    fsync_directory,
    private_path,
    secure_private_path,
)

from workbench_project_intelligence.workspace_doctor import (
    new_report,
    validate_workspace_doctor_report,
)
from workbench_project_intelligence.git_observation import (
    GitObservationError,
    require_configured_git_executable,
)

from workbench_core.human_presentation import human_command, human_presentation

from .workspace_home import (
    HOME_ACTION_LIMIT,
    WorkspaceHomeError,
    build_workspace_home,
    validate_workspace_home,
)
from workbench_api.state_paths import default_product_spine_state_root
from workbench_api.state_paths import default_suite_state_root


HOME_V2_FORMAT = "workbench-workspace-home-v2"
HOME_V2_SCHEMA_VERSION = 2
ADOPTION_FORMAT = "workbench-workspace-home-adoption-v1"
ADOPTION_SCHEMA_VERSION = 1
STATE_DIRECTORY = "workspace-home-v2"
MAX_OWNER_RECORD_BYTES = 16 * 1024 * 1024
MAX_STATE_RECORD_BYTES = 2 * 1024 * 1024
MAX_DIRTY_DIFF_BYTES = 64 * 1024 * 1024
MAX_DIRTY_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_DIRTY_SOURCE_FILES = 20_000
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,255}$")
_BINDING_ID_RE = re.compile(r"^workspace-home-binding:sha256:[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(r"^work-session-v2-[0-9a-f]{32}$")
_CAPABILITY_CATALOG_ID_RE = re.compile(
    r"^workbench-product-capabilities:sha256:[0-9a-f]{64}$"
)
_JOB_PRIORITIES = {
    "workspace-health": 10,
    "cleanroom-fixture-build": 15,
    "search-workspace": 20,
    "inspect-exact-context": 30,
    "understand-recipes": 40,
    "change-recipe": 50,
    "run-development-client": 60,
}
_CATALOG_ACTIONS = {
    "workspace-health": ("doctor.inspect", "workspace"),
    "search-workspace": ("explorer.search", "search"),
    "change-recipe": ("developer-features.options", "recipe-options"),
    "understand-recipes": ("atlas.recipes-context", "recipe-context"),
    "cleanroom-fixture-build": (
        "cleanroom.fixture-build",
        "cleanroom-fixture",
    ),
}
_CLEANROOM_CONSTRUCTION_KIND = "workbench-new-project-kind:cleanroom-mod"
_CLEANROOM_CONSTRUCTION_OWNER_FORMAT = (
    "workbench-cleanroom-mod-construction-owner-v2"
)
_FILE_CUSTODY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
_CROSS_VIEW_FILE_CUSTODY_FIELDS = tuple(
    field for field in _FILE_CUSTODY_FIELDS if field != "st_ctime_ns"
)


class WorkspaceHomeV2Error(RuntimeError):
    """A Home V2 input or ignored-local state record failed closed."""


Validator = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


def _stable_file_custody(
    before: os.stat_result,
    opened: os.stat_result,
    after: os.stat_result,
    current: os.stat_result,
) -> bool:
    """Compare pathname and handle metadata without equating ctime views.

    NTFS can expose a different, stable ``st_ctime_ns`` through a pathname
    stat than through an already-open file handle. Every custody field must
    remain stable within each view; only cross-view comparisons omit ctime.
    """

    def matches(
        left: os.stat_result,
        right: os.stat_result,
        fields: tuple[str, ...],
    ) -> bool:
        return all(getattr(left, field) == getattr(right, field) for field in fields)

    return (
        matches(before, current, _FILE_CUSTODY_FIELDS)
        and matches(opened, after, _FILE_CUSTODY_FIELDS)
        and matches(before, opened, _CROSS_VIEW_FILE_CUSTODY_FIELDS)
        and matches(current, after, _CROSS_VIEW_FILE_CUSTODY_FIELDS)
    )


@dataclass(frozen=True, slots=True)
class OwnerRecordPort:
    """One owner record plus the owner's validator.

    ``bound_workspace_revision`` is an adapter assertion about applicability,
    not copied owner truth.  The supplied validator remains authoritative for
    the record's own semantics.
    """

    kind: str
    owner_id: str
    value: Mapping[str, Any]
    validator: Validator
    expected_format: str | None = None
    bound_workspace_revision: str | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedOwnerRecord:
    payload: dict[str, Any]
    reference: dict[str, Any]


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkspaceHomeV2Error("Home V2 values must be canonical JSON") from exc


def _digest(value: Any) -> str:
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _identity(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def _json_copy(value: Any, *, label: str, maximum_bytes: int) -> Any:
    payload = _canonical_bytes(value)
    if len(payload) > maximum_bytes:
        raise WorkspaceHomeV2Error(
            f"{label} exceeds the {maximum_bytes}-byte composition bound"
        )
    return json.loads(payload)


def _absolute_without_following(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _reject_symlink_components(path: Path, *, label: str) -> None:
    absolute = _absolute_without_following(path.expanduser())
    chain = [absolute, *absolute.parents]
    for candidate in reversed(chain):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise WorkspaceHomeV2Error(f"cannot inspect {label}: {candidate}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise WorkspaceHomeV2Error(
                f"{label} cannot traverse a symlink: {candidate}"
            )


def _safe_requested_path(requested_path: Path | str) -> Path:
    requested = _absolute_without_following(Path(requested_path).expanduser())
    _reject_symlink_components(requested, label="workspace path")
    try:
        return requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceHomeV2Error(
            f"workspace path is unavailable: {requested}"
        ) from exc


def _terminal_text(value: Any) -> str:
    rendered: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        if character == "\n":
            rendered.append("\\n")
        elif character == "\r":
            rendered.append("\\r")
        elif character == "\t":
            rendered.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            rendered.append(f"\\x{codepoint:02x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _doctor_matches_home(
    doctor: Mapping[str, Any], home_v1: Mapping[str, Any]
) -> None:
    target = doctor.get("target")
    if not isinstance(target, Mapping):
        raise WorkspaceHomeV2Error("Project Intelligence returned no target")
    workspace = target.get("workspace")
    repository = target.get("repository")
    if not isinstance(workspace, Mapping) or not isinstance(repository, Mapping):
        raise WorkspaceHomeV2Error("Project Intelligence target is incomplete")
    if workspace.get("root") != home_v1["workspace"]["root"]:
        raise WorkspaceHomeV2Error(
            "workspace identity changed while Home was being composed"
        )
    for field in ("state", "root", "branch", "head", "dirty", "changes"):
        if repository.get(field) != home_v1["repository"].get(field):
            raise WorkspaceHomeV2Error(
                "repository state changed while Home was being composed"
            )


def _workspace_identity(
    home_v1: Mapping[str, Any],
    doctor_digest: str,
    workspace_root: Path | None = None,
    *,
    dirty_fingerprint: str | None = None,
) -> tuple[str, str, str, str]:
    workspace = home_v1["workspace"]
    repository = home_v1["repository"]
    workspace_id = _identity(
        "workspace",
        {
            "root": workspace["root"],
            "kind": workspace["kind"],
        },
    )
    if dirty_fingerprint is None:
        if workspace_root is None:
            raise WorkspaceHomeV2Error(
                "workspace source bytes are required to compose its revision"
            )
        dirty_fingerprint = _workspace_content_fingerprint(home_v1, workspace_root)
    elif _DIGEST_RE.fullmatch(dirty_fingerprint) is None:
        raise WorkspaceHomeV2Error("workspace dirty fingerprint is invalid")
    revision_projection = {
        "workspace_id": workspace_id,
        "doctor_record_digest": doctor_digest,
        "repository": {
            key: repository.get(key)
            for key in ("state", "root", "branch", "head", "dirty", "changes")
        },
        "context": home_v1["context"],
        "dirty_fingerprint": dirty_fingerprint,
    }
    workspace_revision = _digest(revision_projection)
    head = repository.get("head")
    source_revision = (
        f"git:{head}"
        if isinstance(head, str) and head
        else "unversioned:" + workspace_revision.removeprefix("sha256:")
    )
    return workspace_id, workspace_revision, source_revision, dirty_fingerprint


def _git_bytes(root: Path, arguments: Sequence[str]) -> bytes:
    try:
        git = require_configured_git_executable()
    except GitObservationError as exc:
        raise WorkspaceHomeV2Error(
            f"cannot bind workspace source bytes: {exc}"
        ) from exc
    try:
        completed = subprocess.run(
            [git, "-C", str(root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceHomeV2Error(
            f"cannot bind workspace source bytes: {exc}"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise WorkspaceHomeV2Error(
            "cannot bind workspace source bytes"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


def _source_object_record(root: Path, raw_relative: bytes) -> tuple[dict[str, Any], int]:
    try:
        relative_text = raw_relative.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise WorkspaceHomeV2Error(
            "workspace source path is not strict UTF-8"
        ) from exc
    relative = Path(relative_text)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise WorkspaceHomeV2Error("workspace source path is unsafe")
    path = root.joinpath(*relative.parts)
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceHomeV2Error(
            f"cannot inspect workspace source object: {relative.as_posix()}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise WorkspaceHomeV2Error(
                f"cannot read workspace source symlink: {relative.as_posix()}"
            ) from exc
        encoded = target.encode("utf-8", "strict")
        return (
            {
                "kind": "symlink",
                "mode": stat.S_IMODE(metadata.st_mode),
                "path": relative.as_posix(),
                "sha256": sha256(encoded).hexdigest(),
                "size": len(encoded),
            },
            len(encoded),
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceHomeV2Error(
            f"workspace source contains a special object: {relative.as_posix()}"
        )
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise WorkspaceHomeV2Error(
                f"workspace source changed while opening: {relative.as_posix()}"
            )
        digest = sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DIRTY_SOURCE_BYTES:
                raise WorkspaceHomeV2Error(
                    "one workspace source object exceeds the custody byte limit"
                )
            digest.update(chunk)
        closed = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if not _stable_file_custody(metadata, opened, closed, current) or (
            size != opened.st_size
        ):
            raise WorkspaceHomeV2Error(
                f"workspace source changed while hashing: {relative.as_posix()}"
            )
    except WorkspaceHomeV2Error:
        raise
    except OSError as exc:
        raise WorkspaceHomeV2Error(
            f"cannot hash workspace source object: {relative.as_posix()}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return (
        {
            "kind": "file",
            "mode": stat.S_IMODE(metadata.st_mode),
            "path": relative.as_posix(),
            "sha256": digest.hexdigest(),
            "size": size,
        },
        size,
    )


def _workspace_content_fingerprint(
    home_v1: Mapping[str, Any], workspace_root: Path
) -> str:
    """Bind tracked drift and the bytes of every visible untracked source."""

    repository = home_v1["repository"]
    repository_root = repository.get("root")
    git_root = (
        Path(repository_root)
        if repository.get("state") == "observed" and isinstance(repository_root, str)
        else None
    )
    if git_root is not None:
        head = repository.get("head")
        tracked_diff = (
            _git_bytes(git_root, ("diff", "--binary", "--no-ext-diff", "HEAD", "--"))
            if isinstance(head, str) and head
            else b""
        )
        if len(tracked_diff) > MAX_DIRTY_DIFF_BYTES:
            raise WorkspaceHomeV2Error(
                "tracked workspace source diff exceeds the custody byte limit"
            )
        raw_paths = _git_bytes(
            git_root,
            ("ls-files", "-z", "--others", "--exclude-standard"),
        ).split(b"\0")
        if not head:
            raw_paths.extend(
                _git_bytes(git_root, ("ls-files", "-z", "--cached")).split(b"\0")
            )
        paths = sorted(set(token for token in raw_paths if token))
        mode = "git"
        root = git_root
    else:
        paths = []
        for directory, names, filenames in os.walk(workspace_root, followlinks=False):
            directory_path = Path(directory)
            if directory_path == workspace_root:
                names[:] = [name for name in names if name not in {".git", ".workbench"}]
            for name in list(names):
                candidate = directory_path / name
                if candidate.is_symlink():
                    paths.append(
                        candidate.relative_to(workspace_root).as_posix().encode("utf-8")
                    )
                    names.remove(name)
            paths.extend(
                (directory_path / name)
                .relative_to(workspace_root)
                .as_posix()
                .encode("utf-8")
                for name in filenames
            )
        paths = sorted(set(paths))
        tracked_diff = b""
        mode = "unversioned"
        root = workspace_root
    if len(paths) > MAX_DIRTY_SOURCE_FILES:
        raise WorkspaceHomeV2Error(
            "workspace source object count exceeds the custody limit"
        )
    records: list[dict[str, Any]] = []
    aggregate = 0
    for raw_path in paths:
        record, size = _source_object_record(root, raw_path)
        aggregate += size
        if aggregate > MAX_DIRTY_SOURCE_BYTES:
            raise WorkspaceHomeV2Error(
                "workspace source bytes exceed the custody limit"
            )
        records.append(record)
    return _digest(
        {
            "format": "workbench-workspace-source-fingerprint-v1",
            "mode": mode,
            "tracked_diff_sha256": sha256(tracked_diff).hexdigest(),
            "tracked_diff_size": len(tracked_diff),
            "objects": records,
        }
    )


def _owner_integrity(payload: Mapping[str, Any]) -> str:
    direct_state = payload.get("integrity_state")
    if direct_state == "verified":
        return "verified"
    if isinstance(direct_state, str) and direct_state:
        return "failed"
    integrity = payload.get("integrity")
    if isinstance(integrity, Mapping):
        state = integrity.get("state")
        if state == "verified":
            return "verified"
        if isinstance(state, str) and state:
            return "failed"
    return "verified"


def _owner_source_identities(
    payload: Mapping[str, Any], *, kind: str
) -> tuple[str | None, str | None, str | None]:
    """Retain owner-returned identities without deriving substitute IDs."""

    if kind == "work-session":
        session_id = payload.get("session_id")
        record_id = payload.get("session_record_id")
        if (
            not isinstance(session_id, str)
            or _SESSION_ID_RE.fullmatch(session_id) is None
            or not isinstance(record_id, str)
            or _IDENTITY_RE.fullmatch(record_id) is None
        ):
            raise WorkspaceHomeV2Error(
                "validated Work Session summary has invalid source identities"
            )
        return record_id, session_id, None
    if kind == "product-capability-catalog":
        catalog_id = payload.get("catalog_id")
        if (
            not isinstance(catalog_id, str)
            or _CAPABILITY_CATALOG_ID_RE.fullmatch(catalog_id) is None
        ):
            raise WorkspaceHomeV2Error(
                "validated capability catalog has an invalid source identity"
            )
        return catalog_id, None, catalog_id
    if kind == "cleanroom-fixture-lock":
        declared = payload.get("declared_values")
        identity = (
            declared.get("identity")
            if isinstance(declared, Mapping)
            else None
        )
        fixture_id = (
            identity.get("fixture_id")
            if isinstance(identity, Mapping)
            else None
        )
        if fixture_id != "workbench-fixture:cleanroom:generic-mod-daily-loop":
            raise WorkspaceHomeV2Error(
                "validated Cleanroom fixture lock has no exact fixture identity"
            )
        return fixture_id, None, None
    if kind == "new-project-construction":
        record_id = payload.get("id")
        if (
            payload.get("kind_id") != _CLEANROOM_CONSTRUCTION_KIND
            or not isinstance(record_id, str)
            or _IDENTITY_RE.fullmatch(record_id) is None
        ):
            raise WorkspaceHomeV2Error(
                "validated Cleanroom construction owner has no exact identity"
            )
        return record_id, None, None
    for key in ("record_id", "summary_id", "catalog_id", "session_record_id"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and _IDENTITY_RE.fullmatch(candidate):
            return candidate, None, None
    return None, None, None


def _corrupt_owner_resolution(
    port: OwnerRecordPort,
    candidate: Mapping[str, Any],
) -> _ResolvedOwnerRecord:
    """Isolate one validator-rejected record without hiding its exact digest."""

    record_digest = _digest(candidate)
    raw_format = candidate.get("format", candidate.get("format_version"))
    record_format = (
        raw_format
        if isinstance(raw_format, str) and raw_format
        else "unvalidated-owner-record"
    )
    reference_projection = {
        "kind": port.kind,
        "owner_id": port.owner_id,
        "record_digest": record_digest,
    }
    return _ResolvedOwnerRecord(
        payload=dict(candidate),
        reference={
            "id": _identity("owner-ref", reference_projection),
            "kind": port.kind,
            "owner_id": port.owner_id,
            "record_format": record_format,
            "record_id": None,
            "session_id": None,
            "catalog_id": None,
            "record_revision": record_digest,
            "record_digest": record_digest,
            "bound_workspace_revision": port.bound_workspace_revision,
            "freshness": "corrupt",
            "integrity": "failed",
            "validation_problem": "OWNER_VALIDATION_FAILED",
        },
    )


def _resolve_owner_port(
    port: OwnerRecordPort,
    *,
    workspace_root: str,
    workspace_revision: str,
    source_revision: str,
    dirty_fingerprint: str,
) -> _ResolvedOwnerRecord:
    if not isinstance(port, OwnerRecordPort):
        raise WorkspaceHomeV2Error("owner records must use OwnerRecordPort")
    if not _IDENTITY_RE.fullmatch(port.kind):
        raise WorkspaceHomeV2Error("owner record kind is invalid")
    if not _IDENTITY_RE.fullmatch(port.owner_id):
        raise WorkspaceHomeV2Error("owner record owner ID is invalid")
    if not callable(port.validator):
        raise WorkspaceHomeV2Error("owner record has no callable owner validator")
    candidate = _json_copy(
        port.value,
        label=f"{port.kind} owner record",
        maximum_bytes=MAX_OWNER_RECORD_BYTES,
    )
    if not isinstance(candidate, dict):
        raise WorkspaceHomeV2Error("owner record must be a JSON object")
    if (
        port.bound_workspace_revision is not None
        and not _DIGEST_RE.fullmatch(port.bound_workspace_revision)
    ):
        raise WorkspaceHomeV2Error(
            f"{port.kind} owner record has an invalid workspace revision binding"
        )
    try:
        normalized = port.validator(deepcopy(candidate))
        if normalized is None:
            normalized = candidate
        payload = _json_copy(
            normalized,
            label=f"validated {port.kind} owner record",
            maximum_bytes=MAX_OWNER_RECORD_BYTES,
        )
        if not isinstance(payload, dict):
            raise WorkspaceHomeV2Error("owner validator did not return an object")
        record_format = payload.get("format", payload.get("format_version"))
        if not isinstance(record_format, str) or not record_format:
            raise WorkspaceHomeV2Error("validated owner record has no format")
        if port.expected_format is not None and record_format != port.expected_format:
            raise WorkspaceHomeV2Error(
                f"{port.kind} owner record format differs from its adapter"
            )
        record_id, session_id, catalog_id = _owner_source_identities(
            payload,
            kind=port.kind,
        )
    except Exception:
        return _corrupt_owner_resolution(port, candidate)
    record_digest = _digest(payload)
    integrity = _owner_integrity(payload)
    effective_binding = port.bound_workspace_revision
    session_workspace = payload.get("workspace")
    if port.kind == "work-session":
        if not isinstance(session_workspace, Mapping):
            raise WorkspaceHomeV2Error(
                "validated Work Session summary has no workspace binding"
            )
        session_binding_matches = (
            session_workspace.get("canonical_root") == workspace_root
            and session_workspace.get("source_revision") == source_revision
            and session_workspace.get("dirty_fingerprint") == dirty_fingerprint
        )
        effective_binding = (
            workspace_revision
            if session_binding_matches
            else _digest(dict(session_workspace))
        )
    if integrity != "verified":
        freshness = "corrupt"
    elif port.kind == "work-session" and not session_binding_matches:
        freshness = "stale"
    elif (
        effective_binding is not None
        and effective_binding != workspace_revision
    ):
        freshness = "stale"
    elif effective_binding is None:
        freshness = "not-applicable"
    else:
        freshness = "current"
    reference_projection = {
        "kind": port.kind,
        "owner_id": port.owner_id,
        "record_digest": record_digest,
    }
    reference = {
        "id": _identity("owner-ref", reference_projection),
        "kind": port.kind,
        "owner_id": port.owner_id,
        "record_format": record_format,
        "record_id": record_id,
        "session_id": session_id,
        "catalog_id": catalog_id,
        "record_revision": record_digest,
        "record_digest": record_digest,
        "bound_workspace_revision": effective_binding,
        "freshness": freshness,
        "integrity": integrity,
        "validation_problem": None,
    }
    return _ResolvedOwnerRecord(payload=payload, reference=reference)


def _doctor_owner_reference(
    doctor: Mapping[str, Any], *, workspace_revision: str
) -> dict[str, Any]:
    record_digest = _digest(doctor)
    projection = {
        "kind": "workspace-context",
        "owner_id": "project-intelligence",
        "record_digest": record_digest,
    }
    return {
        "id": _identity("owner-ref", projection),
        "kind": "workspace-context",
        "owner_id": "project-intelligence",
        "record_format": doctor["format"],
        "record_id": None,
        "session_id": None,
        "catalog_id": None,
        "record_revision": record_digest,
        "record_digest": record_digest,
        "bound_workspace_revision": workspace_revision,
        "freshness": "current",
        "integrity": "verified",
        "validation_problem": None,
    }


def _owner_projection(
    resolved: _ResolvedOwnerRecord | None,
    *,
    absent_reason: str,
) -> dict[str, Any]:
    if resolved is None:
        return {
            "state": "unavailable",
            "owner_ref_id": None,
            "record_id": None,
            "session_id": None,
            "catalog_id": None,
            "record_revision": None,
            "freshness": "unavailable",
            "reason": absent_reason,
        }
    reference = resolved.reference
    state = (
        "available"
        if reference["freshness"] in {"current", "not-applicable"}
        else "unavailable"
    )
    return {
        "state": state,
        "owner_ref_id": reference["id"],
        "record_id": reference["record_id"],
        "session_id": reference["session_id"],
        "catalog_id": reference["catalog_id"],
        "record_revision": reference["record_revision"],
        "freshness": reference["freshness"],
        "reason": (
            None
            if state == "available"
            else f"The validated owner record is {reference['freshness']}."
        ),
    }


def _cleanroom_fixture_profile() -> Any | None:
    """Resolve the installed owner API; missing integration adds no fixture job."""

    try:
        owner = require_profile_extension("workbench.workspace_home_fixtures", "cleanroom")
    except ProfileExtensionError:
        return None
    required = (
        "fixture_root",
        "read_owner_lock",
        "validate_owner_lock",
        "source_inputs",
        "cleanup_path",
        "inspect_build_inputs",
        "build_input_digest",
    )
    if any(not callable(getattr(owner, name, None)) for name in required):
        raise WorkspaceHomeV2Error("Cleanroom fixture profile API is incomplete")
    return owner


def _cleanroom_fixture_owner_resolution(
    *,
    workspace_root: str,
    workspace_revision: str,
) -> _ResolvedOwnerRecord | None:
    owner = _cleanroom_fixture_profile()
    if owner is None:
        return None
    try:
        canonical = Path(owner.fixture_root()).resolve(strict=True)
    except (OSError, ValueError, TypeError) as exc:
        raise WorkspaceHomeV2Error("Cleanroom fixture profile root is invalid") from exc
    if Path(workspace_root) != canonical:
        return None
    try:
        candidate = owner.read_owner_lock()
    except (OSError, UnicodeError, ValueError):
        candidate = {}

    def validate(value: Mapping[str, Any]) -> Mapping[str, Any]:
        return owner.validate_owner_lock(value)

    return _resolve_owner_port(
        OwnerRecordPort(
            kind="cleanroom-fixture-lock",
            owner_id="cleanroom-platform-profile",
            value=candidate,
            validator=validate,
            expected_format="workbench-v2-boundary-owner-declaration-v1",
            bound_workspace_revision=workspace_revision,
        ),
        workspace_root=workspace_root,
        workspace_revision=workspace_revision,
        source_revision="not-applicable",
        dirty_fingerprint="sha256:" + "0" * 64,
    )


def _cleanroom_construction_owner_resolution(
    suite_root: Path,
    *,
    workspace_root: str,
    workspace_revision: str,
    source_revision: str,
    dirty_fingerprint: str,
) -> _ResolvedOwnerRecord:
    """Resolve the profile's global new-project constructor without inference."""

    from .cleanroom_new_project_cli import (
        load_cleanroom_mod_construction_owner,
        validate_cleanroom_mod_construction_owner,
    )

    try:
        candidate = load_cleanroom_mod_construction_owner(suite_root)
    except Exception:
        candidate = {}

    def validate(value: Mapping[str, Any]) -> Mapping[str, Any]:
        current = validate_cleanroom_mod_construction_owner(suite_root, value)
        if current.get("kind_id") != _CLEANROOM_CONSTRUCTION_KIND:
            raise WorkspaceHomeV2Error(
                "Cleanroom constructor owns another new-project kind"
            )
        return current

    return _resolve_owner_port(
        OwnerRecordPort(
            kind="new-project-construction",
            owner_id="cleanroom-platform-profile",
            value=candidate,
            validator=validate,
            expected_format=_CLEANROOM_CONSTRUCTION_OWNER_FORMAT,
        ),
        workspace_root=workspace_root,
        workspace_revision=workspace_revision,
        source_revision=source_revision,
        dirty_fingerprint=dirty_fingerprint,
    )


def _tool_input(path: Path, *, kind: str, display_path: str) -> dict[str, Any]:
    record, _ = _source_object_record(
        path.parent,
        path.name.encode("utf-8", "strict"),
    )
    if record["kind"] != "file":
        raise WorkspaceHomeV2Error("Cleanroom fixture tool input is not a file")
    return {
        "kind": kind,
        "path": display_path,
        "sha256": "sha256:" + record["sha256"],
        "size": record["size"],
        "mode": record["mode"],
    }


def _profile_fixture_preflight(
    owner: Any,
    *,
    fixture_owner: _ResolvedOwnerRecord,
    gradle_cmd: str,
    java_home: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Consume and check the profile's exact read-only build-input port."""

    inspected = owner.inspect_build_inputs(
        gradle_cmd=Path(gradle_cmd),
        java_home=Path(java_home),
    )
    expected_fixture = str(Path(owner.fixture_root()).resolve(strict=True))
    expected_tree_digest = fixture_owner.payload.get("declared_values", {}).get(
        "tree_digest"
    )
    if (
        type(inspected) is not dict
        or set(inspected)
        != {
            "format",
            "fixture",
            "fixture_digest",
            "gradle",
            "java",
            "cleanup_init",
        }
        or inspected.get("format")
        != "workbench-cleanroom-fixture-build-input-v1"
        or inspected.get("fixture") != expected_fixture
        or inspected.get("fixture_digest") != expected_tree_digest
    ):
        raise WorkspaceHomeV2Error(
            "Cleanroom profile preflight returned a foreign fixture identity"
        )
    gradle = inspected.get("gradle")
    java = inspected.get("java")
    cleanup = inspected.get("cleanup_init")
    if (
        type(gradle) is not dict
        or set(gradle) != {"path", "sha256", "size"}
        or not isinstance(gradle.get("path"), str)
        or not gradle["path"]
        or _DIGEST_RE.fullmatch(str(gradle.get("sha256", ""))) is None
        or type(gradle.get("size")) is not int
        or gradle["size"] < 0
        or type(java) is not dict
        or set(java) != {"home", "release_sha256", "executable_sha256"}
        or not isinstance(java.get("home"), str)
        or not java["home"]
        or _DIGEST_RE.fullmatch(str(java.get("release_sha256", ""))) is None
        or _DIGEST_RE.fullmatch(str(java.get("executable_sha256", ""))) is None
        or type(cleanup) is not dict
        or set(cleanup) != {"path", "sha256", "size"}
        or not isinstance(cleanup.get("path"), str)
        or not cleanup["path"]
        or _DIGEST_RE.fullmatch(str(cleanup.get("sha256", ""))) is None
        or type(cleanup.get("size")) is not int
        or cleanup["size"] < 0
    ):
        raise WorkspaceHomeV2Error(
            "Cleanroom profile preflight returned an invalid input binding"
        )
    gradle_path = Path(gradle["path"])
    java_home_path = Path(java["home"])
    cleanup_path = Path(cleanup["path"])
    selected_gradle = Path(gradle_cmd).expanduser().resolve(strict=True)
    selected_java_home = Path(java_home).expanduser().resolve(strict=True)
    expected_cleanup_path = Path(owner.cleanup_path()).resolve(strict=True)
    if (
        not gradle_path.is_absolute()
        or not java_home_path.is_absolute()
        or gradle_path != selected_gradle
        or java_home_path != selected_java_home
        or cleanup_path != expected_cleanup_path
    ):
        raise WorkspaceHomeV2Error(
            "Cleanroom profile preflight returned a foreign tool input"
        )
    retained = [
        _tool_input(
            gradle_path,
            kind="gradle-executable",
            display_path=str(gradle_path),
        ),
        _tool_input(
            java_home_path / "release",
            kind="java-release",
            display_path=str(java_home_path / "release"),
        ),
        _tool_input(
            java_home_path / "bin/java",
            kind="java-executable",
            display_path=str(java_home_path / "bin/java"),
        ),
        _tool_input(
            cleanup_path,
            kind="fixture-cleanup-init",
            display_path=str(cleanup_path),
        ),
    ]
    retained_by_kind = {row["kind"]: row for row in retained}
    if (
        retained_by_kind["gradle-executable"]["sha256"] != gradle["sha256"]
        or retained_by_kind["gradle-executable"]["size"] != gradle["size"]
        or retained_by_kind["java-release"]["sha256"]
        != java["release_sha256"]
        or retained_by_kind["java-executable"]["sha256"]
        != java["executable_sha256"]
        or retained_by_kind["fixture-cleanup-init"]["sha256"]
        != cleanup["sha256"]
        or retained_by_kind["fixture-cleanup-init"]["size"]
        != cleanup["size"]
    ):
        raise WorkspaceHomeV2Error(
            "Cleanroom profile preflight inputs changed while being retained"
        )
    input_digest = owner.build_input_digest(inspected)
    if _DIGEST_RE.fullmatch(str(input_digest)) is None:
        raise WorkspaceHomeV2Error(
            "Cleanroom profile preflight returned an invalid input digest"
        )
    return (
        {
            "gradle_cmd": str(gradle_path),
            "java_home": str(java_home_path),
            "expected_input_digest": input_digest,
        },
        retained,
    )


def _cleanroom_fixture_tool_context(
    suite_root: Path,
    fixture_owner: _ResolvedOwnerRecord,
) -> dict[str, Any]:
    tool_inputs: list[dict[str, Any]] = []
    source_error: str | None = None
    owner = _cleanroom_fixture_profile()
    try:
        if owner is None:
            raise WorkspaceHomeV2Error("Cleanroom fixture profile API is unavailable")
        sources = owner.source_inputs()
        if (
            type(sources) not in {tuple, list}
            or {row.get("kind") for row in sources if type(row) is dict}
            != {"fixture-owner-lock", "fixture-owner-schema", "profile-preflight-tool"}
            or len(sources) != 3
        ):
            raise WorkspaceHomeV2Error("Cleanroom fixture profile source contract is invalid")
        for row in sources:
            if (
                type(row) is not dict
                or set(row) != {"kind", "path", "display_path"}
                or not isinstance(row["display_path"], str)
                or not row["display_path"]
            ):
                raise WorkspaceHomeV2Error("Cleanroom fixture profile source contract is invalid")
            tool_inputs.append(
                _tool_input(Path(row["path"]), kind=row["kind"],
                            display_path=row["display_path"])
            )
    except (OSError, ValueError, TypeError, WorkspaceHomeV2Error) as exc:
        source_error = str(exc)
    raw_gradle = os.environ.get("WORKBENCH_CLEANROOM_FIXTURE_GRADLEW") or None
    raw_java_home = os.environ.get("WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME") or None
    arguments: dict[str, Any] = {
        "gradle_cmd": raw_gradle,
        "java_home": raw_java_home,
        "expected_input_digest": None,
        "state_root": str(
            default_product_spine_state_root(suite_root)
            / "cleanroom-fixture"
        ),
    }
    blockers: list[str] = []
    next_safe_action: str | None = None
    reason: str | None = None
    if fixture_owner.reference["freshness"] != "current":
        blockers.append("CLEANROOM_FIXTURE_LOCK_INVALID")
        reason = "The profile-owned fixture lock or complete source tree is invalid."
        next_safe_action = (
            "Restore the exact profile-owned fixture tree and lock before retrying."
        )
    elif source_error is not None:
        blockers.append("CLEANROOM_FIXTURE_PREFLIGHT_UNAVAILABLE")
        reason = source_error
        next_safe_action = (
            "Restore the profile-owned read-only fixture preflight tool and schema."
        )
    elif raw_gradle is None or raw_java_home is None:
        blockers.append("CLEANROOM_FIXTURE_TOOL_CONFIGURATION_REQUIRED")
        reason = "The exact developer-selected Gradle and Java 25 paths are absent."
        next_safe_action = (
            "Set WORKBENCH_CLEANROOM_FIXTURE_GRADLEW and "
            "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME to absolute non-symlink paths."
        )
    else:
        try:
            retained_arguments, retained_inputs = _profile_fixture_preflight(
                owner,
                fixture_owner=fixture_owner,
                gradle_cmd=raw_gradle,
                java_home=raw_java_home,
            )
            arguments.update(retained_arguments)
            tool_inputs.extend(retained_inputs)
        except Exception as exc:
            blockers.append("CLEANROOM_FIXTURE_TOOL_INVALID")
            reason = f"The explicit Cleanroom fixture toolchain failed preflight: {exc}"
            next_safe_action = (
                "Set both Cleanroom fixture variables to an executable Gradle and "
                "an ordinary Java 25 home, without symlink substitution."
            )
    tool_inputs.sort(key=lambda item: (item["kind"], item["path"]))
    return {
        "arguments": arguments,
        "tool_inputs": tool_inputs,
        "blockers": sorted(set(blockers)),
        "ready": not blockers,
        "reason": reason,
        "next_safe_action": next_safe_action,
    }


def _v2_actions(
    home_v1: Mapping[str, Any],
    *,
    include_cleanroom_fixture: bool,
) -> list[dict[str, Any]]:
    actions = [deepcopy(action) for action in home_v1["actions"]]
    if include_cleanroom_fixture:
        actions.append(
            {
                "id": "cleanroom-fixture-build",
                "title": "Build the frozen Cleanroom mod fixture",
                "purpose": (
                    "Run the exact profile-owned generic-mod daily-loop check with "
                    "developer-selected Gradle and Java 25 tools."
                ),
                "available": True,
                "argv": None,
                "blockers": [],
                "unavailable_reason": None,
            }
        )
    return actions


def _eligibility_digest(job: Mapping[str, Any]) -> str:
    projection = dict(job)
    projection.pop("eligibility_digest", None)
    return _digest(projection)


def _catalog_action_binding(
    catalog: Any,
    action: Mapping[str, Any],
    *,
    suite_root: Path,
    workspace_root: str,
) -> dict[str, Any] | None:
    selected = _CATALOG_ACTIONS.get(action["id"])
    if selected is None or not action.get("available"):
        return None
    command_id, adapter = selected
    values = _catalog_arguments(adapter, workspace_root)
    try:
        command = catalog.command(command_id)
        exact_argv, _ = command.build_argv(values, root=suite_root, execute=False)
    except (OSError, ValueError) as exc:
        raise WorkspaceHomeV2Error(
            f"catalog action {command_id} could not compose exact argv: {exc}"
        ) from exc
    legacy_argv = action.get("argv")
    expected_argv = (
        [sys.executable, str(suite_root / "tools/workbench.py"), *legacy_argv[1:]]
        if isinstance(legacy_argv, list) and legacy_argv[:1] == ["workbench"]
        else None
    )
    if expected_argv is None or exact_argv != expected_argv:
        return None
    return {
        "command_id": command_id,
        "catalog_digest": catalog.catalog_digest,
        "action_digest": command.action_digest(root=suite_root),
        "arguments": dict(values),
        "exact_argv": exact_argv,
    }


def _cleanroom_fixture_catalog_binding(
    catalog: Any,
    *,
    suite_root: Path,
    fixture_tool_context: Mapping[str, Any],
) -> dict[str, Any]:
    command_id = "cleanroom.fixture-build"
    try:
        command = catalog.command(command_id)
    except ValueError as exc:
        raise WorkspaceHomeV2Error(
            "Cleanroom fixture catalog action is unavailable"
        ) from exc
    arguments = dict(fixture_tool_context["arguments"])
    exact_argv: list[str] | None = None
    if fixture_tool_context["ready"]:
        try:
            exact_argv, _ = command.build_argv(
                arguments,
                root=suite_root,
                execute=False,
            )
        except (OSError, ValueError) as exc:
            raise WorkspaceHomeV2Error(
                "Cleanroom fixture catalog action rejected preflighted tools"
            ) from exc
    return {
        "command_id": command_id,
        "catalog_digest": catalog.catalog_digest,
        "action_digest": command.action_digest(root=suite_root),
        "arguments": arguments,
        "exact_argv": exact_argv,
    }


def _catalog_arguments(adapter: str, workspace_root: str) -> dict[str, Any]:
    values: dict[str, Any]
    if adapter == "workspace":
        values = {"workspace": workspace_root}
    elif adapter == "search":
        values = {"project": workspace_root, "source": "project"}
    elif adapter == "recipe-options":
        values = {"family": "recipe-change", "workspace": workspace_root}
    elif adapter == "recipe-context":
        values = {"path": workspace_root}
    else:  # pragma: no cover - closed constant table
        raise WorkspaceHomeV2Error("Home catalog adapter is invalid")
    return values


def _product_capability_binding(
    capability_catalog: _ResolvedOwnerRecord | None,
    command_id: str | None,
    action_digest: str | None,
) -> dict[str, Any] | None:
    """Match one exact capability row from the registered product catalog."""

    if capability_catalog is None or command_id is None:
        return None
    rows = capability_catalog.payload.get("capabilities")
    if not isinstance(rows, list):
        return None
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("catalog_action"), Mapping)
        and row["catalog_action"].get("command_id") == command_id
    ]
    if len(matches) != 1:
        return None
    row = matches[0]
    handler = row.get("handler")
    return {
        "capability": deepcopy(dict(row)),
        "executable": (
            row.get("availability") != "unavailable"
            and isinstance(handler, Mapping)
            and handler.get("registered") is True
            and handler.get("executable") is True
            and row["catalog_action"].get("action_digest") == action_digest
        ),
    }


def _capability_record_is_executable(
    capability: Mapping[str, Any],
    *,
    action_digest: str | None,
) -> bool:
    catalog_action = capability.get("catalog_action")
    handler = capability.get("handler")
    return bool(
        isinstance(catalog_action, Mapping)
        and catalog_action.get("action_digest") == action_digest
        and capability.get("availability") != "unavailable"
        and isinstance(handler, Mapping)
        and handler.get("registered") is True
        and handler.get("executable") is True
    )


def _validate_capability_record(
    capability: Any,
    *,
    command_id: str,
) -> None:
    """Validate the exact product row retained by a catalog-bound Home job."""

    required = {
        "capability_id",
        "capability_key",
        "title",
        "summary",
        "authority",
        "risk",
        "availability",
        "handler",
        "catalog_action",
        "limitations",
    }
    if type(capability) is not dict or set(capability) != required:
        raise WorkspaceHomeV2Error("Home V2 product capability row is invalid")
    if (
        re.fullmatch(
            r"capability:sha256:[0-9a-f]{64}",
            str(capability.get("capability_id", "")),
        )
        is None
        or re.fullmatch(
            r"[a-z][a-z0-9.-]+",
            str(capability.get("capability_key", "")),
        )
        is None
        or any(
            not isinstance(capability.get(field), str)
            or not capability[field]
            for field in ("title", "summary", "authority")
        )
        or capability.get("risk")
        not in {"read-only", "writes-output", "mutating", "destructive"}
        or capability.get("availability")
        not in {"available", "experimental", "unavailable"}
        or type(capability.get("limitations")) is not list
        or any(
            not isinstance(item, str) for item in capability["limitations"]
        )
    ):
        raise WorkspaceHomeV2Error("Home V2 product capability identity is invalid")

    catalog_action = capability.get("catalog_action")
    if (
        type(catalog_action) is not dict
        or set(catalog_action)
        != {"action_digest", "command_id", "suite_id"}
        or catalog_action.get("command_id") != command_id
        or _DIGEST_RE.fullmatch(str(catalog_action.get("action_digest", "")))
        is None
        or re.fullmatch(
            r"[a-z][a-z0-9-]+",
            str(catalog_action.get("suite_id", "")),
        )
        is None
    ):
        raise WorkspaceHomeV2Error("Home V2 product catalog action is invalid")

    handler = capability.get("handler")
    if (
        type(handler) is not dict
        or set(handler) != {"kind", "registered", "executable"}
        or handler.get("kind") not in {"process", "document"}
        or handler.get("registered") is not True
        or type(handler.get("executable")) is not bool
        or (handler["kind"] == "document" and handler["executable"] is not False)
        or (
            capability["availability"] == "unavailable"
            and handler["executable"] is not False
        )
    ):
        raise WorkspaceHomeV2Error("Home V2 product handler binding is invalid")


def _is_project_intelligence_context_resolution(
    *,
    job_id: str,
    command_id: str | None,
    action_digest: str | None,
    capability: Mapping[str, Any] | None,
    doctor_ref: Mapping[str, Any],
    workspace_revision: str,
) -> bool:
    """Bind Doctor to both its registered handler and exact PI context."""

    if (
        job_id != "workspace-health"
        or command_id != "doctor.inspect"
        or capability is None
        or doctor_ref.get("kind") != "workspace-context"
        or doctor_ref.get("owner_id") != "project-intelligence"
        or doctor_ref.get("freshness") != "current"
        or doctor_ref.get("integrity") != "verified"
        or doctor_ref.get("validation_problem") is not None
        or doctor_ref.get("bound_workspace_revision") != workspace_revision
    ):
        return False
    catalog_action = capability.get("catalog_action")
    return bool(
        isinstance(catalog_action, Mapping)
        and catalog_action.get("command_id") == command_id
        and catalog_action.get("action_digest") == action_digest
        and _capability_record_is_executable(
            capability,
            action_digest=action_digest,
        )
    )


def _is_cleanroom_fixture_context_resolution(
    *,
    job_id: str,
    command_id: str | None,
    action_digest: str | None,
    capability: Mapping[str, Any] | None,
    fixture_owner_ref: Mapping[str, Any] | None,
    workspace_revision: str,
) -> bool:
    if (
        job_id != "cleanroom-fixture-build"
        or command_id != "cleanroom.fixture-build"
        or capability is None
        or fixture_owner_ref is None
        or fixture_owner_ref.get("kind") != "cleanroom-fixture-lock"
        or fixture_owner_ref.get("owner_id") != "cleanroom-platform-profile"
        or fixture_owner_ref.get("freshness") != "current"
        or fixture_owner_ref.get("integrity") != "verified"
        or fixture_owner_ref.get("validation_problem") is not None
        or fixture_owner_ref.get("bound_workspace_revision")
        != workspace_revision
    ):
        return False
    catalog_action = capability.get("catalog_action")
    return bool(
        isinstance(catalog_action, Mapping)
        and catalog_action.get("command_id") == command_id
        and catalog_action.get("action_digest") == action_digest
        and _capability_record_is_executable(
            capability,
            action_digest=action_digest,
        )
    )


def _job_availability_basis(
    *,
    job_id: str,
    command_id: str | None,
    action_digest: str | None,
    capability: Mapping[str, Any] | None,
    doctor_ref: Mapping[str, Any],
    capability_catalog_ref: Mapping[str, Any] | None,
    fixture_owner_ref: Mapping[str, Any] | None,
    workspace_revision: str,
) -> dict[str, Any]:
    if command_id is None:
        return {
            "kind": "base-home",
            "scope": "base-home",
            "owner_ref_id": doctor_ref["id"],
            "owner_record_revision": doctor_ref["record_revision"],
            "command_id": None,
            "capability_id": None,
            "global_capability_effect": "not-applicable",
        }
    if _is_cleanroom_fixture_context_resolution(
        job_id=job_id,
        command_id=command_id,
        action_digest=action_digest,
        capability=capability,
        fixture_owner_ref=fixture_owner_ref,
        workspace_revision=workspace_revision,
    ):
        return {
            "kind": "owner-context-resolution",
            "scope": "cleanroom-fixture",
            "owner_ref_id": fixture_owner_ref["id"],
            "owner_record_revision": fixture_owner_ref["record_revision"],
            "command_id": command_id,
            "capability_id": capability["capability_id"],
            "global_capability_effect": "retained-unmodified",
        }
    if _is_project_intelligence_context_resolution(
        job_id=job_id,
        command_id=command_id,
        action_digest=action_digest,
        capability=capability,
        doctor_ref=doctor_ref,
        workspace_revision=workspace_revision,
    ):
        return {
            "kind": "owner-context-resolution",
            "scope": "workspace-context",
            "owner_ref_id": doctor_ref["id"],
            "owner_record_revision": doctor_ref["record_revision"],
            "command_id": command_id,
            "capability_id": capability["capability_id"],
            "global_capability_effect": "retained-unmodified",
        }
    return {
        "kind": "product-capability",
        "scope": "global",
        "owner_ref_id": (
            capability_catalog_ref["id"]
            if capability_catalog_ref is not None
            else None
        ),
        "owner_record_revision": (
            capability_catalog_ref["record_revision"]
            if capability_catalog_ref is not None
            else None
        ),
        "command_id": command_id,
        "capability_id": (
            capability["capability_id"] if capability is not None else None
        ),
        "global_capability_effect": "authoritative",
    }


def _jobs(
    home_v1: Mapping[str, Any],
    *,
    suite_root: Path,
    catalog: Any,
    workspace_revision: str,
    doctor_ref: Mapping[str, Any],
    session: _ResolvedOwnerRecord | None,
    capability_catalog: _ResolvedOwnerRecord | None,
    fixture_owner: _ResolvedOwnerRecord | None,
    fixture_tool_context: Mapping[str, Any] | None,
    adoption_stale: bool,
) -> list[dict[str, Any]]:
    session_revision = (
        session.reference["record_revision"] if session is not None else None
    )
    capability_catalog_revision = (
        capability_catalog.reference["record_revision"]
        if capability_catalog is not None
        else None
    )
    capability_catalog_freshness = (
        capability_catalog.reference["freshness"]
        if capability_catalog is not None
        else "unavailable"
    )
    jobs: list[dict[str, Any]] = []
    for action in _v2_actions(
        home_v1,
        include_cleanroom_fixture=fixture_owner is not None,
    ):
        action_id = action["id"]
        blockers = list(action["blockers"])
        reason = action["unavailable_reason"]
        catalog_binding = (
            _cleanroom_fixture_catalog_binding(
                catalog,
                suite_root=suite_root,
                fixture_tool_context=fixture_tool_context,
            )
            if action_id == "cleanroom-fixture-build"
            and fixture_tool_context is not None
            else _catalog_action_binding(
                catalog,
                action,
                suite_root=suite_root,
                workspace_root=home_v1["workspace"]["root"],
            )
        )
        capability_binding = _product_capability_binding(
            (
                capability_catalog
                if capability_catalog_freshness in {"current", "not-applicable"}
                else None
            ),
            (
                catalog_binding["command_id"]
                if catalog_binding is not None
                else None
            ),
            (
                catalog_binding["action_digest"]
                if catalog_binding is not None
                else None
            ),
        )
        capability = (
            capability_binding["capability"]
            if capability_binding is not None
            else None
        )
        context_resolution = _is_project_intelligence_context_resolution(
            job_id=action_id,
            command_id=(
                catalog_binding["command_id"]
                if catalog_binding is not None
                else None
            ),
            action_digest=(
                catalog_binding["action_digest"]
                if catalog_binding is not None
                else None
            ),
            capability=capability,
            doctor_ref=doctor_ref,
            workspace_revision=workspace_revision,
        ) or _is_cleanroom_fixture_context_resolution(
            job_id=action_id,
            command_id=(
                catalog_binding["command_id"]
                if catalog_binding is not None
                else None
            ),
            action_digest=(
                catalog_binding["action_digest"]
                if catalog_binding is not None
                else None
            ),
            capability=capability,
            fixture_owner_ref=(
                fixture_owner.reference if fixture_owner is not None else None
            ),
            workspace_revision=workspace_revision,
        )
        if action_id == "cleanroom-fixture-build" and fixture_tool_context is not None:
            blockers.extend(fixture_tool_context["blockers"])
            if fixture_tool_context["reason"] is not None:
                reason = fixture_tool_context["reason"]
        if action["available"] and catalog_binding is None:
            blockers.append("CATALOG_ACTION_UNAVAILABLE")
            reason = (
                "No exact current catalog action matches this Home job and argv."
            )
        owner_ref_ids = [doctor_ref["id"]]
        context_dependent = action_id != "workspace-health"
        capability_dependent = catalog_binding is not None
        if context_dependent:
            if session is not None:
                owner_ref_ids.append(session.reference["id"])
                session_freshness = session.reference["freshness"]
                if session_freshness not in {"current", "not-applicable"}:
                    blockers.append("WORK_SESSION_" + session_freshness.upper())
                    reason = (
                        "The supplied Work Session summary is "
                        f"{session_freshness}."
                    )
        if capability_dependent:
            if capability_catalog is None:
                blockers.append("CAPABILITY_CATALOG_UNAVAILABLE")
                reason = (
                    "The product capability catalog was not supplied through its "
                    "owner validator."
                )
            elif capability_catalog_freshness not in {"current", "not-applicable"}:
                blockers.append(
                    "CAPABILITY_CATALOG_" + capability_catalog_freshness.upper()
                )
                reason = (
                    "The supplied capability catalog is "
                    f"{capability_catalog_freshness}."
                )
            elif catalog_binding is not None and (
                capability_binding is None
                or capability_binding["executable"] is not True
            ) and not context_resolution:
                blockers.append("PRODUCT_CAPABILITY_UNAVAILABLE")
                reason = (
                    "The validated product catalog contains no exact executable "
                    "capability for this catalog action."
                )
            owner_ref_ids.extend(
                [capability_catalog.reference["id"]]
                if capability_catalog is not None
                else []
            )
        if action_id == "cleanroom-fixture-build" and fixture_owner is not None:
            owner_ref_ids.append(fixture_owner.reference["id"])
        if adoption_stale and action_id != "workspace-health":
            blockers.append("ADOPTION_BINDING_STALE")
            reason = (
                "The adopted workspace revision differs from the current workspace."
            )
        blockers = sorted(set(blockers))
        available = action["available"] and not blockers
        job = {
            "id": action_id,
            "rank": _JOB_PRIORITIES.get(action_id, 1000),
            "title": action["title"],
            "purpose": action["purpose"],
            "state": "available" if available else "unavailable",
            "argv": (
                list(catalog_binding["exact_argv"])
                if available and catalog_binding is not None
                else None
            ),
            "blockers": blockers,
            "unavailable_reason": None if available else reason,
            "owner_ref_ids": sorted(owner_ref_ids),
            "eligibility_binding": {
                "workspace_revision": workspace_revision,
                "session_revision": (
                    session_revision if context_dependent else None
                ),
                "capability_catalog_revision": (
                    capability_catalog_revision if capability_dependent else None
                ),
            },
            "command_id": (
                catalog_binding["command_id"]
                if catalog_binding is not None
                else None
            ),
            "catalog_digest": catalog.catalog_digest,
            "action_digest": (
                catalog_binding["action_digest"]
                if catalog_binding is not None
                else None
            ),
            "capability_id": (
                capability_binding["capability"]["capability_id"]
                if capability_binding is not None
                else None
            ),
            "capability_key": (
                capability_binding["capability"]["capability_key"]
                if capability_binding is not None
                else None
            ),
            "capability": (
                deepcopy(capability)
                if capability is not None
                else None
            ),
            "availability_basis": _job_availability_basis(
                job_id=action_id,
                command_id=(
                    catalog_binding["command_id"]
                    if catalog_binding is not None
                    else None
                ),
                action_digest=(
                    catalog_binding["action_digest"]
                    if catalog_binding is not None
                    else None
                ),
                capability=capability,
                doctor_ref=doctor_ref,
                capability_catalog_ref=(
                    capability_catalog.reference
                    if capability_catalog is not None
                    else None
                ),
                fixture_owner_ref=(
                    fixture_owner.reference
                    if fixture_owner is not None
                    else None
                ),
                workspace_revision=workspace_revision,
            ),
            "tool_inputs": (
                deepcopy(fixture_tool_context["tool_inputs"])
                if action_id == "cleanroom-fixture-build"
                and fixture_tool_context is not None
                else []
            ),
            "next_safe_action": (
                fixture_tool_context["next_safe_action"]
                if action_id == "cleanroom-fixture-build"
                and fixture_tool_context is not None
                else None
            ),
            "arguments": (
                dict(catalog_binding["arguments"])
                if catalog_binding is not None
                else None
            ),
            "eligibility_digest": "pending",
        }
        job["eligibility_digest"] = _eligibility_digest(job)
        jobs.append(job)
    jobs.sort(key=lambda item: (item["rank"], item["id"]))
    return jobs[:HOME_ACTION_LIMIT]


def _problems(
    *,
    session: _ResolvedOwnerRecord | None,
    capability_catalog: _ResolvedOwnerRecord | None,
    construction_owner: _ResolvedOwnerRecord,
    adoption_stale: bool,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if session is None:
        rows.append(
            {
                "id": "WORK_SESSION_UNAVAILABLE",
                "severity": "info",
                "detail": "No validated C01 Work Session summary was supplied.",
            }
        )
    elif session.reference["freshness"] not in {"current", "not-applicable"}:
        rows.append(
            {
                "id": "WORK_SESSION_" + session.reference["freshness"].upper(),
                "severity": "warning",
                "detail": (
                    "The supplied Work Session summary is "
                    f"{session.reference['freshness']}."
                ),
            }
        )
    if capability_catalog is None:
        rows.append(
            {
                "id": "CAPABILITY_CATALOG_UNAVAILABLE",
                "severity": "warning",
                "detail": "No validated product capability catalog was supplied.",
            }
        )
    elif capability_catalog.reference["freshness"] not in {
        "current",
        "not-applicable",
    }:
        rows.append(
            {
                "id": (
                    "CAPABILITY_CATALOG_"
                    + capability_catalog.reference["freshness"].upper()
                ),
                "severity": "warning",
                "detail": (
                    "The supplied capability catalog is "
                    f"{capability_catalog.reference['freshness']}."
                ),
            }
        )
    if adoption_stale:
        rows.append(
            {
                "id": "ADOPTION_BINDING_STALE",
                "severity": "warning",
                "detail": (
                    "The adopted workspace revision differs from the current "
                    "Project Intelligence observation."
                ),
            }
        )
    if (
        construction_owner.reference["integrity"] != "verified"
        or construction_owner.reference["freshness"]
        not in {"current", "not-applicable"}
    ):
        rows.append(
            {
                "id": "NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE",
                "severity": "warning",
                "detail": (
                    "The exact Cleanroom new-project construction owner could not "
                    "be verified; Home will not infer a replacement constructor."
                ),
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def _status(home_v1: Mapping[str, Any], problems: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    severities = [row["severity"] for row in problems]
    base = home_v1["status"]["status"]
    state = (
        "blocked"
        if base == "blocked" or "blocker" in severities
        else "attention"
        if base == "attention" or any(item in {"warning", "info"} for item in severities)
        else "ready"
    )
    return {
        "state": state,
        "base_home_state": base,
        "blockers": severities.count("blocker") + home_v1["status"]["blockers"],
        "warnings": severities.count("warning") + home_v1["status"]["warnings"],
        "information": severities.count("info") + home_v1["status"]["information"],
    }


def _home_id(value: Mapping[str, Any]) -> str:
    projection = dict(value)
    projection.pop("home_id", None)
    return _identity("workspace-home", projection)


def _compose_workspace_home_v2(
    suite_root: Path | str,
    requested_path: Path | str,
    *,
    session_record: OwnerRecordPort | None,
    capability_catalog_record: OwnerRecordPort | None,
    owner_records: Sequence[OwnerRecordPort],
    operation: str,
    adoption: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if operation not in {"open", "adopt", "reopen"}:
        raise WorkspaceHomeV2Error("Home V2 operation is invalid")
    try:
        suite = Path(suite_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceHomeV2Error("Workbench suite root is unavailable") from exc
    requested = _safe_requested_path(requested_path)
    try:
        doctor = new_report(requested, requested_path=requested)
        validate_workspace_doctor_report(doctor)
        home_v1 = build_workspace_home(suite, requested)
        validate_workspace_home(home_v1)
    except (OSError, ValueError, WorkspaceHomeError) as exc:
        raise WorkspaceHomeV2Error(f"workspace inspection failed: {exc}") from exc
    _doctor_matches_home(doctor, home_v1)
    try:
        from .catalog import build_catalog

        catalog = build_catalog(suite)
    except (ImportError, OSError, ValueError) as exc:
        raise WorkspaceHomeV2Error(
            f"current Workbench command catalog is unavailable: {exc}"
        ) from exc
    doctor_digest = _digest(doctor)
    (
        workspace_id,
        workspace_revision,
        source_revision,
        dirty_fingerprint,
    ) = _workspace_identity(
        home_v1,
        doctor_digest,
        requested,
    )

    if session_record is not None and session_record.kind != "work-session":
        raise WorkspaceHomeV2Error("session_record must use kind work-session")
    if (
        capability_catalog_record is not None
        and capability_catalog_record.kind != "product-capability-catalog"
    ):
        raise WorkspaceHomeV2Error(
            "capability_catalog_record must use kind product-capability-catalog"
        )
    if any(
        port.kind
        in {
            "work-session",
            "product-capability-catalog",
            "workspace-context",
            "cleanroom-fixture-lock",
            "new-project-construction",
        }
        for port in owner_records
    ):
        raise WorkspaceHomeV2Error(
            "reserved Home owner kinds must use their dedicated ports"
        )
    ports = [
        *([session_record] if session_record is not None else []),
        *(
            [capability_catalog_record]
            if capability_catalog_record is not None
            else []
        ),
        *owner_records,
    ]
    resolved = [
        _resolve_owner_port(
            port,
            workspace_root=home_v1["workspace"]["root"],
            workspace_revision=workspace_revision,
            source_revision=source_revision,
            dirty_fingerprint=dirty_fingerprint,
        )
        for port in ports
    ]
    session = resolved[0] if session_record is not None else None
    catalog_index = 1 if session_record is not None else 0
    capability_catalog = (
        resolved[catalog_index]
        if capability_catalog_record is not None
        else None
    )
    fixture_owner = _cleanroom_fixture_owner_resolution(
        workspace_root=home_v1["workspace"]["root"],
        workspace_revision=workspace_revision,
    )
    if fixture_owner is not None:
        resolved.append(fixture_owner)
    construction_owner = _cleanroom_construction_owner_resolution(
        suite,
        workspace_root=home_v1["workspace"]["root"],
        workspace_revision=workspace_revision,
        source_revision=source_revision,
        dirty_fingerprint=dirty_fingerprint,
    )
    resolved.append(construction_owner)
    reference_ids = [row.reference["id"] for row in resolved]
    if len(reference_ids) != len(set(reference_ids)):
        raise WorkspaceHomeV2Error("Home V2 owner records are duplicated")
    doctor_ref = _doctor_owner_reference(
        doctor,
        workspace_revision=workspace_revision,
    )
    all_references = [doctor_ref, *(row.reference for row in resolved)]
    all_references.sort(key=lambda row: (row["kind"], row["owner_id"], row["id"]))

    adoption_value = dict(adoption) if adoption is not None else {
        "state": "unadopted",
        "binding_id": None,
        "session_id": None,
        "state_revision": None,
        "adopted_workspace_id": None,
        "adopted_workspace_root": None,
        "freshness": "not-applicable",
        "stale_reasons": [],
        "recovery_state": "none",
        "recovery_reasons": [],
        "interrupted_write_count": 0,
    }
    adoption_stale = adoption_value.get("freshness") == "stale"
    problems = _problems(
        session=session,
        capability_catalog=capability_catalog,
        construction_owner=construction_owner,
        adoption_stale=adoption_stale,
    )
    construction_available = (
        construction_owner.reference["integrity"] == "verified"
        and construction_owner.reference["freshness"]
        in {"current", "not-applicable"}
        and construction_owner.payload.get("kind_id")
        == _CLEANROOM_CONSTRUCTION_KIND
    )
    result: dict[str, Any] = {
        "format": HOME_V2_FORMAT,
        "schema_version": HOME_V2_SCHEMA_VERSION,
        "home_id": "pending",
        "operation": operation,
        "read_only": True,
        "local_state_effect": "none",
        "workspace": {
            **dict(home_v1["workspace"]),
            "workspace_id": workspace_id,
            "workspace_revision": workspace_revision,
            "source_revision": source_revision,
            "dirty_fingerprint": dirty_fingerprint,
        },
        "status": _status(home_v1, problems),
        "freshness": {
            "workspace": "current",
            "adoption": adoption_value["freshness"],
            "session": (
                session.reference["freshness"]
                if session is not None
                else "unavailable"
            ),
            "capability_catalog": (
                capability_catalog.reference["freshness"]
                if capability_catalog is not None
                else "unavailable"
            ),
        },
        "owner_records": all_references,
        "session": _owner_projection(
            session,
            absent_reason="No validated C01 Work Session summary was supplied.",
        ),
        "capability_catalog": _owner_projection(
            capability_catalog,
            absent_reason="No validated product capability catalog was supplied.",
        ),
        "jobs": _jobs(
            home_v1,
            suite_root=suite,
            catalog=catalog,
            workspace_revision=workspace_revision,
            doctor_ref=doctor_ref,
            session=session,
            capability_catalog=capability_catalog,
            fixture_owner=fixture_owner,
            fixture_tool_context=(
                _cleanroom_fixture_tool_context(suite, fixture_owner)
                if fixture_owner is not None
                else None
            ),
            adoption_stale=adoption_stale,
        ),
        "catalog": {
            "format_version": "workbench-live-console-command-catalog-v2",
            "catalog_digest": catalog.catalog_digest,
        },
        "new_project": {
            "state": "available" if construction_available else "unavailable",
            "admitted_kinds": (
                [_CLEANROOM_CONSTRUCTION_KIND]
                if construction_available
                else []
            ),
            "owner_ref_ids": (
                [construction_owner.reference["id"]]
                if construction_available
                else []
            ),
            "blockers": (
                []
                if construction_available
                else ["NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE"]
            ),
            "reason": (
                "The Cleanroom profile owns one exact 19-file fresh-project "
                "constructor through Blueprints V2."
                if construction_available
                else "The exact profile owner record is unavailable or invalid; "
                "Home does not infer mod or pack construction authority."
            ),
            "next_safe_action": (
                "workbench new cleanroom-mod preview --help"
                if construction_available
                else "Restore the exact profile construction owner and reopen Home."
            ),
        },
        "adoption": adoption_value,
        "problems": problems,
        "base_home": home_v1,
        "limitations": [
            "Home V2 is a projection; owner records remain authoritative.",
            "Open and reopen perform no network, graph load, source mutation, build, runtime, or world mutation.",
            "Adopt writes only one private binding below the declared Workbench state root.",
        ],
    }
    result["home_id"] = _home_id(result)
    validate_workspace_home_v2(
        result,
        suite_root=suite,
        _current_catalog=catalog,
        _current_capability_catalog=(
            capability_catalog.payload
            if capability_catalog is not None
            and capability_catalog.reference["integrity"] == "verified"
            else None
        ),
    )
    return result


def build_workspace_home_v2(
    suite_root: Path | str,
    requested_path: Path | str,
    *,
    session_record: OwnerRecordPort | None = None,
    capability_catalog_record: OwnerRecordPort | None = None,
    owner_records: Sequence[OwnerRecordPort] = (),
) -> dict[str, Any]:
    """Open a workspace without creating or changing retained state."""

    return _compose_workspace_home_v2(
        suite_root,
        requested_path,
        session_record=session_record,
        capability_catalog_record=capability_catalog_record,
        owner_records=owner_records,
        operation="open",
        adoption=None,
    )


def load_product_capability_owner_port(
    repository_root: Path | str,
) -> OwnerRecordPort:
    """Adapt the live product catalog through Home's narrow input port."""

    from .product_capability_catalog import (
        FORMAT,
        load_product_capability_catalog,
        validate_product_capability_catalog,
    )

    root = Path(repository_root).expanduser().resolve(strict=True)
    value = load_product_capability_catalog(root)

    def validate(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
        # The loader has already validated this exact immutable owner value.
        # Home resolves the same port in several regions; rebuilding every
        # source-derived capability record would duplicate owner work.
        if type(candidate) is dict and candidate == value:
            return deepcopy(value)
        return validate_product_capability_catalog(
            candidate,
            repository_root=root,
        )

    return OwnerRecordPort(
        kind="product-capability-catalog",
        owner_id="workbench-shell",
        value=value,
        validator=validate,
        expected_format=FORMAT,
    )


def work_session_summary_owner_port(
    value: Mapping[str, Any],
) -> OwnerRecordPort:
    """Adapt one C01 status result through its public summary validator."""

    from .work_session import SUMMARY_FORMAT, validate_work_session_summary

    return OwnerRecordPort(
        kind="work-session",
        owner_id="workbench-shell",
        value=value,
        validator=validate_work_session_summary,
        expected_format=SUMMARY_FORMAT,
    )


def _state_root(suite_root: Path | str, state_root: Path | str | None) -> Path:
    suite = Path(suite_root).expanduser().resolve(strict=True)
    raw = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser()
    )
    if not raw.is_absolute():
        raw = suite / raw
    absolute = _absolute_without_following(raw)
    _reject_symlink_components(absolute, label="Workbench state root")
    return absolute


def _storage_paths(
    suite_root: Path | str,
    state_root: Path | str | None,
    *,
    create: bool,
) -> tuple[Path, Path, Path]:
    root = _state_root(suite_root, state_root)
    module_root = root / STATE_DIRECTORY
    bindings = module_root / "adoptions"
    locks = module_root / "locks"
    for path in (root, module_root, bindings, locks):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_dir():
                raise WorkspaceHomeV2Error(
                    f"Workspace Home state path is unsafe: {path}"
                )
        elif create:
            try:
                path.mkdir(mode=0o700)
                fsync_directory(path.parent)
            except FileExistsError:
                if path.is_symlink() or not path.is_dir():
                    raise WorkspaceHomeV2Error(
                        f"Workspace Home state path raced unsafely: {path}"
                    )
            except OSError as exc:
                raise WorkspaceHomeV2Error(
                    f"cannot create Workspace Home state directory: {path}"
                ) from exc
        elif path == root or path == module_root or path == bindings:
            raise WorkspaceHomeV2Error("Workspace Home adoption state is unavailable")
        if create:
            try:
                secure_private_path(path, directory=True)
            except (OSError, HostFilesystemError) as exc:
                raise WorkspaceHomeV2Error(
                    f"cannot secure Workspace Home state directory: {path}"
                ) from exc
        else:
            if not private_path(path, directory=True):
                raise WorkspaceHomeV2Error(
                    f"Workspace Home state directory is not owner-private: {path}"
                )
    return root, bindings, locks


def _binding_filename(binding_id: str) -> str:
    if not _BINDING_ID_RE.fullmatch(binding_id):
        raise WorkspaceHomeV2Error("Workspace Home binding ID is invalid")
    return binding_id.rsplit(":", 1)[-1] + ".json"


def workspace_home_binding_id(workspace_id: str) -> str:
    """Return the one canonical adoption binding identity for a workspace."""

    if type(workspace_id) is not str or _IDENTITY_RE.fullmatch(workspace_id) is None:
        raise WorkspaceHomeV2Error("Workspace Home workspace ID is invalid")
    return _identity(
        "workspace-home-binding",
        {"workspace_id": workspace_id},
    )


def _lock_path(locks: Path, binding_id: str) -> Path:
    return locks / (_binding_filename(binding_id).removesuffix(".json") + ".lock")


def _ensure_lease_file(path: Path, *, create: bool) -> None:
    created = False
    if path.exists() or path.is_symlink():
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise WorkspaceHomeV2Error(
                "cannot inspect Workspace Home lease file"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceHomeV2Error(
                "Workspace Home lease path must be a regular file"
            )
    elif create:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            return _ensure_lease_file(path, create=create)
        except OSError as exc:
            raise WorkspaceHomeV2Error(
                "cannot create Workspace Home lease file"
            ) from exc
        else:
            os.close(descriptor)
            created = True
    else:
        raise WorkspaceHomeV2Error("Workspace Home physical lease is unavailable")
    if create:
        try:
            secure_private_path(path, directory=False)
            if created:
                fsync_directory(path.parent)
        except (OSError, HostFilesystemError) as exc:
            raise WorkspaceHomeV2Error(
                "cannot secure Workspace Home lease file"
            ) from exc
    elif not private_path(path, directory=False):
        raise WorkspaceHomeV2Error(
            "Workspace Home lease file is not owner-private"
        )


@contextmanager
def _exclusive_state_lease(path: Path, *, create: bool):
    """Use the C01/Host Adapter physical lease; the file is not the lease."""

    _ensure_lease_file(path, create=create)
    try:
        from workbench_core.service.host import local_service_physical_lease_ports

        provider = local_service_physical_lease_ports()
        with provider.exclusive(path):
            yield
    except WorkspaceHomeV2Error:
        raise
    except Exception as exc:
        raise WorkspaceHomeV2Error(
            "Workspace Home physical state lease failed closed"
        ) from exc


def _interrupted_adoption_count(bindings: Path, binding_path: Path) -> int:
    prefix = f".{binding_path.name}."
    observed = 0
    try:
        candidates = sorted(bindings.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise WorkspaceHomeV2Error(
            "cannot inspect interrupted Workspace Home adoptions"
        ) from exc
    for candidate in candidates:
        if not candidate.name.startswith(prefix) or not candidate.name.endswith(".tmp"):
            continue
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise WorkspaceHomeV2Error(
                "cannot inspect interrupted Workspace Home adoption"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceHomeV2Error(
                "interrupted Workspace Home adoption is not a regular file"
            )
        observed += 1
    return observed


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical_bytes(value)
    if len(payload) > MAX_STATE_RECORD_BYTES:
        raise WorkspaceHomeV2Error("Workspace Home state record exceeds its bound")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            secure_private_path(temporary, directory=False)
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise WorkspaceHomeV2Error(
                "Workspace Home is already adopted; use reopen"
            ) from exc
        except HostFilesystemError as exc:
            raise WorkspaceHomeV2Error(
                "cannot secure Workspace Home adoption temporary"
            ) from exc
        fsync_directory(path.parent)
    except WorkspaceHomeV2Error:
        raise
    except OSError as exc:
        raise WorkspaceHomeV2Error("cannot publish Workspace Home adoption") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _adoption_state_revision(value: Mapping[str, Any]) -> str:
    projection = dict(value)
    projection.pop("state_revision", None)
    return _digest(projection)


def _retained_owner_revisions(
    owner_records: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Snapshot owner authority that must remain immutable across reopen.

    A Work Session is the retained navigation journal itself. Its summary
    revision necessarily advances when another frontend reopens the session or
    appends owner custody. The adoption pins that journal through ``session_id``
    and Home independently validates its workspace binding and integrity, so
    treating the summary digest as immutable makes every normal resume stale.
    """

    return {
        str(row["id"]): str(row["record_revision"])
        for row in owner_records
        if row.get("kind") != "work-session"
    }


def _validate_adoption(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "format",
        "schema_version",
        "binding_id",
        "workspace_id",
        "workspace_root",
        "workspace_revision",
        "source_revision",
        "session_id",
        "owner_record_revisions",
        "state_revision",
    }
    if set(value) != required:
        raise WorkspaceHomeV2Error("Workspace Home adoption fields are invalid")
    if (
        value.get("format") != ADOPTION_FORMAT
        or value.get("schema_version") != ADOPTION_SCHEMA_VERSION
        or not _BINDING_ID_RE.fullmatch(str(value.get("binding_id", "")))
        or not _IDENTITY_RE.fullmatch(str(value.get("workspace_id", "")))
        or not isinstance(value.get("workspace_root"), str)
        or not value["workspace_root"]
        or not _DIGEST_RE.fullmatch(str(value.get("workspace_revision", "")))
        or not isinstance(value.get("source_revision"), str)
        or not value["source_revision"]
        or (
            value.get("session_id") is not None
            and not _SESSION_ID_RE.fullmatch(str(value.get("session_id")))
        )
        or not isinstance(value.get("owner_record_revisions"), dict)
        or any(
            not _IDENTITY_RE.fullmatch(str(key))
            or not _DIGEST_RE.fullmatch(str(revision))
            for key, revision in value["owner_record_revisions"].items()
        )
        or value.get("state_revision") != _adoption_state_revision(value)
        or value.get("binding_id")
        != workspace_home_binding_id(str(value.get("workspace_id", "")))
    ):
        raise WorkspaceHomeV2Error("Workspace Home adoption is corrupt")
    return dict(value)


def _read_adoption(path: Path) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        before = os.stat(path, follow_symlinks=False)
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption must be a regular file"
            )
        if os.name != "nt" and (
            opened.st_mode & 0o077
            or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
        ):
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption is not owner-private"
            )
        if opened.st_size < 2 or opened.st_size > MAX_STATE_RECORD_BYTES:
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption is outside its byte bound"
            )
        raw = b""
        while len(raw) <= MAX_STATE_RECORD_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, MAX_STATE_RECORD_BYTES + 1 - len(raw)),
            )
            if not chunk:
                break
            raw += chunk
        closed = os.fstat(descriptor)
        if not private_path(path, directory=False):
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption is not owner-private"
            )
        current = os.stat(path, follow_symlinks=False)
        if (
            len(raw) > MAX_STATE_RECORD_BYTES
            or len(raw) != opened.st_size
            or not _stable_file_custody(before, opened, closed, current)
        ):
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption changed while being read"
            )
        value = json.loads(raw.decode("utf-8", "strict"))
    except WorkspaceHomeV2Error:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceHomeV2Error("Workspace Home adoption is corrupt") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise WorkspaceHomeV2Error("Workspace Home adoption is not an object")
    validated = _validate_adoption(value)
    if raw != _canonical_bytes(validated):
        raise WorkspaceHomeV2Error("Workspace Home adoption is noncanonical")
    return validated


def _set_home_operation(
    home: dict[str, Any],
    *,
    operation: str,
    local_state_effect: str,
    adoption: Mapping[str, Any],
) -> dict[str, Any]:
    home = deepcopy(home)
    home["operation"] = operation
    home["local_state_effect"] = local_state_effect
    home["adoption"] = dict(adoption)
    home["freshness"]["adoption"] = adoption["freshness"]
    adoption_stale = adoption["freshness"] == "stale"
    adoption_recovery = adoption["recovery_state"] == "required"
    move_recovery = (
        "WORKSPACE_LOCATION_CHANGED" in adoption["recovery_reasons"]
    )
    existing = {
        row["id"]: row
        for row in home["problems"]
        if row["id"]
        not in {"ADOPTION_BINDING_STALE", "ADOPTION_RECOVERY_REQUIRED"}
    }
    if adoption_stale:
        existing["ADOPTION_BINDING_STALE"] = {
            "id": "ADOPTION_BINDING_STALE",
            "severity": "warning",
            "detail": (
                "The adopted workspace revision differs from the current Project "
                "Intelligence observation."
            ),
        }
    if adoption_recovery:
        existing["ADOPTION_RECOVERY_REQUIRED"] = {
            "id": "ADOPTION_RECOVERY_REQUIRED",
            "severity": "warning",
            "detail": (
                "Interrupted adoption temporary bytes remain isolated and require "
                "an explicit recovery decision."
            ),
        }
    home["problems"] = [existing[key] for key in sorted(existing)]
    for job in home["jobs"]:
        if (adoption_stale or adoption_recovery) and (
            job["id"] != "workspace-health" or move_recovery
        ):
            required = set(job["blockers"])
            if adoption_stale:
                required.add("ADOPTION_BINDING_STALE")
            if adoption_recovery:
                required.add("ADOPTION_RECOVERY_REQUIRED")
            blockers = sorted(required)
            job["state"] = "unavailable"
            job["argv"] = None
            job["blockers"] = blockers
            job["unavailable_reason"] = (
                "The adoption requires an explicit recovery decision."
                if adoption_recovery
                else "The adopted workspace revision differs from the current workspace."
            )
            job["eligibility_digest"] = _eligibility_digest(job)
    home["status"] = _status(home["base_home"], home["problems"])
    home["home_id"] = _home_id(home)
    validate_workspace_home_v2(home)
    return home


def adopt_workspace_home_v2(
    suite_root: Path | str,
    requested_path: Path | str,
    *,
    state_root: Path | str | None = None,
    session_record: OwnerRecordPort | None = None,
    capability_catalog_record: OwnerRecordPort | None = None,
    owner_records: Sequence[OwnerRecordPort] = (),
) -> dict[str, Any]:
    """Persist one private binding without writing the target checkout."""

    home = build_workspace_home_v2(
        suite_root,
        requested_path,
        session_record=session_record,
        capability_catalog_record=capability_catalog_record,
        owner_records=owner_records,
    )
    _, bindings, locks = _storage_paths(
        suite_root,
        state_root,
        create=True,
    )
    binding_id = workspace_home_binding_id(home["workspace"]["workspace_id"])
    binding_path = bindings / _binding_filename(binding_id)
    lock_path = _lock_path(locks, binding_id)
    with _exclusive_state_lease(lock_path, create=True):
        interrupted_write_count = _interrupted_adoption_count(
            bindings,
            binding_path,
        )
        owner_revisions = _retained_owner_revisions(home["owner_records"])
        adoption_record: dict[str, Any] = {
            "format": ADOPTION_FORMAT,
            "schema_version": ADOPTION_SCHEMA_VERSION,
            "binding_id": binding_id,
            "workspace_id": home["workspace"]["workspace_id"],
            "workspace_root": home["workspace"]["root"],
            "workspace_revision": home["workspace"]["workspace_revision"],
            "source_revision": home["workspace"]["source_revision"],
            "session_id": home["session"]["session_id"],
            "owner_record_revisions": owner_revisions,
            "state_revision": "pending",
        }
        adoption_record["state_revision"] = _adoption_state_revision(
            adoption_record
        )
        _validate_adoption(adoption_record)
        _atomic_private_json(binding_path, adoption_record)
    return _set_home_operation(
        home,
        operation="adopt",
        local_state_effect="adoption-binding-created",
        adoption={
            "state": "adopted",
            "binding_id": binding_id,
            "session_id": adoption_record["session_id"],
            "state_revision": adoption_record["state_revision"],
            "adopted_workspace_id": adoption_record["workspace_id"],
            "adopted_workspace_root": adoption_record["workspace_root"],
            "freshness": "current",
            "stale_reasons": [],
            "recovery_state": (
                "required" if interrupted_write_count else "none"
            ),
            "recovery_reasons": (
                ["INTERRUPTED_ADOPTION_WRITE"]
                if interrupted_write_count
                else []
            ),
            "interrupted_write_count": interrupted_write_count,
        },
    )


def reopen_workspace_home_v2(
    suite_root: Path | str,
    binding_id: str,
    *,
    state_root: Path | str | None = None,
    replacement_workspace_path: Path | str | None = None,
    session_record: OwnerRecordPort | None = None,
    capability_catalog_record: OwnerRecordPort | None = None,
    owner_records: Sequence[OwnerRecordPort] = (),
) -> dict[str, Any]:
    """Reopen one exact private binding and expose revision drift."""

    _, bindings, locks = _storage_paths(
        suite_root,
        state_root,
        create=False,
    )
    binding_path = bindings / _binding_filename(binding_id)
    lock_path = _lock_path(locks, binding_id)
    with _exclusive_state_lease(lock_path, create=False):
        adoption_record = _read_adoption(binding_path)
        interrupted_write_count = _interrupted_adoption_count(
            bindings,
            binding_path,
        )
        if adoption_record["binding_id"] != binding_id:
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption names another binding"
            )
        home = build_workspace_home_v2(
            suite_root,
            (
                adoption_record["workspace_root"]
                if replacement_workspace_path is None
                else replacement_workspace_path
            ),
            session_record=session_record,
            capability_catalog_record=capability_catalog_record,
            owner_records=owner_records,
        )
        if (
            replacement_workspace_path is None
            and home["workspace"]["workspace_id"]
            != adoption_record["workspace_id"]
        ):
            raise WorkspaceHomeV2Error(
                "adopted workspace identity differs at its retained path"
            )
        if home["session"]["session_id"] != adoption_record["session_id"]:
            raise WorkspaceHomeV2Error(
                "reopen Work Session differs from the adopted session association"
            )
        current_owner_revisions = _retained_owner_revisions(
            home["owner_records"]
        )
        stale_reasons: list[str] = []
        if home["workspace"]["workspace_id"] != adoption_record["workspace_id"]:
            stale_reasons.append("WORKSPACE_ID_CHANGED")
        if home["workspace"]["root"] != adoption_record["workspace_root"]:
            stale_reasons.append("WORKSPACE_ROOT_CHANGED")
        if (
            home["workspace"]["workspace_revision"]
            != adoption_record["workspace_revision"]
        ):
            stale_reasons.append("WORKSPACE_REVISION_CHANGED")
        if home["workspace"]["source_revision"] != adoption_record["source_revision"]:
            stale_reasons.append("SOURCE_REVISION_CHANGED")
        if current_owner_revisions != adoption_record["owner_record_revisions"]:
            stale_reasons.append("OWNER_RECORD_REVISIONS_CHANGED")
        freshness = "stale" if stale_reasons else "current"
        recovery_reasons = (
            ["INTERRUPTED_ADOPTION_WRITE"]
            if interrupted_write_count
            else []
        )
        if any(
            reason in {"WORKSPACE_ID_CHANGED", "WORKSPACE_ROOT_CHANGED"}
            for reason in stale_reasons
        ):
            recovery_reasons.append("WORKSPACE_LOCATION_CHANGED")
        return _set_home_operation(
            home,
            operation="reopen",
            local_state_effect="none",
            adoption={
                "state": "adopted",
                "binding_id": binding_id,
                "session_id": adoption_record["session_id"],
                "state_revision": adoption_record["state_revision"],
                "adopted_workspace_id": adoption_record["workspace_id"],
                "adopted_workspace_root": adoption_record["workspace_root"],
                "freshness": freshness,
                "stale_reasons": sorted(stale_reasons),
                "recovery_state": (
                    "required" if recovery_reasons else "none"
                ),
                "recovery_reasons": sorted(recovery_reasons),
                "interrupted_write_count": interrupted_write_count,
            },
        )


def load_workspace_home_adoption(
    suite_root: Path | str,
    binding_id: str,
    *,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read one exact validated adoption without changing retained state."""

    _, bindings, locks = _storage_paths(
        suite_root,
        state_root,
        create=False,
    )
    binding_path = bindings / _binding_filename(binding_id)
    lock_path = _lock_path(locks, binding_id)
    with _exclusive_state_lease(lock_path, create=False):
        adoption = _read_adoption(binding_path)
        if adoption["binding_id"] != binding_id:
            raise WorkspaceHomeV2Error(
                "Workspace Home adoption names another binding"
            )
        return _json_copy(
            adoption,
            label="Workspace Home adoption",
            maximum_bytes=MAX_STATE_RECORD_BYTES,
        )


def workspace_home_adoption_exists(
    suite_root: Path | str,
    binding_id: str,
    *,
    state_root: Path | str | None = None,
) -> bool:
    """Return false only for absent state; validate every retained binding."""

    _binding_filename(binding_id)
    root = _state_root(suite_root, state_root)
    module_root = root / STATE_DIRECTORY
    bindings = module_root / "adoptions"
    locks = module_root / "locks"

    def present_private_directory(path: Path) -> bool:
        if not path.exists() and not path.is_symlink():
            return False
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise WorkspaceHomeV2Error(
                f"cannot inspect Workspace Home state directory: {path}"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode) or not private_path(
            path,
            directory=True,
        ):
            raise WorkspaceHomeV2Error(
                f"Workspace Home state directory is unsafe: {path}"
            )
        return True

    for directory in (root, module_root, bindings):
        if not present_private_directory(directory):
            return False
    binding_path = bindings / _binding_filename(binding_id)
    if not binding_path.exists() and not binding_path.is_symlink():
        return False
    if not present_private_directory(locks):
        raise WorkspaceHomeV2Error("Workspace Home binding has no physical lease store")
    load_workspace_home_adoption(
        suite_root,
        binding_id,
        state_root=state_root,
    )
    return True


def _validate_owner_reference(reference: Mapping[str, Any]) -> None:
    required = {
        "id",
        "kind",
        "owner_id",
        "record_format",
        "record_id",
        "session_id",
        "catalog_id",
        "record_revision",
        "record_digest",
        "bound_workspace_revision",
        "freshness",
        "integrity",
        "validation_problem",
    }
    if set(reference) != required:
        raise WorkspaceHomeV2Error("Workspace Home owner reference fields are invalid")
    kind = reference.get("kind")
    owner_id = reference.get("owner_id")
    record_digest = reference.get("record_digest")
    record_id = reference.get("record_id")
    session_id = reference.get("session_id")
    catalog_id = reference.get("catalog_id")
    bound_revision = reference.get("bound_workspace_revision")
    validation_problem = reference.get("validation_problem")
    if (
        not isinstance(kind, str)
        or _IDENTITY_RE.fullmatch(kind) is None
        or not isinstance(owner_id, str)
        or _IDENTITY_RE.fullmatch(owner_id) is None
        or not isinstance(reference.get("record_format"), str)
        or not reference["record_format"]
        or not isinstance(record_digest, str)
        or _DIGEST_RE.fullmatch(record_digest) is None
        or reference.get("record_revision") != record_digest
        or (
            record_id is not None
            and (
                not isinstance(record_id, str)
                or _IDENTITY_RE.fullmatch(record_id) is None
            )
        )
        or (
            session_id is not None
            and (
                not isinstance(session_id, str)
                or _SESSION_ID_RE.fullmatch(session_id) is None
            )
        )
        or (
            catalog_id is not None
            and (
                not isinstance(catalog_id, str)
                or _CAPABILITY_CATALOG_ID_RE.fullmatch(catalog_id) is None
            )
        )
        or (
            bound_revision is not None
            and (
                not isinstance(bound_revision, str)
                or _DIGEST_RE.fullmatch(bound_revision) is None
            )
        )
        or reference.get("freshness")
        not in {"current", "stale", "corrupt", "not-applicable"}
        or reference.get("integrity") not in {"verified", "failed"}
        or validation_problem not in {None, "OWNER_VALIDATION_FAILED"}
    ):
        raise WorkspaceHomeV2Error("Workspace Home owner reference is invalid")
    expected_id = _identity(
        "owner-ref",
        {"kind": kind, "owner_id": owner_id, "record_digest": record_digest},
    )
    if reference.get("id") != expected_id:
        raise WorkspaceHomeV2Error("Workspace Home owner reference identity is stale")
    if validation_problem is not None:
        if (
            reference["freshness"] != "corrupt"
            or reference["integrity"] != "failed"
            or any(item is not None for item in (record_id, session_id, catalog_id))
        ):
            raise WorkspaceHomeV2Error(
                "rejected Home owner record is not isolated"
            )
    elif reference["integrity"] == "failed":
        if reference["freshness"] != "corrupt":
            raise WorkspaceHomeV2Error("failed Home owner integrity is not isolated")
    elif reference["freshness"] == "corrupt":
        raise WorkspaceHomeV2Error("corrupt Home owner reference claims integrity")
    if kind == "work-session" and validation_problem is None:
        if record_id is None or session_id is None or catalog_id is not None:
            raise WorkspaceHomeV2Error(
                "Work Session owner reference lost its exact source identities"
            )
    elif kind == "product-capability-catalog" and validation_problem is None:
        if record_id != catalog_id or catalog_id is None or session_id is not None:
            raise WorkspaceHomeV2Error(
                "product catalog owner reference lost its exact identity"
            )
    elif kind not in {"work-session", "product-capability-catalog"} and (
        session_id is not None or catalog_id is not None
    ):
        raise WorkspaceHomeV2Error("unrelated owner reference carries a reserved identity")


def _validate_owner_projection(
    projection: Any,
    *,
    kind: str,
    owner_records: Sequence[Mapping[str, Any]],
) -> None:
    required = {
        "state",
        "owner_ref_id",
        "record_id",
        "session_id",
        "catalog_id",
        "record_revision",
        "freshness",
        "reason",
    }
    if not isinstance(projection, Mapping) or set(projection) != required:
        raise WorkspaceHomeV2Error(f"Home {kind} projection fields are invalid")
    matches = [row for row in owner_records if row["kind"] == kind]
    if len(matches) > 1:
        raise WorkspaceHomeV2Error(f"Home has multiple {kind} owners")
    if not matches:
        if (
            projection["state"] != "unavailable"
            or projection["owner_ref_id"] is not None
            or projection["record_id"] is not None
            or projection["session_id"] is not None
            or projection["catalog_id"] is not None
            or projection["record_revision"] is not None
            or projection["freshness"] != "unavailable"
            or not isinstance(projection["reason"], str)
            or not projection["reason"]
        ):
            raise WorkspaceHomeV2Error(f"absent Home {kind} owner was inferred")
        return
    reference = matches[0]
    state = (
        "available"
        if reference["freshness"] in {"current", "not-applicable"}
        else "unavailable"
    )
    expected_reason = (
        None
        if state == "available"
        else f"The validated owner record is {reference['freshness']}."
    )
    if projection != {
        "state": state,
        "owner_ref_id": reference["id"],
        "record_id": reference["record_id"],
        "session_id": reference["session_id"],
        "catalog_id": reference["catalog_id"],
        "record_revision": reference["record_revision"],
        "freshness": reference["freshness"],
        "reason": expected_reason,
    }:
        raise WorkspaceHomeV2Error(
            f"Home {kind} projection differs from its owner reference"
        )


def _validate_adoption_projection(
    value: Any,
    *,
    operation: str,
    session_id: Any,
    workspace: Mapping[str, Any],
) -> None:
    required = {
        "state",
        "binding_id",
        "session_id",
        "state_revision",
        "adopted_workspace_id",
        "adopted_workspace_root",
        "freshness",
        "stale_reasons",
        "recovery_state",
        "recovery_reasons",
        "interrupted_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise WorkspaceHomeV2Error("Home adoption projection fields are invalid")
    interrupted_count = value.get("interrupted_write_count")
    stale_reasons = value.get("stale_reasons")
    recovery_reasons = value.get("recovery_reasons")
    if (
        type(interrupted_count) is not int
        or interrupted_count < 0
        or value.get("recovery_state") not in {"none", "required"}
        or type(recovery_reasons) is not list
        or any(not isinstance(reason, str) for reason in recovery_reasons)
        or recovery_reasons != sorted(set(recovery_reasons))
        or any(
            reason
            not in {
                "INTERRUPTED_ADOPTION_WRITE",
                "WORKSPACE_LOCATION_CHANGED",
            }
            for reason in recovery_reasons
        )
        or (value["recovery_state"] == "required") != bool(recovery_reasons)
        or ("INTERRUPTED_ADOPTION_WRITE" in recovery_reasons)
        != (interrupted_count > 0)
        or type(stale_reasons) is not list
        or any(not isinstance(reason, str) for reason in stale_reasons)
        or stale_reasons != sorted(set(stale_reasons))
        or any(
            reason
            not in {
                "WORKSPACE_REVISION_CHANGED",
                "WORKSPACE_ID_CHANGED",
                "WORKSPACE_ROOT_CHANGED",
                "SOURCE_REVISION_CHANGED",
                "OWNER_RECORD_REVISIONS_CHANGED",
            }
            for reason in stale_reasons
        )
    ):
        raise WorkspaceHomeV2Error("Home adoption recovery count is invalid")
    if operation == "open":
        if value != {
            "state": "unadopted",
            "binding_id": None,
            "session_id": None,
            "state_revision": None,
            "adopted_workspace_id": None,
            "adopted_workspace_root": None,
            "freshness": "not-applicable",
            "stale_reasons": [],
            "recovery_state": "none",
            "recovery_reasons": [],
            "interrupted_write_count": 0,
        }:
            raise WorkspaceHomeV2Error("read-only Home open invented adoption state")
        return
    if (
        value.get("state") != "adopted"
        or not isinstance(value.get("binding_id"), str)
        or _BINDING_ID_RE.fullmatch(value["binding_id"]) is None
        or not isinstance(value.get("state_revision"), str)
        or _DIGEST_RE.fullmatch(value["state_revision"]) is None
        or not isinstance(value.get("adopted_workspace_id"), str)
        or _IDENTITY_RE.fullmatch(value["adopted_workspace_id"]) is None
        or not isinstance(value.get("adopted_workspace_root"), str)
        or not value["adopted_workspace_root"]
        or value.get("freshness") not in {"current", "stale"}
        or (value["freshness"] == "stale") != bool(stale_reasons)
        or value.get("session_id") != session_id
    ):
        raise WorkspaceHomeV2Error("Home adoption projection is invalid")
    workspace_id_changed = (
        workspace["workspace_id"] != value["adopted_workspace_id"]
    )
    workspace_root_changed = (
        workspace["root"] != value["adopted_workspace_root"]
    )
    location_changed = workspace_id_changed or workspace_root_changed
    if (
        ("WORKSPACE_ID_CHANGED" in stale_reasons) != workspace_id_changed
        or ("WORKSPACE_ROOT_CHANGED" in stale_reasons)
        != workspace_root_changed
        or ("WORKSPACE_LOCATION_CHANGED" in recovery_reasons)
        != location_changed
        or value["binding_id"]
        != workspace_home_binding_id(value["adopted_workspace_id"])
    ):
        raise WorkspaceHomeV2Error(
            "Home adoption move projection is inconsistent"
        )
    if operation == "adopt" and (
        value["freshness"] != "current" or stale_reasons or location_changed
    ):
        raise WorkspaceHomeV2Error("new Home adoption cannot already be stale")


def validate_workspace_home_v2(
    value: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
    _current_catalog: Any | None = None,
    _current_capability_catalog: Mapping[str, Any] | None = None,
) -> None:
    """Reject drifted Home V2 projections before a strict client uses them."""

    if type(value) is not dict:
        raise WorkspaceHomeV2Error("Workspace Home V2 must be an ordinary object")
    required = {
        "format",
        "schema_version",
        "home_id",
        "operation",
        "read_only",
        "local_state_effect",
        "workspace",
        "status",
        "freshness",
        "owner_records",
        "session",
        "capability_catalog",
        "jobs",
        "catalog",
        "new_project",
        "adoption",
        "problems",
        "base_home",
        "limitations",
    }
    if set(value) != required:
        raise WorkspaceHomeV2Error("Workspace Home V2 fields are invalid")
    if (
        value.get("format") != HOME_V2_FORMAT
        or value.get("schema_version") != HOME_V2_SCHEMA_VERSION
        or value.get("operation") not in {"open", "adopt", "reopen"}
        or value.get("read_only") is not True
        or value.get("local_state_effect")
        not in {"none", "adoption-binding-created"}
    ):
        raise WorkspaceHomeV2Error("Workspace Home V2 identity is invalid")
    try:
        validate_workspace_home(value["base_home"])
    except (KeyError, TypeError, WorkspaceHomeError) as exc:
        raise WorkspaceHomeV2Error("Workspace Home V2 base Home is invalid") from exc
    workspace = value.get("workspace")
    expected_workspace_fields = set(value["base_home"]["workspace"]) | {
        "workspace_id",
        "workspace_revision",
        "source_revision",
        "dirty_fingerprint",
    }
    if (
        type(workspace) is not dict
        or set(workspace) != expected_workspace_fields
        or not _IDENTITY_RE.fullmatch(str(workspace.get("workspace_id", "")))
        or not _DIGEST_RE.fullmatch(str(workspace.get("workspace_revision", "")))
        or not isinstance(workspace.get("source_revision"), str)
        or not workspace["source_revision"]
        or not _DIGEST_RE.fullmatch(str(workspace.get("dirty_fingerprint", "")))
    ):
        raise WorkspaceHomeV2Error("Workspace Home V2 workspace identity is invalid")
    if any(
        workspace.get(key) != item
        for key, item in value["base_home"]["workspace"].items()
    ):
        raise WorkspaceHomeV2Error("Workspace Home V2 changes its V1 root")
    catalog_ref = value.get("catalog")
    if (
        type(catalog_ref) is not dict
        or set(catalog_ref) != {"format_version", "catalog_digest"}
        or catalog_ref.get("format_version")
        != "workbench-live-console-command-catalog-v2"
        or not _DIGEST_RE.fullmatch(str(catalog_ref.get("catalog_digest", "")))
    ):
        raise WorkspaceHomeV2Error("Workspace Home V2 catalog binding is invalid")
    current_catalog = _current_catalog
    current_root = None
    if suite_root is not None:
        current_root = Path(suite_root).expanduser().resolve(strict=True)
        if current_catalog is None:
            from .catalog import build_catalog

            current_catalog = build_catalog(current_root)
        if current_catalog.catalog_digest != catalog_ref["catalog_digest"]:
            raise WorkspaceHomeV2Error("Workspace Home V2 catalog binding is stale")
    owner_records = value.get("owner_records")
    if type(owner_records) is not list or not owner_records:
        raise WorkspaceHomeV2Error("Workspace Home V2 has no owner references")
    owner_ids: set[str] = set()
    for reference in owner_records:
        if type(reference) is not dict:
            raise WorkspaceHomeV2Error("Workspace Home owner reference is invalid")
        _validate_owner_reference(reference)
        reference_id = reference.get("id")
        if reference_id in owner_ids:
            raise WorkspaceHomeV2Error("Workspace Home owner reference is invalid")
        owner_ids.add(reference_id)
    if owner_records != sorted(
        owner_records,
        key=lambda row: (row["kind"], row["owner_id"], row["id"]),
    ):
        raise WorkspaceHomeV2Error("Workspace Home owner references are not canonical")
    doctor_refs = [
        row
        for row in owner_records
        if row["kind"] == "workspace-context"
        and row["owner_id"] == "project-intelligence"
    ]
    if len(doctor_refs) != 1:
        raise WorkspaceHomeV2Error(
            "Workspace Home V2 lacks one Project Intelligence owner reference"
        )
    doctor_ref = doctor_refs[0]
    expected_workspace = _workspace_identity(
        value["base_home"],
        doctor_ref["record_digest"],
        dirty_fingerprint=workspace["dirty_fingerprint"],
    )
    if (
        (
            workspace["workspace_id"],
            workspace["workspace_revision"],
            workspace["source_revision"],
            workspace["dirty_fingerprint"],
        )
        != expected_workspace
        or doctor_ref["bound_workspace_revision"]
        != workspace["workspace_revision"]
        or doctor_ref["freshness"] != "current"
        or doctor_ref["integrity"] != "verified"
        or doctor_ref["validation_problem"] is not None
    ):
        raise WorkspaceHomeV2Error(
            "Workspace Home V2 differs from its Project Intelligence revision"
        )
    if suite_root is not None:
        current_workspace = _safe_requested_path(workspace["root"])
        current_dirty_fingerprint = _workspace_content_fingerprint(
            value["base_home"],
            current_workspace,
        )
        if current_dirty_fingerprint != workspace["dirty_fingerprint"]:
            raise WorkspaceHomeV2Error(
                "Workspace Home V2 source bytes changed during validation"
            )
    fixture_refs = [
        row
        for row in owner_records
        if row["kind"] == "cleanroom-fixture-lock"
        and row["owner_id"] == "cleanroom-platform-profile"
    ]
    fixture_owner_ref: Mapping[str, Any] | None = None
    current_fixture_tool_context: Mapping[str, Any] | None = None
    if current_root is not None:
        expected_fixture_owner = _cleanroom_fixture_owner_resolution(
            workspace_root=workspace["root"],
            workspace_revision=workspace["workspace_revision"],
        )
        if expected_fixture_owner is None:
            if fixture_refs:
                raise WorkspaceHomeV2Error(
                    "Home invented Cleanroom fixture authority for another workspace"
                )
        elif (
            len(fixture_refs) != 1
            or fixture_refs[0] != expected_fixture_owner.reference
        ):
            raise WorkspaceHomeV2Error(
                "Home Cleanroom fixture owner reference is stale"
            )
        else:
            fixture_owner_ref = fixture_refs[0]
            current_fixture_tool_context = _cleanroom_fixture_tool_context(
                current_root,
                expected_fixture_owner,
            )
    elif len(fixture_refs) > 1:
        raise WorkspaceHomeV2Error(
            "Home has duplicate Cleanroom fixture owner references"
        )
    elif fixture_refs:
        fixture_owner_ref = fixture_refs[0]
    construction_refs = [
        row
        for row in owner_records
        if row["kind"] == "new-project-construction"
        and row["owner_id"] == "cleanroom-platform-profile"
    ]
    construction_owner_ref: Mapping[str, Any] | None = None
    if current_root is not None:
        expected_construction_owner = _cleanroom_construction_owner_resolution(
            current_root,
            workspace_root=workspace["root"],
            workspace_revision=workspace["workspace_revision"],
            source_revision=workspace["source_revision"],
            dirty_fingerprint=workspace["dirty_fingerprint"],
        )
        if (
            len(construction_refs) != 1
            or construction_refs[0] != expected_construction_owner.reference
        ):
            raise WorkspaceHomeV2Error(
                "Home Cleanroom construction owner reference is stale"
            )
        construction_owner_ref = construction_refs[0]
    elif len(construction_refs) != 1:
        raise WorkspaceHomeV2Error(
            "Home requires one Cleanroom construction owner reference"
        )
    else:
        construction_owner_ref = construction_refs[0]
    _validate_owner_projection(
        value.get("session"),
        kind="work-session",
        owner_records=owner_records,
    )
    _validate_owner_projection(
        value.get("capability_catalog"),
        kind="product-capability-catalog",
        owner_records=owner_records,
    )
    current_capability_catalog: Mapping[str, Any] | None = _current_capability_catalog
    if current_root is not None and value["capability_catalog"]["catalog_id"] is not None:
        if current_capability_catalog is None:
            from .product_capability_catalog import (
                load_product_capability_catalog,
            )

            current_capability_catalog = load_product_capability_catalog(current_root)
        if (
            current_capability_catalog["catalog_id"]
            != value["capability_catalog"]["catalog_id"]
        ):
            raise WorkspaceHomeV2Error("Home capability catalog identity is stale")
    freshness = value.get("freshness")
    if freshness != {
        "workspace": "current",
        "adoption": value["adoption"].get("freshness")
        if isinstance(value.get("adoption"), Mapping)
        else None,
        "session": value["session"]["freshness"],
        "capability_catalog": value["capability_catalog"]["freshness"],
    }:
        raise WorkspaceHomeV2Error("Workspace Home V2 freshness is inconsistent")
    _validate_adoption_projection(
        value.get("adoption"),
        operation=value["operation"],
        session_id=value["session"]["session_id"],
        workspace=workspace,
    )
    expected_effect = (
        "adoption-binding-created" if value["operation"] == "adopt" else "none"
    )
    if value["local_state_effect"] != expected_effect:
        raise WorkspaceHomeV2Error("Workspace Home V2 state effect is inconsistent")
    if value["operation"] != "open" and value["adoption"]["binding_id"] != (
        workspace_home_binding_id(value["adoption"]["adopted_workspace_id"])
    ):
        raise WorkspaceHomeV2Error("Workspace Home V2 adoption binding is stale")
    problems = value.get("problems")
    if type(problems) is not list or len(problems) > 128:
        raise WorkspaceHomeV2Error("Workspace Home V2 problems are invalid")
    problem_ids: set[str] = set()
    for problem in problems:
        if (
            type(problem) is not dict
            or set(problem) != {"id", "severity", "detail"}
            or not _IDENTITY_RE.fullmatch(str(problem.get("id", "")))
            or problem["id"] in problem_ids
            or problem.get("severity") not in {"info", "warning", "blocker"}
            or not isinstance(problem.get("detail"), str)
            or not problem["detail"]
        ):
            raise WorkspaceHomeV2Error("Workspace Home V2 problem is invalid")
        problem_ids.add(problem["id"])
    if problems != sorted(problems, key=lambda row: row["id"]):
        raise WorkspaceHomeV2Error("Workspace Home V2 problems are not canonical")
    if value.get("status") != _status(value["base_home"], problems):
        raise WorkspaceHomeV2Error("Workspace Home V2 status is inconsistent")
    jobs = value.get("jobs")
    if type(jobs) is not list or not jobs or len(jobs) > HOME_ACTION_LIMIT:
        raise WorkspaceHomeV2Error("Workspace Home V2 must expose one to five jobs")
    if jobs != sorted(jobs, key=lambda row: (row["rank"], row["id"])):
        raise WorkspaceHomeV2Error("Workspace Home V2 jobs are not deterministically ranked")
    projected_actions = _v2_actions(
        value["base_home"],
        include_cleanroom_fixture=fixture_owner_ref is not None,
    )
    base_actions = {action["id"]: action for action in projected_actions}
    expected_job_ids = [
        action["id"]
        for action in sorted(
            projected_actions,
            key=lambda item: (_JOB_PRIORITIES.get(item["id"], 1000), item["id"]),
        )[:HOME_ACTION_LIMIT]
    ]
    if [job.get("id") for job in jobs] != expected_job_ids:
        raise WorkspaceHomeV2Error("Workspace Home V2 job membership drifted from V1")
    session_revision = value["session"]["record_revision"]
    capability_catalog_revision = value["capability_catalog"]["record_revision"]
    owner_records_by_id = {row["id"]: row for row in owner_records}
    capability_catalog_ref = owner_records_by_id.get(
        value["capability_catalog"]["owner_ref_id"]
    )
    job_ids: set[str] = set()
    for job in jobs:
        required_job_fields = {
            "id",
            "rank",
            "title",
            "purpose",
            "state",
            "argv",
            "blockers",
            "unavailable_reason",
            "owner_ref_ids",
            "eligibility_binding",
            "command_id",
            "catalog_digest",
            "action_digest",
            "capability_id",
            "capability_key",
            "capability",
            "availability_basis",
            "tool_inputs",
            "next_safe_action",
            "arguments",
            "eligibility_digest",
        }
        if type(job) is not dict or set(job) != required_job_fields:
            raise WorkspaceHomeV2Error("Workspace Home V2 job is invalid")
        job_id = job.get("id")
        blockers = job.get("blockers")
        binding = job.get("eligibility_binding")
        owner_ref_ids = job.get("owner_ref_ids")
        command_id = job.get("command_id")
        context_dependent = job_id != "workspace-health"
        capability_dependent = command_id is not None
        expected_owner_ids = [doctor_ref["id"]]
        if context_dependent and value["session"]["owner_ref_id"] is not None:
            expected_owner_ids.append(value["session"]["owner_ref_id"])
        if (
            capability_dependent
            and value["capability_catalog"]["owner_ref_id"] is not None
        ):
            expected_owner_ids.append(value["capability_catalog"]["owner_ref_id"])
        if job_id == "cleanroom-fixture-build" and fixture_owner_ref is not None:
            expected_owner_ids.append(fixture_owner_ref["id"])
        if (
            not _IDENTITY_RE.fullmatch(str(job_id or ""))
            or job_id in job_ids
            or type(job.get("rank")) is not int
            or job["rank"] != _JOB_PRIORITIES.get(job_id, 1000)
            or job.get("title") != base_actions[job_id]["title"]
            or job.get("purpose") != base_actions[job_id]["purpose"]
            or job.get("state") not in {"available", "unavailable"}
            or type(blockers) is not list
            or blockers != sorted(set(blockers))
            or type(owner_ref_ids) is not list
            or owner_ref_ids != sorted(set(owner_ref_ids))
            or owner_ref_ids != sorted(expected_owner_ids)
            or type(binding) is not dict
            or set(binding)
            != {
                "workspace_revision",
                "session_revision",
                "capability_catalog_revision",
            }
            or binding.get("workspace_revision") != workspace["workspace_revision"]
            or binding.get("session_revision")
            != (session_revision if context_dependent else None)
            or binding.get("capability_catalog_revision")
            != (capability_catalog_revision if capability_dependent else None)
            or any(ref_id not in owner_ids for ref_id in owner_ref_ids)
            or job.get("catalog_digest") != value["catalog"]["catalog_digest"]
            or job.get("eligibility_digest") != _eligibility_digest(job)
        ):
            raise WorkspaceHomeV2Error("Workspace Home V2 job is inconsistent")
        if job["state"] == "available":
            if (
                blockers
                or not isinstance(job.get("argv"), list)
                or not job["argv"]
                or not _IDENTITY_RE.fullmatch(str(job.get("command_id") or ""))
                or not _DIGEST_RE.fullmatch(str(job.get("action_digest") or ""))
                or job.get("unavailable_reason") is not None
            ):
                raise WorkspaceHomeV2Error("available Home V2 job is not executable")
        elif (
            job.get("argv") is not None
            or not blockers
            or not isinstance(job.get("unavailable_reason"), str)
            or not job["unavailable_reason"]
        ):
            raise WorkspaceHomeV2Error("unavailable Home V2 job is not fail-closed")
        action_digest = job.get("action_digest")
        capability_id = job.get("capability_id")
        capability_key = job.get("capability_key")
        capability = job.get("capability")
        tool_inputs = job.get("tool_inputs")
        next_safe_action = job.get("next_safe_action")
        arguments = job.get("arguments")
        if not (
            (command_id is None and action_digest is None and arguments is None)
            or (
                isinstance(command_id, str)
                and isinstance(action_digest, str)
                and type(arguments) is dict
            )
        ):
            raise WorkspaceHomeV2Error("Home V2 job has a partial catalog action")
        if capability_id is None and capability_key is None and capability is None:
            pass
        elif (
            isinstance(capability_id, str)
            and isinstance(capability_key, str)
            and type(capability) is dict
        ):
            _validate_capability_record(
                capability,
                command_id=str(command_id or ""),
            )
            if (
                capability["capability_id"] != capability_id
                or capability["capability_key"] != capability_key
            ):
                raise WorkspaceHomeV2Error(
                    "Home V2 product capability identity is inconsistent"
                )
        else:
            raise WorkspaceHomeV2Error("Home V2 job has a partial product capability")
        if (
            type(tool_inputs) is not list
            or any(
                type(item) is not dict
                or set(item) != {"kind", "path", "sha256", "size", "mode"}
                or not isinstance(item.get("kind"), str)
                or not item["kind"]
                or not isinstance(item.get("path"), str)
                or not item["path"]
                or _DIGEST_RE.fullmatch(str(item.get("sha256", ""))) is None
                or type(item.get("size")) is not int
                or item["size"] < 0
                or type(item.get("mode")) is not int
                or not 0 <= item["mode"] <= 0o7777
                for item in tool_inputs
            )
            or tool_inputs
            != sorted(tool_inputs, key=lambda item: (item["kind"], item["path"]))
            or len({(item["kind"], item["path"]) for item in tool_inputs})
            != len(tool_inputs)
            or not (
                next_safe_action is None
                or (isinstance(next_safe_action, str) and next_safe_action)
            )
        ):
            raise WorkspaceHomeV2Error(
                "Home V2 job tool inputs are invalid"
            )
        if job_id != "cleanroom-fixture-build" and (
            tool_inputs or next_safe_action is not None
        ):
            raise WorkspaceHomeV2Error(
                "Home V2 non-fixture job invented fixture tool inputs"
            )
        if command_id is not None:
            selected = _CATALOG_ACTIONS.get(job_id)
            if (
                not _IDENTITY_RE.fullmatch(str(command_id))
                or not _DIGEST_RE.fullmatch(str(action_digest))
                or selected is None
                or command_id != selected[0]
            ):
                raise WorkspaceHomeV2Error("Home V2 catalog action is invalid")
            if job_id == "cleanroom-fixture-build":
                if (
                    set(arguments)
                    != {
                        "gradle_cmd",
                        "java_home",
                        "expected_input_digest",
                        "state_root",
                    }
                    or any(
                        arguments.get(key) is not None
                        and not isinstance(arguments.get(key), str)
                        for key in ("gradle_cmd", "java_home")
                    )
                    or not isinstance(arguments.get("state_root"), str)
                    or not Path(str(arguments.get("state_root"))).is_absolute()
                    or (
                        arguments.get("expected_input_digest") is not None
                        and _DIGEST_RE.fullmatch(
                            str(arguments.get("expected_input_digest"))
                        )
                        is None
                    )
                ):
                    raise WorkspaceHomeV2Error(
                        "Home V2 Cleanroom fixture arguments are invalid"
                    )
            elif arguments != _catalog_arguments(
                selected[1],
                workspace["root"],
            ):
                raise WorkspaceHomeV2Error("Home V2 catalog action is invalid")
            if current_catalog is not None and current_root is not None:
                try:
                    command = current_catalog.command(command_id)
                    expected_action_digest = command.action_digest(root=current_root)
                    expected_argv = None
                    if job_id != "cleanroom-fixture-build" or (
                        current_fixture_tool_context is not None
                        and current_fixture_tool_context["ready"]
                    ):
                        expected_argv, _ = command.build_argv(
                            arguments,
                            root=current_root,
                            execute=False,
                        )
                except ValueError as exc:
                    raise WorkspaceHomeV2Error(
                        "Home V2 job cites an unknown current catalog action"
                    ) from exc
                if action_digest != expected_action_digest:
                    raise WorkspaceHomeV2Error(
                        "Home V2 job cites a stale catalog action digest"
                    )
                if job["state"] == "available" and job["argv"] != expected_argv:
                    raise WorkspaceHomeV2Error(
                        "Home V2 job argv differs from its exact catalog arguments"
                    )
        if job_id == "cleanroom-fixture-build":
            if current_root is not None:
                if (
                    current_fixture_tool_context is None
                    or arguments != current_fixture_tool_context["arguments"]
                    or tool_inputs != current_fixture_tool_context["tool_inputs"]
                    or next_safe_action
                    != current_fixture_tool_context["next_safe_action"]
                ):
                    raise WorkspaceHomeV2Error(
                        "Home V2 Cleanroom fixture preflight inputs are stale"
                    )
        exact_capability = None
        if current_capability_catalog is not None and command_id is not None:
            exact_capability = _product_capability_binding(
                _ResolvedOwnerRecord(
                    payload=dict(current_capability_catalog),
                    reference={},
                ),
                command_id,
                action_digest,
            )
        if exact_capability is not None:
            if (
                capability != exact_capability["capability"]
                or capability_id
                != exact_capability["capability"]["capability_id"]
                or capability_key
                != exact_capability["capability"]["capability_key"]
            ):
                raise WorkspaceHomeV2Error(
                    "Home V2 job differs from its exact product capability"
                )
        elif current_capability_catalog is not None and (
            capability_id is not None
            or capability_key is not None
            or capability is not None
        ):
            raise WorkspaceHomeV2Error("Home V2 job invents a product capability")
        if value["capability_catalog"]["catalog_id"] is None and (
            capability_id is not None
            or capability_key is not None
            or capability is not None
        ):
            raise WorkspaceHomeV2Error("Home V2 job invents a product capability")
        expected_availability_basis = _job_availability_basis(
            job_id=job_id,
            command_id=command_id,
            action_digest=action_digest,
            capability=capability,
            doctor_ref=doctor_ref,
            capability_catalog_ref=capability_catalog_ref,
            fixture_owner_ref=fixture_owner_ref,
            workspace_revision=workspace["workspace_revision"],
        )
        if job.get("availability_basis") != expected_availability_basis:
            raise WorkspaceHomeV2Error(
                "Home V2 job availability basis is invalid"
            )
        context_resolution = (
            expected_availability_basis["kind"]
            == "owner-context-resolution"
        )
        move_recovery = (
            "WORKSPACE_LOCATION_CHANGED"
            in value["adoption"]["recovery_reasons"]
        )
        expected_blockers = set(base_actions[job_id]["blockers"])
        if (
            job_id == "cleanroom-fixture-build"
            and current_fixture_tool_context is not None
        ):
            expected_blockers.update(current_fixture_tool_context["blockers"])
        elif job_id == "cleanroom-fixture-build" and current_root is None:
            fixture_blockers = {
                "CLEANROOM_FIXTURE_LOCK_INVALID",
                "CLEANROOM_FIXTURE_PREFLIGHT_UNAVAILABLE",
                "CLEANROOM_FIXTURE_TOOL_CONFIGURATION_REQUIRED",
                "CLEANROOM_FIXTURE_TOOL_INVALID",
            }
            expected_blockers.update(set(blockers) & fixture_blockers)
        if base_actions[job_id]["available"] and command_id is None:
            expected_blockers.add("CATALOG_ACTION_UNAVAILABLE")
        if context_dependent:
            if value["session"]["owner_ref_id"] is not None and value["session"][
                "freshness"
            ] not in {"current", "not-applicable"}:
                expected_blockers.add(
                    "WORK_SESSION_" + value["session"]["freshness"].upper()
                )
        if context_dependent or move_recovery:
            if value["adoption"]["freshness"] == "stale":
                expected_blockers.add("ADOPTION_BINDING_STALE")
            if value["adoption"]["recovery_state"] == "required":
                expected_blockers.add("ADOPTION_RECOVERY_REQUIRED")
        if capability_dependent:
            if value["capability_catalog"]["owner_ref_id"] is None:
                expected_blockers.add("CAPABILITY_CATALOG_UNAVAILABLE")
            elif value["capability_catalog"]["freshness"] not in {
                "current",
                "not-applicable",
            }:
                expected_blockers.add(
                    "CAPABILITY_CATALOG_"
                    + value["capability_catalog"]["freshness"].upper()
                )
            elif command_id is not None and (
                capability is None
                or not _capability_record_is_executable(
                    capability,
                    action_digest=action_digest,
                )
            ) and not context_resolution:
                expected_blockers.add("PRODUCT_CAPABILITY_UNAVAILABLE")
        if set(blockers) != expected_blockers:
            raise WorkspaceHomeV2Error("Home V2 job blockers are inconsistent")
        expected_available = base_actions[job_id]["available"] and not blockers
        if (job["state"] == "available") != expected_available:
            raise WorkspaceHomeV2Error("Home V2 job availability is inconsistent")
        job_ids.add(job_id)
    new_project = value.get("new_project")
    construction_available = (
        construction_owner_ref is not None
        and construction_owner_ref["integrity"] == "verified"
        and construction_owner_ref["freshness"] in {"current", "not-applicable"}
        and construction_owner_ref["record_format"]
        == _CLEANROOM_CONSTRUCTION_OWNER_FORMAT
        and construction_owner_ref["record_id"] is not None
    )
    expected_new_project = {
        "state": "available" if construction_available else "unavailable",
        "admitted_kinds": (
            [_CLEANROOM_CONSTRUCTION_KIND] if construction_available else []
        ),
        "owner_ref_ids": (
            [construction_owner_ref["id"]]
            if construction_available and construction_owner_ref is not None
            else []
        ),
        "blockers": (
            []
            if construction_available
            else ["NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE"]
        ),
    }
    if (
        type(new_project) is not dict
        or set(new_project)
        != {
            "state",
            "admitted_kinds",
            "owner_ref_ids",
            "blockers",
            "reason",
            "next_safe_action",
        }
        or any(
            new_project.get(key) != expected
            for key, expected in expected_new_project.items()
        )
        or not isinstance(new_project.get("reason"), str)
        or not new_project["reason"]
        or not isinstance(new_project.get("next_safe_action"), str)
        or not new_project["next_safe_action"]
    ):
        raise WorkspaceHomeV2Error(
            "Home V2 new-project construction authority is inconsistent"
        )
    limitations = value.get("limitations")
    if (
        type(limitations) is not list
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise WorkspaceHomeV2Error("Workspace Home V2 limitations are invalid")
    if value.get("home_id") != _home_id(value):
        raise WorkspaceHomeV2Error("Workspace Home V2 identity digest is stale")


_WORKSPACE_KIND_LABELS = {
    "cleanroom-mod": "Cleanroom mod",
    "directory": "Folder",
    "gradle-project": "Gradle project",
    "packwiz-pack": "Packwiz modpack",
    "repository": "Git repository",
    "supersymmetry-pack": "Supersymmetry modpack",
}


def _human_workspace_kind(value: Any) -> str:
    raw = str(value)
    return _WORKSPACE_KIND_LABELS.get(
        raw,
        raw.replace("_", " ").replace("-", " ").capitalize(),
    )


def _human_command(argv: Sequence[Any]) -> str:
    """Render exact argv as the public, copyable Workbench command."""

    arguments = [str(item) for item in argv]
    if (
        len(arguments) >= 2
        and Path(arguments[1]).name == "workbench.py"
        and Path(arguments[1]).parent.name == "tools"
    ):
        arguments = ["workbench", *arguments[2:]]
    return _terminal_text(human_command(arguments))


def _human_job_guidance(job: Mapping[str, Any]) -> str:
    """Explain an unavailable job without exposing authority-layer reason codes."""

    blockers = set(job["blockers"])
    if (
        "ADOPTION_RECOVERY_REQUIRED" in blockers
        or "ADOPTION_BINDING_STALE" in blockers
    ):
        return (
            "This workspace moved or changed since it was saved. Open it again "
            "before running this action."
        )
    if "PROJECT_SURFACE_UNAVAILABLE" in blockers:
        return (
            "Open a project folder that contains source, resources, scripts, or "
            "configuration files."
        )
    if "EXACT_RUN_PROFILE_REQUIRED" in blockers:
        return (
            "Select a supported Cleanroom development profile before launching "
            "the game."
        )
    if "RECIPE_OWNER_PREFLIGHT_FAILED" in blockers:
        return (
            "Workbench could not verify where recipe changes belong in this "
            "project. Inspect the project profile before editing."
        )
    if "RECIPE_EVIDENCE_PREFLIGHT_FAILED" in blockers:
        return (
            "Workbench could not verify the recipe information for this project. "
            "Inspect the project profile before continuing."
        )
    if "CLEANROOM_FIXTURE_TOOL_CONFIGURATION_REQUIRED" in blockers:
        return (
            "Choose working Gradle and Java 25 tools for the Cleanroom build, then "
            "reopen Home."
        )
    if blockers & {
        "CLEANROOM_FIXTURE_LOCK_INVALID",
        "CLEANROOM_FIXTURE_PREFLIGHT_UNAVAILABLE",
        "CLEANROOM_FIXTURE_TOOL_INVALID",
    }:
        return (
            "Workbench could not verify the bundled Cleanroom build tools. Repair "
            "the installation, then reopen Home."
        )
    if "CATALOG_ACTION_UNAVAILABLE" in blockers:
        return (
            "This Workbench installation does not include a runnable command for "
            "this action. Repair or reinstall Workbench."
        )
    if "PRODUCT_CAPABILITY_UNAVAILABLE" in blockers:
        return "This action is not available in the current Workbench release."
    if any(blocker.startswith("CAPABILITY_CATALOG_") for blocker in blockers):
        return (
            "Workbench could not load action availability for this workspace. Run "
            "workbench setup, then reopen Home."
        )
    if any(blocker.startswith("WORK_SESSION_") for blocker in blockers):
        return (
            "The saved work session no longer matches this workspace. Reopen the "
            "workspace to use current information."
        )

    reason = job.get("unavailable_reason")
    if isinstance(reason, str) and reason:
        # Owner-provided prose remains useful, but internal SCREAMING_SNAKE_CASE
        # identifiers are a JSON diagnostic surface rather than human Home copy.
        reason = re.sub(
            r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b",
            lambda match: match.group(0).replace("_", " ").lower(),
            reason,
        )
        return _terminal_text(reason)
    return "This action is not ready for the current workspace."


def render_workspace_home_v2(
    value: Mapping[str, Any],
    *,
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Render a compact, inert Home V2 view without changing its contract."""

    validate_workspace_home_v2(value)
    presentation = human_presentation(stream, environment=environ)
    workspace = value["workspace"]
    new_project = value["new_project"]
    available = [job for job in value["jobs"] if job["state"] == "available"]
    unavailable = [job for job in value["jobs"] if job["state"] == "unavailable"]

    status_messages = {
        "ready": "Workbench can run the actions below.",
        "attention": "Some Workbench features are not ready for this workspace.",
        "blocked": "This workspace needs attention before Workbench can continue.",
    }
    status_tones = {"ready": "good", "attention": "attention", "blocked": "blocked"}
    status_state = str(value["status"]["state"])
    lines = [
        "Workbench Home",
        (
            f"Workspace: {_terminal_text(workspace['display_name'])} · "
            f"{_terminal_text(_human_workspace_kind(workspace['kind']))}"
        ),
        f"Location: {_terminal_text(workspace['root'])}",
        presentation.label(status_state, status_tones[status_state])
        + " "
        + status_messages[status_state],
        "",
        "Ready now",
    ]

    for job in available:
        lines.append(
            "  "
            + presentation.label("ready", "good")
            + f" {_terminal_text(job['title'])}  →  {_human_command(job['argv'])}"
        )
    if new_project["state"] == "available":
        lines.append(
            "  "
            + presentation.label("ready", "good")
            + " Create a Cleanroom mod project  →  "
            + _terminal_text(new_project["next_safe_action"])
        )
    if not available and new_project["state"] != "available":
        lines.append("  No actions are ready yet.")

    if unavailable or new_project["state"] == "unavailable":
        lines.extend(["", "Needs setup", "  Start here  →  workbench setup"])
        for job in unavailable:
            lines.extend(
                [
                    "  "
                    + presentation.label("attention", "attention")
                    + f" {_terminal_text(job['title'])}",
                    f"    {_human_job_guidance(job)}",
                ]
            )
        if new_project["state"] == "unavailable":
            lines.extend(
                [
                    "  "
                    + presentation.label("attention", "attention")
                    + " Create a Cleanroom mod project",
                    (
                        "    Workbench could not verify its Cleanroom project "
                        "template. Repair or reinstall Workbench, then reopen Home."
                    ),
                ]
            )

    lines.extend(
        [
            "",
            (
                "Read-only — Home did not change project files, builds, runtimes, "
                "worlds, or dependencies."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "ADOPTION_FORMAT",
    "HOME_V2_FORMAT",
    "HOME_V2_SCHEMA_VERSION",
    "OwnerRecordPort",
    "WorkspaceHomeV2Error",
    "adopt_workspace_home_v2",
    "build_workspace_home_v2",
    "load_product_capability_owner_port",
    "load_workspace_home_adoption",
    "render_workspace_home_v2",
    "reopen_workspace_home_v2",
    "validate_workspace_home_v2",
    "work_session_summary_owner_port",
    "workspace_home_adoption_exists",
    "workspace_home_binding_id",
]
