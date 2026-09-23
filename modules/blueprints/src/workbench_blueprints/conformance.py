#!/usr/bin/env python3

"""Independent BLUEPRINTS-M1-V01 whole-engine conformance proof."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from workbench_blueprints import standards
from workbench_blueprints.layout import SCHEMA_ROOT


CONTRACT_ID = "BLUEPRINTS-EXECUTABLE-ENGINE-V1"
PROOF_FORMAT = "workbench-blueprints-engine-conformance-proof-v1"
PROOF_PREFIX = "workbench-blueprints-engine-conformance-proof:sha256:"
PROOF_SCHEMA = SCHEMA_ROOT / "blueprints-engine-conformance-proof-v1.schema.json"
SYNTHETIC_SCOPE = "synthetic-non-authoritative"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


SCENARIO_CATALOG: dict[str, dict[str, tuple[str, ...]]] = {
    "candidate-invalidation": {
        "states": ("planned", "invalidated"),
        "diagnostics": ("BPA134_INVALIDATED_INPUT",),
        "assertions": (
            "downstream-identities-bound",
            "generation-advanced",
            "release-withheld",
        ),
    },
    "direct-apply-verification": {
        "states": (
            "initialized",
            "planned",
            "simulated",
            "released",
            "applied",
            "verified",
        ),
        "diagnostics": (),
        "assertions": (
            "all-eight-core-phases",
            "history-retained",
            "private-payloads-excluded",
            "target-verified",
        ),
    },
    "instructions-release": {
        "states": ("initialized", "planned", "simulated", "released"),
        "diagnostics": (),
        "assertions": (
            "portable-proof-exported",
            "release-validated",
            "target-unchanged",
        ),
    },
    "output-mode-agreement": {
        "states": ("released",),
        "diagnostics": (),
        "assertions": (
            "all-output-modes-exercised",
            "instructions-patch-direct-same-operations",
        ),
    },
    "patch-bundle-release": {
        "states": ("initialized", "planned", "simulated", "released"),
        "diagnostics": (),
        "assertions": (
            "portable-proof-exported",
            "release-validated",
            "target-unchanged",
        ),
    },
    "rollback-recovery": {
        "states": (
            "initialized",
            "planned",
            "simulated",
            "released",
            "application-rejected",
        ),
        "diagnostics": ("BPA144_APPLICATION_ROLLED_BACK",),
        "assertions": (
            "partial-mutation-injected",
            "rollback-succeeded",
            "target-restored",
            "transaction-closed",
        ),
    },
    "sealed-candidate-boundary": {
        "states": ("initialized", "planned"),
        "diagnostics": (),
        "assertions": (
            "candidate-metadata-closed",
            "candidate-payload-not-disclosed",
            "sealed-locator-content-addressed",
        ),
    },
    "simulation-gate-failure": {
        "states": ("initialized", "planned", "simulation-failed"),
        "diagnostics": ("BPI131_SIMULATION_FAILED",),
        "assertions": (
            "later-lifecycle-rejected",
            "no-release",
            "required-gate-failed",
        ),
    },
    "stale-target-rejection": {
        "states": (
            "initialized",
            "planned",
            "simulated",
            "released",
            "application-rejected",
        ),
        "diagnostics": ("BPA138_STALE_APPLICATION_TARGET",),
        "assertions": (
            "candidate-not-applied",
            "observed-drift-preserved",
            "stale-target-detected",
        ),
    },
    "standard-selection-failure": {
        "states": ("initialized",),
        "diagnostics": ("BPI130_PLAN_BLOCKED",),
        "assertions": (
            "no-candidate",
            "no-release",
            "no-standard-no-plan",
        ),
    },
}


class ConformanceValidationError(ValueError):
    """Raised when a claimed V01 proof is not closed by exact observations."""


def _canonical_bytes(value: Any) -> bytes:
    return standards.canonical_json(value).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConformanceValidationError(message)


def scenario_observation(
    scenario_id: str,
    *,
    states: Sequence[str],
    diagnostic_codes: Sequence[str],
    assertions: Sequence[str],
) -> dict[str, Any]:
    """Close one already-executed scenario into deterministic semantic evidence."""

    material = {
        "scenario_id": scenario_id,
        "status": "passed",
        "states": list(states),
        "diagnostic_codes": list(diagnostic_codes),
        "assertions": list(assertions),
    }
    _validate_scenario(material)
    return {**material, "evidence_sha256": _digest(material)}


def _validate_scenario(value: Mapping[str, Any]) -> None:
    _require(
        set(value) == {
            "scenario_id",
            "status",
            "states",
            "diagnostic_codes",
            "assertions",
        },
        "conformance scenario has undeclared fields",
    )
    scenario_id = value.get("scenario_id")
    _require(
        isinstance(scenario_id, str) and scenario_id in SCENARIO_CATALOG,
        "conformance scenario ID is not admitted",
    )
    expected = SCENARIO_CATALOG[scenario_id]
    _require(value.get("status") == "passed", f"{scenario_id} did not pass")
    _require(
        tuple(value.get("states", ())) == expected["states"],
        f"{scenario_id} state closure differs",
    )
    _require(
        tuple(value.get("diagnostic_codes", ())) == expected["diagnostics"],
        f"{scenario_id} diagnostic closure differs",
    )
    _require(
        tuple(value.get("assertions", ())) == expected["assertions"],
        f"{scenario_id} assertion closure differs",
    )


def build_engine_conformance_proof(
    scenarios: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and independently revalidate the closed V01 proof."""

    normalized = [deepcopy(dict(row)) for row in scenarios]
    _require(
        [row.get("scenario_id") for row in normalized]
        == sorted(SCENARIO_CATALOG),
        "conformance proof does not contain the exact canonical scenario set",
    )
    for row in normalized:
        evidence_sha256 = row.pop("evidence_sha256", None)
        _validate_scenario(row)
        _require(
            isinstance(evidence_sha256, str)
            and _SHA256_RE.fullmatch(evidence_sha256) is not None
            and evidence_sha256 == _digest(row),
            f"{row['scenario_id']} evidence identity differs",
        )
        row["evidence_sha256"] = evidence_sha256
    material = {
        "schema_version": 1,
        "format": PROOF_FORMAT,
        "contract_id": CONTRACT_ID,
        "fixture_scope": SYNTHETIC_SCOPE,
        "scenarios": normalized,
        "claims": [
            "generic-engine-state-machine-conforms",
            "required-failures-withhold-release",
            "three-output-modes-share-one-operation-identity",
        ],
        "limitations": [
            "no-feature-standard-admitted",
            "no-minecraft-runtime-claim",
            "no-pack-pattern-authorized",
        ],
    }
    proof = {
        **material,
        "proof_id": PROOF_PREFIX + _digest(material),
    }
    return parse_engine_conformance_proof(proof)


