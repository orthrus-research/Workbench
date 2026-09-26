"""Opt-in large POSIX managed-tree inventory and crash recovery."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workbench_api.managed_trees import ManagedTreeError
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.storage.registered import ResourceCatalog
from workbench_core.storage.tree_catalog import EXACT_INTENT_KIND, TreeCatalog
from workbench_core.storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


@unittest.skipUnless(os.name == "posix" and sys.platform.startswith("linux"),
                     "exact tree publication requires Linux POSIX no-replace rename")
class ExactManagedTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.config = self.home / "config"
        self.evidence = self.home / "evidence"
        self.host = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="shell",
        )

    def _publish(self, *, name: str = "template", file_count: int = 1):
        with self.host.stage("evidence", name) as stage:
            stage.path.mkdir(mode=0o700)
            for index in range(file_count):
                (stage.path / f"generated-{index:05d}.txt").write_bytes(b"x")
            secret = stage.path / "notes:local.cfg"
            secret.write_bytes(b"private")
            secret.chmod(0o600)
            return stage.publish(validate=lambda _: None,
                                 inventory_policy=EXACT_INVENTORY_POLICY)

    def test_large_colon_tree_has_compact_intent_and_exact_mode_attestation(self) -> None:
        reference = self._publish(file_count=4_100)
        self.assertEqual(4_101, len(reference.members))
        self.assertEqual(0o600, next(row["mode"] for row in reference.members
                                     if row["path"] == "notes:local.cfg"))
        intent = TreeCatalog(self.config / "resources-v1").intent(reference.tree_id)
        self.assertEqual(EXACT_INTENT_KIND, intent["format"])
        self.assertEqual(4_101, intent["member_count"])
        self.assertNotIn("members", intent)
        nonce = reference.tree_id.rsplit(":", 1)[1]
        self.assertLess((self.config / "resources-v1" / "trees" / "intents" /
                         f"{nonce}.json").stat().st_size, 4 * 1024 * 1024)
        self.assertEqual(reference, self.host.describe(reference.tree_id))
        row = ResourceCatalog(self.config).inventory()["trees"][0]
        self.assertEqual(("committed", 4_101), (row["status"], row["member_count"]))

        secret = reference.path / "notes:local.cfg"
        secret.chmod(0o644)
        with self.assertRaises(ManagedTreeError) as changed:
            self.host.describe(reference.tree_id)
        self.assertEqual("tree.changed", changed.exception.code)
        self.assertEqual("changed", ResourceCatalog(self.config).inventory()["trees"][0]["status"])
        secret.chmod(0o600)
        self.assertEqual(reference, self.host.describe(reference.tree_id))
        reference.path.chmod(0o755)
        with self.assertRaises(ManagedTreeError) as changed:
            self.host.describe(reference.tree_id)
        self.assertEqual("tree.changed", changed.exception.code)

    def test_default_v1_bounds_and_paths_remain_unchanged(self) -> None:
        with self.host.stage("evidence", "v1-limit") as stage:
            stage.path.mkdir()
            for index in range(4_097):
                (stage.path / f"file-{index:05d}").write_bytes(b"")
            with self.assertRaises(ManagedTreeError) as bounded:
                stage.publish(validate=lambda _: None)
        self.assertEqual("tree.bounds", bounded.exception.code)
        with self.host.stage("evidence", "v1-path") as stage:
            stage.path.mkdir()
            (stage.path / "notes:local.cfg").write_bytes(b"x")
            with self.assertRaises(ValueError):
                stage.publish(validate=lambda _: None)

    def test_exact_policy_rejects_derived_members_and_unsafe_inputs(self) -> None:
        with self.host.stage("evidence", "derived") as stage:
            stage.path.mkdir()
            (stage.path / "index.db").write_bytes(b"x")
            with self.assertRaises(ManagedTreeError) as rejected:
                stage.publish(validate=lambda _: None,
                              inventory_policy=EXACT_INVENTORY_POLICY,
                              derived_members=("index.db",))
        self.assertEqual("tree.policy", rejected.exception.code)
        for name, create in (
            ("link", lambda path: (path / "linked").symlink_to("outside")),
            ("hardlink", lambda path: os.link(path / "base", path / "linked")),
        ):
            with self.subTest(name=name):
                with self.host.stage("evidence", name) as stage:
                    stage.path.mkdir(mode=0o700)
                    (stage.path / "base").write_bytes(b"x")
                    create(stage.path)
                    with self.assertRaises(ManagedTreeError):
                        stage.publish(validate=lambda _: None,
                                      inventory_policy=EXACT_INVENTORY_POLICY)
                self.assertFalse(stage.target.exists())

    def test_literal_posix_names_and_directory_modes_are_attested(self) -> None:
        with self.host.stage("evidence", "literal-names") as stage:
            stage.path.mkdir(mode=0o700)
            directory = stage.path / ".git"
            directory.mkdir(mode=0o700)
            (directory / "name\\part:one\n.txt").write_bytes(b"literal")
            reference = stage.publish(validate=lambda _: None,
                                      inventory_policy=EXACT_INVENTORY_POLICY)
        self.assertEqual({".git", ".git/name\\part:one\n.txt"},
                         {row["path"] for row in reference.members})
        self.assertEqual(0o700, next(row["mode"] for row in reference.members
                                     if row["path"] == ".git"))
        self.assertEqual(reference, self.host.describe(reference.tree_id))
        directory = reference.path / ".git"
        directory.chmod(0o755)
        with self.assertRaises(ManagedTreeError) as changed:
            self.host.describe(reference.tree_id)
        self.assertEqual("tree.changed", changed.exception.code)

    def _abrupt_exit_and_reconcile(self, count: int) -> None:
        output = self.workspace / "templates" / "recovered"
        script = """
