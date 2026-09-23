"""Carry one retained runtime identity to its exact owner-backed location."""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable, Mapping, Sequence, TextIO

from workbench_runtime_explorer import (
    Explorer,
    ExplorerError,
    ExplorerRequest,
    validate_result,
)
from workbench_runtime_explorer.providers import (
    ProviderResult,
    atlas_runtime_provider,
    receipt_provider,
)
from workbench_runtime_explorer.query import InterpretedIdentity, parse_query
from workbench_api.events import sanitize_terminal


FORMAT = "workbench-relay-location-v1"
SCHEMA_VERSION = 1
RESULT_PREFIX = "workbench-relay-location:sha256:"
MAX_EXPLORER_RESULT_BYTES = 64 * 1024 * 1024
_IDENTITY_KIND = re.compile(r"^[a-z][a-z0-9-]{0,127}$")


class RelayError(RuntimeError):
    """The exact handoff input or requested identity is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_id(value: Mapping[str, Any]) -> str:
    material = dict(value)
    material.pop("relay_result_id", None)
    return RESULT_PREFIX + hashlib.sha256(_canonical_bytes(material)).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench relay locate",
        description=(
            "Carry one exact identity from retained Atlas, Crucible, or Explorer "
            "evidence to an owner-backed evidence or source location. The "
            "command is read-only and reports unresolved joins honestly."
        ),
    )
    parser.add_argument(
        "identity",
        help=(
            "exact typed Explorer identity, such as machine:example:press; use "
            "--identity-kind for identity kinds without a shorthand"
        ),
    )
    parser.add_argument(
        "--identity-kind",
        help="exact Explorer identity kind when IDENTITY is the raw value",
    )
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument(
        "--explorer-result",
        type=Path,
        help="complete retained Exact Runtime Explorer V1 JSON result",
    )
    inputs.add_argument(
        "--receipt",
        type=Path,
        help="exact Atlas or Crucible receipt understood by Exact Runtime Explorer",
    )
    inputs.add_argument(
        "--runtime-db",
        type=Path,
        help="exact immutable Atlas runtime-graph query database",
    )
    parser.add_argument(
        "--profile",
        help="exact profile filter for --runtime-db",
    )
    parser.add_argument(
        "--side",
        help="exact physical-side filter for --runtime-db",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete Relay result and preserved owner records",
    )
    return parser


def _bounded_text(value: object, label: str, maximum: int = 8192) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(character in value for character in "\r\n\x00")
    ):
        raise RelayError(f"{label} must be bounded single-line text")
    return value


def _identity(raw: str, explicit_kind: str | None) -> InterpretedIdentity:
    value = _bounded_text(raw, "Relay identity")
    if explicit_kind is not None:
        kind = _bounded_text(explicit_kind, "Relay identity kind", 128)
        if _IDENTITY_KIND.fullmatch(kind) is None:
            raise RelayError(
                "--identity-kind must use lowercase letters, numbers, and hyphens"
            )
        return InterpretedIdentity(kind, value, "explicit Relay identity kind")
    try:
        parsed = parse_query(value, limit=100)
    except ExplorerError as exc:
        raise RelayError(str(exc)) from exc
    explicit = [
        row for row in parsed.interpreted if row.basis.startswith("explicit ")
    ]
    if len(explicit) != 1:
        raise RelayError(
            "IDENTITY must use one exact Explorer prefix (for example "
            "machine:example:press), or --identity-kind must be supplied"
        )
    return explicit[0]


def _strict_object(raw: bytes, label: str) -> dict[str, Any]:
    def unique(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise RelayError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda token: (_ for _ in ()).throw(
                RelayError(f"{label} contains non-finite value {token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RelayError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RelayError(f"{label} root must be an object")
    return value


def _read_regular(path: Path, label: str) -> bytes:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= MAX_EXPLORER_RESULT_BYTES
        ):
            raise RelayError(f"{label} is not one bounded regular non-symlink file")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino, before.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            raise RelayError(f"{label} changed while opening")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise RelayError(f"{label} ended while reading")
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        visible = path.lstat()
        identity = lambda row: (
            row.st_dev,
            row.st_ino,
            row.st_size,
            row.st_mtime_ns,
        )
        if identity(opened) != identity(after) or identity(after) != identity(visible):
            raise RelayError(f"{label} changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise RelayError(f"cannot read {label}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_explorer_result(path: Path) -> dict[str, Any]:
    value = _strict_object(_read_regular(path, "Explorer result"), "Explorer result")
    try:
        validate_result(value)
    except ExplorerError as exc:
        raise RelayError(f"Explorer result is invalid: {exc}") from exc
    return value


def _request(identity: InterpretedIdentity) -> ExplorerRequest:
    return ExplorerRequest(
        raw=f"{identity.kind}={identity.value}",
        text=identity.value,
        terms=(identity.value.casefold(),),
        filters={},
        interpreted=(
            InterpretedIdentity(
                identity.kind,
                identity.value,
                "explicit Relay exact identity",
            ),
        ),
        limit=100,
    )


def _provider_result(
    provider: ProviderResult,
    identity: InterpretedIdentity,
) -> dict[str, Any]:
    return Explorer((provider.source,), provider.records).search(_request(identity))


def _direct_result(
    arguments: argparse.Namespace,
    identity: InterpretedIdentity,
) -> tuple[dict[str, Any], str, Path]:
    if (
        (arguments.profile is not None or arguments.side is not None)
        and arguments.runtime_db is None
    ):
        raise RelayError("--profile and --side require --runtime-db")
    if arguments.receipt is not None:
        try:
            provider = receipt_provider(arguments.receipt)
        except (ExplorerError, OSError, RuntimeError, ValueError) as exc:
            raise RelayError(f"retained receipt is invalid: {exc}") from exc
        return _provider_result(provider, identity), "receipt", arguments.receipt
    if arguments.runtime_db is not None:
        try:
            provider = atlas_runtime_provider(
                arguments.runtime_db,
                identity.value,
                profile=arguments.profile,
                physical_side=arguments.side,
                limit=100,
            )
        except (ExplorerError, OSError, RuntimeError, ValueError) as exc:
            raise RelayError(f"Atlas runtime input is invalid: {exc}") from exc
        return _provider_result(provider, identity), "atlas-runtime-db", arguments.runtime_db
    result = _load_explorer_result(arguments.explorer_result)
    return result, "explorer-result", arguments.explorer_result


def _has_identity(entity: Mapping[str, Any], identity: InterpretedIdentity) -> bool:
    return any(
        isinstance(row, Mapping)
        and row.get("kind") == identity.kind
        and row.get("value") == identity.value
        for row in entity.get("identities", [])
    )


def _is_truncated(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            folded = str(key).casefold()
            if "truncated" in folded and (
                child is True
                or (
                    isinstance(child, int)
                    and not isinstance(child, bool)
                    and child > 0
                )
            ):
                return True
            if _is_truncated(child):
                return True
    elif isinstance(value, list):
        return any(_is_truncated(child) for child in value)
    return False


def _runtime_backing(
    entity: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
) -> bool:
    for facet in entity.get("facets", []):
        if not isinstance(facet, Mapping) or facet.get("state") != "observed":
            continue
        source = sources.get(str(facet.get("source_id")), {})
        if (
            facet.get("authority") == "Atlas"
            and source.get("source_kind") == "atlas-runtime-graph"
        ) or facet.get("authority") == "Crucible":
            return True
    return False


def _owner_backing(entity: Mapping[str, Any]) -> bool:
    for owner in entity.get("owners", []):
        if not isinstance(owner, Mapping) or owner.get("state") == "unresolved":
            continue
        actors = owner.get("actors")
        if isinstance(actors, list) and any(isinstance(row, Mapping) for row in actors):
            return True
    return False


def _locations(entity: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for facet in entity.get("facets", []):
        if not isinstance(facet, Mapping):
            continue
        for navigation in facet.get("navigation", []):
            if (
                not isinstance(navigation, Mapping)
                or not isinstance(navigation.get("path"), str)
                or not navigation["path"]
                or navigation.get("resolution") in {None, "unavailable", "unresolved"}
            ):
                continue
            rows.append(
                {
                    "authority": facet.get("authority"),
                    "source_id": facet.get("source_id"),
                    "record_id": facet.get("record_id"),
                    "evidence_state": facet.get("state"),
                    "owner": deepcopy(facet.get("owner")),
                    "navigation": deepcopy(dict(navigation)),
                }
            )
    unique = {_canonical_bytes(row): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def _resolution(
    explorer: Mapping[str, Any],
    identity: InterpretedIdentity,
) -> tuple[str, str, Mapping[str, Any] | None, list[dict[str, Any]]]:
    if explorer["summary"]["truncated"]:
        return "unresolved", "explorer-result-truncated", None, []
    matches = [
        row
        for row in explorer["matches"]
        if isinstance(row, Mapping) and _has_identity(row, identity)
    ]
    if not matches:
        return "unresolved", "identity-not-found", None, []
    if len(matches) != 1 or matches[0].get("ambiguous") is True:
        return "unresolved", "identity-ambiguous", None, []
    entity = matches[0]
    sources = {
        str(row["source_id"]): row
        for row in explorer["sources"]
        if isinstance(row, Mapping) and isinstance(row.get("source_id"), str)
    }
    relevant_ids = {
        str(row["source_id"])
        for row in entity["facets"]
        if isinstance(row, Mapping) and isinstance(row.get("source_id"), str)
    }
    if any(_is_truncated(sources[source_id].get("coverage")) for source_id in relevant_ids):
        return "unresolved", "owner-evidence-truncated", entity, []
    if not _runtime_backing(entity, sources):
        return "unresolved", "retained-runtime-evidence-unavailable", entity, []
    if not _owner_backing(entity):
        return "unresolved", "owner-binding-unavailable", entity, []
    locations = _locations(entity)
    if not locations:
        return "unresolved", "owner-location-unavailable", entity, []
    return "resolved", "exact-owner-location", entity, locations


def build_location(
    explorer: Mapping[str, Any],
    identity: InterpretedIdentity,
    *,
    input_kind: str,
    input_path: Path,
) -> dict[str, Any]:
    """Build one presentation result while retaining owner rows verbatim."""

    try:
        validate_result(explorer)
    except ExplorerError as exc:
        raise RelayError(f"Explorer result is invalid: {exc}") from exc
    resolution, reason, entity, locations = _resolution(explorer, identity)
    relevant_source_ids = (
        {
            str(facet["source_id"])
            for facet in entity.get("facets", [])
            if isinstance(facet, Mapping)
            and isinstance(facet.get("source_id"), str)
        }
        if entity is not None
        else set()
    )
    source_rows = [
        deepcopy(row)
        for row in explorer["sources"]
        if not relevant_source_ids or row.get("source_id") in relevant_source_ids
    ]
    result: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "relay_result_id": "pending",
        "request": {
            "identity_kind": identity.kind,
            "identity_value": identity.value,
            "input_kind": input_kind,
            "input_path": str(input_path.expanduser().resolve()),
        },
        "explorer_result_id": explorer["result_id"],
        "resolution": resolution,
        "reason_code": reason,
        "owner_entity": None if entity is None else deepcopy(dict(entity)),
        "owner_sources": source_rows,
        "locations": locations,
        "uncertainty": deepcopy(list(explorer.get("uncertainty", []))),
        "limitations": [
            "Relay transports identities and owner evidence; it does not create an Atlas fact, profile decision, or runtime claim.",
            "A printed path or receipt pointer is a retained locator, not proof that a current editor or filesystem still contains equivalent bytes.",
        ],
    }
    result["relay_result_id"] = _content_id(result)
    validate_location(result)
    return result


def validate_location(value: Mapping[str, Any]) -> None:
    required = {
        "format",
        "schema_version",
        "read_only",
        "relay_result_id",
        "request",
        "explorer_result_id",
        "resolution",
        "reason_code",
        "owner_entity",
        "owner_sources",
        "locations",
        "uncertainty",
        "limitations",
    }
    if set(value) != required:
        raise RelayError("Relay result fields are unexpected or missing")
    if (
        value.get("format") != FORMAT
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("read_only") is not True
    ):
        raise RelayError("Relay result format is unsupported")
    if value.get("resolution") not in {"resolved", "unresolved"}:
        raise RelayError("Relay resolution is unsupported")
    if not isinstance(value.get("request"), Mapping):
        raise RelayError("Relay request is malformed")
    for field in ("identity_kind", "identity_value", "input_kind", "input_path"):
        _bounded_text(value["request"].get(field), f"Relay request {field}")
    for field in ("owner_sources", "locations", "uncertainty", "limitations"):
        if not isinstance(value.get(field), list):
            raise RelayError(f"Relay {field} must be an array")
    if value["resolution"] == "resolved":
        if not isinstance(value.get("owner_entity"), Mapping) or not value["locations"]:
            raise RelayError("resolved Relay result lacks owner evidence or a location")
    elif value["locations"]:
        raise RelayError("unresolved Relay result cannot expose a resolved location")
    if value.get("relay_result_id") != _content_id(value):
        raise RelayError("Relay result identity is stale or invalid")


def _safe(value: object) -> str:
    return sanitize_terminal(str(value))


def _context_values(entity: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    result: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in keys:
                    if isinstance(child, str) and child:
                        result.add(child)
                    elif isinstance(child, list):
                        result.update(str(row) for row in child if isinstance(row, str))
                if key in {"scope", "runtime_form", "runtime_identity", "evidence"}:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(entity.get("facets", []))
    return sorted(result)


def render_location(result: Mapping[str, Any], output: TextIO) -> None:
    request = result["request"]
    output.write(
        f"Relay · {_safe(result['resolution'])}\n"
        f"Identity: {_safe(request['identity_kind'])}={_safe(request['identity_value'])}\n"
        f"Evidence: {_safe(result['explorer_result_id'])}\n"
    )
    entity = result.get("owner_entity")
    if isinstance(entity, Mapping):
        profiles = _context_values(
            entity,
            {"profile", "profile_id", "platform_profile_id", "pack_profile_id"},
        )
        sides = _context_values(entity, {"physical_side", "side"})
        sessions = _context_values(entity, {"session_id", "session_ids"})
        snapshots = _context_values(
            entity,
            {"snapshot_id", "runtime_snapshot_id"},
        )
        states = sorted(
            {
                str(facet.get("state"))
                for facet in entity.get("facets", [])
                if isinstance(facet, Mapping) and facet.get("state")
            }
        )
        for label, values in (
            ("Profile", profiles),
            ("Side", sides),
            ("Session", sessions),
            ("Snapshot", snapshots),
            ("Evidence state", states),
        ):
            if values:
                output.write(f"{label}: {_safe(', '.join(values))}\n")
    if result["resolution"] == "resolved":
        output.write("\nOpen\n")
        for row in result["locations"]:
            navigation = row["navigation"]
            suffix = f":{navigation['line']}" if navigation.get("line") else ""
            pointer = (
                f" {navigation['json_pointer']}"
                if navigation.get("json_pointer")
                else ""
            )
            output.write(
                f"- {_safe(navigation['path'])}{_safe(suffix)}{_safe(pointer)} "
                f"({_safe(row['authority'])}; {_safe(navigation['resolution'])})\n"
            )
    else:
        messages = {
            "explorer-result-truncated": "The retained Explorer result is incomplete, so uniqueness cannot be proved.",
            "identity-not-found": "The exact identity is absent from the supplied evidence.",
            "identity-ambiguous": "More than one owner-backed entity carries this identity.",
            "owner-evidence-truncated": "The relevant owner evidence is truncated.",
            "retained-runtime-evidence-unavailable": "The identity has no observed Atlas or Crucible runtime facet.",
            "owner-binding-unavailable": "The retained runtime facet has no owner binding.",
            "owner-location-unavailable": "The owner evidence contains no exact source or evidence locator.",
        }
        output.write(
            "\nUnresolved: "
            + messages.get(str(result["reason_code"]), str(result["reason_code"]))
            + "\n"
        )
    for uncertainty in result.get("uncertainty", []):
        output.write(f"! {_safe(uncertainty)}\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    del root  # The flow consumes explicit retained inputs only.
    parser = _parser()
    arguments = parser.parse_args(argv)
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    try:
        identity = _identity(arguments.identity, arguments.identity_kind)
        explorer, input_kind, input_path = _direct_result(arguments, identity)
        result = build_location(
            explorer,
            identity,
            input_kind=input_kind,
            input_path=input_path,
        )
        if arguments.json:
            stdout.write(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
        else:
            render_location(result, stdout)
        return 0 if result["resolution"] == "resolved" else 1
    except (RelayError, ExplorerError, OSError, RuntimeError, ValueError) as exc:
        stderr.write(f"Relay locate failed: {_safe(exc)}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
