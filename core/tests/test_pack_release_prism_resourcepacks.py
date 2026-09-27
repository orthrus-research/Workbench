"""Resource-pack placement and custody remain separate from mod candidates."""

from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha1, sha256
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core.cli import _dispatch
from workbench_core.pack_release import PackReleaseService, load_authority
from workbench_core.pack_release_local import _canonical
from workbench_core.pack_release_prism_import import review_prism_import
from workbench_core.pack_release_prism_resourcepacks import (
    apply_prism_resourcepacks, load_resourcepack_policy, plan_prism_resourcepacks,
    reopen_prism_resourcepacks, review_prism_resourcepacks,
)


ROOT = Path(__file__).resolve().parents[2]
MOD_POLICY = ROOT / "profiles/packs/supersymmetry/runtime/release-local-input-policy-v1.json"
AUTHORITY = ROOT / "profiles/packs/supersymmetry/release-authority-v1.json"


class PackReleasePrismResourcepacksTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.resourcepacks = self.root / "prism/minecraft/resourcepacks"
        self.resourcepacks.mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config = self.root / "config"
        self.config.mkdir(mode=0o700)
        self.archive = self.root / "release.zip"
        with ZipFile(self.archive, "w") as output:
            output.writestr("overrides/", b"")
            output.writestr("manifest.json", b"{}")
        self.pairs = [(10, 100), (20, 200), (30, 300)]
        body = {
            "format": "workbench-pack-release-input-plan-v1", "schema_version": 1,
            "profile": "supersymmetry", "source_kind": "published-client-archive",
            "release_id": "profile-release:sha256:" + "2" * 64,
            "version": "0.1.16.16",
            "asset_sha256": "sha256:" + sha256(self.archive.read_bytes()).hexdigest(),
            "asset_size": self.archive.stat().st_size,
            "manifest_sha256": "sha256:" + "3" * 64,
            "archive_member_count": 2, "override_file_count": 0, "other_file_count": 0,
            "external_files": [
                {"project_id": project, "file_id": file, "required": True}
                for project, file in self.pairs
            ] + [{"project_id": 40, "file_id": 400, "required": True}],
            "acquisition_state": "external-file-bytes-unresolved",
        }
        self.input_plan = {**body, "plan_id": "workbench-pack-release-input-plan:sha256:"
                           + sha256(_canonical(body)).hexdigest()}
        self.policy = self.root / "policy.json"
        self.policy_value = {
            "format": "workbench-supersymmetry-release-resourcepack-input-policy-v1",
            "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"],
            "version": "0.1.16.16",
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "external_file_count": 4, "allowed_extensions": [".zip"],
            "max_file_bytes": 536870912, "max_total_bytes": 1610612736,
            "placements": [
                {"project_id": project, "file_id": file, "required": True,
                 "destination_root": "resourcepacks"}
                for project, file in self.pairs
            ],
        }
        self._write_policy()
        self._sidecar(10, 100, "Alpha.zip", b"alpha")
        self._sidecar(20, 200, "Beta.zip", b"beta")
        self._sidecar(30, 300, "Gamma.zip", b"gamma")

    def _write_policy(self) -> None:
        self.policy.write_bytes(_canonical(self.policy_value) + b"\n")

    def _sidecar(self, project: int, file: int, filename: str, raw: bytes) -> None:
        (self.resourcepacks / filename).write_bytes(raw)
        (self.resourcepacks / f"{project}.pw.toml").write_text(
            f'filename = "{filename}"\n'
            '[download]\nmode = "metadata:curseforge"\n'
            f'hash = "{sha1(raw).hexdigest()}"\nhash-format = "sha1"\n'
            '[update.curseforge]\n'
            f'project-id = {project}\nfile-id = {file}\n', encoding="utf-8",
        )

    def _plan(self):
        return plan_prism_resourcepacks(
            self.input_plan, source_root=self.resourcepacks,
            archive_path=self.archive, policy_path=self.policy,
            state_root=self.state, config_home=self.config,
        )

    def _apply(self, plan):
        return apply_prism_resourcepacks(
            self.input_plan, source_root=self.resourcepacks,
            archive_path=self.archive, policy_path=self.policy,
            state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"],
        )

    def _reopen(self, plan):
        return reopen_prism_resourcepacks(
            self.input_plan, expected_plan_id=plan["plan_id"],
            policy_path=self.policy, state_root=self.state,
            config_home=self.config,
        )

    def test_exact_typed_tree_reopens_and_reuses_without_source_or_install(self) -> None:
        plan = self._plan()
        self.assertEqual("acquire", plan["action"])
        self.assertEqual("resourcepacks", plan["destination_root"])
        self.assertEqual(3, plan["retained_file_count"])
        self.assertEqual([], plan["unresolved"])
        self.assertEqual("unproven-by-local-sidecar", plan["curseforge_file_identity_state"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        for row in plan["files"]:
            self.assertEqual("resourcepacks", row["destination_root"])
            self.assertEqual("resourcepacks/" + row["filename"], row["relative_path"])
            self.assertEqual("sha256:" + sha256((self.resourcepacks / row["filename"]).read_bytes()).hexdigest(),
                             row["sha256"])
            self.assertEqual((self.resourcepacks / row["filename"]).stat().st_size, row["size"])
        result = self._apply(plan)
        self.assertEqual("imported", result["outcome"])
        self.assertEqual("not-installed", result["installation_state"])
        target = next((self.state / "pack-release-resourcepack-inputs").glob("*/snapshot"))
        self.assertEqual({"resourcepacks", "source-lock.json"}, {p.name for p in target.iterdir()})
        self.assertEqual({"Alpha.zip", "Beta.zip", "Gamma.zip"},
                         {p.name for p in (target / "resourcepacks").iterdir()})
        self.assertFalse((target / "mods").exists())
        self.assertEqual("reuse", self._plan()["action"])
        self.assertEqual("reused", self._apply(self._plan())["outcome"])
        self.resourcepacks.rename(self.root / "former-resourcepacks")
        reopened = self._reopen(plan)
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(result["tree_id"], reopened["tree_id"])
        self.assertEqual(result["tree_content_sha256"], reopened["tree_content_sha256"])
        self.assertEqual(result["files"], reopened["files"])
        self.assertNotIn(str(self.root), json.dumps(reopened))

    def test_partial_import_retains_missing_required_resourcepack(self) -> None:
        (self.resourcepacks / "Beta.zip").unlink()
        plan = self._plan()
        self.assertEqual(2, plan["retained_file_count"])
        self.assertEqual([{"project_id": 20, "file_id": 200, "required": True}], plan["unresolved"])
        self.assertEqual("imported", self._apply(plan)["outcome"])

    def test_mod_route_refuses_resourcepack_source_and_wrong_resourcepack_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "instance mods directory"):
            review_prism_import(
                self.input_plan, source_root=self.resourcepacks,
                archive_path=self.archive, policy_path=MOD_POLICY,
            )
        other = self.root / "wrong-name"
        self.resourcepacks.rename(other)
        with self.assertRaisesRegex(ValueError, "instance resourcepacks directory"):
            review_prism_resourcepacks(
                self.input_plan, source_root=other,
                archive_path=self.archive, policy_path=self.policy,
            )

    def test_changed_sidecar_or_file_and_links_refuse(self) -> None:
        plan = self._plan()
        (self.resourcepacks / "Alpha.zip").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-1"):
            self._apply(plan)
        (self.resourcepacks / "Alpha.zip").write_bytes(b"alpha")
        (self.resourcepacks / "10.pw.toml").write_bytes(
            (self.resourcepacks / "10.pw.toml").read_bytes() + b"\n# changed\n",
        )
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self._apply(plan)
        (self.resourcepacks / "10.pw.toml").write_bytes(
            (self.resourcepacks / "10.pw.toml").read_bytes().split(b"\n# changed")[0],
        )
        os.link(self.resourcepacks / "Alpha.zip", self.resourcepacks / "alias.zip")
        with self.assertRaisesRegex(ValueError, "ordinary"):
            self._plan()

    def test_wrong_release_or_policy_refuses(self) -> None:
        wrong = {**self.input_plan, "version": "0.1.16.17"}
        with self.assertRaisesRegex(ValueError, "another selected release"):
            review_prism_resourcepacks(
                wrong, source_root=self.resourcepacks,
                archive_path=self.archive, policy_path=self.policy,
            )
        self.policy_value["placements"][0]["destination_root"] = "mods"
        self._write_policy()
        with self.assertRaisesRegex(ValueError, "placement is invalid"):
            load_resourcepack_policy(self.policy)

    def test_readonly_9p_is_unqualified_and_writable_9p_refuses(self) -> None:
        class ReadOnly:
            f_flag = os.ST_RDONLY
        class Writable:
            f_flag = 0
        with (patch("workbench_core.pack_release_prism_import._mount_type", return_value="9p"),
              patch("workbench_core.pack_release_prism_import.os.statvfs", return_value=ReadOnly())):
            plan = review_prism_resourcepacks(
                self.input_plan, source_root=self.resourcepacks,
                archive_path=self.archive, policy_path=self.policy,
            )
        self.assertEqual("unqualified-readonly-wsl-9p", plan["source_filesystem_state"])
        with (patch("workbench_core.pack_release_prism_import._mount_type", return_value="9p"),
              patch("workbench_core.pack_release_prism_import.os.statvfs", return_value=Writable())):
            with self.assertRaisesRegex(ValueError, "read-only WSL 9p"):
                review_prism_resourcepacks(
                    self.input_plan, source_root=self.resourcepacks,
                    archive_path=self.archive, policy_path=self.policy,
                )

    def test_interrupted_stage_refuses_reuse(self) -> None:
        plan = self._plan()
        with patch("workbench_core.pack_release_prism_resourcepacks._copy_to_stage",
                   side_effect=RuntimeError("interrupted copy")):
            with self.assertRaisesRegex(RuntimeError, "interrupted copy"):
                self._apply(plan)
        with self.assertRaisesRegex(ValueError, "incomplete stage"):
            self._plan()

    def test_reopen_refuses_changed_retained_bytes_without_prism_source(self) -> None:
        plan = self._plan()
        self._apply(plan)
        self.resourcepacks.rename(self.root / "former-resourcepacks")
        target = next((self.state / "pack-release-resourcepack-inputs").glob("*/snapshot"))
        (target / "resourcepacks/Alpha.zip").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self._reopen(plan)

    def test_cli_plan_import_reopen(self) -> None:
        service = PackReleaseService(
            load_authority(AUTHORITY), config_home=self.config, state_root=self.state,
        )
        prepared = {"status": "planned", "reason": None, "input_plan": self.input_plan,
                    "selected": {"artifact_path": str(self.archive)}}
        def resource(kind: str) -> dict[str, Path]:
            return {"supersymmetry": self.policy if kind == "release-resourcepack-input-policy" else AUTHORITY}
        def dispatch(*args: str) -> tuple[int, dict]:
            output = StringIO()
            with redirect_stdout(output):
                code = _dispatch(["pack", "release", *args, "--profile", "supersymmetry", "--json"], ROOT)
            self.assertNotIn(str(self.root), output.getvalue())
            return code, json.loads(output.getvalue())
        with (patch("workbench_core.pack_release.profile_resources", side_effect=resource),
              patch("workbench_core.pack_release.PackReleaseService", return_value=service),
              patch.object(service, "inputs", return_value=prepared)):
            code, review = dispatch("prism-resourcepack-inputs", "--resourcepacks-root", str(self.resourcepacks))
            self.assertEqual(0, code)
            self.assertEqual("planned", review["status"])
            plan_id = review["resourcepack_plan"]["plan_id"]
            code, imported = dispatch("prism-resourcepack-import", "--resourcepacks-root", str(self.resourcepacks),
                                      "--expected-plan-id", plan_id)
            self.assertEqual(0, code)
            self.assertEqual("retained", imported["status"])
            self.resourcepacks.rename(self.root / "former-resourcepacks")
            code, reopened = dispatch("prism-resourcepack-reopen", "--expected-plan-id", plan_id)
            self.assertEqual(0, code)
            self.assertEqual("reopened", reopened["status"])
            self.assertEqual(imported["resourcepack_result"]["tree_id"],
                             reopened["resourcepack_result"]["tree_id"])


if __name__ == "__main__":
    unittest.main()
