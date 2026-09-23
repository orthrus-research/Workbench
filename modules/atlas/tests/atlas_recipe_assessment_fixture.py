"""Owner-generated result shared by CLI adapter boundary tests."""

from pathlib import Path
import tempfile

from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, node_record
from workbench_atlas_recipe_health import assess_proposed_recipe, open_recipe_health


def assessment_fixture():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "graph"
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={"profile": "recipe-cli-fixture"},
            evidence_binding={"capture_id": "recipe-cli-fixture"},
        )
        builder.add_partition(
            "recipe-cli",
            classification="recipe CLI adapter fixture",
            dependencies=(),
            nodes=[
                node_record("gt-recipe-map", "mixer", {"name": "mixer"}),
                node_record("forge-fluid", "feed", {"name": "feed"}),
                node_record("forge-fluid", "product", {"name": "product"}),
            ],
            edges=[],
            evidence_categories=("fixture",),
        )
        builder.close()
        with open_recipe_health(root) as view:
            return assess_proposed_recipe(view, {
                "proposal_id": "workbench-plan:sha256:" + "1" * 64,
                "source_format": "workbench-plan-v1",
                "source_kind": "workbench-plan",
                "mutation": "add",
                "recipe_map": "mixer",
                "duration": 40,
                "voltage_tier": "LV",
                "item_inputs": [],
                "fluid_inputs": [{"name": "feed", "amount": 1000}],
                "item_outputs": [],
                "fluid_outputs": [{"name": "product", "amount": 1000}],
            }, max_depth=3, max_nodes=100)
