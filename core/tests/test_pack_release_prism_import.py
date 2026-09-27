"""Prism sidecars are local byte hints, not released file-ID provenance."""

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

from workbench_core.pack_release_prism_import import (
    apply_prism_import, plan_prism_import, reopen_prism_import,
    review_prism_import,
)
from workbench_core.pack_release import PackReleaseService, load_authority
from workbench_core.cli import _dispatch


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "profiles/packs/supersymmetry/runtime/release-local-input-policy-v1.json"
AUTHORITY = ROOT / "profiles/packs/supersymmetry/release-authority-v1.json"


class PackReleasePrismImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mods = self.root / "prism/minecraft/mods"
        (self.mods / ".index").mkdir(parents=True)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config = self.root / "config"
        self.config.mkdir(mode=0o700)
        self.archive = self.root / "release.zip"
        with ZipFile(self.archive, "w") as output:
            output.writestr("overrides/", b"")
            output.writestr("manifest.json", b"{}")
        self.input_plan = {
            "format": "workbench-pack-release-input-plan-v1",
            "plan_id": "workbench-pack-release-input-plan:sha256:" + "1" * 64,
            "release_id": "profile-release:sha256:" + "2" * 64,
            "asset_sha256": "sha256:" + sha256(self.archive.read_bytes()).hexdigest(),
            "asset_size": self.archive.stat().st_size,
            "external_files": [
                {"project_id": 10, "file_id": 100, "required": True},
                {"project_id": 20, "file_id": 200, "required": True},
                {"project_id": 30, "file_id": 300, "required": False},
            ],
        }
        self._sidecar(10, 100, "Alpha.jar", b"alpha")
        self._sidecar(30, 300, "Gamma.jar", b"gamma")

    def _sidecar(self, project: int, file: int, filename: str, raw: bytes) -> None:
        (self.mods / filename).write_bytes(raw)
        (self.mods / ".index" / f"{project}.pw.toml").write_text(
            f'filename = "{filename}"\n'
            '[download]\nmode = "metadata:curseforge"\n'
            f'hash = "{sha1(raw).hexdigest()}"\nhash-format = "sha1"\n'
            '[update.curseforge]\n'
            f'project-id = {project}\nfile-id = {file}\n', encoding="utf-8",
        )

    def _plan(self, *, optional=()):
        return plan_prism_import(
            self.input_plan, source_root=self.mods, archive_path=self.archive,
            policy_path=POLICY, state_root=self.state, config_home=self.config,
            optional_selected=optional,
        )

    def _apply(self, plan, *, optional=()):
        return apply_prism_import(
            self.input_plan, source_root=self.mods, archive_path=self.archive,
            policy_path=POLICY, state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"], optional_selected=optional,
        )

    def test_partial_import_reopens_exact_tree_and_reuses_without_install(self) -> None:
        optional = ((30, 300),)
        plan = self._plan(optional=optional)
        self.assertEqual("acquire", plan["action"])
        self.assertEqual(2, plan["retained_file_count"])
        self.assertEqual([{"project_id": 20, "file_id": 200, "required": True}], plan["unresolved"])
        self.assertEqual("unproven-by-local-sidecar", plan["curseforge_file_identity_state"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        result = self._apply(plan, optional=optional)
        self.assertEqual("imported", result["outcome"])
        self.assertEqual("not-installed", result["installation_state"])
        reuse = self._plan(optional=optional)
        self.assertEqual("reuse", reuse["action"])
        self.assertEqual(result["tree_id"], self._apply(reuse, optional=optional)["tree_id"])
        target = next((self.state / "pack-release-mod-inputs").glob("*/snapshot"))
        self.assertEqual(b"alpha", (target / "Alpha.jar").read_bytes())
        self.assertEqual(b"gamma", (target / "Gamma.jar").read_bytes())
        self.assertEqual("workbench-pack-release-prism-source-lock-v1",
                         json.loads((target / "source-lock.json").read_bytes())["format"])

    def test_changed_sidecar_or_linked_file_refuses_before_stage(self) -> None:
        plan = self._plan()
        (self.mods / "Alpha.jar").write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "SHA-1"):
            self._plan()
        with self.assertRaisesRegex(ValueError, "SHA-1"):
            self._apply(plan)
        (self.mods / "Alpha.jar").write_bytes(b"alpha")
        os.link(self.mods / "Alpha.jar", self.mods / "alias.jar")
        with self.assertRaisesRegex(ValueError, "ordinary"):
            self._plan()

    def test_incomplete_stage_is_retained_and_refuses_reuse(self) -> None:
        plan = self._plan()
        with patch("workbench_core.pack_release_prism_import._copy_to_stage",
                   side_effect=RuntimeError("synthetic interrupted copy")):
            with self.assertRaisesRegex(RuntimeError, "interrupted copy"):
                self._apply(plan)
        with self.assertRaisesRegex(ValueError, "incomplete stage"):
            self._plan()
        self.assertTrue(list((self.state / "pack-release-mod-inputs").glob("**/.workbench-tree-*.pending")))

    def test_unselected_optional_is_not_imported(self) -> None:
        plan = self._plan()
        self.assertEqual(1, plan["retained_file_count"])
        self.assertEqual([{"project_id": 30, "file_id": 300}], plan["optional_unselected"])
        result = self._apply(plan)
        self.assertEqual(1, result["retained_file_count"])

    def test_retained_tree_reopens_after_prism_source_disappears(self) -> None:
        plan = self._plan()
        imported = self._apply(plan)
        self.mods.rename(self.root / "former-mods")
        reopened = reopen_prism_import(
            self.input_plan, expected_plan_id=plan["plan_id"],
            policy_path=POLICY, state_root=self.state, config_home=self.config,
        )
        self.assertEqual("reopened", reopened["outcome"])
        self.assertEqual(imported["tree_content_sha256"], reopened["tree_content_sha256"])
        self.assertEqual("unproven-by-local-sidecar", reopened["curseforge_file_identity_state"])
        self.assertNotIn(str(self.root), json.dumps(reopened))

    def test_retained_tree_rejects_changed_bytes_after_source_disappears(self) -> None:
        plan = self._plan()
        self._apply(plan)
        self.mods.rename(self.root / "former-mods")
        target = next((self.state / "pack-release-mod-inputs").glob("*/snapshot"))
        (target / "Alpha.jar").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            reopen_prism_import(
                self.input_plan, expected_plan_id=plan["plan_id"],
                policy_path=POLICY, state_root=self.state, config_home=self.config,
            )

    def test_source_sidecar_change_during_copy_preserves_incomplete_stage(self) -> None:
        from workbench_core import pack_release_prism_import as importer
        plan = self._plan()
        original = importer._measure_file
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("destination_fd") is not None:
                sidecar = self.mods / ".index/10.pw.toml"
                sidecar.write_bytes(sidecar.read_bytes() + b"\n# changed during copy\n")
            return result
        with patch.object(importer, "_measure_file", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "sidecar changed during import"):
                self._apply(plan)
        self.assertTrue(list((self.state / "pack-release-mod-inputs").glob("**/.workbench-tree-*.pending")))

    def test_readonly_wsl_9p_is_marked_unqualified_and_writable_9p_refuses(self) -> None:
        class ReadOnly:
            f_flag = os.ST_RDONLY
        class Writable:
            f_flag = 0
        with (patch("workbench_core.pack_release_prism_import._mount_type", return_value="9p"),
              patch("workbench_core.pack_release_prism_import.os.statvfs", return_value=ReadOnly())):
            plan = review_prism_import(
                self.input_plan, source_root=self.mods,
                archive_path=self.archive, policy_path=POLICY,
            )
        self.assertEqual("unqualified-readonly-wsl-9p", plan["source_filesystem_state"])
        with (patch("workbench_core.pack_release_prism_import._mount_type", return_value="9p"),
              patch("workbench_core.pack_release_prism_import.os.statvfs", return_value=Writable())):
            with self.assertRaisesRegex(ValueError, "read-only WSL 9p"):
                review_prism_import(
                    self.input_plan, source_root=self.mods,
                    archive_path=self.archive, policy_path=POLICY,
                )

    def test_cli_dispatch_plan_import_and_source_independent_reopen(self) -> None:
        service = PackReleaseService(
            load_authority(AUTHORITY), config_home=self.config, state_root=self.state,
        )
        prepared = {"status": "planned", "reason": None, "input_plan": self.input_plan,
                    "selected": {"artifact_path": str(self.archive)}}
        def resource(kind: str) -> dict[str, Path]:
            return {"supersymmetry": POLICY if kind == "release-local-input-policy" else AUTHORITY}
        def dispatch(*args: str) -> tuple[int, dict]:
            output = StringIO()
            with redirect_stdout(output):
                code = _dispatch(["pack", "release", *args, "--profile", "supersymmetry", "--json"], ROOT)
            self.assertNotIn(str(self.root), output.getvalue())
            return code, json.loads(output.getvalue())
        with (patch("workbench_core.pack_release.profile_resources", side_effect=resource),
              patch("workbench_core.pack_release.PackReleaseService", return_value=service),
              patch.object(service, "inputs", return_value=prepared)):
            code, review = dispatch("prism-inputs", "--mods-root", str(self.mods),
                                    "--include-optional", "30:300")
            self.assertEqual(0, code)
            self.assertEqual("planned", review["status"])
            plan_id = review["prism_import_plan"]["plan_id"]
            code, imported = dispatch("prism-import", "--mods-root", str(self.mods),
                                      "--include-optional", "30:300",
                                      "--expected-plan-id", plan_id)
            self.assertEqual(0, code)
            self.assertEqual("retained", imported["status"])
            self.mods.rename(self.root / "former-mods")
            code, reopened = dispatch("prism-reopen", "--expected-plan-id", plan_id)
            self.assertEqual(0, code)
            self.assertEqual("reopened", reopened["status"])
            self.assertEqual(imported["prism_import_result"]["tree_id"],
                             reopened["prism_import_result"]["tree_id"])
            code, missing = dispatch("prism-inputs", "--mods-root", str(self.mods))
            self.assertEqual(2, code)
            self.assertEqual("unavailable", missing["status"])


if __name__ == "__main__":
    unittest.main()
