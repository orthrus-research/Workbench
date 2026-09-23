#!/usr/bin/env python3

"""Focused current-checkout tests for Supersymmetry recipe ADD authority."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

WORKBENCH_ROOT = Path(__file__).resolve().parents[5]
for source in (
    WORKBENCH_ROOT / "modules/blueprints/src",
    WORKBENCH_ROOT / "modules/project-intelligence/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

MODULE_PATH = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/recipe_change.py"
)
SPEC = importlib.util.spec_from_file_location(
    "workbench_supersymmetry_recipe_change_test", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
recipe_change = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe_change)


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

OWNER = "groovy/postInit/chemistry/Probe.groovy"
MAPS = "groovy/prePostInit/Recipemaps.groovy"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def _checkout(parent: Path) -> Path:
    root = parent / "supersymmetry"
    root.mkdir()
    (root / "config").mkdir()
    (root / "mods").mkdir()
    _write(root, "pack.toml", PACK_TOML)
    _write(root, "index.toml", "")
    _write(
        root,
        MAPS,
        "package prePostInit\n\n"
        "class Recipemaps {\n"
        "    static final def MIXER = recipemap('mixer')\n"
        "    static final def BR = recipemap('batch_reactor')\n"
        "}\n",
    )
    _write(
        root,
        OWNER,
        "import static prePostInit.Recipemaps.*\n"
        "import static gregtech.api.GTValues.*\n\n"
        "MIXER.recipeBuilder()\n"
        "    .fluidInputs(fluid('water') * 1000)\n"
        "    .fluidOutputs(fluid('distilled_water') * 1000)\n"
        "    .duration(20)\n"
        "    .EUt(VA[LV])\n"
        "    .buildAndRegister()\n",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@workbench.invalid")
    _git(root, "config", "user.name", "Workbench Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


class SupersymmetryRecipeChangeTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.state = self.root / "state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self) -> dict[str, object]:
        return recipe_change.build_recipe_add_plan(
            WORKBENCH_ROOT,
            self.checkout,
            recipe_script=OWNER,
            recipe_map="batch_reactor",
            item_inputs=[
                {"kind": "ore", "name": "dustSulfur", "amount": 2},
                {
                    "kind": "metaitem",
                    "name": "dustSodiumHydroxide",
                    "amount": 1,
                },
            ],
            fluid_inputs=[{"name": "water", "amount": 1000}],
            item_outputs=[
                {
                    "kind": "item",
                    "name": "minecraft:clay_ball",
                    "metadata": 0,
                    "amount": 4,
                }
            ],
            duration=100,
            voltage_tier="LV",
        )

    def _append_owner(self, block: str) -> None:
        path = self.checkout / OWNER
        path.write_text(
            path.read_text(encoding="utf-8").rstrip() + "\n\n" + block.strip() + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _reordered_duplicate_block(*, duration: str = "100") -> str:
        return f"""
BR.recipeBuilder()
    .EUt(VA[LV])
    .outputs(item("minecraft:clay_ball", 0) * 4)
    .inputs(metaitem("dustSodiumHydroxide"))
    .duration({duration})
    .fluidInputs(fluid("water") * 1000)
    .inputs(ore("dustSulfur") * 2)
    .buildAndRegister()
