from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_gtceu_worldgen import (  # noqa: E402
    GtceuWorldgenValidationError,
    build_gtceu_worldgen_inventory,
    materialize_overlay,
    parse_overlay_materialization,
    parse_gtceu_worldgen_inventory,
    build_gtceu_overlay_copy_inventory,
    planned_overlay_effects,
    build_overlay_materialization,
    overlay_inventory_bytes,
    overlay_materialization_bytes,
    review_overlay_sibling_bytes,
    parse_gtceu_overlay_copy_inventory,
    verify_gtceu_overlay_copy_source,
)
from workbench_crucible_gtceu_worldgen.inventory import (  # noqa: E402
    REQUIRED_API_CLASSES,
    canonical_json_bytes,
)
from workbench_crucible_gtceu_worldgen import transport_inventory  # noqa: E402


class GtceuWorldgenInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jar = self.root / "gregtech.jar"
        with ZipFile(self.jar, "w") as archive:
            archive.writestr(
                "mcmod.info",
                json.dumps(
                    [
                        {
                            "modid": "gregtech",
                            "version": "2.8.10-beta",
                            "mcversion": "1.12.2",
                        }
                    ]
                ),
            )
            for name in REQUIRED_API_CLASSES:
                archive.writestr(name, b"fixture-api")

        self.config = self.root / "config/gregtech"
        self.write_json(
            self.config / "dimensions.json",
            {"dims": [{"dimID": 0, "dimName": "Overworld"}]},
        )
        self.write_json(
            self.config / "worldgen_extracted.json",
            {"fluidVersion": 2, "veinVersion": 1},
        )
        self.ore_path = self.config / "worldgen/vein/overworld/fluorite.json"
        self.write_json(self.ore_path, self.ore_definition())
        self.write_json(
            self.config / "worldgen/fluid/overworld/oil.json",
            {
                "weight": 20,
                "name": "Oil",
                "yield": {"min": 100, "max": 200},
                "depletion": {"amount": 1, "chance": 5, "depleted_yield": 10},
                "fluid": "oil",
            },
        )
        self.package = self.root / "observed.strataview"
        self.write_json(
            self.package,
            {
                "schema": "strata.strataview.package.v1",
                "chunkWindow": {
                    "minChunkX": 0,
                    "minChunkZ": 0,
                    "chunkSizeX": 1,
                    "chunkSizeZ": 1,
                    "haloChunks": 1,
                },
                "resourceStats": {
                    "blockStateCounts": [
                        {
                            "blockState": "gregtech:ore_fluorite_0[stone_type=stone]",
                            "count": 42,
                        }
                    ]
                },
            },
        )

    @staticmethod
    def ore_definition() -> dict:
        return {
            "weight": 80,
            "density": 0.75,
            "min_height": 0,
            "max_height": 80,
            "dimension_filter": ["dimension_id:0"],
            "generator": {"type": "layered", "radius": [20, 20]},
            "filler": {
                "type": "layered",
                "values": [
                    {"primary": "ore:fluorite"},
                    {"secondary": "ore:fluorite"},
                    {"between": "ore:fluorite"},
                    {"sporadic": "ore:sphalerite"},
                ],
            },
            "vein_populator": {"type": "surface_rock", "material": "fluorite"},
        }

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def build(self) -> dict:
        return build_gtceu_worldgen_inventory(
            jar_path=self.jar,
            config_root=self.config,
            strataview_path=self.package,
        )

    def build_copy_inventory(self, source_inventory: dict) -> tuple[dict, list[bytes]]:
        chunks: dict[int, bytes] = {}

        def emit(index: int, raw: bytes) -> None:
            self.assertNotIn(index, chunks)
            chunks[index] = raw

        manifest = build_gtceu_overlay_copy_inventory(
            config_root=self.config, source_inventory=source_inventory,
            emit_chunk=emit,
        )
        return manifest, [chunks[index] for index in range(len(chunks))]

    def add_plan(self, source_inventory: dict) -> dict:
        return {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": source_inventory["inventory_id"],
            "operations": [{
                "op": "add", "kind": "ore",
                "relative_path": "worldgen/vein/overworld/new.json",
                "definition": self.ore_definition(),
            }],
        }

    def core_context(self) -> tuple[Path, Path, Path]:
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        configuration_home = self.root / "settings"
        output = workspace / ".workbench/overlays/selected/config/gregtech"
        return workspace, configuration_home, output

    def test_exact_inventory_quantifies_and_correlates_without_causality(self) -> None:
        report = self.build()
        self.assertEqual(report["summary"]["definition_count"], 2)
        self.assertEqual(report["summary"]["ore_definition_count"], 1)
        self.assertEqual(report["summary"]["fluid_definition_count"], 1)
        self.assertIn("fluorite", report["summary"]["material_tokens"])
        self.assertEqual(report["observation"]["observed_gtceu_block_count"], 42)
        self.assertEqual(
            report["observation"]["states"][0]["candidate_definition_paths"],
            ["worldgen/vein/overworld/fluorite.json"],
        )
        self.assertFalse(report["boundaries"]["observed_state_proves_deposit_cause"])
        self.assertEqual(parse_gtceu_worldgen_inventory(report), report)

    def test_version_and_identity_drift_fail_closed(self) -> None:
        report = self.build()
        changed = deepcopy(report)
        changed["summary"]["definition_count"] = 3
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "ID drift"):
            parse_gtceu_worldgen_inventory(changed)

        with ZipFile(self.jar, "w") as archive:
            archive.writestr(
                "mcmod.info",
                json.dumps(
                    [{"modid": "gregtech", "version": "2.9.0", "mcversion": "1.12.2"}]
                ),
            )
            for name in REQUIRED_API_CLASSES:
                archive.writestr(name, b"fixture-api")
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "expected GTCEu"):
            self.build()

    def test_checked_overlay_changes_only_a_fresh_materialization(self) -> None:
        report = self.build()
        definition = self.ore_definition()
        definition["weight"] = 81
        ore_binding = next(
            row
            for row in report["configuration"]["files"]
            if row["relative_path"] == "worldgen/vein/overworld/fluorite.json"
        )
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": report["inventory_id"],
            "operations": [
                {
                    "op": "replace",
                    "kind": "ore",
                    "relative_path": "worldgen/vein/overworld/fluorite.json",
                    "expected_sha256": ore_binding["sha256"],
                    "definition": definition,
                }
            ],
        }
        output = self.root / "materialized/config/gregtech"
        materialization = materialize_overlay(
            jar_path=self.jar,
            config_root=self.config,
            inventory=report,
            plan=plan,
            output_config_root=output,
        )
        effects = planned_overlay_effects(plan=plan, source_inventory=report)
        self.assertEqual(1, len(effects))
        self.assertEqual("replace", effects[0]["op"])
        self.assertEqual(
            (json.dumps(definition, indent=2, sort_keys=True) + "\n").encode(),
            effects[0]["data"],
        )
        self.assertEqual(
            effects[0]["data"], (output / effects[0]["relative_path"]).read_bytes(),
        )
        output_inventory = build_gtceu_worldgen_inventory(
            jar_path=self.jar, config_root=output,
        )
        self.assertEqual(
            materialization,
            build_overlay_materialization(
                source_inventory=report, output_inventory=output_inventory, plan=plan,
            ),
        )
        self.assertEqual(
            overlay_inventory_bytes(output_inventory),
            (output.parent / "gtceu-worldgen-inventory-v1.json").read_bytes(),
        )
        self.assertEqual(
            overlay_materialization_bytes(materialization),
            (json.dumps(materialization, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.assertEqual(json.loads(self.ore_path.read_text())["weight"], 80)
        self.assertEqual(
            json.loads(
                (output / "worldgen/vein/overworld/fluorite.json").read_text()
            )["weight"],
            81,
        )
        self.assertNotEqual(
            materialization["source_inventory_id"],
            materialization["output_inventory_id"],
        )
        self.assertEqual(
            parse_overlay_materialization(materialization),
            materialization,
        )
        changed = deepcopy(materialization)
        changed["operation_count"] = 2
        with self.assertRaisesRegex(
            GtceuWorldgenValidationError,
            "operation count drift",
        ):
            parse_overlay_materialization(changed)

    def test_overlay_rejects_source_drift_before_creating_output(self) -> None:
        report = self.build()
        definition = self.ore_definition()
        definition["weight"] = 81
        binding = next(
            row
            for row in report["configuration"]["files"]
            if row["relative_path"] == "worldgen/vein/overworld/fluorite.json"
        )
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": report["inventory_id"],
            "operations": [
                {
                    "op": "replace",
                    "kind": "ore",
                    "relative_path": "worldgen/vein/overworld/fluorite.json",
                    "expected_sha256": binding["sha256"],
                    "definition": definition,
                }
            ],
        }
        changed_source = self.ore_definition()
        changed_source["weight"] = 79
        self.write_json(self.ore_path, changed_source)
        output = self.root / "materialized/config/gregtech"
        with self.assertRaisesRegex(
            GtceuWorldgenValidationError,
            "configuration drifted",
        ):
            materialize_overlay(
                jar_path=self.jar,
                config_root=self.config,
                inventory=report,
                plan=plan,
                output_config_root=output,
            )
        self.assertFalse(output.exists())

    def test_overlay_rejects_unknown_or_action_inappropriate_fields(self) -> None:
        report = self.build()
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": report["inventory_id"],
            "operations": [
                {
                    "op": "add",
                    "kind": "ore",
                    "relative_path": "worldgen/vein/overworld/new.json",
                    "expected_sha256": "0" * 64,
                    "definition": self.ore_definition(),
                }
            ],
        }
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "fields drift"):
            materialize_overlay(
                jar_path=self.jar,
                config_root=self.config,
                inventory=report,
                plan=plan,
                output_config_root=self.root / "materialized/config/gregtech",
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_binds_sidecars_and_keeps_v1_bytes(self) -> None:
        report = self.build()
        v1_bytes = canonical_json_bytes(report)
        sidecar = self.config / "worldgen/vein/overworld/notes.txt"
        sidecar.write_bytes(b"copied sidecar\n")
        manifest, chunks = self.build_copy_inventory(report)
        self.assertEqual(manifest, parse_gtceu_overlay_copy_inventory(manifest, chunks))
        self.assertEqual(
            manifest, verify_gtceu_overlay_copy_source(
                config_root=self.config, source_inventory=report,
                manifest=manifest, chunks=chunks,
                expected_inventory_id=manifest["inventory_id"],
            ),
        )
        rows = [json.loads(line) for chunk in chunks for line in chunk.splitlines()]
        self.assertEqual(
            next(row for row in rows if row["path"].endswith("notes.txt"))["sha256"],
            sha256(b"copied sidecar\n").hexdigest(),
        )
        self.assertEqual(canonical_json_bytes(self.build()), v1_bytes)
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "after selection"):
            verify_gtceu_overlay_copy_source(
                config_root=self.config, source_inventory=report,
                manifest=manifest, chunks=chunks,
                expected_inventory_id="crucible-gtceu-overlay-copy:sha256:" + "0" * 64,
            )
        sidecar.write_bytes(b"changed sidecar\n")
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "copied source changed"):
            verify_gtceu_overlay_copy_source(
                config_root=self.config, source_inventory=report,
                manifest=manifest, chunks=chunks,
                expected_inventory_id=manifest["inventory_id"],
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 Core copy uses Linux pinned handles")
    def test_v2_inventory_streams_into_core_published_overlay(self) -> None:
        try:
            from workbench_core.managed_trees import CoreManagedTrees
            from workbench_core.overlay_envelope_inputs import CoreOverlayEnvelopeInputs
        except ModuleNotFoundError:
            self.skipTest("Core source is unavailable in this standalone Crucible test run")

        report = self.build()
        sidecar = self.config / "worldgen/vein/overworld/notes.txt"
        sidecar.write_bytes(b"selected sidecar\n")
        os.chmod(sidecar, 0o600)
        workspace = self.root / "workspace"
        workspace.mkdir()
        artifacts = workspace / ".workbench"
        configuration_home = self.root / "settings"
        target = artifacts / "overlays" / "selected" / "config"
        trees = CoreManagedTrees(
            workspace=workspace, configuration_home=configuration_home,
            locations={"artifacts": artifacts}, owner_id="crucible",
        )
        host = CoreOverlayEnvelopeInputs(
            workspace=workspace, configuration_home=configuration_home,
            owner_id="crucible",
        )
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1, "target_inventory_id": report["inventory_id"],
            "operations": [{"op": "add", "kind": "ore",
                            "relative_path": "worldgen/vein/overworld/new.json",
                            "definition": self.ore_definition()}],
        }
        effects = planned_overlay_effects(plan=plan, source_inventory=report)
        with trees.stage("artifacts", "config", requested_path=target) as stage:
            attempt = host.start(
                stage=stage, source_root=self.config,
                plan_chunks=(canonical_json_bytes(plan),),
            )
            manifest = build_gtceu_overlay_copy_inventory(
                config_root=self.config, source_inventory=report,
                emit_chunk=attempt.emit_chunk,
            )
            attempt.seal_inputs(manifest, validate_inventory=parse_gtceu_overlay_copy_inventory)
            attempt.seal_effects(
                effects, validate_plan=lambda chunks: planned_overlay_effects(
                    plan=json.loads(b"".join(chunks)), source_inventory=report,
                ),
            )
            copied = attempt.copy_source(verify_source=lambda selected, chunks:
                verify_gtceu_overlay_copy_source(
                    config_root=self.config, source_inventory=report,
                    manifest=selected, chunks=chunks,
                    expected_inventory_id=manifest["inventory_id"],
                ))
            self.assertEqual(b"selected sidecar\n", (copied / "worldgen/vein/overworld/notes.txt").read_bytes())
            self.assertEqual(0o600, (copied / "worldgen/vein/overworld/notes.txt").stat().st_mode & 0o7777)
            attempt.apply_effects(effects)

            def reviewed_siblings(config_root, plan_chunks):
                return review_overlay_sibling_bytes(
                    jar_path=self.jar, staged_config_root=config_root,
                    source_inventory=report, plan=json.loads(b"".join(plan_chunks)),
                )

            inventory_bytes, materialization_bytes = reviewed_siblings(
                copied, (canonical_json_bytes(plan),),
            )
            sibling_paths = attempt.write_siblings(
                inventory_bytes=inventory_bytes, materialization_bytes=materialization_bytes,
                validate_output=reviewed_siblings,
            )
            self.assertEqual(inventory_bytes, sibling_paths[0].read_bytes())
            self.assertEqual(materialization_bytes, sibling_paths[1].read_bytes())
            self.assertIsNone(json.loads(inventory_bytes)["observation"])
            self.assertEqual("siblings-complete", host.inventory()[0]["status"])
            self.assertFalse(target.exists())
            reference = attempt.publish_envelope(validate_output=reviewed_siblings)
            self.assertEqual(target, reference.path)
            self.assertEqual(attempt.attempt_id, reference.domain_id)
            self.assertEqual("published", host.inventory()[0]["status"])
        self.assertEqual(inventory_bytes, (target / "gtceu-worldgen-inventory-v1.json").read_bytes())
        self.assertEqual(materialization_bytes, (target / "overlay-materialization-v1.json").read_bytes())
        self.assertEqual(b"selected sidecar\n", (target / "gregtech/worldgen/vein/overworld/notes.txt").read_bytes())
        self.assertEqual("published", host.inventory()[0]["status"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Core overlay requires Linux")
    def test_v2_cli_publishes_reviews_and_refuses_existing_target(self) -> None:
        report = self.build()
        plan = self.add_plan(report)
        workspace, configuration_home, output = self.core_context()
        inventory_path = self.root / "inventory.json"
        plan_path = self.root / "plan.json"
        inventory_path.write_text(json.dumps(report), encoding="utf-8")
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        tool = ROOT / "modules/crucible/tools/materialize_gtceu_worldgen_overlay_v2.py"
        prefix = [
            sys.executable, str(tool), "--workspace", str(workspace),
            "--configuration-home", str(configuration_home),
        ]
        command = [
            *prefix, "materialize", "--jar", str(self.jar),
            "--config-root", str(self.config), "--inventory", str(inventory_path),
            "--plan", str(plan_path), "--out-config-root", str(output.relative_to(workspace)),
        ]
        first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertIn(f"config: {output}\n", first.stdout)
        first_bytes = {
            path.relative_to(output.parent).as_posix(): path.read_bytes()
            for path in output.parent.rglob("*") if path.is_file()
        }
        self.assertIn("overlay-materialization-v1.json", first_bytes)
        self.assertIn("gtceu-worldgen-inventory-v1.json", first_bytes)
        second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(1, second.returncode)
        self.assertIn("already exists", second.stderr)
        self.assertEqual(first_bytes, {
            path.relative_to(output.parent).as_posix(): path.read_bytes()
            for path in output.parent.rglob("*") if path.is_file()
        })
        review = subprocess.run([*prefix, "review"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, review.returncode, review.stderr)
        attempts = json.loads(review.stdout)["attempts"]
        self.assertEqual(1, len(attempts))
        self.assertEqual("published", attempts[0]["status"])
        self.assertEqual(str(output.parent), attempts[0]["target"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Core overlay requires Linux")
    def test_v2_core_route_refuses_capacity_before_first_copy(self) -> None:
        from workbench_api.managed_trees import managed_trees_scope
        from workbench_api.overlay_envelopes import overlay_envelopes_scope
        from workbench_core.managed_trees import CoreManagedTrees
        from workbench_core.overlay_envelope_inputs import (
            CoreOverlayEnvelopeInputAttempt, CoreOverlayEnvelopeInputs,
            OverlayEnvelopeInputError,
        )
        import workbench_core.overlay_envelope_inputs as envelope
        from workbench_crucible_gtceu_worldgen.core_overlay import materialize_core_overlay

        report = self.build()
        workspace, configuration_home, output = self.core_context()
        trees = CoreManagedTrees(
            workspace=workspace, configuration_home=configuration_home,
            locations={"artifacts": workspace}, owner_id="crucible",
        )
        inputs = CoreOverlayEnvelopeInputs(
            workspace=workspace, configuration_home=configuration_home,
            owner_id="crucible",
        )
        with managed_trees_scope(trees), overlay_envelopes_scope(inputs), patch.object(
            envelope, "MAX_FILES", 1,
        ), patch.object(
            CoreOverlayEnvelopeInputAttempt, "copy_source",
            side_effect=AssertionError("copy must not begin"),
        ):
            with self.assertRaises(OverlayEnvelopeInputError) as error:
                materialize_core_overlay(
                    jar_path=self.jar, config_root=self.config, inventory=report,
                    plan_bytes=canonical_json_bytes(self.add_plan(report)),
                    output_config_root=output,
                )
        self.assertEqual("overlay.unsupported", error.exception.code)
        self.assertFalse(output.parent.exists())
        rows = CoreOverlayEnvelopeInputs(
            workspace=workspace, configuration_home=configuration_home,
            owner_id="crucible",
        ).inventory()
        self.assertEqual(1, len(rows))
        self.assertEqual("input-sealed", rows[0]["status"])
        self.assertFalse(Path(rows[0]["stage_path"]).exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Core overlay requires Linux")
    def test_v2_core_route_reviews_and_reconciles_pre_rename_restart(self) -> None:
        report = self.build()
        workspace, configuration_home, output = self.core_context()
        inventory_path = self.root / "inventory.json"
        plan_path = self.root / "plan.json"
        inventory_path.write_text(json.dumps(report), encoding="utf-8")
        plan_path.write_bytes(canonical_json_bytes(self.add_plan(report)))
        hard_exit = """
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from workbench_api.managed_trees import managed_trees_scope
from workbench_api.overlay_envelopes import overlay_envelopes_scope
from workbench_core import managed_trees
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.overlay_envelope_inputs import CoreOverlayEnvelopeInputs
from workbench_crucible_gtceu_worldgen.core_overlay import materialize_core_overlay

jar, source, inventory, plan, output, workspace, home = map(Path, sys.argv[1:])
trees = CoreManagedTrees(workspace=workspace, configuration_home=home,
                         locations={'artifacts': workspace}, owner_id='crucible')
inputs = CoreOverlayEnvelopeInputs(workspace=workspace, configuration_home=home,
                                   owner_id='crucible')
with managed_trees_scope(trees), overlay_envelopes_scope(inputs), patch.object(
    managed_trees, '_rename_no_replace', side_effect=lambda *a, **k: os._exit(77),
):
    materialize_core_overlay(
        jar_path=jar, config_root=source,
        inventory=json.loads(inventory.read_bytes()), plan_bytes=plan.read_bytes(),
        output_config_root=output,
    )
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "api/src:core/src:modules/crucible/src"
        interrupted = subprocess.run(
            [sys.executable, "-c", hard_exit, str(self.jar), str(self.config),
             str(inventory_path), str(plan_path), str(output), str(workspace),
             str(configuration_home)],
            cwd=ROOT, env=environment, capture_output=True, text=True,
        )
        self.assertEqual(77, interrupted.returncode, interrupted.stderr)
        self.assertFalse(output.parent.exists())
        tool = ROOT / "modules/crucible/tools/materialize_gtceu_worldgen_overlay_v2.py"
        prefix = [
            sys.executable, str(tool), "--workspace", str(workspace),
            "--configuration-home", str(configuration_home),
        ]
        review = subprocess.run([*prefix, "review"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, review.returncode, review.stderr)
        rows = json.loads(review.stdout)["attempts"]
        self.assertEqual("publication-prepared", rows[0]["status"])
        selected_id = rows[0]["attempt_id"]
        reconciled = subprocess.run(
            [*prefix, "reconcile", "--attempt-id", selected_id],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertIn(f"config: {output}\n", reconciled.stdout)
        self.assertIn(f"attempt: {selected_id}\n", reconciled.stdout)
        self.assertTrue(output.is_dir())
        final_review = subprocess.run([*prefix, "review"], cwd=ROOT,
                                      capture_output=True, text=True)
        self.assertEqual(0, final_review.returncode, final_review.stderr)
        self.assertEqual("published", json.loads(final_review.stdout)["attempts"][0]["status"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_refuses_external_symlink(self) -> None:
        report = self.build()
        external = self.root / "external.txt"
        external.write_bytes(b"external bytes\n")
        (self.config / "worldgen/vein/overworld/sidecar.txt").symlink_to(external)
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "symlink or special"):
            self.build_copy_inventory(report)

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_chunks_more_than_4096_entries_and_four_mib(self) -> None:
        report = self.build()
        sidecars = self.config / "worldgen/vein/overworld" / ("x" * 200)
        sidecars.mkdir()
        for index in range(12000):
            (sidecars / f"{index:05d}-{'y' * 120}.txt").write_bytes(b"x")
        manifest, chunks = self.build_copy_inventory(report)
        self.assertGreater(manifest["entry_count"], 4096)
        self.assertGreater(sum(map(len, chunks)), 4 * 1024 * 1024)
        self.assertLess(len(canonical_json_bytes(manifest)), 16 * 1024)
        self.assertGreater(manifest["chunk_count"], 4)
        self.assertTrue(all(0 < len(chunk) <= transport_inventory.CHUNK_BYTES for chunk in chunks))
        self.assertEqual(manifest, parse_gtceu_overlay_copy_inventory(manifest, chunks))
        self.assertEqual(
            manifest, verify_gtceu_overlay_copy_source(
                config_root=self.config, source_inventory=report,
                manifest=manifest, chunks=chunks,
                expected_inventory_id=manifest["inventory_id"],
            ),
        )

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_rejects_chunk_tampering_and_extra_chunk(self) -> None:
        manifest, chunks = self.build_copy_inventory(self.build())
        changed = chunks.copy()
        changed[0] = changed[0].replace(b"worldgen", b"worldgex", 1)
        with self.assertRaises(GtceuWorldgenValidationError):
            parse_gtceu_overlay_copy_inventory(manifest, changed)
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "unexpected chunk"):
            parse_gtceu_overlay_copy_inventory(manifest, [*chunks, b"{}\n"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_refuses_drift_between_scans(self) -> None:
        report = self.build()
        sidecar = self.config / "worldgen/vein/overworld/notes.txt"
        sidecar.write_bytes(b"before\n")
        original = transport_inventory._source_rows
        scans = 0
        emitted: list[bytes] = []

        def racing(root: Path):
            nonlocal scans
            scans += 1
            if scans == 2:
                sidecar.write_bytes(b"after\n")
            yield from original(root)

        with patch.object(transport_inventory, "_source_rows", racing):
            with self.assertRaisesRegex(GtceuWorldgenValidationError, "between inventory scans"):
                build_gtceu_overlay_copy_inventory(
                    config_root=self.config, source_inventory=report,
                    emit_chunk=lambda _index, raw: emitted.append(raw),
                )
        self.assertTrue(emitted)

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_refuses_linked_file_and_redirected_parent(self) -> None:
        report = self.build()
        external = self.root / "external.txt"
        external.write_bytes(b"external bytes\n")
        os.link(external, self.config / "worldgen/vein/overworld/linked.txt")
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "linked or nonregular"):
            self.build_copy_inventory(report)
        (self.config / "worldgen/vein/overworld/linked.txt").unlink()
        redirected = self.root / "redirected"
        redirected.symlink_to(self.config / "worldgen/vein", target_is_directory=True)
        with self.assertRaises(GtceuWorldgenValidationError):
            build_gtceu_overlay_copy_inventory(
                config_root=redirected, source_inventory=report,
                emit_chunk=lambda _index, _raw: None,
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_refuses_portable_name_collision(self) -> None:
        report = self.build()
        folder = self.config / "worldgen/vein/overworld"
        (folder / "Sidecar.txt").write_bytes(b"first")
        (folder / "sidecar.txt").write_bytes(b"second")
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "collide"):
            self.build_copy_inventory(report)

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_refuses_nonportable_names(self) -> None:
        report = self.build()
        folder = self.config / "worldgen/vein/overworld"
        for name in ("CON.txt", "bad\x7f.txt"):
            selected = folder / name
            selected.write_bytes(b"sidecar")
            with self.assertRaisesRegex(GtceuWorldgenValidationError, "nonportable"):
                self.build_copy_inventory(report)
            selected.unlink()

    @unittest.skipUnless(sys.platform.startswith("linux"), "V2 inventory uses Linux mount IDs")
    def test_v2_copy_inventory_refuses_child_mount_identity(self) -> None:
        report = self.build()
        original = transport_inventory._mount_id
        vein_inode = (self.config / "worldgen/vein").stat().st_ino

        def different_mount(descriptor: int) -> int:
            observed = original(descriptor)
            return observed + 1 if os.fstat(descriptor).st_ino == vein_inode else observed

        with patch.object(transport_inventory, "_mount_id", different_mount):
            with self.assertRaisesRegex(GtceuWorldgenValidationError, "mount boundary"):
                self.build_copy_inventory(report)


if __name__ == "__main__":
    unittest.main()
