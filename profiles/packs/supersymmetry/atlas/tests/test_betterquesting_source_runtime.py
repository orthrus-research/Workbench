from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / "profiles/packs/supersymmetry/src"
ATLAS = ROOT / "modules/atlas/src"
CRUCIBLE = ROOT / "modules/crucible/src"
for path in (SOURCE, ATLAS, CRUCIBLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from workbench_profile_supersymmetry.betterquesting_source_runtime import (  # noqa: E402
    _cycles,
    _item,
    _typed_name,
    _typed_value,
)


class BetterQuestingSourceRuntimeTests(unittest.TestCase):
    def test_typed_keys_preserve_namespaces_and_strip_only_nbt_suffix(self) -> None:
        self.assertEqual("oc:color", _typed_name("oc:color:3"))
        self.assertEqual("gregtech:material", _typed_name("gregtech:material"))

    def test_numeric_typed_object_normalizes_as_ordered_nbt_list(self) -> None:
        self.assertEqual(
            [{"id": 20}, {"id": 21}],
            _typed_value({"0:10": {"id:2": 20}, "1:10": {"id:2": 21}}),
        )

    def test_item_normalization_adds_only_semantic_defaults(self) -> None:
        self.assertEqual(
            {
                "item_id": "gregtech:meta_dust",
                "count": 1,
                "damage": 0,
                "ore_dictionary": "",
                "tag": None,
            },
            _item({"id:8": "gregtech:meta_dust"}),
        )

    def test_prerequisite_cycle_detection_keeps_dangling_targets_out(self) -> None:
        cycles = _cycles(
            {1, 2, 3},
            [
                {"quest_id": 1, "required_quest_id": 2},
                {"quest_id": 2, "required_quest_id": 1},
                {"quest_id": 3, "required_quest_id": 99},
            ],
        )
        self.assertEqual([[1, 2]], cycles)


if __name__ == "__main__":
    unittest.main()
