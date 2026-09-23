#!/usr/bin/env python3

"""Build and verify exact revision-bound Atlas source-span primitives."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from workbench_atlas.atlas_causal_provenance_contract import (
    canonical_json,
    canonical_sha256,
)
from workbench_atlas.groovy_parser import (
    CONTROL_KEYWORDS,
    TYPE_KEYWORDS,
    analyze_bytes,
    bracket_pairs,
    member_chain,
)
from workbench_atlas.knowledge_catalog import (
    SNAPSHOT_ID,
    _validate_schema_definition,
    _validate_schema_value,
    read_json,
    sha256_path,
)
from workbench_atlas.layout import (
    DATA_ROOT,
    SCHEMA_ROOT,
    atlas_knowledge_root,
    atlas_source_root,
)
from workbench_atlas.source_lock import (
    SourceLockError,
    load_source_lock,
)
from workbench_pack_program_studio.source_locations import JsonSource, SourceLocationError
from . import profile


SCHEMA_PATH = SCHEMA_ROOT / "atlas-source-span-index-v1.schema.json"
CORPUS_ROOT = DATA_ROOT
SOURCE_LOCK_PATH = profile().resource("source-lock")


def default_output_path() -> Path:
    return atlas_knowledge_root() / SNAPSHOT_ID / "atlas-source-spans.json"

FORMAT = "susy-atlas-source-span-index-v1"
INDEX_PREFIX = "atlas-source-span-index:sha256:"
SPAN_PREFIX = "atlas-source-span:sha256:"
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")

JAVA_POLICY = "ATLAS-JAVA-COMPILER-TREE-SPAN-V1"
GROOVY_SCRIPT_POLICY = "ATLAS-GROOVY-SCRIPT-BYTES-V1"
GROOVY_SYMBOL_POLICY = "ATLAS-GROOVY-LEXICAL-SYMBOL-V1"
GROOVY_CALL_POLICY = "ATLAS-GROOVY-BALANCED-CALL-V1"
JSON_VALUE_POLICY = "ATLAS-JSON-VALUE-SPAN-V1"
LINE_CONFIG_POLICY = "ATLAS-LINE-CONFIG-VALUE-SPAN-V1"

EXTRACTION_POLICIES = [
    {
        "policy_id": GROOVY_CALL_POLICY,
        "language": "groovy",
        "span_semantics": "callee token through balanced closing parenthesis",
        "extraction_state": "lexical-exact-resolution-unverified",
    },
    {
        "policy_id": GROOVY_SCRIPT_POLICY,
        "language": "groovy",
        "span_semantics": "complete non-empty script bytes",
        "extraction_state": "byte-exact",
    },
    {
        "policy_id": GROOVY_SYMBOL_POLICY,
        "language": "groovy",
        "span_semantics": "exact declared identifier token",
        "extraction_state": "lexical-exact-or-candidate-as-recorded",
    },
    {
        "policy_id": JAVA_POLICY,
        "language": "java",
        "span_semantics": "complete javac compiler-tree interval",
        "extraction_state": "syntax-exact-resolution-unverified",
    },
    {
        "policy_id": JSON_VALUE_POLICY,
        "language": "json",
        "span_semantics": "exact JSON value lexeme excluding surrounding whitespace",
        "extraction_state": "syntax-exact",
    },
    {
        "policy_id": LINE_CONFIG_POLICY,
        "language": "line-config",
        "span_semantics": "exact right-hand value bytes excluding surrounding whitespace",
        "extraction_state": "line-syntax-exact",
    },
]
POLICY_IDS = {item["policy_id"] for item in EXTRACTION_POLICIES}

SPAN_KINDS = {
    "call-site",
    "configuration-entry",
    "declaration",
    "field",
    "method",
    "script",
}
LANGUAGES = {"groovy", "java", "json", "line-config"}


class AtlasSourceIndexError(ValueError):
    """Raised when an exact source identity or span cannot be proven."""


@dataclass(frozen=True)
class SourceBinding:
    source_lock_id: str
    source_id: str
    repository: str
    revision: str
    tree: str
    git_root: Path


def _run_git(root: Path, arguments: Sequence[str]) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise AtlasSourceIndexError(
            f"Git command failed for {root}: {' '.join(arguments)}: {message}"
        )
    return process.stdout


def _canonical_repository(value: str) -> str:
    return value.rstrip("/").removesuffix(".git")


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise AtlasSourceIndexError("source path must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AtlasSourceIndexError(f"unsafe source path: {value}")
    return path.as_posix()


def _json_pointer_token(value: str) -> str:
    """Escape one configuration path component using JSON Pointer spelling."""

    return value.replace("~", "~0").replace("/", "~1")


class SourceResolver:
    """Resolve source bytes only from validated locked Git objects."""

    def __init__(
        self,
        *,
        pack_root: Path | None = None,
        source_root: Path | None = None,
    ) -> None:
        if source_root is None:
            source_root = atlas_source_root()
        try:
            source_lock = load_source_lock(SOURCE_LOCK_PATH)
        except SourceLockError as exc:
            raise AtlasSourceIndexError(f"invalid source lock: {exc}") from exc
        pack = source_lock["pack"]
        if pack["snapshot_id"] != SNAPSHOT_ID:
            raise AtlasSourceIndexError("source lock does not bind the knowledge snapshot")
        self.snapshot_id = SNAPSHOT_ID
        self.source_lock_id = source_lock["lock_id"]
        self.pack_commit = pack["revision"]
        self.pack_tree = pack["tree"]
        self._bindings: dict[str, SourceBinding] = {
            "SRC-PACK": SourceBinding(
                source_lock_id=source_lock["lock_id"],
                source_id="SRC-PACK",
                repository=_canonical_repository(pack["repository"]),
                revision=pack["revision"],
                tree=pack["tree"],
                git_root=(
                    pack_root
                    if pack_root is not None
                    else source_root / "SRC-PACK" / pack["revision"]
                ),
            )
        }
        for item in source_lock["sources"]:
            source_id = item["source_id"]
            self._bindings[source_id] = SourceBinding(
                source_lock_id=source_lock["lock_id"],
                source_id=source_id,
                repository=_canonical_repository(item["repository"]),
                revision=item["revision"],
                tree=item["tree"],
                git_root=source_root / source_id / item["revision"],
            )
        self._verified: set[str] = set()
        self._bytes: dict[tuple[str, str], bytes] = {}

    @property
    def bindings(self) -> dict[str, SourceBinding]:
        return dict(self._bindings)

    def binding(self, source_id: str) -> SourceBinding:
        try:
            binding = self._bindings[source_id]
        except KeyError as exc:
            raise AtlasSourceIndexError(f"source is not locked: {source_id}") from exc
        self._verify(binding)
        return binding

    def _verify(self, binding: SourceBinding) -> None:
        if binding.source_id in self._verified:
            return
        if not (binding.git_root / ".git").exists():
            raise AtlasSourceIndexError(
                f"locked source Git repository is missing: {binding.git_root}"
            )
        revision = _run_git(
            binding.git_root, ["rev-parse", f"{binding.revision}^{{commit}}"]
        ).decode("ascii").strip()
        tree = _run_git(
            binding.git_root, ["rev-parse", f"{binding.revision}^{{tree}}"]
        ).decode("ascii").strip()
        if revision != binding.revision or tree != binding.tree:
            raise AtlasSourceIndexError(
                f"locked Git identity differs for {binding.source_id}"
            )
        if binding.source_id != "SRC-PACK":
            head = _run_git(binding.git_root, ["rev-parse", "HEAD"]).decode(
                "ascii"
            ).strip()
            dirty = _run_git(binding.git_root, ["status", "--porcelain"])
            if head != binding.revision or dirty:
                raise AtlasSourceIndexError(
                    f"source cache identity differs for {binding.source_id}"
                )
        self._verified.add(binding.source_id)

    def read_bytes(self, source_id: str, path: str) -> bytes:
        path = _safe_path(path)
        key = (source_id, path)
        if key in self._bytes:
            return self._bytes[key]
        binding = self.binding(source_id)
        value = _run_git(
            binding.git_root, ["show", f"{binding.revision}:{path}"]
        )
        self._bytes[key] = value
        return value

    def list_paths(
        self,
        source_id: str,
        *,
        prefix: str = "",
        suffix: str | None = None,
    ) -> list[str]:
        binding = self.binding(source_id)
        arguments = ["ls-tree", "-r", "--name-only", binding.revision]
        if prefix:
            arguments.extend(["--", _safe_path(prefix)])
        paths = _run_git(binding.git_root, arguments).decode("utf-8").splitlines()
        if suffix is not None:
            paths = [path for path in paths if path.endswith(suffix)]
        return sorted((_safe_path(path) for path in paths), key=lambda item: item.encode())


def _line_number(source: bytes, offset: int) -> int:
    return source[:offset].count(b"\n") + 1


def _span_id(identity: dict[str, Any]) -> str:
    return SPAN_PREFIX + canonical_sha256(identity)


def build_span_record(
    resolver: SourceResolver,
    *,
    source_id: str,
    path: str,
    symbol: str,
    span_kind: str,
    language: str,
    byte_start: int,
    byte_end: int,
    extraction_policy_id: str,
    extraction_state: str,
    syntax_kind: str,
) -> dict[str, Any]:
    if span_kind not in SPAN_KINDS:
        raise AtlasSourceIndexError(f"unsupported source span kind: {span_kind}")
    if language not in LANGUAGES:
        raise AtlasSourceIndexError(f"unsupported source language: {language}")
    if extraction_policy_id not in POLICY_IDS:
        raise AtlasSourceIndexError(
            f"unknown source span extraction policy: {extraction_policy_id}"
        )
    if not isinstance(symbol, str) or not symbol:
        raise AtlasSourceIndexError("source span symbol is empty")
    source = resolver.read_bytes(source_id, path)
    if (
        type(byte_start) is not int
        or type(byte_end) is not int
        or byte_start < 0
        or byte_end < byte_start
        or byte_end >= len(source)
    ):
        raise AtlasSourceIndexError(
            f"invalid inclusive byte interval for {source_id}:{path}"
        )
    binding = resolver.binding(source_id)
    span = source[byte_start : byte_end + 1]
    identity = {
        "source_lock_id": binding.source_lock_id,
        "source_id": source_id,
        "repository": binding.repository,
        "revision": binding.revision,
        "tree": binding.tree,
        "path": _safe_path(path),
        "symbol": symbol,
        "line_start": _line_number(source, byte_start),
        "line_end": _line_number(source, byte_end),
        "byte_start": byte_start,
        "byte_end": byte_end,
        "file_sha256": hashlib.sha256(source).hexdigest(),
        "span_sha256": hashlib.sha256(span).hexdigest(),
        "extraction_policy_id": extraction_policy_id,
    }
    return {
        "source_span_id": _span_id(identity),
        "span_kind": span_kind,
        "language": language,
        "identity": identity,
        "extraction": {
            "state": extraction_state,
            "syntax_kind": syntax_kind,
        },
    }


def _groovy_lifecycle(path: str) -> str:
    phase = path.split("/", 2)[1] if path.startswith("groovy/") else ""
    try:
        return {
            "classes": "script-library",
            "globals": "script-library",
            "material": "script-registration-unverified",
            "postInit": "declared-post-init",
            "preInit": "declared-pre-init",
            "prePostInit": "declared-pre-post-init",
        }[phase]
    except KeyError as exc:
        raise AtlasSourceIndexError(f"unknown Groovy root for {path}") from exc


def _char_byte_offsets(source: str) -> list[int]:
    result = [0]
    total = 0
    for character in source:
        total += len(character.encode("utf-8"))
        result.append(total)
    return result


def _groovy_script_symbol(path: str) -> str:
    return path.removeprefix("groovy/").removesuffix(".groovy").replace("/", ".")


def index_groovy_file(
    resolver: SourceResolver,
    path: str,
) -> list[dict[str, Any]]:
    source_bytes = resolver.read_bytes("SRC-PACK", path)
    if not source_bytes:
        raise AtlasSourceIndexError(f"Groovy script is empty: {path}")
    file_row = {"path": path, "lifecycle": _groovy_lifecycle(path)}
    try:
        analysis = analyze_bytes(file_row, source_bytes)
        pairs = bracket_pairs(analysis.tokens, path)
    except ValueError as exc:
        raise AtlasSourceIndexError(f"cannot analyze locked Groovy bytes: {exc}") from exc
    offsets = _char_byte_offsets(analysis.source)
    script_symbol = _groovy_script_symbol(path)
    records = [
        build_span_record(
            resolver,
            source_id="SRC-PACK",
            path=path,
            symbol=script_symbol,
            span_kind="script",
            language="groovy",
            byte_start=0,
            byte_end=len(source_bytes) - 1,
            extraction_policy_id=GROOVY_SCRIPT_POLICY,
            extraction_state="byte-exact",
            syntax_kind="groovy-script",
        )
    ]
    token_locations: dict[tuple[int, int, str], list[Any]] = {}
    for token in analysis.tokens:
        token_locations.setdefault(
            (token.line, token.column, token.value), []
        ).append(token)
    for declaration in analysis.declarations:
        location = (
            int(declaration["line"]),
            int(declaration["column"]),
            declaration["symbol_name"],
        )
        matches = token_locations.get(location, [])
        if len(matches) != 1:
            raise AtlasSourceIndexError(
                f"Groovy declaration does not resolve to one exact token: "
                f"{path}:{location[0]}:{location[1]}:{location[2]}"
            )
        token = matches[0]
        symbol_kind = declaration["symbol_kind"]
        span_kind = (
            "method"
            if symbol_kind in {"constructor", "method"}
            else "field"
            if symbol_kind == "field"
            else "declaration"
        )
        symbol = declaration["qualified_hint"] or (
            f"{script_symbol}#{symbol_kind}:{declaration['symbol_name']}"
        )
        records.append(
            build_span_record(
                resolver,
                source_id="SRC-PACK",
                path=path,
                symbol=symbol,
                span_kind=span_kind,
                language="groovy",
                byte_start=offsets[token.start],
                byte_end=offsets[token.end] - 1,
                extraction_policy_id=GROOVY_SYMBOL_POLICY,
                extraction_state=declaration["extraction_state"],
                syntax_kind=symbol_kind,
            )
        )
    declaration_parens = {item.open_paren_index for item in analysis.callables}
    excluded = analysis.excluded_reference_lines
    for index, token in enumerate(analysis.tokens[:-1]):
        if (
            token.kind != "IDENT"
            or token.value in CONTROL_KEYWORDS | TYPE_KEYWORDS
            or token.line in excluded
            or analysis.tokens[index + 1].value != "("
            or index + 1 not in pairs
            or index + 1 in declaration_parens
        ):
            continue
        close = analysis.tokens[pairs[index + 1]]
        callee = member_chain(analysis.tokens, index)
        records.append(
            build_span_record(
                resolver,
                source_id="SRC-PACK",
                path=path,
                symbol=(
                    f"{script_symbol}#call:{callee}@{token.line}:{token.column}"
                ),
                span_kind="call-site",
                language="groovy",
                byte_start=offsets[token.start],
                byte_end=offsets[close.end] - 1,
                extraction_policy_id=GROOVY_CALL_POLICY,
                extraction_state="lexical-exact-resolution-unverified",
                syntax_kind="method-invocation-candidate",
            )
        )
    return _unique_records(records)


def index_java_rows(
    resolver: SourceResolver,
    source_id: str,
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    binding = resolver.binding(source_id)
    records: list[dict[str, Any]] = []
    for row in rows:
        required = {
            "byte_end",
            "byte_start",
            "extraction_policy_id",
            "file_sha256",
            "id",
            "kind",
            "line",
            "line_end",
            "line_start",
            "modifiers",
            "name",
            "owner",
            "path",
            "revision",
            "signature",
            "source_id",
            "span_sha256",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise AtlasSourceIndexError("Java compiler-tree row fields differ")
        if (
            row["source_id"] != source_id
            or row["revision"] != binding.revision
            or row["extraction_policy_id"] != JAVA_POLICY
        ):
            raise AtlasSourceIndexError(
                f"Java compiler-tree row identity differs for {source_id}"
            )
        kind = row["kind"]
        span_kind = {
            "call-site": "call-site",
            "field": "field",
            "method": "method",
            "import": "declaration",
            "type": "declaration",
        }.get(kind)
        if span_kind is None:
            raise AtlasSourceIndexError(f"unsupported Java syntax kind: {kind}")
        record = build_span_record(
            resolver,
            source_id=source_id,
            path=row["path"],
            symbol=row["name"],
            span_kind=span_kind,
            language="java",
            byte_start=row["byte_start"],
            byte_end=row["byte_end"],
            extraction_policy_id=JAVA_POLICY,
            extraction_state="syntax-exact-resolution-unverified",
            syntax_kind=kind,
        )
        identity = record["identity"]
        if (
            identity["line_start"] != row["line_start"]
            or identity["line_end"] != row["line_end"]
            or identity["file_sha256"] != row["file_sha256"]
            or identity["span_sha256"] != row["span_sha256"]
        ):
            raise AtlasSourceIndexError(
                f"Java compiler-tree span does not match locked bytes: "
                f"{source_id}:{row['path']}:{row['name']}"
            )
        records.append(record)
    return _unique_records(records)


def json_config_entries(
    resolver: SourceResolver,
    source_id: str,
    path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = resolver.read_bytes(source_id, path)
    try:
        parsed = JsonSource(source, path).parse()
    except SourceLocationError as exc:
        raise AtlasSourceIndexError(str(exc)) from exc
    spans: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    for pointer, start, end, value in parsed:
        span = build_span_record(
            resolver,
            source_id=source_id,
            path=path,
            symbol=f"json-pointer:{pointer}",
            span_kind="configuration-entry",
            language="json",
            byte_start=start,
            byte_end=end,
            extraction_policy_id=JSON_VALUE_POLICY,
            extraction_state="syntax-exact",
            syntax_kind="json-value",
        )
        spans.append(span)
        selections.append(
            {
                "source_span_id": span["source_span_id"],
                "key_path": pointer,
                "selected_value": value,
                "selected_value_sha256": canonical_sha256(value),
            }
        )
    return _unique_records(spans), selections


def line_config_entries(
    resolver: SourceResolver,
    source_id: str,
    path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Index exact one-line ``key=value`` and Forge ``T:key=value`` entries."""

    source = resolver.read_bytes(source_id, path)
    spans: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    categories: list[str] = []
    offset = 0
    for raw_line in source.splitlines(keepends=True):
        content = raw_line.rstrip(b"\r\n")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AtlasSourceIndexError(
                f"line configuration is not UTF-8: {path}: {exc}"
            ) from exc
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            offset += len(raw_line)
            continue
        category = re.fullmatch(r"([A-Za-z0-9_. -]+)\s*\{", stripped)
        if category:
            categories.append(category.group(1).strip())
            offset += len(raw_line)
            continue
        if stripped == "}":
            if categories:
                categories.pop()
            offset += len(raw_line)
            continue
        match = re.match(r"^\s*(?:(?P<type>[BIDS]):)?(?P<key>[^=]+?)=(?P<value>.*)$", text)
        if match is None:
            offset += len(raw_line)
            continue
        key = match.group("key").strip()
        raw_value = match.group("value")
        left_trim = len(raw_value) - len(raw_value.lstrip())
        right_value = raw_value.rstrip()
        if not right_value:
            offset += len(raw_line)
            continue
        value_start_character = match.start("value") + left_trim
        value_end_character = match.start("value") + len(right_value)
        prefix = text[:value_start_character].encode("utf-8")
        value_bytes = text[value_start_character:value_end_character].encode("utf-8")
        start = offset + len(prefix)
        end = start + len(value_bytes) - 1
        key_path = "/" + "/".join(
            _json_pointer_token(item) for item in [*categories, key]
        )
        span = build_span_record(
            resolver,
            source_id=source_id,
            path=path,
            symbol=f"config-key:{key_path}",
            span_kind="configuration-entry",
            language="line-config",
            byte_start=start,
            byte_end=end,
            extraction_policy_id=LINE_CONFIG_POLICY,
            extraction_state="line-syntax-exact",
            syntax_kind="typed-key-value" if match.group("type") else "key-value",
        )
        spans.append(span)
        selections.append(
            {
                "source_span_id": span["source_span_id"],
                "key_path": key_path,
                "selected_value": text[value_start_character:value_end_character],
                "selected_value_sha256": canonical_sha256(
                    text[value_start_character:value_end_character]
                ),
            }
        )
        offset += len(raw_line)
    return _unique_records(spans), selections


