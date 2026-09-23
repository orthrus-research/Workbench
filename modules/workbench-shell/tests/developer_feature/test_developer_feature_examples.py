"""Tests for the bounded, read-only Supersymmetry example catalog."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.developer_feature_cli import main as feature_main  # noqa: E402
from workbench_profile_supersymmetry.examples import (  # noqa: E402
    CATALOG_FORMAT,
    PLAN_RESULT_FORMAT,
    DeveloperFeatureExampleError,
    EXAMPLE_DESCRIPTORS,
    load_feature_examples,
)
from workbench_shell.developer_feature_workflow_views import (  # noqa: E402
    COMPACT_PLAN_FORMAT,
    build_compact_plan_result,
    validate_compact_plan_result,
)


class DeveloperFeatureExampleTests(unittest.TestCase):
    def _quest_workspace(self, root: Path) -> Path:
        workspace = root / "supersymmetry"
        quests = workspace / "config/betterquesting/DefaultQuests/Quests/5"
        language_root = (
            workspace
            / "config/betterquesting/resources/supersymmetry/lang"
        )
        quests.mkdir(parents=True)
        language_root.mkdir(parents=True)
        (workspace / "mods").mkdir()
        (workspace / "groovy").mkdir()
        (workspace / "pack.toml").write_text(
            "name = \"Supersymmetry\"\n"
            "author = \"SymmetricDevs\"\n"
            "version = \"example-test\"\n"
            "pack-format = \"packwiz:1.1.0\"\n\n"
            "[index]\n"
            "file = \"index.toml\"\n"
            "hash-format = \"sha256\"\n"
            "hash = \"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\"\n\n"
            "[versions]\n"
            "forge = \"14.23.5.2860\"\n"
            "minecraft = \"1.12.2\"\n",
            encoding="utf-8",
        )
        (workspace / "index.toml").write_text("", encoding="utf-8")
        titles = {
            757: "Existing prerequisite",
            90454916: "Hot Isostatic Press",
            336324327: "Gas Atomizer",
        }
        for quest_id, prerequisites in (
            (757, []),
            (90454916, []),
            (336324327, [757]),
        ):
            value = {
                "preRequisiteTypes:7": [1 for _ in prerequisites],
                "preRequisites:11": prerequisites,
                "properties:10": {
                    "betterquesting:10": {
                        "desc:8": f"susy.quest.db.{quest_id}.desc",
                        "name:8": f"susy.quest.db.{quest_id}.title",
                    }
                },
                "questID:3": quest_id,
                "tasks:9": {},
            }
            (quests / f"{quest_id}.json").write_text(
                json.dumps(value, indent=2), encoding="utf-8"
            )
        (language_root / "en_us.lang").write_text(
            "".join(
                f"susy.quest.db.{quest_id}.title={title}\n"
                f"susy.quest.db.{quest_id}.desc=Description {quest_id}\n"
                for quest_id, title in titles.items()
            ),
            encoding="utf-8",
        )
        for arguments in (
            ("init", "--quiet"),
            ("config", "user.name", "Workbench Example Test"),
            ("config", "user.email", "workbench@example.invalid"),
            ("add", "--all"),
            ("commit", "--quiet", "-m", "example fixture"),
        ):
            subprocess.run(
                ["git", "-C", str(workspace), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        return workspace

    def test_catalog_is_exact_bounded_and_digest_bound(self) -> None:
        result = load_feature_examples(ROOT)

        self.assertEqual(CATALOG_FORMAT, result["format"])
        self.assertEqual("available", result["state"])
        self.assertEqual("supersymmetry", result["profile"])
        self.assertEqual({"kind": "all", "value": None}, result["selection"])
        self.assertEqual(3, result["count"])
        self.assertEqual(
            [row["example_key"] for row in EXAMPLE_DESCRIPTORS],
            [row["example_key"] for row in result["examples"]],
        )
        for example in result["examples"]:
            raw = (ROOT / example["source_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), example["source_sha256"])
            self.assertEqual(len(raw), example["source_size"])
            self.assertFalse(example["record"]["identity_bearing"])
            boundary = example["record"]["authority_boundary"]
            self.assertFalse(boundary["profile_action_authorized"])
            self.assertFalse(boundary["release_qualified"])
            self.assertFalse(boundary["publication_authorized"])
        self.assertTrue(result["authority_boundary"]["read_only"])
        self.assertFalse(result["authority_boundary"]["construction_authority"])

    def test_family_and_exact_key_select_one_record(self) -> None:
        family = load_feature_examples(ROOT, selector="recipe-change")
        self.assertEqual({"kind": "family", "value": "recipe-change"}, family["selection"])
        self.assertEqual(1, family["count"])
        key = family["examples"][0]["example_key"]

        exact = load_feature_examples(ROOT, selector=key)
        self.assertEqual({"kind": "example-key", "value": key}, exact["selection"])
        self.assertEqual(family["examples"], exact["examples"])

        with self.assertRaisesRegex(DeveloperFeatureExampleError, "unknown"):
            load_feature_examples(ROOT, selector="recipe")

    def test_loader_does_not_scan_and_rejects_overclaiming_packaged_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            source = ROOT / "profiles/packs/supersymmetry/blueprints/examples"
            target = suite / "profiles/packs/supersymmetry/blueprints/examples"
            shutil.copytree(source, target)
            (target / "unlisted.json").write_text(
                '{"example_key":"not-packaged"}\n', encoding="utf-8"
            )

            self.assertEqual(3, load_feature_examples(suite)["count"])
            selected = target / "quest-for-process-gas-atomizer.json"
            value = json.loads(selected.read_text(encoding="utf-8"))
            value["authority_boundary"]["release_qualified"] = True
            selected.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                DeveloperFeatureExampleError,
                "overclaims release_qualified",
            ):
                load_feature_examples(suite)
            value["authority_boundary"]["release_qualified"] = False
            value["request"]["unowned_argument"] = "ignored"
            selected.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                DeveloperFeatureExampleError,
                "request fields are invalid",
            ):
                load_feature_examples(suite)

    def test_public_cli_emits_json_and_honest_human_summary(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = feature_main(
                ["examples", "quest-for-process", "--json"],
                suite_root=ROOT,
            )
        self.assertEqual(0, code, errors.getvalue())
        record = json.loads(output.getvalue())
        self.assertEqual(1, record["count"])
        self.assertEqual("required-not-observed", record["examples"][0]["runtime_evidence_state"])

        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = feature_main(["examples"], suite_root=ROOT)
        self.assertEqual(0, code, errors.getvalue())
        self.assertIn("Supersymmetry Blueprint examples: AVAILABLE (3)", output.getvalue())
        self.assertIn("Runtime evidence: complete", output.getvalue())
        self.assertIn("Runtime evidence: required-not-observed", output.getvalue())
        self.assertIn("no profile support, action, release", output.getvalue())

    def test_public_example_plan_uses_owner_planner_and_retains_ordinary_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self._quest_workspace(root)
            state = root / "state"
            target = (
                workspace
                / "config/betterquesting/DefaultQuests/Quests/5/336324327.json"
            )
            before = target.read_bytes()
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = feature_main(
                    [
                        "plan",
                        "--example",
                        "supersymmetry-quest-for-process-gas-atomizer",
                        str(workspace),
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    suite_root=ROOT,
                )
            self.assertEqual(0, code, errors.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(PLAN_RESULT_FORMAT, result["format"])
            self.assertEqual("ready", result["state"])
            self.assertEqual("quest-for-process", result["example"]["family"])
            self.assertEqual(
                "provenance-only",
                result["applicability"]["historical"]["state"],
            )
            self.assertEqual(
                "different-current-revision",
                result["applicability"]["historical"]["workspace_relation"],
            )
            self.assertEqual(
                "owner-plan-validated",
                result["applicability"]["current"]["state"],
            )
            plan = result["plan"]
            self.assertEqual(
                "experimental-ready-runtime-unverified", plan["state"]
            )
            retained = Path(result["retained_plan_uri"].removeprefix("file://"))
            self.assertTrue(retained.is_file())
            self.assertEqual(plan, json.loads(retained.read_text(encoding="utf-8")))
            self.assertEqual(before, target.read_bytes())

            checked = io.StringIO()
            with redirect_stdout(checked), redirect_stderr(io.StringIO()):
                check_code = feature_main(
                    [
                        "check",
                        "quest-for-process",
                        plan["id"],
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    suite_root=ROOT,
                )
            self.assertEqual(0, check_code)
            self.assertEqual("ready", json.loads(checked.getvalue())["state"])

    def test_public_example_plan_offers_compact_machine_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self._quest_workspace(root)
            state = root / "state"
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = feature_main(
                    [
                        "plan",
                        "--example",
                        "supersymmetry-quest-for-process-gas-atomizer",
                        str(workspace),
                        "--state-root",
                        str(state),
                        "--compact-json",
                    ],
                    suite_root=ROOT,
                )
            self.assertEqual(0, code, errors.getvalue())
            rendered = output.getvalue()
            self.assertNotIn("base64", rendered.casefold())
            result = json.loads(rendered)
            self.assertEqual(COMPACT_PLAN_FORMAT, result["format"])
            self.assertEqual("ready", result["state"])
            self.assertEqual(
                "supersymmetry-quest-for-process-gas-atomizer",
                result["example"]["example_key"],
            )
            self.assertEqual(
                "owner-plan-validated",
                result["example"]["current_applicability_state"],
            )
            self.assertEqual(result, validate_compact_plan_result(result))

    def test_compact_material_example_accepts_absent_historical_binding(self) -> None:
        result = build_compact_plan_result(
            family="material-fluid-recipe",
            owner_plan=None,
            retained_plan_uri=None,
            transaction_view=None,
            example_result={
                "applicability": {
                    "current": {
                        "reason": "selected owner is unavailable",
                        "state": "owner-plan-rejected",
                    },
                    "historical": {"state": "not-declared"},
                },
                "example": {
                    "example_key": "supersymmetry-material-fluid-recipe-radon"
                },
            },
        )
        self.assertEqual("not-applicable", result["state"])
        self.assertEqual(
            "not-declared",
            result["example"]["historical_applicability_state"],
        )
        self.assertEqual(result, validate_compact_plan_result(result))

    def test_example_plan_reports_current_noop_without_retaining_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self._quest_workspace(root)
            state = root / "state"
            target = (
                workspace
                / "config/betterquesting/DefaultQuests/Quests/5/336324327.json"
            )
            quest = json.loads(target.read_text(encoding="utf-8"))
            quest["preRequisiteTypes:7"].append(1)
            quest["preRequisites:11"].append(90454916)
            target.write_text(json.dumps(quest, indent=2), encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(workspace), "add", "--all"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "commit",
                    "--quiet",
                    "-m",
                    "example already applied",
                ],
                check=True,
            )

            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = feature_main(
                    [
                        "plan",
                        "example",
                        "supersymmetry-quest-for-process-gas-atomizer",
                        str(workspace),
                        "--state-root",
                        str(state),
                        "--json",
                    ],
                    suite_root=ROOT,
                )
            self.assertEqual(1, code, errors.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual("not-applicable", result["state"])
            self.assertEqual(
                "owner-plan-rejected",
                result["applicability"]["current"]["state"],
            )
            self.assertIn(
                "already exists", result["applicability"]["current"]["reason"]
            )
            self.assertIsNone(result["plan"])
            self.assertIsNone(result["retained_plan_uri"])
            self.assertFalse((state / "plans").exists())


if __name__ == "__main__":
    unittest.main()
