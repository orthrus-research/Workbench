"""Supersymmetry-only adapter for Atlas semantic regression projections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from workbench_atlas_projection import build_projection, canonical_semantic_identity
from workbench_crucible_stage_snapshot import (
    build_stage_snapshot,
    effect_observation,
    registry_observation,
)
from workbench_pack_program_studio import build_source_declarations, source_declaration


PROFILE_API_VERSION = 1
SEMANTIC_PROJECTION_API_VERSION = 1


PACK_PROFILE_ID = "workbench-pack:supersymmetry"
HISTORICAL_PLATFORM_PROFILE_ID = "workbench-platform:legacy-forge:forge-14.23.5.2860"


class SupersymmetrySemanticFixtureError(ValueError):
    """A profile-owned regression fixture is malformed."""


def _projection_root(root: Path) -> Path:
    return root / "profiles/packs/supersymmetry/atlas/semantic-projection"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SupersymmetrySemanticFixtureError(f"JSON root must be an object: {path}")
    return value


def load_adapter(root: Path) -> dict[str, Any]:
    return _json(_projection_root(root) / "adapter-v1.json")


def load_fixture(root: Path, name: str) -> dict[str, Any]:
    manifest = _json(_projection_root(root) / "fixtures/manifest.json")
    entries = {row["name"]: row for row in manifest.get("fixtures", [])}
    if name not in entries:
        raise SupersymmetrySemanticFixtureError(f"unknown Supersymmetry semantic fixture: {name}")
    value = _json(_projection_root(root) / "fixtures" / entries[name]["path"])
    _validate_fixture(value, expected_name=name)
    return value


def build_fixture_projection(root: Path, name: str) -> dict[str, Any]:
    fixture = load_fixture(root, name)
    descriptors = _descriptors(fixture["declarations"])
    declarations = []
    for row in fixture["declarations"]:
        descriptor = descriptors[row["alias"]]
        attributes = _expand_attributes(row["attributes"], descriptors)
        declarations.append(
            source_declaration(
                semantic_descriptor=descriptor,
                attributes=attributes,
                lifecycle=row["lifecycle"],
                provenance={
                    "authority": "Pack Program Studio",
                    "fixture": name,
                    **row["provenance"],
                },
            )
        )
    replay_ref = fixture["historical_evidence"]["replay_ref"]
    feed = build_source_declarations(
        program_id=f"workbench-supersymmetry-history:{name}:{replay_ref}",
        pack_profile_id=PACK_PROFILE_ID,
        platform_profile_id=HISTORICAL_PLATFORM_PROFILE_ID,
        source_sha256=hashlib.sha256(str(replay_ref).encode("utf-8")).hexdigest(),
        declarations=declarations,
        source_kind="supersymmetry-historical-regression-fixture",
    )
    stage = fixture["runtime"]["stage"]
    registry = [
        registry_observation(
            stage=stage,
            semantic_descriptor=descriptors[row["descriptor_alias"]],
            registry_state=row["state"],
            provenance={"fixture": name, **row["provenance"]},
        )
        for row in fixture["runtime"]["registry"]
    ]
    effects = [
        effect_observation(
            stage=stage,
            semantic_descriptor=descriptors[row["descriptor_alias"]],
            operation=row["operation"],
            effect_state=row["state"],
            provenance={"fixture": name, **row["provenance"]},
        )
        for row in fixture["runtime"]["effects"]
    ]
    snapshot = build_stage_snapshot(
        pack_profile_id=PACK_PROFILE_ID,
        platform_profile_id=HISTORICAL_PLATFORM_PROFILE_ID,
        stage=stage,
        registry=registry,
        effects=effects,
        capture=fixture["runtime"]["capture"],
    )
    return build_projection(feed, snapshot, load_adapter(root))


def run_acceptance_gate(
    root: Path,
    *,
    fixture_names: Sequence[str] | None = None,
    include_next: bool = False,
) -> dict[str, Any]:
    manifest = _json(_projection_root(root) / "fixtures/manifest.json")
    if fixture_names is None:
        selected = [
            row["name"]
            for row in manifest["fixtures"]
            if row["gate"] == "first-acceptance" or include_next
        ]
    else:
        selected = list(fixture_names)
    rows = []
    for name in selected:
        fixture = load_fixture(root, name)
        projection = build_fixture_projection(root, name)
        expected_codes = sorted(fixture["expected"]["diagnostic_codes"])
        actual_codes = sorted(row["code"] for row in projection["diagnostics"])
        expected_semantics = sorted(
            canonical_semantic_identity(_descriptors(fixture["declarations"])[alias])
            for alias in fixture["expected"]["failed_aliases"]
        )
        actual_semantics = sorted({row["semantic_id"] for row in projection["diagnostics"]})
        status = (
            "pass"
            if expected_codes == actual_codes and expected_semantics == actual_semantics
            else "fail"
        )
        rows.append(
            {
                "fixture": name,
                "status": status,
                "projection_id": projection["projection_id"],
                "expected_diagnostic_codes": expected_codes,
                "actual_diagnostic_codes": actual_codes,
                "expected_failed_semantic_ids": expected_semantics,
                "actual_failed_semantic_ids": actual_semantics,
                "historical_evidence": fixture["historical_evidence"],
            }
        )
    return {
        "format": "workbench-supersymmetry-semantic-acceptance-v1",
        "profile": PACK_PROFILE_ID,
        "fixtures": rows,
        "summary": {
            "status": "pass" if rows and all(row["status"] == "pass" for row in rows) else "fail",
            "fixtures": len(rows),
            "passed": sum(row["status"] == "pass" for row in rows),
            "failed": sum(row["status"] != "pass" for row in rows),
        },
    }


def _descriptors(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    unresolved = list(rows)
    while unresolved:
        progress = False
        retained = []
        for row in unresolved:
            alias = row["alias"]
            if "descriptor" in row:
                result[alias] = row["descriptor"]
                progress = True
            elif row.get("descriptor_alias") in result:
                result[alias] = result[row["descriptor_alias"]]
                progress = True
            else:
                retained.append(row)
        if not progress:
            raise SupersymmetrySemanticFixtureError("fixture descriptor aliases contain a cycle or unknown target")
        unresolved = retained
    return result


def _expand_attributes(value: Mapping[str, Any], descriptors: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    result = dict(value)
    if "requirements" in result:
        result["requirements"] = [
            {"semantic_descriptor": descriptors[row["semantic_alias"]]}
            for row in result["requirements"]
        ]
    machine = result.get("machine_feasibility")
    if isinstance(machine, Mapping):
        machine_value = dict(machine)
        aliases = machine_value.pop("candidate_machine_aliases", [])
        machine_value["candidate_machine_descriptors"] = [descriptors[alias] for alias in aliases]
        result["machine_feasibility"] = machine_value
    return result


def _validate_fixture(value: Mapping[str, Any], *, expected_name: str) -> None:
    required = {
        "format",
        "schema_version",
        "fixture",
        "historical_evidence",
        "declarations",
        "runtime",
        "expected",
    }
    if set(value) != required:
        raise SupersymmetrySemanticFixtureError("semantic fixture has unexpected keys")
    if value["format"] != "workbench-supersymmetry-semantic-regression-fixture-v1":
        raise SupersymmetrySemanticFixtureError("unsupported semantic fixture format")
    if value["schema_version"] != 1 or value["fixture"] != expected_name:
        raise SupersymmetrySemanticFixtureError("semantic fixture identity does not match manifest")
    declarations = value["declarations"]
    if not isinstance(declarations, list) or not declarations:
        raise SupersymmetrySemanticFixtureError("semantic fixture declarations are missing")
    aliases = [row.get("alias") for row in declarations]
    if None in aliases or len(aliases) != len(set(aliases)):
        raise SupersymmetrySemanticFixtureError("semantic fixture declaration aliases are invalid")
    _descriptors(declarations)
    runtime = value["runtime"]
    if not isinstance(runtime, Mapping) or not isinstance(runtime.get("stage"), str):
        raise SupersymmetrySemanticFixtureError("semantic fixture runtime stage is missing")


__all__ = [
    "build_fixture_projection",
    "load_adapter",
    "load_fixture",
    "run_acceptance_gate",
]
