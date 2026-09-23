from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from io import StringIO
import sys
import unittest
import json

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/runtime-explorer/src"
sys.path.insert(0, str(SOURCE))

from workbench_runtime_explorer.model import (  # noqa: E402
    ExplorerError,
    ExplorerRecord,
    ExplorerSource,
    content_id,
    unresolved_owner,
    validate_result,
)
from workbench_runtime_explorer.query import Explorer, parse_query  # noqa: E402
from workbench_runtime_explorer.render import render_result  # noqa: E402


def _source(identifier: str, authority: str) -> ExplorerSource:
    return ExplorerSource(
        source_id=identifier,
        source_kind=(
            "atlas-runtime-graph"
            if authority == "Atlas"
            else authority.casefold()
        ),
        authority=authority,
        state="complete",
        identity={"id": identifier},
    )


def _record(
    identifier: str,
    source: str,
    *,
    state: str,
    kind: str,
    name: str,
    identities: tuple[dict[str, str], ...],
    owner: dict[str, object] | None = None,
    scope: dict[str, object] | None = None,
) -> ExplorerRecord:
    return ExplorerRecord(
        record_id=identifier,
        source_id=source,
        authority="Atlas" if state == "observed" else "Project Intelligence",
        state=state,
        kind=kind,
        name=name,
        identities=identities,
        owner=owner or unresolved_owner(),
        scope=scope or {},
    )


