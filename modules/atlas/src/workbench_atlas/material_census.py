#!/usr/bin/env python3

"""Observe material registrations in a live Git workspace.

This is a deliberately narrow Atlas source adapter.  It reports every
parseable ``Material.Builder`` registration in current tracked Groovy bytes,
keeps unparsed builder IDs occupied, and resolves the two pinned source-query
shapes used by the first Supersymmetry Blueprint.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any, NoReturn, Sequence


_BUILDER_START_RE = re.compile(r"\bnew\s+Material\.Builder\s*\(")
_NUMERIC_BUILDER_START_RE = re.compile(
    r"\bnew\s+Material\.Builder\s*\(\s*(?P<material_id>[0-9]+)\s*,"
)
_BUILDER_RE = re.compile(
    r"\bnew\s+Material\.Builder\s*\(\s*"
    r"(?P<material_id>[0-9]+)\s*,\s*(?:"
    r"(?P<helper>[A-Za-z_][A-Za-z0-9_.]*)\s*\(\s*"
    r"(?P<helper_quote>['\"])(?P<helper_name>[A-Za-z0-9_.:/-]+)"
    r"(?P=helper_quote)\s*\)"
    r"|(?P<literal_quote>['\"])(?P<literal_name>[A-Za-z0-9_.:/-]+)"
    r"(?P=literal_quote))"
)
_LOCALIZATION_QUERY_RE = re.compile(
    r"^SRC-PACK@(?P<revision>[0-9a-f]{40}):"
    r"(?P<path>[^#]+)#(?P<key>[^#]+)$"
)
_SOURCE_RESULT_RE = re.compile(r"^source-file:sha256:(?P<digest>[0-9a-f]{64})$")
_MATERIAL_RESULT_RE = re.compile(
    r"^SRC-PACK@(?P<revision>[0-9a-f]{40}):(?P<citation>CIT-[A-Z0-9-]+)$"
)
_MAX_SOURCE_BYTES = 8 * 1024 * 1024


class MaterialCensusError(ValueError):
    """Raised when Atlas cannot make a safe material observation."""


def _fail(message: str) -> NoReturn:
    raise MaterialCensusError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _git(
    workspace: Path,
    arguments: Sequence[str],
    *,
    executable: Path | str = "git",
) -> bytes:
    try:
        completed = subprocess.run(
            [os.fspath(executable), "-C", str(workspace), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(f"cannot inspect material workspace with Git: {exc}")
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        _fail(
            f"Git {' '.join(arguments)} failed: "
            f"{detail or 'unknown Git failure'}"
        )
    return completed.stdout


def _workspace_root(
    workspace: Path | str,
    *,
    git_executable: Path | str = "git",
) -> tuple[Path, str]:
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("material workspace must be a regular directory")
    top = Path(
        os.fsdecode(
            _git(
                root,
                ("rev-parse", "--show-toplevel"),
                executable=git_executable,
            )
        ).strip()
    ).resolve()
    if top != root:
        _fail(f"material workspace must be its Git root: {root}")
    revision = os.fsdecode(
        _git(root, ("rev-parse", "HEAD"), executable=git_executable)
    ).strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        _fail("material workspace has an invalid Git revision")
    return root, revision


def _safe_path(value: str, label: str) -> PurePosixPath:
    if not value or "\\" in value:
        _fail(f"{label} is not a portable repository path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"{label} is not a portable repository path")
    return path


def _read_regular(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        _fail(f"cannot read {label}: {exc}")
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            _fail(f"{label} is not a regular file")
        if status.st_size > _MAX_SOURCE_BYTES:
            _fail(f"{label} exceeds the Atlas source-size limit")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _mask_comments(text: str) -> str:
    """Replace Groovy comments with spaces while retaining offsets."""

    output = list(text)
    state = "code"
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char in {"'", '"'}:
                state = "string"
                quote = char
            elif char == "/" and following == "/":
                output[index] = output[index + 1] = " "
                state = "line-comment"
                index += 1
            elif char == "/" and following == "*":
                output[index] = output[index + 1] = " "
                state = "block-comment"
                index += 1
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == quote:
                state = "code"
        elif state == "line-comment":
            if char in {"\r", "\n"}:
                state = "code"
            else:
                output[index] = " "
        else:
            if char == "*" and following == "/":
                output[index] = output[index + 1] = " "
                state = "code"
                index += 1
            elif char not in {"\r", "\n"}:
                output[index] = " "
        index += 1
    return "".join(output)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _registration(match: re.Match[str]) -> tuple[int, str, str]:
    material_id = int(match.group("material_id"))
    if match.group("helper_name") is not None:
        return material_id, match.group("helper_name"), match.group("helper")
    return material_id, match.group("literal_name"), "literal"


def _census_paths(
    root: Path,
    revision: str,
    paths: Sequence[PurePosixPath],
    *,
    selection: str,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    uncertainties: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for relative in sorted(set(paths), key=lambda item: item.as_posix()):
        path = root.joinpath(*relative.parts)
        content = _read_regular(path, relative.as_posix())
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail(f"tracked Groovy file is not UTF-8: {relative}: {exc}")
        masked = _mask_comments(text)
        digest = sha256(content).hexdigest()
        matched_starts: set[int] = set()
        for match in _BUILDER_RE.finditer(masked):
            material_id, registry_name, helper = _registration(match)
            matched_starts.add(match.start())
            observations.append({
                "file_sha256": digest,
                "line": _line_number(masked, match.start()),
                "material_id": material_id,
                "namespace_helper": helper,
                "path": relative.as_posix(),
                "registry_name": registry_name,
            })
        for match in _BUILDER_START_RE.finditer(masked):
            if match.start() in matched_starts:
                continue
            numeric = _NUMERIC_BUILDER_START_RE.match(masked, match.start())
            row: dict[str, Any] = {
                "kind": (
                    "unparsed-material-name"
                    if numeric is not None
                    else "dynamic-material-builder"
                ),
                "line": _line_number(masked, match.start()),
                "path": relative.as_posix(),
            }
            if numeric is not None:
                row["material_id"] = int(numeric.group("material_id"))
            else:
                arguments = masked[match.end(): match.end() + 256]
                first_argument = arguments.split(",", 1)[0].strip()
                row["material_id_expression"] = first_argument[:128]
            uncertainties.append(row)
        if any(
            row["path"] == relative.as_posix()
            for row in (*observations, *uncertainties)
        ):
            files.append({
                "path": relative.as_posix(),
                "sha256": digest,
                "size": len(content),
            })

    observations.sort(
        key=lambda row: (
            row["material_id"],
            row["registry_name"],
            row["path"],
            row["line"],
        )
    )
    uncertainties.sort(key=lambda row: (
        row.get("material_id", 2_147_483_648),
        row["path"],
        row["line"],
    ))
    occupied_values = sorted({
        *(row["material_id"] for row in observations),
        *(
            row["material_id"]
            for row in uncertainties
            if "material_id" in row
        ),
    })
    by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        location = {"line": row["line"], "path": row["path"]}
        by_id[row["material_id"]].append(location)
        by_name[row["registry_name"]].append(location)
    collisions = [
        {
            "kind": kind,
            "occurrences": occurrences,
            "value": value,
        }
        for kind, groups in (("material-id", by_id), ("registry-name", by_name))
        for value, occurrences in sorted(groups.items())
        if len(occurrences) > 1
    ]
    census = {
        "schema_version": 1,
        "format": "workbench-atlas-material-census-v1",
        "census_id": "",
        "workspace": {
            "revision": revision,
            "root_uri": root.as_uri(),
            "selection": selection,
        },
        "files": files,
        "observations": observations,
        "occupied_values": occupied_values,
        "registry_names": sorted({
            row["registry_name"] for row in observations
        }),
        "collisions": collisions,
        "uncertainties": uncertainties,
    }
    identity = {**census, "workspace": dict(census["workspace"])}
    identity.pop("census_id")
    identity["workspace"].pop("root_uri")
    census["census_id"] = "atlas-material-census:sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    return census


def census_material_builders(
    workspace: Path | str,
    *,
    git_executable: Path | str = "git",
) -> dict[str, Any]:
    """Census current tracked Groovy material-builder registrations."""

    root, revision = _workspace_root(
        workspace,
        git_executable=git_executable,
    )
    raw_paths = _git(
        root,
        ("ls-files", "-z", "--", "*.groovy"),
        executable=git_executable,
    ).split(b"\0")
    paths: list[PurePosixPath] = []
    for raw_path in raw_paths:
        if not raw_path:
            continue
        try:
            text_path = os.fsdecode(raw_path)
        except UnicodeError as exc:
            _fail(f"Git returned an invalid Groovy path: {exc}")
        paths.append(_safe_path(text_path, "tracked Groovy path"))
    return _census_paths(
        root,
        revision,
        paths,
        selection="current-git-tracked-groovy-regular-files",
    )


def census_material_directory(runtime_root: Path | str) -> dict[str, Any]:
    """Census regular Groovy bytes in an installed Minecraft payload.

    Installed instances are intentionally not made into Git workspaces.  The
    content-set identity keeps this observation reproducible while allowing
    Workbench to inspect the instance the developer actually selected.
    """

    root = Path(runtime_root).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("material runtime root must be a regular directory")
    groovy_root = root / "groovy"
    if not groovy_root.is_dir() or groovy_root.is_symlink():
        _fail("material runtime root lacks a regular groovy directory")
    paths: list[PurePosixPath] = []
    for path in sorted(
        groovy_root.rglob("*.groovy"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file():
            _fail(
                "runtime Groovy selection contains a non-regular path: "
                f"{relative.as_posix()}"
            )
        if any(parent.is_symlink() for parent in path.parents if parent != root):
            _fail(
                "runtime Groovy selection contains a symbolic-link parent: "
                f"{relative.as_posix()}"
            )
        paths.append(_safe_path(relative.as_posix(), "runtime Groovy path"))
    if not paths:
        _fail("material runtime root contains no Groovy scripts")
    content_identity = [
        {
            "path": relative.as_posix(),
            "sha256": sha256(
                _read_regular(
                    root.joinpath(*relative.parts),
                    relative.as_posix(),
                )
            ).hexdigest(),
        }
        for relative in paths
    ]
    revision = "content-set:sha256:" + sha256(
        _canonical_bytes(content_identity)
    ).hexdigest()
    return _census_paths(
        root,
        revision,
        paths,
        selection="current-runtime-groovy-regular-files",
    )


def _git_blob(
    root: Path,
    revision: str,
    relative: str,
    *,
    git_executable: Path | str = "git",
) -> bytes:
    path = _safe_path(relative, "pinned source path")
    return _git(
        root,
        ("show", f"{revision}:{path.as_posix()}"),
        executable=git_executable,
    )


def _load_authority_citations(path: Path | str) -> dict[str, dict[str, Any]]:
    source = Path(path).resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot load Atlas source authorities: {exc}")
    citations = value.get("citations") if isinstance(value, dict) else None
    if not isinstance(citations, list):
        _fail("Atlas source-authority registry lacks citations")
    indexed: dict[str, dict[str, Any]] = {}
    for row in citations:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            _fail("Atlas source-authority citation is malformed")
        if row["id"] in indexed:
            _fail(f"duplicate Atlas source-authority citation: {row['id']}")
        indexed[row["id"]] = row
    return indexed


def _material_query_result(
    root: Path,
    query: dict[str, Any],
    citations: dict[str, dict[str, Any]],
    *,
    git_executable: Path | str = "git",
) -> dict[str, Any]:
    query_id = query.get("query_id")
    result_id = query.get("result_id")
    if not isinstance(query_id, str) or query_id not in citations:
        _fail(f"Atlas material citation is unavailable: {query_id}")
    citation = citations[query_id]
    result_match = (
        _MATERIAL_RESULT_RE.fullmatch(result_id)
        if isinstance(result_id, str)
        else None
    )
    revision = citation.get("revision")
    relative = citation.get("path")
    expected_digest = citation.get("file_sha256")
    anchor = citation.get("anchor")
    if (
        result_match is None
        or result_match.group("citation") != query_id
        or result_match.group("revision") != revision
        or citation.get("source_id") != "SRC-PACK"
        or not isinstance(revision, str)
        or not isinstance(relative, str)
        or not isinstance(expected_digest, str)
        or not isinstance(anchor, str)
    ):
        _fail(f"Atlas material citation identity is inconsistent: {query_id}")
    content = _git_blob(
        root,
        revision,
        relative,
        git_executable=git_executable,
    )
    observed_digest = sha256(content).hexdigest()
    if observed_digest != expected_digest:
        _fail(f"pinned Atlas material source digest differs: {query_id}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"pinned Atlas material source is not UTF-8: {exc}")
    anchor_offset = text.find(anchor)
    if anchor_offset < 0:
        _fail(f"pinned Atlas material anchor is absent: {query_id}")
    builder_offset = text.find("new Material.Builder", anchor_offset)
    match = _BUILDER_RE.match(_mask_comments(text), builder_offset)
    if match is None:
        _fail(f"pinned Atlas material anchor cannot be parsed: {query_id}")
    material_id, registry_name, helper = _registration(match)
    block = text[match.start(): match.start() + 4096]
    build_end = block.find(".build()")
    if build_end >= 0:
        block = block[:build_end]
    form = "liquid" if re.search(r"\.liquid\s*\(", block) else "unknown"
    return {
        "source": {
            "revision": revision,
            "path": relative,
            "file_sha256": observed_digest,
        },
        "registration": {
            "material_id": material_id,
            "registry_name": registry_name,
            "namespace_helper": helper,
            "form": form,
        },
    }


def _localization_query_result(
    root: Path,
    query: dict[str, Any],
    *,
    git_executable: Path | str = "git",
) -> dict[str, Any]:
    query_id = query.get("query_id")
    result_id = query.get("result_id")
    query_match = (
        _LOCALIZATION_QUERY_RE.fullmatch(query_id)
        if isinstance(query_id, str)
        else None
    )
    result_match = (
        _SOURCE_RESULT_RE.fullmatch(result_id)
        if isinstance(result_id, str)
        else None
    )
    if query_match is None or result_match is None:
        _fail(f"unsupported Atlas localization query: {query_id}")
    revision = query_match.group("revision")
    relative = query_match.group("path")
    key = query_match.group("key")
    content = _git_blob(
        root,
        revision,
        relative,
        git_executable=git_executable,
    )
    observed_digest = sha256(content).hexdigest()
    if observed_digest != result_match.group("digest"):
        _fail(f"pinned Atlas localization source digest differs: {query_id}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"pinned Atlas localization source is not UTF-8: {exc}")
    matches = [
        line.split("=", 1)[1]
        for line in text.splitlines()
        if line.startswith(key + "=")
    ]
    if len(matches) != 1:
        _fail(f"pinned Atlas localization key is not unique: {query_id}")
    return {
        "source": {
            "revision": revision,
            "path": relative,
            "file_sha256": observed_digest,
        },
        "localization": {"key": key, "value": matches[0]},
    }


def resolve_standard_queries(
    workspace: Path | str,
    queries: Sequence[dict[str, Any]],
    *,
    authority_registry_path: Path | str,
    git_executable: Path | str = "git",
) -> list[dict[str, Any]]:
    """Resolve pinned Atlas query records against exact Git blobs."""

    root, _revision = _workspace_root(
        workspace,
        git_executable=git_executable,
    )
    citations = _load_authority_citations(authority_registry_path)
    resolved: list[dict[str, Any]] = []
    for query in queries:
        query_id = query.get("query_id")
        if isinstance(query_id, str) and query_id.startswith("CIT-"):
            result = _material_query_result(
                root,
                query,
                citations,
                git_executable=git_executable,
            )
        else:
            result = _localization_query_result(
                root,
                query,
                git_executable=git_executable,
            )
        resolved.append({
            "query_id": query_id,
            "result_id": query.get("result_id"),
            "availability": "available",
            "result": result,
        })
    return resolved


__all__ = [
    "MaterialCensusError",
    "census_material_builders",
    "census_material_directory",
    "resolve_standard_queries",
]
