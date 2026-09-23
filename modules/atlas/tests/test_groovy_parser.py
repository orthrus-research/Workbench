"""Focused coverage for the packaged Groovy lexical parser."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
ATLAS_SOURCE = ROOT / "modules/atlas/src"
sys.path.insert(0, str(ATLAS_SOURCE))

from workbench_atlas import groovy_parser  # noqa: E402


class GroovyParserTests(unittest.TestCase):
    def test_analysis_preserves_declarations_and_excludes_header_lines(self) -> None:
        analysis = groovy_parser.analyze_bytes(
            {
                "path": "groovy/postInit/example/demo.groovy",
                "lifecycle": "declared-post-init",
            },
            b"""package example
import static foo.Bar.*
class Factory {
    static String make(String name) {
        def recipe = maps.chemical.recipeBuilder().input(name).buildAndRegister()
        return recipe
    }
}
items.each { value, int index ->
    recipes.removeByOutput(value)
}
""",
        )

        self.assertEqual({1, 2}, analysis.excluded_reference_lines)
        self.assertEqual(["make"], [item.name for item in analysis.callables])
        self.assertEqual(
            [
                ("type-class", "Factory", "example.Factory"),
                ("method", "make", "example.Factory.make"),
                ("parameter", "name", "example.Factory.make(name)"),
                ("variable", "recipe", "example.Factory.recipe"),
                ("parameter", "value", "example::<closure@9:12>(value)"),
                ("parameter", "index", "example::<closure@9:12>(index)"),
            ],
            [
                (
                    item["symbol_kind"],
                    item["symbol_name"],
                    item["qualified_hint"],
                )
                for item in analysis.declarations
            ],
        )

    def test_call_and_expression_primitives_share_one_token_stream(self) -> None:
        source = 'maps.chemical.recipeBuilder().input("susy:light_oil", value)'
        tokens = groovy_parser.tokenize(source)
        pairs = groovy_parser.bracket_pairs(tokens, "<test>")
        call_index = next(
            index for index, token in enumerate(tokens) if token.value == "input"
        )
        open_index = call_index + 1
        arguments = groovy_parser.split_arguments(
            tokens, open_index + 1, pairs[open_index]
        )

        builder_index = next(
            index
            for index, token in enumerate(tokens)
            if token.value == "recipeBuilder"
        )
        self.assertEqual(
            "maps.chemical.recipeBuilder",
            groovy_parser.member_chain(tokens, builder_index),
        )
        self.assertEqual("input", groovy_parser.member_chain(tokens, call_index))
        self.assertEqual(
            (
                "\"susy:light_oil\"",
                groovy_parser.sha256_bytes(b'"susy:light_oil"'),
                "susy:light_oil",
                "exact-literal",
            ),
            groovy_parser.expression_value(source, tokens, arguments[0]),
        )
        self.assertEqual(2, len(arguments))

    def test_module_has_no_repository_generator_entry_points(self) -> None:
        self.assertFalse(hasattr(groovy_parser, "main"))
        self.assertFalse(hasattr(groovy_parser, "validate_repository"))
        self.assertFalse(hasattr(groovy_parser, "analyze_source"))


if __name__ == "__main__":
    unittest.main()
