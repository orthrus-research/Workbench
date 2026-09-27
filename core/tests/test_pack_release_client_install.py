"""Synthetic release installation; no launcher or Java executable is run."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import pack_release_client_install as install
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.pack_release_client_composition import LOCK_FORMAT, PLAN_FORMAT
from workbench_core.pack_release_prism_zip_composition import (
    apply_prism_zip_composition, plan_prism_zip_composition,
)
from workbench_core.pack_release_local import _canonical
from workbench_core.storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


ROOT = Path(__file__).resolve().parents[2]


class ReleaseClientInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.launcher = self.root / "PrismLauncher"
        for path in (self.state, self.config, self.launcher, self.launcher / "instances"):
            path.mkdir(mode=0o700)
        (self.launcher / "prismlauncher.cfg").write_text("[General]\n", encoding="utf-8")
        self.java = self.root / "jdk/bin/java"
        self.java.parent.mkdir(mode=0o700, parents=True)
        self.java.write_bytes(b"synthetic-java")
        self.java_result = {
            "format": "workbench-java-runtime-result-v2", "source": "managed",
            "receipt": {
                "format": "workbench-java-runtime-receipt-v2", "state": "ready",
                "runtime_id": "sha256:" + "a" * 64,
                "policy": {"feature_version": 25}, "host": {"os": "linux"},
                "probe": {"java_version": "25.0.4"},
                "target": {"java_uri": self.java.as_uri()},
            },
        }
        body = {
            "format": PLAN_FORMAT, "schema_version": 2, "profile": "supersymmetry",
            "version": "0.1.16.16", "file_count": 1, "total_bytes": 5,
            "asset_sha256": "sha256:" + "b" * 64,
            "files": [{"relative_path": "mods/Alpha.jar", "size": 5,
                       "sha256": "sha256:" + sha256(b"alpha").hexdigest()}],
            "blockers": ["external-file-origin-unverified",
                         "runtime-compatibility-unqualified"],
        }
        self.composition_plan_id = (
            "workbench-pack-release-client-composition-plan:sha256:"
            + sha256(_canonical(body)).hexdigest()
        )
        host = CoreManagedTrees(
            workspace=self.state, configuration_home=self.config,
            locations={"artifacts": self.state / "pack-release-client-compositions"},
            owner_id="supersymmetry", policy_id="test-install-source",
        )
        target = (self.state / "pack-release-client-compositions"
                  / self.composition_plan_id.rsplit(":", 1)[-1] / "snapshot")
        with host.stage("artifacts", target.name, requested_path=target) as staged:
            staged.path.mkdir(mode=0o700)
            (staged.path / "minecraft-root/mods").mkdir(parents=True, mode=0o700)
            (staged.path / "minecraft-root/mods/Alpha.jar").write_bytes(b"alpha")
            lock = {**body, "format": LOCK_FORMAT, "plan_id": self.composition_plan_id}
            (staged.path / "source-lock.json").write_bytes(_canonical(lock) + b"\n")
            (staged.path / "source-lock.json").chmod(0o600)
            reference = staged.publish(
                validate=lambda path: self.assertTrue((path / "source-lock.json").is_file()),
                domain_id=self.composition_plan_id,
                inventory_policy=EXACT_INVENTORY_POLICY,
            )
        self.composition = {
            "format": "workbench-pack-release-client-composition-result-v2",
            "outcome": "reopened", "installation_state": "not-installed",
            "plan_id": self.composition_plan_id, "tree_id": reference.tree_id,
            "tree_content_sha256": reference.content_sha256,
            "file_count": 1, "total_bytes": 5,
        }
        self.archive = self.root / "cleanroom.zip"
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("instance.cfg", "name=Cleanroom\n")
            archive.writestr("mmc-pack.json", json.dumps({"components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "0.0.0.0"},
            ]}))
            archive.writestr("patches/net.minecraft.json", "{}")
            archive.writestr("patches/net.minecraftforge.json", "{}")
        raw = self.archive.read_bytes()
        self.policy = self.root / "policy.json"
        self.policy.write_bytes(_canonical({
            "format": install.POLICY_FORMAT, "schema_version": 1,
            "profile": "supersymmetry",
            "minecraft_version": "1.12.2", "launcher": "prism",
            "platform_profile_id": "workbench-platform:cleanroom:provisional",
            "cleanroom_version": "0.6.12-alpha",
            "cleanroom_client": {"size": len(raw),
                                 "sha256": "sha256:" + sha256(raw).hexdigest()},
            "recommended_java_feature": 25, "memory_mib": 4096,
            "accepted_source_limitations": [
                "external-file-origin-unverified",
                "runtime-compatibility-unqualified"],
        }) + b"\n")
        cache = self.state / "artifacts/sha256" / sha256(raw).hexdigest()
        cache.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        cache.write_bytes(raw)
        self.args = dict(
            policy_path=self.policy, state_root=self.state, config_home=self.config,
            launcher_root=self.launcher, managed_java_result=self.java_result,
        )
        tooling = patch.object(install, "inspect_tools", return_value={
            "tools": {"prism": {"state": "ready",
                                "executable": str(self.root / "managed/PrismLauncher")}}})
        tooling.start()
        self.addCleanup(tooling.stop)

    def test_plan_install_reopen_and_launcher_mutation(self) -> None:
        plan = install.plan_release_client_install(self.composition, **self.args)
        self.assertEqual(("ready", "acquire", []),
                         (plan["state"], plan["action"], plan["blockers"]))
        result = install.apply_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        instance = Path(result["instance_path"])
        self.assertEqual("installed", result["installation_state"])
        self.assertEqual("not-qualified", result["runtime_qualification_state"])
        self.assertEqual(b"alpha", (instance / ".minecraft/mods/Alpha.jar").read_bytes())
        self.assertIn("JavaPath=" + str(self.java),
                      (instance / "instance.cfg").read_text(encoding="utf-8"))
        reopened = install.reopen_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        self.assertEqual("initial", reopened["instance_state"])
        (instance / ".minecraft/logs").mkdir(mode=0o700)
        (instance / ".minecraft/logs/latest.log").write_text("launched", encoding="utf-8")
        modified = install.reopen_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        self.assertEqual("launcher-modified", modified["instance_state"])

    def test_fresh_prism_data_root_initializes_without_replacing_it(self) -> None:
        fresh = self.root / ".local/share/fresh-PrismLauncher"
        plan = install.plan_prism_data_root(fresh, state_root=self.state)
        self.assertEqual(("ready", "initialize"), (plan["state"], plan["action"]))
        created = install.initialize_prism_data_root(
            fresh, state_root=self.state, expected_plan_id=plan["plan_id"])
        self.assertEqual("initialized", created["outcome"])
        self.assertTrue((fresh / "instances").is_dir())
        self.assertEqual(b"[General]\nConfigVersion=1.3\n",
                         (fresh / "prismlauncher.cfg").read_bytes())
        self.assertEqual("reuse", install.plan_prism_data_root(
            fresh, state_root=self.state)["action"])
        self.assertEqual("reused", install.initialize_prism_data_root(
            fresh, state_root=self.state, expected_plan_id=plan["plan_id"])["outcome"])

    def test_interrupted_prism_data_root_reconciles_only_complete_stage(self) -> None:
        fresh = self.root / "interrupted-PrismLauncher"
        plan = install.plan_prism_data_root(fresh, state_root=self.state)
        count = 0

        def cancel() -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            install.initialize_prism_data_root(
                fresh, state_root=self.state, expected_plan_id=plan["plan_id"],
                check_cancelled=cancel)
        pending = install.plan_prism_data_root(fresh, state_root=self.state)
        self.assertEqual("reconcile", pending["action"])
        recovered = install.reconcile_prism_data_root(
            fresh, state_root=self.state, expected_plan_id=plan["plan_id"])
        self.assertEqual("reconciled", recovered["outcome"])
        self.assertTrue((fresh / "instances").is_dir())

    def test_incomplete_prism_root_stage_is_retained_then_abandoned(self) -> None:
        fresh = self.root / "incomplete-PrismLauncher"
        plan = install.plan_prism_data_root(fresh, state_root=self.state)
        write = install._write_file

        def interrupt(path: Path, data: bytes) -> None:
            if path.name == "prismlauncher.cfg":
                raise RuntimeError("interrupted")
            write(path, data)

        with patch.object(install, "_write_file", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                install.initialize_prism_data_root(
                    fresh, state_root=self.state, expected_plan_id=plan["plan_id"])
        self.assertEqual("reconcile", install.plan_prism_data_root(
            fresh, state_root=self.state)["action"])
        with self.assertRaises(ValueError):
            install.reconcile_prism_data_root(
                fresh, state_root=self.state, expected_plan_id=plan["plan_id"])
        abandoned = install.abandon_interrupted_prism_data_root(
            fresh, state_root=self.state, expected_plan_id=plan["plan_id"])
        self.assertTrue(Path(abandoned["retained_stage_path"]).is_dir())
        self.assertEqual("ready", install.plan_prism_data_root(
            fresh, state_root=self.state)["state"])

    def test_missing_bootstrap_is_explicit_blocker(self) -> None:
        (self.state / "artifacts/sha256"
         / install.load_release_install_policy(self.policy)["cleanroom_client"]["sha256"].removeprefix("sha256:")).unlink()
        plan = install.plan_release_client_install(self.composition, **self.args)
        self.assertEqual("blocked", plan["state"])
        self.assertIn("cleanroom-bootstrap-unavailable", plan["blockers"])

    def test_missing_managed_prism_is_separate_blocker(self) -> None:
        with patch.object(install, "inspect_tools", return_value={
            "tools": {"prism": {"state": "missing", "executable": None}}}):
            plan = install.plan_release_client_install(self.composition, **self.args)
        self.assertEqual("blocked", plan["state"])
        self.assertIn("prism-executable-unavailable", plan["blockers"])
        self.assertNotIn("prism-root-uninitialized-or-unsupported-filesystem", plan["blockers"])

    def test_reopen_after_bootstrap_cache_eviction(self) -> None:
        plan = install.plan_release_client_install(self.composition, **self.args)
        install.apply_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        cache = self.state / "artifacts/sha256" / install.load_release_install_policy(
            self.policy)["cleanroom_client"]["sha256"].removeprefix("sha256:")
        cache.unlink()
        reopened = install.reopen_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        self.assertEqual("initial", reopened["instance_state"])

    def test_selected_java8_and_unprobed_user_path(self) -> None:
        default_plan = install.plan_release_client_install(self.composition, **self.args)
        managed8 = json.loads(json.dumps(self.java_result))
        managed8["receipt"]["policy"]["feature_version"] = 8
        managed8["receipt"]["probe"]["java_version"] = "1.8.0_452"
        args = {**self.args, "managed_java_result": managed8}
        plan = install.plan_release_client_install(self.composition, **args)
        self.assertEqual("ready", plan["state"])
        self.assertEqual(8, plan["java_selected_feature"])
        self.assertEqual("not-qualified", plan["runtime_qualification_state"])
        self.assertNotEqual(default_plan["instance_path"], plan["instance_path"])
        managed8_result = install.apply_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **args)
        managed8_cfg = (Path(managed8_result["instance_path"]) / "instance.cfg").read_text(
            encoding="utf-8")
        self.assertIn("name=Workbench Supersymmetry 0.1.16.16 (Java 8)", managed8_cfg)

        missing_home = self.root / "custom/java-that-is-not-yet-installed"
        user_choice = {
            "format": "workbench-java-runtime-result-v3", "schema_version": 3,
            "outcome": "selected", "source": "user-path",
            "runtime": {"state": "unverified", "origin": "user",
                        "java_home_uri": missing_home.as_uri()},
        }
        args["managed_java_result"] = user_choice
        plan = install.plan_release_client_install(self.composition, **args)
        self.assertEqual("ready", plan["state"])
        self.assertEqual("user-path-unverified", plan["java_selection_state"])
        self.assertNotEqual(default_plan["instance_path"], plan["instance_path"])
        result = install.apply_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **args)
        instance_cfg = (Path(result["instance_path"]) / "instance.cfg").read_text(
            encoding="utf-8")
        self.assertIn("JavaPath=" + str(missing_home / "bin/java"), instance_cfg)
        self.assertNotIn("JavaVersion=", instance_cfg)
        self.assertIn("name=Workbench Supersymmetry 0.1.16.16 (custom Java)", instance_cfg)

    def test_receipt_gap_can_be_reconciled_without_replacing_instance(self) -> None:
        plan = install.plan_release_client_install(self.composition, **self.args)
        with patch.object(install, "_publish_receipt", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                install.apply_release_client_install(
                    self.composition, expected_plan_id=plan["plan_id"], **self.args)
        target = Path(plan["instance_path"])
        inode = target.stat().st_ino
        pending = install.plan_release_client_install(self.composition, **self.args)
        self.assertEqual("reconcile", pending["action"])
        recovered = install.reconcile_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        self.assertEqual("installed", recovered["installation_state"])
        self.assertEqual(inode, target.stat().st_ino)

    def test_imported_prism_composition_retains_actual_source_version(self) -> None:
        archive_path = self.root / "versioned-instance.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr("instance.cfg", "ManagedPackVersionName=0.1.16.15\n")
            archive.writestr("mmc-pack.json", json.dumps({"components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "0.6.8-alpha"},
            ]}))
            archive.writestr("patches/net.minecraftforge.json", "{}")
            archive.writestr("minecraft/mods/Alpha.jar", "alpha")
        source_plan = plan_prism_zip_composition(
            archive_path, state_root=self.state, config_home=self.config)
        result = apply_prism_zip_composition(
            archive_path, state_root=self.state, config_home=self.config,
            expected_plan_id=source_plan["plan_id"])
        install_plan = install.plan_release_client_install(result, **self.args)
        self.assertEqual("ready", install_plan["state"])
        self.assertIsNone(install_plan["release_version"])
        self.assertEqual("0.1.16.15", install_plan["source_version"])
        self.assertEqual("cleanroom", install_plan["source_platform"]["kind"])
        self.assertIsNone(install_plan["bootstrap_sha256"])
        installed = install.apply_release_client_install(
            result, expected_plan_id=install_plan["plan_id"], **self.args)
        self.assertEqual("user-prism-zip", installed["source_kind"])
        self.assertIsNone(installed["bootstrap_resource_id"])
        self.assertIn("0.1.16.15 (imported)",
                      (Path(installed["instance_path"]) / "instance.cfg").read_text())

    def test_real_prism_zip_importer_result_installs_separate_instance(self) -> None:
        archive_path = self.root / "developer-instance.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr("instance.cfg", "ManagedPackVersionName=0.1.16.15\n")
            archive.writestr("mmc-pack.json", json.dumps({"formatVersion": 1,
                "components": [{"uid": "net.minecraft", "version": "1.12.2"},
                               {"uid": "net.minecraftforge", "version": "0.6.8-alpha"}]}))
            archive.writestr("patches/net.minecraftforge.json", "{}")
            archive.writestr("minecraft/mods/Susy-Core.jar", "synthetic-jar")
        imported_plan = plan_prism_zip_composition(
            archive_path, state_root=self.state, config_home=self.config)
        imported = apply_prism_zip_composition(
            archive_path, state_root=self.state, config_home=self.config,
            expected_plan_id=imported_plan["plan_id"])
        install_plan = install.plan_release_client_install(imported, **self.args)
        self.assertEqual("ready", install_plan["state"])
        installed = install.apply_release_client_install(
            imported, expected_plan_id=install_plan["plan_id"], **self.args)
        self.assertEqual("user-prism-zip", installed["source_kind"])
        self.assertEqual("not-qualified", installed["runtime_qualification_state"])
        self.assertEqual(b"synthetic-jar",
                         (Path(installed["instance_path"]) / ".minecraft/mods/Susy-Core.jar").read_bytes())
        self.assertNotEqual(Path(installed["instance_path"]),
                            self.state / "pack-release-client-compositions" /
                            imported["plan_id"].rsplit(":", 1)[-1] / "snapshot")

    def test_imported_forge_and_custom_platforms_survive_without_cleanroom_bootstrap(self) -> None:
        pinned = (self.state / "artifacts/sha256"
                  / install.load_release_install_policy(self.policy)["cleanroom_client"][
                      "sha256"].removeprefix("sha256:"))
        pinned.unlink()
        for uid, version, kind in (
            ("net.minecraftforge", "14.23.5.2860", "forge"),
            ("custom.loader", "dev-branch-1", "custom"),
        ):
            with self.subTest(uid=uid):
                archive_path = self.root / (kind + "-instance.zip")
                manifest = json.dumps({"formatVersion": 1, "components": [
                    {"uid": "net.minecraft", "version": "1.12.2"},
                    {"uid": uid, "version": version},
                ]}).encode()
                patch_bytes = ("{\"component\":\"" + uid + "\"}").encode()
                instance_cfg = (b"[General]\nname=Original\nJavaPath=/old/java\n"
                                b"MaxMemAlloc=2048\nCustomSetting=preserved\n")
                with ZipFile(archive_path, "w") as archive:
                    archive.writestr("instance.cfg", instance_cfg)
                    archive.writestr("mmc-pack.json", manifest)
                    archive.writestr("patches/" + uid + ".json", patch_bytes)
                    archive.writestr("minecraft/mods/Susy-Core.jar", b"selected-mod")
                source_plan = plan_prism_zip_composition(
                    archive_path, state_root=self.state, config_home=self.config)
                imported = apply_prism_zip_composition(
                    archive_path, state_root=self.state, config_home=self.config,
                    expected_plan_id=source_plan["plan_id"])
                selected_args = dict(self.args)
                if kind == "forge":
                    implicit = install.plan_release_client_install(imported, **self.args)
                    self.assertEqual("blocked", implicit["state"])
                    self.assertIn("imported-forge-needs-java-8-or-custom-path",
                                  implicit["blockers"])
                    with self.assertRaisesRegex(ValueError, "unavailable or changed"):
                        install.apply_release_client_install(
                            imported, expected_plan_id=implicit["plan_id"],
                            **self.args)
                    selected_java = deepcopy(self.java_result)
                    selected_java["receipt"]["runtime_id"] = "sha256:" + "8" * 64
                    selected_java["receipt"]["policy"]["feature_version"] = 8
                    selected_java["receipt"]["probe"]["java_version"] = "8.0.452"
                    selected_args["managed_java_result"] = selected_java
                    custom_path = {
                        "format": "workbench-java-runtime-result-v3",
                        "schema_version": 3, "source": "user-path",
                        "outcome": "selected",
                        "runtime": {"state": "unverified",
                                    "java_home_uri": (self.root / "my-java").as_uri()},
                    }
                    custom = install.plan_release_client_install(
                        imported, **{**self.args, "managed_java_result": custom_path})
                    self.assertEqual("ready", custom["state"])
                    self.assertEqual("user-path-unverified", custom["java_selection_state"])
                    self.assertIsNone(custom["java_selected_feature"])
                plan = install.plan_release_client_install(imported, **selected_args)
                self.assertEqual("ready", plan["state"])
                self.assertEqual(kind, plan["source_platform"]["kind"])
                self.assertEqual(version, plan["source_platform"]["component_version"])
                self.assertIsNone(plan["bootstrap_sha256"])
                self.assertIsNone(plan["cleanroom_version"])
                installed = install.apply_release_client_install(
                    imported, expected_plan_id=plan["plan_id"], **selected_args)
                target = Path(installed["instance_path"])
                self.assertIsNone(installed["bootstrap_resource_id"])
                self.assertEqual(manifest, (target / "mmc-pack.json").read_bytes())
                self.assertEqual(patch_bytes,
                                 (target / "patches" / (uid + ".json")).read_bytes())
                self.assertEqual(b"selected-mod",
                                 (target / ".minecraft/mods/Susy-Core.jar").read_bytes())
                configured = (target / "instance.cfg").read_text(encoding="utf-8")
                self.assertIn("JavaPath=" + str(self.java), configured)
                self.assertIn("MaxMemAlloc=4096", configured)
                self.assertIn("CustomSetting=preserved", configured)
                self.assertNotIn("JavaPath=/old/java", configured)
                reopened = install.reopen_release_client_install(
                    imported, expected_plan_id=plan["plan_id"], **selected_args)
                self.assertEqual("initial", reopened["instance_state"])
                self.assertEqual(plan["source_platform"], reopened["source_platform"])

    def test_fresh_official_curseforge_composition_installs(self) -> None:
        from core.tests.test_pack_release_curseforge_composition import (
            PackReleaseCurseForgeCompositionTests,
        )
        from workbench_core import pack_release_curseforge_composition as official

        source = PackReleaseCurseForgeCompositionTests(
            "test_compose_exact_official_payload_and_reopen")
        source.setUp()
        self.addCleanup(source.doCleanups)
        composition_plan = official.plan_curseforge_client_composition(
            source.input_plan, **source._kwargs())
        composed = official.apply_curseforge_client_composition(
            source.input_plan, **source._kwargs(),
            expected_plan_id=composition_plan["plan_id"])
        launcher = source.root / "PrismLauncher"
        launcher.mkdir(mode=0o700)
        (launcher / "instances").mkdir(mode=0o700)
        (launcher / "prismlauncher.cfg").write_text("[General]\n")
        raw = self.archive.read_bytes()
        cache = source.state / "artifacts/sha256" / sha256(raw).hexdigest()
        cache.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        cache.write_bytes(raw)
        args = {**self.args, "state_root": source.state,
                "config_home": source.config, "launcher_root": launcher}
        install_plan = install.plan_release_client_install(composed, **args)
        self.assertEqual("ready", install_plan["state"])
        self.assertEqual("official-release-curseforge", install_plan["source_kind"])
        self.assertEqual(["runtime-compatibility-unqualified"],
                         install_plan["source_limitations"])
        installed = install.apply_release_client_install(
            composed, expected_plan_id=install_plan["plan_id"], **args)
        instance = Path(installed["instance_path"])
        self.assertEqual(b"alpha", (instance / ".minecraft/mods/Alpha.jar").read_bytes())
        self.assertEqual(b"theme", (instance / ".minecraft/resourcepacks/Theme.zip").read_bytes())
        self.assertEqual(source.override_content,
                         (instance / ".minecraft/config/client.cfg").read_bytes())

    def test_interrupted_copy_is_preserved_and_refuses_retry(self) -> None:
        plan = install.plan_release_client_install(self.composition, **self.args)
        count = 0

        def cancel() -> None:
            nonlocal count
            count += 1
            if count >= 3:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            install.apply_release_client_install(
                self.composition, expected_plan_id=plan["plan_id"],
                check_cancelled=cancel, **self.args)
        pending = install.plan_release_client_install(self.composition, **self.args)
        self.assertEqual("reconcile", pending["action"])
        self.assertIn("interrupted-install-needs-reconcile", pending["blockers"])
        with self.assertRaisesRegex(ValueError, "unavailable or changed"):
            install.apply_release_client_install(
                self.composition, expected_plan_id=plan["plan_id"], **self.args)
        with self.assertRaises(ValueError):
            install.reconcile_release_client_install(
                self.composition, expected_plan_id=plan["plan_id"], **self.args)
        abandoned = install.abandon_interrupted_release_client_install(
            self.composition, expected_plan_id=plan["plan_id"], **self.args)
        self.assertEqual("abandoned", abandoned["outcome"])
        self.assertTrue(Path(abandoned["retained_stage_path"]).is_dir())
        self.assertEqual("ready", install.plan_release_client_install(
            self.composition, **self.args)["state"])


if __name__ == "__main__":
    unittest.main()
