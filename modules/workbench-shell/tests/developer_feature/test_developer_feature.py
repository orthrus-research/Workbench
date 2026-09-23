"""Fast current-checkout material, fluid, and recipe transaction tests."""

from __future__ import annotations

import base64
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[4]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.developer_feature import (  # noqa: E402
    DeveloperFeatureError,
    apply_material_fluid_recipe_plan,
    build_material_fluid_recipe_plan,
    material_fluid_recipe_options,
    recover_material_fluid_recipe,
    retain_feature_record,
    rollback_material_fluid_recipe,
    validate_material_fluid_recipe_plan,
    validate_material_fluid_recipe_receipt,
    validate_material_fluid_recipe_recovery,
    verify_material_fluid_recipe_plan,
    workspace_transaction_lock_path,
)
from workbench_shell import developer_feature  # noqa: E402
from workbench_blueprints import application_transaction  # noqa: E402


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

TARGET_PATHS = (
    "groovy/material/PetrochemistryMaterials.groovy",
    "groovy/material/SuSyMaterials.groovy",
    "groovy/postInit/chemistry/Probe.groovy",
    "resources/langfiles/lang/en_us.lang",
)

DEPENDENCY_PATHS = (
    "groovy/preInit/MaterialChanges.groovy",
    "groovy/prePostInit/Recipemaps.groovy",
)


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


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _checkout(parent: Path) -> Path:
    root = parent / "supersymmetry"
    root.mkdir()
    (root / "config").mkdir()
    (root / "mods").mkdir()
    _write(root, "pack.toml", PACK_TOML)
    _write(root, "index.toml", "")
    _write(
        root,
        "groovy/preInit/MaterialChanges.groovy",
        "package preInit\n\n"
        "class MaterialChanges {\n"
        "    static void init() {\n"
        "        SuSyMaterials.init()\n"
        "    }\n"
        "}\n",
    )
    _write(
        root,
        "groovy/material/SuSyMaterials.groovy",
        "package material\n\n"
        "class SuSyMaterials {\n\n"
        "    // Petrochem Materials\n\n"
        "    public static Material ExistingFluid\n\n"
        "    static void init() {\n"
        "        PetrochemistryMaterials.register()\n"
        "    }\n\n"
        "    // First Degree Materials A\n"
        "}\n",
    )
    _write(
        root,
        "groovy/material/PetrochemistryMaterials.groovy",
        "package material\n\n"
        "import static material.SuSyMaterials.*\n\n"
        "class PetrochemistryMaterials {\n\n"
        "    static void register() {\n\n"
        "        ExistingFluid = new Material.Builder(20000, "
        "SuSyUtility.susyId('existing_fluid'))\n"
        "                .liquid()\n"
        "                .color(0x111111)\n"
        "                .flags(FLAMMABLE)\n"
        "                .build()\n"
        "    }\n"
        "}",
    )
    _write(
        root,
        "resources/langfiles/lang/en_us.lang",
        "# Fluids\n\n"
        "susy.material.existing_fluid=Existing Fluid\n"
        "\n# Thermodynamics\n",
    )
    _write(
        root,
        "groovy/prePostInit/Recipemaps.groovy",
        "package prePostInit\n\n"
        "class Recipemaps {\n"
        "    static final def MIXER = recipemap('mixer')\n"
        "    static final def BR = recipemap('batch_reactor')\n"
        "}\n",
    )
    _write(
        root,
        "groovy/postInit/chemistry/Probe.groovy",
        "import static prePostInit.Recipemaps.*\n"
        "import static gregtech.api.GTValues.*\n\n"
        "MIXER.recipeBuilder()\n"
        "    .fluidInputs(fluid('water') * 1000)\n"
        "    .fluidOutputs(fluid('distilled_water') * 1000)\n"
        "    .duration(20)\n"
        "    .EUt(VA[LV])\n"
        "    .buildAndRegister()",
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Workbench Developer Feature Test")
    _git(root, "config", "user.email", "workbench@example.invalid")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "synthetic Supersymmetry baseline")
    return root


