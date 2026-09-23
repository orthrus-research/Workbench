from __future__ import annotations

from copy import deepcopy
import io
import json
import jsonschema
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/subsurface-studio/src",
    ROOT / "modules/crucible/src",
    ROOT / "modules/workbench-shell/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_crucible_gtceu_subsurface import (  # noqa: E402
    build_gtceu_subsurface_trace,
)
from workbench_crucible_gtceu_worldgen import (  # noqa: E402
    build_gtceu_worldgen_inventory,
)
from workbench_crucible_gtceu_worldgen.inventory import (  # noqa: E402
    REQUIRED_API_CLASSES,
)
from workbench_crucible_gtceu_worldgen_impact import (  # noqa: E402
    build_gtceu_worldgen_impact_inventory,
)
from workbench_crucible_gtceu_worldgen_impact.impact import (  # noqa: E402
    IMPACT_EXACT_CLASSES,
)
from workbench_subsurface_studio.cli import run as cli_run  # noqa: E402
from workbench_subsurface_studio.model import (  # noqa: E402
    SubsurfaceStudioError,
    load_json_file,
    load_profile,
    validate_result,
)
from workbench_subsurface_studio.render import render_report  # noqa: E402
from workbench_subsurface_studio.strata import load_strata_region  # noqa: E402
from workbench_subsurface_studio.studio import (  # noqa: E402
    chunk_map,
    compare,
    definitions,
    explain,
    fluids,
    load_dataset,
    section,
    summary,
)


ORE_STATE = "gregtech:ore_fluorite_0[stone_type=stone]"
SURFACE_STATE = "gregtech:meta_block_surface_rock_0[variant=fluorite]"
STONE_STATE = "minecraft:stone[variant=stone]"
AIR_STATE = "minecraft:air"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _counter_rows(counts: dict[str, int]) -> list[dict[str, object]]:
    return [
        {"blockState": key, "count": value}
        for key, value in sorted(counts.items())
        if value > 0
    ]


def _profile() -> dict:
    return {
        "format": "workbench-subsurface-pack-profile-v1",
        "schema_version": 1,
        "profile_id": "fixture-gtceu-subsurface-v1",
        "pack_profile": "fixture-pack",
        "adapter": {
            "inventory_profile_id": "gtceu-1.12.2-2.8.10-worldgen-v1",
            "impact_profile_id": "gtceu-1.12.2-2.8.10-worldgen-impact-v2",
            "trace_profile_id": "gtceu-1.12.2-2.8.10-subsurface-trace-v1",
        },
        "defaults": {
            "inventory": "inventory.json",
            "impact": "impact.json",
            "manifest": "strata/manifest.json",
        },
        "dimension_semantics": [
            {
                "dimension_id": 0,
                "aliases": ["dimension_id:0", "name:overworld"],
                "surface_world": True,
            }
        ],
        "state_semantics": {
            "ore_prefix": "gregtech:ore_",
            "surface_rock_prefix": "gregtech:meta_block_surface_rock_",
            "air_states": ["minecraft:air", "minecraft:cave_air", "minecraft:void_air"],
            "fluid_prefixes": ["minecraft:water", "minecraft:lava"],
            "host_properties": ["stone_type", "variant"],
            "known_host_variants": ["stone"],
        },
        "grid_semantics": {
            "ore_grid_chunks": 3,
            "consulted_grid_radius": 1,
            "bedrock_fluid_grid_chunks": 8,
        },
        "boundaries": {
            "atlas_remains_causal_authority": True,
            "bedrock_fluids_are_virtual_cells": True,
            "profile_is_pack_specific": True,
            "strata_remains_final_state_authority": True,
        },
    }


def _ore_definition() -> dict:
    return {
        "weight": 80,
        "density": 1,
        "min_height": 0,
        "max_height": 80,
        "dimension_filter": ["dimension_id:0"],
        "generator": {"type": "layered", "radius": [4, 4]},
        "filler": {
            "type": "layered",
            "values": [
                {"primary": "ore:fluorite"},
                {"secondary": "ore:fluorite"},
                {"between": "ore:fluorite"},
                {"sporadic": "ore:fluorite"},
            ],
        },
        "vein_populator": {"type": "surface_rock", "material": "fluorite"},
    }