"""

    def test_live_options_and_plan_bind_profile_map_owner_and_exact_bytes(self) -> None:
        before = (self.checkout / OWNER).read_bytes()
        options = recipe_change.recipe_change_options(
            WORKBENCH_ROOT, self.checkout
        )
        self.assertEqual(
            {"supported": ["add"], "unsupported": ["replace", "remove"]},
            options["mutation_modes"],
        )
        self.assertEqual(
            [
                {"alias": "BR", "registry_name": "batch_reactor"},
                {"alias": "MIXER", "registry_name": "mixer"},
            ],
            options["recipe_maps"],
        )

        plan = self._plan()
        self.assertEqual(plan, recipe_change.validate_recipe_change_plan(plan))
        self.assertEqual("add", plan["request"]["mutation"])
        self.assertEqual("BR", plan["request"]["recipe_map"])
        self.assertEqual(self.checkout.as_uri(), plan["workspace_uri"])
        self.assertEqual(OWNER, plan["authority_bindings"]["owner"]["path"])
        self.assertEqual(
            plan["operations"][0]["before_sha256"],
            plan["authority_bindings"]["owner"]["sha256"],
        )
        self.assertEqual(
            plan["dependencies"][0],
            plan["authority_bindings"]["recipe_map"]["source"],
        )
        self.assertEqual(MAPS, plan["dependencies"][0]["path"])
        self.assertEqual(
            before,
            base64.b64decode(plan["operations"][0]["before_base64"]),
        )
        after = base64.b64decode(plan["operations"][0]["after_base64"])
        self.assertIn(
            b"BR.recipeBuilder()\n"
            b"    .inputs(ore('dustSulfur') * 2)\n"
            b"    .inputs(metaitem('dustSodiumHydroxide'))\n"
            b"    .fluidInputs(fluid('water') * 1000)\n"
            b"    .outputs(item('minecraft:clay_ball', 0) * 4)\n",
            after,
        )
        self.assertEqual(
            "ready",
            recipe_change.verify_recipe_change_plan(
                WORKBENCH_ROOT, plan
            )["state"],
        )

    def test_exact_rendered_duplicate_is_rejected_before_plan_return(self) -> None:
        first = self._plan()
        (self.checkout / OWNER).write_bytes(
            base64.b64decode(first["operations"][0]["after_base64"])
        )

        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "semantic duplicate of an existing recipe",
        ):
            self._plan()

    def test_reordered_double_quoted_duplicate_is_rejected_semantically(self) -> None:
        self._append_owner(self._reordered_duplicate_block())

        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "semantic duplicate of an existing recipe",
        ):
            self._plan()

    def test_same_inputs_with_distinct_duration_are_rejected_as_collision(self) -> None:
        self._append_owner(self._reordered_duplicate_block(duration="101"))

        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "input collision with an existing recipe",
        ):
            self._plan()

    def test_dynamic_same_signature_candidate_fails_closed(self) -> None:
        self._append_owner(
            self._reordered_duplicate_block().replace(
                'ore("dustSulfur") * 2',
                'ore("dustSulfur") * sulfurAmount',
            )
        )

        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "cannot be proven distinct from one",
        ):
            self._plan()

    def test_legacy_duplicate_plan_is_rejected_before_apply_mutation(self) -> None:
        self._append_owner(self._reordered_duplicate_block())
        guard = recipe_change._reject_semantic_recipe_duplicate
        recipe_change._reject_semantic_recipe_duplicate = lambda *_args, **_kwargs: None
        try:
            legacy_plan = self._plan()
        finally:
            recipe_change._reject_semantic_recipe_duplicate = guard
        before = (self.checkout / OWNER).read_bytes()

        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "plan is stale: machine-recipe ADD is a semantic duplicate",
        ):
            recipe_change.apply_recipe_change_plan(
                WORKBENCH_ROOT,
                legacy_plan,
                self.state,
                consent_plan_id=legacy_plan["id"],
            )
        self.assertEqual(before, (self.checkout / OWNER).read_bytes())
        self.assertFalse(self.state.exists())

    def test_replace_and_remove_are_explicitly_unsupported(self) -> None:
        for mutation in ("replace", "remove"):
            with (
                self.subTest(mutation=mutation),
                self.assertRaisesRegex(
                    recipe_change.RecipeChangeError,
                    "supports add only",
                ),
            ):
                recipe_change.build_recipe_change_plan(
                    WORKBENCH_ROOT,
                    self.checkout,
                    mutation=mutation,
                    recipe_script=OWNER,
                    recipe_map="BR",
                    fluid_inputs=[{"name": "water", "amount": 1}],
                    fluid_outputs=[{"name": "steam", "amount": 1}],
                    duration=1,
                    voltage_tier="LV",
                )

    def test_resealed_non_renderer_after_payload_is_rejected(self) -> None:
        plan = self._plan()
        forged = json.loads(json.dumps(plan))
        operation = forged["operations"][0]
        before = base64.b64decode(operation["before_base64"])
        after = before.rstrip() + b"\n\n// syntactically valid arbitrary edit\n"
        operation["after_base64"] = base64.b64encode(after).decode("ascii")
        operation["after_sha256"] = recipe_change.sha256(after).hexdigest()
        operation["after_size"] = len(after)
        operation["diff"] = recipe_change._unified_diff(
            before, after, operation["path"]
        )
        forged["review"] = recipe_change._review(operation)
        forged.pop("id")
        forged = recipe_change.application_transaction.seal(
            recipe_change.PLAN_KIND, forged
        )

        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "differs from the exact Blueprints renderer result",
        ):
            recipe_change.validate_recipe_change_plan(forged)

    def test_observation_contract_requires_exact_presence_and_no_collision(self) -> None:
        plan = self._plan()
        client = recipe_change.build_recipe_observation_contract(
            plan, physical_side="client"
        )
        server = recipe_change.build_recipe_observation_contract(
            plan, physical_side="dedicated-server"
        )
        self.assertNotEqual(client["id"], server["id"])
        self.assertEqual(
            {
                "competing_input_match_count": 0,
                "declared_identities_resolve": True,
                "exact_recipe_match_count": 1,
                "groovy_origin": True,
                "lookup_resolves_exact_recipe": True,
                "recipe_map_alias_binding": True,
                "recipe_signature": True,
                "source_owner_compiled": True,
                "unresolved_input_expansion_count": 0,
            },
            client["required_checks"],
        )
        self.assertEqual(
            client,
            recipe_change.validate_recipe_observation_contract(client, plan),
        )
        tampered = json.loads(json.dumps(client))
        tampered["required_checks"]["exact_recipe_match_count"] = 2
        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError, "differs from its source plan"
        ):
            recipe_change.validate_recipe_observation_contract(tampered, plan)

    def test_dependency_race_rejects_before_owner_mutation(self) -> None:
        plan = self._plan()
        before = (self.checkout / OWNER).read_bytes()

        def change_alias_source(_workspace: Path) -> None:
            path = self.checkout / MAPS
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "recipemap('batch_reactor')",
                    "recipemap('changed_batch_reactor')",
                ),
                encoding="utf-8",
            )

        receipt = recipe_change.apply_recipe_change_plan(
            WORKBENCH_ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            after_preflight=change_alias_source,
        )
        self.assertEqual("rejected", receipt["state"])
        self.assertEqual("BLUEPRINTS_M2_STALE_PLAN", receipt["diagnostic_code"])
        self.assertEqual(before, (self.checkout / OWNER).read_bytes())

    def test_apply_partial_failure_rollback_and_later_edit_preservation(self) -> None:
        plan = self._plan()
        before = (self.checkout / OWNER).read_bytes()
        rejected = recipe_change.apply_recipe_change_plan(
            WORKBENCH_ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            fail_after_ordinal=0,
        )
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual(
            "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
            rejected["diagnostic_code"],
        )
        self.assertEqual(before, (self.checkout / OWNER).read_bytes())

        applied = recipe_change.apply_recipe_change_plan(
            WORKBENCH_ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
        )
        self.assertEqual("applied", applied["state"])
        exact_after = (self.checkout / OWNER).read_bytes()
        (self.checkout / OWNER).write_bytes(exact_after + b"// later edit\n")
        preserved = recipe_change.rollback_recipe_change(
            plan,
            self.state,
            application_receipt=applied,
        )
        self.assertEqual("rejected", preserved["state"])
        self.assertEqual(
            "BLUEPRINTS_M2_LATER_EDIT_PRESERVED",
            preserved["diagnostic_code"],
        )
        self.assertTrue((self.checkout / OWNER).read_bytes().endswith(b"// later edit\n"))

        (self.checkout / OWNER).write_bytes(exact_after)
        restored = recipe_change.rollback_recipe_change(
            plan,
            self.state,
            application_receipt=applied,
        )
        self.assertEqual("restored", restored["state"])
        self.assertEqual(before, (self.checkout / OWNER).read_bytes())

    def test_process_death_after_replace_is_recovered_then_rollbackable(self) -> None:
        plan = self._plan()
        before = (self.checkout / OWNER).read_bytes()
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        script = r"""
import importlib.util
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
for source in (
    root / 'api/src',
    root / 'core/src',
    root / 'modules/blueprints/src',
    root / 'modules/project-intelligence/src',
):
    sys.path.insert(0, str(source))
