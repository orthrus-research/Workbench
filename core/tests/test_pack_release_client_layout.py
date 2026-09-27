"""Read-only release layout joins retained Core bytes without installing them."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

from workbench_core import pack_release_client_layout as layout
from workbench_core.pack_release import _input_plan
from workbench_core.pack_release_local import _canonical
from workbench_core.pack_release_mod_augmentation import LOCK_FORMAT_V2, PLAN_FORMAT_V2


ROOT = Path(__file__).resolve().parents[2]


class PackReleaseClientLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive_state = self.root / "archive-state"
        self.archive_state.mkdir()
        self.mod_state = self.root / "mod-state"
        self.mod_state.mkdir()
        self.rp_state = self.root / "rp-state"
        self.rp_state.mkdir()
        self.mod_config = self.root / "mod-config"
        self.mod_config.mkdir()
        self.rp_config = self.root / "rp-config"
        self.rp_config.mkdir()
        self.authority = self.root / "authority.json"
        self.authority.write_text("{}", encoding="utf-8")
        self.mod_policy = self.root / "mod-policy.json"
        self.mod_policy.write_text("{}", encoding="utf-8")
        self.prior_mod_policy = self.root / "prior-mod-policy.json"
        self.prior_mod_policy.write_text("{}", encoding="utf-8")
        self.local_policy = self.root / "local-policy.json"
        self.local_policy.write_text("{}", encoding="utf-8")
        self.rp_policy = self.root / "rp-policy.json"
        self.rp_policy.write_bytes(_canonical({
            "format": "workbench-supersymmetry-release-resourcepack-input-policy-v1",
            "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": "workbench-pack-release-input-plan:sha256:" + "0" * 64,
            "version": "0.1.16.16", "manifest_sha256": "sha256:" + "0" * 64,
            "external_file_count": 3, "allowed_extensions": [".zip"],
            "max_file_bytes": 536870912, "max_total_bytes": 1610612736,
            "placements": [{"project_id": 3, "file_id": 30, "required": True,
                            "destination_root": "resourcepacks"}],
        }) + b"\n")
        self.mod_files = [
            {"project_id": 1, "file_id": 10, "relative_path": "mods/Alpha.jar",
             "filename": "Alpha.jar", "size": 5, "sha256": "sha256:" + sha256(b"alpha").hexdigest()},
            {"project_id": 2, "file_id": 20, "relative_path": "mods/Optional.jar",
             "filename": "Optional.jar", "size": 8, "sha256": "sha256:" + sha256(b"optional").hexdigest()},
        ]
        self.rp_files = [
            {"project_id": 3, "file_id": 30, "relative_path": "resourcepacks/Theme.zip",
             "filename": "Theme.zip", "size": 5, "sha256": "sha256:" + sha256(b"theme").hexdigest()},
        ]
        self.mod_result = {
            "outcome": "reopened", "installation_state": "not-installed",
            "tree_id": "workbench-tree-v1:" + "a" * 32,
            "tree_content_sha256": "sha256:" + "a" * 64,
            "retained_file_count": 2, "retained_total_bytes": 13,
            "unresolved": [{"project_id": 3, "file_id": 30, "required": True}],
            "curseforge_file_identity_state": "unproven-by-local-assertions",
        }
        self.rp_result = {
            "outcome": "reopened", "installation_state": "not-installed",
            "tree_id": "workbench-tree-v1:" + "b" * 32,
            "tree_content_sha256": "sha256:" + "b" * 64,
            "retained_file_count": 1, "retained_total_bytes": 5,
            "files": self.rp_files, "unresolved": [],
            "curseforge_file_identity_state": "unproven-by-local-sidecar",
        }
        self.layout_policy = self.root / "layout-policy.json"
        self._archive({"config/client.cfg": b"test"})

    def _archive(self, overrides: dict[str, bytes], *, corrupt: bool = False) -> None:
        temp = self.root / "release.zip"
        manifest = {
            "manifestType": "minecraftModpack", "manifestVersion": 1,
            "version": "0.1.16.16", "overrides": "overrides",
            "minecraft": {"version": "1.12.2", "modLoaders": [
                {"id": "forge-14.23.5.2860", "primary": True}]},
            "files": [
                {"projectID": 1, "fileID": 10, "required": True},
                {"projectID": 2, "fileID": 20, "required": False},
                {"projectID": 3, "fileID": 30, "required": True},
            ],
        }
        with ZipFile(temp, "w", compression=ZIP_STORED) as archive:
            archive.writestr("manifest.json", _canonical(manifest))
            archive.writestr("overrides/", b"")
            for name, data in overrides.items():
                archive.writestr("overrides/" + name, data)
        if corrupt:
            raw = bytearray(temp.read_bytes())
            with ZipFile(temp) as archive:
                info = archive.getinfo("overrides/" + next(iter(overrides)))
            start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
            raw[start] ^= 1
            temp.write_bytes(raw)
        digest = sha256(temp.read_bytes()).hexdigest()
        path = self.archive_state / "artifacts" / "sha256" / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(temp.read_bytes())
        self.archive_path = path
        with ZipFile(path) as archive:
            entries = archive.infolist()
            manifest_raw = archive.read("manifest.json")
        selected = {"release_id": "profile-release:sha256:" + "c" * 64,
                    "version": "0.1.16.16", "asset_sha256": "sha256:" + digest,
                    "asset_size": path.stat().st_size}
        self.input_plan = _input_plan(selected, manifest, manifest_raw, entries)
        self.policy_value = {
            "format": layout.POLICY_FORMAT, "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"], "release_id": self.input_plan["release_id"],
            "version": self.input_plan["version"], "asset_sha256": self.input_plan["asset_sha256"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "override_source_root": "overrides/", "destination_root": "minecraft-root",
            "override_file_count": len(overrides),
            "override_total_bytes": sum(len(value) for value in overrides.values()),
            "optional_selected": [{"project_id": 2, "file_id": 20}],
            "collision_policy": "reject", "installation_state": "not-installed",
            "runtime_qualification_state": "not-qualified",
        }
        self.layout_policy.write_bytes(_canonical(self.policy_value) + b"\n")
        rp = json.loads(self.rp_policy.read_text(encoding="utf-8"))
        rp["input_plan_id"] = self.input_plan["plan_id"]
        rp["manifest_sha256"] = self.input_plan["manifest_sha256"]
        self.rp_policy.write_bytes(_canonical(rp) + b"\n")
        self._mod_lock()

    def _mod_lock(self) -> None:
        body = {
            "format": PLAN_FORMAT_V2, "schema_version": 2,
            "profile": "supersymmetry", "input_plan_id": self.input_plan["plan_id"],
            "release_id": self.input_plan["release_id"],
            "asset_sha256": self.input_plan["asset_sha256"],
            "policy_id": "workbench-pack-release-mod-augmentation-policy:sha256:" + "d" * 64,
            "files": self.mod_files, "retained_file_count": 2,
            "retained_total_bytes": 13, "unresolved": self.mod_result["unresolved"],
        }
        self.mod_plan_id = "workbench-pack-release-mod-augmentation-plan:sha256:" + sha256(
            _canonical(body)).hexdigest()
        self.mod_result["plan_id"] = self.mod_plan_id
        lock = {**body, "format": LOCK_FORMAT_V2, "plan_id": self.mod_plan_id}
        path = (self.mod_state / "pack-release-mod-augmentations"
                / self.mod_plan_id.rsplit(":", 1)[-1] / "snapshot" / "source-lock.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_bytes(_canonical(lock) + b"\n")
        path.chmod(0o600)
        self.mod_lock_path = path

    def _plan(self) -> dict:
        with (patch.object(layout, "load_authority", return_value=type("Authority", (), {
            "minecraft_version": "1.12.2", "mod_loader": "forge-14.23.5.2860"})()),
              patch.object(layout, "reopen_mod_augmentation_v2", return_value=self.mod_result) as mod_reopen,
              patch.object(layout, "reopen_prism_resourcepacks", return_value=self.rp_result) as rp_reopen):
            plan = layout.plan_release_client_layout(
                self.input_plan, archive_path=self.archive_path,
                archive_state_root=self.archive_state, authority_path=self.authority,
                layout_policy_path=self.layout_policy,
                resourcepack_policy_path=self.rp_policy,
                local_input_policy_path=self.local_policy,
                prior_mod_policy_path=self.prior_mod_policy,
                mod_policy_path=self.mod_policy, mod_plan_id=self.mod_plan_id,
                mod_state_root=self.mod_state, mod_config_home=self.mod_config,
                resourcepack_plan_id="workbench-pack-release-prism-resourcepack-plan:sha256:" + "e" * 64,
                resourcepack_state_root=self.rp_state, resourcepack_config_home=self.rp_config,
            )
            mod_reopen.assert_called_once()
            rp_reopen.assert_called_once()
            return plan

    def test_exact_selected_inputs_produce_path_free_blocked_plan(self) -> None:
        plan = self._plan()
        self.assertEqual(plan["state"], "blocked")
        self.assertEqual(plan["file_count"], 4)
        self.assertEqual(plan["override_file_count"], 1)
        self.assertEqual(plan["total_bytes"], 22)
        self.assertEqual(plan["source_catalog_state"], "split-core-catalogs")
        self.assertEqual(plan["blockers"], [
            "cross-catalog-publication-unavailable", "external-file-origin-unverified",
            "runtime-compatibility-unqualified",
        ])
        body = {key: value for key, value in plan.items() if key != "plan_id"}
        self.assertEqual(plan["plan_id"], "workbench-pack-release-client-layout-plan:sha256:"
                         + sha256(_canonical(body)).hexdigest())
        self.assertNotIn(str(self.root), json.dumps(plan))
        self.assertEqual(plan, self._plan())

    def test_override_collision_with_retained_mod_is_rejected(self) -> None:
        self._archive({"mods/Alpha.jar": b"collision"})
        with self.assertRaisesRegex(ValueError, "collide"):
            self._plan()

    def test_corrupt_override_crc_is_rejected_even_with_new_asset_hash(self) -> None:
        self._archive({"config/client.cfg": b"test"}, corrupt=True)
        with self.assertRaisesRegex(ValueError, "CRC"):
            self._plan()

    def test_substituted_source_lock_rows_fail_full_plan_hash(self) -> None:
        lock = json.loads(self.mod_lock_path.read_text(encoding="utf-8"))
        lock["files"][0]["sha256"] = "sha256:" + "f" * 64
        self.mod_lock_path.write_bytes(_canonical(lock) + b"\n")
        with self.assertRaisesRegex(ValueError, "mapping changed"):
            self._plan()

    def test_selected_external_id_must_be_present_once(self) -> None:
        self.rp_result["files"] = []
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            self._plan()

    def test_casefold_and_file_prefix_conflicts_are_rejected(self) -> None:
        self._archive({"config/Client.cfg": b"a", "config/client.cfg": b"b"})
        with self.assertRaisesRegex(ValueError, "collide"):
            self._plan()
        self._archive({"mods": b"blocks directory"})
        with self.assertRaisesRegex(ValueError, "blocks another destination"):
            self._plan()

    def test_windows_unsafe_component_is_rejected_at_any_depth(self) -> None:
        for name in ("config/foo:bar", "CON/readme", "config/aux.txt",
                     "config/trailing.", "config/trailing ", "config/line\x01break"):
            with self.subTest(name=name):
                self._archive({name: b"unsafe"})
                with self.assertRaisesRegex(ValueError, "unsafe relative member path"):
                    self._plan()

    def test_nonempty_override_directory_entry_is_rejected(self) -> None:
        self._archive({"config/": b"hidden", "config/client.cfg": b"ordinary"})
        with self.assertRaisesRegex(ValueError, "nonempty override directory"):
            self._plan()


if __name__ == "__main__":
    unittest.main()
