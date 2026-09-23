"""Paired recipe-change disposable runtime orchestration checks."""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from test_developer_source_feature import ROOT, _checkout

from workbench_blueprints.profile_construction import recipe_change_authority
from workbench_shell.developer_feature_cli import build_parser
from workbench_shell.developer_recipe_runtime import (
    RecipeRuntimeComparisonError,
    RecipeRuntimeLifecycleUnresolvedError,
    run_recipe_change_runtime_comparison,
    validate_recipe_change_runtime_comparison,
)
from workbench_shell import developer_recipe_runtime as runtime
from workbench_shell import runtime_observe as runtime_observe_module


class _Observer:
    ASSESSMENT_FORMAT = "workbench-supersymmetry-recipe-runtime-assessment-v1"
    COMPARISON_FORMAT = "workbench-supersymmetry-recipe-runtime-comparison-v1"
    CONTRACT_FORMAT = "workbench-supersymmetry-recipe-observation-contract-v1"
    MARKER_PREFIX = "[WORKBENCH-RECIPE-CHANGE-RUNTIME-V1]"
    PACK_PROFILE_ID = "workbench-pack:supersymmetry"
    PLATFORM_PROFILE_ID = "workbench-platform:cleanroom:provisional"

    @staticmethod
    def derive_recipe_change_probe_spec(_contract, *, projection_role):
        return SimpleNamespace(
            probe_id=f"probe-{projection_role}",
            contract_id="contract",
            projection_role=projection_role,
            physical_side="client",
            source_plan_id="plan",
        )

    @staticmethod
    def build_recipe_change_probe(spec):
        return f"// {spec.projection_role}\n".encode()

    @staticmethod
    def build_recipe_change_probe_overlay(spec):
        return {
            "patch_id": f"overlay-{spec.projection_role}",
            "target": {
                "path": ".minecraft/groovy/postInit/utils/Probe.groovy",
                "must_be_absent": True,
            },
            "source": {"path": "RecipeChangeAssertion.groovy"},
        }

    @staticmethod
    def interpret_recipe_change_observation(spec, *, groovy_log_bytes, capture):
        if groovy_log_bytes != b"groovy-log":
            raise AssertionError("Groovy log bytes changed")
        return {
            "assessment_id": f"assessment-{spec.projection_role}",
            "capture": dict(capture),
            "projection_role": spec.projection_role,
            "state": "observed",
        }

    @staticmethod
    def validate_recipe_change_assessment(spec, assessment):
        if assessment.get("projection_role") != spec.projection_role:
            raise ValueError("assessment role changed")
        return dict(assessment)

    @staticmethod
    def compare_recipe_change_observations(
        _baseline_spec,
        baseline,
        _candidate_spec,
        candidate,
    ):
        return {
            "baseline_assessment_id": baseline["assessment_id"],
            "candidate_assessment_id": candidate["assessment_id"],
            "state": "observed-change",
        }

    @classmethod
    def validate_recipe_change_comparison(
        cls,
        baseline_spec,
        baseline,
        candidate_spec,
        candidate,
        comparison,
    ):
        expected = cls.compare_recipe_change_observations(
            baseline_spec, baseline, candidate_spec, candidate
        )
        if dict(comparison) != expected:
            raise ValueError("comparison changed")
        return dict(comparison)


class RecipeRuntimeComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.state = self.root / "state"
        self.authority = recipe_change_authority("supersymmetry")
        self.plan = self.authority.build_recipe_change_plan(
            ROOT,
            self.checkout,
            mutation="add",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="batch_reactor",
            fluid_inputs=[{"name": "steam", "amount": 1000}],
            fluid_outputs=[{"name": "water", "amount": 1000}],
            duration=100,
            voltage_tier="LV",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runtime_result(
        self,
        role: str,
        *,
        outcome: str = "completed",
        launch_outcome: str = "checkpoint-reached",
        lifecycle_state: str = "exited",
        lifecycle_checks: list[dict[str, object]] | None = None,
        analysis_failures: list[dict[str, object]] | None = None,
        launcher_process_state: str = "running",
    ) -> dict[str, object]:
        instance_id = f"workbench-supersymmetry-{role}"
        attached = lifecycle_state not in {
            "attach-timeout",
            "launch-ended-before-attach",
        }
        lifecycle = {
            "state": lifecycle_state,
            "observed_pids": [42] if attached else [],
            "attached_at": "2026-08-16T00:00:00.000Z" if attached else None,
        }
        checks = (
            [{"state": "absent", "observed_pids": []}]
            if lifecycle_checks is None
            else lifecycle_checks
        )
        return {
            "outcome": outcome,
            "role": role,
            "receipt": {
                "outcome": outcome,
                "launch": {
                    "instance_id": instance_id,
                    "process_observation": lifecycle,
                },
                "analysis_lifecycle_checks": checks,
                "analysis_failures": (
                    [] if analysis_failures is None else analysis_failures
                ),
            },
            "launch_receipt": {
                "outcome": launch_outcome,
                "launcher": {
                    "executable_uri": (
                        self.root / "PrismLauncher.exe"
                    ).as_uri(),
                    "host": {"os": "windows"},
                    "instance_id": instance_id,
                    "process_state": launcher_process_state,
                },
                "launch_policy": {
                    "observation_boundary": "projected-client-process-exit"
                },
                "observation": {"session_exit": lifecycle},
            },
        }

    def _patches(
        self,
        *,
        fail_role: str | None = None,
        capture_completed: bool = True,
    ):
        def runtime_plan(_suite, staged, **_kwargs):
            stage = Path(staged)
            import subprocess

            revision = subprocess.check_output(
                ["git", "-C", str(stage), "rev-parse", "HEAD"], text=True
            ).strip()
            return {
                "state": "ready",
                "blockers": [],
                "plan_id": "sha256:" + "a" * 64,
                "workspace": {
                    "root_uri": stage.as_uri(),
                    "revision": revision,
                    "dirty": False,
                },
            }

        def observe(_suite, staged, **_kwargs):
            role = Path(staged).parent.name
            return self._runtime_result(role)

        def captured(_root, _result, _probe, _policy, _plan, staged):
            role_root = Path(staged).parent
            log = role_root / "captured-groovy.log"
            receipt = role_root / "captured-runtime.json"
            log.write_bytes(b"groovy-log")
            receipt.write_bytes(b"{}\n")
            from hashlib import sha256

            return b"groovy-log", {
                "groovy_log_sha256": sha256(b"groovy-log").hexdigest(),
                "groovy_log_uri": log.as_uri(),
                "session_receipt_sha256": sha256(b"{}\n").hexdigest(),
                "session_receipt_size": 3,
                "session_receipt_uri": receipt.as_uri(),
            }

        assertions = {
            "fml_client_load": {"state": "observed", "meaning": "loaded"}
        }

        def summarize(result, **_kwargs):
            if result["role"] == fail_role:
                raise RuntimeError(f"{fail_role} closed-runtime validation failed")
            return {"state": "observed"}, assertions

        def receipt_evidence(result):
            role = result["role"]
            digit = "b" if role == "baseline" else "c"
            role_root = self.state / "runtime/recipe-change-comparisons" / role
            return {
                "final_launch_receipt": {
                    "id": "sha256:" + digit * 64,
                    "sha256": digit * 64,
                    "size": 3,
                    "uri": (role_root / "runtime-launch-v3.json").as_uri(),
                },
                "runtime_session_receipt": {
                    "id": "sha256:" + digit * 64,
                    "sha256": digit * 64,
                    "size": 3,
                    "uri": (role_root / "runtime-observation-v1.json").as_uri(),
                },
            }

        def retained(
            _suite,
            runtime_summary,
            _assessment,
            **kwargs,
        ):
            role = kwargs["projection_role"]
            role_root = kwargs["attempt_root"] / role
            log = role_root / "captured-groovy.log"
            from hashlib import sha256

            return {
                "assertions": assertions,
                "capture": {
                    "groovy_log_sha256": sha256(b"groovy-log").hexdigest(),
                    "groovy_log_uri": log.as_uri(),
                    "session_receipt_sha256": sha256(b"{}\n").hexdigest(),
                    "session_receipt_size": 3,
                    "session_receipt_uri": (role_root / "captured-runtime.json").as_uri(),
                },
                "groovy_log_bytes": b"groovy-log",
                "runtime_plan": {},
                "runtime_result": {"outcome": "completed", "role": role},
                "runtime_summary": dict(runtime_summary),
                "receipt_evidence": receipt_evidence({"role": role}),
            }

        return (
            patch.object(runtime, "_observer", return_value=_Observer),
            patch.object(
                runtime,
                "preflight_close_observer_launcher",
                return_value={"host": {"os": "windows"}},
            ),
            patch.object(runtime, "plan_project_runtime", side_effect=runtime_plan),
            patch.object(runtime, "observe_project_runtime", side_effect=observe),
            patch.object(
                runtime_observe_module,
                "_windows_runtime_pids",
                return_value=frozenset(),
            ),
            patch.object(
                runtime,
                "material_fluid_runtime_compatibility_policy",
                return_value=((self.root / "compatibility-overlay.json",), {"patches": []}),
            ),
            patch.object(
                runtime,
                "summarize_disposable_runtime",
                side_effect=summarize,
            ),
            patch.object(
                runtime,
                "captured_disposable_groovy_log",
                side_effect=captured,
            ),
            patch.object(
                runtime,
                "disposable_runtime_capture_completed",
                return_value=capture_completed,
            ),
            patch.object(
                runtime,
                "disposable_runtime_receipt_evidence",
                side_effect=receipt_evidence,
            ),
            patch.object(
                runtime,
                "validate_retained_disposable_runtime",
                side_effect=retained,
            ),
        )

    def _run(
        self,
        *,
        fail_role: str | None = None,
        capture_completed: bool = True,
        **kwargs,
    ):
        patches = self._patches(
            fail_role=fail_role,
            capture_completed=capture_completed,
        )
        launcher_root = kwargs.pop("launcher_root", self.root / "launcher")
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            return run_recipe_change_runtime_comparison(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=launcher_root,
                **kwargs,
            )

    @staticmethod
    def _reseal_and_retain(record, mutate):
        body = deepcopy(record)
        body.pop("id")
        mutate(body)
        forged = runtime._seal(body)
        receipt = Path(forged["target"]["receipt_uri"].removeprefix("file://"))
        receipt.write_text(
            json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return forged

    def test_pair_stages_one_source_tree_and_retains_complete_comparison(self) -> None:
        source_before = (
            self.checkout / "groovy/postInit/chemistry/Probe.groovy"
        ).read_bytes()
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            record = run_recipe_change_runtime_comparison(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=self.root / "launcher",
            )
            self.assertEqual(
                record,
                validate_recipe_change_runtime_comparison(ROOT, record, self.plan),
            )

        self.assertEqual("complete", record["state"])
        self.assertEqual("runtime-comparison-completed", record["outcome"])
        self.assertEqual(
            record["stages"]["baseline"]["source_tree"],
            record["stages"]["candidate"]["source_tree"],
        )
        self.assertEqual(
            record["stages"]["baseline"]["revision"],
            record["stages"]["baseline"]["baseline_revision"],
        )
        self.assertNotEqual(
            record["stages"]["candidate"]["revision"],
            record["stages"]["candidate"]["baseline_revision"],
        )
        self.assertEqual(
            source_before,
            (self.checkout / "groovy/postInit/chemistry/Probe.groovy").read_bytes(),
        )
        receipt = Path(
            record["target"]["receipt_uri"].removeprefix("file://")
        )
        self.assertTrue(receipt.is_file())

    def test_public_parser_exposes_explicit_pair_order_and_client_inputs(self) -> None:
        arguments = build_parser().parse_args(
            [
                "compare-runtime",
                "recipe-change",
                self.plan["id"],
                "--consent",
                self.plan["id"],
                "--order",
                "candidate-first",
                "--launcher-executable",
                str(self.root / "launcher.exe"),
                "--launcher-root",
                str(self.root / "launcher"),
            ]
        )
        self.assertEqual("compare-runtime", arguments.action)
        self.assertEqual("recipe-change", arguments.family)
        self.assertEqual("candidate-first", arguments.order)

    def test_one_failed_side_is_retained_as_incomplete_not_comparable(self) -> None:
        record = self._run(fail_role="baseline", order="candidate-first")

        self.assertEqual("incomplete", record["state"])
        self.assertEqual("failed", record["outcome"])
        self.assertEqual("incomplete", record["sides"]["baseline"]["state"])
        self.assertEqual("complete", record["sides"]["candidate"]["state"])
        self.assertIsNone(record["comparison"])

    def test_exceptional_prelaunch_side_stops_pair_and_marks_later_not_run(self) -> None:
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            run_side = stack.enter_context(
                patch.object(
                    runtime,
                    "_run_side",
                    side_effect=RuntimeError("baseline prelaunch failed"),
                )
            )
            record = run_recipe_change_runtime_comparison(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=self.root / "launcher",
            )

        self.assertEqual(1, run_side.call_count)
        self.assertEqual("failed", record["outcome"])
        self.assertEqual("failed", record["sides"]["baseline"]["outcome"])
        self.assertEqual("not-run", record["sides"]["candidate"]["outcome"])
        self.assertEqual(
            {"state": "not-run"}, record["sides"]["candidate"]["runtime"]
        )
        self.assertIsNone(record["sides"]["candidate"]["retained_evidence"])
        attempt = Path(record["target"]["attempt_root_uri"].removeprefix("file://"))
        self.assertFalse((attempt / "candidate/cleanroom-runtime").exists())
        self.assertFalse((attempt / "candidate/observation-probe").exists())
        self.assertIsNone(record["comparison"])

    def test_unknown_runtime_lifecycles_never_publish_or_run_second_side(
        self,
    ) -> None:
        cases = (
            (
                "session-timeout",
                {
                    "outcome": "timed-out",
                    "lifecycle_state": "session-timeout",
                },
            ),
            (
                "attach-timeout",
                {
                    "outcome": "timed-out",
                    "lifecycle_state": "attach-timeout",
                },
            ),
            (
                "probe-failed",
                {
                    "outcome": "probe-failed",
                    "lifecycle_state": "probe-failed",
                },
            ),
            (
                "crash-before-attach",
                {
                    "outcome": "launch-failed",
                    "launch_outcome": "failed",
                    "lifecycle_state": "launch-ended-before-attach",
                    "lifecycle_checks": [],
                    "launcher_process_state": "exited",
                },
            ),
            (
                "post-exit-process-reappeared",
                {
                    "outcome": "analysis-incomplete",
                    "lifecycle_checks": [
                        {"state": "process-reappeared", "observed_pids": [99]}
                    ],
                    "analysis_failures": [
                        {"kind": "live-root-lifecycle-invalid"}
                    ],
                },
            ),
        )
        for label, overrides in cases:
            with self.subTest(label=label):
                roles: list[str] = []

                def observe(_suite, staged, **_kwargs):
                    role = Path(staged).parent.name
                    roles.append(role)
                    return self._runtime_result(role, **overrides)

                patches = self._patches()
                with ExitStack() as stack:
                    for active in patches:
                        stack.enter_context(active)
                    stack.enter_context(
                        patch.object(
                            runtime,
                            "observe_project_runtime",
                            side_effect=observe,
                        )
                    )
                    with self.assertRaisesRegex(
                        RecipeRuntimeLifecycleUnresolvedError,
                        "no comparison receipt was published",
                    ):
                        run_recipe_change_runtime_comparison(
                            ROOT,
                            self.plan,
                            self.state,
                            consent_plan_id=self.plan["id"],
                            launcher_executable=self.root / "launcher.exe",
                            launcher_root=self.root / "launcher",
                        )

                self.assertEqual(["baseline"], roles)
                self.assertEqual([], list(self.state.rglob("receipt.json")))
                self.assertEqual([], list(self.state.rglob(".receipt.json.tmp")))

    def test_observer_exception_and_live_current_process_never_publish(self) -> None:
        cases = ("observer-exception", "live-current-process")
        for label in cases:
            with self.subTest(label=label):
                patches = self._patches()
                with ExitStack() as stack:
                    for active in patches:
                        stack.enter_context(active)
                    if label == "observer-exception":
                        stack.enter_context(
                            patch.object(
                                runtime,
                                "observe_project_runtime",
                                side_effect=RuntimeError("attach failed"),
                            )
                        )
                    else:
                        stack.enter_context(
                            patch.object(
                                runtime_observe_module,
                                "_windows_runtime_pids",
                                return_value=frozenset({99}),
                            )
                        )
                    with self.assertRaises(
                        RecipeRuntimeLifecycleUnresolvedError
                    ):
                        run_recipe_change_runtime_comparison(
                            ROOT,
                            self.plan,
                            self.state,
                            consent_plan_id=self.plan["id"],
                            launcher_executable=self.root / "launcher.exe",
                            launcher_root=self.root / "launcher",
                        )

                self.assertEqual([], list(self.state.rglob("receipt.json")))

    def test_closed_non_lifecycle_analysis_failure_can_run_both_sides(self) -> None:
        roles: list[str] = []

        def observe(_suite, staged, **_kwargs):
            role = Path(staged).parent.name
            roles.append(role)
            return self._runtime_result(
                role,
                outcome=("analysis-incomplete" if role == "baseline" else "completed"),
                analysis_failures=(
                    [{"kind": "anvil-observation-set-failed"}]
                    if role == "baseline"
                    else []
                ),
            )

        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            stack.enter_context(
                patch.object(
                    runtime,
                    "observe_project_runtime",
                    side_effect=observe,
                )
            )
            record = run_recipe_change_runtime_comparison(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=self.root / "launcher",
            )

        self.assertEqual(["baseline", "candidate"], roles)
        self.assertEqual("runtime-comparison-completed", record["outcome"])

    def test_launcher_readiness_fails_before_attempt_or_staging(self) -> None:
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            stack.enter_context(
                patch.object(
                    runtime,
                    "preflight_close_observer_launcher",
                    side_effect=runtime_observe_module.RuntimeObserveError(
                        "Windows process inventory is unavailable"
                    ),
                )
            )
            prepare = stack.enter_context(
                patch.object(runtime, "prepare_feature_runtime_attempt_parent")
            )
            stage = stack.enter_context(
                patch.object(runtime, "stage_reviewed_feature_plan")
            )
            with self.assertRaisesRegex(
                RecipeRuntimeComparisonError,
                "process inventory is unavailable",
            ):
                run_recipe_change_runtime_comparison(
                    ROOT,
                    self.plan,
                    self.state,
                    consent_plan_id=self.plan["id"],
                    launcher_executable=self.root / "launcher.exe",
                    launcher_root=self.root / "launcher",
                )

        prepare.assert_not_called()
        stage.assert_not_called()
        self.assertEqual([], list(self.state.rglob("receipt.json")))

    def test_ordinary_runtime_mismatches_still_run_both_sides(self) -> None:
        record = self._run(capture_completed=False)

        self.assertEqual("runtime-comparison-mismatch", record["outcome"])
        self.assertEqual(
            ["runtime-mismatch", "runtime-mismatch"],
            [record["sides"][role]["outcome"] for role in ("baseline", "candidate")],
        )
        self.assertIsNotNone(record["comparison"])

    def test_assertion_failure_retains_and_reopens_closed_runtime_evidence(self) -> None:
        original = _Observer.interpret_recipe_change_observation

        def interpret(spec, *, groovy_log_bytes, capture):
            if spec.projection_role == "baseline":
                raise ValueError("marker assertion failed")
            return original(
                spec, groovy_log_bytes=groovy_log_bytes, capture=capture
            )

        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            stack.enter_context(
                patch.object(
                    _Observer,
                    "interpret_recipe_change_observation",
                    side_effect=interpret,
                )
            )
            record = run_recipe_change_runtime_comparison(
                ROOT,
                self.plan,
                self.state,
                consent_plan_id=self.plan["id"],
                launcher_executable=self.root / "launcher.exe",
                launcher_root=self.root / "launcher",
            )

            baseline = record["sides"]["baseline"]
            self.assertEqual("failed", record["outcome"])
            self.assertEqual("assertion", baseline["error"]["phase"])
            self.assertEqual("observed", baseline["runtime"]["state"])
            self.assertIsNotNone(baseline["probe"])
            self.assertIsNotNone(baseline["retained_evidence"]["groovy_log"])
            self.assertTrue(
                baseline["retained_evidence"]["final_launch_receipt"]["uri"].startswith(
                    "file:"
                )
            )
            self.assertEqual("complete", record["sides"]["candidate"]["state"])

            forged = self._reseal_and_retain(
                record,
                lambda body: body["sides"]["baseline"]["retained_evidence"][
                    "final_launch_receipt"
                ].__setitem__("id", "sha256:" + "f" * 64),
            )
            with self.assertRaisesRegex(ValueError, "retained evidence changed"):
                validate_recipe_change_runtime_comparison(ROOT, forged, self.plan)

    def test_v2_binds_exact_current_observer_protocol(self) -> None:
        record = self._run()
        self.assertEqual(2, record["schema_version"])
        self.assertRegex(
            record["observer_protocol"]["id"],
            r"^workbench-recipe-change-runtime-observer-protocol:sha256:[0-9a-f]{64}$",
        )
        forged = self._reseal_and_retain(
            record,
            lambda body: body["observer_protocol"].__setitem__(
                "decoder_sha256", "f" * 64
            ),
        )
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            with self.assertRaisesRegex(ValueError, "observer protocol changed"):
                validate_recipe_change_runtime_comparison(ROOT, forged, self.plan)

    def test_noncurrent_record_schema_is_rejected(self) -> None:
        record = self._run()

        def change_schema(body):
            body["schema_version"] = 1

        noncurrent = self._reseal_and_retain(record, change_schema)
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            with self.assertRaisesRegex(ValueError, "identity or shape changed"):
                validate_recipe_change_runtime_comparison(ROOT, noncurrent, self.plan)

    def test_resealed_record_cannot_add_completion_assertions(self) -> None:
        record = self._run()
        forged = self._reseal_and_retain(
            record,
            lambda body: body["assertions"].__setitem__("trust_me", True),
        )
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            with self.assertRaisesRegex(ValueError, "assertions changed"):
                validate_recipe_change_runtime_comparison(ROOT, forged, self.plan)

    def test_resealed_record_cannot_relabel_side_state(self) -> None:
        record = self._run()

        def mutate(body):
            body["sides"]["baseline"]["state"] = "incomplete"
            body["sides"]["baseline"]["outcome"] = "runtime-mismatch"
            body["state"] = "incomplete"
            body["outcome"] = "runtime-comparison-mismatch"
            body["assertions"]["both_client_cold_starts_observed"] = False

        forged = self._reseal_and_retain(record, mutate)
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            with self.assertRaisesRegex(ValueError, "state contradicts"):
                validate_recipe_change_runtime_comparison(ROOT, forged, self.plan)

    def test_resealed_record_cannot_rebind_attempt_id(self) -> None:
        record = self._run()
        forged = self._reseal_and_retain(
            record,
            lambda body: body.__setitem__("attempt_id", "uuid:" + "f" * 32),
        )
        with self.assertRaisesRegex(ValueError, "escaped its attempt"):
            validate_recipe_change_runtime_comparison(ROOT, forged, self.plan)

    def test_assessment_is_reinterpreted_from_retained_groovy_log(self) -> None:
        record = self._run()
        forged = self._reseal_and_retain(
            record,
            lambda body: body["sides"]["baseline"]["assessment"].__setitem__(
                "state", "forged-observed"
            ),
        )
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            with self.assertRaisesRegex(ValueError, "differs from its Groovy log"):
                validate_recipe_change_runtime_comparison(ROOT, forged, self.plan)

    def test_retained_stage_bytes_are_reopened(self) -> None:
        record = self._run()
        candidate = Path(
            record["stages"]["candidate"]["workspace_uri"].removeprefix("file://")
        )
        output = candidate / self.plan["operations"][0]["path"]
        output.write_bytes(output.read_bytes() + b"// drift\n")
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            with self.assertRaisesRegex(ValueError, "retained Git stage changed"):
                validate_recipe_change_runtime_comparison(ROOT, record, self.plan)

    def test_historical_validation_does_not_require_live_source(self) -> None:
        record = self._run()
        self.checkout.rename(self.root / "source-moved-after-runtime")
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            self.assertEqual(
                record,
                validate_recipe_change_runtime_comparison(ROOT, record, self.plan),
            )

    def test_staging_failure_never_publishes_a_receipt(self) -> None:
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            stack.enter_context(
                patch.object(
                    runtime,
                    "stage_reviewed_feature_plan",
                    side_effect=RuntimeError("staging failed"),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "staging failed"):
                run_recipe_change_runtime_comparison(
                    ROOT,
                    self.plan,
                    self.state,
                    consent_plan_id=self.plan["id"],
                    launcher_executable=self.root / "launcher.exe",
                    launcher_root=self.root / "launcher",
                )
        self.assertEqual([], list(self.state.rglob("receipt.json")))

    def test_tampered_stage_is_rejected_before_either_runtime_side(self) -> None:
        original_stage = runtime.stage_reviewed_feature_plan

        def tampered_stage(*args, **kwargs):
            result = original_stage(*args, **kwargs)
            if kwargs.get("apply_operations") is True:
                workspace = Path(args[1])
                target = workspace / self.plan["operations"][0]["path"]
                target.write_bytes(target.read_bytes() + b"// prelaunch drift\n")
            return result

        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            stack.enter_context(
                patch.object(
                    runtime,
                    "stage_reviewed_feature_plan",
                    side_effect=tampered_stage,
                )
            )
            run_side = stack.enter_context(patch.object(runtime, "_run_side"))
            with self.assertRaisesRegex(ValueError, "retained Git stage changed"):
                run_recipe_change_runtime_comparison(
                    ROOT,
                    self.plan,
                    self.state,
                    consent_plan_id=self.plan["id"],
                    launcher_executable=self.root / "launcher.exe",
                    launcher_root=self.root / "launcher",
                )
        run_side.assert_not_called()
        self.assertEqual([], list(self.state.rglob("receipt.json")))

    def test_invalid_compatibility_policy_is_rejected_before_runtime(self) -> None:
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            stack.enter_context(
                patch.object(
                    runtime,
                    "material_fluid_runtime_compatibility_policy",
                    return_value=((), {"patches": []}),
                )
            )
            run_side = stack.enter_context(patch.object(runtime, "_run_side"))
            with self.assertRaisesRegex(ValueError, "compatibility policy changed"):
                run_recipe_change_runtime_comparison(
                    ROOT,
                    self.plan,
                    self.state,
                    consent_plan_id=self.plan["id"],
                    launcher_executable=self.root / "launcher.exe",
                    launcher_root=self.root / "launcher",
                )
        run_side.assert_not_called()
        self.assertEqual([], list(self.state.rglob("receipt.json")))

    def test_mutable_output_roots_are_rejected_before_attempt_or_runtime(self) -> None:
        cases = (
            (
                "launcher-in-source",
                self.root / "state-launcher-source",
                self.checkout / "launcher",
                None,
            ),
            (
                "launcher-is-state",
                self.root / "shared-launcher-state",
                self.root / "shared-launcher-state",
                None,
            ),
            (
                "java-in-state",
                self.root / "state-java-state",
                self.root / "launcher-java-state",
                self.root / "state-java-state/java",
            ),
            (
                "java-in-launcher-instances",
                self.root / "state-java-launcher",
                self.root / "launcher-java-launcher",
                self.root / "launcher-java-launcher/instances/java",
            ),
        )
        patches = self._patches()
        with ExitStack() as stack:
            for active in patches:
                stack.enter_context(active)
            run_side = stack.enter_context(patch.object(runtime, "_run_side"))
            for label, state, launcher, java_state in cases:
                with self.subTest(label=label):
                    with self.assertRaisesRegex(ValueError, "cannot overlap"):
                        run_recipe_change_runtime_comparison(
                            ROOT,
                            self.plan,
                            state,
                            consent_plan_id=self.plan["id"],
                            launcher_executable=self.root / "launcher.exe",
                            launcher_root=launcher,
                            launcher_java_state=java_state,
                        )
        run_side.assert_not_called()
        self.assertFalse((self.checkout / "launcher").exists())

    def test_launcher_managed_java_lane_is_the_only_allowed_root_overlap(self) -> None:
        launcher = self.root / "managed-launcher"
        record = self._run(
            launcher_root=launcher,
            launcher_java_state=launcher / ".workbench",
        )

        self.assertEqual("complete", record["state"])


if __name__ == "__main__":
    unittest.main()
