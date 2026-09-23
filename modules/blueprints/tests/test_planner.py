#!/usr/bin/env python3

"""P01 conformance tests for deterministic planning and sealed synthesis."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import stat
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
from workbench_blueprints import standards  # noqa: E402


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


class PlannerTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.repository = self.root / "target"
        self.registry = self.root / "standards"
        self.sealed_root = self.root / "sealed"
        self.repository.mkdir()
        self.registry.mkdir()
        for name in (
            "material-backed-fluid.yaml",
            "localization-component.yaml",
        ):
            shutil.copyfile(FIXTURE_ROOT / name, self.registry / name)
        _git(self.repository, "init", "-q")
        _git(self.repository, "config", "user.email", "blueprints@example.invalid")
        _git(self.repository, "config", "user.name", "Blueprints Test")
        (self.repository / "README.md").write_text(
            "synthetic target\n", encoding="utf-8"
        )
        _git(self.repository, "add", "README.md")
        _git(self.repository, "commit", "-q", "-m", "initial")
        standards.build_registry(self.registry, asset_root=REPO_ROOT)
        self.ledger = self.root / "ledger.json"
        shutil.copyfile(CENTRAL_LEDGER, self.ledger)
        self.target = planner.capture_target_state(self.repository, "pack")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _intake(
        self,
        *,
        consent_revision: bool = False,
        parameters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "sequence": 0,
            "feature_family": "material-backed-fluid",
            "intent": {
                "operation": "create",
                "target_key": "susy:syntheticium",
                "desired_outcome": "Create a material-backed fluid.",
            },
            "parameters": parameters
            if parameters is not None
            else [
                {"name": "translation", "value": "Syntheticium"},
                {"name": "name", "value": "Syntheticium"},
            ],
            "requested_variants": [],
            "output_mode": "instructions",
            "consent": {
                "accept_compliant_revision": consent_revision,
                "allow_direct_apply": False,
            },
        }

    def _evidence(
        self,
        *,
        drift: str = "none",
        material_result: dict[str, Any] | None = None,
        allocation_occupied: list[int] | None = None,
        reconciliation: str = "identity-absent",
        include_component: bool = False,
    ) -> dict[str, Any]:
        queries = [
            {
                "standard_key": "material-backed-fluid",
                "query_id": "atlas-query:synthetic-material-builder",
                "result_id": "atlas-result:synthetic-material-builder",
                "availability": "available",
                "result": material_result
                if material_result is not None
                else {"registration": {"id_argument": 4000}},
            }
        ]
        if include_component:
            queries.append(
                {
                    "standard_key": "localization-component",
                    "query_id": "atlas-query:synthetic-language-root",
                    "result_id": "atlas-result:synthetic-language-root",
                    "availability": "available",
                    "result": {"paths": {"language": "assets/susy/lang/"}},
                }
            )
        return planner.build_planning_evidence(
            self.target["target_state_id"],
            queries=queries,
            allocation=[
                {
                    "domain_name": "susy-material-id",
                    "authority_id": "blueprints-allocation-ledger-v1",
                    "occupied_values": allocation_occupied or [],
                    "proposed_value": None,
                }
            ],
            reconciliation=[
                {
                    "standard_key": "material-backed-fluid",
                    "identity_kind": reconciliation,
                    "observed": (
                        None
                        if reconciliation == "identity-absent"
                        else {"registry_name": "syntheticium"}
                    ),
                }
            ],
            relevant_drift=drift,
        )

    def _engine(
        self,
        formatter: planner.FormatterRunner | None = None,
    ) -> planner.Planner:
        return planner.Planner(
            registry_root=self.registry,
            asset_root=REPO_ROOT,
            ledger_path=self.ledger,
            target_repository=self.repository,
            sealed_store=planner.SealedStore(self.sealed_root),
            formatter_runner=formatter,
        )

    @staticmethod
    def _identity_formatter(
        _definition: dict[str, Any], content: dict[str, bytes]
    ) -> dict[str, bytes]:
        return content

    def test_contract_schemas_are_valid(self) -> None:
        for name in (
            "blueprints-target-manifest-v1.schema.json",
            "blueprints-planning-evidence-v1.schema.json",
            "blueprints-sealed-manifest-v1.schema.json",
            "blueprints-plan-v1.schema.json",
        ):
            schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)

    def test_target_capture_binds_head_index_worktree_and_untracked(self) -> None:
        readme = self.repository / "README.md"
        readme.write_text("staged\n", encoding="utf-8")
        _git(self.repository, "add", "README.md")
        readme.write_text("unstaged\n", encoding="utf-8")
        (self.repository / "new.txt").write_text("untracked\n", encoding="utf-8")
        captured = planner.capture_target_state(self.repository, "pack")
        rows = {row["path"]: row for row in captured["entries"]}
        self.assertEqual(
            rows["README.md"]["layers"],
            ["tracked", "staged", "unstaged"],
        )
        self.assertEqual(rows["new.txt"]["layers"], ["untracked"])
        self.assertNotEqual(
            rows["README.md"]["head_sha256"],
            rows["README.md"]["index_sha256"],
        )
        self.assertNotEqual(
            rows["README.md"]["index_sha256"],
            rows["README.md"]["worktree_sha256"],
        )
        self.assertTrue(captured["dirty"])
        self.assertNotEqual(captured["target_state_id"], self.target["target_state_id"])

    def test_canonical_intake_is_order_independent_and_consent_is_exact(self) -> None:
        first = self._intake()
        second = copy.deepcopy(first)
        second["parameters"].reverse()
        self.assertEqual(
            planner.compile_request(first, self.target),
            planner.compile_request(second, self.target),
        )
        direct = self._intake()
        direct["output_mode"] = "direct-apply"
        with self.assertRaisesRegex(
            planner.PlannerDiagnostic, "BPP126_DIRECT_APPLY_CONSENT"
        ):
            planner.compile_request(direct, self.target)

    def test_ready_plan_is_deterministic_allocated_and_sealed(self) -> None:
        ledger_before = self.ledger.read_bytes()
        engine = self._engine(self._identity_formatter)
        first = engine.execute(
            self._intake(),
            self.target,
            self._evidence(allocation_occupied=[4000, 4001]),
        )
        second = engine.execute(
            self._intake(),
            self.target,
            self._evidence(allocation_occupied=[4000, 4001]),
        )
        self.assertEqual(first, second)
        self.assertEqual(first["plan"]["status"], "ready")
        self.assertEqual(
            [
                row["path"] for row in first["plan"]["operations"]
            ],
            [
                "groovy/material/syntheticium.groovy",
                "groovy/material/syntheticiumFluid.groovy",
            ],
        )
        effective = {
            row["name"]: row for row in first["plan"]["effective_parameters"]
        }
        self.assertEqual(effective["material_id"]["value"], 4002)
        self.assertEqual(effective["registry_name"]["value"], "syntheticium")
        self.assertEqual(first["plan"]["selection"]["variant_ids"], ["liquid"])
        candidate = first["candidate"]
        self.assertNotIn("path", json.dumps(candidate))
        self.assertNotIn("syntheticium.groovy", json.dumps(candidate))
        sealed = engine.sealed_store.read(candidate["sealed_locator"])
        self.assertEqual(sealed["plan_id"], first["plan"]["plan_id"])
        decoded = {
            row["path"]: row["content_base64"]
            for row in sealed["operations"]
        }
        self.assertEqual(
            set(decoded),
            {row["path"] for row in first["plan"]["operations"]},
        )
        self.assertEqual(self.ledger.read_bytes(), ledger_before)
        self.assertEqual(stat.S_IMODE(self.sealed_root.stat().st_mode), 0o700)
        object_files = list(self.sealed_root.rglob("*.json"))
        self.assertEqual(len(object_files), 1)
        self.assertEqual(stat.S_IMODE(object_files[0].stat().st_mode), 0o600)

    def test_component_selection_is_explicit_compatible_and_composed(self) -> None:
        result = self._engine(self._identity_formatter).execute(
            self._intake(),
            self.target,
            self._evidence(include_component=True),
            choices={"include_components": ["localization-component"]},
        )
        self.assertEqual(result["plan"]["status"], "ready")
        self.assertEqual(
            result["plan"]["selection"]["variant_ids"],
            ["liquid", "localization-component--default"],
        )
        self.assertEqual(len(result["plan"]["standards"]["components"]), 1)
        self.assertEqual(len(result["plan"]["operations"]), 4)

    def test_equal_rank_standard_requires_explicit_developer_choice(self) -> None:
        tied_registry = self.root / "tied-standards"
        tied_registry.mkdir()
        for source in self.registry.glob("*.yaml"):
            shutil.copyfile(source, tied_registry / source.name)
        primary_source = tied_registry / "material-backed-fluid.yaml"
        primary = yaml.safe_load(primary_source.read_text(encoding="utf-8"))
        primary["version"] = "1.0.1"
        second_source = tied_registry / "material-backed-fluid-1.0.1.yaml"
        second_source.write_text(
            yaml.safe_dump(primary, sort_keys=False),
            encoding="utf-8",
        )
        standards.build_registry(tied_registry, asset_root=REPO_ROOT)
        tied_engine = planner.Planner(
            registry_root=tied_registry,
            asset_root=REPO_ROOT,
            ledger_path=self.ledger,
            target_repository=self.repository,
            sealed_store=planner.SealedStore(self.sealed_root),
            formatter_runner=self._identity_formatter,
        )
        blocked = tied_engine.execute(
            self._intake(), self.target, self._evidence()
        )
        self.assertEqual(blocked["plan"]["status"], "blocked")
        self.assertTrue(
            blocked["plan"]["selection"]["developer_choice_required"]
        )
        self.assertIn(
            "BPP201_STANDARD_SELECTION_TIE",
            blocked["plan"]["blocked_reasons"],
        )
        registry = standards.check_registry(
            tied_registry, asset_root=REPO_ROOT
        )
        chosen_id = next(
            row["standard_id"]
            for row in registry["standards"]
            if row["kind"] == "primary"
        )
        selected = tied_engine.execute(
            self._intake(),
            self.target,
            self._evidence(),
            choices={"primary_standard_id": chosen_id},
        )
        self.assertEqual(selected["plan"]["status"], "ready")
        self.assertEqual(
            selected["plan"]["standards"]["primary"]["standard_id"],
            chosen_id,
        )

    def test_compliant_revision_is_never_silent(self) -> None:
        parameters = [
            {"name": "name", "value": "Syntheticium"},
            {"name": "registry_name", "value": "forced_name"},
        ]
        blocked = self._engine(self._identity_formatter).execute(
            self._intake(parameters=parameters),
            self.target,
            self._evidence(),
        )
        self.assertEqual(blocked["plan"]["status"], "blocked")
        self.assertIsNone(blocked["candidate"])
        self.assertIn(
            "BPP205_COMPLIANT_REVISION_REQUIRED",
            blocked["plan"]["blocked_reasons"],
        )
        accepted = self._engine(self._identity_formatter).execute(
            self._intake(
                parameters=parameters,
                consent_revision=True,
            ),
            self.target,
            self._evidence(),
        )
        self.assertEqual(accepted["plan"]["status"], "ready")
        derived = next(
            row
            for row in accepted["plan"]["effective_parameters"]
            if row["name"] == "registry_name"
        )
        self.assertEqual(derived["value"], "syntheticium")
        self.assertTrue(derived["accepted"])

    def test_atlas_drift_invariant_reconciliation_and_formatter_block(self) -> None:
        cases = [
            (
                self._evidence(drift="blocking"),
                self._identity_formatter,
                "BPP219_ATLAS_RELEVANT_DRIFT",
            ),
            (
                self._evidence(material_result={"registration": {}}),
                self._identity_formatter,
                "BPP218_ATLAS_INVARIANT",
            ),
            (
                self._evidence(reconciliation="identity-equivalent"),
                self._identity_formatter,
                "BPP222_RECONCILIATION_EQUIVALENT",
            ),
            (
                self._evidence(),
                None,
                "BPP232_FORMATTER_UNAVAILABLE",
            ),
        ]
        for evidence, formatter, code in cases:
            with self.subTest(code=code):
                result = self._engine(formatter).execute(
                    self._intake(), self.target, evidence
                )
                self.assertEqual(result["plan"]["status"], "blocked")
                self.assertIsNone(result["candidate"])
                self.assertTrue(
                    any(
                        reason == code or reason.startswith(code + ":")
                        for reason in result["plan"]["blocked_reasons"]
                    )
                )

    def test_formatter_and_target_races_fail_closed(self) -> None:
        counter = {"calls": 0}

        def unstable(
            _definition: dict[str, Any], content: dict[str, bytes]
        ) -> dict[str, bytes]:
            counter["calls"] += 1
            return {
                path: value + str(counter["calls"]).encode("ascii")
                for path, value in content.items()
            }

        result = self._engine(unstable).execute(
            self._intake(), self.target, self._evidence()
        )
        self.assertEqual(result["plan"]["status"], "blocked")
        self.assertIn(
            "BPP223_NONDETERMINISTIC_RENDER",
            result["plan"]["blocked_reasons"],
        )

        mutated = {"done": False}

        def target_mutator(
            _definition: dict[str, Any], content: dict[str, bytes]
        ) -> dict[str, bytes]:
            if not mutated["done"]:
                (self.repository / "raced.txt").write_text(
                    "changed during rendering\n", encoding="utf-8"
                )
                mutated["done"] = True
            return content

        with self.assertRaisesRegex(
            planner.PlannerDiagnostic, "BPP171_STALE_TARGET"
        ):
            self._engine(target_mutator).execute(
                self._intake(), self.target, self._evidence()
            )

    def test_no_standard_means_no_plan_or_candidate(self) -> None:
        empty = self.root / "empty-standards"
        empty.mkdir()
        (empty / "README.md").write_text("empty\n", encoding="utf-8")
        standards.build_registry(empty, asset_root=REPO_ROOT)
        result = planner.Planner(
            registry_root=empty,
            asset_root=REPO_ROOT,
            ledger_path=self.ledger,
            target_repository=self.repository,
            sealed_store=planner.SealedStore(self.sealed_root),
            formatter_runner=self._identity_formatter,
        ).execute(self._intake(), self.target, self._evidence())
        self.assertIsNone(result["plan"])
        self.assertIsNone(result["candidate"])
        self.assertEqual(
            result["diagnostics"], ["BPP200_NO_ADMITTED_STANDARD"]
        )

    def test_evidence_id_order_and_candidate_invalidation_are_bound(self) -> None:
        evidence = self._evidence()
        evidence["allocation"][0]["evidence_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            planner.PlannerDiagnostic, "BPP132_EVIDENCE_ID"
        ):
            self._engine(self._identity_formatter).execute(
                self._intake(), self.target, evidence
            )
        result = self._engine(self._identity_formatter).execute(
            self._intake(), self.target, self._evidence()
        )
        invalidation = planner.invalidate_candidate(
            result["candidate"],
            invalidated_ids=["blueprints-simulation:sha256:" + "1" * 64],
        )
        self.assertEqual(invalidation["next_edit_generation"], 1)
        self.assertIn(
            result["candidate"]["candidate_id"],
            invalidation["invalidated_ids"],
        )


if __name__ == "__main__":
    unittest.main()