def parse_engine_conformance_proof(value: Any) -> dict[str, Any]:
    """Validate schema, identities, exact scenario semantics, and limitations."""

    try:
        schema = json.loads(PROOF_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConformanceValidationError(f"cannot load conformance schema: {exc}") from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        raise ConformanceValidationError(
            f"conformance proof schema error at {list(error.absolute_path)}: {error.message}"
        )
    proof = deepcopy(dict(value))
    material = dict(proof)
    proof_id = material.pop("proof_id")
    _require(
        proof_id == PROOF_PREFIX + _digest(material),
        "conformance proof identity differs",
    )
    _require(proof["fixture_scope"] == SYNTHETIC_SCOPE, "fixture scope is authoritative")
    _require(
        proof["claims"]
        == [
            "generic-engine-state-machine-conforms",
            "required-failures-withhold-release",
            "three-output-modes-share-one-operation-identity",
        ],
        "conformance proof claims differ",
    )
    _require(
        proof["limitations"]
        == [
            "no-feature-standard-admitted",
            "no-minecraft-runtime-claim",
            "no-pack-pattern-authorized",
        ],
        "conformance proof limitations differ",
    )
    scenarios = proof["scenarios"]
    _require(
        [row["scenario_id"] for row in scenarios] == sorted(SCENARIO_CATALOG),
        "conformance proof scenario set differs",
    )
    for row in scenarios:
        material_row = dict(row)
        evidence_sha256 = material_row.pop("evidence_sha256")
        _validate_scenario(material_row)
        _require(
            evidence_sha256 == _digest(material_row),
            f"{row['scenario_id']} evidence identity differs",
        )
    return proof


def write_engine_conformance_proof(path: Path, proof: Mapping[str, Any]) -> None:
    """Write a validated proof without treating the fixture as product authority."""

    validated = parse_engine_conformance_proof(proof)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(validated) + b"\n")
