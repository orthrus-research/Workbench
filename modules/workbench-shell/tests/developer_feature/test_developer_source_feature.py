"""Focused plan and transaction tests for profile-owned source features."""

from __future__ import annotations

import base64
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.developer_feature import (  # noqa: E402
    DeveloperFeatureError,
    _review,
    _seal,
    _unified_diff,
    retain_feature_record,
    transaction_state_root,
    workspace_transaction_lock_path_for_uri,
)
from workbench_shell.developer_source_feature import (  # noqa: E402
    AUTHORITY_BOUNDARY,
    RECEIPT_FORMAT,
    RECEIPT_KIND,
    RECOVERY_FORMAT,
    RECOVERY_KIND,
    apply_source_feature_plan,
    build_quest_for_process_plan,
    quest_for_process_options,
    rollback_source_feature,
    validate_source_feature_plan,
    validate_source_feature_recovery,
    verify_source_feature_plan,
)
from workbench_shell.developer_feature_cli import main as feature_main  # noqa: E402


PACK_TOML = """\
name = "Supersymmetry"
author = "SymmetricDevs"
version = "test"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

[versions]
forge = "14.23.5.2860"
minecraft = "1.12.2"
"""


def _git(root: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)


def _quest(quest_id: int, prerequisites: list[int]) -> bytes:
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
    return json.dumps(value, indent=2).replace("\n", "\r\n").encode("utf-8")