def _bytes(root: Path) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in TARGET_PATHS}


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class DeveloperFeatureTests(unittest.TestCase):

    def setUp(self) -> None:
        temporary_parent = ROOT / ".workbench/test-tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_parent)
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.state = self.root / "transaction-state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self, **overrides) -> dict:
        request = {
            "name": "Thermal Solvent",
            "color": "#Aa44Cc",
            "translation": "Thermal Solvent Localized",
            "symbol": "ThermalSolventX",
            "recipe_script": "groovy/postInit/chemistry/Probe.groovy",
            "recipe_map": "batch_reactor",
            "input_fluid": "steam",
            "input_amount": 750,
            "output_amount": 500,
            "duration": 320,
            "voltage_tier": "MV",
        }
        request.update(overrides)
        return build_material_fluid_recipe_plan(ROOT, self.checkout, **request)

    def _crash_apply(
        self,
        checkout: Path,
        state: Path,
        plan: dict,
        lock: Path,
        *,
        target_replace_count: int,
    ) -> None:
        plan_path = state.parent / "crash-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        script = r"""
import json
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
for relative in ("api/src", "core/src"):
    sys.path.insert(0, str(root / relative))
for source in sorted((root / "modules").glob("*/src")):
    sys.path.insert(0, str(source))

from workbench_core.host_services import install_local_host_services
install_local_host_services()

from workbench_shell.developer_feature import apply_material_fluid_recipe_plan
from workbench_blueprints import application_transaction

checkout = Path(sys.argv[2])
state = Path(sys.argv[3])
plan = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
lock = Path(sys.argv[5])
crash_count = int(sys.argv[6])
targets = {str((checkout / row["path"]).resolve()) for row in plan["operations"]}
real_replace = application_transaction.os.replace
target_calls = 0

def crash_after_target(source, target):
    global target_calls
    real_replace(source, target)
    if str(Path(target).resolve()) in targets:
        target_calls += 1
        if target_calls == crash_count:
            os._exit(91)

