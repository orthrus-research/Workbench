from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[4]
MATRIX = (
    ROOT
    / "profiles/platforms/cleanroom/mixins"
    / "cleanmix-0.7.0-runtime-regression-matrix-v1.json"
)
CANDIDATE_LOCK = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/candidate-lock-v1.json"
)
TOOLCHAIN_LOCK = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "transformer-toolchain-lock-v1.json"
)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(path: Path) -> dict[str, object]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CleanMixRuntimeRegressionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = _load(MATRIX)
        cls.candidate_lock = _load(CANDIDATE_LOCK)
        cls.toolchain_lock = _load(TOOLCHAIN_LOCK)
        cls.rows = cls.matrix["rows"]
        cls.by_id = {row["id"]: row for row in cls.rows}

    def test_matrix_is_content_addressed(self) -> None:
        material = deepcopy(self.matrix)
        matrix_id = material.pop("matrix_id")
        self.assertEqual(
            "cleanroom-cleanmix-runtime-regression-matrix:sha256:"
            + _canonical_digest(material),
            matrix_id,
        )

    def test_exact_profile_binds_separate_distribution_source_and_behavior_axes(
        self,
    ) -> None:
        self.assertEqual(
            "workbench-cleanroom-cleanmix-runtime-regression-matrix-v1",
            self.matrix["format"],
        )
        self.assertEqual(1, self.matrix["schema_version"])
        axes = self.matrix["identity_axes"]
        self.assertEqual({"distribution", "upstream", "behavior"}, set(axes))

        artifacts = {
            row["coordinate"]: row for row in self.toolchain_lock["artifacts"]
        }
        locked_artifact = artifacts["com.cleanroommc:cleanmix:0.7.0"]
        distribution = axes["distribution"]
        for key in ("coordinate", "filename", "sha1", "sha256", "size", "url"):
            self.assertEqual(locked_artifact[key], distribution[key])
        self.assertEqual("byte_locked", distribution["axis_state"])
        self.assertEqual("not_proven", distribution["source_byte_equivalence"])
        self.assertEqual("not_claimed", distribution["runtime_behavior_claim"])

        upstream = axes["upstream"]
        self.assertEqual("0.7.0", upstream["tag"])
        self.assertEqual(
            "24898e32adc735fe48f4a3732b1831bd566c4626",
            upstream["revision"],
        )
        self.assertEqual(
            "7bdf03df469a1bbcf4cf5cba93f74e6bc1641218",
            upstream["tree_sha1"],
        )
        self.assertEqual("not_proven", upstream["published_byte_equivalence"])
        self.assertEqual("not_claimed", upstream["runtime_behavior_claim"])

        behavior = axes["behavior"]
        self.assertEqual("unexecuted", behavior["axis_state"])
        self.assertEqual("cleanmix-release-0.7.x", behavior["runtime_epoch"])
        self.assertEqual("0.1.0", behavior["compatibility_metadata_default"])
        self.assertEqual(
            "0.6.0", behavior["latest_declared_compatibility_boundary"]
        )
        self.assertEqual(
            "partial_source_fixture_compile_tested",
            behavior["harness_state"],
        )

    def test_exact_cleanroom_profile_and_existing_locks_are_byte_bound(self) -> None:
        profile = self.matrix["exact_profile"]
        self.assertEqual(self.candidate_lock["candidate_id"], profile["candidate_id"])
        self.assertEqual(
            self.candidate_lock["cleanroom"]["source_revision"],
            profile["cleanroom"]["source_revision"],
        )
        self.assertEqual(25, profile["java_major"])
        self.assertEqual(
            {"dedicated_server", "client"}, set(profile["physical_sides"])
        )
        self.assertEqual(
            "2d83b071fc959a4885eb1049258bb6900d7ff4d5",
            profile["cleanmix_0_7_0_selection"]["revision"],
        )
        self.assertEqual(
            _sha256(CANDIDATE_LOCK), profile["candidate_lock"]["sha256"]
        )
        self.assertEqual(
            _sha256(TOOLCHAIN_LOCK),
            profile["transformer_toolchain_lock"]["sha256"],
        )

        accepted = self.matrix["execution_contract"][
            "accepted_runtime_distribution"
        ]
        execution = self.matrix["execution_contract"]
        self.assertEqual("not_implemented", execution["harness_state"])
        self.assertEqual(
            "partial_source_fixture_compile_tested",
            execution["fixture_state"],
        )
        self.assertEqual("no_valid_row_execution", execution["runtime_execution"])
        distribution = self.matrix["identity_axes"]["distribution"]
        self.assertEqual(
            {
                "coordinate": distribution["coordinate"],
                "sha256": distribution["sha256"],
                "size": distribution["size"],
            },
            accepted,
        )

    def test_phase_fix_and_pr_3_identities_are_exact_and_referenced(self) -> None:
        changes = self.matrix["audited_change_identities"]
        phase_fixes = changes["phase_fixes"]
        self.assertEqual([1, 2, 3], [row["ordinal"] for row in phase_fixes])
        self.assertEqual(
            [
                "0be24ae04cbf027da2fe78f36f6f0ea45192096b",
                "a86fa8cc559343a8a9cca62d439e82cdf78a7d6f",
                "24ef30c41c23d778b3fd5ee9606f7965391618d0",
            ],
            [row["revision"] for row in phase_fixes],
        )
        self.assertEqual(
            "invalid_intermediate_state",
            phase_fixes[1]["standalone_behavior_evaluation"],
        )

        pr = changes["injector_handler_conformance"]
        self.assertEqual(3, pr["pull_request"])
        self.assertEqual(
            "Conform injector handler names during config preparation", pr["title"]
        )
        self.assertEqual(
            "ac2b06c920608d566efe57386bf6b9b88552b02d", pr["base_revision"]
        )
        self.assertEqual(
            "376c68778694569c7f9bbbb5fabf279a115f285a", pr["head_revision"]
        )
        self.assertEqual(
            "26adacb0944b81b700d5902789c68cfbf01abda2", pr["merge_revision"]
        )
        self.assertEqual(
            (3, 57, 0),
            (pr["files_changed"], pr["additions"], pr["deletions"]),
        )
        self.assertEqual(
            (0, 0, 0),
            (
                pr["head_actions_runs"],
                pr["head_check_runs"],
                pr["head_status_contexts"],
            ),
        )
        self.assertEqual("not_performed", pr["patched_jar_game_rerun"])
        self.assertEqual("not_claimed", pr["behavioral_proof"])

        authority_ids = {row["id"] for row in phase_fixes} | {
            pr["id"],
            self.matrix["identity_axes"]["upstream"]["id"],
        }
        for row in self.rows:
            self.assertTrue(row["authority_refs"])
            self.assertLessEqual(set(row["authority_refs"]), authority_ids)

    def test_required_p0_cases_are_bounded_and_use_fresh_jvms(self) -> None:
        required_p0_ids = {
            "P0-HANDLER-PARENT-FIRST-SERVER",
            "P0-HANDLER-CHILD-FIRST-SERVER",
            "P0-PHASE-SEQUENCE-SERVER",
            "P0-PHASE-SEQUENCE-CLIENT",
            "P0-LATE-DEFAULT-BEFORE-TARGET-SERVER",
            "P0-LATE-DEFAULT-AFTER-TARGET-SERVER",
            "P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER",
            "P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER",
            "P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER",
        }
        p0_rows = [row for row in self.rows if row["priority"] == "P0"]
        self.assertEqual(required_p0_ids, {row["id"] for row in p0_rows})
        self.assertEqual(len(self.rows), len(self.by_id))
        self.assertTrue(all(row["isolation"] == "fresh_jvm" for row in p0_rows))
        self.assertEqual(
            {"dedicated_server", "client"}, {row["side"] for row in p0_rows}
        )

        self.assertEqual(
            "parent_then_child",
            self.by_id["P0-HANDLER-PARENT-FIRST-SERVER"]["fixture_spec"][
                "target_order"
            ],
        )
        self.assertEqual(
            (
                "child_transformation_requested_before_explicit_"
                "parent_target_transformation"
            ),
            self.by_id["P0-HANDLER-CHILD-FIRST-SERVER"]["fixture_spec"][
                "target_order"
            ],
        )
        child_first = self.by_id["P0-HANDLER-CHILD-FIRST-SERVER"]
        self.assertIn(
            "The independent no-op chain observer records ChildTarget entry "
            "before ParentTarget entry.",
            child_first["oracle"]["required"],
        )
        self.assertIn(
            "a child-first claim inferred only from a request label or "
            "class-loading API call",
            child_first["oracle"]["forbidden"],
        )
        for side in ("SERVER", "CLIENT"):
            phase = self.by_id[f"P0-PHASE-SEQUENCE-{side}"]
            self.assertEqual(
                ["PREINIT", "INIT", "DEFAULT"], phase["fixture_spec"]["phase_order"]
            )
            self.assertEqual(
                {
                    "before_initial_consumption": 3,
                    "after_PREINIT_consumption": 2,
                    "after_INIT_refresh": 1,
                    "after_DEFAULT_refresh": 0,
                },
                phase["fixture_spec"]["pending_counts_by_boundary"],
            )

        before = self.by_id["P0-LATE-DEFAULT-BEFORE-TARGET-SERVER"]
        after = self.by_id["P0-LATE-DEFAULT-AFTER-TARGET-SERVER"]
        self.assertEqual(
            "not_loaded", before["fixture_spec"]["target_state_at_registration"]
        )
        self.assertEqual(
            "already_defined", after["fixture_spec"]["target_state_at_registration"]
        )
        self.assertEqual("must_reject", after["expected_disposition"])
        self.assertIn(
            "parseTargetsWithoutCheck",
            after["fixture_spec"]["source_prediction"],
        )

    def test_expected_failures_are_explicit_unexecuted_named_defect_sentinels(
        self,
    ) -> None:
        expected = {
            "P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER": (
                "cleanmix-0.7.0-lazy-inheritance-tracker"
            ),
            "P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER": (
                "cleanmix-0.7.0-reentrant-pending-target"
            ),
            "P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER": (
                "cleanmix-0.7.0-three-deep-handler-order"
            ),
        }
        xfails = [
            row for row in self.rows if row["expected_disposition"] == "xfail"
        ]
        self.assertEqual(
            expected, {row["id"]: row["xfail_reason_code"] for row in xfails}
        )
        for row in xfails:
            self.assertEqual("P0", row["priority"])
            self.assertEqual("unexecuted", row["execution_state"])
            self.assertEqual("known_gap", row["category"])
            self.assertTrue(row["oracle"]["named_defect"])
            self.assertGreaterEqual(len(row["oracle"]["required_for_xfail"]), 2)
            self.assertEqual(
                "XPASS requiring exact upstream-drift review",
                row["oracle"]["unexpected_pass"],
            )

        lazy = self.by_id[
            "P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER"
        ]["oracle"]
        self.assertIn("ACONST_NULL", lazy["named_defect"])
        self.assertTrue(
            any("callback_info_null=1" in item for item in lazy["required_for_xfail"])
        )
        reentrant = self.by_id[
            "P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER"
        ]["oracle"]
        self.assertTrue(
            any(
                "config prepare enter" in item
                for item in reentrant["required_for_xfail"]
            )
        )
        three_deep = self.by_id[
            "P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER"
        ]["oracle"]
        self.assertTrue(
            any("middle handler" in item for item in three_deep["required_for_xfail"])
        )

        control = self.matrix["execution_contract"][
            "historical_control_requirement"
        ]
        self.assertEqual("unresolved", control["state"])
        self.assertEqual(
            "not_bound_by_this_asset", control["published_artifact_identity"]
        )
        self.assertEqual(
            "intentionally_omitted_until_byte_locked", control["matrix_row"]
        )

    def test_p1_is_limited_to_three_high_value_rows(self) -> None:
        expected = {
            "P1-PLUGIN-TARGET-VISIBILITY-SERVER",
            "P1-CONFORMANCE-ERROR-HARD-GATE-SERVER",
            "P1-PHASE-REPEAT-BACKWARD-NON-DUPLICATION-SERVER",
        }
        p1_rows = [row for row in self.rows if row["priority"] == "P1"]
        self.assertEqual(expected, {row["id"] for row in p1_rows})
        self.assertEqual(
            {
                "plugin_phase_visibility",
                "observer_health_gate",
                "phase_transition_robustness",
            },
            {row["category"] for row in p1_rows},
        )
        self.assertTrue(all(row["isolation"] == "fresh_jvm" for row in p1_rows))
        self.assertEqual(
            "must_reject",
            self.by_id["P1-CONFORMANCE-ERROR-HARD-GATE-SERVER"][
                "expected_disposition"
            ],
        )
        self.assertEqual(
            ["PREINIT", "INIT", "INIT", "PREINIT", "DEFAULT", "INIT", "DEFAULT"],
            self.by_id["P1-PHASE-REPEAT-BACKWARD-NON-DUPLICATION-SERVER"][
                "fixture_spec"
            ]["phase_signals"],
        )

    def test_matrix_distinguishes_partial_fixture_from_valid_execution(self) -> None:
        contract = self.matrix["execution_contract"]
        self.assertEqual("incomplete", self.matrix["lifecycle_state"])
        self.assertEqual("unexecuted", contract["matrix_execution_state"])
        self.assertEqual("not_implemented", contract["harness_state"])
        self.assertEqual(
            "partial_source_fixture_compile_tested",
            contract["fixture_state"],
        )
        self.assertEqual("no_valid_row_execution", contract["runtime_execution"])
        self.assertEqual("fresh_jvm_per_row", contract["row_isolation"])
        self.assertTrue(
            all(row["execution_state"] == "unexecuted" for row in self.rows)
        )
        self.assertTrue(
            all(
                row["expected_disposition"] in {"pass", "must_reject", "xfail"}
                for row in self.rows
            )
        )
        self.assertEqual(
            {
                "P0-LATE-DEFAULT-AFTER-TARGET-SERVER",
                "P1-CONFORMANCE-ERROR-HARD-GATE-SERVER",
            },
            {
                row["id"]
                for row in self.rows
                if row["expected_disposition"] == "must_reject"
            },
        )

        forbidden_outcome_keys = {
            "actual_disposition",
            "completed_at",
            "execution_id",
            "observations",
            "result",
            "runtime_receipt",
        }

        def assert_no_outcomes(value: object) -> None:
            if isinstance(value, dict):
                self.assertFalse(forbidden_outcome_keys & set(value))
                for item in value.values():
                    assert_no_outcomes(item)
            elif isinstance(value, list):
                for item in value:
                    assert_no_outcomes(item)

        assert_no_outcomes(self.matrix)

        required = set(contract["required_process_evidence"])
        self.assertIn(
            "exact installed component paths, sizes, and SHA-256 digests", required
        )
        self.assertIn(
            "forced target loads and Foundation-custodied final defined class bytes",
            required,
        )
        self.assertIn(
            "observer start, end, and health independent of cleanmix.log", required
        )
        self.assertIn(
            "ordered no-op target transform-entry observations proving actual "
            "target order independently of request labels and CleanMix APPLY lines",
            required,
        )
        self.assertIn("a successful upstream build", contract["insufficient_evidence"])
        self.assertIn(
            "a CleanMix APPLY or GENERATE audit line",
            contract["insufficient_evidence"],
        )
        self.assertIn(
            "a requested-order property or class-loading API call without "
            "matching no-op transform-entry observations",
            contract["insufficient_evidence"],
        )

    def test_recurrent_complex_is_outside_the_cleanmix_matrix(self) -> None:
        excluded = self.matrix["scope"]["excluded"]
        self.assertIn(
            "A Recurrent Complex adapter, dependency, or special-case execution path.",
            excluded,
        )
        for row in self.rows:
            serialized = json.dumps(row, sort_keys=True).lower()
            self.assertNotIn("recurrent complex", serialized)
            self.assertNotIn("recurrent_complex", serialized)


if __name__ == "__main__":
    unittest.main()
