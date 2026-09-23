"""Integration coverage for the read-only recipe-review product alias."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import jsonschema


ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "tools/workbench.py"
BASELINE = ROOT / "modules/pack-program-studio/tests/fixtures/baseline"
CANDIDATE = ROOT / "modules/pack-program-studio/tests/fixtures/candidate"
PACKAGE_SOURCE = ROOT / "modules/pack-program-studio/src"
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(PACKAGE_SOURCE))

from workbench_pack_program_studio.cli import build_parser  # noqa: E402
from workbench_project_intelligence.pr_preparation import (  # noqa: E402
    apply_pr_preparation_plan_v2,
    build_pr_preparation_plan_v2,
    load_pull_request_provider_profile,
)
from workbench_project_intelligence.project_acquisition import (  # noqa: E402
    load_acquisition_profile,
)


class RecipeReviewCliTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKBENCH), "review", "recipes", *arguments],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
        )

    def test_help_defines_the_strict_report_summary_contract(self) -> None:
        result = self._run("--help")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertIn(
            "return exit 1 when report summary status is attention",
            result.stdout,
        )
        self.assertIn("supplied runtime evidence can drive it", result.stdout)
        self.assertIn("runConfig warnings do not", result.stdout)

    def test_human_review_routes_to_the_source_linked_recipe_diff(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertEqual(
            "Recipe review: 1 modified · 0 added · 0 removed",
            result.stdout.splitlines()[0],
        )
        self.assertIn("machine-recipe multiset · +1 / -1", result.stdout)
        self.assertIn("~ MIXER ×1", result.stdout)
        self.assertIn("properties duration: 20 → 40", result.stdout)
        self.assertIn("1 unambiguous one-to-one property-only modification", result.stdout)
        self.assertIn("Review guidance", result.stdout)
        self.assertIn("Evidence    static source candidates", result.stdout)
        self.assertIn("Other report attention", result.stdout)
        self.assertIn(
            "--json for bounded V2; --full-json-v1 for complete owner evidence",
            result.stdout,
        )
        self.assertNotIn("GroovyScript Pack Program Studio", result.stdout)
        self.assertNotIn("\nLifecycle\n", result.stdout)
        self.assertNotIn("\nProgram surface\n", result.stdout)
        self.assertNotIn("\nDependencies\n", result.stdout)
        self.assertNotIn("\nIdentity review\n", result.stdout)
        self.assertNotIn("\nRuntime correlation\n", result.stdout)

    def test_verbose_review_restores_full_program_context(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
            "--verbose",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertTrue(result.stdout.startswith("Recipe review: 1 modified"))
        self.assertIn("Program details", result.stdout)
        self.assertIn("\nLifecycle\n", result.stdout)
        self.assertIn("\nProgram surface\n", result.stdout)
        self.assertIn("\nDependencies\n", result.stdout)
        self.assertIn("\nIdentity review\n", result.stdout)
        self.assertIn("\nRuntime correlation\n", result.stdout)
        self.assertNotIn("More        Use --verbose", result.stdout)

    def test_compact_review_makes_absent_workspace_attention_explicit(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(BASELINE),
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertTrue(result.stdout.startswith("Recipe review: 0 modified"))
        self.assertIn("Other report attention", result.stdout)
        self.assertIn(
            "None; --strict does not change the successful exit status.",
            result.stdout,
        )

    def test_strict_uses_the_separately_labeled_report_attention(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
            "--strict",
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertIn("Other report attention", result.stdout)
        self.assertIn("These report-level signals drive --strict", result.stdout)

    def test_source_configuration_warning_does_not_drive_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            shutil.copytree(BASELINE, candidate)
            run_config = candidate / "groovy/runConfig.json"
            value = json.loads(run_config.read_text(encoding="utf-8"))
            value["packName"] = "Unexpected fixture name"
            run_config.write_text(
                json.dumps(value, indent=2) + "\n",
                encoding="utf-8",
            )

            result = self._run(
                "--profile",
                "supersymmetry",
                "--baseline",
                str(BASELINE),
                "--source",
                str(candidate),
                "--strict",
            )
            strict_all = self._run(
                "--profile",
                "supersymmetry",
                "--baseline",
                str(BASELINE),
                "--source",
                str(candidate),
                "--strict-all",
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Source configuration warnings", result.stdout)
        self.assertIn("these warnings do not drive --strict", result.stdout)
        self.assertIn(
            "None; --strict does not change the successful exit status.",
            result.stdout,
        )
        self.assertEqual(1, strict_all.returncode, strict_all.stderr)
        self.assertIn("Source configuration warnings", strict_all.stdout)

    def test_internal_pr_routing_flags_are_not_public_recipe_options(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
            "--pr-delta-strict",
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("internal review-pr routing flags", result.stderr)

        pr_result = subprocess.run(
            [
                sys.executable,
                str(WORKBENCH),
                "review",
                "pr",
                "2002",
                "--profile",
                "supersymmetry",
                "--source",
                str(CANDIDATE),
                "--pr-review-attention",
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
        )
        self.assertEqual(2, pr_result.returncode)
        self.assertIn("internal review-pr routing flags", pr_result.stderr)

    def test_pr_runtime_attention_is_truthful_without_static_delta_attention(self) -> None:
        specification = importlib.util.spec_from_file_location(
            "workbench_pr_runtime_attention_test",
            ROOT / "modules/workbench-shell/src/workbench_shell/review_commands.py",
        )
        self.assertIsNotNone(specification)
        self.assertIsNotNone(specification.loader)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)

        rendered = module._render_pr_attention_partition(
            {
                "introduced": 0,
                "preexisting": 0,
                "candidate_total": 0,
            },
            runtime_attention=True,
        )
        self.assertIn(
            "--strict       exits 1 for introduced PR signals or supplied runtime attention",
            rendered,
        )
        self.assertNotIn("--strict       does not change exit status", rendered)
        self.assertEqual(
            1,
            module._recipe_review_strict_all_exit(
                0,
                {
                    "baseline": {"collisions": [], "effects": []},
                    "candidate": {"collisions": [], "effects": []},
                    "runtime_evidence": {"state": "attention"},
                },
                strict_all=False,
                extra_attention=False,
                pr_delta_strict=True,
            ),
        )
        relabeled = module._relabel_pr_review_attention(
            "Informational in V1; these warnings do not drive --strict."
        )
        self.assertEqual(
            1,
            relabeled.count(
                "Informational in V1; they do not drive PR --strict but do drive --strict-all."
            ),
        )

    def test_supplied_runtime_failure_does_drive_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            groovy_log = Path(directory) / "groovy.log"
            groovy_log.write_text(
                "[00:00:00] [SERVER/ERROR] [fixture]: failure\n",
                encoding="utf-8",
            )

            result = self._run(
                "--profile",
                "supersymmetry",
                "--baseline",
                str(BASELINE),
                "--source",
                str(BASELINE),
                "--groovy-log",
                str(groovy_log),
                "--strict",
            )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Other report attention", result.stdout)
        self.assertIn("Supplied runtime evidence", result.stdout)
        self.assertIn("These report-level signals drive --strict", result.stdout)

    def test_recipe_review_mode_is_a_hidden_dev_option(self) -> None:
        parser = build_parser()
        dev = next(
            action.choices["dev"]
            for action in parser._actions
            if action.dest == "operation"
        )

        self.assertNotIn("--recipe-review", dev.format_help())
        self.assertIn("--verbose", dev.format_help())
        args = parser.parse_args(
            ["dev", "--profile", "supersymmetry", "--recipe-review"]
        )
        self.assertTrue(args.recipe_review)

    def test_help_describes_pairing_and_verbose_detail(self) -> None:
        result = self._run("--help")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertIn("--verbose", result.stdout)
        self.assertIn("conservatively pairs", result.stdout)
        self.assertIn("property-only recipe modifications", result.stdout)
        self.assertNotIn("not paired into inferred modified recipes", result.stdout)

    def test_full_json_v1_preserves_the_existing_owner_report(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
            "--full-json-v1",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("workbench-groovy-pack-program-report-v1", report["format"])
        self.assertEqual("changed", report["comparison"]["state"])
        self.assertEqual(3, report["comparison"]["files"]["changed"])
        added_recipes = [
            row
            for row in report["comparison"]["effects"]["added_rows"]
            if row["kind"] == "machine-recipe"
        ]
        removed_recipes = [
            row
            for row in report["comparison"]["effects"]["removed_rows"]
            if row["kind"] == "machine-recipe"
        ]
        self.assertEqual(1, sum(row["count"] for row in added_recipes))
        self.assertEqual(1, sum(row["count"] for row in removed_recipes))

    def test_json_review_emits_bounded_reviewer_v2(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
            "--json",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("workbench-recipe-review-v2", report["format"])
        self.assertEqual("directory", report["selection"]["kind"])
        self.assertEqual(1, report["summary"]["machine_recipes"]["modified"])
        self.assertNotIn("baseline", report)
        self.assertLess(len(result.stdout.encode("utf-8")), 16 * 1024 * 1024)
        schema = json.loads(
            (
                ROOT
                / "modules/pack-program-studio/schemas/workbench-recipe-review-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(report)

    def test_output_writes_v2_without_replacing_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.json"
            result = self._run(
                "--profile",
                "supersymmetry",
                "--baseline",
                str(BASELINE),
                "--source",
                str(CANDIDATE),
                "--output",
                str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(f"Report written: {output.resolve()}", result.stdout)
            self.assertEqual(
                "workbench-recipe-review-v2",
                json.loads(output.read_text(encoding="utf-8"))["format"],
            )

            repeated = self._run(
                "--profile",
                "supersymmetry",
                "--baseline",
                str(BASELINE),
                "--source",
                str(CANDIDATE),
                "--output",
                str(output),
            )

        self.assertEqual(2, repeated.returncode)
        self.assertIn("already exists", repeated.stderr)

    def test_review_rejects_a_missing_baseline_selector(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--source",
            str(CANDIDATE),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--baseline", result.stderr)
        self.assertIn("--baseline-ref", result.stderr)
        self.assertIn("--pr-base", result.stderr)

    def test_review_rejects_an_implicit_candidate_tree(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--source is required", result.stderr)

    def test_review_rejects_implicit_pack_selection(self) -> None:
        result = self._run(
            "--baseline",
            str(BASELINE),
            "--source",
            str(CANDIDATE),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--profile supersymmetry is required", result.stderr)
        self.assertIn("pack selection is never implicit", result.stderr)


@unittest.skipUnless(shutil.which("git"), "Git is required for prepared PR coverage")
class PreparedPullRequestRecipeReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workbench-prepared-pr-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote.git"
        self.author = self.root / "author"
        self.checkout = self.root / "checkout"
        self.state = self.root / "state"
        self.git = str(Path(shutil.which("git") or "git").resolve())
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
                "WORKBENCH_CONFIG_HOME": str(self.root / "workbench-config"),
                "WORKBENCH_STATE_ROOT": str(self.state),
            }
        )

        self._git("init", "--bare", str(self.remote))
        self._git("init", "--initial-branch", "master-ceu", str(self.author))
        self._git("-C", str(self.author), "config", "user.name", "Workbench Test")
        self._git(
            "-C",
            str(self.author),
            "config",
            "user.email",
            "workbench-test@example.invalid",
        )
        self._write_workspace(BASELINE)
        self._commit("baseline")
        self.original_base_oid = self._git(
            "-C", str(self.author), "rev-parse", "HEAD"
        )
        self._git("-C", str(self.author), "remote", "add", "origin", str(self.remote))
        self._git("-C", str(self.author), "push", "origin", "master-ceu")
        self._git("-C", str(self.author), "switch", "-c", "pull-request")
        self._write_workspace(CANDIDATE)
        (self.author / "betterquesting").mkdir()
        (self.author / "betterquesting/quest-change.json").write_text(
            "{}\n", encoding="utf-8"
        )
        self._commit("pull request")
        self.original_head_oid = self._git(
            "-C", str(self.author), "rev-parse", "HEAD"
        )
        self._git(
            "-C",
            str(self.author),
            "push",
            "origin",
            "HEAD:refs/pull/2002/head",
        )
        head_tree = self._git(
            "-C",
            str(self.author),
            "rev-parse",
            f"{self.original_head_oid}^{{tree}}",
        )
        self.provider_merge_oid = self._git(
            "-C",
            str(self.author),
            "commit-tree",
            head_tree,
            "-p",
            self.original_base_oid,
            "-p",
            self.original_head_oid,
            "-m",
            "provider merge fixture",
        )
        self._git(
            "-C",
            str(self.author),
            "push",
            "origin",
            f"{self.provider_merge_oid}:refs/pull/2002/merge",
        )
        self._git(
            "clone",
            "--branch",
            "master-ceu",
            str(self.remote),
            str(self.checkout),
        )

        git_config = self.root / "gitconfig"
        git_config.write_text(
            '[url "{}"]\n\tinsteadOf = https://github.com/SymmetricDevs/Supersymmetry.git\n'.format(
                self.remote.as_posix()
            ),
            encoding="utf-8",
        )
        self.environment["GIT_CONFIG_GLOBAL"] = str(git_config)
        self.provider_metadata = self.root / "provider-pr-2002.json"
        self.provider_metadata.write_text(
            json.dumps(
                {
                    "number": 2002,
                    "html_url": "https://github.com/SymmetricDevs/Supersymmetry/pull/2002",
                    "state": "closed",
                    "merged": True,
                    "base": {
                        "ref": "master-ceu",
                        "sha": self.original_base_oid,
                        "repo": {"full_name": "SymmetricDevs/Supersymmetry"},
                    },
                    "head": {
                        "ref": "recipe-review",
                        "sha": self.original_head_oid,
                        "repo": {"full_name": "Contributor/Supersymmetry"},
                    },
                    "merge_commit_sha": self.provider_merge_oid,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            [self.git, *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            env=self.environment,
        )
        if result.returncode:
            self.fail(
                f"Git fixture command failed ({result.returncode}): "
                f"{' '.join(arguments)}\n{result.stderr}"
            )
        return result.stdout.strip()

    def _write_workspace(self, fixture: Path) -> None:
        shutil.copytree(fixture, self.author, dirs_exist_ok=True)
        for directory in ("config", "mods"):
            (self.author / directory).mkdir(exist_ok=True)
        (self.author / "pack.toml").write_text(
            "name = 'Prepared PR fixture'\n", encoding="utf-8"
        )
        (self.author / "index.toml").write_text(
            "hash-format = 'sha256'\n", encoding="utf-8"
        )

    def _commit(self, message: str) -> None:
        self._git("-C", str(self.author), "add", "--all")
        self._git("-C", str(self.author), "commit", "-m", message)

    def _workbench(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKBENCH), *arguments],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=90,
            env=self.environment,
        )

    def test_pr_hygiene_treats_diff_check_output_as_attention(self) -> None:
        repository = self.root / "hygiene"
        self._git("init", str(repository))
        self._git("-C", str(repository), "config", "user.email", "test@example.com")
        self._git("-C", str(repository), "config", "user.name", "Recipe Review")
        source = repository / "recipe.groovy"
        source.write_text("println 'clean'\n", encoding="utf-8")
        self._git("-C", str(repository), "add", "recipe.groovy")
        self._git("-C", str(repository), "commit", "-m", "base")
        base = self._git("-C", str(repository), "rev-parse", "HEAD")
        source.write_text("println 'trailing' \n", encoding="utf-8")
        self._git("-C", str(repository), "add", "recipe.groovy")
        self._git("-C", str(repository), "commit", "-m", "whitespace")
        head = self._git("-C", str(repository), "rev-parse", "HEAD")

        specification = importlib.util.spec_from_file_location(
            "workbench_pr_hygiene_test",
            ROOT / "modules/workbench-shell/src/workbench_shell/review_commands.py",
        )
        self.assertIsNotNone(specification)
        self.assertIsNotNone(specification.loader)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        state, detail = module._pr_review_hygiene(repository, base, head)

        self.assertEqual("attention", state)
        self.assertIn("trailing whitespace", detail)

    def test_provider_bound_v2_receipt_routes_to_offline_historical_review(self) -> None:
        before_head = self._git("-C", str(self.checkout), "rev-parse", "HEAD")
        before_status = self._git("-C", str(self.checkout), "status", "--porcelain=v1")
        acquisition = load_acquisition_profile(
            ROOT / "profiles/packs/supersymmetry/acquisition-v1.json"
        )
        provider = load_pull_request_provider_profile(
            ROOT / "profiles/packs/supersymmetry/github-pr-provider-v1.json"
        )
        plan = build_pr_preparation_plan_v2(
            acquisition,
            provider,
            pull_request=2002,
            repository=self.checkout,
            state_root=self.state,
            git_executable=self.git,
            environment=self.environment,
            provider_metadata_path=self.provider_metadata,
        )
        self.assertEqual("workbench-pr-preparation-plan-v2", plan["format"])
        self.assertEqual(self.original_base_oid, plan["base_oid"])
        self.assertEqual(self.original_head_oid, plan["head_oid"])
        self.assertEqual("closed", plan["pull_request_state"])
        self.assertTrue(plan["pull_request_merged"])

        result = apply_pr_preparation_plan_v2(
            acquisition,
            provider,
            plan,
            environment=self.environment,
            provider_metadata_path=self.provider_metadata,
        )
        self.assertEqual("provider-bound-prepared", result["outcome"])
        receipt = json.loads(
            Path(result["receipt_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual("workbench-pr-preparation-receipt-v2", receipt["format"])

        self.remote.rename(self.root / "remote-offline.git")
        self.provider_metadata.unlink()
        review_arguments = (
            "review",
            "recipes",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
        )
        reviewed = self._workbench(*review_arguments, "--json")
        self.assertEqual(0, reviewed.returncode, reviewed.stderr)
        report = json.loads(reviewed.stdout)
        selection = report["selection"]
        self.assertEqual("prepared-provider-pull-request", selection["kind"])
        self.assertEqual("provider-base-to-head", selection["delta_kind"])
        self.assertEqual("closed", selection["pull_request_state"])
        self.assertTrue(selection["pull_request_merged"])
        self.assertEqual(self.original_base_oid, selection["base"]["oid"])
        self.assertEqual(self.original_head_oid, selection["head"]["oid"])
        self.assertEqual(self.provider_merge_oid, selection["provider_merge"]["oid"])
        scope = selection["committed_scope"]
        self.assertEqual(4, scope["repository"]["path_count"])
        self.assertEqual(3, scope["selected"]["path_count"])
        self.assertEqual(1, report["summary"]["machine_recipes"]["modified"])

        human = self._workbench(*review_arguments)
        self.assertEqual(0, human.returncode, human.stderr)
        self.assertIn("provider-bound supersymmetry pull request #2002", human.stdout)
        self.assertIn(
            "merged · reviewing provider-recorded base → head PR delta",
            human.stdout,
        )
        self.assertIn("Historical delta provider-recorded base", human.stdout)
        self.assertIn("Git hygiene", human.stdout)
        self.assertIn("diff --check    clean", human.stdout)

        orchestrated = self._workbench(
            "review",
            "pr",
            "2002",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
            "--json",
        )
        self.assertEqual(0, orchestrated.returncode, orchestrated.stderr)
        pr_report = json.loads(orchestrated.stdout)
        self.assertEqual("workbench-recipe-review-v2", pr_report["format"])
        self.assertEqual(
            "prepared-provider-pull-request", pr_report["selection"]["kind"]
        )
        self.assertEqual(2002, pr_report["selection"]["pull_request"])
        self.assertEqual(
            {
                "introduced_static_signals": 1,
                "preexisting_static_signals": 0,
                "candidate_static_signal_total": 1,
                "supplied_runtime_attention": False,
                "pr_strict": (
                    "introduced static signals and supplied runtime attention"
                ),
                "strict_all": (
                    "candidate-wide static signals, supplied runtime attention, "
                    "source configuration warnings, and Git hygiene attention"
                ),
            },
            pr_report["selection"]["attention_scope"],
        )
        self.assertEqual(
            {
                "state": "clean",
                "detail": "no whitespace or conflict-marker errors reported",
            },
            pr_report["selection"]["git_hygiene"],
        )

        pr_human = self._workbench(
            "review",
            "pr",
            "2002",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
        )
        self.assertEqual(0, pr_human.returncode, pr_human.stderr)
        self.assertIn("PR/delta attention", pr_human.stdout)
        self.assertIn("Baseline/candidate-wide attention", pr_human.stdout)
        self.assertIn("they do not drive PR --strict", pr_human.stdout)
        self.assertNotIn(
            "These report-level signals drive --strict", pr_human.stdout
        )

        pr_strict = self._workbench(
            "review",
            "pr",
            "2002",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
            "--strict",
        )
        self.assertEqual(1, pr_strict.returncode, pr_strict.stderr)
        self.assertIn("Introduced     1", pr_strict.stdout)

        runtime_log = self.root / "runtime-attention.log"
        runtime_log.write_text(
            "[00:00:00] [SERVER/ERROR] [fixture]: failure\n",
            encoding="utf-8",
        )
        runtime_strict = self._workbench(
            "review",
            "pr",
            "2002",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
            "--groovy-log",
            str(runtime_log),
            "--strict",
            "--json",
        )
        self.assertEqual(1, runtime_strict.returncode, runtime_strict.stderr)
        runtime_report = json.loads(runtime_strict.stdout)
        self.assertTrue(
            runtime_report["selection"]["attention_scope"][
                "supplied_runtime_attention"
            ]
        )

        pr_strict_all = self._workbench(
            "review",
            "pr",
            "2002",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
            "--strict-all",
        )
        self.assertEqual(1, pr_strict_all.returncode, pr_strict_all.stderr)
        self.assertIn("Candidate      1", pr_strict_all.stdout)

        mismatched = self._workbench(
            "review",
            "pr",
            "2003",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
            "--prepared-receipt",
            result["receipt_path"],
        )
        self.assertEqual(2, mismatched.returncode)
        self.assertIn("does not identify", mismatched.stderr)
        self.assertEqual(
            before_head, self._git("-C", str(self.checkout), "rev-parse", "HEAD")
        )
        self.assertEqual(
            before_status,
            self._git("-C", str(self.checkout), "status", "--porcelain=v1"),
        )

    def test_provider_bound_public_cli_has_no_fixture_bypass_and_bounds_timeout(self) -> None:
        common = (
            "review",
            "prepare-pr",
            "2002",
            "--profile",
            "supersymmetry",
            "--source",
            str(self.checkout),
        )
        rejected_fixture = self._workbench(
            *common,
            "--provider-metadata",
            str(self.provider_metadata),
        )
        self.assertEqual(2, rejected_fixture.returncode)
        self.assertIn("unrecognized arguments: --provider-metadata", rejected_fixture.stderr)

        for timeout in ("nan", "-1", "0", "601"):
            with self.subTest(timeout=timeout):
                rejected_timeout = self._workbench(
                    *common,
                    "--network-timeout",
                    timeout,
                )
                self.assertEqual(2, rejected_timeout.returncode)
                self.assertIn(
                    "network timeout must be finite, positive",
                    rejected_timeout.stderr,
                )


@unittest.skipUnless(shutil.which("git"), "Git is required for baseline-ref coverage")
class RecipeReviewGitRefCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workbench-recipe-review-git-ref-")
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.repository = self.temporary / "pack-repository"
        self.repository.mkdir()
        self.pack = self.repository / "packs" / "supersymmetry"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )

        self._git("init", "--quiet")
        self._git("config", "user.name", "Workbench Test")
        self._git("config", "user.email", "workbench-test@example.invalid")
        shutil.copytree(BASELINE, self.pack)
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "baseline fixture")
        self.baseline_revision = self._git("rev-parse", "HEAD").stdout.strip()
        self.baseline_tree = self._git("rev-parse", "HEAD^{tree}").stdout.strip()
        self.target_tip = self._git(
            "commit-tree",
            self.baseline_tree,
            "-p",
            self.baseline_revision,
            "-m",
            "target tip fixture",
        ).stdout.strip()
        self._git("update-ref", "refs/heads/review-target", self.target_tip)

        shutil.copytree(CANDIDATE, self.pack, dirs_exist_ok=True)
        self.assertNotEqual("", self._git_status())

    def _git(
        self,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=20,
            env=self.environment,
        )
        if check and result.returncode != 0:
            self.fail(
                f"Git fixture command failed ({result.returncode}): "
                f"{' '.join(arguments)}\n{result.stderr}"
            )
        return result

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKBENCH), "review", "recipes", *arguments],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            env=self.environment,
        )

    def _git_status(self) -> str:
        return self._git(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ).stdout

    def _candidate_tree_snapshot(self) -> tuple[tuple[str, str, int, str], ...]:
        rows: list[tuple[str, str, int, str]] = []
        for directory, directory_names, file_names in os.walk(
            self.repository,
            followlinks=False,
        ):
            current = Path(directory)
            if current == self.repository and ".git" in directory_names:
                directory_names.remove(".git")
            directory_names.sort()
            file_names.sort()
            for name in directory_names:
                path = current / name
                relative = path.relative_to(self.repository).as_posix()
                metadata = path.lstat()
                if path.is_symlink():
                    rows.append(
                        (relative, "symlink", metadata.st_mode, os.readlink(path))
                    )
                else:
                    rows.append((relative, "directory", metadata.st_mode, ""))
            for name in file_names:
                path = current / name
                relative = path.relative_to(self.repository).as_posix()
                metadata = path.lstat()
                if path.is_symlink():
                    rows.append(
                        (relative, "symlink", metadata.st_mode, os.readlink(path))
                    )
                else:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    rows.append((relative, "file", metadata.st_mode, digest))
        return tuple(rows)

    def _custody_snapshot(self) -> dict[str, object]:
        return {
            "tree": self._candidate_tree_snapshot(),
            "status": self._git_status(),
            "head": self._git("rev-parse", "HEAD").stdout.strip(),
            "worktrees": self._git("worktree", "list", "--porcelain").stdout,
        }

    def _baseline_ref_arguments(self, reference: str = "HEAD") -> tuple[str, ...]:
        return (
            "--profile",
            "supersymmetry",
            "--baseline-ref",
            reference,
            "--source",
            str(self.pack),
        )

    def _pr_base_arguments(
        self, reference: str = "refs/heads/review-target"
    ) -> tuple[str, ...]:
        return (
            "--profile",
            "supersymmetry",
            "--pr-base",
            reference,
            "--source",
            str(self.pack),
        )

    def _commit_candidate_pr(self) -> str:
        for index in range(3):
            (self.repository / f"pr-scope-{index}.txt").write_text(
                f"scope {index}\n", encoding="utf-8"
            )
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "candidate PR fixture")
        selected_paths = self._git(
            "diff",
            "--name-only",
            self.baseline_revision,
            "HEAD",
            "--",
            "packs/supersymmetry/groovy",
        ).stdout.splitlines()
        repository_paths = self._git(
            "diff", "--name-only", self.baseline_revision, "HEAD"
        ).stdout.splitlines()
        self.assertEqual(3, len(selected_paths))
        self.assertEqual(6, len(repository_paths))
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_human_review_resolves_ref_and_preserves_dirty_candidate(self) -> None:
        before = self._custody_snapshot()

        result = self._run(*self._baseline_ref_arguments())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertIn("Recipe review", result.stdout)
        self.assertIn("machine-recipe multiset · +1 / -1", result.stdout)
        self.assertIn("~ MIXER ×1", result.stdout)
        self.assertIn("properties duration: 20 → 40", result.stdout)
        self.assertTrue(result.stdout.startswith("Recipe review:"))
        self.assertIn("Git scope and provenance", result.stdout)
        self.assertRegex(result.stdout, r"Selection[^\n]*exact baseline")
        self.assertRegex(result.stdout, r"Requested base[^\n]*HEAD")
        self.assertRegex(
            result.stdout,
            rf"Resolved commit[^\n]*{self.baseline_revision}",
        )
        self.assertRegex(result.stdout, rf"Commit tree[^\n]*{self.baseline_tree}")
        self.assertRegex(
            result.stdout,
            r"Selected root[^\n]*packs/supersymmetry/groovy",
        )
        self.assertRegex(
            result.stdout,
            rf"Candidate[^\n]*working tree at {self.baseline_revision}[^\n]*dirty",
        )
        self.assertEqual(before, self._custody_snapshot())

    def test_json_review_binds_commit_and_dirty_worktree_bytes(self) -> None:
        before = self._custody_snapshot()

        result = self._run(*self._baseline_ref_arguments(), "--full-json-v1")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        report = json.loads(result.stdout)
        baseline_git = report["baseline"]["binding"]["git"]
        candidate_git = report["candidate"]["binding"]["git"]
        self.assertEqual(self.baseline_revision, baseline_git["revision"])
        self.assertFalse(baseline_git["dirty"])
        self.assertEqual(self.baseline_revision, candidate_git["revision"])
        self.assertTrue(candidate_git["dirty"])
        self.assertNotEqual(
            report["baseline"]["binding"]["source_sha256"],
            report["candidate"]["binding"]["source_sha256"],
        )
        added_recipes = [
            row
            for row in report["comparison"]["effects"]["added_rows"]
            if row["kind"] == "machine-recipe"
        ]
        removed_recipes = [
            row
            for row in report["comparison"]["effects"]["removed_rows"]
            if row["kind"] == "machine-recipe"
        ]
        self.assertEqual(1, sum(row["count"] for row in added_recipes))
        self.assertEqual(1, sum(row["count"] for row in removed_recipes))
        self.assertEqual(before, self._custody_snapshot())

    def test_pr_base_uses_merge_base_and_reports_committed_scope(self) -> None:
        candidate_tip = self._commit_candidate_pr()
        before = self._custody_snapshot()

        result = self._run(*self._pr_base_arguments())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertTrue(result.stdout.startswith("Recipe review:"))
        self.assertIn("Git scope and provenance", result.stdout)
        self.assertRegex(result.stdout, r"Selection[^\n]*PR merge-base")
        self.assertLess(
            result.stdout.index("Recipe review:"),
            result.stdout.index("Git scope and provenance"),
        )
        self.assertLess(
            result.stdout.index("Git scope and provenance"),
            result.stdout.index("\nFindings\n"),
        )
        self.assertRegex(
            result.stdout,
            r"Requested target[^\n]*refs/heads/review-target",
        )
        self.assertRegex(result.stdout, rf"Target tip[^\n]*{self.target_tip}")
        self.assertRegex(
            result.stdout,
            rf"Merge base[^\n]*{self.baseline_revision}",
        )
        self.assertRegex(
            result.stdout,
            r"PR scope[^\n]*3 selected / 6 total committed files",
        )
        self.assertIn(
            "Selected files   packs/supersymmetry/groovy/material/Materials.groovy",
            result.stdout,
        )
        self.assertIn(
            "packs/supersymmetry/groovy/postInit/Recipes.groovy",
            result.stdout,
        )
        self.assertIn(
            "packs/supersymmetry/groovy/preInit/RegisterMetaItems.groovy",
            result.stdout,
        )
        self.assertIn("Excluded files   pr-scope-0.txt", result.stdout)
        self.assertIn("pr-scope-1.txt", result.stdout)
        self.assertIn("pr-scope-2.txt", result.stdout)
        self.assertIn(
            "Dirty scope      Git status observed clean before analysis",
            result.stdout,
        )
        self.assertRegex(
            result.stdout,
            r"Target freshness[^\n]*unverified[^\n]*local ref only[^\n]*no fetch",
        )
        self.assertRegex(
            result.stdout,
            rf"Candidate[^\n]*working tree at {candidate_tip}[^\n]*clean",
        )
        self.assertIn("machine-recipe multiset · +1 / -1", result.stdout)
        self.assertEqual(before, self._custody_snapshot())

    def test_pr_base_v2_retains_bounded_selected_and_excluded_scope(self) -> None:
        self._commit_candidate_pr()
        before = self._custody_snapshot()

        result = self._run(*self._pr_base_arguments(), "--json")

        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        scope = report["selection"]["committed_scope"]
        self.assertEqual(6, scope["repository"]["path_count"])
        self.assertEqual(3, scope["selected"]["path_count"])
        self.assertEqual(3, scope["excluded"]["path_count"])
        self.assertEqual(
            ["pr-scope-0.txt", "pr-scope-1.txt", "pr-scope-2.txt"],
            scope["excluded"]["paths"],
        )
        self.assertFalse(scope["repository"]["truncated"])
        self.assertEqual(before, self._custody_snapshot())

    def test_pr_base_distinguishes_dirty_selected_and_excluded_paths(self) -> None:
        candidate_tip = self._commit_candidate_pr()
        selected_dirty = self.pack / "groovy" / "postInit" / "Recipes.groovy"
        selected_dirty.write_text(
            selected_dirty.read_text(encoding="utf-8") + "// dirty review byte\n",
            encoding="utf-8",
        )
        (self.repository / "dirty-outside.txt").write_text(
            "outside selected root\n", encoding="utf-8"
        )
        before = self._custody_snapshot()

        result = self._run(*self._pr_base_arguments())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertRegex(
            result.stdout,
            rf"Candidate[^\n]*working tree at {candidate_tip}[^\n]*dirty",
        )
        self.assertIn(
            "Dirty scope      1 selected / 2 total working-tree paths",
            result.stdout,
        )
        self.assertIn("not included in committed PR counts", result.stdout)
        self.assertIn(
            "selected dirty paths affect comparison scope",
            result.stdout,
        )
        self.assertIn("analysis reads the resulting candidate tree", result.stdout)
        self.assertNotIn("selected dirty bytes", result.stdout)
        self.assertIn(
            "Dirty selected   packs/supersymmetry/groovy/postInit/Recipes.groovy",
            result.stdout,
        )
        self.assertIn("Dirty excluded   dirty-outside.txt", result.stdout)
        self.assertEqual(before, self._custody_snapshot())

    def test_pr_base_reports_rename_out_as_scope_not_analyzed_bytes(self) -> None:
        self._commit_candidate_pr()
        selected = "packs/supersymmetry/groovy/postInit/Recipes.groovy"
        excluded = "Recipes-moved-out.groovy"
        self._git("mv", selected, excluded)
        before = self._custody_snapshot()

        result = self._run(*self._pr_base_arguments())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "Dirty scope      1 selected / 2 total working-tree paths",
            result.stdout,
        )
        self.assertIn("selected dirty paths affect comparison scope", result.stdout)
        self.assertIn("analysis reads the resulting candidate tree", result.stdout)
        self.assertIn(f"Dirty selected   {selected}", result.stdout)
        self.assertIn(f"Dirty excluded   {excluded}", result.stdout)
        self.assertNotIn("selected dirty bytes", result.stdout)
        self.assertEqual(before, self._custody_snapshot())

    def test_pr_base_reports_rename_in_as_scope_not_analyzed_source(self) -> None:
        self._commit_candidate_pr()
        excluded = "pr-scope-0.txt"
        selected = "packs/supersymmetry/groovy/postInit/MovedIn.groovy"
        self._git("mv", excluded, selected)
        before = self._custody_snapshot()

        result = self._run(*self._pr_base_arguments())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "Dirty scope      1 selected / 2 total working-tree paths",
            result.stdout,
        )
        self.assertIn("selected dirty paths affect comparison scope", result.stdout)
        self.assertIn("analysis reads the resulting candidate tree", result.stdout)
        self.assertIn(f"Dirty selected   {selected}", result.stdout)
        self.assertIn(f"Dirty excluded   {excluded}", result.stdout)
        self.assertNotIn("selected dirty bytes", result.stdout)
        self.assertEqual(before, self._custody_snapshot())

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "requires a Linux filesystem with arbitrary filename bytes",
    )
    def test_pr_base_escapes_arbitrary_dirty_git_path_bytes(self) -> None:
        self._commit_candidate_pr()
        # Some WSL installations point TMP at DrvFS, which replaces invalid
        # byte names before Git can observe them. Keep this byte-custody case
        # on the native POSIX filesystem.
        with tempfile.TemporaryDirectory(
            prefix="workbench-git-path-bytes-",
            dir="/tmp",
        ) as directory:
            original_repository = self.repository
            original_pack = self.pack
            self.repository = Path(directory) / "pack-repository"
            shutil.copytree(original_repository, self.repository)
            self.pack = self.repository / "packs" / "supersymmetry"
            try:
                root = os.fsencode(self.repository)
                names = (b"raw-\xff", b"literal\\xff", b"line\nbreak.groovy")
                for name in names:
                    descriptor = os.open(
                        root + b"/" + name,
                        os.O_WRONLY | os.O_CREAT,
                        0o600,
                    )
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(b"dirty fixture\n")
                before = (
                    self._candidate_tree_snapshot(),
                    self._git("rev-parse", "HEAD").stdout.strip(),
                    self._git("worktree", "list", "--porcelain").stdout,
                )

                result = self._run(*self._pr_base_arguments())

                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("Dirty excluded", result.stdout)
                self.assertIn(r"raw-\xff", result.stdout)
                self.assertIn(r"literal\\xff", result.stdout)
                self.assertIn(r"line\x0abreak.groovy", result.stdout)
                self.assertNotIn("line\nbreak.groovy", result.stdout)
                after = (
                    self._candidate_tree_snapshot(),
                    self._git("rev-parse", "HEAD").stdout.strip(),
                    self._git("worktree", "list", "--porcelain").stdout,
                )
                self.assertEqual(before, after)
            finally:
                self.repository = original_repository
                self.pack = original_pack

    def test_pr_base_full_json_v1_binds_exact_merge_base(self) -> None:
        candidate_tip = self._commit_candidate_pr()
        before = self._custody_snapshot()

        result = self._run(*self._pr_base_arguments(), "--full-json-v1")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("workbench-groovy-pack-program-report-v1", report["format"])
        self.assertEqual(
            self.baseline_revision,
            report["baseline"]["binding"]["git"]["revision"],
        )
        self.assertEqual(
            candidate_tip,
            report["candidate"]["binding"]["git"]["revision"],
        )
        self.assertNotIn("Requested target", result.stdout)
        self.assertNotIn("Target freshness", result.stdout)
        self.assertEqual(before, self._custody_snapshot())

    def test_baseline_ref_remains_exact_instead_of_using_merge_base(self) -> None:
        result = self._run(
            *self._baseline_ref_arguments("refs/heads/review-target"),
            "--full-json-v1",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            self.target_tip,
            report["baseline"]["binding"]["git"]["revision"],
        )
        self.assertNotEqual(
            self.baseline_revision,
            report["baseline"]["binding"]["git"]["revision"],
        )

    def test_ref_review_preserves_a_direct_groovy_root_selection(self) -> None:
        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline-ref",
            "HEAD",
            "--source",
            str(self.pack / "groovy"),
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Selected root    packs/supersymmetry/groovy", result.stdout)
        self.assertIn("machine-recipe multiset · +1 / -1", result.stdout)

    def test_review_rejects_an_invalid_baseline_ref_without_residue(self) -> None:
        before = self._custody_snapshot()

        result = self._run(
            *self._baseline_ref_arguments("refs/heads/does-not-exist")
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("baseline-ref", result.stderr.casefold())
        self.assertTrue(
            "resolve" in result.stderr.casefold()
            or "commit" in result.stderr.casefold()
        )
        self.assertIn("refresh the requested ref outside Workbench", result.stderr)
        self.assertIn("Workbench never fetches", result.stderr)
        self.assertEqual(before, self._custody_snapshot())

    def test_review_rejects_an_invalid_pr_target_without_residue(self) -> None:
        before = self._custody_snapshot()

        result = self._run(
            *self._pr_base_arguments("refs/heads/does-not-exist")
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--pr-base", result.stderr)
        self.assertIn("local commit", result.stderr)
        self.assertIn("refresh the requested ref outside Workbench", result.stderr)
        self.assertIn("Workbench never fetches", result.stderr)
        self.assertEqual(before, self._custody_snapshot())

    def test_review_rejects_baseline_ref_for_a_non_git_source(self) -> None:
        source = self.temporary / "non-git-pack"
        shutil.copytree(CANDIDATE, source)
        before = self._standalone_tree_snapshot(source)

        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline-ref",
            "HEAD",
            "--source",
            str(source),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("git", result.stderr.casefold())
        self.assertTrue(
            "repository" in result.stderr.casefold()
            or "worktree" in result.stderr.casefold()
        )
        self.assertIn("--baseline PATH", result.stderr)
        self.assertEqual(before, self._standalone_tree_snapshot(source))

    def test_review_explains_an_incompatible_source_layout(self) -> None:
        source = self.repository / "not-a-pack"
        source.mkdir()
        before = self._custody_snapshot()

        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline-ref",
            "HEAD",
            "--source",
            str(source),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("neither a compatible pack root nor its Groovy root", result.stderr)
        self.assertIn("Choose the directory containing", result.stderr)
        self.assertEqual(before, self._custody_snapshot())

    def test_review_rejects_both_baseline_selectors(self) -> None:
        before = self._custody_snapshot()

        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline",
            str(BASELINE),
            "--baseline-ref",
            "HEAD",
            "--source",
            str(self.pack),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--baseline", result.stderr)
        self.assertIn("--baseline-ref", result.stderr)
        self.assertTrue(
            "exclusive" in result.stderr.casefold()
            or "exactly one" in result.stderr.casefold()
        )
        self.assertEqual(before, self._custody_snapshot())

    def test_review_rejects_exact_ref_and_pr_base_together(self) -> None:
        before = self._custody_snapshot()

        result = self._run(
            "--profile",
            "supersymmetry",
            "--baseline-ref",
            "HEAD",
            "--pr-base",
            "refs/heads/review-target",
            "--source",
            str(self.pack),
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--baseline-ref", result.stderr)
        self.assertIn("--pr-base", result.stderr)
        self.assertIn("mutually exclusive", result.stderr)
        self.assertEqual(before, self._custody_snapshot())

    def test_materialization_is_cleaned_when_baseline_analysis_fails(self) -> None:
        (self.pack / "groovy" / "runConfig.json").unlink()
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "invalid historical source")
        invalid_revision = self._git("rev-parse", "HEAD").stdout.strip()
        shutil.copytree(CANDIDATE, self.pack, dirs_exist_ok=True)
        before = self._custody_snapshot()

        result = self._run(*self._baseline_ref_arguments(invalid_revision))

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("runconfig", result.stderr.casefold())
        self.assertEqual(before, self._custody_snapshot())

    @staticmethod
    def _standalone_tree_snapshot(
        root: Path,
    ) -> tuple[tuple[str, str, int, str], ...]:
        rows: list[tuple[str, str, int, str]] = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if path.is_symlink():
                rows.append((relative, "symlink", metadata.st_mode, os.readlink(path)))
            elif path.is_dir():
                rows.append((relative, "directory", metadata.st_mode, ""))
            else:
                rows.append(
                    (
                        relative,
                        "file",
                        metadata.st_mode,
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )
        return tuple(rows)


if __name__ == "__main__":
    unittest.main()
