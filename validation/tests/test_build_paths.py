"""Build routes derive from native ownership, not an embedded runtime suite."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_paths import load_build_paths


class BuildPathsTests(unittest.TestCase):
    def test_native_routes_are_closed_and_not_qualification(self):
        value = load_build_paths(ROOT)
        self.assertEqual({"source", "native-component", "native-suite", "client", "axiom-engine"}, {row["id"] for row in value["build_paths"]})
        self.assertEqual(23, len(value["components"]))
        self.assertFalse(value["qualified"])
        self.assertFalse(value["published"])
        self.assertTrue(all((ROOT / row["argv"][1]).is_file() for row in value["build_paths"]))
