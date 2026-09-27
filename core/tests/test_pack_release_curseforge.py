"""Bounded fresh acquisition and complete Core tree publication."""

from __future__ import annotations

from hashlib import sha1, sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from workbench_core.pack_release_curseforge import (
    CurseForgeAccessUnavailable, CurseForgeSourceUnavailable,
    acquire_curseforge_file, plan_curseforge_acquisition,
    publish_curseforge_acquisition, reopen_curseforge_acquisition,
    reopen_curseforge_file,
)
from workbench_core.pack_release_local import _canonical


ROOT = Path(__file__).resolve().parents[2]
URL = "https://edge.forgecdn.net/files/1234/567/selected.jar"


class _Download(BytesIO):
    def __init__(self, content: bytes, url: str = URL) -> None:
        super().__init__(content)
        self.url = url

    def geturl(self) -> str:
        return self.url


class PackReleaseCurseForgeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.config = self.root / "config"
        self.config.mkdir(mode=0o700)
        self.content = {
            (10, 100): ("alpha.jar", b"alpha-mod"),
            (20, 200): ("resource.zip", b"resource-pack"),
            (30, 300): ("optional.jar", b"optional-mod"),
        }
        body = {
            "format": "workbench-pack-release-input-plan-v1", "schema_version": 1,
            "profile": "supersymmetry", "source_kind": "published-client-archive",
            "release_id": "profile-release:sha256:" + "2" * 64,
            "version": "0.1.16.16", "asset_sha256": "sha256:" + "3" * 64,
            "asset_size": 100, "manifest_sha256": "sha256:" + "4" * 64,
            "archive_member_count": 2, "override_file_count": 0,
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
        self.policy_path = self.root / "resourcepack-policy.json"
        policy = {
            "format": "workbench-supersymmetry-release-resourcepack-input-policy-v1",
            "schema_version": 1, "profile": "supersymmetry",
            "input_plan_format": "workbench-pack-release-input-plan-v1",
            "input_plan_id": self.input_plan["plan_id"],
            "version": self.input_plan["version"],
            "manifest_sha256": self.input_plan["manifest_sha256"],
            "external_file_count": 3, "allowed_extensions": [".zip"],
            "max_file_bytes": 536870912, "max_total_bytes": 1610612736,
            "placements": [
                {"project_id": 20, "file_id": 200, "required": True,
                 "destination_root": "resourcepacks"},
            ],
        }
        self.policy_path.write_bytes(_canonical(policy) + b"\n")

    def _plan(self, *, include_optional: bool = False) -> dict:
        return plan_curseforge_acquisition(
            self.input_plan, resourcepack_policy_path=self.policy_path,
            optional_selected=((30, 300),) if include_optional else (),
        )

    def _metadata(self, project: int, file: int, key: str) -> dict:
        self.assertEqual("test-Workbench-key", key)
        filename, content = self.content[project, file]
        return {"data": {
            "id": file, "modId": project, "gameId": 432,
            "isAvailable": True, "fileName": filename,
            "fileLength": len(content),
            "hashes": [{"algo": 1, "value": sha1(content).hexdigest()}],
            "downloadUrl": URL,
        }}

    def _acquire(self, plan: dict, project: int, file: int) -> dict:
        return acquire_curseforge_file(
            plan, project_id=project, file_id=file, state_root=self.state,
            provider_key="test-Workbench-key", metadata_fetcher=self._metadata,
            download_opener=lambda _: _Download(self.content[project, file][1]),
        )

    def test_plan_selects_required_and_explicit_optional_files(self) -> None:
        plan = self._plan()
        self.assertEqual((2, 0), (plan["required_count"], plan["optional_count"]))
        self.assertEqual(["mods", "resourcepacks"],
                         [row["destination_root"] for row in plan["files"]])
        selected = self._plan(include_optional=True)
        self.assertEqual((2, 1), (selected["required_count"], selected["optional_count"]))
        with self.assertRaisesRegex(ValueError, "optional selection"):
            plan_curseforge_acquisition(
                self.input_plan, resourcepack_policy_path=self.policy_path,
                optional_selected=((10, 100),),
            )

    def test_missing_provider_access_refuses_without_receipt(self) -> None:
        plan = self._plan()
        with self.assertRaises(CurseForgeAccessUnavailable):
            acquire_curseforge_file(
                plan, project_id=10, file_id=100, state_root=self.state,
            )
        self.assertFalse((self.state / "pack-release-curseforge").exists())

    def test_acquire_reopen_and_publish_exact_tree_without_credential(self) -> None:
        plan = self._plan(include_optional=True)
        for project, file in self.content:
            result = self._acquire(plan, project, file)
            self.assertEqual("downloaded", result["outcome"])
            self.assertEqual("ready", result["status"])
            self.assertNotIn("test-Workbench-key", json.dumps(result))
        reused = acquire_curseforge_file(
            plan, project_id=10, file_id=100, state_root=self.state,
        )
        self.assertEqual("reused", reused["outcome"])
        result = publish_curseforge_acquisition(
            plan, state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"],
        )
        self.assertEqual("published", result["outcome"])
        target = Path(result["tree_path"])
        self.assertEqual(b"alpha-mod", (target / "mods/alpha.jar").read_bytes())
        self.assertEqual(b"optional-mod", (target / "mods/optional.jar").read_bytes())
        self.assertEqual(b"resource-pack", (target / "resourcepacks/resource.zip").read_bytes())
        self.assertEqual(3, result["file_count"])
        self.assertEqual("reused", publish_curseforge_acquisition(
            plan, state_root=self.state, config_home=self.config,
            expected_plan_id=plan["plan_id"],
        )["outcome"])
        for receipt in (self.state / "pack-release-curseforge/receipts").rglob("*.json"):
            self.assertNotIn(b"test-Workbench-key", receipt.read_bytes())
            self.assertNotIn(b"forgecdn.net", receipt.read_bytes())
        self.assertEqual(result["tree_id"], reopen_curseforge_acquisition(
            plan, state_root=self.state, config_home=self.config,
        )["tree_id"])

    def test_incomplete_or_changed_file_refuses_tree_publication(self) -> None:
        plan = self._plan()
        self._acquire(plan, 10, 100)
        with self.assertRaisesRegex(ValueError, "no retained Core receipt"):
            publish_curseforge_acquisition(
                plan, state_root=self.state, config_home=self.config,
                expected_plan_id=plan["plan_id"],
            )
        self.assertFalse(list((self.state / "pack-release-external-inputs").glob("*/snapshot")))
        self._acquire(plan, 20, 200)
        retained = reopen_curseforge_file(
            plan, project_id=10, file_id=100, state_root=self.state,
        )
        Path(retained["artifact_path"]).write_bytes(b"brokenxxx")
        with self.assertRaisesRegex(ValueError, "bytes or custody changed"):
            publish_curseforge_acquisition(
                plan, state_root=self.state, config_home=self.config,
                expected_plan_id=plan["plan_id"],
            )
        self.assertFalse(list((self.state / "pack-release-external-inputs").glob("*/snapshot")))

    def test_unavailable_url_and_wrong_metadata_are_refused(self) -> None:
        plan = self._plan()
        bad = self._metadata(10, 100, "test-Workbench-key")
        bad["data"]["modId"] = 99
        with self.assertRaisesRegex(CurseForgeSourceUnavailable, "differs"):
            acquire_curseforge_file(
                plan, project_id=10, file_id=100, state_root=self.state,
                provider_key="test-Workbench-key", metadata_fetcher=lambda *_: bad,
            )
        empty = self._metadata(10, 100, "test-Workbench-key")
        empty["data"]["downloadUrl"] = None
        with self.assertRaisesRegex(CurseForgeSourceUnavailable, "no authorized download URL"):
            acquire_curseforge_file(
                plan, project_id=10, file_id=100, state_root=self.state,
                provider_key="test-Workbench-key", metadata_fetcher=lambda *_: empty,
                download_url_fetcher=lambda *_: None,
            )
        self.assertFalse(list((self.state / "pack-release-curseforge").rglob("*.json")))

    def test_download_url_endpoint_can_supply_missing_metadata_url(self) -> None:
        plan = self._plan()
        metadata = self._metadata(10, 100, "test-Workbench-key")
        metadata["data"]["downloadUrl"] = None
        result = acquire_curseforge_file(
            plan, project_id=10, file_id=100, state_root=self.state,
            provider_key="test-Workbench-key", metadata_fetcher=lambda *_: metadata,
            download_url_fetcher=lambda *_: URL,
            download_opener=lambda _: _Download(b"alpha-mod"),
        )
        self.assertEqual("downloaded", result["outcome"])

    def test_cancellation_prevents_metadata_request_and_custody(self) -> None:
        plan = self._plan()

        def cancelled() -> None:
            raise InterruptedError("cancelled")

        with self.assertRaisesRegex(InterruptedError, "cancelled"):
            acquire_curseforge_file(
                plan, project_id=10, file_id=100, state_root=self.state,
                provider_key="test-Workbench-key", check_cancelled=cancelled,
                metadata_fetcher=lambda *_: self.fail("metadata must not be requested"),
            )
        self.assertFalse((self.state / "pack-release-curseforge").exists())

    def test_off_cdn_redirect_and_changed_sha1_are_refused(self) -> None:
        plan = self._plan()
        with self.assertRaisesRegex(CurseForgeSourceUnavailable, "admitted CDN"):
            acquire_curseforge_file(
                plan, project_id=10, file_id=100, state_root=self.state,
                provider_key="test-Workbench-key", metadata_fetcher=self._metadata,
                download_opener=lambda _: _Download(b"alpha-mod", "https://example.com/files/bad"),
            )
        with self.assertRaisesRegex(CurseForgeSourceUnavailable, "size or SHA-1"):
            acquire_curseforge_file(
                plan, project_id=10, file_id=100, state_root=self.state,
                provider_key="test-Workbench-key", metadata_fetcher=self._metadata,
                download_opener=lambda _: _Download(b"alpha-bad"),
            )
        self.assertFalse(list((self.state / "pack-release-curseforge").rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
