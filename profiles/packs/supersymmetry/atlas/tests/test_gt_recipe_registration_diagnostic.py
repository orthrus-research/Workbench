"""Tests for the bounded Supersymmetry latest.log registration observer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[5]
MODULE_PATH = (
    ROOT
    / "profiles/packs/supersymmetry/atlas/src/gt_recipe_registration_diagnostic.py"
)
SPEC = importlib.util.spec_from_file_location(
    "gt_recipe_registration_diagnostic",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def _row(
    level: str,
    logger: str,
    message: str,
    *,
    context: str = "Client thread",
) -> str:
    return f"[07:31:30] [{context}/{level}] [{logger}]: {message}"


def _frame(owner_class: str, method: str, source: str, line: int) -> str:
    return f"\tat {owner_class}.{method}({source}:{line})"


def _empty_output(
    *,
    owner_class: str = (
        "supersymmetry.loaders.recipes.SuSyMaterialRecipeHandler"
    ),
    owner_method: str = "processHIPPressing",
    source_file: str = "SuSyMaterialRecipeHandler.java",
    source_line: int = 98,
    ore_prefix: str = "dust/32",
    material: str = "incoloy_908",
    context: str = "Client thread",
) -> list[str]:
    return [
        _row(
            "ERROR",
            "GregTech",
            "Invalid amount of recipe outputs. Recipe outputs are empty.",
            context=context,
        ),
        _row("ERROR", "GregTech", "Stacktrace:", context=context),
        "java.lang.IllegalArgumentException: Invalid number of Outputs",
        _frame(
            "gregtech.api.recipes.RecipeMap",
            "postValidateRecipe",
            "RecipeMap.java",
            374,
        ),
        _frame(
            "gregtech.api.recipes.RecipeMap",
            "addRecipe",
            "RecipeMap.java",
            271,
        ),
        _frame(
            "gregtech.api.recipes.RecipeBuilder",
            "buildAndRegister",
            "RecipeBuilder.java",
            917,
        ),
        _frame(owner_class, owner_method, source_file, source_line),
        _row(
            "ERROR",
            "GregTech",
            (
                "Error happened during processing ore registration of prefix "
                f"{ore_prefix} and material {material}. Seems like cross-mod "
                "compatibility issue. Report to GTCEu github."
            ),
            context=context,
        ),
    ]


def _duplicate_furnace(
    *,
    owner_class: str = "gregtech.loaders.recipe.handlers.OreRecipeHandler",
    owner_method: str = "processOre",
    source_file: str = "OreRecipeHandler.java",
    source_line: int = 116,
    input_namespace: str = "biomesoplenty",
    input_display: str = "Ruby Ore",
    output_namespace: str = "gregtech",
    output_display: str = "Ruby",
) -> list[str]:
    return [
        _row("WARN", "GregTech", "Invalid Recipe Found"),
        (
            "java.lang.IllegalArgumentException: Tried to register duplicate "
            f"Furnace Recipe: 1x {input_namespace}:{input_display} -> "
            f"1x {output_namespace}:{output_display}, 0.5exp"
        ),
        _frame(
            "gregtech.api.recipes.ModHandler",
            "logInvalidRecipe",
            "ModHandler.java",
            755,
        ),
        _frame(
            "gregtech.api.recipes.ModHandler",
            "addSmeltingRecipe",
            "ModHandler.java",
            159,
        ),
        _frame(
            "gregtech.api.recipes.ModHandler",
            "addSmeltingRecipe",
            "ModHandler.java",
            135,
        ),
        _frame(owner_class, owner_method, source_file, source_line),
    ]


def _invalid_summary() -> list[str]:
    return [
        _row("FATAL", "GregTech Core", message)
        for message in (
            "Seems like invalid recipe was found.",
            "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
            "Ignoring invalid recipes and continuing loading",
            "Some things may lack recipes or have invalid ones, proceed at your own risk",
            "Report to GTCEu GitHub to get more help and fix the problem",
            "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
        )
    ]


CHECKPOINT_MARKER = "Forge Mod Loader has successfully loaded 209 mods"

SOURCE = {
    "launch_id": "sha256:" + "1" * 64,
    "evidence": {"label": "minecraft-latest-log", "sha256": "2" * 64},
    "checkpoint": {
        "id": "fml-client-loaded",
        "marker": CHECKPOINT_MARKER,
        "source": "minecraft-latest-log",
    },
}
PROFILE = {
    "profile_id": "workbench-pack:supersymmetry:gt-recipe-registration-v1",
    "maturity": "feature",
    "observer_sha256": "3" * 64,
}
COMPARISON_PROFILE = {
    "diagnostic_profile": PROFILE,
    "function": "compare_gt_recipe_registration_diagnostics",
    "report_format": (
        "workbench-supersymmetry-gt-recipe-registration-comparison-v1"
    ),
}


def _comparison_source(token: str) -> dict:
    return {
        "workspace": {
            "root_uri": "file:///workspace",
            "revision": "a" * 40,
            "launch_binding": {"state": "unverified-current-context"},
        },
        "project": {
            "name": "Supersymmetry",
            "version": "0.1.16.12",
            "minecraft_version": "1.12.2",
            "launch_binding": "matched-receipt-project-fields",
        },
        "pack_profile": {
            "profile_family_id": "workbench-pack:supersymmetry",
            "selected_profile": "supersymmetry",
            "document_sha256": "a" * 64,
            "launch_binding": {"state": "unverified-current-diagnostic-context"},
        },
        "launch_receipt": {
            "uri": f"file:///{token}.json",
            "sha256": token * 64,
            "size": 100,
        },
        "launch_id": "sha256:" + token * 64,
        "launch_outcome": "checkpoint-reached",
        "process_observation": {"state": "exited", "samples": 2},
        "checkpoint": {
            "id": "fml-client-loaded",
            "marker": CHECKPOINT_MARKER,
            "source": "minecraft-latest-log",
        },
        "evidence": {
            "label": "minecraft-latest-log",
            "state": "verified",
            "capture_uri": f"file:///{token}.log",
            "sha256": token * 64,
            "size": 100,
        },
    }


def _startup_text(lines: list[str]) -> str:
    return "\n".join([
        _row("INFO", "GregTech", "Registering recipes..."),
        *lines,
        _row("INFO", "FML", CHECKPOINT_MARKER),
    ]) + "\n"


def _diagnose(lines: list[str], *, source: dict = SOURCE) -> dict:
    return diagnostic.build_gt_recipe_registration_diagnostic(
        _startup_text(lines),
        source=source,
        profile=PROFILE,
    )


def _report(token: str, lines: list[str]) -> dict:
    return diagnostic.build_gt_recipe_registration_diagnostic(
        _startup_text(lines),
        source=_comparison_source(token),
        profile=PROFILE,
    )


class GtRecipeRegistrationDiagnosticTest(unittest.TestCase):
    def test_groups_complete_signals_by_line_stable_owner_method(self) -> None:
        lines = [
            *_empty_output(material="incoloy_908", source_line=98),
            *_empty_output(material="tungsten", source_line=105),
            *_duplicate_furnace(input_namespace="biomesoplenty"),
            *_duplicate_furnace(
                input_namespace="techguns",
                input_display="Copper Ore",
                output_display="Copper Ingot",
            ),
            *_invalid_summary(),
        ]

        first = _diagnose(lines)
        second = _diagnose(lines)

        self.assertEqual(first, second)
        self.assertEqual(first["format"], diagnostic.REPORT_FORMAT)
        self.assertEqual(first["state"], "attention")
        self.assertEqual(first["summary"]["complete_signal_count"], 4)
        self.assertEqual(first["summary"]["empty_output_count"], 2)
        self.assertEqual(first["summary"]["duplicate_furnace_count"], 2)
        self.assertEqual(first["summary"]["group_count"], 2)
        self.assertEqual(first["summary"]["incomplete_sequence_count"], 0)
        self.assertEqual(first["summary"]["matched_invalid_recipe_summary_count"], 1)
        self.assertEqual(first["scope"]["startup_boundary"]["state"], "complete")
        self.assertEqual(
            [
                (
                    group["reason_code"],
                    group["observed_owner_class"],
                    group["observed_owner_method"],
                    group["count"],
                )
                for group in first["groups"]
            ],
            [
                (
                    "empty-recipe-outputs",
                    "supersymmetry.loaders.recipes.SuSyMaterialRecipeHandler",
                    "processHIPPressing",
                    2,
                ),
                (
                    "duplicate-furnace-recipe",
                    "gregtech.loaders.recipe.handlers.OreRecipeHandler",
                    "processOre",
                    2,
                ),
            ],
        )
        empty = first["groups"][0]
        self.assertEqual(empty["context_count"], 2)
        self.assertEqual(
            [item["source_line"] for item in empty["contexts"]],
            [98, 105],
        )
        self.assertEqual(
            first["scope"]["group_identity"],
            [
                "kind",
                "reason_code",
                "observed_owner_class",
                "observed_owner_method",
            ],
        )

    def test_complete_summary_is_a_frontier_when_no_empty_output_matches(self) -> None:
        report = _diagnose(_invalid_summary())

        self.assertEqual(report["state"], "inconclusive")
        self.assertEqual(report["summary"]["complete_signal_count"], 0)
        self.assertEqual(
            report["summary"]["unmatched_invalid_recipe_summary_count"],
            1,
        )
        self.assertEqual(report["summary"]["incomplete_sequence_count"], 1)
        self.assertEqual(report["frontiers"][0]["reason_code"], "invalid-recipe-summary")

    def test_incomplete_or_wrong_thread_empty_output_is_a_frontier(self) -> None:
        complete = _empty_output()
        for length in range(1, len(complete)):
            with self.subTest(length=length):
                report = _diagnose(complete[:length])
                self.assertEqual(report["summary"]["complete_signal_count"], 0)
                self.assertEqual(report["summary"]["incomplete_sequence_count"], 1)
        wrong_thread = list(complete)
        wrong_thread[-1] = wrong_thread[-1].replace(
            "[Client thread/ERROR]", "[Worker/ERROR]"
        )
        report = _diagnose(wrong_thread)
        self.assertEqual(report["summary"]["incomplete_sequence_count"], 1)
        self.assertEqual(report["summary"]["complete_signal_count"], 0)

    def test_duplicate_requires_header_wrappers_owner_and_bounded_text(self) -> None:
        complete = _duplicate_furnace()
        for length in range(2, len(complete)):
            with self.subTest(length=length):
                report = _diagnose(complete[:length])
                self.assertEqual(report["summary"]["complete_signal_count"], 0)
                self.assertEqual(report["summary"]["incomplete_sequence_count"], 1)

        orphan = _diagnose([complete[1]])
        self.assertEqual(orphan["summary"]["incomplete_sequence_count"], 1)

        control = _duplicate_furnace(input_display="Ruby\x1b[31m Ore")
        rejected = _diagnose(control)
        self.assertEqual(rejected["summary"]["complete_signal_count"], 0)
        self.assertEqual(rejected["summary"]["incomplete_sequence_count"], 1)

    def test_ignores_nearby_out_of_scope_recipe_messages(self) -> None:
        lines = [
            _row(
                "INFO",
                "FML",
                "Invalid recipe found with multiple oredict ingredients in the same ingredient...",
            ),
            _row("ERROR", "FML", "Parsing error loading recipe gaspunk:syringe"),
            _row(
                "INFO",
                "FML",
                "Ignored smelting recipe with conflicting input: a = b",
            ),
            _row("WARN", "GregTech", "Invalid Recipe Found"),
            "java.lang.IllegalArgumentException: Recipe cannot be empty",
        ]
        report = _diagnose(lines)

        self.assertEqual(report["state"], "no-signals-observed")
        self.assertEqual(report["summary"]["complete_signal_count"], 0)
        self.assertEqual(report["summary"]["incomplete_sequence_count"], 0)

    def test_requires_unique_ordered_receipt_boundaries_and_scopes_events(self) -> None:
        start = _row("INFO", "GregTech", "Registering recipes...")
        checkpoint = _row("INFO", "FML", CHECKPOINT_MARKER)
        target = _empty_output()
        invalid_windows = {
            "missing-start": [*target, checkpoint],
            "repeated-start": [start, start, *target, checkpoint],
            "missing-checkpoint": [start, *target],
            "repeated-checkpoint": [start, *target, checkpoint, checkpoint],
            "reversed": [checkpoint, start, *target],
        }
        for name, lines in invalid_windows.items():
            with self.subTest(name=name):
                report = diagnostic.build_gt_recipe_registration_diagnostic(
                    "\n".join(lines) + "\n",
                    source=SOURCE,
                    profile=PROFILE,
                )
                self.assertEqual(report["state"], "inconclusive")
                self.assertEqual(report["summary"]["complete_signal_count"], 0)
                self.assertTrue(all(
                    item["reason_code"] == "startup-registration-boundary"
                    for item in report["frontiers"]
                ))
                self.assertEqual(
                    report["scope"]["startup_boundary"]["state"],
                    "incomplete",
                )

        unsupported_source = {
            **SOURCE,
            "checkpoint": {
                **SOURCE["checkpoint"],
                "source": "runtime-stdout",
            },
        }
        unsupported = diagnostic.build_gt_recipe_registration_diagnostic(
            _startup_text(target),
            source=unsupported_source,
            profile=PROFILE,
        )
        self.assertEqual(unsupported["state"], "inconclusive")
        self.assertEqual(unsupported["summary"]["complete_signal_count"], 0)
        self.assertEqual(
            unsupported["frontiers"][0]["reason_code"],
            "startup-registration-boundary",
        )

        scoped = diagnostic.build_gt_recipe_registration_diagnostic(
            "\n".join([
                *_duplicate_furnace(input_namespace="before"),
                start,
                *_empty_output(),
                checkpoint,
                *_duplicate_furnace(input_namespace="after"),
            ]) + "\n",
            source=SOURCE,
            profile=PROFILE,
        )
        self.assertEqual(scoped["summary"]["complete_signal_count"], 1)
        self.assertEqual(scoped["summary"]["empty_output_count"], 1)
        self.assertEqual(scoped["summary"]["duplicate_furnace_count"], 0)
        self.assertEqual(scoped["scope"]["startup_boundary"]["state"], "complete")

        for name, target, prefix_length in (
            ("empty-output", _empty_output(), 1),
            ("duplicate-furnace", _duplicate_furnace(), 1),
            ("fatal-summary", _invalid_summary(), 1),
        ):
            with self.subTest(straddles_checkpoint=name):
                report = diagnostic.build_gt_recipe_registration_diagnostic(
                    "\n".join([
                        start,
                        *target[:prefix_length],
                        checkpoint,
                        *target[prefix_length:],
                    ]) + "\n",
                    source=SOURCE,
                    profile=PROFILE,
                )
                self.assertEqual(report["summary"]["complete_signal_count"], 0)
                self.assertGreater(
                    report["summary"]["incomplete_sequence_count"],
                    0,
                )

    def test_frontiers_examples_contexts_and_groups_are_bounded(self) -> None:
        orphan = _duplicate_furnace()[1]
        frontier_report = _diagnose([orphan] * 51)
        self.assertEqual(frontier_report["summary"]["incomplete_sequence_count"], 51)
        self.assertEqual(frontier_report["summary"]["emitted_frontier_count"], 50)
        self.assertTrue(frontier_report["summary"]["frontiers_truncated"])

        example_report = _diagnose([
            line
            for ordinal in range(4)
            for line in _duplicate_furnace(input_display=f"Ruby Ore {ordinal}")
        ])
        self.assertEqual(example_report["groups"][0]["count"], 4)
        self.assertEqual(len(example_report["groups"][0]["examples"]), 3)
        self.assertEqual(example_report["groups"][0]["context_count"], 1)

        many_groups = _diagnose([
            line
            for ordinal in range(101)
            for line in _duplicate_furnace(
                owner_class=f"example.Owner{ordinal}",
                owner_method="register",
                source_file=f"Owner{ordinal}.java",
            )
        ])
        self.assertEqual(many_groups["summary"]["group_count"], 101)
        self.assertEqual(many_groups["summary"]["emitted_group_count"], 100)
        self.assertTrue(many_groups["summary"]["groups_truncated"])

    def test_context_and_distinct_group_hard_bounds_fail_closed(self) -> None:
        old_context_bound = diagnostic.MAX_DISTINCT_CONTEXTS_PER_GROUP
        old_group_bound = diagnostic.MAX_DISTINCT_GROUPS
        old_event_bound = diagnostic.MAX_COMPLETE_EVENTS
        try:
            diagnostic.MAX_DISTINCT_CONTEXTS_PER_GROUP = 2
            with self.assertRaisesRegex(
                diagnostic.GtRecipeRegistrationDiagnosticError,
                "per-group registration-context bound",
            ):
                _diagnose([
                    line
                    for ordinal in range(3)
                    for line in _empty_output(
                        material=f"material_{ordinal}",
                    )
                ])
            diagnostic.MAX_DISTINCT_GROUPS = 2
            with self.assertRaisesRegex(
                diagnostic.GtRecipeRegistrationDiagnosticError,
                "group diagnostic bound",
            ):
                _diagnose([
                    line
                    for ordinal in range(3)
                    for line in _duplicate_furnace(
                        owner_class=f"example.Owner{ordinal}",
                    )
                ])
            diagnostic.MAX_COMPLETE_EVENTS = 2
            with self.assertRaisesRegex(
                diagnostic.GtRecipeRegistrationDiagnosticError,
                "event diagnostic bound",
            ):
                _diagnose([
                    line
                    for _ in range(3)
                    for line in _duplicate_furnace()
                ])
        finally:
            diagnostic.MAX_DISTINCT_CONTEXTS_PER_GROUP = old_context_bound
            diagnostic.MAX_DISTINCT_GROUPS = old_group_bound
            diagnostic.MAX_COMPLETE_EVENTS = old_event_bound

    def test_comparison_is_count_only_and_line_shift_stable(self) -> None:
        baseline = _report("b", [
            *_empty_output(source_line=98),
            *_duplicate_furnace(),
        ])
        candidate = _report("c", [
            *_empty_output(source_line=140, material="incoloy_908"),
            *_empty_output(source_line=147, material="tungsten"),
            *_duplicate_furnace(source_line=200),
            *_duplicate_furnace(
                owner_class="supersymmetry.loaders.SuSyRecipes",
                owner_method="registerSmelting",
                source_file="SuSyRecipes.java",
                source_line=40,
            ),
        ])

        first = diagnostic.compare_gt_recipe_registration_diagnostics(
            baseline, candidate, profile=COMPARISON_PROFILE
        )
        second = diagnostic.compare_gt_recipe_registration_diagnostics(
            baseline, candidate, profile=COMPARISON_PROFILE
        )

        self.assertEqual(first, second)
        self.assertEqual(first["format"], diagnostic.COMPARISON_FORMAT)
        self.assertEqual(first["state"], "more-observed")
        self.assertEqual(first["summary"]["baseline_complete_signal_count"], 2)
        self.assertEqual(first["summary"]["candidate_complete_signal_count"], 4)
        self.assertEqual(first["summary"]["newly_observed_group_count"], 1)
        self.assertEqual(first["summary"]["increased_group_count"], 1)
        self.assertEqual(first["summary"]["same_count_group_count"], 1)
        self.assertEqual(
            [item["classification"] for item in first["groups"]],
            ["newly-observed", "increased", "same-count"],
        )
        increased = first["groups"][1]
        self.assertEqual(increased["reason_code"], "empty-recipe-outputs")
        self.assertEqual(increased["baseline_count"], 1)
        self.assertEqual(increased["candidate_count"], 2)
        self.assertEqual(increased["delta"], 1)

    def test_comparison_rejects_frontiers_truncation_stale_id_and_same_receipt(self) -> None:
        baseline = _report("b", [])
        incomplete = _report("c", [_empty_output()[0]])
        with self.assertRaisesRegex(
            diagnostic.GtRecipeRegistrationDiagnosticError,
            "complete, untruncated launch observation",
        ):
            diagnostic.compare_gt_recipe_registration_diagnostics(
                baseline, incomplete, profile=COMPARISON_PROFILE
            )

        truncated = _report(
            "c",
            [
                line
                for ordinal in range(101)
                for line in _duplicate_furnace(
                    owner_class=f"example.Owner{ordinal}",
                )
            ],
        )
        with self.assertRaisesRegex(
            diagnostic.GtRecipeRegistrationDiagnosticError,
            "complete, untruncated launch observation",
        ):
            diagnostic.compare_gt_recipe_registration_diagnostics(
                baseline, truncated, profile=COMPARISON_PROFILE
            )

        candidate = _report("c", [])
        stale = dict(candidate)
        stale["diagnostic_id"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(
            diagnostic.GtRecipeRegistrationDiagnosticError,
            "incompatible identity",
        ):
            diagnostic.compare_gt_recipe_registration_diagnostics(
                baseline, stale, profile=COMPARISON_PROFILE
            )
        with self.assertRaisesRegex(
            diagnostic.GtRecipeRegistrationDiagnosticError,
            "not distinct compatible observations",
        ):
            diagnostic.compare_gt_recipe_registration_diagnostics(
                baseline, baseline, profile=COMPARISON_PROFILE
            )

    def test_retained_current_log_matches_exact_census_when_available(self) -> None:
        retained = (
            ROOT
            / ".workbench/evidence/runtime/471ef46788be2875/launches/"
            "workbench-supersymmetry-9bed91599955-20260814T214243857944Z/"
            "final/minecraft-latest.log"
        )
        if not retained.is_file():
            self.skipTest("retained latest.log is not available")
        report = diagnostic.build_gt_recipe_registration_diagnostic(
            retained.read_text(encoding="utf-8"),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["summary"]["complete_signal_count"], 22)
        self.assertEqual(report["summary"]["empty_output_count"], 14)
        self.assertEqual(report["summary"]["duplicate_furnace_count"], 8)
        self.assertEqual(report["summary"]["group_count"], 2)
        self.assertEqual(report["summary"]["incomplete_sequence_count"], 0)
        self.assertEqual(
            [group["count"] for group in report["groups"]],
            [14, 8],
        )


if __name__ == "__main__":
    unittest.main()
