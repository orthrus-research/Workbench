#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROFILE_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "profiles/packs/supersymmetry/src"
)
if str(PROFILE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROFILE_SOURCE))
TEST_SOURCE = Path(__file__).resolve().parent
if str(TEST_SOURCE) not in sys.path:
    sys.path.insert(0, str(TEST_SOURCE))

import workbench_atlas.runtime_graph as graph
import workbench_atlas.runtime_graph_material_classification as materials
import workbench_atlas.runtime_graph_query as query
from workbench_profile_supersymmetry.gtceu_material_classification import (  # noqa: E402
    GTCEU_MATERIAL_CLASSIFICATION_POLICY,
    classify_supersymmetry_materials,
)
from test_runtime_graph_fluid_classification import (  # noqa: E402
    FluidFixture,
    SCOPE,
)


class MaterialFixture(FluidFixture):
    def add_node(
        self,
        identifier: str,
        kind: str,
        attributes: dict[str, object] | None = None,
        *,
        adapter: str = "fixture",
    ) -> str:
        if identifier in self.known_nodes:
            return identifier
        self.known_nodes.add(identifier)
        scope = {**self.scope(), "adapter": adapter}
        row = {
            "record_type": "node",
            "id": identifier,
            "kind": kind,
            "scope": scope,
            "attributes": {} if attributes is None else attributes,
        }
        self.nodes.append(
            (
                identifier,
                SCOPE.profile,
                SCOPE.physical_side,
                adapter,
                kind,
                graph.canonical_json_payload(row).decode("utf-8"),
            )
        )
        return identifier

    def add_edge(
        self,
        predicate: str,
        subject: str,
        object_: str,
        attributes: dict[str, object] | None = None,
        *,
        adapter: str = "fixture",
    ) -> str:
        self.edge_sequence += 1
        edge_attributes = {
            "fixture_sequence": self.edge_sequence,
            **({} if attributes is None else attributes),
        }
        scope = {**self.scope(), "adapter": adapter}
        row: dict[str, object] = {
            "record_type": "edge",
            "predicate": predicate,
            "subject": subject,
            "object": object_,
            "scope": scope,
            "attributes": edge_attributes,
        }
        row["id"] = graph.edge_id(row)
        self.edges.append(
            (
                row["id"],
                SCOPE.profile,
                SCOPE.physical_side,
                adapter,
                predicate,
                subject,
                object_,
                graph.canonical_json_payload(row).decode("utf-8"),
            )
        )
        return str(row["id"])

    def add_material(
        self,
        suffix: str,
        numeric_id: int,
        properties: tuple[str, ...],
        *,
        marker: bool = False,
        component_count: int = 0,
        fluid_form_count: int = 0,
        flags: tuple[str, ...] = (),
        formula: str = "",
    ) -> str:
        identifier = f"material:{suffix}"
        registry_name = f"example:{suffix}"
        attributes: dict[str, object] = {
            "blast_temperature": 0,
            "chemical_formula": formula,
            "component_count": component_count,
            "components_initialized": not marker,
            "flags": list(flags),
            "fluid_form_count": fluid_form_count,
            "has_fluid": "fluid" in properties,
            "id": numeric_id,
            "icon_set": {
                "id": 0,
                "name": "dull",
                "parent": None,
                "root": True,
            },
            "marker": marker,
            "mass": None if marker else 1,
            "name": suffix,
            "namespace": "example",
            "neutrons": None if marker else 1,
            "property_count": len(properties),
            "protons": None if marker else 1,
            "radioactive": None if marker else False,
            "registry_name": registry_name,
            "rgb": 0xA0B0C0,
            "solid": "ingot" in properties or "gem" in properties,
            "storage_registry_mod_id": None if marker else "example",
            "storage_registry_network_id": None if marker else 7,
        }
        self.add_node(
            identifier,
            "material",
            attributes,
            adapter="gt_materials",
        )
        self.add_key(
            identifier,
            "material-resource-location",
            registry_name,
        )
        if not marker:
            self.add_key(identifier, "material-numeric-id", str(numeric_id))
        for key in properties:
            property_id = f"material_property:{suffix}:{key}"
            implementation = (
                "example.ProfileProperty"
                if key == "fiber"
                else f"gregtech.{key}.Property"
            )
            self.add_node(
                property_id,
                "material_property",
                {
                    "implementation_class": implementation,
                    "key": key,
                    "material": registry_name,
                    "raw_value_null": False,
                    "value": {},
                },
                adapter="gt_materials",
            )
            self.add_edge(
                "has_property",
                identifier,
                property_id,
                {"key": key},
                adapter="gt_materials",
            )
        return identifier


