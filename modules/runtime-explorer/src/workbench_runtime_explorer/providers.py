"""Authority-preserving input providers for the Exact Runtime Explorer."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from .model import (
    ExplorerError,
    ExplorerRecord,
    ExplorerSource,
    canonical_bytes,
    content_id,
    unresolved_owner,
    unique_dicts,
)


MAX_EVIDENCE_BYTES = 128 * 1024 * 1024
MAX_EVIDENCE_RECORDS = 100_000
MAX_ARTIFACT_INPUTS = 32
MAX_ARTIFACT_SET_BYTES = 512 * 1024 * 1024
MAX_MANUAL_FILES = 2_000
MAX_MANUAL_BYTES = 16 * 1024 * 1024
MAX_RELATIONSHIPS_PER_RUNTIME_NODE = 256
MAX_RUNTIME_IDENTITIES = 256

_MINECRAFT_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([a-z0-9_.-]+:[a-z0-9_./-]+)(?![A-Za-z0-9_./-])"
)
_STACK_FRAME_RE = re.compile(
    r"(?:^|\s)(?:at\s+)?"
    r"(?P<class>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)"
    r"\.(?P<method>[A-Za-z_$][A-Za-z0-9_$<>]*)"
    r"\((?P<file>[^():\r\n]+)(?::(?P<line>[1-9][0-9]*))?\)"
)
_COORDINATE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:dim(?:ension)?\s*[=:]\s*(?P<dimension>-?[0-9]+)\s+)?"
    r"(?:x\s*[=:]\s*)?(?P<x>-?[0-9]+)\s*[, ]\s*"
    r"(?:y\s*[=:]\s*)?(?P<y>-?[0-9]+)\s*[, ]\s*"
    r"(?:z\s*[=:]\s*)?(?P<z>-?[0-9]+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?:password|passwd|secret|access[_-]?token|refresh[_-]?token|api[_-]?key|credential)",
    re.IGNORECASE,
)
_GENERIC_IDENTITY_FIELDS: dict[str, str] = {
    "modid": "mod-id",
    "mod_id": "mod-id",
    "owner": "owner-id",
    "owner_id": "owner-id",
    "registry": "registry-name",
    "registry_name": "registry-name",
    "registryname": "registry-name",
    "class": "class-name",
    "class_name": "class-name",
    "target_class": "target-class",
    "targetclass": "target-class",
    "method": "method-name",
    "method_name": "method-name",
    "field": "field-name",
    "field_name": "field-name",
    "resource": "resource-location",
    "resource_location": "resource-location",
    "recipe": "recipe-id",
    "recipe_id": "recipe-id",
    "block": "block-id",
    "block_id": "block-id",
    "block_state": "blockstate-id",
    "blockstate": "blockstate-id",
    "blockstate_id": "blockstate-id",
    "item": "item-id",
    "item_id": "item-id",
    "item_stack": "item-stack",
    "stack": "item-stack",
    "metadata": "metadata-value",
    "metadata_value": "metadata-value",
    "loot_table": "loot-table-id",
    "loot_table_id": "loot-table-id",
    "biome": "biome-id",
    "biome_id": "biome-id",
    "structure": "structure-id",
    "structure_id": "structure-id",
    "generator": "generator-id",
    "generator_id": "generator-id",
    "machine": "machine-id",
    "machine_id": "machine-id",
    "config": "config-key",
    "config_key": "config-key",
    "groovy_key": "groovy-key",
    "fluid": "fluid-id",
    "fluid_id": "fluid-id",
    "material": "material-id",
    "material_id": "material-id",
    "ore_prefix": "ore-prefix",
    "dimension": "dimension-id",
    "dimension_id": "dimension-id",
    "world_type": "world-type-id",
    "world_type_id": "world-type-id",
    "mixin": "mixin-class",
    "mixin_class": "mixin-class",
    "transformer": "transformer-class",
    "event": "event-name",
    "event_name": "event-name",
    "event_id": "event-id",
    "capability": "capability-name",
    "capability_name": "capability-name",
    "profiler": "profiler-event",
    "profiler_event": "profiler-event",
    "coordinate": "coordinate",
    "source_path": "file-path",
}


@dataclass(frozen=True, slots=True)
class ProviderResult:
    source: ExplorerSource
    records: tuple[ExplorerRecord, ...]


def _source_facet_id(source_id: str, upstream_id: object) -> str:
    return content_id(
        "workbench-runtime-explorer-facet:sha256:",
        {"source_id": source_id, "upstream_id": str(upstream_id)},
    )


class _DuplicateJsonKey(ValueError):
    pass


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise ExplorerError(f"{label} is malformed JSON: {exc}") from exc


def _safe_bytes(path: Path, *, maximum: int = MAX_EVIDENCE_BYTES) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ExplorerError(f"cannot inspect evidence file {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ExplorerError(f"evidence path is not a regular non-symlink file: {path}")
    if before.st_size > maximum:
        raise ExplorerError(f"evidence file exceeds {maximum} bytes: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise ExplorerError(f"evidence file changed while opening: {path}")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise ExplorerError(f"evidence file ended while reading: {path}")
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        visible = path.lstat()
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or visible.st_dev != opened.st_dev
            or visible.st_ino != opened.st_ino
        ):
            raise ExplorerError(f"evidence file changed while reading: {path}")
        return b"".join(chunks), opened
    except OSError as exc:
        raise ExplorerError(f"cannot read evidence file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _authority_for_format(format_name: str) -> str:
    folded = format_name.casefold()
    if "atlas" in folded or "runtime-graph" in folded:
        return "Atlas"
    if "blueprint" in folded:
        return "Blueprints"
    if "manual" in folded:
        return "Manuals"
    if "crucible" in folded or any(
        token in folded
        for token in ("worldgen-observatory", "managed-runtime", "world-snapshot", "strata")
    ):
        return "Crucible"
    if "project-intelligence" in folded or "doctor" in folded or "mixin" in folded:
        return "Project Intelligence"
    if "console" in folded or "workbench-shell" in folded:
        return "Workbench Shell"
    if "groovy-pack-program" in folded or "groovy-language-service" in folded:
        return "Workbench Pack Program Studio"
    return "declared receipt owner"


def _owner_from_candidates(candidates: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], str | None]:
    rows = [dict(row) for row in candidates]
    rows = list(unique_dicts(rows))
    if not rows:
        return unresolved_owner(), None
    versions = {
        str(row["version"])
        for row in rows
        if isinstance(row.get("version"), str) and row.get("version")
    }
    state = "declared" if len(rows) == 1 else "ambiguous"
    return (
        {
            "state": state,
            "actors": [
                {
                    "kind": "mod",
                    "id": row.get("mod_id"),
                    "version": row.get("version"),
                }
                for row in rows
            ],
            "basis": sorted(
                {
                    str(row.get("basis"))
                    for row in rows
                    if row.get("basis") is not None
                }
            ),
        },
        next(iter(versions)) if len(versions) == 1 else None,
    )


def workspace_provider(workspace: Path) -> ProviderResult:
    from workbench_project_intelligence.runtime_surface import (
        RuntimeSurfaceError,
        scan_runtime_surface,
        validate_runtime_surface,
    )

    try:
        surface = scan_runtime_surface(workspace)
        validate_runtime_surface(surface)
    except RuntimeSurfaceError as exc:
        raise ExplorerError(f"Project Intelligence surface failed: {exc}") from exc
    source_id = str(surface["surface_id"])
    workspace_root = Path(surface["workspace"]["root"])
    source = ExplorerSource(
        source_id=source_id,
        source_kind="project-intelligence-runtime-surface",
        authority="Project Intelligence",
        state="complete" if surface["coverage"]["complete"] else "partial",
        identity={
            "format": surface["format"],
            "surface_id": surface["surface_id"],
        },
        scope={
            "workspace": str(workspace_root),
            "repository": surface["workspace"]["repository"],
            "platform": surface["workspace"]["platform"],
        },
        coverage=surface["coverage"],
        limitations=tuple(surface["limitations"]),
    )
    records: list[ExplorerRecord] = []
    for row in surface["declarations"]:
        owner, version = _owner_from_candidates(row["owner_candidates"])
        declaration = dict(row["declaration"])
        absolute_path = workspace_root / str(declaration["path"])
        navigation = (
            {
                "kind": "source",
                "path": str(absolute_path),
                "line": declaration["line"],
                "column": None,
                "label": f"{row['kind']} declaration",
                "resolution": "exact-workspace-file",
            },
        )
        search_terms: list[str] = [str(declaration["path"])]
        for value in row.get("attributes", {}).values():
            if isinstance(value, (str, int)) and str(value):
                search_terms.append(str(value).replace("\r", " ").replace("\n", " ")[:8192])
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(
                    source_id, row["declaration_id"]
                ),
                source_id=source_id,
                authority="Project Intelligence",
                state=str(row["state"]),
                kind=str(row["kind"]),
                name=str(row["name"]),
                identities=tuple(dict(value) for value in row["identities"]),
                owner=owner,
                version=version,
                scope={
                    "workspace": str(workspace_root),
                    "profile": None,
                    "physical_side": None,
                },
                declaration=declaration,
                relationships=tuple(dict(value) for value in row["relationships"]),
                evidence=(
                    {
                        "source_id": source_id,
                        "path": declaration["path"],
                        "sha256": declaration["file_sha256"],
                        "claim": "static-declaration",
                    },
                ),
                navigation=navigation,
                limitations=(
                    "Static project evidence does not establish assembled-game presence or runtime behavior.",
                ),
                search_terms=tuple(dict.fromkeys(search_terms)),
            )
        )
    return ProviderResult(source, tuple(records))


def _runtime_surface_receipt_records(
    surface: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    workspace = surface.get("workspace")
    recorded_root = (
        workspace.get("root") if isinstance(workspace, Mapping) else None
    )
    records: list[ExplorerRecord] = []
    declarations = surface.get("declarations", [])
    for index, row in enumerate(declarations):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            return records, True
        if not isinstance(row, Mapping):
            continue
        owner, version = _owner_from_candidates(row.get("owner_candidates", ()))
        declaration = row.get("declaration")
        if not isinstance(declaration, Mapping):
            continue
        attributes = row.get("attributes")
        search_terms: list[str] = [str(declaration.get("path", ""))]
        if isinstance(attributes, Mapping):
            search_terms.extend(
                str(value).replace("\r", " ").replace("\n", " ")[:8192]
                for value in attributes.values()
                if isinstance(value, (str, int))
                and not isinstance(value, bool)
                and str(value)
            )
        pointer = f"/declarations/{index}"
        records.append(
            ExplorerRecord(
                record_id=str(row["declaration_id"]),
                source_id=source_id,
                authority="Project Intelligence",
                state=str(row["state"]),
                kind=str(row["kind"]),
                name=str(row["name"]),
                identities=tuple(
                    [
                        {
                            "kind": "declaration-id",
                            "value": str(row["declaration_id"]),
                            "basis": "validated runtime-surface declaration",
                        },
                        *(
                            dict(identity)
                            for identity in row.get("identities", ())
                        ),
                    ]
                ),
                owner=owner,
                version=version,
                scope={
                    "recorded_workspace": recorded_root,
                    "profile": None,
                    "physical_side": None,
                },
                declaration=dict(declaration),
                runtime_form={
                    "runtime_surface_id": surface.get("surface_id"),
                    "recorded_declaration": dict(row),
                },
                relationships=tuple(
                    dict(value) for value in row.get("relationships", ())
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "json_pointer": pointer,
                        "workspace_file_sha256": declaration.get("file_sha256"),
                        "claim": "validated-serialized-static-declaration",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path,
                    pointer,
                    f"Recorded {row['kind']} declaration",
                ),
                limitations=(
                    "The source path is recorded evidence and was not revalidated against the current filesystem; navigation opens the receipt declaration.",
                ),
                search_terms=tuple(dict.fromkeys(search_terms)),
            )
        )
    return records, False


def manuals_provider(root: Path) -> ProviderResult:
    manuals_root = root / "modules/manuals/guides"
    if not manuals_root.is_dir() or manuals_root.is_symlink():
        source = ExplorerSource(
            source_id="workbench-runtime-explorer-source:manuals-unavailable",
            source_kind="manuals",
            authority="Manuals",
            state="unavailable",
            identity={"path": str(manuals_root)},
            limitations=("No checked-in Manuals guide root is available.",),
        )
        return ProviderResult(source, ())
    paths = sorted(
        path
        for path in manuals_root.rglob("*.md")
        if path.is_file() and not path.is_symlink()
    )
    truncated = len(paths) > MAX_MANUAL_FILES
    paths = paths[:MAX_MANUAL_FILES]
    inventory: list[dict[str, Any]] = []
    payloads: list[tuple[Path, str, str]] = []
    total = 0
    for path in paths:
        raw, _ = _safe_bytes(path, maximum=1024 * 1024)
        if total + len(raw) > MAX_MANUAL_BYTES:
            truncated = True
            break
        total += len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExplorerError(f"Manual is not UTF-8: {path}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        relative = path.relative_to(root).as_posix()
        inventory.append({"path": relative, "bytes": len(raw), "sha256": digest})
        payloads.append((path, text, digest))
    source_id = content_id("workbench-runtime-explorer-manuals:sha256:", inventory)
    source = ExplorerSource(
        source_id=source_id,
        source_kind="manuals",
        authority="Manuals",
        state="partial" if truncated else "complete",
        identity={"inventory_sha256": source_id.rsplit(":", 1)[-1]},
        coverage={"files": len(payloads), "bytes": total, "complete": not truncated},
        limitations=(
            "Manuals teach applicable workflows; they do not authorize code or establish runtime facts.",
        ),
    )
    records: list[ExplorerRecord] = []
    for path, text, digest in payloads:
        relative = path.relative_to(root).as_posix()
        lines = text.splitlines()
        title = next(
            (line.lstrip("#").strip() for line in lines if line.startswith("#") and line.lstrip("#").strip()),
            path.stem,
        )
        headings = [
            line.lstrip("#").strip()[:8192]
            for line in lines
            if line.startswith("#") and line.lstrip("#").strip()
        ][:128]
        context_lines = [
            line.strip()[:8192]
            for line in lines
            if line.strip() and "\x00" not in line
        ][:512]
        record_id = content_id(
            "workbench-runtime-manual:sha256:",
            {"path": relative, "sha256": digest},
        )
        records.append(
            ExplorerRecord(
                record_id=record_id,
                source_id=source_id,
                authority="Manuals",
                state="teaching",
                kind="manual",
                name=title,
                identities=(
                    {"kind": "manual-id", "value": record_id, "basis": "content-addressed guide"},
                    {"kind": "file-path", "value": relative, "basis": "checked-in Manual"},
                ),
                owner={"state": "authority", "actors": [{"kind": "module", "id": "Manuals"}], "basis": "guide location"},
                declaration={"path": relative, "line": 1, "file_sha256": digest, "language": "markdown"},
                evidence=({"path": relative, "sha256": digest, "claim": "teaching"},),
                navigation=({"kind": "manual", "path": str(path), "line": 1, "column": None, "label": title, "resolution": "exact-workspace-file"},),
                limitations=("This result is teaching context, not runtime or construction authority.",),
                search_terms=tuple(dict.fromkeys([*headings, *context_lines])),
            )
        )
    return ProviderResult(source, tuple(records))


def _attribute_identities(attributes: Mapping[str, Any]) -> list[dict[str, str]]:
    identities: list[dict[str, str]] = []
    for key, value in attributes.items():
        normalized = str(key).casefold().replace("-", "_")
        kind = _GENERIC_IDENTITY_FIELDS.get(normalized)
        if kind is None or not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        rendered = str(value)
        if not rendered or len(rendered) > 8192 or any(char in rendered for char in "\r\n\x00"):
            continue
        identities.append({"kind": kind, "value": rendered, "basis": f"Atlas node attribute {key}"})
    return identities


def _runtime_domain_identities(
    node_kind: object,
    identities: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    normalized_kind = str(node_kind).casefold().replace("_", "-")
    identity_kind = {
        "biome": "biome-id",
        "block": "block-id",
        "block-state": "blockstate-id",
        "capability": "capability-name",
        "event": "event-name",
        "fluid": "fluid-id",
        "generator": "generator-id",
        "item": "item-id",
        "loot-table": "loot-table-id",
        "machine": "machine-id",
        "material": "material-id",
        "recipe": "recipe-id",
        "structure": "structure-id",
        "world-type": "world-type-id",
    }.get(normalized_kind)
    if identity_kind is None:
        return []
    rows = list(identities)
    if any(row.get("kind") == identity_kind for row in rows):
        return []
    source = next(
        (
            row
            for row in rows
            if row.get("kind") in {"registry-name", "resource-location"}
            and isinstance(row.get("value"), str)
            and row.get("value")
        ),
        None,
    )
    if source is None:
        return []
    return [
        {
            "kind": identity_kind,
            "value": str(source["value"]),
            "basis": (
                f"Atlas {normalized_kind} node kind plus "
                f"{source['kind']} identity"
            ),
        }
    ]


def _runtime_name(node: Mapping[str, Any], identities: Iterable[Mapping[str, Any]]) -> str:
    attributes = node.get("attributes")
    if isinstance(attributes, Mapping):
        for key in (
            "display_name",
            "registry_name",
            "recipe_id",
            "block_id",
            "blockstate_id",
            "item_id",
            "loot_table_id",
            "biome_id",
            "structure_id",
            "generator_id",
            "machine_id",
            "name",
            "mod_id",
            "key",
            "identifier",
        ):
            value = attributes.get(key)
            if isinstance(value, str) and value and "\n" not in value and "\r" not in value:
                return value[:8192]
    preferred = (
        "registry-name",
        "mod-id",
        "resource-location",
        "recipe-id",
        "block-id",
        "blockstate-id",
        "item-id",
        "item-stack",
        "loot-table-id",
        "biome-id",
        "structure-id",
        "generator-id",
        "machine-id",
        "fluid-id",
        "material-id",
        "display-name",
    )
    rows = list(identities)
    for kind in preferred:
        for identity in rows:
            if identity.get("kind") == kind:
                return str(identity["value"])
    return str(node["id"])


def atlas_runtime_provider(
    database: Path,
    query_text: str,
    *,
    kinds: tuple[str, ...] = (),
    profile: str | None = None,
    physical_side: str | None = None,
    limit: int = 100,
) -> ProviderResult:
    from workbench_atlas.runtime_graph_query import (
        PageRequest,
        RuntimeGraphQueryError,
        RuntimeGraphReader,
    )

    try:
        with RuntimeGraphReader(database) as reader:
            projection = reader.projection_identity()
            page = reader.search_nodes(
                query_text,
                kinds=kinds,
                profile=profile,
                physical_side=physical_side,
                page=PageRequest(limit=limit),
            )
            hydrated: list[
                tuple[dict[str, Any], Any, Any, Any]
            ] = []
            for match in page.items:
                node = match["node"]
                identity_page = reader.node_identity_keys(
                    str(node["id"]), page=PageRequest(limit=MAX_RUNTIME_IDENTITIES)
                )
                relation_page = reader.related_nodes(
                    str(node["id"]),
                    page=PageRequest(limit=MAX_RELATIONSHIPS_PER_RUNTIME_NODE),
                )
                binding_page = reader.related_nodes(
                    str(node["id"]),
                    predicates=("belongs_to_mod", "configured_by", "defined_by_resource"),
                    page=PageRequest(limit=MAX_RELATIONSHIPS_PER_RUNTIME_NODE),
                )
                hydrated.append(
                    (match, identity_page, relation_page, binding_page)
                )
    except RuntimeGraphQueryError as exc:
        raise ExplorerError(f"Atlas runtime graph query failed: {exc}") from exc
    source_id = content_id("workbench-runtime-explorer-atlas-source:sha256:", projection)
    identity_truncated = sum(
        1 for _, identity_page, _, _ in hydrated if identity_page.truncated
    )
    relationship_truncated = sum(
        1
        for _, _, relation_page, binding_page in hydrated
        if relation_page.truncated or binding_page.truncated
    )
    source = ExplorerSource(
        source_id=source_id,
        source_kind="atlas-runtime-graph",
        authority="Atlas",
        state=(
            "partial"
            if page.truncated or identity_truncated or relationship_truncated
            else "complete"
        ),
        identity={"path": str(database.resolve()), **projection},
        scope={"profile_filter": profile, "physical_side_filter": physical_side},
        coverage={
            "matching_nodes": page.total,
            "hydrated_nodes": len(hydrated),
            "search_truncated": page.truncated,
            "identity_truncated_nodes": identity_truncated,
            "relationship_truncated_nodes": relationship_truncated,
            "truncated": bool(
                page.truncated
                or identity_truncated
                or relationship_truncated
            ),
        },
        limitations=(
            "Runtime claims apply only to the exact immutable Atlas projection, profile, and physical side supplied.",
            "Explorer ranking is presentation; Atlas node and edge records remain authoritative.",
        ),
    )
    records: list[ExplorerRecord] = []
    for match, identity_page, relation_page, binding_page in hydrated:
        node = match["node"]
        key_rows = identity_page.items
        attributes = node.get("attributes") if isinstance(node.get("attributes"), dict) else {}
        identities: list[dict[str, str]] = [
            {"kind": "runtime-node-id", "value": str(node["id"]), "basis": "Atlas canonical node ID"}
        ]
        identities.extend(
            {"kind": str(row["key_kind"]), "value": str(row["key_value"]), "basis": "Atlas typed identity key"}
            for row in key_rows
        )
        identities.extend(_attribute_identities(attributes))
        identities.extend(
            _runtime_domain_identities(node.get("kind"), identities)
        )
        identities = list(unique_dicts(identities))
        relationships: list[dict[str, Any]] = []
        owner_actors: list[dict[str, Any]] = []
        navigation: list[dict[str, Any]] = []
        related_rows = {
            (
                str(related["direction"]),
                str(related["relationship"]["id"]),
            ): related
            for related in (*relation_page.items, *binding_page.items)
        }
        for related in (
            related_rows[key] for key in sorted(related_rows)
        ):
            edge = related["relationship"]
            other = related["node"]
            relationships.append(
                {
                    "direction": related["direction"],
                    "predicate": edge["predicate"],
                    "relationship_id": edge["id"],
                    "node": other,
                }
            )
            if (
                edge.get("predicate") == "belongs_to_mod"
                and related["direction"] == "outgoing"
                and other.get("kind") == "mod"
            ):
                other_attributes = other.get("attributes") if isinstance(other.get("attributes"), dict) else {}
                actor_id = next(
                    (
                        str(other_attributes[key])
                        for key in ("mod_id", "name", "registry_name")
                        if isinstance(other_attributes.get(key), str) and other_attributes.get(key)
                    ),
                    str(other["id"]),
                )
                version = next(
                    (
                        str(other_attributes[key])
                        for key in ("version", "display_version")
                        if isinstance(other_attributes.get(key), str) and other_attributes.get(key)
                    ),
                    None,
                )
                owner_actors.append({"kind": "mod", "id": actor_id, "version": version, "runtime_node_id": other["id"]})
            if edge.get("predicate") == "defined_by_resource":
                other_attributes = other.get("attributes") if isinstance(other.get("attributes"), dict) else {}
                resource_path = other_attributes.get("path")
                if isinstance(resource_path, str) and resource_path and "\x00" not in resource_path:
                    navigation.append(
                        {"kind": "resource", "path": resource_path, "line": 1, "column": None, "label": "Atlas defined resource", "resolution": "runtime-recorded-locator"}
                    )
        if node.get("kind") == "mod":
            actor_id = next(
                (
                    str(attributes[key])
                    for key in ("mod_id", "name")
                    if isinstance(attributes.get(key), str) and attributes.get(key)
                ),
                str(node["id"]),
            )
            actor_version = next(
                (
                    str(attributes[key])
                    for key in ("version", "display_version")
                    if isinstance(attributes.get(key), str) and attributes.get(key)
                ),
                None,
            )
            owner_actors.append({"kind": "mod", "id": actor_id, "version": actor_version, "runtime_node_id": node["id"]})
        owner_actors = list(unique_dicts(owner_actors))
        owner = (
            {"state": "observed", "actors": owner_actors, "basis": "Atlas belongs_to_mod relationship"}
            if owner_actors
            else unresolved_owner("Atlas projection contains no hydrated belongs_to_mod relationship for this node")
        )
        versions = {str(row["version"]) for row in owner_actors if row.get("version")}
        records.append(
            ExplorerRecord(
                record_id=str(node["id"]),
                source_id=source_id,
                authority="Atlas",
                state="observed",
                kind=str(node["kind"]),
                name=_runtime_name(node, identities),
                identities=tuple(identities),
                owner=owner,
                version=next(iter(versions)) if len(versions) == 1 else None,
                scope=dict(node.get("scope", {})),
                runtime_form=dict(node),
                relationships=tuple(relationships),
                evidence=({"source_id": source_id, "projection": projection, "claim": "accepted-runtime-graph-node"},),
                navigation=tuple(navigation),
                limitations=(
                    tuple(
                        limitation
                        for condition, limitation in (
                            (
                                identity_page.truncated,
                                "Atlas typed identities were truncated for this node.",
                            ),
                            (
                                relation_page.truncated
                                or binding_page.truncated,
                                "Atlas relationships were truncated for this node.",
                            ),
                        )
                        if condition
                    )
                ),
                search_terms=tuple(
                    str(value).replace("\r", " ").replace("\n", " ")[:8192]
                    for value in attributes.values()
                    if isinstance(value, (str, int)) and str(value)
                ),
                rank_hint=max(0, 40 - int(match.get("rank", 8)) * 4),
            )
        )
    return ProviderResult(source, tuple(records))


def _topology_records(receipt: Mapping[str, Any], source_id: str) -> list[ExplorerRecord]:
    artifacts = {
        str(row["artifact_id"]): row
        for row in receipt.get("artifacts", [])
        if isinstance(row, dict) and isinstance(row.get("artifact_id"), str)
    }
    components = {
        str(row["component_id"]): row
        for row in receipt.get("components", [])
        if isinstance(row, dict) and isinstance(row.get("component_id"), str)
    }
    registrations = [
        row for row in receipt.get("registrations", []) if isinstance(row, dict)
    ]
    relationships_by_endpoint: dict[str, list[dict[str, Any]]] = {}
    for registration in registrations:
        source_endpoint = registration.get("source")
        target_endpoint = registration.get("target")
        if not isinstance(source_endpoint, dict) or not isinstance(
            target_endpoint, dict
        ):
            continue
        common = {
            "predicate": str(registration.get("registration_kind", "registration")),
            "registration_id": registration.get("registration_id"),
            "phase": registration.get("phase"),
            "runtime_state": registration.get("runtime_state"),
            "basis": "validated Project Intelligence topology registration",
        }
        source_endpoint_id = source_endpoint.get("id")
        target_endpoint_id = target_endpoint.get("id")
        if isinstance(source_endpoint_id, str):
            relationships_by_endpoint.setdefault(source_endpoint_id, []).append(
                {
                    **common,
                    "direction": "outgoing",
                    "target": dict(target_endpoint),
                }
            )
        if isinstance(target_endpoint_id, str):
            relationships_by_endpoint.setdefault(target_endpoint_id, []).append(
                {
                    **common,
                    "direction": "incoming",
                    "source": dict(source_endpoint),
                }
            )

    def artifact_scope(artifact: Mapping[str, Any] | None) -> dict[str, Any]:
        scope = dict(receipt.get("scope", {}))
        if artifact is not None:
            scope.update(
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "artifact_sha256": artifact.get("sha256"),
                    "runtime_selection": "unobserved",
                }
            )
        return scope

    def artifact_version(artifact: Mapping[str, Any] | None) -> str | None:
        if artifact is None:
            return None
        versions = {
            str(claim["value"])
            for claim in artifact.get("version_claims", [])
            if isinstance(claim, Mapping)
            and isinstance(claim.get("value"), str)
            and claim.get("value")
        }
        return next(iter(versions)) if len(versions) == 1 else None

    records: list[ExplorerRecord] = []
    for row in artifacts.values():
        identities = [
            {"kind": "artifact-id", "value": str(row["artifact_id"]), "basis": "topology receipt"},
            {"kind": "artifact-sha256", "value": str(row["sha256"]), "basis": "exact archive bytes"},
            {"kind": "file-name", "value": str(row["file_name"]), "basis": "artifact label"},
        ]
        if isinstance(row.get("coordinate"), str):
            identities.append({"kind": "dependency-coordinate", "value": row["coordinate"], "basis": row.get("coordinate_state", "receipt")})
        version = artifact_version(row)
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(source_id, row["artifact_id"]),
                source_id=source_id,
                authority="Project Intelligence",
                state="static-possible",
                kind="artifact",
                name=str(row["file_name"]),
                identities=tuple(identities),
                owner={"state": "exact-artifact", "actors": [{"kind": "artifact", "id": row["artifact_id"], "sha256": row["sha256"], "version": version}], "basis": "exact archive bytes"},
                version=version,
                scope=artifact_scope(row),
                runtime_form={"artifact": dict(row)},
                relationships=tuple(
                    relationships_by_endpoint.get(str(row["artifact_id"]), ())
                ),
                evidence=({"receipt_id": receipt.get("receipt_id"), "artifact_sha256": row["sha256"], "claim": "static-artifact"},),
                limitations=("Artifact presence in a static set does not prove runtime loading.",),
                search_terms=(() if version is None else (version,)),
            )
        )
    for row in components.values():
        artifact = artifacts.get(str(row.get("artifact_id")))
        implementation = row.get("implementation") if isinstance(row.get("implementation"), dict) else {}
        identities = [
            {"kind": "component-id", "value": str(row["component_id"]), "basis": "topology receipt"},
            {"kind": "logical-name", "value": str(row["logical_name"]), "basis": "topology receipt"},
        ]
        class_name = implementation.get("class_name")
        if isinstance(class_name, str):
            identity_kind = (
                "mixin-class"
                if row.get("component_kind") == "mixin-class"
                else "transformer-class"
                if row.get("component_kind") == "transformer"
                else "class-name"
            )
            identities.append({"kind": identity_kind, "value": class_name, "basis": "exact artifact component"})
        service_interface = implementation.get("service_interface")
        if isinstance(service_interface, str):
            identities.append(
                {
                    "kind": "capability-name",
                    "value": service_interface,
                    "basis": "declared Java service interface",
                }
            )
        identities.extend(
            {
                "kind": "capability-name",
                "value": value,
                "basis": "topology component capability",
            }
            for value in row.get("capabilities", [])
            if isinstance(value, str) and value
        )
        entry_path = implementation.get("entry_path")
        if isinstance(entry_path, str):
            identities.append({"kind": "archive-entry", "value": entry_path, "basis": "exact artifact component"})
        owner = (
            {"state": "exact-artifact", "actors": [{"kind": "artifact", "id": artifact["artifact_id"], "sha256": artifact["sha256"], "name": artifact["file_name"], "version": artifact_version(artifact)}], "basis": "topology component artifact_id"}
            if artifact
            else unresolved_owner("topology component artifact is unresolved")
        )
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(source_id, row["component_id"]),
                source_id=source_id,
                authority="Project Intelligence",
                state="static-possible",
                kind=str(row["component_kind"]),
                name=str(row["logical_name"]),
                identities=tuple(unique_dicts(identities)),
                owner=owner,
                version=artifact_version(artifact),
                scope=artifact_scope(artifact),
                declaration=(
                    {"path": entry_path, "line": 1, "file_sha256": implementation.get("entry_sha256"), "language": "jvm-bytecode"}
                    if isinstance(entry_path, str)
                    else None
                ),
                runtime_form={"component": dict(row)},
                relationships=tuple(
                    relationships_by_endpoint.get(str(row["component_id"]), ())
                ),
                evidence=({"receipt_id": receipt.get("receipt_id"), "evidence_ids": row.get("evidence_ids", []), "claim": "static-component"},),
                limitations=("Static component topology does not prove runtime selection or transformation completion.",),
                search_terms=tuple(str(value) for value in row.get("capabilities", []) if isinstance(value, str)),
            )
        )
    for row in registrations:
        registration_id = row.get("registration_id")
        source_endpoint = row.get("source")
        target_endpoint = row.get("target")
        if (
            not isinstance(registration_id, str)
            or not isinstance(source_endpoint, dict)
            or not isinstance(target_endpoint, dict)
        ):
            continue
        artifact_ids: set[str] = set()
        for endpoint in (source_endpoint, target_endpoint):
            endpoint_id = endpoint.get("id")
            if endpoint.get("kind") == "artifact" and endpoint_id in artifacts:
                artifact_ids.add(str(endpoint_id))
            elif endpoint.get("kind") == "component" and endpoint_id in components:
                component_artifact = components[str(endpoint_id)].get("artifact_id")
                if component_artifact in artifacts:
                    artifact_ids.add(str(component_artifact))
        bound_artifacts = [artifacts[key] for key in sorted(artifact_ids)]
        if len(bound_artifacts) == 1:
            artifact = bound_artifacts[0]
            owner = {
                "state": "exact-artifact",
                "actors": [
                    {
                        "kind": "artifact",
                        "id": artifact["artifact_id"],
                        "sha256": artifact["sha256"],
                        "name": artifact["file_name"],
                        "version": artifact_version(artifact),
                    }
                ],
                "basis": "registration endpoints resolve to one exact artifact",
            }
            scope = artifact_scope(artifact)
        elif bound_artifacts:
            owner = {
                "state": "ambiguous-static",
                "actors": [
                    {
                        "kind": "artifact",
                        "id": artifact["artifact_id"],
                        "sha256": artifact["sha256"],
                        "name": artifact["file_name"],
                    }
                    for artifact in bound_artifacts
                ],
                "basis": "registration endpoints span multiple exact artifacts",
            }
            scope = dict(receipt.get("scope", {}))
        else:
            owner = unresolved_owner(
                "registration endpoints do not resolve to an exact artifact"
            )
            scope = dict(receipt.get("scope", {}))
        registration_kind = str(row.get("registration_kind", "registration"))
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(source_id, registration_id),
                source_id=source_id,
                authority="Project Intelligence",
                state=(
                    "declared"
                    if row.get("declaration_state") == "declared-exact"
                    else "static-possible"
                ),
                kind="registration",
                name=(
                    f"{registration_kind}: {source_endpoint.get('id')} -> "
                    f"{target_endpoint.get('id')}"
                )[:8192],
                identities=(
                    {
                        "kind": "registration-id",
                        "value": registration_id,
                        "basis": "topology registration identity",
                    },
                    {
                        "kind": "registration-kind",
                        "value": registration_kind,
                        "basis": "topology registration kind",
                    },
                ),
                owner=owner,
                version=(
                    artifact_version(bound_artifacts[0])
                    if len(bound_artifacts) == 1
                    else None
                ),
                scope=scope,
                runtime_form={"registration": dict(row)},
                relationships=(
                    {
                        "direction": "outgoing",
                        "predicate": registration_kind,
                        "source": dict(source_endpoint),
                        "target": dict(target_endpoint),
                        "phase": row.get("phase"),
                        "runtime_state": row.get("runtime_state"),
                        "basis": "validated Project Intelligence topology registration",
                    },
                ),
                evidence=(
                    {
                        "receipt_id": receipt.get("receipt_id"),
                        "evidence_ids": row.get("evidence_ids", []),
                        "claim": "static-registration",
                    },
                ),
                limitations=(
                    "A static registration route does not prove runtime loading, ordering, or activation.",
                ),
                search_terms=tuple(
                    str(value)
                    for value in (
                        row.get("phase"),
                        row.get("runtime_state"),
                        row.get("declaration_state"),
                    )
                    if isinstance(value, str) and value
                ),
            )
        )
    return records


def _static_artifact_owner(
    scan: Mapping[str, Any], artifact: Mapping[str, Any]
) -> tuple[dict[str, Any], str | None]:
    actors = [
        {
            "kind": "mod-id-candidate",
            "id": row.get("raw_owner_id"),
            "normalized_id": row.get("normalized_owner_id"),
            "basis": row.get("source"),
            "artifact_id": artifact.get("artifact_id"),
            "artifact_sha256": artifact.get("sha256"),
        }
        for row in scan.get("owner_id_inputs", [])
        if isinstance(row, dict) and isinstance(row.get("raw_owner_id"), str)
    ]
    actors = list(unique_dicts(actors))
    versions = {
        str(claim["value"])
        for claim in artifact.get("version_claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("value"), str)
    }
    if not actors:
        owner = {
            "state": "exact-artifact",
            "actors": [
                {
                    "kind": "artifact",
                    "id": artifact.get("artifact_id"),
                    "sha256": artifact.get("sha256"),
                    "name": artifact.get("file_name"),
                }
            ],
            "basis": "exact archive bytes; mod owner unresolved",
        }
    else:
        owner = {
            "state": "inferred-static" if len(actors) == 1 else "ambiguous-static",
            "actors": actors,
            "basis": "Project Intelligence static owner-ID inputs",
        }
    return owner, next(iter(versions)) if len(versions) == 1 else None


def _jvm_surface_records(
    *,
    path: Path,
    scan: Mapping[str, Any],
    surface: Mapping[str, Any],
    artifact: Mapping[str, Any],
    source_id: str,
    maximum: int = MAX_EVIDENCE_RECORDS,
) -> tuple[list[ExplorerRecord], bool]:
    owner, version = _static_artifact_owner(scan, artifact)
    records: list[ExplorerRecord] = []
    truncated = False

    def append(record: ExplorerRecord) -> bool:
        nonlocal truncated
        if len(records) >= maximum:
            truncated = True
            return False
        records.append(record)
        return True

    artifact_evidence = {
        "artifact_id": artifact.get("artifact_id"),
        "artifact_sha256": artifact.get("sha256"),
        "path": str(path),
        "surface_id": surface.get("surface_id"),
        "claim": "static-classfile",
    }
    for class_row in surface.get("classes", []):
        if not isinstance(class_row, dict):
            continue
        entry_path = str(class_row["entry_path"])
        navigation = (
            {
                "kind": "archive-entry",
                "path": str(path.resolve()),
                "entry": entry_path,
                "line": 1,
                "column": None,
                "label": str(class_row["class_name"]),
                "resolution": "exact-archive-entry",
            },
        )
        class_terms: list[str] = []
        for value in (
            class_row.get("super_class"),
            class_row.get("source_file"),
            *class_row.get("interfaces", []),
            *class_row.get("class_references", []),
            *class_row.get("constant_strings", []),
        ):
            if isinstance(value, str) and value and "\n" not in value and "\r" not in value:
                class_terms.append(value[:8192])
        if not append(
            ExplorerRecord(
                record_id=_source_facet_id(source_id, class_row["class_id"]),
                source_id=source_id,
                authority="Project Intelligence",
                state="static-possible",
                kind="class",
                name=str(class_row["class_name"]),
                identities=(
                    {"kind": "class-id", "value": str(class_row["class_id"]), "basis": "Project Intelligence class surface"},
                    {"kind": "class-name", "value": str(class_row["class_name"]), "basis": "exact classfile this_class"},
                    {"kind": "archive-entry", "value": entry_path, "basis": "exact JAR member"},
                    {"kind": "class-sha256", "value": str(class_row["sha256"]), "basis": "exact classfile bytes"},
                ),
                owner=owner,
                version=version,
                scope={"artifact_id": artifact.get("artifact_id"), "artifact_sha256": artifact.get("sha256"), "runtime_selection": "unobserved"},
                declaration={"path": f"{path}!/{entry_path}", "line": 1, "file_sha256": class_row["sha256"], "language": "jvm-bytecode"},
                runtime_form={"classfile": dict(class_row)},
                relationships=tuple(
                    [
                        *(
                            [
                                {
                                    "predicate": "extends",
                                    "target": {"kind": "class-name", "value": class_row["super_class"]},
                                    "basis": "exact classfile header",
                                }
                            ]
                            if class_row.get("super_class")
                            else []
                        ),
                        *[
                            {
                                "predicate": "implements",
                                "target": {"kind": "class-name", "value": target},
                                "basis": "exact classfile header",
                            }
                            for target in class_row.get("interfaces", [])
                        ],
                    ]
                ),
                evidence=(artifact_evidence,),
                navigation=navigation,
                limitations=tuple(class_row.get("limitations", [])),
                search_terms=tuple(dict.fromkeys(class_terms)),
            )
        ):
            break
        for member in [*class_row.get("fields", []), *class_row.get("methods", [])]:
            if not isinstance(member, dict):
                continue
            code = member.get("code") if isinstance(member.get("code"), dict) else {}
            member_navigation = (
                {
                    "kind": "archive-member",
                    "path": str(path.resolve()),
                    "entry": entry_path,
                    "source_file": class_row.get("source_file"),
                    "line": code.get("source_line_min") or 1,
                    "column": None,
                    "label": str(member["descriptor_key"]),
                    "resolution": "exact-classfile-member",
                },
            )
            identities = [
                {"kind": "jvm-member-id", "value": str(member["member_id"]), "basis": "Project Intelligence JVM member surface"},
                {"kind": f"{member['kind']}-name", "value": str(member["name"]), "basis": "exact classfile member_info"},
                {"kind": "source-member", "value": str(member["qualified_name"]), "basis": "exact declaring class and member name"},
                {"kind": "jvm-descriptor", "value": str(member["descriptor"]), "basis": "exact classfile descriptor"},
                {"kind": "jvm-member-key", "value": str(member["descriptor_key"]), "basis": "exact classfile member identity"},
            ]
            if not append(
                ExplorerRecord(
                    record_id=_source_facet_id(source_id, member["member_id"]),
                    source_id=source_id,
                    authority="Project Intelligence",
                    state="static-possible",
                    kind=str(member["kind"]),
                    name=str(member["qualified_name"]),
                    identities=tuple(identities),
                    owner=owner,
                    version=version,
                    scope={"artifact_id": artifact.get("artifact_id"), "class_id": class_row.get("class_id"), "runtime_selection": "unobserved"},
                    declaration={"path": f"{path}!/{entry_path}", "line": code.get("source_line_min") or 1, "file_sha256": class_row["sha256"], "language": "jvm-bytecode"},
                    runtime_form={"classfile_member": dict(member), "class_name": class_row["class_name"], "classfile_version": class_row["classfile_version"]},
                    relationships=({"predicate": "member-of", "target": {"kind": "class-name", "value": class_row["class_name"]}, "basis": "exact classfile member table"},),
                    evidence=(artifact_evidence,),
                    navigation=member_navigation,
                    limitations=("Exact bytecode structure does not prove that this artifact or member was loaded or executed.",),
                    search_terms=tuple(
                        str(value)
                        for value in (
                            member.get("display_type"),
                            member.get("return_type"),
                            *(member.get("parameter_types") or []),
                            *(member.get("exceptions") or []),
                        )
                        if isinstance(value, str) and value
                    ),
                )
            ):
                break
        if truncated:
            break
        for reference in class_row.get("member_references", []):
            if not isinstance(reference, dict):
                continue
            qualified = f"{reference['owner']}#{reference['name']}"
            reference_id = content_id(
                "workbench-jvm-member-reference:sha256:",
                {"class_id": class_row["class_id"], **reference},
            )
            if not append(
                ExplorerRecord(
                    record_id=_source_facet_id(source_id, reference_id),
                    source_id=source_id,
                    authority="Project Intelligence",
                    state="static-possible",
                    kind="member-reference",
                    name=qualified,
                    identities=(
                        {"kind": "member-reference-id", "value": reference_id, "basis": "Project Intelligence JVM reference surface"},
                        {"kind": "source-member", "value": qualified, "basis": "classfile constant-pool reference"},
                        {"kind": "jvm-descriptor", "value": str(reference["descriptor"]), "basis": "classfile constant-pool reference"},
                    ),
                    owner=unresolved_owner("a classfile reference does not establish the referenced member's owner artifact"),
                    scope={"referring_artifact_id": artifact.get("artifact_id"), "referring_class": class_row["class_name"]},
                    runtime_form={"constant_pool_reference": dict(reference)},
                    relationships=({"predicate": "referenced-by", "target": {"kind": "class-name", "value": class_row["class_name"]}, "basis": "exact classfile constant pool"},),
                    evidence=(artifact_evidence,),
                    navigation=navigation,
                    limitations=("A constant-pool reference is statically possible and does not prove linkage or invocation.",),
                )
            ):
                break
        if truncated:
            break
    if not truncated:
        for resource in surface.get("resources", []):
            if not isinstance(resource, dict):
                continue
            entry_path = str(resource["path"])
            identities = [
                {"kind": "archive-entry", "value": entry_path, "basis": "exact JAR member"}
            ]
            parts = PurePosixPath(entry_path).parts
            kind = "resource"
            name = entry_path
            marker_index = next(
                (
                    parts.index(marker)
                    for marker in ("assets", "data")
                    if marker in parts
                ),
                None,
            )
            if marker_index is not None and len(parts) > marker_index + 2:
                namespace = parts[marker_index + 1]
                logical_parts = parts[marker_index + 2 :]
                logical = PurePosixPath(*logical_parts).as_posix()
                identities.append(
                    {
                        "kind": "resource-location",
                        "value": f"{namespace}:{logical}",
                        "basis": "archive resource path",
                    }
                )
                category = logical_parts[0]
                domain = {
                    "recipes": "recipe",
                    "loot_tables": "loot-table",
                    "advancements": "advancement",
                    "blockstates": "blockstate",
                    "models": "model",
                }.get(category)
                if domain is not None:
                    suffixless = PurePosixPath(*logical_parts[1:]).with_suffix("")
                    logical_id = suffixless.as_posix()
                    if logical_id and logical_id != ".":
                        identities.append(
                            {
                                "kind": f"{domain}-id",
                                "value": f"{namespace}:{logical_id}",
                                "basis": "archive resource path",
                            }
                        )
                        kind = domain
                        name = f"{namespace}:{logical_id}"
            if PurePosixPath(entry_path).name.startswith("mixins") and entry_path.endswith(".json"):
                kind = "mixin-config"
                name = entry_path
            elif entry_path.endswith(('.cfg', '.conf', '.toml', '.properties')):
                kind = "config-file"
                name = entry_path
            resource_id = content_id(
                "workbench-jvm-resource:sha256:",
                {"artifact_sha256": artifact.get("sha256"), "path": entry_path, "sha256": resource["sha256"]},
            )
            identities.append(
                {
                    "kind": "resource-record-id",
                    "value": resource_id,
                    "basis": "Project Intelligence JVM resource surface",
                }
            )
            if not append(
                ExplorerRecord(
                    record_id=_source_facet_id(source_id, resource_id),
                    source_id=source_id,
                    authority="Project Intelligence",
                    state="static-possible",
                    kind=kind,
                    name=name,
                    identities=tuple(identities),
                    owner=owner,
                    version=version,
                    scope={"artifact_id": artifact.get("artifact_id"), "runtime_selection": "unobserved"},
                    declaration={"path": f"{path}!/{entry_path}", "line": 1, "file_sha256": resource["sha256"], "language": "resource"},
                    runtime_form={"archive_resource": dict(resource)},
                    evidence=(artifact_evidence,),
                    navigation=({"kind": "archive-entry", "path": str(path.resolve()), "entry": entry_path, "line": 1, "column": None, "label": entry_path, "resolution": "exact-archive-entry"},),
                    limitations=("Archive resource presence does not prove runtime resource-pack selection or override order.",),
                )
            ):
                break
    return records, truncated


def artifact_provider(paths: tuple[Path, ...]) -> ProviderResult:
    if not paths:
        raise ExplorerError("artifact provider requires at least one artifact")
    if len(paths) > MAX_ARTIFACT_INPUTS:
        raise ExplorerError(
            f"artifact provider accepts at most {MAX_ARTIFACT_INPUTS} artifacts"
        )
    from workbench_project_intelligence import (
        ArtifactInput,
        ArtifactScanError,
        build_topology_receipt,
        scan_artifact_bytes,
        scan_jvm_archive_bytes,
        validate_topology_receipt,
    )

    try:
        inputs: list[ArtifactInput] = []
        scans: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        total_artifact_bytes = 0
        for path in paths:
            raw, _ = _safe_bytes(path, maximum=512 * 1024 * 1024)
            total_artifact_bytes += len(raw)
            if total_artifact_bytes > MAX_ARTIFACT_SET_BYTES:
                raise ExplorerError(
                    "artifact inputs exceed the 512 MiB aggregate explorer bound"
                )
            inputs.append(ArtifactInput(label=path.name, data=raw))
            scans.append(
                (
                    path,
                    scan_artifact_bytes(raw, label=path.name),
                    scan_jvm_archive_bytes(raw, label=path.name),
                )
            )
        receipt = build_topology_receipt(inputs)
        validate_topology_receipt(receipt)
    except ArtifactScanError as exc:
        raise ExplorerError(f"Project Intelligence artifact scan failed: {exc}") from exc
    source_id = str(receipt["receipt_id"])
    records = _topology_records(receipt, source_id)
    truncated = len(records) > MAX_EVIDENCE_RECORDS
    records = records[:MAX_EVIDENCE_RECORDS]
    artifacts_by_sha = {
        str(row["sha256"]): row
        for row in receipt["artifacts"]
        if isinstance(row, dict)
    }
    jvm_coverage: list[dict[str, Any]] = []
    for path, scan, surface in scans:
        artifact = artifacts_by_sha.get(str(surface["artifact"]["sha256"]))
        if artifact is None:
            raise ExplorerError(
                f"JVM surface is not bound to the topology receipt: {path}"
            )
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        remaining = MAX_EVIDENCE_RECORDS - len(records)
        rows, limited = _jvm_surface_records(
            path=path,
            scan=scan,
            surface=surface,
            artifact=artifact,
            source_id=source_id,
            maximum=remaining,
        )
        records.extend(rows)
        limited = limited or not bool(surface["coverage"]["complete"])
        truncated = truncated or limited
        jvm_coverage.append(
            {
                "path": str(path.resolve()),
                **dict(surface["coverage"]),
                "explorer_records": len(rows),
                "explorer_truncated": limited,
            }
        )
    boundaries = receipt.get("boundaries")
    if isinstance(boundaries, list):
        limitations = tuple(str(value) for value in boundaries)
    elif isinstance(boundaries, dict):
        limitations = tuple(
            f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"
            for key, value in sorted(boundaries.items())
        )
    else:
        limitations = ()
    source = ExplorerSource(
        source_id=source_id,
        source_kind="mixin-artifact-topology",
        authority="Project Intelligence",
        state="partial" if truncated else "complete",
        identity={"format": receipt["format"], "receipt_id": receipt["receipt_id"]},
        scope=dict(receipt["scope"]),
        coverage={"topology": dict(receipt["summary"]), "jvm_surfaces": jvm_coverage, "records": len(records), "truncated": truncated},
        limitations=limitations,
    )
    return ProviderResult(source, tuple(records))


def _generic_identity_kind(field: str) -> str | None:
    normalized = field.casefold().replace("-", "_")
    if _SENSITIVE_FIELD_RE.search(normalized):
        return None
    known = _GENERIC_IDENTITY_FIELDS.get(normalized)
    if known:
        return known
    if normalized.endswith("_id") or normalized in {"id", "identifier"}:
        return "record-id"
    if normalized.endswith("_name") or normalized in {"name", "logical_name"}:
        return "declared-name"
    if normalized.endswith("_path") or normalized in {"path", "file"}:
        return "declared-path"
    if normalized in {"predicate", "kind", "code", "stage", "phase"}:
        return "evidence-label"
    return None


def _generic_receipt_records(
    value: Mapping[str, Any],
    *,
    source_id: str,
    authority: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    records: list[ExplorerRecord] = []
    truncated = False

    def walk(item: Any, pointer: str, field: str, depth: int) -> None:
        nonlocal truncated
        if truncated or depth > 64:
            truncated = True
            return
        if isinstance(item, dict):
            for key in sorted(item):
                encoded = str(key).replace("~", "~0").replace("/", "~1")
                walk(item[key], pointer + "/" + encoded, str(key), depth + 1)
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, pointer + f"/{index}", field, depth + 1)
            return
        identity_kind = _generic_identity_kind(field)
        if identity_kind is None or isinstance(item, bool) or not isinstance(item, (str, int)):
            return
        rendered = str(item)
        if (
            not rendered
            or len(rendered) > 8192
            or any(character in rendered for character in "\r\n\x00")
        ):
            return
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            return
        record_id = content_id(
            "workbench-runtime-evidence-field:sha256:",
            {"source_id": source_id, "pointer": pointer, "field": field, "value": rendered},
        )
        records.append(
            ExplorerRecord(
                record_id=record_id,
                source_id=source_id,
                authority=authority,
                state="verbatim-evidence",
                kind=identity_kind,
                name=rendered,
                identities=({"kind": identity_kind, "value": rendered, "basis": f"verbatim receipt field {field}"},),
                owner=unresolved_owner("generic receipt projection does not infer ownership"),
                scope={"receipt_format": value.get("format"), "receipt_id": value.get("receipt_id") or value.get("id")},
                runtime_form={"json_pointer": pointer, "field": field, "value": rendered},
                evidence=({"path": str(receipt_path), "sha256": receipt_sha256, "json_pointer": pointer, "claim": "verbatim-field"},),
                navigation=({"kind": "receipt", "path": str(receipt_path), "line": 1, "column": None, "label": pointer, "resolution": "exact-file-json-pointer"},),
                limitations=("This generic projection preserves a receipt field but does not infer its runtime meaning or authority state.",),
                search_terms=(pointer,),
            )
        )

    walk(value, "", "root", 0)
    return records, truncated


def _receipt_identity(
    kind: str,
    value: object,
    basis: str,
) -> dict[str, str] | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    rendered = str(value)
    if (
        not rendered
        or len(rendered) > 8192
        or any(character in rendered for character in "\r\n\x00")
    ):
        return None
    return {"kind": kind, "value": rendered, "basis": basis}


def _receipt_limitations(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        rendered = " ".join(value.replace("\x00", "\\x00").splitlines())[:8192]
        if rendered:
            result.append(rendered)
    return tuple(dict.fromkeys(result))


def _receipt_navigation(
    path: Path,
    pointer: str,
    label: str,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "kind": "receipt",
            "path": str(path.resolve()),
            "line": 1,
            "column": None,
            "label": label,
            "json_pointer": pointer,
            "resolution": "exact-file-json-pointer",
        },
    )


def _mixin_ledger_records(
    ledger: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = ledger["session"]
    ledger_id = str(ledger["ledger_id"])
    common_limitations = _receipt_limitations(ledger.get("limitations", ()))
    records: list[ExplorerRecord] = []
    truncated = False
    for index, observation in enumerate(ledger["observations"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        subject = observation["subject"]
        identities: list[dict[str, str]] = [
            {
                "kind": "event-id",
                "value": f"{ledger_id}#observation-{observation['sequence']}",
                "basis": "validated Crucible ledger sequence",
            }
        ]
        for field, kind in (
            ("artifact_sha256", "artifact-sha256"),
            ("owner_label", "owner-label"),
            ("config", "config-key"),
            ("mixin", "mixin-class"),
            ("target_class", "class-name"),
            ("target_member", "target-member"),
            ("generated_class", "class-name"),
        ):
            identity = _receipt_identity(
                kind,
                subject.get(field),
                f"validated transformation subject.{field}",
            )
            if identity is not None:
                identities.append(identity)
        target_class = subject.get("target_class")
        target_member = subject.get("target_member")
        if isinstance(target_class, str) and isinstance(target_member, str):
            member = _receipt_identity(
                "source-member",
                f"{target_class}#{target_member}",
                "validated final-member attribution",
            )
            if member is not None:
                identities.append(member)
        for field in (
            "input_bytecode_sha256",
            "stage_bytecode_sha256",
            "final_bytecode_sha256",
        ):
            identity = _receipt_identity(
                field.replace("bytecode_", "class-").replace("_", "-"),
                observation.get(field),
                f"validated transformation observation.{field}",
            )
            if identity is not None:
                identities.append(identity)
        owner_actors: list[dict[str, Any]] = []
        if isinstance(subject.get("owner_label"), str):
            owner_actors.append(
                {
                    "kind": "runtime-owner-label",
                    "id": subject["owner_label"],
                }
            )
        if isinstance(subject.get("artifact_sha256"), str):
            owner_actors.append(
                {
                    "kind": "artifact",
                    "sha256": subject["artifact_sha256"],
                }
            )
        owner = (
            {
                "state": "receipt-bound",
                "actors": owner_actors,
                "basis": "validated transformation observation subject",
            }
            if owner_actors
            else unresolved_owner(
                "the validated transformation observation supplied no owner label or artifact"
            )
        )
        name = next(
            (
                str(subject[key])
                for key in (
                    "target_member",
                    "target_class",
                    "mixin",
                    "generated_class",
                    "config",
                    "artifact_sha256",
                )
                if isinstance(subject.get(key), str) and subject.get(key)
            ),
            str(observation["stage"]),
        )
        pointer = f"/observations/{index}"
        outcome = str(observation["outcome"])
        stage_limitations = list(common_limitations)
        if observation["stage"] == "apply_started":
            stage_limitations.append(
                "An observed apply_started stage does not prove applicator completion or survival through the later transformer chain."
            )
        records.append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-mixin-observation:sha256:",
                    {
                        "ledger_id": ledger_id,
                        "sequence": observation["sequence"],
                    },
                ),
                source_id=source_id,
                authority="Crucible",
                state="observed" if outcome in {"observed", "failed"} else "unresolved",
                kind="mixin-" + str(observation["stage"]).replace("_", "-"),
                name=name,
                identities=tuple(unique_dicts(identities)),
                owner=owner,
                scope={
                    "session_id": session["session_id"],
                    "launch_id": session["launch_id"],
                    "profile": session["profile_id"],
                    "physical_side": session["side"],
                    "launch_state": ledger["launch_state"],
                    "phase": observation["phase"],
                },
                runtime_form={
                    "ledger_id": ledger_id,
                    "observation": dict(observation),
                },
                relationships=tuple(
                    row
                    for row in (
                        {
                            "predicate": "targets",
                            "target": {"kind": "class-name", "value": target_class},
                            "basis": "validated transformation subject",
                        }
                        if isinstance(target_class, str)
                        else None,
                        {
                            "predicate": "applies-mixin",
                            "target": {"kind": "mixin-class", "value": subject["mixin"]},
                            "basis": "validated transformation subject",
                        }
                        if isinstance(subject.get("mixin"), str)
                        else None,
                    )
                    if row is not None
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "ledger_id": ledger_id,
                        "json_pointer": pointer,
                        "claim": "validated-crucible-transformation-observation",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path,
                    pointer,
                    f"Mixin {observation['stage']} observation",
                ),
                limitations=tuple(dict.fromkeys(stage_limitations)),
                search_terms=tuple(
                    str(value)[:8192]
                    for value in (
                        observation.get("message"),
                        observation.get("thread"),
                        observation["stage"],
                        observation["phase"],
                        observation["evidence"].get("kind"),
                        observation["evidence"].get("source_record"),
                    )
                    if isinstance(value, str)
                    and value
                    and not any(character in value for character in "\r\n\x00")
                ),
            )
        )
    return records, truncated


def _mixin_runtime_service_records(
    receipt: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = receipt["session"]
    receipt_id = str(receipt["receipt_id"])
    artifacts = {
        str(row["artifact_sha256"]): row
        for row in receipt["artifacts"]
        if isinstance(row, Mapping)
    }
    common_limitations = _receipt_limitations(receipt.get("limitations", ()))
    records: list[ExplorerRecord] = []
    truncated = False

    def append(record: ExplorerRecord) -> None:
        nonlocal truncated
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            return
        records.append(record)

    for index, observation in enumerate(receipt["observations"]):
        identities: list[dict[str, str]] = []
        for kind, value, basis in (
            ("service-name", observation.get("service_name"), "validated runtime service report"),
            ("class-name", observation.get("service_class"), "validated runtime service class report"),
            ("artifact-sha256", observation.get("source_artifact_sha256"), "validated runtime service code source"),
            ("event-id", f"{receipt_id}#observation-{observation['sequence']}", "validated runtime service sequence"),
        ):
            identity = _receipt_identity(kind, value, basis)
            if identity is not None:
                identities.append(identity)
        artifact = artifacts.get(str(observation.get("source_artifact_sha256")))
        owner = (
            {
                "state": "receipt-bound",
                "actors": [
                    {
                        "kind": "artifact",
                        "sha256": artifact["artifact_sha256"],
                        "name": artifact["label"],
                        "role": artifact["role"],
                    }
                ],
                "basis": "validated source_artifact_sha256 binding",
            }
            if artifact is not None
            else unresolved_owner(
                "a reported service name does not establish its implementation artifact"
            )
        )
        pointer = f"/observations/{index}"
        append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-mixin-service-report:sha256:",
                    {"receipt_id": receipt_id, "sequence": observation["sequence"]},
                ),
                source_id=source_id,
                authority="Crucible",
                state="observed",
                kind="mixin-runtime-service-report",
                name=str(observation["service_name"]),
                identities=tuple(unique_dicts(identities)),
                owner=owner,
                version=(
                    str(observation["subsystem_version"])
                    if isinstance(observation.get("subsystem_version"), str)
                    else None
                ),
                scope={
                    "session_id": session["session_id"],
                    "launch_id": session["launch_id"],
                    "profile": session["profile_id"],
                    "physical_side": session["side"],
                    "report_kind": observation["kind"],
                },
                runtime_form={
                    "receipt_id": receipt_id,
                    "observation": dict(observation),
                },
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "receipt_id": receipt_id,
                        "json_pointer": pointer,
                        "claim": "validated-crucible-runtime-service-report",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path, pointer, "Mixin runtime service report"
                ),
                limitations=common_limitations,
                search_terms=tuple(
                    str(value)[:8192]
                    for value in (
                        observation.get("kind"),
                        observation.get("environment"),
                        observation.get("logger"),
                        observation.get("thread"),
                        observation.get("source_record"),
                    )
                    if isinstance(value, str)
                    and value
                    and not any(character in value for character in "\r\n\x00")
                ),
            )
        )
        if truncated:
            break

    enumeration = receipt["provider_enumeration"]
    if not truncated and enumeration["state"] == "enumerated":
        for index, provider in enumerate(enumeration["providers"]):
            identities = tuple(
                identity
                for identity in (
                    _receipt_identity(
                        "class-name",
                        provider.get("service_class"),
                        "validated ServiceLoader enumeration",
                    ),
                    _receipt_identity(
                        "service-name",
                        provider.get("service_name"),
                        "validated ServiceLoader enumeration",
                    ),
                    _receipt_identity(
                        "artifact-sha256",
                        provider.get("provider_artifact_sha256"),
                        "validated ServiceLoader provider artifact",
                    ),
                )
                if identity is not None
            )
            artifact = artifacts.get(str(provider.get("provider_artifact_sha256")))
            pointer = f"/provider_enumeration/providers/{index}"
            append(
                ExplorerRecord(
                    record_id=content_id(
                        "workbench-runtime-mixin-service-provider:sha256:",
                        {"receipt_id": receipt_id, "provider": provider},
                    ),
                    source_id=source_id,
                    authority="Crucible",
                    state="observed",
                    kind="mixin-service-provider",
                    name=str(provider["service_class"]),
                    identities=identities,
                    owner=(
                        {
                            "state": "receipt-bound",
                            "actors": [
                                {
                                    "kind": "artifact",
                                    "sha256": artifact["artifact_sha256"],
                                    "name": artifact["label"],
                                    "role": artifact["role"],
                                }
                            ],
                            "basis": "validated provider_artifact_sha256 binding",
                        }
                        if artifact is not None
                        else unresolved_owner(
                            "enumerated provider artifact is absent from the receipt artifact set"
                        )
                    ),
                    scope={
                        "session_id": session["session_id"],
                        "launch_id": session["launch_id"],
                        "profile": session["profile_id"],
                        "physical_side": session["side"],
                        "enumeration_mechanism": enumeration["mechanism"],
                    },
                    runtime_form={
                        "receipt_id": receipt_id,
                        "provider_enumeration": dict(provider),
                    },
                    evidence=(
                        {
                            "path": str(receipt_path.resolve()),
                            "sha256": receipt_sha256,
                            "receipt_id": receipt_id,
                            "json_pointer": pointer,
                            "claim": "validated-service-loader-enumeration",
                        },
                    ),
                    navigation=_receipt_navigation(
                        receipt_path, pointer, "Enumerated Mixin service provider"
                    ),
                    limitations=tuple(
                        dict.fromkeys(
                            [
                                *common_limitations,
                                "ServiceLoader enumeration proves provider visibility during the probe, not runtime selection.",
                            ]
                        )
                    ),
                )
            )
            if truncated:
                break
    return records, truncated


def _mixin_service_component_records(
    receipt: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = receipt["session"]
    receipt_id = str(receipt["receipt_id"])
    artifacts = {
        str(row["artifact_sha256"]): row
        for row in receipt["artifacts"]
        if isinstance(row, Mapping)
    }
    common_limitations = _receipt_limitations(receipt.get("limitations", ()))
    records: list[ExplorerRecord] = []
    truncated = False
    for index, component in enumerate(receipt["components"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        artifact = artifacts.get(str(component["artifact_sha256"]))
        identities = tuple(
            identity
            for identity in (
                _receipt_identity(
                    "component-role",
                    component.get("role"),
                    "validated selected-service component role",
                ),
                _receipt_identity(
                    "class-name",
                    component.get("implementation_class"),
                    "observed component implementation",
                ),
                _receipt_identity(
                    "object-identity",
                    component.get("object_identity"),
                    "launch-local observed object identity",
                ),
                _receipt_identity(
                    "classloader-class",
                    component.get("implementation_loader_class"),
                    "observed component defining loader",
                ),
                _receipt_identity(
                    "classloader-identity",
                    component.get("implementation_loader_identity"),
                    "launch-local component defining-loader identity",
                ),
                _receipt_identity(
                    "artifact-sha256",
                    component.get("artifact_sha256"),
                    "measured component code source",
                ),
                _receipt_identity(
                    "service-name",
                    component.get("reported_name"),
                    "normally reported component name",
                ),
            )
            if identity is not None
        )
        pointer = f"/components/{index}"
        records.append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-mixin-service-component:sha256:",
                    {
                        "receipt_id": receipt_id,
                        "role": component["role"],
                        "sequence": component["sequence"],
                    },
                ),
                source_id=source_id,
                authority="Crucible",
                state="observed",
                kind="mixin-service-component",
                name=str(component["implementation_class"]),
                identities=identities,
                owner={
                    "state": "exact-artifact",
                    "actors": [
                        {
                            "kind": "artifact",
                            "sha256": component["artifact_sha256"],
                            "name": artifact["label"] if artifact else None,
                            "code_source_uri": component["code_source_uri"],
                        }
                    ],
                    "basis": "validated component artifact binding",
                },
                scope={
                    "capture_id": session["capture_id"],
                    "launch_id": session["launch_id"],
                    "profile": session["profile_id"],
                    "physical_side": session["side"],
                    "component_role": component["role"],
                },
                runtime_form={
                    "receipt_id": receipt_id,
                    "component": dict(component),
                    "observation": dict(receipt["observation"]),
                },
                relationships=(
                    {
                        "predicate": "defined-by-loader",
                        "target": {
                            "kind": "classloader-identity",
                            "value": component["implementation_loader_identity"],
                        },
                        "basis": "validated selected-service component observation",
                    },
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "receipt_id": receipt_id,
                        "json_pointer": pointer,
                        "claim": "validated-selected-mixin-service-component",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path,
                    pointer,
                    f"Mixin service component {component['role']}",
                ),
                limitations=tuple(
                    dict.fromkeys(
                        [
                            *common_limitations,
                            "Object and loader identity values are launch-local and are not cross-launch semantic identities.",
                        ]
                    )
                ),
                search_terms=tuple(
                    str(value)
                    for value in (
                        component["role"],
                        component.get("reported_name"),
                        component["code_source_uri"],
                    )
                    if isinstance(value, str) and value
                ),
            )
        )
    return records, truncated


def _mixin_transformer_chain_records(
    receipt: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = receipt["session"]
    receipt_id = str(receipt["receipt_id"])
    artifacts = {
        str(row["artifact_sha256"]): row
        for row in receipt["artifacts"]
        if isinstance(row, Mapping)
    }
    refreshes = {
        int(row["refresh_id"]): row
        for row in receipt["refreshes"]
        if isinstance(row, Mapping)
    }
    common_limitations = _receipt_limitations(receipt.get("limitations", ()))
    records: list[ExplorerRecord] = []
    truncated = False
    for index, epoch in enumerate(receipt["epochs"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        provider_artifact = artifacts.get(str(epoch["provider_artifact_sha256"]))
        foundation_artifact = artifacts.get(
            str(epoch["foundation_artifact_sha256"])
        )
        refresh = refreshes[int(epoch["refresh_id"])]
        identities = tuple(
            identity
            for identity in (
                _receipt_identity(
                    "transformer-chain-epoch",
                    epoch.get("epoch"),
                    "validated chain epoch ordinal",
                ),
                _receipt_identity(
                    "class-name",
                    epoch.get("provider_class"),
                    "observed Cleanroom transformer provider",
                ),
                _receipt_identity(
                    "artifact-sha256",
                    epoch.get("provider_artifact_sha256"),
                    "measured provider code source",
                ),
                _receipt_identity(
                    "foundation-artifact-sha256",
                    epoch.get("foundation_artifact_sha256"),
                    "measured Foundation transformer code source",
                ),
            )
            if identity is not None
        )
        pointer = f"/epochs/{index}"
        relationships = tuple(
            {
                "predicate": "delegates-transformer",
                "target": {
                    "kind": "class-name",
                    "value": transformer["implementation_class"],
                    "ordinal": transformer["ordinal"],
                },
                "basis": "validated delegated transformer-chain order",
            }
            for transformer in epoch["delegated_chain"]
        )
        search_terms = tuple(
            dict.fromkeys(
                str(value)
                for value in (
                    epoch["phase"],
                    epoch["refresh_reason"],
                    refresh["outcome"],
                    *(item
                      for transformer in epoch["live_chain"]
                      for item in (
                          transformer["reported_name"],
                          transformer["implementation_class"],
                          transformer.get("wrapper_class"),
                      )),
                    *epoch["provider_exclusions"],
                    *epoch["foundation_transformer_exclusions"],
                )
                if isinstance(value, str) and value
            )
        )
        records.append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-mixin-transformer-chain-epoch:sha256:",
                    {"receipt_id": receipt_id, "epoch": epoch["epoch"]},
                ),
                source_id=source_id,
                authority="Crucible",
                state="observed",
                kind="mixin-transformer-chain-epoch",
                name=f"Transformer chain epoch {epoch['epoch']} ({epoch['phase']})",
                identities=identities,
                owner={
                    "state": "exact-artifacts",
                    "actors": [
                        {
                            "kind": "artifact",
                            "sha256": epoch["provider_artifact_sha256"],
                            "name": (
                                provider_artifact["label"]
                                if provider_artifact is not None
                                else None
                            ),
                            "role": "transformer-provider",
                        },
                        {
                            "kind": "artifact",
                            "sha256": epoch["foundation_artifact_sha256"],
                            "name": (
                                foundation_artifact["label"]
                                if foundation_artifact is not None
                                else None
                            ),
                            "role": "classloader-transformer-chain",
                        },
                    ],
                    "basis": "validated provider and Foundation artifact bindings",
                },
                scope={
                    "capture_id": session["capture_id"],
                    "launch_id": session["launch_id"],
                    "profile": session["profile_id"],
                    "physical_side": session["side"],
                    "epoch": epoch["epoch"],
                    "phase": epoch["phase"],
                    "refresh_id": epoch["refresh_id"],
                    "refresh_reason": epoch["refresh_reason"],
                    "refresh_outcome": refresh["outcome"],
                    "live_transformer_count": len(epoch["live_chain"]),
                    "delegated_transformer_count": len(epoch["delegated_chain"]),
                },
                runtime_form={
                    "receipt_id": receipt_id,
                    "epoch": dict(epoch),
                    "refresh": dict(refresh),
                },
                relationships=relationships,
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "receipt_id": receipt_id,
                        "json_pointer": pointer,
                        "claim": "validated-transformer-chain-epoch",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path,
                    pointer,
                    f"Transformer chain epoch {epoch['epoch']}",
                ),
                limitations=common_limitations,
                search_terms=search_terms,
            )
        )
        for chain_name in ("live_chain", "delegated_chain"):
            for transformer_index, transformer in enumerate(epoch[chain_name]):
                if len(records) >= MAX_EVIDENCE_RECORDS:
                    truncated = True
                    break
                artifact = artifacts.get(str(transformer["artifact_sha256"]))
                transformer_pointer = (
                    f"/epochs/{index}/{chain_name}/{transformer_index}"
                )
                entry_id = (
                    f"{receipt_id}#epoch={epoch['epoch']}"
                    f"#chain={chain_name}#ordinal={transformer['ordinal']}"
                )
                entry_identities = tuple(
                    identity
                    for identity in (
                        _receipt_identity(
                            "transformer-chain-entry",
                            entry_id,
                            "validated epoch, chain, and ordinal",
                        ),
                        _receipt_identity(
                            "class-name",
                            transformer.get("implementation_class"),
                            "observed transformer implementation",
                        ),
                        _receipt_identity(
                            "transformer-name",
                            transformer.get("reported_name"),
                            "observed transformer-reported name",
                        ),
                        _receipt_identity(
                            "wrapper-class-name",
                            transformer.get("wrapper_class"),
                            "observed generated or runtime wrapper",
                        ),
                        _receipt_identity(
                            "artifact-sha256",
                            transformer.get("artifact_sha256"),
                            "measured transformer implementation code source",
                        ),
                    )
                    if identity is not None
                )
                records.append(
                    ExplorerRecord(
                        record_id=content_id(
                            "workbench-runtime-mixin-transformer-chain-entry:sha256:",
                            {"receipt_id": receipt_id, "entry": entry_id},
                        ),
                        source_id=source_id,
                        authority="Crucible",
                        state="observed",
                        kind="mixin-transformer-chain-entry",
                        name=str(transformer["implementation_class"]),
                        identities=entry_identities,
                        owner={
                            "state": "exact-artifact",
                            "actors": [
                                {
                                    "kind": "artifact",
                                    "sha256": transformer["artifact_sha256"],
                                    "name": artifact["label"] if artifact else None,
                                }
                            ],
                            "basis": "validated transformer implementation artifact binding",
                        },
                        scope={
                            "capture_id": session["capture_id"],
                            "launch_id": session["launch_id"],
                            "profile": session["profile_id"],
                            "physical_side": session["side"],
                            "epoch": epoch["epoch"],
                            "phase": epoch["phase"],
                            "chain": chain_name.removesuffix("_chain"),
                            "ordinal": transformer["ordinal"],
                            "delegation_excluded": transformer[
                                "delegation_excluded"
                            ],
                        },
                        runtime_form={
                            "receipt_id": receipt_id,
                            "transformer": dict(transformer),
                            "refresh_id": epoch["refresh_id"],
                            "refresh_reason": epoch["refresh_reason"],
                        },
                        relationships=(
                            {
                                "predicate": "member-of-transformer-chain-epoch",
                                "target": {
                                    "kind": "transformer-chain-epoch",
                                    "value": epoch["epoch"],
                                },
                                "basis": "validated epoch chain membership",
                            },
                        ),
                        evidence=(
                            {
                                "path": str(receipt_path.resolve()),
                                "sha256": receipt_sha256,
                                "receipt_id": receipt_id,
                                "json_pointer": transformer_pointer,
                                "claim": "validated-transformer-chain-entry",
                            },
                        ),
                        navigation=_receipt_navigation(
                            receipt_path,
                            transformer_pointer,
                            (
                                f"Transformer epoch {epoch['epoch']} "
                                f"{chain_name.removesuffix('_chain')} "
                                f"ordinal {transformer['ordinal']}"
                            ),
                        ),
                        limitations=common_limitations,
                        search_terms=tuple(
                            str(value)
                            for value in (
                                epoch["refresh_reason"],
                                epoch["phase"],
                            )
                            if isinstance(value, str) and value
                        ),
                    )
                )
            if truncated:
                break
        if truncated:
            break
    return records, truncated


def _mixin_final_definition_records(
    receipt: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = receipt["session"]
    receipt_id = str(receipt["receipt_id"])
    artifacts = {
        str(row["artifact_sha256"]): row
        for row in receipt["artifacts"]
        if isinstance(row, Mapping)
    }
    foundation = receipt["foundation"]
    common_limitations = _receipt_limitations(receipt.get("limitations", ()))
    records: list[ExplorerRecord] = []
    truncated = False
    for index, definition in enumerate(receipt["definitions"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        target_artifact = artifacts.get(str(definition["target_artifact_sha256"]))
        foundation_artifact = artifacts.get(
            str(foundation["foundation_artifact_sha256"])
        )
        pointer = f"/definitions/{index}"
        identities = tuple(
            identity
            for identity in (
                _receipt_identity(
                    "class-name",
                    definition.get("target_class"),
                    "successfully defined target class",
                ),
                _receipt_identity(
                    "final-class-sha256",
                    definition.get("final_bytecode_sha256"),
                    "Foundation-custodied final definition bytes",
                ),
                _receipt_identity(
                    "artifact-sha256",
                    definition.get("target_artifact_sha256"),
                    "measured target code source",
                ),
                _receipt_identity(
                    "classloader-identity",
                    definition.get("defining_loader_identity"),
                    "launch-local successful defining-loader identity",
                ),
            )
            if identity is not None
        )
        records.append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-mixin-final-class-definition:sha256:",
                    {
                        "receipt_id": receipt_id,
                        "target_class": definition["target_class"],
                        "final_bytecode_sha256": definition[
                            "final_bytecode_sha256"
                        ],
                    },
                ),
                source_id=source_id,
                authority="Crucible",
                state="observed",
                kind="mixin-final-class-definition",
                name=str(definition["target_class"]),
                identities=identities,
                owner={
                    "state": "exact-artifacts",
                    "actors": [
                        {
                            "kind": "artifact",
                            "sha256": definition["target_artifact_sha256"],
                            "name": (
                                target_artifact["label"]
                                if target_artifact is not None
                                else None
                            ),
                            "role": "target-code-source",
                        },
                        {
                            "kind": "artifact",
                            "sha256": foundation[
                                "foundation_artifact_sha256"
                            ],
                            "name": (
                                foundation_artifact["label"]
                                if foundation_artifact is not None
                                else None
                            ),
                            "role": "final-definition-custodian",
                        },
                    ],
                    "basis": "validated target and Foundation artifact bindings",
                },
                scope={
                    "capture_id": session["capture_id"],
                    "launch_id": session["launch_id"],
                    "profile": session["profile_id"],
                    "physical_side": session["side"],
                    "defining_loader_class": definition[
                        "defining_loader_class"
                    ],
                    "final_bytecode_size": definition["final_bytecode_size"],
                },
                runtime_form={
                    "receipt_id": receipt_id,
                    "definition": dict(definition),
                    "foundation": dict(foundation),
                },
                relationships=(
                    {
                        "predicate": "defined-by-loader",
                        "target": {
                            "kind": "classloader-identity",
                            "value": definition["defining_loader_identity"],
                        },
                        "basis": "validated successful Foundation findClass return",
                    },
                    {
                        "predicate": "retained-by-foundation-manifest",
                        "target": {
                            "kind": "foundation-manifest-sha256",
                            "value": foundation["manifest_sha256"],
                        },
                        "basis": "validated stable Foundation dump generation",
                    },
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "receipt_id": receipt_id,
                        "json_pointer": pointer,
                        "claim": "validated-final-class-definition",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path,
                    pointer,
                    f"Final definition {definition['target_class']}",
                ),
                limitations=common_limitations,
                search_terms=(
                    str(definition["dump_relative_path"]),
                    str(foundation["manifest_sha256"]),
                ),
            )
        )
    return records, truncated


def _mixin_config_lifecycle_records(
    receipt: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = receipt["session"]
    receipt_id = str(receipt["receipt_id"])
    artifacts = {
        str(row["artifact_sha256"]): row
        for row in receipt["artifacts"]
        if isinstance(row, Mapping)
    }
    common_limitations = _receipt_limitations(receipt.get("limitations", ()))
    records: list[ExplorerRecord] = []
    truncated = False
    for index, config in enumerate(receipt["configurations"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        resource = config["resource"]
        admission = config["admission"]
        terminal = config["terminal"]
        artifact = artifacts.get(str(resource.get("artifact_sha256")))
        identities = tuple(
            identity
            for identity in (
                _receipt_identity(
                    "mixin-config-name",
                    config.get("config_name") or config.get("requested_config"),
                    "validated CleanMix configuration name",
                ),
                _receipt_identity(
                    "mod-id",
                    resource.get("owner_id"),
                    "observed configuration source id",
                ),
                _receipt_identity(
                    "artifact-sha256",
                    resource.get("artifact_sha256"),
                    "verified configuration owner artifact",
                ),
                _receipt_identity(
                    "resource-entry-sha256",
                    resource.get("entry_sha256"),
                    "verified installed configuration entry",
                ),
            )
            if identity is not None
        )
        pointer = f"/configurations/{index}"
        records.append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-mixin-config-lifecycle:sha256:",
                    {
                        "receipt_id": receipt_id,
                        "attempt": config["attempt"],
                        "terminal": terminal,
                    },
                ),
                source_id=source_id,
                authority="Crucible",
                state="observed",
                kind="mixin-config-lifecycle",
                name=str(config.get("config_name") or config["requested_config"]),
                identities=identities,
                owner=(
                    {
                        "state": "exact-artifact",
                        "actors": [
                            {
                                "kind": "artifact",
                                "sha256": resource["artifact_sha256"],
                                "name": artifact["label"] if artifact else None,
                                "code_source_uri": resource["owner_description"],
                            }
                        ],
                        "basis": "verified configuration resource archive entry",
                    }
                    if resource.get("artifact_sha256") is not None
                    else unresolved_owner(
                        "this lifecycle attempt did not independently resolve a configuration resource"
                    )
                ),
                scope={
                    "capture_id": session["capture_id"],
                    "launch_id": session["launch_id"],
                    "profile": session["profile_id"],
                    "physical_side": session["side"],
                    "attempt": config["attempt"],
                    "admission_decision": admission["decision"],
                    "queued_phase": admission["queued_phase"],
                    "terminal_outcome": terminal["outcome"],
                },
                runtime_form={
                    "receipt_id": receipt_id,
                    "configuration": dict(config),
                    "observation": dict(receipt["observation"]),
                },
                relationships=tuple(
                    relationship
                    for relationship in (
                        {
                            "predicate": "owned-by-source",
                            "target": {
                                "kind": "mod-id",
                                "value": resource["owner_id"],
                            },
                            "basis": "observed CleanMix configuration source",
                        }
                        if resource.get("owner_id") is not None
                        else None,
                        {
                            "predicate": "queued-for-phase",
                            "target": {
                                "kind": "mixin-environment-phase",
                                "value": admission["queued_phase"],
                            },
                            "basis": "observed CleanMix admission decision",
                        }
                        if admission.get("queued_phase") is not None
                        else None,
                    )
                    if relationship is not None
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "receipt_id": receipt_id,
                        "json_pointer": pointer,
                        "claim": "validated-mixin-config-lifecycle",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path,
                    pointer,
                    f"Mixin config lifecycle attempt {config['attempt']}",
                ),
                limitations=common_limitations,
                search_terms=tuple(
                    str(value)
                    for value in (
                        config["requested_config"],
                        resource.get("url"),
                        resource.get("owner_id"),
                        admission["decision"],
                        admission["reason_code"],
                        admission.get("queued_phase"),
                        terminal["outcome"],
                        terminal["reason_code"],
                    )
                    if isinstance(value, str) and value
                ),
            )
        )
    return records, truncated


def _runtime_snapshot_records(
    snapshot: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    runtime = snapshot["runtime_identity"]
    epoch = snapshot["epoch"]
    snapshot_id = str(snapshot["snapshot_id"])
    common_scope = {
        "launch_id": runtime["launch_id"],
        "profile": runtime["platform_profile_id"],
        "pack_profile_id": runtime["pack_profile_id"],
        "physical_side": runtime["physical_side"],
        "process_outcome": runtime["process_outcome"],
        "profile_epoch_id": epoch["profile_epoch_id"],
        "adapter_id": epoch["adapter_id"],
        "adapter_version": epoch["adapter_version"],
    }
    custody = {
        "state": "runtime-custody",
        "actors": [{"kind": "authority", "id": "Crucible"}],
        "basis": "validated Crucible runtime snapshot V2",
    }
    records: list[ExplorerRecord] = [
        ExplorerRecord(
            record_id=content_id(
                "workbench-runtime-snapshot-observation:sha256:",
                {"snapshot_id": snapshot_id},
            ),
            source_id=source_id,
            authority="Crucible",
            state="observed",
            kind="runtime-snapshot",
            name=snapshot_id,
            identities=tuple(
                unique_dicts(
                    (
                        {
                            "kind": "runtime-snapshot-id",
                            "value": snapshot_id,
                            "basis": "validated content-addressed snapshot",
                        },
                        {
                            "kind": "launch-id",
                            "value": runtime["launch_id"],
                            "basis": "validated runtime identity",
                        },
                        {
                            "kind": "profile-id",
                            "value": runtime["platform_profile_id"],
                            "basis": "validated runtime identity",
                        },
                        {
                            "kind": "runtime-epoch-id",
                            "value": epoch["profile_epoch_id"],
                            "basis": "profile-owned epoch adapter",
                        },
                    )
                )
            ),
            owner=custody,
            version="2",
            scope=common_scope,
            runtime_form={
                "snapshot_id": snapshot_id,
                "runtime_identity": dict(runtime),
                "epoch": dict(epoch),
                "capture_health": dict(snapshot["capture_health"]),
                "summary": dict(snapshot["summary"]),
            },
            evidence=(
                {
                    "path": str(receipt_path.resolve()),
                    "sha256": receipt_sha256,
                    "snapshot_id": snapshot_id,
                    "json_pointer": "",
                    "claim": "validated-crucible-runtime-snapshot-v2",
                },
            ),
            navigation=_receipt_navigation(
                receipt_path, "", "Crucible runtime snapshot V2"
            ),
            limitations=_receipt_limitations(snapshot["limitations"]),
            search_terms=tuple(
                str(axis["value"])
                for axis in epoch["axes"]
                if isinstance(axis.get("value"), str)
            ),
        )
    ]

    for index, capability in enumerate(snapshot["capabilities"]):
        pointer = f"/capabilities/{index}"
        identities = [
            {
                "kind": "capability-name",
                "value": capability["name"],
                "basis": "profile epoch capability contract",
            },
            {
                "kind": "runtime-snapshot-id",
                "value": snapshot_id,
                "basis": "validated composite snapshot",
            },
        ]
        if capability["semantic_fingerprint"] is not None:
            identities.append(
                {
                    "kind": "semantic-fingerprint",
                    "value": capability["semantic_fingerprint"],
                    "basis": "bounded profile-adapter projection",
                }
            )
        for receipt_id in capability["receipt_ids"]:
            identities.append(
                {
                    "kind": "receipt-id",
                    "value": receipt_id,
                    "basis": "validated snapshot receipt join",
                }
            )
        state_limitations: list[str] = []
        if capability["state"] == "partial":
            state_limitations.append(
                "This capability has valid but explicitly partial runtime coverage."
            )
        elif capability["state"] == "not_observed":
            state_limitations.append(
                "The runtime epoch declares this capability, but this capture did not observe it."
            )
        elif capability["state"] == "unavailable":
            state_limitations.append(
                "The bound profile epoch adapter declares no producer for this capability."
            )
        elif capability["state"] == "failed":
            state_limitations.append(
                "The observer retained a typed failure for this capability; success is not claimed."
            )
        records.append(
            ExplorerRecord(
                record_id=content_id(
                    "workbench-runtime-capability:sha256:",
                    {"snapshot_id": snapshot_id, "capability": capability},
                ),
                source_id=source_id,
                authority="Crucible",
                state=(
                    "observed"
                    if capability["state"] in {"observed", "partial", "failed"}
                    else "unresolved"
                ),
                kind="runtime-capability",
                name=str(capability["name"]),
                identities=tuple(unique_dicts(identities)),
                owner=custody,
                version=epoch["adapter_version"],
                scope={**common_scope, "capability_state": capability["state"]},
                runtime_form={
                    "snapshot_id": snapshot_id,
                    "capability": dict(capability),
                },
                relationships=tuple(
                    {
                        "predicate": "supported-by-receipt",
                        "target": {"kind": "receipt-id", "value": receipt_id},
                        "basis": "validated V2 capability receipt join",
                    }
                    for receipt_id in capability["receipt_ids"]
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "snapshot_id": snapshot_id,
                        "json_pointer": pointer,
                        "claim": "validated-crucible-runtime-capability-v2",
                    },
                ),
                navigation=_receipt_navigation(
                    receipt_path, pointer, f"Runtime capability {capability['name']}"
                ),
                limitations=tuple(
                    dict.fromkeys(
                        [*capability["limitations"], *state_limitations]
                    )
                ),
                search_terms=tuple(
                    item
                    for item in (
                        capability["state"],
                        capability["reason_code"],
                        epoch["profile_epoch_id"],
                        epoch["adapter_id"],
                    )
                    if isinstance(item, str)
                ),
            )
        )
    return records, False


def _defining_loader_trace_records(
    receipt: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    session = receipt["session"]
    receipt_id = str(receipt["receipt_id"])
    observation = receipt["observation"]
    selection = receipt["selection"]
    records: list[ExplorerRecord] = []
    common_scope = {
        "capture_id": session["capture_id"],
        "launch_id": session["launch_id"],
        "profile": session["profile_id"],
        "physical_side": session["side"],
        "defining_loader_identity": observation["defining_loader_identity"],
    }
    observation_identities = tuple(
        identity
        for identity in (
            _receipt_identity(
                "class-name",
                observation.get("target_class"),
                "validated instrumentation target",
            ),
            _receipt_identity(
                "classloader-class",
                observation.get("defining_loader_class"),
                "observed defining loader",
            ),
            _receipt_identity(
                "classloader-identity",
                observation.get("defining_loader_identity"),
                "observed defining loader identity",
            ),
            _receipt_identity(
                "input-class-sha256",
                observation.get("target_input_sha256"),
                "observed instrumentation input",
            ),
            _receipt_identity(
                "output-class-sha256",
                observation.get("target_output_sha256"),
                "observed instrumentation output",
            ),
        )
        if identity is not None
    )
    records.append(
        ExplorerRecord(
            record_id=content_id(
                "workbench-runtime-defining-loader-observation:sha256:",
                {"receipt_id": receipt_id, "observation": observation},
            ),
            source_id=source_id,
            authority="Crucible",
            state="observed",
            kind="classloader-transformation-observation",
            name=str(observation["target_class"]),
            identities=observation_identities,
            owner={
                "state": "observed-loader",
                "actors": [
                    {
                        "kind": "classloader",
                        "id": observation["defining_loader_identity"],
                        "class": observation["defining_loader_class"],
                    }
                ],
                "basis": "validated defining-loader trace",
            },
            version=str(observation["java_version"]),
            scope=common_scope,
            runtime_form={
                "receipt_id": receipt_id,
                "observation": dict(observation),
            },
            relationships=(
                {
                    "predicate": "defined-by-loader",
                    "target": {
                        "kind": "classloader-identity",
                        "value": observation["defining_loader_identity"],
                    },
                    "basis": "validated instrumentation observation",
                },
            ),
            evidence=(
                {
                    "path": str(receipt_path.resolve()),
                    "sha256": receipt_sha256,
                    "receipt_id": receipt_id,
                    "json_pointer": "/observation",
                    "claim": "validated-defining-loader-observation",
                },
            ),
            navigation=_receipt_navigation(
                receipt_path, "/observation", "Defining-loader transformation"
            ),
            limitations=(
                "This receipt observes exact substitution of its instrumentation target; it is not a complete ordered transform chain for every loaded class.",
            ),
        )
    )

    selection_identities = tuple(
        identity
        for identity in (
            _receipt_identity(
                "class-name",
                selection.get("provider_class"),
                "observed provider selection",
            ),
            _receipt_identity(
                "classloader-class",
                selection.get("provider_loader_class"),
                "observed provider loader",
            ),
            _receipt_identity(
                "classloader-identity",
                selection.get("provider_loader_identity"),
                "observed provider loader identity",
            ),
            _receipt_identity(
                "artifact-sha256",
                selection.get("provider_artifact_sha256"),
                "exact selected provider artifact",
            ),
        )
        if identity is not None
    )
    records.append(
        ExplorerRecord(
            record_id=content_id(
                "workbench-runtime-selected-mixin-provider:sha256:",
                {"receipt_id": receipt_id, "selection": selection},
            ),
            source_id=source_id,
            authority="Crucible",
            state="observed",
            kind="mixin-service-selection",
            name=str(selection["provider_class"]),
            identities=selection_identities,
            owner={
                "state": "exact-artifact",
                "actors": [
                    {
                        "kind": "artifact",
                        "sha256": selection["provider_artifact_sha256"],
                        "code_source_uri": selection["provider_code_source_uri"],
                    }
                ],
                "basis": "validated selected-provider artifact binding",
            },
            scope={**common_scope, "attempt_id": selection["attempt_id"]},
            runtime_form={
                "receipt_id": receipt_id,
                "selection": dict(selection),
            },
            evidence=(
                {
                    "path": str(receipt_path.resolve()),
                    "sha256": receipt_sha256,
                    "receipt_id": receipt_id,
                    "json_pointer": "/selection",
                    "claim": "validated-defining-loader-provider-selection",
                },
            ),
            navigation=_receipt_navigation(
                receipt_path, "/selection", "Selected Mixin service provider"
            ),
            limitations=(
                "The observed selected provider is scoped to this exact launch, side, profile, and defining-loader trace.",
            ),
        )
    )

    truncated = False
    for attempt_index, attempt in enumerate(receipt["ordered_attempts"]):
        for event_index, event in enumerate(attempt["events"]):
            if len(records) >= MAX_EVIDENCE_RECORDS:
                truncated = True
                break
            payload = event["payload"]
            provider_class = payload.get("provider_class")
            if not isinstance(provider_class, str) or not provider_class:
                continue
            pointer = (
                f"/ordered_attempts/{attempt_index}/events/{event_index}"
            )
            identities = tuple(
                identity
                for identity in (
                    _receipt_identity(
                        "class-name",
                        provider_class,
                        "observed defining-loader attempt event",
                    ),
                    _receipt_identity(
                        "classloader-class",
                        payload.get("provider_loader_class"),
                        "observed provider loader",
                    ),
                    _receipt_identity(
                        "classloader-identity",
                        payload.get("provider_loader_identity"),
                        "observed provider loader identity",
                    ),
                    _receipt_identity(
                        "service-name",
                        payload.get("service_name"),
                        "observed service name result",
                    ),
                    _receipt_identity(
                        "class-name",
                        payload.get("service_class_name"),
                        "observed bootstrap service class result",
                    ),
                )
                if identity is not None
            )
            records.append(
                ExplorerRecord(
                    record_id=content_id(
                        "workbench-runtime-mixin-provider-attempt:sha256:",
                        {
                            "receipt_id": receipt_id,
                            "attempt_id": attempt["attempt_id"],
                            "event_sequence": event["sequence"],
                        },
                    ),
                    source_id=source_id,
                    authority="Crucible",
                    state="observed",
                    kind="mixin-provider-" + str(event["event"]).replace("_", "-"),
                    name=provider_class,
                    identities=identities,
                    owner={
                        "state": "observed-code-source",
                        "actors": [
                            {
                                "kind": "code-source",
                                "uri": payload.get("provider_code_source_uri"),
                            }
                        ],
                        "basis": "validated provider attempt event",
                    },
                    scope={
                        **common_scope,
                        "stage": attempt["stage"],
                        "attempt_id": attempt["attempt_id"],
                        "mechanism": attempt["mechanism"],
                    },
                    runtime_form={
                        "receipt_id": receipt_id,
                        "attempt_event": dict(event),
                        "terminal_outcome": attempt["terminal_outcome"],
                    },
                    evidence=(
                        {
                            "path": str(receipt_path.resolve()),
                            "sha256": receipt_sha256,
                            "receipt_id": receipt_id,
                            "json_pointer": pointer,
                            "claim": "validated-defining-loader-attempt-event",
                        },
                    ),
                    navigation=_receipt_navigation(
                        receipt_path, pointer, "Mixin provider attempt event"
                    ),
                    limitations=(
                        "A provider attempt event is distinct from the receipt's final selected-provider projection.",
                    ),
                    search_terms=(
                        str(attempt["stage"]),
                        str(attempt["mechanism"]),
                        str(attempt["terminal_outcome"]),
                    ),
                )
            )
        if truncated:
            break
    return records, truncated


def receipt_provider(path: Path) -> ProviderResult:
    raw, _ = _safe_bytes(path)
    value = _strict_json(raw, f"receipt {path}")
    if not isinstance(value, dict):
        raise ExplorerError(f"receipt root must be an object: {path}")
    format_name = value.get("format") or value.get("format_version")
    if not isinstance(format_name, str) or not format_name:
        raise ExplorerError(f"receipt has no format identity: {path}")
    if format_name == "workbench-crucible-worldgen-observatory-bundle-v1":
        raise ExplorerError(
            "workbench-crucible-worldgen-observatory-bundle-v1 is retired "
            "from Exact Runtime Explorer; use the embedded V2 graph/query presenter"
        )
    receipt_sha256 = hashlib.sha256(raw).hexdigest()
    source_id = content_id(
        "workbench-runtime-explorer-receipt-source:sha256:",
        {"file_sha256": receipt_sha256, "format": format_name},
    )
    authority = _authority_for_format(format_name)
    validated = False
    records: list[ExplorerRecord]
    truncated = False
    source_kind = "receipt"
    scope: dict[str, Any] = {
        "receipt_id": value.get("receipt_id") or value.get("ledger_id") or value.get("id"),
        "profile": value.get("profile"),
        "physical_side": value.get("physical_side"),
    }
    coverage: dict[str, Any] = {}
    source_limitations: tuple[str, ...] = ()
    try:
        if format_name == "workbench-crucible-runtime-snapshot-v2":
            from workbench_crucible_runtime_snapshot import parse_runtime_snapshot

            admitted = parse_runtime_snapshot(value)
            validated = True
            source_kind = "crucible-runtime-snapshot-v2"
            records, truncated = _runtime_snapshot_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            runtime = admitted["runtime_identity"]
            scope = {
                "snapshot_id": admitted["snapshot_id"],
                "launch_id": runtime["launch_id"],
                "profile": runtime["platform_profile_id"],
                "pack_profile_id": runtime["pack_profile_id"],
                "physical_side": runtime["physical_side"],
                "process_outcome": runtime["process_outcome"],
                "profile_epoch_id": admitted["epoch"]["profile_epoch_id"],
            }
            coverage = {
                **dict(admitted["summary"]),
                "capture_health": dict(admitted["capture_health"]),
            }
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-project-intelligence-mixin-component-topology-receipt-v1":
            from workbench_project_intelligence import validate_topology_receipt

            validate_topology_receipt(value)
            validated = True
            source_kind = "mixin-artifact-topology-receipt"
            records = _topology_records(value, source_id)
            truncated = len(records) > MAX_EVIDENCE_RECORDS
            records = records[:MAX_EVIDENCE_RECORDS]
            scope = dict(value.get("scope", {}))
            coverage = dict(value.get("summary", {}))
        elif format_name == "workbench-project-intelligence-runtime-surface-v1":
            from workbench_project_intelligence.runtime_surface import validate_runtime_surface

            validate_runtime_surface(value)
            validated = True
            source_kind = "project-intelligence-runtime-surface-receipt"
            records, truncated = _runtime_surface_receipt_records(
                value,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            scope = dict(value.get("workspace", {}))
            coverage = dict(value.get("coverage", {}))
            source_limitations = (
                "Serialized workspace paths are retained verbatim and are not treated as current source navigation.",
            )
        elif format_name == "workbench-crucible-mixin-transformation-ledger-v1":
            from workbench_crucible_mixin_custody import parse_ledger

            admitted = parse_ledger(value)
            validated = True
            source_kind = "crucible-mixin-transformation-ledger"
            records, truncated = _mixin_ledger_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "ledger_id": admitted["ledger_id"],
                "session_id": session["session_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
                "launch_state": admitted["launch_state"],
            }
            coverage = dict(admitted["summary"])
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-crucible-mixin-runtime-service-receipt-v1":
            from workbench_crucible_mixin_custody import parse_runtime_service_receipt

            admitted = parse_runtime_service_receipt(value)
            validated = True
            source_kind = "crucible-mixin-runtime-service-receipt"
            records, truncated = _mixin_runtime_service_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "receipt_id": admitted["receipt_id"],
                "session_id": session["session_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
            }
            coverage = dict(admitted["summary"])
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-crucible-mixin-defining-loader-discovery-trace-receipt-v2":
            from workbench_crucible_mixins import (
                parse_defining_loader_trace_receipt,
            )

            admitted = parse_defining_loader_trace_receipt(value)
            validated = True
            source_kind = "crucible-mixin-defining-loader-trace"
            records, truncated = _defining_loader_trace_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "receipt_id": admitted["receipt_id"],
                "capture_id": session["capture_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
            }
            coverage = {
                "health": dict(admitted["health"]),
                "summary": dict(admitted["summary"]),
                "attempts": len(admitted["ordered_attempts"]),
                "failures": len(admitted["failures"]),
            }
            source_limitations = (
                "This trace proves the exact bounded defining-loader discovery path, not every transformation in the assembled game.",
            )
        elif format_name == "workbench-crucible-mixin-selected-service-components-receipt-v1":
            from workbench_crucible_mixins import parse_service_components_receipt

            admitted = parse_service_components_receipt(value)
            validated = True
            source_kind = "crucible-mixin-selected-service-components-receipt"
            records, truncated = _mixin_service_component_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "receipt_id": admitted["receipt_id"],
                "capture_id": session["capture_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
            }
            coverage = {
                "health": dict(admitted["health"]),
                "summary": dict(admitted["summary"]),
                "components": len(admitted["components"]),
                "failures": len(admitted["failures"]),
            }
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-crucible-mixin-transformer-chain-epoch-receipt-v1":
            from workbench_crucible_mixins import parse_transformer_chain_receipt

            admitted = parse_transformer_chain_receipt(value)
            validated = True
            source_kind = "crucible-mixin-transformer-chain-epoch-receipt"
            records, truncated = _mixin_transformer_chain_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "receipt_id": admitted["receipt_id"],
                "capture_id": session["capture_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
            }
            coverage = {
                "health": dict(admitted["health"]),
                "summary": dict(admitted["summary"]),
                "epochs": len(admitted["epochs"]),
                "refreshes": len(admitted["refreshes"]),
                "failures": len(admitted["failures"]),
            }
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-crucible-mixin-final-class-definition-receipt-v1":
            from workbench_crucible_mixins import parse_final_definition_receipt

            admitted = parse_final_definition_receipt(value)
            validated = True
            source_kind = "crucible-mixin-final-class-definition-receipt"
            records, truncated = _mixin_final_definition_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "receipt_id": admitted["receipt_id"],
                "capture_id": session["capture_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
            }
            coverage = {
                "health": dict(admitted["health"]),
                "summary": dict(admitted["summary"]),
                "definitions": len(admitted["definitions"]),
                "failures": len(admitted["failures"]),
                "foundation": dict(admitted["foundation"]),
            }
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-crucible-mixin-config-lifecycle-receipt-v1":
            from workbench_crucible_mixins import parse_config_lifecycle_receipt

            admitted = parse_config_lifecycle_receipt(value)
            validated = True
            source_kind = "crucible-mixin-config-lifecycle-receipt"
            records, truncated = _mixin_config_lifecycle_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            session = admitted["session"]
            scope = {
                "receipt_id": admitted["receipt_id"],
                "capture_id": session["capture_id"],
                "launch_id": session["launch_id"],
                "profile": session["profile_id"],
                "physical_side": session["side"],
            }
            coverage = {
                "health": dict(admitted["health"]),
                "summary": dict(admitted["summary"]),
                "configurations": len(admitted["configurations"]),
                "failures": len(admitted["failures"]),
            }
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-groovy-pack-program-report-v1":
            from workbench_pack_program_studio import validate_report

            admitted = validate_report(value)
            validated = True
            source_kind = "groovy-pack-program-report"
            records, truncated = _groovy_program_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            binding = admitted["candidate"]["binding"]
            scope = {
                "report_id": admitted["report_id"],
                "program_id": admitted["candidate"]["program_id"],
                "profile": binding["pack_profile_id"],
                "platform_profile_id": binding["platform_profile_id"],
                "physical_side": binding["physical_side"],
                "packmode": binding["packmode"],
            }
            coverage = dict(admitted["candidate"]["coverage"])
            coverage["summary"] = dict(admitted["summary"])
            source_limitations = _receipt_limitations(admitted["limitations"])
        elif format_name == "workbench-groovy-language-service-result-v1":
            from workbench_pack_program_studio import validate_language_result

            admitted = validate_language_result(value)
            validated = True
            source_kind = "groovy-language-service-result"
            records, truncated = _groovy_language_service_records(
                admitted,
                source_id=source_id,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            program = admitted["program"]
            service = admitted["service"]
            scope = {
                "result_id": admitted["result_id"],
                "program_id": program["program_id"],
                "profile": program["pack_profile_id"],
                "platform_profile_id": program["platform_profile_id"],
                "physical_side": program["physical_side"],
                "packmode": program["packmode"],
                "runtime_id": admitted["runtime"]["runtime_id"],
                "compiler_phase": admitted["profile"]["server_semantics"][
                    "compilation_phase"
                ],
            }
            coverage = dict(admitted["summary"])
            coverage["service_state"] = service["state"]
            coverage["transcript_messages"] = service["transcript"][
                "message_count"
            ]
            source_limitations = tuple(
                dict.fromkeys(
                    [
                        *_receipt_limitations(admitted["limitations"]),
                        "Compiler file checks and diagnostics remain verbatim evidence; they are not promoted to observed runtime state.",
                    ]
                )
            )
        else:
            records, truncated = _generic_receipt_records(
                value,
                source_id=source_id,
                authority=authority,
                receipt_path=path,
                receipt_sha256=receipt_sha256,
            )
            source_limitations = (
                "Unknown receipt families are searched as bounded verbatim identity fields and are not semantically reinterpreted.",
            )
    except (ValueError, TypeError, KeyError) as exc:
        raise ExplorerError(
            f"{format_name} semantic validation failed: {exc}"
        ) from exc
    coverage = {**coverage, "records": len(records), "truncated": truncated, "schema_validated": validated}
    partial_semantic_source = any(
        [
        (
            format_name == "workbench-crucible-runtime-snapshot-v2"
            and (
                value.get("capture_health", {}).get("observer_state") != "healthy"
                or any(
                    row.get("state") in {"partial", "not_observed", "failed"}
                    for row in value.get("capabilities", ())
                    if isinstance(row, Mapping)
                )
            )
        ),
        (
            format_name == "workbench-project-intelligence-runtime-surface-v1"
            and value.get("coverage", {}).get("complete") is not True
        ),
        (
            format_name == "workbench-crucible-mixin-transformation-ledger-v1"
            and (
                value.get("launch_state") != "complete"
                or value.get("summary", {}).get("evidence_state")
                in {"incomplete", "conflicted"}
            )
        ),
        (
            format_name
            == "workbench-crucible-mixin-defining-loader-discovery-trace-receipt-v2"
            and (
                value.get("health", {}).get("end_health") != "healthy"
                or bool(value.get("failures"))
            )
        ),
        (
            format_name
            == "workbench-crucible-mixin-selected-service-components-receipt-v1"
            and (
                value.get("health", {}).get("end_health") != "healthy"
                or bool(value.get("failures"))
                or value.get("summary", {}).get("all_required_components_observed")
                is not True
            )
        ),
        (
            format_name
            == "workbench-crucible-mixin-transformer-chain-epoch-receipt-v1"
            and (
                value.get("health", {}).get("state") != "complete"
                or bool(value.get("failures"))
            )
        ),
        (
            format_name
            == "workbench-crucible-mixin-final-class-definition-receipt-v1"
            and (
                value.get("health", {}).get("state") != "complete"
                or bool(value.get("failures"))
            )
        ),
        (
            format_name
            == "workbench-crucible-mixin-config-lifecycle-receipt-v1"
            and (
                value.get("health", {}).get("state") != "complete"
                or bool(value.get("failures"))
            )
        ),
        (
            format_name == "workbench-groovy-language-service-result-v1"
            and (
                value.get("service", {}).get("state") != "completed"
                or value.get("summary", {}).get("compiler_state")
                in {"blocked", "inconclusive"}
            )
        ),
        ]
    )
    source = ExplorerSource(
        source_id=source_id,
        source_kind=source_kind,
        authority=authority,
        state=(
            "partial"
            if truncated or partial_semantic_source
            else "complete"
            if validated
            else "unvalidated"
        ),
        identity={"path": str(path.resolve()), "sha256": receipt_sha256, "format": format_name},
        scope=scope,
        coverage=coverage,
        limitations=source_limitations,
    )
    return ProviderResult(source, tuple(records))


def _groovy_program_records(
    report: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    """Project validated static Groovy effects without upgrading their state."""

    candidate = report["candidate"]
    binding = candidate["binding"]
    owner = {
        "state": "declared-profile",
        "actors": [
            {
                "kind": "pack-profile",
                "id": binding["pack_profile_id"],
                "profile_id": binding["profile_id"],
            }
        ],
        "basis": "validated Pack Program Studio profile binding",
    }
    scope = {
        "profile": binding["pack_profile_id"],
        "platform_profile_id": binding["platform_profile_id"],
        "physical_side": binding["physical_side"],
        "program_id": candidate["program_id"],
        "packmode": binding["packmode"],
    }
    records: list[ExplorerRecord] = []
    truncated = False
    for index, effect in enumerate(candidate["effects"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        source = effect["source"]
        pointer = f"/candidate/effects/{index}"
        identities = [
            {
                "kind": "groovy-effect-id",
                "value": str(effect["effect_id"]),
                "basis": "validated Pack Program Studio effect identity",
            },
            {
                "kind": "groovy-semantic-effect",
                "value": str(effect["semantic_key"]),
                "basis": "validated normalized static call identity",
            },
        ]
        identities.extend(_groovy_effect_identities(effect))
        name = _groovy_effect_name(effect)
        limitations = tuple(
            dict.fromkeys(
                [
                    *effect.get("limitations", []),
                    "Serialized source navigation is retained from the report and is not revalidated against the current workspace.",
                ]
            )
        )
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(source_id, effect["effect_id"]),
                source_id=source_id,
                authority="Workbench Pack Program Studio",
                state="static-possible",
                kind=str(effect["kind"]),
                name=name,
                identities=tuple(identities),
                owner=owner,
                scope={
                    **scope,
                    "stage": effect["lifecycle"]["stage"],
                    "execution_state": effect["lifecycle"]["execution_state"],
                    "reload_state": effect["reload"]["state"],
                },
                declaration={
                    "path": source["absolute_path"],
                    "recorded_relative_path": source["path"],
                    "line": source["line"],
                    "column": source["column"],
                    "file_sha256": source["sha256"],
                    "language": "groovy",
                },
                runtime_form={
                    "state": "not-observed",
                    "static_effect": dict(effect),
                },
                relationships=(
                    {
                        "predicate": "classified-at-stage",
                        "target": {
                            "kind": "groovy-stage",
                            "value": effect["lifecycle"]["stage"],
                        },
                        "basis": "validated runConfig loader mapping",
                    },
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "json_pointer": pointer,
                        "claim": "validated-static-groovy-candidate",
                    },
                ),
                navigation=_receipt_navigation(receipt_path, pointer, name),
                limitations=limitations,
                search_terms=tuple(
                    str(value)[:8192]
                    for value in (
                        effect["rule_id"],
                        effect["category"],
                        effect["operation"],
                        effect["expression"],
                        source["path"],
                        source["snippet"],
                    )
                    if isinstance(value, str) and value
                ),
            )
        )
    if not truncated:
        for index, collision in enumerate(candidate["collisions"]):
            if len(records) >= MAX_EVIDENCE_RECORDS:
                truncated = True
                break
            pointer = f"/candidate/collisions/{index}"
            rendered_value = str(collision["value"])
            records.append(
                ExplorerRecord(
                    record_id=_source_facet_id(source_id, collision["collision_id"]),
                    source_id=source_id,
                    authority="Workbench Pack Program Studio",
                    state="static-possible",
                    kind="identity-collision-candidate",
                    name=f"{collision['identity_kind']}={rendered_value}",
                    identities=(
                        {
                            "kind": "groovy-collision-candidate-id",
                            "value": collision["collision_id"],
                            "basis": "validated Pack Program Studio collision identity",
                        },
                        {
                            "kind": str(collision["identity_kind"]),
                            "value": rendered_value,
                            "basis": "duplicate profile-classified static literal",
                        },
                    ),
                    owner=owner,
                    scope={**scope, "execution_state": collision["execution_state"]},
                    runtime_form={"state": "not-observed", "static_collision": dict(collision)},
                    relationships=tuple(
                        {
                            "predicate": "references-effect",
                            "target": {"kind": "groovy-effect-id", "value": effect_id},
                            "basis": "validated collision occurrence",
                        }
                        for effect_id in collision["effect_ids"]
                    ),
                    evidence=(
                        {
                            "path": str(receipt_path.resolve()),
                            "sha256": receipt_sha256,
                            "json_pointer": pointer,
                            "claim": "validated-static-collision-candidate",
                        },
                    ),
                    navigation=_receipt_navigation(
                        receipt_path,
                        pointer,
                        f"Groovy collision candidate {rendered_value}",
                    ),
                    limitations=tuple(collision["limitations"]),
                    search_terms=(collision["policy_id"], collision["identity_kind"]),
                )
            )
    return records, truncated


def _groovy_language_service_records(
    result: Mapping[str, Any],
    *,
    source_id: str,
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[list[ExplorerRecord], bool]:
    """Project exact compiler evidence without implying script execution."""

    program = result["program"]
    profile = result["profile"]
    runtime = result["runtime"]
    selected_by_path = {
        str(row["path"]): row for row in program["selected_files"]
    }
    owner = {
        "state": "declared-profile",
        "actors": [
            {
                "kind": "pack-profile",
                "id": program["pack_profile_id"],
            },
            {
                "kind": "platform-profile",
                "id": program["platform_profile_id"],
            },
            {
                "kind": "language-service-profile",
                "id": profile["language_service_profile_id"],
            },
        ],
        "basis": "validated Pack Program Studio language-service bindings",
    }
    base_scope = {
        "profile": program["pack_profile_id"],
        "platform_profile_id": program["platform_profile_id"],
        "physical_side": program["physical_side"],
        "program_id": program["program_id"],
        "runtime_id": runtime["runtime_id"],
        "packmode": program["packmode"],
        "compiler_phase": profile["server_semantics"]["compilation_phase"],
    }
    common_limitations = (
        "This is canonicalization evidence for exact in-memory source bytes, not script execution or effective registry state.",
        "The language-server endpoint cannot authenticate itself as the inventoried local runtime.",
        "Serialized source paths are retained as declarations and are not treated as current workspace navigation.",
    )
    records: list[ExplorerRecord] = []
    truncated = False
    for file_index, checked in enumerate(result["service"]["files"]):
        if len(records) >= MAX_EVIDENCE_RECORDS:
            truncated = True
            break
        selected = selected_by_path[str(checked["path"])]
        file_pointer = f"/service/files/{file_index}"
        file_name = _bounded_receipt_text(
            f"Groovy compiler check: {checked['path']}"
        )
        check_without_diagnostics = {
            key: value for key, value in checked.items() if key != "diagnostics"
        }
        check_without_diagnostics["diagnostic_ids"] = [
            diagnostic["diagnostic_id"] for diagnostic in checked["diagnostics"]
        ]
        file_limitations = list(common_limitations)
        if checked["state"] == "inconclusive":
            file_limitations.append(
                "The compiler synchronization request was inconclusive for this file."
            )
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(
                    source_id,
                    f"compiler-file:{checked['path']}:{checked['sha256']}",
                ),
                source_id=source_id,
                authority="Workbench Pack Program Studio",
                state="verbatim-evidence",
                kind="groovy-compiler-file-check",
                name=file_name,
                identities=(
                    {
                        "kind": "groovy-source-path",
                        "value": str(checked["path"]),
                        "basis": "validated selected-file binding",
                    },
                    {
                        "kind": "groovy-source-version",
                        "value": f"{checked['path']}@sha256:{checked['sha256']}",
                        "basis": "path and exact source bytes sent to the language service",
                    },
                ),
                owner=owner,
                scope={
                    **base_scope,
                    "stage": checked["stage"],
                    "execution_state": checked["execution_state"],
                    "compiler_state": checked["state"],
                },
                declaration={
                    "path": selected["absolute_path"],
                    "recorded_relative_path": checked["path"],
                    "line": 1,
                    "column": 1,
                    "file_sha256": checked["sha256"],
                    "language": "groovy",
                },
                runtime_form={
                    "state": "not-observed",
                    "compiler_observation": check_without_diagnostics,
                },
                relationships=(
                    {
                        "predicate": "checked-at-compiler-phase",
                        "target": {
                            "kind": "groovy-compiler-phase",
                            "value": base_scope["compiler_phase"],
                        },
                        "basis": "validated language-service profile",
                    },
                ),
                evidence=(
                    {
                        "path": str(receipt_path.resolve()),
                        "sha256": receipt_sha256,
                        "json_pointer": file_pointer,
                        "claim": "validated-exact-groovy-compiler-check",
                    },
                ),
                navigation=_receipt_navigation(receipt_path, file_pointer, file_name),
                limitations=tuple(file_limitations),
                search_terms=tuple(
                    _bounded_receipt_text(str(value))
                    for value in (
                        checked["path"],
                        checked["server_uri"],
                        checked["stage"],
                        checked["execution_state"],
                        checked["state"],
                    )
                    if value is not None
                ),
            )
        )

        for diagnostic_index, diagnostic in enumerate(checked["diagnostics"]):
            if len(records) >= MAX_EVIDENCE_RECORDS:
                truncated = True
                break
            diagnostic_pointer = (
                f"{file_pointer}/diagnostics/{diagnostic_index}"
            )
            start = diagnostic["range"]["start"]
            line = int(start["line"]) + 1
            column = int(start["character"]) + 1
            severity = _groovy_severity(diagnostic["severity"])
            message = _bounded_receipt_text(str(diagnostic["message"]))
            diagnostic_name = _bounded_receipt_text(
                f"{severity} {checked['path']}:{line}:{column}: {message}"
            )
            diagnostic_limitations = list(common_limitations)
            if diagnostic["severity"] is None:
                diagnostic_limitations.append(
                    "The upstream diagnostic did not declare a severity."
                )
            records.append(
                ExplorerRecord(
                    record_id=_source_facet_id(
                        source_id, diagnostic["diagnostic_id"]
                    ),
                    source_id=source_id,
                    authority="Workbench Pack Program Studio",
                    state="verbatim-evidence",
                    kind="groovy-compiler-diagnostic",
                    name=diagnostic_name,
                    identities=(
                        {
                            "kind": "groovy-diagnostic-id",
                            "value": str(diagnostic["diagnostic_id"]),
                            "basis": "source-bound Pack Program Studio diagnostic identity",
                        },
                    ),
                    owner=owner,
                    scope={
                        **base_scope,
                        "stage": checked["stage"],
                        "execution_state": checked["execution_state"],
                        "severity": severity,
                    },
                    declaration={
                        "path": selected["absolute_path"],
                        "recorded_relative_path": checked["path"],
                        "line": line,
                        "column": column,
                        "file_sha256": checked["sha256"],
                        "language": "groovy",
                    },
                    runtime_form={
                        "state": "not-observed",
                        "compiler_phase": base_scope["compiler_phase"],
                        "compiler_diagnostic": dict(diagnostic),
                    },
                    relationships=(
                        {
                            "predicate": "reported-for-source",
                            "target": {
                                "kind": "groovy-source-path",
                                "value": str(checked["path"]),
                            },
                            "basis": "validated diagnostic/file binding",
                        },
                    ),
                    evidence=(
                        {
                            "path": str(receipt_path.resolve()),
                            "sha256": receipt_sha256,
                            "json_pointer": diagnostic_pointer,
                            "claim": "validated-source-bound-groovy-diagnostic",
                        },
                    ),
                    navigation=_receipt_navigation(
                        receipt_path, diagnostic_pointer, diagnostic_name
                    ),
                    limitations=tuple(diagnostic_limitations),
                    search_terms=tuple(
                        _bounded_receipt_text(str(value))
                        for value in (
                            message,
                            diagnostic["source"],
                            diagnostic["code"],
                            checked["path"],
                            severity,
                        )
                        if value is not None and str(value)
                    ),
                    rank_hint=20 if diagnostic["severity"] == 1 else 10,
                )
            )
        if truncated:
            break
    return records, truncated


def _bounded_receipt_text(value: str) -> str:
    rendered = " ".join(value.replace("\x00", "\\x00").splitlines()).strip()
    return (rendered or "unavailable")[:8192]


def _groovy_severity(value: object) -> str:
    return {
        1: "error",
        2: "warning",
        3: "information",
        4: "hint",
        None: "unspecified",
    }.get(value, "unspecified")


def _groovy_effect_identities(effect: Mapping[str, Any]) -> list[dict[str, str]]:
    identity_kinds = {
        ("material", "registry_name"): "material-id",
        ("material", "numeric_id"): "material-numeric-id",
        ("metaitem", "registry_name"): "item-id",
        ("metaitem", "numeric_id"): "metaitem-numeric-id",
        ("item", "registry_name"): "item-id",
        ("fluid", "registry_name"): "fluid-id",
        ("ore-dictionary", "ore_name"): "ore-prefix",
        ("recipe-map", "registry_name"): "recipe-map-id",
        ("crafting-recipe", "recipe_id"): "recipe-id",
        ("machine-recipe", "recipe_map"): "recipe-map-id",
    }
    result: list[dict[str, str]] = []
    for field, value in effect["identity"].items():
        kind = identity_kinds.get((effect["kind"], field), f"groovy-{field.replace('_', '-')}")
        result.append(
            {
                "kind": kind,
                "value": str(value),
                "basis": f"profile-classified static Groovy {field}",
            }
        )
    if effect["kind"] == "machine-recipe" and effect.get("recipe", {}).get("recipe_map"):
        value = str(effect["recipe"]["recipe_map"])
        if not any(row["kind"] == "recipe-map-id" and row["value"] == value for row in result):
            result.append(
                {
                    "kind": "recipe-map-id",
                    "value": value,
                    "basis": "static recipeBuilder qualifier",
                }
            )
    return result


def _groovy_effect_name(effect: Mapping[str, Any]) -> str:
    identity = effect.get("identity")
    if isinstance(identity, Mapping) and identity:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(identity.items()))
        return f"{effect['kind']} {rendered}"
    recipe = effect.get("recipe")
    if isinstance(recipe, Mapping) and recipe.get("recipe_map"):
        return f"{recipe['recipe_map']} recipe at {effect['source']['path']}:{effect['source']['line']}"
    return f"{effect['kind']} at {effect['source']['path']}:{effect['source']['line']}"


def _event_identities(event: Mapping[str, Any]) -> list[dict[str, str]]:
    identities = [
        {"kind": "event-id", "value": str(event.get("event_id", "event")), "basis": "console event"}
    ]
    message = str(event.get("message", ""))
    for minecraft_id in _MINECRAFT_ID_RE.findall(message):
        if minecraft_id.split(":", 1)[0] in {"http", "https", "file"}:
            continue
        identities.append({"kind": "resource-location", "value": minecraft_id, "basis": "console message literal"})
    for match in _STACK_FRAME_RE.finditer(message):
        identities.extend(
            [
                {"kind": "class-name", "value": match.group("class"), "basis": "stack frame"},
                {"kind": "method-name", "value": match.group("method"), "basis": "stack frame"},
                {"kind": "source-member", "value": f"{match.group('class')}#{match.group('method')}", "basis": "stack frame"},
            ]
        )
    for match in _COORDINATE_RE.finditer(message):
        coordinate = ",".join(match.group(name) for name in ("x", "y", "z"))
        identities.append({"kind": "coordinate", "value": coordinate, "basis": "console message"})
        if match.group("dimension") is not None:
            identities.append({"kind": "dimension-id", "value": match.group("dimension"), "basis": "console message"})
    for locator in event.get("source_locators", []):
        if isinstance(locator, dict) and isinstance(locator.get("path"), str):
            identities.append({"kind": "file-path", "value": locator["path"], "basis": "console source candidate"})
    return list(unique_dicts(identities))


def _validate_console_timestamp(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value.endswith("Z")
        or len(value) > 128
        or any(character in value for character in "\r\n\x00")
    ):
        raise ExplorerError(f"{label} is not a bounded UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ExplorerError(f"{label} is malformed: {exc}") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ExplorerError(f"{label} is not UTC")


def _validated_console_event(
    value: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    from workbench_api.events import ConsoleEvent, RawLocator, SourceLocator

    expected = {
        "format_version",
        "event_id",
        "sequence",
        "ingested_at",
        "monotonic_ns",
        "source_timestamp",
        "source",
        "stream",
        "raw_locator",
        "kind",
        "severity",
        "subsystem",
        "logger",
        "thread",
        "message",
        "parse_provenance",
        "classification_basis",
        "cluster_key",
        "signal",
        "outcome_failure",
        "source_locators",
        "limitations",
    }
    if set(value) != expected:
        raise ExplorerError(f"{label} fields are unexpected or missing")
    if not isinstance(value.get("event_id"), str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,511}",
        str(value.get("event_id", "")),
    ) is None:
        raise ExplorerError(f"{label} event ID is malformed")
    _validate_console_timestamp(value.get("ingested_at"), f"{label} ingested_at")
    for key in ("sequence", "monotonic_ns"):
        number = value.get(key)
        minimum = 1 if key == "sequence" else 0
        if isinstance(number, bool) or not isinstance(number, int) or number < minimum:
            raise ExplorerError(f"{label} {key} is malformed")
    for key in ("source_timestamp", "logger", "thread"):
        item = value.get(key)
        if item is not None and (
            not isinstance(item, str)
            or not item
            or len(item) > 8192
            or any(character in item for character in "\r\n\x00")
        ):
            raise ExplorerError(f"{label} {key} is malformed")
    message = value.get("message")
    if (
        not isinstance(message, str)
        or len(message) > 1024 * 1024
        or any(character in message for character in "\r\n\x00")
    ):
        raise ExplorerError(f"{label} message is malformed")
    if not isinstance(value.get("cluster_key"), str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(value.get("cluster_key", ""))
    ) is None:
        raise ExplorerError(f"{label} cluster key is malformed")

    raw = value.get("raw_locator")
    if not isinstance(raw, dict) or set(raw) != {
        "artifact",
        "byte_start",
        "byte_end",
        "line",
        "chunk",
        "boundary",
    }:
        raise ExplorerError(f"{label} raw locator is malformed")
    for key in ("byte_start", "byte_end", "line", "chunk"):
        if isinstance(raw.get(key), bool):
            raise ExplorerError(f"{label} raw locator {key} is malformed")
    locator_rows = value.get("source_locators")
    if not isinstance(locator_rows, list) or len(locator_rows) > 32:
        raise ExplorerError(f"{label} source locators are malformed")
    source_locators: list[SourceLocator] = []
    for locator in locator_rows:
        if not isinstance(locator, dict) or set(locator) != {
            "path",
            "line",
            "column",
            "label",
        }:
            raise ExplorerError(f"{label} source locator is malformed")
        if isinstance(locator.get("line"), bool) or isinstance(
            locator.get("column"), bool
        ):
            raise ExplorerError(f"{label} source locator position is malformed")
        try:
            source_locators.append(SourceLocator(**locator))
        except (TypeError, ValueError) as exc:
            raise ExplorerError(f"{label} source locator is invalid: {exc}") from exc

    classification = value.get("classification_basis")
    limitations = value.get("limitations")
    for rows, maximum, row_label, pattern in (
        (
            classification,
            32,
            "classification basis",
            r"[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,255}",
        ),
        (limitations, 256, "limitations", r"[^\r\n\x00]{1,8192}"),
    ):
        if (
            not isinstance(rows, list)
            or len(rows) > maximum
            or any(not isinstance(row, str) or re.fullmatch(pattern, row) is None for row in rows)
            or len(rows) != len(set(rows))
        ):
            raise ExplorerError(f"{label} {row_label} are malformed")
    try:
        normalized = dict(value)
        normalized["raw_locator"] = RawLocator(**raw)
        normalized["source_locators"] = tuple(source_locators)
        normalized["classification_basis"] = tuple(classification)
        normalized["limitations"] = tuple(limitations)
        event = ConsoleEvent(**normalized)
    except (TypeError, ValueError) as exc:
        raise ExplorerError(f"{label} is invalid: {exc}") from exc
    if event.as_dict() != dict(value):
        raise ExplorerError(f"{label} is not a canonical V1 event")
    return event.as_dict()


def _validate_console_summary(
    manifest: Mapping[str, Any],
    events: list[Mapping[str, Any]],
) -> None:
    if manifest.get("state") == "running":
        return
    summary = manifest.get("summary")
    if not isinstance(summary, Mapping):
        raise ExplorerError("console session summary is missing")
    expected = {
        "event_count": len(events),
        "severity_counts": dict(
            sorted(Counter(str(row["severity"]) for row in events).items())
        ),
        "subsystem_counts": dict(
            sorted(Counter(str(row["subsystem"]) for row in events).items())
        ),
        "kind_counts": dict(
            sorted(Counter(str(row["kind"]) for row in events).items())
        ),
        "outcome_failure_events": sum(
            int(bool(row["outcome_failure"])) for row in events
        ),
        "source_locator_count": sum(
            len(row["source_locators"]) for row in events
        ),
    }
    for key, expected_value in expected.items():
        if summary.get(key) != expected_value:
            raise ExplorerError(
                f"console session summary {key} disagrees with retained events"
            )


def _event_records(
    events: Iterable[Mapping[str, Any]],
    *,
    source_id: str,
    evidence_path: Path,
    evidence_sha256: str,
) -> list[ExplorerRecord]:
    records: list[ExplorerRecord] = []
    for ordinal, event in enumerate(events, 1):
        message = str(event.get("message", ""))
        if not message:
            message = "[empty console event]"
        message = message.replace("\r", " ").replace("\n", " ")[:8192]
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            event_id = content_id(
                "workbench-runtime-log-event:sha256:",
                {"source_id": source_id, "ordinal": ordinal, "event": event},
            )
        locators = event.get("source_locators", [])
        navigation = tuple(
            {
                "kind": "source-candidate",
                "path": locator["path"],
                "line": locator["line"],
                "column": locator.get("column"),
                "label": locator.get("label") or "console source candidate",
                "resolution": "unresolved-output-candidate",
            }
            for locator in locators
            if isinstance(locator, dict)
            and isinstance(locator.get("path"), str)
            and isinstance(locator.get("line"), int)
        )
        records.append(
            ExplorerRecord(
                record_id=_source_facet_id(source_id, event_id),
                source_id=source_id,
                authority="Workbench Shell",
                state="presentation-observation",
                kind=str(event.get("kind", "event")),
                name=message,
                identities=tuple(_event_identities({**event, "event_id": event_id})),
                owner=unresolved_owner("console output does not establish runtime actor ownership"),
                scope={"source": event.get("source"), "stream": event.get("stream"), "subsystem": event.get("subsystem")},
                runtime_form=dict(event),
                evidence=({"path": str(evidence_path), "sha256": evidence_sha256, "sequence": event.get("sequence") or ordinal, "claim": "console-presentation"},),
                navigation=navigation,
                limitations=("Console classification and source candidates are presentation metadata, not Atlas causal findings.",),
                search_terms=tuple(
                    str(value)
                    for value in (event.get("subsystem"), event.get("logger"), event.get("thread"), event.get("severity"))
                    if isinstance(value, str) and value
                ),
            )
        )
    return records


def console_session_provider(path: Path) -> ProviderResult:
    session_directory: Path | None = None
    events_path = path
    manifest: dict[str, Any] | None = None
    manifest_sha256: str | None = None
    if path.is_dir() and not path.is_symlink():
        from workbench_api.sessions import (
            SessionError,
            validate_session_manifest,
        )

        session_directory = path.resolve()
        events_path = session_directory / "events-v1.jsonl"
        manifest_path = session_directory / "session-v1.json"
        manifest_raw, _ = _safe_bytes(manifest_path, maximum=4 * 1024 * 1024)
        loaded_manifest = _strict_json(manifest_raw, f"console manifest {manifest_path}")
        try:
            loaded_manifest = validate_session_manifest(
                loaded_manifest,
                physical_directory=session_directory,
            )
        except SessionError as exc:
            raise ExplorerError(f"console session manifest is invalid: {exc}") from exc
        retention = loaded_manifest.get("retention")
        if (
            not isinstance(retention, dict)
            or retention.get("events") != "events-v1.jsonl"
            or retention.get("raw_is_source_record") is not True
            or retention.get("events_are_projection") is not True
        ):
            raise ExplorerError(
                "console session does not bind the canonical event projection"
            )
        manifest_limitations = loaded_manifest.get("limitations", [])
        if (
            not isinstance(manifest_limitations, list)
            or len(manifest_limitations) > 256
            or any(
                not isinstance(value, str)
                or not value
                or len(value) > 8192
                or any(character in value for character in "\r\n\x00")
                for value in manifest_limitations
            )
            or len(manifest_limitations) != len(set(manifest_limitations))
        ):
            raise ExplorerError("console session limitations are malformed")
        manifest = loaded_manifest
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    raw, _ = _safe_bytes(events_path)
    events: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    previous_sequence = 0
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line:
            raise ExplorerError(f"console event stream contains an empty line: {events_path}:{line_number}")
        value = _strict_json(line, f"console event {events_path}:{line_number}")
        if not isinstance(value, dict):
            raise ExplorerError(
                f"console event is not an object: {events_path}:{line_number}"
            )
        event = _validated_console_event(
            value,
            label=f"console event {events_path}:{line_number}",
        )
        sequence = int(event["sequence"])
        event_id = str(event["event_id"])
        if sequence <= previous_sequence:
            raise ExplorerError("console event sequence is not strictly increasing")
        if event_id in event_ids:
            raise ExplorerError("console event stream repeats an event ID")
        previous_sequence = sequence
        event_ids.add(event_id)
        events.append(event)
        if len(events) > MAX_EVIDENCE_RECORDS:
            raise ExplorerError(f"console event stream exceeds {MAX_EVIDENCE_RECORDS} events")
    if manifest is not None:
        _validate_console_summary(manifest, events)
    digest = hashlib.sha256(raw).hexdigest()
    source_id = content_id(
        "workbench-runtime-explorer-console-source:sha256:",
        {
            "events_sha256": digest,
            "manifest_sha256": manifest_sha256,
            "session_id": None if manifest is None else manifest.get("session_id"),
        },
    )
    session_state = None if manifest is None else manifest.get("state")
    source_limitations = [
        "Console events are a searchable projection of retained output, not runtime causality authority."
    ]
    if manifest is not None:
        source_limitations.extend(manifest.get("limitations", []))
        if session_state in {"running", "incomplete"}:
            source_limitations.append(
                "The retained console session is still running or incomplete; event coverage is partial."
            )
    source = ExplorerSource(
        source_id=source_id,
        source_kind="live-console-session",
        authority="Workbench Shell",
        state=(
            "partial"
            if session_state in {"running", "incomplete"}
            else "complete"
        ),
        identity={
            "path": str(events_path.resolve()),
            "sha256": digest,
            "manifest_sha256": manifest_sha256,
            "session_id": None if manifest is None else manifest.get("session_id"),
        },
        scope={
            "command": None if manifest is None else manifest.get("command"),
            "session_state": session_state,
        },
        coverage={
            "events": len(events),
            "manifest_summary": None
            if manifest is None
            else manifest.get("summary"),
        },
        limitations=tuple(dict.fromkeys(source_limitations)),
    )
    return ProviderResult(source, tuple(_event_records(events, source_id=source_id, evidence_path=events_path, evidence_sha256=digest)))


def raw_log_provider(path: Path, *, workspace: Path | None = None) -> ProviderResult:
    from workbench_api.events import EventNormalizer, IncrementalEventDecoder

    raw, _ = _safe_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    source_id = content_id(
        "workbench-runtime-explorer-log-source:sha256:",
        {"path": str(path.resolve()), "sha256": digest},
    )
    from workbench_api.profile_extensions import event_classifiers

    normalizer = EventNormalizer(root=workspace, classifiers=event_classifiers())
    decoder = IncrementalEventDecoder(
        normalizer,
        source="runtime-explorer",
        stream="log",
        raw_path=path,
    )
    events = [event.as_dict() for event in decoder.feed(raw)]
    events.extend(event.as_dict() for event in decoder.finish())
    stable_events: list[dict[str, Any]] = []
    for ordinal, event in enumerate(events, 1):
        stable = {
            key: value
            for key, value in event.items()
            if key not in {"event_id", "ingested_at", "monotonic_ns"}
        }
        stable["event_id"] = content_id(
            "workbench-runtime-log-event:sha256:",
            {"source_id": source_id, "ordinal": ordinal, "event": stable},
        )
        stable_events.append(stable)
    source = ExplorerSource(
        source_id=source_id,
        source_kind="raw-log",
        authority="Workbench Shell",
        state="complete",
        identity={"path": str(path.resolve()), "sha256": digest},
        coverage={"bytes": len(raw), "events": len(stable_events)},
        limitations=("Raw-log classification is presentation metadata and does not establish effective process outcome or causality.",),
    )
    return ProviderResult(source, tuple(_event_records(stable_events, source_id=source_id, evidence_path=path, evidence_sha256=digest)))
