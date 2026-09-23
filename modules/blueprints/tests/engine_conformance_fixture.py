"""Executable synthetic producer for the BLUEPRINTS-M1-V01 proof."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any, Iterator

from _support import WORKBENCH_ROOT
import test_simulation

from workbench_blueprints import conformance, interface, lifecycle, planner, simulation, standards


_CANDIDATE_KEYS = {
    "candidate_id",
    "plan_id",
    "target_state_id",
    "standard_ids",
    "content_manifest_sha256",
    "sealed_locator",
    "edit_generation",
}


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@contextmanager
def _fixture() -> Iterator[test_simulation.SimulationTest]:
    value = test_simulation.SimulationTest()
    value.setUp()
    try:
        yield value
    finally:
        value.tearDown()


def _intake(fixture: test_simulation.SimulationTest, output_mode: str) -> dict[str, Any]:
    value = copy.deepcopy(fixture.intake)
    value["output_mode"] = output_mode
    value["consent"]["allow_direct_apply"] = output_mode == "direct-apply"
    return value


def _adapters(
    fixture: test_simulation.SimulationTest,
    *,
    dependency_provider: bool = True,
    mutation_hook: lifecycle.MutationHook | None = None,
) -> interface.AdapterSet:
    return interface.AdapterSet(
        formatter_runner=fixture._formatter,
        dependency_provider=fixture._provider if dependency_provider else None,
        post_checks={
            "synthetic-registration-check": lambda root: (
                (root / "groovy/material/syntheticium.groovy").is_file(),
                {"registration": "present"},
            )
        },
        mutation_hook=mutation_hook,
    )


def _core(
    fixture: test_simulation.SimulationTest,
    name: str,
    output_mode: str,
    *,
    adapters: interface.AdapterSet | None = None,
    intake: dict[str, Any] | None = None,
) -> interface.BlueprintsCore:
    core = interface.BlueprintsCore(
        fixture.repository / ".workbench/blueprints" / name,
        adapters=_adapters(fixture) if adapters is None else adapters,
    )
    initialized = core.init(
        target_repository=fixture.repository,
        repository_id="pack",
        registry_root=fixture.registry,
        asset_root=WORKBENCH_ROOT,
        ledger_path=fixture.ledger,
        intake=_intake(fixture, output_mode) if intake is None else intake,
    )
    _check(initialized["state"] == "initialized", f"{name} did not initialize")
    return core


def _observation(scenario_id: str) -> dict[str, Any]:
    expected = conformance.SCENARIO_CATALOG[scenario_id]
    return conformance.scenario_observation(
        scenario_id,
        states=expected["states"],
        diagnostic_codes=expected["diagnostics"],
        assertions=expected["assertions"],
    )


def _standard_selection_failure() -> dict[str, Any]:
    with _fixture() as fixture:
        intake = _intake(fixture, "instructions")
        intake["feature_family"] = "unadmitted-synthetic-family"
        core = _core(
            fixture,
            "standard-selection-failure",
            "instructions",
            intake=intake,
        )
        result = core.plan(fixture.planning_evidence)
        session = interface.SessionStore(core.workspace).load()
        _check(result["status"] == "failed", "missing standard did not fail")
        _check(result["data"]["plan"] is None, "missing standard produced a plan")
        _check(result["data"]["candidate"] is None, "missing standard produced a candidate")
        _check(session["run"]["release"] is None, "missing standard produced a release")
        _check(
            [row["code"] for row in result["diagnostics"]] == ["BPI130_PLAN_BLOCKED"],
            "missing-standard diagnostic differs",
        )
    return _observation("standard-selection-failure")


def _sealed_candidate_boundary() -> dict[str, Any]:
    with _fixture() as fixture:
        core = _core(fixture, "sealed-candidate-boundary", "instructions")
        result = core.plan(fixture.planning_evidence)
        candidate = result["data"]["candidate"]
        _check(result["state"] == "planned", "candidate was not planned")
        _check(set(candidate) == _CANDIDATE_KEYS, "candidate metadata is not closed")
        _check(
            candidate["sealed_locator"].startswith("local-cas:sha256:"),
            "candidate locator is not content-addressed",
        )
        encoded = standards.canonical_json(candidate)
        for forbidden in ("Syntheticium", "content_base64", "groovy/material/syntheticium"):
            _check(forbidden not in encoded, f"candidate disclosed {forbidden}")
    return _observation("sealed-candidate-boundary")


def _simulation_gate_failure() -> dict[str, Any]:
    with _fixture() as fixture:
        core = _core(
            fixture,
            "simulation-gate-failure",
            "instructions",
            adapters=_adapters(fixture, dependency_provider=False),
        )
        core.plan(fixture.planning_evidence)
        result = core.simulate(fixture.environment)
        _check(result["state"] == "simulation-failed", "simulation failure was not retained")
        _check(
            any(row["status"] != "passed" for row in result["data"]["simulation"]["gates"]),
            "required gate failure was not retained",
        )
        _check(
            [row["code"] for row in result["diagnostics"]] == ["BPI131_SIMULATION_FAILED"],
            "simulation diagnostic differs",
        )
        try:
            core.generate()
        except interface.InterfaceDiagnostic as exc:
            _check(exc.code == "BPI120_ILLEGAL_PREDECESSOR", "later phase rejection differs")
        else:
            raise AssertionError("failed simulation generated a release")
        session = interface.SessionStore(core.workspace).load()
        _check(session["run"]["release"] is None, "failed simulation retained a release")
    return _observation("simulation-gate-failure")


def _non_mutating_release(output_mode: str, scenario_id: str) -> tuple[dict[str, Any], str]:
    with _fixture() as fixture:
        before = planner.capture_target_state(fixture.repository, "pack")
        core = _core(fixture, scenario_id, output_mode)
        core.plan(fixture.planning_evidence)
        core.simulate(fixture.environment)
        generated = core.generate()
        history = core.history()
        exported = core.export_proof()
        after = planner.capture_target_state(fixture.repository, "pack")
        _check(generated["state"] == "released", f"{output_mode} did not release")
        _check(generated["data"]["delivery"]["output_mode"] == output_mode, "delivery mode differs")
        _check(before == after, f"{output_mode} mutated the target")
        _check(history["data"]["history"]["run_id"] == history["run_id"], "history is stale")
        proof = exported["data"]["proof"]
        _check(proof["closure"] == "release-validated", "non-mutating proof overclaimed")
        session = interface.SessionStore(core.workspace).load()
        operation_identity = session["run"]["release"]["operations_sha256"]
    return _observation(scenario_id), operation_identity


def _direct_apply_verification() -> tuple[dict[str, Any], str]:
    with _fixture() as fixture:
        before = planner.capture_target_state(fixture.repository, "pack")
        core = _core(fixture, "direct-apply-verification", "direct-apply")
        core.plan(fixture.planning_evidence)
        core.simulate(fixture.environment)
        core.generate()
        applied = core.apply()
        verified = core.verify()
        history = core.history()
        exported = core.export_proof()
        after = planner.capture_target_state(fixture.repository, "pack")
        _check(applied["state"] == "applied", "direct release was not applied")
        _check(verified["state"] == "verified", "direct release was not verified")
        _check(before != after, "direct release did not change the target")
        _check(history["data"]["history"]["run_id"] == history["run_id"], "history is stale")
        proof = exported["data"]["proof"]
        _check(proof["closure"] == "target-verified", "direct proof did not bind target")
        private_ids = {
            row["artifact_id"]
            for row in proof["artifacts"]
            if row["privacy"] == "local-private"
        }
        exported_ids = {
            row["artifact_id"] for row in exported["data"]["export_bundle"]["artifacts"]
        }
        _check(private_ids.isdisjoint(exported_ids), "portable proof disclosed private payloads")
        session = interface.SessionStore(core.workspace).load()
        _check(
            [row["phase"] for row in session["run"]["events"]]
            == [
                "init",
                "plan",
                "simulate",
                "generate",
                "apply",
                "verify",
                "history",
                "export-proof",
            ],
            "direct lifecycle phase closure differs",
        )
        operation_identity = session["run"]["release"]["operations_sha256"]
    return _observation("direct-apply-verification"), operation_identity


def _stale_target_rejection() -> dict[str, Any]:
    with _fixture() as fixture:
        core = _core(fixture, "stale-target-rejection", "direct-apply")
        core.plan(fixture.planning_evidence)
        core.simulate(fixture.environment)
        core.generate()
        (fixture.repository / "README.md").write_text("application drift\n", encoding="utf-8")
        drifted = planner.capture_target_state(fixture.repository, "pack")
        result = core.apply()
        _check(result["state"] == "application-rejected", "stale application was not rejected")
        _check(
            [row["code"] for row in result["diagnostics"]]
            == ["BPA138_STALE_APPLICATION_TARGET"],
            "stale application diagnostic differs",
        )
        _check(
            planner.capture_target_state(fixture.repository, "pack") == drifted,
            "stale application changed the observed drift",
        )
        _check(
            not (fixture.repository / "groovy/material/syntheticium.groovy").exists(),
            "stale application exposed candidate bytes",
        )
    return _observation("stale-target-rejection")


def _rollback_recovery() -> dict[str, Any]:
    with _fixture() as fixture:
        injected = {"reached": False}

        def fail_after_first(ordinal: int, _path: str) -> None:
            if ordinal == 0:
                injected["reached"] = True
                raise RuntimeError("synthetic V01 rollback failure")

        core = _core(
            fixture,
            "rollback-recovery",
            "direct-apply",
            adapters=_adapters(fixture, mutation_hook=fail_after_first),
        )
        core.plan(fixture.planning_evidence)
        core.simulate(fixture.environment)
        core.generate()
        before = planner.capture_target_state(fixture.repository, "pack")
        result = core.apply()
        application = result["data"]["application"]
        _check(injected["reached"], "rollback failure seam was not reached")
        _check(application["rollback"] == "succeeded", "rollback did not succeed")
        _check(application["atomic"] is True, "successful rollback was not atomic")
        _check(
            planner.capture_target_state(fixture.repository, "pack") == before,
            "rollback did not restore the exact target",
        )
        _check(
            [row["code"] for row in result["diagnostics"]]
            == ["BPA144_APPLICATION_ROLLED_BACK"],
            "rollback diagnostic differs",
        )
        history_root = core.workspace / "history"
        _check(not (history_root / "active-transaction.json").exists(), "journal remained active")
        _check(not (history_root / "active-transaction.lock").exists(), "transaction lock remained")
    return _observation("rollback-recovery")


def _candidate_invalidation() -> dict[str, Any]:
    with _fixture() as fixture:
        planning_result = fixture.planning_result
        candidate = planning_result["candidate"]
        simulation_result = fixture._simulator().execute(
            planning_result,
            intake=fixture.intake,
            target_manifest=fixture.target,
            planning_evidence=fixture.planning_evidence,
            environment_lock=fixture.environment,
        )
        simulation_id = simulation_result["simulation"]["simulation_id"]
        invalidation = planner.invalidate_candidate(
            candidate,
            invalidated_ids=[simulation_id],
        )
        _check(
            invalidation["next_edit_generation"] == candidate["edit_generation"] + 1,
            "invalidation did not advance the edit generation",
        )
        _check(
            invalidation["invalidated_ids"] == sorted([candidate["candidate_id"], simulation_id]),
            "invalidation did not close downstream identities",
        )
        local_root = fixture.repository / ".workbench/blueprints/v01-invalidation"
        engine = lifecycle.LifecycleEngine(
            registry_root=fixture.registry,
            asset_root=WORKBENCH_ROOT,
            ledger_path=fixture.ledger,
            target_repository=fixture.repository,
            sealed_store=fixture.sealed_store,
            simulation_evidence_store=simulation.SimulationEvidenceStore(fixture.evidence_root),
            artifact_store=lifecycle.ArtifactStore(local_root / "release"),
            history_store=lifecycle.HistoryStore(local_root / "history"),
        )
        rejected = engine.release(
            planning_result,
            simulation_result,
            target_manifest=fixture.target,
            environment_lock=fixture.environment,
            invalidated_ids=invalidation["invalidated_ids"],
        )
        _check(rejected["release"] is None, "invalidated candidate released bytes")
        _check(rejected["delivery"] is None, "invalidated candidate exposed delivery")
        _check(rejected["diagnostics"] == ["BPA134_INVALIDATED_INPUT"], "invalidation diagnostic differs")
    return _observation("candidate-invalidation")


def run_conformance() -> dict[str, Any]:
    """Execute every V01 scenario and return the independently validated proof."""

    observations = [
        _candidate_invalidation(),
        _standard_selection_failure(),
        _sealed_candidate_boundary(),
        _simulation_gate_failure(),
    ]
    instructions, instructions_operations = _non_mutating_release(
        "instructions", "instructions-release"
    )
    patch, patch_operations = _non_mutating_release(
        "patch-bundle", "patch-bundle-release"
    )
    direct, direct_operations = _direct_apply_verification()
    observations.extend([instructions, patch, direct])
    _check(
        instructions_operations == patch_operations == direct_operations,
        "output modes did not retain one operation identity",
    )
    observations.append(_observation("output-mode-agreement"))
    observations.extend([_rollback_recovery(), _stale_target_rejection()])
    return conformance.build_engine_conformance_proof(
        sorted(observations, key=lambda row: row["scenario_id"])
    )