def populated_material_fixture() -> MaterialFixture:
    fixture = MaterialFixture()
    copper = fixture.add_material(
        "copper",
        1,
        ("dust", "fiber", "ingot"),
        flags=("custom_profile_flag", "generate_plate"),
        formula="Cu",
    )
    element = fixture.add_node(
        "element:copper",
        "element",
        {
            "decay_to": None,
            "half_life_seconds": -1,
            "isotope": False,
            "mass": 64,
            "name": "Copper",
            "neutrons": 35,
            "protons": 29,
            "symbol": "Cu",
        },
        adapter="gt_materials",
    )
    fixture.add_edge(
        "has_element",
        copper,
        element,
        {},
        adapter="gt_materials",
    )

    alloy = fixture.add_material(
        "alloy",
        2,
        ("dust", "ingot"),
        component_count=1,
    )
    fixture.add_edge(
        "has_component",
        alloy,
        copper,
        {"amount": 3, "ordinal": 0},
        adapter="gt_materials",
    )
    dust_variant = fixture.add_node("item_variant:alloy-dust", "item_variant")
    fixture.add_edge(
        "has_form",
        alloy,
        dust_variant,
        {"prefix_name": "dust", "selected_by_get": True},
        adapter="gt_ore_prefixes",
    )
    prefix = fixture.add_node(
        "ore_prefix:dust",
        "ore_prefix",
        {"name": "dust"},
        adapter="gt_ore_prefixes",
    )
    constraint = fixture.add_node(
        "constraint:dust:alloy",
        "constraint",
        {
            "constraint_kind": "ore_prefix_material_generation",
            "do_generate_item": True,
            "effective_material_amount": 3_628_800,
            "is_amount_modified": False,
            "is_ignored": False,
            "material_node_id": alloy,
            "material_registry_name": "example:alloy",
            "prefix_name": "dust",
        },
        adapter="gt_ore_prefixes",
    )
    fixture.add_edge(
        "has_constraint",
        prefix,
        constraint,
        {},
        adapter="gt_ore_prefixes",
    )
    fixture.add_edge(
        "has_material",
        constraint,
        alloy,
        {"relationship": "evaluated_material"},
        adapter="gt_ore_prefixes",
    )

    coolant = fixture.add_material(
        "coolant",
        3,
        ("fluid",),
        fluid_form_count=1,
    )
    fluid = fixture.add_node("fluid:coolant", "fluid")
    fixture.add_edge(
        "has_form",
        coolant,
        fluid,
        {
            "primary": True,
            "storage_key": "gregtech:liquid",
        },
        adapter="gt_materials",
    )

    fixture.add_material("tier_lv", -1, (), marker=True)
    return fixture