def _unique_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        identifier = record["source_span_id"]
        if identifier in by_id and by_id[identifier] != record:
            raise AtlasSourceIndexError(
                f"source span ID collision with different records: {identifier}"
            )
        by_id[identifier] = record
    return sorted(
        by_id.values(),
        key=lambda item: (
            item["identity"]["source_id"].encode(),
            item["identity"]["path"].encode(),
            item["identity"]["byte_start"],
            item["identity"]["byte_end"],
            item["identity"]["symbol"].encode(),
        ),
    )


def create_index(
    records: Iterable[dict[str, Any]],
    resolver: SourceResolver,
) -> dict[str, Any]:
    records = _unique_records(records)
    if not records:
        raise AtlasSourceIndexError("source span index cannot be empty")
    document: dict[str, Any] = {
        "schema_version": 1,
        "format": FORMAT,
        "index_id": "",
        "snapshot_id": resolver.snapshot_id,
        "source_lock_id": resolver.source_lock_id,
        "source_lock_sha256": sha256_path(SOURCE_LOCK_PATH),
        "pack_commit": resolver.pack_commit,
        "pack_tree": resolver.pack_tree,
        "extraction_policies": copy.deepcopy(EXTRACTION_POLICIES),
        "record_count": len(records),
        "records": records,
    }
    document["index_id"] = INDEX_PREFIX + canonical_sha256(
        {key: value for key, value in document.items() if key != "index_id"}
    )
    return document


