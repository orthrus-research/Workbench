"""Crash-tolerant retained state for live-console sessions."""

from __future__ import annotations

from collections import Counter
from contextlib import suppress
from datetime import datetime, timezone
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, BinaryIO, Mapping, TextIO

from workbench_api.sessions import (
    EVENTS_NAME,
    FORMAT_VERSION,
    MAX_RAW_STREAMS,
    RawLocator,
    SessionError,
    _COMMAND_ID_RE,
    _STREAM_RE,
    _single_line,
    _validate_command_metadata,
    _validate_manifest_command,
    _validate_manifest_timestamp,
    validate_session_manifest,
)

from workbench_core.host_filesystem import fsync_directory, private_path

from workbench_api.state_paths import PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE
from workbench_api.state_paths import default_suite_state_root


MANIFEST_NAME = "session-v1.json"
MANIFEST_REVISIONS_DIRECTORY = "manifest-revisions-v2"
MANIFEST_REVISION_FORMAT = "workbench-live-console-manifest-revision-v2"
ARTIFACT_INDEX_NAME = "artifact-index-v1.json"
ARTIFACT_INDEX_FORMAT = "workbench-live-console-artifact-index-v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MANIFEST_REVISIONS = 4096
MAX_REVISION_BYTES = 64 * 1024
MAX_EVENT_JOURNAL_BYTES = 64 * 1024 * 1024
# The public reader must cover the largest record emitted by the default V1
# supervisor, including a two-byte CRLF terminator.  It remains bounded and
# returns one selected event range, never an arbitrary whole transcript.
MAX_ARTIFACT_RANGE_BYTES = 256 * 1024 + 2
MAX_ARTIFACT_EVENT_PAGE = 1024
MAX_RETAINED_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
MAX_EVENT_MESSAGE_CHARACTERS = 1024 * 1024
MAX_EVENT_RECORD_JSON_BYTES = 8 * 1024 * 1024
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,120}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_BOOT_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)




def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def _open_private(path: Path, *, binary: bool) -> BinaryIO | TextIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    if binary:
        return os.fdopen(descriptor, "wb", buffering=0)
    return os.fdopen(descriptor, "w", encoding="utf-8", buffering=1)


