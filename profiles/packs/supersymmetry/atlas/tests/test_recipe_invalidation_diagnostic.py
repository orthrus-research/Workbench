"""Tests for the preview two-channel recipe invalidation composition."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[5]


def _module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


composer = _module(
    "recipe_invalidation_diagnostic",
    "profiles/packs/supersymmetry/atlas/src/recipe_invalidation_diagnostic.py",
)
groovy_observer = _module(
    "recipe_reload_diagnostic_for_composition",
    "profiles/packs/supersymmetry/atlas/src/recipe_reload_diagnostic.py",
)
java_observer = _module(
    "gt_recipe_registration_for_composition",
    "profiles/packs/supersymmetry/atlas/src/gt_recipe_registration_diagnostic.py",
)


def _row(level: str, logger: str, message: str) -> str:
    return f"[07:31:30] [Client thread/{level}] [{logger}]: {message}"


GROOVY_PROFILE = {
    "channel_id": "groovy_postinit",
    "evidence_label": "minecraft-groovy-log",
    "diagnostic_function": "build_recipe_reload_diagnostic",
    "comparison_function": "compare_recipe_reload_diagnostics",
    "diagnostic_format": "workbench-supersymmetry-recipe-reload-diagnostic-v1",
    "comparison_format": "workbench-supersymmetry-groovy-conflict-group-comparison-v1",
    "observer_uri": "file:///groovy.py",
    "observer_sha256": "1" * 64,
    "observer_size": 1,
}
JAVA_PROFILE = {
    "channel_id": "gt_startup_registration",
    "evidence_label": "minecraft-latest-log",
    "diagnostic_function": "build_gt_recipe_registration_diagnostic",
    "comparison_function": "compare_gt_recipe_registration_diagnostics",
    "diagnostic_format": "workbench-supersymmetry-gt-recipe-registration-diagnostic-v1",
    "comparison_format": "workbench-supersymmetry-gt-recipe-registration-comparison-v1",
    "observer_uri": "file:///java.py",
    "observer_sha256": "2" * 64,
    "observer_size": 1,
}
PROFILE = {
    "profile_id": "workbench-pack:supersymmetry:recipe-invalidation-diagnostic-v2",
    "pack_profile_id": "workbench-pack:supersymmetry",
    "capability_maturity": "preview",
    "runtime_support": "provisional",
    "diagnostic_format": "workbench-supersymmetry-recipe-invalidation-diagnostic-v2",
    "comparison_format": "workbench-supersymmetry-recipe-invalidation-comparison-v2",
    "composer": {
        "diagnostic_function": "build_recipe_invalidation_diagnostic",
        "comparison_function": "compare_recipe_invalidation_diagnostics",
        "observer_uri": "file:///composer.py",
        "observer_sha256": "3" * 64,
        "observer_size": 1,
    },
    "channels": {
        "groovy_postinit": GROOVY_PROFILE,
        "gt_startup_registration": JAVA_PROFILE,
    },
}
COMPARISON_PROFILE = {
    "diagnostic_profile": PROFILE,
    "function": "compare_recipe_invalidation_diagnostics",
    "report_format": "workbench-supersymmetry-recipe-invalidation-comparison-v2",
}


def _source(token: str, evidence_label: str) -> dict:
    return {
        "workspace": {"root_uri": "file:///workspace", "revision": "a" * 40},
        "project": {
            "name": "Supersymmetry",
            "version": "test",
            "minecraft_version": "1.12.2",
            "launch_binding": "matched-receipt-project-fields",
        },
        "pack_profile": {
            "profile_family_id": "workbench-pack:supersymmetry",
            "selected_profile": "cleanroom-provisional",
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
            "marker": "Forge Mod Loader has successfully loaded 210 mods",
            "source": "minecraft-latest-log",
        },
        "evidence": {
            "label": evidence_label,
            "state": "verified",
            "capture_uri": f"file:///{token}-{evidence_label}.log",
            "sha256": token * 64,
            "size": 100,
        },
    }


def _groovy_report(token: str, count: int) -> dict:
    lines = [_row("INFO", "supersymmetry", "Running scripts in loader 'postInit'")]
    for ordinal in range(count):
        lines.extend((
            _row(
                "WARN",
                "postInit.Example",
                "Recipe duplicate or conflict found in RecipeMap mixer and was not added. See next lines for details",
            ),
            _row("WARN", "postInit.Example", f"Attempted to add Recipe: Recipe@{token}{ordinal}"),
            _row("WARN", "postInit.Example", f"Which conflicts with: Recipe@other{ordinal}"),
        ))
    lines.append(_row(
        "INFO",
        "supersymmetry",
        "Groovy scripts took 1ms to compile and 2ms to run in postInit.",
    ))
    return groovy_observer.build_recipe_reload_diagnostic(
        "\n".join(lines),
        source=_source(token, "minecraft-groovy-log"),
        profile=GROOVY_PROFILE,
    )


def _groovy_reload_report(token: str) -> dict:
    lines = []
    for _ in range(2):
        lines.extend((
            _row("INFO", "supersymmetry", "Running scripts in loader 'postInit'"),
            _row(
                "INFO",
                "supersymmetry",
                "Groovy scripts took 1ms to compile and 2ms to run in postInit.",
            ),
        ))
    return groovy_observer.build_recipe_reload_diagnostic(
        "\n".join(lines),
        source=_source(token, "minecraft-groovy-log"),
        profile=GROOVY_PROFILE,
    )


def _java_report(
    token: str,
    duplicate_count: int,
    *,
    incomplete: bool = False,
) -> dict:
    marker = "Forge Mod Loader has successfully loaded 210 mods"
    lines = [_row("INFO", "GregTech", "Registering recipes...")]
    for ordinal in range(duplicate_count):
        lines.extend((
            _row("WARN", "GregTech", "Invalid Recipe Found"),
            (
                "java.lang.IllegalArgumentException: Tried to register duplicate "
                f"Furnace Recipe: 1x example:Ore {ordinal} -> 1x gregtech:Ingot, 0.5exp"
            ),
            "\tat gregtech.api.recipes.ModHandler.logInvalidRecipe(ModHandler.java:755)",
            "\tat gregtech.api.recipes.ModHandler.addSmeltingRecipe(ModHandler.java:159)",
            "\tat gregtech.api.recipes.ModHandler.addSmeltingRecipe(ModHandler.java:135)",
            "\tat example.RecipeLoader.register(RecipeLoader.java:42)",
        ))
    if incomplete:
        lines.append(_row(
            "ERROR",
            "GregTech",
            "Invalid amount of recipe outputs. Recipe outputs are empty.",
        ))
    lines.append(_row("INFO", "FML", marker))
    return java_observer.build_gt_recipe_registration_diagnostic(
        "\n".join(lines),
        source=_source(token, "minecraft-latest-log"),
        profile=JAVA_PROFILE,
    )


def _combined(
    token: str,
    groovy_count: int,
    java_count: int,
    *,
    java_incomplete: bool = False,
) -> dict:
    groovy = _groovy_report(token, groovy_count)
    java = _java_report(token, java_count, incomplete=java_incomplete)
    source = _source(token, "combined")
    source["evidence"] = {
        "groovy_postinit": groovy["source"]["evidence"],
        "gt_startup_registration": java["source"]["evidence"],
    }
    return composer.build_recipe_invalidation_diagnostic(
        source=source,
        profile=PROFILE,
        channels={
            "groovy_postinit": groovy,
            "gt_startup_registration": java,
        },
    )


class RecipeInvalidationDiagnosticTest(unittest.TestCase):
    def test_composes_channels_without_an_aggregate_count(self) -> None:
        report = _combined("a", 1, 2)

        self.assertEqual(report["state"], "attention")
        self.assertNotIn("summary", report)
        self.assertNotIn("groups", report)
        self.assertEqual(
            report["channels"]["groovy_postinit"]["summary"]["complete_conflict_count"],
            1,
        )
        self.assertEqual(
            report["channels"]["gt_startup_registration"]["summary"]["complete_signal_count"],
            2,
        )
        self.assertTrue(any(
            "never summed" in limitation for limitation in report["limitations"]
        ))
        schema = json.loads((
            ROOT
            / "profiles/packs/supersymmetry/diagnostics/recipe-invalidation-diagnostic-v2.schema.json"
        ).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(report)

    def test_attention_is_incomplete_when_a_signal_channel_has_a_frontier(self) -> None:
        report = _combined("a", 0, 1, java_incomplete=True)

        self.assertEqual(
            report["channels"]["gt_startup_registration"]["state"],
            "attention",
        )
        self.assertEqual(
            report["channels"]["gt_startup_registration"]["summary"][
                "incomplete_sequence_count"
            ],
            1,
        )
        self.assertEqual(report["state"], "attention-incomplete")
        self.assertEqual(
            report["recommendation"]["state"],
            "inspect-signals-and-recapture-complete-evidence",
        )

    def test_reload_without_conflicts_is_not_mislabeled_as_signal_attention(self) -> None:
        groovy = _groovy_reload_report("a")
        java = _java_report("a", 0)
        source = _source("a", "combined")
        source["evidence"] = {
            "groovy_postinit": groovy["source"]["evidence"],
            "gt_startup_registration": java["source"]["evidence"],
        }
        report = composer.build_recipe_invalidation_diagnostic(
            source=source,
            profile=PROFILE,
            channels={
                "groovy_postinit": groovy,
                "gt_startup_registration": java,
            },
        )

        self.assertEqual(groovy["summary"]["complete_conflict_count"], 0)
        self.assertEqual(java["summary"]["complete_signal_count"], 0)
        self.assertEqual(report["state"], "inconclusive")
        self.assertEqual(report["recommendation"]["state"], "restart-required")

    def test_comparison_keeps_channel_deltas_separate(self) -> None:
        baseline = _combined("b", 0, 1)
        candidate = _combined("c", 1, 3)
        groovy_comparison = groovy_observer.compare_recipe_reload_diagnostics(
            baseline["channels"]["groovy_postinit"],
            candidate["channels"]["groovy_postinit"],
            profile={
                "diagnostic_profile": GROOVY_PROFILE,
                "function": "compare_recipe_reload_diagnostics",
                "report_format": GROOVY_PROFILE["comparison_format"],
            },
        )
        java_comparison = java_observer.compare_gt_recipe_registration_diagnostics(
            baseline["channels"]["gt_startup_registration"],
            candidate["channels"]["gt_startup_registration"],
            profile={
                "diagnostic_profile": JAVA_PROFILE,
                "function": "compare_gt_recipe_registration_diagnostics",
                "report_format": JAVA_PROFILE["comparison_format"],
            },
        )

        result = composer.compare_recipe_invalidation_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
            compatibility={"state": "comparable", "findings": []},
            channels={
                "groovy_postinit": groovy_comparison,
                "gt_startup_registration": java_comparison,
            },
        )

        self.assertEqual(result["state"], "more-observed")
        self.assertNotIn("summary", result)
        self.assertNotIn("groups", result)
        self.assertEqual(
            result["channels"]["groovy_postinit"]["summary"]["candidate_complete_conflict_count"],
            1,
        )
        self.assertEqual(
            result["channels"]["gt_startup_registration"]["summary"]["candidate_complete_signal_count"],
            3,
        )
        schema = json.loads((
            ROOT
            / "profiles/packs/supersymmetry/diagnostics/recipe-invalidation-comparison-v2.schema.json"
        ).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)

    def test_incomparable_result_never_contains_semantic_channel_deltas(self) -> None:
        baseline = _combined("b", 0, 0)
        candidate = _combined("c", 0, 0)
        result = composer.compare_recipe_invalidation_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
            compatibility={
                "state": "incomparable",
                "findings": ["Java runtime identities differ."],
            },
            channels={
                "groovy_postinit": {"state": "not-compared"},
                "gt_startup_registration": {"state": "not-compared"},
            },
        )

        self.assertEqual(result["state"], "incomparable")
        self.assertEqual(
            result["channels"]["gt_startup_registration"],
            {"state": "not-compared"},
        )

    def test_v2_schemas_reject_empty_authority_and_channel_envelopes(self) -> None:
        diagnostic_report = _combined("a", 1, 1)
        diagnostic_schema = json.loads((
            ROOT
            / "profiles/packs/supersymmetry/diagnostics/recipe-invalidation-diagnostic-v2.schema.json"
        ).read_text(encoding="utf-8"))
        diagnostic_validator = Draft202012Validator(diagnostic_schema)
        for field in ("authority", "profile", "source", "recommendation"):
            with self.subTest(diagnostic_field=field):
                malformed = deepcopy(diagnostic_report)
                malformed[field] = {}
                self.assertTrue(list(diagnostic_validator.iter_errors(malformed)))
        for channel_id in ("groovy_postinit", "gt_startup_registration"):
            with self.subTest(diagnostic_channel=channel_id):
                malformed = deepcopy(diagnostic_report)
                malformed["channels"][channel_id] = {}
                self.assertTrue(list(diagnostic_validator.iter_errors(malformed)))

        baseline = _combined("b", 0, 1)
        candidate = _combined("c", 1, 2)
        groovy_comparison = groovy_observer.compare_recipe_reload_diagnostics(
            baseline["channels"]["groovy_postinit"],
            candidate["channels"]["groovy_postinit"],
            profile={
                "diagnostic_profile": GROOVY_PROFILE,
                "function": "compare_recipe_reload_diagnostics",
                "report_format": GROOVY_PROFILE["comparison_format"],
            },
        )
        java_comparison = java_observer.compare_gt_recipe_registration_diagnostics(
            baseline["channels"]["gt_startup_registration"],
            candidate["channels"]["gt_startup_registration"],
            profile={
                "diagnostic_profile": JAVA_PROFILE,
                "function": "compare_gt_recipe_registration_diagnostics",
                "report_format": JAVA_PROFILE["comparison_format"],
            },
        )
        comparison_report = composer.compare_recipe_invalidation_diagnostics(
            baseline,
            candidate,
            profile=COMPARISON_PROFILE,
            compatibility={"state": "comparable", "findings": []},
            channels={
                "groovy_postinit": groovy_comparison,
                "gt_startup_registration": java_comparison,
            },
        )
        comparison_schema = json.loads((
            ROOT
            / "profiles/packs/supersymmetry/diagnostics/recipe-invalidation-comparison-v2.schema.json"
        ).read_text(encoding="utf-8"))
        comparison_validator = Draft202012Validator(comparison_schema)
        for field in ("authority", "profile", "source", "recommendation"):
            with self.subTest(comparison_field=field):
                malformed = deepcopy(comparison_report)
                malformed[field] = {}
                self.assertTrue(list(comparison_validator.iter_errors(malformed)))
        for channel_id in ("groovy_postinit", "gt_startup_registration"):
            with self.subTest(comparison_channel=channel_id):
                malformed = deepcopy(comparison_report)
                malformed["channels"][channel_id] = {}
                self.assertTrue(list(comparison_validator.iter_errors(malformed)))


if __name__ == "__main__":
    unittest.main()
