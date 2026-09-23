"""Compact current-state and retained-lineage view for developer features."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, NoReturn
from urllib.parse import urlparse
from urllib.request import url2pathname

from .developer_feature import DeveloperFeatureError
from .developer_feature_presentation import (
    COLLECTIONS,
    FAMILIES,
    _KIND_FAMILIES,
    discover_feature_records,
    present_feature_record,
)


FORMAT = "workbench-developer-feature-transaction-view-v1"
KIND = "workbench-developer-feature-transaction-view"
_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_OPERATIONS = 64
_MAX_RECORDS = 4096
_RECORD_STATES = {
    "plans": {
        "material-fluid-recipe": {"experimental-ready"},
        "recipe-change": {"experimental-ready"},
        "quest-for-process": {"experimental-ready-runtime-unverified"},
    },
    "receipts": {
        family: {"applied", "rejected"} for family in FAMILIES
    },
    "rollbacks": {
        family: {"rejected", "restored"} for family in FAMILIES
    },
    "recoveries": {
        family: {"applied", "restored", "review-required"}
        for family in FAMILIES
    },
    "runs": {
        "material-fluid-recipe": {"complete", "incomplete"},
        "recipe-change": {"complete", "incomplete"},
    },
}


def _fail(message: str) -> NoReturn:
    raise DeveloperFeatureError(message)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _seal(body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(body),
        "id": f"{KIND}:sha256:{sha256(_canonical_bytes(body)).hexdigest()}",
    }


def _workspace(value: Any) -> Path:
    if type(value) is not str:
        _fail("developer feature transaction view lacks a workspace URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail("developer feature transaction workspace must be a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        _fail("developer feature transaction workspace must be absolute")
    try:
        resolved = path.resolve(strict=True)
        row = path.lstat()
    except OSError as exc:
        raise DeveloperFeatureError(
            f"developer feature transaction workspace is unavailable: {exc}"
        ) from exc
    if resolved != path or stat.S_ISLNK(row.st_mode) or not stat.S_ISDIR(row.st_mode):
        _fail("developer feature transaction workspace is not canonical")
    return resolved


def _relative_path(value: Any) -> PurePosixPath:
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        _fail("developer feature transaction operation path is unsafe")
    result = PurePosixPath(value)
    if result.is_absolute() or any(part in {"", ".", ".."} for part in result.parts):
        _fail("developer feature transaction operation path is unsafe")
    return result


def _target_state(root: Path, operation: Mapping[str, Any]) -> dict[str, Any]:
    relative = _relative_path(operation.get("path"))
    target = root.joinpath(*relative.parts)
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            row = current.lstat()
        except OSError as exc:
            return {
                "actual_sha256": None,
                "actual_size": None,
                "ordinal": operation["ordinal"],
                "path": operation["path"],
                "state": "drifted",
                "reason": f"ancestor unavailable: {exc}",
            }
        if stat.S_ISLNK(row.st_mode) or not stat.S_ISDIR(row.st_mode):
            return {
                "actual_sha256": None,
                "actual_size": None,
                "ordinal": operation["ordinal"],
                "path": operation["path"],
                "state": "drifted",
                "reason": "ancestor is not an ordinary directory",
            }
    try:
        before = target.lstat()
    except FileNotFoundError:
        state = "matches-before" if operation.get("before_base64") is None else "drifted"
        return {
            "actual_sha256": None,
            "actual_size": None,
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "reason": None if state == "matches-before" else "target is missing",
            "state": state,
        }
    except OSError as exc:
        return {
            "actual_sha256": None,
            "actual_size": None,
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "reason": f"target unavailable: {exc}",
            "state": "drifted",
        }
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        return {
            "actual_sha256": None,
            "actual_size": None,
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "reason": "target is not an ordinary file",
            "state": "drifted",
        }
    expected_sizes = {operation.get("before_size"), operation.get("after_size")}
    if before.st_size not in expected_sizes:
        return {
            "actual_sha256": None,
            "actual_size": before.st_size,
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "reason": "target size matches neither reviewed state",
            "state": "drifted",
        }
    descriptor = -1
    try:
        descriptor = os.open(
            target,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        opened = os.fstat(descriptor)
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            or not stat.S_ISREG(opened.st_mode)
        ):
            _fail("developer feature transaction target changed while opening")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                _fail("developer feature transaction target changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            _fail("developer feature transaction target changed while reading")
    except OSError as exc:
        raise DeveloperFeatureError(
            f"cannot read developer feature transaction target: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    digest = sha256(raw).hexdigest()
    if (
        opened.st_size == operation.get("before_size")
        and digest == operation.get("before_sha256")
    ):
        state = "matches-before"
    elif (
        opened.st_size == operation.get("after_size")
        and digest == operation.get("after_sha256")
    ):
        state = "matches-after"
    else:
        state = "drifted"
    return {
        "actual_sha256": digest,
        "actual_size": opened.st_size,
        "ordinal": operation["ordinal"],
        "path": operation["path"],
        "reason": None if state != "drifted" else "target digest matches neither reviewed state",
        "state": state,
    }


def _workspace_match(presentation: Mapping[str, Any]) -> dict[str, Any]:
    operations = presentation.get("operations")
    if type(operations) is not list or not 1 <= len(operations) <= _MAX_OPERATIONS:
        _fail("developer feature transaction operation set is unsupported")
    try:
        root = _workspace(presentation.get("workspace_uri"))
    except DeveloperFeatureError as exc:
        return {
            "operations": [],
            "reason": str(exc)[:4096],
            "state": "unavailable",
        }
    rows = [_target_state(root, operation) for operation in operations]
    observed = {row["state"] for row in rows}
    if observed == {"matches-before"}:
        state = "matches-before"
    elif observed == {"matches-after"}:
        state = "matches-after"
    elif observed <= {"matches-before", "matches-after"}:
        state = "mixed"
    else:
        state = "drifted"
    return {"operations": rows, "reason": None, "state": state}


def _current_state(
    workspace_match: Mapping[str, Any],
    records: list[Mapping[str, Any]],
    *,
    interrupted: bool = False,
) -> str:
    if interrupted:
        return "interrupted"
    match = workspace_match["state"]
    has_receipt = any(
        row["collection"] == "receipts" and row["record_state"] == "applied"
        for row in records
    )
    has_restoration = any(
        row["collection"] in {"rollbacks", "recoveries"}
        and row["record_state"] in {"restored", "rolled-back"}
        or row["collection"] == "receipts"
        and row["record_state"] == "rejected"
        and row.get("diagnostic_code")
        == "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
        for row in records
    )
    if match == "matches-before":
        return "restored" if has_restoration else "planned"
    if match == "matches-after":
        return "applied" if has_receipt else "matches-after-without-receipt"
    return str(match)


def _actions(
    plan_id: str,
    workspace_match: Mapping[str, Any],
    records: list[Mapping[str, Any]],
    plan_actions: list[Mapping[str, Any]],
    plan_freshness: Mapping[str, Any],
) -> list[dict[str, Any]]:
    current = workspace_match["state"]
    ready = plan_freshness.get("state") == "ready"
    receipt_ids = sorted(
        row["record_id"]
        for row in records
        if row["collection"] == "receipts" and row["record_state"] == "applied"
    )
    recovery = next(
        (row for row in plan_actions if row.get("action") == "recover"),
        {"available": False, "reason": "no interrupted transaction is retained"},
    )
    recovery_available = recovery.get("available") is True
    apply_available = current == "matches-before" and ready and not recovery_available
    rollback_available = (
        current == "matches-after"
        and len(receipt_ids) == 1
        and not recovery_available
    )
    return [
        {
            "action": "check",
            "available": True,
            "consent_id": None,
            "record_id": plan_id,
            "reason": None,
        },
        {
            "action": "apply",
            "available": apply_available,
            "consent_id": plan_id if apply_available else None,
            "record_id": plan_id,
            "reason": (
                None
                if apply_available
                else "an interrupted transaction must be recovered before applying"
                if recovery_available
                else "plan is stale"
                if not ready
                else "workspace does not match reviewed before bytes"
            ),
        },
        {
            "action": "rollback",
            "available": rollback_available,
            "consent_id": None,
            "record_id": receipt_ids[0] if rollback_available else None,
            "reason": (
                None
                if rollback_available
                else "an interrupted transaction must be recovered before rolling back"
                if recovery_available
                else "workspace does not match reviewed after bytes"
                if current != "matches-after"
                else "rollback requires one unambiguous retained applied receipt"
            ),
        },
        {
            "action": "recover",
            "available": recovery_available,
            "consent_id": None,
            "record_id": plan_id,
            "reason": recovery.get("reason"),
        },
    ]


def build_feature_transaction_view(
    suite_root: Path | str,
    state_root: Path | str,
    *,
    family: str,
    plan_reference: Path | str,
) -> dict[str, Any]:
    """Build one compact, current-state view without changing owner records."""

    if family not in FAMILIES:
        _fail("developer feature transaction family is unsupported")
    plan = present_feature_record(
        suite_root,
        state_root,
        family=family,
        collection="plans",
        reference=plan_reference,
    )
    catalog = discover_feature_records(
        suite_root,
        state_root,
        family=family,
    )
    records = [
        dict(row) for row in catalog["records"] if row["plan_id"] == plan["plan_id"]
    ]
    if not any(
        row["collection"] == "plans" and row["record_id"] == plan["plan_id"]
        for row in records
    ):
        _fail("selected plan is not retained in the selected feature state root")
    match = _workspace_match(plan)
    recovery_available = any(
        row.get("action") == "recover" and row.get("available") is True
        for row in plan["actions"]
    )
    current = _current_state(match, records, interrupted=recovery_available)
    compact_operations = [
        {
            "after_sha256": operation["after_sha256"],
            "after_size": operation["after_size"],
            "before_sha256": operation["before_sha256"],
            "before_size": operation["before_size"],
            "diff": operation["diff"],
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "role": operation["role"],
        }
        for operation in plan["operations"]
    ]
    body = {
        "actions": _actions(
            plan["plan_id"],
            match,
            records,
            plan["actions"],
            plan["verification"],
        ),
        "current_effective_state": current,
        "family": family,
        "format": FORMAT,
        "kind": KIND,
        "limitations": list(
            dict.fromkeys(
                [
                    *catalog["limitations"],
                    (
                        "Retained records are immutable content identities; current "
                        "effective state is derived separately from live workspace bytes."
                    ),
                    (
                        "The retained record set is grouped by plan identity and does "
                        "not encode wall-clock chronology."
                    ),
                    (
                        "Workspace match and action availability are point-in-time "
                        "observations; every mutating owner command revalidates under "
                        "its transaction lock."
                    ),
                    (
                        "This compact view omits operation Base64; reopen the owner "
                        "presentation for exact byte custody."
                    ),
                ]
            )
        ),
        "operations": compact_operations,
        "plan_freshness": plan["verification"],
        "plan_id": plan["plan_id"],
        "records": records,
        "schema_version": 1,
        "workspace_match": match,
        "workspace_uri": plan["workspace_uri"],
    }
    return validate_feature_transaction_view(_seal(body))


def validate_feature_transaction_view(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact compact transaction view envelope."""

    if type(value) is not dict:
        _fail("developer feature transaction view must be one ordinary object")
    result = dict(value)
    body = dict(result)
    supplied = body.pop("id", None)
    fields = {
        "actions",
        "current_effective_state",
        "family",
        "format",
        "id",
        "kind",
        "limitations",
        "operations",
        "plan_freshness",
        "plan_id",
        "records",
        "schema_version",
        "workspace_match",
        "workspace_uri",
    }
    if (
        set(result) != fields
        or result.get("format") != FORMAT
        or result.get("kind") != KIND
        or result.get("schema_version") != 1
        or type(result.get("schema_version")) is bool
        or type(supplied) is not str
        or supplied != _seal(body)["id"]
        or result.get("family") not in FAMILIES
        or type(result.get("plan_id")) is not str
        or _CONTENT_ID.fullmatch(result["plan_id"]) is None
        or type(result.get("workspace_uri")) is not str
        or not result["workspace_uri"].startswith("file:")
        or result.get("current_effective_state")
        not in {
            "applied",
            "drifted",
            "interrupted",
            "matches-after-without-receipt",
            "mixed",
            "planned",
            "restored",
            "unavailable",
        }
        or type(result.get("limitations")) is not list
        or not all(type(row) is str and row for row in result["limitations"])
        or type(result.get("operations")) is not list
        or not 1 <= len(result["operations"]) <= _MAX_OPERATIONS
        or type(result.get("records")) is not list
        or len(result["records"]) > _MAX_RECORDS
        or type(result.get("actions")) is not list
    ):
        _fail("developer feature transaction view identity or shape changed")
    operation_fields = {
        "after_sha256",
        "after_size",
        "before_sha256",
        "before_size",
        "diff",
        "ordinal",
        "path",
        "role",
    }
    for ordinal, operation in enumerate(result["operations"]):
        if (
            type(operation) is not dict
            or set(operation) != operation_fields
            or operation.get("ordinal") != ordinal
            or type(operation.get("path")) is not str
            or type(operation.get("role")) is not str
            or not operation["role"]
            or type(operation.get("diff")) is not str
            or type(operation.get("before_sha256")) is not str
            or _DIGEST.fullmatch(operation["before_sha256"]) is None
            or type(operation.get("after_sha256")) is not str
            or _DIGEST.fullmatch(operation["after_sha256"]) is None
            or type(operation.get("before_size")) is not int
            or type(operation.get("before_size")) is bool
            or operation["before_size"] < 0
            or type(operation.get("after_size")) is not int
            or type(operation.get("after_size")) is bool
            or operation["after_size"] < 0
        ):
            _fail("developer feature transaction operation changed")
        _relative_path(operation["path"])
    match = result.get("workspace_match")
    if (
        type(match) is not dict
        or set(match) != {"operations", "reason", "state"}
        or match.get("state")
        not in {"matches-before", "matches-after", "mixed", "drifted", "unavailable"}
        or type(match.get("operations")) is not list
        or match.get("reason") is not None
        and type(match.get("reason")) is not str
    ):
        _fail("developer feature transaction workspace match changed")
    if match["state"] == "unavailable":
        if match["operations"] != [] or not match["reason"]:
            _fail("developer feature unavailable workspace match changed")
    elif len(match["operations"]) != len(result["operations"]):
        _fail("developer feature transaction workspace operation set changed")
    workspace_operation_fields = {
        "actual_sha256",
        "actual_size",
        "ordinal",
        "path",
        "reason",
        "state",
    }
    for ordinal, operation in enumerate(match["operations"]):
        compact = result["operations"][ordinal]
        if (
            type(operation) is not dict
            or set(operation) != workspace_operation_fields
            or operation.get("ordinal") != ordinal
            or operation.get("path") != compact["path"]
            or operation.get("state")
            not in {"matches-before", "matches-after", "drifted"}
            or operation.get("actual_sha256") is not None
            and (
                type(operation.get("actual_sha256")) is not str
                or _DIGEST.fullmatch(operation["actual_sha256"]) is None
            )
            or operation.get("actual_size") is not None
            and (
                type(operation.get("actual_size")) is not int
                or type(operation.get("actual_size")) is bool
                or operation["actual_size"] < 0
            )
            or operation.get("reason") is not None
            and (type(operation.get("reason")) is not str or not operation["reason"])
        ):
            _fail("developer feature transaction workspace operation changed")
        if operation["state"] == "matches-before" and (
            operation["actual_sha256"] != compact["before_sha256"]
            or operation["actual_size"] != compact["before_size"]
        ):
            _fail("developer feature transaction before-byte match changed")
        if operation["state"] == "matches-after" and (
            operation["actual_sha256"] != compact["after_sha256"]
            or operation["actual_size"] != compact["after_size"]
        ):
            _fail("developer feature transaction after-byte match changed")
        if operation["state"] != "drifted" and operation["reason"] is not None:
            _fail("developer feature transaction matched-byte reason changed")
    if match["state"] != "unavailable":
        observed = {row["state"] for row in match["operations"]}
        derived_match = (
            "matches-before"
            if observed == {"matches-before"}
            else "matches-after"
            if observed == {"matches-after"}
            else "mixed"
            if observed <= {"matches-before", "matches-after"}
            else "drifted"
        )
        if match["state"] != derived_match or match["reason"] is not None:
            _fail("developer feature transaction aggregate workspace match changed")

    record_fields = {
        "collection",
        "diagnostic_code",
        "family",
        "operation_count",
        "plan_id",
        "record_id",
        "record_kind",
        "record_state",
        "reference",
        "uri",
        "verification_state",
        "workspace_uri",
    }
    observed_records: set[tuple[str, str]] = set()
    for row in result["records"]:
        if (
            type(row) is not dict
            or set(row) != record_fields
            or row.get("family") != result["family"]
            or row.get("collection") not in COLLECTIONS
            or type(row.get("record_id")) is not str
            or _CONTENT_ID.fullmatch(row["record_id"]) is None
            or row.get("reference") != row["record_id"]
            or type(row.get("record_kind")) is not str
            or not row["record_kind"]
            or _KIND_FAMILIES[row["collection"]].get(row["record_kind"])
            != row["family"]
            or not row["record_id"].startswith(f"{row['record_kind']}:sha256:")
            or type(row.get("record_state")) is not str
            or row["record_state"]
            not in _RECORD_STATES[row["collection"]].get(row["family"], set())
            or row.get("plan_id") != result["plan_id"]
            or type(row.get("operation_count")) is not int
            or type(row.get("operation_count")) is bool
            or row["operation_count"] != len(result["operations"])
            or row.get("verification_state") not in {"ready", "stale"}
            or type(row.get("uri")) is not str
            or not row["uri"].startswith("file:")
            or row.get("workspace_uri") != result["workspace_uri"]
            or row.get("diagnostic_code") is not None
            and type(row.get("diagnostic_code")) is not str
        ):
            _fail("developer feature transaction retained record changed")
        key = (row["collection"], row["record_id"])
        if key in observed_records:
            _fail("developer feature transaction contains a duplicate record")
        observed_records.add(key)
    expected_records = sorted(
        result["records"],
        key=lambda row: (COLLECTIONS.index(row["collection"]), row["record_id"]),
    )
    if result["records"] != expected_records or not any(
        row["collection"] == "plans" and row["record_id"] == result["plan_id"]
        for row in result["records"]
    ):
        _fail("developer feature transaction retained record set changed")

    freshness = result.get("plan_freshness")
    if (
        type(freshness) is not dict
        or set(freshness)
        != {"format", "plan_id", "reason", "schema_version", "state"}
        or type(freshness.get("format")) is not str
        or not freshness["format"]
        or freshness.get("schema_version") != 1
        or type(freshness.get("schema_version")) is bool
        or freshness.get("plan_id") != result["plan_id"]
        or freshness.get("state") not in {"ready", "stale"}
        or freshness.get("reason") is not None
        and type(freshness.get("reason")) is not str
    ):
        _fail("developer feature transaction plan freshness changed")

    recover_available = (
        len(result["actions"]) == 4
        and type(result["actions"][3]) is dict
        and result["actions"][3].get("action") == "recover"
        and result["actions"][3].get("available") is True
    )
    effective_by_match = {
        "matches-before": {"interrupted"} if recover_available else {"planned", "restored"},
        "matches-after": (
            {"interrupted"}
            if recover_available
            else {"applied", "matches-after-without-receipt"}
        ),
        "mixed": {"interrupted"} if recover_available else {"mixed"},
        "drifted": {"interrupted"} if recover_available else {"drifted"},
        "unavailable": {"interrupted"} if recover_available else {"unavailable"},
    }
    if result["current_effective_state"] not in effective_by_match[match["state"]]:
        _fail("developer feature transaction effective state changed")
    derived_current = _current_state(
        match, result["records"], interrupted=recover_available
    )
    if result["current_effective_state"] != derived_current:
        _fail("developer feature transaction retained lineage changed")
    action_fields = {"action", "available", "consent_id", "reason", "record_id"}
    if not all(type(row) is dict for row in result["actions"]):
        _fail("developer feature transaction actions changed")
    if [row.get("action") for row in result["actions"]] != [
        "check",
        "apply",
        "rollback",
        "recover",
    ]:
        _fail("developer feature transaction actions changed")
    for action in result["actions"]:
        if (
            type(action) is not dict
            or set(action) != action_fields
            or type(action.get("available")) is not bool
            or action.get("consent_id") is not None
            and (
                type(action.get("consent_id")) is not str
                or _CONTENT_ID.fullmatch(action["consent_id"]) is None
            )
            or action.get("record_id") is not None
            and (
                type(action.get("record_id")) is not str
                or _CONTENT_ID.fullmatch(action["record_id"]) is None
            )
            or action.get("reason") is not None
            and type(action.get("reason")) is not str
        ):
            _fail("developer feature transaction action changed")
    check, apply, rollback, recover = result["actions"]
    if check != {
        "action": "check",
        "available": True,
        "consent_id": None,
        "record_id": result["plan_id"],
        "reason": None,
    }:
        _fail("developer feature transaction check action changed")
    before = (
        match["state"] == "matches-before"
        and freshness["state"] == "ready"
        and not recover["available"]
    )
    if (
        apply["available"] is not before
        or apply["record_id"] != result["plan_id"]
        or apply["consent_id"] != (result["plan_id"] if before else None)
        or (apply["reason"] is None) is not before
    ):
        _fail("developer feature transaction apply action changed")
    applied_receipts = sorted(
        row["record_id"]
        for row in result["records"]
        if row["collection"] == "receipts" and row["record_state"] == "applied"
    )
    rollback_available = (
        match["state"] == "matches-after"
        and len(applied_receipts) == 1
        and not recover["available"]
    )
    if (
        rollback["available"] is not rollback_available
        or rollback["consent_id"] is not None
        or rollback["record_id"]
        != (applied_receipts[0] if rollback_available else None)
        or (rollback["reason"] is None) is not rollback_available
    ):
        _fail("developer feature transaction rollback action changed")
    if (
        recover["record_id"] != result["plan_id"]
        or recover["consent_id"] is not None
        or recover["reason"]
        != (
            None
            if recover["available"]
            else "no interrupted transaction is retained"
        )
    ):
        _fail("developer feature transaction recovery action changed")
    expected_actions = _actions(
        result["plan_id"],
        match,
        result["records"],
        [recover],
        freshness,
    )
    if result["actions"] != expected_actions:
        _fail("developer feature transaction action meaning changed")
    return result


__all__ = [
    "FORMAT",
    "KIND",
    "build_feature_transaction_view",
    "validate_feature_transaction_view",
]
