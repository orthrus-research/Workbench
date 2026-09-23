from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.profile_extensions import ProfileExtensionError
from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, node_record
from workbench_atlas_recipe_health.cli import main


class RecipeCaptureCliTests(unittest.TestCase):
    def run_import(self, root: Path, adapter):
        output, error = StringIO(), StringIO()
        with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter) as resolver:
            status = main(["import-capture", str(root / "capture"), "--output", str(root / "graph"),
                           "--input-manifest", str(root / "input.json"), "--pack-profile", "fixture", "--json"],
                          output=output, error=error)
        resolver.assert_called_once_with("workbench.recipe_graphs", "fixture")
        return status, output.getvalue(), error.getvalue()

    def adapter(self, *, corrupt: bool = False, false_receipt: bool = False):
        def project(capture, output, *, input_manifest, max_source_bytes, check_cancelled):
            builder = CategoricalGraphBundleBuilder(output, scope={"fixture": True}, evidence_binding={"capture": "fixture"})
            builder.add_partition("recipes", classification="fixture", dependencies=(),
                                  nodes=[node_record("gt-recipe", "fixture|one|0", {})], edges=(),
                                  evidence_categories=("gt-recipes",))
            manifest = builder.close()
            if corrupt:
                (output / "recipes/nodes.jsonl").write_text("{}\n")
            return {"state": "complete", "root": str(output.resolve()),
                    "graph_set_id": "false" if false_receipt else manifest["graph_set_id"]}
        return SimpleNamespace(RECIPE_GRAPH_API_VERSION=1, project_capture=project)

    def test_admits_explicit_adapter_and_reopens_its_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            status, output, error = self.run_import(Path(temporary), self.adapter())
            self.assertEqual(0, status, error)
            self.assertEqual("complete", json.loads(output)["state"])

    def test_refuses_bad_graph_or_misbound_adapter_receipt(self):
        for arguments in ({"corrupt": True}, {"false_receipt": True}):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as temporary:
                status, output, error = self.run_import(Path(temporary), self.adapter(**arguments))
                self.assertEqual(2, status)
                self.assertEqual("", output)
                self.assertIn("Atlas recipes failed", error)

    def test_missing_or_incompatible_adapter_fails_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status, _, error = self.run_import(root, SimpleNamespace(RECIPE_GRAPH_API_VERSION=True))
            self.assertEqual(2, status)
            self.assertIn("compatible recipe graph", error)
            self.assertFalse((root / "graph").exists())
            with patch("workbench_api.profile_extensions.require_profile_extension",
                       side_effect=ProfileExtensionError("profile disabled")):
                error_stream = StringIO()
                status = main(["import-capture", str(root), "--output", str(root / "graph"),
                               "--input-manifest", str(root / "input.json"), "--pack-profile", "fixture"],
                              output=StringIO(), error=error_stream)
                self.assertEqual(2, status)
                self.assertIn("disabled", error_stream.getvalue())
                self.assertFalse((root / "graph").exists())


if __name__ == "__main__":
    unittest.main()
