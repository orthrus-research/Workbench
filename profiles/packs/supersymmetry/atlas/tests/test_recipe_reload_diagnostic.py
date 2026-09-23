"""Tests for the bounded Supersymmetry recipe reload diagnostic."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[5]
MODULE_PATH = (
    ROOT
    / "profiles/packs/supersymmetry/atlas/src/recipe_reload_diagnostic.py"
)
SPEC = importlib.util.spec_from_file_location(
    "recipe_reload_diagnostic",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
recipe_reload_diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe_reload_diagnostic)


def _row(level: str, logger: str, message: str) -> str:
    return f"[07:31:30] [SERVER/{level}] [{logger}]: {message}"


def _conflict(logger: str, recipe_map: str, token: str) -> list[str]:
    return [
        _row(
            "WARN",
            logger,
            (
                "Recipe duplicate or conflict found in RecipeMap "
                f"{recipe_map} and was not added. See next lines for details"
            ),
        ),
        _row("WARN", logger, f"Attempted to add Recipe: Recipe@{token}"),
        _row("WARN", logger, f"Which conflicts with: Recipe@{token}f"),
    ]


def _completion() -> str:
    return _row(
        "INFO",
        "supersymmetry",
        "Groovy scripts took 1ms to compile and 2ms to run in postInit.",
    )


SOURCE = {
    "launch_id": "sha256:" + ("1" * 64),
    "evidence": {"label": "minecraft-groovy-log", "sha256": "2" * 64},
}
PROFILE = {
    "profile_id": "workbench-pack:supersymmetry:recipe-reload-diagnostic-v1",
    "maturity": "experimental",
    "observer_sha256": "3" * 64,
}
COMPARISON_PROFILE = {
    "diagnostic_profile": PROFILE,
    "function": "compare_recipe_reload_diagnostics",
    "report_format": (
        "workbench-supersymmetry-groovy-conflict-group-comparison-v1"
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
            "version": "test",
            "minecraft_version": "1.12.2",
            "launch_binding": "matched-receipt-project-fields",
        },
        "pack_profile": {
            "profile_family_id": "workbench-pack:supersymmetry",
            "selected_profile": "supersymmetry",
            "document_sha256": "a" * 64,
            "launch_binding": {
                "state": "unverified-current-diagnostic-context"
            },
        },
        "launch_receipt": {
            "uri": f"file:///{token}.json",
            "sha256": token * 64,
            "size": 100,
        },
        "launch_id": "sha256:" + token * 64,
        "launch_outcome": "checkpoint-reached",
        "process_observation": {"state": "exited", "samples": 2},
        "evidence": {
            "label": "minecraft-groovy-log",
            "state": "verified",
            "capture_uri": f"file:///{token}.log",
            "sha256": token * 64,
            "size": 100,
        },
    }


def _cold_report(
    token: str,
    groups: list[tuple[str, str, int]],
    *,
    unidentified: frozenset[tuple[str, str]] = frozenset(),
):
    lines = [
        _row("INFO", "supersymmetry", "Running scripts in loader 'postInit'")
    ]
    ordinal = 0
    for logger, recipe_map, count in groups:
        for _ in range(count):
            event = _conflict(logger, recipe_map, f"{token}{ordinal}")
            if (logger, recipe_map) in unidentified:
                event[-1] = _row(
                    "WARN", logger, "Could not find exact duplicate/conflict."
                )
            lines.extend(event)
            ordinal += 1
    lines.append(_completion())
    return recipe_reload_diagnostic.build_recipe_reload_diagnostic(
        "\n".join(lines) + "\n",
        source=_comparison_source(token),
        profile=PROFILE,
    )


class RecipeReloadDiagnosticTest(unittest.TestCase):
    def test_groups_initial_and_reload_conflicts_with_restart_guidance(self) -> None:
        lines = [
            _row("INFO", "supersymmetry", "Running scripts in loader 'postInit'"),
            *_conflict("postInit.chemistry.Catalysts", "blender", "aa"),
            _row("WARN", "unrelated", "This warning is outside the grammar"),
            _row("INFO", "supersymmetry", "Running scripts in loader 'postInit'"),
            *_conflict(
                "postInit.materials.metallurgy.Quenching",
                "quencher",
                "ba",
            ),
            *_conflict(
                "postInit.materials.metallurgy.Quenching",
                "quencher",
                "bb",
            ),
            *_conflict("postInit.gameplay.BoilerTweaks", "boiler", "ca"),
            _row(
                "WARN",
                "postInit.broken",
                (
                    "Recipe duplicate or conflict found in RecipeMap mixer "
                    "and was not added. See next lines for details"
                ),
            ),
            _row("INFO", "postInit.broken", "not an attempted recipe"),
        ]
        text = "\n".join(lines) + "\n"

        first = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            text,
            source=SOURCE,
            profile=PROFILE,
        )
        second = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            text,
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            first["format"],
            "workbench-supersymmetry-recipe-reload-diagnostic-v1",
        )
        self.assertEqual(first["operation_class"], "read-only")
        self.assertEqual(first["state"], "attention")
        self.assertEqual(first["scope"], {
            "loader": "postInit",
            "post_init_execution_count": 2,
            "completed_post_init_execution_count": 0,
            "initial_execution_ordinal": 1,
            "initial_execution_completed": False,
            "reload_execution_count": 1,
            "completed_reload_execution_count": 0,
        })
        self.assertEqual(first["summary"]["complete_conflict_count"], 4)
        self.assertEqual(first["summary"]["initial_conflict_count"], 1)
        self.assertEqual(first["summary"]["reload_conflict_count"], 3)
        self.assertEqual(first["summary"]["group_count"], 3)
        self.assertEqual(first["summary"]["incomplete_sequence_count"], 1)
        self.assertEqual(
            [
                (
                    group["script_logger"],
                    group["recipe_map"],
                    group["counts"]["total"],
                )
                for group in first["groups"]
            ],
            [
                ("postInit.materials.metallurgy.Quenching", "quencher", 2),
                ("postInit.gameplay.BoilerTweaks", "boiler", 1),
                ("postInit.chemistry.Catalysts", "blender", 1),
            ],
        )
        self.assertEqual(
            first["groups"][0]["examples"][0]["lifecycle"],
            "reload",
        )
        self.assertEqual(
            first["recommendation"]["state"],
            "restart-required",
        )
        self.assertEqual(
            first["frontiers"][0]["reason"],
            "attempt-row-does-not-share-warning-logger",
        )

    def test_reports_clean_only_with_an_observed_initial_post_init(self) -> None:
        text = "\n".join([
            _row("INFO", "supersymmetry", "Running scripts in loader 'postInit'"),
            _row("WARN", "unrelated", "This warning is outside the grammar"),
            _completion(),
        ])

        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            text,
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["state"], "no-groovy-conflicts-observed")
        self.assertEqual(report["summary"]["complete_conflict_count"], 0)
        self.assertEqual(report["recommendation"]["state"], "no-action-from-this-diagnostic")

    def test_post_init_start_without_completion_is_inconclusive(self) -> None:
        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            _row(
                "INFO",
                "supersymmetry",
                "Running scripts in loader 'postInit'",
            ),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["state"], "inconclusive")
        self.assertFalse(report["scope"]["initial_execution_completed"])
        self.assertEqual(
            report["recommendation"]["state"],
            "capture-complete-post-init-log",
        )

    def test_does_not_call_a_log_without_post_init_clear(self) -> None:
        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            _row("WARN", "unrelated", "warning") + "\n",
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["state"], "inconclusive")
        self.assertEqual(report["scope"]["post_init_execution_count"], 0)

    def test_retains_conflicts_without_loader_boundary_as_unbound(self) -> None:
        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            "\n".join(_conflict("postInit.example.Script", "mixer", "aa")),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["state"], "attention")
        self.assertEqual(report["summary"]["unbound_conflict_count"], 1)
        self.assertEqual(
            report["recommendation"]["state"],
            "inspect-unbound-conflicts",
        )

    def test_script_logger_cannot_spoof_a_post_init_boundary(self) -> None:
        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            "\n".join([
                _row("INFO", "postInit.example.Script", "Running scripts in loader 'postInit'"),
                *_conflict("postInit.example.Script", "mixer", "aa"),
            ]),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["scope"]["post_init_execution_count"], 0)
        self.assertEqual(report["summary"]["unbound_conflict_count"], 1)

    def test_completion_closes_the_active_post_init_epoch(self) -> None:
        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            "\n".join([
                _row(
                    "INFO",
                    "supersymmetry",
                    "Running scripts in loader 'postInit'",
                ),
                _completion(),
                *_conflict("postInit.late.Script", "mixer", "late"),
            ]),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertTrue(report["scope"]["initial_execution_completed"])
        self.assertEqual(report["summary"]["initial_conflict_count"], 0)
        self.assertEqual(report["summary"]["unbound_conflict_count"], 1)

    def test_rejects_terminal_control_characters_in_rendered_identities(self) -> None:
        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            "\n".join([
                _row(
                    "INFO",
                    "supersymmetry",
                    "Running scripts in loader 'postInit'",
                ),
                *_conflict("postInit.Bad\x1b[31m", "mixer", "control"),
                _completion(),
            ]),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["summary"]["complete_conflict_count"], 0)
        self.assertEqual(report["summary"]["incomplete_sequence_count"], 1)
        self.assertEqual(
            report["frontiers"][0]["reason"],
            "script-logger-or-recipe-map-exceeds-the-identity-bound",
        )

    def test_counts_all_frontiers_while_retaining_only_the_first_fifty(self) -> None:
        lines = [
            _row(
                "INFO",
                "supersymmetry",
                "Running scripts in loader 'postInit'",
            )
        ]
        for ordinal in range(60):
            lines.extend((
                _row(
                    "WARN",
                    f"postInit.Broken{ordinal}",
                    (
                        "Recipe duplicate or conflict found in RecipeMap mixer "
                        "and was not added. See next lines for details"
                    ),
                ),
                _row("INFO", "unrelated", "not a continuation"),
            ))
        lines.append(_completion())

        report = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            "\n".join(lines),
            source=SOURCE,
            profile=PROFILE,
        )

        self.assertEqual(report["summary"]["incomplete_sequence_count"], 60)
        self.assertEqual(report["summary"]["emitted_frontier_count"], 50)
        self.assertTrue(report["summary"]["frontiers_truncated"])
        self.assertEqual(len(report["frontiers"]), 50)
        self.assertTrue(any(
            limitation.startswith("10 incomplete sequence frontier(s)")
            for limitation in report["limitations"]
        ))

    def test_compares_complete_cold_start_group_counts_without_net_masking(self) -> None:
        baseline = _cold_report("b", [
            ("postInit.Alpha", "mixer", 2),
            ("postInit.Beta", "quencher", 1),
            ("postInit.Gamma", "boiler", 3),
            ("postInit.Delta", "assembler", 3),
        ])
        candidate = _cold_report("c", [
            ("postInit.Alpha", "mixer", 2),
            ("postInit.Beta", "quencher", 4),
            ("postInit.Delta", "assembler", 1),
            ("postInit.Epsilon", "chemical_bath", 2),
        ])

        first = recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
        )
        second = recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["state"], "more-observed")
        self.assertEqual(
            first["summary"]["baseline_complete_conflict_count"], 9
        )
        self.assertEqual(
            first["summary"]["candidate_complete_conflict_count"], 9
        )
        self.assertEqual(first["summary"]["net_conflict_count_delta"], 0)
        self.assertEqual(
            [group["classification"] for group in first["groups"]],
            [
                "newly-observed",
                "increased",
                "decreased",
                "no-longer-observed",
                "same-count",
            ],
        )
        self.assertEqual(
            [group["delta"] for group in first["groups"]],
            [2, 3, -2, -3, 0],
        )

    def test_surfaces_same_count_resolution_shape_change(self) -> None:
        group = ("postInit.Alpha", "mixer")
        baseline = _cold_report("b", [(*group, 1)])
        candidate = _cold_report(
            "c",
            [(*group, 1)],
            unidentified=frozenset({group}),
        )

        result = recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
        )

        self.assertEqual(
            result["state"], "same-counts-with-resolution-count-changes"
        )
        self.assertEqual(result["groups"][0]["classification"], "same-count")
        self.assertTrue(result["groups"][0]["resolution_counts_changed"])
        self.assertEqual(
            result["summary"]["same_count_resolution_changed_group_count"],
            1,
        )

    def test_rejects_reload_contaminated_comparison_input(self) -> None:
        baseline = _cold_report("b", [])
        candidate = recipe_reload_diagnostic.build_recipe_reload_diagnostic(
            "\n".join([
                _row(
                    "INFO",
                    "supersymmetry",
                    "Running scripts in loader 'postInit'",
                ),
                _completion(),
                _row(
                    "INFO",
                    "supersymmetry",
                    "Running scripts in loader 'postInit'",
                ),
                *_conflict("postInit.Reload", "mixer", "reload"),
                _completion(),
            ]),
            source=_comparison_source("c"),
            profile=PROFILE,
        )

        with self.assertRaisesRegex(
            recipe_reload_diagnostic.RecipeReloadDiagnosticError,
            "complete, untruncated initial postInit",
        ):
            recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
                baseline,
                candidate,
                profile=COMPARISON_PROFILE,
            )

    def test_rejects_truncated_group_set(self) -> None:
        baseline = _cold_report("b", [])
        candidate = _cold_report(
            "c",
            [
                (f"postInit.Script{ordinal}", f"map_{ordinal}", 1)
                for ordinal in range(101)
            ],
        )
        self.assertTrue(candidate["summary"]["groups_truncated"])

        with self.assertRaisesRegex(
            recipe_reload_diagnostic.RecipeReloadDiagnosticError,
            "complete, untruncated initial postInit",
        ):
            recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
                baseline,
                candidate,
                profile=COMPARISON_PROFILE,
            )

    def test_emits_complete_two_hundred_group_union(self) -> None:
        baseline = _cold_report(
            "b",
            [
                (f"postInit.Baseline{ordinal}", f"baseline_{ordinal}", 1)
                for ordinal in range(100)
            ],
        )
        candidate = _cold_report(
            "c",
            [
                (f"postInit.Candidate{ordinal}", f"candidate_{ordinal}", 1)
                for ordinal in range(100)
            ],
        )

        result = recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
        )

        self.assertEqual(result["summary"]["group_count"], 200)
        self.assertEqual(result["summary"]["newly_observed_group_count"], 100)
        self.assertEqual(
            result["summary"]["no_longer_observed_group_count"], 100
        )
        self.assertEqual(len(result["groups"]), 200)

    def test_rejects_stale_input_identity_and_same_receipt(self) -> None:
        baseline = _cold_report("b", [])
        candidate = _cold_report("c", [])
        stale = dict(candidate)
        stale["diagnostic_id"] = "sha256:" + "0" * 64

        with self.assertRaisesRegex(
            recipe_reload_diagnostic.RecipeReloadDiagnosticError,
            "incompatible identity",
        ):
            recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
                baseline,
                stale,
                profile=COMPARISON_PROFILE,
            )
        with self.assertRaisesRegex(
            recipe_reload_diagnostic.RecipeReloadDiagnosticError,
            "not distinct compatible observations",
        ):
            recipe_reload_diagnostic.compare_recipe_reload_diagnostics(
                baseline,
                baseline,
                profile=COMPARISON_PROFILE,
            )


if __name__ == "__main__":
    unittest.main()
