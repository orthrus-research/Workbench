"""Source read models consume retained contracts without producer installations."""

from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]


class SourceContractIndependenceTests(unittest.TestCase):
    def test_retained_feed_navigation_and_review_without_product_imports(self):
        program = r'''
import importlib.abc
import json
from pathlib import Path
import sys
from types import SimpleNamespace

root = Path(sys.argv[1])
sys.path[:0] = [str(root / "api/src"), str(root / "modules/atlas/src")]
blocked = ("workbench_pack_program_studio", "workbench_material_semantics", "workbench_project_intelligence",
           "workbench_profile_supersymmetry", "atlas_pack_mutations", "atlas_source_index")
class RefuseProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("unexpected implementation dependency: " + fullname)
sys.meta_path.insert(0, RefuseProducts())

from workbench_atlas.source_navigation import SourceNavigation
from workbench_atlas.source_review import build_source_review
from workbench_atlas import atlas_provenance_normalizer

retained = json.loads(atlas_provenance_normalizer.EXAMPLE_PATH.read_text())
assert atlas_provenance_normalizer.validate_normalization(retained) is retained

feed = json.loads((root / "api/tests/fixtures/source-navigation-v1.json").read_text())
view = SourceNavigation(feed)
assert view.describe()["counts"]["declarations"] == 1
assert view.describe()["relationship_states"]["dangling"] == 1
selection = view.search("硫酸")["results"][0]["selection_id"]
assert view.location(selection)["location"]["path"] == "quests.json"
inputs = SimpleNamespace(
    observation=feed["binding"]["source_observation"],
    sources={"quests.json": b"x"},
    modes=(("quests.json", "100644"),),
)
review = build_source_review(inputs, inputs, before_feed=feed, after_feed=feed)
assert review["counts"]["files"] == 0
assert not any(name in sys.modules for name in blocked)
'''
        result = subprocess.run(
            [sys.executable, "-I", "-c", program, str(ROOT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
