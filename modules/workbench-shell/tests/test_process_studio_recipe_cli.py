"""Tests for Process Studio's Atlas-owned finite-recipe flow."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell import process_recipe_cli  # noqa: E402


class ProcessStudioRecipeCliTests(unittest.TestCase):
    def test_delegates_complete_owner_comparison_without_relabeling_output(self) -> None:
        output = StringIO()
        error = StringIO()

        def owner(argv, *, suite_root, output, error):
            self.assertEqual(ROOT, suite_root)
            self.assertEqual(
                [
                    "compare-runtime",
                    "/evidence/before",
                    "/evidence/after",
                    "--max-recipes",
                    "250",
                    "--max-recipe-deltas",
                    "25",
                    "--max-resources",
                    "30",
                    "--max-depth",
                    "3",
                    "--max-nodes",
                    "400",
                    "--json",
                ],
                argv,
            )
            output.write('{"format":"workbench-atlas-runtime-recipe-comparison-v1"}\n')
            return 0

        with mock.patch.object(process_recipe_cli, "atlas_recipe_main", side_effect=owner):
            code = process_recipe_cli.main(
                [
                    "/evidence/before",
                    "/evidence/after",
                    "--max-recipes",
                    "250",
                    "--max-recipe-deltas",
                    "25",
                    "--max-resources",
                    "30",
                    "--max-depth",
                    "3",
                    "--max-nodes",
                    "400",
                    "--json",
                ],
                root=ROOT,
                output=output,
                error=error,
            )

        self.assertEqual(0, code)
        self.assertEqual(
            '{"format":"workbench-atlas-runtime-recipe-comparison-v1"}\n',
            output.getvalue(),
        )
        self.assertEqual("", error.getvalue())

    def test_help_states_the_owner_and_claim_boundary(self) -> None:
        help_text = " ".join(process_recipe_cli.build_parser().format_help().split())
        self.assertIn("Atlas remains the evidence authority", help_text)
        self.assertIn("does not prove causality", help_text)
        self.assertIn("unchanged Atlas runtime comparison", help_text)


if __name__ == "__main__":
    unittest.main()