application_transaction.os.replace = crash_after_target
apply_material_fluid_recipe_plan(
    root,
    plan,
    state,
    consent_plan_id=plan["id"],
    transaction_lock=lock,
)
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(ROOT),
                str(checkout),
                str(state),
                str(plan_path),
                str(lock),
                str(target_replace_count),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(91, completed.returncode, completed.stderr)

    def test_options_and_verified_plan_cover_four_existing_owner_files(self) -> None:
        before = _bytes(self.checkout)
        options = material_fluid_recipe_options(ROOT, self.checkout)
        self.assertEqual("experimental", options["state"])
        self.assertEqual(
            [
                {"alias": "BR", "registry_name": "batch_reactor"},
                {"alias": "MIXER", "registry_name": "mixer"},
            ],
            options["recipe_maps"],
        )
        self.assertEqual(
            ["groovy/postInit/chemistry/Probe.groovy"], options["scripts"]
        )

        plan = self._plan()
        self.assertEqual(plan, validate_material_fluid_recipe_plan(plan))
        self.assertEqual(
            {
                "localization",
                "machine-recipe",
                "material-declaration",
                "material-fluid-registration",
            },
            {operation["role"] for operation in plan["operations"]},
        )
        self.assertEqual(set(TARGET_PATHS), set(plan["review"]["changed_files"]))
        self.assertEqual(4, len(plan["operations"]))
        review_lines = plan["review"]["unified_diff"].splitlines()
        self.assertEqual(
            4,
            sum(line.startswith("--- a/") for line in review_lines),
            "every no-final-newline owner still needs a separate diff header",
        )
        self.assertNotIn("-        .buildAndRegister()", review_lines)
        for operation in plan["operations"]:
            self.assertEqual("update", operation["operation"])
            self.assertEqual("approved-update", operation["outcome"])
            self.assertTrue((self.checkout / operation["path"]).is_file())
            self.assertEqual(
                before[operation["path"]],
                base64.b64decode(operation["before_base64"], validate=True),
            )
        self.assertEqual(before, _bytes(self.checkout), "planning mutated the checkout")
        self.assertEqual(
            {
                "color": "0xaa44cc",
                "duration": 320,
                "input_amount": 750,
                "input_fluid": "steam",
                "material_id": 20001,
                "name": "Thermal Solvent",
                "output_amount": 500,
                "recipe_map": "BR",
                "recipe_map_registry_name": "batch_reactor",
                "recipe_script": "groovy/postInit/chemistry/Probe.groovy",
                "registry_name": "thermal_solvent",
                "symbol": "ThermalSolventX",
                "translation": "Thermal Solvent Localized",
                "voltage_tier": "MV",
            },
            plan["request"],
        )
        after_text = {
            operation["path"]: base64.b64decode(
                operation["after_base64"], validate=True
            ).decode("utf-8")
            for operation in plan["operations"]
        }
        self.assertIn(
            "ThermalSolventX = new Material.Builder(20001, "
            "SuSyUtility.susyId('thermal_solvent'))",
            after_text["groovy/material/PetrochemistryMaterials.groovy"],
        )
        self.assertIn(
            "public static Material ThermalSolventX",
            after_text["groovy/material/SuSyMaterials.groovy"],
        )
        self.assertIn(
            "susy.material.thermal_solvent=Thermal Solvent Localized",
            after_text["resources/langfiles/lang/en_us.lang"],
        )
        recipe = after_text["groovy/postInit/chemistry/Probe.groovy"]
        self.assertIn("BR.recipeBuilder()", recipe)
        self.assertIn(".fluidInputs(fluid('steam') * 750)", recipe)
        self.assertIn(".fluidOutputs(fluid('thermal_solvent') * 500)", recipe)
        self.assertIn(".duration(320)", recipe)
        self.assertIn(".EUt(VA[MV])", recipe)
        self.assertEqual(
            {
                "format": "workbench-developer-feature-verification-v1",
                "schema_version": 1,
                "plan_id": plan["id"],
                "state": "ready",
                "reason": None,
            },
            verify_material_fluid_recipe_plan(ROOT, plan),
        )

        for function in (
            build_material_fluid_recipe_plan,
            apply_material_fluid_recipe_plan,
            rollback_material_fluid_recipe,
        ):
            parameters = " ".join(inspect.signature(function).parameters).lower()
            for forbidden in ("admission", "freeze", "proof"):
                self.assertNotIn(forbidden, parameters)
        for text in _strings({"options": options, "plan": plan}):
            lowered = text.lower()
            for forbidden in ("admission", "freeze", "proof"):
                self.assertNotIn(forbidden, lowered)
        self.assertNotIn("authority_bindings", plan)

    def test_plan_preserves_a_uniform_crlf_checkout(self) -> None:
        for relative in (*TARGET_PATHS, *DEPENDENCY_PATHS):
            path = self.checkout / relative
            content = path.read_bytes()
            self.assertNotIn(b"\r", content)
            path.write_bytes(content.replace(b"\n", b"\r\n"))
        _git(self.checkout, "config", "core.autocrlf", "false")
        _git(self.checkout, "add", "--all")
        _git(self.checkout, "commit", "--quiet", "-m", "use checkout CRLF bytes")

        before = {
            relative: (self.checkout / relative).read_bytes()
            for relative in (*TARGET_PATHS, *DEPENDENCY_PATHS)
        }
        plan = self._plan()

        self.assertEqual("experimental-ready", plan["state"])
        self.assertEqual(4, len(plan["operations"]))
        for operation in plan["operations"]:
            after = base64.b64decode(operation["after_base64"], validate=True)
            self.assertIn(b"\r\n", after)
            self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertEqual(
            before,
            {
                relative: (self.checkout / relative).read_bytes()
                for relative in (*TARGET_PATHS, *DEPENDENCY_PATHS)
            },
        )

    def test_exact_consent_apply_and_rollback_restore_exact_bytes(self) -> None:
        plan = self._plan()
        mode_target = self.checkout / TARGET_PATHS[0]
        mode_target.chmod(0o750)
        before = _bytes(self.checkout)
        with self.assertRaisesRegex(DeveloperFeatureError, "exact reviewed plan ID"):
            apply_material_fluid_recipe_plan(
                ROOT,
                plan,
                self.state,
                consent_plan_id="workbench-developer-material-fluid-recipe-plan:sha256:"
                + "0" * 64,
            )
        self.assertEqual(before, _bytes(self.checkout))

        applied = apply_material_fluid_recipe_plan(
            ROOT, plan, self.state, consent_plan_id=plan["id"]
        )
        self.assertEqual("applied", applied["state"])
        self.assertEqual(
            "applied-experimental-local-edit", applied["mutation_state"]
        )
        self.assertEqual(0o750, mode_target.stat().st_mode & 0o777)
        for operation in plan["operations"]:
            self.assertEqual(
                base64.b64decode(operation["after_base64"], validate=True),
                (self.checkout / operation["path"]).read_bytes(),
            )

        restored = rollback_material_fluid_recipe(
            plan, self.state, application_receipt=applied
        )
        self.assertEqual("restored", restored["state"])
        self.assertTrue(restored["workspace_mutated"])
        self.assertEqual(before, _bytes(self.checkout))
        self.assertEqual(0o750, mode_target.stat().st_mode & 0o777)
        mode_target.chmod(0o644)
        self.assertEqual("", _git(self.checkout, "status", "--porcelain=v1"))

    def test_unrelated_post_plan_dirty_edit_is_allowed_and_preserved(self) -> None:
        plan = self._plan()
        before = _bytes(self.checkout)
        unrelated = self.checkout / "developer-notes.txt"
        unrelated.write_text("keep this unrelated edit\n", encoding="utf-8")

        applied = apply_material_fluid_recipe_plan(
            ROOT, plan, self.state, consent_plan_id=plan["id"]
        )
        self.assertEqual("applied", applied["state"])
        self.assertEqual("keep this unrelated edit\n", unrelated.read_text())
        restored = rollback_material_fluid_recipe(
            plan, self.state, application_receipt=applied
        )
        self.assertEqual("restored", restored["state"])
        self.assertEqual(before, _bytes(self.checkout))
        self.assertEqual("keep this unrelated edit\n", unrelated.read_text())

    def test_untracked_material_builder_participates_in_allocation(self) -> None:
        _write(
            self.checkout,
            "groovy/material/DeveloperScratch.groovy",
            "class DeveloperScratch {\n"
            "    static final def Scratch = new Material.Builder(20001, "
            "SuSyUtility.susyId('developer_scratch')).build()\n"
            "}\n",
        )

        plan = self._plan()

        self.assertEqual(20002, plan["request"]["material_id"])

    def test_recipe_map_dependency_race_rejects_before_any_target_mutation(self) -> None:
        plan = self._plan()
        before = _bytes(self.checkout)

        def change_recipe_map(_root: Path) -> None:
            recipe_maps = self.checkout / "groovy/prePostInit/Recipemaps.groovy"
            recipe_maps.write_text(
                recipe_maps.read_text(encoding="utf-8").replace(
                    "recipemap('batch_reactor')",
                    "recipemap('wrong_map')",
                ),
                encoding="utf-8",
            )

        rejected = apply_material_fluid_recipe_plan(
            ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            after_preflight=change_recipe_map,
        )

        self.assertEqual("rejected", rejected["state"])
        self.assertEqual("BLUEPRINTS_M2_STALE_PLAN", rejected["diagnostic_code"])
        self.assertEqual(before, _bytes(self.checkout))

    def test_different_plans_for_one_workspace_share_one_writer_lock(self) -> None:
        first = self._plan()
        second = self._plan(
            name="Route Solvent",
            translation="Route Solvent",
            symbol="RouteSolventX",
        )
        shared_lock = workspace_transaction_lock_path(
            first,
            lock_root=self.root / "workspace-locks",
        )
        nested: list[dict] = []

        def attempt_second(_root: Path) -> None:
            nested.append(
                apply_material_fluid_recipe_plan(
                    ROOT,
                    second,
                    self.root / "second-transaction",
                    consent_plan_id=second["id"],
                    transaction_lock=shared_lock,
                )
            )

        applied = apply_material_fluid_recipe_plan(
            ROOT,
            first,
            self.state,
            consent_plan_id=first["id"],
            after_preflight=attempt_second,
            transaction_lock=shared_lock,
        )

        self.assertEqual("applied", applied["state"])
        self.assertEqual(1, len(nested))
        self.assertEqual("rejected", nested[0]["state"])
        self.assertEqual(
            "BLUEPRINTS_M2_TRANSACTION_LOCKED",
            nested[0]["diagnostic_code"],
        )

    def test_transaction_does_not_remove_a_substituted_lock(self) -> None:
        plan = self._plan()
        lock = workspace_transaction_lock_path(
            plan,
            lock_root=self.root / "workspace-locks",
        )
        replacement = b'{"token":"replacement-writer"}'

        def substitute_lock(_root: Path) -> None:
            lock.unlink()
            lock.write_bytes(replacement)

        applied = apply_material_fluid_recipe_plan(
            ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            after_preflight=substitute_lock,
            transaction_lock=lock,
        )

        self.assertEqual("applied", applied["state"])
        self.assertEqual(replacement, lock.read_bytes())
        lock.unlink()

    def test_retained_records_reject_symlinked_collection(self) -> None:
        plan = self._plan()
        state = self.root / "record-state"
        outside = self.root / "outside"
        state.mkdir()
        outside.mkdir()
        (state / "plans").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(DeveloperFeatureError, "symbolic link"):
            retain_feature_record(state, "plans", plan)
        self.assertEqual([], list(outside.iterdir()))

        forged = dict(plan)
        forged["id"] = developer_feature.PLAN_KIND + ":sha256:" + "a" * 64
        with self.assertRaisesRegex(DeveloperFeatureError, "does not match"):
            retain_feature_record(self.root / "forged-state", "plans", forged)

    def test_rejected_receipt_schema_does_not_accept_resealed_extra_fields(self) -> None:
        plan = self._plan()
        body = {
            "authority_boundary": plan["authority_boundary"],
            "diagnostic_code": "BLUEPRINTS_M2_STALE_PLAN",
            "format": developer_feature.RECEIPT_FORMAT,
            "kind": developer_feature.RECEIPT_KIND,
            "mutation_state": "not-started",
            "plan_id": plan["id"],
            "rollback": "not-needed",
            "schema_version": 1,
            "state": "rejected",
            "workspace_mutated": True,
        }
        forged = developer_feature._seal(developer_feature.RECEIPT_KIND, body)

        with self.assertRaisesRegex(DeveloperFeatureError, "fields changed"):
            validate_material_fluid_recipe_receipt(forged, plan)

    def test_plan_validation_is_pure_and_live_inspection_failure_is_stale(self) -> None:
        plan = self._plan()
        (self.checkout / "pack.toml").write_text("not valid toml = [", encoding="utf-8")

        verification = verify_material_fluid_recipe_plan(ROOT, plan)

        self.assertEqual("stale", verification["state"])
        self.assertIn("malformed", verification["reason"])
        shutil.rmtree(self.checkout)
        self.assertEqual(plan, validate_material_fluid_recipe_plan(plan))
        self.assertEqual(
            "stale",
            verify_material_fluid_recipe_plan(ROOT, plan)["state"],
        )

    def test_recipe_cannot_consume_the_material_fluid_it_creates(self) -> None:
        with self.assertRaisesRegex(DeveloperFeatureError, "cannot be"):
            self._plan(
                name="Steam",
                translation="Steam",
                symbol="SteamX",
                input_fluid="steam",
            )

    def test_double_quoted_recipe_map_binding_is_valid_current_evidence(self) -> None:
        recipe_maps = self.checkout / "groovy/prePostInit/Recipemaps.groovy"
        recipe_maps.write_text(
            recipe_maps.read_text(encoding="utf-8").replace(
                "recipemap('batch_reactor')",
                'recipemap("batch_reactor")',
            ),
            encoding="utf-8",
        )

        plan = self._plan()

        self.assertEqual("BR", plan["request"]["recipe_map"])
        self.assertEqual(
            "batch_reactor",
            plan["request"]["recipe_map_registry_name"],
        )

    def test_lifecycle_dependency_change_makes_plan_stale_before_apply(self) -> None:
        plan = self._plan()
        lifecycle = self.checkout / "groovy/preInit/MaterialChanges.groovy"
        lifecycle.write_text(
            lifecycle.read_text(encoding="utf-8").replace(
                "SuSyMaterials.init()",
                "if (false) SuSyMaterials.init()",
            ),
            encoding="utf-8",
        )

        self.assertEqual(
            "stale",
            verify_material_fluid_recipe_plan(ROOT, plan)["state"],
        )
        observed = _bytes(self.checkout)
        with self.assertRaisesRegex(DeveloperFeatureError, "stale"):
            apply_material_fluid_recipe_plan(
                ROOT,
                plan,
                self.state,
                consent_plan_id=plan["id"],
            )
        self.assertEqual(observed, _bytes(self.checkout))

    def test_resealed_unknown_field_and_boolean_ordinals_are_rejected(self) -> None:
        plan = self._plan()
        arbitrary_metadata = json.loads(json.dumps(plan))
        arbitrary_metadata["evidence"] = {"arbitrary": True}
        arbitrary_metadata.pop("id")
        arbitrary_metadata = developer_feature._seal(
            developer_feature.PLAN_KIND,
            arbitrary_metadata,
        )
        with self.assertRaisesRegex(DeveloperFeatureError, "identity"):
            validate_material_fluid_recipe_plan(arbitrary_metadata)

        boolean_ordinals = json.loads(json.dumps(plan))
        boolean_ordinals["operations"][0]["ordinal"] = False
        boolean_ordinals["operations"][1]["ordinal"] = True
        boolean_ordinals.pop("id")
        boolean_ordinals = developer_feature._seal(
            developer_feature.PLAN_KIND,
            boolean_ordinals,
        )
        with self.assertRaisesRegex(DeveloperFeatureError, "order"):
            validate_material_fluid_recipe_plan(boolean_ordinals)

    def test_resealed_after_bytes_must_reproduce_blueprints_request(self) -> None:
        plan = self._plan()
        forged = json.loads(json.dumps(plan))
        operation = next(
            row for row in forged["operations"] if row["role"] == "localization"
        )
        after = base64.b64decode(operation["after_base64"], validate=True)
        after = after.replace(
            b"Thermal Solvent Localized",
            b"Arbitrary Rollback Payload",
        )
        operation["after_base64"] = base64.b64encode(after).decode("ascii")
        operation["after_sha256"] = developer_feature.sha256(after).hexdigest()
        operation["after_size"] = len(after)
        before = base64.b64decode(operation["before_base64"], validate=True)
        operation["diff"] = developer_feature._unified_diff(
            before,
            after,
            operation["path"],
        )
        forged["review"] = developer_feature._review(forged["operations"])
        forged.pop("id")
        forged = developer_feature._seal(developer_feature.PLAN_KIND, forged)

        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "differs from its Blueprints construction",
        ):
            validate_material_fluid_recipe_plan(forged)

    def test_applied_recovery_requires_applied_nested_receipt(self) -> None:
        plan = self._plan()
        rejected_body = {
            "authority_boundary": plan["authority_boundary"],
            "diagnostic_code": "BLUEPRINTS_M2_STALE_PLAN",
            "format": developer_feature.RECEIPT_FORMAT,
            "kind": developer_feature.RECEIPT_KIND,
            "mutation_state": "not-started",
            "plan_id": plan["id"],
            "rollback": "not-needed",
            "schema_version": 1,
            "state": "rejected",
        }
        rejected = developer_feature._seal(
            developer_feature.RECEIPT_KIND,
            rejected_body,
        )
        recovery_body = {
            "application_receipt": rejected,
            "attempted_ordinals": list(range(len(plan["operations"]))),
            "diagnostic_code": None,
            "format": developer_feature.RECOVERY_FORMAT,
            "kind": developer_feature.RECOVERY_KIND,
            "plan_id": plan["id"],
            "schema_version": 1,
            "state": "applied",
            "workspace_mutated": False,
        }
        recovery = developer_feature._seal(
            developer_feature.RECOVERY_KIND,
            recovery_body,
        )

        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "requires a successful application receipt",
        ):
            validate_material_fluid_recipe_recovery(recovery, plan)

    def test_planned_target_staleness_rejects_without_mutating_any_target(self) -> None:
        plan = self._plan()
        stale = self.checkout / TARGET_PATHS[0]
        stale.write_text(
            stale.read_text(encoding="utf-8") + "// concurrent target edit\n",
            encoding="utf-8",
        )
        observed = _bytes(self.checkout)

        with self.assertRaisesRegex(DeveloperFeatureError, "stale"):
            apply_material_fluid_recipe_plan(
                ROOT, plan, self.state, consent_plan_id=plan["id"]
            )
        self.assertEqual(observed, _bytes(self.checkout))

    def test_injected_partial_failure_restores_all_four_targets(self) -> None:
        plan = self._plan()
        before = _bytes(self.checkout)
        rejected = apply_material_fluid_recipe_plan(
            ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            fail_after_ordinal=1,
        )
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual(
            "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
            rejected["diagnostic_code"],
        )
        self.assertEqual("restored", rejected["mutation_state"])
        self.assertEqual("succeeded", rejected["rollback"])
        self.assertEqual(before, _bytes(self.checkout))

    def test_receipt_commit_failure_rolls_back_before_return(self) -> None:
        plan = self._plan()
        before = _bytes(self.checkout)

        def fail_commit(_receipt) -> None:
            raise OSError("injected receipt-store failure")

        rejected = apply_material_fluid_recipe_plan(
            ROOT,
            plan,
            self.state,
            consent_plan_id=plan["id"],
            commit_receipt=fail_commit,
        )

        self.assertEqual("rejected", rejected["state"])
        self.assertEqual(
            "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
            rejected["diagnostic_code"],
        )
        self.assertEqual(before, _bytes(self.checkout))

    def test_interrupt_after_replace_restores_from_observed_target_bytes(self) -> None:
        plan = self._plan()
        before = _bytes(self.checkout)
        real_replace = application_transaction.os.replace
        calls = 0

        def interrupt_after_replace(source: object, target: object) -> None:
            nonlocal calls
            calls += 1
            real_replace(source, target)
            if calls == 1:
                raise KeyboardInterrupt

        with (
            patch.object(
                application_transaction.os,
                "replace",
                side_effect=interrupt_after_replace,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            apply_material_fluid_recipe_plan(
                ROOT,
                plan,
                self.state,
                consent_plan_id=plan["id"],
            )

        self.assertEqual(before, _bytes(self.checkout))
        self.assertFalse((self.state / "active-transaction.lock").exists())
        self.assertFalse((self.state / "active-transaction.json").exists())

    def test_process_death_after_every_target_replace_is_recoverable(self) -> None:
        for target_replace_count in range(1, len(TARGET_PATHS) + 1):
            with self.subTest(target_replace_count=target_replace_count):
                case = self.root / f"hard-exit-{target_replace_count}"
                case.mkdir()
                checkout = _checkout(case)
                state = case / "state"
                plan = build_material_fluid_recipe_plan(
                    ROOT,
                    checkout,
                    name="Thermal Solvent",
                    color="#Aa44Cc",
                    translation="Thermal Solvent Localized",
                    symbol="ThermalSolventX",
                    recipe_script="groovy/postInit/chemistry/Probe.groovy",
                    recipe_map="batch_reactor",
                    input_fluid="steam",
                    input_amount=750,
                    output_amount=500,
                    duration=320,
                    voltage_tier="MV",
                )
                before = _bytes(checkout)
                lock = workspace_transaction_lock_path(
                    plan,
                    lock_root=case / "workspace-locks",
                )
                self._crash_apply(
                    checkout,
                    state,
                    plan,
                    lock,
                    target_replace_count=target_replace_count,
                )
                self.assertTrue((state / "active-transaction.json").is_file())
                self.assertTrue(lock.is_file())

                recovered = recover_material_fluid_recipe(
                    plan,
                    state,
                    transaction_lock=lock,
                )
                if target_replace_count < len(TARGET_PATHS):
                    self.assertEqual("restored", recovered["state"])
                    self.assertEqual(before, _bytes(checkout))
                    self.assertIsNone(recovered["application_receipt"])
                else:
                    self.assertEqual("applied", recovered["state"])
                    applied = recovered["application_receipt"]
                    self.assertIsNotNone(applied)
                    restored = rollback_material_fluid_recipe(
                        plan,
                        state,
                        application_receipt=applied,
                        transaction_lock=lock,
                    )
                    self.assertEqual("restored", restored["state"])
                    self.assertEqual(before, _bytes(checkout))
                self.assertFalse((state / "active-transaction.json").exists())
                self.assertFalse((state / "prepared-receipt.json").exists())
                self.assertFalse(lock.exists())

    def test_recovery_preserves_a_later_edit_and_remains_retryable(self) -> None:
        plan = self._plan()
        lock = workspace_transaction_lock_path(
            plan,
            lock_root=self.root / "recovery-locks",
        )
        self._crash_apply(
            self.checkout,
            self.state,
            plan,
            lock,
            target_replace_count=1,
        )
        first = self.checkout / plan["operations"][0]["path"]
        later = b"developer edit after interrupted transaction\n"
        first.write_bytes(later)

        recovered = recover_material_fluid_recipe(
            plan,
            self.state,
            transaction_lock=lock,
        )
        self.assertEqual("review-required", recovered["state"])
        self.assertEqual(later, first.read_bytes())
        self.assertTrue((self.state / "active-transaction.json").is_file())
        self.assertTrue(lock.is_file())

        # The preserved marker is deliberately stale, not falsely owned by
        # this still-running test process, so another recovery can inspect it.
        second = recover_material_fluid_recipe(
            plan,
            self.state,
            transaction_lock=lock,
        )
        self.assertEqual("review-required", second["state"])
        self.assertEqual(later, first.read_bytes())

    def test_later_edit_blocks_rollback_and_preserves_all_applied_bytes(self) -> None:
        plan = self._plan()
        applied = apply_material_fluid_recipe_plan(
            ROOT, plan, self.state, consent_plan_id=plan["id"]
        )
        applied_bytes = _bytes(self.checkout)
        edited_path = self.checkout / TARGET_PATHS[0]
        later = b"later developer edit\n"
        edited_path.write_bytes(later)

        rejected = rollback_material_fluid_recipe(
            plan, self.state, application_receipt=applied
        )
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual(
            "BLUEPRINTS_M2_LATER_EDIT_PRESERVED", rejected["diagnostic_code"]
        )
        self.assertFalse(rejected["workspace_mutated"])
        self.assertEqual(later, edited_path.read_bytes())
        for relative in TARGET_PATHS[1:]:
            self.assertEqual(applied_bytes[relative], (self.checkout / relative).read_bytes())


if __name__ == "__main__":
    unittest.main()
