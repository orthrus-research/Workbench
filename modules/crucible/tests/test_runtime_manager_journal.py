from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_api.state_paths import default_runtime_state_root


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import workbench_core.storage.manager as manager  # noqa: E402
from workbench_core.storage import (  # noqa: E402
    RuntimeManagerError,
    execute_cleanup,
    execute_purge_trash,
    execute_runtime_create,
    inventory_storage,
    plan_cleanup,
    plan_purge_trash,
    plan_runtime_create,
    validate_operation_receipt,
)


NOW = datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)


def _write(path: Path, payload: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_mod(path: Path, mod_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mcmod.info",
            json.dumps([{"modid": mod_id, "name": mod_id, "version": "1"}]),
        )


def _profile(root: Path) -> None:
    fixture = root / "profiles/platforms/cleanroom/candidates/test/fixture"
    plan = fixture / "plan.groovy"
    _write(plan, "mods.worldStudio.profile = 'runtime-manager-journal-test'\n")
    profile = root / "profiles/packs/fixture/worldgen/worldgen-iteration-profile-v2.json"
    _write_json(
        profile,
        {
            "artifact": {
                "exclude_suffixes": ["-dev.jar", "-sources.jar"],
                "glob": ".workbench/build/libs/fixture-*.jar",
                "mod_id": "fixture_mod",
            },
            "cleanroom": "test",
            "defaults": {
                "debug_region": [0, 0, 1, 1],
                "diagnostic_sample_modulo": 1,
                "fast_region": [0, 0, 1, 1],
                "halo_chunks": 0,
                "heap": "512M",
                "seed": 1234,
            },
            "fixture": fixture.relative_to(root).as_posix(),
            "format": "workbench-worldgen-iteration-profile-v2",
            "minecraft_version": "1.12.2",
            "plan": plan.relative_to(root).as_posix(),
            "platform_profile_id": "workbench-platform:cleanroom:test",
            "profile_id": "workbench-pack:fixture:worldgen-journal-test",
            "runtime": {
                "discovery_roots": [".workbench/templates"],
                "passthrough_integrations": [],
                "required_mod_filename_tokens": ["RequiredMod"],
                "required_mod_ids": ["requiredmod"],
                "server_jar_glob": "cleanroom-test.jar",
            },
            "schema_version": 2,
            "toolchains": {
                "minimum_cleanroom_java_major": 25,
                "proven_cleanroom_java": "25.0.4",
                "proven_gradle": "9.6.1",
            },
            "world_type": "fixture_world",
        },
    )


def _runtime_template(root: Path) -> Path:
    template = root / ".workbench/templates/runtime-template"
    _write_mod(template / "cleanroom-test.jar", "cleanroom_server")
    _write_mod(template / "mods/RequiredMod.jar", "requiredmod")
    _write(template / "config/pack.cfg", "enabled=true\n")
    _write(template / "data-a.bin", b"a" * 8192)
    _write(template / "data-b.bin", b"b" * 8192)
    return template


def _operation_receipt_path(storage: Path, plan: dict[str, object]) -> Path:
    suffix = str(plan["plan_id"]).rsplit(":", 1)[-1]
    return storage / "runtime-manager/operations" / f"{suffix}.json"


def _load_receipt(storage: Path, plan: dict[str, object]) -> dict[str, object]:
    path = _operation_receipt_path(storage, plan)
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _assert_plan_action_lifecycle(
    case: unittest.TestCase,
    receipt: dict[str, object],
    plan: dict[str, object],
) -> None:
    receipt_actions = receipt["actions"]
    plan_actions = plan["actions"]
    case.assertEqual(
        [row["action_id"] for row in plan_actions],
        [row["action_id"] for row in receipt_actions],
    )
    case.assertEqual(
        [row["kind"] for row in plan_actions],
        [row["kind"] for row in receipt_actions],
    )


class RuntimeManagerJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        from workbench_crucible_worldgen_iteration import iteration
        provider = patch("workbench_core.storage.manager.runtime_provider", return_value=iteration)
        provider.start()
        self.addCleanup(provider.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Workbench"
        self.root.mkdir()
        self.storage = self.root / ".workbench"
        self.storage.mkdir()
        _profile(self.root)
        self.template = _runtime_template(self.root)

    def _runtime_plan(self, label: str) -> dict[str, object]:
        return plan_runtime_create(
            self.root,
            profile_name="fixture",
            label=label,
            runtime_template=self.template,
            now=NOW,
        )

    def _create_runtime(self, label: str) -> Path:
        plan = self._runtime_plan(label)
        execute_runtime_create(self.root, plan, now=NOW)
        return self.storage / f"fixtures/managed-runtime--fixture--{label}"

    def test_mutation_failure_leaves_one_stable_plan_bound_failed_receipt(self) -> None:
        plan = self._runtime_plan("injected-failure")

        def fail_during_provision(
            _template: Path, staging: Path, _profile_value: dict[str, object]
        ) -> None:
            _write(staging / "partial.bin", b"partial mutation")
            raise RuntimeManagerError("injected provision failure")

        with patch.object(manager, "provision_runtime", fail_during_provision):
            with self.assertRaisesRegex(RuntimeManagerError, "injected provision failure"):
                execute_runtime_create(self.root, plan, now=NOW)

        receipt_path = _operation_receipt_path(self.storage, plan)
        self.assertTrue(receipt_path.is_file())
        self.assertEqual(
            [receipt_path],
            sorted((self.storage / "runtime-manager/operations").glob("*.json")),
        )
        receipt = _load_receipt(self.storage, plan)
        validate_operation_receipt(receipt, plan=plan)
        self.assertEqual(plan["plan_id"], receipt["plan_id"])
        self.assertEqual("failed", receipt["status"])
        self.assertIsNotNone(receipt["completed_at"])
        self.assertIn("injected provision failure", receipt["error"]["message"])
        _assert_plan_action_lifecycle(self, receipt, plan)

        statuses = [row["status"] for row in receipt["actions"]]
        self.assertEqual(1, statuses.count("failed"))
        self.assertNotIn("running", statuses)
        failed_index = statuses.index("failed")
        self.assertEqual(["complete"] * failed_index, statuses[:failed_index])
        self.assertEqual(
            ["pending"] * (len(statuses) - failed_index - 1),
            statuses[failed_index + 1 :],
        )
        self.assertFalse(
            (self.storage / "fixtures/managed-runtime--fixture--injected-failure").exists()
        )
        self.assertFalse(any(self.storage.glob("fixtures/*.partial")))

    def test_success_rewrites_same_plan_receipt_with_all_actions_complete(self) -> None:
        plan = self._runtime_plan("complete")

        receipt = execute_runtime_create(self.root, plan, now=NOW)

        stored = _load_receipt(self.storage, plan)
        self.assertEqual(receipt, stored)
        validate_operation_receipt(stored, plan=plan)
        self.assertEqual("complete", stored["status"])
        self.assertIsNotNone(stored["completed_at"])
        self.assertIsNone(stored["error"])
        _assert_plan_action_lifecycle(self, stored, plan)
        self.assertTrue(stored["actions"])
        self.assertEqual(
            ["complete"] * len(stored["actions"]),
            [row["status"] for row in stored["actions"]],
        )
        for action in stored["actions"]:
            self.assertIsNotNone(action["started_at"])
            self.assertIsNotNone(action["completed_at"])
            self.assertIsNone(action["error"])

    def test_failed_purge_records_bytes_removed_before_the_failure(self) -> None:
        runtime = self._create_runtime("partial-purge")
        inventory = inventory_storage(self.root, now=NOW)
        runtime_item = next(
            row for row in inventory["items"] if row["path"] == str(runtime)
        )
        cleanup = plan_cleanup(
            self.root, selector=runtime_item["item_id"], now=NOW
        )
        execute_cleanup(self.root, cleanup, now=NOW)
        trashed = inventory_storage(self.root, now=NOW)
        trash_item = next(
            row
            for row in trashed["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path") == runtime_item["relative_path"]
                for reference in row["references"]
            )
        )
        purge = plan_purge_trash(
            self.root,
            selector=trash_item["item_id"],
            confirmation=trash_item["resource_id"],
            now=NOW,
        )

        real_unlink = os.unlink
        unlink_calls = 0

        def fail_second_unlink(path: str, *args: object, **kwargs: object) -> None:
            nonlocal unlink_calls
            unlink_calls += 1
            if unlink_calls == 2:
                raise OSError("injected purge interruption")
            real_unlink(path, *args, **kwargs)

        with patch.object(manager.os, "unlink", fail_second_unlink):
            with self.assertRaisesRegex(RuntimeManagerError, "injected purge interruption"):
                execute_purge_trash(self.root, purge, now=NOW)

        self.assertGreaterEqual(unlink_calls, 2)
        receipt = _load_receipt(self.storage, purge)
        validate_operation_receipt(receipt, plan=purge)
        self.assertEqual("failed", receipt["status"])
        self.assertEqual(["failed"], [row["status"] for row in receipt["actions"]])
        self.assertGreater(receipt["actions"][0]["bytes_processed"], 0)
        self.assertEqual(0, receipt["result"]["purged_bytes"])
        self.assertTrue(Path(trash_item["path"]).exists())


class WorkbenchRuntimeManagerRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        module_path = ROOT / "tools/workbench.py"
        spec = importlib.util.spec_from_file_location(
            "workbench_router_journal_test", module_path
        )
        if spec is None or spec.loader is None:
            raise AssertionError("could not load tools/workbench.py")
        cls.router = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.router)

    def test_top_level_router_delegates_runtime_storage_and_world_commands(self) -> None:
        from workbench_core.storage import cli

        commands = (
            ["runtime", "create", "--profile", "fixture", "--label", "one"],
            ["storage", "list"],
            ["world", "snapshot", "item-id", "--label", "one"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "XDG_CONFIG_HOME": str(Path(temporary) / "config"),
                "XDG_STATE_HOME": str(Path(temporary) / "state"),
            }
            expected_state = default_runtime_state_root(
                self.router.ROOT, environment=environment
            )
            for command in commands:
                with self.subTest(command=command[0]):
                    with (
                        patch.dict(os.environ, environment, clear=True),
                        patch(
                            "workbench_core.dispatch_setup._activate_user_setup",
                            return_value=True,
                        ),
                        patch.object(cli, "main", return_value=17) as delegated,
                    ):
                        self.assertEqual(17, self.router.main(command))
                    delegated.assert_called_once_with(
                        command, root=self.router.ROOT, workspace_root=expected_state
                    )

    def test_top_level_router_keeps_installed_runtime_custody_outside_suite(self) -> None:
        from workbench_core.storage import cli

        with tempfile.TemporaryDirectory() as temporary:
            selected = Path(temporary) / "external state"
            selected.mkdir()
            command = ["runtime", "create", "--profile", "fixture", "--label", "one"]
            with (
                patch.dict(
                    os.environ,
                    {"WORKBENCH_STATE_ROOT": str(selected)},
                    clear=True,
                ),
                patch.object(cli, "main", return_value=17) as delegated,
            ):
                self.assertEqual(17, self.router.main(command))
            delegated.assert_called_once_with(
                command,
                root=self.router.ROOT,
                workspace_root=selected,
            )


if __name__ == "__main__":
    unittest.main()
