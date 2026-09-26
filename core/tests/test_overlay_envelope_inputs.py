"""Core-retained Forge copy inputs and pinned destination copying."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_core.managed_trees import CoreManagedTrees, ManagedTreeError
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

    def _attempt(self, stage, effects=None):
        manifest, chunk = self._inventory()
        attempt = self.host.start(stage=stage, source_root=self.source,
                                  plan_chunks=(b'{"operations":["replace"]}',))
        attempt.emit_chunk(0, chunk)
        attempt.seal_inputs(manifest, validate_inventory=lambda selected, chunks: (
            selected if list(chunks) == [chunk] else None
        ))
        if effects is None:
            effects = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                        "expected_sha256": None, "data": b"{}\n"},)
        attempt.seal_effects(effects, validate_plan=lambda _chunks: effects)
        return attempt, manifest

    def _ready_envelope(self, stage):
        effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                   "expected_sha256": None, "data": b"{}\n"},)
        attempt, manifest = self._attempt(stage, effect)
        attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
        attempt.apply_effects(effect)
        siblings = (b'{"inventory":"reviewed"}\n', b'{"materialization":"reviewed"}\n')

        def validate_output(_content, _chunks):
            return siblings

        attempt.write_siblings(
            inventory_bytes=siblings[0], materialization_bytes=siblings[1],
            validate_output=validate_output,
        )
        return attempt, validate_output

    def test_core_copies_complete_rows_and_retains_pre_copy_attempt(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            self.assertEqual("effects-sealed", self.host.inventory()[0]["status"])
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

    def test_ordered_effects_write_only_the_unpublished_stage(self) -> None:
        oil = (self.source / "worldgen/fluid/oil.json").read_bytes()
        sidecar = (self.source / "worldgen/vein/sidecar.txt").read_bytes()
        effects = (
            {"op": "replace", "relative_path": "worldgen/fluid/oil.json",
             "expected_sha256": sha256(oil).hexdigest(), "data": b'{"fluid":"gas"}\n'},
            {"op": "remove", "relative_path": "worldgen/vein/sidecar.txt",
             "expected_sha256": sha256(sidecar).hexdigest(), "data": None},
            {"op": "add", "relative_path": "worldgen/vein/new/ore.json",
             "expected_sha256": None, "data": b"{}\n"},
        )
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage, effects)
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            self.assertEqual("copy-complete", self.host.inventory()[0]["status"])
            attempt.apply_effects(effects)
            self.assertEqual(b'{"fluid":"gas"}\n', (content / "worldgen/fluid/oil.json").read_bytes())
            self.assertFalse((content / "worldgen/vein/sidecar.txt").exists())
            self.assertEqual(b"{}\n", (content / "worldgen/vein/new/ore.json").read_bytes())
            self.assertEqual("operations-complete", self.host.inventory()[0]["status"])
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "already attempted"):
                attempt.apply_effects(effects)
            self.assertFalse(self.target.exists())
        self.assertEqual(oil, (self.source / "worldgen/fluid/oil.json").read_bytes())
        self.assertEqual(sidecar, (self.source / "worldgen/vein/sidecar.txt").read_bytes())

    def test_interrupted_operation_keeps_stage_and_refuses_replay(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                       "expected_sha256": None, "data": b"{}\n"},)
            with patch.object(attempt, "_apply_effect", side_effect=OSError("hard exit window")):
                with self.assertRaisesRegex(OSError, "hard exit window"):
                    attempt.apply_effects(effect)
            self.assertEqual("operations-incomplete", self.host.inventory()[0]["status"])
            self.assertFalse((content / "worldgen/vein/new.json").exists())
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "already attempted"):
                attempt.apply_effects(effect)
            self.assertFalse(self.target.exists())

    def test_cancellation_after_effect_write_keeps_completion_unsealed(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                       "expected_sha256": None, "data": b"{}\n"},)
            attempt, manifest = self._attempt(stage, effect)
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            cancelled = False
            original = attempt._apply_effect

            def check_cancelled():
                if cancelled:
                    raise RuntimeError("cancelled")

            def cancel_after_write(*args):
                nonlocal cancelled
                original(*args)
                cancelled = True

            with (patch.object(self.trees, "check_cancelled", side_effect=check_cancelled),
                  patch.object(attempt, "_apply_effect", side_effect=cancel_after_write)):
                with self.assertRaisesRegex(ManagedTreeError, "cancelled"):
                    attempt.apply_effects(effect)
            self.assertEqual(b"{}\n", (content / "worldgen/vein/new.json").read_bytes())
            self.assertFalse((attempt.root / "operations-complete.json").exists())
            self.assertFalse(self.target.exists())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_hard_exit_after_each_effect_write_keeps_unpublished_attempt(self) -> None:
        old_oil = (self.source / "worldgen/fluid/oil.json").read_bytes()
        old_sidecar = (self.source / "worldgen/vein/sidecar.txt").read_bytes()
        cases = (
            ("add", "worldgen/vein/new.json", None, b"{}\n"),
            ("replace", "worldgen/fluid/oil.json", sha256(old_oil).hexdigest(), b"gas\n"),
            ("remove", "worldgen/vein/sidecar.txt", sha256(old_sidecar).hexdigest(), None),
        )
        for action, relative_path, expected_sha256, data in cases:
            with self.subTest(action=action):
                target = self.target.parent / action / "config"
                effect = ({"op": action, "relative_path": relative_path,
                           "expected_sha256": expected_sha256, "data": data},)
                with self.trees.stage("artifacts", "config", requested_path=target) as stage:
                    attempt, manifest = self._attempt(stage, effect)
                    content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
                    child = os.fork()
                    if child == 0:
                        original = attempt._apply_effect

                        def exit_after_write(*args):
                            original(*args)
                            os._exit(73)

                        try:
                            with patch.object(attempt, "_apply_effect", side_effect=exit_after_write):
                                attempt.apply_effects(effect)
                        except BaseException:
                            os._exit(74)
                        os._exit(75)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(73, os.waitstatus_to_exitcode(status))
                    selected = content / relative_path
                    if action == "remove":
                        self.assertFalse(selected.exists())
                    else:
                        self.assertEqual(data, selected.read_bytes())
                    row = next(row for row in self.host.inventory()
                               if row["attempt_id"] == attempt.attempt_id)
                    self.assertEqual("operations-incomplete", row["status"])
                    with self.assertRaisesRegex(OverlayEnvelopeInputError, "already attempted"):
                        attempt.apply_effects(effect)
                    self.assertFalse(target.exists())
        self.assertEqual(old_oil, (self.source / "worldgen/fluid/oil.json").read_bytes())
        self.assertEqual(old_sidecar, (self.source / "worldgen/vein/sidecar.txt").read_bytes())

    def test_completed_effect_stage_is_checked_again_on_inventory(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                       "expected_sha256": None, "data": b"{}\n"},)
            attempt.apply_effects(effect)
            self.assertEqual("operations-complete", self.host.inventory()[0]["status"])
            (content / "worldgen/vein/new.json").write_bytes(b"changed\n")
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "operation completion changed"):
                self.host.inventory()
            self.assertFalse(self.target.exists())

    def test_core_writes_validated_siblings_into_only_the_unpublished_envelope(self) -> None:
        effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                   "expected_sha256": None, "data": b"{}\n"},)
        inventory_bytes = b'{"inventory":"reviewed"}\n'
        materialization_bytes = b'{"materialization":"reviewed"}\n'
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage, effect)
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            attempt.apply_effects(effect)

            def validate_output(selected, plan_chunks):
                self.assertEqual(content, selected)
                self.assertEqual([b'{"operations":["replace"]}'], list(plan_chunks))
                self.assertEqual(b"{}\n", (selected / "worldgen/vein/new.json").read_bytes())
                return inventory_bytes, materialization_bytes

            siblings = attempt.write_siblings(
                inventory_bytes=inventory_bytes, materialization_bytes=materialization_bytes,
                validate_output=validate_output,
            )
            self.assertEqual((stage.path / "gtceu-worldgen-inventory-v1.json",
                              stage.path / "overlay-materialization-v1.json"), siblings)
            self.assertEqual(inventory_bytes, siblings[0].read_bytes())
            self.assertEqual(materialization_bytes, siblings[1].read_bytes())
            self.assertEqual("siblings-complete", self.host.inventory()[0]["status"])
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "already attempted"):
                attempt.write_siblings(
                    inventory_bytes=inventory_bytes, materialization_bytes=materialization_bytes,
                    validate_output=validate_output,
                )
            self.assertFalse(self.target.exists())
        self.assertEqual("siblings-complete", self.host.inventory()[0]["status"])

    def test_changed_or_unvalidated_sibling_bytes_never_become_a_complete_envelope(self) -> None:
        effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                   "expected_sha256": None, "data": b"{}\n"},)
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage, effect)
            attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            attempt.apply_effects(effect)
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "differ from validated output"):
                attempt.write_siblings(
                    inventory_bytes=b"different\n", materialization_bytes=b"receipt\n",
                    validate_output=lambda _content, _chunks: (b"expected\n", b"receipt\n"),
                )
            self.assertFalse((attempt.root / "siblings-attempted.json").exists())
            siblings = attempt.write_siblings(
                inventory_bytes=b"inventory\n", materialization_bytes=b"receipt\n",
                validate_output=lambda _content, _chunks: (b"inventory\n", b"receipt\n"),
            )
            siblings[1].write_bytes(b"changed\n")
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "sibling file changed"):
                self.host.inventory()
            self.assertFalse(self.target.exists())

    def test_output_validator_cannot_change_operated_stage_before_sibling_attempt(self) -> None:
        effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                   "expected_sha256": None, "data": b"{}\n"},)
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage, effect)
            content = attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            attempt.apply_effects(effect)

            def changed_output(_selected, _chunks):
                (content / "worldgen/vein/sidecar.txt").write_bytes(b"changed\n")
                return b"inventory\n", b"receipt\n"

            with self.assertRaisesRegex(OverlayEnvelopeInputError, "operation completion changed"):
                attempt.write_siblings(
                    inventory_bytes=b"inventory\n", materialization_bytes=b"receipt\n",
                    validate_output=changed_output,
                )
            self.assertFalse((attempt.root / "siblings-attempted.json").exists())
            self.assertFalse(self.target.exists())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_hard_exit_after_either_sibling_write_retains_unpublished_attempt(self) -> None:
        effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                   "expected_sha256": None, "data": b"{}\n"},)
        for exit_after in (1, 2):
            with self.subTest(exit_after=exit_after):
                target = self.target.parent / f"sibling-{exit_after}" / "config"
                with self.trees.stage("artifacts", "config", requested_path=target) as stage:
                    attempt, manifest = self._attempt(stage, effect)
                    attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
                    attempt.apply_effects(effect)
                    child = os.fork()
                    if child == 0:
                        original = attempt._write_sibling
                        written = 0

                        def exit_after_write(*args):
                            nonlocal written
                            original(*args)
                            written += 1
                            if written == exit_after:
                                os._exit(73)

                        try:
                            with patch.object(attempt, "_write_sibling", side_effect=exit_after_write):
                                attempt.write_siblings(
                                    inventory_bytes=b"inventory\n", materialization_bytes=b"receipt\n",
                                    validate_output=lambda _content, _chunks: (b"inventory\n", b"receipt\n"),
                                )
                        except BaseException:
                            os._exit(74)
                        os._exit(75)
                    _pid, status = os.waitpid(child, 0)
                    self.assertEqual(73, os.waitstatus_to_exitcode(status))
                    self.assertTrue((stage.path / "gtceu-worldgen-inventory-v1.json").exists())
                    self.assertEqual(exit_after == 2,
                                     (stage.path / "overlay-materialization-v1.json").exists())
                    row = next(row for row in self.host.inventory()
                               if row["attempt_id"] == attempt.attempt_id)
                    self.assertEqual("siblings-incomplete", row["status"])
                    with self.assertRaisesRegex(OverlayEnvelopeInputError, "already attempted"):
                        attempt.write_siblings(
                            inventory_bytes=b"inventory\n", materialization_bytes=b"receipt\n",
                            validate_output=lambda _content, _chunks: (b"inventory\n", b"receipt\n"),
                        )
                    self.assertFalse(target.exists())

    def test_exact_core_tree_publishes_one_verified_envelope(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, validate_output = self._ready_envelope(stage)
            reference = attempt.publish_envelope(validate_output=validate_output)
            self.assertEqual(stage.tree_id, reference.tree_id)
            self.assertEqual(attempt.attempt_id, reference.domain_id)
            self.assertEqual(self.target, reference.path)
            self.assertTrue(self.target.is_dir())
            self.assertFalse(stage.path.exists())
            self.assertEqual("published", self.host.inventory()[0]["status"])
        reopened = CoreOverlayEnvelopeInputs(
            workspace=self.workspace, configuration_home=self.configuration_home,
            owner_id="crucible",
        )
        self.assertEqual("published", reopened.inventory()[0]["status"])
        self.assertEqual("published", ResourceCatalog(self.configuration_home).inventory(
            workspace=self.workspace)["overlay_envelopes"][0]["status"])
        with self.assertRaisesRegex(OverlayEnvelopeInputError, "already attempted"):
            attempt.publish_envelope(validate_output=validate_output)

    def test_changed_published_sibling_blocks_reconciliation_and_inventory(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, validate_output = self._ready_envelope(stage)
            attempt.publish_envelope(validate_output=validate_output)
        (self.target / "overlay-materialization-v1.json").write_bytes(b"changed\n")
        with self.assertRaisesRegex(OverlayEnvelopeInputError, "published overlay target changed"):
            self.host.inventory()
        with self.assertRaises(OverlayEnvelopeInputError):
            self.host.reconcile_publication(attempt_id=attempt.attempt_id, trees=self.trees)

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_publication_crash_windows_reconcile_only_exact_core_intents(self) -> None:
        for phase, expected_status in (
            ("before-intent", "publication-incomplete"),
            ("before-rename", "publication-prepared"),
            ("after-rename-before-commit", "published-uncommitted"),
            ("after-rename", "published-unreconciled"),
        ):
            with self.subTest(phase=phase):
                target = self.target.parent / phase / "config"
                child = os.fork()
                if child == 0:
                    try:
                        with self.trees.stage("artifacts", "config", requested_path=target) as stage:
                            attempt, validate_output = self._ready_envelope(stage)
                            if phase == "before-intent":
                                with patch.object(stage, "publish", side_effect=lambda **_kwargs: os._exit(73)):
                                    attempt.publish_envelope(validate_output=validate_output)
                            elif phase == "before-rename":
                                with patch("workbench_core.managed_trees._rename_no_replace",
                                           side_effect=lambda *_args, **_kwargs: os._exit(73)):
                                    attempt.publish_envelope(validate_output=validate_output)
                            elif phase == "after-rename-before-commit":
                                from workbench_core import managed_trees as managed_tree_module
                                original_rename = managed_tree_module._rename_no_replace

                                def exit_after_rename(*args, **kwargs):
                                    original_rename(*args, **kwargs)
                                    os._exit(73)

                                with patch("workbench_core.managed_trees._rename_no_replace",
                                           side_effect=exit_after_rename):
                                    attempt.publish_envelope(validate_output=validate_output)
                            else:
                                original = stage.publish

                                def exit_after_publish(**kwargs):
                                    original(**kwargs)
                                    os._exit(73)

                                with patch.object(stage, "publish", side_effect=exit_after_publish):
                                    attempt.publish_envelope(validate_output=validate_output)
                    except BaseException:
                        os._exit(74)
                    os._exit(75)
                _pid, status = os.waitpid(child, 0)
                self.assertEqual(73, os.waitstatus_to_exitcode(status))
                row = next(row for row in self.host.inventory() if row["target"] == str(target))
                self.assertEqual(expected_status, row["status"])
                if phase == "before-intent":
                    with self.assertRaisesRegex(OverlayEnvelopeInputError, "not ready"):
                        self.host.reconcile_publication(attempt_id=row["attempt_id"], trees=self.trees)
                    self.assertFalse(target.exists())
                    self.assertTrue(Path(row["stage_path"]).exists())
                else:
                    reference = self.host.reconcile_publication(
                        attempt_id=row["attempt_id"], trees=self.trees,
                    )
                    self.assertEqual(target, reference.path)
                    self.assertEqual("published", next(
                        item for item in self.host.inventory() if item["attempt_id"] == row["attempt_id"]
                    )["status"])

    def test_occupied_target_at_no_replace_rename_remains_a_publication_conflict(self) -> None:
        count = 0
        with self.assertRaises(ManagedTreeError):
            with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
                attempt, _validate_output = self._ready_envelope(stage)

                def collide_during_publish(_content, _chunks):
                    nonlocal count
                    count += 1
                    if count == 2:
                        self.target.mkdir()
                    return b'{"inventory":"reviewed"}\n', b'{"materialization":"reviewed"}\n'

                attempt.publish_envelope(validate_output=collide_during_publish)
        row = self.host.inventory()[0]
        self.assertEqual("publication-collision", row["status"])
        self.assertTrue(Path(row["stage_path"]).exists())
        self.assertTrue(self.target.exists())
        with self.assertRaisesRegex(OverlayEnvelopeInputError, "not ready"):
            self.host.reconcile_publication(attempt_id=row["attempt_id"], trees=self.trees)

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_prepared_stage_with_same_bytes_but_new_inode_cannot_reconcile(self) -> None:
        child = os.fork()
        if child == 0:
            try:
                with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
                    attempt, validate_output = self._ready_envelope(stage)
                    with patch("workbench_core.managed_trees._rename_no_replace",
                               side_effect=lambda *_args, **_kwargs: os._exit(73)):
                        attempt.publish_envelope(validate_output=validate_output)
            except BaseException:
                os._exit(74)
            os._exit(75)
        _pid, status = os.waitpid(child, 0)
        self.assertEqual(73, os.waitstatus_to_exitcode(status))
        row = self.host.inventory()[0]
        self.assertEqual("publication-prepared", row["status"])
        original = Path(row["stage_path"])
        retained = original.with_name("retained-payload")
        original.rename(retained)
        shutil.copytree(retained, original)
        with self.assertRaisesRegex(OverlayEnvelopeInputError, "stage identity changed"):
            self.host.inventory()
        with self.assertRaises(OverlayEnvelopeInputError):
            self.host.reconcile_publication(attempt_id=row["attempt_id"], trees=self.trees)
        self.assertFalse(self.target.exists())

    def test_changed_effect_record_is_reported_as_changed_attempt(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, _manifest = self._attempt(stage)
            path = attempt.root / "effects" / "0000000000000000.json"
            record = json.loads(path.read_bytes())
            record["effect"]["op"] = ["add"]
            path.write_bytes(self._canonical(record) + b"\n")
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "effect metadata changed"):
                self.host.inventory()
            self.assertFalse(stage.path.exists())

    def test_changed_sealed_capacity_blocks_copy_before_payload(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            path = attempt.root / "effect-seal.json"
            record = json.loads(path.read_bytes())
            record["capacity"]["reserved_bytes"] -= 1
            path.write_bytes(self._canonical(record) + b"\n")
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "sealed overlay capacity changed"):
                attempt.copy_source(verify_source=lambda _selected, _chunks: manifest)
            self.assertFalse((attempt.root / "copy-attempted.json").exists())
            self.assertFalse(stage.path.exists())

    def test_add_conflicting_with_copied_directory_refuses_before_copy(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, _manifest = self._attempt(stage)
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "add precondition"):
                attempt.preflight_v3(({
                    "op": "add", "relative_path": "worldgen/vein",
                    "expected_sha256": None, "data": b"{}\n",
                },))
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "add precondition"):
                attempt.preflight_v3(({
                    "op": "add", "relative_path": "dimensions.json/child.json",
                    "expected_sha256": None, "data": b"{}\n",
                },))
            self.assertFalse((attempt.root / "copy-attempted.json").exists())
            self.assertFalse(stage.path.exists())

    def test_read_only_copied_parent_refuses_effects_before_copy(self) -> None:
        vein = self.source / "worldgen/vein"
        os.chmod(vein, 0o550)
        self.addCleanup(os.chmod, vein, 0o750)
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            manifest, chunk = self._inventory()
            attempt = self.host.start(stage=stage, source_root=self.source,
                                      plan_chunks=(b'{"operations":["add"]}',))
            attempt.emit_chunk(0, chunk)
            attempt.seal_inputs(manifest, validate_inventory=lambda selected, chunks: (
                selected if list(chunks) == [chunk] else None
            ))
            effect = ({"op": "add", "relative_path": "worldgen/vein/new.json",
                       "expected_sha256": None, "data": b"{}\n"},)
            with self.assertRaisesRegex(OverlayEnvelopeInputError, "not writable") as raised:
                attempt.seal_effects(effect, validate_plan=lambda _chunks: effect)
            self.assertEqual("overlay.unsupported", raised.exception.code)
            self.assertFalse((attempt.root / "copy-attempted.json").exists())
            self.assertFalse(stage.path.exists())

    def test_v3_preflight_checks_copied_tree_and_planned_add_before_copy(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, manifest = self._attempt(stage)
            effect = {"op": "add", "relative_path": "worldgen/vein/new/sub/ore.json",
                      "expected_sha256": None, "data": b"{}\n"}
            capacity = attempt.preflight_v3((effect,))
            self.assertEqual(manifest["inventory_id"], capacity["inventory_id"])
            self.assertEqual("posix-exact-v1", capacity["inventory_policy"])
            self.assertEqual(6, capacity["maximum_files"])
            self.assertEqual(6, capacity["maximum_directories"])
            self.assertEqual(1, capacity["effect_count"])
            with patch("workbench_core.overlay_envelope_inputs.MAX_FILES", 5):
                with self.assertRaisesRegex(OverlayEnvelopeInputError, "V3 bound") as raised:
                    attempt.preflight_v3((effect,))
                self.assertEqual("overlay.unsupported", raised.exception.code)
            self.assertFalse((attempt.root / "copy-attempted.json").exists())
            self.assertFalse(stage.path.exists())

    def test_v3_preflight_refuses_oversized_source_row_before_copy(self) -> None:
        with self.trees.stage("artifacts", "config", requested_path=self.target) as stage:
            attempt, _manifest = self._attempt(stage)
            with patch("workbench_core.overlay_envelope_inputs.MAX_FILE_BYTES", 3):
                with self.assertRaisesRegex(OverlayEnvelopeInputError, "source file exceeds") as raised:
                    attempt.preflight_v3(({"op": "add", "relative_path": "worldgen/vein/ore.json",
                                           "expected_sha256": None, "data": b"{}\n"},))
                self.assertEqual("overlay.unsupported", raised.exception.code)
            self.assertFalse((attempt.root / "copy-attempted.json").exists())

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
    effects = ({'op': 'add', 'relative_path': 'worldgen/vein/new.json',
                'expected_sha256': None, 'data': b'{}\\n'},)
    attempt.seal_effects(effects, validate_plan=lambda _chunks: effects)
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
