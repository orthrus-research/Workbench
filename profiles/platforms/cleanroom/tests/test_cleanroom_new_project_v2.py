from __future__ import annotations

import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, os.fspath(ROOT / "modules/blueprints/src"))
sys.path.insert(0, os.fspath(ROOT / "profiles/platforms/cleanroom/src"))

from workbench_blueprints import fresh_project  # noqa: E402
from workbench_blueprints.interface import AdapterSet  # noqa: E402
from workbench_cleanroom_new_project import construction  # noqa: E402
from workbench_cleanroom_new_project import cli as construction_cli  # noqa: E402


PROFILE = ROOT / "profiles/platforms/cleanroom"
SCHEMAS = PROFILE / "schemas"
OWNER = PROFILE / "new-project-kinds/cleanroom-mod-construction-owner-v2.json"
KIND = PROFILE / "new-project-kinds/cleanroom-mod.json"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _git(target: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    for key in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_EMAIL": "workbench@example.invalid",
            "GIT_AUTHOR_NAME": "Workbench Test",
            "GIT_COMMITTER_EMAIL": "workbench@example.invalid",
            "GIT_COMMITTER_NAME": "Workbench Test",
            "LC_ALL": "C",
        }
    )
    return subprocess.run(
        ["git", "-C", os.fspath(target), *arguments],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
    )