class QueryTests(unittest.TestCase):
    def test_omnibox_parses_filters_typed_identity_stack_and_coordinate(self) -> None:
        request = parse_query(
            'kind:mixin owner:example class:example.mixin.TargetMixin'
        )
        self.assertEqual(("mixin",), request.filters["kind"])
        self.assertEqual(("example",), request.filters["owner"])
        self.assertIn(
            ("class-name", "example.mixin.TargetMixin"),
            {(row.kind, row.value) for row in request.interpreted},
        )

        stack = parse_query(
            "at example.ExampleMod.register(ExampleMod.java:42)"
        )
        interpreted = {(row.kind, row.value) for row in stack.interpreted}
        self.assertIn(("class-name", "example.ExampleMod"), interpreted)
        self.assertIn(
            ("source-member", "example.ExampleMod#register"), interpreted
        )
        self.assertIn(("file-path", "ExampleMod.java"), interpreted)
        coordinate = parse_query("0@128,64,-32")
        self.assertIn(
            ("coordinate", "128,64,-32"),
            {(row.kind, row.value) for row in coordinate.interpreted},
        )
        domains = {
            prefix: next(iter(parse_query(f"{prefix}:example:value").interpreted)).kind
            for prefix in (
                "block",
                "blockstate",
                "item",
                "stack",
                "metadata",
                "loot",
                "machine",
                "groovy",
                "profiler",
            )
        }
        self.assertEqual(
            {
                "block": "block-id",
                "blockstate": "blockstate-id",
                "item": "item-id",
                "stack": "item-stack",
                "metadata": "metadata-value",
                "loot": "loot-table-id",
                "machine": "machine-id",
                "groovy": "groovy-key",
                "profiler": "profiler-event",
            },
            domains,
        )
        with self.assertRaisesRegex(ExplorerError, "quoting"):
            parse_query('class:"unfinished')

    def test_domain_identity_joins_static_and_observed_recipe_facets(self) -> None:
        project = _source("source:project", "Project Intelligence")
        atlas = _source("source:atlas", "Atlas")
        static = _record(
            "decl:recipe",
            project.source_id,
            state="declared",
            kind="recipe",
            name="example:machine",
            identities=(
                {
                    "kind": "recipe-id",
                    "value": "example:machine",
                    "basis": "resource path",
                },
            ),
        )
        observed = _record(
            "rg:recipe",
            atlas.source_id,
            state="observed",
            kind="recipe",
            name="example:machine",
            identities=(
                {
                    "kind": "recipe-id",
                    "value": "example:machine",
                    "basis": "Atlas key",
                },
                {
                    "kind": "runtime-node-id",
                    "value": "rg:recipe",
                    "basis": "Atlas",
                },
            ),
        )
        unrelated_registry = _record(
            "decl:registry",
            project.source_id,
            state="declared",
            kind="registry-name",
            name="example:machine",
            identities=(
                {
                    "kind": "registry-name",
                    "value": "example:machine",
                    "basis": "source literal",
                },
            ),
        )

        result = Explorer(
            (project, atlas), (static, observed, unrelated_registry)
        ).search(
            parse_query("recipe:example:machine")
        )

        self.assertEqual("exact", result["summary"]["status"])
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual(
            ["declared", "observed"], result["matches"][0]["states"]
        )

    def test_exact_static_and_observed_facets_join_without_upgrading_authority(self) -> None:
        project = _source("source:project", "Project Intelligence")
        atlas = _source("source:atlas", "Atlas")
        static = _record(
            "decl:machine",
            project.source_id,
            state="declared",
            kind="registry-name",
            name="example:machine",
            identities=(
                {
                    "kind": "registry-name",
                    "value": "example:machine",
                    "basis": "source",
                },
            ),
            owner={
                "state": "declared",
                "actors": [{"kind": "mod", "id": "example"}],
                "basis": "descriptor",
            },
        )
        observed = _record(
            "rg:machine",
            atlas.source_id,
            state="observed",
            kind="machine",
            name="example:machine",
            identities=(
                {
                    "kind": "registry-name",
                    "value": "example:machine",
                    "basis": "Atlas key",
                },
                {
                    "kind": "runtime-node-id",
                    "value": "rg:machine",
                    "basis": "Atlas",
                },
            ),
            owner={
                "state": "observed",
                "actors": [{"kind": "mod", "id": "example", "version": "1.0"}],
                "basis": "belongs_to_mod",
            },
            scope={"profile": "COMMON_FINAL_STATE", "physical_side": "CLIENT"},
        )
        result = Explorer((project, atlas), (static, observed)).search(
            parse_query("example:machine")
        )

        self.assertEqual("exact", result["summary"]["status"])
        self.assertEqual(1, result["summary"]["entities"])
        entity = result["matches"][0]
        self.assertEqual(["declared", "observed"], entity["states"])
        self.assertEqual(2, len(entity["facets"]))
        self.assertEqual("supplied", result["summary"]["runtime_coverage"])
        self.assertFalse(result["uncertainty"])
        validate_result(result)
        schema = json.loads(
            (
                ROOT
                / "modules/runtime-explorer/schemas/"
                "workbench-exact-runtime-explorer-result-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        errors = sorted(
            Draft202012Validator(schema).iter_errors(result),
            key=lambda error: list(error.path),
        )
        self.assertEqual(
            [], [(list(error.path), error.message) for error in errors]
        )
        stale = deepcopy(result)
        stale["summary"]["returned"] = 0
        with self.assertRaisesRegex(ExplorerError, "counts"):
            validate_result(stale)
        reidentified = deepcopy(result)
        reidentified["matches"][0]["entity_id"] = (
            "workbench-runtime-explorer-entity:sha256:" + "0" * 64
        )
        material = dict(reidentified)
        material.pop("result_id")
        reidentified["result_id"] = content_id(
            "workbench-runtime-explorer-result:sha256:", material
        )
        with self.assertRaisesRegex(ExplorerError, "entity identity"):
            validate_result(reidentified)
        forged_projection = deepcopy(result)
        forged_projection["matches"][0]["identities"].append(
            {"kind": "recipe-id", "value": "forged:value", "basis": "forged"}
        )
        material = dict(forged_projection)
        material.pop("result_id")
        forged_projection["result_id"] = content_id(
            "workbench-runtime-explorer-result:sha256:", material
        )
        with self.assertRaisesRegex(ExplorerError, "identity projection"):
            validate_result(forged_projection)

    def test_filters_and_no_runtime_evidence_are_truthful(self) -> None:
        project = _source("source:project", "Project Intelligence")
        rows = (
            _record(
                "decl:a",
                project.source_id,
                state="declared",
                kind="class",
                name="example.Target",
                identities=({"kind": "class-name", "value": "example.Target", "basis": "source"},),
                owner={"state": "declared", "actors": [{"kind": "mod", "id": "example"}], "basis": "descriptor"},
            ),
            _record(
                "decl:b",
                project.source_id,
                state="static-possible",
                kind="mixin",
                name="example.TargetMixin",
                identities=({"kind": "mixin-class", "value": "example.TargetMixin", "basis": "source"},),
                owner={"state": "declared", "actors": [{"kind": "mod", "id": "example"}], "basis": "descriptor"},
            ),
        )
        result = Explorer((project,), rows).search(
            parse_query("kind:mixin owner:example Target")
        )
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual("mixin", result["matches"][0]["kind"])
        self.assertEqual("unavailable", result["summary"]["runtime_coverage"])
        self.assertIn("No Atlas runtime projection", result["uncertainty"][0])

    def test_duplicate_observed_identity_is_reported_as_ambiguous_exact(self) -> None:
        atlas = _source("source:atlas", "Atlas")
        records = tuple(
            _record(
                f"rg:machine:{index}",
                atlas.source_id,
                state="observed",
                kind="machine",
                name="example:machine",
                identities=(
                    {
                        "kind": "registry-name",
                        "value": "example:machine",
                        "basis": "Atlas key",
                    },
                    {
                        "kind": "runtime-node-id",
                        "value": f"rg:machine:{index}",
                        "basis": "Atlas",
                    },
                ),
                scope={"profile": "COMMON_FINAL_STATE", "physical_side": "CLIENT"},
            )
            for index in (1, 2)
        )

        result = Explorer((atlas,), records).search(
            parse_query("example:machine")
        )

        self.assertEqual("ambiguous-exact", result["summary"]["status"])
        self.assertEqual(1, result["summary"]["entities"])
        self.assertTrue(result["matches"][0]["ambiguous"])
        self.assertEqual(
            ["multiple-observed-runtime-nodes"],
            result["matches"][0]["ambiguity"],
        )

    def test_conflicting_exact_owner_bindings_remain_ambiguous(self) -> None:
        project = _source("source:project", "Project Intelligence")
        atlas = _source("source:atlas", "Atlas")
        records = (
            _record(
                "decl:owner-a",
                project.source_id,
                state="declared",
                kind="registry-name",
                name="example:machine",
                identities=(
                    {
                        "kind": "registry-name",
                        "value": "example:machine",
                        "basis": "source",
                    },
                ),
                owner={
                    "state": "declared",
                    "actors": [{"kind": "mod", "id": "source-owner"}],
                    "basis": "descriptor",
                },
            ),
            _record(
                "rg:owner-b",
                atlas.source_id,
                state="observed",
                kind="machine",
                name="example:machine",
                identities=(
                    {
                        "kind": "registry-name",
                        "value": "example:machine",
                        "basis": "Atlas",
                    },
                ),
                owner={
                    "state": "observed",
                    "actors": [{"kind": "mod", "id": "runtime-owner"}],
                    "basis": "belongs_to_mod",
                },
            ),
        )

        result = Explorer((project, atlas), records).search(
            parse_query("example:machine")
        )

        self.assertEqual("ambiguous-exact", result["summary"]["status"])
        self.assertIn("owner-conflicted", result["matches"][0]["ambiguity"])

    def test_text_renderer_removes_terminal_control_sequences(self) -> None:
        project = _source("source:project", "Project Intelligence")
        record = _record(
            "decl:unsafe",
            project.source_id,
            state="declared",
            kind="class",
            name="before\x1b]0;owned\x07after",
            identities=(
                {"kind": "class-name", "value": "unsafe.Target", "basis": "source"},
            ),
            owner={
                "state": "declared",
                "actors": [
                    {"kind": "mod", "id": "bad\x1b[31mowner\x1b[0m"}
                ],
                "basis": "fixture",
            },
        )
        result = Explorer((project,), (record,)).search(
            parse_query("class:unsafe.Target")
        )
        output = StringIO()

        render_result(result, output)

        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertIn("beforeafter", rendered)
        self.assertIn("badowner", rendered)


if __name__ == "__main__":
    unittest.main()
