"""Searchable recipe-health views over categorical graphs or source checkouts.

This module deliberately keeps runtime graph claims and source-only observations
separate.  A categorical V2 graph can support exact recipe signature, flow, and
mutation-attribution answers.  A checkout without such a graph can only support
source occurrence answers; its reports state the missing runtime evidence rather
than treating source text as a loaded recipe registry.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote, unquote

from workbench_atlas_categorical_graph import (
    CategoricalGraphQuery,
    inspect_query_index,
    validate_bundle_directory,
)
from workbench_atlas_categorical_graph.bundle import BUNDLE_FORMAT


CONTEXT_FORMAT = "workbench-atlas-recipe-health-context-v1"
OPERATIONAL_CONTEXT_FORMAT = "workbench-atlas-recipe-health-operational-context-v1"
SEARCH_FORMAT = "workbench-atlas-recipe-health-search-v1"
REPORT_FORMAT = "workbench-atlas-recipe-health-report-v1"

_RECIPE_KIND = "gt-recipe"
_ITEM_INPUT_RELATIONS = frozenset(
    {"accepts-gt-item-alternative", "accepts-ore-dictionary-class"}
)
_FLUID_INPUT_RELATIONS = frozenset({"accepts-gt-fluid-input"})
_INPUT_RELATIONS = _ITEM_INPUT_RELATIONS | _FLUID_INPUT_RELATIONS
_REPRESENTATIVE_RELATIONS = frozenset({
    "observes-gt-item-input-representative", "observes-gt-fluid-input-representative",
})
_OUTPUT_RELATIONS = frozenset({"produces-gt-item", "produces-gt-fluid"})
_SELECTOR_RELATIONS = frozenset(
    {"has-item-input-selector", "has-fluid-input-selector"}
)
_SOURCE_RELATIONS = frozenset(
    {
        "causally-attributed-to-groovy-source",
        "invoked-through-source-callsite",
    }
)
_SOURCE_SUFFIXES = frozenset({".groovy", ".zs"})
_IGNORED_SOURCE_PARTS = frozenset(
    {".git", ".workbench", ".gradle", "build", "out", "run"}
)
_SOURCE_SEARCH_ROOTS = (
    PurePosixPath("groovy"),
    PurePosixPath("scripts"),
    PurePosixPath("src/main/resources"),
)
_MAX_SOURCE_BYTES = 4 * 1024 * 1024


class RecipeHealthError(ValueError):
    """A recipe-health context or exact selection is invalid."""


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip() or any(c in value for c in "\r\n\x00"):
        raise RecipeHealthError(f"{label} must be nonempty single-line text")
    return value.strip()


def _limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 10_000:
        raise RecipeHealthError("recipe-health limit is outside 1..10000")
    return value


def _bundle_root(path: Path) -> Path:
    return path.parent if path.name == "manifest.json" else path


def _manifest_format(path: Path) -> str | None:
    root = _bundle_root(path)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value.get("format") if type(value) is dict else None


def _relation_counts(manifest: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for partition in manifest["partitions"]:
        for relation, count in partition["edges"]["relations"].items():
            counts[relation] = counts.get(relation, 0) + count
    return dict(sorted(counts.items()))


def _kind_counts(manifest: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for partition in manifest["partitions"]:
        for kind, count in partition["nodes"]["kinds"].items():
            counts[kind] = counts.get(kind, 0) + count
    return dict(sorted(counts.items()))


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": node["id"],
        "kind": node["kind"],
        "semantic_key": node["semantic_key"],
        "properties": node["properties"],
        "evidence": node["evidence"],
    }


def _edge_summary(edge: dict[str, Any], node: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "relation": edge["relation"],
        "semantic_key": edge["semantic_key"],
        "properties": edge["properties"],
        "evidence": edge["evidence"],
        "node": None if node is None else _node_summary(node),
    }


def _deduplicate_summaries(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {node["selection_id"]: node for node in nodes}
    return [by_id[node_id] for node_id in sorted(by_id)]


class GraphRecipeHealthView:
    """Recipe-health view over one explicit, verified categorical V2 bundle."""

    def __init__(self, path: Path, *, rebuild_if_missing: bool = False, check_cancelled=None) -> None:
        self.root = _bundle_root(Path(path)).resolve()
        self.query = CategoricalGraphQuery(
            self.root, rebuild_if_missing=rebuild_if_missing, check_cancelled=check_cancelled
        )
        self.manifest = self.query.manifest
        self.kinds = _kind_counts(self.manifest)
        self.relations = _relation_counts(self.manifest)

    def close(self) -> None:
        self.__dict__.pop("_complete_impact_index", None)
        self.query.close()

    def __enter__(self) -> "GraphRecipeHealthView":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def describe(self) -> dict[str, Any]:
        return {
            "format": CONTEXT_FORMAT,
            "schema_version": 1,
            "context_type": "categorical-graph-v2",
            "root": str(self.root),
            "graph_set_id": self.manifest["graph_set_id"],
            "scope": self.manifest["scope"],
            "summary": self.manifest["summary"],
            "capabilities": {
                "recipe_search": self.kinds.get(_RECIPE_KIND, 0) > 0,
                "duplicate_signatures": self.kinds.get(_RECIPE_KIND, 0) > 0,
                "producers": any(self.relations.get(row, 0) for row in _OUTPUT_RELATIONS),
                "consumers": any(self.relations.get(row, 0) for row in _INPUT_RELATIONS),
                "source_mutations": self.relations.get(
                    "identifies-surviving-recipe-by-object-identity", 0
                )
                > 0,
                "stoichiometry": False,
                "reachability": False,
            },
            "recipe_count": self.kinds.get(_RECIPE_KIND, 0),
            "search": {
                "requires_semantic_key": False,
                "selection_is_exact_node_id": True,
            },
        }

    @staticmethod
    def _like(value: str) -> str:
        return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    def search(
        self,
        text: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Find selectable nodes without requiring a known semantic key."""

        text = _text(text, "recipe-health search text")
        limit = _limit(limit)
        parameters: list[object] = []
        filters: list[str] = []
        if kinds is not None:
            if type(kinds) not in (list, tuple) or not kinds:
                raise RecipeHealthError("recipe-health search kinds must be a nonempty array")
            kind_rows = [_text(kind, "recipe-health search kind") for kind in kinds]
            filters.append("kind IN (" + ",".join("?" for _ in kind_rows) + ")")
            parameters.extend(kind_rows)
        needle = self._like(text.casefold())
        filters.append(
            "(lower(semantic_key) LIKE ? ESCAPE '\\' "
            "OR lower(kind) LIKE ? ESCAPE '\\' "
            "OR lower(properties_json) LIKE ? ESCAPE '\\')"
        )
        parameters.extend((needle, needle, needle))
        parameters.extend((text.casefold(), text.casefold() + "%", limit + 1))
        sql = (
            "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes WHERE "
            + " AND ".join(filters)
            + " ORDER BY CASE WHEN lower(semantic_key)=? THEN 0 "
            "WHEN lower(semantic_key) LIKE ? THEN 1 ELSE 2 END,kind,semantic_key,id LIMIT ?"
        )
        rows = list(self.query.connection.execute(sql, parameters))
        truncated = len(rows) > limit
        nodes = [self.query._node_row(row) for row in rows[:limit]]
        return {
            "format": SEARCH_FORMAT,
            "schema_version": 1,
            "context": self.describe(),
            "query": text,
            "results": [_node_summary(node) for node in nodes],
            "truncated": truncated,
        }

    def _node(self, node_id: str) -> dict[str, Any] | None:
        row = self.query.connection.execute(
            "SELECT id,kind,semantic_key,properties_json,evidence_json "
            "FROM nodes WHERE id=?",
            (node_id,),
        ).fetchone()
        return None if row is None else self.query._node_row(row)

    def _edges_from(
        self, node_id: str, relations: frozenset[str] | None = None
    ) -> list[dict[str, Any]]:
        if relations is None:
            rows = self.query.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
                "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
                "JOIN nodes t ON t.node_key=e.target_node "
                "WHERE s.id=? ORDER BY e.relation,t.id,e.edge_key",
                (node_id,),
            )
        else:
            ordered = sorted(relations)
            rows = self.query.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
                "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
                "JOIN nodes t ON t.node_key=e.target_node "
                "WHERE s.id=? AND e.relation IN ("
                + ",".join("?" for _ in ordered)
                + ") ORDER BY e.relation,t.id,e.edge_key",
                [node_id, *ordered],
            )
        return [self.query._edge_row(row) for row in rows]

    def _edges_to(
        self, node_id: str, relations: frozenset[str] | None = None
    ) -> list[dict[str, Any]]:
        if relations is None:
            rows = self.query.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
                "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
                "JOIN nodes t ON t.node_key=e.target_node "
                "WHERE t.id=? ORDER BY e.relation,s.id,e.edge_key",
                (node_id,),
            )
        else:
            ordered = sorted(relations)
            rows = self.query.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
                "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
                "JOIN nodes t ON t.node_key=e.target_node "
                "WHERE t.id=? AND e.relation IN ("
                + ",".join("?" for _ in ordered)
                + ") ORDER BY e.relation,s.id,e.edge_key",
                [node_id, *ordered],
            )
        return [self.query._edge_row(row) for row in rows]

    def _duplicate_signature(self, recipe: dict[str, Any]) -> dict[str, Any]:
        properties = recipe["properties"]
        recipe_map = properties.get("recipe_map")
        signature = properties.get("semantic_sha256")
        if type(recipe_map) is not str or type(signature) is not str:
            return {
                "status": "unavailable",
                "exact_match_count": None,
                "recipes": [],
                "limitation": "recipe map or semantic SHA-256 is absent",
            }
        rows = self.query.connection.execute(
            "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
            "WHERE kind=? AND json_extract(properties_json,'$.recipe_map')=? "
            "AND json_extract(properties_json,'$.semantic_sha256')=? "
            "ORDER BY semantic_key,id",
            (_RECIPE_KIND, recipe_map, signature),
        )
        matches = [self.query._node_row(row) for row in rows]
        return {
            "status": "collision" if len(matches) > 1 else "unique",
            "recipe_map": recipe_map,
            "semantic_sha256": signature,
            "exact_match_count": len(matches),
            "recipes": [_node_summary(node) for node in matches],
        }

    def _recipe_io(self, recipe: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        inputs: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
        for edge in self._edges_from(recipe["id"]):
            if edge["relation"] in _OUTPUT_RELATIONS:
                outputs.append(_edge_summary(edge, self._node(edge["target"])))
            elif edge["relation"] in _SELECTOR_RELATIONS:
                selector = self._node(edge["target"])
                for accepted in self._edges_from(edge["target"], _INPUT_RELATIONS | _REPRESENTATIVE_RELATIONS):
                    row = _edge_summary(accepted, self._node(accepted["target"]))
                    row["selector"] = None if selector is None else _node_summary(selector)
                    row["selector_edge"] = {
                        "relation": edge["relation"],
                        "properties": edge["properties"],
                        "evidence": edge["evidence"],
                    }
                    inputs.append(row)
        return inputs, outputs

    def _source_mutations(self, recipe: dict[str, Any]) -> dict[str, Any]:
        origin_classes: list[dict[str, Any]] = []
        for edge in self._edges_from(
            recipe["id"], frozenset({"has-recipe-origin-class"})
        ):
            target = self._node(edge["target"])
            if target is not None:
                origin_classes.append(_node_summary(target))

        event_rows: dict[str, dict[str, Any]] = {}
        for identity_edge in self._edges_to(
            recipe["id"],
            frozenset({"identifies-surviving-recipe-by-object-identity"}),
        ):
            subject = self._node(identity_edge["source"])
            for subject_edge in self._edges_to(
                identity_edge["source"], frozenset({"has-mutation-subject"})
            ):
                event = self._node(subject_edge["source"])
                if event is None:
                    continue
                sources: list[dict[str, Any]] = []
                for source_edge in self._edges_from(event["id"], _SOURCE_RELATIONS):
                    source = self._node(source_edge["target"])
                    sources.append(_edge_summary(source_edge, source))
                event_rows[event["id"]] = {
                    "event": _node_summary(event),
                    "subject": None if subject is None else _node_summary(subject),
                    "identity_evidence": identity_edge["evidence"],
                    "sources": sources,
                }

        mutations = [event_rows[event_id] for event_id in sorted(event_rows)]
        source_paths: set[str] = set()
        for row in mutations:
            path = row["event"]["properties"].get("execution_source_path")
            if type(path) is str:
                source_paths.add(path)
            for source in row["sources"]:
                if source["node"] is None:
                    continue
                source_path = source["node"]["properties"].get("source_path")
                if type(source_path) is str:
                    source_paths.add(source_path)
        if source_paths:
            status = "observed-source-mutation"
        elif mutations:
            status = "observed-mutation-source-unresolved"
        elif origin_classes:
            status = "runtime-origin-class-only"
        else:
            status = "unavailable"
        return {
            "status": status,
            "source_paths": sorted(source_paths),
            "origin_classes": origin_classes,
            "mutations": mutations,
        }

    def _producers_consumers(self, target: dict[str, Any]) -> dict[str, Any]:
        producer_edges = self._edges_to(target["id"], _OUTPUT_RELATIONS)
        producers = [
            {
                "recipe": _node_summary(recipe),
                "output": _edge_summary(edge, target),
            }
            for edge in producer_edges
            if (recipe := self._node(edge["source"])) is not None
        ]

        consumers: list[dict[str, Any]] = []
        for accepted in self._edges_to(target["id"], _INPUT_RELATIONS):
            selector = self._node(accepted["source"])
            for selector_edge in self._edges_to(
                accepted["source"], _SELECTOR_RELATIONS
            ):
                recipe = self._node(selector_edge["source"])
                if recipe is None:
                    continue
                consumers.append(
                    {
                        "recipe": _node_summary(recipe),
                        "selector": None if selector is None else _node_summary(selector),
                        "acceptance": _edge_summary(accepted, target),
                    }
                )
        producers.sort(key=lambda row: row["recipe"]["selection_id"])
        consumers.sort(key=lambda row: row["recipe"]["selection_id"])
        return {
            "producer_status": (
                "observed" if any(self.relations.get(row, 0) for row in _OUTPUT_RELATIONS) else "evidence-unavailable"
            ),
            "consumer_status": (
                "observed" if any(self.relations.get(row, 0) for row in _INPUT_RELATIONS) else "evidence-unavailable"
            ),
            "producers": producers,
            "consumers": consumers,
        }

    def _evidence_gaps(self, *, role: str, ownership_status: str | None = None) -> list[dict[str, str]]:
        gaps = [
            {
                "code": "stoichiometry-not-assessed",
                "message": "This view has no admitted stoichiometric interpretation of recipe quantities.",
            },
            {
                "code": "reachability-not-assessed",
                "message": "Producer and consumer edges do not establish progression reachability.",
            },
        ]
        if self.kinds.get(_RECIPE_KIND, 0) == 0:
            gaps.append(
                {
                    "code": "runtime-recipes-unavailable",
                    "message": "The graph has no finite GT recipe nodes.",
                }
            )
        if role == "recipe" and ownership_status in {
            "runtime-origin-class-only",
            "unavailable",
        }:
            gaps.append(
                {
                    "code": "source-mutation-evidence-unavailable",
                    "message": "No retained mutation event identifies this surviving recipe and source.",
                }
            )
        if not any(self.relations.get(row, 0) for row in _OUTPUT_RELATIONS):
            gaps.append(
                {
                    "code": "producer-evidence-unavailable",
                    "message": "The graph publishes no supported recipe-output relation.",
                }
            )
        if not any(self.relations.get(row, 0) for row in _INPUT_RELATIONS):
            gaps.append(
                {
                    "code": "consumer-evidence-unavailable",
                    "message": "The graph publishes no supported recipe-input acceptance relation.",
                }
            )
        limitations = list(dict.fromkeys(
            limitation for partition in self.manifest["partitions"]
            for limitation in partition["limitations"]
        ))
        if limitations:
            gaps.append({"code": "graph-projection-limitations", "message": " ".join(limitations)})
        return gaps

    def inspect(self, selection_id: str) -> dict[str, Any]:
        """Inspect one exact node ID returned by :meth:`search`."""

        selection_id = _text(selection_id, "recipe-health selection ID")
        node = self._node(selection_id)
        if node is None:
            raise RecipeHealthError("recipe-health selection does not exist in this graph")
        if node["kind"] == _RECIPE_KIND:
            inputs, outputs = self._recipe_io(node)
            ownership = self._source_mutations(node)
            return {
                "format": REPORT_FORMAT,
                "schema_version": 1,
                "context": self.describe(),
                "role": "recipe",
                "selection": _node_summary(node),
                "duplicate_signature": self._duplicate_signature(node),
                "ownership": ownership,
                "inputs": inputs,
                "outputs": outputs,
                "evidence_gaps": self._evidence_gaps(
                    role="recipe", ownership_status=ownership["status"]
                ),
            }
        flow = self._producers_consumers(node)
        return {
            "format": REPORT_FORMAT,
            "schema_version": 1,
            "context": self.describe(),
            "role": "target",
            "selection": _node_summary(node),
            "flow": flow,
            "related_recipes": _deduplicate_summaries(
                [row["recipe"] for row in flow["producers"] + flow["consumers"]]
            ),
            "evidence_gaps": self._evidence_gaps(role="target"),
        }

    def impact(
        self,
        selection_id: str,
        *,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Project bounded dependency exposure for one observed finite recipe."""

        from .impact import build_recipe_impact

        return build_recipe_impact(
            self,
            selection_id,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    def complete_impact(
        self,
        selection_id: str,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Exhaust the declared finite dependencies, retaining evidence gaps."""

        from .complete_impact import derive_complete_recipe_impact

        return derive_complete_recipe_impact(
            self, selection_id, check_cancelled=check_cancelled
        )


def _source_roots(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for candidate in _SOURCE_SEARCH_ROOTS:
        path = root.joinpath(*candidate.parts)
        try:
            state = path.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(state.st_mode) and not stat.S_ISLNK(state.st_mode):
            candidates.append(path)
    return tuple(candidates) or (root,)


def _source_files(root: Path) -> tuple[Path, ...]:
    files: set[Path] = set()
    for search_root in _source_roots(root):
        for path in search_root.rglob("*"):
            try:
                relative = path.relative_to(root)
                path.resolve(strict=True).relative_to(root)
            except ValueError:
                continue
            except OSError:
                continue
            current = root
            traverses_link = False
            for part in relative.parts[:-1]:
                current = current / part
                try:
                    if stat.S_ISLNK(current.lstat().st_mode):
                        traverses_link = True
                        break
                except OSError:
                    traverses_link = True
                    break
            if (
                path.is_file()
                and not path.is_symlink()
                and not traverses_link
                and path.suffix.casefold() in _SOURCE_SUFFIXES
                and not any(part in _IGNORED_SOURCE_PARTS for part in relative.parts)
            ):
                files.add(path)
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def _source_selection_id(location: dict[str, Any]) -> str:
    return f"source-text:{location['sha256']}:{quote(location['path'], safe='')}:{location['byte_start']}:{location['byte_end']}"


class SourceRecipeHealthView:
    """Conservative source-occurrence view for a checkout without a V2 graph."""

    def __init__(self, path: Path) -> None:
        self.root = Path(path).resolve()
        if not self.root.is_dir():
            raise RecipeHealthError("source-only recipe-health context must be a directory")
        self.files = _source_files(self.root)
        if not self.files:
            raise RecipeHealthError("no Groovy or ZenScript sources were found")

    def close(self) -> None:
        return None

    def __enter__(self) -> "SourceRecipeHealthView":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def describe(self) -> dict[str, Any]:
        return {
            "format": CONTEXT_FORMAT,
            "schema_version": 1,
            "context_type": "source-only-checkout",
            "root": str(self.root),
            "source_file_count": len(self.files),
            "source_roots": [
                path.relative_to(self.root).as_posix() or "."
                for path in _source_roots(self.root)
            ],
            "capabilities": {
                "source_occurrence_search": True,
                "recipe_search": False,
                "duplicate_signatures": False,
                "producers": False,
                "consumers": False,
                "source_mutations": False,
                "stoichiometry": False,
                "reachability": False,
            },
            "search": {
                "requires_semantic_key": False,
                "selection_is_exact_source_occurrence": True,
            },
        }

    @staticmethod
    def _gaps(skipped_files: Sequence[str] = ()) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = [
            {
                "code": "runtime-recipes-unavailable",
                "message": "Source text does not establish the loaded recipe registry.",
            },
            {
                "code": "duplicate-signatures-unavailable",
                "message": "Exact runtime recipe signatures and collisions require a categorical graph.",
            },
            {
                "code": "producer-consumer-evidence-unavailable",
                "message": "Source occurrences do not establish exact runtime producers or consumers.",
            },
            {
                "code": "source-mutation-evidence-unavailable",
                "message": "A source line is not retained runtime mutation attribution.",
            },
            {
                "code": "stoichiometry-not-assessed",
                "message": "This view does not interpret source quantities stoichiometrically.",
            },
            {
                "code": "reachability-not-assessed",
                "message": "This view does not establish progression reachability.",
            },
        ]
        if skipped_files:
            gaps.append(
                {
                    "code": "source-files-skipped",
                    "message": "Some source files were unreadable or exceeded the source-view size limit.",
                    "paths": list(skipped_files),
                }
            )
        return gaps

    def search(self, text: str, *, limit: int = 50, **_: object) -> dict[str, Any]:
        from workbench_api.source_locations import character_location

        text = _text(text, "recipe-health search text")
        limit = _limit(limit)
        needle = text.casefold()
        results: list[dict[str, Any]] = []
        skipped: list[str] = []
        truncated = False
        for path in self.files:
            relative = path.relative_to(self.root).as_posix()
            try:
                if path.stat().st_size > _MAX_SOURCE_BYTES:
                    skipped.append(relative)
                    continue
                raw = path.read_bytes()
                lines = raw.decode("utf-8").splitlines(keepends=True)
            except (OSError, UnicodeError):
                skipped.append(relative)
                continue
            character_offset = 0
            for line_number, line in enumerate(lines, 1):
                start = 0
                folded = line.casefold()
                offsets = [index for index, character in enumerate(line) for _ in character.casefold()]
                while (column := folded.find(needle, start)) >= 0:
                    if len(results) >= limit:
                        truncated = True
                        break
                    begin = offsets[column]
                    end = offsets[column + len(needle) - 1] + 1
                    location = character_location(raw, relative, character_offset + begin, character_offset + end)
                    results.append(
                        {
                            "selection_id": _source_selection_id(location),
                            "kind": "source-occurrence",
                            "source_path": relative,
                            "line": line_number,
                            "column": location["start"]["column"],
                            "snippet": line.strip(),
                        }
                    )
                    start = column + max(1, len(needle))
                if truncated:
                    break
                character_offset += len(line)
            if truncated:
                break
        return {
            "format": SEARCH_FORMAT,
            "schema_version": 1,
            "context": self.describe(),
            "query": text,
            "results": results,
            "truncated": truncated,
            "evidence_gaps": self._gaps(skipped),
        }

    def _parse_selection(self, selection_id: str):
        from workbench_api.source_locations import source_location

        selection_id = _text(selection_id, "recipe-health selection ID")
        prefix = "source-text:"
        if not selection_id.startswith(prefix):
            raise RecipeHealthError("source-only selection ID has the wrong format")
        try:
            digest, encoded, start_text, end_text = selection_id[len(prefix) :].split(":")
            start, end = int(start_text), int(end_text)
        except (ValueError, TypeError) as exc:
            raise RecipeHealthError("source-only selection ID has the wrong format") from exc
        relative_text = unquote(encoded)
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts or "\\" in relative_text or start < 0 or end <= start:
            raise RecipeHealthError("source-only selection ID is unsafe")
        path = self.root.joinpath(*relative.parts)
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise RecipeHealthError("source-only selection escapes its checkout") from exc
        if path not in self.files or path not in _source_files(self.root):
            raise RecipeHealthError("source-only selection is not an indexed source file")
        if path.stat().st_size > _MAX_SOURCE_BYTES:
            raise RecipeHealthError("selected source exceeds its byte bound")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise RecipeHealthError("selected source occurrence is stale; search again")
        return path, raw, source_location(raw, relative_text, start, end)

    def inspect(self, selection_id: str) -> dict[str, Any]:
        path, raw, location = self._parse_selection(selection_id)
        line, column = location["start"]["line"], location["start"]["column"]
        relative = path.relative_to(self.root).as_posix()
        try:
            lines = raw.decode("utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise RecipeHealthError("selected source occurrence cannot be read") from exc
        if line > len(lines):
            raise RecipeHealthError("selected source occurrence is stale")
        return {
            "format": REPORT_FORMAT,
            "schema_version": 1,
            "context": self.describe(),
            "role": "source-occurrence",
            "location": location,
            "selection": {
                "selection_id": selection_id,
                "kind": "source-occurrence",
                "source_path": relative,
                "line": line,
                "column": column,
                "snippet": lines[line - 1].strip(),
            },
            "ownership": {
                "status": "source-location-only",
                "source_paths": [relative],
                "mutations": [],
            },
            "evidence_gaps": self._gaps(),
        }

    def impact(
        self,
        selection_id: str,
        *,
        max_depth: int = 4,
        max_nodes: int = 500,
    ) -> dict[str, Any]:
        """Reject source-only counterfactuals that lack an observed recipe graph."""

        del selection_id, max_depth, max_nodes
        raise RecipeHealthError(
            "recipe impact requires one exact observed categorical graph recipe"
        )

    def complete_impact(
        self,
        selection_id: str,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Source text cannot supply complete finite runtime dependencies."""

        if check_cancelled is not None:
            check_cancelled()
        raise RecipeHealthError(
            "complete recipe impact requires one exact observed categorical graph recipe"
        )


def open_recipe_health(
    path: Path, *, rebuild_if_missing: bool = False,
    check_cancelled: Callable[[], None] | None = None,
) -> GraphRecipeHealthView | SourceRecipeHealthView:
    """Open an explicit categorical bundle or a conservative source checkout."""

    if check_cancelled is not None:
        check_cancelled()
    path = Path(path)
    if not path.exists():
        raise RecipeHealthError("recipe-health context does not exist")
    if _manifest_format(path) == BUNDLE_FORMAT:
        return GraphRecipeHealthView(path, rebuild_if_missing=rebuild_if_missing,
                                     check_cancelled=check_cancelled)
    if path.is_file():
        raise RecipeHealthError("recipe-health context file is not a categorical manifest")
    return SourceRecipeHealthView(path)


def discover_recipe_health_context(path: Path) -> dict[str, Any]:
    """Describe a usable context without requiring any semantic node key."""

    path = Path(path)
    if _manifest_format(path) == BUNDLE_FORMAT:
        root = _bundle_root(path).resolve()
        manifest = validate_bundle_directory(root)
        kinds = _kind_counts(manifest)
        relations = _relation_counts(manifest)
        return {
            "format": CONTEXT_FORMAT,
            "schema_version": 1,
            "context_type": "categorical-graph-v2",
            "root": str(root),
            "graph_set_id": manifest["graph_set_id"],
            "scope": manifest["scope"],
            "summary": manifest["summary"],
            "recipe_count": kinds.get(_RECIPE_KIND, 0),
            "capabilities": {
                "recipe_search": kinds.get(_RECIPE_KIND, 0) > 0,
                "duplicate_signatures": kinds.get(_RECIPE_KIND, 0) > 0,
                "producers": any(relations.get(row, 0) for row in _OUTPUT_RELATIONS),
                "consumers": any(relations.get(row, 0) for row in _INPUT_RELATIONS),
                "source_mutations": relations.get(
                    "identifies-surviving-recipe-by-object-identity", 0
                )
                > 0,
                "stoichiometry": False,
                "reachability": False,
            },
            "search": {
                "requires_semantic_key": False,
                "selection_is_exact_node_id": True,
            },
        }
    if path.is_file():
        raise RecipeHealthError("recipe-health context file is not a categorical manifest")
    with SourceRecipeHealthView(path) as view:
        return view.describe()


def discover_recipe_health_operational_context(path: Path) -> dict[str, Any]:
    """Describe evidence separately from the commands executable right now.

    The V1 evidence context embedded in search/inspection reports is preserved
    verbatim.  This additive operational context prevents a graph with missing,
    stale, or corrupt derived storage from advertising executable search merely
    because authoritative recipe nodes exist.
    """

    path = Path(path)
    if _manifest_format(path) != BUNDLE_FORMAT:
        return discover_recipe_health_context(path)

    inspection = inspect_query_index(_bundle_root(path))
    root = Path(inspection["root"])
    manifest = inspection["manifest"]
    index = inspection["query_index"]
    kinds = _kind_counts(manifest)
    relations = _relation_counts(manifest)
    has_recipes = kinds.get(_RECIPE_KIND, 0) > 0
    evidence_capabilities = {
        "recipe_search": has_recipes,
        "duplicate_signatures": has_recipes,
        "producers": any(relations.get(row, 0) for row in _OUTPUT_RELATIONS),
        "consumers": any(relations.get(row, 0) for row in _INPUT_RELATIONS),
        "source_mutations": relations.get(
            "identifies-surviving-recipe-by-object-identity", 0
        )
        > 0,
        "stoichiometry": False,
        "reachability": False,
    }
    usable = index["usable"] is True
    capabilities = {
        key: (value and usable)
        if key not in {"stoichiometry", "reachability"}
        else False
        for key, value in evidence_capabilities.items()
    }
    repair = None
    if index["repair_supported"] is True:
        repair = {
            "action": "index",
            "argv": ["workbench", "atlas", "recipes", "index", str(root)],
            "authority": "derived-index-storage-only",
            "authoritative_graph_evidence_mutated": False,
        }
    return {
        "format": OPERATIONAL_CONTEXT_FORMAT,
        "schema_version": 1,
        "context_type": "categorical-graph-v2",
        "root": str(root),
        "graph_set_id": manifest["graph_set_id"],
        "scope": manifest["scope"],
        "summary": manifest["summary"],
        "recipe_count": kinds.get(_RECIPE_KIND, 0),
        "capabilities": capabilities,
        "evidence_capabilities": evidence_capabilities,
        "query_index": index,
        "repair": repair,
        "search": {
            "executable": usable,
            "requires_semantic_key": False,
            "selection_is_exact_node_id": True,
            "unavailable_reason_code": (
                None if usable else index["reason_code"]
            ),
        },
    }
