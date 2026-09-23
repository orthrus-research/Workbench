"""Durable, cross-frontend Work Session V2 custody.

This module owns navigation and orchestration history only.  It retains exact
identities and references supplied by semantic owners; it never copies owner
records or infers their success, support, evidence, or approval state.

The V2 format is deliberately separate from ``sessions.py``.  Live-console V1
continues to mean one retained console invocation.  A Work Session can instead
span frontends and owner jobs.  Its source is an immutable header plus an
append-only directory of immutable, hash-chained events.  ``summary-v1.json``
is an atomic cache and can always be regenerated from those source records.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
from typing import Any, NoReturn

from workbench_api.service import ServicePhysicalLeasePorts, ServiceV3Error
from workbench_api.host_filesystem import (
    HostFilesystemError,
    fsync_directory,
    private_path,
    secure_private_path,
)

from workbench_core.service.host import local_service_physical_lease_ports


SESSION_FORMAT = "workbench-work-session-v2"
EVENT_FORMAT = "workbench-work-session-event-v1"
SUMMARY_FORMAT = "workbench-work-session-summary-v1"
VIEW_FORMAT = "workbench-work-session-view-v1"
RECOVERY_PREVIEW_FORMAT = "workbench-work-session-recovery-preview-v1"
PREPARED_ACTION_FORMAT = "workbench-work-session-prepared-action-v1"

SESSION_RECORD_NAME = "session-v2.json"
JOURNAL_DIRECTORY_NAME = "journal-v1"
SUMMARY_NAME = "summary-v1.json"
LOCK_DIRECTORY_NAME = "locks-v1"
STORAGE_DIRECTORY = Path(".workbench/sessions/work-session-v2")

MAX_RECORD_BYTES = 1024 * 1024
MAX_EVENTS = 100_000
MAX_REFERENCES = 256
MAX_PROBLEMS = 256
MAX_NEXT_ACTIONS = 128
MAX_ARGUMENT_BYTES = 64 * 1024

_SESSION_ID = re.compile(r"work-session-v2-[0-9a-f]{32}\Z")
_PLAIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}\Z")
_EVENT_ID = re.compile(r"work-session-event:sha256:[0-9a-f]{64}\Z")
_RECORD_ID = re.compile(r"work-session-record:sha256:[0-9a-f]{64}\Z")
_SUMMARY_ID = re.compile(r"work-session-summary:sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EVENT_FILE = re.compile(r"([0-9]{20})\.json\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z\Z"
)

LIFECYCLES = frozenset(
    {
        "discovered",
        "ready",
        "attention",
        "blocked",
        "running",
        "complete",
        "failed",
        "cancelled",
        "incomplete",
        "recoverable",
    }
)
TERMINAL_LIFECYCLES = frozenset({"complete", "failed", "cancelled"})
TERMINAL_STAGE_STATES = frozenset({"complete", "failed", "cancelled"})
EXECUTION_CUSTODY_STATES = frozenset({"allocated", "starting", "running"})
FRONTEND_CREATE_LIFECYCLES = frozenset(
    {"discovered", "ready", "attention", "blocked", "incomplete"}
)
FRONTEND_KINDS = frozenset({"cli", "vscode", "intellij-community", "service", "test"})
AVAILABILITY = frozenset(
    {"available", "unavailable", "experimental", "blocked", "stale", "unknown"}
)
MUTATION_BUDGETS = frozenset(
    {"read-only", "writes-output", "mutating", "destructive"}
)
PROBLEM_SEVERITIES = frozenset({"info", "warning", "error", "fatal"})
RECOVERY_STATES = frozenset({"required", "recovering", "resolved"})
OWNER_REVISION_TRANSITIONS = {
    "allocated": frozenset(
        {"allocated", "starting", "running", "complete", "failed", "cancelled", "incomplete"}
    ),
    "starting": frozenset(
        {"starting", "running", "complete", "failed", "cancelled", "incomplete"}
    ),
    "running": frozenset(
        {"running", "complete", "failed", "cancelled", "incomplete"}
    ),
    "incomplete": frozenset({"incomplete", "running", "complete", "failed", "cancelled"}),
    "complete": frozenset({"complete"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}

_SECRET_ARGUMENT_KEYS = re.compile(
    r"(?:^|[-_.])(?:auth|credential|password|secret|token|api[-_]?key)(?:$|[-_.])",
    re.IGNORECASE,
)
_SECRET_KEY_MATERIAL = (
    "apikey",
    "accesstoken",
    "authorization",
    "clientsecret",
    "credential",
    "launcherprofile",
    "password",
    "privatekey",
    "secret",
    "sessioncookie",
    "token",
)


class WorkSessionError(RuntimeError):
    """Stable failure raised for unsafe, stale, or corrupt Work Session state."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class WorkSessionConflictError(WorkSessionError):
    """A frontend attempted to append from a stale journal sequence."""


def _fail(code: str, message: str, *, retryable: bool = False) -> NoReturn:
    raise WorkSessionError(code, message, retryable=retryable)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("work-session.invalid-json", f"record is not bounded JSON: {exc}")


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def work_session_recovery_action(session_id: str) -> dict[str, Any]:
    """Return the exact owner-native route for developer-selected recovery."""

    if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
        _fail("work-session.invalid-record", "recovery action session ID is invalid")
    body = {
        "action_id": "workbench.session.recover",
        "owner_id": "workbench-shell",
        "availability": "available",
        "mutation_budget": "read-only",
        "arguments": {"session_id": session_id},
    }
    return _normalize_action(
        {
            **body,
            "action_digest": "sha256:"
            + hashlib.sha256(_canonical_bytes(body)).hexdigest(),
        }
    )


def _snapshot(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _text(
    value: Any,
    label: str,
    *,
    maximum: int = 8192,
    nullable: bool = False,
) -> str | None:
    if nullable and value is None:
        return None
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        _fail("work-session.invalid-record", f"{label} must be bounded single-line text")
    return value


def _identifier(value: Any, label: str, *, nullable: bool = False) -> str | None:
    text = _text(value, label, maximum=512, nullable=nullable)
    if text is not None and _PLAIN_ID.fullmatch(text) is None:
        _fail("work-session.invalid-record", f"{label} is not a valid identity")
    return text


def _timestamp(value: Any, label: str) -> str:
    text = _text(value, label, maximum=64)
    assert text is not None
    if _TIMESTAMP.fullmatch(text) is None:
        _fail("work-session.invalid-record", f"{label} is not an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkSessionError(
            "work-session.invalid-record", f"{label} is not a real timestamp"
        ) from exc
    return text


def _exact_mapping(value: Any, label: str, allowed: set[str]) -> Mapping[str, Any]:
    if type(value) is not dict:
        _fail("work-session.invalid-record", f"{label} must be an ordinary object")
    unknown = set(value) - allowed
    if unknown:
        _fail(
            "work-session.invalid-record",
            f"{label} contains unsupported fields: {', '.join(sorted(unknown))}",
        )
    return value


def _bounded_strings(
    value: Any,
    label: str,
    *,
    maximum_items: int = 128,
    maximum_bytes: int = 4096,
) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum_items:
        _fail("work-session.invalid-record", f"{label} must be a bounded list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _text(item, f"{label}[{index}]", maximum=maximum_bytes)
        assert text is not None
        result.append(text)
    return result


def _normalize_task(value: Any) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "task",
        {"task_id", "owner_id", "label", "owner_record_ref"},
    )
    return {
        "task_id": _identifier(row.get("task_id"), "task.task_id"),
        "owner_id": _identifier(row.get("owner_id"), "task.owner_id"),
        "label": _text(row.get("label"), "task.label", maximum=1024, nullable=True),
        "owner_record_ref": (
            None
            if row.get("owner_record_ref") is None
            else _normalize_owner_ref(row["owner_record_ref"])
        ),
    }


def _normalize_workspace(value: Any, *, require_present: bool) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "workspace",
        {
            "identity_id",
            "canonical_root",
            "root_uri",
            "source_revision",
            "dirty_fingerprint",
        },
    )
    identity_id = _identifier(row.get("identity_id"), "workspace.identity_id")
    root_text = _text(row.get("canonical_root"), "workspace.canonical_root")
    assert root_text is not None
    root = Path(root_text).expanduser()
    if not root.is_absolute():
        _fail("work-session.invalid-record", "workspace.canonical_root must be absolute")
    if root.is_symlink():
        _fail("work-session.unsafe-path", "workspace root cannot be a symbolic link")
    if require_present and (not root.is_dir() or root.is_symlink()):
        _fail("work-session.workspace-unavailable", "workspace root is not a real directory")
    canonical = root.resolve(strict=require_present)
    observed_uri = canonical.as_uri()
    supplied_uri = row.get("root_uri")
    if supplied_uri is not None and supplied_uri != observed_uri:
        _fail("work-session.invalid-record", "workspace root URI does not match its path")
    return {
        "identity_id": identity_id,
        "canonical_root": str(canonical),
        "root_uri": observed_uri,
        "source_revision": _identifier(
            row.get("source_revision"), "workspace.source_revision", nullable=True
        ),
        "dirty_fingerprint": _identifier(
            row.get("dirty_fingerprint"),
            "workspace.dirty_fingerprint",
            nullable=True,
        ),
    }


def _normalize_identities(value: Any) -> dict[str, Any]:
    from .developer_context import DeveloperSelection

    row = _exact_mapping(
        value,
        "identities",
        {
            "core_id",
            "catalog_id",
            "host_adapter_id",
            "platform_profile_id",
            "pack_profile_id",
            "developer_selection",
        },
    )
    result = {
        "core_id": _identifier(row.get("core_id"), "identities.core_id"),
        "catalog_id": _identifier(row.get("catalog_id"), "identities.catalog_id"),
        "host_adapter_id": _identifier(
            row.get("host_adapter_id"), "identities.host_adapter_id"
        ),
        "platform_profile_id": _identifier(
            row.get("platform_profile_id"),
            "identities.platform_profile_id",
            nullable=True,
        ),
        "pack_profile_id": _identifier(
            row.get("pack_profile_id"),
            "identities.pack_profile_id",
            nullable=True,
        ),
    }
    if "developer_selection" in row:
        from dataclasses import asdict
        result["developer_selection"] = asdict(DeveloperSelection.from_dict(row["developer_selection"]))
    return result


def _normalize_frontend(value: Any) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "frontend",
        {"frontend_id", "kind", "version", "instance_id", "process_id"},
    )
    kind = _text(row.get("kind"), "frontend.kind", maximum=64)
    if kind not in FRONTEND_KINDS:
        _fail("work-session.invalid-record", f"unsupported frontend kind: {kind!r}")
    process_id = row.get("process_id")
    if process_id is not None and (type(process_id) is not int or process_id <= 0):
        _fail("work-session.invalid-record", "frontend.process_id must be positive")
    return {
        "frontend_id": _identifier(row.get("frontend_id"), "frontend.frontend_id"),
        "kind": kind,
        "version": _text(row.get("version"), "frontend.version", maximum=256),
        "instance_id": _identifier(
            row.get("instance_id"), "frontend.instance_id", nullable=True
        ),
        "process_id": process_id,
    }


def _normalize_owner_ref(value: Any) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "owner record reference",
        {
            "owner_id",
            "record_id",
            "record_kind",
            "uri",
            "digest",
            "last_verified_state",
            "verified_at",
        },
    )
    uri = _text(row.get("uri"), "owner record uri", maximum=8192)
    assert uri is not None
    if ":" not in uri:
        _fail("work-session.invalid-record", "owner record uri must include a scheme")
    verified_at = row.get("verified_at")
    digest = _identifier(
        row.get("digest"), "owner record digest", nullable=True
    )
    if digest is not None and _DIGEST.fullmatch(digest) is None:
        _fail(
            "work-session.invalid-record",
            "owner record digest must be one lowercase SHA-256 identity",
        )
    return {
        "owner_id": _identifier(row.get("owner_id"), "owner record owner_id"),
        "record_id": _identifier(row.get("record_id"), "owner record record_id"),
        "record_kind": _identifier(
            row.get("record_kind"), "owner record record_kind"
        ),
        "uri": uri,
        "digest": digest,
        "last_verified_state": _identifier(
            row.get("last_verified_state"),
            "owner record last_verified_state",
            nullable=True,
        ),
        "verified_at": (
            None if verified_at is None else _timestamp(verified_at, "owner record verified_at")
        ),
    }


