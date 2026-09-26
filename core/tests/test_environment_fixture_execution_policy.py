"""Portable fixture execution policy is reviewed from retained Core inputs."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from unittest import TestCase, skipIf
from unittest.mock import patch
import sys

from workbench_core.environment_fixture_execution_policy import (
    reopen_fixture_execution_policy, review_fixture_execution_policy,
)
from workbench_core.environment_fixture_import import apply_fixture_import, plan_fixture_import
from workbench_core.environment_input_candidates import build_input_candidate
from workbench_core.environment_reconstruction import ReconstructionError, _seal

import test_environment_input_candidates as candidate_tests
from test_environment_fixture_import import _ImportFixtureOwner
from test_environment_reconstruction import _environment


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "profiles/platforms/cleanroom"


class _PortableFixtureOwner(_ImportFixtureOwner):
    def __init__(self, root: Path):
        super().__init__(root)
        self.lock["declaration_id"] = "cleanroom-generic-mod-daily-loop-fixture-v1"
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")
        base = root / "profiles/platforms/cleanroom"
        self.cleanup = base / "tools/clean_generic_mod_fixture.gradle"
        self.cleanup.write_bytes((PROFILE / "tools/clean_generic_mod_fixture.gradle").read_bytes())
        self.policy_path = base / "policies/generic-mod-fixture-execution-v1.json"
        self.policy_path.parent.mkdir()
        self.schema_path = base / "schemas/workbench-cleanroom-fixture-execution-policy-v1.schema.json"
        self.schema_path.write_bytes((PROFILE / "schemas/workbench-cleanroom-fixture-execution-policy-v1.schema.json").read_bytes())
        policy = json.loads((PROFILE / "policies/generic-mod-fixture-execution-v1.json").read_text(encoding="utf-8"))
        policy["fixture"] = {
            "declaration_id": self.lock["declaration_id"],
            "tree_digest": self.lock["declared_values"]["tree_digest"],
        }
        policy["cleanup_init"]["sha256"] = "sha256:" + sha256(self.cleanup.read_bytes()).hexdigest()
        policy["cleanup_init"]["size"] = self.cleanup.stat().st_size
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.inputs += tuple({
            "kind": kind, "path": path, "display_path": path.relative_to(root).as_posix(),
        } for kind, path in (
            ("fixture-cleanup-init", self.cleanup),
            ("fixture-execution-policy", self.policy_path),
            ("fixture-execution-schema", self.schema_path),
        ))

    def read_execution_policy(self):
        return json.loads(self.policy_path.read_text(encoding="utf-8"))

    def validate_execution_policy(self, value):
        if value != self.read_execution_policy():
            raise ValueError("execution policy changed")
        return value


@skipIf(not sys.platform.startswith("linux"), "fixture import is a Linux/WSL slice")
class EnvironmentFixtureExecutionPolicyTests(TestCase):
    def setUp(self) -> None:
        candidate_tests.EnvironmentInputCandidateTests.setUp(self)
        self.suite = self.root / "suite"
        self.workspace = self.root / "workspace"
        self.environment = _environment(self.root / "user")
        self.owner = _PortableFixtureOwner(self.root)
        for name in (
            "workbench_core.environment_input_candidates.require_profile_extension",
            "workbench_core.environment_fixture_import.require_profile_extension",
        ):
            replacement = patch(name, return_value=self.owner)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.candidate = build_input_candidate(
            self.share, wheels=(self.wheel,), profile_owner_id="cleanroom",
        )

    def _import(self):
        plan = plan_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            environment=self.environment,
        )
        return apply_fixture_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            expected_plan_id=plan["plan_id"], environment=self.environment,
        )

    def _review(self, result):
        return review_fixture_execution_policy(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_six_witness_import_reopens_without_original_profile_sources(self) -> None:
        result = self._import()
        self.assertEqual(7, len(result["files"]))
        review = self._review(result)
        self.assertEqual("review-only-unqualified", review["state"])
        self.assertEqual("9.6.1", review["policy"]["gradle"]["version"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], review["unresolved_inputs"])
        for row in self.owner.inputs:
            row["path"].unlink()
        (self.owner.root / "src/main.txt").unlink()
        self.assertEqual(review, reopen_fixture_execution_policy(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            fixture_result_resource_id=result["resource"]["resource_id"],
            expected_review_id=review["review_id"], environment=self.environment,
        ))
        with self.assertRaisesRegex(ReconstructionError, "review changed"):
            reopen_fixture_execution_policy(
                self.suite, self.share, self.candidate, workspace=self.workspace,
                fixture_result_resource_id=result["resource"]["resource_id"],
                expected_review_id="wrong", environment=self.environment,
            )

    def test_retained_witness_change_refuses_review(self) -> None:
        result = self._import()
        retained = Path(result["tree_path"]) / self.owner.policy_path.relative_to(self.root)
        retained.write_bytes(b"{}")
        with self.assertRaises(ReconstructionError):
            self._review(result)

    def test_retained_witness_redirect_refuses_review(self) -> None:
        result = self._import()
        retained = Path(result["tree_path"]) / self.owner.policy_path.relative_to(self.root)
        retained.unlink()
        retained.symlink_to(self.owner.policy_path)
        with self.assertRaises(ReconstructionError):
            self._review(result)

    def test_policy_contract_refuses_java8_even_with_owner_candidate(self) -> None:
        policy = self.owner.read_execution_policy()
        policy["java"]["required_major"] = 8
        self.owner.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.candidate = build_input_candidate(
            self.share, wheels=(self.wheel,), profile_owner_id="cleanroom",
        )
        result = self._import()
        with self.assertRaisesRegex(ReconstructionError, "schema|V1 contract"):
            self._review(result)

    def test_legacy_three_witness_candidate_has_no_portable_policy(self) -> None:
        body = deepcopy({key: value for key, value in self.candidate.items()
                         if key != "candidate_id"})
        body["profile_fixture"]["sources"] = [
            row for row in body["profile_fixture"]["sources"]
            if row["kind"] in {"fixture-owner-lock", "fixture-owner-schema", "profile-preflight-tool"}
        ]
        self.candidate = _seal(body, "workbench-environment-input-candidate", "candidate_id")
        with self.assertRaisesRegex(ReconstructionError, "portable execution witnesses"):
            review_fixture_execution_policy(
                self.suite, self.share, self.candidate, workspace=self.workspace,
                fixture_result_resource_id="unused", environment=self.environment,
            )
