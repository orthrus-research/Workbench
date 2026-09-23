"""Executable material-fluid change workspace composition tests."""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256
import json
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from workbench_crucible.runtime_pair import FeatureRuntimePairPorts

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_developer_feature import ROOT, TARGET_PATHS, _bytes, _checkout

from workbench_shell.feature_change_workspace import (
    HEADER_FORMAT,
    START_RESULT_FORMAT,
    VIEW_FORMAT,
    apply_feature_change,
    open_feature_change,
    recover_feature_change,
    rollback_feature_change,
    run_feature_change_matrix,
    start_material_fluid_recipe_change,
    verify_feature_change,
)


SCHEMA_ROOT = ROOT / "modules/workbench-shell/schemas"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class _RuntimePorts(FeatureRuntimePairPorts):
    def __init__(self, *, unstable_control: bool = False) -> None:
        self.requests: list[dict] = []
        self.unstable_control = unstable_control

    def run_pair(
        self,
        request: dict,
        *,
        suite_root: Path,
        plan: dict,
        attempt_root: Path,
    ) -> dict:
        self.assert_runtime_inputs(suite_root, plan, attempt_root)
        self.requests.append(request)
        comparison = request["comparison"]
        order = request["order"]
        side = request["side"]
        first_role, second_role = request["roles"]
        assertion_names = (
            "material_registration",
            "fluid_registration",
            "recipe_registration",
            "unification_identity",
            "forbidden_delta_absent",
            "localization" if side == "client" else "dedicated_server_safe",
        )
        state = (
            "mismatch"
            if comparison == "aa" and self.unstable_control
            else "stable"
            if comparison == "aa"
            else "matches-candidate"
            if comparison == "post-apply"
            else "matches-baseline"
            if comparison == "post-rollback"
            else "observed-change"
        )
        owner_path = attempt_root / "owner.json"
        owner_path.write_text(
            json.dumps({
                "request_id": request["request_id"],
                "comparison_state": state,
            }, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        owner_raw = owner_path.read_bytes()
        return {
            "format": "workbench-feature-runtime-pair-result-v1",
            "request_id": request["request_id"],
            "side": side,
            "comparison": comparison,
            "order": order,
            "state": "complete",
            "outcome": "passed" if state != "mismatch" else "failed",
            "comparison_state": state,
            "observations": [
                {
                    "role": role,
                    "observation_id": f"fixture:{request['request_id']}:{ordinal}",
                    "assertions": {name: "observed" for name in assertion_names},
                }
                for ordinal, role in enumerate((first_role, second_role))
            ],
            "cleanup": {"contained": True, "owned_processes_running": False},
            "owner_refs": [{
                "owner_id": "fixture-runtime-owner",
                "record_id": f"fixture-owner:{request['request_id']}",
                "record_kind": "fixture-runtime-pair-v1",
                "uri": owner_path.as_uri(),
                "sha256": "sha256:" + sha256(owner_raw).hexdigest(),
                "size": len(owner_raw),
                "outcome": "passed" if state != "mismatch" else "failed",
            }],
        }

    @staticmethod
    def assert_runtime_inputs(suite_root: Path, plan: dict, attempt_root: Path) -> None:
        if suite_root != ROOT or plan.get("id") is None or not attempt_root.is_dir():
            raise AssertionError("runtime port did not receive exact retained context")


class _ExplodingRuntimePorts(FeatureRuntimePairPorts):
    def run_pair(self, request: dict, **_context: object) -> dict:
        raise RuntimeError(f"physical runtime unavailable for {request['side']}")


class FeatureChangeWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.state = self.root / "change-state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def start(self) -> dict:
        return start_material_fluid_recipe_change(
            ROOT,
            self.checkout,
            self.state,
            name="Thermal Solvent",
            color="#Aa44Cc",
            translation="Thermal Solvent Localized",
            symbol="ThermalSolventX",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="batch_reactor",
            input_fluid="steam",
            input_amount=750,
            output_amount=500,
            duration=320,
            voltage_tier="MV",
        )

    def test_full_matrix_then_apply_verify_rollback_and_recovery_uses_change_id_only(self) -> None:
        before = _bytes(self.checkout)
        started = self.start()
        self.assertEqual(START_RESULT_FORMAT, started["format"])
        _validator(
            "workbench-feature-change-start-result-v1.schema.json"
        ).validate(started)
        self.assertEqual(before, _bytes(self.checkout))
        ports = _RuntimePorts()

        matrix = run_feature_change_matrix(
            ROOT, self.state, started["change_id"], ports=ports
        )

        self.assertEqual("passed", matrix["outcome"])
        _validator(
            "workbench-feature-change-runtime-matrix-v1.schema.json"
        ).validate(matrix)
        self.assertEqual(
            [
                ("aa", "client", "baseline-first"),
                ("aa", "server", "baseline-first"),
                ("ab", "client", "baseline-first"),
                ("ab", "server", "baseline-first"),
                ("ab", "client", "candidate-first"),
                ("ab", "server", "candidate-first"),
            ],
            [
                (row["comparison"], row["side"], row["order"])
                for row in ports.requests
            ],
        )
        self.assertEqual(before, _bytes(self.checkout))

        applied = apply_feature_change(
            ROOT,
            self.state,
            started["change_id"],
            consent_plan_id=started["plan_id"],
        )
        self.assertEqual("applied", applied["state"])
        verified = verify_feature_change(
            ROOT, self.state, started["change_id"], ports=ports
        )
        self.assertEqual("verified", verified["state"])
        restored = rollback_feature_change(
            ROOT, self.state, started["change_id"], ports=ports
        )
        self.assertEqual("restored", restored["state"])
        self.assertEqual(before, _bytes(self.checkout))
        recovered = recover_feature_change(
            self.state, started["change_id"]
        )
        self.assertEqual("not-needed", recovered["state"])

        reopened = open_feature_change(self.state, started["change_id"])
        self.assertEqual(VIEW_FORMAT, reopened["format"])
        self.assertEqual(HEADER_FORMAT, reopened["header"]["format"])
        _validator("workbench-feature-change-header-v1.schema.json").validate(
            reopened["header"]
        )
        _validator("workbench-feature-change-view-v1.schema.json").validate(reopened)
        event_validator = _validator(
            "workbench-feature-change-event-v1.schema.json"
        )
        for event in reopened["events"]:
            event_validator.validate(event)
        request_validator = _validator(
            "workbench-feature-runtime-pair-request-v1.schema.json"
        )
        result_validator = _validator(
            "workbench-feature-runtime-pair-result-v1.schema.json"
        )
        change_directory = Path(started["record_uri"].removeprefix("file://")).parent
        for request_path in sorted((change_directory / "runtime").glob("*/request.json")):
            request_validator.validate(json.loads(request_path.read_text(encoding="utf-8")))
            result_validator.validate(
                json.loads((request_path.parent / "result.json").read_text(encoding="utf-8"))
            )
        self.assertEqual("rolled-back", reopened["lifecycle"])
        self.assertEqual(
            ["started", "matrix-completed", "applied", "verified", "rolled-back", "recovery-checked"],
            [event["kind"] for event in reopened["events"]],
        )

    def test_unstable_aa_is_a_no_go_and_ab_never_runs(self) -> None:
        started = self.start()
        ports = _RuntimePorts(unstable_control=True)

        matrix = run_feature_change_matrix(
            ROOT, self.state, started["change_id"], ports=ports
        )

        self.assertEqual("no-go", matrix["outcome"])
        self.assertEqual(2, len(ports.requests))
        self.assertTrue(all(row["comparison"] == "aa" for row in ports.requests))
        opened = open_feature_change(self.state, started["change_id"])
        self.assertEqual("test-no-go", opened["lifecycle"])
        with self.assertRaisesRegex(ValueError, "passing runtime matrix"):
            apply_feature_change(
                ROOT,
                self.state,
                started["change_id"],
                consent_plan_id=started["plan_id"],
            )

    def test_runtime_owner_exception_is_retained_as_a_no_go(self) -> None:
        started = self.start()

        matrix = run_feature_change_matrix(
            ROOT,
            self.state,
            started["change_id"],
            ports=_ExplodingRuntimePorts(),
        )

        self.assertEqual("no-go", matrix["outcome"])
        self.assertEqual(2, len(matrix["cases"]))
        for row in matrix["cases"]:
            self.assertFalse(row["composition_passed"])
            self.assertEqual([], row["owner_refs"])
            self.assertIn("physical runtime unavailable", row["owner_error"])
        self.assertEqual(
            "test-no-go",
            open_feature_change(self.state, started["change_id"])["lifecycle"],
        )

    def test_later_edit_blocks_rollback_through_the_workspace_facade(self) -> None:
        started = self.start()
        run_feature_change_matrix(ROOT, self.state, started["change_id"], ports=_RuntimePorts())
        apply_feature_change(
            ROOT,
            self.state,
            started["change_id"],
            consent_plan_id=started["plan_id"],
        )
        target = self.checkout / TARGET_PATHS[0]
        target.write_bytes(target.read_bytes() + b"\n// later developer edit\n")

        blocked = rollback_feature_change(ROOT, self.state, started["change_id"])

        self.assertEqual("blocked", blocked["state"])
        self.assertIn(b"later developer edit", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
