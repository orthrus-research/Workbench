#!/usr/bin/env python3

"""Read-only, repeatable capture trial for the historical light-oil graph.

The experiment admits one exact EVID-9089 query projection, exercises three
independent identity routes to the same recipe occurrences, and retains a
bounded upstream route-DAG summary.  Timing data is deliberately written to a
separate sidecar and never participates in semantic identity.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time
from typing import Any, Callable, Sequence, TypeVar

from workbench_atlas.runtime_graph_chain_query import (
    ChainOptions,
    RuntimeGraphChainQuery,
    canonical_json_bytes,
    validate_process_chain_result,
)
from workbench_atlas.runtime_graph_domain_query import (
    GraphScope,
    ProfileScope,
    RuntimeGraphDomainQuery,
)
from workbench_atlas.runtime_graph_query import NodeSelector, PageRequest, RuntimeGraphReader


FORMAT = "susy-light-oil-capture-experiment-v1"
OUTPUT_FORMAT = "susy-light-oil-capture-output-v1"
TIMING_FORMAT = "susy-light-oil-capture-timings-v1"
WORKBENCH_ROOT = Path(__file__).resolve().parents[5]
WORKBENCH_STORAGE_ROOT = WORKBENCH_ROOT / ".workbench"

PROFILE = "COMMON_FINAL_STATE"
PHYSICAL_SIDE = "DEDICATED_SERVER"
LIGHT_OIL_MATERIAL_KEY = "gregtech:oil_light"
LIGHT_OIL_FLUID_KEY = "oil_light"
LIGHT_OIL_RECIPE_AMOUNT_MB = 1_000
LIGHT_OIL_MATERIAL_ID = (
    "rg:common_final_state_dedicated_server:material:gregtech:oil_light"
)
LIGHT_OIL_FLUID_ID = (
    "rg:common_final_state_dedicated_server:fluid:oil_light"
)
LIGHT_OIL_VARIANT_ID = (
    "rg:common_final_state_dedicated_server:fluid_variant:"
    "9d742dae9caac3765c123084241049d8790025a5ad619abc69210a92e7531e9d"
)
LIGHT_OIL_WORLDGEN_ID = (
    "rg:common_final_state_dedicated_server:worldgen_deposit:"
    "bedrock_fluid|overworld/light_oil_deposit.json|occurrence:0"
)

DILUTED_MATERIAL_KEY = "susy:diluted_oil_light"
DILUTED_FLUID_KEY = "diluted_oil_light"
DILUTED_AMOUNT_MB = 1_100
DILUTED_MATERIAL_ID = (
    "rg:common_final_state_dedicated_server:material:susy:diluted_oil_light"
)
DILUTED_FLUID_ID = (
    "rg:common_final_state_dedicated_server:fluid:diluted_oil_light"
)
DILUTED_VARIANT_ID = (
    "rg:common_final_state_dedicated_server:fluid_variant:"
    "6b4e205396aa1878904f7cbba5aa8281c1bfa60513626d89e62cec36008686ea"
)

BLENDER_RECIPE_ID = (
    "rg:common_final_state_dedicated_server:recipe:blender:"
    "c7440004c10d66b21d263b2d7cc1bb4aaf2b85e16f2508f4131bf133ad127e29:0"
)
SEPARATOR_RECIPE_ID = (
    "rg:common_final_state_dedicated_server:recipe:electrostatic_separator:"
    "f01e259f1f532295e9c2b1b9863de03bad970cd2f67f52f55789132b4e9ac348:0"
)
MIXER_RECIPE_ID = (
    "rg:common_final_state_dedicated_server:recipe:mixer:"
    "df6746019b0a353d79297f92643ee8f9adf089facc513f210c3fad086d7a05b6:0"
)
EXPECTED_PROJECTION_IDENTITY = {
    "format": "susy-runtime-graph-query-projection-v1",
    "bytes": 33_449_480_192,
    "sha256": (
        "fc01689dfa76b386e78814343b629e0f4fccd865d1e60d6ee7d19d9e564d193e"
    ),
    "sqlite_application_id": 1_398_098_247,
    "sqlite_user_version": 2,
}
EXPECTED_SQLITE_SCHEMA_FORMAT = 4

CHAIN_OPTIONS = ChainOptions(
    max_depth=8,
    max_routes=50,
    max_alternatives_per_slot=25,
    max_visited_nodes=10_000,
)
EXPECTED_CHAIN_COUNTS = {
    "roots": 1,
    "subproblems": 1,
    "routes": 1,
    "cycles": 0,
    "unresolved_leaves": 0,
}
EXPECTED_UNRESOLVED_REASON_HISTOGRAM: dict[str, int] = {}
EXPECTED_CHAIN_RESULT_SHA256 = (
    "01ceada46ddbbe505820363cdd4d85e9248d41a9ab1baec98d05b70f582c0f07"
)
EXPECTED_CHAIN_RESULT_BYTES = 9_434
MAX_REPETITIONS = 20


class LightOilCaptureError(ValueError):
    """Raised when retained evidence drifts or a trial would overclaim it."""


@dataclass(frozen=True)
class TargetSpec:
    """Exact identity routes and occurrence expectations for one fluid."""

    label: str
    material_key: str
    fluid_key: str
    material_id: str
    fluid_id: str
    variant_id: str
    producer_ids: tuple[str, ...]
    consumer_ids: tuple[str, ...]
    producer_kind: str
    consumer_kind: str
    producer_channel: str
    consumer_channel: str
    producer_quantity: tuple[tuple[str, int], ...]
    consumer_quantity: tuple[tuple[str, int], ...]


LIGHT_OIL = TargetSpec(
    label="light-oil",
    material_key=LIGHT_OIL_MATERIAL_KEY,
    fluid_key=LIGHT_OIL_FLUID_KEY,
    material_id=LIGHT_OIL_MATERIAL_ID,
    fluid_id=LIGHT_OIL_FLUID_ID,
    variant_id=LIGHT_OIL_VARIANT_ID,
    producer_ids=(LIGHT_OIL_WORLDGEN_ID,),
    consumer_ids=(BLENDER_RECIPE_ID, MIXER_RECIPE_ID),
    producer_kind="worldgen_deposit",
    consumer_kind="recipe",
    producer_channel="bedrock_fluid_output",
    consumer_channel="fluid_input",
    producer_quantity=(
        ("minimum_yield_inclusive", 400),
        ("maximum_yield_exclusive", 600),
        ("depleted_yield", 400),
    ),
    consumer_quantity=(("amount", LIGHT_OIL_RECIPE_AMOUNT_MB),),
)

DILUTED_LIGHT_OIL = TargetSpec(
    label="diluted-light-oil",
    material_key=DILUTED_MATERIAL_KEY,
    fluid_key=DILUTED_FLUID_KEY,
    material_id=DILUTED_MATERIAL_ID,
    fluid_id=DILUTED_FLUID_ID,
    variant_id=DILUTED_VARIANT_ID,
    producer_ids=(BLENDER_RECIPE_ID, MIXER_RECIPE_ID),
    consumer_ids=(SEPARATOR_RECIPE_ID,),
    producer_kind="recipe",
    consumer_kind="recipe",
    producer_channel="fluid_output",
    consumer_channel="fluid_input",
    producer_quantity=(("amount", DILUTED_AMOUNT_MB),),
    consumer_quantity=(("amount", DILUTED_AMOUNT_MB),),
)


@dataclass(frozen=True)
class ExperimentResult:
    """Timing-free semantic result and its explicitly separate measurements."""

    semantic: dict[str, Any]
    semantic_bytes: bytes
    semantic_sha256: str
    output_document: dict[str, Any]
    timing_document: dict[str, Any]


_T = TypeVar("_T")


def _measure(
    phases: dict[str, dict[str, int]],
    name: str,
    operation: Callable[[], _T],
) -> _T:
    wall_start = time.monotonic_ns()
    cpu_start = time.process_time_ns()
    try:
        return operation()
    finally:
        phases[name] = {
            "wall_ns": time.monotonic_ns() - wall_start,
            "cpu_ns": time.process_time_ns() - cpu_start,
        }


def _read_sqlite_header(path: Path) -> dict[str, int]:
    try:
        with path.open("rb") as stream:
            header = stream.read(100)
    except OSError as exc:
        raise LightOilCaptureError(
            f"cannot read query projection SQLite header: {path}: {exc}"
        ) from exc
    if len(header) != 100 or header[:16] != b"SQLite format 3\0":
        raise LightOilCaptureError("query projection is not a SQLite 3 database")
    encoded_page_size = int.from_bytes(header[16:18], "big")
    page_size = 65_536 if encoded_page_size == 1 else encoded_page_size
    return {
        "sqlite_schema_format": int.from_bytes(header[44:48], "big"),
        "sqlite_user_version": int.from_bytes(header[60:64], "big"),
        "sqlite_application_id": int.from_bytes(header[68:72], "big"),
        "page_size": page_size,
        "page_count": int.from_bytes(header[28:32], "big"),
    }


def _admit_projection(
    reader: RuntimeGraphReader,
    database: Path,
) -> dict[str, Any]:
    identity = reader.projection_identity()
    if identity != EXPECTED_PROJECTION_IDENTITY:
        raise LightOilCaptureError(
            "query projection differs from the exact EVID-9089 projection identity"
        )
    header = _read_sqlite_header(database)
    if (
        header["sqlite_schema_format"] != EXPECTED_SQLITE_SCHEMA_FORMAT
        or header["sqlite_user_version"]
        != EXPECTED_PROJECTION_IDENTITY["sqlite_user_version"]
        or header["sqlite_application_id"]
        != EXPECTED_PROJECTION_IDENTITY["sqlite_application_id"]
        or header["page_size"] <= 0
        or header["page_count"] <= 0
        or header["page_size"] * header["page_count"]
        != EXPECTED_PROJECTION_IDENTITY["bytes"]
    ):
        raise LightOilCaptureError(
            "query projection SQLite application, user, schema, or page identity differs"
        )
    return {
        "identity": dict(identity),
        "sqlite_header": dict(header),
    }


def _selectors(
    target: TargetSpec,
) -> tuple[tuple[str, NodeSelector, str, str], ...]:
    return (
        (
            "material",
            NodeSelector.by_key(
                "material-resource-location",
                target.material_key,
                kind="material",
            ),
            target.material_id,
            "material",
        ),
        (
            "fluid-base",
            NodeSelector.by_key(
                "fluid-name",
                target.fluid_key,
                kind="fluid",
            ),
            target.fluid_id,
            "fluid",
        ),
        (
            "fluid-variant",
            NodeSelector.by_id(target.variant_id, kind="fluid_variant"),
            target.variant_id,
            "fluid_variant",
        ),
    )


def _light_oil_traversal_selector() -> NodeSelector:
    return NodeSelector.by_key(
        "fluid-resource-location",
        LIGHT_OIL_MATERIAL_KEY,
        kind="fluid",
    )


def _selector_dict(selector: NodeSelector) -> dict[str, str]:
    result = {
        "key_kind": selector.key_kind,
        "key_value": selector.key_value,
    }
    if selector.kind is not None:
        result["kind"] = selector.kind
    return result


def _validate_resolution(
    result: Any,
    selector: NodeSelector,
    expected_target_id: str,
    expected_target_kind: str,
    label: str,
) -> None:
    resolution = getattr(result, "resolution", None)
    if resolution is None or getattr(resolution, "selector", None) != selector:
        raise LightOilCaptureError(f"{label} target resolution changed its selector")
    if tuple(getattr(resolution, "gaps", ())) != ():
        raise LightOilCaptureError(f"{label} target resolution contains a profile gap")
    targets = tuple(getattr(resolution, "targets", ()))
    if len(targets) != 1:
        raise LightOilCaptureError(
            f"{label} target resolution expected one exact node; found {len(targets)}"
        )
    target = targets[0]
    node = getattr(target, "node", None)
    scope = getattr(target, "scope", None)
    if (
        not isinstance(node, dict)
        or node.get("id") != expected_target_id
        or node.get("kind") != expected_target_kind
        or getattr(scope, "profile", None) != PROFILE
        or getattr(scope, "physical_side", None) != PHYSICAL_SIDE
    ):
        raise LightOilCaptureError(f"{label} resolved target identity differs")


def _compact_occurrences(
    result: Any,
    *,
    target: TargetSpec,
    direction: str,
    selector_label: str,
) -> list[dict[str, Any]]:
    page = getattr(result, "page", None)
    items = tuple(getattr(page, "items", ()))
    expected_ids = (
        target.producer_ids if direction == "producer" else target.consumer_ids
    )
    if (
        page is None
        or getattr(page, "total", None) != len(expected_ids)
        or getattr(page, "truncated", None) is not False
        or len(items) != len(expected_ids)
    ):
        raise LightOilCaptureError(
            f"{selector_label} {direction} occurrence cardinality differs"
        )

    expected_predicate = "produces" if direction == "producer" else "consumes"
    expected_kind = (
        target.producer_kind if direction == "producer" else target.consumer_kind
    )
    expected_channel = (
        target.producer_channel
        if direction == "producer"
        else target.consumer_channel
    )
    expected_quantity = dict(
        target.producer_quantity
        if direction == "producer"
        else target.consumer_quantity
    )
    compact: list[dict[str, Any]] = []
    for occurrence in items:
        owner = occurrence.get("owner") if isinstance(occurrence, dict) else None
        relationship = (
            occurrence.get("owner_relationship")
            if isinstance(occurrence, dict)
            else None
        )
        slot = occurrence.get("slot") if isinstance(occurrence, dict) else None
        alternatives = (
            occurrence.get("matched_alternatives")
            if isinstance(occurrence, dict)
            else None
        )
        attributes = slot.get("attributes") if isinstance(slot, dict) else None
        if (
            not isinstance(owner, dict)
            or owner.get("kind") != expected_kind
            or not isinstance(owner.get("id"), str)
            or not isinstance(relationship, dict)
            or relationship.get("predicate") != expected_predicate
            or relationship.get("subject") != owner.get("id")
            or not isinstance(slot, dict)
            or not isinstance(slot.get("id"), str)
            or relationship.get("object") != slot.get("id")
            or not isinstance(attributes, dict)
            or any(
                type(attributes.get(key)) is not int
                or attributes.get(key) != value
                for key, value in expected_quantity.items()
            )
            or attributes.get("channel") != expected_channel
            or not isinstance(alternatives, (list, tuple))
            or len(alternatives) != 1
            or not isinstance(alternatives[0], dict)
            or not isinstance(alternatives[0].get("node"), dict)
            or alternatives[0]["node"].get("id") != target.variant_id
        ):
            raise LightOilCaptureError(
                f"{target.label} {selector_label} {direction} owner or quantity differs"
            )
        compact.append(
            {
                "direction": direction,
                "owner_id": owner["id"],
                "owner_kind": expected_kind,
                "predicate": expected_predicate,
                "slot_id": slot["id"],
                "channel": expected_channel,
                "quantity": expected_quantity,
                "matched_variant_id": target.variant_id,
            }
        )
    compact.sort(key=lambda row: str(row["owner_id"]))
    observed_ids = tuple(str(row["owner_id"]) for row in compact)
    if observed_ids != expected_ids:
        raise LightOilCaptureError(
            f"{target.label} {selector_label} {direction} exact owner identities differ"
        )
    return compact


def _capture_selector_routes(
    domain: RuntimeGraphDomainQuery,
    target: TargetSpec,
) -> dict[str, Any]:
    scope = GraphScope.of(ProfileScope(PROFILE, PHYSICAL_SIDE))
    common_occurrences: dict[str, list[dict[str, Any]]] | None = None
    routes: list[dict[str, Any]] = []
    for label, selector, target_id, target_kind in _selectors(target):
        producers = domain.producers(
            selector,
            page=PageRequest(limit=100),
            graph_scope=scope,
        )
        consumers = domain.consumers(
            selector,
            page=PageRequest(limit=100),
            graph_scope=scope,
        )
        _validate_resolution(
            producers, selector, target_id, target_kind, f"{label} producer"
        )
        _validate_resolution(
            consumers, selector, target_id, target_kind, f"{label} consumer"
        )
        occurrences = {
            "producers": _compact_occurrences(
                producers,
                target=target,
                direction="producer",
                selector_label=label,
            ),
            "consumers": _compact_occurrences(
                consumers,
                target=target,
                direction="consumer",
                selector_label=label,
            ),
        }
        if common_occurrences is None:
            common_occurrences = occurrences
        elif occurrences != common_occurrences:
            raise LightOilCaptureError(
                "material, fluid-base, and fluid-variant selectors disagree"
            )
        occurrence_bytes = canonical_json_bytes(occurrences)
        routes.append(
            {
                "route": label,
                "selector": _selector_dict(selector),
                "resolved_target_id": target_id,
                "occurrence_sha256": hashlib.sha256(occurrence_bytes).hexdigest(),
                "producer_count": len(occurrences["producers"]),
                "consumer_count": len(occurrences["consumers"]),
            }
        )
    if common_occurrences is None:
        raise LightOilCaptureError(
            f"no {target.label} selector routes were evaluated"
        )
    return {
        "target": target.label,
        "routes": routes,
        "agreement": "exact-occurrence-agreement",
        "occurrences": common_occurrences,
    }


def _summarize_chain(chain: dict[str, Any]) -> dict[str, Any]:
    result_bytes = canonical_json_bytes(chain)
    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    counts = {
        name: len(chain.get(name, ()))
        for name in (
            "roots",
            "subproblems",
            "routes",
            "cycles",
            "unresolved_leaves",
        )
    }
    unresolved = chain.get("unresolved_leaves")
    if not isinstance(unresolved, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("reason"), str)
        for row in unresolved
    ):
        raise LightOilCaptureError("bounded chain unresolved leaves are invalid")
    reason_histogram = dict(
        sorted(Counter(str(row["reason"]) for row in unresolved).items())
    )
    truncation = chain.get("truncation")
    expected_truncation = {
        "truncated": False,
        "reasons": [],
        "limits": CHAIN_OPTIONS.limits(),
    }
    if (
        counts != EXPECTED_CHAIN_COUNTS
        or reason_histogram != EXPECTED_UNRESOLVED_REASON_HISTOGRAM
        or truncation != expected_truncation
        or len(result_bytes) != EXPECTED_CHAIN_RESULT_BYTES
        or result_sha256 != EXPECTED_CHAIN_RESULT_SHA256
    ):
        raise LightOilCaptureError(
            "bounded light-oil chain counts, reasons, truncation, or canonical digest differs"
        )
    return {
        "coverage": "bounded-untruncated-generative-source-route",
        "bounded_result_closed": True,
        "full_recipe_traversal_claim": False,
        "realized_extraction_observed": False,
        "producer_semantics": "worldgen-deposit-generation-rule",
        "selector": _selector_dict(_light_oil_traversal_selector()),
        "independently_validated": True,
        "counts": counts,
        "unresolved_reason_histogram": reason_histogram,
        "truncation": truncation,
        "canonical_result_bytes": len(result_bytes),
        "canonical_result_sha256": result_sha256,
    }


def _downstream_recipe_links(
    light_oil_capture: dict[str, Any],
    diluted_capture: dict[str, Any],
) -> dict[str, Any]:
    light_consumers = {
        row["owner_id"]
        for row in light_oil_capture["occurrences"]["consumers"]
    }
    diluted_producers = {
        row["owner_id"]
        for row in diluted_capture["occurrences"]["producers"]
    }
    expected = {BLENDER_RECIPE_ID, MIXER_RECIPE_ID}
    if light_consumers != expected or diluted_producers != expected:
        raise LightOilCaptureError(
            "light oil and diluted light oil no longer share the exact mixer/blender rows"
        )
    return {
        "relation": "recipe-consumes-light-oil-and-produces-diluted-light-oil",
        "recipe_ids": sorted(light_consumers & diluted_producers),
        "explicit_mixer_recipe_id": MIXER_RECIPE_ID,
        "input_amount_mb": LIGHT_OIL_RECIPE_AMOUNT_MB,
        "output_amount_mb": DILUTED_AMOUNT_MB,
    }


def _build_semantic(
    projection: dict[str, Any],
    light_oil_capture: dict[str, Any],
    diluted_capture: dict[str, Any],
    chain: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "status": "success",
        "snapshot_id": "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0",
        "profile_kind": "historical-exact",
        "scope": {
            "profile": PROFILE,
            "physical_side": PHYSICAL_SIDE,
        },
        "target": {
            "name": "Light Oil",
            "material_key": LIGHT_OIL_MATERIAL_KEY,
            "fluid_key": LIGHT_OIL_FLUID_KEY,
            "fluid_node_id": LIGHT_OIL_FLUID_ID,
        },
        "projection": projection,
        "light_oil_capture": light_oil_capture,
        "diluted_light_oil_downstream_checkpoint": diluted_capture,
        "downstream_recipe_links": _downstream_recipe_links(
            light_oil_capture,
            diluted_capture,
        ),
        "bounded_traversal": _summarize_chain(chain),
        "finding": "light-oil-generative-source-and-downstream-recipes-captured",
        "qualification": (
            "The producer is a worldgen deposit rule; this is not evidence of a "
            "realized deposit, extraction operation, or extracted fluid instance."
        ),
    }


def _max_rss() -> dict[str, Any]:
    unit = "bytes" if sys.platform == "darwin" else "KiB"
    return {
        "value": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "unit": unit,
        "scope": "process-high-water-mark",
    }


def _run_once(
    database: Path,
    repetition: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    phases: dict[str, dict[str, int]] = {}
    total_wall_start = time.monotonic_ns()
    total_cpu_start = time.process_time_ns()
    reader = _measure(phases, "reader_open", lambda: RuntimeGraphReader(database))
    try:
        projection = _measure(
            phases,
            "projection_identity",
            lambda: _admit_projection(reader, database),
        )
        light_oil_capture = _measure(
            phases,
            "light_oil_selector_queries",
            lambda: _capture_selector_routes(
                RuntimeGraphDomainQuery(reader), LIGHT_OIL
            ),
        )
        diluted_capture = _measure(
            phases,
            "diluted_checkpoint_queries",
            lambda: _capture_selector_routes(
                RuntimeGraphDomainQuery(reader), DILUTED_LIGHT_OIL
            ),
        )
        chain = _measure(
            phases,
            "bounded_chain",
            lambda: RuntimeGraphChainQuery(reader).chain(
                _light_oil_traversal_selector(),
                options=CHAIN_OPTIONS,
                graph_scope=GraphScope.of(
                    ProfileScope(PROFILE, PHYSICAL_SIDE)
                ),
            ),
        )
        _measure(
            phases,
            "chain_validation",
            lambda: validate_process_chain_result(chain),
        )
        semantic = _measure(
            phases,
            "semantic_assembly",
            lambda: _build_semantic(
                projection,
                light_oil_capture,
                diluted_capture,
                chain,
            ),
        )
    finally:
        _measure(phases, "reader_close", reader.close)
    phases["total"] = {
        "wall_ns": time.monotonic_ns() - total_wall_start,
        "cpu_ns": time.process_time_ns() - total_cpu_start,
    }
    measurement_class = (
        "first-reader-in-process" if repetition == 0 else "reopened-reader"
    )
    timing = {
        "repetition": repetition + 1,
        "measurement_class": measurement_class,
        "cache_qualification": (
            "not-os-cold; filesystem and OS page-cache state are uncontrolled"
        ),
        "projection_identity_cache_qualification": (
            "may-preexist-for-programmatic-caller; fresh CLI computes full SHA-256"
            if repetition == 0
            else "reuses-process-local-full-SHA-256 identity cache"
        ),
        "phases": phases,
        "max_rss": _max_rss(),
    }
    return semantic, timing


def run_experiment(
    database: Path | str,
    *,
    repetitions: int = 3,
) -> ExperimentResult:
    """Run repeated reopened-reader trials and fail on semantic instability."""

    if (
        type(repetitions) is not int
        or repetitions < 1
        or repetitions > MAX_REPETITIONS
    ):
        raise LightOilCaptureError(
            f"repetitions must be an integer from 1 through {MAX_REPETITIONS}"
        )
    database_path = Path(database)
    stable_semantic: dict[str, Any] | None = None
    stable_bytes: bytes | None = None
    stable_sha256: str | None = None
    timings: list[dict[str, Any]] = []
    for repetition in range(repetitions):
        semantic, timing = _run_once(database_path, repetition)
        semantic_bytes = canonical_json_bytes(semantic)
        semantic_sha256 = hashlib.sha256(semantic_bytes).hexdigest()
        timing["semantic_sha256"] = semantic_sha256
        if stable_bytes is None:
            stable_semantic = semantic
            stable_bytes = semantic_bytes
            stable_sha256 = semantic_sha256
        elif semantic_bytes != stable_bytes or semantic_sha256 != stable_sha256:
            raise LightOilCaptureError(
                "reopened-reader repetitions produced different timing-free semantics"
            )
        timings.append(timing)
    if stable_semantic is None or stable_bytes is None or stable_sha256 is None:
        raise AssertionError("validated repetitions produced no semantic result")
    # Keep the public semantic view and serialized wrapper independent.  The
    # write seam still revalidates both snapshots because nested dictionaries
    # remain intentionally convenient for callers to inspect.
    result_semantic = copy.deepcopy(stable_semantic)
    output_document = {
        "format": OUTPUT_FORMAT,
        "semantic_sha256": stable_sha256,
        "semantic": copy.deepcopy(stable_semantic),
    }
    timing_document = {
        "format": TIMING_FORMAT,
        "semantic_sha256": stable_sha256,
        "measurement_note": (
            "The CLI starts a fresh process; its first-reader-in-process and "
            "reopened-reader measurements are not OS-cold benchmarks. "
            "The fresh CLI's first identity phase computes the full projection "
            "SHA-256; later repetitions reuse the process-local identity cache. "
            "Programmatic callers can inherit prior process cache state. Timing "
            "and RSS are excluded from semantic identity."
        ),
        "repetitions": timings,
    }
    return ExperimentResult(
        semantic=result_semantic,
        semantic_bytes=stable_bytes,
        semantic_sha256=stable_sha256,
        output_document=output_document,
        timing_document=timing_document,
    )


def _guard_output_path(path: Path | str) -> Path:
    storage_root = WORKBENCH_STORAGE_ROOT.resolve(strict=False)
    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate == storage_root:
        raise LightOilCaptureError("output must name a JSON file below .workbench")
    try:
        candidate.relative_to(storage_root)
    except ValueError as exc:
        raise LightOilCaptureError(
            "experiment output must resolve below the repository .workbench directory"
        ) from exc
    if candidate.suffix != ".json":
        raise LightOilCaptureError("experiment output must have a .json suffix")
    if candidate.exists() and not candidate.is_file():
        raise LightOilCaptureError("experiment output is not a regular file")
    return candidate


def timing_sidecar_path(output: Path) -> Path:
    return output.with_name(output.stem + ".timings.json")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _canonical_snapshot_bytes(value: Any, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError, RecursionError, MemoryError) as exc:
        raise LightOilCaptureError(
            f"experiment {label} is not bounded canonical JSON: {exc}"
        ) from exc


def write_result(result: ExperimentResult, output: Path | str) -> tuple[Path, Path]:
    """Snapshot the pair, write timing, then atomically commit semantics."""

    if not isinstance(result, ExperimentResult):
        raise LightOilCaptureError(
            "experiment output requires an ExperimentResult snapshot"
        )
    output_path = _guard_output_path(output)
    sidecar = timing_sidecar_path(output_path)

    # Snapshot every mutable public container before the first filesystem
    # change.  The frozen dataclass prevents field replacement, not mutation of
    # nested dictionaries, so recompute every semantic identity and wrapper
    # invariant from these exact bytes immediately before writing.
    try:
        semantic_snapshot = copy.deepcopy(result.semantic)
        output_snapshot = copy.deepcopy(result.output_document)
        timing_snapshot = copy.deepcopy(result.timing_document)
    except (TypeError, ValueError, RuntimeError, MemoryError) as exc:
        raise LightOilCaptureError(
            f"cannot snapshot mutable experiment output: {exc}"
        ) from exc

    semantic_payload = _canonical_snapshot_bytes(
        semantic_snapshot,
        "semantic snapshot",
    )
    semantic_sha256 = hashlib.sha256(semantic_payload).hexdigest()
    if (
        semantic_payload != result.semantic_bytes
        or semantic_sha256 != result.semantic_sha256
    ):
        raise LightOilCaptureError(
            "experiment semantic snapshot differs from its canonical bytes or SHA-256"
        )
    expected_output_document = {
        "format": OUTPUT_FORMAT,
        "semantic_sha256": semantic_sha256,
        "semantic": semantic_snapshot,
    }
    output_payload = _canonical_snapshot_bytes(output_snapshot, "output wrapper")
    if output_payload != _canonical_snapshot_bytes(
        expected_output_document,
        "expected output wrapper",
    ):
        raise LightOilCaptureError(
            "experiment output wrapper differs from its semantic snapshot"
        )
    if (
        not isinstance(timing_snapshot, dict)
        or timing_snapshot.get("format") != TIMING_FORMAT
        or timing_snapshot.get("semantic_sha256") != semantic_sha256
    ):
        raise LightOilCaptureError(
            "experiment timing sidecar differs from its semantic snapshot"
        )
    timing_payload = _canonical_snapshot_bytes(timing_snapshot, "timing sidecar")

    _atomic_write(sidecar, timing_payload)
    _atomic_write(output_path, output_payload)
    return output_path, sidecar


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the exact historical Light Oil graph trial."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="explicit JSON output path below this repository's .workbench directory",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    arguments = parser.parse_args(argv)
    try:
        output = _guard_output_path(arguments.output)
        result = run_experiment(
            arguments.database,
            repetitions=arguments.repetitions,
        )
        output, sidecar = write_result(result, output)
    except LightOilCaptureError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "output": str(output),
                "timing_sidecar": str(sidecar),
                "semantic_sha256": result.semantic_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