class RuntimeGraphMaterialClassificationTests(unittest.TestCase):
    def database(self, *, reverse: bool = False) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "runtime-graph.sqlite"
        populated_material_fixture().write(path, reverse=reverse)
        return path

    def test_material_classification_without_material_semantics_or_pps(self) -> None:
        root = Path(__file__).resolve().parents[3]
        script = textwrap.dedent(
            """
            import importlib.abc
            from pathlib import Path
            import sys
            import tempfile

            class AbsentProducts(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'workbench_material_semantics',
                        'workbench_pack_program_studio',
                    }:
                        raise ModuleNotFoundError('absent product: ' + fullname)

            sys.meta_path.insert(0, AbsentProducts())
            root = Path(sys.argv[1])
            sys.path[:0] = [str(root / path) for path in (
                'api/src',
                'modules/atlas/src',
                'modules/atlas/tests',
                'profiles/packs/supersymmetry/src',
            )]
            from test_runtime_graph_material_classification import (
                GTCEU_MATERIAL_CLASSIFICATION_POLICY,
                SCOPE,
                classify_supersymmetry_materials,
                populated_material_fixture,
                query,
            )
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'graph.sqlite'
                populated_material_fixture().write(path)
                with query.RuntimeGraphReader(path) as reader:
                    result = classify_supersymmetry_materials(reader, SCOPE)
            assert len(result.rows) == 4
            assert GTCEU_MATERIAL_CLASSIFICATION_POLICY.sha256 == (
                '2f053aa83e27942f409e413306a8774b01fd76928668c495cef3855b1205c280'
            )
            assert result.sha256 == (
                '3f3acd457d35d52676e92979f7fe2505946e5b056650c7a56a97ec50f8fa6391'
            )
            assert not any(name.startswith((
                'workbench_material_semantics', 'workbench_pack_program_studio'
            )) for name in sys.modules)
            """
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(root)],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_material_policy_failures_translate_to_atlas_graph_errors(self) -> None:
        from dataclasses import replace
        from workbench_api.material_classification import MaterialPolicyValidationError

        for attribute, value, message in (
            ("api_version", 2, "API version"),
            ("base_property_keys", ("blast",), "overlap"),
        ):
            policy = replace(GTCEU_MATERIAL_CLASSIFICATION_POLICY)
            object.__setattr__(policy, attribute, value)
            with self.subTest(attribute=attribute):
                with query.RuntimeGraphReader(self.database()) as reader:
                    with self.assertRaisesRegex(query.RuntimeGraphQueryError, message) as raised:
                        materials.classify_materials(reader, SCOPE, policy)
                    self.assertIsInstance(raised.exception.__cause__, MaterialPolicyValidationError)

    def test_material_core_is_independent_of_realized_forms(self) -> None:
        with query.RuntimeGraphReader(self.database()) as reader:
            result = classify_supersymmetry_materials(reader, SCOPE)

        self.assertEqual(4, len(result.rows))
        self.assertEqual(
            {"marker": 1, "persistent": 3},
            result.summary["registry_role_counts"],
        )
        indexed = {row["material_id"]: row for row in result.rows}

        copper = indexed["material:copper"]
        self.assertEqual("exact", copper["classification_status"])
        self.assertEqual("elemental", copper["core"]["composition"]["basis"])
        self.assertEqual(0, copper["form_lens"]["realized"]["count"])
        self.assertEqual(
            ["dust", "ingot"],
            copper["core"]["properties"]["base_keys"],
        )
        self.assertEqual(
            ["fiber"],
            copper["core"]["properties"]["profile_extension_keys"],
        )
        self.assertEqual(
            ["profile_extension"],
            copper["core"]["flags"]["classified"][0]["categories"],
        )
        self.assertEqual(64, len(copper["core_sha256"]))

        coolant = indexed["material:coolant"]
        self.assertEqual(["fluid"], coolant["core"]["properties"]["base_keys"])
        self.assertEqual(
            {"fluid": 1, "item_variant": 0, "other": 0},
            coolant["form_lens"]["realized"]["counts_by_kind"],
        )

        marker = indexed["material:tier_lv"]
        self.assertEqual("marker", marker["core"]["registry_role"])
        self.assertFalse(marker["core"]["identity"]["numeric_id_authoritative"])
        self.assertEqual([], marker["core"]["properties"]["keys"])
        self.assertEqual("exact", marker["classification_status"])

    def test_prefix_eligibility_and_item_realization_are_separate_facts(self) -> None:
        with query.RuntimeGraphReader(self.database()) as reader:
            result = classify_supersymmetry_materials(reader, SCOPE)

        alloy = result.find_materials("runtime-node-id", "material:alloy")[0]
        self.assertEqual("composite", alloy["core"]["composition"]["basis"])
        generation = alloy["form_lens"]["prefix_generation"]
        self.assertEqual(1, generation["decision_count"])
        decision = generation["decisions"][0]
        self.assertTrue(decision["do_generate_item"])
        self.assertEqual(
            "eligible_with_realized_form",
            decision["realization_status"],
        )
        self.assertEqual(
            ["item_variant:alloy-dust"],
            decision["realized_item_variant_ids"],
        )

    def test_profile_closure_violations_remain_queryable_frontier(self) -> None:
        fixture = populated_material_fixture()
        fixture.add_material("broken", 4, ("ingot",))
        fixture.add_material(
            "broken_flag",
            5,
            ("dust", "ingot"),
            flags=("generate_foil",),
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "runtime-graph.sqlite"
        fixture.write(path)

        with query.RuntimeGraphReader(path) as reader:
            result = classify_supersymmetry_materials(reader, SCOPE)

        broken = result.find_materials(
            "material-resource-location",
            "example:broken",
        )[0]
        self.assertEqual("frontier", broken["classification_status"])
        self.assertEqual(
            ["property:ingot:missing:dust"],
            broken["frontier_issue_codes"],
        )
        self.assertEqual(
            ["material:broken"],
            [
                row["material_id"]
                for row in result.frontier("property:ingot:missing:dust")
            ],
        )
        broken_flag = result.find_materials(
            "material-resource-location",
            "example:broken_flag",
        )[0]
        self.assertEqual(
            ["flag:generate_foil:missing:generate_plate"],
            broken_flag["frontier_issue_codes"],
        )

    def test_policy_and_storage_order_are_bound_but_batching_is_not(self) -> None:
        with query.RuntimeGraphReader(self.database()) as reader:
            first = classify_supersymmetry_materials(reader, SCOPE)
        with query.RuntimeGraphReader(self.database(reverse=True)) as reader:
            second = classify_supersymmetry_materials(
                reader,
                SCOPE,
                materials.MaterialClassificationBounds(metadata_chunk_size=1),
            )

        self.assertEqual(first.rows, second.rows)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(
            GTCEU_MATERIAL_CLASSIFICATION_POLICY.sha256,
            first.to_dict()["policy"]["sha256"],
        )
        self.assertEqual(
            ["material:copper"],
            [
                row["material_id"]
                for row in first.by_property("fiber")
            ],
        )
        self.assertEqual(
            ["material:tier_lv"],
            [
                row["material_id"]
                for row in first.by_registry_role("marker")
            ],
        )

    def test_bounds_scope_and_adapter_fail_closed(self) -> None:
        with query.RuntimeGraphReader(self.database()) as reader:
            with self.assertRaisesRegex(query.RuntimeGraphQueryError, "max_materials"):
                classify_supersymmetry_materials(
                    reader,
                    SCOPE,
                    materials.MaterialClassificationBounds(max_materials=3),
                )
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "explicit ProfileScope",
            ):
                materials.classify_materials(
                    reader,
                    None,  # type: ignore[arg-type]
                    GTCEU_MATERIAL_CLASSIFICATION_POLICY,
                )

        fixture = populated_material_fixture()
        identifier = "material:foreign"
        fixture.add_node(
            identifier,
            "material",
            {},
            adapter="foreign_materials",
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "foreign.sqlite"
        fixture.write(path)
        with query.RuntimeGraphReader(path) as reader:
            with self.assertRaisesRegex(
                query.RuntimeGraphQueryError,
                "unadmitted material adapter",
            ):
                classify_supersymmetry_materials(reader, SCOPE)


if __name__ == "__main__":
    unittest.main()
