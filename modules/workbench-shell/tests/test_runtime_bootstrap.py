"""Focused tests for guarded Cleanroom client bootstrap materialization."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
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
from workbench_shell import runtime_bootstrap as bootstrap  # noqa: E402
from workbench_api import verified_artifacts  # noqa: E402
from workbench_api.managed_trees import ManagedTreeError, managed_trees  # noqa: E402
from workbench_core.host_services import install_local_host_services  # noqa: E402
from workbench_core.packwiz_tree_scope import direct_packwiz_tree_scope  # noqa: E402
from workbench_core.storage.registered import ResourceCatalog  # noqa: E402
from workbench_core.storage.tree_catalog import TreeCatalog  # noqa: E402


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
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()

    @staticmethod
    def _tree_scope(state_root: Path):
        return direct_packwiz_tree_scope(
            workspace=Path("/workspace/supersymmetry"),
            state_root=state_root,
            configuration_home=state_root.parent / "config",
        )

    def _materialize(
        self, plan: dict[str, object], *, artifact_size: int,
        artifact_source_revision: str, state_root: Path,
    ) -> dict[str, object]:
        with self._tree_scope(state_root):
            return materialize_client_bootstrap(
                plan,
                artifact_size=artifact_size,
                artifact_source_revision=artifact_source_revision,
                state_root=state_root,
            )

    def test_materializes_verified_instance_and_reuses_exact_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)

            with patch(
                "workbench_shell.runtime_bootstrap.acquire_verified_artifact",
                wraps=verified_artifacts.acquire_verified_artifact,
            ) as acquire, patch(
                "workbench_shell.runtime_bootstrap._extract_client_archive",
                wraps=bootstrap._extract_client_archive,
            ) as extract:
                created = self._materialize(
                    plan,
                    artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )
            acquire.assert_called_once_with(
                url=archive.as_uri(),
                expected_sha256=sha256(archive.read_bytes()).hexdigest(),
                expected_size=archive.stat().st_size,
                state_root=state_root,
                label="Cleanroom client artifact",
                timeout_seconds=30.0,
                user_agent="Workbench-Cleanroom-Bootstrap/0.1",
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
            if sys.platform.startswith("linux"):
                self.assertIsInstance(extract.call_args.args[0], bytes)
            self.assertNotIn("resource_id", receipt["artifact"])
            fixture_root = Path(
                receipt["target"]["fixture_root_uri"].removeprefix("file://")
            )
            if sys.platform.startswith("linux"):
                with self._tree_scope(state_root):
                    target = managed_trees().lookup_target("artifacts", fixture_root)
                    cataloged = managed_trees().reconcile(target.tree_id)
                self.assertEqual(target.status, "committed")
                self.assertEqual(cataloged.domain_id, receipt["bootstrap_id"])
                self.assertEqual(cataloged.inventory_policy, "posix-exact-v1")
                self.assertEqual(cataloged.derived_status, "current")
                self.assertEqual(len(cataloged.references), 1)
                with self._tree_scope(state_root):
                    source, source_bytes = managed_trees().read_file_reference(
                        cataloged.references[0],
                    )
                self.assertEqual(source.domain_id, plan["plan_id"] + ":cleanroom-client-zip")
                self.assertEqual(source_bytes, archive.read_bytes())

            reused = self._materialize(
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

    def test_historical_receipt_only_fixture_reopens_without_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            fixture_root = bootstrap._local_path(
                plan["target"]["fixture_root_uri"], "fixture root",
            )
            instance_root = fixture_root / "instance"
            bootstrap._extract_client_archive(archive, instance_root)
            bootstrap._validate_client_instance(
                instance_root, minecraft_version="1.12.2",
                cleanroom_version="0.6.8-alpha",
            )
            receipt = bootstrap._receipt(
                plan, artifact=bootstrap._client_artifact(plan),
                artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                cache_path=archive, fixture_root=fixture_root,
                instance=bootstrap._tree_manifest(instance_root),
            )
            receipt_path = fixture_root / bootstrap.RECEIPT_PATH
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n", encoding="utf-8",
            )

            reopened = self._materialize(
                plan, artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            self.assertEqual(reopened["outcome"], "reused")
            self.assertEqual(reopened["receipt"], receipt)
            with self._tree_scope(state_root):
                with self.assertRaises(ManagedTreeError) as missing:
                    managed_trees().lookup_target("artifacts", fixture_root)
            self.assertEqual(missing.exception.code, "tree.unavailable")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_pre_source_edge_core_tree_reopens_without_fabricated_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            fixture_root = bootstrap._local_path(
                plan["target"]["fixture_root_uri"], "fixture root",
            )
            with self._tree_scope(state_root):
                host = managed_trees()
                with host.stage(
                    "artifacts", fixture_root.name, requested_path=fixture_root,
                ) as stage:
                    receipt, _bytes = bootstrap._prepare_bootstrap_fixture(
                        stage.path, plan, cache_path=archive,
                        archive_source=archive,
                        artifact=bootstrap._client_artifact(plan),
                        artifact_size=archive.stat().st_size,
                        artifact_source_revision=SOURCE_REVISION,
                        fixture_root=fixture_root,
                    )
                    reference = stage.publish(
                        validate=lambda _: None,
                        domain_id=receipt["bootstrap_id"],
                        inventory_policy="posix-exact-v1",
                    )
            self.assertEqual(reference.references, ())
            reopened = self._materialize(
                plan, artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            self.assertEqual(reopened["outcome"], "reused")
            self.assertEqual(reopened["receipt"], receipt)
            with self._tree_scope(state_root):
                self.assertEqual(managed_trees().reconcile(reference.tree_id).references, ())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_interrupted_source_snapshot_retains_unknown_before_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            fixture_root = bootstrap._local_path(
                plan["target"]["fixture_root_uri"], "fixture root",
            )
            original_write = ResourceCatalog._write
            interrupted = False

            def interrupted_commit(catalog, name, nonce, kind, body):
                nonlocal interrupted
                if name == "commits" and not interrupted:
                    interrupted = True
                    raise OSError("synthetic lost source commit")
                return original_write(catalog, name, nonce, kind, body)

            with patch.object(ResourceCatalog, "_write", interrupted_commit):
                with self.assertRaisesRegex(
                    RuntimeBootstrapError, "Core cannot retain Cleanroom client source",
                ):
                    self._materialize(
                        plan, artifact_size=archive.stat().st_size,
                        artifact_source_revision=SOURCE_REVISION,
                        state_root=state_root,
                    )
            self.assertTrue(interrupted)
            self.assertFalse(fixture_root.exists())
            self.assertEqual(
                len(list((root / "config/resources-v1/intents").glob("*.json"))), 1,
            )
            self.assertEqual(
                len(list((root / "config/resources-v1/commits").glob("*.json"))), 0,
            )
            self.assertEqual(
                len(list((state_root / "evidence/outputs/workbench-shell").glob("*"))), 2,
            )
            rows = ResourceCatalog(root / "config").inventory(
                workspace=Path("/workspace/supersymmetry"),
            )["resources"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "published-uncommitted")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_oversized_source_refuses_before_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            with patch(
                "workbench_shell.runtime_bootstrap.acquire_verified_artifact",
            ) as acquire:
                with self.assertRaisesRegex(
                    RuntimeBootstrapError, "source-reference byte limit",
                ):
                    self._materialize(
                        plan, artifact_size=32 * 1024 * 1024 + 1,
                        artifact_source_revision=SOURCE_REVISION,
                        state_root=state_root,
                    )
            acquire.assert_not_called()
            self.assertFalse(state_root.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_changed_retained_source_refuses_new_tree_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            created = self._materialize(
                plan, artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            fixture_root = bootstrap._local_path(
                created["receipt"]["target"]["fixture_root_uri"], "fixture root",
            )
            with self._tree_scope(state_root):
                host = managed_trees()
                target = host.lookup_target("artifacts", fixture_root)
                source_id = host.reconcile(target.tree_id).references[0]
                source, _bytes = host.read_file_reference(source_id)
            source.path.write_bytes(b"x" * source.bytes)
            with self.assertRaisesRegex(RuntimeBootstrapError, "requires review"):
                self._materialize(
                    plan, artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_published_without_commit_reconciles_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            fixture_root = bootstrap._local_path(
                plan["target"]["fixture_root_uri"], "fixture root",
            )
            original_write = TreeCatalog._write
            interrupted = False

            def interrupted_commit(catalog, name, nonce, kind, body):
                nonlocal interrupted
                if name == "commits" and not interrupted:
                    interrupted = True
                    raise OSError("synthetic lost commit")
                return original_write(catalog, name, nonce, kind, body)

            with patch.object(TreeCatalog, "_write", interrupted_commit):
                with self.assertRaisesRegex(
                    RuntimeBootstrapError, "Core cannot publish runtime bootstrap",
                ):
                    self._materialize(
                        plan, artifact_size=archive.stat().st_size,
                        artifact_source_revision=SOURCE_REVISION,
                        state_root=state_root,
                    )
            self.assertTrue(interrupted)
            self.assertTrue(fixture_root.is_dir())

            recovered = self._materialize(
                plan, artifact_size=archive.stat().st_size,
                artifact_source_revision=SOURCE_REVISION,
                state_root=state_root,
            )
            self.assertEqual(recovered["outcome"], "reused")
            with self._tree_scope(state_root):
                target = managed_trees().lookup_target("artifacts", fixture_root)
                reference = managed_trees().reconcile(target.tree_id)
            self.assertEqual(target.status, "committed")
            self.assertEqual(reference.domain_id, recovered["receipt"]["bootstrap_id"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_unsupported_publication_retains_stage_and_blocks_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            fixture_root = bootstrap._local_path(
                plan["target"]["fixture_root_uri"], "fixture root",
            )
            with patch(
                "workbench_core.managed_trees._rename_no_replace",
                side_effect=ManagedTreeError(
                    "output.filesystem", "selected filesystem lacks atomic publication",
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeBootstrapError, "atomic publication",
                ):
                    self._materialize(
                        plan, artifact_size=archive.stat().st_size,
                        artifact_source_revision=SOURCE_REVISION,
                        state_root=state_root,
                    )
            self.assertFalse(fixture_root.exists())
            self.assertEqual(
                len(list(fixture_root.parent.glob(".workbench-tree-*.pending"))), 1,
            )
            rows = ResourceCatalog(root / "config").inventory(
                workspace=Path("/workspace/supersymmetry"),
            )["resources"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "committed")
            with self.assertRaisesRegex(
                RuntimeBootstrapError, "requires review",
            ):
                self._materialize(
                    plan, artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_historical_incomplete_stage_is_retained_and_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            fixture_root = bootstrap._local_path(
                plan["target"]["fixture_root_uri"], "fixture root",
            )
            fixture_root.parent.mkdir(parents=True)
            incomplete = fixture_root.parent / f".{fixture_root.name}.bootstrap-incomplete"
            incomplete.mkdir(mode=0o700)

            with self.assertRaisesRegex(
                RuntimeBootstrapError, "prepared stage requires review",
            ):
                self._materialize(
                    plan, artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )
            self.assertTrue(incomplete.is_dir())
            self.assertFalse(fixture_root.exists())

    def test_missing_core_host_refuses_without_creating_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            with patch.object(verified_artifacts, "_host", None):
                with self.assertRaisesRegex(
                    RuntimeBootstrapError, "no verified artifact host",
                ):
                    self._materialize(
                        plan,
                        artifact_size=archive.stat().st_size,
                        artifact_source_revision=SOURCE_REVISION,
                        state_root=state_root,
                    )
            self.assertFalse(state_root.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux managed tree route")
    def test_unbound_linux_tree_host_refuses_new_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)

            with self.assertRaisesRegex(
                RuntimeBootstrapError, "Core Cleanroom bootstrap custody is unavailable",
            ):
                materialize_client_bootstrap(
                    plan, artifact_size=archive.stat().st_size,
                    artifact_source_revision=SOURCE_REVISION,
                    state_root=state_root,
                )
            self.assertFalse(state_root.exists())

    def test_modified_existing_target_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "cleanroom.zip"
            _write_archive(archive)
            state_root = root / "state"
            plan = _plan(state_root, archive)
            created = self._materialize(
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
                self._materialize(
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
            created = self._materialize(
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
                self._materialize(
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
                self._materialize(
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
                self._materialize(
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
                self._materialize(
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
