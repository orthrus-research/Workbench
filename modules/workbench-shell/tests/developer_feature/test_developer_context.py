"""Shared selection, source freshness and installed owner boundary contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.profiles import profile_scope
from workbench_api.profile_extensions import (
    ProfileExtensionError,
    profile_extension_identity,
)
from workbench_blueprints.profile_construction import recipe_change_authority
from workbench_shell.developer_context import (
    DeveloperContextError,
    DeveloperSelection,
    observe_developer_context,
    retain_selection,
    selected_context,
    verify_developer_owner_reference,
)
from workbench_shell.developer_context_cli import run_selected_action
from workbench_shell.developer_recipe_preparation import prepare_recipe_comparison
from workbench_shell.work_session import (
    WorkSessionStore,
    WorkSessionError,
    WorkSessionConflictError,
)
from test_developer_feature import ROOT, _checkout


FRONTEND = {
    "frontend_id": "context-test",
    "kind": "test",
    "version": "1",
    "instance_id": None,
    "process_id": None,
}


class DeveloperContextTests(unittest.TestCase):
    def test_domain_contract_has_no_legacy_shell_export(self):
        import workbench_shell
        from workbench_shell import bootstrap
        from workbench_crucible.runtime_pair import FeatureRuntimePairPorts

        self.assertTrue(callable(FeatureRuntimePairPorts.run_pair))
        self.assertNotIn("FeatureRuntimePairPorts", workbench_shell.__all__)
        self.assertFalse(hasattr(bootstrap, "inspect_repository"))

    def setUp(self):
        temporary = ROOT / ".workbench/test-tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack = _checkout(self.root)
        self.selection = DeveloperSelection(
            self.pack.as_uri(), "supersymmetry", "cleanroom", "cleanroom-provisional"
        )

    def test_source_only_selection_uses_existing_private_work_session(self):
        retained = retain_selection(
            self.selection, self.root / "state", frontend=FRONTEND
        )
        loaded = selected_context(self.root / "state", retained["session_id"])
        self.assertEqual(loaded, self.selection)
        session = WorkSessionStore(self.root / "state").open(retained["session_id"])
        self.assertEqual(
            self.selection.pack_uri, session["session"]["workspace"]["root_uri"]
        )
        self.assertFalse((self.pack / ".workbench").exists())
        self.assertFalse(retained["observation"]["authority"]["execution_authorized"])
        with self.assertRaises(DeveloperContextError):
            selected_context(self.root / "state", "latest")

    def test_successive_dirty_edits_refresh_without_reselection(self):
        target = self.pack / "groovy/postInit/chemistry/Probe.groovy"
        target.write_text(target.read_text() + "\n// edit one\n")
        first = observe_developer_context(self.selection)
        target.write_text(target.read_text().replace("edit one", "edit two"))
        second = observe_developer_context(self.selection)
        self.assertEqual(first.selection.id, second.selection.id)
        self.assertEqual(
            first.as_dict()["source"]["revision"],
            second.as_dict()["source"]["revision"],
        )
        self.assertNotEqual(first.id, second.id)
        with self.assertRaisesRegex(DeveloperContextError, "inputs changed"):
            first.require_fresh()
        with self.assertRaises(FrozenInstanceError):
            first.snapshot_json = "changed"
        detached = first.as_dict()
        detached["source"]["revision"] = "changed"
        self.assertNotEqual(detached, first.as_dict())

    def test_switching_or_concurrent_contexts_do_not_retarget_prior_operation(self):
        other_parent = self.root / "other"
        other_parent.mkdir()
        other = _checkout(other_parent)
        selections = [self.selection, replace(self.selection, pack_uri=other.as_uri())]
        with ThreadPoolExecutor(max_workers=2) as executor:
            observations = list(executor.map(observe_developer_context, selections))
        self.assertNotEqual(observations[0].id, observations[1].id)
        self.assertEqual(
            self.selection.pack_uri, observations[0].as_dict()["source"]["root_uri"]
        )
        observations[0].require_fresh()

    def test_cached_construction_cannot_bypass_disabled_profile(self):
        recipe_change_authority("supersymmetry")
        with profile_scope(disabled=("supersymmetry",)):
            with self.assertRaises((DeveloperContextError, ProfileExtensionError)):
                observe_developer_context(self.selection)
            with self.assertRaises(ProfileExtensionError):
                recipe_change_authority("supersymmetry")

    def test_native_identity_has_actual_code_digest(self):
        identity = profile_extension_identity(
            "workbench.recipe_changes", "supersymmetry"
        )
        self.assertEqual(
            "workbench_profile_supersymmetry.recipe_change", identity["module"]
        )
        self.assertEqual(64, len(identity["sha256"]))
        self.assertGreater(identity["size"], 0)

    def test_source_action_returns_same_selection_and_explicit_override_wins(self):
        action = run_selected_action(
            self.selection, ["feature", "options", "recipe-change"], suite_root=ROOT
        )
        self.assertEqual(0, action["exit_code"], action)
        self.assertEqual(self.selection.id, action["context"]["selection_id"])
        other_parent = self.root / "other"
        other_parent.mkdir()
        other = _checkout(other_parent)
        action = run_selected_action(
            self.selection,
            ["feature", "options", "recipe-change", str(other)],
            suite_root=ROOT,
        )
        self.assertEqual(0, action["exit_code"], action)
        self.assertEqual(other.as_uri(), action["context"]["selection"]["pack_uri"])

    def test_context_does_not_authorize_apply_or_launch(self):
        for argv in (["feature", "apply"], ["runtime-observe"], ["dev", "run"]):
            with self.assertRaises(DeveloperContextError):
                run_selected_action(self.selection, argv, suite_root=ROOT)

    def test_reviewed_pair_preparation_preserves_source_and_rejects_stale_plan(self):
        action = run_selected_action(
            self.selection,
            [
                "feature",
                "plan",
                "recipe-change",
                "--recipe-script",
                "groovy/postInit/chemistry/Probe.groovy",
                "--recipe-map",
                "batch_reactor",
                "--fluid-input",
                '{"name":"water","amount":1000}',
                "--fluid-output",
                '{"name":"steam","amount":1000}',
                "--duration",
                "100",
                "--voltage-tier",
                "LV",
            ],
            suite_root=ROOT,
            state_root=self.root / "state",
        )
        self.assertEqual(0, action["exit_code"], action)
        plan = action["result"]
        before = observe_developer_context(self.selection)
        prepared = prepare_recipe_comparison(
            self.selection, plan, suite_root=ROOT, state_root=self.root / "state"
        )
        self.assertEqual("prepared-not-run", prepared["preparation"]["state"])
        self.assertEqual(plan["id"], prepared["preparation"]["plan_id"])
        self.assertEqual(before.id, observe_developer_context(self.selection).id)
        self.assertFalse(prepared["preparation"]["authority"]["runtime_launched"])
        retained = retain_selection(
            self.selection, self.root / "state", frontend=FRONTEND
        )
        store = WorkSessionStore(self.root / "state")
        session = retained["session_id"]
        sequence = store.status(session)["latest_sequence"]
        for reference in (action["owner_record_ref"], prepared["owner_record_ref"]):
            with self.assertRaises(WorkSessionError):
                store.append(
                    session,
                    expected_sequence=sequence,
                    frontend=FRONTEND,
                    kind="navigation",
                    owner_record_refs=[reference],
                )
            binding = store.bind_owner_artifacts(
                session,
                expected_sequence=sequence,
                frontend=FRONTEND,
                owner_record_refs=[reference],
                owner_reference_verifier=lambda row: verify_developer_owner_reference(
                    row, self.selection, suite_root=ROOT
                ),
            )
            self.assertNotIn(binding["summary"]["lifecycle"], {"running", "complete"})
            with self.assertRaises(WorkSessionConflictError):
                store.bind_owner_artifacts(
                    session,
                    expected_sequence=sequence,
                    frontend=FRONTEND,
                    owner_record_refs=[reference],
                    owner_reference_verifier=lambda row: row,
                )
            sequence += 1
        self.assertEqual(2, len(store.open(session)["summary"]["owner_record_refs"]))
        bad = {**action["owner_record_ref"], "digest": "sha256:" + "0" * 64}
        with self.assertRaisesRegex(DeveloperContextError, "changed"):
            verify_developer_owner_reference(bad, self.selection, suite_root=ROOT)
        with self.assertRaises(WorkSessionError):
            store.bind_owner_artifacts(
                session,
                expected_sequence=sequence,
                frontend=FRONTEND,
                owner_record_refs=[action["owner_record_ref"]],
                owner_reference_verifier=lambda row: {
                    **row,
                    "last_verified_state": "complete",
                    "verified_at": "2026-09-07T00:00:00Z",
                },
            )
        target = self.pack / "groovy/postInit/chemistry/Probe.groovy"
        target.write_text(target.read_text() + "\n// later source edit\n")
        with self.assertRaisesRegex(DeveloperContextError, "stale"):
            prepare_recipe_comparison(
                self.selection, plan, suite_root=ROOT, state_root=self.root / "stale"
            )
        self.assertFalse((self.root / "stale").exists())

    def test_selection_storage_must_not_be_inside_pack(self):
        with self.assertRaisesRegex(DeveloperContextError, "outside"):
            retain_selection(self.selection, self.pack, frontend=FRONTEND)
        with self.assertRaisesRegex(DeveloperContextError, "outside"):
            run_selected_action(
                self.selection,
                ["feature", "plan", "recipe-change"],
                suite_root=ROOT,
                state_root=self.pack / "forbidden-state",
            )
        self.assertFalse((self.pack / "forbidden-state").exists())

    def test_source_observation_rejects_links_and_non_root_selection(self):
        from workbench_project_intelligence.working_tree import (
            observe_source,
            WorkingTreeError,
        )

        with self.assertRaisesRegex(WorkingTreeError, "checkout root"):
            observe_source(self.pack / "groovy")
        target = self.pack / "untracked-link"
        target.symlink_to(self.pack / "pack.toml")
        with self.assertRaisesRegex(WorkingTreeError, "non-regular"):
            observe_source(self.pack)

    def test_source_observation_tracks_deletions_and_ignores_private_outputs(self):
        from workbench_project_intelligence.working_tree import observe_source

        (self.pack / ".gitignore").write_text("private-output/\n")
        before = observe_source(self.pack)
        output = self.pack / "private-output"
        output.mkdir()
        (output / "runtime.log").write_text("not source")
        self.assertEqual(before, observe_source(self.pack))
        (self.pack / "groovy/postInit/chemistry/Probe.groovy").unlink()
        self.assertNotEqual(before, observe_source(self.pack))

    def test_operation_snapshot_records_missing_optional_owner_without_claiming_it(
        self,
    ):
        with patch(
            "workbench_shell.developer_context.profile_extension_identity",
            side_effect=ProfileExtensionError("missing"),
        ):
            observation = observe_developer_context(self.selection).as_dict()
        self.assertTrue(
            all(owner["state"] == "unavailable" for owner in observation["owners"])
        )


if __name__ == "__main__":
    unittest.main()