class FreshProjectV2Tests(unittest.TestCase):
    def test_observes_absent_empty_and_unborn_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            parent = Path(temporary)
            absent = parent / "absent"
            absent_observation = fresh_project.observe_fresh_target(absent)
            self.assertEqual("absent", absent_observation["state"])
            self.assertFalse(absent.exists())

            empty = parent / "empty"
            empty.mkdir()
            empty_observation = fresh_project.observe_fresh_target(empty)
            self.assertEqual("empty-directory", empty_observation["state"])
            self.assertEqual([], list(empty.iterdir()))

            unborn = parent / "unborn"
            unborn.mkdir()
            _git(unborn, "init", "--quiet", "--initial-branch=main")
            before = (unborn / ".git/info/exclude").read_bytes()
            unborn_observation = fresh_project.observe_fresh_target(unborn)
            self.assertEqual("unborn-git", unborn_observation["state"])
            self.assertEqual("unborn", unborn_observation["git"]["head_state"])
            self.assertEqual(before, (unborn / ".git/info/exclude").read_bytes())

    def test_rejects_nonfresh_born_and_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            parent = Path(temporary)
            nonempty = parent / "nonempty"
            nonempty.mkdir()
            (nonempty / "owned.txt").write_text("not fresh", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contains bytes"):
                fresh_project.observe_fresh_target(nonempty)

            born = parent / "born"
            born.mkdir()
            _git(born, "init", "--quiet", "--initial-branch=main")
            _git(born, "commit", "--allow-empty", "--no-gpg-sign", "-m", "born")
            with self.assertRaisesRegex(ValueError, "born HEAD"):
                fresh_project.observe_fresh_target(born)

            link = parent / "link"
            link.symlink_to(nonempty, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "ordinary directory"):
                fresh_project.observe_fresh_target(link)

    def test_bootstrap_restores_exact_empty_and_unborn_states(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            parent = Path(temporary)
            for state_name in ("absent", "empty", "unborn"):
                with self.subTest(state=state_name):
                    target = parent / state_name
                    if state_name != "absent":
                        target.mkdir()
                    if state_name == "unborn":
                        _git(target, "init", "--quiet", "--initial-branch=main")
                        exclude_before = (target / ".git/info/exclude").read_bytes()
                    observation = fresh_project.observe_fresh_target(target)
                    state_root = parent / f"state-{state_name}"
                    fresh_project.prepare_fresh_target(
                        target, observation, state_root, plan_id=f"plan:{state_name}"
                    )
                    self.assertTrue((target / ".git").is_dir())
                    restored = fresh_project.restore_fresh_target(
                        target, state_root, plan_id=f"plan:{state_name}"
                    )
                    self.assertEqual("restored", restored["outcome"])
                    if state_name == "absent":
                        self.assertFalse(target.exists())
                    elif state_name == "empty":
                        self.assertEqual([], list(target.iterdir()))
                    else:
                        self.assertEqual(
                            exclude_before, (target / ".git/info/exclude").read_bytes()
                        )


class CleanroomModConstructionV2Tests(unittest.TestCase):
    def _preview(self, target: Path, *, direct: bool = False) -> dict[str, object]:
        request = construction.build_cleanroom_mod_request(
            target,
            output_mode="direct-apply" if direct else "instructions",
            allow_direct_apply=direct,
        )
        return construction.preview_cleanroom_mod_construction(ROOT, request)

    def test_owner_and_current_kind_schemas_are_closed(self) -> None:
        owner = construction.validate_construction_owner(ROOT)
        self.assertEqual("workbench-cleanroom-mod-construction-owner-v2", owner["format"])
        self.assertEqual(construction.KIND_ID, owner["kind_id"])
        self.assertEqual(19, len(owner["output_manifest"]["files"]))
        self.assertEqual(
            "sha256:c45e87e64b44f7d432408204577170d5acea1d178f8b929ef79c56d44eb2276a",
            owner["output_manifest"]["tree_digest"],
        )
        pairs = (
            (OWNER, SCHEMAS / "workbench-cleanroom-mod-construction-owner-v2.schema.json"),
            (KIND, SCHEMAS / "workbench-cleanroom-new-project-kind.schema.json"),
        )
        for record_path, schema_path in pairs:
            with self.subTest(record=record_path.name):
                Draft202012Validator(
                    _load(schema_path), format_checker=FormatChecker()
                ).validate(_load(record_path))
        kind = _load(KIND)
        self.assertEqual("workbench-cleanroom-new-project-kind-v4", kind["format"])
        slot = kind["declared_values"]["identity"]["construction_slot"]
        self.assertEqual(owner["id"], slot["construction_owner_id"])
        reference = slot["construction_owner_ref"]
        self.assertEqual(OWNER, ROOT / reference["path"])
        self.assertEqual(
            f"sha256:{sha256(OWNER.read_bytes()).hexdigest()}", reference["sha256"]
        )
        self.assertEqual(
            f"sha256:{sha256((ROOT / reference['schema_path']).read_bytes()).hexdigest()}",
            reference["schema_sha256"],
        )
        self.assertEqual(
            {
                "path": "profiles/platforms/cleanroom/candidates/0.6.8-alpha/candidate-lock-v1.json",
                "schema_path": "profiles/platforms/cleanroom/schemas/workbench-cleanroom-candidate-lock-v1.schema.json",
                "schema_sha256": "sha256:847e6b0c0b3178e775bc9d537a41a8a5cddb0b35984743c1a7bb4c7f410fe6d7",
                "sha256": "sha256:d6f34886222a4d9e376ef6b1144a3f00e70ce14e8c779363997e8aa206aaf541",
            },
            kind["declared_values"]["candidate_lock_ref"],
        )

    def test_preview_is_read_only_and_binds_exact_portable_tree(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            target = Path(temporary) / "fresh-project"
            result = self._preview(target)
            self.assertEqual("ready", result["state"])
            self.assertEqual("preview", result["command"])
            self.assertFalse(target.exists())
            plan = result["plan"]
            self.assertEqual(19, len(plan["operations"]))
            self.assertEqual(list(range(19)), [row["ordinal"] for row in plan["operations"]])
            build = next(row for row in plan["operations"] if row["path"] == "build.gradle")
            rendered = __import__("base64").b64decode(build["after_base64"], validate=True)
            self.assertIn(b"layout.projectDirectory.dir('.workbench/build')", rendered)
            self.assertIn(b"rootProject.file('.workbench')", rendered)
            self.assertNotIn(b"../../../../../.workbench", rendered)

    def test_exact_consent_constructs_an_unborn_git_project_and_retains_receipts(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            target = root / "fresh-project"
            state_root = root / "state"
            preview = self._preview(target, direct=True)
            plan = preview["plan"]
            result = construction.apply_cleanroom_mod_construction(
                ROOT, plan, state_root, consent_plan_id=plan["id"]
            )
            self.assertEqual("applied", result["state"])
            self.assertEqual("applied", result["application_receipt"]["state"])
            self.assertEqual("applied", result["bootstrap_receipt"]["state"])
            self.assertTrue(construction._verify_constructed_target(target, plan))
            self.assertNotEqual(0, _git(target, "rev-parse", "--verify", "HEAD", check=False).returncode)
            self.assertFalse((target / ".deconstruction").exists())
            self.assertFalse((target / ".workbench").exists())
            self.assertTrue((state_root / "construction-application-receipt-v2.json").is_file())
            retained = fresh_project.load_retained_bootstrap_receipt(
                state_root, plan_id=plan["id"]
            )
            self.assertEqual(result["bootstrap_receipt"], retained)

    def test_wrong_consent_stale_target_and_missing_adapter_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            target = root / "fresh-project"
            preview = self._preview(target, direct=True)
            plan = preview["plan"]
            with self.assertRaisesRegex(ValueError, "exact reviewed plan"):
                construction.apply_cleanroom_mod_construction(
                    ROOT, plan, root / "state-a", consent_plan_id="wrong"
                )
            target.mkdir()
            (target / "later.txt").write_text("later", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                construction.apply_cleanroom_mod_construction(
                    ROOT, plan, root / "state-b", consent_plan_id=plan["id"]
                )
            self.assertEqual("later", (target / "later.txt").read_text(encoding="utf-8"))
            clean_target = root / "adapter-missing"
            request = construction.build_cleanroom_mod_request(clean_target)
            with self.assertRaisesRegex(ValueError, "profile render hook"):
                construction.preview_cleanroom_mod_construction(
                    ROOT, request, adapters=AdapterSet()
                )

    def test_partial_failure_restores_absent_target(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            target = root / "fresh-project"
            state_root = root / "state"
            plan = self._preview(target, direct=True)["plan"]
            result = construction.apply_cleanroom_mod_construction(
                ROOT,
                plan,
                state_root,
                consent_plan_id=plan["id"],
                fail_after_ordinal=7,
            )
            self.assertEqual("rejected", result["state"])
            self.assertEqual(
                "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
                result["application_receipt"]["diagnostic_code"],
            )
            self.assertEqual("restored", result["recovery"]["outcome"])
            self.assertFalse(target.exists())

    def test_recovery_restores_bootstrap_interrupted_before_transaction(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            target = root / "fresh-project"
            state_root = root / "state"
            plan = self._preview(target, direct=True)["plan"]
            fresh_project.prepare_fresh_target(
                target,
                plan["target_observation"],
                state_root,
                plan_id=plan["id"],
            )
            result = construction.recover_cleanroom_mod_construction(
                ROOT, plan, state_root
            )
            self.assertEqual("restored", result["state"])
            self.assertFalse(target.exists())

    def test_altered_plan_and_result_identities_reject(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            target = Path(temporary) / "fresh-project"
            preview = self._preview(target)
            changed = copy.deepcopy(preview["plan"])
            changed["operations"][0]["after_size"] += 1
            with self.assertRaisesRegex(ValueError, "identity|schema|rendered"):
                construction.validate_cleanroom_mod_plan(ROOT, changed)
            changed_result = copy.deepcopy(preview)
            changed_result["state"] = "applied"
            with self.assertRaisesRegex(ValueError, "identity"):
                construction.validate_cleanroom_mod_result(ROOT, changed_result)

    def test_profile_local_cli_preview_uses_same_public_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            target = Path(temporary) / "fresh-project"
            result = construction_cli.run(
                ["preview", os.fspath(target), "--suite-root", os.fspath(ROOT)]
            )
            self.assertEqual("ready", result["state"])
            self.assertEqual(construction.KIND_ID, result["plan"]["kind_id"])
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
