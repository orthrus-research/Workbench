"""Core-retained Forge copy inputs and pinned destination copying."""

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.overlay_envelope_inputs import (
    CoreOverlayEnvelopeInputs, OverlayEnvelopeInputError,
)
from workbench_core.storage.registered import ResourceCatalog
from workbench_api.durable_resources import DurableResourceError


@unittest.skipUnless(sys.platform == "linux", "overlay copy requires Linux no-follow handles")
class OverlayEnvelopeInputTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.configuration_home = self.base / "settings"
        self.artifacts = self.workspace / ".workbench"
        self.source = self.base / "source" / "config" / "gregtech"
        fluid = self.source / "worldgen" / "fluid"
        vein = self.source / "worldgen" / "vein"
        fluid.mkdir(parents=True)
        vein.mkdir()
        (self.source / "dimensions.json").write_bytes(b"{}\n")
        (fluid / "oil.json").write_bytes(b'{"fluid":"oil"}\n')
        (vein / "sidecar.txt").write_bytes(b"sidecar\n")
        os.chmod(vein / "sidecar.txt", 0o600)
        os.chmod(vein, 0o750)
        self.host = CoreOverlayEnvelopeInputs(
            workspace=self.workspace, configuration_home=self.configuration_home,
            owner_id="crucible",
        )
        self.trees = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.configuration_home,
            locations={"artifacts": self.artifacts}, owner_id="crucible",
        )
        self.target = self.artifacts / "overlays" / "fixture" / "config"

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode()

    def _inventory(self) -> tuple[dict[str, object], bytes]:
        rows = []
        for relative in (
            "dimensions.json", "worldgen", "worldgen/fluid", "worldgen/fluid/oil.json",
            "worldgen/vein", "worldgen/vein/sidecar.txt",
        ):
            selected = self.source / relative
            if selected.is_dir():
                rows.append({"path": relative, "kind": "directory",
                             "mode": None if relative == "worldgen" else stat.S_IMODE(selected.stat().st_mode)})
            else:
                raw = selected.read_bytes()
                rows.append({"path": relative, "kind": "file", "mode": stat.S_IMODE(selected.stat().st_mode),
                             "size_bytes": len(raw), "sha256": sha256(raw).hexdigest()})
        chunk = b"".join(self._canonical(row) + b"\n" for row in rows)
        chunks_digest = sha256(
            f"0\0{len(chunk)}\0{sha256(chunk).hexdigest()}\n".encode("ascii")
        ).hexdigest()
        return {"inventory_id": "fixture-copy:sha256:" + sha256(chunk).hexdigest(),
                "chunk_count": 1, "chunks_sha256": chunks_digest}, chunk

    def _attempt(self, stage):
        manifest, chunk = self._inventory()
        attempt = self.host.start(stage=stage, source_root=self.source,
                                  plan_chunks=(b'{"operations":["replace"]}',))
        attempt.emit_chunk(0, chunk)
        attempt.seal_inputs(manifest, validate_inventory=lambda selected, chunks: (
            selected if list(chunks) == [chunk] else None
        ))
        return attempt, manifest

    def test_core_copies_complete_rows_and_retains_pre_copy_attempt(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            self.assertEqual("input-sealed", self.host.inventory()[0]["status"])
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            self.assertEqual(b"sidecar\n", (content / "worldgen/vein/sidecar.txt").read_bytes())
            self.assertEqual(0o600, stat.S_IMODE((content / "worldgen/vein/sidecar.txt").stat().st_mode))
            self.assertEqual(0o750, stat.S_IMODE((content / "worldgen/vein").stat().st_mode))
            self.assertEqual(b"sidecar\n", (self.source / "worldgen/vein/sidecar.txt").read_bytes())
            self.assertFalse(self.target.exists())
        reopened = CoreOverlayEnvelopeInputs(
            workspace=self.workspace, configuration_home=self.configuration_home,
            owner_id="crucible",
        )
        self.assertEqual("copy-complete", reopened.inventory()[0]["status"])
        self.assertEqual("copy-complete", ResourceCatalog(self.configuration_home).inventory(
            workspace=self.workspace)["overlay_envelopes"][0]["status"])
        json.dumps(ResourceCatalog(self.configuration_home).inventory(
            workspace=self.workspace)["overlay_envelopes"])
        self.assertTrue(stage.path.is_dir())

    def test_source_drift_leaves_unpublished_recoverable_stage(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            (self.source / "worldgen/vein/sidecar.txt").write_bytes(b"changed\n")
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "source bytes changed"):
                attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
        self.assertEqual("copy-incomplete", self.host.inventory()[0]["status"])
        self.assertFalse(self.target.exists())
        self.assertTrue(stage.path.is_dir())

    def test_injected_copy_interruption_retains_exact_attempt(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            with patch.object(attempt, "_copy_file", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
        reopened = CoreOverlayEnvelopeInputs(
            workspace=self.workspace, configuration_home=self.configuration_home,
            owner_id="crucible",
        )
        row = reopened.inventory()[0]
        self.assertEqual("copy-incomplete", row["status"])
        self.assertEqual(stage.tree_id, row["tree_id"])
        self.assertTrue(stage.path.exists())
        self.assertFalse(self.target.exists())

    def test_retained_chunk_mutation_blocks_copy_before_payload(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            chunk_path = attempt.root / "inventory" / "0000000000000000.bin"
            chunk_path.write_bytes(chunk_path.read_bytes().replace(b"sidecar", b"wrongxx", 1))
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "inventory changed"):
                attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
        with self.assertRaisesRegex(OverlayEnvelopeInputError, "copied-entry bytes changed"):
            self.host.inventory()
        self.assertFalse(stage.path.exists())

    def test_catalog_retains_orphan_and_rejects_unknown_attempt_entry(self) -> None:
        self.host._ensure()
        orphan = self.host.root / ("a" * 32)
        orphan.mkdir(mode=0o700)
        rows = ResourceCatalog(self.configuration_home).inventory(workspace=self.workspace)["overlay_envelopes"]
        self.assertEqual("orphan-pre-reservation", rows[0]["status"])
        (orphan / "unrecognized").write_bytes(b"?")
        with self.assertRaises(DurableResourceError):
            ResourceCatalog(self.configuration_home).inventory(workspace=self.workspace)

    def test_failed_plan_stream_is_a_visible_pre_reservation_orphan(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "plan chunk"):
                self.host.start(stage=stage, source_root=self.source,
                                plan_chunks=(b"first", b""))
        rows = ResourceCatalog(self.configuration_home).inventory(workspace=self.workspace)
        self.assertEqual("orphan-pre-reservation", rows["overlay_envelopes"][0]["status"])
        self.assertFalse(self.target.exists())

    def test_unregistered_attempt_root_is_visible_without_root_manifest_mutation(self) -> None:
        catalog = ResourceCatalog(self.configuration_home)
        catalog._ensure()
        self.host.root.mkdir(mode=0o700)
        rows = catalog.inventory(workspace=self.workspace)["overlay_envelopes"]
        self.assertEqual("unregistered-store", rows[0]["status"])
        self.assertEqual(str(self.host.root), rows[0]["path"])

    def test_broken_redirected_attempt_root_is_not_reported_absent(self) -> None:
        self.configuration_home.mkdir(mode=0o700)
        self.host.root.symlink_to(self.base / "missing", target_is_directory=True)
        with self.assertRaises(OverlayEnvelopeInputError):
            self.host.inventory()

    def test_changed_reservation_target_is_rejected_against_core_tree(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, _manifest = self._attempt(stage)
            path = attempt.root / "reservation.json"
            changed = json.loads(path.read_bytes())
            changed["target"] = str(self.artifacts / "another" / "config")
            path.write_bytes(self._canonical(changed) + b"\n")
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "managed-stage binding"):
                self.host.inventory()

    def test_hard_exit_after_first_copied_file_reopens_as_incomplete(self) -> None:
        manifest, chunk = self._inventory()
        manifest_path = self.base / "manifest.json"
        chunk_path = self.base / "copy.bin"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        chunk_path.write_bytes(chunk)
        source_root = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(source_root / "api/src"), str(source_root / "core/src"),
        ))
        environment.update({
            "W5_WORKSPACE": str(self.workspace), "W5_SETTINGS": str(self.configuration_home),
            "W5_ARTIFACTS": str(self.artifacts), "W5_SOURCE": str(self.source),
            "W5_TARGET": str(self.target), "W5_MANIFEST": str(manifest_path),
            "W5_CHUNK": str(chunk_path),
        })
        script = """
import json, os
from pathlib import Path
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.overlay_envelope_inputs import CoreOverlayEnvelopeInputs
workspace = Path(os.environ['W5_WORKSPACE'])
settings = Path(os.environ['W5_SETTINGS'])
trees = CoreManagedTrees(workspace=workspace, configuration_home=settings,
    locations={'artifacts': Path(os.environ['W5_ARTIFACTS'])}, owner_id='crucible')
host = CoreOverlayEnvelopeInputs(workspace=workspace, configuration_home=settings,
    owner_id='crucible')
manifest = json.loads(Path(os.environ['W5_MANIFEST']).read_text())
chunk = Path(os.environ['W5_CHUNK']).read_bytes()
with trees.stage('artifacts', 'config', requested_path=Path(os.environ['W5_TARGET'])) as stage:
    attempt = host.start(stage=stage, source_root=Path(os.environ['W5_SOURCE']),
        plan_chunks=(b'{"operations":["replace"]}',))
    attempt.emit_chunk(0, chunk)
    attempt.seal_inputs(manifest, validate_inventory=lambda m, chunks: m if list(chunks) == [chunk] else None)
    copy_file = attempt._copy_file
    def crash_after_one(*args):
        copy_file(*args)
        os._exit(73)
    attempt._copy_file = crash_after_one
    attempt.copy_source(verify_source=lambda m, _chunks: m)
"""
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment,
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(73, result.returncode, result.stderr)
        rows = ResourceCatalog(self.configuration_home).inventory(workspace=self.workspace)
        self.assertEqual("copy-incomplete", rows["overlay_envelopes"][0]["status"])
        self.assertEqual("incomplete", rows["trees"][0]["status"])
        self.assertTrue(Path(rows["overlay_envelopes"][0]["stage_path"]).is_dir())
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