from workbench_core.host_services import install_local_host_services
install_local_host_services()
from workbench_core.development import enable_source_checkout
enable_source_checkout(root)
module_path = root / 'profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/recipe_change.py'
spec = importlib.util.spec_from_file_location('recipe_change_crash_test', module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

plan = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
state = Path(sys.argv[3])
target_path = Path(sys.argv[4]).resolve()
real_replace = module.application_transaction.os.replace

def crash_after_target(source, target):
    real_replace(source, target)
    if Path(target).resolve() == target_path:
        os._exit(91)

module.application_transaction.os.replace = crash_after_target
module.apply_recipe_change_plan(
    root,
    plan,
    state,
    consent_plan_id=plan['id'],
)
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(WORKBENCH_ROOT),
                str(plan_path),
                str(self.state),
                str(self.checkout / OWNER),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(91, completed.returncode, completed.stderr)
        self.assertTrue((self.state / "active-transaction.json").is_file())

        recovered = recipe_change.recover_recipe_change(plan, self.state)
        self.assertEqual("applied", recovered["state"])
        self.assertEqual([0], recovered["attempted_ordinals"])
        applied = recovered["application_receipt"]
        self.assertIsNotNone(applied)
        restored = recipe_change.rollback_recipe_change(
            plan,
            self.state,
            application_receipt=applied,
        )
        self.assertEqual("restored", restored["state"])
        self.assertEqual(before, (self.checkout / OWNER).read_bytes())

    def test_applied_recovery_rejects_nested_rejected_application(self) -> None:
        plan = self._plan()

        def change_alias_source(_workspace: Path) -> None:
            path = self.checkout / MAPS
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "recipemap('batch_reactor')",
                    "recipemap('changed_batch_reactor')",
                ),
                encoding="utf-8",
            )

        rejected = recipe_change.apply_recipe_change_plan(
            WORKBENCH_ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            after_preflight=change_alias_source,
        )
        self.assertEqual("rejected", rejected["state"])
        body = {
            "application_receipt": rejected,
            "attempted_ordinals": [0],
            "diagnostic_code": None,
            "format": recipe_change.RECOVERY_FORMAT,
            "kind": recipe_change.RECOVERY_KIND,
            "plan_id": plan["id"],
            "schema_version": 1,
            "state": "applied",
            "workspace_mutated": False,
        }
        forged = recipe_change.application_transaction.seal(
            recipe_change.RECOVERY_KIND, body
        )
        with self.assertRaisesRegex(
            recipe_change.RecipeChangeError,
            "requires an applied application receipt",
        ):
            recipe_change.validate_recipe_change_recovery(forged, plan)


if __name__ == "__main__":
    unittest.main()
