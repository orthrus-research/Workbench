"""Release-generic policy custody from a newly verified official ZIP."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from workbench_core.pack_release import _input_plan
from workbench_core.pack_release_client_layout import (
    apply_release_override_custody, load_client_layout_policy,
    plan_release_override_custody,
)
from workbench_core.pack_release_curseforge import plan_curseforge_acquisition
from workbench_core.pack_release_dynamic_policies import (
    apply_release_dynamic_policies, plan_release_dynamic_policies,
    reopen_release_dynamic_policies, suggest_resourcepack_placements,
)
from workbench_core.pack_release_fresh_setup import OfficialFreshReleaseService
from workbench_core.pack_release_local import _canonical
from workbench_core.pack_release_prism_resourcepacks import load_resourcepack_policy


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "profiles/packs/supersymmetry/release-authority-v1.json"
BASELINE_RESOURCEPACKS = (ROOT / "profiles/packs/supersymmetry/runtime"
                          / "release-resourcepack-input-policy-v1.json")
BASELINE_LAYOUT = (ROOT / "profiles/packs/supersymmetry/runtime"
                   / "release-client-layout-policy-v1.json")
RESOURCEPACKS = ((851152, 9001), (885673, 9002), (1290857, 9003))


class ReleaseDynamicPoliciesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.state.mkdir(mode=0o700)
        self.config.mkdir(mode=0o700)
        manifest = {
            "manifestType": "minecraftModpack", "manifestVersion": 1,
            "version": "0.1.16.17", "overrides": "overrides",
            "minecraft": {"version": "1.12.2", "modLoaders": [{
                "id": "forge-14.23.5.2860", "primary": True,
            }]},
            "files": [
                {"projectID": 100, "fileID": 1001, "required": True},
                {"projectID": 200, "fileID": 2001, "required": False},
                *({"projectID": project, "fileID": file, "required": True}
                  for project, file in RESOURCEPACKS),
            ],
        }
        temporary_zip = self.root / "new-release.zip"
        with ZipFile(temporary_zip, "w") as archive:
            archive.writestr("manifest.json", _canonical(manifest))
            archive.writestr("overrides/", b"")
            archive.writestr("overrides/config/client.cfg", b"new-release-setting")
        raw = temporary_zip.read_bytes()
        digest = sha256(raw).hexdigest()
        self.archive = self.state / "artifacts/sha256" / digest
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(raw)
        with ZipFile(self.archive) as archive:
            entries = archive.infolist()
            manifest_raw = archive.read("manifest.json")
        selected = {
            "release_id": "github-release:sha256:" + "c" * 64,
            "version": manifest["version"],
            "asset_sha256": "sha256:" + digest,
            "asset_size": len(raw),
        }
        self.input_plan = _input_plan(selected, manifest, manifest_raw, entries)

    def _kwargs(self) -> dict:
        return dict(
            archive_path=self.archive, authority_path=AUTHORITY,
            baseline_resourcepack_policy_path=BASELINE_RESOURCEPACKS,
            resourcepack_pairs=RESOURCEPACKS, optional_selected=((200, 2001),),
            state_root=self.state, config_home=self.config,
        )

    def test_new_release_policy_pair_is_reviewed_retained_and_reopened(self) -> None:
        suggestions = suggest_resourcepack_placements(
            self.input_plan, baseline_policy_path=BASELINE_RESOURCEPACKS,
        )
        self.assertEqual("requires-user-review", suggestions["review_state"])
        self.assertEqual(RESOURCEPACKS, tuple(
            (row["project_id"], row["file_id"]) for row in suggestions["suggested"]))
        plan = plan_release_dynamic_policies(self.input_plan, **self._kwargs())
        self.assertEqual("acquire", plan["action"])
        self.assertEqual("0.1.16.17", plan["version"])
        self.assertEqual(1, plan["layout_policy"]["override_file_count"])
        retained = apply_release_dynamic_policies(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        self.assertEqual("retained", retained["outcome"])
        layout = load_client_layout_policy(Path(retained["layout_policy_path"]))
        resourcepack = load_resourcepack_policy(Path(retained["resourcepack_policy_path"]))
        self.assertEqual(self.input_plan["plan_id"], layout["input_plan_id"])
        self.assertEqual(3, len(resourcepack["placements"]))
        acquisition = plan_curseforge_acquisition(
            self.input_plan,
            resourcepack_policy_path=Path(retained["resourcepack_policy_path"]),
            optional_selected=((200, 2001),),
        )
        self.assertEqual(4, acquisition["required_count"])
        self.assertEqual(1, acquisition["optional_count"])
        override_args = dict(
            archive_path=self.archive, archive_state_root=self.state,
            authority_path=AUTHORITY,
            layout_policy_path=Path(retained["layout_policy_path"]),
            state_root=self.state, config_home=self.config,
        )
        override_plan = plan_release_override_custody(self.input_plan, **override_args)
        override = apply_release_override_custody(
            self.input_plan, **override_args,
            expected_plan_id=override_plan["plan_id"],
        )
        self.assertEqual("retained", override["outcome"])
        self.archive.unlink()
        reopened = reopen_release_dynamic_policies(
            expected_plan_id=plan["plan_id"], state_root=self.state,
            config_home=self.config,
        )
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(retained["tree_id"], reopened["tree_id"])

    def test_unreviewed_or_optional_resourcepack_mapping_refuses(self) -> None:
        for pairs in ((), ((200, 2001),), ((851152, 9999),),
                      ((851152, 9001), (851152, 9001))):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                plan_release_dynamic_policies(
                    self.input_plan, **{**self._kwargs(),
                                        "resourcepack_pairs": pairs},
                )
        self.assertFalse((self.state / "pack-release-derived-policies").exists())

    def test_archive_change_or_policy_tree_change_refuses(self) -> None:
        plan = plan_release_dynamic_policies(self.input_plan, **self._kwargs())
        self.archive.write_bytes(self.archive.read_bytes() + b"changed")
        with self.assertRaises(ValueError):
            apply_release_dynamic_policies(
                self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
            )
        self.archive.write_bytes(self.archive.read_bytes()[:-7])
        retained = apply_release_dynamic_policies(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        Path(retained["resourcepack_policy_path"]).write_bytes(b"{}\n")
        with self.assertRaises(ValueError):
            reopen_release_dynamic_policies(
                expected_plan_id=plan["plan_id"], state_root=self.state,
                config_home=self.config,
            )

    def test_new_release_mapping_choice_survives_restart_and_resumes(self) -> None:
        class SavedRelease:
            def __init__(self, outer: ReleaseDynamicPoliciesTests) -> None:
                self.outer = outer
                self.state_root = outer.state
                self.choice_path = outer.config / "pack-release-supersymmetry.json"
                self.current_release_id = outer.input_plan["release_id"]

            def inputs(self) -> dict:
                return {"status": "planned", "input_plan": self.outer.input_plan,
                        "selected": {"artifact_path": str(self.outer.archive)}}

            def _read_choice(self) -> dict:
                return {"selected": {
                    "release_id": self.current_release_id,
                    "version": self.outer.input_plan["version"],
                    "asset_sha256": self.outer.input_plan["asset_sha256"],
                }}

        saved = SavedRelease(self)

        def service() -> OfficialFreshReleaseService:
            return OfficialFreshReleaseService(
                saved, authority_path=AUTHORITY,
                layout_policy_path=BASELINE_LAYOUT,
                resourcepack_policy_path=BASELINE_RESOURCEPACKS,
            )

        first = service()
        initial = first.policy_status()
        self.assertEqual("review_required", initial["status"])
        self.assertEqual(3, len(initial["suggestions"]["suggested"]))
        self.assertEqual(
            {(100, 1001), *RESOURCEPACKS},
            {(row["project_id"], row["file_id"])
             for row in initial["required_external_files"]},
        )
        self.assertEqual(
            [{"project_id": 200, "file_id": 2001}],
            initial["optional_external_files"],
        )
        reviewed = first.plan_policy_review(
            resourcepack_pairs=RESOURCEPACKS,
            optional_selected=((200, 2001),),
        )
        self.assertEqual("planned", reviewed["status"])
        retained = first.apply_policy_review(
            resourcepack_pairs=RESOURCEPACKS,
            optional_selected=((200, 2001),),
            expected_plan_id=reviewed["policy_plan"]["plan_id"],
        )
        self.assertEqual("ready", retained["status"])
        choice = first.policy_choice_path.read_text(encoding="utf-8")
        self.assertIn(reviewed["policy_plan"]["plan_id"], choice)
        self.assertNotIn(str(self.state), choice)
        restarted = service()
        pending = restarted.reopen_pending_policy()
        self.assertEqual("ready", pending["status"])
        policy = restarted.policy_status()
        self.assertEqual("ready", policy["status"])
        self.assertEqual("reviewed", policy["source"])
        progress = restarted.status()
        self.assertEqual("pending", progress["status"])
        self.assertEqual(5, progress["selected_file_count"])
        self.assertEqual(1, progress["optional_file_count"])
        saved.current_release_id = "github-release:sha256:" + "d" * 64
        self.assertEqual("stale", restarted.reopen_pending_policy()["status"])


if __name__ == "__main__":
    unittest.main()
