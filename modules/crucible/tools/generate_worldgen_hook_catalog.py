#!/usr/bin/env python3

"""Compile the exact Cleanroom 0.6.8-alpha world-generation hook catalog."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_ROOT = (
    ROOT / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
)
DEFAULT_BINDING = CANDIDATE_ROOT / "candidate-lock-v1.json"
DEFAULT_SPEC = CANDIDATE_ROOT / "worldgen-hook-spec-v1.json"
DEFAULT_OUTPUT = CANDIDATE_ROOT / "worldgen-hook-catalog-v1.json"
DEFAULT_OPTIONAL_SOURCE_ROOT = ROOT / ".workbench/cache/cleanroom-src"
COMPATIBILITY_DELIVERY_VALUES = frozenset(
    {
        "default-implementation-only",
        "framework-automatic",
        "framework-callback-generator-owned",
        "generator-opt-in",
        "independent-registration",
    }
)


class CatalogError(RuntimeError):
    """Raised when the binding, specification, or source checkout is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"JSON root must be an object: {path}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def render_catalog(catalog: dict[str, Any]) -> bytes:
    """Return the sole checked-in representation of a compiled catalog."""

    return (
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _require_text(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{context}.{key} must be a non-empty string")
    return value


def _validate_binding(binding: dict[str, Any]) -> None:
    if binding.get("format") != "workbench-cleanroom-candidate-lock-v1":
        raise CatalogError("candidate lock has the wrong format")
    if binding.get("schema_version") != 1:
        raise CatalogError("candidate lock has the wrong schema version")
    expected = {
        "minecraft.version": "1.12.2",
        "cleanroom.version": "0.6.8-alpha",
        "cleanroom.source_revision": (
            "9946eb1f17a66a72d518e5e4a92d45c62c6d33fc"
        ),
        "forge.version": "14.23.5.2864",
        "mappings.mcp_version": "9.42",
        "mappings.coordinate": "stable_39",
    }
    for dotted, wanted in expected.items():
        value: Any = binding
        for part in dotted.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value != wanted:
            raise CatalogError(
                f"candidate lock {dotted} is {value!r}, expected {wanted!r}"
            )

    release = binding.get("cleanroom", {}).get("release", {})
    if release != {
        "sha256": (
            "64e4d8af4f117224f7b69efb5e270f75b05feef7fd034fcce185ae2e5b5ec9eb"
        ),
        "size": 58129,
        "url": (
            "https://github.com/CleanroomMC/Cleanroom/releases/download/"
            "0.6.8-alpha/cleanroom-0.6.8-alpha.zip"
        ),
    }:
        raise CatalogError("candidate lock release artifact is not exact")


def _validate_sources(sources: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(sources, dict) or not sources:
        raise CatalogError("spec.sources must be a non-empty object")
    normalized: dict[str, dict[str, Any]] = {}
    for reference, raw in sorted(sources.items()):
        context = f"spec.sources.{reference}"
        if not isinstance(reference, str) or not reference:
            raise CatalogError("source reference must be a non-empty string")
        if not isinstance(raw, dict):
            raise CatalogError(f"{context} must be an object")
        record = deepcopy(raw)
        path = _require_text(record, "path", context)
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise CatalogError(f"{context}.path must be source-root relative")
        status = record.get("status")
        digest = record.get("sha256")
        if status == "bound":
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise CatalogError(f"{context}.sha256 must be a lowercase SHA-256")
            if "reason" in record:
                raise CatalogError(f"{context} bound source cannot have a reason")
        elif status == "unresolved":
            if digest is not None:
                raise CatalogError(f"{context} unresolved source sha256 must be null")
            _require_text(record, "reason", context)
        else:
            raise CatalogError(
                f"{context}.status must be 'bound' or 'unresolved'"
            )
        normalized[reference] = record
    return normalized


def _compile_rows(
    raw_rows: Any,
    *,
    row_group: str,
    sources: dict[str, dict[str, Any]],
    required_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list) or not raw_rows:
        raise CatalogError(f"spec.{row_group} must be a non-empty array")
    compiled: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        context = f"spec.{row_group}[{index}]"
        if not isinstance(raw, dict):
            raise CatalogError(f"{context} must be an object")
        record = deepcopy(raw)
        identity = _require_text(record, "id", context)
        if identity in seen:
            raise CatalogError(f"duplicate {row_group} id: {identity}")
        seen.add(identity)
        for field in required_fields:
            _require_text(record, field, context)

        references = record.pop("source_refs", None)
        if not isinstance(references, list) or not references:
            raise CatalogError(f"{context}.source_refs must be a non-empty array")
        if len(references) != len(set(references)):
            raise CatalogError(f"{context}.source_refs contains a duplicate")
        evidence: list[dict[str, Any]] = []
        for reference in sorted(references):
            if reference not in sources:
                raise CatalogError(
                    f"{context}.source_refs names unknown source {reference!r}"
                )
            evidence.append({"ref": reference, **deepcopy(sources[reference])})
        record["sources"] = evidence
        compiled.append(record)
    return sorted(compiled, key=lambda row: row["id"])


def _validate_compatibility_delivery(
    rows: list[dict[str, Any]], *, row_group: str
) -> None:
    for row in rows:
        if "compatibility_delivery" not in row:
            continue
        value = row["compatibility_delivery"]
        if value not in COMPATIBILITY_DELIVERY_VALUES:
            raise CatalogError(
                f"spec.{row_group} {row['id']} has invalid "
                f"compatibility_delivery {value!r}"
            )


def _compile_call_orders(
    raw_orders: Any, known_hook_ids: set[str]
) -> list[dict[str, Any]]:
    if not isinstance(raw_orders, list) or not raw_orders:
        raise CatalogError("spec.call_orders must be a non-empty array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_orders):
        context = f"spec.call_orders[{index}]"
        if not isinstance(raw, dict):
            raise CatalogError(f"{context} must be an object")
        order = deepcopy(raw)
        identity = _require_text(order, "id", context)
        _require_text(order, "applicability", context)
        if identity in seen:
            raise CatalogError(f"duplicate call order id: {identity}")
        seen.add(identity)
        steps = order.get("steps")
        if not isinstance(steps, list) or not steps:
            raise CatalogError(f"{context}.steps must be a non-empty array")
        ordinals: list[int] = []
        for step_index, step in enumerate(steps):
            step_context = f"{context}.steps[{step_index}]"
            if not isinstance(step, dict):
                raise CatalogError(f"{step_context} must be an object")
            ordinal = step.get("ordinal")
            if not isinstance(ordinal, int) or isinstance(ordinal, bool):
                raise CatalogError(f"{step_context}.ordinal must be an integer")
            ordinals.append(ordinal)
            _require_text(step, "operation", step_context)
            related = _require_text(step, "related_hook", step_context)
            if related not in known_hook_ids:
                raise CatalogError(
                    f"{step_context}.related_hook names unknown hook {related!r}"
                )
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise CatalogError(
                f"{context}.steps ordinals must be unique and increasing"
            )
        result.append(order)
    return sorted(result, key=lambda row: row["id"])


def build_catalog(
    binding_path: Path = DEFAULT_BINDING,
    spec_path: Path = DEFAULT_SPEC,
) -> dict[str, Any]:
    """Compile a deterministic catalog without consulting ignored state."""

    binding_bytes = binding_path.read_bytes()
    spec_bytes = spec_path.read_bytes()
    binding = _read_json(binding_path)
    spec = _read_json(spec_path)
    _validate_binding(binding)
    if spec.get("format") != "workbench-worldgen-hook-spec-v1":
        raise CatalogError("worldgen hook spec has the wrong format")
    if spec.get("schema_version") != 1:
        raise CatalogError("worldgen hook spec has the wrong schema version")
    binding_digest = _sha256_bytes(binding_bytes)
    if spec.get("candidate_lock_sha256") != binding_digest:
        raise CatalogError(
            "worldgen hook spec is not bound to the candidate lock bytes"
        )

    sources = _validate_sources(spec.get("sources"))
    buses = _compile_rows(
        spec.get("buses"),
        row_group="buses",
        sources=sources,
        required_fields=(
            "applicability",
            "callsite",
            "field",
            "probe_target",
            "semantics",
        ),
    )
    hooks = _compile_rows(
        spec.get("hooks"),
        row_group="hooks",
        sources=sources,
        required_fields=(
            "applicability",
            "callsite",
            "probe_target",
            "semantics",
            "stability",
        ),
    )
    terrain_events = _compile_rows(
        spec.get("terraingen_events"),
        row_group="terraingen_events",
        sources=sources,
        required_fields=(
            "applicability",
            "bus",
            "callsite",
            "probe_target",
            "semantics",
        ),
    )
    _validate_compatibility_delivery(hooks, row_group="hooks")
    _validate_compatibility_delivery(
        terrain_events,
        row_group="terraingen_events",
    )

    bus_ids = {row["id"] for row in buses}
    for event in terrain_events:
        if event["bus"] not in bus_ids:
            raise CatalogError(
                f"terraingen event {event['id']} names unknown bus {event['bus']}"
            )
    hook_ids = {row["id"] for row in hooks}
    call_orders = _compile_call_orders(spec.get("call_orders"), hook_ids)

    catalog: dict[str, Any] = {
        "binding": binding,
        "buses": buses,
        "call_orders": call_orders,
        "candidate_lock_sha256": binding_digest,
        "format": "workbench-worldgen-hook-catalog-v1",
        "hooks": hooks,
        "schema_version": 1,
        "source_inventory": {
            key: deepcopy(sources[key]) for key in sorted(sources)
        },
        "source_spec_sha256": _sha256_bytes(spec_bytes),
        "terraingen_events": terrain_events,
    }
    catalog["catalog_id"] = (
        "worldgen-hook-catalog:sha256:" + _sha256_bytes(_canonical_bytes(catalog))
    )
    return catalog


def verify_source_root(
    source_root: Path,
    *,
    binding_path: Path = DEFAULT_BINDING,
    spec_path: Path = DEFAULT_SPEC,
) -> list[str]:
    """Fail closed if a supplied exact Cleanroom checkout differs from the spec."""

    root = source_root.resolve()
    if not root.is_dir():
        raise CatalogError(f"Cleanroom source root is not a directory: {source_root}")
    binding = _read_json(binding_path)
    _validate_binding(binding)
    spec = _read_json(spec_path)
    sources = _validate_sources(spec.get("sources"))

    git_dir = root / ".git"
    if git_dir.exists():
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise CatalogError(
                f"cannot resolve Cleanroom source revision: {completed.stderr.strip()}"
            )
        actual_revision = completed.stdout.strip()
        expected_revision = binding["cleanroom"]["source_revision"]
        if actual_revision != expected_revision:
            raise CatalogError(
                "Cleanroom source revision mismatch: "
                f"{actual_revision}, expected {expected_revision}"
            )

    verified: list[str] = []
    for reference, source in sources.items():
        if source["status"] == "unresolved":
            continue
        path = (root / source["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CatalogError(
                f"source path escapes source root: {source['path']}"
            ) from exc
        if not path.is_file():
            raise CatalogError(
                f"bound source is absent for {reference}: {source['path']}"
            )
        actual = _sha256_bytes(path.read_bytes())
        if actual != source["sha256"]:
            raise CatalogError(
                f"bound source digest mismatch for {reference}: {actual}, "
                f"expected {source['sha256']}"
            )
        verified.append(reference)
    return sorted(verified)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, default=DEFAULT_BINDING)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source-root",
        type=Path,
        help=(
            "optional exact Cleanroom source checkout to verify; ignored source "
            "state never changes catalog content"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless the checked-in output is byte-identical",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.source_root is not None:
            verified = verify_source_root(
                args.source_root,
                binding_path=args.binding,
                spec_path=args.spec,
            )
            print(
                f"verified {len(verified)} bound Cleanroom source files",
                file=sys.stderr,
            )
        rendered = render_catalog(build_catalog(args.binding, args.spec))
        if args.check:
            try:
                existing = args.output.read_bytes()
            except OSError as exc:
                raise CatalogError(
                    f"cannot read checked-in catalog {args.output}: {exc}"
                ) from exc
            if existing != rendered:
                raise CatalogError(
                    f"checked-in catalog is stale: {args.output}"
                )
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(rendered)
    except (CatalogError, OSError) as exc:
        print(f"worldgen hook catalog: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