def validate_index(
    document: dict[str, Any],
    resolver: SourceResolver | None = None,
) -> dict[str, Any]:
    if resolver is None:
        resolver = SourceResolver()
    try:
        schema = read_json(SCHEMA_PATH)
        _validate_schema_definition(schema, SCHEMA_PATH.name)
        _validate_schema_value(document, schema, FORMAT)
    except (OSError, ValueError) as exc:
        raise AtlasSourceIndexError(f"source span index schema failed: {exc}") from exc
    if set(document) != {
        "schema_version",
        "format",
        "index_id",
        "snapshot_id",
        "source_lock_id",
        "source_lock_sha256",
        "pack_commit",
        "pack_tree",
        "extraction_policies",
        "record_count",
        "records",
    }:
        raise AtlasSourceIndexError("source span index fields differ")
    expected_id = INDEX_PREFIX + canonical_sha256(
        {key: value for key, value in document.items() if key != "index_id"}
    )
    if document["index_id"] != expected_id:
        raise AtlasSourceIndexError("source span index content ID differs")
    if (
        document["snapshot_id"] != resolver.snapshot_id
        or document["source_lock_id"] != resolver.source_lock_id
        or document["source_lock_sha256"] != sha256_path(SOURCE_LOCK_PATH)
        or document["pack_commit"] != resolver.pack_commit
        or document["pack_tree"] != resolver.pack_tree
    ):
        raise AtlasSourceIndexError("source span index lock binding differs")
    if document["extraction_policies"] != EXTRACTION_POLICIES:
        raise AtlasSourceIndexError("source span extraction policies differ")
    records = document["records"]
    if document["record_count"] != len(records) or records != _unique_records(records):
        raise AtlasSourceIndexError("source span records are not unique and canonical")
    for record in records:
        if set(record) != {
            "source_span_id",
            "span_kind",
            "language",
            "identity",
            "extraction",
        }:
            raise AtlasSourceIndexError("source span record fields differ")
        identity = record["identity"]
        if set(identity) != {
            "source_lock_id",
            "source_id",
            "repository",
            "revision",
            "tree",
            "path",
            "symbol",
            "line_start",
            "line_end",
            "byte_start",
            "byte_end",
            "file_sha256",
            "span_sha256",
            "extraction_policy_id",
        }:
            raise AtlasSourceIndexError("source span identity fields differ")
        rebuilt = build_span_record(
            resolver,
            source_id=identity["source_id"],
            path=identity["path"],
            symbol=identity["symbol"],
            span_kind=record["span_kind"],
            language=record["language"],
            byte_start=identity["byte_start"],
            byte_end=identity["byte_end"],
            extraction_policy_id=identity["extraction_policy_id"],
            extraction_state=record["extraction"]["state"],
            syntax_kind=record["extraction"]["syntax_kind"],
        )
        if rebuilt != record:
            raise AtlasSourceIndexError(
                f"source span does not verify against locked bytes: "
                f"{identity['source_id']}:{identity['path']}:{identity['symbol']}"
            )
    return document


