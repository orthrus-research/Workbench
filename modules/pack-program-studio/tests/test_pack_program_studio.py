from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import jsonschema


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/pack-program-studio/src"
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
if str(PROJECT_INTELLIGENCE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_pack_program_studio import (  # noqa: E402
    AnalysisContext,
    PackProgramError,
    analyze_program,
    build_report,
    compare_programs,
    load_profile,
    validate_report,
)
from workbench_pack_program_studio.cli import run as cli_run  # noqa: E402
import workbench_pack_program_studio.analyzer as analyzer_module  # noqa: E402
import workbench_pack_program_studio.profile as profile_module  # noqa: E402
from workbench_pack_program_studio.lexer import calls, tokenize  # noqa: E402
from workbench_pack_program_studio.profile import (  # noqa: E402
    _windows_display_path_text,
    _windows_extended_path_text,
    display_filesystem_path,
    native_filesystem_path,
    portable_relative_path,
    portable_relative_reference,
    safe_regular_bytes,
)
from workbench_pack_program_studio.render import render_report  # noqa: E402


PROFILE_PATH = (
    ROOT
    / "profiles/packs/supersymmetry/groovy/groovy-program-profile-v1.json"
)
PLATFORM_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/groovyscript/groovyscript-1.4.3-v1.json"
)
BASELINE = ROOT / "modules/pack-program-studio/tests/fixtures/baseline"
CANDIDATE = ROOT / "modules/pack-program-studio/tests/fixtures/candidate"


class PackProgramStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_profile(PROFILE_PATH)
        cls.context = AnalysisContext(side="dedicated-server")
        cls.baseline = analyze_program(BASELINE, cls.profile, context=cls.context)
        cls.candidate = analyze_program(CANDIDATE, cls.profile, context=cls.context)

    def test_lexer_ignores_comments_and_rejects_dynamic_literal_identity(self) -> None:
        source = """
        // new Material.Builder(7, Helper.id('line_comment'))
        /* new Material.Builder(7, Helper.id('block_comment')) */
        new Material.Builder(8, Helper.id('literal'))
        new Material.Builder(9, Helper.id('prefix_' + variable))
        "new Material.Builder(10, 'inside_string')"
        """
        parsed = [call for call in calls(tokenize(source)) if call.callee == "Material.Builder"]
        self.assertEqual(2, len(parsed))
        self.assertEqual("literal", parsed[0].arguments[1].wrapped_string())
        self.assertIsNone(parsed[1].arguments[1].wrapped_string())

    def test_exact_lifecycle_order_and_preprocessors_are_visible(self) -> None:
        by_path = {row["path"]: row for row in self.baseline["files"]}
        self.assertEqual("postInit", by_path["prePostInit/Recipemaps.groovy"]["stage"])
        self.assertLess(
            by_path["prePostInit/Recipemaps.groovy"]["execution_index"],
            by_path["postInit/ClientOnly.groovy"]["execution_index"],
        )
        self.assertEqual("excluded", by_path["postInit/ClientOnly.groovy"]["execution_state"])
        self.assertIn("side preprocessor", by_path["postInit/ClientOnly.groovy"]["execution_reasons"][0])
        self.assertEqual("restart-required", self.baseline["stages"][0]["reload"])
        self.assertEqual("reload-candidate", self.baseline["stages"][2]["reload"])

    def test_program_graph_effects_and_recipe_chain_are_source_linked(self) -> None:
        self.assertEqual(1, self.baseline["dependencies"]["summary"]["cycles"])
        cycle = self.baseline["dependencies"]["cycles"][0]
        self.assertEqual(["classes/A.groovy", "classes/B.groovy"], cycle)
        materials = [
            row
            for row in self.baseline["effects"]
            if row["rule_id"] == "gtceu-material-definition"
        ]
        self.assertEqual(2, len(materials))
        literal = next(row for row in materials if row["fields"]["numeric_id"] == 32000)
        self.assertEqual("alpha", literal["fields"]["registry_name"])
        dynamic = next(row for row in materials if row["fields"]["numeric_id"] == 32010)
        self.assertNotIn("registry_name", dynamic["fields"])
        self.assertEqual("unresolved", dynamic["field_states"]["registry_name"])
        recipe = next(row for row in self.baseline["effects"] if row["kind"] == "machine-recipe")
        self.assertTrue(recipe["recipe"]["complete"])
        self.assertEqual("MIXER", recipe["recipe"]["recipe_map"])
        reference_values = {
            next(iter(row["fields"].values()))
            for row in recipe["recipe"]["references"]
            if row["fields"]
        }
        self.assertTrue({"dustAlpha", "water", "dustBeta"} <= reference_values)
        self.assertTrue(Path(recipe["source"]["absolute_path"]).is_absolute())

    def test_comment_aware_collision_candidates_do_not_promote_to_findings(self) -> None:
        self.assertEqual([], self.baseline["collisions"])
        numeric = [
            row
            for row in self.candidate["collisions"]
            if row["identity_kind"] == "gtceu-material-id"
        ]
        self.assertEqual(1, len(numeric))
        self.assertEqual(32000, numeric[0]["value"])
        self.assertEqual("static-candidate", numeric[0]["evidence_state"])
        self.assertEqual(2, len(numeric[0]["occurrences"]))

    def test_semantic_diff_and_reload_guidance_preserve_dynamic_boundaries(self) -> None:
        comparison = compare_programs(self.baseline, self.candidate)
        self.assertEqual("changed", comparison["state"])
        self.assertIn("material/Materials.groovy", comparison["files"]["modified"])
        self.assertGreater(comparison["effects"]["added"], 0)
        report = build_report(
            source=CANDIDATE,
            baseline=BASELINE,
            profile=self.profile,
            context=self.context,
        )
        self.assertEqual("restart-required", report["change_assessment"]["state"])
        self.assertIn("save-compatibility-review", report["change_assessment"]["reload_checks"])
        post_only = build_report(
            source=CANDIDATE,
            profile=self.profile,
            context=self.context,
            changed_paths=["postInit/Recipes.groovy"],
        )
        self.assertEqual("restart-recommended", post_only["change_assessment"]["state"])
        self.assertIn("reload-twice", post_only["change_assessment"]["reload_checks"])

    def test_recipe_review_pairs_an_unambiguous_property_only_modification(self) -> None:
        report = build_report(
            source=CANDIDATE,
            baseline=BASELINE,
            profile=self.profile,
            context=self.context,
        )
        rendered = render_report(report)
        self.assertIn("Recipe review", rendered)
        self.assertIn(
            "Exact static-candidate machine-recipe multiset · +1 / -1",
            rendered,
        )
        self.assertIn(
            "~ MIXER ×1 · postInit/Recipes.groovy:6-12 → "
            "postInit/Recipes.groovy:8-14",
            rendered,
        )
        self.assertIn("properties duration: 20 → 40", rendered)
        self.assertIn("Removed none", rendered)
        self.assertIn("Added   none", rendered)
        self.assertIn("complete=true", rendered)
        self.assertIn("execution=postInit/enabled", rendered)
        self.assertIn("reload=reload-candidate", rendered)
        self.assertIn("1 unambiguous one-to-one property-only modification", rendered)
        self.assertIn("compiler, runtime, and effective registry state are not observed", rendered)
        self.assertLess(rendered.index("Recipe review"), rendered.index("Lifecycle"))

        without_baseline = build_report(
            source=CANDIDATE,
            profile=self.profile,
            context=self.context,
        )
        self.assertNotIn("Recipe review", render_report(without_baseline))

    def test_recipe_review_renders_an_absent_circuit_meta_property(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            shutil.copytree(BASELINE, baseline)
            shutil.copytree(BASELINE, candidate)
            candidate_recipe = candidate / "groovy/postInit/Recipes.groovy"
            candidate_recipe.write_text(
                candidate_recipe.read_text(encoding="utf-8").replace(
                    "    .duration(20)",
                    "    .circuitMeta(2)\n    .duration(20)",
                ),
                encoding="utf-8",
            )

            report = build_report(
                source=candidate,
                baseline=baseline,
                profile=self.profile,
                context=self.context,
            )

        rendered = render_report(report)
        self.assertIn("Modified", rendered)
        self.assertIn("properties circuitMeta: absent → 2", rendered)
        self.assertNotIn("\n    - MIXER", rendered)
        self.assertNotIn("\n    + MIXER", rendered)

    def test_recipe_review_labels_line_ending_only_byte_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            shutil.copytree(BASELINE, baseline)
            shutil.copytree(BASELINE, candidate)
            for path in candidate.rglob("*"):
                if not path.is_file() or (
                    path.suffix != ".groovy" and path.name != "runConfig.json"
                ):
                    continue
                lf = path.read_bytes().replace(b"\r\n", b"\n")
                path.write_bytes(lf.replace(b"\n", b"\r\n"))

            report = build_report(
                source=candidate,
                baseline=baseline,
                profile=self.profile,
                context=self.context,
            )
            validate_report(report)
            rendered = render_report(report, recipe_review=True)

        changed = report["comparison"]["files"]["changed"]
        self.assertGreater(changed, 0)
        self.assertEqual(0, report["comparison"]["effects"]["added"])
        self.assertEqual(0, report["comparison"]["effects"]["removed"])
        self.assertIn(
            f"0 other exact-byte/stage changes · {changed} "
            "line-ending-only byte changes",
            rendered,
        )
        self.assertIn(
            "Reload      no-action: only LF/CRLF byte representation differs; "
            "V1 JSON retains the exact source hashes.",
            rendered,
        )

    def test_recipe_review_leaves_ambiguous_property_pairs_independent(self) -> None:
        def recipes(*durations: int) -> str:
            rows = ["package postInit", "", "import static prePostInit.Recipemaps.*", ""]
            for duration in durations:
                rows.extend(
                    [
                        "MIXER.recipeBuilder()",
                        "    .inputs(ore('dustAlpha'))",
                        "    .fluidInputs(fluid('water') * 1000)",
                        "    .outputs(metaitem('dustBeta'))",
                        f"    .duration({duration})",
                        "    .EUt(8)",
                        "    .buildAndRegister()",
                        "",
                    ]
                )
            return "\n".join(rows)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            shutil.copytree(BASELINE, baseline)
            shutil.copytree(BASELINE, candidate)
            (baseline / "groovy/postInit/Recipes.groovy").write_text(
                recipes(10, 20), encoding="utf-8"
            )
            (candidate / "groovy/postInit/Recipes.groovy").write_text(
                recipes(30, 40), encoding="utf-8"
            )
            report = build_report(
                source=candidate,
                baseline=baseline,
                profile=self.profile,
                context=self.context,
            )

        rendered = render_report(report)
        self.assertIn("machine-recipe multiset · +2 / -2", rendered)
        self.assertIn("Modified none", rendered)
        self.assertIn("Pairing        none", rendered)
        self.assertEqual(2, rendered.count("\n    - MIXER ×1"))
        self.assertEqual(2, rendered.count("\n    + MIXER ×1"))

    def test_recipe_review_deduplicates_direct_gregtech_removal_calls(self) -> None:
        call = (
            "mods.gregtech.fluid_solidifier.removeByInput(7, "
            "[metaitem('shape.mold.ingot')], [fluid('tin') * 144])"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            shutil.copytree(BASELINE, baseline)
            shutil.copytree(BASELINE, candidate)
            candidate_recipe = candidate / "groovy/postInit/Recipes.groovy"
            candidate_recipe.write_text(
                candidate_recipe.read_text(encoding="utf-8") + f"\n{call}\n",
                encoding="utf-8",
            )
            added_report = build_report(
                source=candidate,
                baseline=baseline,
                profile=self.profile,
                context=self.context,
            )
            removed_report = build_report(
                source=baseline,
                baseline=candidate,
                profile=self.profile,
                context=self.context,
            )

        added_rules = {
            row["rule_id"]
            for row in added_report["comparison"]["effects"]["added_rows"]
            if row["source"]["path"] == "postInit/Recipes.groovy"
        }
        self.assertTrue(
            {
                "groovyscript-mod-integration-call",
                "groovyscript-mod-removal",
            }
            <= added_rules
        )
        added = render_report(added_report)
        self.assertIn("Direct GregTech recipe-removal calls", added)
        self.assertIn("static source statements shown · +1 / -0", added)
        self.assertIn("+ mods.gregtech.fluid_solidifier.removeByInput", added)
        self.assertEqual(1, added.count("mods.gregtech.fluid_solidifier.removeByInput"))
        self.assertIn("loop bodies are not expanded", added)
        self.assertIn("runtime invocation counts are unknown", added)

        compact = render_report(added_report, recipe_review=True)
        self.assertTrue(
            compact.startswith(
                "Recipe review: 0 modified · 0 added · 0 removed · "
                "1 removal statement added"
            )
        )
        truncated_report = {
            **added_report,
            "comparison": {
                **added_report["comparison"],
                "effects": {
                    **added_report["comparison"]["effects"],
                    "truncated": True,
                },
            },
        }
        truncated_compact = render_report(truncated_report, recipe_review=True)
        self.assertIn("at least 1 removal statement added", truncated_compact)
        self.assertIn("direct-removal count incomplete", truncated_compact)
        self.assertIn(
            "static source statements shown · +1 / -0 "
            "(incomplete; at least these counts)",
            truncated_compact,
        )

        removed = render_report(removed_report)
        self.assertIn("static source statements shown · +0 / -1", removed)
        self.assertIn("- mods.gregtech.fluid_solidifier.removeByInput", removed)
        self.assertEqual(1, removed.count("mods.gregtech.fluid_solidifier.removeByInput"))

    def test_runtime_correlation_does_not_let_a_clean_groovy_log_hide_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            groovy = root / "groovy.log"
            groovy.write_text(
                """GroovyScript version: 1.4.3
[00:00:00] [CLIENT/INFO] [fixture]: Running scripts in loader 'postInit'
[00:00:00] [CLIENT/INFO] [fixture]:  - running script postInit.Recipes
[00:00:01] [CLIENT/INFO] [fixture]: Groovy scripts took 600ms to compile and 400ms to run in postInit.
""",
                encoding="utf-8",
            )
            diagnosis = root / "diagnosis.json"
            diagnosis.write_text(
                json.dumps(
                    {
                        "format": "workbench-runtime-diagnosis-v2",
                        "diagnosis_id": "fixture",
                        "checkpoint": {"id": "loaded"},
                        "primary_failure": None,
                        "authority": {"classification": "integration-observation"},
                        "log_observations": [
                            {
                                "classification": "non-terminal-log-observation",
                                "count": 2,
                                "evidence_label": "latest-log",
                                "sample_messages": ["Invalid number of Outputs"],
                                "type": "java.lang.IllegalArgumentException",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = build_report(
                source=BASELINE,
                profile=self.profile,
                context=self.context,
                groovy_log=groovy,
                runtime_diagnosis=diagnosis,
            )
        runtime = report["runtime_evidence"]
        self.assertEqual("attention", runtime["state"])
        self.assertEqual(0, runtime["groovy_log"]["diagnostics"]["fatal_or_error"])
        self.assertEqual(2, runtime["runtime_diagnosis"]["exception_count"])
        self.assertFalse(runtime["acceptance"]["candidate_source_bound"])

    def test_report_semantics_and_json_schemas_validate(self) -> None:
        report = build_report(
            source=BASELINE,
            profile=self.profile,
            context=self.context,
        )
        self.assertEqual(report, validate_report(report))
        schemas = ROOT / "modules/pack-program-studio/schemas"
        for value_path, schema_path in (
            (PROFILE_PATH, schemas / "workbench-groovy-pack-profile-v1.schema.json"),
            (PLATFORM_PATH, schemas / "workbench-groovyscript-platform-profile-v1.schema.json"),
        ):
            jsonschema.Draft202012Validator(
                json.loads(schema_path.read_text(encoding="utf-8"))
            ).validate(json.loads(value_path.read_text(encoding="utf-8")))
        jsonschema.Draft202012Validator(
            json.loads(
                (schemas / "workbench-groovy-pack-program-report-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        ).validate(report)

    def test_cli_emits_json_and_refuses_to_replace_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "report.json"
            output = StringIO()
            error = StringIO()
            code = cli_run(
                [
                    "dev",
                    "--profile",
                    "supersymmetry",
                    "--source",
                    str(BASELINE),
                    "--json",
                    "--output",
                    str(destination),
                ],
                root=ROOT,
                output=output,
                error=error,
            )
            self.assertEqual(0, code, error.getvalue())
            self.assertEqual(
                json.loads(output.getvalue())["report_id"],
                json.loads(destination.read_text(encoding="utf-8"))["report_id"],
            )
            second = cli_run(
                [
                    "dev",
                    "--profile",
                    "supersymmetry",
                    "--source",
                    str(BASELINE),
                    "--output",
                    str(destination),
                ],
                root=ROOT,
                output=StringIO(),
                error=error,
            )
            self.assertEqual(2, second)
            self.assertIn("already exists", error.getvalue())

    def test_profile_rejects_unknown_named_adapter_and_unsafe_source_path(self) -> None:
        output = StringIO()
        error = StringIO()
        code = cli_run(
            ["dev", "--profile", "not-a-pack", "--source", str(BASELINE)],
            root=ROOT,
            output=output,
            error=error,
        )
        self.assertEqual(2, code)
        self.assertIn("unknown Groovy pack-program profile", error.getvalue())
        with self.assertRaises(PackProgramError):
            analyze_program(PROFILE_PATH, self.profile, context=self.context)

    def test_portable_path_contract_rejects_cross_host_ambiguity(self) -> None:
        invalid = (
            "",
            ".",
            "../outside",
            "C:/outside",
            "C:outside",
            "\\\\server\\share\\outside",
            "postInit\\Recipes.groovy",
            "postInit//Recipes.groovy",
            "postInit/./Recipes.groovy",
            "postInit/CON.groovy",
            "postInit/bad:name.groovy",
            "postInit/trailing. ",
            "postInit/e\u0301.groovy",
            ".git",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(
                PackProgramError, "canonical portable relative path"
            ):
                portable_relative_path(value, "test path")
        self.assertEqual(
            "postInit/Recipes.groovy",
            portable_relative_path(
                "postInit/Recipes.groovy", "test path"
            ).as_posix(),
        )
        self.assertEqual(
            "classes",
            portable_relative_path(
                "classes/", "loader path", allow_directory_marker=True
            ).as_posix(),
        )
        self.assertEqual(
            "../../../platforms/cleanroom/platform.json",
            portable_relative_reference(
                "../../../platforms/cleanroom/platform.json", "profile reference"
            ).as_posix(),
        )
        invalid_references = (
            "..\\..\\platform.json",
            "C:/profiles/platform.json",
            "C:profiles/platform.json",
            "//server/share/platform.json",
            "platforms/../platform.json",
            "/profiles/platform.json",
        )
        for value in invalid_references:
            with self.subTest(reference=value), self.assertRaisesRegex(
                PackProgramError, "canonical portable relative reference"
            ):
                portable_relative_reference(value, "profile reference")

    def test_pack_profile_source_layout_uses_portable_path_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "profiles"
            profile_path = (
                profiles / "packs/supersymmetry/groovy/groovy-program-profile-v1.json"
            )
            platform_path = (
                profiles
                / "platforms/cleanroom/groovyscript/groovyscript-1.4.3-v1.json"
            )
            profile_path.parent.mkdir(parents=True)
            platform_path.parent.mkdir(parents=True)
            value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            value["source_layout"]["groovy_root"] = "C:\\escaped-groovy"
            profile_path.write_text(json.dumps(value), encoding="utf-8")
            shutil.copy2(PLATFORM_PATH, platform_path)

            with self.assertRaisesRegex(
                PackProgramError, "canonical portable relative path"
            ):
                load_profile(profile_path)

    def test_pack_profile_platform_reference_is_host_independent_and_contained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles"
            profile_path = (
                profiles / "packs/supersymmetry/groovy/groovy-program-profile-v1.json"
            )
            profile_path.parent.mkdir(parents=True)
            value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            value["platform_profile"] = "..\\..\\..\\platforms\\platform.json"
            profile_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                PackProgramError, "canonical portable relative reference"
            ):
                load_profile(profile_path)

            outside = root / "outside-platform.json"
            shutil.copy2(PLATFORM_PATH, outside)
            value["platform_profile"] = "../../../../outside-platform.json"
            profile_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(PackProgramError, "escapes the profiles tree"):
                load_profile(profile_path)

    def test_loader_paths_are_admitted_before_native_join(self) -> None:
        unsafe = (
            "..\\outside\\Outside.groovy",
            "C:\\outside\\Outside.groovy",
            "C:outside\\Outside.groovy",
            "\\\\server\\share\\Outside.groovy",
            "postInit//Recipes.groovy",
            ".git/",
        )
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            shutil.copytree(BASELINE, candidate)
            run_config_path = candidate / "groovy/runConfig.json"
            original = json.loads(run_config_path.read_text(encoding="utf-8"))
            for entry in unsafe:
                with self.subTest(entry=entry):
                    run_config = dict(original)
                    run_config["loaders"] = dict(original["loaders"])
                    run_config["loaders"]["preInit"] = [entry]
                    run_config_path.write_text(
                        json.dumps(run_config), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(
                        PackProgramError, "canonical portable relative path"
                    ):
                        analyze_program(candidate, self.profile, context=self.context)

    def test_loader_path_cannot_escape_through_an_intermediate_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            outside = root / "outside"
            shutil.copytree(BASELINE, candidate)
            outside.mkdir()
            (outside / "Outside.groovy").write_text("// fixture\n", encoding="utf-8")
            link = candidate / "groovy/linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            run_config_path = candidate / "groovy/runConfig.json"
            run_config = json.loads(run_config_path.read_text(encoding="utf-8"))
            run_config["loaders"]["preInit"] = ["linked/Outside.groovy"]
            run_config_path.write_text(json.dumps(run_config), encoding="utf-8")

            with self.assertRaisesRegex(PackProgramError, "traverses a filesystem link"):
                analyze_program(candidate, self.profile, context=self.context)

    def test_native_case_alias_cannot_change_a_configured_logical_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PostInit").mkdir()
            real_listdir = os.listdir

            def mismatched_listdir(path: object) -> list[str]:
                entries = real_listdir(path)
                if Path(path) == root:
                    return ["postinit" if entry == "PostInit" else entry for entry in entries]
                return entries

            with patch.object(analyzer_module.os, "listdir", side_effect=mismatched_listdir):
                with self.assertRaisesRegex(PackProgramError, "exact path spelling"):
                    analyzer_module._portable_child(
                        root,
                        portable_relative_path("PostInit", "fixture"),
                        "Groovy loader path",
                    )

    def test_native_case_alias_cannot_change_a_platform_profile_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / "Platforms/profile.json"
            platform.parent.mkdir()
            platform.write_text("{}\n", encoding="utf-8")
            real_listdir = os.listdir

            def mismatched_listdir(path: object) -> list[str]:
                entries = real_listdir(path)
                if Path(path) == root:
                    return [
                        "platforms" if entry == "Platforms" else entry
                        for entry in entries
                    ]
                return entries

            with patch.object(
                profile_module.os, "listdir", side_effect=mismatched_listdir
            ):
                with self.assertRaisesRegex(PackProgramError, "exact path spelling"):
                    profile_module._portable_reference_path(
                        root,
                        portable_relative_reference(
                            "Platforms/profile.json", "fixture"
                        ),
                        "Groovy platform profile reference",
                    )

    def test_loader_directory_markers_have_canonical_logical_identity(self) -> None:
        by_path = {row["path"]: row for row in self.baseline["files"]}
        self.assertEqual("classes", by_path["classes/A.groovy"]["loader_entry"])
        self.assertEqual("postInit", by_path["postInit/Recipes.groovy"]["loader_entry"])

    def test_direct_groovy_root_prunes_exact_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            worktree = root / "worktree"
            shutil.copytree(BASELINE / "groovy", repository)
            shutil.copytree(BASELINE / "groovy", worktree)
            metadata = repository / ".git/objects"
            metadata.mkdir(parents=True)
            (metadata / "CON.groovy").write_text("not source\n", encoding="utf-8")
            (worktree / ".git").write_text(
                "gitdir: ../metadata/worktrees/fixture\n", encoding="utf-8"
            )

            repository_program = analyze_program(
                repository, self.profile, context=self.context
            )
            worktree_program = analyze_program(
                worktree, self.profile, context=self.context
            )

        expected = {row["path"] for row in self.baseline["files"]}
        self.assertEqual(expected, {row["path"] for row in repository_program["files"]})
        self.assertEqual(expected, {row["path"] for row in worktree_program["files"]})

    def test_source_reads_and_hashes_preserve_crlf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            candidate = root / "candidate"
            shutil.copytree(BASELINE, baseline)
            shutil.copytree(BASELINE, candidate)
            baseline_recipe = baseline / "groovy/postInit/Recipes.groovy"
            candidate_recipe = candidate / "groovy/postInit/Recipes.groovy"
            lf = baseline_recipe.read_bytes().replace(b"\r\n", b"\n")
            crlf = lf.replace(b"\n", b"\r\n")
            baseline_recipe.write_bytes(lf)
            candidate_recipe.write_bytes(crlf)

            self.assertEqual(crlf, safe_regular_bytes(candidate_recipe))
            baseline_program = analyze_program(
                baseline, self.profile, context=self.context
            )
            candidate_program = analyze_program(
                candidate, self.profile, context=self.context
            )

        baseline_file = next(
            row
            for row in baseline_program["files"]
            if row["path"] == "postInit/Recipes.groovy"
        )
        candidate_file = next(
            row
            for row in candidate_program["files"]
            if row["path"] == "postInit/Recipes.groovy"
        )
        self.assertNotEqual(baseline_file["sha256"], candidate_file["sha256"])
        self.assertIn(
            "postInit/Recipes.groovy",
            compare_programs(baseline_program, candidate_program)["files"]["modified"],
        )

    @unittest.skipIf(os.name == "nt", "Windows cannot create reserved source names")
    def test_candidate_walk_rejects_nonportable_discovered_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            shutil.copytree(BASELINE, candidate)
            (candidate / "groovy/postInit/CON.groovy").write_text(
                "// fixture\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                PackProgramError, "canonical portable relative path"
            ):
                analyze_program(candidate, self.profile, context=self.context)

    @unittest.skipIf(os.name == "nt", "Windows cannot create reserved source names")
    def test_directory_baseline_uses_the_same_portable_walk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline"
            shutil.copytree(BASELINE, baseline)
            (baseline / "groovy/postInit/NUL.groovy").write_text(
                "// fixture\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                PackProgramError, "canonical portable relative path"
            ):
                build_report(
                    source=CANDIDATE,
                    baseline=baseline,
                    profile=self.profile,
                    context=self.context,
                )

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires a case-sensitive fixture filesystem")
    def test_candidate_walk_rejects_directory_prefix_case_collisions(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            candidate = Path(directory) / "candidate"
            shutil.copytree(BASELINE, candidate)
            alternate = candidate / "groovy/PostInit"
            alternate.mkdir()
            (alternate / "Other.groovy").write_text("// fixture\n", encoding="utf-8")
            with self.assertRaisesRegex(PackProgramError, "collide by case"):
                analyze_program(candidate, self.profile, context=self.context)

    def test_candidate_walk_fails_closed_on_traversal_errors(self) -> None:
        failure = PermissionError("fixture denied")
        failure.filename = "blocked"

        def failing_walk(*_args: object, **kwargs: object) -> object:
            onerror = kwargs["onerror"]
            assert callable(onerror)
            onerror(failure)
            return iter(())

        with patch.object(analyzer_module.os, "walk", side_effect=failing_walk):
            with self.assertRaisesRegex(PackProgramError, "cannot traverse Groovy source"):
                analyzer_module._walk_groovy(BASELINE / "groovy")

    def test_candidate_walk_rejects_junctions_when_reported_by_host(self) -> None:
        def junction(path: Path, _context: str) -> bool:
            return path.name == "postInit"

        with patch.object(analyzer_module, "_is_junction", side_effect=junction):
            with self.assertRaisesRegex(PackProgramError, "is a junction"):
                analyzer_module._walk_groovy(BASELINE / "groovy")

    def test_git_binding_decodes_unicode_repository_root_from_utf8_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "r\u00e9pertoire"
            repository.mkdir()
            revision = "a" * 40
            responses = (
                subprocess.CompletedProcess(
                    args=(),
                    returncode=0,
                    stdout=(str(repository) + "\n").encode("utf-8"),
                    stderr=b"",
                ),
                subprocess.CompletedProcess(
                    args=(), returncode=0, stdout=b"", stderr=b""
                ),
                subprocess.CompletedProcess(
                    args=(), returncode=0, stdout=(revision + "\n").encode("ascii"), stderr=b""
                ),
                subprocess.CompletedProcess(
                    args=(), returncode=0, stdout=b" M dirty-file\x00", stderr=b""
                ),
            )
            with patch.object(analyzer_module.subprocess, "run", side_effect=responses) as run:
                binding = analyzer_module._git_binding(repository)

        self.assertEqual(
            {
                "repository_root": str(repository.resolve()),
                "revision": revision,
                "dirty": True,
            },
            binding,
        )
        self.assertEqual(4, run.call_count)
        self.assertTrue(all("text" not in call.kwargs for call in run.call_args_list))
        self.assertIn("-z", run.call_args_list[3].args[0])

    def test_git_binding_honors_setup_selection_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            selected_git = shutil.which("git")
            self.assertIsNotNone(selected_git)
            for arguments in (
                ("init", "--quiet"),
                ("config", "user.name", "Workbench Test"),
                ("config", "user.email", "workbench@example.invalid"),
            ):
                subprocess.run(
                    [str(selected_git), "-C", str(repository), *arguments],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            for arguments in (
                ("add", "tracked.txt"),
                ("commit", "--quiet", "-m", "fixture"),
            ):
                subprocess.run(
                    [str(selected_git), "-C", str(repository), *arguments],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            with patch.dict(
                os.environ,
                {
                    "PATH": "",
                    "WORKBENCH_GIT_EXECUTABLE": str(selected_git),
                },
            ):
                binding = analyzer_module._git_binding(repository)

            self.assertIsNotNone(binding)
            self.assertEqual(str(repository.resolve()), binding["repository_root"])
            self.assertFalse(binding["dirty"])

    def test_git_binding_rejects_missing_setup_selection_precisely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-git"
            with patch.dict(
                os.environ,
                {
                    "PATH": "",
                    "WORKBENCH_GIT_EXECUTABLE": str(missing),
                },
            ):
                with self.assertRaisesRegex(
                    PackProgramError,
                    rf"configured Git executable is unavailable: {missing}",
                ):
                    analyzer_module._git_binding(root)

    def test_windows_extended_path_adapter_preserves_display_spelling(self) -> None:
        drive = r"C:\workspace\very-long-path"
        unc = r"\\server\share\very-long-path"
        self.assertEqual(r"\\?\C:\workspace\very-long-path", _windows_extended_path_text(drive))
        self.assertEqual(
            r"\\?\UNC\server\share\very-long-path",
            _windows_extended_path_text(unc),
        )
        self.assertEqual(drive, _windows_display_path_text(_windows_extended_path_text(drive)))
        self.assertEqual(unc, _windows_display_path_text(_windows_extended_path_text(unc)))

    @unittest.skipUnless(os.name == "nt", "requires native Windows extended paths")
    def test_long_windows_candidate_and_profile_paths_are_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            native_root = native_filesystem_path(Path(directory))
            long_root = native_root.joinpath("a" * 100, "b" * 100, "c" * 80)
            try:
                profile_path = (
                    long_root / "profiles/packs/supersymmetry/groovy/profile.json"
                )
                platform_path = (
                    long_root
                    / "profiles/platforms/cleanroom/groovyscript/"
                    "groovyscript-1.4.3-v1.json"
                )
                profile_path.parent.mkdir(parents=True)
                platform_path.parent.mkdir(parents=True)
                shutil.copy2(PROFILE_PATH, profile_path)
                shutil.copy2(PLATFORM_PATH, platform_path)
                candidate = long_root / "candidate"
                shutil.copytree(BASELINE, candidate)
                self.assertGreater(len(display_filesystem_path(candidate)), 260)

                long_profile = load_profile(Path(display_filesystem_path(profile_path)))
                program = analyze_program(
                    Path(display_filesystem_path(candidate)),
                    long_profile,
                    context=self.context,
                )
                self.assertIn(
                    "postInit/Recipes.groovy",
                    {row["path"] for row in program["files"]},
                )
                self.assertNotIn("\\\\?\\", program["binding"]["groovy_root"])
            finally:
                if long_root.exists():
                    shutil.rmtree(long_root)


if __name__ == "__main__":
    unittest.main()
