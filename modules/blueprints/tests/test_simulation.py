#!/usr/bin/env python3

"""X01 conformance tests for isolated, evidence-bound simulation."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import unittest

from jsonschema import Draft202012Validator
import yaml

from _support import (
    FIXTURE_ROOT,
    LEDGER_PATH,
    SCHEMA_ROOT,
    SOURCE_ROOT,
    WORKBENCH_ROOT,
)

REPO_ROOT = WORKBENCH_ROOT
BLUEPRINTS_TOOLS = SOURCE_ROOT
CENTRAL_LEDGER = LEDGER_PATH

if str(BLUEPRINTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(BLUEPRINTS_TOOLS))

from workbench_blueprints import planner  # noqa: E402
from workbench_blueprints import simulation  # noqa: E402
from workbench_blueprints import standards  # noqa: E402


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


class SimulationTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.repository = self.root / "target"
        self.registry = self.root / "standards"
        self.sealed_root = self.root / "sealed"
        self.cache_root = self.root / "dependencies"
        self.evidence_root = self.root / "evidence"
        self.workspace_root = self.root / "workspaces"
        self.repository.mkdir()
        self.registry.mkdir()
        for name in (
            "material-backed-fluid.yaml",
            "localization-component.yaml",
        ):
            shutil.copyfile(FIXTURE_ROOT / name, self.registry / name)
        primary_path = self.registry / "material-backed-fluid.yaml"
        primary = yaml.safe_load(primary_path.read_text(encoding="utf-8"))
        extra_fixtures = [
            (
                "runtime-fixture",
                "runtime",
                "groovy/material/fixtures/runtime.txt",
            ),
            (
                "presentation-fixture",
                "presentation",
                "groovy/material/fixtures/presentation.txt",
            ),
            (
                "integration-fixture",
                "integration",
                "groovy/material/fixtures/integration.txt",
            ),
            (
                "formed-world-fixture",
                "formed-world",
                "groovy/material/fixtures/world.txt",
            ),
        ]
        for fixture_id, kind, path in extra_fixtures:
            primary["validation"]["fixtures"].append(
                {
                    "id": fixture_id,
                    "kind": kind,
                    "repository_id": "pack",
                    "paths": [path],
                    "disposable": True,
                }
            )
            primary["validation"]["tests"][0]["fixture_ids"].append(fixture_id)
        primary_path.write_text(
            yaml.safe_dump(primary, sort_keys=False), encoding="utf-8"
        )
        _git(self.repository, "init", "-q")
        _git(self.repository, "config", "user.email", "blueprints@example.invalid")
        _git(self.repository, "config", "user.name", "Blueprints Test")
        (self.repository / "README.md").write_text(
            "synthetic target\n", encoding="utf-8"
        )
        fixture = self.repository / "groovy/material/SyntheticMaterial.groovy"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("fixture\n", encoding="utf-8")
        for _fixture_id, _kind, path in extra_fixtures:
            target = self.repository / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{_kind} fixture\n", encoding="utf-8")
        mode_probe = self.repository / "mode-probe"
        mode_probe.write_text("head\n", encoding="utf-8")
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-q", "-m", "initial")

        # Exercise all three mutable Git layers plus an index/worktree kind split.
        readme = self.repository / "README.md"
        readme.write_text("staged\n", encoding="utf-8")
        _git(self.repository, "add", "README.md")
        readme.write_text("unstaged\n", encoding="utf-8")
        mode_probe.unlink()
        mode_probe.symlink_to("README.md")
        _git(self.repository, "add", "mode-probe")
        mode_probe.unlink()
        mode_probe.write_text("worktree file\n", encoding="utf-8")
        (self.repository / "untracked.txt").write_text(
            "untracked\n", encoding="utf-8"
        )

        standards.build_registry(self.registry, asset_root=REPO_ROOT)
        self.ledger = self.root / "ledger.json"
        shutil.copyfile(CENTRAL_LEDGER, self.ledger)
        self.target = planner.capture_target_state(self.repository, "pack")
        self.sealed_store = planner.SealedStore(self.sealed_root)
        self.intake = {
            "sequence": 0,
            "feature_family": "material-backed-fluid",
            "intent": {
                "operation": "create",
                "target_key": "susy:syntheticium",
                "desired_outcome": "Create a material-backed fluid.",
            },
            "parameters": [
                {"name": "translation", "value": "Syntheticium"},
                {"name": "name", "value": "Syntheticium"},
            ],
            "requested_variants": [],
            "output_mode": "instructions",
            "consent": {
                "accept_compliant_revision": False,
                "allow_direct_apply": False,
            },
        }
        self.planning_evidence = planner.build_planning_evidence(
            self.target["target_state_id"],
            queries=[
                {
                    "standard_key": "material-backed-fluid",
                    "query_id": "atlas-query:synthetic-material-builder",
                    "result_id": "atlas-result:synthetic-material-builder",
                    "availability": "available",
                    "result": {"registration": {"id_argument": 4000}},
                }
            ],
            allocation=[
                {
                    "domain_name": "susy-material-id",
                    "authority_id": "blueprints-allocation-ledger-v1",
                    "occupied_values": [4000],
                    "proposed_value": None,
                }
            ],
            reconciliation=[
                {
                    "standard_key": "material-backed-fluid",
                    "identity_kind": "identity-absent",
                    "observed": None,
                }
            ],
            relevant_drift="none",
        )
        self.planning_result = self._planner().execute(
            self.intake, self.target, self.planning_evidence
        )
        self.tools = {
            "synthetic-compile": (
                b"#!/bin/sh\nset -eu\n"
                b"test -f groovy/material/syntheticium.groovy\n"
                b"test -f groovy/material/syntheticiumFluid.groovy\n"
                b"test ! -e /workspace/source-outside-sandbox/planner.py\n"
            ),
            "synthetic-existing-tests": (
                b"#!/bin/sh\nset -eu\ntest \"$(cat README.md)\" = unstaged\n"
            ),
            "synthetic-test": (
                b"#!/bin/sh\nset -eu\n"
                b"test -f groovy/material/SyntheticMaterial.groovy\n"
                b"test -f groovy/material/syntheticiumFluid.groovy\n"
            ),
        }
        self.environment = self._environment()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _formatter(
        _definition: dict[str, Any], content: dict[str, bytes]
    ) -> dict[str, bytes]:
        return content

    def _planner(
        self,
        formatter: planner.FormatterRunner | None = None,
    ) -> planner.Planner:
        return planner.Planner(
            registry_root=self.registry,
            asset_root=REPO_ROOT,
            ledger_path=self.ledger,
            target_repository=self.repository,
            sealed_store=self.sealed_store,
            formatter_runner=self._formatter if formatter is None else formatter,
        )

    def _environment(self) -> dict[str, Any]:
        dependencies = [
            {
                "id": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "executable": True,
            }
            for name, content in self.tools.items()
        ]
        isolator = Path("/usr/bin/bwrap")
        return {
            "schema_version": 1,
            "format": "susy-blueprints-environment-lock-v1",
            "contract_id": "BLUEPRINTS-EXECUTABLE-ENGINE-V1",
            "isolation": {
                "kind": "bubblewrap",
                "executable": str(isolator),
                "executable_sha256": hashlib.sha256(
                    isolator.read_bytes()
                ).hexdigest(),
                "network": "disabled",
                "environment": "cleared",
            },
            "limits": {
                "command_timeout_seconds": 5,
                "max_output_bytes": 4096,
            },
            "dependencies": dependencies,
            "commands": [
                {
                    "stage_id": "isolated-compilation",
                    "argv": ["synthetic-compile"],
                    "dependency_ids": ["synthetic-compile"],
                },
                {
                    "stage_id": "target-existing-tests",
                    "argv": ["synthetic-existing-tests"],
                    "dependency_ids": ["synthetic-existing-tests"],
                },
            ],
        }

    def _provider(self, definition: dict[str, Any]) -> bytes:
        return self.tools[definition["id"]]

    def _simulator(
        self,
        *,
        provider: simulation.DependencyProvider | None = None,
        formatter: planner.FormatterRunner | None = None,
    ) -> simulation.Simulator:
        return simulation.Simulator(
            registry_root=self.registry,
            asset_root=REPO_ROOT,
            ledger_path=self.ledger,
            target_repository=self.repository,
            sealed_store=self.sealed_store,
            dependency_cache=simulation.DependencyCache(self.cache_root),
            evidence_store=simulation.SimulationEvidenceStore(
                self.evidence_root
            ),
            workspace_root=self.workspace_root,
            dependency_provider=self._provider if provider is None else provider,
            formatter_runner=self._formatter if formatter is None else formatter,
        )

    def _execute(
        self, simulator: simulation.Simulator | None = None
    ) -> dict[str, Any]:
        return (simulator or self._simulator()).execute(
            self.planning_result,
            intake=self.intake,
            target_manifest=self.target,
            planning_evidence=self.planning_evidence,
            environment_lock=self.environment,
        )

    def test_new_schemas_are_valid(self) -> None:
        for name in (
            "blueprints-environment-lock-v1.schema.json",
            "blueprints-simulation-evidence-v1.schema.json",
        ):
            schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)

    def test_exact_dirty_target_passes_all_gates_without_mutation_or_disclosure(
        self,
    ) -> None:
        before = planner.capture_target_state(self.repository, "pack")
        result = self._execute()
        simulation_result = result["simulation"]
        self.assertEqual(simulation_result["status"], "passed")
        self.assertTrue(
            all(row["status"] == "passed" for row in simulation_result["gates"])
        )
        self.assertEqual(
            [row["ordinal"] for row in simulation_result["gates"]],
            list(range(len(simulation_result["gates"]))),
        )
        self.assertEqual(
            [row["stage_id"] for row in simulation_result["gates"]],
            [
                row["stage_id"]
                for row in self.planning_result["plan"]["validation_stages"]
            ],
        )
        projection = copy.deepcopy(simulation_result)
        projection.pop("simulation_id")
        self.assertEqual(
            simulation_result["simulation_id"],
            "blueprints-simulation:sha256:"
            + hashlib.sha256(
                standards.canonical_json(projection).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(before, planner.capture_target_state(self.repository, "pack"))
        self.assertEqual(list(self.workspace_root.iterdir()), [])
        private = simulation.SimulationEvidenceStore(
            self.evidence_root
        ).read(result["evidence_locator"])
        self.assertTrue(private["disposable_worktrees_removed"])
        self.assertEqual(
            [row["evidence_sha256"] for row in private["gates"]],
            [row["evidence_sha256"] for row in simulation_result["gates"]],
        )
        public = json.dumps(result, sort_keys=True)
        self.assertNotIn("content_base64", public)
        self.assertNotIn("sealed_locator", public)
        self.assertNotIn("material(", public)
        mode_row = next(
            row for row in self.target["entries"] if row["path"] == "mode-probe"
        )
        self.assertEqual(mode_row["head_mode"], "100644")
        self.assertEqual(mode_row["index_mode"], "120000")
        self.assertEqual(mode_row["kind"], "file")

    def test_environment_and_dependency_digests_fail_closed(self) -> None:
        tampered = copy.deepcopy(self.environment)
        tampered["isolation"]["executable_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            simulation.SimulationDiagnostic, "BPX108_ISOLATOR_DIGEST"
        ):
            simulation.compile_environment_lock(tampered)
        cache = simulation.DependencyCache(self.cache_root)
        definition = self.environment["dependencies"][0]
        with self.assertRaisesRegex(
            simulation.SimulationDiagnostic, "BPX113_DEPENDENCY_DIGEST"
        ):
            cache.ensure(definition, lambda _row: b"wrong")

    def test_missing_dependency_is_unavailable_and_closes_later_gates(self) -> None:
        unavailable = self._simulator(
            provider=lambda definition: (
                self.tools[definition["id"]]
                if definition["id"] != "synthetic-compile"
                else (_ for _ in ()).throw(
                    simulation.SimulationDiagnostic(
                        "BPX111_DEPENDENCY_UNAVAILABLE",
                        definition["id"],
                        "offline",
                    )
                )
            )
        )
        result = self._execute(unavailable)["simulation"]
        compile_index = next(
            row["ordinal"]
            for row in result["gates"]
            if row["stage_id"] == "isolated-compilation"
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["gates"][compile_index]["status"], "unavailable")
        self.assertTrue(
            all(
                row["status"] == "unavailable"
                for row in result["gates"][compile_index + 1 :]
            )
        )

    def test_timeout_and_output_limit_fail_the_command_gate(self) -> None:
        for script, expected_reason in (
            (b"#!/bin/sh\nsleep 5\n", "BPX127_COMMAND_TIMEOUT"),
            (
                b"#!/bin/sh\n"
                b"dd if=/dev/zero bs=5000 count=1 2>/dev/null\n",
                "BPX114_OUTPUT_LIMIT",
            ),
        ):
            with self.subTest(expected_reason=expected_reason):
                self.tools["synthetic-compile"] = script
                self.environment = self._environment()
                self.environment["limits"]["command_timeout_seconds"] = 1
                result = self._execute()
                private = simulation.SimulationEvidenceStore(
                    self.evidence_root
                ).read(result["evidence_locator"])
                compile_gate = next(
                    row
                    for row in private["gates"]
                    if row["stage_id"] == "isolated-compilation"
                )
                self.assertEqual(compile_gate["status"], "failed")
                self.assertEqual(compile_gate["reason_code"], expected_reason)
                shutil.rmtree(self.cache_root, ignore_errors=True)

    def test_disposable_formed_world_fixture_is_required(self) -> None:
        self.tools["synthetic-existing-tests"] = (
            b"#!/bin/sh\nset -eu\n"
            b"rm groovy/material/fixtures/world.txt\n"
        )
        self.environment = self._environment()
        result = self._execute()
        private = simulation.SimulationEvidenceStore(
            self.evidence_root
        ).read(result["evidence_locator"])
        standard_gate = private["gates"][-1]
        self.assertEqual(
            standard_gate["stage_id"],
            "material-backed-fluid--registration-gate",
        )
        self.assertEqual(standard_gate["status"], "failed")
        self.assertEqual(standard_gate["reason_code"], "BPX145_FIXTURE_MISSING")

    def test_command_failure_prevents_standard_fixture_execution(self) -> None:
        self.tools["synthetic-compile"] = b"#!/bin/sh\nexit 9\n"
        self.environment = self._environment()
        result = self._execute()["simulation"]
        compile_gate = next(
            row
            for row in result["gates"]
            if row["stage_id"] == "isolated-compilation"
        )
        standard_gate = result["gates"][-1]
        self.assertEqual(compile_gate["status"], "failed")
        self.assertEqual(standard_gate["status"], "unavailable")
        self.assertEqual(result["status"], "failed")

    def test_regeneration_mismatch_fails_before_any_command(self) -> None:
        def uppercase(
            _definition: dict[str, Any], content: dict[str, bytes]
        ) -> dict[str, bytes]:
            return {path: value.upper() for path, value in content.items()}

        result = self._execute(
            self._simulator(formatter=uppercase)
        )["simulation"]
        regeneration = next(
            row
            for row in result["gates"]
            if row["stage_id"] == "deterministic-regeneration"
        )
        compilation = next(
            row
            for row in result["gates"]
            if row["stage_id"] == "isolated-compilation"
        )
        self.assertEqual(regeneration["status"], "failed")
        self.assertEqual(compilation["status"], "unavailable")

    def test_candidate_binding_tamper_is_rejected_before_simulation(self) -> None:
        tampered = copy.deepcopy(self.planning_result)
        tampered["candidate"]["content_manifest_sha256"] = "0" * 64
        with self.assertRaises(
            (planner.PlannerDiagnostic, simulation.SimulationDiagnostic)
        ):
            self._simulator().execute(
                tampered,
                intake=self.intake,
                target_manifest=self.target,
                planning_evidence=self.planning_evidence,
                environment_lock=self.environment,
            )


if __name__ == "__main__":
    unittest.main()
