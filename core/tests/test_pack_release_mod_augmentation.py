"""An older fixture adds exact bytes to a new Core tree and preserves its parent."""

from __future__ import annotations

from hashlib import sha1, sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import pack_release_mod_augmentation as augmentation
from workbench_core.artifact_store import fetch_verified_artifact
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.pack_release_local import _canonical
from workbench_core.pack_release_prism_import import apply_prism_import, plan_prism_import


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "profiles/packs/supersymmetry/runtime/release-local-input-policy-v1.json"
AUGMENTATION_POLICY = ROOT / "profiles/packs/supersymmetry/runtime/release-mod-augmentation-policy-v1.json"
AUGMENTATION_POLICY_V2 = ROOT / "profiles/packs/supersymmetry/runtime/release-mod-augmentation-policy-v2.json"


class ModAugmentationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mods = self.root / "prism/minecraft/mods"
        (self.mods / ".index").mkdir(parents=True)
        self.older = self.root / "older"
        self.older.mkdir()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config = self.root / "config"
        self.config.mkdir(mode=0o700)
        self.archive = self.root / "release.zip"
        with ZipFile(self.archive, "w") as output:
            output.writestr("overrides/", b"")
            output.writestr("manifest.json", b"{}")
        actual_policy = augmentation.load_augmentation_policy(AUGMENTATION_POLICY)
        self.reviewed = [
            dict(row, size=len(data), sha1=sha1(data).hexdigest(),
                 sha256="sha256:" + sha256(data).hexdigest())
            for row, data in zip(actual_policy["sources"],
                                 (b"reviewed-open", b"reviewed-commons"), strict=True)
        ]
        self.paths = {}
        for row, data in zip(self.reviewed, (b"reviewed-open", b"reviewed-commons"), strict=True):
            path = self.older / row["filename"]
            path.write_bytes(data)
            self.paths[row["project_id"], row["file_id"]] = path
        declarations = []
        for index in range(187):
            project, file_id = 1000 + index, 2000 + index
            name = f"mod-{index:03}.jar"
            raw = f"mod-{index}".encode()
            (self.mods / name).write_bytes(raw)
            (self.mods / ".index" / f"{project}.pw.toml").write_text(
                f'filename = "{name}"\n'
                '[download]\nmode = "metadata:curseforge"\n'
                f'hash = "{sha1(raw).hexdigest()}"\nhash-format = "sha1"\n'
                '[update.curseforge]\n'
                f'project-id = {project}\nfile-id = {file_id}\n', encoding="utf-8",
            )
            declarations.append({"project_id": project, "file_id": file_id, "required": True})
        declarations.extend({"project_id": row["project_id"], "file_id": row["file_id"],
                             "required": True} for row in self.reviewed)
        self.susy_id = (846224, 8891423)
        declarations.append({"project_id": self.susy_id[0], "file_id": self.susy_id[1],
                             "required": True})
        body = {"format": "workbench-pack-release-input-plan-v1", "schema_version": 1,
                "profile": "supersymmetry", "release_id": "profile-release:sha256:" + "2" * 64,
                "version": "0.1.16.16", "manifest_sha256": "sha256:" + "3" * 64,
                "asset_sha256": "sha256:" + sha256(self.archive.read_bytes()).hexdigest(),
                "asset_size": self.archive.stat().st_size, "external_files": declarations}
        self.input_plan = {**body, "plan_id": "workbench-pack-release-input-plan:sha256:"
                           + sha256(_canonical(body)).hexdigest()}
        old_plan = plan_prism_import(
            self.input_plan, source_root=self.mods, archive_path=self.archive,
            policy_path=POLICY, state_root=self.state, config_home=self.config,
        )
        self.old_plan_id = old_plan["plan_id"]
        old_result = apply_prism_import(
            self.input_plan, source_root=self.mods, archive_path=self.archive,
            policy_path=POLICY, state_root=self.state, config_home=self.config,
            expected_plan_id=self.old_plan_id,
        )
        self.old_tree_id = old_result["tree_id"]
        self.old_path = next((self.state / "pack-release-mod-inputs").glob("*/snapshot"))
        self.augmentation_policy = self.root / "augmentation-policy.json"
        test_policy = {**actual_policy, "input_plan_id": self.input_plan["plan_id"],
                       "release_id": self.input_plan["release_id"],
                       "manifest_sha256": self.input_plan["manifest_sha256"],
                       "external_file_count": len(declarations), "sources": self.reviewed}
        self.augmentation_policy.write_text(json.dumps(test_policy), encoding="utf-8")
        susy_bytes = b"reviewed-susy-core"
        actual_v2 = augmentation.load_augmentation_policy_v2(AUGMENTATION_POLICY_V2)
        self.susy_source = self.root / "source-susy-core.jar"
        self.susy_source.write_bytes(susy_bytes)
        self.susy_row = dict(actual_v2["sources"][0], size=len(susy_bytes),
                             sha1=sha1(susy_bytes).hexdigest(),
                             sha256="sha256:" + sha256(susy_bytes).hexdigest())
        self.artifact_path, _ = fetch_verified_artifact(
            url=self.susy_source.as_uri(), expected_sha256=self.susy_row["sha256"][7:],
            expected_size=self.susy_row["size"], state_root=self.state, label="synthetic Susy-Core",
        )
        self.augmentation_policy_v2 = self.root / "augmentation-policy-v2.json"
        test_v2 = {**actual_v2, "input_plan_id": self.input_plan["plan_id"],
                   "release_id": self.input_plan["release_id"],
                   "manifest_sha256": self.input_plan["manifest_sha256"],
                   "external_file_count": len(declarations), "sources": [self.susy_row]}
        self.augmentation_policy_v2.write_text(json.dumps(test_v2), encoding="utf-8")

    def _plan(self):
        return augmentation.plan_mod_augmentation(
            self.input_plan, prior_plan_id=self.old_plan_id, prior_tree_id=self.old_tree_id,
            source_paths=self.paths, policy_path=POLICY,
            augmentation_policy_path=self.augmentation_policy,
            state_root=self.state, config_home=self.config,
        )

    def _apply(self, plan):
        return augmentation.apply_mod_augmentation(
            self.input_plan, prior_plan_id=self.old_plan_id, prior_tree_id=self.old_tree_id,
            source_paths=self.paths, policy_path=POLICY,
            augmentation_policy_path=self.augmentation_policy,
            state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"],
        )

    def _plan_v2(self, prior):
        return augmentation.plan_mod_augmentation_v2(
            self.input_plan, prior_plan_id=prior["plan_id"], prior_tree_id=prior["tree_id"],
            artifact_path=self.artifact_path, policy_path=POLICY,
            prior_augmentation_policy_path=self.augmentation_policy,
            augmentation_policy_path=self.augmentation_policy_v2,
            state_root=self.state, config_home=self.config,
        )

    def _apply_v2(self, prior, plan):
        return augmentation.apply_mod_augmentation_v2(
            self.input_plan, prior_plan_id=prior["plan_id"], prior_tree_id=prior["tree_id"],
            artifact_path=self.artifact_path, policy_path=POLICY,
            prior_augmentation_policy_path=self.augmentation_policy,
            augmentation_policy_path=self.augmentation_policy_v2,
            state_root=self.state, config_home=self.config, expected_plan_id=plan["plan_id"],
        )

    def test_combines_189_exact_mods_references_old_tree_and_reopens_without_sources(self) -> None:
        before = (self.old_path / "mod-000.jar").read_bytes()
        plan = self._plan()
        self.assertEqual("acquire", plan["action"])
        self.assertEqual(189, plan["retained_file_count"])
        self.assertEqual([{"project_id": self.susy_id[0], "file_id": self.susy_id[1],
                           "required": True}], plan["unresolved"])
        self.assertEqual(self.old_tree_id, plan["prior_tree_id"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        result = self._apply(plan)
        self.assertEqual("retained", result["outcome"])
        self.assertEqual("not-installed", result["installation_state"])
        self.assertEqual(self.old_tree_id, result["prior_tree_id"])
        target = next((self.state / "pack-release-mod-augmentations").glob("*/snapshot"))
        self.assertEqual(189, len(list((target / "mods").iterdir())))
        self.assertEqual(before, (self.old_path / "mod-000.jar").read_bytes())
        self.assertEqual(b"reviewed-open", (target / "mods" / self.reviewed[0]["filename"]).read_bytes())
        self.assertEqual(b"reviewed-commons", (target / "mods" / self.reviewed[1]["filename"]).read_bytes())
        lock = json.loads((target / "source-lock.json").read_bytes())
        self.assertEqual(augmentation.LOCK_FORMAT, lock["format"])
        self.assertEqual(self.old_tree_id, lock["prior_tree_id"])
        host = CoreManagedTrees(
            workspace=self.state, configuration_home=self.config,
            locations={"artifacts": self.state / "pack-release-mod-augmentations"},
            owner_id="supersymmetry", policy_id=plan["policy_id"],
        )
        self.assertEqual((self.old_tree_id,), host.describe(result["tree_id"]).references)
        self.mods.rename(self.root / "former-prism-mods")
        self.older.rename(self.root / "former-older")
        reopened = augmentation.reopen_mod_augmentation(
            self.input_plan, expected_plan_id=plan["plan_id"], policy_path=POLICY,
            augmentation_policy_path=self.augmentation_policy,
            state_root=self.state, config_home=self.config,
        )
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(result["tree_content_sha256"], reopened["tree_content_sha256"])
        self.assertNotIn(str(self.root), json.dumps(reopened))

    def test_changed_local_byte_or_wrong_prior_tree_refuses_before_new_tree(self) -> None:
        plan = self._plan()
        self.paths[300957, 3143467].write_bytes(b"x" * len(b"reviewed-open"))
        with self.assertRaisesRegex(ValueError, "reviewed identity"):
            self._apply(plan)
        self.assertFalse((self.state / "pack-release-mod-augmentations").exists())
        self.paths[300957, 3143467].write_bytes(b"reviewed-open")
        with self.assertRaises(ValueError):
            augmentation.plan_mod_augmentation(
                self.input_plan, prior_plan_id=self.old_plan_id,
                prior_tree_id="workbench-tree-v1:" + "0" * 32,
                source_paths=self.paths, policy_path=POLICY,
                augmentation_policy_path=self.augmentation_policy,
                state_root=self.state, config_home=self.config,
            )

    def test_uncertain_stage_remains_visible_and_refuses_second_attempt(self) -> None:
        plan = self._plan()
        with patch.object(augmentation, "_copy_to_stage", side_effect=RuntimeError("interrupted copy")):
            with self.assertRaisesRegex(RuntimeError, "interrupted copy"):
                self._apply(plan)
        with self.assertRaisesRegex(ValueError, "incomplete stage"):
            self._plan()
        self.assertTrue(list((self.state / "pack-release-mod-augmentations").glob("**/.workbench-tree-*.pending")))

    def test_reopen_rejects_changed_new_tree_after_local_sources_disappear(self) -> None:
        plan = self._plan()
        self._apply(plan)
        self.older.rename(self.root / "former-older")
        target = next((self.state / "pack-release-mod-augmentations").glob("*/snapshot"))
        (target / "mods" / "mod-000.jar").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            augmentation.reopen_mod_augmentation(
                self.input_plan, expected_plan_id=plan["plan_id"], policy_path=POLICY,
                augmentation_policy_path=self.augmentation_policy,
                state_root=self.state, config_home=self.config,
            )

    def test_v2_extends_189_tree_references_parent_and_reopens_without_cache(self) -> None:
        prior_plan = self._plan()
        prior = self._apply(prior_plan)
        prior_target = self.state / "pack-release-mod-augmentations" / prior_plan["plan_id"].rsplit(":", 1)[-1] / "snapshot"
        original = (prior_target / "mods" / "mod-000.jar").read_bytes()
        plan = self._plan_v2(prior)
        self.assertEqual("acquire", plan["action"])
        self.assertEqual(190, plan["retained_file_count"])
        self.assertEqual([], plan["unresolved"])
        self.assertEqual(prior["tree_id"], plan["prior_tree_id"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        result = self._apply_v2(prior, plan)
        self.assertEqual("retained", result["outcome"])
        self.assertEqual(augmentation.RESULT_FORMAT_V2, result["format"])
        self.assertEqual("unproven-by-local-assertions", result["curseforge_file_identity_state"])
        self.assertEqual("not-installed", result["installation_state"])
        self.assertNotIn(str(self.root), json.dumps(result))
        target = self.state / "pack-release-mod-augmentations" / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot"
        self.assertEqual(190, len(list((target / "mods").iterdir())))
        self.assertEqual(original, (prior_target / "mods" / "mod-000.jar").read_bytes())
        self.assertEqual(self.susy_source.read_bytes(), (target / "mods" / self.susy_row["filename"]).read_bytes())
        host = CoreManagedTrees(
            workspace=self.state, configuration_home=self.config,
            locations={"artifacts": self.state / "pack-release-mod-augmentations"},
            owner_id="supersymmetry", policy_id=plan["policy_id"],
        )
        self.assertEqual((prior["tree_id"],), host.describe(result["tree_id"]).references)
        self.artifact_path.unlink()
        self.susy_source.unlink()
        self.older.rename(self.root / "former-older")
        self.mods.rename(self.root / "former-prism-mods")
        reopened = augmentation.reopen_mod_augmentation_v2(
            self.input_plan, expected_plan_id=plan["plan_id"], policy_path=POLICY,
            prior_augmentation_policy_path=self.augmentation_policy,
            augmentation_policy_path=self.augmentation_policy_v2,
            state_root=self.state, config_home=self.config,
        )
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(result["tree_content_sha256"], reopened["tree_content_sha256"])
        v1_reopened = augmentation.reopen_mod_augmentation(
            self.input_plan, expected_plan_id=prior_plan["plan_id"], policy_path=POLICY,
            augmentation_policy_path=self.augmentation_policy,
            state_root=self.state, config_home=self.config,
        )
        self.assertEqual(prior["tree_content_sha256"], v1_reopened["tree_content_sha256"])

    def test_v2_refuses_wrong_cache_path_changed_bytes_and_prior_tree(self) -> None:
        prior = self._apply(self._plan())
        wrong = self.root / self.artifact_path.name
        wrong.write_bytes(self.artifact_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "exact verified Core artifact-cache path"):
            augmentation.plan_mod_augmentation_v2(
                self.input_plan, prior_plan_id=prior["plan_id"], prior_tree_id=prior["tree_id"],
                artifact_path=wrong, policy_path=POLICY,
                prior_augmentation_policy_path=self.augmentation_policy,
                augmentation_policy_path=self.augmentation_policy_v2,
                state_root=self.state, config_home=self.config,
            )
        with self.assertRaises(ValueError):
            augmentation.plan_mod_augmentation_v2(
                self.input_plan, prior_plan_id=prior["plan_id"],
                prior_tree_id="workbench-tree-v1:" + "0" * 32,
                artifact_path=self.artifact_path, policy_path=POLICY,
                prior_augmentation_policy_path=self.augmentation_policy,
                augmentation_policy_path=self.augmentation_policy_v2,
                state_root=self.state, config_home=self.config,
            )
        plan = self._plan_v2(prior)
        self.artifact_path.write_bytes(b"x" * self.susy_row["size"])
        with self.assertRaisesRegex(ValueError, "reviewed identity"):
            self._apply_v2(prior, plan)
        target = self.state / "pack-release-mod-augmentations" / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot"
        self.assertFalse(target.exists())

    def test_v2_reuses_exact_tree_and_incomplete_stage_refuses(self) -> None:
        prior = self._apply(self._plan())
        plan = self._plan_v2(prior)
        result = self._apply_v2(prior, plan)
        reused_plan = self._plan_v2(prior)
        self.assertEqual("reuse", reused_plan["action"])
        self.assertEqual(result["tree_id"], reused_plan["tree_id"])
        reused = self._apply_v2(prior, reused_plan)
        self.assertEqual("reused", reused["outcome"])
        self.assertEqual(result["tree_id"], reused["tree_id"])
        self.assertEqual(result["tree_content_sha256"], reused["tree_content_sha256"])

    def test_v2_incomplete_stage_stays_visible_and_refuses_next_plan(self) -> None:
        prior = self._apply(self._plan())
        plan = self._plan_v2(prior)
        with patch.object(augmentation, "_copy_v2_to_stage", side_effect=RuntimeError("interrupted V2 copy")):
            with self.assertRaisesRegex(RuntimeError, "interrupted V2 copy"):
                self._apply_v2(prior, plan)
        with self.assertRaisesRegex(ValueError, "incomplete stage"):
            self._plan_v2(prior)
        self.assertTrue(list((self.state / "pack-release-mod-augmentations").glob("**/.workbench-tree-*.pending")))


if __name__ == "__main__":
    unittest.main()