def _checkout(parent: Path) -> Path:
    root = parent / "supersymmetry"
    quests = root / "config/betterquesting/DefaultQuests/Quests/5"
    language = root / "config/betterquesting/resources/supersymmetry/lang"
    quests.mkdir(parents=True)
    language.mkdir(parents=True)
    (root / "mods").mkdir()
    (root / "groovy").mkdir()
    (root / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
    (root / "index.toml").write_text("", encoding="utf-8")
    for quest_id, prerequisites in ((100, [200]), (200, []), (300, [])):
        (quests / f"{quest_id}.json").write_bytes(
            _quest(quest_id, prerequisites)
        )
    (language / "en_us.lang").write_bytes(
        b"susy.quest.db.100.title=Old title\r\n"
        b"susy.quest.db.100.desc=Old description\r\n"
        b"susy.quest.db.200.title=Second\r\n"
        b"susy.quest.db.200.desc=Second description\r\n"
        b"susy.quest.db.300.title=Third\r\n"
        b"susy.quest.db.300.desc=Third description\r\n"
    )
    recipe_maps = root / "groovy/prePostInit/Recipemaps.groovy"
    recipe_maps.parent.mkdir(parents=True)
    recipe_maps.write_text(
        "package prePostInit\n\n"
        "class Recipemaps {\n"
        "    static final def MIXER = recipemap('mixer')\n"
        "    static final def BR = recipemap('batch_reactor')\n"
        "}\n",
        encoding="utf-8",
    )
    recipe_owner = root / "groovy/postInit/chemistry/Probe.groovy"
    recipe_owner.parent.mkdir(parents=True)
    recipe_owner.write_text(
        "import static prePostInit.Recipemaps.*\n"
        "import static gregtech.api.GTValues.*\n\n"
        "MIXER.recipeBuilder()\n"
        "    .fluidInputs(fluid('water') * 1000)\n"
        "    .fluidOutputs(fluid('distilled_water') * 1000)\n"
        "    .duration(20)\n"
        "    .EUt(VA[LV])\n"
        "    .buildAndRegister()\n",
        encoding="utf-8",
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Workbench Source Feature Test")
    _git(root, "config", "user.email", "workbench@example.invalid")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "synthetic quest baseline")
    return root


class DeveloperSourceFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_parent = ROOT / ".workbench/test-tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_parent)
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.state = self.root / "state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self, **overrides: object) -> dict:
        request: dict[str, object] = {
            "quest_id": 100,
            "add_prerequisite_id": 300,
            "requirement_type": "IMPLICIT",
        }
        request.update(overrides)
        return build_quest_for_process_plan(ROOT, self.checkout, **request)

    def test_quest_options_join_owner_path_title_and_prerequisite_type(self) -> None:
        before = {
            path.relative_to(self.checkout).as_posix(): path.read_bytes()
            for path in self.checkout.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        result = quest_for_process_options(
            ROOT,
            self.checkout,
            query="old title",
            limit=10,
        )
        self.assertEqual(
            "workbench-supersymmetry-quest-for-process-options-v1",
            result["format"],
        )
        self.assertEqual(3, result["total_count"])
        self.assertEqual(1, result["matched_count"])
        self.assertEqual(
            {
                "current_prerequisites": [
                    {"quest_id": 200, "requirement_type": "IMPLICIT"}
                ],
                "localization_state": "resolved",
                "localized_title": "Old title",
                "path": (
                    "config/betterquesting/DefaultQuests/Quests/5/100.json"
                ),
                "quest_id": 100,
                "title_key": "susy.quest.db.100.title",
            },
            result["options"][0],
        )
        self.assertFalse(result["authority_boundary"]["mutation_authorized"])

        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = feature_main(
                [
                    "options",
                    "quest-for-process",
                    str(self.checkout),
                    "--query",
                    "third",
                    "--limit",
                    "1",
                    "--json",
                ],
                suite_root=ROOT,
            )
        self.assertEqual(0, code, errors.getvalue())
        cli_result = json.loads(output.getvalue())
        self.assertEqual(300, cli_result["options"][0]["quest_id"])
        self.assertEqual(before, {
            path.relative_to(self.checkout).as_posix(): path.read_bytes()
            for path in self.checkout.rglob("*")
            if path.is_file() and ".git" not in path.parts
        })

    def test_plan_is_reviewable_reproducible_and_does_not_mutate(self) -> None:
        target = (
            self.checkout
            / "config/betterquesting/DefaultQuests/Quests/5/100.json"
        )
        before = target.read_bytes()
        plan = self._plan(title="Gas Atomizer")

        self.assertEqual(before, target.read_bytes())
        self.assertEqual(
            ["quest-definition", "quest-localization"],
            [row["role"] for row in plan["operations"]],
        )
        self.assertEqual([], plan["source_dependencies"])
        self.assertEqual("ready", verify_source_feature_plan(ROOT, plan)["state"])
        retained = retain_feature_record(self.state, "plans", plan)
        self.assertTrue(retained.is_file())

        forged = deepcopy(plan)
        forged["operations"][0]["diff"] += "forged\n"
        with self.assertRaisesRegex(
            DeveloperFeatureError, "identity|diff|operation"
        ):
            validate_source_feature_plan(forged)

    def test_resealed_non_blueprint_operation_is_rejected_offline(self) -> None:
        plan = self._plan()
        forged = deepcopy(plan)
        operation = forged["operations"][0]
        arbitrary = b'{\r\n  "questID:3": 100\r\n}'
        before = base64.b64decode(operation["before_base64"], validate=True)
        operation["after_base64"] = base64.b64encode(arbitrary).decode("ascii")
        operation["after_sha256"] = hashlib.sha256(arbitrary).hexdigest()
        operation["after_size"] = len(arbitrary)
        operation["diff"] = _unified_diff(before, arbitrary, operation["path"])
        forged["review"] = _review(forged["operations"])
        body = dict(forged)
        body.pop("id")
        forged = _seal(plan["kind"], body)

        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "profile Blueprint",
        ):
            validate_source_feature_plan(forged, suite_root=ROOT)

    def test_applied_recovery_requires_an_applied_nested_receipt(self) -> None:
        plan = self._plan()
        rejected = _seal(
            RECEIPT_KIND,
            {
                "authority_boundary": dict(AUTHORITY_BOUNDARY),
                "diagnostic_code": "BLUEPRINTS_M2_STALE_PLAN",
                "format": RECEIPT_FORMAT,
                "kind": RECEIPT_KIND,
                "mutation_state": "not-started",
                "plan_id": plan["id"],
                "rollback": "not-needed",
                "schema_version": 1,
                "state": "rejected",
            },
        )
        recovery = _seal(
            RECOVERY_KIND,
            {
                "application_receipt": rejected,
                "attempted_ordinals": list(range(len(plan["operations"]))),
                "diagnostic_code": None,
                "format": RECOVERY_FORMAT,
                "kind": RECOVERY_KIND,
                "plan_id": plan["id"],
                "schema_version": 1,
                "state": "applied",
                "workspace_mutated": False,
            },
        )
        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "requires an applied receipt",
        ):
            validate_source_feature_recovery(
                recovery,
                plan,
                suite_root=ROOT,
            )

    def test_exact_consent_apply_and_rollback_restore_bytes(self) -> None:
        plan = self._plan(title="Gas Atomizer")
        before = {
            row["path"]: (self.checkout / row["path"]).read_bytes()
            for row in plan["operations"]
        }
        transaction = transaction_state_root(self.state, plan["id"])
        lock = workspace_transaction_lock_path_for_uri(
            plan["workspace_uri"], lock_root=self.state
        )
        with self.assertRaisesRegex(DeveloperFeatureError, "exact"):
            apply_source_feature_plan(
                ROOT,
                plan,
                transaction,
                consent_plan_id="wrong",
                transaction_lock=lock,
            )

        receipt = apply_source_feature_plan(
            ROOT,
            plan,
            transaction,
            consent_plan_id=plan["id"],
            transaction_lock=lock,
        )
        self.assertEqual("applied", receipt["state"])
        for row in plan["operations"]:
            self.assertEqual(
                row["after_sha256"],
                hashlib.sha256((self.checkout / row["path"]).read_bytes()).hexdigest(),
            )
        rollback = rollback_source_feature(
            plan,
            transaction,
            application_receipt=receipt,
            transaction_lock=lock,
        )
        self.assertEqual("restored", rollback["state"])
        for relative, raw in before.items():
            self.assertEqual(raw, (self.checkout / relative).read_bytes())

    def test_any_quest_graph_drift_makes_plan_stale_before_mutation(self) -> None:
        plan = self._plan()
        selected = self.checkout / plan["operations"][0]["path"]
        selected_before = selected.read_bytes()
        dependency = (
            self.checkout
            / "config/betterquesting/DefaultQuests/Quests/5/200.json"
        )
        dependency.write_bytes(dependency.read_bytes() + b"\r\n")

        result = verify_source_feature_plan(ROOT, plan)
        self.assertEqual("stale", result["state"])
        with self.assertRaisesRegex(DeveloperFeatureError, "stale"):
            apply_source_feature_plan(
                ROOT,
                plan,
                transaction_state_root(self.state, plan["id"]),
                consent_plan_id=plan["id"],
                transaction_lock=workspace_transaction_lock_path_for_uri(
                    plan["workspace_uri"], lock_root=self.state
                ),
            )
        self.assertEqual(selected_before, selected.read_bytes())

    def test_localization_only_plan_updates_no_quest_definition(self) -> None:
        plan = build_quest_for_process_plan(
            ROOT,
            self.checkout,
            quest_id=100,
            title="A clearer title",
        )
        self.assertEqual(
            ["quest-localization"],
            [row["role"] for row in plan["operations"]],
        )
        self.assertEqual(
            ["quest-definition-source"],
            [row["role"] for row in plan["source_dependencies"]],
        )
        self.assertEqual("ready", verify_source_feature_plan(ROOT, plan)["state"])

    def test_public_cli_retains_checks_applies_and_rolls_back(self) -> None:
        def invoke(arguments: list[str]) -> tuple[int, dict]:
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = feature_main(arguments, suite_root=ROOT)
            self.assertEqual(0, code, errors.getvalue())
            return code, json.loads(output.getvalue())

        _, plan = invoke(
            [
                "plan",
                "quest-for-process",
                str(self.checkout),
                "--quest-id",
                "100",
                "--add-prerequisite-id",
                "300",
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        _, check = invoke(
            [
                "check",
                "quest-for-process",
                plan["id"],
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        self.assertEqual("ready", check["state"])
        _, applied = invoke(
            [
                "apply",
                "quest-for-process",
                plan["id"],
                "--consent",
                plan["id"],
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        self.assertEqual("applied", applied["state"])
        _, rollback = invoke(
            [
                "rollback",
                "quest-for-process",
                plan["id"],
                applied["id"],
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        self.assertEqual("restored", rollback["state"])

    def test_public_recipe_change_cli_adds_and_restores_one_recipe(self) -> None:
        def invoke(arguments: list[str]) -> dict:
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = feature_main(arguments, suite_root=ROOT)
            self.assertEqual(0, code, errors.getvalue())
            return json.loads(output.getvalue())

        plan = invoke(
            [
                "plan",
                "recipe-change",
                str(self.checkout),
                "--recipe-script",
                "groovy/postInit/chemistry/Probe.groovy",
                "--recipe-map",
                "batch_reactor",
                "--fluid-input",
                '{"name":"steam","amount":1000}',
                "--fluid-output",
                '{"name":"water","amount":1000}',
                "--duration",
                "100",
                "--voltage-tier",
                "LV",
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        self.assertEqual("required-not-observed", plan["runtime_observation"]["state"])
        self.assertEqual("ready", invoke(
            [
                "check",
                "recipe-change",
                plan["id"],
                "--state-root",
                str(self.state),
                "--json",
            ]
        )["state"])
        applied = invoke(
            [
                "apply",
                "recipe-change",
                plan["id"],
                "--consent",
                plan["id"],
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        self.assertEqual("applied", applied["state"])
        rollback = invoke(
            [
                "rollback",
                "recipe-change",
                plan["id"],
                applied["id"],
                "--state-root",
                str(self.state),
                "--json",
            ]
        )
        self.assertEqual("restored", rollback["state"])


if __name__ == "__main__":
    unittest.main()
