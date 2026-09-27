"""Complete Prism ZIP admission and immutable Core composition custody."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from workbench_core import pack_release_prism_zip_composition as prism_zip


ROOT = Path(__file__).resolve().parents[2]


class PrismZipCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.state.mkdir(mode=0o700)
        self.config.mkdir(mode=0o700)
        self.archive = self.root / "instance.zip"

    def _members(self, prefix: str = "", game: str = "minecraft") -> dict[str, bytes]:
        return {
            prefix + "instance.cfg": b"[General]\nname=User instance\n",
            prefix + "mmc-pack.json": json.dumps({"formatVersion": 1, "components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "0.6.8-alpha"},
            ]}).encode(),
            prefix + "patches/net.minecraftforge.json": b"{}",
            prefix + game + "/mods/Susy-Core.jar": b"jar-contents",
            prefix + game + "/config/user.cfg": b"custom=true\n",
        }

    def _zip(self, members: dict[str, bytes | ZipInfo]) -> None:
        with ZipFile(self.archive, "w") as archive:
            for name, content in members.items():
                if isinstance(content, ZipInfo):
                    archive.writestr(content, b"target")
                else:
                    archive.writestr(name, content)

    def _plan(self) -> dict:
        return prism_zip.plan_prism_zip_composition(
            self.archive, state_root=self.state, config_home=self.config,
        )

    def _apply(self, plan_id: str) -> dict:
        return prism_zip.apply_prism_zip_composition(
            self.archive, state_root=self.state, config_home=self.config,
            expected_plan_id=plan_id,
        )

    def test_retains_root_zip_and_reopens_without_archive(self) -> None:
        self._zip(self._members())
        plan = self._plan()
        self.assertEqual(plan["action"], "acquire")
        self.assertEqual(plan["source_kind"], "user-prism-zip")
        self.assertEqual({
            "kind": "cleanroom", "component_uid": "net.minecraftforge",
            "component_version": "0.6.8-alpha", "minecraft_version": "1.12.2",
            "classification": "version-pattern",
        }, plan["source_platform"])
        self.assertEqual(plan["file_count"], 2)
        self.assertEqual(plan["source_archive_sha256"],
                         "sha256:" + sha256(self.archive.read_bytes()).hexdigest())
        result = self._apply(plan["plan_id"])
        self.assertEqual(result["outcome"], "retained")
        target = (self.state / "pack-release-client-compositions"
                  / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot")
        self.assertEqual((target / "minecraft-root/mods/Susy-Core.jar").read_bytes(),
                         b"jar-contents")
        self.assertEqual((target / "source-metadata/instance.cfg").read_bytes(),
                         b"[General]\nname=User instance\n")
        self.assertFalse((target / "minecraft-root/mmc-pack.json").exists())
        self.assertEqual(self._plan()["action"], "reuse")
        self.assertEqual(self._apply(plan["plan_id"])["outcome"], "reused")
        self.archive.unlink()
        reopened = prism_zip.reopen_prism_zip_composition(
            state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"],
        )
        self.assertEqual(reopened["outcome"], "reopened")
        self.assertEqual(reopened["tree_content_sha256"], result["tree_content_sha256"])
        self.assertEqual(plan["source_platform"], reopened["source_platform"])

    def test_forge_and_custom_platforms_are_exactly_reported(self) -> None:
        for uid, version, kind in (
            ("net.minecraftforge", "14.23.5.2860", "forge"),
            ("custom.loader", "dev-branch-1", "custom"),
        ):
            with self.subTest(uid=uid):
                members = self._members()
                members["mmc-pack.json"] = json.dumps({"formatVersion": 1,
                    "components": [{"uid": "net.minecraft", "version": "1.12.2"},
                                   {"uid": uid, "version": version}]}).encode()
                self._zip(members)
                plan = self._plan()
                self.assertEqual((kind, uid, version), (
                    plan["source_platform"]["kind"],
                    plan["source_platform"]["component_uid"],
                    plan["source_platform"]["component_version"],
                ))
                imported = self._apply(plan["plan_id"])
                self.assertEqual(plan["source_platform"], imported["source_platform"])

    def test_single_wrapper_and_dot_minecraft(self) -> None:
        members = self._members("Supersymmetry/", ".minecraft")
        members = {"Supersymmetry/": b"", **members}
        members["Supersymmetry/instance.cfg"] += b"ManagedPackVersionName=feature/new-branch\n"
        self._zip(members)
        plan = self._plan()
        self.assertEqual(plan["archive_root"], "Supersymmetry")
        self.assertEqual(plan["minecraft_source_root"], ".minecraft")
        self.assertEqual(plan["source_version"], "feature/new-branch")
        result = self._apply(plan["plan_id"])
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["source_version"], "feature/new-branch")
        self.archive.unlink()
        reopened = prism_zip.reopen_prism_zip_composition(
            state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"],
        )
        self.assertEqual(reopened["source_version"], "feature/new-branch")

    def test_rejects_unsafe_and_incomplete_archives(self) -> None:
        baseline = self._members()
        cases: list[dict[str, bytes | ZipInfo]] = [
            {**baseline, "minecraft/../escape": b"x"},
            {**baseline, "minecraft/mods/susy-core.jar": b"duplicate-case"},
            {**baseline, ".minecraft/mods/Other.jar": b"ambiguous"},
            {key: value for key, value in baseline.items()
             if not key.endswith("Susy-Core.jar")},
            {**self._members("Wrapper/"), "outside.txt": b"x"},
            {**baseline, ".minecraft": b"conflicting launcher metadata"},
            {**baseline, ".workbench-release-install.json": b"forged receipt"},
            {**baseline, "mmc-pack.json": b'{"components":[{"uid":"net.minecraft",'
             b'"version":"1.20.1"}]}'},
            {**baseline, "mmc-pack.json": json.dumps({"components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "14.23.5.2860"},
                {"uid": "net.minecraftforge", "version": "14.23.5.2861"},
            ]}).encode()},
        ]
        symlink = ZipInfo("minecraft/mods/linked.jar")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases.append({**baseline, symlink.filename: symlink})
        for index, members in enumerate(cases):
            with self.subTest(index=index):
                self._zip(members)
                with self.assertRaises(ValueError):
                    self._plan()

    def test_changed_zip_cannot_apply_reviewed_plan(self) -> None:
        members = self._members()
        self._zip(members)
        plan = self._plan()
        self._zip({**members, "minecraft/config/user.cfg": b"changed=true\n"})
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self._apply(plan["plan_id"])

    def test_reopen_detects_payload_drift(self) -> None:
        self._zip(self._members())
        plan = self._plan()
        self._apply(plan["plan_id"])
        target = (self.state / "pack-release-client-compositions"
                  / plan["plan_id"].rsplit(":", 1)[-1] / "snapshot")
        (target / "minecraft-root/mods/Susy-Core.jar").write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "no committed Core tree"):
            prism_zip.reopen_prism_zip_composition(
                state_root=self.state, config_home=self.config,
                expected_plan_id=plan["plan_id"],
            )

    def test_wsl_9p_source_requires_readonly_mount(self) -> None:
        self._zip(self._members())
        with patch.object(prism_zip, "_mount_type", side_effect=lambda path:
                          "9p" if path == self.archive.parent else "ext4"), patch.object(
                              prism_zip.os, "statvfs", return_value=SimpleNamespace(f_flag=0)):
            with self.assertRaisesRegex(ValueError, "read-only WSL 9p"):
                self._plan()
        with patch.object(prism_zip, "_mount_type", side_effect=lambda path:
                          "9p" if path == self.archive.parent else "ext4"), patch.object(
                              prism_zip.os, "statvfs",
                              return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            self.assertEqual(self._plan()["action"], "acquire")


if __name__ == "__main__":
    unittest.main()
