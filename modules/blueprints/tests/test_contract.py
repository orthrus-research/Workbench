#!/usr/bin/env python3

"""Focused C01 tests for the Blueprints executable engine contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import unittest

from jsonschema import Draft202012Validator

from _support import CONTRACT_ROOT, EXAMPLE_ROOT, SCHEMA_ROOT

CONTRACT_PATH = CONTRACT_ROOT / "executable-engine-v1.md"
EXAMPLES_PATH = EXAMPLE_ROOT / "executable-engine-examples-v1.json"
SCHEMA_PATHS = {
    "request": SCHEMA_ROOT / "blueprints-request-v1.schema.json",
    "plan": SCHEMA_ROOT / "blueprints-plan-v1.schema.json",
    "run": SCHEMA_ROOT / "blueprints-run-v1.schema.json",
    "proof": SCHEMA_ROOT / "blueprints-proof-v1.schema.json",
}

CONTRACT_ID = "BLUEPRINTS-EXECUTABLE-ENGINE-V1"
IDENTITIES = {
    "request": ("request_id", "blueprints-request:sha256:"),
    "plan": ("plan_id", "blueprints-plan:sha256:"),
    "candidate": ("candidate_id", "blueprints-candidate:sha256:"),
    "simulation": ("simulation_id", "blueprints-simulation:sha256:"),
    "release": ("release_id", "blueprints-release:sha256:"),
    "patch": ("patch_id", "blueprints-patch:sha256:"),
    "application": ("application_id", "blueprints-application:sha256:"),
    "verification": (
        "verification_id",
        "blueprints-verification:sha256:",
    ),
    "run": ("run_id", "blueprints-run:sha256:"),
    "proof": ("proof_id", "blueprints-proof:sha256:"),
    "target": ("target_state_id", "blueprints-target-state:sha256:"),
}

def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _identity(kind: str, value: dict[str, Any]) -> str:
    field, prefix = IDENTITIES[kind]
    projected = copy.deepcopy(value)
    projected.pop(field, None)
    return prefix + _sha256(projected)


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise AssertionError(f"mutation path is not a JSON pointer: {pointer}")
    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    ]


def _apply_mutation(document: dict[str, Any], mutation: dict[str, Any]) -> None:
    parts = _pointer_parts(mutation["path"])
    parent: Any = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    operation = mutation["op"]
    if isinstance(parent, list):
        if operation == "add" and leaf == "-":
            parent.append(copy.deepcopy(mutation["value"]))
        elif operation == "remove":
            parent.pop(int(leaf))
        elif operation == "replace":
            parent[int(leaf)] = copy.deepcopy(mutation["value"])
        else:
            raise AssertionError(f"unsupported list mutation: {mutation}")
    elif operation == "remove":
        del parent[leaf]
    elif operation in {"add", "replace"}:
        parent[leaf] = copy.deepcopy(mutation["value"])
    else:
        raise AssertionError(f"unsupported object mutation: {mutation}")


def _schema_errors(
    bundle: dict[str, Any],
    schemas: dict[str, dict[str, Any]],
) -> set[str]:
    errors: set[str] = set()
    for name, schema in schemas.items():
        if next(Draft202012Validator(schema).iter_errors(bundle[name]), None):
            errors.add(f"schema:{name}")
    return errors


def _normalized_relative_path(value: str, *, directory: bool) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    if directory != value.endswith("/"):
        return False
    stripped = value[:-1] if directory else value
    parts = stripped.split("/")
    return bool(parts) and not any(
        part in {"", ".", "..", ".git"} for part in parts
    )


def _semantic_errors(bundle: dict[str, Any]) -> set[str]:
    errors: set[str] = set()
    request = bundle["request"]
    plan = bundle["plan"]
    run = bundle["run"]
    proof = bundle["proof"]

    if request.get("contract_id") != CONTRACT_ID:
        errors.add("contract-id")
    target = request.get("target", {})
    if target.get("target_state_id") != _identity("target", target):
        errors.add("target-state-id")
    if request.get("request_id") != _identity("request", request):
        errors.add("request-id")
    parameter_names = [row.get("name") for row in request.get("parameters", [])]
    if parameter_names != sorted(parameter_names) or len(parameter_names) != len(
        set(parameter_names)
    ):
        errors.add("request-parameter-order")
    variants = request.get("requested_variants", [])
    if variants != sorted(variants):
        errors.add("request-variant-order")
    if (
        request.get("output_mode") == "direct-apply"
        and not request.get("consent", {}).get("allow_direct_apply")
    ):
        errors.add("direct-apply-consent")

    standards = plan.get("standards", {})
    primary = standards.get("primary")
    if not isinstance(primary, dict):
        errors.add("missing-standard")
        primary_ids: list[str] = []
        if any(
            run.get(field) is not None
            for field in (
                "candidate",
                "simulation",
                "release",
                "application",
                "verification",
            )
        ):
            errors.add("release-without-standard")
    else:
        expected_standard_id = (
            "blueprints-standard:sha256:" + primary.get("standard_sha256", "")
        )
        if primary.get("standard_id") != expected_standard_id:
            errors.add("standard-id")
        components = standards.get("components", [])
        primary_ids = [primary.get("standard_id")] + [
            row.get("standard_id") for row in components
        ]
        for component in components:
            expected = (
                "blueprints-standard:sha256:"
                + component.get("standard_sha256", "")
            )
            if component.get("standard_id") != expected:
                errors.add("standard-id")
        component_ids = [
            component.get("standard_id") for component in components
        ]
        if component_ids != sorted(component_ids) or len(
            component_ids
        ) != len(set(component_ids)):
            errors.add("component-standard-order")
    if plan.get("request_id") != request.get("request_id"):
        errors.add("plan-request-binding")
    if plan.get("target_state_id") != target.get("target_state_id"):
        errors.add("plan-target-binding")
    if plan.get("plan_id") != _identity("plan", plan):
        errors.add("plan-id")
    selection = plan.get("selection", {})
    for field in ("variant_ids", "rationale_codes"):
        values = selection.get(field, [])
        if values != sorted(values) or len(values) != len(set(values)):
            errors.add("selection-order")
    effective_parameters = plan.get("effective_parameters", [])
    effective_names = [
        parameter.get("name") for parameter in effective_parameters
    ]
    if effective_names != sorted(effective_names) or len(
        effective_names
    ) != len(set(effective_names)):
        errors.add("effective-parameter-order")

    authorized = plan.get("authorized_paths", [])
    if authorized != sorted(authorized) or any(
        not _normalized_relative_path(path, directory=True)
        for path in authorized
    ):
        errors.add("authorized-path")
    operations = plan.get("operations", [])
    operation_paths = [row.get("path", "") for row in operations]
    if (
        [row.get("ordinal") for row in operations]
        != list(range(len(operations)))
        or operation_paths != sorted(operation_paths)
        or len(operation_paths) != len(set(operation_paths))
    ):
        errors.add("operation-order")
    for operation in operations:
        path = operation.get("path", "")
        if (
            not _normalized_relative_path(path, directory=False)
            or not any(path.startswith(prefix) for prefix in authorized)
        ):
            errors.add("operation-path")
        if (
            operation.get("operation") == "delete"
            and operation.get("content_sha256") is not None
        ) or (
            operation.get("operation") in {"create", "update"}
            and operation.get("content_sha256") is None
        ):
            errors.add("operation-content")
    stages = plan.get("validation_stages", [])
    if [row.get("ordinal") for row in stages] != list(range(len(stages))):
        errors.add("stage-order")
    if plan.get("status") == "ready":
        invariant_results = plan.get("atlas", {}).get("invariant_results", [])
        if (
            plan.get("blocked_reasons")
            or selection.get("developer_choice_required")
            or plan.get("atlas", {}).get("relevant_drift") != "none"
            or any(row.get("status") != "pass" for row in invariant_results)
            or any(
                not row.get("accepted") for row in effective_parameters
            )
        ):
            errors.add("ready-plan-closure")

    if run.get("request_id") != request.get("request_id"):
        errors.add("run-request-binding")
    if run.get("plan_id") != plan.get("plan_id"):
        errors.add("run-plan-binding")
    if run.get("target_state_id") != target.get("target_state_id"):
        errors.add("run-target-binding")
    if run.get("output_mode") != request.get("output_mode"):
        errors.add("run-output-mode")

    candidate = run.get("candidate")
    simulation = run.get("simulation")
    release = run.get("release")
    application = run.get("application")
    verification = run.get("verification")
    operation_sha256 = _sha256(operations)

    if isinstance(candidate, dict):
        if candidate.get("candidate_id") != _identity("candidate", candidate):
            errors.add("candidate-id")
        if (
            candidate.get("plan_id") != plan.get("plan_id")
            or candidate.get("target_state_id") != target.get("target_state_id")
            or candidate.get("standard_ids") != primary_ids
            or candidate.get("content_manifest_sha256") != operation_sha256
        ):
            errors.add("candidate-binding")
        if candidate.get("edit_generation", 0) > 0 and any(
            value is not None
            for value in (simulation, release, application, verification)
        ):
            if not run.get("invalidation", {}).get("invalidated"):
                errors.add("downstream-not-invalidated")

    if isinstance(simulation, dict):
        if simulation.get("simulation_id") != _identity(
            "simulation", simulation
        ):
            errors.add("simulation-id")
        if not isinstance(candidate, dict) or simulation.get(
            "candidate_id"
        ) != candidate.get("candidate_id"):
            errors.add("simulation-candidate-binding")
        gate_rows = simulation.get("gates", [])
        if (
            [row.get("ordinal") for row in gate_rows]
            != list(range(len(gate_rows)))
            or [row.get("stage_id") for row in gate_rows]
            != [row.get("stage_id") for row in stages if row.get("required")]
        ):
            errors.add("simulation-gate-closure")
        all_pass = all(row.get("status") == "passed" for row in gate_rows)
        if (simulation.get("status") == "passed") != all_pass:
            errors.add("simulation-status")

    if isinstance(release, dict):
        if release.get("release_id") != _identity("release", release):
            errors.add("release-id")
        patch = release.get("projections", {}).get("patch", {})
        instructions = release.get("projections", {}).get("instructions", {})
        direct_diff = release.get("projections", {}).get("direct_diff", {})
        if patch.get("patch_id") != _identity("patch", patch):
            errors.add("patch-id")
        if (
            release.get("operations_sha256") != operation_sha256
            or patch.get("operations_sha256") != operation_sha256
            or instructions.get("operations_sha256") != operation_sha256
            or direct_diff.get("operations_sha256") != operation_sha256
        ):
            errors.add("output-operation-divergence")
        if (
            not isinstance(simulation, dict)
            or simulation.get("status") != "passed"
            or any(
                gate.get("status") != "passed"
                for gate in simulation.get("gates", [])
            )
        ):
            errors.add("release-without-passing-simulation")
        if (
            not isinstance(candidate, dict)
            or release.get("candidate_id") != candidate.get("candidate_id")
            or release.get("simulation_id")
            != simulation.get("simulation_id", "")
        ):
            errors.add("release-binding")

    if isinstance(application, dict):
        observed_target = application.get("observed_target", {})
        post_target = application.get("post_target")
        if observed_target.get("target_state_id") != _identity(
            "target", observed_target
        ):
            errors.add("target-state-id")
        if isinstance(post_target, dict) and post_target.get(
            "target_state_id"
        ) != _identity("target", post_target):
            errors.add("target-state-id")
        if application.get("application_id") != _identity(
            "application", application
        ):
            errors.add("application-id")
        observed_identity = _identity("target", observed_target)
        if (
            application.get("expected_target_state_id") != observed_identity
            or application.get("expected_target_state_id")
            != target.get("target_state_id")
        ):
            errors.add("stale-target")
        if (
            request.get("output_mode") != "direct-apply"
            or not request.get("consent", {}).get("allow_direct_apply")
        ):
            errors.add("application-not-authorized")
        if application.get("status") == "applied" and (
            not application.get("atomic")
            or not isinstance(post_target, dict)
            or application.get("rollback") != "not-needed"
        ):
            errors.add("applied-transaction-closure")

    if isinstance(verification, dict):
        if verification.get("verification_id") != _identity(
            "verification", verification
        ):
            errors.add("verification-id")
        post_target = (
            application.get("post_target")
            if isinstance(application, dict)
            else None
        )
        if (
            not isinstance(application, dict)
            or verification.get("application_id")
            != application.get("application_id")
            or verification.get("release_id") != release.get("release_id", "")
            or not isinstance(post_target, dict)
            or verification.get("target_state_id")
            != post_target.get("target_state_id")
        ):
            errors.add("verification-binding")
        checks_pass = all(
            row.get("status") == "passed"
            for row in verification.get("checks", [])
        )
        if (verification.get("status") == "passed") != checks_pass:
            errors.add("verification-status")

    allowed_transitions = {
        ("init", None, "initialized", "succeeded"),
        ("plan", "initialized", "planned", "succeeded"),
        ("plan", "simulation-failed", "planned", "succeeded"),
        ("plan", "application-rejected", "planned", "succeeded"),
        ("plan", "verification-failed", "planned", "succeeded"),
        ("plan", "invalidated", "planned", "succeeded"),
        ("plan", "initialized", "initialized", "failed"),
        (
            "plan",
            "simulation-failed",
            "simulation-failed",
            "failed",
        ),
        (
            "plan",
            "application-rejected",
            "application-rejected",
            "failed",
        ),
        (
            "plan",
            "verification-failed",
            "verification-failed",
            "failed",
        ),
        ("plan", "invalidated", "invalidated", "failed"),
        ("simulate", "planned", "simulated", "succeeded"),
        ("simulate", "planned", "simulation-failed", "failed"),
        ("generate", "simulated", "released", "succeeded"),
        ("generate", "simulated", "simulated", "failed"),
        ("apply", "released", "applied", "succeeded"),
        ("apply", "released", "application-rejected", "failed"),
        ("verify", "applied", "verified", "succeeded"),
        ("verify", "applied", "verification-failed", "failed"),
        ("verify", "verification-failed", "verified", "succeeded"),
        ("verify", "verification-failed", "verification-failed", "failed"),
    }
    persisted_states = {
        "initialized",
        "planned",
        "simulation-failed",
        "simulated",
        "released",
        "application-rejected",
        "applied",
        "verification-failed",
        "verified",
        "invalidated",
    }
    events = run.get("events", [])
    if [row.get("sequence") for row in events] != list(range(len(events))):
        errors.add("event-sequence")
    for index, event in enumerate(events):
        previous = None if index == 0 else events[index - 1].get("to_state")
        if event.get("from_state") != previous:
            errors.add("event-chain")
        transition = (
            event.get("phase"),
            event.get("from_state"),
            event.get("to_state"),
            event.get("result"),
        )
        if event.get("phase") in {"history", "export-proof"}:
            export_admitted = (
                event.get("from_state") == "verified"
                or (
                    event.get("from_state") == "released"
                    and request.get("output_mode")
                    in {"instructions", "patch-bundle"}
                )
            )
            legal = (
                event.get("from_state") in persisted_states
                and event.get("to_state") == event.get("from_state")
                and event.get("result") in {"no-op", "failed"}
                and (
                    event.get("phase") != "export-proof"
                    or export_admitted
                )
            )
        elif event.get("phase") == "invalidate":
            legal = (
                event.get("from_state")
                not in {None, "initialized", "invalidated"}
                and event.get("to_state") == "invalidated"
                and event.get("result") == "succeeded"
            )
        else:
            legal = transition in allowed_transitions
        if not legal:
            errors.add("illegal-transition")
    if events and run.get("state") != events[-1].get("to_state"):
        errors.add("state-event-mismatch")
    invalidation = run.get("invalidation", {})
    if invalidation.get("invalidated"):
        if (
            run.get("state") != "invalidated"
            or invalidation.get("cause") == "none"
            or invalidation.get("sequence") is None
            or not invalidation.get("invalidated_ids")
        ):
            errors.add("invalidation-closure")
    elif (
        invalidation.get("cause") != "none"
        or invalidation.get("sequence") is not None
        or invalidation.get("invalidated_ids")
    ):
        errors.add("invalidation-closure")

    if run.get("state") == "verified" and (
        not isinstance(simulation, dict)
        or simulation.get("status") != "passed"
        or not isinstance(release, dict)
        or not isinstance(application, dict)
        or application.get("status") != "applied"
        or not isinstance(verification, dict)
        or verification.get("status") != "passed"
    ):
        errors.add("verified-run-closure")
    if run.get("run_id") != _identity("run", run):
        errors.add("run-id")

    if proof.get("proof_id") != _identity("proof", proof):
        errors.add("proof-id")
    if proof.get("closure") == "target-verified":
        if run.get("state") != "verified":
            errors.add("proof-without-verification")
    elif proof.get("closure") == "release-validated":
        if (
            run.get("state") != "released"
            or request.get("output_mode") == "direct-apply"
        ):
            errors.add("release-proof-closure")
    target_verified = proof.get("closure") == "target-verified"
    expected_proof_bindings = {
        "run_id": run.get("run_id"),
        "request_id": request.get("request_id"),
        "plan_id": plan.get("plan_id"),
        "candidate_id": candidate.get("candidate_id", ""),
        "simulation_id": simulation.get("simulation_id", ""),
        "release_id": release.get("release_id", ""),
        "patch_id": release.get("projections", {})
        .get("patch", {})
        .get("patch_id", ""),
        "application_id": (
            application.get("application_id", "")
            if target_verified
            else None
        ),
        "verification_id": (
            verification.get("verification_id", "")
            if target_verified
            else None
        ),
    }
    if any(
        proof.get(field) != expected
        for field, expected in expected_proof_bindings.items()
    ):
        errors.add("proof-binding")
    post_target = (
        application.get("post_target")
        if isinstance(application, dict)
        else None
    )
    proof_target = proof.get("target", {})
    expected_applied_target = (
        post_target.get("target_state_id")
        if target_verified and isinstance(post_target, dict)
        else None
    )
    if (
        proof_target.get("baseline_target_state_id")
        != target.get("target_state_id")
        or proof_target.get("applied_target_state_id")
        != expected_applied_target
        or proof_target.get("verified_target_state_id")
        != expected_applied_target
        or proof.get("standard_ids") != primary_ids
    ):
        errors.add("proof-closure")
    artifacts = {
        row.get("artifact_id"): row for row in proof.get("artifacts", [])
    }
    if len(artifacts) != len(proof.get("artifacts", [])) or None in artifacts:
        errors.add("proof-artifact-id")
    export_record = proof.get("export")
    exported = (
        set(export_record.get("artifact_ids", []))
        if isinstance(export_record, dict)
        else set()
    )
    if any(
        row.get("privacy") == "local-private"
        and (
            row.get("included_in_export")
            or row.get("artifact_id") in exported
        )
        for row in artifacts.values()
    ):
        errors.add("private-export")
    if exported != {
        artifact_id
        for artifact_id, row in artifacts.items()
        if row.get("included_in_export")
    }:
        errors.add("export-artifact-closure")
    expected_gate_evidence = [
        {
            "stage_id": row.get("stage_id"),
            "status": "passed",
            "evidence_sha256": row.get("evidence_sha256"),
        }
        for row in simulation.get("gates", [])
    ] if isinstance(simulation, dict) else []
    if proof.get("gate_evidence") != expected_gate_evidence:
        errors.add("proof-gate-closure")
    return errors


def _release_proof_fixture(
    source: dict[str, Any],
    output_mode: str,
) -> dict[str, Any]:
    """Derive and re-identify one non-mutating release fixture for tests."""

    bundle = copy.deepcopy(source)
    request = bundle["request"]
    plan = bundle["plan"]
    run = bundle["run"]
    proof = bundle["proof"]

    request["output_mode"] = output_mode
    request["consent"]["allow_direct_apply"] = False
    request["target"]["target_state_id"] = _identity(
        "target", request["target"]
    )
    request["request_id"] = _identity("request", request)

    plan["request_id"] = request["request_id"]
    plan["target_state_id"] = request["target"]["target_state_id"]
    plan["plan_id"] = _identity("plan", plan)

    run["request_id"] = request["request_id"]
    run["plan_id"] = plan["plan_id"]
    run["target_state_id"] = request["target"]["target_state_id"]
    run["output_mode"] = output_mode
    run["state"] = "released"
    candidate = run["candidate"]
    candidate["plan_id"] = plan["plan_id"]
    candidate["target_state_id"] = request["target"]["target_state_id"]
    candidate["candidate_id"] = _identity("candidate", candidate)
    simulation = run["simulation"]
    simulation["candidate_id"] = candidate["candidate_id"]
    simulation["simulation_id"] = _identity("simulation", simulation)
    release = run["release"]
    release["candidate_id"] = candidate["candidate_id"]
    release["simulation_id"] = simulation["simulation_id"]
    patch = release["projections"]["patch"]
    patch["patch_id"] = _identity("patch", patch)
    release["release_id"] = _identity("release", release)
    run["application"] = None
    run["verification"] = None
    run["events"] = run["events"][:4] + [
        {
            "sequence": 4,
            "phase": "export-proof",
            "from_state": "released",
            "to_state": "released",
            "result": "no-op",
            "record_ids": [],
        }
    ]
    run["events"][0]["record_ids"] = [request["request_id"]]
    run["events"][1]["record_ids"] = [
        plan["plan_id"],
        candidate["candidate_id"],
    ]
    run["events"][2]["record_ids"] = [simulation["simulation_id"]]
    run["events"][3]["record_ids"] = [
        release["release_id"],
        patch["patch_id"],
    ]
    run["run_id"] = _identity("run", run)

    proof["closure"] = "release-validated"
    proof["run_id"] = run["run_id"]
    proof["request_id"] = request["request_id"]
    proof["plan_id"] = plan["plan_id"]
    proof["candidate_id"] = candidate["candidate_id"]
    proof["simulation_id"] = simulation["simulation_id"]
    proof["release_id"] = release["release_id"]
    proof["patch_id"] = patch["patch_id"]
    proof["application_id"] = None
    proof["verification_id"] = None
    proof["target"]["baseline_target_state_id"] = request["target"][
        "target_state_id"
    ]
    proof["target"]["applied_target_state_id"] = None
    proof["target"]["verified_target_state_id"] = None
    proof["proof_id"] = _identity("proof", proof)
    return bundle


class BlueprintsExecutableContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = CONTRACT_PATH.read_text(encoding="utf-8")
        cls.examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
        cls.schemas = {
            name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in SCHEMA_PATHS.items()
        }

    def test_contract_fixes_authority_identity_and_state_machine(self) -> None:
        normalized_contract = " ".join(self.contract.split())
        for required in (
            CONTRACT_ID,
            "## Authority boundary",
            "## Canonical JSON and identity",
            "## Plan admission",
            "## Candidate boundary",
            "## Simulation admission",
            "## Release and output projections",
            "## Target application transaction",
            "## Verification and proof closure",
            "## CLI state machine",
            "No admitted standard means no plan",
            "Only `direct-apply` may execute the v1 `apply` phase",
            "`export-proof`",
            "does not admit a standard or prove that a real feature can be generated.",
        ):
            with self.subTest(required=required):
                self.assertIn(" ".join(required.split()), normalized_contract)

    def test_schemas_are_supported_closed_definitions(self) -> None:
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    schema["$schema"],
                )

    def test_valid_fixture_closes_schemas_identities_and_proof(self) -> None:
        valid = self.examples["valid"]
        self.assertEqual(set(), _schema_errors(valid, self.schemas))
        self.assertEqual(set(), _semantic_errors(valid))
        self.assertEqual(
            valid["run"]["release"]["operations_sha256"],
            _sha256(valid["plan"]["operations"]),
        )
        self.assertEqual(
            valid["proof"]["target"]["verified_target_state_id"],
            valid["run"]["application"]["post_target"]["target_state_id"],
        )

    def test_non_mutating_output_modes_close_release_proof(self) -> None:
        for output_mode in ("instructions", "patch-bundle"):
            with self.subTest(output_mode=output_mode):
                candidate = _release_proof_fixture(
                    self.examples["valid"],
                    output_mode,
                )
                self.assertEqual(
                    set(),
                    _schema_errors(candidate, self.schemas),
                )
                self.assertEqual(set(), _semantic_errors(candidate))
                self.assertEqual(
                    "release-validated",
                    candidate["proof"]["closure"],
                )
                self.assertIsNone(candidate["run"]["application"])
                self.assertIsNone(candidate["run"]["verification"])

    def test_fail_closed_mutations_hit_named_boundaries(self) -> None:
        valid = self.examples["valid"]
        identifiers: set[str] = set()
        for mutation in self.examples["mutations"]:
            with self.subTest(mutation=mutation["id"]):
                self.assertNotIn(mutation["id"], identifiers)
                identifiers.add(mutation["id"])
                candidate = copy.deepcopy(valid)
                for operation in mutation["operations"]:
                    _apply_mutation(candidate, operation)
                observed = _schema_errors(candidate, self.schemas)
                observed.update(_semantic_errors(candidate))
                self.assertTrue(
                    set(mutation["expected_errors"]).issubset(observed),
                    f"{mutation['id']} expected={mutation['expected_errors']} "
                    f"observed={sorted(observed)}",
                )

    def test_fixture_is_explicitly_synthetic_and_non_authoritative(self) -> None:
        self.assertEqual(
            {
                "schema_version",
                "format",
                "contract_id",
                "valid",
                "mutations",
            },
            set(self.examples),
        )
        self.assertEqual(CONTRACT_ID, self.examples["contract_id"])
        self.assertEqual(
            "synthetic-contract-fixture",
            self.examples["valid"]["request"]["feature_family"],
        )
        self.assertGreaterEqual(len(self.examples["mutations"]), 10)


if __name__ == "__main__":
    unittest.main()
