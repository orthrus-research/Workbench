"""Synthetic three-tree release composition; no client is installed or run."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipInfo

from workbench_core import pack_release_client_composition as composition
from workbench_core import pack_release_client_layout as layout
from workbench_core.durable_files import _directory as pinned_directory
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.pack_release import _input_plan
from workbench_core.pack_release_local import _canonical
from workbench_core.pack_release_mod_augmentation import LOCK_FORMAT_V2, PLAN_FORMAT_V2
from workbench_core.storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


ROOT = Path(__file__).resolve().parents[2]


class PackReleaseClientCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.state.mkdir(mode=0o700)
        self.config.mkdir(mode=0o700)
        self.local_policy = self.root / "local-policy.json"
        self.prior_mod_policy = self.root / "prior-mod-policy.json"
        self.mod_policy = self.root / "mod-policy.json"
        for path in (self.local_policy, self.prior_mod_policy, self.mod_policy):
            path.write_text("{}\n", encoding="utf-8")

        manifest = {"overrides": "overrides", "files": [
            {"projectID": 1, "fileID": 10, "required": True},
            {"projectID": 2, "fileID": 20, "required": False},
            {"projectID": 3, "fileID": 30, "required": True},
        ]}
        selected = {"release_id": "profile-release:sha256:" + "c" * 64,
                    "version": "0.1.16.16",
                    "asset_sha256": "sha256:" + sha256(b"synthetic release archive").hexdigest(),
                    "asset_size": 25}
        entries = [ZipInfo(name) for name in (
            "manifest.json", "overrides/", "overrides/config/client.cfg",
            "overrides/config/empty.txt",
        )]
        self.input_plan = _input_plan(selected, manifest, _canonical(manifest), entries)
        self.layout_policy = self.root / "layout-policy.json"
        self.policy = {
            "format": layout.POLICY_FORMAT, "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"],
            "release_id": self.input_plan["release_id"], "version": self.input_plan["version"],
            "asset_sha256": self.input_plan["asset_sha256"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "override_source_root": "overrides/", "destination_root": "minecraft-root",
            "override_file_count": 2, "override_total_bytes": 4,
            "optional_selected": [{"project_id": 2, "file_id": 20}],
            "collision_policy": "reject", "installation_state": "not-installed",
            "runtime_qualification_state": "not-qualified",
        }
        self.layout_policy.write_bytes(_canonical(self.policy) + b"\n")
        self.rp_policy = self.root / "rp-policy.json"
        self.rp_policy.write_bytes(_canonical({
            "format": "workbench-supersymmetry-release-resourcepack-input-policy-v1",
            "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"], "version": self.input_plan["version"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "external_file_count": 3, "allowed_extensions": [".zip"],
            "max_file_bytes": 536870912, "max_total_bytes": 1610612736,
            "placements": [{"project_id": 3, "file_id": 30, "required": True,
                            "destination_root": "resourcepacks"}],
        }) + b"\n")

        self.mod_files = [
            {"project_id": 1, "file_id": 10, "relative_path": "mods/Alpha.jar",
             "filename": "Alpha.jar", "size": 5,
             "sha256": "sha256:" + sha256(b"alpha").hexdigest()},
            {"project_id": 2, "file_id": 20, "relative_path": "mods/Optional.jar",
             "filename": "Optional.jar", "size": 8,
             "sha256": "sha256:" + sha256(b"optional").hexdigest()},
        ]
        self.rp_files = [
            {"project_id": 3, "file_id": 30, "relative_path": "resourcepacks/Theme.zip",
             "filename": "Theme.zip", "size": 5,
             "sha256": "sha256:" + sha256(b"theme").hexdigest()},
        ]
        mod_body = {
            "format": PLAN_FORMAT_V2, "schema_version": 2, "profile": "supersymmetry",
            "input_plan_id": self.input_plan["plan_id"],
            "release_id": self.input_plan["release_id"],
            "asset_sha256": self.input_plan["asset_sha256"],
            "policy_id": "workbench-pack-release-mod-augmentation-policy:sha256:" + "d" * 64,
            "files": self.mod_files, "retained_file_count": 2,
            "retained_total_bytes": 13, "unresolved": [],
        }
        self.mod_plan_id = "workbench-pack-release-mod-augmentation-plan:sha256:" + sha256(
            _canonical(mod_body)).hexdigest()
        mod_lock = _canonical({**mod_body, "format": LOCK_FORMAT_V2,
                               "plan_id": self.mod_plan_id}) + b"\n"
        mod_ref = self._source_tree(
            "pack-release-mod-augmentations", self.mod_plan_id, mod_body["policy_id"],
            {"mods/Alpha.jar": b"alpha", "mods/Optional.jar": b"optional",
             "source-lock.json": mod_lock},
        )
        self.mod_result = {
            "outcome": "reopened", "installation_state": "not-installed",
            "plan_id": self.mod_plan_id, "tree_id": mod_ref.tree_id,
            "tree_content_sha256": mod_ref.content_sha256,
            "retained_file_count": 2, "retained_total_bytes": 13,
            "unresolved": [], "curseforge_file_identity_state": "unproven-by-local-assertions",
        }
        self.mod_path = mod_ref.path

        self.rp_plan_id = "workbench-pack-release-prism-resourcepack-plan:sha256:" + "e" * 64
        rp_ref = self._source_tree(
            "pack-release-resourcepack-inputs", self.rp_plan_id,
            "workbench-pack-release-resourcepack-policy:sha256:" + "e" * 64,
            {"resourcepacks/Theme.zip": b"theme", "source-lock.json": b"synthetic\n"},
        )
        self.rp_result = {
            "outcome": "reopened", "installation_state": "not-installed",
            "plan_id": self.rp_plan_id, "tree_id": rp_ref.tree_id,
            "tree_content_sha256": rp_ref.content_sha256,
            "retained_file_count": 1, "retained_total_bytes": 5,
            "files": self.rp_files, "unresolved": [],
            "curseforge_file_identity_state": "unproven-by-local-sidecar",
        }

        override_files = [
            {"relative_path": "config/client.cfg", "size": 4,
             "sha256": "sha256:" + sha256(b"test").hexdigest(),
             "source": "release-overrides"},
            {"relative_path": "config/empty.txt", "size": 0,
             "sha256": "sha256:" + sha256(b"").hexdigest(),
             "source": "release-overrides"},
        ]
        override_body = {
            "format": layout.OVERRIDE_PLAN_FORMAT, "schema_version": 1,
            "profile": "supersymmetry", "input_plan_id": self.input_plan["plan_id"],
            "release_id": self.input_plan["release_id"],
            "version": self.input_plan["version"],
            "asset_sha256": self.input_plan["asset_sha256"],
            "asset_size": self.input_plan["asset_size"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "layout_policy_id": layout._override_policy_id(self.policy),
            "override_source_root": "overrides/", "destination_root": "minecraft-root",
            "override_file_count": 2, "override_total_bytes": 4,
            "override_content_sha256": "sha256:" + sha256(_canonical(override_files)).hexdigest(),
            "files": override_files, "installation_state": "not-installed",
            "runtime_qualification_state": "not-qualified",
        }
        self.override_plan_id = ("workbench-pack-release-override-custody-plan:sha256:"
                                 + sha256(_canonical(override_body)).hexdigest())
        override_plan = {**override_body, "plan_id": self.override_plan_id}
        self._source_tree(
            "pack-release-overrides", self.override_plan_id,
            override_body["layout_policy_id"],
            {"overrides/config/client.cfg": b"test", "overrides/config/empty.txt": b"",
             "source-lock.json": layout._override_lock(override_plan)},
        )
        self.kwargs = dict(
            layout_policy_path=self.layout_policy, resourcepack_policy_path=self.rp_policy,
            local_input_policy_path=self.local_policy,
            prior_mod_policy_path=self.prior_mod_policy,
            mod_policy_path=self.mod_policy, mod_plan_id=self.mod_plan_id,
            resourcepack_plan_id=self.rp_plan_id, override_plan_id=self.override_plan_id,
            state_root=self.state, config_home=self.config,
        )

    def _source_tree(self, location: str, plan_id: str, policy_id: str,
                     files: dict[str, bytes]):
        source_root = self.state / location
        target = source_root / plan_id.rsplit(":", 1)[-1] / "snapshot"
        host = CoreManagedTrees(
            workspace=self.state, configuration_home=self.config,
            locations={"artifacts": source_root}, owner_id="supersymmetry",
            policy_id=policy_id,
        )
        with host.stage("artifacts", target.name, requested_path=target) as stage:
            stage.path.mkdir(mode=0o700)
            for relative, data in files.items():
                destination = stage.path / relative
                parent = pinned_directory(destination.parent, create=True)
                os.close(parent)
                destination.write_bytes(data)
                destination.chmod(0o600)
            return stage.publish(
                validate=lambda _path: None, domain_id=plan_id,
                inventory_policy=EXACT_INVENTORY_POLICY,
            )

    def _call(self, operation, *, check_cancelled=None, expected_plan_id=None):
        kwargs = dict(self.kwargs)
        if check_cancelled is not None:
            kwargs["check_cancelled"] = check_cancelled
        if expected_plan_id is not None:
            kwargs["expected_plan_id"] = expected_plan_id
        with (patch.object(composition, "reopen_mod_augmentation_v2",
                           return_value=self.mod_result) as mod_reopen,
              patch.object(composition, "reopen_prism_resourcepacks",
                           return_value=self.rp_result) as rp_reopen):
            result = operation(self.input_plan, **kwargs)
            mod_reopen.assert_called()
            rp_reopen.assert_called()
            return result

    def _plan(self, *, check_cancelled=None):
        return self._call(composition.plan_release_client_composition,
                          check_cancelled=check_cancelled)

    def _target(self, plan: dict) -> Path:
        return (self.state / "pack-release-client-compositions"
                / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot")

    def test_exact_plan_apply_reopen_and_reuse_without_release_zip(self) -> None:
        plan = self._plan()
        self.assertEqual("blocked", plan["state"])
        self.assertEqual("reviewed", plan["custody_state"])
        self.assertEqual("acquire", plan["action"])
        self.assertEqual(5, plan["file_count"])
        self.assertEqual(22, plan["total_bytes"])
        self.assertEqual(["external-file-origin-unverified", "runtime-compatibility-unqualified"],
                         plan["blockers"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        retained = self._call(composition.apply_release_client_composition,
                              expected_plan_id=plan["plan_id"])
        self.assertEqual("retained", retained["outcome"])
        self.assertEqual("not-installed", retained["installation_state"])
        self.assertEqual("not-qualified", retained["runtime_qualification_state"])
        self.assertEqual([plan[name]["tree_id"] for name in ("mod", "resourcepacks", "overrides")],
                         retained["source_tree_ids"])
        target = self._target(plan)
        self.assertEqual(b"alpha", (target / "minecraft-root/mods/Alpha.jar").read_bytes())
        self.assertEqual(b"optional", (target / "minecraft-root/mods/Optional.jar").read_bytes())
        self.assertEqual(b"theme", (target / "minecraft-root/resourcepacks/Theme.zip").read_bytes())
        self.assertEqual(b"test", (target / "minecraft-root/config/client.cfg").read_bytes())
        self.assertEqual(b"", (target / "minecraft-root/config/empty.txt").read_bytes())
        self.assertTrue((target / "source-lock.json").is_file())
        self.assertFalse((target / "minecraft-root/source-lock.json").exists())
        reopened = self._call(composition.reopen_release_client_composition,
                              expected_plan_id=plan["plan_id"])
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(retained["tree_id"], reopened["tree_id"])
        self.assertEqual("reuse", self._plan()["action"])
        reused = self._call(composition.apply_release_client_composition,
                            expected_plan_id=plan["plan_id"])
        self.assertEqual("reused", reused["outcome"])
        self.assertEqual(retained["tree_content_sha256"], reused["tree_content_sha256"])

    def test_changed_source_before_copy_is_refused(self) -> None:
        plan = self._plan()
        (self.mod_path / "mods/Alpha.jar").write_bytes(b"ALPHA")
        with self.assertRaises((ValueError, composition.ManagedTreeError)):
            self._call(composition.apply_release_client_composition,
                       expected_plan_id=plan["plan_id"])
        self.assertFalse(self._target(plan).exists())

    def test_missing_optional_choice_is_refused(self) -> None:
        # Build a different, internally valid mod lock/tree containing only
        # the required JAR while the selected manifest still asks for both.
        body = json.loads((self.mod_path / "source-lock.json").read_text(encoding="utf-8"))
        body["format"] = PLAN_FORMAT_V2
        body.pop("plan_id")
        body["files"] = body["files"][:1]
        body["retained_file_count"] = 1
        body["retained_total_bytes"] = 5
        selected_plan_id = ("workbench-pack-release-mod-augmentation-plan:sha256:"
                            + sha256(_canonical(body)).hexdigest())
        lock = _canonical({**body, "format": LOCK_FORMAT_V2,
                           "plan_id": selected_plan_id}) + b"\n"
        reference = self._source_tree(
            "pack-release-mod-augmentations", selected_plan_id, body["policy_id"],
            {"mods/Alpha.jar": b"alpha", "source-lock.json": lock},
        )
        self.mod_result.update({
            "plan_id": selected_plan_id, "tree_id": reference.tree_id,
            "tree_content_sha256": reference.content_sha256,
            "retained_file_count": 1, "retained_total_bytes": 5,
        })
        self.kwargs["mod_plan_id"] = selected_plan_id
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            self._plan()

    def test_changed_composed_tree_is_refused_on_reopen(self) -> None:
        plan = self._plan()
        self._call(composition.apply_release_client_composition,
                   expected_plan_id=plan["plan_id"])
        (self._target(plan) / "minecraft-root/config/client.cfg").write_bytes(b"evil")
        with self.assertRaises((ValueError, composition.ManagedTreeError)):
            self._call(composition.reopen_release_client_composition,
                       expected_plan_id=plan["plan_id"])

    def test_cancelled_copy_cannot_reuse_unvalidated_reservation(self) -> None:
        plan = self._plan()

        class Cancelled(Exception):
            pass

        def check_cancelled() -> None:
            parent = self._target(plan).parent
            if any(parent.glob(".workbench-tree-*.pending/payload/minecraft-root/config/client.cfg")):
                raise Cancelled("cancelled during composition copy")

        with self.assertRaisesRegex(Cancelled, "during composition copy"):
            self._call(composition.apply_release_client_composition,
                       expected_plan_id=plan["plan_id"], check_cancelled=check_cancelled)
        self.assertFalse(self._target(plan).exists())
        with self.assertRaisesRegex(ValueError, "incomplete stage"):
            self._plan()
        with self.assertRaisesRegex(ValueError, "not prepared"):
            self._call(composition.reconcile_release_client_composition,
                       expected_plan_id=plan["plan_id"])

    def test_published_without_commit_requires_exact_core_reconciliation(self) -> None:
        plan = self._plan()
        retained = self._call(composition.apply_release_client_composition,
                              expected_plan_id=plan["plan_id"])
        host, _ = composition._host(self.state, self.config, plan["layout_policy_id"])
        nonce = retained["tree_id"].rsplit(":", 1)[-1]
        host.catalog.trees._path("commits", nonce).unlink()
        with self.assertRaisesRegex(ValueError, "incomplete stage.*published-uncommitted"):
            self._plan()
        recovered = self._call(composition.reconcile_release_client_composition,
                               expected_plan_id=plan["plan_id"])
        self.assertEqual("reconciled", recovered["outcome"])
        self.assertEqual(retained["tree_id"], recovered["tree_id"])
        self.assertEqual("reopened", self._call(
            composition.reopen_release_client_composition,
            expected_plan_id=plan["plan_id"],
        )["outcome"])

    def test_layout_mapping_boundary_at_193_ids_and_4168_zero_byte_overrides(self) -> None:
        empty = "sha256:" + sha256(b"").hexdigest()
        mods = [
            {"project_id": index + 1, "file_id": index + 101,
             "relative_path": f"mods/m{index:03d}.jar", "size": 1,
             "sha256": "sha256:" + sha256(bytes([index])).hexdigest()}
            for index in range(190)
        ]
        resourcepacks = [
            {"project_id": index + 191, "file_id": index + 291,
             "relative_path": f"resourcepacks/r{index}.zip", "size": 1,
             "sha256": empty}
            for index in range(3)
        ]
        overrides = [
            {"relative_path": f"config/synthetic-{index:04d}.cfg", "size": 0,
             "sha256": empty, "source": "release-overrides"}
            for index in range(4168)
        ]
        declared = {(row["project_id"], row["file_id"]): True
                    for row in mods + resourcepacks}
        rows = layout._destinations(mods, resourcepacks, overrides, set(), declared, set())
        self.assertEqual(4361, len(rows))
        self.assertEqual(4168, sum(row["source"] == "release-overrides" for row in rows))
        self.assertEqual(193, len({(row["project_id"], row["file_id"]) for row in rows
                                   if "project_id" in row}))


if __name__ == "__main__":
    unittest.main()
