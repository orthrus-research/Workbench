#!/usr/bin/env python3

"""Normalize a Cleanroom CleanMix 0.7 audit file into Crucible observations.

This exact-profile importer deliberately maps ``APPLY`` to ``apply_started``.
It never emits an application-completed or final-byte observation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


OBSERVATION_SET_FORMAT = "workbench-crucible-mixin-observation-set-v1"
OWNER_BINDINGS_FORMAT = "workbench-cleanroom-mixin-owner-bindings-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LINE_RE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\] "
    r"\[(?P<thread>[^\]]+)/(?P<level>[A-Z]+)\] "
    r"\[(?P<logger>[^\]]+)\]: (?P<message>.*)$"
)
_APPLY_RE = re.compile(
    r"^APPLY (?P<config>[^: ]+):(?P<mixin>\S+) "
    r"from mod (?P<owner>\S+) -> (?P<target>\S+)$"
)
_POSTPROCESS_RE = re.compile(r"^POSTPROCESS (?P<class_name>\S+)$")
_GENERATE_RE = re.compile(
    r"^GENERATE (?P<class_name>\S+) \(by (?P<generator>[^)]+)\)$"
)
_AUDIT_LOGGERS = frozenset({"CleanMix/Audit"})
_LIMIT_APPLY = (
    "CleanMix 0.7 emits APPLY before applicator passes; this observation proves application entry only.",
    "The best-effort audit sink may omit records after an I/O failure and truncates the file on the next process's first write.",
)
_LIMIT_AUDIT = (
    "The best-effort audit sink may omit records after an I/O failure and truncates the file on the next process's first write.",
)


class AuditImportError(ValueError):
    """Raised when an exact-profile audit file is ambiguous or malformed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditImportError(message)


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _load_owner_bindings(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditImportError(f"cannot read owner bindings {path}: {exc}") from exc
    _require(isinstance(value, dict), "owner bindings must be an object")
    _require(
        set(value) == {"format", "bindings"},
        "owner bindings surface is not exactly V1",
    )
    _require(value["format"] == OWNER_BINDINGS_FORMAT, "owner bindings format is not V1")
    rows = value["bindings"]
    _require(isinstance(rows, list), "owner bindings.bindings must be an array")
    result: dict[str, str] = {}
    digests: dict[str, str] = {}
    for index, row in enumerate(rows):
        context = f"owner bindings[{index}]"
        _require(isinstance(row, dict), f"{context} must be an object")
        _require(
            set(row) == {"owner_label", "artifact_sha256"},
            f"{context} surface is not exactly V1",
        )
        label = _text(row["owner_label"], f"{context}.owner_label")
        digest = _sha256(row["artifact_sha256"], f"{context}.artifact_sha256")
        _require(label not in result, f"duplicate owner binding {label!r}")
        _require(
            digest not in digests or digests[digest] == label,
            f"artifact {digest} is ambiguously bound to multiple owner labels",
        )
        result[label] = digest
        digests[digest] = label
    return result


def _subject_with_owner(
    *,
    owner: str,
    bindings: Mapping[str, str],
    extra: Mapping[str, str],
) -> dict[str, str]:
    subject = {"owner_label": owner, **dict(extra)}
    digest = bindings.get(owner)
    if digest is not None:
        subject["artifact_sha256"] = digest
    return subject


def _evidence(
    *,
    log_sha256: str,
    line_number: int,
    limitations: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "kind": "cleanmix_audit",
        "source_sha256": log_sha256,
        "source_record": f"line:{line_number}",
        "limitations": sorted(limitations),
    }


def import_audit_bytes(
    data: bytes,
    *,
    owner_bindings: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parse exact CleanMix/Audit lines and return normalized observations."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditImportError(f"CleanMix audit is not UTF-8: {exc}") from exc
    _require("\x00" not in text, "CleanMix audit contains a NUL byte")
    bindings = dict(owner_bindings or {})
    for label, digest in bindings.items():
        _text(label, "owner binding label")
        _sha256(digest, f"owner binding {label}")

    log_sha256 = hashlib.sha256(data).hexdigest()
    observations: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = _LINE_RE.fullmatch(line)
        if match is None:
            # Exception continuation lines and unrelated diagnostic text are
            # outside the structured Audit logger surface.
            if "[CleanMix/Audit]" in line or line.startswith("["):
                raise AuditImportError(f"malformed log line {line_number}")
            continue
        if match.group("logger") not in _AUDIT_LOGGERS:
            continue
        _require(
            match.group("level") == "INFO",
            f"unexpected audit level at line {line_number}",
        )
        message = match.group("message")
        apply_match = _APPLY_RE.fullmatch(message)
        postprocess_match = _POSTPROCESS_RE.fullmatch(message)
        generate_match = _GENERATE_RE.fullmatch(message)
        sequence = len(observations) + 1
        common: dict[str, Any] = {
            "sequence": sequence,
            "outcome": "observed",
            "phase": "UNKNOWN",
            "thread": match.group("thread"),
            "wallclock": match.group("time"),
        }
        if apply_match is not None:
            owner = apply_match.group("owner")
            observations.append(
                {
                    **common,
                    "stage": "apply_started",
                    "subject": _subject_with_owner(
                        owner=owner,
                        bindings=bindings,
                        extra={
                            "config": apply_match.group("config"),
                            "mixin": apply_match.group("mixin"),
                            "target_class": apply_match.group("target"),
                        },
                    ),
                    "evidence": _evidence(
                        log_sha256=log_sha256,
                        line_number=line_number,
                        limitations=_LIMIT_APPLY,
                    ),
                }
            )
        elif postprocess_match is not None:
            observations.append(
                {
                    **common,
                    "stage": "mixin_postprocess_seen",
                    "subject": {
                        "target_class": postprocess_match.group("class_name")
                    },
                    "evidence": _evidence(
                        log_sha256=log_sha256,
                        line_number=line_number,
                        limitations=_LIMIT_AUDIT,
                    ),
                }
            )
        elif generate_match is not None:
            observations.append(
                {
                    **common,
                    "stage": "generated",
                    "subject": {
                        "generated_class": generate_match.group("class_name")
                    },
                    "message": f"generator={generate_match.group('generator')}",
                    "evidence": _evidence(
                        log_sha256=log_sha256,
                        line_number=line_number,
                        limitations=_LIMIT_AUDIT,
                    ),
                }
            )
        else:
            raise AuditImportError(
                f"unknown CleanMix/Audit event at line {line_number}: {message}"
            )
    return observations


def build_observation_set(
    *,
    audit_bytes: bytes,
    session: Mapping[str, Any],
    launch_state: str,
    owner_bindings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    expected_session = {
        "session_id",
        "launch_id",
        "profile_id",
        "side",
        "component_receipt_sha256",
    }
    _require(set(session) == expected_session, "session surface is not exactly V1")
    normalized_session = {
        "session_id": _text(session["session_id"], "session.session_id"),
        "launch_id": _text(session["launch_id"], "session.launch_id"),
        "profile_id": _text(session["profile_id"], "session.profile_id"),
        "side": session["side"],
        "component_receipt_sha256": _sha256(
            session["component_receipt_sha256"],
            "session.component_receipt_sha256",
        ),
    }
    _require(
        normalized_session["side"]
        in {"client", "dedicated_server", "integrated_server"},
        "session.side is unsupported",
    )
    _require(
        launch_state in {"complete", "crashed", "incomplete"},
        "launch_state is unsupported",
    )
    return {
        "format": OBSERVATION_SET_FORMAT,
        "session": normalized_session,
        "launch_state": launch_state,
        "observations": import_audit_bytes(
            audit_bytes,
            owner_bindings=owner_bindings,
        ),
        "limitations": sorted(
            {
                "CleanMix audit evidence is a best-effort diagnostic prefix, not a complete transformation ledger.",
                "The importer does not infer phase because CleanMix 0.7 audit records do not carry it.",
            }
        ),
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise AuditImportError(f"output cannot be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".cleanmix-audit-",
        suffix=".json.tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import exact Cleanroom CleanMix audit events for Crucible."
    )
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument(
        "--side",
        required=True,
        choices=("client", "dedicated_server", "integrated_server"),
    )
    parser.add_argument("--component-receipt-sha256", required=True)
    parser.add_argument(
        "--launch-state",
        required=True,
        choices=("complete", "crashed", "incomplete"),
    )
    parser.add_argument("--owner-bindings", type=Path)
    args = parser.parse_args()
    try:
        audit_bytes = args.audit.read_bytes()
    except OSError as exc:
        raise AuditImportError(f"cannot read audit file {args.audit}: {exc}") from exc
    bindings = _load_owner_bindings(args.owner_bindings)
    observation_set = build_observation_set(
        audit_bytes=audit_bytes,
        session={
            "session_id": args.session_id,
            "launch_id": args.launch_id,
            "profile_id": args.profile_id,
            "side": args.side,
            "component_receipt_sha256": args.component_receipt_sha256,
        },
        launch_state=args.launch_state,
        owner_bindings=bindings,
    )
    _atomic_write(args.output, observation_set)
    print(len(observation_set["observations"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditImportError as exc:
        print(f"CleanMix audit import failed: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
