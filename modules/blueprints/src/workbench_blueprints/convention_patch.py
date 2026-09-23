#!/usr/bin/env python3

"""Deterministic, profile-owned anchored text construction.

The generic engine owns safe rendering and collision checks.  Pack profiles
own every path, anchor, guard, snippet, parameter constraint, and allocation
range in a content-addressed JSON pattern.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, NoReturn


PATTERN_FORMAT = "workbench-blueprints-convention-patch-v1"
_PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
_MAX_PATTERN_BYTES = 1024 * 1024
_MAX_TARGET_BYTES = 8 * 1024 * 1024


class ConventionPatchError(ValueError):
    """Raised when a convention patch cannot be rendered safely."""


def _fail(message: str) -> NoReturn:
    raise ConventionPatchError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _closed(value: Any, expected: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{location} must be an object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        _fail(
            f"{location} fields differ; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    return value


def _safe_relative(value: Any, location: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail(f"{location} is not a portable repository path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"{location} is not a portable repository path")
    return path


def _read_regular(path: Path, maximum: int, location: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        _fail(f"cannot read {location}: {exc}")
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            _fail(f"{location} is not a regular file")
        if status.st_size > maximum:
            _fail(f"{location} exceeds its size limit")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_pattern(value: Any, source: str) -> dict[str, Any]:
    pattern = _closed(
        value,
        {
            "schema_version",
            "format",
            "pattern_key",
            "version",
            "lifecycle",
            "authority",
            "parameters",
            "allocation",
            "atlas",
            "edits",
        },
        source,
    )
    if pattern["schema_version"] != 1 or pattern["format"] != PATTERN_FORMAT:
        _fail(f"{source} is not a supported convention-patch pattern")
    for field in ("pattern_key", "version", "lifecycle"):
        if not isinstance(pattern[field], str) or not pattern[field]:
            _fail(f"{source}#{field} must be a non-empty string")
    authority = _closed(
        pattern["authority"],
        {"profile", "observed_revision", "organization"},
        f"{source}#authority",
    )
    if not all(isinstance(value, str) and value for value in authority.values()):
        _fail(f"{source}#authority values must be non-empty strings")
    if re.fullmatch(r"[0-9a-f]{40}", authority["observed_revision"]) is None:
        _fail(f"{source}#authority observed revision must be a Git object ID")

    parameters = pattern["parameters"]
    if not isinstance(parameters, dict) or not parameters:
        _fail(f"{source}#parameters must be a non-empty object")
    for name, constraint_value in parameters.items():
        if re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
            _fail(f"{source}#parameters has an invalid name {name!r}")
        constraint = _closed(
            constraint_value,
            {"type", "minimum", "maximum", "min_length", "max_length", "pattern"},
            f"{source}#parameters/{name}",
        )
        if constraint["type"] not in {"string", "integer"}:
            _fail(f"{source}#parameters/{name} has an unsupported type")
        if constraint["type"] == "string":
            try:
                re.compile(constraint["pattern"])
            except (TypeError, re.error) as exc:
                _fail(f"{source}#parameters/{name} has an invalid pattern: {exc}")
        elif constraint["pattern"] is not None:
            _fail(f"{source}#parameters/{name} integer pattern must be null")

    allocation = _closed(
        pattern["allocation"],
        {"domain", "minimum", "maximum", "owner_path"},
        f"{source}#allocation",
    )
    if (
        not isinstance(allocation["domain"], str)
        or not isinstance(allocation["minimum"], int)
        or isinstance(allocation["minimum"], bool)
        or not isinstance(allocation["maximum"], int)
        or isinstance(allocation["maximum"], bool)
        or allocation["minimum"] > allocation["maximum"]
    ):
        _fail(f"{source}#allocation is invalid")
    _safe_relative(allocation["owner_path"], f"{source}#allocation/owner_path")

    atlas = _closed(pattern["atlas"], {"baseline_id", "queries"}, f"{source}#atlas")
    if not isinstance(atlas["baseline_id"], str) or not atlas["baseline_id"]:
        _fail(f"{source}#atlas/baseline_id must be a non-empty string")
    if not isinstance(atlas["queries"], list) or not atlas["queries"]:
        _fail(f"{source}#atlas/queries must be a non-empty array")
    for index, query_value in enumerate(atlas["queries"]):
        query = _closed(
            query_value,
            {"id", "query_id", "result_id", "invariants"},
            f"{source}#atlas/queries/{index}",
        )
        if not all(
            isinstance(query[field], str) and query[field]
            for field in ("id", "query_id", "result_id")
        ):
            _fail(f"{source}#atlas/queries/{index} identities are invalid")
        if not isinstance(query["invariants"], list) or not query["invariants"]:
            _fail(f"{source}#atlas/queries/{index}/invariants must be non-empty")
        for invariant_index, invariant_value in enumerate(query["invariants"]):
            invariant = _closed(
                invariant_value,
                {"id", "result_path", "operator", "expected"},
                f"{source}#atlas/queries/{index}/invariants/{invariant_index}",
            )
            if invariant["operator"] != "equals":
                _fail("convention-patch Atlas invariants currently require equals")
            if (
                not isinstance(invariant["result_path"], str)
                or not invariant["result_path"].startswith("/")
            ):
                _fail("convention-patch Atlas invariant path is invalid")

    edits = pattern["edits"]
    if not isinstance(edits, list) or not edits:
        _fail(f"{source}#edits must be a non-empty array")
    edit_ids: set[str] = set()
    paths: set[str] = set()
    for index, edit_value in enumerate(edits):
        edit = _closed(
            edit_value,
            {
                "id",
                "path",
                "position",
                "anchor",
                "snippet",
                "required_once",
                "forbidden",
            },
            f"{source}#edits/{index}",
        )
        if not isinstance(edit["id"], str) or not edit["id"] or edit["id"] in edit_ids:
            _fail(f"{source}#edits/{index}/id is invalid or duplicated")
        edit_ids.add(edit["id"])
        path = _safe_relative(edit["path"], f"{source}#edits/{index}/path")
        if path.as_posix() in paths:
            _fail(f"{source}#edits has duplicate path {path.as_posix()}")
        paths.add(path.as_posix())
        if edit["position"] not in {"before", "after"}:
            _fail(f"{source}#edits/{index}/position is invalid")
        for field in ("anchor", "snippet"):
            if not isinstance(edit[field], str) or not edit[field]:
                _fail(f"{source}#edits/{index}/{field} must be non-empty")
        for field in ("required_once", "forbidden"):
            if (
                not isinstance(edit[field], list)
                or any(not isinstance(item, str) or not item for item in edit[field])
            ):
                _fail(f"{source}#edits/{index}/{field} is invalid")
    return pattern


def load_pattern(path: Path | str) -> dict[str, Any]:
    """Load and validate one content-addressed convention patch pattern."""

    source = Path(path).resolve()
    content = _read_regular(source, _MAX_PATTERN_BYTES, str(source))
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot decode convention patch pattern {source}: {exc}")
    pattern = _validate_pattern(value, str(source))
    return {
        **pattern,
        "pattern_sha256": sha256(_canonical_bytes(pattern)).hexdigest(),
    }


def _parameter_text(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        _fail("convention-patch parameters must be strings or integers")
    return str(value)


def _render(value: str, parameters: dict[str, Any], location: str) -> str:
    matches = list(_PLACEHOLDER_RE.finditer(value))
    residue = _PLACEHOLDER_RE.sub("", value)
    if "{{" in residue or "}}" in residue:
        _fail(f"{location} has invalid template syntax")
    unknown = sorted({match.group(1) for match in matches} - set(parameters))
    if unknown:
        _fail(f"{location} references unknown parameters {unknown}")
    return _PLACEHOLDER_RE.sub(
        lambda match: _parameter_text(parameters[match.group(1)]),
        value,
    )


def _validate_parameters(pattern: dict[str, Any], values: dict[str, Any]) -> None:
    constraints = pattern["parameters"]
    if set(values) != set(constraints):
        _fail(
            "convention-patch parameter set differs; "
            f"missing={sorted(set(constraints) - set(values))}, "
            f"unknown={sorted(set(values) - set(constraints))}"
        )
    for name, constraint in constraints.items():
        value = values[name]
        if constraint["type"] == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                _fail(f"parameter {name} must be an integer")
            if value < constraint["minimum"] or value > constraint["maximum"]:
                _fail(f"parameter {name} is outside its admitted range")
        else:
            if not isinstance(value, str):
                _fail(f"parameter {name} must be a string")
            if (
                len(value) < constraint["min_length"]
                or len(value) > constraint["max_length"]
            ):
                _fail(f"parameter {name} is outside its admitted length")
            if re.fullmatch(constraint["pattern"], value) is None:
                _fail(f"parameter {name} does not match its admitted pattern")


def _pointer(value: Any, pointer: str) -> tuple[bool, Any]:
    current = value
    for raw in pointer.removeprefix("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def verify_atlas_evidence(
    pattern: dict[str, Any], resolved: list[dict[str, Any]]
) -> None:
    """Require exact query identities and profile-declared equality invariants."""

    by_id = {row.get("query_id"): row for row in resolved if isinstance(row, dict)}
    for query in pattern["atlas"]["queries"]:
        row = by_id.get(query["query_id"])
        if (
            row is None
            or row.get("availability") != "available"
            or row.get("result_id") != query["result_id"]
        ):
            _fail(f"Atlas evidence is unavailable for {query['query_id']}")
        for invariant in query["invariants"]:
            found, actual = _pointer(row.get("result"), invariant["result_path"])
            if not found or actual != invariant["expected"]:
                _fail(
                    f"Atlas invariant {query['id']}:{invariant['id']} failed"
                )


def allocate_first_free(pattern: dict[str, Any], occupied: list[int]) -> int:
    """Allocate the first globally unoccupied ID inside the profile range."""

    allocation = pattern["allocation"]
    used = set(occupied)
    for candidate in range(allocation["minimum"], allocation["maximum"] + 1):
        if candidate not in used:
            return candidate
    _fail(f"allocation domain {allocation['domain']} is exhausted")


def _normalized_lines(text: str, label: str) -> tuple[str, str]:
    """Normalize one uniformly LF or CRLF source without changing its style."""

    if "\r" not in text:
        return text, "\n"
    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf or "\n" in without_crlf:
        _fail(f"{label} has mixed or bare-CR line endings")
    return text.replace("\r\n", "\n"), "\r\n"


def render_updates(
    pattern: dict[str, Any],
    target_root: Path | str,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Render exact replacement bytes for all guarded aggregate-file edits."""

    _validate_parameters(pattern, parameters)
    root = Path(target_root).resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("convention-patch target must be a regular directory")
    operations: list[dict[str, Any]] = []
    for edit in pattern["edits"]:
        relative = _safe_relative(edit["path"], f"edit {edit['id']} path")
        path = root.joinpath(*relative.parts)
        if any(
            cursor.is_symlink()
            for cursor in (path, *path.parents)
            if cursor != root.parent
        ):
            _fail(f"edit target contains a symbolic link: {relative.as_posix()}")
        content = _read_regular(path, _MAX_TARGET_BYTES, relative.as_posix())
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail(f"edit target is not UTF-8: {relative.as_posix()}: {exc}")
        normalized, line_ending = _normalized_lines(
            text,
            f"edit target {relative.as_posix()}",
        )
        anchor = _render(edit["anchor"], parameters, f"{edit['id']} anchor")
        snippet = _render(edit["snippet"], parameters, f"{edit['id']} snippet")
        for required in edit["required_once"]:
            rendered = _render(required, parameters, f"{edit['id']} required guard")
            if normalized.count(rendered) != 1:
                _fail(
                    f"edit {edit['id']} required guard is not unique in "
                    f"{relative.as_posix()}"
                )
        for forbidden in edit["forbidden"]:
            rendered = _render(forbidden, parameters, f"{edit['id']} collision guard")
            if rendered in normalized:
                _fail(
                    f"edit {edit['id']} identity already exists in "
                    f"{relative.as_posix()}"
                )
        if normalized.count(anchor) != 1:
            _fail(
                f"edit {edit['id']} anchor is not unique in {relative.as_posix()}"
            )
        if edit["position"] == "before":
            updated = normalized.replace(anchor, snippet + anchor, 1)
        else:
            updated = normalized.replace(anchor, anchor + snippet, 1)
        updated_bytes = updated.replace("\n", line_ending).encode("utf-8")
        if updated_bytes == content:
            _fail(f"edit {edit['id']} produced no change")
        operations.append({
            "operation": "update",
            "path": relative.as_posix(),
            "before_sha256": sha256(content).hexdigest(),
            "content_sha256": sha256(updated_bytes).hexdigest(),
            "content": updated_bytes,
        })
    operations.sort(key=lambda row: row["path"])
    return operations


__all__ = [
    "ConventionPatchError",
    "allocate_first_free",
    "load_pattern",
    "render_updates",
    "verify_atlas_evidence",
]
