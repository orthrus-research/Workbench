"""Exact Feature Studio pair custody, reopening, and interrupted publication."""

from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.feature_exports import FeatureExportError
from workbench_core.feature_exports import CoreFeatureExports
from workbench_core.transport_trees import CoreTransportTrees


@unittest.skipUnless(sys.platform == "linux", "V2 transport trees require Linux/WSL")
class FeatureExportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "source"
        self.workspace.mkdir()
        self.parent = self.root / "outputs"
        self.parent.mkdir()
        self.target = self.parent / "export"
        self.host = CoreFeatureExports(configuration_home=self.root / "config")
        self.patch = b"--- a/source\n+++ b/source\n"
        self.receipt = b'{"format":"v1"}\n'
        self.receipt_id = "feature-studio-export-receipt:sha256:" + "a" * 64

    def _publish(self, target: Path | None = None) -> str:
        return self.host.publish(
            workspace=self.workspace, target=target or self.target,
            patch=self.patch, receipt=self.receipt, receipt_id=self.receipt_id,
        )

    def _verify(self, tree_id: str, target: Path | None = None) -> None:
        self.host.verify(
            workspace=self.workspace, target=target or self.target, tree_id=tree_id,
            patch_sha256=sha256(self.patch).hexdigest(), patch_size=len(self.patch),
            receipt_sha256=sha256(self.receipt).hexdigest(), receipt_size=len(self.receipt),
            receipt_id=self.receipt_id,
        )

    def test_exact_pair_reopens_and_reuses_only_same_review(self) -> None:
        tree_id = self._publish()
        self.assertEqual({"feature.patch", "receipt.json"}, {p.name for p in self.target.iterdir()})
        self.assertEqual(self.patch, (self.target / "feature.patch").read_bytes())
        self.assertEqual(self.receipt, (self.target / "receipt.json").read_bytes())
        self._verify(tree_id)
        self.assertEqual(tree_id, self._publish())
        with self.assertRaises(FeatureExportError):
            self.host.publish(
                workspace=self.workspace, target=self.target,
                patch=self.patch + b"changed", receipt=self.receipt,
                receipt_id=self.receipt_id,
            )
        with self.assertRaises(FeatureExportError):
            self.host.publish(
                workspace=self.workspace, target=self.target,
                patch=self.patch, receipt=self.receipt,
                receipt_id="feature-studio-export-receipt:sha256:" + "b" * 64,
            )

    def test_changed_member_extra_member_and_redirect_refuse_reopen(self) -> None:
        tree_id = self._publish()
        patch_file = self.target / "feature.patch"
        original = patch_file.read_bytes()
        patch_file.write_bytes(b"x" * len(original))
        with self.assertRaises(FeatureExportError):
            self._verify(tree_id)
        patch_file.write_bytes(original)
        extra = self.target / "extra.txt"
        extra.write_bytes(b"x")
        with self.assertRaises(FeatureExportError):
            self._verify(tree_id)
        extra.unlink()
        redirect = self.root / "redirect"
        redirect.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(FeatureExportError):
            self._publish(redirect / "elsewhere")

    def test_existing_unmanaged_target_and_wrong_tree_refuse(self) -> None:
        unmanaged = self.parent / "unmanaged"
        unmanaged.mkdir()
        with self.assertRaises(FeatureExportError) as caught:
            self._publish(unmanaged)
        self.assertEqual("output.exists", caught.exception.code)
        tree_id = self._publish()
        with self.assertRaises(FeatureExportError):
            self._verify(tree_id, self.parent / "elsewhere")

    def test_post_rename_interruption_reconciles_exact_pair(self) -> None:
        original = CoreTransportTrees._write

        def interrupted(host, name, nonce, kind, body, **kwargs):
            if name == "commits":
                raise OSError("injected post-rename interruption")
            return original(host, name, nonce, kind, body, **kwargs)

        with patch.object(CoreTransportTrees, "_write", interrupted):
            with self.assertRaises(FeatureExportError):
                self._publish()
        self.assertTrue(self.target.is_dir())
        rows = self.host._host(self.workspace).inventory()
        self.assertEqual("prepared-incomplete", rows[0]["status"])
        tree_id = self._publish()
        self.assertEqual(rows[0]["tree_id"], tree_id)
        self._verify(tree_id)

    def test_prepared_stage_change_refuses_reconciliation(self) -> None:
        def interrupted(host, reservation, intent, stage=None):
            raise OSError("injected pre-rename interruption")

        with patch.object(CoreTransportTrees, "_publish_intent", interrupted):
            with self.assertRaises(FeatureExportError):
                self._publish()
        rows = self.host._host(self.workspace).inventory()
        self.assertEqual("prepared-incomplete", rows[0]["status"])
        self.assertFalse(self.target.exists())
        staged = Path(rows[0]["staging"])
        (staged / "receipt.json").write_bytes(b"changed")
        with self.assertRaises(FeatureExportError):
            self._publish()
        self.assertFalse(self.target.exists())
        self.assertEqual("prepared-incomplete", self.host._host(self.workspace).inventory()[0]["status"])

    def test_prepared_stage_reconciles_before_rename(self) -> None:
        def interrupted(host, reservation, intent, stage=None):
            raise OSError("injected pre-rename interruption")

        with patch.object(CoreTransportTrees, "_publish_intent", interrupted):
            with self.assertRaises(FeatureExportError):
                self._publish()
        tree_id = self._publish()
        self.assertTrue(self.target.is_dir())
        self._verify(tree_id)

    def test_unsupported_host_and_changed_parent_refuse_before_stage(self) -> None:
        with patch("workbench_core.feature_exports.sys.platform", "darwin"):
            with self.assertRaises(FeatureExportError) as caught:
                self._publish()
            self.assertEqual("feature-export.filesystem", caught.exception.code)
        self.assertEqual((), self.host._host(self.workspace).inventory())
        old_parent = self.parent
        moved = self.root / "moved-outputs"
        old_parent.rename(moved)
        old_parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(FeatureExportError) as caught:
            self._publish()
        self.assertEqual("feature-export.path", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
