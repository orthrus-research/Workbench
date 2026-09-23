#!/usr/bin/env python3

"""Load and validate the canonical Supersymmetry source lock."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from workbench_api.modules import ModuleError
from workbench_api.profiles import profiles
from workbench_atlas.layout import HISTORICAL_SOURCE_LOCK_PATH


# The packaged historical lock reopens Atlas's fixed V1 corpus without a
# current pack installation. Selected current locks use the profile API.
SOURCE_LOCK_PATH = HISTORICAL_SOURCE_LOCK_PATH


FORMAT = "workbench-source-lock-v3"
LOCK_ID = "supersymmetry-legacy-forge"
PACK_SOURCE_ID = "SRC-PACK"
PACK_SNAPSHOT_ID = "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
SOURCE_IDS = (
    "SRC-FORGE",
    "SRC-GREGICALITY-MULTIBLOCKS",
    "SRC-GROOVYSCRIPT",
    "SRC-GTCEU",
    "SRC-SUPERCRITICAL",
    "SRC-SUSYCORE",
)

TOP_LEVEL_FIELDS = {"format", "schema_version", "lock_id", "pack", "sources"}
PACK_FIELDS = {
    "source_id",
    "snapshot_id",
    "version",
    "repository",
    "revision",
    "tree",
}
SOURCE_FIELDS = {
    "source_id",
    "repository",
    "revision",
    "tree",
    "license",
    "scope",
}
GIT_ID_RE = re.compile(r"^[0-9a-f]{40}$")
SOURCE_ID_RE = re.compile(r"^SRC-[A-Z0-9-]+$")


class SourceLockError(ValueError):
    """Raised when the canonical source lock is unavailable or invalid."""


def profile_source_lock_path(profile_id: str) -> Path:
    """Resolve a selected owner's lock through its admitted profile resource."""

    if not isinstance(profile_id, str) or not profile_id:
        raise SourceLockError("a pack profile ID is required")
    selected = next((owner for owner in profiles() if owner.id == profile_id), None)
    if selected is None or selected.kind != "pack" or "source-lock" not in selected.resources:
        raise SourceLockError(
            f"pack profile {profile_id!r} has no available source-lock resource"
        )
    try:
        return selected.resource("source-lock")
    except ModuleError as exc:
        raise SourceLockError(
            f"pack profile {profile_id!r} source-lock resource is unavailable: {exc}"
        ) from exc


def _strict_json_loads(text: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON number: {token}")

    def parse_finite_float(token: str) -> float:
        value = float(token)
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number: {token}")
        return value

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    return json.loads(
        text,
        parse_float=parse_finite_float,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _exact_fields(record: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SourceLockError(f"{label} must be an object")
    actual = set(record)
    if actual != expected:
        raise SourceLockError(
            f"{label} fields differ: missing={sorted(expected - actual)} "
            f"unexpected={sorted(actual - expected)}"
        )
    return record


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SourceLockError(f"{label} must be a non-empty trimmed string")
    return value


def _git_id(value: Any, label: str) -> str:
    text = _text(value, label)
    if GIT_ID_RE.fullmatch(text) is None:
        raise SourceLockError(f"{label} must be a full lowercase Git identity")
    return text


def _repository(value: Any, label: str) -> str:
    text = _text(value, label)
    if not text.startswith("https://") or not text.endswith(".git"):
        raise SourceLockError(f"{label} must be a canonical HTTPS Git URL")
    if any(character.isspace() for character in text):
        raise SourceLockError(f"{label} must not contain whitespace")
    return text


def validate_source_lock(document: Any) -> dict[str, Any]:
    """Validate the only supported source-lock format."""

    lock = _exact_fields(document, TOP_LEVEL_FIELDS, "source lock")
    if lock.get("format") != FORMAT or type(lock.get("schema_version")) is not int:
        raise SourceLockError(f"source lock must use {FORMAT} schema version 3")
    if lock["schema_version"] != 3:
        raise SourceLockError(f"source lock must use {FORMAT} schema version 3")
    if lock.get("lock_id") != LOCK_ID:
        raise SourceLockError("source lock identity differs")

    pack = _exact_fields(lock.get("pack"), PACK_FIELDS, "source lock pack")
    if pack.get("source_id") != PACK_SOURCE_ID:
        raise SourceLockError(f"source lock pack source_id must be {PACK_SOURCE_ID}")
    if pack.get("snapshot_id") != PACK_SNAPSHOT_ID:
        raise SourceLockError("source lock pack snapshot identity differs")
    _text(pack.get("version"), "source lock pack version")
    _repository(pack.get("repository"), "source lock pack repository")
    _git_id(pack.get("revision"), "source lock pack revision")
    _git_id(pack.get("tree"), "source lock pack tree")

    sources = lock.get("sources")
    if not isinstance(sources, list) or any(not isinstance(row, dict) for row in sources):
        raise SourceLockError("source lock sources must be a list of objects")
    source_ids = [row.get("source_id") for row in sources]
    if tuple(source_ids) != SOURCE_IDS:
        raise SourceLockError(
            "source lock must contain exactly the six current sources sorted by source_id"
        )
    revisions: set[str] = set()
    trees: set[str] = set()
    repositories: set[str] = set()
    for index, row in enumerate(sources):
        _exact_fields(row, SOURCE_FIELDS, f"source lock sources[{index}]")
        source_id = row["source_id"]
        if SOURCE_ID_RE.fullmatch(source_id) is None:
            raise SourceLockError(f"source lock sources[{index}].source_id is invalid")
        repository = _repository(
            row.get("repository"), f"source lock source {source_id} repository"
        )
        revision = _git_id(
            row.get("revision"), f"source lock source {source_id} revision"
        )
        tree = _git_id(row.get("tree"), f"source lock source {source_id} tree")
        _text(row.get("license"), f"source lock source {source_id} license")
        _text(row.get("scope"), f"source lock source {source_id} scope")
        if repository in repositories:
            raise SourceLockError("source lock repositories must be unique")
        if revision in revisions or tree in trees:
            raise SourceLockError("source lock Git identities must be unique")
        repositories.add(repository)
        revisions.add(revision)
        trees.add(tree)
    return lock


def load_source_lock(
    path: Path | None = None, *, pack_profile: str | None = None
) -> dict[str, Any]:
    if path is not None and pack_profile is not None:
        raise SourceLockError("select either a source-lock path or a pack profile")
    if pack_profile is not None:
        path = profile_source_lock_path(pack_profile)
    elif path is None:
        # Retained V1 callers still reopen the original canonical lock.
        path = SOURCE_LOCK_PATH
    try:
        payload = path.read_bytes()
        document = _strict_json_loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SourceLockError(f"cannot read source lock {path}: {exc}") from exc
    if payload != canonical_json(document):
        raise SourceLockError("source lock must use canonical JSON encoding")
    return validate_source_lock(document)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    selected = result.add_mutually_exclusive_group()
    selected.add_argument("--source-lock", type=Path)
    selected.add_argument("--pack-profile")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        source_lock = load_source_lock(args.source_lock, pack_profile=args.pack_profile)
    except SourceLockError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "Source lock check passed: "
        f"{source_lock['lock_id']} with {len(source_lock['sources'])} sources."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