def _normalize_owner_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_REFERENCES:
        _fail("work-session.invalid-record", "owner_record_refs must be bounded")
    rows = [_normalize_owner_ref(item) for item in value]
    identifiers = [item["record_id"] for item in rows]
    if len(identifiers) != len(set(identifiers)):
        _fail("work-session.invalid-record", "owner_record_refs contain duplicate IDs")
    return rows


def _verify_owner_refs(
    value: Any,
    *,
    verifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    allowed_states: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve references through their owner and reject caller-authored truth."""

    if not callable(verifier):
        _fail(
            "work-session.owner-verifier-required",
            "owner-backed references require an exact owner verifier",
        )
    supplied = _normalize_owner_refs(value)
    verified: list[dict[str, Any]] = []
    for reference in supplied:
        try:
            resolved = _normalize_owner_ref(verifier(_snapshot(reference)))
        except WorkSessionError:
            raise
        except Exception as exc:
            raise WorkSessionError(
                "work-session.owner-verification-failed",
                f"owner record could not be verified: {reference['record_id']}: {exc}",
            ) from exc
        identity_fields = ("owner_id", "record_id", "record_kind", "uri")
        if any(resolved[field] != reference[field] for field in identity_fields):
            _fail(
                "work-session.owner-identity-drift",
                "owner verifier changed a retained custody identity",
            )
        if reference["digest"] is not None and resolved["digest"] != reference["digest"]:
            _fail(
                "work-session.owner-digest-drift",
                "owner record digest changed before it could be bound",
            )
        if (
            not isinstance(resolved["digest"], str)
            or _DIGEST.fullmatch(resolved["digest"]) is None
            or resolved["last_verified_state"] is None
            or resolved["verified_at"] is None
        ):
            _fail(
                "work-session.owner-verification-invalid",
                "owner verifier did not return a digest-bound current record",
            )
        if (
            allowed_states is not None
            and resolved["last_verified_state"] not in allowed_states
        ):
            _fail(
                "work-session.owner-state-invalid",
                "owner record state is not valid for this binding",
            )
        verified.append(resolved)
    return verified


def _normalize_result_ref(value: Any) -> dict[str, Any]:
    row = _exact_mapping(value, "result reference", {"result_id", "owner_record_ref"})
    return {
        "result_id": _identifier(row.get("result_id"), "result.result_id"),
        "owner_record_ref": _normalize_owner_ref(row.get("owner_record_ref")),
    }


def _normalize_result_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_REFERENCES:
        _fail("work-session.invalid-record", "result_refs must be bounded")
    rows = [_normalize_result_ref(item) for item in value]
    identifiers = [item["result_id"] for item in rows]
    if len(identifiers) != len(set(identifiers)):
        _fail("work-session.invalid-record", "result_refs contain duplicate IDs")
    return rows


def _sanitize_argument(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        _fail("work-session.invalid-action", "action arguments exceed nesting budget")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            _fail("work-session.invalid-action", "action arguments contain non-finite number")
        return value
    if type(value) is str:
        text = _text(value, "action argument", maximum=8192, nullable=True)
        return text
    if type(value) is list:
        if len(value) > 256:
            _fail("work-session.invalid-action", "action argument list is too large")
        return [_sanitize_argument(item, depth=depth + 1) for item in value]
    if type(value) is dict:
        if len(value) > 256:
            _fail("work-session.invalid-action", "action argument object is too large")
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = _text(key, "action argument key", maximum=256)
            assert name is not None
            normalized_key = re.sub(r"[^a-z0-9]", "", name.casefold())
            if _SECRET_ARGUMENT_KEYS.search(name) or any(
                marker in normalized_key for marker in _SECRET_KEY_MATERIAL
            ):
                _fail(
                    "work-session.sensitive-argument",
                    f"action argument {name!r} appears credential-bearing and cannot be retained",
                )
            result[name] = _sanitize_argument(item, depth=depth + 1)
        return result
    _fail("work-session.invalid-action", "action arguments must be ordinary JSON values")


def _normalize_action(value: Any) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "action",
        {
            "action_id",
            "action_digest",
            "owner_id",
            "availability",
            "mutation_budget",
            "arguments",
        },
    )
    availability = _text(row.get("availability"), "action.availability", maximum=32)
    mutation_budget = _text(
        row.get("mutation_budget"), "action.mutation_budget", maximum=32
    )
    if availability not in AVAILABILITY:
        _fail("work-session.invalid-action", "action availability is invalid")
    if mutation_budget not in MUTATION_BUDGETS:
        _fail("work-session.invalid-action", "action mutation budget is invalid")
    arguments = _sanitize_argument(row.get("arguments", {}))
    if type(arguments) is not dict or len(_canonical_bytes(arguments)) > MAX_ARGUMENT_BYTES:
        _fail("work-session.invalid-action", "sanitized action arguments exceed byte budget")
    return {
        "action_id": _identifier(row.get("action_id"), "action.action_id"),
        "action_digest": _identifier(
            row.get("action_digest"), "action.action_digest"
        ),
        "owner_id": _identifier(row.get("owner_id"), "action.owner_id"),
        "availability": availability,
        "mutation_budget": mutation_budget,
        "arguments": arguments,
    }


def _normalize_next_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_NEXT_ACTIONS:
        _fail("work-session.invalid-record", "next_actions must be bounded")
    rows = [_normalize_action(item) for item in value]
    identifiers = [item["action_id"] for item in rows]
    if len(identifiers) != len(set(identifiers)):
        _fail("work-session.invalid-record", "next_actions contain duplicate IDs")
    return rows


def _normalize_catalog_action(value: Any, *, catalog: Any) -> dict[str, Any]:
    """Verify a retained action against its catalog and reject secret values."""

    # Apply the journal's fail-closed secret filter before consulting an
    # optional catalog.  A caller cannot bypass the persistence boundary (or
    # turn a credential failure into a weaker "catalog missing" failure) by
    # omitting the catalog adapter.
    action = _normalize_action(value)
    if catalog is None or not callable(getattr(catalog, "command", None)):
        _fail(
            "work-session.catalog-required",
            "frontend-authored actions require the exact current catalog",
        )
    try:
        command = catalog.command(action["action_id"])
        digest = command.action_digest(root=catalog.root)
    except WorkSessionError:
        raise
    except Exception as exc:
        raise WorkSessionError(
            "work-session.action-invalid",
            f"catalog rejected retained action {action['action_id']!r}: {exc}",
        ) from exc
    if digest != action["action_digest"] or command.risk != action["mutation_budget"]:
        _fail(
            "work-session.stale-action",
            "retained action digest or mutation budget differs from the catalog",
        )
    if action["availability"] not in {command.availability, "unavailable", "blocked", "stale"}:
        _fail(
            "work-session.stale-action",
            "retained action availability differs from the catalog",
        )
    sensitive = {
        field.key
        for field in command.fields
        if getattr(field, "sensitive", False)
    }
    supplied_sensitive = sorted(sensitive.intersection(action["arguments"]))
    if supplied_sensitive:
        _fail(
            "work-session.sensitive-argument",
            "catalog-sensitive action arguments cannot be retained: "
            + ", ".join(supplied_sensitive),
        )
    try:
        command.build_argv(action["arguments"], root=catalog.root, execute=False)
    except Exception as exc:
        raise WorkSessionError(
            "work-session.action-invalid",
            f"catalog rejected retained action arguments: {exc}",
        ) from exc
    return action


def _normalize_catalog_actions(value: Any, *, catalog: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_NEXT_ACTIONS:
        _fail("work-session.invalid-record", "next_actions must be bounded")
    rows = [_normalize_catalog_action(item, catalog=catalog) for item in value]
    identifiers = [item["action_id"] for item in rows]
    if len(identifiers) != len(set(identifiers)):
        _fail("work-session.invalid-record", "next_actions contain duplicate IDs")
    return rows


def _normalize_problem(value: Any) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "problem",
        {
            "problem_id",
            "code",
            "severity",
            "message",
            "affected_identity",
            "evidence_refs",
            "next_action_id",
        },
    )
    severity = _text(row.get("severity"), "problem.severity", maximum=32)
    if severity not in PROBLEM_SEVERITIES:
        _fail("work-session.invalid-record", "problem severity is invalid")
    return {
        "problem_id": _identifier(row.get("problem_id"), "problem.problem_id"),
        "code": _identifier(row.get("code"), "problem.code"),
        "severity": severity,
        "message": _text(row.get("message"), "problem.message", maximum=4096),
        "affected_identity": _identifier(
            row.get("affected_identity"),
            "problem.affected_identity",
            nullable=True,
        ),
        "evidence_refs": _normalize_owner_refs(row.get("evidence_refs", [])),
        "next_action_id": _identifier(
            row.get("next_action_id"), "problem.next_action_id", nullable=True
        ),
    }


def _normalize_problems(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_PROBLEMS:
        _fail("work-session.invalid-record", "problems must be bounded")
    rows = [_normalize_problem(item) for item in value]
    identifiers = [item["problem_id"] for item in rows]
    if len(identifiers) != len(set(identifiers)):
        _fail("work-session.invalid-record", "problems contain duplicate IDs")
    return rows


def _normalize_stage(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    row = _exact_mapping(value, "stage", {"stage_id", "state"})
    state = _text(row.get("state"), "stage.state", maximum=32)
    if state not in {
        "pending",
        "running",
        "complete",
        "failed",
        "cancelled",
        "incomplete",
        "blocked",
    }:
        _fail("work-session.invalid-record", "stage state is invalid")
    return {
        "stage_id": _identifier(row.get("stage_id"), "stage.stage_id"),
        "state": state,
    }


def _normalize_recovery(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    row = _exact_mapping(
        value,
        "recovery",
        {"state", "reason", "owner_record_refs", "safe_action_ids"},
    )
    state = _text(row.get("state"), "recovery.state", maximum=32)
    if state not in RECOVERY_STATES:
        _fail("work-session.invalid-record", "recovery state is invalid")
    return {
        "state": state,
        "reason": _text(row.get("reason"), "recovery.reason", maximum=4096),
        "owner_record_refs": _normalize_owner_refs(
            row.get("owner_record_refs", [])
        ),
        "safe_action_ids": _bounded_strings(
            row.get("safe_action_ids", []),
            "recovery.safe_action_ids",
            maximum_items=MAX_NEXT_ACTIONS,
            maximum_bytes=512,
        ),
    }


def _validate_session_record(value: Any, *, physical_directory: Path | None = None) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        "session record",
        {
            "format_version",
            "schema_version",
            "session_id",
            "session_record_id",
            "created_at",
            "label",
            "task",
            "workspace",
            "identities",
            "retention",
        },
    )
    if row.get("format_version") != SESSION_FORMAT or row.get("schema_version") != 2:
        _fail("work-session.unsupported-format", "session record format is unsupported")
    session_id = row.get("session_id")
    if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
        _fail("work-session.invalid-record", "session ID is invalid")
    if physical_directory is not None and session_id != physical_directory.name:
        _fail("work-session.tampered-record", "session ID differs from its directory")
    record_id = row.get("session_record_id")
    if type(record_id) is not str or _RECORD_ID.fullmatch(record_id) is None:
        _fail("work-session.invalid-record", "session record ID is invalid")
    retention = _exact_mapping(
        row.get("retention"),
        "session retention",
        {"journal", "summary", "append_only", "summary_disposable"},
    )
    if retention != {
        "journal": JOURNAL_DIRECTORY_NAME,
        "summary": SUMMARY_NAME,
        "append_only": True,
        "summary_disposable": True,
    }:
        _fail("work-session.tampered-record", "session retention bindings are invalid")
    normalized = {
        "format_version": SESSION_FORMAT,
        "schema_version": 2,
        "session_id": session_id,
        "session_record_id": record_id,
        "created_at": _timestamp(row.get("created_at"), "session.created_at"),
        "label": _text(row.get("label"), "session.label", maximum=1024, nullable=True),
        "task": _normalize_task(row.get("task")),
        "workspace": _normalize_workspace(row.get("workspace"), require_present=False),
        "identities": _normalize_identities(row.get("identities")),
        "retention": dict(retention),
    }
    body = dict(normalized)
    body.pop("session_record_id")
    if record_id != _content_id("work-session-record", body):
        _fail("work-session.tampered-record", "session record identity changed")
    return normalized


def validate_work_session_record(value: Any) -> dict[str, Any]:
    """Validate one immutable Work Session V2 header through its public owner port."""

    return _validate_session_record(value)


def validate_work_session_event(
    value: Any,
    *,
    session_id: str | None = None,
    sequence: int | None = None,
    previous_event_id: str | None | object = ...,
) -> dict[str, Any]:
    """Validate one immutable Work Session event and its optional chain slot."""

    row = _exact_mapping(
        value,
        "work session event",
        {
            "format_version",
            "event_id",
            "session_id",
            "session_record_id",
            "sequence",
            "previous_event_id",
            "occurred_at",
            "frontend",
            "kind",
            "task_id",
            "lifecycle",
            "stage",
            "action",
            "result_refs",
            "problems",
            "next_actions",
            "owner_record_refs",
            "recovery",
            "workspace_observation",
            "closed",
            "message",
            "limitations",
            "unknowns",
        },
    )
    if row.get("format_version") != EVENT_FORMAT:
        _fail("work-session.unsupported-format", "event format is unsupported")
    event_id = row.get("event_id")
    if type(event_id) is not str or _EVENT_ID.fullmatch(event_id) is None:
        _fail("work-session.invalid-event", "event ID is invalid")
    observed_session_id = row.get("session_id")
    if type(observed_session_id) is not str or _SESSION_ID.fullmatch(observed_session_id) is None:
        _fail("work-session.invalid-event", "event session ID is invalid")
    if session_id is not None and observed_session_id != session_id:
        _fail("work-session.tampered-event", "event session ID changed")
    observed_sequence = row.get("sequence")
    if type(observed_sequence) is not int or not 0 <= observed_sequence < MAX_EVENTS:
        _fail("work-session.invalid-event", "event sequence is invalid")
    if sequence is not None and observed_sequence != sequence:
        _fail("work-session.tampered-event", "event sequence differs from its journal slot")
    observed_previous = row.get("previous_event_id")
    if observed_previous is not None and (
        type(observed_previous) is not str or _EVENT_ID.fullmatch(observed_previous) is None
    ):
        _fail("work-session.invalid-event", "previous event ID is invalid")
    if previous_event_id is not ... and observed_previous != previous_event_id:
        _fail("work-session.tampered-event", "event chain does not match its predecessor")
    lifecycle = row.get("lifecycle")
    if lifecycle not in LIFECYCLES:
        _fail("work-session.invalid-event", "event lifecycle is invalid")
    kind = _identifier(row.get("kind"), "event.kind")
    recovery = _normalize_recovery(row.get("recovery"))
    next_actions = _normalize_next_actions(row.get("next_actions", []))
    if lifecycle == "recoverable" and (
        recovery is None
        or recovery["state"] != "required"
        or not recovery["safe_action_ids"]
    ):
        _fail(
            "work-session.invalid-recovery",
            "recoverable events require an exact required recovery and safe action",
        )
    if recovery is not None:
        available = {item["action_id"] for item in next_actions}
        if not set(recovery["safe_action_ids"]).issubset(available):
            _fail(
                "work-session.invalid-recovery",
                "recovery safe actions must be present in next_actions",
            )
    workspace_observation = row.get("workspace_observation")
    normalized = {
        "format_version": EVENT_FORMAT,
        "event_id": event_id,
        "session_id": observed_session_id,
        "session_record_id": _identifier(
            row.get("session_record_id"), "event.session_record_id"
        ),
        "sequence": observed_sequence,
        "previous_event_id": observed_previous,
        "occurred_at": _timestamp(row.get("occurred_at"), "event.occurred_at"),
        "frontend": _normalize_frontend(row.get("frontend")),
        "kind": kind,
        "task_id": _identifier(row.get("task_id"), "event.task_id"),
        "lifecycle": lifecycle,
        "stage": _normalize_stage(row.get("stage")),
        "action": None if row.get("action") is None else _normalize_action(row["action"]),
        "result_refs": _normalize_result_refs(row.get("result_refs", [])),
        "problems": _normalize_problems(row.get("problems", [])),
        "next_actions": next_actions,
        "owner_record_refs": _normalize_owner_refs(row.get("owner_record_refs", [])),
        "recovery": recovery,
        "workspace_observation": (
            None
            if workspace_observation is None
            else _normalize_workspace(workspace_observation, require_present=False)
        ),
        "closed": row.get("closed"),
        "message": _text(row.get("message"), "event.message", maximum=4096, nullable=True),
        "limitations": _bounded_strings(
            row.get("limitations", []), "event.limitations"
        ),
        "unknowns": _bounded_strings(row.get("unknowns", []), "event.unknowns"),
    }
    if lifecycle in {"running", "recoverable"} and not normalized["owner_record_refs"]:
        _fail(
            "work-session.invalid-owner-custody",
            "running or recoverable events require exact owner custody references",
        )
    if lifecycle == "recoverable":
        retained_owners = {
            _canonical_bytes(reference)
            for reference in normalized["owner_record_refs"]
        }
        if any(
            _canonical_bytes(reference) not in retained_owners
            for reference in normalized["recovery"]["owner_record_refs"]
        ):
            _fail(
                "work-session.invalid-owner-custody",
                "recovery owner references must be retained by the event",
            )
    stage = normalized["stage"]
    terminal_stage = (
        stage is not None and stage["state"] in TERMINAL_STAGE_STATES
    )
    if lifecycle in TERMINAL_LIFECYCLES:
        if (
            stage is None
            or stage["state"] != lifecycle
            or not normalized["result_refs"]
            or not normalized["owner_record_refs"]
        ):
            _fail(
                "work-session.invalid-terminal-event",
                "terminal events require a matching stage and exact owner result references",
            )
        retained_owners = {
            _canonical_bytes(reference)
            for reference in normalized["owner_record_refs"]
        }
        result_owners = {
            _canonical_bytes(result["owner_record_ref"])
            for result in normalized["result_refs"]
        }
        if result_owners != retained_owners:
            _fail(
                "work-session.invalid-terminal-event",
                "terminal events require results for the exact full owner custody set",
            )
        for result in normalized["result_refs"]:
            owner = result["owner_record_ref"]
            if (
                _canonical_bytes(owner) not in retained_owners
                or owner["digest"] is None
                or owner["verified_at"] is None
                or owner["last_verified_state"] != lifecycle
            ):
                _fail(
                    "work-session.invalid-terminal-event",
                    "terminal result custody is absent, unverified, or contradictory",
                )
    elif terminal_stage:
        _fail(
            "work-session.invalid-terminal-event",
            "terminal stage state requires the matching terminal lifecycle",
        )
    if type(normalized["closed"]) is not bool:
        _fail("work-session.invalid-event", "event.closed must be boolean")
    body = dict(normalized)
    body.pop("event_id")
    if event_id != _content_id("work-session-event", body):
        _fail("work-session.tampered-event", "event identity changed")
    return normalized


def validate_work_session_summary(value: Any) -> dict[str, Any]:
    """Validate and defensively copy a disposable Work Session summary."""

    row = _exact_mapping(
        value,
        "work session summary",
        {
            "format_version",
            "summary_id",
            "session_id",
            "session_record_id",
            "task",
            "workspace",
            "identities",
            "lifecycle",
            "created_at",
            "updated_at",
            "latest_sequence",
            "latest_event_id",
            "event_count",
            "closed",
            "frontend_ids",
            "owner_record_refs",
            "result_refs",
            "problems",
            "next_actions",
            "recovery",
            "limitations",
            "unknowns",
            "integrity_state",
            "integrity_problem_ids",
        },
    )
    if row.get("format_version") != SUMMARY_FORMAT:
        _fail("work-session.unsupported-format", "summary format is unsupported")
    summary_id = row.get("summary_id")
    if type(summary_id) is not str or _SUMMARY_ID.fullmatch(summary_id) is None:
        _fail("work-session.invalid-summary", "summary ID is invalid")
    session_id = row.get("session_id")
    if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
        _fail("work-session.invalid-summary", "summary session ID is invalid")
    lifecycle = row.get("lifecycle")
    if lifecycle not in LIFECYCLES:
        _fail("work-session.invalid-summary", "summary lifecycle is invalid")
    latest_sequence = row.get("latest_sequence")
    event_count = row.get("event_count")
    if (
        type(latest_sequence) is not int
        or latest_sequence < -1
        or type(event_count) is not int
        or event_count < 0
        or event_count > MAX_EVENTS
        or (event_count == 0 and latest_sequence != -1)
        or (event_count > 0 and latest_sequence != event_count - 1)
    ):
        _fail("work-session.invalid-summary", "summary event counters are invalid")
    latest_event_id = row.get("latest_event_id")
    if latest_event_id is not None and (
        type(latest_event_id) is not str or _EVENT_ID.fullmatch(latest_event_id) is None
    ):
        _fail("work-session.invalid-summary", "summary latest event ID is invalid")
    if (event_count == 0) != (latest_event_id is None):
        _fail("work-session.invalid-summary", "summary latest event binding is invalid")
    integrity_state = row.get("integrity_state")
    if integrity_state not in {"verified", "recoverable", "corrupt"}:
        _fail("work-session.invalid-summary", "summary integrity state is invalid")
    normalized = {
        "format_version": SUMMARY_FORMAT,
        "summary_id": summary_id,
        "session_id": session_id,
        "session_record_id": _identifier(
            row.get("session_record_id"), "summary.session_record_id"
        ),
        "task": _normalize_task(row.get("task")),
        "workspace": _normalize_workspace(row.get("workspace"), require_present=False),
        "identities": _normalize_identities(row.get("identities")),
        "lifecycle": lifecycle,
        "created_at": _timestamp(row.get("created_at"), "summary.created_at"),
        "updated_at": _timestamp(row.get("updated_at"), "summary.updated_at"),
        "latest_sequence": latest_sequence,
        "latest_event_id": latest_event_id,
        "event_count": event_count,
        "closed": row.get("closed"),
        "frontend_ids": _bounded_strings(
            row.get("frontend_ids", []), "summary.frontend_ids", maximum_bytes=512
        ),
        "owner_record_refs": _normalize_owner_refs(row.get("owner_record_refs", [])),
        "result_refs": _normalize_result_refs(row.get("result_refs", [])),
        "problems": _normalize_problems(row.get("problems", [])),
        "next_actions": _normalize_next_actions(row.get("next_actions", [])),
        "recovery": _normalize_recovery(row.get("recovery")),
        "limitations": _bounded_strings(row.get("limitations", []), "summary.limitations"),
        "unknowns": _bounded_strings(row.get("unknowns", []), "summary.unknowns"),
        "integrity_state": integrity_state,
        "integrity_problem_ids": _bounded_strings(
            row.get("integrity_problem_ids", []),
            "summary.integrity_problem_ids",
            maximum_bytes=512,
        ),
    }
    if type(normalized["closed"]) is not bool:
        _fail("work-session.invalid-summary", "summary.closed must be boolean")
    body = dict(normalized)
    body.pop("summary_id")
    if summary_id != _content_id("work-session-summary", body):
        _fail("work-session.tampered-summary", "summary identity changed")
    return normalized


def _safe_storage_root(root: Path, *, create: bool) -> Path:
    if not isinstance(root, Path):
        _fail("work-session.invalid-root", "storage root must be a Path")
    supplied = root.expanduser()
    lexical = Path(os.path.abspath(supplied))
    for component in [*reversed(lexical.parents), lexical]:
        if component.is_symlink():
            _fail(
                "work-session.unsafe-path",
                f"storage root parent cannot be a symbolic link: {component}",
            )
    supplied = lexical
    if create:
        supplied.mkdir(parents=True, exist_ok=True)
    if not supplied.is_dir() or supplied.is_symlink():
        _fail("work-session.invalid-root", "storage root must be a real directory")
    base_root = supplied.resolve(strict=True)
    current = base_root
    for member in STORAGE_DIRECTORY.parts:
        current = current / member
        if current.is_symlink():
            _fail("work-session.unsafe-path", f"storage path cannot be a symlink: {current}")
        if create:
            try:
                secure_private_path(current, directory=True)
            except HostFilesystemError as exc:
                raise WorkSessionError(
                    "work-session.unsafe-path", f"cannot secure session storage: {exc}"
                ) from exc
    return current


def _read_private_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink():
        _fail("work-session.unsafe-path", f"{label} is a symbolic link")
    if path.exists() and not private_path(path, directory=False):
        _fail("work-session.unsafe-path", f"{label} is not owner-private")
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_RECORD_BYTES
                or (
                    os.name != "nt"
                    and (
                        metadata.st_mode & 0o077
                        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
                    )
                )
            ):
                _fail("work-session.invalid-record", f"{label} is not a bounded regular file")
            raw = b""
            while len(raw) <= MAX_RECORD_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_RECORD_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise WorkSessionError(
            "work-session.unreadable-record", f"cannot read {label}: {exc}"
        ) from exc
    if len(raw) > MAX_RECORD_BYTES:
        _fail("work-session.invalid-record", f"{label} exceeds its byte budget")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkSessionError(
            "work-session.corrupt-record", f"{label} is not strict UTF-8 JSON: {exc}"
        ) from exc
    if type(value) is not dict:
        _fail("work-session.corrupt-record", f"{label} is not an object")
    return value, raw


_RECORD_CUSTODY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns")


def _same_record_custody(
    left: os.stat_result,
    right: os.stat_result,
    *,
    expected_size: int,
) -> bool:
    """Compare one record across descriptor/path views without NTFS ctime.

    Applying a private Windows DACL can change or expose a different ctime view
    even though the file identity and content-bearing metadata are unchanged.
    The stable fields below retain the actual custody boundary while allowing
    that documented host difference.
    """

    return (
        stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
        and left.st_size == expected_size
        and right.st_size == expected_size
        and all(
            getattr(left, field) == getattr(right, field)
            for field in _RECORD_CUSTODY_FIELDS
        )
    )


def _secure_new_private_record(
    path: Path,
    created: os.stat_result,
    *,
    expected_size: int,
    label: str,
) -> os.stat_result:
    """Secure and re-identify one uncommitted record before publication."""

    try:
        secure_private_path(path, directory=False)
    except OSError as exc:
        raise WorkSessionError(
            "work-session.unsafe-path",
            f"cannot secure {label} before publication: {exc}",
        ) from exc
    if not private_path(path, directory=False):
        _fail(
            "work-session.unsafe-path",
            f"{label} is not owner-private before publication",
        )
    try:
        secured = path.lstat()
    except OSError as exc:
        raise WorkSessionError(
            "work-session.unsafe-path",
            f"cannot re-identify {label} before publication: {exc}",
        ) from exc
    if not _same_record_custody(created, secured, expected_size=expected_size):
        _fail(
            "work-session.unsafe-path",
            f"{label} custody changed while it was secured",
        )
    return secured


def _verify_published_private_record(
    path: Path,
    secured: os.stat_result,
    *,
    expected_size: int,
    label: str,
) -> None:
    """Verify an atomic publication still names the secured source file."""

    if path.is_symlink() or not private_path(path, directory=False):
        _fail(
            "work-session.unsafe-path",
            f"published {label} is not owner-private",
        )
    try:
        published = path.lstat()
    except OSError as exc:
        raise WorkSessionError(
            "work-session.unsafe-path",
            f"cannot re-identify published {label}: {exc}",
        ) from exc
    if not _same_record_custody(secured, published, expected_size=expected_size):
        _fail(
            "work-session.unsafe-path",
            f"published {label} does not retain secured file custody",
        )


@contextmanager
def _private_record_temporary(path: Path, raw: bytes, *, label: str):
    """Create, flush, secure, and verify one unpublished record file."""

    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    descriptor_open = True
    try:
        os.chmod(temporary, 0o600)
        output = os.fdopen(descriptor, "wb")
        descriptor_open = False
        with output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            created = os.fstat(output.fileno())
        secured = _secure_new_private_record(
            temporary,
            created,
            expected_size=len(raw),
            label=label,
        )
        yield temporary, secured
    finally:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    raw = _canonical_bytes(value) + b"\n"
    if path.is_symlink():
        _fail("work-session.unsafe-path", f"immutable target is a symlink: {path}")
    if path.exists():
        _, observed = _read_private_json(path, "immutable record")
        if observed != raw:
            _fail("work-session.immutable-collision", f"immutable record differs: {path}")
        return
    with _private_record_temporary(path, raw, label="immutable record") as (
        temporary,
        secured,
    ):
        published = False
        try:
            os.link(temporary, path)
        except FileExistsError:
            _, observed = _read_private_json(path, "immutable record")
            if observed != raw:
                _fail("work-session.immutable-collision", f"immutable record raced: {path}")
        else:
            published = True
        if published:
            _verify_published_private_record(
                path,
                secured,
                expected_size=len(raw),
                label="immutable record",
            )
            _, observed = _read_private_json(path, "immutable record")
            if observed != raw:
                _fail(
                    "work-session.immutable-collision",
                    f"published immutable record differs: {path}",
                )
        # Preserve the original durability boundary for a same-content link
        # race as well as for the link published by this writer.
        fsync_directory(path.parent)


def _replace_summary(path: Path, value: Mapping[str, Any]) -> None:
    if path.is_symlink():
        _fail("work-session.unsafe-path", "summary cache is a symbolic link")
    if path.exists() and not private_path(path, directory=False):
        _fail("work-session.unsafe-path", "summary cache is not owner-private")
    raw = _canonical_bytes(value) + b"\n"
    with _private_record_temporary(path, raw, label="summary cache") as (
        temporary,
        secured,
    ):
        os.replace(temporary, path)
        _verify_published_private_record(
            path,
            secured,
            expected_size=len(raw),
            label="summary cache",
        )
        _, observed = _read_private_json(path, "summary cache")
        if observed != raw:
            _fail("work-session.stale-summary", "published summary bytes changed")
        fsync_directory(path.parent)


def _make_session_id() -> str:
    return "work-session-v2-" + secrets.token_hex(16)


def _make_event(body: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(body)
    value["event_id"] = _content_id("work-session-event", value)
    return validate_work_session_event(value)


def _latest_by_id(
    current: dict[str, dict[str, Any]],
    rows: Iterable[Mapping[str, Any]],
    key: str,
) -> None:
    for row in rows:
        current[str(row[key])] = dict(row)


def _storage_problem(
    *,
    session_id: str,
    code: str,
    message: str,
    sequence: int,
) -> dict[str, Any]:
    body = {
        "code": code,
        "message": message[:4096].replace("\r", " ").replace("\n", " "),
        "sequence": sequence,
        "session_id": session_id,
    }
    return {
        "problem_id": "work-session-problem:sha256:"
        + hashlib.sha256(_canonical_bytes(body)).hexdigest(),
        "code": code,
        "severity": "error",
        "message": body["message"],
        "affected_identity": session_id,
        "evidence_refs": [],
        "next_action_id": None,
    }


def _derive_summary(
    session: Mapping[str, Any],
    events: list[Mapping[str, Any]],
    *,
    integrity_state: str,
    integrity_problems: list[Mapping[str, Any]],
) -> dict[str, Any]:
    task = dict(session["task"])
    workspace = dict(session["workspace"])
    identities = dict(session["identities"])
    lifecycle = "incomplete" if not events else str(events[-1]["lifecycle"])
    updated_at = str(session["created_at"] if not events else events[-1]["occurred_at"])
    closed = False
    frontends: set[str] = set()
    owner_refs: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    problems: dict[str, dict[str, Any]] = {}
    next_actions: dict[str, dict[str, Any]] = {}
    recovery: dict[str, Any] | None = None
    limitations: list[str] = []
    unknowns: list[str] = []
    for event in events:
        frontends.add(str(event["frontend"]["frontend_id"]))
        if event.get("workspace_observation") is not None:
            observed_workspace = dict(event["workspace_observation"])
            if observed_workspace != workspace:
                next_actions = {
                    key: {**action, "availability": "stale"}
                    for key, action in next_actions.items()
                }
            workspace = observed_workspace
        _latest_by_id(owner_refs, event.get("owner_record_refs", []), "record_id")
        _latest_by_id(results, event.get("result_refs", []), "result_id")
        _latest_by_id(problems, event.get("problems", []), "problem_id")
        if event.get("next_actions"):
            next_actions = {
                str(row["action_id"]): dict(row) for row in event["next_actions"]
            }
        if event.get("recovery") is not None:
            recovery = dict(event["recovery"])
        limitations.extend(str(item) for item in event.get("limitations", []))
        unknowns.extend(str(item) for item in event.get("unknowns", []))
        closed = bool(event.get("closed", closed))
    for problem in integrity_problems:
        problems[str(problem["problem_id"])] = dict(problem)
    if integrity_state == "corrupt":
        lifecycle = "incomplete"
        next_actions = {}
        recovery = None
        closed = False
    body: dict[str, Any] = {
        "format_version": SUMMARY_FORMAT,
        "session_id": session["session_id"],
        "session_record_id": session["session_record_id"],
        "task": task,
        "workspace": workspace,
        "identities": identities,
        "lifecycle": lifecycle,
        "created_at": session["created_at"],
        "updated_at": updated_at,
        "latest_sequence": -1 if not events else events[-1]["sequence"],
        "latest_event_id": None if not events else events[-1]["event_id"],
        "event_count": len(events),
        "closed": closed,
        "frontend_ids": sorted(frontends),
        "owner_record_refs": [owner_refs[key] for key in sorted(owner_refs)],
        "result_refs": [results[key] for key in sorted(results)],
        "problems": [problems[key] for key in sorted(problems)],
        "next_actions": [next_actions[key] for key in sorted(next_actions)],
        "recovery": recovery,
        "limitations": list(dict.fromkeys(limitations))[-128:],
        "unknowns": list(dict.fromkeys(unknowns))[-128:],
        "integrity_state": integrity_state,
        "integrity_problem_ids": sorted(
            str(problem["problem_id"]) for problem in integrity_problems
        ),
    }
    body["summary_id"] = _content_id("work-session-summary", body)
    return validate_work_session_summary(body)


class WorkSessionStore:
    """Private append-only Work Session store shared by CLI and IDE clients."""

    def __init__(
        self,
        root: Path,
        *,
        physical_leases: ServicePhysicalLeasePorts | None = None,
        clock: Callable[[], str] = utc_now,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.root = root
        self.base = _safe_storage_root(root, create=True)
        self.locks = self.base / LOCK_DIRECTORY_NAME
        try:
            secure_private_path(self.locks, directory=True)
        except HostFilesystemError as exc:
            raise WorkSessionError(
                "work-session.unsafe-path", f"cannot secure session locks: {exc}"
            ) from exc
        self._physical_leases = physical_leases or local_service_physical_lease_ports()
        if type(self._physical_leases) is not ServicePhysicalLeasePorts:
            _fail(
                "work-session.invalid-lease-provider",
                "an exact Host Adapter physical lease provider is required",
            )
        if not callable(clock) or (fault_injector is not None and not callable(fault_injector)):
            _fail("work-session.invalid-port", "clock and fault injector must be callable")
        self._clock = clock
        self._fault_injector = fault_injector
        self._thread_lock = threading.RLock()

    def _fault(self, checkpoint: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(checkpoint)

    def _lock_path(self, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("ascii")).hexdigest()
        path = self.locks / f"{digest}.lock"
        if path.is_symlink():
            _fail("work-session.unsafe-path", "session lock is a symbolic link")
        flags = os.O_RDWR | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        descriptor = -1
        created = False
        try:
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if path.is_symlink():
                    _fail("work-session.unsafe-path", "session lock is a symbolic link")
                descriptor = os.open(path, flags)
            else:
                created = True

            opened = os.fstat(descriptor)
            current = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                _fail(
                    "work-session.unsafe-path",
                    "session lock custody changed while it was opened",
                )
            if created:
                try:
                    secure_private_path(path, directory=False)
                except HostFilesystemError as exc:
                    raise WorkSessionError(
                        "work-session.unsafe-path", f"cannot secure session lock: {exc}"
                    ) from exc
            if not private_path(path, directory=False):
                _fail("work-session.unsafe-path", "session lock is not owner-private")
            verified = path.lstat()
            if (
                not stat.S_ISREG(verified.st_mode)
                or (opened.st_dev, opened.st_ino)
                != (verified.st_dev, verified.st_ino)
            ):
                _fail(
                    "work-session.unsafe-path",
                    "session lock custody changed while it was secured",
                )
            if created:
                os.fsync(descriptor)
        except WorkSessionError:
            raise
        except OSError as exc:
            raise WorkSessionError(
                "work-session.lock-failed", f"cannot open session lock: {exc}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if created:
            try:
                fsync_directory(path.parent)
            except OSError as exc:
                raise WorkSessionError(
                    "work-session.lock-failed",
                    f"cannot publish session lock: {exc}",
                ) from exc
        return path

    @contextmanager
    def _exclusive(self, session_id: str):
        path = self._lock_path(session_id)
        with self._thread_lock:
            try:
                lease = self._physical_leases.exclusive(path)
                lease.__enter__()
            except ServiceV3Error as exc:
                raise WorkSessionError(
                    "work-session.lock-failed", str(exc), retryable=exc.retryable
                ) from exc
            except Exception as exc:
                raise WorkSessionError(
                    "work-session.lock-failed",
                    "Host Adapter record lease failed closed",
                ) from exc
            try:
                yield
            finally:
                try:
                    lease.__exit__(None, None, None)
                except Exception as exc:
                    raise WorkSessionError(
                        "work-session.lock-failed",
                        "Host Adapter record lease release failed closed",
                    ) from exc

    def _directory(self, session_id: str, *, must_exist: bool = True) -> Path:
        if type(session_id) is not str or _SESSION_ID.fullmatch(session_id) is None:
            _fail("work-session.invalid-selector", "Work Session selector is invalid")
        path = self.base / session_id
        if path.is_symlink():
            _fail("work-session.unsafe-path", "Work Session directory is a symbolic link")
        if must_exist and (not path.is_dir() or path.is_symlink()):
            _fail("work-session.not-found", f"Work Session does not exist: {session_id}")
        if must_exist and not private_path(path, directory=True):
            _fail("work-session.unsafe-path", "Work Session directory is not owner-private")
        return path

    def _resolve(self, selector: str | None) -> str:
        if selector in {None, "latest"}:
            rows = self.list()
            if not rows:
                _fail("work-session.not-found", "no Work Sessions exist")
            return str(rows[0]["session_id"])
        if type(selector) is not str or not selector:
            _fail("work-session.invalid-selector", "Work Session selector is invalid")
        if _SESSION_ID.fullmatch(selector):
            self._directory(selector)
            return selector
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", selector):
            _fail("work-session.invalid-selector", "Work Session selector is unsafe")
        matches = [
            path.name
            for path in self.base.iterdir()
            if path.is_dir() and not path.is_symlink() and path.name.startswith(selector)
        ]
        if not matches:
            _fail("work-session.not-found", f"no Work Session matches {selector!r}")
        if len(matches) != 1:
            _fail("work-session.ambiguous-selector", f"Work Session selector is ambiguous: {selector!r}")
        return matches[0]

    def _session(self, directory: Path) -> dict[str, Any]:
        value, _ = _read_private_json(directory / SESSION_RECORD_NAME, "session record")
        return _validate_session_record(value, physical_directory=directory)

    def _scan_journal(
        self,
        directory: Path,
        session: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        journal = directory / JOURNAL_DIRECTORY_NAME
        if (
            journal.is_symlink()
            or not journal.is_dir()
            or not private_path(journal, directory=True)
        ):
            problem = _storage_problem(
                session_id=str(session["session_id"]),
                code="work-session.journal-unsafe",
                message="Work Session journal is absent or symbolic; no completion was inferred.",
                sequence=-1,
            )
            return [], [problem], "corrupt"
        entries: list[Path] = []
        try:
            with os.scandir(journal) as scanner:
                for entry in scanner:
                    entries.append(Path(entry.path))
                    if len(entries) > MAX_EVENTS + MAX_REFERENCES:
                        problem = _storage_problem(
                            session_id=str(session["session_id"]),
                            code="work-session.journal-too-large",
                            message=(
                                "Journal entry count exceeds its pre-sort budget; "
                                "no completion was inferred."
                            ),
                            sequence=-1,
                        )
                        return [], [problem], "corrupt"
        except OSError as exc:
            problem = _storage_problem(
                session_id=str(session["session_id"]),
                code="work-session.journal-unreadable",
                message=f"Journal cannot be enumerated safely: {exc}",
                sequence=-1,
            )
            return [], [problem], "corrupt"
        entries.sort(key=lambda path: path.name)
        residue = [path for path in entries if path.name.startswith(".")]
        candidates = [path for path in entries if not path.name.startswith(".")]
        events: list[dict[str, Any]] = []
        problems: list[dict[str, Any]] = []
        integrity = "recoverable" if residue else "verified"
        if residue:
            problems.append(
                _storage_problem(
                    session_id=str(session["session_id"]),
                    code="work-session.orphan-temporary",
                    message="An interrupted append left uncommitted temporary residue; it was not treated as an event.",
                    sequence=-1,
                )
            )
        previous: str | None = None
        for expected, path in enumerate(candidates):
            match = _EVENT_FILE.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file():
                problems.append(
                    _storage_problem(
                        session_id=str(session["session_id"]),
                        code="work-session.journal-entry-unsafe",
                        message=f"Journal entry {path.name!r} is not a valid immutable event slot.",
                        sequence=expected,
                    )
                )
                integrity = "corrupt"
                break
            observed_slot = int(match.group(1))
            if observed_slot != expected:
                problems.append(
                    _storage_problem(
                        session_id=str(session["session_id"]),
                        code="work-session.journal-sequence-gap",
                        message=f"Journal expected sequence {expected} but found {observed_slot}; later events were isolated.",
                        sequence=expected,
                    )
                )
                integrity = "corrupt"
                break
            try:
                value, _ = _read_private_json(path, f"journal event {expected}")
                event = validate_work_session_event(
                    value,
                    session_id=str(session["session_id"]),
                    sequence=expected,
                    previous_event_id=previous,
                )
                if event["session_record_id"] != session["session_record_id"]:
                    _fail(
                        "work-session.tampered-event",
                        "event session record identity changed",
                    )
                if event["task_id"] != session["task"]["task_id"]:
                    _fail("work-session.tampered-event", "event task identity changed")
                if expected == 0 and event["kind"] != "session-created":
                    _fail("work-session.tampered-event", "first event is not session-created")
            except WorkSessionError as exc:
                problems.append(
                    _storage_problem(
                        session_id=str(session["session_id"]),
                        code=exc.code,
                        message=f"Journal event {expected} is corrupt: {exc}",
                        sequence=expected,
                    )
                )
                integrity = "corrupt"
                break
            events.append(event)
            previous = str(event["event_id"])
        if len(candidates) > MAX_EVENTS:
            problems.append(
                _storage_problem(
                    session_id=str(session["session_id"]),
                    code="work-session.journal-too-large",
                    message="Journal exceeds its event budget; later events were isolated.",
                    sequence=MAX_EVENTS,
                )
            )
            events = events[:MAX_EVENTS]
            integrity = "corrupt"
        return events, problems, integrity

    def _load_locked(self, session_id: str, *, repair_summary: bool) -> dict[str, Any]:
        directory = self._directory(session_id)
        session = self._session(directory)
        events, problems, integrity = self._scan_journal(directory, session)
        summary = _derive_summary(
            session,
            events,
            integrity_state=integrity,
            integrity_problems=problems,
        )
        summary_state = "verified"
        try:
            cached_value, cached_raw = _read_private_json(
                directory / SUMMARY_NAME, "summary cache"
            )
            cached = validate_work_session_summary(cached_value)
            if cached != summary or cached_raw != _canonical_bytes(summary) + b"\n":
                raise WorkSessionError(
                    "work-session.stale-summary", "summary cache differs from the journal"
                )
        except WorkSessionError as exc:
            if exc.code == "work-session.unsafe-path":
                raise
            summary_state = "summary-regenerated"
            if repair_summary:
                _replace_summary(directory / SUMMARY_NAME, summary)
        return {
            "format_version": VIEW_FORMAT,
            "session": session,
            "events": events,
            "summary": summary,
            "integrity": {
                "state": "corrupt" if integrity == "corrupt" else summary_state,
                "journal_state": integrity,
                "summary_state": summary_state,
                "problems": problems,
            },
        }

    def create(
        self,
        *,
        task: Mapping[str, Any],
        workspace: Mapping[str, Any],
        identities: Mapping[str, Any],
        frontend: Mapping[str, Any],
        label: str | None = None,
        lifecycle: str = "discovered",
        limitations: Iterable[str] = (),
        unknowns: Iterable[str] = (),
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a session whose first event and summary are durably published."""

        normalized_task = _normalize_task(task)
        normalized_workspace = _normalize_workspace(workspace, require_present=True)
        normalized_identities = _normalize_identities(identities)
        normalized_frontend = _normalize_frontend(frontend)
        normalized_label = _text(label, "session label", maximum=1024, nullable=True)
        if lifecycle not in FRONTEND_CREATE_LIFECYCLES:
            _fail(
                "work-session.owner-custody-required",
                "new sessions may only begin in frontend-owned non-execution states",
            )
        selected_id = session_id or _make_session_id()
        if type(selected_id) is not str or _SESSION_ID.fullmatch(selected_id) is None:
            _fail("work-session.invalid-record", "session ID is invalid")
        created_at = _timestamp(self._clock(), "created_at")
        header_body: dict[str, Any] = {
            "format_version": SESSION_FORMAT,
            "schema_version": 2,
            "session_id": selected_id,
            "created_at": created_at,
            "label": normalized_label,
            "task": normalized_task,
            "workspace": normalized_workspace,
            "identities": normalized_identities,
            "retention": {
                "journal": JOURNAL_DIRECTORY_NAME,
                "summary": SUMMARY_NAME,
                "append_only": True,
                "summary_disposable": True,
            },
        }
        header = dict(header_body)
        header["session_record_id"] = _content_id("work-session-record", header_body)
        header = _validate_session_record(header)
        directory = self._directory(selected_id, must_exist=False)
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkSessionError(
                "work-session.session-exists", f"Work Session already exists: {selected_id}"
            ) from exc
        try:
            secure_private_path(directory, directory=True)
            journal = directory / JOURNAL_DIRECTORY_NAME
            secure_private_path(journal, directory=True)
            _write_immutable(directory / SESSION_RECORD_NAME, header)
            first = _make_event(
                {
                    "format_version": EVENT_FORMAT,
                    "session_id": selected_id,
                    "session_record_id": header["session_record_id"],
                    "sequence": 0,
                    "previous_event_id": None,
                    "occurred_at": created_at,
                    "frontend": normalized_frontend,
                    "kind": "session-created",
                    "task_id": normalized_task["task_id"],
                    "lifecycle": lifecycle,
                    "stage": None,
                    "action": None,
                    "result_refs": [],
                    "problems": [],
                    "next_actions": [],
                    "owner_record_refs": [],
                    "recovery": None,
                    "workspace_observation": normalized_workspace,
                    "closed": False,
                    "message": None,
                    "limitations": _bounded_strings(list(limitations), "limitations"),
                    "unknowns": _bounded_strings(list(unknowns), "unknowns"),
                }
            )
            _write_immutable(journal / "00000000000000000000.json", first)
            self._fault("after-event-publish")
            summary = _derive_summary(
                header, [first], integrity_state="verified", integrity_problems=[]
            )
            _replace_summary(directory / SUMMARY_NAME, summary)
            fsync_directory(directory)
            # The session is not acknowledged until its directory entry is
            # durable in the parent store as well as its own contents.
            fsync_directory(self.base)
            self._fault("after-summary-publish")
        except Exception:
            # The directory is intentionally retained.  An interrupted or failed
            # creation remains inspectable and is never rewritten as success.
            raise
        return {
            "session_id": selected_id,
            "session_record_id": header["session_record_id"],
            "event": first,
            "summary": summary,
        }

    def open(self, selector: str | None = "latest") -> dict[str, Any]:
        """Open a validated timeline, repairing only its disposable summary."""

        session_id = self._resolve(selector)
        with self._exclusive(session_id):
            return _snapshot(self._load_locked(session_id, repair_summary=True))

    def status(self, selector: str | None = "latest") -> dict[str, Any]:
        """Return the validated disposable summary for Home and native clients."""

        return validate_work_session_summary(self.open(selector)["summary"])

    def timeline(
        self,
        selector: str | None = "latest",
        *,
        after_sequence: int = -1,
        limit: int = 1024,
    ) -> dict[str, Any]:
        if type(after_sequence) is not int or after_sequence < -1:
            _fail("work-session.invalid-range", "after_sequence is invalid")
        if type(limit) is not int or not 1 <= limit <= 4096:
            _fail("work-session.invalid-range", "timeline limit is invalid")
        opened = self.open(selector)
        selected = [
            event for event in opened["events"] if event["sequence"] > after_sequence
        ][:limit]
        latest = opened["summary"]["latest_sequence"]
        next_sequence = after_sequence if not selected else selected[-1]["sequence"]
        return {
            "format_version": "workbench-work-session-timeline-v1",
            "session_id": opened["summary"]["session_id"],
            "after_sequence": after_sequence,
            "events": selected,
            "next_sequence": next_sequence,
            "has_more": next_sequence < latest,
            "integrity": opened["integrity"],
        }

    def _append_event(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        kind: str,
        lifecycle: str | None = None,
        stage: Mapping[str, Any] | None = None,
        action: Mapping[str, Any] | None = None,
        result_refs: Iterable[Mapping[str, Any]] = (),
        problems: Iterable[Mapping[str, Any]] = (),
        next_actions: Iterable[Mapping[str, Any]] = (),
        owner_record_refs: Iterable[Mapping[str, Any]] = (),
        recovery: Mapping[str, Any] | None = None,
        workspace_observation: Mapping[str, Any] | None = None,
        closed: bool | None = None,
        message: str | None = None,
        limitations: Iterable[str] = (),
        unknowns: Iterable[str] = (),
        _terminal_owner_authorized: bool,
        _owner_custody_authorized: bool,
    ) -> dict[str, Any]:
        """Append exactly one internally authorized event after a sequence CAS."""

        session_id = self._resolve(selector)
        if type(expected_sequence) is not int or expected_sequence < 0:
            _fail("work-session.invalid-sequence", "expected sequence must be non-negative")
        normalized_frontend = _normalize_frontend(frontend)
        normalized_kind = _identifier(kind, "event kind")
        with self._exclusive(session_id):
            opened = self._load_locked(session_id, repair_summary=False)
            if opened["integrity"]["journal_state"] != "verified":
                _fail(
                    "work-session.corrupt-journal",
                    "cannot append until non-verified journal state is explicitly recovered",
                )
            summary = opened["summary"]
            observed_sequence = summary["latest_sequence"]
            if observed_sequence != expected_sequence:
                raise WorkSessionConflictError(
                    "work-session.stale-sequence",
                    f"expected sequence {expected_sequence}, observed {observed_sequence}",
                    retryable=True,
                )
            observed_lifecycle = summary["lifecycle"]
            selected_lifecycle = lifecycle or observed_lifecycle
            if selected_lifecycle not in LIFECYCLES:
                _fail("work-session.invalid-lifecycle", "event lifecycle is invalid")
            normalized_stage = _normalize_stage(stage)
            if not _terminal_owner_authorized and (
                selected_lifecycle in TERMINAL_LIFECYCLES
                or (
                    normalized_stage is not None
                    and normalized_stage["state"] in TERMINAL_STAGE_STATES
                )
            ):
                _fail(
                    "work-session.terminal-owner-required",
                    "terminal lifecycle or stage state requires an owner-verified result",
                )
            if not _owner_custody_authorized and (
                selected_lifecycle in {"running", "recoverable"}
                or (
                    normalized_stage is not None
                    and normalized_stage["state"] in {"running", "recoverable"}
                )
            ):
                _fail(
                    "work-session.owner-custody-required",
                    "running or recoverable state requires owner-verified custody",
                )
            if observed_lifecycle in TERMINAL_LIFECYCLES and selected_lifecycle != observed_lifecycle:
                _fail(
                    "work-session.terminal-session",
                    "terminal owner outcome cannot be replaced by Work Session navigation",
                )
            selected_closed = summary["closed"] if closed is None else closed
            if type(selected_closed) is not bool:
                _fail("work-session.invalid-record", "closed must be boolean")
            normalized_results = _normalize_result_refs(list(result_refs))
            normalized_problems = _normalize_problems(list(problems))
            normalized_owners = _normalize_owner_refs(list(owner_record_refs))
            normalized_recovery = _normalize_recovery(recovery)
            if not _owner_custody_authorized:
                if normalized_results:
                    _fail(
                        "work-session.owner-result-required",
                        "navigation append cannot introduce owner result identities",
                    )
                retained_owners = {
                    row["record_id"]: row for row in summary["owner_record_refs"]
                }
                referenced = list(normalized_owners)
                for problem in normalized_problems:
                    referenced.extend(problem["evidence_refs"])
                if normalized_recovery is not None:
                    referenced.extend(normalized_recovery["owner_record_refs"])
                if any(
                    retained_owners.get(row["record_id"]) != row for row in referenced
                ):
                    _fail(
                        "work-session.owner-verifier-required",
                        "navigation append may only carry exact previously verified owner references",
                    )
            session = opened["session"]
            event = _make_event(
                {
                    "format_version": EVENT_FORMAT,
                    "session_id": session_id,
                    "session_record_id": session["session_record_id"],
                    "sequence": observed_sequence + 1,
                    "previous_event_id": summary["latest_event_id"],
                    "occurred_at": _timestamp(self._clock(), "occurred_at"),
                    "frontend": normalized_frontend,
                    "kind": normalized_kind,
                    "task_id": session["task"]["task_id"],
                    "lifecycle": selected_lifecycle,
                    "stage": normalized_stage,
                    "action": None if action is None else _normalize_action(action),
                    "result_refs": normalized_results,
                    "problems": normalized_problems,
                    "next_actions": _normalize_next_actions(list(next_actions)),
                    "owner_record_refs": normalized_owners,
                    "recovery": normalized_recovery,
                    "workspace_observation": (
                        None
                        if workspace_observation is None
                        else _normalize_workspace(
                            workspace_observation, require_present=False
                        )
                    ),
                    "closed": selected_closed,
                    "message": _text(message, "event message", maximum=4096, nullable=True),
                    "limitations": _bounded_strings(list(limitations), "limitations"),
                    "unknowns": _bounded_strings(list(unknowns), "unknowns"),
                }
            )
            journal = self._directory(session_id) / JOURNAL_DIRECTORY_NAME
            _write_immutable(journal / f"{event['sequence']:020d}.json", event)
            self._fault("after-event-publish")
            events = list(opened["events"]) + [event]
            next_summary = _derive_summary(
                session, events, integrity_state="verified", integrity_problems=[]
            )
            _replace_summary(self._directory(session_id) / SUMMARY_NAME, next_summary)
            self._fault("after-summary-publish")
            return _snapshot({"event": event, "summary": next_summary})

    def append(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        kind: str,
        lifecycle: str | None = None,
        stage: Mapping[str, Any] | None = None,
        action: Mapping[str, Any] | None = None,
        result_refs: Iterable[Mapping[str, Any]] = (),
        problems: Iterable[Mapping[str, Any]] = (),
        next_actions: Iterable[Mapping[str, Any]] = (),
        owner_record_refs: Iterable[Mapping[str, Any]] = (),
        recovery: Mapping[str, Any] | None = None,
        workspace_observation: Mapping[str, Any] | None = None,
        closed: bool | None = None,
        message: str | None = None,
        limitations: Iterable[str] = (),
        unknowns: Iterable[str] = (),
        catalog: Any | None = None,
    ) -> dict[str, Any]:
        """Append navigation state; terminal owner claims are not accepted here."""

        supplied_actions = list(next_actions)
        normalized_action = (
            None
            if action is None
            else _normalize_catalog_action(action, catalog=catalog)
        )
        normalized_actions = (
            []
            if not supplied_actions
            else _normalize_catalog_actions(supplied_actions, catalog=catalog)
        )

        return self._append_event(
            selector,
            expected_sequence=expected_sequence,
            frontend=frontend,
            kind=kind,
            lifecycle=lifecycle,
            stage=stage,
            action=normalized_action,
            result_refs=result_refs,
            problems=problems,
            next_actions=normalized_actions,
            owner_record_refs=owner_record_refs,
            recovery=recovery,
            workspace_observation=workspace_observation,
            closed=closed,
            message=message,
            limitations=limitations,
            unknowns=unknowns,
            _terminal_owner_authorized=False,
            _owner_custody_authorized=False,
        )

    def prepare_catalog_action(
        self,
        selector: str,
        *,
        expected_sequence: int,
        catalog: Any,
        expected_catalog_digest: str,
        action_id: str,
        expected_action_digest: str,
        arguments: Mapping[str, Any],
        workspace_observation: Mapping[str, Any],
        execute: bool,
    ) -> dict[str, Any]:
        """Bind one session action to the existing catalog argv composer.

        This is a pre-execution port, not an executor.  The CLI or service must
        hand the returned argv and intent to its existing supervised execution
        path, then retain the resulting live-console/job reference through
        :meth:`bind_owner_execution`.
        """

        if type(execute) is not bool:
            _fail("work-session.invalid-action", "execute must be boolean")
        summary = self.status(selector)
        if summary["latest_sequence"] != expected_sequence:
            raise WorkSessionConflictError(
                "work-session.stale-sequence",
                f"expected sequence {expected_sequence}, observed {summary['latest_sequence']}",
                retryable=True,
            )
        if summary["closed"]:
            _fail("work-session.closed", "closed Work Session navigation must be reopened")
        if summary["integrity_state"] != "verified":
            _fail("work-session.integrity-required", "action preparation requires a verified journal")
        if summary["lifecycle"] in {"running", "recoverable", *TERMINAL_LIFECYCLES}:
            _fail(
                "work-session.execution-active",
                "a Work Session with active, recoverable, or terminal owner custody cannot prepare another action",
            )
        current_workspace = _normalize_workspace(workspace_observation, require_present=True)
        if current_workspace != summary["workspace"]:
            _fail(
                "work-session.stale-workspace",
                "workspace identity, root, revision, or dirty fingerprint changed",
            )
        catalog_digest = getattr(catalog, "catalog_digest", None)
        if (
            type(catalog_digest) is not str
            or catalog_digest != expected_catalog_digest
            or summary["identities"]["catalog_id"] != catalog_digest
        ):
            _fail("work-session.stale-catalog", "catalog identity changed after session review")
        selected = [
            action for action in summary["next_actions"] if action["action_id"] == action_id
        ]
        if len(selected) != 1:
            _fail("work-session.action-unavailable", "action is not an exact eligible session action")
        action = selected[0]
        if action["availability"] not in {"available", "experimental"}:
            _fail(
                "work-session.action-unavailable",
                f"action availability is {action['availability']}",
            )
        if action["action_digest"] != expected_action_digest:
            _fail("work-session.stale-action", "retained action digest changed after review")
        normalized_arguments = _sanitize_argument(dict(arguments))
        if normalized_arguments != action["arguments"]:
            _fail("work-session.stale-action", "action arguments changed after review")
        try:
            command = catalog.command(action_id)
            current_action_digest = command.action_digest(root=catalog.root)
            if current_action_digest != expected_action_digest:
                _fail("work-session.stale-action", "catalog action changed after review")
            if command.risk != action["mutation_budget"]:
                _fail("work-session.stale-action", "catalog action risk changed after review")
            if command.availability != action["availability"]:
                _fail("work-session.stale-action", "catalog action availability changed after review")
            argv, intent = command.build_argv(
                normalized_arguments,
                root=catalog.root,
                execute=execute,
            )
        except WorkSessionError:
            raise
        except Exception as exc:
            raise WorkSessionError(
                "work-session.action-invalid",
                f"existing catalog rejected the retained action: {exc}",
            ) from exc
        body: dict[str, Any] = {
            "format_version": PREPARED_ACTION_FORMAT,
            "session_id": summary["session_id"],
            "session_record_id": summary["session_record_id"],
            "expected_sequence": expected_sequence,
            "catalog_digest": catalog_digest,
            "workspace": current_workspace,
            "action": action,
            "argv": list(argv),
            "intent": intent,
            "execute": execute,
            "shell": False,
        }
        body["preparation_id"] = _content_id("work-session-prepared-action", body)
        return _snapshot(body)

    def bind_owner_artifacts(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        owner_record_refs: Iterable[Mapping[str, Any]],
        owner_reference_verifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Link owner-verified artifacts, never transfer execution custody.

        Saved checks supervise their own attempts. Their sealed outcomes may be
        linked here without promoting this navigation session to an execution.
        """
        current = self.status(selector)
        if current["latest_sequence"] != expected_sequence:
            raise WorkSessionConflictError(
                "work-session.stale-sequence", "session changed before artifact binding",
                retryable=True,
            )
        if current["closed"] or current["lifecycle"] in {
            "running", "recoverable", *TERMINAL_LIFECYCLES
        }:
            _fail("work-session.execution-active", "artifact links require an open navigation session")
        refs = _verify_owner_refs(
            list(owner_record_refs), verifier=owner_reference_verifier,
        )
        for reference in refs:
            states = {"retained-plan", "prepared-not-run"}
            if reference["owner_id"] == "crucible" and reference["record_kind"] == "workbench-saved-check-result-v4":
                states = {"completed", "failed", "cancelled", "timed-out", "inconclusive"}
            elif reference["owner_id"] == "crucible" and reference["record_kind"] == "workbench-check-comparison-v2":
                states = {"compared", "partial", "not-comparable"}
            elif reference["owner_id"] == "axiom" and reference["record_kind"] in {
                "workbench-material-check-result-v1", "workbench-check-snapshot-publication-v1"
            }:
                # Retaining an invocation is not material-validity authority.
                states = {"completed", "incomplete"}
            if reference["last_verified_state"] not in states:
                _fail("work-session.owner-state-invalid", "owner record state is not valid for this binding")
        if not refs:
            _fail("work-session.owner-result-required", "artifact binding requires an owner reference")
        return self._append_event(
            selector, expected_sequence=expected_sequence, frontend=frontend,
            kind="owner-artifacts-retained", owner_record_refs=refs,
            message="Verified owner artifact references; no transfer of execution or qualification authority.",
            _terminal_owner_authorized=False, _owner_custody_authorized=True,
        )

    def bind_owner_execution(
        self,
        prepared: Mapping[str, Any],
        *,
        catalog: Any,
        frontend: Mapping[str, Any],
        owner_record_refs: Iterable[Mapping[str, Any]],
        owner_execution_verifier: Callable[
            [Mapping[str, Any]], Mapping[str, Any]
        ],
    ) -> dict[str, Any]:
        """Bind custody before the existing executor launches the prepared argv.

        A preparation ID is content integrity, not authority.  Revalidate the
        current session and catalog here, win the sequence CAS, and only then
        may the caller hand ``argv`` to the existing executor.  If launch
        subsequently fails, the running event truthfully becomes explicit
        recovery work rather than an invented completion.
        """

        allowed = {
            "format_version",
            "preparation_id",
            "session_id",
            "session_record_id",
            "expected_sequence",
            "catalog_digest",
            "workspace",
            "action",
            "argv",
            "intent",
            "execute",
            "shell",
        }
        row = _exact_mapping(prepared, "prepared action", allowed)
        body = dict(row)
        preparation_id = body.pop("preparation_id", None)
        if (
            row.get("format_version") != PREPARED_ACTION_FORMAT
            or type(preparation_id) is not str
            or preparation_id != _content_id("work-session-prepared-action", body)
            or row.get("shell") is not False
            or row.get("execute") is not True
            or row.get("intent") != "execute"
        ):
            _fail("work-session.tampered-preparation", "prepared action identity changed")
        action = _normalize_action(row.get("action"))
        session_id = str(row.get("session_id"))
        summary = self.status(session_id)
        if (
            summary["session_record_id"] != row.get("session_record_id")
            or summary["latest_sequence"] != row.get("expected_sequence")
            or summary["closed"]
            or summary["integrity_state"] != "verified"
            or summary["workspace"]
            != _normalize_workspace(row.get("workspace"), require_present=True)
        ):
            _fail(
                "work-session.stale-preparation",
                "session or workspace changed before owner custody binding",
            )
        if summary["lifecycle"] in {"running", "recoverable", *TERMINAL_LIFECYCLES}:
            _fail(
                "work-session.execution-active",
                "a Work Session with active, recoverable, or terminal owner custody cannot bind another execution",
            )
        if (
            getattr(catalog, "catalog_digest", None) != row.get("catalog_digest")
            or summary["identities"]["catalog_id"] != row.get("catalog_digest")
        ):
            _fail(
                "work-session.stale-catalog",
                "catalog changed before owner custody binding",
            )
        retained = [
            candidate
            for candidate in summary["next_actions"]
            if candidate["action_id"] == action["action_id"]
        ]
        if retained != [action]:
            _fail(
                "work-session.stale-action",
                "prepared action is no longer the exact eligible session action",
            )
        try:
            command = catalog.command(action["action_id"])
            expected_argv, expected_intent = command.build_argv(
                action["arguments"], root=catalog.root, execute=True
            )
        except Exception as exc:
            raise WorkSessionError(
                "work-session.action-invalid",
                f"existing catalog rejected the prepared execution: {exc}",
            ) from exc
        if (
            command.action_digest(root=catalog.root) != action["action_digest"]
            or command.risk != action["mutation_budget"]
            or command.availability != action["availability"]
            or list(expected_argv) != row.get("argv")
            or expected_intent != "execute"
        ):
            _fail(
                "work-session.stale-action",
                "catalog action changed before owner custody binding",
            )
        from .catalog import redact_argv

        if not callable(owner_execution_verifier):
            _fail(
                "work-session.owner-verifier-required",
                "execution requires an exact owner execution verifier",
            )
        refs: list[dict[str, Any]] = []
        expected_command = {
            "command_id": action["action_id"],
            "argv": redact_argv(expected_argv, command.fields),
            "cwd": summary["workspace"]["canonical_root"],
            "intent": expected_intent,
            "shell": False,
        }
        for supplied in _normalize_owner_refs(list(owner_record_refs)):
            try:
                reproduced = owner_execution_verifier(_snapshot(supplied))
            except WorkSessionError:
                raise
            except Exception as exc:
                raise WorkSessionError(
                    "work-session.owner-verification-failed",
                    f"execution owner could not be verified: {supplied['record_id']}: {exc}",
                ) from exc
            if not isinstance(reproduced, Mapping) or set(reproduced) != {
                "owner_record_ref", "command"
            }:
                _fail(
                    "work-session.owner-verification-invalid",
                    "execution owner did not reproduce command custody",
                )
            verified = _verify_owner_refs(
                [supplied],
                verifier=lambda _row, value=reproduced["owner_record_ref"]: value,
                allowed_states=EXECUTION_CUSTODY_STATES,
            )[0]
            if reproduced["command"] != expected_command:
                _fail(
                    "work-session.owner-execution-drift",
                    "owner command custody differs from the prepared action",
                )
            refs.append(verified)
        if not refs:
            _fail(
                "work-session.owner-custody-required",
                "execution requires one or more digest-bound, verified owner custody references",
            )
        return self._append_event(
            session_id,
            expected_sequence=row["expected_sequence"],
            frontend=frontend,
            kind="owner-execution-bound",
            lifecycle="running",
            stage={"stage_id": "owner-execution", "state": "running"},
            action=action,
            owner_record_refs=refs,
            message="Owner custody was bound before the existing executor launch boundary.",
            _terminal_owner_authorized=False,
            _owner_custody_authorized=True,
        )

    def bind_owner_result(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        lifecycle: str,
        stage_id: str,
        result_refs: Iterable[Mapping[str, Any]],
        owner_result_verifier: Callable[
            [Mapping[str, Any]], Mapping[str, Any]
        ],
        problems: Iterable[Mapping[str, Any]] = (),
        message: str | None = None,
    ) -> dict[str, Any]:
        """Bind a terminal outcome only after its owner verifies exact results."""

        if lifecycle not in TERMINAL_LIFECYCLES:
            _fail(
                "work-session.invalid-lifecycle",
                "owner result lifecycle must be complete, failed, or cancelled",
            )
        normalized_results = _normalize_result_refs(list(result_refs))
        if not normalized_results:
            _fail(
                "work-session.owner-result-required",
                "terminal lifecycle requires one or more owner result references",
            )
        verified_results: list[dict[str, Any]] = []
        verified_owners: dict[str, dict[str, Any]] = {}
        current = self.status(selector)
        if current["latest_sequence"] != expected_sequence:
            raise WorkSessionConflictError(
                "work-session.stale-sequence",
                f"expected sequence {expected_sequence}, observed {current['latest_sequence']}",
                retryable=True,
            )
        if current["lifecycle"] not in {"running", "recoverable"}:
            _fail(
                "work-session.owner-lineage-required",
                "terminal owner result requires prior running or recoverable custody",
            )
        retained_identities = {
            (
                row["owner_id"], row["record_id"], row["record_kind"], row["uri"]
            )
            for row in current["owner_record_refs"]
        }
        if not callable(owner_result_verifier):
            _fail(
                "work-session.owner-verifier-required",
                "terminal results require an exact owner result verifier",
            )
        for result in normalized_results:
            try:
                reproduced = _normalize_result_ref(
                    owner_result_verifier(_snapshot(result))
                )
            except WorkSessionError:
                raise
            except Exception as exc:
                raise WorkSessionError(
                    "work-session.owner-verification-failed",
                    f"owner result could not be verified: {result['result_id']}: {exc}",
                ) from exc
            if reproduced["result_id"] != result["result_id"]:
                _fail(
                    "work-session.owner-result-drift",
                    "owner verifier did not reproduce the exact result identity",
                )
            supplied_owner = result["owner_record_ref"]
            reproduced_owner = reproduced["owner_record_ref"]
            identity_fields = ("owner_id", "record_id", "record_kind", "uri")
            if any(
                supplied_owner[field] != reproduced_owner[field]
                for field in identity_fields
            ):
                _fail(
                    "work-session.owner-identity-drift",
                    "owner verifier changed a retained result identity",
                )
            if (
                supplied_owner["digest"] is not None
                and supplied_owner["digest"] != reproduced_owner["digest"]
            ):
                _fail(
                    "work-session.owner-digest-drift",
                    "owner result digest changed before it could be bound",
                )
            if (
                not isinstance(reproduced_owner["digest"], str)
                or _DIGEST.fullmatch(reproduced_owner["digest"]) is None
                or reproduced_owner["last_verified_state"] != lifecycle
                or reproduced_owner["verified_at"] is None
            ):
                _fail(
                    "work-session.owner-state-invalid",
                    "owner did not reproduce a digest-bound terminal result",
                )
            reproduced_identity = (
                reproduced_owner["owner_id"],
                reproduced_owner["record_id"],
                reproduced_owner["record_kind"],
                reproduced_owner["uri"],
            )
            if reproduced_identity not in retained_identities:
                _fail(
                    "work-session.owner-lineage-required",
                    "terminal result owner does not match prior execution custody",
                )
            verified_results.append(reproduced)
            verified_owners[reproduced_owner["record_id"]] = reproduced_owner
        verified_identities = {
            (row["owner_id"], row["record_id"], row["record_kind"], row["uri"])
            for row in verified_owners.values()
        }
        if verified_identities != retained_identities:
            _fail(
                "work-session.owner-lineage-incomplete",
                "terminal lifecycle requires exact terminal results for every retained execution owner",
            )
        return self._append_event(
            selector,
            expected_sequence=expected_sequence,
            frontend=frontend,
            kind="owner-result-bound",
            lifecycle=lifecycle,
            stage={"stage_id": stage_id, "state": lifecycle},
            result_refs=verified_results,
            problems=problems,
            owner_record_refs=[
                verified_owners[key] for key in sorted(verified_owners)
            ],
            message=(
                message
                or "Terminal lifecycle was reproduced from exact owner-verified result references."
            ),
            _terminal_owner_authorized=True,
            _owner_custody_authorized=True,
        )

    def mark_owner_recoverable(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        reason: str,
        owner_record_refs: Iterable[Mapping[str, Any]],
        owner_reference_verifier: Callable[
            [Mapping[str, Any]], Mapping[str, Any]
        ],
        next_actions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Retain recovery only after its owner reproduces uncertain custody."""

        refs = _verify_owner_refs(
            list(owner_record_refs),
            verifier=owner_reference_verifier,
            allowed_states=frozenset(
                {
                    "allocated",
                    "starting",
                    "running",
                    "incomplete",
                    "complete",
                    "failed",
                    "cancelled",
                }
            ),
        )
        current = self.status(selector)
        if current["latest_sequence"] != expected_sequence:
            raise WorkSessionConflictError(
                "work-session.stale-sequence",
                f"expected sequence {expected_sequence}, observed {current['latest_sequence']}",
                retryable=True,
            )
        if current["lifecycle"] in {"running", "recoverable"}:
            retained_identities = {
                (
                    row["owner_id"],
                    row["record_id"],
                    row["record_kind"],
                    row["uri"],
                )
                for row in current["owner_record_refs"]
            }
            if any(
                (
                    row["owner_id"],
                    row["record_id"],
                    row["record_kind"],
                    row["uri"],
                )
                not in retained_identities
                for row in refs
            ):
                _fail(
                    "work-session.owner-lineage-required",
                    "recovery custody must match the active owner execution",
                )
        actions = list(next_actions)
        if not actions:
            _fail(
                "work-session.invalid-recovery",
                "owner recovery requires one exact developer-selectable safe action",
            )
        return self._append_event(
            selector,
            expected_sequence=expected_sequence,
            frontend=frontend,
            kind="recovery-required",
            lifecycle="recoverable",
            owner_record_refs=refs,
            next_actions=actions,
            recovery={
                "state": "required",
                "reason": reason,
                "owner_record_refs": refs,
                "safe_action_ids": [str(row.get("action_id")) for row in actions],
            },
            message="Recovery requires an explicit developer-selected safe action.",
            _terminal_owner_authorized=False,
            _owner_custody_authorized=True,
        )

    def reopen(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        workspace_observation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = self.status(selector)
        carried_actions = status["next_actions"]
        if workspace_observation is not None:
            normalized_workspace = _normalize_workspace(
                workspace_observation, require_present=False
            )
            if normalized_workspace != status["workspace"]:
                carried_actions = [
                    {**action, "availability": "stale"}
                    for action in carried_actions
                ]
        values: dict[str, Any] = {
            "expected_sequence": expected_sequence,
            "frontend": frontend,
            "kind": "frontend-reopened",
            "next_actions": carried_actions,
            "owner_record_refs": status["owner_record_refs"],
            "recovery": status["recovery"],
            "workspace_observation": workspace_observation,
            "closed": False,
            "message": "Work Session navigation reopened; no owner action was executed.",
        }
        if status["lifecycle"] in TERMINAL_LIFECYCLES:
            values.update({
                "lifecycle": status["lifecycle"],
                "stage": {
                    "stage_id": "retained-owner-result",
                    "state": status["lifecycle"],
                },
                "result_refs": status["result_refs"],
                "_terminal_owner_authorized": True,
                "_owner_custody_authorized": True,
            })
            return self._append_event(selector, **values)
        if status["lifecycle"] in {"running", "recoverable"}:
            values.update({
                "lifecycle": status["lifecycle"],
                "_terminal_owner_authorized": False,
                "_owner_custody_authorized": True,
            })
            return self._append_event(selector, **values)
        return self._append_event(
            selector,
            **values,
            _terminal_owner_authorized=False,
            _owner_custody_authorized=False,
        )

    def close(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
    ) -> dict[str, Any]:
        status = self.status(selector)
        values: dict[str, Any] = {
            "expected_sequence": expected_sequence,
            "frontend": frontend,
            "kind": "session-closed",
            "next_actions": status["next_actions"],
            "owner_record_refs": status["owner_record_refs"],
            "recovery": status["recovery"],
            "closed": True,
            "message": "Work Session navigation closed; owner records were not changed.",
        }
        if status["lifecycle"] in TERMINAL_LIFECYCLES:
            values.update({
                "lifecycle": status["lifecycle"],
                "stage": {
                    "stage_id": "retained-owner-result",
                    "state": status["lifecycle"],
                },
                "result_refs": status["result_refs"],
                "_terminal_owner_authorized": True,
                "_owner_custody_authorized": True,
            })
            return self._append_event(selector, **values)
        if status["lifecycle"] in {"running", "recoverable"}:
            values.update({
                "lifecycle": status["lifecycle"],
                "_terminal_owner_authorized": False,
                "_owner_custody_authorized": True,
            })
            return self._append_event(selector, **values)
        return self._append_event(
            selector,
            **values,
            _terminal_owner_authorized=False,
            _owner_custody_authorized=False,
        )

    def mark_recoverable(
        self,
        selector: str,
        *,
        expected_sequence: int,
        frontend: Mapping[str, Any],
        reason: str,
        owner_record_refs: Iterable[Mapping[str, Any]],
        next_actions: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        status = self.status(selector)
        retained = {
            row["record_id"]: row for row in status["owner_record_refs"]
        }
        refs = _normalize_owner_refs(list(owner_record_refs))
        if any(retained.get(row["record_id"]) != row for row in refs):
            _fail(
                "work-session.owner-verifier-required",
                "navigation recovery may only carry prior verified owner custody",
            )
        actions = list(next_actions)
        if not actions:
            _fail(
                "work-session.invalid-recovery",
                "navigation recovery requires one exact safe action",
            )
        safe_action_ids = [str(action.get("action_id")) for action in actions]
        return self._append_event(
            selector,
            expected_sequence=expected_sequence,
            frontend=frontend,
            kind="recovery-required",
            lifecycle="recoverable",
            owner_record_refs=refs,
            next_actions=actions,
            recovery={
                "state": "required",
                "reason": reason,
                "owner_record_refs": refs,
                "safe_action_ids": safe_action_ids,
            },
            message="Recovery requires an explicit developer-selected safe action.",
            _terminal_owner_authorized=False,
            _owner_custody_authorized=True,
        )

    def preview_recovery(
        self,
        selector: str | None = "latest",
        *,
        owner_reference_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return recovery facts without appending, executing, or cleaning."""

        opened = self.open(selector)
        summary = opened["summary"]
        recovery = summary["recovery"]
        implicit_interruption = summary["lifecycle"] in {"running", "incomplete"}
        required = (
            opened["integrity"]["journal_state"] != "verified"
            or implicit_interruption
            or (recovery is not None and recovery["state"] != "resolved")
        )
        safe_actions = []
        if recovery is not None:
            allowed = set(recovery["safe_action_ids"])
            safe_actions = [
                action
                for action in summary["next_actions"]
                if action["action_id"] in allowed
            ]
        selected_refs = (
            recovery["owner_record_refs"]
            if recovery is not None
            else summary["owner_record_refs"]
        )
        resolved_refs: list[dict[str, Any]] = []
        resolution_problems: list[dict[str, str]] = []
        if owner_reference_resolver is None:
            resolved_refs = list(selected_refs)
            resolution_state = "not-requested"
        else:
            if not callable(owner_reference_resolver):
                _fail("work-session.invalid-port", "owner reference resolver must be callable")
            for owner_ref in selected_refs:
                try:
                    resolution = owner_reference_resolver(dict(owner_ref))
                    verified_transition = False
                    if (
                        isinstance(resolution, Mapping)
                        and set(resolution) == {
                            "owner_record_ref",
                            "prior_digest",
                            "transition",
                        }
                        and resolution.get("transition") == "verified-owner-revision"
                    ):
                        if resolution.get("prior_digest") != owner_ref["digest"]:
                            _fail(
                                "work-session.owner-digest-drift",
                                "owner revision verifier did not bind the retained digest",
                            )
                        refreshed = _normalize_owner_ref(
                            resolution.get("owner_record_ref")
                        )
                        allowed = OWNER_REVISION_TRANSITIONS.get(
                            str(owner_ref["last_verified_state"]), frozenset()
                        )
                        if refreshed["last_verified_state"] not in allowed:
                            _fail(
                                "work-session.owner-state-invalid",
                                "owner revision verifier reported an invalid state transition",
                            )
                        verified_transition = True
                    else:
                        refreshed = _normalize_owner_ref(resolution)
                    identity_fields = ("owner_id", "record_id", "record_kind", "uri")
                    if any(refreshed[key] != owner_ref[key] for key in identity_fields):
                        _fail(
                            "work-session.owner-identity-drift",
                            "owner resolver changed a retained custody identity",
                        )
                    if (
                        refreshed["digest"] != owner_ref["digest"]
                        and not verified_transition
                    ):
                        refreshed["last_verified_state"] = "stale"
                        resolution_problems.append(
                            {
                                "record_id": str(owner_ref["record_id"]),
                                "message": (
                                    "owner record digest changed after the custody "
                                    "reference was retained"
                                ),
                            }
                        )
                except Exception as exc:
                    resolved_refs.append(dict(owner_ref))
                    resolution_problems.append(
                        {
                            "record_id": str(owner_ref["record_id"]),
                            "message": str(exc)[:2048].replace("\r", " ").replace("\n", " "),
                        }
                    )
                else:
                    resolved_refs.append(refreshed)
            resolution_state = "partial" if resolution_problems else "verified"
        if resolution_problems:
            required = True
        reason = (
            recovery["reason"]
            if recovery is not None
            else (
                "One or more retained owner records changed or could not be verified; inspect custody before choosing recovery."
                if resolution_problems
                else (
                    "The journal ended while work was running or incomplete; inspect referenced owner custody before choosing recovery."
                    if required
                    else None
                )
            )
        )
        if required and not safe_actions:
            safe_actions = [work_session_recovery_action(summary["session_id"])]
        return {
            "format_version": RECOVERY_PREVIEW_FORMAT,
            "session_id": summary["session_id"],
            "session_record_id": summary["session_record_id"],
            "latest_sequence": summary["latest_sequence"],
            "required": required,
            "automatic": False,
            "reason": reason,
            "owner_record_refs": resolved_refs,
            "owner_resolution": resolution_state,
            "owner_resolution_problems": resolution_problems,
            "safe_actions": safe_actions,
            "integrity": opened["integrity"],
        }

    def regenerate_summary(self, selector: str | None = "latest") -> dict[str, Any]:
        session_id = self._resolve(selector)
        with self._exclusive(session_id):
            opened = self._load_locked(session_id, repair_summary=False)
            _replace_summary(self._directory(session_id) / SUMMARY_NAME, opened["summary"])
            return validate_work_session_summary(opened["summary"])

    def list(self) -> list[dict[str, Any]]:
        """List validated summaries; isolate malformed sessions as visible rows."""

        if not self.base.is_dir() or self.base.is_symlink():
            return []
        rows: list[dict[str, Any]] = []
        for directory in self.base.iterdir():
            if directory.name == LOCK_DIRECTORY_NAME:
                continue
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                if _SESSION_ID.fullmatch(directory.name) is None:
                    continue
                with self._exclusive(directory.name):
                    rows.append(self._load_locked(directory.name, repair_summary=True)["summary"])
            except WorkSessionError as exc:
                # Header corruption cannot safely supply task/workspace identity.
                # Keep the row visible without manufacturing those identities.
                rows.append(
                    {
                        "format_version": SUMMARY_FORMAT,
                        "session_id": directory.name,
                        "session_record_id": None,
                        "lifecycle": "incomplete",
                        "created_at": None,
                        "updated_at": None,
                        "closed": False,
                        "integrity_state": "corrupt",
                        "error": {"code": exc.code, "message": str(exc)[:2048]},
                    }
                )
        rows.sort(
            key=lambda row: (str(row.get("updated_at") or ""), str(row["session_id"])),
            reverse=True,
        )
        return _snapshot(rows)


__all__ = [
    "EVENT_FORMAT",
    "JOURNAL_DIRECTORY_NAME",
    "PREPARED_ACTION_FORMAT",
    "RECOVERY_PREVIEW_FORMAT",
    "SESSION_FORMAT",
    "SESSION_RECORD_NAME",
    "SUMMARY_FORMAT",
    "SUMMARY_NAME",
    "VIEW_FORMAT",
    "WorkSessionConflictError",
    "WorkSessionError",
    "WorkSessionStore",
    "validate_work_session_event",
    "validate_work_session_record",
    "validate_work_session_summary",
    "work_session_recovery_action",
]
