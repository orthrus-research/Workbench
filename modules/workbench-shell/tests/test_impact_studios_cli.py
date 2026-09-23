"""Focused tests for Machine, Asset, and Evolution Studio routes."""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_atlas_categorical_graph import (  # noqa: E402
    CategoricalGraphBundleBuilder,
    edge_record,
    node_record,
)
from workbench_shell import (  # noqa: E402
    asset_studio_cli,
    evolution_studio_cli,
    machine_studio_cli,
)
from workbench_shell.catalog import build_catalog  # noqa: E402


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ImpactStudioCliTests(unittest.TestCase):
    def _asset_workspace(self, root: Path, *, include_texture: bool = True) -> Path:
        workspace = root / "Example Mod"
        assets = workspace / "src/main/resources/assets/example"
        blockstate = assets / "blockstates/widget.json"
        model = assets / "models/block/widget.json"
        texture = assets / "textures/blocks/widget.png"
        language = assets / "lang/en_us.lang"
        for path in (blockstate, model, texture, language):
            path.parent.mkdir(parents=True, exist_ok=True)
        blockstate.write_text(
            json.dumps({"variants": {"normal": {"model": "example:block/widget"}}}),
            encoding="utf-8",
        )
        model.write_text(
            json.dumps(
                {
                    "parent": "minecraft:block/cube_all",
                    "textures": {"all": "example:blocks/widget"},
                    "elements": [
                        {
                            "faces": {
                                "north": {"texture": "#all"},
                            }
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        if include_texture:
            texture.write_bytes(b"not-decoded-by-source-closure")
        language.write_text("tile.example.widget.name=Widget\n", encoding="utf-8")
        return workspace

    def _runtime_database(self, root: Path) -> tuple[Path, str]:
        database = root / "runtime graph.sqlite"
        machine_id = "rg:common_final_state_client:machine:example:widget"
        scope = {
            "snapshot_id": "workbench-atlas-runtime-graph-snapshot-v1",
            "profile": "COMMON_FINAL_STATE",
            "physical_side": "CLIENT",
            "adapter": "fixture",
        }

        def node(identifier: str, kind: str, **attributes: object) -> dict[str, object]:
            return {
                "record_type": "node",
                "id": identifier,
                "kind": kind,
                "scope": scope,
                "attributes": attributes,
            }

        machine = node(machine_id, "machine", registry_name="example:widget")
        recipe_map = node("rg:map:widget", "recipe_map")
        recipe = node("rg:recipe:widget", "recipe", duration=20, eut=8)
        form = node("rg:item:widget", "item_variant")
        constraint = node("rg:constraint:widget", "constraint")
        energy = node("rg:energy:eu", "energy")
        nodes = (machine, recipe_map, recipe, form, constraint, energy)

        def edge(
            identifier: str,
            predicate: str,
            subject: str,
            object_: str,
            **attributes: object,
        ) -> dict[str, object]:
            return {
                "record_type": "edge",
                "id": identifier,
                "predicate": predicate,
                "subject": subject,
                "object": object_,
                "scope": scope,
                "attributes": attributes,
            }

        edges = (
            edge("rg:edge:map", "executes_recipe_map", machine_id, recipe_map["id"]),
            edge(
                "rg:edge:recipe",
                "has_recipe",
                recipe_map["id"],
                recipe["id"],
                lookup_active=True,
            ),
            edge("rg:edge:form", "has_form", machine_id, form["id"]),
            edge("rg:edge:constraint", "has_constraint", machine_id, constraint["id"]),
            edge("rg:edge:energy", "uses_energy", machine_id, energy["id"]),
        )
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                PRAGMA application_id = 1398098247;
                PRAGMA user_version = 2;
                CREATE TABLE nodes (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL,
                    physical_side TEXT NOT NULL, adapter TEXT NOT NULL,
                    kind TEXT NOT NULL, json TEXT NOT NULL
                ) WITHOUT ROWID;
                CREATE TABLE node_keys (
                    node_id TEXT NOT NULL, key_kind TEXT NOT NULL,
                    key_value TEXT NOT NULL, profile TEXT NOT NULL,
                    physical_side TEXT NOT NULL,
                    PRIMARY KEY (key_kind, key_value, profile, physical_side, node_id)
                ) WITHOUT ROWID;
                CREATE INDEX node_keys_node ON node_keys(node_id);
                CREATE INDEX node_keys_lookup ON node_keys(key_kind, key_value);
                CREATE TABLE edges (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL,
                    physical_side TEXT NOT NULL, adapter TEXT NOT NULL,
                    predicate TEXT NOT NULL, subject TEXT NOT NULL,
                    object TEXT NOT NULL, json TEXT NOT NULL
                ) WITHOUT ROWID;
                """
            )
            connection.executemany(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        row["id"],
                        "COMMON_FINAL_STATE",
                        "CLIENT",
                        "fixture",
                        row["kind"],
                        _canonical(row),
                    )
                    for row in nodes
                ],
            )
            connection.execute(
                "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
                (machine_id, "registry-name", "example:widget", "COMMON_FINAL_STATE", "CLIENT"),
            )
            connection.executemany(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        row["id"],
                        "COMMON_FINAL_STATE",
                        "CLIENT",
                        "fixture",
                        row["predicate"],
                        row["subject"],
                        row["object"],
                        _canonical(row),
                    )
                    for row in edges
                ],
            )
        return database, machine_id

    def _recipe_graph(self, root: Path, *, capture_id: str) -> Path:
        recipe = node_record(
            "gt-recipe",
            "mixer|" + "1" * 64 + "|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "1" * 64,
                "duplicate_ordinal": 0,
                "duration": 20,
                "eut": 30,
                "lookup_active": True,
            },
        )
        fluid = node_record("forge-fluid", "water", {"name": "water"})
        selector = node_record(
            "gt-recipe-input-selector",
            f"{recipe['semantic_key']}|fluid|0",
            {"ordinal": 0},
        )
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={
                "pack_profile_id": "workbench-pack:supersymmetry",
                "platform_profile_id": "workbench-platform:cleanroom:provisional",
                "platform_candidate": "fixture-cleanroom",
                "physical_side": "dedicated_server",
                "projection_profile": "impact-studio-test-v1",
            },
            evidence_binding={
                "capture_id": capture_id,
                "adapter_profile_sha256": "a" * 64,
                "input_manifest_sha256": "b" * 64,
                "category_results": {
                    "gt-recipes": {
                        "category_id": "transformation-recipe",
                        "checkpoint_id": "post-start-end-tick",
                        "record_count": 1,
                        "records_sha256": "c" * 64,
                        "result_sha256": "d" * 64,
                    }
                },
            },
        )
        builder.add_partition(
            "fixture",
            classification="impact studio route fixture",
            dependencies=(),
            nodes=(recipe, fluid, selector),
            edges=(
                edge_record(
                    "has-fluid-input-selector", recipe["id"], selector["id"], {"ordinal": 0}
                ),
                edge_record(
                    "accepts-gt-fluid-input", selector["id"], fluid["id"], {"amount": 1000}
                ),
            ),
            evidence_categories=("transformation-recipe",),
        )
        builder.close()
        return root

    def test_asset_check_resolves_local_closure_and_keeps_external_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._asset_workspace(Path(temporary))
            report = asset_studio_cli.check_assets(
                workspace,
                identity="example:widget",
                translation_keys=("tile.example.widget.name",),
            )
        self.assertEqual("limited", report["summary"]["status"])
        self.assertEqual(0, report["summary"]["broken_local_references"])
        self.assertEqual(1, report["summary"]["external_references"])
        self.assertFalse(report["claims"]["runtime_registration_observed"])
        self.assertFalse(report["claims"]["render_success_observed"])
        self.assertEqual("present", report["localization"]["explicit_keys"][0]["state"])

    def test_asset_check_reports_missing_local_texture_as_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._asset_workspace(Path(temporary), include_texture=False)
            output = StringIO()
            error = StringIO()
            status = asset_studio_cli.main(
                ["check", str(workspace), "--identity", "example:widget", "--json"],
                output=output,
                error=error,
            )
        self.assertEqual(1, status, error.getvalue())
        report = json.loads(output.getvalue())
        self.assertEqual("attention", report["summary"]["status"])
        self.assertIn(
            "example:blocks/widget",
            {row.get("identity") for row in report["closure"]["broken_local_references"]},
        )

    def test_machine_inspection_separates_registration_from_world_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database, machine_id = self._runtime_database(Path(temporary))
            report = machine_studio_cli.inspect_machine(
                database,
                "example:widget",
            )
        self.assertEqual(machine_id, report["atlas"]["machine"]["id"])
        self.assertEqual("observed", report["evidence_states"]["registered_machine"]["state"])
        self.assertEqual("not-supplied", report["evidence_states"]["formed_world_machine"]["state"])
        self.assertFalse(report["claims"]["world_machine_formed"])
        self.assertEqual(1, report["summary"]["recipes"])
        self.assertEqual(1, report["explorer"]["summary"]["returned"])

    def test_evolution_route_delegates_streams_arguments_and_status_to_atlas(self) -> None:
        output = StringIO()
        error = StringIO()
        with mock.patch.object(
            evolution_studio_cli,
            "atlas_recipe_main",
            return_value=7,
        ) as delegated:
            status = evolution_studio_cli.main(
                ["recipes", "/before", "/after", "--json"],
                suite_root=ROOT,
                output=output,
                error=error,
            )
        self.assertEqual(7, status)
        delegated.assert_called_once_with(
            ["compare-runtime", "/before", "/after", "--json"],
            suite_root=ROOT,
            output=output,
            error=error,
        )

    def test_catalog_exposes_three_read_only_owner_preserving_routes(self) -> None:
        catalog = build_catalog(ROOT)
        expected = {
            "machine-studio.inspect": ("machine-studio", "tools/workbench.py machine inspect"),
            "asset-studio.check": ("asset-studio", "tools/workbench.py assets check"),
            "evolution-studio.recipes": ("evolution-studio", "tools/workbench.py evolution recipes"),
        }
        for command_id, (suite_id, preview) in expected.items():
            with self.subTest(command=command_id):
                command = catalog.command(command_id)
                self.assertEqual(suite_id, command.suite_id)
                self.assertEqual("read-only", command.risk)
                self.assertEqual("experimental", command.availability)
                self.assertEqual(
                    "docs/product/IMPACT-STUDIOS.md",
                    command.documentation,
                )
                self.assertIn(preview, command.render_template(ROOT))

    def test_installed_style_root_routes_execute_all_three_flows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self._asset_workspace(root)
            database, machine_id = self._runtime_database(root)
            before = self._recipe_graph(root / "before", capture_id="before")
            after = self._recipe_graph(root / "after", capture_id="after")
            commands = (
                (
                    ["assets", "check", str(workspace), "--identity", "example:widget", "--json"],
                    "workbench-asset-closure-report-v1",
                ),
                (
                    [
                        "machine",
                        "inspect",
                        machine_id,
                        "--runtime-db",
                        str(database),
                        "--key-kind",
                        "runtime-node-id",
                        "--json",
                    ],
                    "workbench-machine-inspection-v1",
                ),
                (
                    ["evolution", "recipes", str(before), str(after), "--json"],
                    "workbench-atlas-runtime-recipe-comparison-v1",
                ),
            )
            for arguments, expected_format in commands:
                with self.subTest(command=arguments[0]):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "tools/workbench.py"), *arguments],
                        cwd=ROOT,
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                        env={**os.environ, "PYTHONUTF8": "1"},
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(expected_format, json.loads(result.stdout)["format"])


if __name__ == "__main__":
    unittest.main()
