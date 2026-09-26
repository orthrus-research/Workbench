#!/usr/bin/env python3

"""End-to-end active-instance registration wizard tests."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse


MODULE_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell.active_instance import (  # noqa: E402
    ActiveInstanceError,
    initialize_active_instance,
    load_active_instance,
)
from workbench_shell.cli import main as cli_main  # noqa: E402
from workbench_core.configuration import load_workbench_configuration  # noqa: E402
from workbench_core.host_services import install_local_host_services  # noqa: E402
from workbench_core.modules import InstalledModule, dispatch  # noqa: E402
from workbench_core.storage.record_stores import CoreRecordStores  # noqa: E402
from workbench_core.storage.registered import ResourceCatalog  # noqa: E402
from workbench_core.source_transactions import _SourceTransaction  # noqa: E402
from workbench_api.source_transactions import SourceImage  # noqa: E402
from workbench_api.modules import Capability, ExecutionContext, Module  # noqa: E402
from workbench_core.registration_attempts import _Attempt  # noqa: E402
from workbench_core import registration_attempts as registration_attempt_core  # noqa: E402
from workbench_api.record_stores import record_store_scope  # noqa: E402
from workbench_shell.registration_wizard import (  # noqa: E402
    RegistrationWizardError,
    apply_active_registration,
    finalize_active_registration_attempt,
    inspect_active_registration_attempt,
    plan_active_registration,
    registration_capabilities,
    resume_active_registration_attempt,
)


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
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _project(parent: Path) -> Path:
    root = parent / "pack"
    root.mkdir()
    for directory in ("config", "groovy", "mods"):
        (root / directory).mkdir()
    (root / "pack.toml").write_text(PACK_TOML, encoding="utf-8")
    (root / "index.toml").write_text("", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Workbench Test")
    _git(root, "config", "user.email", "workbench@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root


def _instance(parent: Path, payload_name: str = ".minecraft") -> tuple[Path, Path]:
    platform = load_workbench_configuration(SUITE_ROOT).platform_document.values
    instance = parent / "instance"
    payload = instance / payload_name
    for directory in ("config", "groovy", "mods"):
        (payload / directory).mkdir(parents=True, exist_ok=True)
    (instance / "mmc-pack.json").write_text(json.dumps({
        "formatVersion": 1,
        "components": [
            {
                "uid": "net.minecraft",
                "version": "1.12.2",
                "cachedName": "Minecraft",
            },
            {
                "uid": "net.minecraftforge",
                "version": platform["cleanroom_version"],
                "cachedName": "Cleanroom",
            },
        ],
    }), encoding="utf-8")
    (instance / "instance.cfg").write_text(
        "name=Workbench Active Test\n"
        "ManagedPackID=Supersymmetry\n"
        "lastLaunchTime=999999\n",
        encoding="utf-8",
    )
    for name in (
        "gregtech-2.8.10-beta.jar",
        "groovyscript-1.2.0.jar",
        "supersymmetry-v0.1.111.jar",
    ):
        (payload / "mods" / name).write_bytes((name + "\n").encode("ascii"))

    material = payload / "groovy/material"
    material.mkdir(parents=True)
    (material / "SuSyMaterials.groovy").write_text(
        "package material\n\n"
        "class SuSyMaterials {\n\n"
        "    // Petrochem Materials\n\n"
        "    public static Material ExistingFluid\n\n"
        "    // First Degree Materials A\n"
        "}\n",
        encoding="utf-8",
    )
    (material / "PetrochemistryMaterials.groovy").write_text(
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
        encoding="utf-8",
    )
    language = payload / "resources/langfiles/lang/en_us.lang"
    language.parent.mkdir(parents=True)
    language.write_text(
        "# Fluids\n\n"
        "susy.material.existing_fluid=Existing Fluid\n"
        "\n# Thermodynamics\n",
        encoding="utf-8",
    )

    prepost = payload / "groovy/prePostInit"
    prepost.mkdir(parents=True)
    (prepost / "Recipemaps.groovy").write_text(
        "package prePostInit\n\n"
        "class Recipemaps {\n"
        "    static final def MIXER = recipemap('mixer')\n"
        "}\n",
        encoding="utf-8",
    )
    (prepost / "oreDict.groovy").write_text(
        "package prePostInit;\n\n"
        "ore('dustExisting').add(metaitem('dustExisting'))\n",
        encoding="utf-8",
    )
    script = payload / "groovy/postInit/chemistry/Probe.groovy"
    script.parent.mkdir(parents=True)
    script.write_text(
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
    return instance, payload


def _uri_path(value: str) -> Path:
    parsed = urlparse(value)
    return Path(unquote(parsed.path))


class RegistrationWizardTest(unittest.TestCase):
    def setUp(self) -> None:
        configuration = tempfile.TemporaryDirectory()
        self.addCleanup(configuration.cleanup)
        self.configuration_home = Path(configuration.name) / "config"
        environment = patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.configuration_home)})
        environment.start()
        self.addCleanup(environment.stop)
        install_local_host_services()

    def _interrupted_material_apply(
        self, root: Path, *, ordinal: int, after_replace: bool,
    ) -> tuple[Path, Path, Path, dict, dict]:
        project = _project(root)
        instance, payload = _instance(root)
        state = root / "state"
        initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
        answers = {"name": "Pilot Coolant", "color": "0x425d73"}
        plan = plan_active_registration(
            SUITE_ROOT, project, pattern_key="material-backed-fluid",
            answers=answers, state_root=state,
        )
        pid = os.fork()
        if pid == 0:
            original_commit = _SourceTransaction.commit
            calls = 0

            def exit_at_replacement(transaction, stage):
                nonlocal calls
                current = calls
                calls += 1
                if current == ordinal and not after_replace:
                    os._exit(86)
                original_commit(transaction, stage)
                if current == ordinal and after_replace:
                    os._exit(86)

            try:
                with patch.object(_SourceTransaction, "commit", exit_at_replacement):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
            except BaseException:
                os._exit(87)
            os._exit(88)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(86, os.waitstatus_to_exitcode(status))
        return project, payload, state, plan, answers

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_classifies_hard_exit_around_each_source_replacement(self) -> None:
        for ordinal in range(3):
            for after_replace in (False, True):
                with self.subTest(ordinal=ordinal, after_replace=after_replace):
                    with tempfile.TemporaryDirectory() as temporary:
                        project, _payload, state, plan, _answers = self._interrupted_material_apply(
                            Path(temporary), ordinal=ordinal, after_replace=after_replace,
                        )
                        result = inspect_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        )
                        self.assertEqual("prepared", result["receipt_state"])
                        self.assertEqual("consistent", result["journal_status"])
                        for index, operation in enumerate(result["operations"]):
                            self.assertEqual(
                                "after" if index < ordinal + int(after_replace) else "before",
                                operation["source_state"],
                            )
                            self.assertEqual(index <= ordinal, operation["attempted"])
                        self.assertTrue((state / "registrations" / (
                            ".apply-" + plan["plan_id"].removeprefix("sha256:")
                        ) / "backups").is_dir())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_resumes_ordered_partial_attempts_and_is_idempotent(self) -> None:
        for ordinal in range(3):
            for after_replace in (False, True):
                with self.subTest(ordinal=ordinal, after_replace=after_replace):
                    with tempfile.TemporaryDirectory() as temporary:
                        project, payload, state, plan, _answers = self._interrupted_material_apply(
                            Path(temporary), ordinal=ordinal, after_replace=after_replace,
                        )
                        retained = state / "registrations" / (
                            ".apply-" + plan["plan_id"].removeprefix("sha256:")
                        )
                        expected = {
                            row["path"]: (retained / "after" / row["path"]).read_bytes()
                            for row in plan["operations"]
                        }
                        result = resume_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        )
                        self.assertEqual("applied", result["outcome"])
                        self.assertEqual(expected, {
                            path: (payload / path).read_bytes() for path in expected
                        })
                        final = state / "registrations" / plan["plan_id"].removeprefix("sha256:")
                        self.assertEqual("applied", json.loads((final / "receipt.json").read_bytes())["state"])
                        self.assertEqual(result, resume_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        ))
                        self.assertFalse(retained.exists())
                        if ordinal == 0 and not after_replace:
                            output = io.StringIO()
                            with redirect_stdout(output):
                                code = cli_main([
                                    "register", str(project), "--suite-root", str(SUITE_ROOT),
                                    "--state-root", str(state), "--resume-attempt", plan["plan_id"],
                                    "--json",
                                ])
                            self.assertEqual(0, code)
                            self.assertEqual("applied", json.loads(output.getvalue())["outcome"])

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_installed_dispatch_resumes_with_selected_configuration_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, payload, state, plan, _answers = self._interrupted_material_apply(
                root, ordinal=0, after_replace=True,
            )
            context = ExecutionContext(
                project, state, configuration_home=self.configuration_home,
            )
            module = InstalledModule(
                "workbench-shell", "workbench-shell", "0.1.0", "available",
                module=Module("workbench-shell", "0.1.0", (
                    Capability(
                        "workbench-shell.register", ("register",),
                        "workbench_shell.commands:register", "Run register",
                    ),
                )),
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(root / "other-config")}):
                with redirect_stdout(output):
                    code = dispatch([
                        "register", str(project), "--suite-root", str(SUITE_ROOT),
                        "--state-root", str(state), "--resume-attempt", plan["plan_id"],
                        "--json",
                    ], context, (module,), suite_root=SUITE_ROOT)
            self.assertEqual(0, code)
            self.assertEqual("applied", json.loads(output.getvalue())["outcome"])
            self.assertTrue(all((payload / row["path"]).is_file()
                                for row in plan["operations"]))

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_resumption_survives_exit_around_replay(self) -> None:
        for window in ("after-new-marker", "after-first-replay-commit"):
            with self.subTest(window=window):
                with tempfile.TemporaryDirectory() as temporary:
                    project, payload, state, plan, _answers = self._interrupted_material_apply(
                        Path(temporary), ordinal=0,
                        after_replace=window == "after-new-marker",
                    )
                    pid = os.fork()
                    if pid == 0:
                        original_commit = _SourceTransaction.commit
                        calls = 0

                        def exit_during_resume(transaction, stage):
                            nonlocal calls
                            current = calls
                            calls += 1
                            if current == 0 and window == "after-new-marker":
                                os._exit(86)
                            original_commit(transaction, stage)
                            if current == 0 and window == "after-first-replay-commit":
                                os._exit(86)

                        try:
                            with patch.object(_SourceTransaction, "commit", exit_during_resume):
                                resume_active_registration_attempt(
                                    SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                                )
                        except BaseException:
                            os._exit(87)
                        os._exit(88)
                    _, status = os.waitpid(pid, 0)
                    self.assertEqual(86, os.waitstatus_to_exitcode(status))
                    inspected = inspect_active_registration_attempt(
                        SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                    )
                    self.assertEqual("prepared", inspected["receipt_state"])
                    self.assertEqual("consistent", inspected["journal_status"])
                    if window == "after-new-marker":
                        self.assertTrue(inspected["operations"][1]["attempted"])
                        self.assertEqual("before", inspected["operations"][1]["source_state"])
                        self.assertTrue(inspected["operations"][1]["stage_present"])
                    else:
                        self.assertEqual("after", inspected["operations"][0]["source_state"])
                        self.assertFalse(inspected["operations"][1]["attempted"])
                    completed = resume_active_registration_attempt(
                        SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                    )
                    self.assertEqual("applied", completed["outcome"])
                    self.assertEqual(["after"] * len(plan["operations"]), [
                        row["source_state"] for row in inspect_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        )["operations"]
                    ])
                    self.assertTrue(all((payload / row["path"]).is_file()
                                        for row in plan["operations"]))

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_resumption_refuses_rollback_unknown_source_and_parent(self) -> None:
        for condition in ("rollback", "unknown-source", "changed-parent", "extra-stage", "changed-backup", "linked-stage"):
            with self.subTest(condition=condition):
                with tempfile.TemporaryDirectory() as temporary:
                    project, payload, state, plan, _answers = self._interrupted_material_apply(
                        Path(temporary), ordinal=1, after_replace=True,
                    )
                    retained = state / "registrations" / (
                        ".apply-" + plan["plan_id"].removeprefix("sha256:")
                    )
                    if condition == "rollback":
                        manifest = json.loads((retained / "attempt.json").read_bytes())
                        ordinal = 1
                        relative = plan["operations"][ordinal]["path"]
                        stage = json.loads((retained / "stages" / f"{ordinal:03d}.json").read_bytes())
                        transaction = _SourceTransaction(
                            payload, binding=f"registration:{plan['plan_id']}",
                            staging_token=manifest["staging_token"], check_cancelled=lambda: None,
                        )
                        reference = transaction.attach(
                            relative,
                            before=SourceImage("file", (retained / "backups" / relative).read_bytes(),
                                               mode=stat.S_IMODE((payload / relative).stat().st_mode)),
                            after=SourceImage("file", (retained / "after" / relative).read_bytes(),
                                              mode=stat.S_IMODE((payload / relative).stat().st_mode)),
                            staged_relative=stage["staged_relative"], attempted=True,
                        )
                        transaction.rollback(reference)
                        self.assertFalse((payload / stage["staged_relative"]).exists())
                    elif condition == "unknown-source":
                        (payload / plan["operations"][0]["path"]).write_bytes(b"later user edit\n")
                    elif condition == "changed-parent":
                        parent = (payload / plan["operations"][0]["path"]).parent
                        parent.rename(parent.with_name(parent.name + "-replaced"))
                        parent.mkdir()
                    elif condition == "extra-stage":
                        manifest = json.loads((retained / "attempt.json").read_bytes())
                        source = payload / plan["operations"][2]["path"]
                        (source.parent / (
                            f".{source.name}.workbench-{manifest['staging_token']}-extra.tmp"
                        )).write_bytes(b"unrecorded")
                    elif condition == "linked-stage":
                        stage = json.loads((retained / "stages/002.json").read_bytes())
                        os.link(payload / stage["staged_relative"], Path(temporary) / "outside-stage-link")
                    else:
                        (retained / "backups" / plan["operations"][0]["path"]).write_bytes(b"changed")
                    before = {row["path"]: (payload / row["path"]).read_bytes()
                              for row in plan["operations"] if (payload / row["path"]).is_file()}
                    with self.assertRaisesRegex(RegistrationWizardError, "review"):
                        resume_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        )
                    self.assertTrue((retained / "attempt.json").is_file())
                    self.assertEqual(before, {path: (payload / path).read_bytes() for path in before})

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_resumption_survives_exit_at_receipt_promotion(self) -> None:
        for after_rename in (False, True):
            with self.subTest(after_rename=after_rename):
                with tempfile.TemporaryDirectory() as temporary:
                    project, _payload, state, plan, _answers = self._interrupted_material_apply(
                        Path(temporary), ordinal=0, after_replace=True,
                    )
                    pid = os.fork()
                    if pid == 0:
                        original_rename = registration_attempt_core._rename_noreplace

                        def exit_at_rename(source, target):
                            if not after_rename:
                                os._exit(86)
                            original_rename(source, target)
                            os._exit(86)

                        try:
                            with patch.object(registration_attempt_core, "_rename_noreplace", exit_at_rename):
                                resume_active_registration_attempt(
                                    SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                                )
                        except BaseException:
                            os._exit(87)
                        os._exit(88)
                    _, status = os.waitpid(pid, 0)
                    self.assertEqual(86, os.waitstatus_to_exitcode(status))
                    completed = resume_active_registration_attempt(
                        SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                    )
                    self.assertEqual("applied", completed["outcome"])
                    self.assertEqual(completed, resume_active_registration_attempt(
                        SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                    ))

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_marks_external_edit_for_review_and_retains_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, payload, state, plan, _answers = self._interrupted_material_apply(
                Path(temporary), ordinal=0, after_replace=True,
            )
            relative = plan["operations"][0]["path"]
            (payload / relative).write_bytes(b"later user edit\n")
            result = inspect_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            )
            self.assertEqual("review-required", result["journal_status"])
            self.assertEqual("other", result["operations"][0]["source_state"])
            retained = state / "registrations" / (
                ".apply-" + plan["plan_id"].removeprefix("sha256:")
            )
            self.assertTrue((retained / "backups" / relative).is_file())
            self.assertEqual(b"later user edit\n", (payload / relative).read_bytes())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_rejects_changed_backup_or_stage_journal(self) -> None:
        for changed in ("backup", "stage"):
            with self.subTest(changed=changed):
                with tempfile.TemporaryDirectory() as temporary:
                    project, _payload, state, plan, _answers = self._interrupted_material_apply(
                        Path(temporary), ordinal=0, after_replace=True,
                    )
                    retained = state / "registrations" / (
                        ".apply-" + plan["plan_id"].removeprefix("sha256:")
                    )
                    path = (
                        retained / "backups" / plan["operations"][0]["path"]
                        if changed == "backup" else retained / "stages/000.json"
                    )
                    path.write_bytes(b"changed retained evidence\n")
                    with self.assertRaisesRegex(RegistrationWizardError, "review"):
                        inspect_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        )
                    self.assertTrue((retained / "attempt.json").is_file())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_keeps_applied_receipt_stage_for_review_before_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, _payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            pid = os.fork()
            if pid == 0:
                with patch.object(_Attempt, "promote", lambda _attempt: os._exit(86)):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
                os._exit(87)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(86, os.waitstatus_to_exitcode(status))
            inspected = inspect_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            )
            self.assertEqual("applied", inspected["receipt_state"])
            self.assertEqual("review-required", inspected["journal_status"])
            self.assertEqual(
                ["after"] * len(plan["operations"]),
                [row["source_state"] for row in inspected["operations"]],
            )
            completed = finalize_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            )
            self.assertEqual("applied", completed["outcome"])
            final = state / "registrations" / plan["plan_id"].removeprefix("sha256:")
            self.assertEqual("applied", json.loads((final / "receipt.json").read_bytes())["state"])
            self.assertFalse((state / "registrations" / (
                ".apply-" + plan["plan_id"].removeprefix("sha256:")
            )).exists())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_finishes_all_committed_source_edits_without_rewriting_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            pid = os.fork()
            if pid == 0:
                with patch.object(_Attempt, "publish_applied", lambda _attempt, _receipt: os._exit(86)):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
                os._exit(87)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(86, os.waitstatus_to_exitcode(status))
            before = {row["path"]: (payload / row["path"]).read_bytes()
                      for row in plan["operations"]}
            inspected = inspect_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            )
            self.assertEqual("prepared", inspected["receipt_state"])
            self.assertEqual("consistent", inspected["journal_status"])
            completed = finalize_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            )
            self.assertEqual("applied", completed["outcome"])
            self.assertEqual(before, {path: (payload / path).read_bytes() for path in before})
            final = state / "registrations" / plan["plan_id"].removeprefix("sha256:")
            self.assertEqual((final / "receipt.json").as_uri(), completed["receipt_uri"])
            self.assertEqual("applied", json.loads((final / "receipt.json").read_bytes())["state"])
            self.assertEqual(completed, finalize_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            ))
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main([
                    "register", str(project), "--suite-root", str(SUITE_ROOT),
                    "--state-root", str(state), "--finalize-attempt", plan["plan_id"],
                    "--json",
                ])
            self.assertEqual(0, code)
            self.assertEqual("applied", json.loads(output.getvalue())["outcome"])

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_does_not_report_applied_if_source_changes_after_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            pid = os.fork()
            if pid == 0:
                with patch.object(_Attempt, "publish_applied", lambda _attempt, _receipt: os._exit(86)):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
                os._exit(87)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(86, os.waitstatus_to_exitcode(status))
            source = payload / plan["operations"][0]["path"]
            original_rename = registration_attempt_core._rename_noreplace

            def rename_then_edit(before: Path, after: Path) -> None:
                original_rename(before, after)
                source.write_bytes(b"later user edit\n")

            with patch.object(registration_attempt_core, "_rename_noreplace", rename_then_edit):
                with self.assertRaisesRegex(RegistrationWizardError, "review"):
                    finalize_active_registration_attempt(
                        SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                    )
            final = state / "registrations" / plan["plan_id"].removeprefix("sha256:")
            self.assertTrue((final / "receipt.json").is_file())
            self.assertEqual(b"later user edit\n", source.read_bytes())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_completion_refuses_partial_changed_or_extra_stage(self) -> None:
        for condition in ("partial", "later-edit", "extra-stage"):
            with self.subTest(condition=condition):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    if condition == "partial":
                        project, payload, state, plan, _answers = self._interrupted_material_apply(
                            root, ordinal=0, after_replace=True,
                        )
                    else:
                        project = _project(root)
                        instance, payload = _instance(root)
                        state = root / "state"
                        initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
                        answers = {"name": "Pilot Coolant", "color": "0x425d73"}
                        plan = plan_active_registration(
                            SUITE_ROOT, project, pattern_key="material-backed-fluid",
                            answers=answers, state_root=state,
                        )
                        pid = os.fork()
                        if pid == 0:
                            with patch.object(_Attempt, "publish_applied", lambda _attempt, _receipt: os._exit(86)):
                                apply_active_registration(
                                    SUITE_ROOT, project, pattern_key="material-backed-fluid",
                                    answers=answers, state_root=state,
                                )
                            os._exit(87)
                        _, status = os.waitpid(pid, 0)
                        self.assertEqual(86, os.waitstatus_to_exitcode(status))
                        first = payload / plan["operations"][0]["path"]
                        if condition == "later-edit":
                            first.write_bytes(b"later user edit\n")
                        else:
                            retained = state / "registrations" / (
                                ".apply-" + plan["plan_id"].removeprefix("sha256:")
                            )
                            token = json.loads((retained / "attempt.json").read_bytes())["staging_token"]
                            (first.parent / f".{first.name}.workbench-{token}-extra.tmp").write_bytes(
                                b"unrecorded stage\n"
                            )
                    with self.assertRaisesRegex(RegistrationWizardError, "review"):
                        finalize_active_registration_attempt(
                            SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                        )
                    retained = state / "registrations" / (
                        ".apply-" + plan["plan_id"].removeprefix("sha256:")
                    )
                    self.assertTrue((retained / "attempt.json").is_file())

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_flags_orphan_stage_before_journal_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            pid = os.fork()
            if pid == 0:
                with patch.object(_Attempt, "record_stage", lambda _attempt, _ordinal, _relative: os._exit(86)):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
                os._exit(87)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(86, os.waitstatus_to_exitcode(status))
            inspected = inspect_active_registration_attempt(
                SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
            )
            self.assertEqual("review-required", inspected["journal_status"])
            self.assertEqual(
                ["before"] * len(plan["operations"]),
                [row["source_state"] for row in inspected["operations"]],
            )
            retained = state / "registrations" / (
                ".apply-" + plan["plan_id"].removeprefix("sha256:")
            )
            token = json.loads((retained / "attempt.json").read_bytes())["staging_token"]
            first = payload / plan["operations"][0]["path"]
            self.assertEqual(1, len(list(first.parent.glob(
                f".{first.name}.workbench-{token}-*.tmp",
            ))))
            with self.assertRaisesRegex(RegistrationWizardError, "review"):
                resume_active_registration_attempt(
                    SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                )

    @unittest.skipUnless(hasattr(os, "fork"), "hard-exit fixture requires fork")
    def test_restart_rejects_source_parent_redirect_and_state_root_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, payload, state, plan, _answers = self._interrupted_material_apply(
                Path(temporary), ordinal=0, after_replace=True,
            )
            parent = (payload / plan["operations"][0]["path"]).parent
            displaced = parent.with_name(parent.name + "-original")
            parent.rename(displaced)
            parent.symlink_to(displaced, target_is_directory=True)
            with self.assertRaisesRegex(RegistrationWizardError, "review"):
                inspect_active_registration_attempt(
                    SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                )
            parent.unlink()
            displaced.rename(parent)
            displaced_state = state.with_name("state-original")
            state.rename(displaced_state)
            state.mkdir()
            shutil.copytree(displaced_state / "active-instances", state / "active-instances")
            with self.assertRaises((RegistrationWizardError, OSError)):
                inspect_active_registration_attempt(
                    SUITE_ROOT, project, plan_id=plan["plan_id"], state_root=state,
                )
            self.assertTrue((displaced_state / "registrations" / (
                ".apply-" + plan["plan_id"].removeprefix("sha256:")
            ) / "attempt.json").is_file())

    def test_applied_receipt_failure_rolls_back_and_discards_exact_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            originals = {
                row["path"]: (payload / row["path"]).read_bytes()
                for row in plan["operations"]
            }
            with patch.object(_Attempt, "publish_applied", side_effect=OSError("receipt unavailable")):
                with self.assertRaisesRegex(RegistrationWizardError, "rolled back"):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
            self.assertEqual([], list((state / "registrations").iterdir()))
            for relative, raw in originals.items():
                self.assertEqual(raw, (payload / relative).read_bytes())

    def test_uncertain_promotion_keeps_prepared_receipt_and_restored_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            originals = {
                row["path"]: (payload / row["path"]).read_bytes()
                for row in plan["operations"]
            }
            original_rename = registration_attempt_core._rename_noreplace

            def rename_then_fail(source, destination):
                original_rename(source, destination)
                raise OSError("injected post-rename failure")

            with patch.object(registration_attempt_core, "_rename_noreplace", rename_then_fail):
                with self.assertRaisesRegex(RegistrationWizardError, "retained attempt requires review"):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
            retained = state / "registrations" / plan["plan_id"].removeprefix("sha256:")
            self.assertEqual("prepared", json.loads((retained / "receipt.json").read_bytes())["state"])
            for relative, raw in originals.items():
                self.assertEqual(raw, (payload / relative).read_bytes())

    def test_replace_then_error_uses_attempt_journal_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            originals = {
                row["path"]: (payload / row["path"]).read_bytes()
                for row in plan["operations"]
            }

            def replace_then_fail(transaction, stage):
                entry = transaction._entry(stage)
                target = transaction._target(entry["relative"], create_parents=False)
                os.replace(entry["staged"], target)
                raise OSError("injected error after replacement")

            with patch.object(_SourceTransaction, "commit", replace_then_fail):
                with self.assertRaisesRegex(RegistrationWizardError, "rolled back"):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
            self.assertEqual([], list((state / "registrations").iterdir()))
            for relative, raw in originals.items():
                self.assertEqual(raw, (payload / relative).read_bytes())

    def test_later_commit_failure_restores_only_unchanged_wizard_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            originals = {
                row["path"]: (payload / row["path"]).read_bytes()
                for row in plan["operations"]
            }
            original_commit = _SourceTransaction.commit
            calls = 0

            def fail_second(transaction, stage):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected second commit failure")
                original_commit(transaction, stage)

            with patch.object(_SourceTransaction, "commit", fail_second):
                with self.assertRaisesRegex(
                    RegistrationWizardError, "failed and was rolled back",
                ):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
            self.assertEqual(calls, 2)
            for relative, before in originals.items():
                self.assertEqual((payload / relative).read_bytes(), before)
            self.assertEqual([], list((state / "registrations").iterdir()))

    def test_changed_output_blocks_rollback_and_keeps_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            initialize_active_instance(SUITE_ROOT, project, instance, state_root=state)
            answers = {"name": "Pilot Coolant", "color": "0x425d73"}
            plan = plan_active_registration(
                SUITE_ROOT, project, pattern_key="material-backed-fluid",
                answers=answers, state_root=state,
            )
            first = plan["operations"][0]["path"]
            target = payload / first
            before = target.read_bytes()
            original_commit = _SourceTransaction.commit
            calls = 0

            def change_after_first(transaction, stage):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected second commit failure")
                original_commit(transaction, stage)
                target.write_bytes(b"later user edit\n")

            with patch.object(_SourceTransaction, "commit", change_after_first):
                with self.assertRaisesRegex(
                    RegistrationWizardError, "rollback also failed",
                ):
                    apply_active_registration(
                        SUITE_ROOT, project, pattern_key="material-backed-fluid",
                        answers=answers, state_root=state,
                    )
            self.assertEqual(target.read_bytes(), b"later user edit\n")
            transaction = state / "registrations" / plan["plan_id"].removeprefix("sha256:")
            self.assertTrue(transaction.is_dir())
            self.assertEqual((transaction / "backups" / first).read_bytes(), before)
            self.assertEqual(json.loads((transaction / "receipt.json").read_bytes())["state"], "prepared")

    def test_active_instance_core_store_keeps_historical_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, _payload = _instance(root)
            state = root / "state"
            catalog_home = root / "catalog"
            with record_store_scope(CoreRecordStores(
                workspace=project, configuration_home=catalog_home,
                owner_id="workbench-shell",
            )):
                result = initialize_active_instance(
                    SUITE_ROOT, project, instance, state_root=state,
                )
            selection = _uri_path(result["selection_uri"])
            self.assertEqual(state / "active-instances", selection.parent)
            self.assertEqual(
                result["selection"], json.loads(selection.read_text("utf-8"))
            )
            self.assertEqual(
                result["selection"]["selection_id"],
                load_active_instance(
                    SUITE_ROOT, project, state_root=state,
                )["selection_id"],
            )
            rows = ResourceCatalog(catalog_home).inventory(workspace=project)["record_stores"]
            self.assertEqual([str(selection.parent)], [row["path"] for row in rows])
            self.assertEqual("available", rows[0]["status"])
            if os.name == "posix":
                self.assertEqual(0o700, selection.parent.stat().st_mode & 0o777)
                self.assertEqual(0o600, selection.stat().st_mode & 0o777)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_active_instance_rejects_linked_selection_without_replacing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, _payload = _instance(root)
            state = root / "state"
            selected = initialize_active_instance(
                SUITE_ROOT, project, instance, state_root=state,
            )
            selection = _uri_path(selected["selection_uri"])
            outside = root / "outside.json"
            before = selection.read_bytes()
            outside.write_bytes(before)
            selection.unlink()
            selection.symlink_to(outside)
            with self.assertRaisesRegex(ActiveInstanceError, "cannot persist"):
                initialize_active_instance(
                    SUITE_ROOT, project, instance, state_root=state,
                )
            self.assertEqual(before, outside.read_bytes())

    @unittest.skipUnless(os.name == "posix", "POSIX historical mode fixture")
    def test_active_instance_reselection_upgrades_ordinary_legacy_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, _payload = _instance(root)
            state = root / "state"
            first = initialize_active_instance(
                SUITE_ROOT, project, instance, state_root=state,
            )
            selection = _uri_path(first["selection_uri"])
            selection.chmod(0o644)
            self.assertEqual(
                first["selection"]["selection_id"],
                load_active_instance(
                    SUITE_ROOT, project, state_root=state,
                )["selection_id"],
            )
            second = initialize_active_instance(
                SUITE_ROOT, project, instance, state_root=state,
            )
            self.assertEqual(first["selection_uri"], second["selection_uri"])
            self.assertEqual(first["selection"], second["selection"])
            self.assertEqual(0o600, selection.stat().st_mode & 0o777)

    def test_all_ready_patterns_apply_directly_and_retain_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            selected = initialize_active_instance(
                SUITE_ROOT,
                project,
                instance,
                state_root=state,
            )

            self.assertEqual(selected["outcome"], "selected")
            self.assertEqual(
                load_active_instance(
                    SUITE_ROOT, project, state_root=state
                )["payload_path"],
                payload.resolve(),
            )
            capabilities = registration_capabilities(
                SUITE_ROOT, project, state_root=state
            )
            self.assertEqual(len(capabilities["families"]), 36)
            self.assertEqual(
                {row["key"] for row in capabilities["patterns"]},
                {
                    "material-backed-fluid",
                    "machine-recipe",
                    "ore-dictionary-entry",
                },
            )
            self.assertEqual(
                capabilities["runtime_options"]["machine-recipe"]["recipe_map"],
                ["MIXER"],
            )

            recipe_answers = {
                "script": "groovy/postInit/chemistry/Probe.groovy",
                "recipe_map": "MIXER",
                "item_inputs": [
                    {"kind": "ore", "name": "dustSulfur", "amount": 1}
                ],
                "fluid_inputs": [],
                "item_outputs": [],
                "fluid_outputs": [{"name": "sulfuric_water", "amount": 1000}],
                "duration": 100,
                "voltage_tier": "LV",
            }
            script = payload / "groovy/postInit/chemistry/Probe.groovy"
            before_recipe = script.read_bytes()
            plan = plan_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="machine-recipe",
                answers=recipe_answers,
                state_root=state,
            )
            self.assertEqual(script.read_bytes(), before_recipe)
            self.assertIn("MIXER.recipeBuilder()", plan["operations"][0]["diff"])
            with self.assertRaisesRegex(
                RegistrationWizardError,
                "plan changed after review",
            ):
                apply_active_registration(
                    SUITE_ROOT,
                    project,
                    pattern_key="machine-recipe",
                    answers=recipe_answers,
                    expected_plan_id="sha256:" + ("0" * 64),
                    state_root=state,
                )
            self.assertEqual(script.read_bytes(), before_recipe)
            recipe_result = apply_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="machine-recipe",
                answers=recipe_answers,
                expected_plan_id=plan["plan_id"],
                state_root=state,
            )
            self.assertIn("fluid('sulfuric_water')", script.read_text("utf-8"))
            inspected = inspect_active_registration_attempt(
                SUITE_ROOT, project, plan_id=recipe_result["plan"]["plan_id"],
                state_root=state,
            )
            self.assertEqual("applied", inspected["receipt_state"])
            self.assertEqual("consistent", inspected["journal_status"])
            self.assertEqual(["after"], [row["source_state"] for row in inspected["operations"]])
            catalog_rows = ResourceCatalog(self.configuration_home).inventory(
                workspace=project,
            )["record_stores"]
            self.assertIn(str(state / "registrations"), [row["path"] for row in catalog_rows])
            receipt_path = _uri_path(
                recipe_result["receipt"]["target"]["receipt_uri"]
            )
            backup = (
                receipt_path.parent
                / recipe_result["receipt"]["outputs"][0]["backup_path"]
            )
            self.assertEqual(backup.read_bytes(), before_recipe)

            ore_result = apply_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="ore-dictionary-entry",
                answers={
                    "ore_name": "dustWorkbenchProbe",
                    "ingredient": {
                        "kind": "metaitem",
                        "name": "dustSodiumHydroxide",
                    },
                },
                state_root=state,
            )
            self.assertEqual(ore_result["outcome"], "applied")
            self.assertIn(
                "ore('dustWorkbenchProbe').add("
                "metaitem('dustSodiumHydroxide'))",
                (payload / "groovy/prePostInit/oreDict.groovy").read_text("utf-8"),
            )

            material_result = apply_active_registration(
                SUITE_ROOT,
                project,
                pattern_key="material-backed-fluid",
                answers={"name": "Pilot Coolant", "color": "0x425d73"},
                state_root=state,
            )
            self.assertEqual(
                material_result["plan"]["effective_answers"]["material_id"],
                20001,
            )
            self.assertIn(
                "PilotCoolant = new Material.Builder(20001, "
                "SuSyUtility.susyId('pilot_coolant'))",
                (payload / "groovy/material/PetrochemistryMaterials.groovy").read_text(
                    "utf-8"
                ),
            )
            self.assertFalse((payload / "groovy/preInit/register_material_pilot_coolant.groovy").exists())
            self.assertEqual(
                load_active_instance(
                    SUITE_ROOT, project, state_root=state
                )["selection_id"],
                selected["selection"]["selection_id"],
            )

    def test_runtime_marker_drift_requires_reinitialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root, "minecraft")
            state = root / "state"
            initialize_active_instance(
                SUITE_ROOT, project, payload, state_root=state
            )
            (payload / "mods/gregtech-2.8.10-beta.jar").write_bytes(b"drift\n")

            with self.assertRaisesRegex(ActiveInstanceError, "identity has drifted"):
                load_active_instance(SUITE_ROOT, project, state_root=state)

    def test_cli_initializes_and_applies_json_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            instance, payload = _instance(root)
            state = root / "state"
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main([
                    "initialize",
                    str(project),
                    "--instance",
                    str(instance),
                    "--suite-root",
                    str(SUITE_ROOT),
                    "--state-root",
                    str(state),
                    "--json",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["outcome"], "selected")

            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main([
                    "register",
                    str(project),
                    "--list",
                    "--suite-root",
                    str(SUITE_ROOT),
                    "--state-root",
                    str(state),
                    "--json",
                ])
            self.assertEqual(status, 0)
            listed = json.loads(output.getvalue())
            self.assertEqual(len(listed["families"]), 36)
            self.assertEqual(len(listed["patterns"]), 3)

            answers = root / "answers.json"
            answers.write_text(json.dumps({
                "ore_name": "dustCliProbe",
                "ingredient": {
                    "kind": "metaitem",
                    "name": "dustSodiumHydroxide",
                },
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main([
                    "register",
                    str(project),
                    "--pattern",
                    "ore-dictionary-entry",
                    "--answers",
                    str(answers),
                    "--apply",
                    "--yes",
                    "--suite-root",
                    str(SUITE_ROOT),
                    "--state-root",
                    str(state),
                    "--json",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["outcome"], "applied")
            self.assertIn(
                "ore('dustCliProbe')",
                (payload / "groovy/prePostInit/oreDict.groovy").read_text("utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
