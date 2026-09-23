from copy import deepcopy
import unittest

from workbench_atlas.source_navigation import SourceNavigation, SourceNavigationError
from workbench_pack_program_studio.declarations import (
    build_source_declarations,
    source_declaration,
    declaration_set_identity,
)
from workbench_pack_program_studio.source_intelligence import (
    semantic_key,
    validate_navigation_declarations,
)
from workbench_pack_program_studio.source_locations import source_location


def declaration(name, path, references=()):
    key = semantic_key("quest", {"id": name}, domain="test")
    return source_declaration(
        semantic_descriptor=key,
        attributes={
            "navigation": {
                "kind": "quest",
                "label": str(name),
                "location": source_location(b"x", path, 0, 1),
                "provides": [key],
                "references": list(references),
                "issues": [],
            }
        },
        lifecycle={},
        provenance={"authority": "Pack Program Studio"},
    )


def feed(rows):
    result = build_source_declarations(
        program_id="test",
        pack_profile_id="test",
        platform_profile_id="test",
        source_sha256="a" * 64,
        declarations=rows,
    )
    result["binding"].update(
        navigation_contract=1,
        source_observation={"source_sha256": "a" * 64},
        source_interpreter={},
        selected_profile={},
        normalizer_sha256="a" * 64,
    )
    result["declaration_set_id"] = declaration_set_identity(result)
    return result


def ref(target, relation="requires-quest"):
    return {
        "relation": relation,
        "target": None
        if target is None
        else semantic_key("quest", {"id": target}, domain="test"),
        "state": "unresolved" if target is None else "declared",
        "details": {},
    }


class SourceNavigationTests(unittest.TestCase):
    def test_duplicates_and_unknowns_never_pick_an_arbitrary_definition(self):
        view = SourceNavigation(
            feed(
                [
                    declaration(1, "a.json"),
                    declaration(1, "b.json"),
                    declaration(2, "c.json", [ref(1), ref(99), ref(None)]),
                ]
            )
        )
        selected = view.search("2", kind="quest")["results"][0]["selection_id"]
        rows = view.inspect(selected)["relationships"]
        self.assertEqual(
            {"declared", "ambiguous", "dangling", "unresolved"},
            {row["state"] for row in rows},
        )
        related = view.related(selected, max_depth=8, max_nodes=2)
        self.assertTrue(related["truncated"])
        self.assertLessEqual(len(related["nodes"]), 2)

    def test_view_does_not_retain_caller_mutability_and_ids_bind_whole_feed(self):
        value = feed([declaration(1, "a.json")])
        view = SourceNavigation(value)
        selected = view.search("")["results"][0]["selection_id"]
        value["binding"]["normalizer_sha256"] = "b" * 64
        value["declaration_set_id"] = declaration_set_identity(value)
        changed = SourceNavigation(value)
        with self.assertRaises(SourceNavigationError):
            changed.inspect(selected)
        result = view.inspect(selected)
        result["selection"]["label"] = "changed"
        self.assertEqual("1", view.inspect(selected)["selection"]["label"])

    def test_bounds_and_locations_are_validated(self):
        value = feed([declaration(1, "a.json")])
        view = SourceNavigation(value)
        selected = view.search("")["results"][0]["selection_id"]
        with self.assertRaises(SourceNavigationError):
            view.related(selected, max_depth=1000)
        changed = deepcopy(value)
        changed["declarations"][0]["attributes"]["navigation"]["location"]["path"] = (
            "../bad"
        )
        row = changed["declarations"][0]
        changed["declarations"][0] = source_declaration(
            semantic_descriptor=row["semantic_descriptor"],
            attributes=row["attributes"],
            lifecycle=row["lifecycle"],
            provenance=row["provenance"],
        )
        changed["declaration_set_id"] = declaration_set_identity(changed)
        with self.assertRaises(ValueError):
            validate_navigation_declarations(changed)

    def test_deep_cycles_use_bounded_iterative_walk(self):
        view = SourceNavigation(
            feed(
                [
                    declaration(i, f"{i}.json", [ref((i + 1) % 1100)])
                    for i in range(1100)
                ]
            )
        )
        self.assertEqual(1, len(view.describe()["prerequisite_cycles"]["back_edges"]))
