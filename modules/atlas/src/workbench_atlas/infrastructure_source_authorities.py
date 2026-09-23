#!/usr/bin/env python3

"""Validate and verify infrastructure source-authority citations."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Sequence

if __package__:
    from .layout import DATA_ROOT, atlas_source_root
    from . import source_lock
else:  # Direct module loading.
    from workbench_atlas.layout import DATA_ROOT, atlas_source_root
    import workbench_atlas.source_lock as source_lock


DEFAULT_REGISTRY_PATH = DATA_ROOT / "infrastructure-source-authorities-v1.json"
REQUIRED_AUTHORITY_SOURCES = {
    "SRC-FORGE",
    "SRC-GREGICALITY-MULTIBLOCKS",
    "SRC-GTCEU",
    "SRC-PACK",
    "SRC-SUSYCORE",
}
TOP_LEVEL_FIELDS = {
    "schema_version",
    "registry_id",
    "snapshot_id",
    "source_lock_id",
    "pack_commit",
    "citations",
    "authority_sets",
}
CITATION_FIELDS = {
    "id",
    "source_id",
    "revision",
    "path",
    "symbol",
    "line_start",
    "line_end",
    "file_sha256",
    "anchor",
    "authority_roles",
    "semantic_domains",
}
AUTHORITY_SET_FIELDS = {
    "id",
    "semantic_kind",
    "citation_ids",
    "required_source_ids",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class InfrastructureSourceAuthorityError(ValueError):
    """Raised when source-authority identity or content is invalid."""


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InfrastructureSourceAuthorityError(
            f"cannot read {label} {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise InfrastructureSourceAuthorityError(
            f"{label} must contain a JSON object"
        )
    return value


def load_authority_registry(
    path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    return _load_json_object(path, "infrastructure source-authority registry")


def _exact_fields(
    record: dict[str, Any],
    expected: set[str],
    label: str,
) -> None:
    actual = set(record)
    if actual != expected:
        raise InfrastructureSourceAuthorityError(
            f"{label} fields differ: missing={sorted(expected - actual)} "
            f"unexpected={sorted(actual - expected)}"
        )


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise InfrastructureSourceAuthorityError(
            f"{label} must be a list of objects"
        )
    return value


def _tokens(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str) or TOKEN_RE.fullmatch(item) is None
            for item in value
        )
        or value != sorted(set(value))
    ):
        raise InfrastructureSourceAuthorityError(
            f"{label} must be a non-empty sorted unique token list"
        )
    return value


def _identifiers(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str) or ID_RE.fullmatch(item) is None
            for item in value
        )
        or value != sorted(set(value))
    ):
        raise InfrastructureSourceAuthorityError(
            f"{label} must be a non-empty sorted unique ID list"
        )
    return value


def _validate_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise InfrastructureSourceAuthorityError(
            f"{label} must be a non-empty POSIX repository path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InfrastructureSourceAuthorityError(
            f"{label} must be a safe repository-relative path"
        )
    return value


def validate_authority_registry(
    document: dict[str, Any],
    source_lock: dict[str, Any],
) -> dict[str, Any]:
    """Validate authority identity without requiring hydrated source trees."""

    _exact_fields(document, TOP_LEVEL_FIELDS, "source-authority registry")
    if document.get("schema_version") != 1:
        raise InfrastructureSourceAuthorityError(
            "source-authority schema_version must be 1"
        )
    if (
        document.get("registry_id")
        != "SUSY-INFRASTRUCTURE-SOURCE-AUTHORITIES-0001"
    ):
        raise InfrastructureSourceAuthorityError(
            "source-authority registry_id differs"
        )
    pack = source_lock.get("pack")
    if not isinstance(pack, dict):
        raise InfrastructureSourceAuthorityError(
            "source lock omits its pack binding"
        )
    if document.get("snapshot_id") != pack.get("snapshot_id"):
        raise InfrastructureSourceAuthorityError(
            "source-authority snapshot_id differs from the source lock"
        )
    if document.get("source_lock_id") != source_lock.get("lock_id"):
        raise InfrastructureSourceAuthorityError(
            "source-authority source_lock_id differs from the source lock"
        )
    pack_commit = document.get("pack_commit")
    if (
        not isinstance(pack_commit, str)
        or COMMIT_RE.fullmatch(pack_commit) is None
        or pack_commit != pack.get("revision")
    ):
        raise InfrastructureSourceAuthorityError(
            "source-authority pack_commit differs from the source lock"
        )

    locked_revisions = {
        row["source_id"]: row["revision"]
        for row in _objects(source_lock.get("sources"), "source lock sources")
        if isinstance(row.get("source_id"), str)
        and isinstance(row.get("revision"), str)
    }
    expected_revisions = {**locked_revisions, "SRC-PACK": pack_commit}

    citations = _objects(document.get("citations"), "citations")
    if not citations:
        raise InfrastructureSourceAuthorityError("citations must not be empty")
    citation_ids: list[str] = []
    citation_sources: set[str] = set()
    citations_by_id: dict[str, dict[str, Any]] = {}
    for index, citation in enumerate(citations):
        label = f"citations[{index}]"
        _exact_fields(citation, CITATION_FIELDS, label)
        identifier = citation.get("id")
        if (
            not isinstance(identifier, str)
            or ID_RE.fullmatch(identifier) is None
        ):
            raise InfrastructureSourceAuthorityError(
                f"{label}.id is not a stable identifier"
            )
        source_id = citation.get("source_id")
        revision = citation.get("revision")
        if source_id not in expected_revisions:
            raise InfrastructureSourceAuthorityError(
                f"{label}.source_id is not pinned by this snapshot: {source_id}"
            )
        if revision != expected_revisions[source_id]:
            raise InfrastructureSourceAuthorityError(
                f"{label}.revision differs from pinned {source_id}"
            )
        _validate_path(citation.get("path"), f"{label}.path")
        for field in ("symbol", "anchor"):
            value = citation.get(field)
            if not isinstance(value, str) or not value.strip():
                raise InfrastructureSourceAuthorityError(
                    f"{label}.{field} must be a non-empty string"
                )
        line_start = citation.get("line_start")
        line_end = citation.get("line_end")
        if (
            not isinstance(line_start, int)
            or isinstance(line_start, bool)
            or not isinstance(line_end, int)
            or isinstance(line_end, bool)
            or line_start < 1
            or line_end < line_start
        ):
            raise InfrastructureSourceAuthorityError(
                f"{label} has an invalid inclusive line span"
            )
        if SHA256_RE.fullmatch(str(citation.get("file_sha256"))) is None:
            raise InfrastructureSourceAuthorityError(
                f"{label}.file_sha256 is not lowercase SHA-256"
            )
        _tokens(citation.get("authority_roles"), f"{label}.authority_roles")
        _tokens(citation.get("semantic_domains"), f"{label}.semantic_domains")
        citation_ids.append(identifier)
        citation_sources.add(str(source_id))
        citations_by_id[identifier] = citation

    if citation_ids != sorted(citation_ids) or len(citation_ids) != len(
        set(citation_ids)
    ):
        raise InfrastructureSourceAuthorityError(
            "citations must have sorted unique IDs"
        )
    if not REQUIRED_AUTHORITY_SOURCES.issubset(citation_sources):
        raise InfrastructureSourceAuthorityError(
            "source-authority citations do not cover required sources: "
            + ", ".join(sorted(REQUIRED_AUTHORITY_SOURCES - citation_sources))
        )

    authority_sets = _objects(
        document.get("authority_sets"),
        "authority_sets",
    )
    if not authority_sets:
        raise InfrastructureSourceAuthorityError(
            "authority_sets must not be empty"
        )
    authority_ids: list[str] = []
    semantic_kinds: list[str] = []
    referenced: set[str] = set()
    for index, authority in enumerate(authority_sets):
        label = f"authority_sets[{index}]"
        _exact_fields(authority, AUTHORITY_SET_FIELDS, label)
        identifier = authority.get("id")
        if (
            not isinstance(identifier, str)
            or ID_RE.fullmatch(identifier) is None
        ):
            raise InfrastructureSourceAuthorityError(
                f"{label}.id is not a stable identifier"
            )
        semantic_kind = authority.get("semantic_kind")
        if (
            not isinstance(semantic_kind, str)
            or TOKEN_RE.fullmatch(semantic_kind) is None
        ):
            raise InfrastructureSourceAuthorityError(
                f"{label}.semantic_kind is not a token"
            )
        bound_ids = _identifiers(
            authority.get("citation_ids"),
            f"{label}.citation_ids",
        )
        unknown = set(bound_ids) - set(citations_by_id)
        if unknown:
            raise InfrastructureSourceAuthorityError(
                f"{label} references unknown citations: {sorted(unknown)}"
            )
        required_sources = _identifiers(
            authority.get("required_source_ids"),
            f"{label}.required_source_ids",
        )
        bound_sources = {
            str(citations_by_id[citation_id]["source_id"])
            for citation_id in bound_ids
        }
        if bound_sources != set(required_sources):
            raise InfrastructureSourceAuthorityError(
                f"{label} citation sources differ from required_source_ids"
            )
        authority_ids.append(identifier)
        semantic_kinds.append(semantic_kind)
        referenced.update(bound_ids)

    if authority_ids != sorted(authority_ids) or len(authority_ids) != len(
        set(authority_ids)
    ):
        raise InfrastructureSourceAuthorityError(
            "authority_sets must have sorted unique IDs"
        )
    if len(semantic_kinds) != len(set(semantic_kinds)):
        raise InfrastructureSourceAuthorityError(
            "authority_sets semantic_kind values must be unique"
        )
    unreferenced = set(citations_by_id) - referenced
    if unreferenced:
        raise InfrastructureSourceAuthorityError(
            f"citations are not bound to an authority set: {sorted(unreferenced)}"
        )
    return {
        "citation_count": len(citations),
        "authority_set_count": len(authority_sets),
        "source_ids": sorted(citation_sources),
    }


def _pack_file_at_revision(
    pack_root: Path,
    revision: str,
    path: str,
) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(pack_root), "show", f"{revision}:{path}"],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InfrastructureSourceAuthorityError(
            f"cannot read SRC-PACK {revision}:{path}: {exc}"
        ) from exc


def verify_authority_sources(
    document: dict[str, Any],
    source_lock: dict[str, Any],
    *,
    source_root: Path | None = None,
    pack_root: Path | None = None,
) -> dict[str, Any]:
    """Verify every citation against exact source bytes and line anchors."""

    structural = validate_authority_registry(document, source_lock)
    if source_root is None:
        source_root = atlas_source_root()
    if pack_root is None:
        pack_root = source_root / "SRC-PACK" / source_lock["pack"]["revision"]
    source_counts: Counter[str] = Counter()
    files: set[tuple[str, str]] = set()
    for citation in document["citations"]:
        source_id = str(citation["source_id"])
        revision = str(citation["revision"])
        path = str(citation["path"])
        if source_id == "SRC-PACK":
            data = _pack_file_at_revision(pack_root, revision, path)
        else:
            source_path = source_root / source_id / revision / path
            try:
                data = source_path.read_bytes()
            except OSError as exc:
                raise InfrastructureSourceAuthorityError(
                    f"cannot read {source_id} {revision}:{path}: {exc}"
                ) from exc
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != citation["file_sha256"]:
            raise InfrastructureSourceAuthorityError(
                f"{citation['id']} file SHA-256 differs: "
                f"expected {citation['file_sha256']} actual {actual_sha256}"
            )
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise InfrastructureSourceAuthorityError(
                f"{citation['id']} source is not UTF-8: {exc}"
            ) from exc
        line_start = int(citation["line_start"])
        line_end = int(citation["line_end"])
        if line_end > len(lines):
            raise InfrastructureSourceAuthorityError(
                f"{citation['id']} line span ends beyond {len(lines)}"
            )
        span = "\n".join(lines[line_start - 1 : line_end])
        if citation["anchor"] not in span:
            raise InfrastructureSourceAuthorityError(
                f"{citation['id']} anchor is absent from its cited line span"
            )
        source_counts[source_id] += 1
        files.add((source_id, path))

    return {
        **structural,
        "verified_file_count": len(files),
        "verified_source_counts": {
            key: source_counts[key] for key in sorted(source_counts)
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
    )
    selected_lock = parser.add_mutually_exclusive_group()
    selected_lock.add_argument(
        "--source-lock",
        type=Path,
    )
    selected_lock.add_argument("--pack-profile")
    parser.add_argument(
        "--verify-files",
        action="store_true",
        help="read all exact source bytes and verify hashes and line anchors",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
    )
    parser.add_argument("--pack-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.pack_profile is not None and args.verify_files and args.source_root is None:
            raise InfrastructureSourceAuthorityError(
                "selected profile file verification requires --source-root"
            )
        document = load_authority_registry(args.registry)
        source_lock_document = source_lock.load_source_lock(
            args.source_lock, pack_profile=args.pack_profile
        )
        result = (
            verify_authority_sources(
                document,
                source_lock_document,
                source_root=args.source_root or atlas_source_root(),
                pack_root=args.pack_root,
            )
            if args.verify_files
            else validate_authority_registry(document, source_lock_document)
        )
    except (
        InfrastructureSourceAuthorityError,
        source_lock.SourceLockError,
    ) as exc:
        print(f"infrastructure source authorities invalid: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