def _build_config(root: Path) -> tuple[Path, Path, Path]:
    runtime = root / "runtime"
    jar = runtime / "mods/gregtech.jar"
    jar.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(jar, "w") as archive:
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
        for name in sorted(set(REQUIRED_API_CLASSES) | set(IMPACT_EXACT_CLASSES)):
            archive.writestr(name, b"fixture-gtceu-class")
        archive.writestr(
            "gregtech/api/worldgen/populator/SurfaceRockPopulator.class",
            b"fixture-surface-rock",
        )
    config = runtime / "config/gregtech"
    _write_json(
        config / "dimensions.json",
        {"dims": [{"dimID": 0, "dimName": "Overworld"}]},
    )
    _write_json(config / "worldgen_extracted.json", {"fluidVersion": 2, "veinVersion": 1})
    _write_json(config / "worldgen/vein/overworld/fluorite.json", _ore_definition())
    _write_json(
        config / "worldgen/fluid/overworld/oil.json",
        {
            "weight": 20,
            "name": "Fixture Oil",
            "yield": {"min": 100, "max": 200},
            "depletion": {"amount": 1, "chance": 5, "depleted_yield": 10},
            "fluid": "oil",
            "dimension_filter": ["dimension_id:0"],
        },
    )
    (config / "gregtech.cfg").write_text(
        (
            '"worldgen options" {\n'
            " B:addLoot=true\n"
            " I:additionalVeinsInSection=4\n"
            " B:allUniqueStoneTypes=false\n"
            " B:disableRubberTreeGeneration=false\n"
            " B:disableVanillaOres=true\n"
            " B:generateVeinsInCenterOfChunk=true\n"
            " B:increaseDungeonLoot=true\n"
            " I:minVeinsInSection=2\n"
            " D:rubberTreeRateIncrease=3.0\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    return runtime, jar, config


def _build_strata(
    root: Path,
    *,
    ore_count: int,
    world_seed: int,
) -> Path:
    strata = root / "strata"
    ore_positions = [(1, 4 + offset, 1) for offset in range(ore_count)]
    cave_position = (2, 4, 1)
    surface_position = (3, 15, 3)
    indices = [1] * 4096
    for local_x, y, local_z in ore_positions:
        indices[(y % 16) * 256 + local_z * 16 + local_x] = 2
    indices[cave_position[1] * 256 + cave_position[2] * 16 + cave_position[0]] = 0
    indices[
        surface_position[1] * 256
        + surface_position[2] * 16
        + surface_position[0]
    ] = 3
    non_air = sum(value != 0 for value in indices)
    state_counts = {
        STONE_STATE: non_air - ore_count - 1,
        ORE_STATE: ore_count,
        SURFACE_STATE: 1,
    }
    fluid_cell = {
        "veinX": 0,
        "veinZ": 0,
        "queryChunkX": 0,
        "queryChunkZ": 0,
        "biome": "minecraft:plains",
        "totalWeight": 20,
        "depositName": "overworld/oil.json",
        "assignedName": "Fixture Oil",
        "fluid": "oil",
        "fluidYield": 150,
        "operationsRemaining": 100,
        "depletedYield": 10,
        "baseWeight": 20,
        "biomeWeight": 0,
    }
    faces = [1, 4, 1, 0, 0, 0] if ore_count else []
    tile = {
        "schema": "strata.strataview.tile.v2",
        "generatedAt": "fixture",
        "tileId": "tile-0-0",
        "parentSchema": "strata.strataview.region-manifest.v2",
        "sourcePackage": "fixture.strataview",
        "tileWindow": {
            "minChunkX": 0,
            "minChunkZ": 0,
            "chunkSizeX": 1,
            "chunkSizeZ": 1,
            "haloChunks": 0,
        },
        "blockPaletteRef": "manifest.blockPalette",
        "voxelPaletteRef": "manifest.voxelMap.palette",
        "heightmaps": {},
        "biomeMap": {},
        "denseSections": [
            {
                "chunkX": 0,
                "chunkZ": 0,
                "ySection": 0,
                "nonAirCount": non_air,
                "indices": indices,
            }
        ],
        "terrainMeshes": [
            {"blockState": ORE_STATE, "faceCount": len(faces) // 6, "faces": faces}
        ],
        "voxelMap": {
            "voxelEncoding": "localX,y,localZ,paletteIndex",
            "voxelCount": ore_count + 1,
            "chunks": [
                {
                    "chunkX": 0,
                    "chunkZ": 0,
                    "voxelCount": ore_count + 1,
                    "voxels": [
                        *[[x, y, z, 0] for x, y, z in ore_positions],
                        [surface_position[0], surface_position[1], surface_position[2], 1],
                    ],
                }
            ],
        },
        "fluidCells": [fluid_cell],
        "tileSummary": {
            "denseSections": 1,
            "nonAirBlocks": non_air,
            "resourceVoxels": ore_count + 1,
            "terrainMeshes": 1,
            "terrainFaces": len(faces) // 6,
            "blockStateCounts": _counter_rows(state_counts),
            "resourceBlockStateCounts": _counter_rows(
                {ORE_STATE: ore_count, SURFACE_STATE: 1}
            ),
            "fluidCells": 1,
        },
    }
    tile_path = strata / "tiles/tile-0-0.json"
    _write_json(tile_path, tile)
    height_row = {
        "chunkX": 0,
        "chunkZ": 0,
        "minY": 16,
        "maxY": 16,
        "values": [16] * 256,
    }
    biome_row = {
        "chunkX": 0,
        "chunkZ": 0,
        "biomePalette": ["1:minecraft:plains"],
        "biomeIds": [1] * 256,
        "biomes": [0] * 256,
    }
    manifest = {
        "schema": "strata.strataview.region-manifest.v2",
        "generatedAt": "fixture",
        "sourcePackage": "fixture.strataview",
        "packageSchema": "strata.strataview.package.v1",
        "tileSizeChunks": 1,
        "source": {},
        "dimensionId": 0,
        "providerName": "fixture-provider",
        "worldSeed": world_seed,
        "terrainType": "DEFAULT",
        "chunkGeneratorClass": "fixture.ChunkGenerator",
        "chunkWindow": {
            "minChunkX": 0,
            "minChunkZ": 0,
            "chunkSizeX": 1,
            "chunkSizeZ": 1,
            "haloChunks": 1,
        },
        "blockPalette": [AIR_STATE, STONE_STATE, ORE_STATE, SURFACE_STATE],
        "voxelMap": {
            "palette": [ORE_STATE, SURFACE_STATE],
            "voxelCount": ore_count + 1,
        },
        "heightmaps": {},
        "biomeMap": {},
        "fluidCells": [fluid_cell],
        "blockCounts": _counter_rows({ORE_STATE: ore_count, SURFACE_STATE: 1}),
        "denseStats": {
            "denseSections": 1,
            "nonAirBlocks": non_air,
            "exposedFaces": len(faces) // 6,
        },
        "overviewStats": {
            "blockStateCounts": _counter_rows(state_counts),
            "resourceBlockStateCounts": _counter_rows(
                {ORE_STATE: ore_count, SURFACE_STATE: 1}
            ),
        },
        "tiles": [
            {
                "tileId": "tile-0-0",
                "path": "tiles/tile-0-0.json",
                "tileWindow": tile["tileWindow"],
                "summary": {},
            }
        ],
        "surfacePreview": {
            "mode": "derived-landform-and-exact-surface-v1",
            "exactHeightmaps": {"chunks": [height_row]},
            "biomeMap": {"chunks": [biome_row]},
        },
    }
    manifest_path = strata / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _build_fixture(
    root: Path,
    *,
    ore_count: int = 1,
    world_seed: int = 42,
    with_trace: bool = False,
) -> dict[str, Path]:
    runtime, jar, config = _build_config(root)
    profile_path = root / "profile.json"
    _write_json(profile_path, _profile())
    manifest_path = _build_strata(root, ore_count=ore_count, world_seed=world_seed)
    package_path = root / "observation.strataview"
    _write_json(
        package_path,
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
                    {"blockState": ORE_STATE, "count": ore_count},
                    {"blockState": SURFACE_STATE, "count": 1},
                ]
            },
        },
    )
    inventory = build_gtceu_worldgen_inventory(
        jar_path=jar,
        config_root=config,
        strataview_path=package_path,
    )
    inventory_path = root / "inventory.json"
    _write_json(inventory_path, inventory)
    impact = build_gtceu_worldgen_impact_inventory(
        jar_path=jar,
        config_root=config,
        runtime_root=runtime,
    )
    impact_path = root / "impact.json"
    _write_json(impact_path, impact)
    trace_path = root / "trace.json"
    if with_trace:
        trace = build_gtceu_subsurface_trace(
            adapter_profile={
                "id": "gtceu-1.12.2-2.8.10-subsurface-trace-v1",
                "inventory_id": inventory["inventory_id"],
                "impact_inventory_id": impact["inventory_id"],
            },
            capture={
                "run_id": "fixture-run",
                "state": "complete",
                "runtime_artifact_set_sha256": "a" * 64,
                "world_seed": world_seed,
                "dimension_id": 0,
                "chunk_window": {
                    "min_chunk_x": 0,
                    "min_chunk_z": 0,
                    "chunk_size_x": 1,
                    "chunk_size_z": 1,
                    "halo_chunks": 1,
                },
            },
            coverage={
                "selected_definitions_complete": True,
                "position_decisions_complete": True,
                "truncated": False,
                "selector": "fixture-all-position-decisions",
            },
            deposits=[
                {
                    "deposit_instance_id": "deposit-1",
                    "definition_path": "worldgen/vein/overworld/fluorite.json",
                    "grid_x": 0,
                    "grid_z": 0,
                    "selection_ordinal": 0,
                    "effective_weight": 80,
                    "priority": 0,
                    "count_as_vein": True,
                    "center": {"x": 1, "y": 4, "z": 1},
                    "bounds": {
                        "min_x": 1,
                        "min_y": 4,
                        "min_z": 1,
                        "max_x": 1,
                        "max_y": 4,
                        "max_z": 1,
                    },
                    "rng": {
                        "algorithm": "fixture-xoshiro",
                        "seed_material_sha256": "b" * 64,
                        "lane": "shape-and-filler",
                    },
                    "cache": {"hit": False, "epoch": "fixture"},
                    "placement": {
                        "candidate_count": 1,
                        "density_rejected_count": 0,
                        "host_rejected_count": 0,
                        "other_rejected_count": 0,
                        "successful_write_count": 1,
                    },
                }
            ],
            decisions=[
                {
                    "decision_id": "decision-1",
                    "deposit_instance_id": "deposit-1",
                    "position": {"x": 1, "y": 4, "z": 1},
                    "outcome": "written",
                    "reason": "host-and-density-accepted",
                    "before_state": STONE_STATE,
                    "after_state": ORE_STATE,
                    "write_chain_id": "chain-1",
                }
            ],
        )
        _write_json(trace_path, trace)
    return {
        "profile": profile_path,
        "inventory": inventory_path,
        "impact": impact_path,
        "manifest": manifest_path,
        "trace": trace_path,
    }


