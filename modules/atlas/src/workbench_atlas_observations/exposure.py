"""Exact crafting definitions that retain a selected native object reference.

This is structural exposure in one saved crafting catalog. It neither evaluates
ingredients nor predicts how changing an object would affect recipe execution.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any
from weakref import WeakKeyDictionary

from workbench_atlas_categorical_graph.bundle import OBSERVATION_BUNDLE_FORMAT

from .projection import ObservationProjectionError, resolve_json_pointer
from .view import ObservationError, ObservationView


CRAFTING_REFERENCE_EXPOSURE_FORMAT = "workbench-atlas-crafting-reference-exposure-v1"
_VALUE_KIND = "initialization-crafting-native-value"
_RECIPE_KIND = "initialization-crafting-recipe"
_CATALOG_KIND = "initialization-crafting-registry"
_REFERENCE = "references-native-value"
_NODE_COLUMNS = "s.id,s.kind,s.semantic_key,s.properties_json,s.evidence_json"
_EDGE_COLUMNS = "e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json"
_VERIFIED_CATALOGS = WeakKeyDictionary()
_CLAIM = ("Named crafting definitions with recorded reference paths to this exact stored object. "
          "This does not establish ingredient acceptance, registration causation, recipe dependency, "
          "the result of an edit, or gameplay viability.")


def _bound(value: Any, name: str) -> int | None:
    if value is not None and (type(value) is not int or value < 1):
        raise ObservationError(f"{name} must be a positive integer or null")
    return value


def _binding(node: dict[str, Any]) -> tuple[str, str]:
    references = node["evidence"]
    if not references:
        raise ObservationError("crafting exposure requires original snapshot references")
    bindings = set()
    for reference in references:
        snapshot, side = reference.get("snapshot_id"), reference.get("side")
        if (type(snapshot) is not str or not snapshot or type(side) is not str
                or side not in {"single", "baseline", "candidate"}
                or reference.get("section") != "report" or reference.get("record_key") != "value"
                or reference.get("json_pointer") != node["properties"].get("json_pointer")):
            raise ObservationError("crafting observation has an unsupported original reference binding")
        bindings.add((snapshot, side))
    if len(bindings) != 1:
        raise ObservationError("crafting observation spans more than one snapshot or side")
    return next(iter(bindings))


def _catalog(view: ObservationView, selected: dict[str, Any]) -> dict[str, Any]:
    rows = view._rows(
        f"SELECT {_NODE_COLUMNS} FROM edges e JOIN nodes s ON s.node_key=e.source_node "
        "JOIN nodes t ON t.node_key=e.target_node WHERE t.id=? AND e.relation='has-native-value'",
        [selected["id"]],
    )
    if len(rows) != 1:
        raise ObservationError("selected native value does not have one unambiguous crafting catalog")
    catalog = view.query._node_row(rows[0])
    raw = catalog["properties"].get("raw_value")
    if (catalog["kind"] != _CATALOG_KIND or type(raw) is not dict
            or raw.get("schema") != "axiom.native-stored-crafting-recipes.v1"):
        raise ObservationError("crafting exposure requires a supported original crafting catalog")
    binding = _binding(catalog)
    if (binding != _binding(selected)
            or binding != (view.manifest["evidence_binding"].get("snapshot_id"),
                           view.manifest["scope"].get("selected_side"))):
        raise ObservationError("crafting catalog, selection and graph bindings differ")
    _member_scope(selected, catalog)
    return catalog


def _member_scope(node: dict[str, Any], catalog: dict[str, Any]) -> None:
    field = "nativeValues" if node["kind"] == _VALUE_KIND else "entries"
    prefix = catalog["properties"].get("json_pointer")
    pointer = node["properties"].get("json_pointer")
    key = node["properties"].get("record_key")
    if (type(prefix) is not str or type(pointer) is not str
            or type(key) is not str
            or pointer != prefix + "/" + field + "/" + key.replace("~", "~0").replace("/", "~1")
            or _binding(node) != _binding(catalog)):
        raise ObservationError("crafting reference leaves its original catalog")


def _check_reference(view, edge, source, target):
    """Confirm the relationship against its retained original source value."""
    pointer = edge["semantic_key"]
    source_pointer = source["properties"]["json_pointer"]
    if pointer != source_pointer and not pointer.startswith(source_pointer + "/"):
        raise ObservationError("crafting reference path leaves its retained source value")
    try:
        raw = resolve_json_pointer(source["properties"].get("raw_value"), pointer[len(source_pointer):])
    except ObservationProjectionError as exc:
        raise ObservationError("crafting reference path does not resolve in its retained source value") from exc
    if raw != {"nativeValueRef": target["properties"]["record_key"]}:
        raise ObservationError("crafting reference target differs from its retained source value")
    if not edge["evidence"]:
        raise ObservationError("crafting reference has no original evidence binding")
    binding = _binding(source)
    for reference in edge["evidence"]:
        view._cancel()
        if ((reference.get("snapshot_id"), reference.get("side")) != binding
                or reference.get("section") != "report" or reference.get("record_key") != "value"
                or reference.get("json_pointer") != pointer
                or reference.get("observation_kind") != "observed-storage"):
            raise ObservationError("crafting reference has an inconsistent original evidence binding")


def _incoming(view: ObservationView, target: dict[str, Any], catalog: dict[str, Any]):
    # Ownership joins prevent a shared-looking local ID or another catalog from
    # silently becoming an exposure path. Do not traverse generic parent edges.
    rows = view._rows(
        f"SELECT {_EDGE_COLUMNS},{_NODE_COLUMNS},"
        "(SELECT count(*) FROM edges m JOIN nodes c ON c.node_key=m.source_node "
        " WHERE c.id=? AND m.target_node=s.node_key AND m.relation="
        " CASE s.kind WHEN 'initialization-crafting-native-value' THEN 'has-native-value' "
        " WHEN 'initialization-crafting-recipe' THEN 'has-stored-crafting-recipe' ELSE '' END),"
        "(SELECT count(*) FROM edges m WHERE m.target_node=s.node_key "
        " AND m.relation IN ('has-native-value','has-stored-crafting-recipe')) "
        "FROM edges e JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node "
        "WHERE t.id=? AND e.relation=? ORDER BY s.id,e.semantic_key,e.edge_key",
        [catalog["id"], target["id"], _REFERENCE],
    )
    for row in rows:
        view._cancel()
        node = view.query._node_row(row[6:11])
        if node["kind"] not in {_VALUE_KIND, _RECIPE_KIND} or row[11:] != (1, 1):
            raise ObservationError("crafting reference has an unsupported or cross-catalog owner")
        _member_scope(node, catalog)
        edge = view.query._edge_row(row[:6])
        _check_reference(view, edge, node, target)
        yield edge, node


def _raw_references(raw, pointer, check_cancelled):
    pending = [(raw, pointer)]
    while pending:
        check_cancelled()
        value, path = pending.pop()
        if type(value) is dict:
            if "nativeValueRef" in value:
                if set(value) != {"nativeValueRef"} or type(value["nativeValueRef"]) is not str:
                    raise ObservationError("malformed retained native value reference")
                yield value["nativeValueRef"], path
            else:
                pending.extend((child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
                               for key, child in value.items())
        elif type(value) is list:
            pending.extend((child, path + "/" + str(i)) for i, child in enumerate(value))


def _collection_count(catalog, field):
    properties = catalog["properties"]
    collections = properties.get("projected_collections", {})
    fields = properties.get("projected_fields", [])
    if type(collections) is not dict or type(fields) is not list:
        raise ObservationError("crafting catalog has invalid projected collections")
    descriptor = collections.get(field)
    if descriptor is not None:
        if (type(descriptor) is not dict or set(descriptor) != {"type", "count"}
                or descriptor["type"] != "mapping" or type(descriptor["count"]) is not int
                or descriptor["count"] < 1 or field in properties["raw_value"]
                or field not in fields):
            raise ObservationError("crafting catalog has an invalid original collection descriptor")
        return descriptor["count"]
    if properties["raw_value"].get(field) != {}:
        raise ObservationError("crafting catalog lacks original collection membership counts")
    return 0


def _verify_catalog(view, catalog):
    """Compare the whole retained catalog with its projection, including omissions.

    Keyset batches bound temporary SQL allocations, never the analysis inventory.
    A successful result is reusable only in this guarded, unchanged open view.
    """
    view.check_current()
    cached = _VERIFIED_CATALOGS.get(view, {}).get(catalog["id"])
    if cached is not None:
        return deepcopy(cached)
    prefix = catalog["properties"]["json_pointer"]
    binding = _binding(catalog)
    counts = {_VALUE_KIND: _collection_count(catalog, "nativeValues"),
              _RECIPE_KIND: _collection_count(catalog, "entries")}
    members, local_keys, expected, gaps = {}, set(), set(), []
    actual_counts = {_VALUE_KIND: 0, _RECIPE_KIND: 0}
    last = 0
    while True:
        rows = view._rows(
            f"SELECT s.node_key,{_NODE_COLUMNS},"
            "(SELECT count(*) FROM edges m JOIN nodes c ON c.node_key=m.source_node "
            " WHERE c.id=? AND m.target_node=s.node_key AND m.relation="
            " CASE s.kind WHEN 'initialization-crafting-native-value' THEN 'has-native-value' "
            " WHEN 'initialization-crafting-recipe' THEN 'has-stored-crafting-recipe' ELSE '' END),"
            "(SELECT count(*) FROM edges m WHERE m.target_node=s.node_key "
            " AND m.relation IN ('has-native-value','has-stored-crafting-recipe')),"
            "(SELECT count(*) FROM edges m JOIN nodes c ON c.node_key=m.source_node "
            " WHERE c.id=? AND m.target_node=s.node_key "
            " AND m.relation IN ('has-native-value','has-stored-crafting-recipe')) "
            "FROM nodes s WHERE s.node_key>? ORDER BY s.node_key LIMIT 2048", [catalog["id"], catalog["id"], last])
        if not rows:
            break
        for row in rows:
            view._cancel()
            last = row[0]
            node = view.query._node_row(row[1:6])
            pointer = node["properties"].get("json_pointer")
            at_scope = (type(pointer) is str and
                        (pointer.startswith(prefix + "/nativeValues/") or pointer.startswith(prefix + "/entries/")))
            if not at_scope and row[8] == 0:
                continue
            if node["kind"] not in counts or row[6:] != (1, 1, 1):
                raise ObservationError("crafting catalog member has missing, duplicate or cross-catalog ownership")
            _member_scope(node, catalog)
            key = node["properties"]["record_key"]
            if (node["kind"], key) in local_keys:
                raise ObservationError("crafting catalog repeats an original member identity")
            local_keys.add((node["kind"], key))
            members[node["id"]] = (key, pointer, node["kind"])
            actual_counts[node["kind"]] += 1
            raw = node["properties"].get("raw_value")
            expected.update((node["id"], target, path) for target, path in _raw_references(raw, pointer, view._cancel))
            unknowns = list(_incomplete_values(raw, view._cancel))
            if unknowns or (node["kind"] == _RECIPE_KIND
                           and (type(raw) is not dict or raw.get("storedValuesComplete") is not True
                                or raw.get("affectingGaps") != [])):
                gaps.append({"code": "incomplete-stored-observation", "observation_id": node["id"],
                             "unknown_values": unknowns, "original_gaps": raw.get("affectingGaps") if type(raw) is dict else None})
    if actual_counts != counts:
        raise ObservationError("crafting catalog membership differs from its retained collection counts")
    last = 0
    while True:
        rows = view._rows(
            f"SELECT e.edge_key,{_EDGE_COLUMNS} FROM edges e "
            "JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node "
            "WHERE e.edge_key>? AND e.relation=? ORDER BY e.edge_key LIMIT 2048", [last, _REFERENCE])
        if not rows:
            break
        for row in rows:
            view._cancel()
            last = row[0]
            edge = view.query._edge_row(row[1:])
            if edge["source"] not in members and edge["target"] not in members:
                continue
            source, target = members.get(edge["source"]), members.get(edge["target"])
            if source is None or target is None or target[2] != _VALUE_KIND:
                raise ObservationError("crafting reference leaves its original catalog")
            triple = (edge["source"], target[0], edge["semantic_key"])
            if triple not in expected:
                raise ObservationError("crafting reference projection has an extra, duplicate or incorrect reference")
            expected.remove(triple)
            if not edge["evidence"]:
                raise ObservationError("crafting reference has no original evidence binding")
            for reference in edge["evidence"]:
                view._cancel()
                if ((reference.get("snapshot_id"), reference.get("side")) != binding
                        or reference.get("section") != "report" or reference.get("record_key") != "value"
                        or reference.get("json_pointer") != edge["semantic_key"]
                        or reference.get("observation_kind") != "observed-storage"):
                    raise ObservationError("crafting reference has an inconsistent original evidence binding")
    if expected:
        raise ObservationError("crafting reference projection omits retained native value references")
    gaps.sort(key=lambda row: row["observation_id"])
    view._cancel()
    view.check_current()
    _VERIFIED_CATALOGS.setdefault(view, {})[catalog["id"]] = deepcopy(gaps)
    return gaps


def _incomplete_values(raw: Any, check_cancelled):
    pending = [(raw, "")]
    while pending:
        check_cancelled()
        value, pointer = pending.pop()
        if type(value) is dict:
            if value.get("observation") == "incomplete":
                yield {"pointer": pointer, "reason": value.get("reason"), "type": value.get("type")}
            pending.extend((child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
                           for key, child in reversed(tuple(value.items())))
        elif type(value) is list:
            pending.extend((value[i], pointer + "/" + str(i)) for i in range(len(value) - 1, -1, -1))


def _witness(recipe_id: str, selection_id: str, toward_selected: dict[str, dict[str, Any]], check_cancelled):
    node_ids, edges = [recipe_id], []
    current = recipe_id
    while current != selection_id:
        check_cancelled()
        edge = toward_selected[current]
        edges.append(edge)
        current = edge["target"]
        node_ids.append(current)
    return {"node_ids": node_ids, "edges": edges}


def derive_crafting_reference_exposure(
    view: ObservationView, selection_id: str, *,
    max_depth: int | None = None, max_nodes: int | None = None,
) -> dict[str, Any]:
    """Enumerate reverse stored references, optionally stopping at explicit bounds.

    Depth counts reference edges. ``max_nodes`` counts the selected value plus
    distinct admitted value/recipe nodes. Defaults exhaust the finite catalog.
    Pagination of the generic relationships view is not an analysis bound.
    """
    view.check_current()
    try:
        return _derive(view, selection_id, max_depth=_bound(max_depth, "max_depth"),
                       max_nodes=_bound(max_nodes, "max_nodes"))
    finally:
        view.check_current()


def _derive(view, selection_id, *, max_depth, max_nodes):
    manifest = view.manifest
    if (manifest["format"] != OBSERVATION_BUNDLE_FORMAT
            or manifest["scope"].get("observation_contract") != "workbench-atlas-initialization-projection-v1"):
        raise ObservationError("crafting exposure requires an admitted initialization observation graph")
    selected = view._node(selection_id)
    if selected["kind"] != _VALUE_KIND:
        raise ObservationError("select an exact crafting native value for reference exposure")
    catalog = _catalog(view, selected)
    raw_catalog = catalog["properties"]["raw_value"]
    gaps = _verify_catalog(view, catalog)
    inventory_complete = (raw_catalog.get("status") == "observed"
                          and raw_catalog.get("storedValuesComplete") is True
                          and raw_catalog.get("affectingGaps") == [] and not gaps)
    if not inventory_complete:
        gaps.append({"code": "crafting-stored-reference-evidence-incomplete", "observation_id": catalog["id"],
                     "status": raw_catalog.get("status"), "stored_values_complete": raw_catalog.get("storedValuesComplete"),
                     "original_gaps": raw_catalog.get("affectingGaps")})
    queue = deque([selection_id])
    nodes = {selection_id: selected}
    distances = {selection_id: 0}
    toward_selected = {}
    reference_edges = {}
    frontier = []
    recipes = {}
    while queue:
        view._cancel()
        target = queue.popleft()
        for edge, source in _incoming(view, nodes[target], catalog):
            reference_edges[edge["id"]] = edge
            identity = source["id"]
            if identity in nodes:
                continue
            depth = distances[target] + 1
            reason = ("max-depth" if max_depth is not None and depth > max_depth else
                      "max-nodes" if max_nodes is not None and len(nodes) >= max_nodes else None)
            if reason is not None:
                frontier.append({"reason": reason, "observation": source, "distance": depth,
                                 "reference_edge_id": edge["id"], "toward_selection_id": target})
                continue
            nodes[identity], distances[identity], toward_selected[identity] = source, depth, edge
            if source["kind"] == _VALUE_KIND:
                queue.append(identity)
            else:
                recipes[identity] = source
    results = []
    for identity, recipe in sorted(recipes.items()):
        view._cancel()
        results.append({"recipe": recipe, "distance": distances[identity],
                        "witness": _witness(identity, selection_id, toward_selected, view._cancel)})
    complete = not frontier
    context = view.describe()
    return {
        "format": CRAFTING_REFERENCE_EXPOSURE_FORMAT, "schema_version": 1,
        "context": context, "selection": selected, "catalog": catalog,
        "claim_boundary": _CLAIM,
        "limits": {"max_depth": max_depth, "max_nodes": max_nodes},
        "traversal": {"state": "complete" if complete else "truncated", "visited_nodes": len(nodes),
                      "visited_values": len(nodes) - len(recipes), "visited_recipes": len(recipes),
                      "examined_reference_edges": len(reference_edges), "maximum_distance": max(distances.values())},
        "evidence": {"crafting_reference_inventory": "complete" if inventory_complete else "incomplete",
                     "native_outcome": manifest["evidence_binding"].get("native_outcome"),
                     "capture_coverage": manifest["evidence_binding"].get("coverage"),
                     "matching_evaluated": False, "edit_effect_evaluated": False},
        "summary": {"recorded_recipe_count": len(results), "status": "referrers-observed" if results else
                    "none-observed" if complete and inventory_complete else "undetermined"},
        "results": results, "reference_edges": [reference_edges[key] for key in sorted(reference_edges)],
        "frontier": sorted(frontier, key=lambda row: (row["distance"], row["observation"]["id"], row["reference_edge_id"])),
        "evidence_gaps": gaps,
    }
