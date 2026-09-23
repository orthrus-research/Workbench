"""Durable orchestration for one exact Supersymmetry material-fluid change.

Blueprints/profile functions remain the construction and transaction owners.
Runtime ports remain the execution owners.  This module gives their retained
records one change identity and enforces the A/A, ordered A/B, apply, verify,
rollback, and recovery sequence without asking for workspace paths again.
"""

from __future__ import annotations

from urllib.request import url2pathname

from workbench_crucible.runtime_pair import FeatureRuntimePairPorts, PAIR_REQUEST_FORMAT, PAIR_RESULT_FORMAT
import base64
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
from typing import Any, Iterator, Mapping, NoReturn
from urllib.parse import urlparse

from workbench_api.host_filesystem import file_lease, fsync_directory

from .developer_feature import (
    apply_material_fluid_recipe_plan,
    build_material_fluid_recipe_plan,
    material_fluid_recipe_workspace,
    recover_material_fluid_recipe,
    retain_feature_record,
    rollback_material_fluid_recipe,
    validate_material_fluid_recipe_plan,
    validate_material_fluid_recipe_receipt,
    verify_material_fluid_recipe_plan,
)
from workbench_api.state_paths import default_product_spine_state_root
from .work_session import validate_work_session_record


HEADER_FORMAT = "workbench-feature-change-header-v1"
START_RESULT_FORMAT = "workbench-feature-change-start-result-v1"
VIEW_FORMAT = "workbench-feature-change-view-v1"
EVENT_FORMAT = "workbench-feature-change-event-v1"
MATRIX_FORMAT = "workbench-feature-change-runtime-matrix-v1"
SESSION_CONTEXT_FORMAT = "workbench-feature-change-session-context-v1"
SESSION_CONTEXT_ID_PREFIX = "workbench-feature-change-session-context:sha256:"
SELECTION_FORMAT = "workbench-feature-change-selected-context-v1"
SELECTION_ID_PREFIX = "workbench-feature-change-selection:sha256:"
CHANGE_ID_PREFIX = "workbench-feature-change:sha256:"
EVENT_ID_PREFIX = "workbench-feature-change-event:sha256:"
MATRIX_ID_PREFIX = "workbench-feature-change-runtime-matrix:sha256:"
PAIR_REQUEST_ID_PREFIX = "workbench-feature-runtime-pair-request:sha256:"
_CHANGE_ID = re.compile(rf"{re.escape(CHANGE_ID_PREFIX)}[0-9a-f]{{64}}\Z")
_EVENT_ID = re.compile(rf"{re.escape(EVENT_ID_PREFIX)}[0-9a-f]{{64}}\Z")
_SESSION_CONTEXT_ID = re.compile(
    rf"{re.escape(SESSION_CONTEXT_ID_PREFIX)}[0-9a-f]{{64}}\Z"
)
_SESSION_ID = re.compile(r"work-session-v2-[0-9a-f]{32}\Z")
_SELECTION_ID = re.compile(
    rf"{re.escape(SELECTION_ID_PREFIX)}[0-9a-f]{{64}}\Z"
)
_MAX_RECORD_BYTES = 64 * 1024 * 1024
_SIDES = ("client", "server")
_CONTROL_CASES = (("aa", "client", "baseline-first"), ("aa", "server", "baseline-first"))
_COMPARISON_CASES = (
    ("ab", "client", "baseline-first"),
    ("ab", "server", "baseline-first"),
    ("ab", "client", "candidate-first"),
    ("ab", "server", "candidate-first"),
)
_REQUIRED_ASSERTIONS = {
    "client": (
        "material_registration",
        "fluid_registration",
        "recipe_registration",
        "unification_identity",
        "localization",
        "forbidden_delta_absent",
    ),
    "server": (
        "material_registration",
        "fluid_registration",
        "recipe_registration",
        "unification_identity",
        "dedicated_server_safe",
        "forbidden_delta_absent",
    ),
}


class FeatureChangeWorkspaceError(ValueError):
    """One retained feature change cannot proceed or be reopened safely."""


def _fail(message: str) -> NoReturn:
    raise FeatureChangeWorkspaceError(message)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FeatureChangeWorkspaceError(
            "feature change record is not canonical JSON"
        ) from exc


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + sha256(_canonical(value)).hexdigest()


