"""Focused tests for guarded Cleanroom client bootstrap materialization."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell import (  # noqa: E402
    RuntimeBootstrapError,
    build_runtime_plan,
    materialize_client_bootstrap,
)


SOURCE_REVISION = "9" * 40


CONTEXT = {
    "workspace": {
        "root_uri": "file:///workspace/supersymmetry",
        "revision": "1" * 40,
        "dirty": False,
        "dirty_entries": [],
    },
    "project": {
        "kind": "packwiz-modpack",
        "name": "Supersymmetry",
        "version": "test",
        "author": "SymmetricDevs",
        "pack_format": "packwiz:1.1.0",
        "manifest_sha256": "2" * 64,
        "minecraft_version": "1.12.2",
        "loaders": [{"id": "cleanroom", "version": "0.6.8-alpha"}],
        "index": {
            "file": "index.toml",
            "hash_format": "sha256",
            "declared_hash": "3" * 64,
            "actual_sha256": "3" * 64,
            "matches_declared_hash": True,
        },
        "matched_paths": ["pack.toml", "index.toml"],
    },
    "platform": {
        "profile_id": "workbench-platform:cleanroom:test",
        "status": "provisional",
        "minecraft_version": "1.12.2",
        "cleanroom_version": "0.6.8-alpha",
        "document_sha256": "4" * 64,
    },
    "pack": {
        "profile_family_id": "workbench-pack:supersymmetry",
        "display_name": "Supersymmetry",
        "status": "active",
        "selected_profile": "cleanroom-test",
        "maturity": "experimental",
        "platform_profile_id": "workbench-platform:cleanroom:test",
        "permitted_operations": ["observe", "construct"],
        "document_sha256": "5" * 64,
    },
}


def _write_archive(
    path: Path,
    *,
    cleanroom_version: str = "0.6.8-alpha",
    unsafe_member: str | None = None,
) -> None:
    manifest = {
        "formatVersion": 1,
        "components": [
            {
                "uid": "net.minecraft",
                "version": "1.12.2",
            },
            {
                "cachedName": "Cleanroom",
                "uid": "net.minecraftforge",
                "version": cleanroom_version,
            },
        ],
    }
    with ZipFile(path, "w") as archive:
        if unsafe_member is not None:
            archive.writestr(unsafe_member, "must not escape")
        archive.writestr(
            "instance.cfg",
            "InstanceType=OneSix\nJavaPath=Replace this with your java path\n",
        )
        archive.writestr(
            "mmc-pack.json",
            json.dumps(manifest, sort_keys=True),
        )
        archive.writestr("patches/net.minecraft.json", "{}\n")
        archive.writestr("patches/net.minecraftforge.json", "{}\n")


def _plan(
    state_root: Path,
    archive: Path,
    *,
    digest: str | None = None,
) -> dict[str, object]:
    actual_digest = sha256(archive.read_bytes()).hexdigest()
    profile = {
        "java": {"runtime": "test-java-25"},
        "runtime_artifacts": {
            "packwiz_installer": {
                "url": "https://example.invalid/packwiz-installer.jar",
                "sha256": "6" * 64,
            },
            "cleanroom_client": {
                "url": archive.as_uri(),
                "sha256": digest or actual_digest,
            },
            "cleanroom_server": {
                "url": "https://example.invalid/cleanroom-server.jar",
                "sha256": "8" * 64,
            },
        },
    }
    return build_runtime_plan(
        deepcopy(CONTEXT),
        profile,
        state_root=state_root,
        side="client",
        launcher="prism",
    )


class RuntimeBootstrapTest(unittest.TestCase):
    def test_materializes_verified_instance_and_reuses_exact_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)

            created = materialize_client_bootstrap(
                plan,
                artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )

            self.assertEqual(created["outcome"], "created")
            self.assertEqual(created["artifact_outcome"], "downloaded")
            receipt = created["receipt"]
            self.assertEqual(receipt["state"], "materialized")
            self.assertEqual(
                receipt["readiness"],
                "launcher-base-instance",
            )
            self.assertEqual(receipt["remaining_plan_blockers"], [])
            self.assertEqual(receipt["instance"]["file_count"], 4)
            instance_root = Path(
                receipt["target"]["instance_root_uri"].removeprefix(
                    "file://"
                )
            )
            self.assertTrue((instance_root / "mmc-pack.json").is_file())
            receipt_path = Path(
                receipt["target"]["receipt_uri"].removeprefix("file://")
            )
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")),
                receipt,
            )
            cache_path = (
                state_root
                / "artifacts"
                / "sha256"
                / sha256(archive.read_bytes()).hexdigest()
            )
            self.assertTrue(cache_path.is_file())

            reused = materialize_client_bootstrap(
                plan,
                artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            self.assertEqual(reused["outcome"], "reused")
            self.assertEqual(
                reused["receipt"]["bootstrap_id"],
                receipt["bootstrap_id"],
            )

    def test_modified_existing_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            created = materialize_client_bootstrap(
                plan,
                artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            instance_root = Path(
                created["receipt"]["target"][
                    "instance_root_uri"
                ].removeprefix("file://")
            )
            (instance_root / "instance.cfg").write_text(
                "tampered\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeBootstrapError,
                "drifted",
            ):
                materialize_client_bootstrap(
                    plan,
                    artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

    def test_reuse_rejects_downstream_payload_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            created = materialize_client_bootstrap(
                plan,
                artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            fixture_root = Path(
                created["receipt"]["target"][
                    "fixture_root_uri"
                ].removeprefix("file://")
            )
            payload = fixture_root / "instance/.minecraft"
            payload.mkdir()
            (payload / "packwiz.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (
                fixture_root
                / "receipts/packwiz-materialization-v2.json"
            ).write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeBootstrapError, "drifted"):
                materialize_client_bootstrap(
                    plan,
                    artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

            self.assertTrue((payload / "packwiz.json").is_file())

    def test_hash_mismatch_never_publishes_a_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive, digest="0" * 64)

            with self.assertRaisesRegex(
                RuntimeBootstrapError,
                "SHA-256 mismatch",
            ):
                materialize_client_bootstrap(
                    plan,
                    artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

            self.assertFalse(
                Path(
                    plan["target"]["fixture_root_uri"].removeprefix(
                        "file://"
                    )
                ).exists()
            )
            self.assertFalse(
                (state_root / "artifacts" / "sha256" / ("0" * 64)).exists()
            )

    def test_unsafe_archive_member_never_escapes_or_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive, unsafe_member="../escaped.txt")
            state_root = root / "state"
            plan = _plan(state_root, archive)

            with self.assertRaisesRegex(
                RuntimeBootstrapError,
                "unsafe archive member",
            ):
                materialize_client_bootstrap(
                    plan,
                    artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

            self.assertFalse(
                Path(
                    plan["target"]["fixture_root_uri"].removeprefix(
                        "file://"
                    )
                ).exists()
            )
            self.assertEqual(
                list(state_root.rglob("escaped.txt")),
                [],
            )

    def test_cleanroom_identity_mismatch_never_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive, cleanroom_version="wrong")
            state_root = root / "state"
            plan = _plan(state_root, archive)

            with self.assertRaisesRegex(
                RuntimeBootstrapError,
                "identity mismatch",
            ):
                materialize_client_bootstrap(
                    plan,
                    artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

            self.assertFalse(
                Path(
                    plan["target"]["fixture_root_uri"].removeprefix(
                        "file://"
                    )
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
