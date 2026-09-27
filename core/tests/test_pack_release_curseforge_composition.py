"""Exact official release composition from authorized files and ZIP overrides."""

from __future__ import annotations

from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import pack_release_client_layout as layout
from workbench_core import pack_release_curseforge_composition as composition
from workbench_core.durable_files import _directory as pinned_directory
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.pack_release_curseforge import (
    acquire_curseforge_file, plan_curseforge_acquisition,
    publish_curseforge_acquisition,
)
from workbench_core.pack_release_local import _canonical
from workbench_core.storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


ROOT = Path(__file__).resolve().parents[2]
URL = "https://edge.forgecdn.net/files/1234/567/selected.jar"


class _Download(BytesIO):
    def geturl(self) -> str:
        return URL


class PackReleaseCurseForgeCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.state.mkdir(mode=0o700)
        self.config.mkdir(mode=0o700)
        self.external = {
            (10, 100): ("Alpha.jar", b"alpha"),
            (20, 200): ("Theme.zip", b"theme"),
            (30, 300): ("Optional.jar", b"optional"),
        }
        body = {
            "format": "workbench-pack-release-input-plan-v1", "schema_version": 1,
            "profile": "supersymmetry", "source_kind": "published-client-archive",
            "release_id": "profile-release:sha256:" + "2" * 64,
            "version": "0.1.16.16", "asset_sha256": "sha256:" + "3" * 64,
            "asset_size": 100, "manifest_sha256": "sha256:" + "4" * 64,
            "archive_member_count": 2, "override_file_count": 1,
            "other_file_count": 0,
            "external_files": [
                {"project_id": 10, "file_id": 100, "required": True},
                {"project_id": 20, "file_id": 200, "required": True},
                {"project_id": 30, "file_id": 300, "required": False},
            ],
            "acquisition_state": "external-file-bytes-unresolved",
        }
        self.input_plan = {
            **body, "plan_id": "workbench-pack-release-input-plan:sha256:"
            + sha256(_canonical(body)).hexdigest(),
        }
        self.rp_policy = self.root / "resourcepack-policy.json"
        self.rp_policy.write_bytes(_canonical({
            "format": "workbench-supersymmetry-release-resourcepack-input-policy-v1",
            "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"],
            "version": self.input_plan["version"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "external_file_count": 3, "allowed_extensions": [".zip"],
            "max_file_bytes": 536870912, "max_total_bytes": 1610612736,
            "placements": [{"project_id": 20, "file_id": 200,
                            "required": True, "destination_root": "resourcepacks"}],
        }) + b"\n")
        self.override_content = b"setting"
        self.layout_policy = self.root / "layout-policy.json"
        self.layout = {
            "format": layout.POLICY_FORMAT, "schema_version": 1,
            "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"],
            "release_id": self.input_plan["release_id"],
            "version": self.input_plan["version"],
            "asset_sha256": self.input_plan["asset_sha256"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "override_source_root": "overrides/",
            "destination_root": "minecraft-root",
            "override_file_count": 1,
            "override_total_bytes": len(self.override_content),
            "optional_selected": [{"project_id": 30, "file_id": 300}],
            "collision_policy": "reject",
            "installation_state": "not-installed",
            "runtime_qualification_state": "not-qualified",
        }
        self.layout_policy.write_bytes(_canonical(self.layout) + b"\n")
        self._create_overrides()
        self.acquisition = self._acquire_files(((30, 300),))

    def _create_overrides(self) -> None:
        files = [{
            "relative_path": "config/client.cfg", "size": len(self.override_content),
            "sha256": "sha256:" + sha256(self.override_content).hexdigest(),
            "source": "release-overrides",
        }]
        body = {
            "format": layout.OVERRIDE_PLAN_FORMAT, "schema_version": 1,
            "profile": "supersymmetry", "input_plan_id": self.input_plan["plan_id"],
            "release_id": self.input_plan["release_id"],
            "version": self.input_plan["version"],
            "asset_sha256": self.input_plan["asset_sha256"],
            "asset_size": self.input_plan["asset_size"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "layout_policy_id": layout._override_policy_id(self.layout),
            "override_source_root": "overrides/",
            "destination_root": "minecraft-root",
            "override_file_count": 1,
            "override_total_bytes": len(self.override_content),
            "override_content_sha256": "sha256:" + sha256(_canonical(files)).hexdigest(),
            "files": files, "installation_state": "not-installed",
            "runtime_qualification_state": "not-qualified",
        }
        self.override_plan_id = ("workbench-pack-release-override-custody-plan:sha256:"
                                 + sha256(_canonical(body)).hexdigest())
        plan = {**body, "plan_id": self.override_plan_id}
        source_root = self.state / "pack-release-overrides"
        target = source_root / self.override_plan_id.rsplit(":", 1)[-1] / "snapshot"
        host = CoreManagedTrees(
            workspace=self.state, configuration_home=self.config,
            locations={"artifacts": source_root}, owner_id="supersymmetry",
            policy_id=body["layout_policy_id"],
        )
        with host.stage("artifacts", target.name, requested_path=target) as stage:
            stage.path.mkdir(mode=0o700)
            for relative, content in {
                "overrides/config/client.cfg": self.override_content,
                "source-lock.json": layout._override_lock(plan),
            }.items():
                destination = stage.path / relative
                directory = pinned_directory(destination.parent, create=True)
                os.close(directory)
                destination.write_bytes(content)
                destination.chmod(0o600)
            stage.publish(
                validate=lambda path: layout._validate_override_stage(path, plan),
                domain_id=self.override_plan_id,
                inventory_policy=EXACT_INVENTORY_POLICY,
            )

    def _acquire_files(self, optional_selected: tuple[tuple[int, int], ...]) -> dict:
        acquisition = plan_curseforge_acquisition(
            self.input_plan, resourcepack_policy_path=self.rp_policy,
            optional_selected=optional_selected,
        )
        selected = {(row["project_id"], row["file_id"]) for row in acquisition["files"]}
        for (project, file), (filename, content) in self.external.items():
            if (project, file) not in selected:
                continue
            acquire_curseforge_file(
                acquisition, project_id=project, file_id=file,
                state_root=self.state, provider_key="synthetic-Workbench-key",
                metadata_fetcher=lambda p, f, _key, n=filename, b=content: {
                    "data": {"id": f, "modId": p, "gameId": 432,
                             "isAvailable": True, "fileName": n,
                             "fileLength": len(b),
                             "hashes": [{"algo": 1, "value": sha1(b).hexdigest()}],
                             "downloadUrl": URL}},
                download_opener=lambda _url, b=content: _Download(b),
            )
        publish_curseforge_acquisition(
            acquisition, state_root=self.state, config_home=self.config,
            expected_plan_id=acquisition["plan_id"],
        )
        return acquisition

    def _kwargs(self) -> dict:
        return dict(
            layout_policy_path=self.layout_policy,
            resourcepack_policy_path=self.rp_policy,
            acquisition_plan_id=self.acquisition["plan_id"],
            override_plan_id=self.override_plan_id,
            state_root=self.state, config_home=self.config,
        )

    def test_compose_exact_official_payload_and_reopen(self) -> None:
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **self._kwargs(),
        )
        self.assertEqual("acquire", plan["action"])
        self.assertEqual(4, plan["file_count"])
        self.assertEqual(["runtime-compatibility-unqualified"], plan["blockers"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        result = composition.apply_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        self.assertEqual(composition.RESULT_FORMAT, result["format"])
        self.assertEqual("retained", result["outcome"])
        self.assertEqual("official-release-curseforge", result["source_kind"])
        target = (self.state / "pack-release-client-compositions"
                  / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot")
        self.assertEqual(b"alpha", (target / "minecraft-root/mods/Alpha.jar").read_bytes())
        self.assertEqual(b"theme", (target / "minecraft-root/resourcepacks/Theme.zip").read_bytes())
        self.assertEqual(b"optional", (target / "minecraft-root/mods/Optional.jar").read_bytes())
        self.assertEqual(self.override_content,
                         (target / "minecraft-root/config/client.cfg").read_bytes())
        self.assertEqual([plan["acquisition"]["tree_id"], plan["overrides"]["tree_id"]],
                         result["source_tree_ids"])
        self.assertEqual("reused", composition.apply_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )["outcome"])
        reopened = composition.reopen_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(result["tree_id"], reopened["tree_id"])

    def test_user_can_decline_optional_file(self) -> None:
        required = self._acquire_files(())
        kwargs = {**self._kwargs(), "acquisition_plan_id": required["plan_id"],
                  "optional_selected": ()}
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **kwargs,
        )
        self.assertEqual([], plan["optional_selected"])
        self.assertEqual(3, plan["file_count"])
        result = composition.apply_curseforge_client_composition(
            self.input_plan, **kwargs, expected_plan_id=plan["plan_id"],
        )
        target = (self.state / "pack-release-client-compositions"
                  / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot")
        self.assertFalse((target / "minecraft-root/mods/Optional.jar").exists())
        self.assertEqual("retained", result["outcome"])

    def test_changed_composition_lock_refuses_reopen(self) -> None:
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **self._kwargs(),
        )
        composition.apply_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        lock = (self.state / "pack-release-client-compositions"
                / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot/source-lock.json")
        lock.write_bytes(lock.read_bytes() + b"x")
        with self.assertRaises(ValueError):
            composition.reopen_curseforge_client_composition(
                self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
            )

    def test_published_without_commit_reconciles_exact_core_intent(self) -> None:
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **self._kwargs(),
        )
        result = composition.apply_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        host, _ = composition._host(self.state, self.config, plan["layout_policy_id"])
        nonce = result["tree_id"].rsplit(":", 1)[-1]
        host.catalog.trees._path("commits", nonce).unlink()
        with self.assertRaisesRegex(ValueError, "incomplete Core stage"):
            composition.plan_curseforge_client_composition(
                self.input_plan, **self._kwargs(),
            )
        recovered = composition.reconcile_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        self.assertEqual("reconciled", recovered["outcome"])
        self.assertEqual(result["tree_id"], recovered["tree_id"])

    def test_saved_plan_reopens_after_current_profile_policy_is_removed(self) -> None:
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **self._kwargs(),
        )
        result = composition.apply_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        self.layout_policy.unlink()
        self.rp_policy.unlink()
        reopened = composition.reopen_curseforge_client_composition_by_plan_id(
            expected_plan_id=plan["plan_id"], state_root=self.state,
            config_home=self.config,
        )
        self.assertEqual(result["tree_id"], reopened["tree_id"])
        self.assertEqual("reopened", reopened["outcome"])

    def test_saved_plan_reopen_refuses_changed_referenced_tree(self) -> None:
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **self._kwargs(),
        )
        composition.apply_curseforge_client_composition(
            self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
        )
        source = (self.state / "pack-release-external-inputs"
                  / self.acquisition["plan_id"].rsplit(":", 1)[-1]
                  / "snapshot/mods/Alpha.jar")
        source.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            composition.reopen_curseforge_client_composition_by_plan_id(
                expected_plan_id=plan["plan_id"], state_root=self.state,
                config_home=self.config,
            )

    def test_wrong_acquisition_or_changed_source_refuses(self) -> None:
        with self.assertRaisesRegex(ValueError, "another external file plan"):
            composition.plan_curseforge_client_composition(
                self.input_plan, **{
                    **self._kwargs(), "acquisition_plan_id": "wrong",
                },
            )
        target = (self.state / "pack-release-external-inputs"
                  / self.acquisition["plan_id"].rsplit(":", 1)[-1]
                  / "snapshot/mods/Alpha.jar")
        target.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            composition.plan_curseforge_client_composition(
                self.input_plan, **self._kwargs(),
            )

    def test_interrupted_copy_is_not_promoted(self) -> None:
        plan = composition.plan_curseforge_client_composition(
            self.input_plan, **self._kwargs(),
        )
        with patch.object(composition, "_copy_to_stage", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                composition.apply_curseforge_client_composition(
                    self.input_plan, **self._kwargs(), expected_plan_id=plan["plan_id"],
                )
        target = (self.state / "pack-release-client-compositions"
                  / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot")
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(ValueError, "incomplete Core stage"):
            composition.plan_curseforge_client_composition(
                self.input_plan, **self._kwargs(),
            )


if __name__ == "__main__":
    unittest.main()