def _canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        # Match the V1 producer byte-for-byte.  The manifest historically used
        # json.dumps defaults, including ASCII escaping; the sidecar uses the
        # same spelling so one digest has one portable representation.
        "ensure_ascii": True,
        "allow_nan": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def _digest(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def _content_identity(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + ":sha256:" + sha256(
        _canonical_json_bytes(value, pretty=False)
    ).hexdigest()


def _atomic_private_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    payload = _canonical_json_bytes(value, pretty=True)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _create_private_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical_json_bytes(value, pretty=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    fsync_directory(path.parent)


def _safe_session_root(root: Path, *, create: bool) -> Path:
    supplied = root.expanduser()
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    lexical = Path(os.path.abspath(absolute))
    for component in [*reversed(lexical.parents), lexical]:
        if component.is_symlink():
            raise SessionError(
                f"console session storage cannot traverse a symlink: {component}"
            )
    root = lexical.resolve()
    # ``root`` has two established meanings at this low-level boundary: the
    # suite root used by the public console and an already selected state base
    # used by Work Sessions and the Cleanroom development loop.  Redirect only
    # the exact embedded suite identified by the verified portable launcher;
    # applying the suite-state policy to an arbitrary state base would move its
    # retained sessions away from the readers that share that base.
    workbench = root / ".workbench"
    packaged_value = os.environ.get(PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE)
    if packaged_value:
        try:
            packaged_suite = Path(packaged_value).expanduser().resolve(strict=True)
        except OSError:
            packaged_suite = None
        if packaged_suite == root:
            workbench = default_suite_state_root(root)
    sessions = workbench / "sessions"
    destination = sessions / "live-console"
    for path in (workbench, sessions, destination):
        if path.is_symlink():
            raise SessionError(f"console session storage cannot be a symlink: {path}")
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
            with suppress(OSError):
                path.chmod(0o700)
        if path.exists() and not private_path(path, directory=True):
            raise SessionError(f"console session storage is not owner-private: {path}")
    return destination




class RetainedSession:
    """Append raw bytes and derived event projections to a private directory."""

    def __init__(
        self,
        *,
        root: Path,
        command_id: str,
        argv: list[str],
        cwd: Path,
        intent: str,
        label: str | None = None,
        session_id: str | None = None,
    ) -> None:
        command_id, label, argv, cwd, intent = _validate_command_metadata(
            command_id=command_id,
            label=label,
            argv=argv,
            cwd=cwd,
            intent=intent,
        )
        base = _safe_session_root(root, create=True)
        self.session_id = session_id or _new_session_id()
        if not _SESSION_ID_RE.fullmatch(self.session_id):
            raise SessionError("invalid console session ID")
        self.directory = base / self.session_id
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise SessionError(f"console session already exists: {self.session_id}") from exc
        self.directory.chmod(0o700)
        self._revision_directory = self.directory / MANIFEST_REVISIONS_DIRECTORY
        self._revision_directory.mkdir(mode=0o700)
        self._revision_directory.chmod(0o700)
        self._events = _open_private(self.directory / EVENTS_NAME, binary=False)
        events_metadata = os.fstat(self._events.fileno())
        self._events_file_identity = (
            events_metadata.st_dev,
            events_metadata.st_ino,
        )
        self._events_digest = sha256()
        self._events_size = 0
        self._raw: dict[str, BinaryIO] = {}
        self._raw_offsets: dict[str, int] = {}
        self._raw_file_identities: dict[str, tuple[int, int]] = {}
        self._raw_digests: dict[str, Any] = {}
        self._closed = False
        self._retention_failure: str | None = None
        self._counts: Counter[str] = Counter()
        self._subsystems: Counter[str] = Counter()
        self._kinds: Counter[str] = Counter()
        self._outcome_failures = 0
        self._source_locators = 0
        self._manifest_digest: str | None = None
        self._manifest_revision = -1
        self._process_custody: dict[str, Any] | None = None
        self._artifact_index_digest: str | None = None
        self._terminal_assigned = False
        started = utc_now()
        self.value: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "session_id": self.session_id,
            "state": "running",
            "started_at": started,
            "updated_at": started,
            "command": {
                "command_id": command_id,
                "label": label,
                "argv": list(argv),
                "cwd": str(cwd),
                "intent": intent,
                "shell": False,
                "pid": None,
                "process_group_id": None,
            },
            "retention": {
                "directory": str(self.directory),
                "events": EVENTS_NAME,
                "raw_streams": {},
                "raw_is_source_record": True,
                "events_are_projection": True,
            },
            "summary": self._summary(),
            "limitations": [
                "Console classifications are presentation metadata, not Atlas causal findings.",
                "Raw streams retain decoder-invalid bytes; rendered event text uses replacement decoding.",
            ],
        }
        self._publish()
        fsync_directory(self.directory)
        fsync_directory(base)

    @property
    def retained(self) -> bool:
        return True

    def bind_process(self, pid: int, process_group_id: int | None) -> None:
        if self._closed:
            raise SessionError("cannot bind a closed console session")
        if self._process_custody is not None or self.value["command"]["pid"] is not None:
            raise SessionError("console session process custody is already bound")
        custody = _capture_process_custody(pid, process_group_id)
        self.value["command"]["pid"] = pid
        self.value["command"]["process_group_id"] = process_group_id
        self._process_custody = custody
        self._touch()
        self._publish()

    def write_raw(self, stream: str, data: bytes) -> RawLocator:
        if self._closed:
            raise SessionError("cannot write a closed console session")
        if not _STREAM_RE.fullmatch(stream):
            raise SessionError(f"invalid raw stream name: {stream!r}")
        output = self._raw.get(stream)
        if output is None:
            if len(self._raw) >= MAX_RAW_STREAMS:
                raise SessionError(
                    f"live-console V1 permits at most {MAX_RAW_STREAMS} raw streams"
                )
            name = f"{stream}.raw"
            opened = _open_private(self.directory / name, binary=True)
            assert hasattr(opened, "write")
            output = opened  # type: ignore[assignment]
            self._raw[stream] = output
            self._raw_offsets[stream] = 0
            metadata = os.fstat(output.fileno())
            self._raw_file_identities[stream] = (
                metadata.st_dev,
                metadata.st_ino,
            )
            self._raw_digests[stream] = sha256()
            self.value["retention"]["raw_streams"][stream] = name
            self._publish()
        start = self._raw_offsets[stream]
        if not isinstance(data, bytes):
            raise SessionError("retained console raw writes must be exact bytes")
        try:
            written = output.write(data)
        except OSError as exc:
            self._remember_retention_failure(f"{stream} write: {exc}")
            raise SessionError(f"could not retain exact {stream} bytes") from exc
        if written != len(data):
            self._remember_retention_failure(
                f"{stream} write retained {written!r} of {len(data)} bytes"
            )
            raise SessionError(f"could not retain exact {stream} bytes")
        end = start + len(data)
        self._raw_offsets[stream] = end
        self._raw_digests[stream].update(data)
        return RawLocator(stream, self.value["retention"]["raw_streams"][stream], start, end)

    def open_raw(self, stream: str) -> RawLocator:
        """Declare an observed stream even when it produces zero bytes."""

        return self.write_raw(stream, b"")

    def record_event(self, event: Mapping[str, Any] | Any) -> dict[str, Any]:
        if self._closed:
            raise SessionError("cannot write a closed console session")
        value = _event_dict(event)
        try:
            encoded = (
                json.dumps(
                    value,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise SessionError("live-console event is not canonical JSON") from exc
        try:
            written = self._events.write(encoded.decode("ascii"))
        except OSError as exc:
            self._remember_retention_failure(f"events write: {exc}")
            raise SessionError("could not retain exact live-console event") from exc
        if written != len(encoded):
            self._remember_retention_failure(
                f"events write retained {written!r} of {len(encoded)} bytes"
            )
            raise SessionError("could not retain exact live-console event")
        self._events_digest.update(encoded)
        self._events_size += len(encoded)
        severity = str(value.get("severity", "unknown"))
        subsystem = str(value.get("subsystem", "unknown"))
        kind = str(value.get("kind", "unknown"))
        self._counts[severity] += 1
        self._subsystems[subsystem] += 1
        self._kinds[kind] += 1
        self._outcome_failures += int(bool(value.get("outcome_failure")))
        locators = value.get("source_locators")
        if isinstance(locators, list):
            self._source_locators += len(locators)
        return value

    def finish(
        self,
        *,
        state: str,
        process_exit_code: int | None,
        effective_exit_code: int,
        outcome: str,
        cancellation: str | None = None,
        extra_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in {"complete", "failed", "cancelled", "incomplete"}:
            raise SessionError(f"invalid terminal console session state: {state}")
        if self._terminal_assigned or self.value["state"] != "running":
            raise SessionError(
                "live-console terminal manifest is single-assignment and cannot be rewritten"
            )
        if extra_summary:
            raise SessionError(
                "live-console terminal summary is derived from retained events"
            )
        self._validate_terminal_claim(
            state=state,
            process_exit_code=process_exit_code,
            effective_exit_code=effective_exit_code,
            outcome=outcome,
            cancellation=cancellation,
            summary=self._summary(),
        )
        self._terminal_assigned = True
        if not self._closed:
            failures: list[str] = []
            for stream, output in self._raw.items():
                try:
                    output.flush()
                    os.fsync(output.fileno())
                except OSError as exc:
                    failures.append(f"{stream}: {exc}")
                finally:
                    try:
                        output.close()
                    except OSError as exc:
                        failures.append(f"{stream} close: {exc}")
            try:
                self._events.flush()
                os.fsync(self._events.fileno())
            except OSError as exc:
                failures.append(f"events: {exc}")
            finally:
                try:
                    self._events.close()
                except OSError as exc:
                    failures.append(f"events close: {exc}")
            self._closed = True
            if failures:
                detail = "; ".join(failures).replace("\r", " ").replace("\n", " ")
                self._retention_failure = detail[:2048]
                limitation = "Retained stream finalization failed: " + self._retention_failure
                if limitation not in self.value["limitations"]:
                    self.value["limitations"].append(limitation)
        if self._retention_failure is None:
            try:
                expected_event_identity = {
                    "size": self._events_size,
                    "digest": "sha256:" + self._events_digest.hexdigest(),
                    "device": self._events_file_identity[0],
                    "inode": self._events_file_identity[1],
                }
                expected_raw_identities = {
                    stream: {
                        "size": self._raw_offsets[stream],
                        "digest": "sha256:" + digest.hexdigest(),
                        "device": self._raw_file_identities[stream][0],
                        "inode": self._raw_file_identities[stream][1],
                    }
                    for stream, digest in self._raw_digests.items()
                }
                self._artifact_index_digest = _create_live_console_artifact_index(
                    self.directory,
                    session_id=self.session_id,
                    predecessor_manifest_digest=self._manifest_digest,
                    command_identity=_manifest_command_identity(self.value["command"]),
                    raw_streams=self.value["retention"]["raw_streams"],
                    expected_event_identity=expected_event_identity,
                    expected_raw_identities=expected_raw_identities,
                    expected_summary=self._summary(),
                )
            except (OSError, SessionError) as exc:
                self._retention_failure = _single_line(str(exc), maximum=2048)
                limitation = (
                    "Retained artifact sealing failed: " + self._retention_failure
                )
                if limitation not in self.value["limitations"]:
                    self.value["limitations"].append(limitation)
        if self._retention_failure is not None:
            state = "incomplete"
            effective_exit_code = 2
            outcome = "retention-error"
        ended = utc_now()
        summary = self._summary()
        self.value.update(
            {
                "state": state,
                "updated_at": ended,
                "ended_at": ended,
                "summary": summary,
                "exit": {
                    "process_exit_code": process_exit_code,
                    "effective_exit_code": effective_exit_code,
                    "outcome": outcome,
                    "cancellation": cancellation,
                },
            }
        )
        self._publish()
        if self._retention_failure is not None:
            raise SessionError(
                "could not durably finalize retained console streams: "
                + self._retention_failure
            )
        return dict(self.value)

    def _remember_retention_failure(self, detail: str) -> None:
        normalized = _single_line(str(detail), maximum=2048)
        if self._retention_failure is None:
            self._retention_failure = normalized

    def _validate_terminal_claim(
        self,
        *,
        state: str,
        process_exit_code: int | None,
        effective_exit_code: int,
        outcome: str,
        cancellation: str | None,
        summary: Mapping[str, Any],
    ) -> None:
        if process_exit_code is not None and type(process_exit_code) is not int:
            raise SessionError("live-console process exit observation is invalid")
        if type(effective_exit_code) is not int or effective_exit_code < 0:
            raise SessionError("live-console effective exit observation is invalid")
        if not isinstance(outcome, str) or not outcome:
            raise SessionError("live-console terminal outcome is invalid")
        _single_line(outcome, maximum=8192, reject=True)
        if cancellation is not None:
            if not isinstance(cancellation, str) or not cancellation:
                raise SessionError("live-console cancellation observation is invalid")
            _single_line(cancellation, maximum=8192, reject=True)

        outcome_failures = summary.get("outcome_failure_events")
        if type(outcome_failures) is not int or outcome_failures < 0:
            raise SessionError("live-console derived outcome count is invalid")
        process_is_live = (
            self._process_custody is not None
            and _manifest_process_alive(self.value, self._process_custody)
        )
        if state in {"complete", "failed", "cancelled"} and process_is_live:
            raise SessionError(
                "live-console terminal state requires observed process termination"
            )
        if cancellation is not None and state not in {"cancelled", "incomplete"}:
            raise SessionError(
                "live-console cancellation cannot produce the requested terminal state"
            )
        if state == "cancelled":
            if cancellation is None or effective_exit_code == 0 or outcome != "cancelled":
                raise SessionError("live-console cancelled terminal tuple is incoherent")
            if self.value["command"]["intent"] == "execute" and process_exit_code is None:
                raise SessionError(
                    "live-console cancellation has no terminal process observation"
                )
            return
        if state == "complete":
            if (
                cancellation is not None
                or effective_exit_code != 0
                or outcome != "complete"
                or outcome_failures != 0
                or process_exit_code not in {None, 0}
            ):
                raise SessionError("live-console complete terminal tuple is incoherent")
            if self.value["command"]["intent"] == "execute" and (
                self._process_custody is None or process_exit_code != 0
            ):
                raise SessionError(
                    "live-console execute completion has no exact process result"
                )
            return
        if state == "failed":
            if (
                cancellation is not None
                or effective_exit_code == 0
                or outcome in {"complete", "cancelled"}
            ):
                raise SessionError("live-console failed terminal tuple is incoherent")
            return
        if (
            state != "incomplete"
            or effective_exit_code == 0
            or outcome in {"complete", "cancelled"}
        ):
            raise SessionError("live-console incomplete terminal tuple is incoherent")

    def abort(self, message: str) -> None:
        if self._closed:
            return
        self.value["limitations"].append(
            "Session writer aborted: " + _single_line(str(message), maximum=2048)
        )
        self.finish(
            state="incomplete",
            process_exit_code=None,
            effective_exit_code=2,
            outcome="console-error",
        )

    def _summary(self) -> dict[str, Any]:
        return {
            "event_count": sum(self._counts.values()),
            "severity_counts": dict(sorted(self._counts.items())),
            "subsystem_counts": dict(sorted(self._subsystems.items())),
            "kind_counts": dict(sorted(self._kinds.items())),
            "outcome_failure_events": self._outcome_failures,
            "source_locator_count": self._source_locators,
        }

    def _touch(self) -> None:
        self.value["updated_at"] = utc_now()

    def _publish(self) -> None:
        if self._manifest_digest is not None:
            _, current_raw, proof = _read_exact_manifest(self.directory)
            if (
                _digest(current_raw) != self._manifest_digest
                or proof["current_digest"] != self._manifest_digest
                or proof["current_revision"] != self._manifest_revision
            ):
                raise SessionError(
                    "live-console manifest changed outside its immutable revision chain"
                )
            if proof["command_identity"] != _manifest_command_identity(
                self.value["command"]
            ):
                raise SessionError("live-console stable command identity changed")
            if proof["process_custody"] is not None and (
                proof["process_custody"] != self._process_custody
            ):
                raise SessionError("live-console process custody changed")
            if (
                proof["artifact_index_digest"] is not None
                and proof["artifact_index_digest"] != self._artifact_index_digest
            ):
                raise SessionError("live-console artifact index custody changed")
        validate_session_manifest(self.value, physical_directory=self.directory)
        command = self.value["command"]
        if self._process_custody is None:
            if command["pid"] is not None or command["process_group_id"] is not None:
                raise SessionError("live-console process has no exact custody identity")
        else:
            _validate_process_custody(self._process_custody)
            if (
                command["pid"] != self._process_custody["pid"]
                or command["process_group_id"]
                != self._process_custody["process_group_id"]
            ):
                raise SessionError("live-console process differs from exact custody")
        manifest_raw = _canonical_json_bytes(self.value, pretty=True)
        manifest_digest = _digest(manifest_raw)
        if manifest_digest == self._manifest_digest:
            return
        revision = self._manifest_revision + 1
        if revision >= MAX_MANIFEST_REVISIONS:
            raise SessionError("live-console manifest revision limit exceeded")
        command_identity = _manifest_command_identity(self.value["command"])
        record: dict[str, Any] = {
            "format_version": MANIFEST_REVISION_FORMAT,
            "revision": revision,
            "revision_id": "pending",
            "session_id": self.session_id,
            "manifest_digest": manifest_digest,
            "predecessor_manifest_digest": self._manifest_digest,
            "command_identity": command_identity,
            "process_custody": (
                None if self._process_custody is None else dict(self._process_custody)
            ),
            "artifact_index_digest": self._artifact_index_digest,
            "published_at": utc_now(),
        }
        record_body = dict(record)
        record_body.pop("revision_id")
        record["revision_id"] = _content_identity(
            "live-console-manifest-revision", record_body
        )
        _validate_manifest_revision(
            record,
            expected_revision=revision,
            expected_session_id=self.session_id,
        )
        _create_private_json(
            self._revision_directory / f"{revision:08d}.json",
            record,
        )
        _atomic_private_json(self.directory / MANIFEST_NAME, self.value)
        _, published_raw, published_proof = _read_exact_manifest(self.directory)
        if (
            _digest(published_raw) != manifest_digest
            or published_proof["current_digest"] != manifest_digest
            or published_proof["current_revision"] != revision
        ):
            raise SessionError("live-console manifest revision was not durably published")
        self._manifest_digest = manifest_digest
        self._manifest_revision = revision


class EphemeralSession:
    """Drop-in non-retaining sink selected explicitly by the user."""

    retained = False
    session_id = None
    directory = None

    def __init__(self) -> None:
        self._offsets: Counter[str] = Counter()
        self.event_count = 0

    def bind_process(self, pid: int, process_group_id: int | None) -> None:
        del pid, process_group_id

    def write_raw(self, stream: str, data: bytes) -> RawLocator:
        start = self._offsets[stream]
        self._offsets[stream] += len(data)
        return RawLocator(stream, "<not-retained>", start, self._offsets[stream])

    def open_raw(self, stream: str) -> RawLocator:
        return self.write_raw(stream, b"")

    def record_event(self, event: Mapping[str, Any] | Any) -> dict[str, Any]:
        self.event_count += 1
        return _event_dict(event)

    def finish(self, **values: Any) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "session_id": None,
            "state": values.get("state"),
            "retention": {"directory": None},
            "summary": {"event_count": self.event_count},
            "exit": {
                "process_exit_code": values.get("process_exit_code"),
                "effective_exit_code": values.get("effective_exit_code"),
                "outcome": values.get("outcome"),
                "cancellation": values.get("cancellation"),
            },
        }

    def abort(self, message: str) -> None:
        del message


def list_sessions(root: Path) -> list[dict[str, Any]]:
    base = _safe_session_root(root, create=False)
    if not base.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(base.iterdir(), reverse=True):
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            value, _, proof = _read_exact_manifest(path)
            if value["state"] == "running" and not _manifest_process_alive(
                value, proof.get("process_custody")
            ):
                value = dict(value)
                value["state"] = "incomplete"
                limitations = list(value.get("limitations", []))
                limitations.append(
                    "Session was recorded running but its exact process custody is "
                    "not live; successful completion was not inferred."
                )
                value["limitations"] = limitations
            result.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError, SessionError) as exc:
            result.append(_unreadable_listing(path, str(exc)))
    return result


def resolve_session(root: Path, selector: str | None) -> tuple[Path, dict[str, Any]]:
    sessions = list_sessions(root)
    if not sessions:
        raise SessionError("no retained live-console sessions exist")
    if selector in {None, "latest"}:
        selected = sessions[0]
    else:
        matches = [
            item for item in sessions if str(item.get("session_id", "")).startswith(selector)
        ]
        if not matches:
            raise SessionError(f"no live-console session matches {selector!r}")
        if len(matches) > 1:
            raise SessionError(f"live-console selector is ambiguous: {selector!r}")
        selected = matches[0]
    base = _safe_session_root(root, create=False)
    candidate = base / str(selected["session_id"])
    if candidate.is_symlink():
        raise SessionError("resolved live-console session directory is a symlink")
    directory = candidate.resolve()
    if directory.parent != base.resolve() or not directory.is_dir():
        raise SessionError("resolved live-console session directory is unsafe")
    return directory, selected


def _exact_session_directory(root: Path, session_id: str) -> Path:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise SessionError("invalid exact live-console session ID")
    base = _safe_session_root(root, create=False)
    candidate = base / session_id
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise SessionError(f"live-console session is unavailable: {session_id}") from exc
    if candidate.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise SessionError("live-console session directory is unsafe")
    if not private_path(candidate, directory=True):
        raise SessionError("live-console session directory is not owner-private")
    directory = candidate.resolve(strict=True)
    try:
        current = candidate.lstat()
        resolved = directory.stat()
    except OSError as exc:
        raise SessionError("live-console session changed during resolution") from exc
    if (
        candidate.is_symlink()
        or not stat.S_ISDIR(current.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
        or (current.st_dev, current.st_ino) != (resolved.st_dev, resolved.st_ino)
        or directory.name != session_id
        or directory.parent != base.resolve(strict=True)
    ):
        raise SessionError("live-console session escaped its storage root")
    return directory


def _read_private_regular_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise SessionError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise SessionError(f"{label} is unsafe")
    if not private_path(path, directory=False):
        raise SessionError(f"{label} is not owner-private")
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise SessionError(f"cannot open {label} safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > maximum
            or opened.st_nlink != 1
            or (
                os.name != "nt"
                and (
                    opened.st_mode & 0o077
                    or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
                )
            )
        ):
            raise SessionError(f"{label} changed before read")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) > maximum
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise SessionError(f"{label} changed during read")
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except OSError as exc:
        raise SessionError(f"{label} disappeared during read") from exc
    if (
        path.is_symlink()
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    ):
        raise SessionError(f"{label} changed during read")
    return raw


def _private_regular_file_identity(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_RETAINED_ARTIFACT_BYTES,
) -> dict[str, Any]:
    """Hash one stable private regular file without loading it into memory."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise SessionError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise SessionError(f"{label} is unsafe")
    if not private_path(path, directory=False):
        raise SessionError(f"{label} is not owner-private")
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise SessionError(f"cannot open {label} safely") from exc
    digest = sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
            or opened.st_size > maximum
            or (
                os.name != "nt"
                and (
                    opened.st_mode & 0o077
                    or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
                )
            )
        ):
            raise SessionError(f"{label} changed before hashing")
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum:
                raise SessionError(f"{label} exceeds the retained artifact bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            observed != opened.st_size
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise SessionError(f"{label} changed during hashing")
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except OSError as exc:
        raise SessionError(f"{label} disappeared during hashing") from exc
    if (
        path.is_symlink()
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    ):
        raise SessionError(f"{label} changed during hashing")
    return {
        "size": before.st_size,
        "digest": "sha256:" + digest.hexdigest(),
        "device": before.st_dev,
        "inode": before.st_ino,
    }


def _validate_live_console_artifact_index(
    value: Any,
    *,
    expected_session_id: str,
    expected_predecessor_manifest_digest: str,
    expected_command_identity: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "format_version",
        "index_id",
        "session_id",
        "predecessor_manifest_digest",
        "command_identity",
        "artifacts",
    }:
        raise SessionError("live-console artifact index fields are invalid")
    if value.get("format_version") != ARTIFACT_INDEX_FORMAT:
        raise SessionError("live-console artifact index format is unsupported")
    if value.get("session_id") != expected_session_id:
        raise SessionError("live-console artifact index session identity changed")
    if value.get("predecessor_manifest_digest") != expected_predecessor_manifest_digest:
        raise SessionError("live-console artifact index predecessor changed")
    if value.get("command_identity") != expected_command_identity:
        raise SessionError("live-console artifact index command identity changed")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_RAW_STREAMS + 1:
        raise SessionError("live-console artifact index inventory is invalid")
    names: set[str] = set()
    streams: set[str] = set()
    event_count = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "kind", "stream", "name", "size", "digest"
        }:
            raise SessionError("live-console artifact index row is invalid")
        kind = artifact.get("kind")
        stream = artifact.get("stream")
        name = artifact.get("name")
        size = artifact.get("size")
        digest = artifact.get("digest")
        if (
            not isinstance(name, str)
            or name in names
            or Path(name).name != name
            or name.startswith(".")
            or type(size) is not int
            or not 0 <= size <= MAX_RETAINED_ARTIFACT_BYTES
            or not isinstance(digest, str)
            or not _DIGEST_RE.fullmatch(digest)
        ):
            raise SessionError("live-console artifact index identity is invalid")
        names.add(name)
        if kind == "event-projection":
            event_count += 1
            if stream is not None or name != EVENTS_NAME:
                raise SessionError("live-console event projection index row is invalid")
        elif kind == "raw-stream":
            if (
                not isinstance(stream, str)
                or not _STREAM_RE.fullmatch(stream)
                or stream in streams
                or name != f"{stream}.raw"
            ):
                raise SessionError("live-console raw stream index row is invalid")
            streams.add(stream)
        else:
            raise SessionError("live-console artifact index kind is invalid")
    if event_count != 1:
        raise SessionError("live-console artifact index has no exact event projection")
    body = dict(value)
    supplied = body.pop("index_id")
    if supplied != _content_identity("live-console-artifact-index", body):
        raise SessionError("live-console artifact index identity is invalid")
    return value


def _create_live_console_artifact_index(
    directory: Path,
    *,
    session_id: str,
    predecessor_manifest_digest: str | None,
    command_identity: str,
    raw_streams: Mapping[str, Any],
    expected_event_identity: Mapping[str, Any],
    expected_raw_identities: Mapping[str, Mapping[str, Any]],
    expected_summary: Mapping[str, Any],
) -> str:
    if (
        not isinstance(predecessor_manifest_digest, str)
        or not _DIGEST_RE.fullmatch(predecessor_manifest_digest)
    ):
        raise SessionError("live-console artifacts have no predecessor manifest")
    if not isinstance(raw_streams, Mapping) or any(
        not isinstance(stream, str)
        or not _STREAM_RE.fullmatch(stream)
        or name != f"{stream}.raw"
        for stream, name in raw_streams.items()
    ):
        raise SessionError("live-console artifact bindings are invalid")
    if set(raw_streams) != set(expected_raw_identities):
        raise SessionError("live-console producer raw-stream inventory changed")
    rows: list[dict[str, Any]] = []
    event_identity = _private_regular_file_identity(
        directory / EVENTS_NAME,
        label="live-console event projection",
    )
    if event_identity != dict(expected_event_identity):
        raise SessionError(
            "live-console event projection differs from producer-observed bytes"
        )
    events, parsed_event_identity = _read_live_console_event_journal(directory)
    if any(
        parsed_event_identity[key] != event_identity[key]
        for key in ("size", "digest")
    ):
        raise SessionError("live-console event projection changed during sealing")
    derived_summary = _live_console_event_summary(events)
    if derived_summary != dict(expected_summary):
        raise SessionError(
            "live-console event summary differs from the retained event journal"
        )
    rows.append({
        "kind": "event-projection",
        "stream": None,
        "name": EVENTS_NAME,
        "size": event_identity["size"],
        "digest": event_identity["digest"],
    })
    raw_identities: dict[str, dict[str, Any]] = {}
    for stream in sorted(raw_streams):
        name = str(raw_streams[stream])
        identity = _private_regular_file_identity(
            directory / name,
            label=f"live-console raw stream {stream}",
        )
        if identity != dict(expected_raw_identities[stream]):
            raise SessionError(
                f"live-console raw stream {stream} differs from producer-observed bytes"
            )
        raw_identities[stream] = identity
        rows.append({
            "kind": "raw-stream",
            "stream": stream,
            "name": name,
            "size": identity["size"],
            "digest": identity["digest"],
        })
    for event in events:
        stream = event["stream"]
        locator = event["raw_locator"]
        if (
            raw_streams.get(stream) != locator["artifact"]
            or stream not in raw_identities
            or locator["byte_end"] > raw_identities[stream]["size"]
        ):
            raise SessionError(
                "live-console event raw range differs from retained source custody"
            )
    body: dict[str, Any] = {
        "format_version": ARTIFACT_INDEX_FORMAT,
        "session_id": session_id,
        "predecessor_manifest_digest": predecessor_manifest_digest,
        "command_identity": command_identity,
        "artifacts": rows,
    }
    value = {
        **body,
        "index_id": _content_identity("live-console-artifact-index", body),
    }
    _validate_live_console_artifact_index(
        value,
        expected_session_id=session_id,
        expected_predecessor_manifest_digest=predecessor_manifest_digest,
        expected_command_identity=command_identity,
    )
    path = directory / ARTIFACT_INDEX_NAME
    _create_private_json(path, value)
    raw = _read_private_regular_bytes(
        path,
        maximum=MAX_REVISION_BYTES,
        label="live-console artifact index",
    )
    if raw != _canonical_json_bytes(value, pretty=True):
        raise SessionError("live-console artifact index is not producer-canonical")
    return _digest(raw)


def _read_live_console_artifact_index(
    directory: Path,
    *,
    expected_digest: str,
    expected_session_id: str,
    expected_predecessor_manifest_digest: str,
    expected_command_identity: str,
) -> dict[str, Any]:
    raw = _read_private_regular_bytes(
        directory / ARTIFACT_INDEX_NAME,
        maximum=MAX_REVISION_BYTES,
        label="live-console artifact index",
    )
    if _digest(raw) != expected_digest:
        raise SessionError("live-console artifact index digest changed")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SessionError("live-console artifact index is invalid JSON") from exc
    if raw != _canonical_json_bytes(value, pretty=True):
        raise SessionError("live-console artifact index is not producer-canonical")
    return _validate_live_console_artifact_index(
        value,
        expected_session_id=expected_session_id,
        expected_predecessor_manifest_digest=expected_predecessor_manifest_digest,
        expected_command_identity=expected_command_identity,
    )


def _read_exact_manifest_unverified(
    directory: Path,
) -> tuple[dict[str, Any], bytes]:
    path = directory / MANIFEST_NAME
    raw = _read_private_regular_bytes(
        path,
        maximum=MAX_MANIFEST_BYTES,
        label="live-console session manifest",
    )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SessionError("live-console session manifest is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SessionError("live-console session manifest is not an object")
    try:
        canonical = _canonical_json_bytes(value, pretty=True)
    except (TypeError, ValueError) as exc:
        raise SessionError("live-console session manifest is not canonical JSON") from exc
    if raw != canonical:
        raise SessionError("live-console session manifest is not producer-canonical JSON")
    return validate_session_manifest(value, physical_directory=directory), raw


def _stable_manifest_command(value: Any) -> dict[str, Any]:
    command = _validate_manifest_command(value)
    return {
        "command_id": command["command_id"],
        "label": command["label"],
        "argv": list(command["argv"]),
        "cwd": command["cwd"],
        "intent": command["intent"],
        "shell": command["shell"],
    }


def _manifest_command_identity(value: Any) -> str:
    return _content_identity("live-console-command", _stable_manifest_command(value))


def _validate_process_custody(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SessionError("live-console manifest revision process custody is invalid")
    kind = value.get("kind")
    if kind == "linux-proc-v1":
        if set(value) != {
            "kind",
            "pid",
            "process_group_id",
            "boot_id",
            "process_start_time_ticks",
        }:
            raise SessionError("Linux process custody fields are invalid")
        boot_id = value.get("boot_id")
        start = value.get("process_start_time_ticks")
        if (
            not isinstance(boot_id, str)
            or not _BOOT_ID_RE.fullmatch(boot_id)
            or not isinstance(start, str)
            or not re.fullmatch(r"[1-9][0-9]*", start)
        ):
            raise SessionError("Linux process custody identity is invalid")
    elif kind == "windows-process-times-v1":
        if set(value) != {
            "kind",
            "pid",
            "process_group_id",
            "process_creation_time_100ns",
        }:
            raise SessionError("Windows process custody fields are invalid")
        creation = value.get("process_creation_time_100ns")
        if not isinstance(creation, str) or not re.fullmatch(r"[1-9][0-9]*", creation):
            raise SessionError("Windows process custody identity is invalid")
    else:
        raise SessionError("live-console process custody kind is unsupported")
    pid = value.get("pid")
    process_group_id = value.get("process_group_id")
    if type(pid) is not int or pid <= 0:
        raise SessionError("live-console process custody PID is invalid")
    if process_group_id is not None and (
        type(process_group_id) is not int or process_group_id <= 0
    ):
        raise SessionError("live-console process custody process group is invalid")
    return value


def _validate_manifest_revision(
    value: Any,
    *,
    expected_revision: int,
    expected_session_id: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "format_version",
        "revision",
        "revision_id",
        "session_id",
        "manifest_digest",
        "predecessor_manifest_digest",
        "command_identity",
        "process_custody",
        "artifact_index_digest",
        "published_at",
    }:
        raise SessionError("live-console manifest revision fields are invalid")
    if value.get("format_version") != MANIFEST_REVISION_FORMAT:
        raise SessionError("live-console manifest revision format is unsupported")
    if type(value.get("revision")) is not int or value["revision"] != expected_revision:
        raise SessionError("live-console manifest revision order is invalid")
    if value.get("session_id") != expected_session_id:
        raise SessionError("live-console manifest revision session identity changed")
    manifest_digest = value.get("manifest_digest")
    predecessor = value.get("predecessor_manifest_digest")
    if not isinstance(manifest_digest, str) or not _DIGEST_RE.fullmatch(manifest_digest):
        raise SessionError("live-console manifest revision digest is invalid")
    if predecessor is not None and (
        not isinstance(predecessor, str) or not _DIGEST_RE.fullmatch(predecessor)
    ):
        raise SessionError("live-console manifest predecessor digest is invalid")
    command_identity = value.get("command_identity")
    if not isinstance(command_identity, str) or not re.fullmatch(
        r"live-console-command:sha256:[0-9a-f]{64}", command_identity
    ):
        raise SessionError("live-console manifest command identity is invalid")
    custody = value.get("process_custody")
    if custody is not None:
        _validate_process_custody(custody)
    artifact_index_digest = value.get("artifact_index_digest")
    if artifact_index_digest is not None and (
        not isinstance(artifact_index_digest, str)
        or not _DIGEST_RE.fullmatch(artifact_index_digest)
    ):
        raise SessionError("live-console manifest artifact index digest is invalid")
    _validate_manifest_timestamp(value.get("published_at"), "revision published_at")
    body = dict(value)
    supplied_identity = body.pop("revision_id")
    expected_identity = _content_identity("live-console-manifest-revision", body)
    if supplied_identity != expected_identity:
        raise SessionError("live-console manifest revision identity is invalid")
    return value


def _read_manifest_revision_chain(
    directory: Path,
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    prior_digest: str | None,
) -> dict[str, Any]:
    chain = directory / MANIFEST_REVISIONS_DIRECTORY
    try:
        before = chain.lstat()
    except OSError as exc:
        raise SessionError("live-console manifest revision chain is missing") from exc
    if chain.is_symlink() or not stat.S_ISDIR(before.st_mode):
        raise SessionError("live-console manifest revision chain directory is unsafe")
    if not private_path(chain, directory=True):
        raise SessionError("live-console manifest revision chain is not owner-private")
    try:
        if chain.resolve(strict=True).parent != directory.resolve(strict=True):
            raise SessionError("live-console manifest revision chain escaped its session")
        entries = list(chain.iterdir())
    except OSError as exc:
        raise SessionError("cannot enumerate live-console manifest revision chain") from exc
    if not entries:
        raise SessionError("live-console manifest revision chain is missing")
    if len(entries) > MAX_MANIFEST_REVISIONS:
        raise SessionError("live-console manifest revision chain is too long")
    expected_names = [f"{index:08d}.json" for index in range(len(entries))]
    names = sorted(entry.name for entry in entries)
    if names != expected_names:
        raise SessionError("live-console manifest revision chain is missing or reordered")

    stable_command = _stable_manifest_command(manifest.get("command"))
    command_identity = _manifest_command_identity(manifest.get("command"))
    expected_predecessor: str | None = None
    retained_custody: dict[str, Any] | None = None
    custody_was_bound = False
    retained_artifact_index_digest: str | None = None
    artifact_index_predecessor: str | None = None
    digest_revisions: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    for index, name in enumerate(expected_names):
        path = chain / name
        raw = _read_private_regular_bytes(
            path,
            maximum=MAX_REVISION_BYTES,
            label=f"live-console manifest revision {index}",
        )
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SessionError(
                f"live-console manifest revision {index} is invalid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise SessionError(f"live-console manifest revision {index} is not an object")
        try:
            canonical = _canonical_json_bytes(record, pretty=True)
        except (TypeError, ValueError) as exc:
            raise SessionError(
                f"live-console manifest revision {index} is not canonical JSON"
            ) from exc
        if raw != canonical:
            raise SessionError(
                f"live-console manifest revision {index} is not producer-canonical JSON"
            )
        record = _validate_manifest_revision(
            record,
            expected_revision=index,
            expected_session_id=str(manifest["session_id"]),
        )
        if record["predecessor_manifest_digest"] != expected_predecessor:
            raise SessionError("live-console manifest revision predecessor chain is invalid")
        if record["command_identity"] != command_identity:
            raise SessionError("live-console manifest command identity changed in its chain")
        digest = record["manifest_digest"]
        if digest in digest_revisions:
            raise SessionError("live-console manifest revision digest was repeated")
        digest_revisions[digest] = index
        expected_predecessor = digest

        custody = record["process_custody"]
        if index == 0 and custody is not None:
            raise SessionError("initial live-console manifest revision is not allocated")
        if custody is None:
            if custody_was_bound:
                raise SessionError("live-console process custody was removed from its chain")
        else:
            exact_custody = dict(_validate_process_custody(custody))
            if not custody_was_bound:
                retained_custody = exact_custody
                custody_was_bound = True
            elif exact_custody != retained_custody:
                raise SessionError("live-console process custody changed in its chain")

        artifact_index_digest = record["artifact_index_digest"]
        if artifact_index_digest is not None:
            if retained_artifact_index_digest is None:
                retained_artifact_index_digest = artifact_index_digest
                artifact_index_predecessor = record["predecessor_manifest_digest"]
            elif artifact_index_digest != retained_artifact_index_digest:
                raise SessionError("live-console artifact index changed in its chain")
        elif retained_artifact_index_digest is not None:
            raise SessionError("live-console artifact index was removed from its chain")

        records.append(record)

    try:
        after = chain.lstat()
    except OSError as exc:
        raise SessionError("live-console manifest revision chain disappeared") from exc
    if (
        chain.is_symlink()
        or (before.st_dev, before.st_ino, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_mtime_ns)
    ):
        raise SessionError("live-console manifest revision chain changed during read")

    current_digest = _digest(manifest_raw)
    if records[-1]["manifest_digest"] != current_digest:
        raise SessionError(
            "live-console manifest digest has no current immutable revision record"
        )
    if retained_artifact_index_digest is not None:
        if artifact_index_predecessor is None:
            raise SessionError("live-console artifact index has no predecessor")
        _read_live_console_artifact_index(
            directory,
            expected_digest=retained_artifact_index_digest,
            expected_session_id=str(manifest["session_id"]),
            expected_predecessor_manifest_digest=artifact_index_predecessor,
            expected_command_identity=command_identity,
        )
    if (
        manifest.get("state") in {"complete", "failed", "cancelled"}
        and retained_artifact_index_digest is None
    ):
        raise SessionError("terminal live-console manifest has no sealed artifact index")
    command = manifest["command"]
    if retained_custody is None:
        if command.get("pid") is not None or command.get("process_group_id") is not None:
            raise SessionError("live-console manifest process has no custody chain record")
    elif (
        command.get("pid") != retained_custody["pid"]
        or command.get("process_group_id") != retained_custody["process_group_id"]
    ):
        raise SessionError("live-console manifest process identity differs from custody")

    prior_revision: int | None = None
    if prior_digest is not None:
        if not isinstance(prior_digest, str) or not _DIGEST_RE.fullmatch(prior_digest):
            raise SessionError("supplied prior live-console manifest digest is invalid")
        prior_revision = digest_revisions.get(prior_digest)
        if prior_revision is None:
            raise SessionError(
                "supplied prior live-console manifest digest is outside the revision chain"
            )
    return {
        "session_id": manifest["session_id"],
        "prior_digest": prior_digest,
        "prior_revision": prior_revision,
        "current_digest": current_digest,
        "current_revision": records[-1]["revision"],
        "revision_count": len(records),
        "command_identity": command_identity,
        "command": stable_command,
        "process_custody": (
            None if retained_custody is None else dict(retained_custody)
        ),
        "artifact_index_digest": retained_artifact_index_digest,
        "artifact_index_predecessor_manifest_digest": artifact_index_predecessor,
    }


def _read_exact_manifest(
    directory: Path,
    *,
    prior_digest: str | None = None,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    manifest, raw = _read_exact_manifest_unverified(directory)
    proof = _read_manifest_revision_chain(
        directory,
        manifest=manifest,
        manifest_raw=raw,
        prior_digest=prior_digest,
    )
    return manifest, raw, proof


def verify_live_console_manifest_chain(
    root: Path,
    session_id: str,
    *,
    prior_digest: str | None = None,
) -> dict[str, Any]:
    """Verify immutable V1-manifest lineage and return its exact custody proof.

    When ``prior_digest`` is supplied, verification additionally proves that
    exact historical manifest digest occurs in the contiguous chain ending at
    the current producer-canonical manifest.
    """

    directory = _exact_session_directory(root, session_id)
    _, _, proof = _read_exact_manifest(directory, prior_digest=prior_digest)
    return proof


def _owner_reference_from_manifest(
    directory: Path,
    manifest: Mapping[str, Any],
    raw: bytes,
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    state = manifest["state"]
    if state == "running":
        command = manifest["command"]
        if command.get("pid") is None and command.get("process_group_id") is None:
            state = "allocated"
        elif not _manifest_process_alive(manifest, proof.get("process_custody")):
            state = "incomplete"
    return {
        "owner_id": "workbench-shell",
        "record_id": manifest["session_id"],
        "record_kind": FORMAT_VERSION,
        "uri": (directory / MANIFEST_NAME).as_uri(),
        "digest": _digest(raw),
        "last_verified_state": state,
        "verified_at": utc_now(),
    }


def live_console_owner_reference(root: Path, session_id: str) -> dict[str, Any]:
    """Return the current exact V1 live-console owner record reference."""

    directory = _exact_session_directory(root, session_id)
    manifest, raw, proof = _read_exact_manifest(directory)
    return _owner_reference_from_manifest(directory, manifest, raw, proof)


def resolve_live_console_owner_reference(
    root: Path,
    owner_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-open a caller-supplied reference through live-console custody."""

    if not isinstance(owner_ref, Mapping):
        raise SessionError("live-console owner reference is not an object")
    record_id = owner_ref.get("record_id")
    if not isinstance(record_id, str):
        raise SessionError("live-console owner reference has no exact record ID")
    if owner_ref.get("owner_id") != "workbench-shell":
        raise SessionError("live-console owner reference owner identity changed")
    if owner_ref.get("record_kind") != FORMAT_VERSION:
        raise SessionError("live-console owner reference record kind changed")
    digest = owner_ref.get("digest")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise SessionError("live-console owner reference has no exact manifest digest")
    directory = _exact_session_directory(root, record_id)
    expected_uri = (directory / MANIFEST_NAME).as_uri()
    if owner_ref.get("uri") != expected_uri:
        raise SessionError("live-console owner reference URI changed")
    manifest, raw, proof = _read_exact_manifest(directory, prior_digest=digest)
    return _owner_reference_from_manifest(directory, manifest, raw, proof)


def live_console_execution_reference(
    root: Path,
    owner_ref: Mapping[str, Any],
) -> dict[str, Any]:
    """Reproduce exact prelaunch command custody plus its current owner ref."""

    reproduced = resolve_live_console_owner_reference(root, owner_ref)
    directory = _exact_session_directory(root, reproduced["record_id"])
    manifest, raw, proof = _read_exact_manifest(
        directory,
        prior_digest=reproduced["digest"],
    )
    reproduced = _owner_reference_from_manifest(directory, manifest, raw, proof)
    command = manifest["command"]
    return {
        "owner_record_ref": reproduced,
        "command": {
            "command_id": command["command_id"],
            "argv": list(command["argv"]),
            "cwd": command["cwd"],
            "intent": command["intent"],
            "shell": command["shell"],
        },
    }


def _read_live_console_event_journal(
    directory: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read one stable, bounded producer-canonical V1 event projection."""

    raw = _read_private_regular_bytes(
        directory / EVENTS_NAME,
        maximum=MAX_EVENT_JOURNAL_BYTES,
        label="live-console event journal",
    )
    if raw and not raw.endswith(b"\n"):
        raise SessionError("live-console event journal has an incomplete final record")
    events: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    previous_sequence = 0
    expected_fields = {
        "format_version", "event_id", "sequence", "ingested_at",
        "monotonic_ns", "source_timestamp", "source", "stream",
        "raw_locator", "kind", "severity", "subsystem", "logger", "thread",
        "message", "parse_provenance", "classification_basis", "cluster_key",
        "signal", "outcome_failure", "source_locators", "limitations",
    }

    def single_line(candidate: Any, *, maximum: int = 8192, nullable: bool = False) -> bool:
        return (
            nullable and candidate is None
        ) or (
            isinstance(candidate, str)
            and bool(candidate)
            and len(candidate) <= maximum
            and not any(marker in candidate for marker in ("\r", "\n", "\x00"))
        )

    for line_number, encoded in enumerate(raw.splitlines(keepends=True), 1):
        if len(encoded) > MAX_EVENT_RECORD_JSON_BYTES:
            raise SessionError("live-console event record exceeds the navigation bound")
        try:
            value = json.loads(encoded.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SessionError(
                f"invalid live-console event JSON at record {line_number}"
            ) from exc
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise SessionError("live-console event record is not an object")
        try:
            canonical = (
                json.dumps(
                    value,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SessionError("live-console event record is not canonical JSON") from exc
        if encoded != canonical:
            raise SessionError("live-console event journal is not producer-canonical")
        event_id = value.get("event_id")
        sequence = value.get("sequence")
        if (
            value.get("format_version") != "workbench-live-console-event-v1"
            or not isinstance(event_id, str)
            or not _COMMAND_ID_RE.fullmatch(event_id)
            or event_id in identifiers
            or type(sequence) is not int
            or sequence != previous_sequence + 1
            or type(value.get("monotonic_ns")) is not int
            or value["monotonic_ns"] < 0
            or not isinstance(value.get("source"), str)
            or not _COMMAND_ID_RE.fullmatch(value["source"])
            or value.get("kind") not in {
                "text", "log", "build", "compiler_diagnostic", "mixin",
                "groovy", "registry", "worldgen", "exception",
                "stack_frame", "stage",
            }
            or value.get("severity") not in {
                "trace", "debug", "info", "warning", "error", "fatal", "unknown"
            }
            or value.get("subsystem") not in {
                "generic", "gradle", "compiler", "minecraft", "cleanroom-fml",
                "mixin", "cleanmix", "groovy", "registry", "worldgen", "java",
                "workbench",
            }
            or value.get("parse_provenance") not in {"raw", "parsed", "heuristic"}
            or type(value.get("signal")) is not bool
            or type(value.get("outcome_failure")) is not bool
        ):
            raise SessionError("live-console event identity or order is invalid")
        try:
            _validate_manifest_timestamp(value.get("ingested_at"), "event ingested_at")
        except SessionError as exc:
            raise SessionError("live-console event timestamp is invalid") from exc
        if (
            not single_line(value.get("source_timestamp"), nullable=True)
            or not single_line(value.get("logger"), nullable=True)
            or not single_line(value.get("thread"), nullable=True)
            or not isinstance(value.get("message"), str)
            or len(value["message"]) > MAX_EVENT_MESSAGE_CHARACTERS
            or any(marker in value["message"] for marker in ("\r", "\n", "\x00"))
            or not isinstance(value.get("cluster_key"), str)
            or not _DIGEST_RE.fullmatch(value["cluster_key"])
        ):
            raise SessionError("live-console event text metadata is invalid")
        basis = value.get("classification_basis")
        limitations = value.get("limitations")
        source_locators = value.get("source_locators")
        if (
            not isinstance(basis, list)
            or len(basis) > 32
            or len(set(basis)) != len(basis)
            or any(
                not isinstance(item, str)
                or len(item) > 256
                or not _COMMAND_ID_RE.fullmatch(item)
                for item in basis
            )
            or not isinstance(limitations, list)
            or len(limitations) > 256
            or len(set(limitations)) != len(limitations)
            or any(not single_line(item) for item in limitations)
            or not isinstance(source_locators, list)
            or len(source_locators) > 32
            or len({
                json.dumps(item, sort_keys=True, separators=(",", ":"))
                for item in source_locators
                if isinstance(item, dict)
            }) != len(source_locators)
        ):
            raise SessionError("live-console event metadata collections are invalid")
        for source_locator in source_locators:
            if (
                not isinstance(source_locator, dict)
                or set(source_locator) != {"path", "line", "column", "label"}
                or not single_line(source_locator.get("path"))
                or not single_line(source_locator.get("label"))
                or type(source_locator.get("line")) is not int
                or source_locator["line"] < 1
                or (
                    source_locator.get("column") is not None
                    and (
                        type(source_locator["column"]) is not int
                        or source_locator["column"] < 1
                    )
                )
            ):
                raise SessionError("live-console event source locator is invalid")
        stream = value.get("stream")
        locator = value.get("raw_locator")
        if (
            not isinstance(stream, str)
            or not _STREAM_RE.fullmatch(stream)
            or not isinstance(locator, dict)
            or set(locator)
            != {"artifact", "byte_start", "byte_end", "line", "chunk", "boundary"}
        ):
            raise SessionError("live-console event raw locator is invalid")
        artifact = locator.get("artifact")
        byte_start = locator.get("byte_start")
        byte_end = locator.get("byte_end")
        if (
            artifact != f"{stream}.raw"
            or type(byte_start) is not int
            or type(byte_end) is not int
            or byte_start < 0
            or byte_end < byte_start
            or byte_end - byte_start > MAX_ARTIFACT_RANGE_BYTES
            or type(locator.get("line")) is not int
            or locator["line"] < 1
            or type(locator.get("chunk")) is not int
            or locator["chunk"] < 1
            or locator.get("boundary") not in {"lf", "crlf", "cr", "limit", "eof"}
        ):
            raise SessionError("live-console event raw range is invalid")
        identifiers.add(event_id)
        previous_sequence = sequence
        events.append(value)
    return events, {
        "size": len(raw),
        "digest": "sha256:" + sha256(raw).hexdigest(),
    }


def _live_console_event_summary(
    events: list[Mapping[str, Any]],
) -> dict[str, Any]:
    severities: Counter[str] = Counter()
    subsystems: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    outcome_failures = 0
    source_locators = 0
    for event in events:
        severities[str(event["severity"])] += 1
        subsystems[str(event["subsystem"])] += 1
        kinds[str(event["kind"])] += 1
        outcome_failures += int(bool(event["outcome_failure"]))
        source_locators += len(event["source_locators"])
    return {
        "event_count": len(events),
        "severity_counts": dict(sorted(severities.items())),
        "subsystem_counts": dict(sorted(subsystems.items())),
        "kind_counts": dict(sorted(kinds.items())),
        "outcome_failure_events": outcome_failures,
        "source_locator_count": source_locators,
    }


def _read_live_console_raw_range(
    path: Path,
    *,
    byte_start: int,
    byte_end: int,
    expected_size: int,
    expected_digest: str,
) -> bytes:
    """Hash a sealed private artifact while extracting one exact range."""

    if (
        type(byte_start) is not int
        or type(byte_end) is not int
        or byte_start < 0
        or byte_end < byte_start
        or byte_end - byte_start > MAX_ARTIFACT_RANGE_BYTES
    ):
        raise SessionError("live-console artifact range is outside the navigation bound")
    try:
        before = path.lstat()
    except OSError as exc:
        raise SessionError("live-console raw artifact is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise SessionError("live-console raw artifact is unsafe")
    if not private_path(path, directory=False):
        raise SessionError("live-console raw artifact is not owner-private")
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise SessionError("cannot open live-console raw artifact safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_nlink != 1
            or opened.st_size != expected_size
            or opened.st_size < byte_end
            or (
                os.name != "nt"
                and (
                    opened.st_mode & 0o077
                    or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
                )
            )
        ):
            raise SessionError("live-console raw artifact changed before range read")
        digest = sha256()
        selected: list[bytes] = []
        offset = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            chunk_end = offset + len(chunk)
            overlap_start = max(byte_start, offset)
            overlap_end = min(byte_end, chunk_end)
            if overlap_start < overlap_end:
                selected.append(
                    chunk[overlap_start - offset : overlap_end - offset]
                )
            offset += len(chunk)
        after = os.fstat(descriptor)
        if (
            offset != expected_size
            or "sha256:" + digest.hexdigest() != expected_digest
            or (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
        ):
            raise SessionError("live-console raw artifact differs from its sealed index")
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except OSError as exc:
        raise SessionError("live-console raw artifact disappeared during range read") from exc
    if (
        path.is_symlink()
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    ):
        raise SessionError("live-console raw artifact changed during range read")
    payload = b"".join(selected)
    if len(payload) != byte_end - byte_start:
        raise SessionError("live-console raw artifact ended before its event range")
    return payload


def read_live_console_owner_artifacts(
    root: Path,
    owner_ref: Mapping[str, Any],
    *,
    event_id: str | None = None,
    after_sequence: int = -1,
    limit: int = 200,
) -> dict[str, Any]:
    """List or read exact owner-retained raw ranges through V1 custody.

    Historical manifest digests are accepted only when they occur in the
    immutable chain ending at the current producer-canonical manifest.  Raw
    bytes remain authoritative; the event journal is only their navigation
    projection.
    """

    if not isinstance(owner_ref, Mapping):
        raise SessionError("live-console artifact owner reference is not an object")
    record_id = owner_ref.get("record_id")
    digest = owner_ref.get("digest")
    if not isinstance(record_id, str) or not isinstance(digest, str):
        raise SessionError("live-console artifact owner identity is incomplete")
    reproduced = resolve_live_console_owner_reference(root, owner_ref)
    directory = _exact_session_directory(root, record_id)
    manifest, _, proof = _read_exact_manifest(directory, prior_digest=digest)
    if reproduced["digest"] != proof["current_digest"]:
        raise SessionError("live-console artifact owner chain changed during read")
    artifact_index_digest = proof.get("artifact_index_digest")
    artifact_index_predecessor = proof.get(
        "artifact_index_predecessor_manifest_digest"
    )
    if (
        not isinstance(artifact_index_digest, str)
        or not isinstance(artifact_index_predecessor, str)
    ):
        raise SessionError(
            "live-console owner artifacts are not sealed by a terminal revision"
        )
    artifact_index = _read_live_console_artifact_index(
        directory,
        expected_digest=artifact_index_digest,
        expected_session_id=record_id,
        expected_predecessor_manifest_digest=artifact_index_predecessor,
        expected_command_identity=proof["command_identity"],
    )
    indexed = {row["name"]: row for row in artifact_index["artifacts"]}
    events, event_identity = _read_live_console_event_journal(directory)
    event_index = indexed.get(EVENTS_NAME)
    if event_index is None or any(
        event_index[key] != event_identity[key] for key in ("size", "digest")
    ):
        raise SessionError("live-console event projection differs from its sealed index")
    streams = manifest["retention"]["raw_streams"]
    projected: list[dict[str, Any]] = []
    for value in events:
        locator = value["raw_locator"]
        stream = value["stream"]
        if streams.get(stream) != locator["artifact"]:
            raise SessionError("live-console event cites an unbound raw artifact")
        projected.append(
            {
                "event_id": value["event_id"],
                "sequence": value["sequence"],
                "kind": value["kind"],
                "severity": value["severity"],
                "subsystem": value["subsystem"],
                "message": value["message"],
                "stream": stream,
                "artifact": locator["artifact"],
                "byte_start": locator["byte_start"],
                "byte_end": locator["byte_end"],
                "boundary": locator["boundary"],
            }
        )
    common = {
        "owner_record_id": record_id,
        "owner_digest": digest,
        "current_owner_digest": proof["current_digest"],
    }
    if event_id is None:
        if type(after_sequence) is not int or after_sequence < -1:
            raise SessionError("live-console artifact after-sequence is invalid")
        if type(limit) is not int or not 1 <= limit <= MAX_ARTIFACT_EVENT_PAGE:
            raise SessionError("live-console artifact page limit is invalid")
        remaining = [row for row in projected if row["sequence"] > after_sequence]
        page = remaining[:limit]
        return {
            "format_version": "workbench-owner-artifact-events-v1",
            **common,
            "after_sequence": after_sequence,
            "limit": limit,
            "events": page,
            "has_more": len(remaining) > len(page),
            "next_after_sequence": (
                after_sequence if not page else page[-1]["sequence"]
            ),
        }
    if not isinstance(event_id, str) or not _COMMAND_ID_RE.fullmatch(event_id):
        raise SessionError("live-console artifact event ID is invalid")
    selected = [row for row in projected if row["event_id"] == event_id]
    if len(selected) != 1:
        raise SessionError("live-console artifact event ID was not found exactly once")
    event = selected[0]
    raw_index = indexed.get(event["artifact"])
    if (
        not isinstance(raw_index, Mapping)
        or raw_index.get("kind") != "raw-stream"
        or raw_index.get("stream") != event["stream"]
    ):
        raise SessionError("live-console raw artifact has no sealed index row")
    payload = _read_live_console_raw_range(
        directory / event["artifact"],
        byte_start=event["byte_start"],
        byte_end=event["byte_end"],
        expected_size=raw_index["size"],
        expected_digest=raw_index["digest"],
    )
    try:
        utf8: str | None = payload.decode("utf-8")
    except UnicodeDecodeError:
        utf8 = None
    return {
        "format_version": "workbench-owner-artifact-range-v1",
        **common,
        "event_id": event["event_id"],
        "sequence": event["sequence"],
        "stream": event["stream"],
        "artifact": event["artifact"],
        "byte_start": event["byte_start"],
        "byte_end": event["byte_end"],
        "byte_count": len(payload),
        "content_sha256": "sha256:" + sha256(payload).hexdigest(),
        "encoding": "base64",
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "utf8": utf8,
    }


def iter_events(directory: Path):
    path = directory / EVENTS_NAME
    try:
        if path.is_symlink():
            raise SessionError("retained console event journal is a symlink")
        with path.open("r", encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SessionError(
                        f"invalid event JSON at {path}:{number}: {exc.msg}"
                    ) from exc
                if not isinstance(value, dict):
                    raise SessionError(f"event at {path}:{number} is not an object")
                yield value
    except OSError as exc:
        raise SessionError(f"cannot read retained console events: {exc}") from exc


def _event_dict(event: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    if hasattr(event, "as_dict"):
        value = event.as_dict()
    elif hasattr(event, "to_dict"):
        value = event.to_dict()
    else:
        raise SessionError(f"cannot serialize event type {type(event).__name__}")
    if not isinstance(value, dict):
        raise SessionError("event serializer did not return an object")
    return value


















def _unreadable_listing(path: Path, detail: str) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "session_id": path.name,
        "state": "incomplete",
        "started_at": None,
        "updated_at": None,
        "command": {"command_id": "unreadable"},
        "retention": {"directory": str(path)},
        "summary": {"event_count": None},
        "limitations": [
            "Session manifest is unreadable or unsafe: "
            + _single_line(detail, maximum=2048)
        ],
    }


def _linux_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip().lower()
    except (OSError, UnicodeError) as exc:
        raise SessionError("Linux process custody boot identity is unverifiable") from exc
    if not _BOOT_ID_RE.fullmatch(value):
        raise SessionError("Linux process custody boot identity is invalid")
    return value


def _linux_process_stat(pid: int) -> tuple[str, int, str]:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SessionError("Linux process custody is unverifiable") from exc
    if len(raw) > 64 * 1024 or not raw.startswith(f"{pid} ("):
        raise SessionError("Linux process custody stat record is invalid")
    command_end = raw.rfind(")")
    if command_end < len(str(pid)) + 2:
        raise SessionError("Linux process custody stat record is invalid")
    fields = raw[command_end + 1 :].strip().split()
    # After the command field, indexes 0, 2, and 19 are proc stat fields
    # state (3), pgrp (5), and starttime (22), respectively.
    if len(fields) < 20 or fields[0] in {"Z", "X", "x"}:
        raise SessionError("Linux process is not live for custody verification")
    try:
        process_group_id = int(fields[2])
        start_time = int(fields[19])
    except ValueError as exc:
        raise SessionError("Linux process custody stat identity is invalid") from exc
    if process_group_id <= 0 or start_time <= 0:
        raise SessionError("Linux process custody stat identity is invalid")
    return fields[0], process_group_id, str(start_time)


def _capture_linux_process_custody(
    pid: int,
    process_group_id: int | None,
) -> dict[str, Any]:
    boot_before = _linux_boot_id()
    _, observed_group, start_time = _linux_process_stat(pid)
    boot_after = _linux_boot_id()
    if boot_before != boot_after:
        raise SessionError("Linux boot changed during process custody capture")
    if process_group_id is not None and process_group_id != observed_group:
        raise SessionError("Linux process group differs from observed custody")
    return {
        "kind": "linux-proc-v1",
        "pid": pid,
        "process_group_id": process_group_id,
        "boot_id": boot_before,
        "process_start_time_ticks": start_time,
    }


def _capture_windows_process_custody(
    pid: int,
    process_group_id: int | None,
) -> dict[str, Any]:
    if process_group_id is not None:
        raise SessionError("Windows process-group custody is unverifiable")
    try:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", wintypes.DWORD),
                ("high", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        get_process_times.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        creation, exit_time, kernel, user = (
            FileTime(),
            FileTime(),
            FileTime(),
            FileTime(),
        )
        try:
            if not get_process_times(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
        finally:
            close_handle(handle)
    except (ImportError, AttributeError, OSError) as exc:
        raise SessionError("Windows process custody is unverifiable") from exc
    creation_time = (int(creation.high) << 32) | int(creation.low)
    exit_identity = (int(exit_time.high) << 32) | int(exit_time.low)
    if exit_identity != 0:
        raise SessionError("Windows process is not live for custody verification")
    if creation_time <= 0:
        raise SessionError("Windows process creation identity is invalid")
    return {
        "kind": "windows-process-times-v1",
        "pid": pid,
        "process_group_id": None,
        "process_creation_time_100ns": str(creation_time),
    }


def _capture_process_custody(
    pid: int,
    process_group_id: int | None,
) -> dict[str, Any]:
    if type(pid) is not int or pid <= 0:
        raise SessionError("live-console process custody PID is invalid")
    if process_group_id is not None and (
        type(process_group_id) is not int or process_group_id <= 0
    ):
        raise SessionError("live-console process custody process group is invalid")
    if sys.platform.startswith("linux"):
        return _capture_linux_process_custody(pid, process_group_id)
    if os.name == "nt":
        return _capture_windows_process_custody(pid, process_group_id)
    raise SessionError("process custody is unverifiable on this host platform")


def _manifest_process_alive(
    value: Mapping[str, Any],
    process_custody: Any,
) -> bool:
    command = value.get("command")
    if not isinstance(command, Mapping) or not isinstance(process_custody, dict):
        return False
    try:
        expected = dict(_validate_process_custody(process_custody))
    except SessionError:
        return False
    try:
        if (
            command.get("pid") != expected["pid"]
            or command.get("process_group_id") != expected["process_group_id"]
        ):
            return False
        observed = _capture_process_custody(
            expected["pid"], expected["process_group_id"]
        )
    except SessionError:
        return False
    return observed == expected


__all__ = [
    "EVENTS_NAME",
    "EphemeralSession",
    "FORMAT_VERSION",
    "MANIFEST_NAME",
    "MANIFEST_REVISIONS_DIRECTORY",
    "MANIFEST_REVISION_FORMAT",
    "MAX_MANIFEST_BYTES",
    "MAX_ARTIFACT_EVENT_PAGE",
    "MAX_ARTIFACT_RANGE_BYTES",
    "MAX_RAW_STREAMS",
    "RawLocator",
    "RetainedSession",
    "SessionError",
    "iter_events",
    "live_console_execution_reference",
    "live_console_owner_reference",
    "list_sessions",
    "resolve_session",
    "resolve_live_console_owner_reference",
    "read_live_console_owner_artifacts",
    "utc_now",
    "validate_session_manifest",
    "verify_live_console_manifest_chain",
]
