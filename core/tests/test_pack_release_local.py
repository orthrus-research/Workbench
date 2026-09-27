"""Local file review never upgrades manifest IDs into verified source identity."""

from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from jsonschema import validate as validate_schema

from workbench_core.artifact_store import fetch_verified_artifact
from workbench_core.cli import _dispatch
from workbench_core.pack_release import PackReleaseService, load_authority


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "profiles/packs/supersymmetry/release-authority-v1.json"
POLICY = ROOT / "profiles/packs/supersymmetry/runtime/release-local-input-policy-v1.json"
SCHEMA = ROOT / "core/src/workbench_core/schemas/workbench-pack-release-local-input-plan-v1.schema.json"
SOURCE_SCHEMA = ROOT / "core/src/workbench_core/schemas/workbench-pack-release-local-sources-v1.schema.json"


def _client_zip(path: Path, *, override_mod: str | None = None) -> None:
    manifest = {
        "manifestType": "minecraftModpack", "manifestVersion": 1,
        "version": "0.1.16.17", "name": "Supersymmetry", "overrides": "overrides",
        "minecraft": {"version": "1.12.2", "modLoaders": [
            {"id": "forge-14.23.5.2860", "primary": True},
        ]},
        "files": [
            {"projectID": 10, "fileID": 100, "required": True},
            {"projectID": 20, "fileID": 200, "required": True},
            {"projectID": 30, "fileID": 300, "required": False},
        ],
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("overrides/", b"")
        if override_mod:
            archive.writestr("overrides/mods/" + override_mod, b"override")


class PackReleaseLocalTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".workbench"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "supersymmetry-0.1.16.17.zip"
        _client_zip(self.archive)
        self.sources = self.root / "local-sources.json"
        self.service = PackReleaseService(
            load_authority(AUTHORITY), config_home=self.root / "config",
            state_root=self.root / "state", latest_fetcher=self._latest,
            artifact_fetcher=self._fetch,
        )
        selected = self.service.check()["candidate"]
        self.assertEqual("accepted", self.service.accept(selected["release_id"])["status"])
        self.input_plan = self.service.inputs()["input_plan"]

    def _latest(self, _url: str) -> dict:
        name = self.archive.name
        return {
            "id": 17, "tag_name": "0.1.16.17",
            "published_at": "2026-09-26T12:00:00Z", "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/SymmetricDevs/Supersymmetry/releases/tag/0.1.16.17",
            "assets": [{"id": 170, "name": name, "state": "uploaded",
                        "size": self.archive.stat().st_size,
                        "digest": "sha256:" + sha256(self.archive.read_bytes()).hexdigest(),
                        "browser_download_url": "https://github.com/SymmetricDevs/Supersymmetry/releases/download/0.1.16.17/" + name}],
        }

    def _fetch(self, **kwargs):
        kwargs.pop("url")
        return fetch_verified_artifact(url=self.archive.as_uri(), **kwargs)

    def _write_sources(self, rows: list[dict], optional: list[dict] | None = None,
                       *, plan_id: str | None = None) -> bytes:
        value = {
            "format": "workbench-pack-release-local-sources-v1",
            "schema_version": 1,
            "input_plan_id": plan_id or self.input_plan["plan_id"],
            "optional_selected": optional or [], "sources": rows,
        }
        validate_schema(value, json.loads(SOURCE_SCHEMA.read_text(encoding="utf-8")))
        raw = json.dumps(value).encode("utf-8")
        self.sources.write_bytes(raw)
        self.sources.chmod(0o600)
        return raw

    def _row(self, project: int, file: int, name: str, contents: bytes) -> dict:
        path = self.root / name
        path.write_bytes(contents)
        return {"project_id": project, "file_id": file,
                "local_path": str(path), "filename": name,
                "size": len(contents), "sha256": "sha256:" + sha256(contents).hexdigest()}

    def _review(self) -> dict:
        return self.service.local_inputs(self.sources, POLICY)

    def test_empty_local_document_reports_required_and_optional_gaps_without_writes(self) -> None:
        source_before = self._write_sources([])
        choice_before = self.service.choice_path.read_bytes()
        observed = self._review()
        self.assertEqual("planned", observed["status"])
        plan = observed["local_input_plan"]
        validate_schema(plan, json.loads(SCHEMA.read_text(encoding="utf-8")))
        self.assertEqual((0, 2, 0, 1),
                         (plan["local_bytes_verified"], plan["required_unresolved"],
                          plan["optional_selected_unresolved"], plan["optional_unselected"]))
        self.assertEqual("unproven-by-local-hash", plan["curseforge_file_identity_state"])
        self.assertEqual("not-acquired", plan["acquisition_state"])
        self.assertEqual("not-installed", plan["installation_state"])
        self.assertEqual(source_before, self.sources.read_bytes())
        self.assertEqual(choice_before, self.service.choice_path.read_bytes())

    def test_held_local_bytes_make_path_free_review_not_identity_proof(self) -> None:
        rows = [self._row(10, 100, "Alpha.jar", b"alpha"),
                self._row(20, 200, "Beta.jar", b"beta"),
                self._row(30, 300, "Gamma.zip", b"gamma")]
        self._write_sources(rows, [{"project_id": 30, "file_id": 300}])
        observed = self._review()
        self.assertEqual("planned", observed["status"])
        plan = observed["local_input_plan"]
        validate_schema(plan, json.loads(SCHEMA.read_text(encoding="utf-8")))
        self.assertEqual("local-byte-set-reviewed", plan["completeness_state"])
        self.assertEqual(3, plan["local_bytes_verified"])
        self.assertEqual("selected", plan["files"][2]["selection"])
        self.assertEqual("unproven-by-local-hash", plan["curseforge_file_identity_state"])
        self.assertEqual("not-acquired", plan["acquisition_state"])
        self.assertEqual("not-installed", plan["installation_state"])
        self.assertNotIn(str(self.root), json.dumps(plan))
        self.assertEqual(plan, self._review()["local_input_plan"])

    def test_selected_optional_can_remain_explicitly_unresolved(self) -> None:
        self._write_sources([], [{"project_id": 30, "file_id": 300}])
        plan = self._review()["local_input_plan"]
        self.assertEqual(1, plan["optional_selected_unresolved"])
        self.assertEqual("optional-selected-unresolved", plan["files"][2]["local_byte_state"])

    def test_changed_bytes_and_reassigned_ids_refuse_without_choice_change(self) -> None:
        row = self._row(10, 100, "Alpha.jar", b"alpha")
        self._write_sources([row])
        choice_before = self.service.choice_path.read_bytes()
        (self.root / "Alpha.jar").write_bytes(b"altered")
        self.assertEqual("unavailable", self._review()["status"])
        self.assertEqual(choice_before, self.service.choice_path.read_bytes())
        self._write_sources([row], plan_id="workbench-pack-release-input-plan:sha256:" + "0" * 64)
        self.assertEqual("unavailable", self._review()["status"])

    def test_unselected_optional_and_duplicate_destination_refuse(self) -> None:
        optional = self._row(30, 300, "Gamma.jar", b"gamma")
        self._write_sources([optional])
        self.assertEqual("unavailable", self._review()["status"])
        first = self._row(10, 100, "Alpha.jar", b"first")
        second = self._row(20, 200, "alpha.JAR", b"second")
        self._write_sources([first, second])
        self.assertIn("collide", self._review()["reason"])

    def test_rejects_symlink_hardlink_and_parent_redirect(self) -> None:
        row = self._row(10, 100, "Alpha.jar", b"alpha")
        self._write_sources([row])
        path = self.root / "Alpha.jar"
        path.rename(self.root / "real.jar")
        path.symlink_to(self.root / "real.jar")
        self.assertEqual("unavailable", self._review()["status"])
        path.unlink()
        os.link(self.root / "real.jar", path)
        self.assertEqual("unavailable", self._review()["status"])
        path.unlink()
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "Alpha.jar").write_bytes(b"alpha")
        (self.root / "alias").symlink_to(nested, target_is_directory=True)
        row["local_path"] = str(self.root / "alias" / "Alpha.jar")
        self._write_sources([row])
        self.assertEqual("unavailable", self._review()["status"])

    def test_rejects_same_observed_file_identity_for_distinct_manifest_ids(self) -> None:
        first = self._row(10, 100, "Alpha.jar", b"alpha")
        second = self._row(20, 200, "Beta.jar", b"beta")
        self._write_sources([first, second])
        with patch("workbench_core.pack_release_local._hash_held",
                   side_effect=[(1, 42), (1, 42)]):
            observed = self._review()
        self.assertEqual("unavailable", observed["status"])
        self.assertIn("multiple external IDs", observed["reason"])

    def test_private_source_document_and_qualified_linux_filesystem_are_required(self) -> None:
        row = self._row(10, 100, "Alpha.jar", b"alpha")
        self._write_sources([row])
        self.sources.chmod(0o644)
        self.assertEqual("unavailable", self._review()["status"])
        self.sources.chmod(0o600)
        with patch("workbench_core.pack_release_local._mount_type", return_value="9p"):
            observed = self._review()
        self.assertEqual("unavailable", observed["status"])
        self.assertIn("qualified Linux/WSL filesystem", observed["reason"])

    def test_rejects_archive_override_name_and_unsafe_destination(self) -> None:
        row = self._row(10, 100, "Alpha.jar", b"alpha")
        self._write_sources([row])
        _client_zip(self.archive, override_mod="Alpha.jar")
        # Reprepare the changed release in a fresh service with matching metadata.
        other = PackReleaseService(
            load_authority(AUTHORITY), config_home=self.root / "other-config",
            state_root=self.root / "other-state", latest_fetcher=self._latest,
            artifact_fetcher=self._fetch,
        )
        selected = other.check()["candidate"]
        self.assertEqual("accepted", other.accept(selected["release_id"])["status"])
        other_plan = other.inputs()["input_plan"]
        self._write_sources([row], plan_id=other_plan["plan_id"])
        self.assertIn("override", other.local_inputs(self.sources, POLICY)["reason"])
        row["filename"] = "../Alpha.jar"
        self._write_sources([row], plan_id=other_plan["plan_id"])
        self.assertEqual("unavailable", other.local_inputs(self.sources, POLICY)["status"])

    def test_core_dispatch_discovers_profile_policy_and_hides_local_paths(self) -> None:
        row = self._row(10, 100, "Alpha.jar", b"alpha")
        self._write_sources([row])
        output = StringIO()
        def resource(kind: str) -> dict[str, Path]:
            return {"supersymmetry": POLICY if kind == "release-local-input-policy" else AUTHORITY}
        with (patch("workbench_core.pack_release.profile_resources", side_effect=resource),
              patch("workbench_core.pack_release.default_user_config_home", return_value=self.root / "config"),
              patch("workbench_core.pack_release.default_runtime_state_root", return_value=self.root / "state"),
              redirect_stdout(output)):
            self.assertEqual(0, _dispatch(["pack", "release", "local-inputs", "--profile",
                                           "supersymmetry", "--sources", str(self.sources), "--json"], ROOT))
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertEqual(1, json.loads(output.getvalue())["local_input_plan"]["local_bytes_verified"])


if __name__ == "__main__":
    unittest.main()
