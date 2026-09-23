"""Portable, historical recipe scans and reading their already evaluated audit.

The domain checks never invoke a profile, game, projection or audit evaluator.
Archive transport is delegated lazily to Core. Original path strings are evidence
only: this reader neither resolves them nor promotes omitted execution custody.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable

from workbench_crucible_runtime_snapshot.capture import read_runtime_capture
from . import dead_ends
from .view import GraphRecipeHealthView, RecipeHealthError

SCAN_FORMAT = "workbench-atlas-completed-scan-v1"
VIEW_FORMAT = "workbench-atlas-completed-scan-view-v1"
PAGE_FORMAT = "workbench-atlas-cached-recipe-audit-page-v1"
MAX_AUDIT_BYTES = 1024**3
MAX_METADATA_BYTES = 32 * 1024**2
MAX_CAPTURE_BYTES = 1024**3
FINDINGS = frozenset({"missing-producer-candidate", "no-output-use-candidate",
                     "both-sides-candidate", "stranded-output-candidate", "structural-cycle"})
_ORIGINALS = ("request.json", "prepared.json", "launch.json", "runtime-lock.json",
              "protocol.json", "result.json", "input-manifest.json", "audit.json")
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_TRUST = {"content_integrity": "verified", "publisher_authenticity": "unverified",
          "original_execution_custody": "not-included", "current_target_match": "unassessed"}


class ScanError(RecipeHealthError):
    """A portable scan or its retained audit does not meet the V1 contract."""


def _canonical(value, *, ascii=True):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=ascii, allow_nan=False).encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScanError("completed scan JSON contains a duplicate key")
        result[key] = value
    return result


def _constant(value):
    raise ScanError("completed scan JSON contains a nonfinite number")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        _constant(value)
    return number


def _native(path):
    """Use a private filesystem spelling without changing original path data."""
    value = os.path.abspath(path)
    if os.name == "nt":
        if value.startswith(("\\\\?\\", "\\\\.\\")):
            raise ScanError("completed scan inputs require ordinary filesystem paths")
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def _ordinary(path):
    path = Path(os.path.abspath(path))
    for parent in (*reversed(path.parents), path):
        info = _native(parent).lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ScanError("completed scan path traverses a symbolic link or reparse point")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ScanError("completed scan member is not an ordinary independent file")
    return info


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(path, poll):
    poll()
    before = _ordinary(path)
    bound = MAX_AUDIT_BYTES if path.name == "audit.json" else MAX_METADATA_BYTES
    _require(before.st_size <= bound, "completed scan JSON member exceeds its supported byte bound")
    descriptor = os.open(_native(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        # Windows path and handle stat expose different ctime clocks. Bind the
        # common identity, then compare each clock with itself across the read.
        _require(_stamp(before)[:-1] == _stamp(opened)[:-1], "completed scan member changed while opening")
        raw = stream.read(before.st_size + 1)
        _require(_stamp(opened) == _stamp(os.fstat(stream.fileno())), "completed scan open member changed while reading")
    _require(_stamp(before) == _stamp(_ordinary(path)) and len(raw) == before.st_size,
             "completed scan member changed while reading")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
    poll()
    if type(value) is not dict:
        raise ScanError("completed scan JSON member must be an object")
    return value, {"size": len(raw), "sha256": sha256(raw).hexdigest()}


def _require(condition, message):
    if not condition:
        raise ScanError(message)


def _seal(value, prefix, *, ascii=True):
    body = {k: v for k, v in value.items() if k != "id"}
    _require(value.get("id") == prefix + ":sha256:" + sha256(_canonical(body, ascii=ascii)).hexdigest(),
             "completed scan original record seal differs")


def _counts_equal(actual, expected, label):
    _require(type(actual) is dict and set(actual) == set(expected)
             and all(type(value) is int and value >= 0 for value in actual.values())
             and actual == expected, "cached audit " + label + " differ")


def validate_cached_recipe_audit(report, view, *, check_cancelled=None):
    """Check a stored V1 report's bindings, inventory, references and counts.

    This is structural reconciliation, not an independent rerun of the evaluator
    or a claim that its candidate findings prove gameplay reachability.
    """
    poll = check_cancelled or (lambda: None)
    poll()
    _require(type(report) is dict and report.get("format") == dead_ends.DEAD_END_AUDIT_FORMAT
             and type(report.get("schema_version")) is int and report["schema_version"] == 1,
             "unsupported cached recipe audit format")
    context = report.get("context")
    expected_context = view.describe()
    _require(type(context) is dict and isinstance(context.get("root"), str)
             and {k: v for k, v in context.items() if k != "root"}
             == {k: v for k, v in expected_context.items() if k != "root"},
             "cached audit graph context differs")
    policy = report.get("policy")
    _require(type(policy) is dict and set(policy) == {"id", "implementation_sha256"}
             and policy["id"] == "workbench-atlas-captured-active-links-v1"
             and type(policy["implementation_sha256"]) is str
             and _HEX.fullmatch(policy["implementation_sha256"]) is not None,
             "unsupported cached recipe audit policy")
    recipes, resources, cycles = (report.get(key) for key in ("recipes", "resources", "cycles"))
    _require(all(type(rows) is list and all(type(row) is dict for row in rows)
                 for rows in (recipes, resources, cycles)), "cached audit inventories must contain objects")
    # Small reference tables are enough; do not retain a second copy of all graph
    # properties or recompute recipe matching, producer alternatives or SCCs.
    nodes = {identifier: (kind, key) for identifier, kind, key in view.query.connection.execute(
        "SELECT id,kind,semantic_key FROM nodes")}
    recipe_values, observed_names = {}, {}
    for identifier, kind, raw in view.query.connection.execute(
            "SELECT id,kind,properties_json FROM nodes WHERE kind IN ('gt-recipe','item-variant','forge-fluid')"):
        poll()
        props = json.loads(raw)
        if kind == "gt-recipe":
            active = props.get("lookup_active")
            counts = props.get("captured_input_counts")
            recipe_values[identifier] = (
                "active" if active is True else "inactive" if active is False else "unknown",
                {key: props[key] for key in ("duration", "eut", "category") if key in props},
                {"status": "observed" if counts is not None else "unknown", "captured_counts": counts},
            )
            observed_names[identifier] = []
        else:
            names = props.get("observed_item_names")
            observed_names[identifier] = sorted({entry["name"] for entry in (names if isinstance(names, list) else [])
                                                 if isinstance(entry, dict) and isinstance(entry.get("name"), str)})
    edge_ids = set()
    for raw in view.query.connection.execute(
            "SELECT e.relation,s.id,t.id,e.semantic_key,'{}','[]' FROM edges e "
            "JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node"):
        poll()
        edge_ids.add(view.query._edge_row(raw)["id"])
    cycle_ids = {row.get("component_id") for row in cycles}
    _require(None not in cycle_ids and len(cycle_ids) == len(cycles), "cached audit cycle identities differ")
    recipe_cycles = {}
    for cycle in cycles:
        members = cycle.get("member_node_ids")
        _require(type(members) is list and all(type(key) is str and key in nodes for key in members)
                 and bool(members) and members == sorted(set(members))
                 and cycle["component_id"] == dead_ends._identity(view.manifest["graph_set_id"], members)
                 and cycle.get("recipe_ids") == [key for key in members if nodes[key][0] == "gt-recipe"]
                 and cycle.get("seed_supply") == "unknown" and cycle.get("viability_effect") == "unknown",
                 "cached audit structural cycle identity or scope differs")
        for recipe_id in cycle["recipe_ids"]:
            recipe_cycles.setdefault(recipe_id, []).append(cycle["component_id"])
    for rows, kinds in ((recipes, {"gt-recipe"}), (resources, {"item-variant", "forge-fluid"})):
        identifiers = [row.get("selection_id") for row in rows]
        _require(all(type(key) is str for key in identifiers)
                 and identifiers == sorted(set(identifiers))
                 and set(identifiers) == {key for key, value in nodes.items() if value[0] in kinds},
                 "cached audit inventory differs from graph")
        for row in rows:
            _require((row.get("kind"), row.get("semantic_key")) == nodes[row["selection_id"]],
                     "cached audit node identity differs")
            _require(row.get("observed_names") == observed_names[row["selection_id"]],
                     "cached audit observed names differ from the graph")

    node_keys = {"selection_id", "selector_id", "resource_id", "recipe_id", "source_id", "target_id"}
    node_lists = {"member_node_ids", "recipe_ids", "node_ids", "missing_selector_ids", "unknown_selector_ids"}
    edge_keys = {"edge_id", "owner_edge_id", "acceptance_edge_id", "observation_edge_id", "output_edge_id"}
    edge_lists = {"stranded_output_edge_ids", "unknown_output_edge_ids"}
    pending = [recipes, resources, cycles]
    while pending:
        poll()
        value = pending.pop()
        if type(value) is list:
            pending.extend(value)
        elif type(value) is dict:
            for key, item in value.items():
                if key in node_keys | edge_keys:
                    _require(type(item) is str and item in (nodes if key in node_keys else edge_ids),
                             "cached audit contains a foreign graph reference")
                elif key in node_lists | edge_lists | {"cycle_component_ids"}:
                    choices = nodes if key in node_lists else edge_ids if key in edge_lists else cycle_ids
                    _require(type(item) is list and all(type(v) is str and v in choices for v in item),
                             "cached audit contains a foreign graph reference")
                pending.append(item)

    findings, lookup, upstream, downstream = Counter(), Counter(), Counter(), Counter()
    unresolved = unknown_inventory = 0
    for row in recipes:
        poll()
        _require(row.get("lookup_state") in {"active", "inactive", "unknown"}, "cached audit lookup state differs")
        _require((row["lookup_state"], row.get("recipe_values"), row.get("input_inventory"))
                 == recipe_values[row["selection_id"]], "cached audit observed recipe values differ from the graph")
        _require(type(row.get("findings")) is list and row["findings"] == sorted(set(row["findings"]))
                 and set(row["findings"]).issubset(FINDINGS), "cached audit finding kinds differ")
        _require(type(row.get("issues")) is list and type(row.get("inputs")) is list
                 and type(row.get("outputs")) is list and type(row.get("recipe_maps")) is list,
                 "cached audit recipe inventory fields differ")
        _require(row["upstream"]["status"] in {"missing-producer-candidate", "unknown", "observed-local-links", "observed-inputless", "unassessed"}
                 and row["downstream"]["status"] in {"unknown", "no-output-use-candidate", "partial-output-use-gap", "observed-local-links", "unassessed"}
                 and row["input_inventory"]["status"] in {"unknown", "observed"}
                 and all(slot["supply_status"] in {"unknown", "observed-active-producer", "no-observed-active-producer"}
                         and slot["consumption"] in {"unknown", "reusable", "consumed"}
                         and slot["acceptance_scope"] == "captured-resource-domain" for slot in row["inputs"])
                 and all(output["use_status"] in {"unknown", "observed-active-use", "no-observed-active-use"}
                         and output["output_kind"] in {"unknown", "guaranteed", "chanced"} for output in row["outputs"]),
                 "cached audit recipe state is outside the stored policy")
        _require(row["cycle_component_ids"] == recipe_cycles.get(row["selection_id"], []),
                 "cached audit cycle membership differs")
        findings.update(row["findings"])
        lookup[row["lookup_state"]] += 1
        upstream[row["upstream"]["status"]] += 1
        downstream[row["downstream"]["status"]] += 1
        unknown_inventory += row["input_inventory"]["status"] == "unknown"
        unresolved += bool(row["issues"]) or row["lookup_state"] != "active" or any(
            slot["issues"] for slot in row["inputs"]) or row["upstream"]["status"] == "unknown" or (
            row["downstream"]["status"] == "unknown") or bool(row["upstream"]["unknown_selector_ids"]) or bool(
            row["downstream"]["unknown_output_edge_ids"])
    summary = report.get("summary")
    _require(type(summary) is dict and summary.get("truncated") is False, "cached audit is not complete")
    numeric = {"recipe_count": len(recipes), "active_recipe_count": lookup["active"],
               "inactive_recipe_count": lookup["inactive"], "unknown_activity_recipe_count": lookup["unknown"],
               "resource_count": len(resources), "cycle_component_count": len(cycles), "cycle_count": len(cycles),
               "unresolved_recipe_count": unresolved, "recipes_with_findings": sum(bool(r["findings"]) for r in recipes)}
    numeric.update({key.replace("-", "_") + "_count": findings[key] for key in FINDINGS if key != "structural-cycle"})
    for key, expected in numeric.items():
        _require(type(summary.get(key)) is int and summary[key] == expected, "cached audit summary count differs")
    for key, expected in (("finding_counts", findings), ("upstream_status_counts", upstream),
                          ("downstream_status_counts", downstream)):
        _counts_equal(summary.get(key), dict(expected), "summary states")
    coverage = report.get("coverage")
    _require(type(coverage) is dict, "cached audit coverage is absent")
    fixed = {"scan": "complete-finite", "truncated": False, "recipe_kinds": ["gt-recipe"],
             "status": "captured-gt-recipes-only" if recipes else "no-supported-recipes",
             "recipe_count": len(recipes), "matching_scope": "captured-resource-domain",
             "other_recipe_families": "unassessed", "external_acquisition": "unsupported", "terminal_use": "unsupported",
             "machine_execution": "unassessed", "transitive_supply": "unassessed", "quantity_feasibility": "unassessed",
             "chance_feasibility": "unassessed", "unknown_input_inventory_recipe_count": unknown_inventory,
             "partition_limitations": [{"partition_id": p.get("id"), "limitations": p["limitations"]}
                                       for p in view.manifest["partitions"] if p.get("limitations")]}
    for key, expected in fixed.items():
        _require(type(coverage.get(key)) is type(expected) and coverage[key] == expected,
                 "cached audit coverage differs")
    _counts_equal(coverage.get("captured_node_kind_counts"), view.kinds, "captured node counts")
    scanned = {kind: count for kind, count in view.kinds.items() if kind in dead_ends._KINDS}
    _counts_equal(coverage.get("scanned_node_kind_counts"), scanned, "scanned node counts")
    relations = {key: count for key, count in view.relations.items() if key in (
        dead_ends._OUTPUTS | dead_ends._OWNERS | dead_ends._ACCEPTS | dead_ends._OBSERVES | dead_ends._MAPS)}
    _counts_equal(coverage.get("relationships"), relations, "relationship counts")
    _require(type(coverage.get("examined_relationship_count")) is int
             and coverage["examined_relationship_count"] == sum(relations.values()), "cached audit relationship total differs")
    selectors = incomplete = limited = 0
    for (raw,) in view.query.connection.execute("SELECT properties_json FROM nodes WHERE kind='gt-recipe-input-selector'"):
        props = json.loads(raw)
        selectors += 1
        incomplete += props.get("acceptance_complete") is not True
        limited += bool(props.get("matching_limits"))
    for key, expected in (("input_selector_count", selectors), ("incomplete_selector_count", incomplete),
                          ("matching_limited_selector_count", limited)):
        _require(type(coverage.get(key)) is int and coverage[key] == expected, "cached audit selector counts differ")
    return report


def _originals(root, report, capture, manifest, measurements, poll):
    docs = {}
    for name in _ORIGINALS:
        if name not in {"audit.json", "input-manifest.json"}:
            docs[name], measurements[name] = _read(root / name, poll)
    request, prepared, launch, lock, protocol, result = (docs[name] for name in _ORIGINALS[:6])
    for value, prefix in ((request, "recipe-capture-request"), (prepared, "recipe-capture-prepared"),
                          (result, "recipe-capture-result")):
        _seal(value, prefix)
    _seal(launch, "forge-recipe-capture-launch", ascii=False)
    _require(result.get("format") == "workbench-developer-recipe-capture-result-v1"
             and result.get("state") == "complete" and result.get("native_admitted") is True,
             "original recipe scan did not complete capture admission")
    _require(request.get("format") == "workbench-developer-recipe-capture-request-v1"
             and prepared.get("format") == "workbench-developer-recipe-capture-prepared-v1"
             and launch.get("format") == "workbench-supersymmetry-recipe-capture-launch-v1"
             and request.get("state") == "planned" and prepared.get("state") == "prepared"
             and lock.get("format") == "workbench-recipe-capture-runtime-lock-v1"
             and protocol.get("format") == "workbench-recipe-capture-protocol-v1",
             "original recipe scan metadata format differs")
    custody = result.get("custody")
    _require(type(custody) is dict and set(custody) == set(_ORIGINALS) - {"result.json"},
             "original recipe scan metadata custody is incomplete")
    for name, descriptor in custody.items():
        _require(type(descriptor) is dict and {k: descriptor.get(k) for k in ("size", "sha256")} == measurements[name],
                 "original recipe scan custody digest differs")
    inputs = capture.input_manifest
    _require(result.get("request_id") == request["id"] == prepared.get("request_id")
             and result.get("prepared_id") == prepared["id"] and result.get("launch_id") == launch["id"]
             and result.get("attempt_id") == request.get("attempt_id") == prepared.get("attempt_id") == inputs.get("capture_id")
             and inputs.get("launch_id") == inputs["capture_id"] + "-launch"
             and inputs.get("candidate_lock_sha256") == sha256(_canonical(lock)).hexdigest()
             and inputs.get("adapter_profile_sha256") == sha256(_canonical(protocol)).hexdigest()
             and launch.get("input_manifest_sha256") == measurements["input-manifest.json"]["sha256"]
             and result.get("input_manifest") == custody["input-manifest.json"]
             and result.get("audit") == custody["audit.json"], "original recipe scan evidence chain differs")
    _require(inputs.get("pack_source") == request.get("source_binding")
             and inputs.get("pack_binding_id") == request.get("source_binding", {}).get("id")
             and lock.get("source_binding_id") == inputs.get("pack_binding_id")
             and protocol.get("profile") == request.get("provider")
             and protocol.get("descriptor") == request.get("descriptor")
             and protocol.get("observer_sources") == request.get("observer_sources")
             and protocol.get("heap_mib") == request.get("heap_mib") == launch.get("heap_mib")
             and protocol.get("explicit_eula_acceptance") is True,
             "original recipe scan source or protocol binding differs")
    projection = result.get("projection")
    recipe_count = report["summary"]["recipe_count"]
    map_count = sum(part["nodes"]["kinds"].get("gt-recipe-map", 0) for part in manifest["partitions"])
    _require(type(projection) is dict and projection.get("state") == "complete"
             and projection.get("graph_set_id") == manifest["graph_set_id"]
             and projection.get("capture_manifest_sha256") == capture.manifest_file_sha256
             and projection.get("summary") == manifest["summary"]
             and type(projection.get("recipe_count")) is int and projection["recipe_count"] == recipe_count
             and type(projection.get("recipe_map_count")) is int and projection["recipe_map_count"] == map_count
             and capture.categories.get("gt-recipes", {}).get("record_count") == recipe_count
             and result.get("summary") == report["summary"] and result.get("coverage") == report["coverage"],
             "original recipe scan projection or audit binding differs")
    inventory = result.get("capture_files")
    expected = {p["file"]: {"size": p["size"], "sha256": p["sha256"]} for p in capture.manifest["payloads"]}
    expected["manifest.json"] = measurements["capture/manifest.json"]
    expected[".capture-complete"] = {"size": 0, "sha256": sha256(b"").hexdigest()}
    _require(type(inventory) is list and all(type(row) is dict for row in inventory)
             and len(inventory) == len(expected) and {row.get("path") for row in inventory} == set(expected)
             and all({k: row.get(k) for k in ("size", "sha256")} == expected[row["path"]] for row in inventory),
             "original recipe scan capture inventory differs")
    return result


def _validate(root, poll):
    root = Path(root)
    poll()
    measurements = {}
    report, measurements["audit.json"] = _read(root / "audit.json", poll)
    inputs, measurements["input-manifest.json"] = _read(root / "input-manifest.json", poll)
    graph_manifest, measurements["graph/manifest.json"] = _read(root / "graph/manifest.json", poll)
    capture_manifest, measurements["capture/manifest.json"] = _read(root / "capture/manifest.json", poll)
    with GraphRecipeHealthView(_native(root / "graph"), check_cancelled=poll) as view:
        _require(view.manifest == graph_manifest, "completed scan graph manifest changed while verifying")
        _require(view.manifest["format"] == "workbench-atlas-categorical-graph-bundle-v2",
                 "completed recipe scan requires a V2 graph")
        binding = view.manifest["evidence_binding"]
        category_results = binding.get("category_results")
        _require(type(category_results) is dict and "gt-recipes" in category_results, "graph has no retained GT recipe category")
        poll()
        capture = read_runtime_capture(_native(root / "capture"), categories=category_results,
                                       input_manifest=_native(root / "input-manifest.json"), max_source_bytes=MAX_CAPTURE_BYTES)
        poll()
        _require(binding.get("capture_manifest") == capture.manifest == capture_manifest
                 and measurements["capture/manifest.json"]["sha256"] == capture.manifest_file_sha256
                 and binding.get("capture_manifest_sha256") == capture.manifest_file_sha256
                 and binding.get("input_manifest") == inputs == capture.input_manifest
                 and category_results == {key: {field: value for field, value in category.items() if field != "records"}
                                          for key, category in capture.categories.items()},
                 "completed scan graph and capture bindings differ")
        for field in ("pack_binding_id", "platform_binding_id", "candidate_lock_sha256", "physical_side"):
            _require(field in inputs and view.manifest["scope"].get(field) == inputs[field],
                     "completed scan graph and observation scope differ")
        validate_cached_recipe_audit(report, view, check_cancelled=poll)
        result = _originals(root, report, capture, view.manifest, measurements, poll)
        metadata = {"format": SCAN_FORMAT, "domain": {
            "graph_set_id": view.manifest["graph_set_id"], "scope": view.manifest["scope"],
            "capture_manifest_sha256": capture.manifest_file_sha256,
            "input_manifest_sha256": measurements["input-manifest.json"]["sha256"],
            "audit_sha256": measurements["audit.json"]["sha256"], "original_result_id": result["id"],
            "audit_policy": report["policy"], "summary": report["summary"], "coverage": report["coverage"],
        }}
        members = {name: root / name for name in _ORIGINALS}
        members.update({"capture/" + name: root / "capture" / name for name in (
            "manifest.json", ".capture-complete", *(p["file"] for p in capture.manifest["payloads"]))})
        names = ["manifest.json", view.manifest["query_index"]["file"]]
        names.extend(p[stream]["file"] for p in view.manifest["partitions"] for stream in ("nodes", "edges"))
        members.update({"graph/" + name: root / "graph" / name for name in names})
        inventory = {name: measurements[name] for name in _ORIGINALS}
        inventory.update({name: measurements[name] for name in ("graph/manifest.json", "capture/manifest.json")})
        inventory["capture/.capture-complete"] = {"size": 0, "sha256": sha256(b"").hexdigest()}
        for payload in capture.manifest["payloads"]:
            inventory["capture/" + payload["file"]] = {key: payload[key] for key in ("size", "sha256")}
        graph_files = [view.manifest["query_index"], *(p[stream] for p in view.manifest["partitions"] for stream in ("nodes", "edges"))]
        for descriptor in graph_files:
            inventory["graph/" + descriptor["file"]] = {key: descriptor[key] for key in ("size", "sha256")}
    poll()
    return metadata, report, members, inventory


def _guard(function, *args):
    try:
        return function(*args)
    except ScanError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise ScanError(f"cannot read completed recipe scan: {exc}") from exc


def validate_completed_scan(root, *, check_cancelled=None):
    """Return deterministic portable metadata after read-only domain validation."""
    return _guard(_validate, Path(root), check_cancelled or (lambda: None))[0]


def _cancelled(poll):
    def check():
        poll()
        return False
    return check


def _transport():
    from workbench_core import archive_exchange
    return archive_exchange


def _verified(root, poll):
    manifest = _transport().verify_directory(root, cancelled=_cancelled(poll))
    metadata, report, members, inventory = _validate(root, poll)
    _require(manifest["metadata"] == metadata, "archive metadata differs from the completed scan")
    _require({row["path"] for row in manifest["members"]} == set(members), "archive member set differs from the completed scan")
    _require(manifest["members"] == [{"path": name, **inventory[name]} for name in sorted(inventory)],
             "archive inventory differs from the completed scan")
    return manifest, metadata, report, members


def _policy_status(policy):
    current = sha256(_native(Path(dead_ends.__file__)).read_bytes()).hexdigest()
    return "same-implementation" if policy["implementation_sha256"] == current else "different-implementation"


def _view(root, manifest, metadata):
    return {"format": VIEW_FORMAT, "state": "verified", "manifest_id": manifest["id"],
            "root": str(Path(root).absolute()), "graph_path": str((Path(root) / "graph").absolute()),
            "metadata": metadata, "policy_status": _policy_status(metadata["domain"]["audit_policy"]),
            "trust": dict(_TRUST), "recomputed": False}


def show_scan(directory, *, check_cancelled=None):
    def run():
        manifest, metadata, _, _ = _verified(Path(directory), check_cancelled or (lambda: None))
        return _view(directory, manifest, metadata)
    return _guard(run)


def import_scan(archive, destination, *, check_cancelled=None):
    def run():
        poll = check_cancelled or (lambda: None)
        validated = {}
        def validate(stage, manifest):
            metadata, _, members, inventory = _validate(stage, poll)
            _require(metadata == manifest["metadata"], "archive metadata differs from the completed scan")
            _require({row["path"] for row in manifest["members"]} == set(members), "archive member set differs from the completed scan")
            _require(manifest["members"] == [{"path": name, **inventory[name]} for name in sorted(inventory)],
                     "archive inventory differs from the completed scan")
            validated.update(manifest=manifest, metadata=metadata)
            return {"format": "workbench-atlas-completed-scan-validation-v1", "graph_set_id": metadata["domain"]["graph_set_id"],
                    "original_result_id": metadata["domain"]["original_result_id"], "trust": dict(_TRUST), "recomputed": False}
        receipt = _transport().import_archive(Path(archive), Path(destination), validate=validate, cancelled=_cancelled(poll))
        return {**_view(destination, validated["manifest"], validated["metadata"]), "import_receipt": receipt}
    return _guard(run)


def export_completed_scan(root, destination, *, check_cancelled=None):
    """Export the fixed data subset of an original completed attempt.

    The caller that owns the original attempt must separately verify its full
    process and runtime custody before calling this domain-only export.
    """
    def run():
        poll = check_cancelled or (lambda: None)
        metadata, _, members, inventory = _validate(Path(root), poll)
        expected = _transport().build_manifest([{"path": name, **inventory[name]} for name in sorted(inventory)], metadata=metadata)
        result = _transport().export_archive(Path(destination), members, metadata=metadata,
                                               expected_manifest=expected, cancelled=_cancelled(poll))
        return {**_view(root, result["manifest"], metadata), "archive": result["archive"]}
    return _guard(run)


def export_scan(directory, destination, *, check_cancelled=None):
    def run():
        poll = check_cancelled or (lambda: None)
        manifest, metadata, _, members = _verified(Path(directory), poll)
        result = _transport().export_archive(Path(destination), members, metadata=metadata,
                                               expected_manifest=manifest, cancelled=_cancelled(poll))
        return {**_view(directory, manifest, metadata), "archive": result["archive"]}
    return _guard(run)


def read_cached_recipe_audit(directory, *, finding=None, lookup_state=None, text=None,
                             offset=0, limit=100, check_cancelled=None):
    """Read a page from a verified stored audit, preserving its original policy."""
    def run():
        _require(finding is None or finding in FINDINGS, "unknown cached audit finding filter")
        _require(lookup_state is None or lookup_state in {"active", "inactive", "unknown"}, "unknown cached audit lookup filter")
        _require(text is None or type(text) is str and bool(text.strip()), "cached audit text must be nonempty")
        _require(type(offset) is int and offset >= 0 and type(limit) is int and 0 <= limit <= 10000,
                 "cached audit paging requires a nonnegative offset and limit within 0..10000")
        poll = check_cancelled or (lambda: None)
        manifest, metadata, report, _ = _verified(Path(directory), poll)
        needle = text.casefold() if text is not None else None
        matching_resources = set()
        if needle is not None:
            for resource in report["resources"]:
                poll()
                if any(needle in str(value).casefold() for value in (
                        resource["selection_id"], resource["semantic_key"], *resource.get("observed_names", []))):
                    matching_resources.add(resource["selection_id"])
        selected = []
        count = 0
        for row in report["recipes"]:
            poll()
            if finding is not None and finding not in row["findings"] or lookup_state is not None and lookup_state != row["lookup_state"]:
                continue
            if needle is not None:
                direct = any(needle in str(value).casefold() for value in (
                    row["selection_id"], row["semantic_key"], *row.get("observed_names", []),
                    *(r["semantic_key"] for r in row["recipe_maps"])))
                related = any(output["resource_id"] in matching_resources for output in row["outputs"]) or any(
                    alternative["resource_id"] in matching_resources for slot in row["inputs"]
                    for alternative in (*slot["alternatives"], *slot["observed_representatives"]))
                if not direct and not related:
                    continue
            if offset <= count < offset + limit:
                selected.append(row)
            count += 1
        return {"format": PAGE_FORMAT, "manifest_id": manifest["id"], "context": report["context"],
                "metadata": metadata,
                "graph_path": str((Path(directory) / "graph").absolute()),
                "audit_sha256": metadata["domain"]["audit_sha256"], "policy": report["policy"],
                "policy_status": _policy_status(report["policy"]), "coverage": report["coverage"], "summary": report["summary"],
                "filters": {"finding": finding, "lookup_state": lookup_state, "text": text}, "items": selected,
                "page": {"offset": offset, "limit": limit, "returned": len(selected), "total_matching": count,
                         "next_offset": offset + len(selected) if limit and offset + len(selected) < count else None},
                "trust": dict(_TRUST), "recomputed": False}
    return _guard(run)
