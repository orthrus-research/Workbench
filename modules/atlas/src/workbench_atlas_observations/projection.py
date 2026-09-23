"""Project an explicitly admitted retained initialization reader into Atlas.

Core and the producer admit and lease the original snapshot. This consumer never
opens a producer's files, starts a native worker, or promotes capture coverage.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile

from workbench_atlas_categorical_graph import (
    CategoricalGraphBundleBuilder, validate_bundle_directory,
)

PROJECTION_FORMAT = "workbench-atlas-initialization-projection-v1"


class ObservationProjectionError(ValueError):
    """Original observation scope or projection custody is unavailable."""


def _check_reader(reader, side):
    manifest = reader.manifest
    if not reader.scope_supported or reader.unsupported_sections:
        raise ObservationProjectionError("retained observation scope or schemas are unsupported")
    if manifest.get("format") != "workbench-check-snapshot-v1":
        raise ObservationProjectionError("unsupported retained snapshot format")
    if manifest.get("producer", {}).get("id") != "axiom":
        raise ObservationProjectionError("initialization adapter requires Axiom observations")
    reports = [row for row in manifest["sections"] if row["id"] == "report"]
    if (len(reports) != 1 or reports[0]["state"] != "observed"
            or reports[0]["schema"] != "axiom-retained-report-v1"):
        raise ObservationProjectionError("the original supported report is unavailable")
    paired = reader.request.get("baseline") is not None
    if side not in ({"baseline", "candidate"} if paired else {"single"}):
        raise ObservationProjectionError("select single for an ordinary snapshot or an explicit paired side")
    return manifest


def _code_binding():
    from . import families
    from workbench_atlas_categorical_graph import bundle
    paths = [Path(__file__), Path(families.__file__), Path(bundle.__file__)]
    return {path.name: sha256(path.read_bytes()).hexdigest() for path in paths}


def project_retained_observations(reader, output, *, side="single",
                                  profile_id, check_cancelled=None):
    """Publish one exact selected side; incomplete native state stays incomplete."""
    from .families import build_initialization_plan

    check = check_cancelled or (lambda: None)
    check()
    manifest = _check_reader(reader, side)
    code = _code_binding()
    output = Path(output)
    if output.is_symlink():
        raise ObservationProjectionError("observation graph output already exists")
    output = output.resolve()
    if output.exists() or output.is_symlink():
        raise ObservationProjectionError("observation graph output already exists")
    report = reader.read_record("report", "value")
    if not isinstance(report, dict) or report.get("id") != manifest["result_id"]:
        raise ObservationProjectionError("retained report identity differs from snapshot")
    check()
    plan = build_initialization_plan(report, snapshot_id=manifest["id"], side=side,
                                     check_cancelled=check)
    source_context = reader.request["inputs"]["context"]
    scope = {
        "observation_contract": PROJECTION_FORMAT,
        "profile_id": profile_id,
        "selected_side": side,
        "initialization_context": source_context,
        "family_coverage": [],
        "meaning": "retained initialization observations; no game or machine execution",
    }
    binding = {
        "snapshot_id": manifest["id"], "request_id": manifest["request_id"],
        "result_id": manifest["result_id"], "producer": manifest["producer"],
        "scope_id": manifest["scope_id"], "bindings": manifest["bindings"],
        "source_result": manifest["source_result"],
        "retained_inputs": manifest["retained_inputs"],
        "native_outcome": manifest["native_outcome"], "coverage": manifest["coverage"],
        "sections": manifest["sections"], "projection_sources": code,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".atlas-observations-", dir=output.parent))
    try:
        staged = temporary / "graph"
        builder = CategoricalGraphBundleBuilder(staged, scope=scope, evidence_binding=binding,
            evidence_authority="retained-observations-v1", check_cancelled=check)
        builder.add_partition("initialization", classification="original retained initialization observations",
            dependencies=(), nodes=plan.iter_nodes(), edges=plan.iter_edges(),
            evidence_categories=("report",), limitations=plan.limitations)
        # Families may count their streamed rows as they are emitted.
        coverage = plan.coverage
        builder.scope["family_coverage"] = coverage
        graph = builder.close()
        check()
        validate_bundle_directory(staged, check_cancelled=check)
        if _code_binding() != code:
            raise ObservationProjectionError("projection implementation changed during import")
        check()
        if output.exists() or output.is_symlink():
            raise ObservationProjectionError("observation graph output appeared during import")
        os.rename(staged, output)
        return {"format": PROJECTION_FORMAT, "schema_version": 1, "state": "complete",
            "root": str(output), "graph_set_id": graph["graph_set_id"],
            "snapshot_id": manifest["id"], "selected_side": side,
            "native_outcome": manifest["native_outcome"],
            "capture_coverage": manifest["coverage"], "family_coverage": coverage,
            "summary": graph["summary"], "limitations": list(plan.limitations)}
    finally:
        # Only this invocation's unpublished staging directory is task-owned.
        shutil.rmtree(temporary)


def resolve_json_pointer(value, pointer):
    """Resolve an exact RFC 6901 address; no filesystem path interpretation."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ObservationProjectionError("invalid original observation pointer")
    if not pointer:
        return value
    for raw in pointer[1:].split("/"):
        position = 0
        while position < len(raw):
            if raw[position] == "~":
                if position + 1 >= len(raw) or raw[position + 1] not in "01":
                    raise ObservationProjectionError("invalid JSON pointer escape")
                position += 1
            position += 1
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isascii() or not key.isdigit() or str(int(key)) != key:
                raise ObservationProjectionError("invalid original array position")
            try:
                value = value[int(key)]
            except IndexError as exc:
                raise ObservationProjectionError("original observation position is unavailable") from exc
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ObservationProjectionError("original observation pointer is unavailable")
    return value


def resolve_retained_evidence(reader, references, *, check_cancelled=None):
    """Resolve original values only from the exact re-admitted source snapshot."""
    check = check_cancelled or (lambda: None)
    if not isinstance(references, (list, tuple)) or not references:
        raise ObservationProjectionError("select original observation references")
    if not reader.scope_supported or reader.unsupported_sections:
        raise ObservationProjectionError("retained observation scope or schemas are unsupported")
    report = None
    rows = []
    for reference in references:
        check()
        if (not isinstance(reference, dict)
                or reference.get("snapshot_id") != reader.manifest["id"]
                or reference.get("section") != "report"
                or reference.get("record_key") != "value"):
            raise ObservationProjectionError("evidence reference names a different snapshot or record")
        if report is None:
            report = reader.read_record("report", "value")
        original = resolve_json_pointer(report, reference.get("json_pointer"))
        encoded = json.dumps(original, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
        rows.append({"reference": reference, "value": original,
                     "canonical_value_sha256": sha256(encoded).hexdigest()})
    check()
    return {"format": "workbench-atlas-resolved-observation-evidence-v1", "schema_version": 1,
            "state": "resolved", "snapshot_id": reader.manifest["id"],
            "source_result": reader.manifest["source_result"], "records": rows}
