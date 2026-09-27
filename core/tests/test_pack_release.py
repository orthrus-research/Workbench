"""Release choice is durable, version-scoped, and gated by exact client bytes."""

from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core.artifact_store import fetch_verified_artifact
from workbench_core.cli import _dispatch
from workbench_core.pack_release import PackReleaseService, ReleaseUnavailable, fetch_latest_release, load_authority, main
from workbench_api.profiles import Profile


AUTHORITY_PATH = (Path(__file__).resolve().parents[2]
                  / "profiles/packs/supersymmetry/release-authority-v1.json")


def _client_zip(path: Path, version: str, *, loader: str = "forge-14.23.5.2860") -> None:
    manifest = {
        "manifestType": "minecraftModpack", "manifestVersion": 1,
        "version": version, "name": "Supersymmetry", "files": [],
        "minecraft": {"version": "1.12.2", "modLoaders": [
            {"id": loader, "primary": True},
        ]},
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("overrides/", b"")


def _release(version: str, asset: Path, *, release_number: int = 17,
             digest: str | None = None) -> dict:
    name = f"supersymmetry-{version}.zip"
    return {
        "id": release_number, "tag_name": version,
        "published_at": "2026-09-26T12:00:00Z", "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/SymmetricDevs/Supersymmetry/releases/tag/{version}",
        "assets": [{
            "id": release_number * 10, "name": name, "state": "uploaded",
            "size": asset.stat().st_size,
            "digest": digest or "sha256:" + sha256(asset.read_bytes()).hexdigest(),
            "browser_download_url": (
                f"https://github.com/SymmetricDevs/Supersymmetry/releases/download/{version}/{name}"
            ),
        }],
    }


class PackReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.authority = load_authority(AUTHORITY_PATH)
        self.source = self.root / "client.zip"
        _client_zip(self.source, "0.1.16.17")
        self.latest = _release("0.1.16.17", self.source)
        self.offline = False
        self.downloads = 0
        self.service = PackReleaseService(
            self.authority, config_home=self.root / ".workbench",
            state_root=self.root / "state", latest_fetcher=self._latest,
            artifact_fetcher=self._download,
        )

    def _latest(self, url: str) -> dict:
        self.assertEqual(self.authority.api_url, url)
        if self.offline:
            raise OSError("offline")
        return self.latest

    def _download(self, **kwargs):
        self.downloads += 1
        self.assertTrue(kwargs.pop("url").startswith(
            "https://github.com/SymmetricDevs/Supersymmetry/releases/download/"
        ))
        return fetch_verified_artifact(url=self.source.as_uri(), **kwargs)

    def test_offline_check_is_distinct_and_does_not_create_choice(self) -> None:
        self.offline = True
        observed = self.service.check()
        self.assertEqual("unavailable", observed["status"])
        self.assertEqual("0.1.16.16", observed["selected_version"])
        self.assertIsNone(observed["candidate"])
        self.assertFalse(self.service.choice_path.exists())
        self.assertFalse(self.service.choice_path.parent.exists())

    def test_ignore_is_durable_only_for_one_exact_release(self) -> None:
        checked = self.service.check()
        self.assertEqual("update_available", checked["status"])
        identity = checked["candidate"]["release_id"]
        self.assertEqual("ignored", self.service.ignore(identity)["status"])
        self.assertEqual("ignored", self.service.check()["status"])
        self.assertEqual("0.1.16.16", self.service.show()["selected_version"])
        self.assertEqual(0, self.downloads)
        self.assertTrue(self.service.choice_path.is_file())
        self.assertEqual("stale", self.service.ignore(identity)["status"])
        self.assertEqual(0, self.downloads)
        newer = self.root / "next-client.zip"
        _client_zip(newer, "0.1.16.18")
        self.latest = _release("0.1.16.18", newer, release_number=18)
        self.assertEqual("update_available", self.service.check()["status"])

    def test_accept_verifies_and_saves_without_touching_workspace(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        witness = workspace / "keep.txt"
        witness.write_text("unchanged", encoding="utf-8")
        identity = self.service.check()["candidate"]["release_id"]
        accepted = self.service.accept(identity)
        self.assertEqual("accepted", accepted["status"])
        self.assertEqual("0.1.16.17", accepted["selected_version"])
        self.assertEqual(1, self.downloads)
        artifact = Path(accepted["artifact_path"])
        self.assertTrue(artifact.is_file())
        self.assertEqual(self.source.read_bytes(), artifact.read_bytes())
        self.assertEqual("current", self.service.check()["status"])
        shown = self.service.show()
        self.assertEqual("verified", shown["artifact_state"])
        self.assertEqual(str(artifact), shown["selected"]["artifact_path"])
        self.assertEqual("unchanged", witness.read_text(encoding="utf-8"))
        self.assertEqual([witness], list(workspace.iterdir()))

    def test_current_baseline_requires_explicit_prepare(self) -> None:
        _client_zip(self.source, "0.1.16.16")
        self.authority = replace(
            self.authority, baseline_sha256="sha256:" + sha256(self.source.read_bytes()).hexdigest(),
            baseline_size=self.source.stat().st_size,
        )
        self.service = PackReleaseService(
            self.authority, config_home=self.root / ".workbench",
            state_root=self.root / "state", latest_fetcher=self._latest,
            artifact_fetcher=self._download,
        )
        self.latest = _release("0.1.16.16", self.source, release_number=16)
        checked = self.service.check()
        self.assertEqual("current", checked["status"])
        self.assertEqual("not_prepared", checked["artifact_state"])
        self.assertEqual(0, self.downloads)
        self.assertEqual("stale", self.service.accept(checked["candidate"]["release_id"])["status"])
        prepared = self.service.prepare(checked["selected"]["release_id"])
        self.assertEqual("prepared", prepared["status"])
        self.assertEqual("0.1.16.16", prepared["selected_version"])
        self.assertEqual("verified", self.service.show()["artifact_state"])
        self.assertEqual(1, self.downloads)
        self.assertTrue(self.service.choice_path.is_file())

    def test_prepare_refuses_changed_baseline_digest(self) -> None:
        _client_zip(self.source, "0.1.16.16")
        self.latest = _release("0.1.16.16", self.source, release_number=16)
        checked = self.service.check()
        self.assertEqual("update_available", checked["status"])
        self.assertEqual("stale", self.service.prepare(checked["candidate"]["release_id"])["status"])
        self.assertEqual(0, self.downloads)
        self.assertFalse(self.service.choice_path.exists())

    def test_ignored_newer_release_does_not_block_preparing_baseline(self) -> None:
        _client_zip(self.source, "0.1.16.16")
        self.authority = replace(
            self.authority, baseline_sha256="sha256:" + sha256(self.source.read_bytes()).hexdigest(),
            baseline_size=self.source.stat().st_size,
        )
        self.service = PackReleaseService(
            self.authority, config_home=self.root / ".workbench",
            state_root=self.root / "state", latest_fetcher=self._latest,
            artifact_fetcher=self._download,
        )
        newer = self.root / "newer.zip"
        _client_zip(newer, "0.1.16.17")
        self.latest = _release("0.1.16.17", newer)
        latest_id = self.service.check()["candidate"]["release_id"]
        self.assertEqual("ignored", self.service.ignore(latest_id)["status"])
        self.offline = True
        selected_id = self.service.show()["selected"]["release_id"]
        prepared = self.service.prepare(selected_id)
        self.assertEqual("prepared", prepared["status"])
        self.assertEqual("0.1.16.16", prepared["selected_version"])
        self.assertEqual("verified", self.service.show()["artifact_state"])

    def test_show_distinguishes_missing_and_changed_artifact(self) -> None:
        identity = self.service.check()["candidate"]["release_id"]
        accepted = self.service.accept(identity)
        artifact = Path(accepted["artifact_path"])
        artifact.unlink()
        shown = self.service.show()
        self.assertEqual("missing", shown["artifact_state"])
        self.assertIsNone(shown["selected"]["artifact_path"])
        self.assertEqual("prepared", self.service.prepare(shown["selected"]["release_id"])["status"])
        artifact.write_bytes(b"tampered")
        shown = self.service.show()
        self.assertEqual("changed", shown["artifact_state"])
        self.assertIsNone(shown["selected"]["artifact_path"])
        refused = self.service.prepare(shown["selected"]["release_id"])
        self.assertEqual("unavailable", refused["status"])
        self.assertEqual("changed", self.service.show()["artifact_state"])

    def test_failed_digest_or_manifest_keeps_previous_choice(self) -> None:
        identity = self.service.check()["candidate"]["release_id"]
        self.source.write_bytes(b"changed after GitHub metadata")
        failed = self.service.accept(identity)
        self.assertEqual("unavailable", failed["status"])
        self.assertEqual("0.1.16.16", failed["selected_version"])
        self.assertFalse(self.service.choice_path.exists())

        _client_zip(self.source, "0.1.16.17", loader="forge-invalid")
        self.latest = _release("0.1.16.17", self.source)
        identity = self.service.check()["candidate"]["release_id"]
        failed = self.service.accept(identity)
        self.assertEqual("unavailable", failed["status"])
        self.assertIn("manifest", failed["reason"])
        self.assertFalse(self.service.choice_path.exists())

    def test_stale_expected_identity_cannot_change_choice(self) -> None:
        checked = self.service.check()
        wrong_id = "github-release:sha256:" + "0" * 64
        self.assertNotEqual(wrong_id, checked["candidate"]["release_id"])
        self.assertEqual("stale", self.service.accept(wrong_id)["status"])
        self.assertEqual("stale", self.service.ignore(wrong_id)["status"])
        self.assertEqual(0, self.downloads)
        self.assertFalse(self.service.choice_path.exists())

    def test_rejects_missing_digest_and_non_client_asset(self) -> None:
        self.latest["assets"][0]["digest"] = None
        observed = self.service.check()
        self.assertEqual("unavailable", observed["status"])
        self.assertIn("SHA-256", observed["reason"])
        self.latest["assets"][0]["digest"] = "sha256:" + "0" * 64
        self.latest["assets"][0]["name"] = "supersymmetry-server-0.1.16.17.zip"
        self.assertEqual("unavailable", self.service.check()["status"])
        self.assertFalse(self.service.choice_path.exists())

    def test_offline_show_cli_returns_saved_selection_as_json(self) -> None:
        output = StringIO()
        with (patch("workbench_core.pack_release.profile_resources",
                    return_value={"supersymmetry": AUTHORITY_PATH}),
              patch("workbench_core.pack_release.default_user_config_home",
                    return_value=self.root / ".workbench"),
              patch("workbench_core.pack_release.default_runtime_state_root",
                    return_value=self.root / "state"),
              redirect_stdout(output)):
            self.assertEqual(0, main(["show", "--profile", "supersymmetry", "--json"]))
        response = json.loads(output.getvalue())
        self.assertEqual("workbench.pack-release.v1", response["schema"])
        self.assertEqual("selected", response["status"])
        self.assertEqual("none", response["artifact_state"])
        self.assertTrue(response["selected"]["release_id"].startswith("profile-release:sha256:"))
        self.assertFalse(self.service.choice_path.exists())

    def test_core_dispatch_discovers_profile_resource_for_show_and_check(self) -> None:
        root = AUTHORITY_PATH.parents[3]
        profile = Profile(
            "supersymmetry", "pack", AUTHORITY_PATH.parent,
            {"release-authority": "release-authority-v1.json"},
        )
        entry = SimpleNamespace(
            name="supersymmetry",
            dist=SimpleNamespace(metadata={"Name": "workbench-profile-supersymmetry"},
                                 version="0.1.0", requires=()),
            load=lambda: (lambda: profile),
        )

        class Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return self_url

            def read(self, limit: int):
                return body[:limit]

        self_url = self.authority.api_url
        body = json.dumps(self.latest).encode("utf-8")
        with (patch("workbench_api.profiles.metadata.entry_points", return_value=[entry]),
              patch("workbench_core.pack_release.default_user_config_home",
                    return_value=self.root / ".workbench"),
              patch("workbench_core.pack_release.default_runtime_state_root",
                    return_value=self.root / "state"),
              patch("workbench_core.pack_release.urlopen", return_value=Response())):
            for action, expected in (("show", "selected"), ("check", "update_available")):
                with self.subTest(action=action):
                    output = StringIO()
                    with redirect_stdout(output):
                        code = _dispatch(["pack", "release", action, "--profile",
                                          "supersymmetry", "--json"], root)
                    self.assertEqual(0, code)
                    response = json.loads(output.getvalue())
                    self.assertEqual(expected, response["status"])
                    self.assertEqual("0.1.16.16", response["selected_version"])

    def test_api_fetch_rejects_oversized_response_and_redirect(self) -> None:
        class Response:
            headers = {"Content-Length": "3"}

            def __init__(self, url: str, body: bytes) -> None:
                self.url = url
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return self.url

            def read(self, limit: int):
                return self.body[:limit]

        with patch("workbench_core.pack_release.urlopen", return_value=Response(
            "https://other.example/release", b"{}"
        )):
            with self.assertRaisesRegex(ReleaseUnavailable, "redirected"):
                fetch_latest_release(self.authority.api_url)
        too_large = Response(self.authority.api_url, b"{}")
        too_large.headers = {"Content-Length": str(1024 * 1024 + 1)}
        with patch("workbench_core.pack_release.urlopen", return_value=too_large):
            with self.assertRaisesRegex(ReleaseUnavailable, "size limit"):
                fetch_latest_release(self.authority.api_url)


if __name__ == "__main__":
    unittest.main()