def _dataset(paths: dict[str, Path], *, root: Path, with_trace: bool = False):
    profile, binding = load_profile(paths["profile"])
    return load_dataset(
        root=root,
        profile=profile,
        profile_binding=binding,
        inventory_path=paths["inventory"],
        impact_path=paths["impact"],
        manifest_path=paths["manifest"],
        trace_path=paths["trace"] if with_trace else None,
    )


class SubsurfaceStudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = _build_fixture(self.root / "base")
        self.dataset = _dataset(self.paths, root=self.root)

    def test_summary_map_section_definitions_and_fluids_are_exact(self) -> None:
        report = summary(self.dataset)
        self.assertEqual(report["result"]["final_state"]["ore_block_count"], 1)
        self.assertEqual(report["result"]["final_state"]["surface_indicator_count"], 1)
        self.assertEqual(report["result"]["final_state"]["subsurface_air_count"], 1)
        self.assertEqual(report["result"]["final_state"]["virtual_fluid_cell_count"], 1)
        self.assertEqual(validate_result(report), report)
        result_schema = json.loads(
            (ROOT / "modules/subsurface-studio/schemas/workbench-subsurface-studio-result-v1.schema.json").read_text()
        )
        profile_schema = json.loads(
            (ROOT / "modules/subsurface-studio/schemas/workbench-subsurface-profile-v1.schema.json").read_text()
        )
        jsonschema.Draft202012Validator(result_schema).validate(report)
        jsonschema.Draft202012Validator(profile_schema).validate(_profile())

        mapped = chunk_map(self.dataset, layer="ore-blocks", material="fluorite")
        self.assertEqual(mapped["result"]["cells"][0]["value"], 1)
        indicators = chunk_map(
            self.dataset, layer="surface-indicators", material="fluorite"
        )
        self.assertEqual(indicators["result"]["cells"][0]["value"], 1)
        plane = section(
            self.dataset,
            x=1,
            min_y=3,
            max_y=5,
            min_axis=1,
            max_axis=2,
            material="fluorite",
        )
        self.assertEqual(plane["result"]["cell_count"], 6)
        self.assertIn("ore-focus", {row["category"] for row in plane["result"]["category_counts"]})

        found = definitions(self.dataset, material="fluorite")
        self.assertEqual(found["result"]["definition_count"], 1)
        self.assertEqual(found["result"]["definitions"][0]["dimension_state"], "eligible")
        self.assertEqual(
            found["result"]["definitions"][0]["observed_surface_indicator_counts"][0]["count"],
            1,
        )
        fluid = fluids(self.dataset)
        self.assertFalse(fluid["result"]["physical_blocks"])
        self.assertEqual(fluid["result"]["cells"][0]["definition_match_state"], "exact-path")

    def test_strata_v1_tiles_supply_exact_surface_and_biome_coverage(self) -> None:
        manifest_path = _build_strata(self.root / "v1", ore_count=1, world_seed=42)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        preview = manifest.pop("surfacePreview")
        manifest["schema"] = "strata.strataview.region-manifest.v1"
        manifest["blockCounts"] = deepcopy(
            manifest["overviewStats"]["blockStateCounts"]
        )
        tile_path = manifest_path.parent / manifest["tiles"][0]["path"]
        tile = json.loads(tile_path.read_text(encoding="utf-8"))
        tile["schema"] = "strata.strataview.tile.v1"
        tile["parentSchema"] = manifest["schema"]
        tile["heightmaps"] = {
            "mode": "chunk-surface-height",
            "chunks": preview["exactHeightmaps"]["chunks"],
        }
        tile["biomeMap"] = {
            "mode": "chunk-biomes",
            "chunks": preview["biomeMap"]["chunks"],
        }
        _write_json(tile_path, tile)
        _write_json(manifest_path, manifest)

        region = load_strata_region(manifest_path, profile=_profile())
        self.assertEqual(region.surface_at(0, 0), 16)
        self.assertEqual(region.biome_at(0, 0), "minecraft:plains")
        self.assertEqual(region.block_at(1, 4, 1), ORE_STATE)
        self.assertEqual(len(region.dense_section_indices(0, 0, 0) or []), 4096)

    def test_explain_separates_final_state_candidates_and_cave_context(self) -> None:
        report = explain(self.dataset, position=(1, 4, 1), cave_radius=2)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["result"]["final_state"]["material"], "fluorite")
        self.assertEqual(report["result"]["attribution"]["state"], "candidate-only")
        nearest = report["result"]["cave_context"]["nearest_final_subsurface_air"]
        self.assertEqual(nearest["position"], {"x": 2, "y": 4, "z": 1})
        self.assertTrue(report["result"]["cave_context"]["exposed_to_final_subsurface_air"])
        rendered = render_report(report)
        self.assertIn("candidate-only", rendered)
        self.assertIn("nearest 2,4,1 at 1.0 blocks", rendered)

    def test_controlled_trace_establishes_write_and_closes_empty_position(self) -> None:
        paths = _build_fixture(self.root / "traced", with_trace=True)
        dataset = _dataset(paths, root=self.root, with_trace=True)
        ore = explain(dataset, position=(1, 4, 1), cave_radius=1)
        self.assertEqual(ore["status"], "answered")
        self.assertEqual(ore["result"]["attribution"]["state"], "observed-controlled")
        self.assertEqual(
            ore["result"]["attribution"]["definition_path"],
            "worldgen/vein/overworld/fluorite.json",
        )
        attempts = chunk_map(dataset, layer="ore-attempts", material="fluorite")
        self.assertEqual(attempts["result"]["cells"][0]["value"], 1)
        self.assertEqual(
            attempts["result"]["cells"][0]["decision_outcomes"], {"written": 1}
        )
        selected = definitions(dataset, material="fluorite")
        self.assertTrue(selected["result"]["selection_observed"])
        self.assertEqual(selected["result"]["selected_deposit_instance_count"], 1)
        empty = explain(
            dataset,
            position=(2, 4, 1),
            material="fluorite",
            cave_radius=1,
        )
        self.assertEqual(empty["status"], "answered")
        self.assertEqual(
            empty["result"]["attribution"]["state"],
            "no-decision-for-trace-selector",
        )

    def test_aligned_compare_reports_ore_delta_and_scope_mismatch(self) -> None:
        candidate_paths = _build_fixture(self.root / "candidate", ore_count=2)
        candidate = _dataset(candidate_paths, root=self.root)
        report = compare(self.dataset, candidate, material="fluorite")
        self.assertEqual(report["status"], "answered")
        self.assertEqual(report["result"]["summary"]["ore_blocks"]["delta"], 1)
        self.assertEqual(report["result"]["summary"]["changed_chunk_count"], 1)

        other_paths = _build_fixture(self.root / "other-seed", world_seed=43)
        other = _dataset(other_paths, root=self.root)
        incomparable = compare(self.dataset, other)
        self.assertEqual(incomparable["status"], "incomparable")
        self.assertFalse(incomparable["result"]["aligned"])

    def test_input_path_result_identity_and_bounds_fail_closed(self) -> None:
        report = summary(self.dataset)
        changed = deepcopy(report)
        changed["result"]["final_state"]["ore_block_count"] = 2
        with self.assertRaisesRegex(SubsurfaceStudioError, "result ID drift"):
            validate_result(changed)
        with self.assertRaisesRegex(SubsurfaceStudioError, "outside"):
            explain(self.dataset, position=(16, 4, 1))
        with self.assertRaisesRegex(SubsurfaceStudioError, "exactly one"):
            section(self.dataset, x=1, z=1)

        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"value": 1, "value": 2}\n', encoding="utf-8")
        with self.assertRaisesRegex(SubsurfaceStudioError, "repeats JSON key"):
            load_json_file(duplicate, context="duplicate fixture")

        manifest = json.loads(self.paths["manifest"].read_text())
        manifest["tiles"][0]["path"] = "../tile.json"
        unsafe = self.root / "unsafe/manifest.json"
        _write_json(unsafe, manifest)
        profile, binding = load_profile(self.paths["profile"])
        with self.assertRaisesRegex(SubsurfaceStudioError, "unsafe path"):
            load_dataset(
                root=self.root,
                profile=profile,
                profile_binding=binding,
                inventory_path=self.paths["inventory"],
                impact_path=self.paths["impact"],
                manifest_path=unsafe,
            )

    def test_cli_emits_terminal_and_exact_json_views(self) -> None:
        common = [
            "--profile-file",
            str(self.paths["profile"]),
            "--inventory",
            str(self.paths["inventory"]),
            "--impact",
            str(self.paths["impact"]),
            "--manifest",
            str(self.paths["manifest"]),
        ]
        output = io.StringIO()
        self.assertEqual(cli_run([*common, "summary"], root=self.root, output=output), 0)
        self.assertIn("GTCEu Subsurface Studio · summary · answered", output.getvalue())

        output = io.StringIO()
        self.assertEqual(
            cli_run([*common, "--json", "map", "--layer", "subsurface-air"], root=self.root, output=output),
            0,
        )
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["operation"], "map")
        self.assertEqual(validate_result(parsed), parsed)


if __name__ == "__main__":
    unittest.main()