def _local_uri(value: Any, label: str) -> Path:
    if type(value) is not str:
        _fail(f"{label} is not a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} is not a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        _fail(f"{label} is not absolute")
    return path


def _safe_state_root(value: Path | str, *, create: bool) -> Path:
    root = Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        if current.is_symlink():
            _fail(f"feature change state cannot traverse a symbolic link: {current}")
        if current.exists() and current != root and not current.is_dir():
            _fail(f"feature change state traverses a non-directory: {current}")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise FeatureChangeWorkspaceError(
            "feature change state root is unavailable"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail("feature change state root is not an ordinary directory")
    return root


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _ordinary_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FeatureChangeWorkspaceError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail(f"{label} is not an ordinary directory")
    return path


def _ordinary_json(path: Path, label: str) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise FeatureChangeWorkspaceError(f"cannot read {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_RECORD_BYTES:
            _fail(f"{label} is not a bounded ordinary file")
        raw = b""
        while chunk := os.read(descriptor, min(1024 * 1024, _MAX_RECORD_BYTES + 1 - len(raw))):
            raw += chunk
            if len(raw) > _MAX_RECORD_BYTES:
                _fail(f"{label} exceeds its byte bound")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != before.st_size
    ):
        _fail(f"{label} changed while it was read")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FeatureChangeWorkspaceError(f"cannot decode {label}: {exc}") from exc
    if type(value) is not dict:
        _fail(f"{label} is not one object")
    return value


def _ordinary_bytes(path: Path, label: str) -> bytes:
    """Read one bounded owner record without following or racing a link."""

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise FeatureChangeWorkspaceError(f"cannot read {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_RECORD_BYTES:
            _fail(f"{label} is not a bounded ordinary file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != before.st_size
    ):
        _fail(f"{label} changed while it was read")
    return raw


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if len(payload) > _MAX_RECORD_BYTES:
        _fail("feature change record exceeds its byte bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
    except OSError as exc:
        raise FeatureChangeWorkspaceError(
            f"cannot publish immutable feature change record: {exc}"
        ) from exc


def _change_directory(root: Path, change_id: str, *, create: bool) -> Path:
    if _CHANGE_ID.fullmatch(change_id) is None:
        _fail("feature change selector is invalid")
    changes = root / "change-workspaces"
    if create:
        changes.mkdir(mode=0o700, exist_ok=True)
    _ordinary_directory(changes, "feature change collection")
    directory = changes / change_id.removeprefix(CHANGE_ID_PREFIX)
    if create:
        directory.mkdir(mode=0o700)
        (directory / "events").mkdir(mode=0o700)
        (directory / "runtime").mkdir(mode=0o700)
    return _ordinary_directory(directory, "feature change workspace")


def _validate_header(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("feature change header is not one object")
    row = deepcopy(dict(value))
    if (
        set(row) != {
            "format", "schema_version", "change_id", "intent", "plan_id",
            "plan_uri", "workspace_uri", "transaction_root_uri", "limitations",
        }
        or row.get("format") != HEADER_FORMAT
        or row.get("schema_version") != 1
        or _CHANGE_ID.fullmatch(str(row.get("change_id"))) is None
        or row.get("intent") != "supersymmetry-material-backed-fluid-recipe"
        or type(row.get("plan_id")) is not str
        or type(row.get("limitations")) is not list
        or not row["limitations"]
    ):
        _fail("feature change header identity or shape changed")
    _local_uri(row["plan_uri"], "feature change plan")
    _local_uri(row["workspace_uri"], "feature change workspace input")
    _local_uri(row["transaction_root_uri"], "feature change transaction root")
    expected = _content_id(
        CHANGE_ID_PREFIX,
        {"intent": row["intent"], "plan_id": row["plan_id"]},
    )
    if row["change_id"] != expected:
        _fail("feature change ID no longer binds its intent and plan")
    return row


def _validate_event(
    value: Mapping[str, Any],
    *,
    change_id: str,
    sequence: int,
    previous_event_id: str | None,
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("feature change event is not one object")
    event = deepcopy(dict(value))
    body = dict(event)
    supplied = body.pop("event_id", None)
    if (
        set(event)
        != {"format", "schema_version", "event_id", "change_id", "sequence", "previous_event_id", "kind", "payload"}
        or event.get("format") != EVENT_FORMAT
        or event.get("schema_version") != 1
        or event.get("change_id") != change_id
        or event.get("sequence") != sequence
        or event.get("previous_event_id") != previous_event_id
        or event.get("kind") not in {
            "started", "matrix-completed", "applied", "apply-rejected",
            "verified", "verification-failed", "rolled-back",
            "rollback-blocked", "recovery-checked",
        }
        or type(event.get("payload")) is not dict
        or type(supplied) is not str
        or _EVENT_ID.fullmatch(supplied) is None
        or supplied != _content_id(EVENT_ID_PREFIX, body)
    ):
        _fail("feature change event identity or chain changed")
    return event


def _append_event(directory: Path, change_id: str, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    opened = _open_directory(directory, allow_empty=kind == "started")
    events = opened["events"]
    previous = events[-1]["event_id"] if events else None
    sequence = len(events)
    body = {
        "format": EVENT_FORMAT,
        "schema_version": 1,
        "change_id": change_id,
        "sequence": sequence,
        "previous_event_id": previous,
        "kind": kind,
        "payload": deepcopy(dict(payload)),
    }
    event = {**body, "event_id": _content_id(EVENT_ID_PREFIX, body)}
    _write_immutable(directory / "events" / f"{sequence:020d}.json", event)
    return event


def _open_directory(directory: Path, *, allow_empty: bool = False) -> dict[str, Any]:
    header = _validate_header(_ordinary_json(directory / "change.json", "feature change header"))
    plan = validate_material_fluid_recipe_plan(
        _ordinary_json(directory / "plan.json", "feature change plan")
    )
    if plan["id"] != header["plan_id"]:
        _fail("feature change plan no longer matches its header")
    entries = sorted((directory / "events").iterdir(), key=lambda path: path.name)
    events: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence, path in enumerate(entries):
        if path.name != f"{sequence:020d}.json" or path.is_symlink() or not path.is_file():
            _fail("feature change event journal has a gap or unsafe entry")
        event = _validate_event(
            _ordinary_json(path, f"feature change event {sequence}"),
            change_id=header["change_id"],
            sequence=sequence,
            previous_event_id=previous,
        )
        events.append(event)
        previous = event["event_id"]
    if not events and allow_empty:
        pass
    elif not events or events[0]["kind"] != "started":
        _fail("feature change journal has no creation event")
    lifecycle = "planned"
    for event in events[1:]:
        kind = event["kind"]
        if kind == "matrix-completed":
            lifecycle = "tested" if event["payload"].get("outcome") == "passed" else "test-no-go"
        elif kind == "applied":
            lifecycle = "applied"
        elif kind == "apply-rejected":
            lifecycle = "apply-rejected"
        elif kind == "verified":
            lifecycle = "verified"
        elif kind == "verification-failed":
            lifecycle = "verification-failed"
        elif kind == "rolled-back":
            lifecycle = "rolled-back"
        elif kind == "rollback-blocked":
            lifecycle = "rollback-blocked"
        # A recovery inspection does not erase the last transaction lifecycle.
    return {
        "format": VIEW_FORMAT,
        "schema_version": 1,
        "change_id": header["change_id"],
        "plan_id": plan["id"],
        "lifecycle": lifecycle,
        "header": header,
        "plan": plan,
        "events": events,
        "latest_sequence": len(events) - 1,
    }


def open_feature_change(state_root: Path | str, change_id: str) -> dict[str, Any]:
    root = _safe_state_root(state_root, create=False)
    directory = _change_directory(root, change_id, create=False)
    return _open_directory(directory)


def start_material_fluid_recipe_change(
    suite_root: Path | str,
    workspace_root: Path | str,
    state_root: Path | str,
    **request: Any,
) -> dict[str, Any]:
    """Plan and retain one typed change without mutating its checkout."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).resolve()
    state = _safe_state_root(state_root, create=True)
    if _paths_overlap(state, workspace):
        _fail("feature change state cannot overlap the developer checkout")
    plan = build_material_fluid_recipe_plan(suite, workspace, **request)
    validate_material_fluid_recipe_plan(plan)
    change_id = _content_id(
        CHANGE_ID_PREFIX,
        {"intent": "supersymmetry-material-backed-fluid-recipe", "plan_id": plan["id"]},
    )
    retained_plan = retain_feature_record(state, "plans", plan)
    candidate = state / "change-workspaces" / change_id.removeprefix(CHANGE_ID_PREFIX)
    if candidate.exists() or candidate.is_symlink():
        directory = _change_directory(state, change_id, create=False)
        opened = _open_directory(directory)
        if opened["plan_id"] != plan["id"]:
            _fail("retained feature change identity collides with another plan")
        return {
            "format": START_RESULT_FORMAT,
            "schema_version": 1,
            "change_id": change_id,
            "plan_id": plan["id"],
            "lifecycle": opened["lifecycle"],
            "record_uri": (directory / "change.json").as_uri(),
            "reused": True,
        }
    directory = _change_directory(state, change_id, create=True)
    transaction = directory / "transaction"
    header = {
        "format": HEADER_FORMAT,
        "schema_version": 1,
        "change_id": change_id,
        "intent": "supersymmetry-material-backed-fluid-recipe",
        "plan_id": plan["id"],
        "plan_uri": retained_plan.as_uri(),
        "workspace_uri": plan["workspace_uri"],
        "transaction_root_uri": transaction.as_uri(),
        "limitations": [
            "This change is a reversible local experiment and is not release admission or publication authority.",
            "Runtime ports must retain their own client/server process and observation custody.",
            "The initial lane proves one exact additive material-backed-fluid recipe, not arbitrary recipe mutation.",
        ],
    }
    _write_immutable(directory / "change.json", header)
    _write_immutable(directory / "plan.json", plan)
    _append_event(directory, change_id, "started", {
        "plan_id": plan["id"], "verification": verify_material_fluid_recipe_plan(suite, plan)
    })
    return {
        "format": START_RESULT_FORMAT,
        "schema_version": 1,
        "change_id": change_id,
        "plan_id": plan["id"],
        "lifecycle": "planned",
        "record_uri": (directory / "change.json").as_uri(),
        "reused": False,
    }


def _sha256_file(path: Path, label: str) -> str:
    raw = _ordinary_bytes(path, label)
    if not 1 <= len(raw) <= _MAX_RECORD_BYTES:
        _fail(f"{label} exceeds its byte bound")
    return "sha256:" + sha256(raw).hexdigest()


def _session_context_root(suite_root: Path | str) -> Path:
    root = (
        default_product_spine_state_root(suite_root)
        / "feature-change-session-context-v1"
    )
    return _safe_state_root(root, create=True)


def _session_context_directory(suite_root: Path | str, session_id: str) -> Path:
    if _SESSION_ID.fullmatch(session_id) is None:
        _fail("feature change Work Session selector is invalid")
    return _session_context_root(suite_root) / "contexts" / session_id


def _contexts_root(suite_root: Path | str) -> Path:
    root = _session_context_root(suite_root) / "contexts"
    root.mkdir(mode=0o700, exist_ok=True)
    return _ordinary_directory(root, "feature change Work Session contexts")


def _session_owner_directory(suite_root: Path | str, session_id: str) -> Path:
    if _SESSION_ID.fullmatch(session_id) is None:
        _fail("feature change Work Session selector is invalid")
    owners = _session_context_root(suite_root) / "session-owners"
    owners.mkdir(mode=0o700, exist_ok=True)
    _ordinary_directory(owners, "feature change Work Session owner collection")
    return owners / session_id


def _context_for_session(suite_root: Path | str, session_id: str) -> Path:
    directory = _session_context_directory(suite_root, session_id)
    try:
        _ordinary_directory(directory, "feature change Work Session context directory")
        matches = [
            path
            for path in directory.iterdir()
            if path.name.startswith("context-") and path.name.endswith(".json")
        ]
    except OSError as exc:
        raise FeatureChangeWorkspaceError(
            "feature change Work Session context is unavailable"
        ) from exc
    if len(matches) != 1 or matches[0].is_symlink() or not matches[0].is_file():
        _fail("feature change Work Session does not have one immutable context")
    return matches[0]


def _timestamp(value: Any, label: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        _fail(f"{label} is not one UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise FeatureChangeWorkspaceError(
            f"{label} is not one UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail(f"{label} is not one UTC timestamp")
    return value


@contextmanager
def _exclusive_record_lock(path: Path, label: str) -> Iterator[int]:
    """Hold a process-scoped advisory lock; a killed writer cannot strand it."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except OSError as exc:
        raise FeatureChangeWorkspaceError(f"cannot open {label}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail(f"{label} is not an ordinary file")
        with ExitStack() as leases:
            try:
                leases.enter_context(file_lease(descriptor, exclusive=True))
            except BlockingIOError as exc:
                raise FeatureChangeWorkspaceError(
                    f"{label} is held by another writer"
                ) from exc
            except OSError as exc:
                raise FeatureChangeWorkspaceError(f"cannot lock {label}: {exc}") from exc
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(descriptor)
            yield descriptor
    finally:
        os.close(descriptor)


def _replace_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise FeatureChangeWorkspaceError(
            f"cannot publish selected feature change context: {exc}"
        ) from exc


def _validate_selection(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("selected feature change context is not one object")
    row = deepcopy(value)
    body = dict(row)
    supplied_id = body.pop("selection_id", None)
    if (
        set(row)
        != {
            "context_id", "context_sha256", "context_uri", "format",
            "previous_selection_id", "previous_selection_sha256",
            "schema_version", "selected_at", "selection_id",
            "selection_sequence", "session_id", "state",
        }
        or row.get("format") != SELECTION_FORMAT
        or row.get("schema_version") != 1
        or row.get("state") not in {"open", "closed"}
        or type(supplied_id) is not str
        or _SELECTION_ID.fullmatch(supplied_id) is None
        or supplied_id != _content_id(SELECTION_ID_PREFIX, body)
        or type(row.get("selection_sequence")) is not int
        or not 0 <= row["selection_sequence"] <= 1_000_000
    ):
        _fail("selected feature change context identity or shape changed")
    _timestamp(row.get("selected_at"), "selected feature change context timestamp")
    previous = (
        row.get("previous_selection_id"),
        row.get("previous_selection_sha256"),
    )
    if row["selection_sequence"] == 0:
        if previous != (None, None):
            _fail("initial feature change selection retains a predecessor")
    elif (
        type(previous[0]) is not str
        or _SELECTION_ID.fullmatch(previous[0]) is None
        or type(previous[1]) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", previous[1]) is None
    ):
        _fail("feature change selection lacks its exact predecessor")
    bound = (
        row.get("context_id"), row.get("context_sha256"),
        row.get("context_uri"), row.get("session_id"),
    )
    if row["state"] == "closed":
        if bound != (None, None, None, None):
            _fail("closed feature change selection retains an active binding")
    elif (
        type(bound[0]) is not str
        or _SESSION_CONTEXT_ID.fullmatch(bound[0]) is None
        or type(bound[1]) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", bound[1]) is None
        or type(bound[2]) is not str
        or type(bound[3]) is not str
        or _SESSION_ID.fullmatch(bound[3]) is None
    ):
        _fail("open feature change selection lacks an exact context binding")
    if row["state"] == "open":
        _local_uri(bound[2], "selected feature change context")
    return row


def validate_feature_change_selected_context(value: Any) -> dict[str, Any]:
    """Validate one identity-bearing V1 current-context selection record."""

    return _validate_selection(value)


def _selection_history_path(root: Path, selection_id: str) -> Path:
    if _SELECTION_ID.fullmatch(selection_id) is None:
        _fail("feature change selection history identity is invalid")
    history = root / "selections"
    history.mkdir(mode=0o700, exist_ok=True)
    _ordinary_directory(history, "feature change selection history")
    return history / f"{selection_id.removeprefix(SELECTION_ID_PREFIX)}.json"


def _ensure_selection_history(
    root: Path,
    selection: Mapping[str, Any],
    selected_raw: bytes,
) -> None:
    path = _selection_history_path(root, selection["selection_id"])
    if path.exists() or path.is_symlink():
        if _ordinary_bytes(path, "retained feature change selection") != selected_raw:
            _fail("retained feature change selection bytes changed")
        return
    _write_immutable(path, selection)
    if _ordinary_bytes(path, "retained feature change selection") != selected_raw:
        _fail("retained feature change selection serialization changed")


def _validate_selection_chain(root: Path, selected: Mapping[str, Any]) -> None:
    current = deepcopy(dict(selected))
    remaining = current["selection_sequence"]
    while remaining:
        previous_path = _selection_history_path(
            root, current["previous_selection_id"]
        )
        if (
            _sha256_file(previous_path, "previous feature change selection")
            != current["previous_selection_sha256"]
        ):
            _fail("feature change selection predecessor digest is stale")
        previous = _validate_selection(
            _ordinary_json(previous_path, "previous feature change selection")
        )
        if (
            previous["selection_id"] != current["previous_selection_id"]
            or previous["selection_sequence"] != remaining - 1
        ):
            _fail("feature change selection predecessor sequence changed")
        current = previous
        remaining -= 1
    if (
        current["selection_sequence"] != 0
        or current["previous_selection_id"] is not None
        or current["previous_selection_sha256"] is not None
    ):
        _fail("feature change selection history does not reach its origin")


def _publish_selection(
    suite_root: Path | str,
    *,
    context: Mapping[str, Any] | None,
    context_path: Path | None,
) -> dict[str, Any]:
    root = _session_context_root(suite_root)
    lock = root / "selection.lock"
    with _exclusive_record_lock(lock, "feature change context selection lock"):
        selected_path = root / "selected-context-v1.json"
        if selected_path.exists() or selected_path.is_symlink():
            selected_raw = _ordinary_bytes(
                selected_path, "previous context selection"
            )
            previous_record = _validate_selection(
                _ordinary_json(selected_path, "previous context selection")
            )
            _validate_selection_chain(root, previous_record)
            _ensure_selection_history(root, previous_record, selected_raw)
            previous_id = previous_record["selection_id"]
            previous_sha256 = "sha256:" + sha256(selected_raw).hexdigest()
            sequence = previous_record["selection_sequence"] + 1
        else:
            previous_id = None
            previous_sha256 = None
            sequence = 0
        body = {
            "context_id": None if context is None else context["context_id"],
            "context_sha256": (
                None if context_path is None else _sha256_file(context_path, "feature change context")
            ),
            "context_uri": None if context_path is None else context_path.as_uri(),
            "format": SELECTION_FORMAT,
            "previous_selection_id": previous_id,
            "previous_selection_sha256": previous_sha256,
            "schema_version": 1,
            "selected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "session_id": None if context is None else context["session_id"],
            "selection_sequence": sequence,
            "state": "closed" if context is None else "open",
        }
        selected = {
            **body,
            "selection_id": _content_id(SELECTION_ID_PREFIX, body),
        }
        selected = _validate_selection(selected)
        selected_raw = json.dumps(
            selected,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        _ensure_selection_history(root, selected, selected_raw)
        _replace_json(selected_path, selected)
        return selected


def _validate_start_result(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("feature change start result is not one object")
    row = deepcopy(value)
    if (
        set(row)
        != {
            "format", "schema_version", "change_id", "plan_id", "lifecycle",
            "record_uri", "reused",
        }
        or row.get("format") != START_RESULT_FORMAT
        or row.get("schema_version") != 1
        or _CHANGE_ID.fullmatch(str(row.get("change_id"))) is None
        or type(row.get("plan_id")) is not str
        or row.get("lifecycle") not in {
            "planned", "test-no-go", "tested", "apply-rejected", "applied",
            "verified", "verification-failed", "rolled-back", "rollback-blocked",
        }
        or type(row.get("reused")) is not bool
    ):
        _fail("feature change start result identity or shape changed")
    _local_uri(row.get("record_uri"), "feature change start record")
    return row


def validate_feature_change_session_context(value: Any) -> dict[str, Any]:
    """Validate and re-open the one immutable current Work Session context."""

    if type(value) is not dict:
        _fail("feature change session context is not one object")
    row = deepcopy(value)
    expected = {
        "change_id", "claims", "context_id", "created_at", "format", "plan_id",
        "runtime_config_sha256", "runtime_config_uri", "schema_version",
        "session_id", "session_record_id", "session_record_sha256",
        "session_record_uri", "start_result_sha256", "start_result_uri",
        "setup_request_sha256", "state_root_uri", "workspace_uri",
    }
    body = dict(row)
    supplied_id = body.pop("context_id", None)
    if (
        set(row) != expected
        or row.get("format") != SESSION_CONTEXT_FORMAT
        or row.get("schema_version") != 1
        or type(supplied_id) is not str
        or _SESSION_CONTEXT_ID.fullmatch(supplied_id) is None
        or supplied_id != _content_id(SESSION_CONTEXT_ID_PREFIX, body)
        or _CHANGE_ID.fullmatch(str(row.get("change_id"))) is None
        or re.fullmatch(
            r"workbench-developer-material-fluid-recipe-plan:sha256:[0-9a-f]{64}",
            str(row.get("plan_id")),
        ) is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(row.get("setup_request_sha256"))
        ) is None
        or row.get("claims")
        != {
            "publication_authorized": False,
            "release_authorized": False,
            "support_claimed": False,
        }
    ):
        _fail("feature change session context identity or shape changed")
    _timestamp(row.get("created_at"), "feature change session context timestamp")
    session_path = _local_uri(row["session_record_uri"], "Work Session record")
    runtime_path = _local_uri(row["runtime_config_uri"], "runtime configuration")
    start_path = _local_uri(row["start_result_uri"], "feature change start result")
    state_root = _local_uri(row["state_root_uri"], "feature change state root")
    workspace = _local_uri(row["workspace_uri"], "feature change workspace")
    if (
        _sha256_file(session_path, "Work Session record")
        != row.get("session_record_sha256")
        or _sha256_file(runtime_path, "runtime configuration")
        != row.get("runtime_config_sha256")
        or _sha256_file(start_path, "feature change start result")
        != row.get("start_result_sha256")
    ):
        _fail("feature change session inputs changed after setup")
    session = validate_work_session_record(_ordinary_json(session_path, "Work Session record"))
    start = _validate_start_result(
        _ordinary_json(start_path, "feature change start result")
    )
    if (
        session["session_id"] != row.get("session_id")
        or session["session_record_id"] != row.get("session_record_id")
        or session["workspace"]["root_uri"] != row.get("workspace_uri")
        or workspace.resolve(strict=True)
        != Path(session["workspace"]["canonical_root"]).resolve(strict=True)
        or start["change_id"] != row.get("change_id")
        or start["plan_id"] != row.get("plan_id")
    ):
        _fail("feature change session bindings changed after setup")
    opened = open_feature_change(state_root, row["change_id"])
    if (
        opened["plan_id"] != row["plan_id"]
        or opened["header"]["workspace_uri"] != row["workspace_uri"]
    ):
        _fail("feature change owner state differs from its Work Session context")
    runtime = _ordinary_json(runtime_path, "runtime configuration")
    if (
        runtime.get("format")
        != "workbench-supersymmetry-installed-material-fluid-runtime-config-v1"
        or runtime.get("schema_version") != 1
    ):
        _fail("feature change runtime configuration format changed")
    return row


def _setup_request_sha256(request: Mapping[str, Any]) -> str:
    if any(type(key) is not str for key in request):
        _fail("feature change setup request keys are not text")
    return "sha256:" + sha256(_canonical(dict(request))).hexdigest()


def _remove_incomplete_setup(path: Path, label: str) -> None:
    """Remove only a session-scoped setup path while its owner lock is held."""

    if not path.exists() and not path.is_symlink():
        return
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail(f"{label} is not an ordinary directory")
        shutil.rmtree(path)
        fsync_directory(path.parent)
    except OSError as exc:
        raise FeatureChangeWorkspaceError(f"cannot clean {label}: {exc}") from exc


def bind_material_fluid_recipe_session_context(
    suite_root: Path | str,
    session_record_path: Path | str,
    runtime_config_path: Path | str,
    **request: Any,
) -> dict[str, Any]:
    """Accept machine-local inputs once and publish one immutable active context."""

    suite = Path(suite_root).resolve(strict=True)
    session_path = Path(session_record_path).expanduser().resolve(strict=True)
    runtime_path = Path(runtime_config_path).expanduser().resolve(strict=True)
    session = validate_work_session_record(_ordinary_json(session_path, "Work Session record"))
    workspace = Path(session["workspace"]["canonical_root"]).resolve(strict=True)
    if workspace.as_uri() != session["workspace"]["root_uri"]:
        _fail("Work Session workspace path changed after session setup")
    runtime = _ordinary_json(runtime_path, "runtime configuration")
    if (
        runtime.get("format")
        != "workbench-supersymmetry-installed-material-fluid-runtime-config-v1"
        or runtime.get("schema_version") != 1
    ):
        _fail("runtime configuration is not the installed Supersymmetry owner contract")
    session_id = session["session_id"]
    contexts = _contexts_root(suite)
    context_directory = _session_context_directory(suite, session_id)
    owner_directory = _session_owner_directory(suite, session_id)
    request_sha256 = _setup_request_sha256(request)
    runtime_sha256 = _sha256_file(runtime_path, "runtime configuration")
    session_sha256 = _sha256_file(session_path, "Work Session record")
    bind_lock = contexts / f".{session_id}.bind.lock"
    with _exclusive_record_lock(bind_lock, "feature change Work Session setup lock"):
        if context_directory.exists() or context_directory.is_symlink():
            context_path = _context_for_session(suite, session_id)
            validated = validate_feature_change_session_context(
                _ordinary_json(context_path, "feature change Work Session context")
            )
            if (
                validated["setup_request_sha256"] != request_sha256
                or validated["runtime_config_sha256"] != runtime_sha256
                or validated["session_record_sha256"] != session_sha256
            ):
                _fail(
                    "this Work Session already owns a different immutable "
                    "feature change context"
                )
        else:
            for stale in contexts.glob(f".{session_id}.staging-*"):
                _remove_incomplete_setup(
                    stale, "stale feature change Work Session staging directory"
                )
            _remove_incomplete_setup(
                owner_directory,
                "incomplete feature change Work Session owner directory",
            )
            staging = contexts / f".{session_id}.staging-{secrets.token_hex(16)}"
            staging.mkdir(mode=0o700)
            try:
                owner_directory.mkdir(mode=0o700)
                state_root = owner_directory / "owner-state"
                start = start_material_fluid_recipe_change(
                    suite, workspace, state_root, **request
                )
                if (
                    start.get("reused") is not False
                    or start.get("lifecycle") != "planned"
                ):
                    _fail(
                        "Work Session setup did not create one fresh planned change"
                    )
                start_path = owner_directory / "start-result-v1.json"
                _write_immutable(start_path, _validate_start_result(start))
                body = {
                    "change_id": start["change_id"],
                    "claims": {
                        "publication_authorized": False,
                        "release_authorized": False,
                        "support_claimed": False,
                    },
                    "created_at": datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "format": SESSION_CONTEXT_FORMAT,
                    "plan_id": start["plan_id"],
                    "runtime_config_sha256": runtime_sha256,
                    "runtime_config_uri": runtime_path.as_uri(),
                    "schema_version": 1,
                    "session_id": session_id,
                    "session_record_id": session["session_record_id"],
                    "session_record_sha256": session_sha256,
                    "session_record_uri": session_path.as_uri(),
                    "setup_request_sha256": request_sha256,
                    "start_result_sha256": _sha256_file(
                        start_path, "feature change start result"
                    ),
                    "start_result_uri": start_path.as_uri(),
                    "state_root_uri": state_root.resolve(strict=True).as_uri(),
                    "workspace_uri": workspace.as_uri(),
                }
                context = {
                    **body,
                    "context_id": _content_id(SESSION_CONTEXT_ID_PREFIX, body),
                }
                staged_context_path = staging / (
                    "context-"
                    + context["context_id"].removeprefix(SESSION_CONTEXT_ID_PREFIX)
                    + ".json"
                )
                _write_immutable(staged_context_path, context)
                validated = validate_feature_change_session_context(context)
                os.rename(staging, context_directory)
                fsync_directory(contexts)
                context_path = context_directory / staged_context_path.name
            except BaseException:
                if not context_directory.exists():
                    _remove_incomplete_setup(
                        staging,
                        "feature change Work Session staging directory",
                    )
                    _remove_incomplete_setup(
                        owner_directory,
                        "feature change Work Session owner directory",
                    )
                raise
        try:
            active, _state, _runtime = resolve_material_fluid_recipe_session_context(
                suite
            )
        except FeatureChangeWorkspaceError:
            active = None
        if active is None or active["context_id"] != validated["context_id"]:
            _publish_selection(
                suite, context=validated, context_path=context_path
            )
        return validated


def select_material_fluid_recipe_session_context(
    suite_root: Path | str,
    session_id: str,
) -> dict[str, Any]:
    """Atomically select one existing immutable Work Session context."""

    path = _context_for_session(suite_root, session_id)
    context = validate_feature_change_session_context(
        _ordinary_json(path, "feature change Work Session context")
    )
    if context["session_id"] != session_id:
        _fail("feature change context directory and session identity differ")
    return _publish_selection(suite_root, context=context, context_path=path)


def close_material_fluid_recipe_session_context(
    suite_root: Path | str,
) -> dict[str, Any]:
    """Atomically close current navigation without deleting owner context."""

    return _publish_selection(suite_root, context=None, context_path=None)


def read_feature_change_selected_context(
    suite_root: Path | str,
) -> dict[str, Any]:
    """Read the current selection and verify its complete predecessor chain."""

    root = _session_context_root(suite_root)
    selected_path = root / "selected-context-v1.json"
    selected_raw = _ordinary_bytes(
        selected_path, "selected feature change context"
    )
    try:
        decoded = json.loads(selected_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FeatureChangeWorkspaceError(
            f"cannot decode selected feature change context: {exc}"
        ) from exc
    selected = validate_feature_change_selected_context(decoded)
    current_history = _selection_history_path(root, selected["selection_id"])
    try:
        retained_raw = _ordinary_bytes(
            current_history, "retained current feature change selection"
        )
    except FeatureChangeWorkspaceError as exc:
        raise FeatureChangeWorkspaceError(
            "current feature change selection is not durably retained"
        ) from exc
    if retained_raw != selected_raw:
        _fail("current feature change selection is not durably retained")
    _validate_selection_chain(root, selected)
    return selected


def resolve_material_fluid_recipe_session_context(
    suite_root: Path | str,
) -> tuple[dict[str, Any], Path, Path]:
    """Resolve the already-open context without accepting a path or owner ID."""

    selected = read_feature_change_selected_context(suite_root)
    if selected["state"] != "open":
        _fail("no feature change Work Session context is currently open")
    context_path = _local_uri(selected["context_uri"], "selected feature change context")
    expected_context_path = _context_for_session(
        suite_root, selected["session_id"]
    )
    if (
        context_path != expected_context_path
        or selected["context_uri"] != expected_context_path.as_uri()
        or expected_context_path.name
        != "context-"
        + selected["context_id"].removeprefix(SESSION_CONTEXT_ID_PREFIX)
        + ".json"
    ):
        _fail("selected feature change context points outside its session owner")
    if _sha256_file(context_path, "selected feature change context") != selected["context_sha256"]:
        _fail("selected feature change context pointer is stale")
    context = validate_feature_change_session_context(
        _ordinary_json(context_path, "active feature change Work Session context")
    )
    if (
        context["context_id"] != selected["context_id"]
        or context["session_id"] != selected["session_id"]
    ):
        _fail("selected feature change context pointer changes identity")
    owner_directory = _session_owner_directory(
        suite_root, context["session_id"]
    )
    _ordinary_directory(
        owner_directory, "feature change Work Session owner directory"
    )
    expected_state_root = owner_directory / "owner-state"
    expected_start_path = owner_directory / "start-result-v1.json"
    if (
        _local_uri(context["state_root_uri"], "feature change state root")
        != expected_state_root
        or _local_uri(
            context["start_result_uri"], "feature change start result"
        )
        != expected_start_path
    ):
        _fail("feature change context points outside its immutable session storage")
    return (
        context,
        expected_state_root,
        _local_uri(context["runtime_config_uri"], "runtime configuration"),
    )




def _pair_request(
    opened: Mapping[str, Any],
    *,
    comparison: str,
    side: str,
    order: str,
    attempt_id: str,
) -> dict[str, Any]:
    if comparison == "aa":
        roles = ["baseline", "baseline"]
    elif comparison in {"post-apply", "post-rollback"}:
        roles = ["candidate", "candidate"] if comparison == "post-apply" else ["baseline", "baseline"]
    else:
        roles = ["baseline", "candidate"] if order == "baseline-first" else ["candidate", "baseline"]
    body = {
        "format": PAIR_REQUEST_FORMAT,
        "schema_version": 1,
        "change_id": opened["change_id"],
        "plan_id": opened["plan_id"],
        "comparison": comparison,
        "side": side,
        "order": order,
        "attempt_id": attempt_id,
        "roles": roles,
        "required_assertions": list(_REQUIRED_ASSERTIONS[side]),
    }
    return {**body, "request_id": _content_id(PAIR_REQUEST_ID_PREFIX, body)}


def _validate_pair_result(
    request: Mapping[str, Any],
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("runtime pair owner returned no ordinary result")
    result = deepcopy(dict(value))
    if (
        set(result) != {
            "format", "request_id", "side", "comparison", "order", "state",
            "outcome", "comparison_state", "observations", "cleanup", "owner_refs",
        }
        or result.get("format") != PAIR_RESULT_FORMAT
        or any(result.get(field) != request[field] for field in ("request_id", "side", "comparison", "order"))
        or result.get("state") not in {"complete", "incomplete", "failed"}
        or result.get("outcome") not in {"passed", "failed", "incomplete"}
        or type(result.get("observations")) is not list
        or len(result["observations"]) != 2
        or type(result.get("cleanup")) is not dict
        or set(result["cleanup"]) != {"contained", "owned_processes_running"}
        or type(result["cleanup"].get("contained")) is not bool
        or type(result["cleanup"].get("owned_processes_running")) is not bool
        or type(result.get("owner_refs")) is not list
        or not result["owner_refs"]
    ):
        _fail("runtime pair owner result identity or shape changed")
    for owner in result["owner_refs"]:
        if (
            type(owner) is not dict
            or set(owner) != {
                "owner_id", "record_id", "record_kind", "uri", "sha256",
                "size", "outcome",
            }
            or any(type(owner.get(field)) is not str or not owner[field] for field in (
                "owner_id", "record_id", "record_kind", "uri", "sha256", "outcome"
            ))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", owner["sha256"])
            or type(owner.get("size")) is not int
            or owner["size"] < 0
            or owner["outcome"] not in {"passed", "failed", "incomplete"}
        ):
            _fail("runtime pair owner reference is not exact")
        path = _local_uri(owner["uri"], "runtime pair owner record")
        raw = _ordinary_bytes(path, "runtime pair owner record")
        if (
            len(raw) != owner["size"]
            or "sha256:" + sha256(raw).hexdigest() != owner["sha256"]
        ):
            _fail("runtime pair owner record bytes changed")
    for ordinal, observation in enumerate(result["observations"]):
        if (
            type(observation) is not dict
            or set(observation) != {"role", "observation_id", "assertions"}
            or observation.get("role") != request["roles"][ordinal]
            or type(observation.get("observation_id")) is not str
            or not observation["observation_id"]
            or type(observation.get("assertions")) is not dict
            or set(observation["assertions"]) != set(request["required_assertions"])
            or any(state not in {"observed", "failed", "unavailable"} for state in observation["assertions"].values())
        ):
            _fail("runtime pair observation changed its exact requested contract")
    expected_comparison = {
        "aa": "stable",
        "ab": "observed-change",
        "post-apply": "matches-candidate",
        "post-rollback": "matches-baseline",
    }[request["comparison"]]
    passed = (
        result["state"] == "complete"
        and result["outcome"] == "passed"
        and result["comparison_state"] == expected_comparison
        and result["cleanup"] == {"contained": True, "owned_processes_running": False}
        and all(
            state == "observed"
            for observation in result["observations"]
            for state in observation["assertions"].values()
        )
    )
    result["composition_passed"] = passed
    return result


def _run_pair(
    suite: Path,
    directory: Path,
    opened: Mapping[str, Any],
    ports: FeatureRuntimePairPorts,
    *,
    comparison: str,
    side: str,
    order: str,
    attempt_id: str,
) -> dict[str, Any]:
    if not isinstance(ports, FeatureRuntimePairPorts):
        _fail("feature runtime requires exact FeatureRuntimePairPorts")
    request = _pair_request(
        opened,
        comparison=comparison,
        side=side,
        order=order,
        attempt_id=attempt_id,
    )
    attempt = directory / "runtime" / request["request_id"].rsplit(":", 1)[1]
    try:
        attempt.mkdir(mode=0o700)
    except OSError as exc:
        raise FeatureChangeWorkspaceError(
            f"feature runtime attempt already exists or is unsafe: {exc}"
        ) from exc
    _write_immutable(attempt / "request.json", request)
    def owner_failure(message: str) -> dict[str, Any]:
        return {
            "format": PAIR_RESULT_FORMAT,
            "request_id": request["request_id"],
            "side": side,
            "comparison": comparison,
            "order": order,
            "state": "failed",
            "outcome": "failed",
            "comparison_state": "unavailable",
            "observations": [
                {
                    "role": role,
                    "observation_id": (
                        f"owner-error:{request['request_id']}:{ordinal}"
                    ),
                    "assertions": {
                        name: "unavailable"
                        for name in request["required_assertions"]
                    },
                }
                for ordinal, role in enumerate(request["roles"])
            ],
            "cleanup": {"contained": False, "owned_processes_running": True},
            "owner_refs": [],
            "composition_passed": False,
            "owner_error": message,
        }

    try:
        result = ports.run_pair(
            deepcopy(request),
            suite_root=suite,
            plan=deepcopy(opened["plan"]),
            attempt_root=attempt,
        )
    except Exception as caught:
        failed = owner_failure(f"{type(caught).__name__}: {str(caught)[:512]}")
        _write_immutable(attempt / "result.json", failed)
        return failed
    try:
        validated = _validate_pair_result(request, result)
    except Exception as caught:
        failed = owner_failure(f"{type(caught).__name__}: {str(caught)[:512]}")
        _write_immutable(attempt / "result.json", failed)
        return failed
    _write_immutable(attempt / "result.json", validated)
    return validated


def run_feature_change_matrix(
    suite_root: Path | str,
    state_root: Path | str,
    change_id: str,
    *,
    ports: FeatureRuntimePairPorts,
) -> dict[str, Any]:
    """Run both A/A controls before ordered A/B on client and server."""

    suite = Path(suite_root).resolve()
    root = _safe_state_root(state_root, create=False)
    directory = _change_directory(root, change_id, create=False)
    opened = _open_directory(directory)
    if opened["lifecycle"] not in {"planned", "test-no-go", "rolled-back"}:
        _fail("feature runtime matrix is not eligible in the current lifecycle")
    initial = verify_material_fluid_recipe_plan(suite, opened["plan"])
    if initial.get("state") != "ready":
        _fail(f"feature plan is stale: {initial.get('reason')}")
    cases: list[dict[str, Any]] = []
    matrix_attempt = "matrix-" + secrets.token_hex(16)
    for comparison, side, order in _CONTROL_CASES:
        cases.append(_run_pair(
            suite, directory, opened, ports,
            comparison=comparison, side=side, order=order,
            attempt_id=matrix_attempt,
        ))
    controls_passed = all(row["composition_passed"] for row in cases)
    if controls_passed:
        for comparison, side, order in _COMPARISON_CASES:
            row = _run_pair(
                suite, directory, opened, ports,
                comparison=comparison, side=side, order=order,
                attempt_id=matrix_attempt,
            )
            cases.append(row)
            if not row["cleanup"]["contained"] or row["cleanup"]["owned_processes_running"]:
                break
    final = verify_material_fluid_recipe_plan(suite, opened["plan"])
    passed = (
        controls_passed
        and len(cases) == len(_CONTROL_CASES) + len(_COMPARISON_CASES)
        and all(row["composition_passed"] for row in cases)
        and final.get("state") == "ready"
    )
    body = {
        "format": MATRIX_FORMAT,
        "schema_version": 1,
        "change_id": change_id,
        "plan_id": opened["plan_id"],
        "outcome": "passed" if passed else "no-go",
        "controls_passed": controls_passed,
        "cases": cases,
        "source": {"initial": initial, "final": final},
        "evidence_domains": {
            "construction": "blueprints-profile-plan",
            "runtime": "owner-pair-results",
            "support": "unchanged",
        },
    }
    matrix = {**body, "matrix_id": _content_id(MATRIX_ID_PREFIX, body)}
    _append_event(directory, change_id, "matrix-completed", matrix)
    return matrix


def _last_payload(opened: Mapping[str, Any], kinds: set[str]) -> dict[str, Any] | None:
    for event in reversed(opened["events"]):
        if event["kind"] in kinds:
            return deepcopy(event["payload"])
    return None


def apply_feature_change(
    suite_root: Path | str,
    state_root: Path | str,
    change_id: str,
    *,
    consent_plan_id: str,
) -> dict[str, Any]:
    """Apply only after a passing complete runtime matrix."""

    suite = Path(suite_root).resolve()
    root = _safe_state_root(state_root, create=False)
    directory = _change_directory(root, change_id, create=False)
    opened = _open_directory(directory)
    matrix = _last_payload(opened, {"matrix-completed"})
    if opened["lifecycle"] != "tested" or matrix is None or matrix.get("outcome") != "passed":
        _fail("feature apply requires a passing runtime matrix")
    transaction = _local_uri(
        opened["header"]["transaction_root_uri"], "feature transaction root"
    )
    committed: list[dict[str, Any]] = []

    def commit(receipt: Mapping[str, Any]) -> None:
        validated = validate_material_fluid_recipe_receipt(receipt, opened["plan"])
        _append_event(directory, change_id, "applied", validated)
        committed.append(validated)

    receipt = apply_material_fluid_recipe_plan(
        suite,
        opened["plan"],
        transaction,
        consent_plan_id=consent_plan_id,
        commit_receipt=commit,
    )
    if receipt["state"] == "applied":
        if committed != [receipt]:
            _fail("feature transaction did not durably commit its exact receipt")
    else:
        _append_event(directory, change_id, "apply-rejected", receipt)
    return receipt


def _current_operation_state(plan: Mapping[str, Any], *, prefix: str) -> tuple[bool, list[dict[str, Any]]]:
    workspace = material_fluid_recipe_workspace(plan)
    rows: list[dict[str, Any]] = []
    matches = True
    for operation in plan["operations"]:
        path = workspace / operation["path"]
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
        expected = base64.b64decode(operation[f"{prefix}_base64"], validate=True)
        observed = {
            "path": operation["path"],
            "expected_sha256": operation[f"{prefix}_sha256"],
            "observed_sha256": sha256(raw).hexdigest(),
            "matches": raw == expected,
        }
        matches = matches and observed["matches"]
        rows.append(observed)
    return matches, rows


def _post_transaction_pairs(
    suite: Path,
    directory: Path,
    opened: Mapping[str, Any],
    ports: FeatureRuntimePairPorts,
    *,
    comparison: str,
) -> list[dict[str, Any]]:
    return [
        _run_pair(
            suite, directory, opened, ports,
            comparison=comparison, side=side, order="baseline-first",
            attempt_id=comparison + "-" + secrets.token_hex(16),
        )
        for side in _SIDES
    ]


def verify_feature_change(
    suite_root: Path | str,
    state_root: Path | str,
    change_id: str,
    *,
    ports: FeatureRuntimePairPorts,
) -> dict[str, Any]:
    """Verify exact applied bytes and rerun both physical-side assertions."""

    suite = Path(suite_root).resolve()
    root = _safe_state_root(state_root, create=False)
    directory = _change_directory(root, change_id, create=False)
    opened = _open_directory(directory)
    if opened["lifecycle"] != "applied":
        _fail("feature verification requires one successful application")
    application = _last_payload(opened, {"applied"})
    assert application is not None
    validate_material_fluid_recipe_receipt(application, opened["plan"])
    bytes_match, files = _current_operation_state(opened["plan"], prefix="after")
    pairs = _post_transaction_pairs(
        suite, directory, opened, ports, comparison="post-apply"
    ) if bytes_match else []
    passed = bytes_match and len(pairs) == 2 and all(row["composition_passed"] for row in pairs)
    result = {
        "format": "workbench-feature-change-verification-v1",
        "state": "verified" if passed else "failed",
        "plan_id": opened["plan_id"],
        "application_receipt_id": application["id"],
        "files": files,
        "runtime_pairs": pairs,
    }
    _append_event(
        directory,
        change_id,
        "verified" if passed else "verification-failed",
        result,
    )
    return result


def rollback_feature_change(
    suite_root: Path | str,
    state_root: Path | str,
    change_id: str,
    *,
    ports: FeatureRuntimePairPorts | None = None,
) -> dict[str, Any]:
    """Restore exact before-bytes, refusing to overwrite any later edit."""

    suite = Path(suite_root).resolve()
    root = _safe_state_root(state_root, create=False)
    directory = _change_directory(root, change_id, create=False)
    opened = _open_directory(directory)
    if opened["lifecycle"] not in {"applied", "verified", "verification-failed", "rollback-blocked"}:
        _fail("feature rollback is not eligible in the current lifecycle")
    application = _last_payload(opened, {"applied"})
    if application is None:
        _fail("feature rollback has no retained application receipt")
    transaction = _local_uri(
        opened["header"]["transaction_root_uri"], "feature transaction root"
    )
    owner = rollback_material_fluid_recipe(
        opened["plan"], transaction, application_receipt=application
    )
    if owner["state"] != "restored":
        result = {"state": "blocked", "owner_receipt": owner, "runtime_pairs": []}
        _append_event(directory, change_id, "rollback-blocked", result)
        return result
    bytes_match, files = _current_operation_state(opened["plan"], prefix="before")
    pairs = (
        _post_transaction_pairs(
            suite, directory, opened, ports, comparison="post-rollback"
        )
        if bytes_match and ports is not None
        else []
    )
    runtime_passed = ports is not None and len(pairs) == 2 and all(
        row["composition_passed"] for row in pairs
    )
    result = {
        "state": "restored" if bytes_match and runtime_passed else "restored-runtime-unverified",
        "owner_receipt": owner,
        "files": files,
        "runtime_pairs": pairs,
    }
    _append_event(directory, change_id, "rolled-back", result)
    return result


def recover_feature_change(state_root: Path | str, change_id: str) -> dict[str, Any]:
    """Invoke the existing transaction recovery unless rollback already closed it."""

    root = _safe_state_root(state_root, create=False)
    directory = _change_directory(root, change_id, create=False)
    opened = _open_directory(directory)
    if opened["lifecycle"] == "rolled-back":
        result = {"state": "not-needed", "owner_receipt": None}
    else:
        transaction = _local_uri(
            opened["header"]["transaction_root_uri"], "feature transaction root"
        )
        owner = recover_material_fluid_recipe(opened["plan"], transaction)
        result = {"state": owner["state"], "owner_receipt": owner}
    _append_event(directory, change_id, "recovery-checked", result)
    return result


__all__ = [
    "EVENT_FORMAT",
    "HEADER_FORMAT",
    "MATRIX_FORMAT",
    "START_RESULT_FORMAT",
    "SESSION_CONTEXT_FORMAT",
    "SELECTION_FORMAT",
    "VIEW_FORMAT",
    "FeatureChangeWorkspaceError",
    "apply_feature_change",
    "bind_material_fluid_recipe_session_context",
    "close_material_fluid_recipe_session_context",
    "open_feature_change",
    "read_feature_change_selected_context",
    "recover_feature_change",
    "resolve_material_fluid_recipe_session_context",
    "select_material_fluid_recipe_session_context",
    "rollback_feature_change",
    "run_feature_change_matrix",
    "start_material_fluid_recipe_change",
    "verify_feature_change",
    "validate_feature_change_session_context",
    "validate_feature_change_selected_context",
]