import os
from pathlib import Path
import workbench_core.managed_trees as trees

trees._rename_no_replace = lambda source, target, **kwargs: os._exit(73)
host = trees.CoreManagedTrees(
    workspace=Path(os.environ["W3_WORKSPACE"]),
    configuration_home=Path(os.environ["W3_CONFIG"]),
    locations={"evidence": Path(os.environ["W3_EVIDENCE"])},
    owner_id="shell",
)
with host.stage("evidence", "recovered", requested_path=Path(os.environ["W3_OUTPUT"])) as stage:
    stage.path.mkdir(mode=0o700)
    for index in range(int(os.environ["W3_COUNT"])):
        (stage.path / f"generated-{index:05d}").write_bytes(b"x")
    (stage.path / "notes:local.cfg").write_bytes(b"private")
    stage.publish(validate=lambda _: None, inventory_policy="posix-exact-v1")
"""
        project = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment.update({
            "PYTHONPATH": os.pathsep.join((str(project / "api" / "src"),
                                           str(project / "core" / "src"),
                                           environment.get("PYTHONPATH", ""))),
            "W3_WORKSPACE": str(self.workspace), "W3_CONFIG": str(self.config),
            "W3_EVIDENCE": str(self.evidence), "W3_OUTPUT": str(output),
            "W3_COUNT": str(count),
        })
        result = subprocess.run([sys.executable, "-c", script], env=environment,
                                capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(73, result.returncode, result.stderr)
        catalog = ResourceCatalog(self.config)
        row = catalog.inventory()["trees"][0]
        self.assertEqual(("incomplete", count + 1), (row["status"], row["member_count"]))
        self.assertFalse(output.exists())
        reference = self.host.reconcile(row["tree_id"])
        self.assertEqual(count + 1, len(reference.members))
        self.assertEqual(output, reference.path)
        self.assertEqual("committed", catalog.inventory()["trees"][0]["status"])
        self.assertEqual(reference, self.host.describe(reference.tree_id))

    def test_abrupt_exit_reconciles_large_exact_stage_without_replacing_target(self) -> None:
        self._abrupt_exit_and_reconcile(4_100)

    @unittest.skipUnless(os.environ.get("WORKBENCH_TEST_LARGE_TREES") == "1",
                         "run explicitly for the 100,000-file capacity probe")
    def test_abrupt_exit_reconciles_near_limit_exact_stage(self) -> None:
        self._abrupt_exit_and_reconcile(99_999)


if __name__ == "__main__":
    unittest.main()