def generate(
    *,
    config_paths: Sequence[str],
    pack_root: Path | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    if source_root is None:
        source_root = atlas_source_root()
    resolver = SourceResolver(pack_root=pack_root, source_root=source_root)
    records: list[dict[str, Any]] = []
    for path in resolver.list_paths("SRC-PACK", prefix="groovy", suffix=".groovy"):
        records.extend(index_groovy_file(resolver, path))
    for path in config_paths:
        if path.endswith(".json"):
            spans, _ = json_config_entries(resolver, "SRC-PACK", path)
        else:
            spans, _ = line_config_entries(resolver, "SRC-PACK", path)
        records.extend(spans)
    document = create_index(records, resolver)
    validate_index(document, resolver)
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    action = result.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    result.add_argument("--output", type=Path)
    result.add_argument(
        "--pack-root",
        type=Path,
        help="exact Supersymmetry Git checkout; defaults to the managed source store",
    )
    result.add_argument("--source-root", type=Path)
    result.add_argument(
        "--config",
        action="append",
        default=[],
        help="locked SRC-PACK configuration path (repeatable)",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        source_root = args.source_root or atlas_source_root()
        output = args.output or default_output_path()
        if args.write:
            config_paths = args.config or ["groovy/runConfig.json"]
            document = generate(
                config_paths=config_paths,
                pack_root=args.pack_root,
                source_root=source_root,
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(canonical_json(document) + b"\n")
        else:
            document = json.loads(output.read_text(encoding="utf-8"))
            validate_index(
                document,
                SourceResolver(pack_root=args.pack_root, source_root=source_root),
            )
        print(
            f"Atlas source span index passed: {document['record_count']} records; "
            f"{document['index_id']}"
        )
        return 0
    except (AtlasSourceIndexError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
