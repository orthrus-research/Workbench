"""Failure-first tests for the selected Work Session feature-change context."""

from __future__ import annotations

from copy import deepcopy
from contextvars import copy_context
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from test_developer_feature import ROOT, _checkout

import workbench_shell.feature_change_workspace as feature_change_workspace
from workbench_shell.feature_change_workspace import (
    FeatureChangeWorkspaceError,
    bind_material_fluid_recipe_session_context,
    close_material_fluid_recipe_session_context,
    read_feature_change_selected_context,
    resolve_material_fluid_recipe_session_context,
    select_material_fluid_recipe_session_context,
    validate_feature_change_selected_context,
    validate_feature_change_session_context,
)
from workbench_shell.work_session import SESSION_RECORD_NAME, WorkSessionStore
from workbench_shell.golden_journey_cli import change_main
from workbench_shell import commands
from workbench_api.modules import ExecutionContext
from workbench_core.host_services import install_local_host_services
from workbench_api.record_stores import record_store_scope
from workbench_core.storage.record_stores import CoreRecordStores
from workbench_core.storage.registered import ResourceCatalog
from workbench_api.managed_trees import managed_trees_scope
from workbench_core.managed_trees import CoreManagedTrees
import workbench_core.managed_trees as core_managed_trees

install_local_host_services()


SCHEMA_ROOT = ROOT / "modules/workbench-shell/schemas"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


class FeatureChangeSessionContextTests(unittest.TestCase):
    def test_core_registers_selection_namespace_at_historical_location(self) -> None:
        provider = CoreRecordStores(
            workspace=self.root, configuration_home=self.root / "config",
            owner_id="workbench-shell",
        )
        with record_store_scope(provider):
            selected = feature_change_workspace._session_context_root(ROOT)
        self.assertEqual(
            self.machine_state / "product-spine/feature-change-session-context-v1",
            selected,
        )
        rows = ResourceCatalog(self.root / "config").inventory(workspace=self.root)["record_stores"]
        self.assertEqual([str(selected)], [row["path"] for row in rows])
        self.assertEqual("available", rows[0]["status"])

    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.machine_state = self.root / "machine-state"
        self.tree_provider = CoreManagedTrees(
            workspace=ROOT, configuration_home=self.root / "config",
            locations={"artifacts": ROOT}, owner_id="workbench-shell",
        )
        self.tree_scope = managed_trees_scope(self.tree_provider)
        self.tree_scope.__enter__()
        self.environment = patch.dict(
            os.environ,
            {"WORKBENCH_STATE_ROOT": str(self.machine_state)},
            clear=False,
        )
        self.environment.start()
        self.runtime_config = self.root / "runtime-config.json"
        self.runtime_config.write_text(
            json.dumps(
                {
                    "format": (
                        "workbench-supersymmetry-installed-material-fluid-"
                        "runtime-config-v1"
                    ),
                    "schema_version": 1,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.environment.stop()
        self.tree_scope.__exit__(None, None, None)
        self.temporary.cleanup()

    @staticmethod
    def _request(name: str) -> dict[str, object]:
        return {
            "name": name,
            "color": "#Aa44Cc",
            "translation": f"{name} Localized",
            "symbol": name.replace(" ", "") + "X",
            "recipe_script": "groovy/postInit/chemistry/Probe.groovy",
            "recipe_map": "batch_reactor",
            "input_fluid": "steam",
            "input_amount": 750,
            "output_amount": 500,
            "duration": 320,
            "voltage_tier": "MV",
        }

    def _session(self, label: str) -> tuple[Path, dict[str, object]]:
        parent = self.root / label
        parent.mkdir()
        workspace = _checkout(parent)
        store = WorkSessionStore(self.root / "session-state")
        created = store.create(
            task={"task_id": f"task:{label}", "owner_id": "workbench-shell"},
            workspace={
                "identity_id": f"workspace:{label}",
                "canonical_root": str(workspace),
                "source_revision": "git:test",
                "dirty_fingerprint": None,
            },
            identities={
                "core_id": "workbench-shell:v2",
                "catalog_id": "workbench-command-catalog:v2",
                "host_adapter_id": "crucible-host-adapter:v3",
                "platform_profile_id": "cleanroom:provisional",
                "pack_profile_id": "workbench-pack:supersymmetry",
            },
            frontend={
                "frontend_id": f"frontend:{label}",
                "kind": "cli",
                "version": "test",
            },
        )
        record = store.base / created["session_id"] / SESSION_RECORD_NAME
        return record, created

    def _bind(self, label: str) -> tuple[dict[str, object], dict[str, object]]:
        record, created = self._session(label)
        context = bind_material_fluid_recipe_session_context(
            ROOT,
            record,
            self.runtime_config,
            **self._request(f"Thermal Solvent {label}"),
        )
        return context, created

    @property
    def _context_root(self) -> Path:
        return (
            self.machine_state
            / "product-spine"
            / "feature-change-session-context-v1"
        )

    def _rewrite_selection(self, value: dict[str, object]) -> None:
        body = deepcopy(value)
        body.pop("selection_id", None)
        value["selection_id"] = (
            feature_change_workspace.SELECTION_ID_PREFIX
            + sha256(feature_change_workspace._canonical(body)).hexdigest()
        )
        (self._context_root / "selected-context-v1.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_two_sessions_switch_close_and_preserve_immutable_contexts(self) -> None:
        first, first_created = self._bind("first")
        second, second_created = self._bind("second")
        self.assertNotEqual(first["context_id"], second["context_id"])
        self.assertEqual(
            second_created["session_id"],
            resolve_material_fluid_recipe_session_context(ROOT)[0]["session_id"],
        )
        first_path = (
            self._context_root
            / "contexts"
            / first_created["session_id"]
            / (
                "context-"
                + str(first["context_id"]).rsplit(":", 1)[1]
                + ".json"
            )
        )
        first_bytes = first_path.read_bytes()

        selected = select_material_fluid_recipe_session_context(
            ROOT, str(first_created["session_id"])
        )
        self.assertEqual("open", selected["state"])
        self.assertEqual(first["context_id"], selected["context_id"])
        self.assertEqual(first_bytes, first_path.read_bytes())
        closed = close_material_fluid_recipe_session_context(ROOT)
        self.assertEqual("closed", closed["state"])
        with self.assertRaisesRegex(
            FeatureChangeWorkspaceError, "currently open"
        ):
            resolve_material_fluid_recipe_session_context(ROOT)
        self.assertTrue(first_path.is_file())

        _validator(
            "workbench-feature-change-session-context-v1.schema.json"
        ).validate(validate_feature_change_session_context(first))
        _validator(
            "workbench-feature-change-selected-context-v1.schema.json"
        ).validate(validate_feature_change_selected_context(closed))

    def test_resealed_pointer_uri_and_stale_predecessor_fail_closed(self) -> None:
        first, first_created = self._bind("first")
        self._bind("second")
        select_material_fluid_recipe_session_context(
            ROOT, str(first_created["session_id"])
        )
        pointer_path = self._context_root / "selected-context-v1.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))

        duplicate = self.root / "copied-context.json"
        duplicate.write_bytes(Path(str(pointer["context_uri"])[7:]).read_bytes())
        forged_uri = deepcopy(pointer)
        forged_uri["context_uri"] = duplicate.as_uri()
        self._rewrite_selection(forged_uri)
        validate_feature_change_selected_context(forged_uri)
        with self.assertRaisesRegex(
            FeatureChangeWorkspaceError,
            "durably retained|outside its session owner",
        ):
            resolve_material_fluid_recipe_session_context(ROOT)

        self._rewrite_selection(pointer)
        stale = deepcopy(pointer)
        stale["previous_selection_sha256"] = "sha256:" + "0" * 64
        self._rewrite_selection(stale)
        validate_feature_change_selected_context(stale)
        with self.assertRaisesRegex(
            FeatureChangeWorkspaceError,
            "durably retained|predecessor digest is stale",
        ):
            read_feature_change_selected_context(ROOT)

        self._rewrite_selection(pointer)
        stale_context = deepcopy(pointer)
        stale_context["context_sha256"] = "sha256:" + "f" * 64
        self._rewrite_selection(stale_context)
        with self.assertRaisesRegex(
            FeatureChangeWorkspaceError,
            "durably retained|pointer is stale",
        ):
            resolve_material_fluid_recipe_session_context(ROOT)

    def test_failed_setup_cleans_partial_state_and_stale_crash_state_is_retryable(self) -> None:
        record, created = self._session("retry")
        session_id = str(created["session_id"])
        with patch.object(
            feature_change_workspace,
            "start_material_fluid_recipe_change",
            side_effect=FeatureChangeWorkspaceError("injected setup failure"),
        ):
            with self.assertRaisesRegex(
                FeatureChangeWorkspaceError, "injected setup failure"
            ):
                bind_material_fluid_recipe_session_context(
                    ROOT,
                    record,
                    self.runtime_config,
                    **self._request("Thermal Solvent Retry"),
                )
        self.assertFalse(
            (self._context_root / "contexts" / session_id).exists()
        )
        self.assertFalse(
            (self._context_root / "session-owners" / session_id).exists()
        )
        self.assertFalse(
            (self._context_root / "selected-context-v1.json").exists()
        )

        stale = self._context_root / "contexts" / f".{session_id}.staging-dead"
        stale.mkdir(parents=True)
        (stale / "partial.json").write_text("{}\n", encoding="utf-8")
        owner = self._context_root / "session-owners" / session_id
        owner.mkdir(parents=True)
        (owner / "partial").write_text("dead\n", encoding="utf-8")
        context = bind_material_fluid_recipe_session_context(
            ROOT,
            record,
            self.runtime_config,
            **self._request("Thermal Solvent Retry"),
        )
        self.assertEqual(session_id, context["session_id"])
        self.assertFalse(stale.exists())

    def test_retry_after_committed_context_before_selection_reuses_exact_owner(self) -> None:
        record, created = self._session("post-commit-retry")
        session_id = str(created["session_id"])
        request = self._request("Thermal Solvent Post Commit Retry")
        with patch.object(
            feature_change_workspace,
            "_publish_selection",
            side_effect=FeatureChangeWorkspaceError(
                "injected crash before selected-context publication"
            ),
        ):
            with self.assertRaisesRegex(
                FeatureChangeWorkspaceError, "before selected-context"
            ):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config, **request
                )
        context_directory = self._context_root / "contexts" / session_id
        owner_directory = self._context_root / "session-owners" / session_id
        self.assertTrue(context_directory.is_dir())
        self.assertTrue(owner_directory.is_dir())
        self.assertFalse(
            (self._context_root / "selected-context-v1.json").exists()
        )
        retained_before = {
            path.relative_to(self._context_root): path.read_bytes()
            for path in (*context_directory.rglob("*"), *owner_directory.rglob("*"))
            if path.is_file()
        }

        context = bind_material_fluid_recipe_session_context(
            ROOT, record, self.runtime_config, **request
        )
        self.assertEqual(session_id, context["session_id"])
        self.assertEqual(
            retained_before,
            {
                path.relative_to(self._context_root): path.read_bytes()
                for path in (
                    *context_directory.rglob("*"),
                    *owner_directory.rglob("*"),
                )
                if path.is_file()
            },
        )
        self.assertEqual(
            context["context_id"],
            resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"],
        )

    def test_context_tree_is_cataloged_and_historical_reader_remains_available(self) -> None:
        context, created = self._bind("cataloged")
        target = self._context_root / "contexts" / str(created["session_id"])
        selected = self.tree_provider.lookup_target("artifacts", target)
        self.assertEqual("committed", selected.status)
        self.assertEqual(
            f"workbench-feature-change-session-context-v1:{created['session_id']}",
            selected.domain_id,
        )
        reference = self.tree_provider.describe(selected.tree_id)
        self.assertEqual(target, reference.path)
        self.assertEqual("workbench-shell", reference.owner_id)
        self.assertEqual(1, len(reference.members))

        # V1 contexts made before Core cataloging still resolve through their
        # historical URI and content digest under a bound host.
        legacy = CoreManagedTrees(
            workspace=ROOT, configuration_home=self.root / "legacy-config",
            locations={"artifacts": ROOT}, owner_id="workbench-shell",
        )
        with managed_trees_scope(legacy):
            reopened = resolve_material_fluid_recipe_session_context(ROOT)[0]
        self.assertEqual(context["context_id"], reopened["context_id"])

    def test_external_dispatch_workspace_reopens_suite_tree_from_same_catalog(self) -> None:
        context, created = self._bind("externaldispatch")
        external_workspace = self.root / "selected-project"
        external_workspace.mkdir()
        foreign = CoreManagedTrees(
            workspace=external_workspace,
            configuration_home=self.root / "config",
            locations={"artifacts": external_workspace},
            owner_id="workbench-shell",
        )
        output, error = StringIO(), StringIO()
        original_change_main = change_main

        def routed_change_main(*args: object, **kwargs: object) -> int:
            return original_change_main(*args, **kwargs, output=output, error=error)

        with managed_trees_scope(foreign):
            with patch.object(commands, "ROOT", ROOT), patch(
                "workbench_shell.golden_journey_cli.change_main", side_effect=routed_change_main,
            ):
                code = commands.change(
                    ["material-fluid-recipe", "select-context", str(created["session_id"]), "--json"],
                    context=ExecutionContext(
                        external_workspace, self.root / "external-state",
                        configuration_home=self.root / "config",
                    ),
                )
        self.assertEqual(0, code, error.getvalue())
        self.assertEqual(context["context_id"], json.loads(output.getvalue())["context_id"])

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_exit_after_tree_intent_reconciles_before_context_reuse(self) -> None:
        record, created = self._session("intent-crash")
        request = self._request("Thermal Solvent Intent Crash")
        child = os.fork()
        if child == 0:
            with patch.object(core_managed_trees, "_rename_no_replace", side_effect=lambda *_a, **_k: os._exit(71)):
                bind_material_fluid_recipe_session_context(ROOT, record, self.runtime_config, **request)
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        target = self._context_root / "contexts" / str(created["session_id"])
        selected = self.tree_provider.lookup_target("artifacts", target)
        self.assertEqual("incomplete", selected.status)
        self.assertFalse(target.exists())
        context = bind_material_fluid_recipe_session_context(ROOT, record, self.runtime_config, **request)
        self.assertEqual("committed", self.tree_provider.lookup_target("artifacts", target).status)
        self.assertEqual(context["context_id"], resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"])

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_exit_after_tree_rename_commits_before_context_reuse(self) -> None:
        record, created = self._session("rename-crash")
        request = self._request("Thermal Solvent Rename Crash")
        target = self._context_root / "contexts" / str(created["session_id"])
        original_sync = core_managed_trees.fsync_directory

        def exit_after_rename(path: Path) -> None:
            if path == target.parent:
                os._exit(71)
            original_sync(path)

        child = os.fork()
        if child == 0:
            with patch.object(core_managed_trees, "fsync_directory", side_effect=exit_after_rename):
                bind_material_fluid_recipe_session_context(ROOT, record, self.runtime_config, **request)
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        self.assertTrue(target.is_dir())
        self.assertEqual("published-uncommitted", self.tree_provider.lookup_target("artifacts", target).status)
        context = bind_material_fluid_recipe_session_context(ROOT, record, self.runtime_config, **request)
        self.assertEqual("committed", self.tree_provider.lookup_target("artifacts", target).status)
        self.assertEqual(context["context_id"], resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"])

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_exit_after_tree_reservation_fails_closed_without_owner_adoption(self) -> None:
        record, created = self._session("reservation-crash")
        request = self._request("Thermal Solvent Reservation Crash")
        original = core_managed_trees._CoreTreeStage.__init__

        def exit_after_reservation(stage: object, *args: object, **kwargs: object) -> None:
            original(stage, *args, **kwargs)
            os._exit(71)

        child = os.fork()
        if child == 0:
            with patch.object(core_managed_trees._CoreTreeStage, "__init__", exit_after_reservation):
                bind_material_fluid_recipe_session_context(ROOT, record, self.runtime_config, **request)
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        target = self._context_root / "contexts" / str(created["session_id"])
        self.assertEqual("allocated", self.tree_provider.lookup_target("artifacts", target).status)
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "incomplete or different Core publication"):
            bind_material_fluid_recipe_session_context(ROOT, record, self.runtime_config, **request)
        self.assertFalse(target.exists())
        self.assertFalse((self._context_root / "selected-context-v1.json").exists())

    def test_core_context_reader_rejects_changed_or_foreign_target(self) -> None:
        context, created = self._bind("refusal")
        target = self._context_root / "contexts" / str(created["session_id"])
        foreign = CoreManagedTrees(
            workspace=self.root / "foreign", configuration_home=self.root / "config",
            locations={"artifacts": self.root / "foreign"}, owner_id="workbench-shell",
        )
        with managed_trees_scope(foreign):
            with self.assertRaisesRegex(FeatureChangeWorkspaceError, "custody"):
                resolve_material_fluid_recipe_session_context(ROOT)
        context_path = next(target.glob("context-*.json"))
        context_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "custody"):
            resolve_material_fluid_recipe_session_context(ROOT)

    def test_selection_lock_contention_rejects_second_writer(self) -> None:
        first, first_created = self._bind("first")
        self._bind("second")
        lock = self._context_root / "selection.lock"
        entered = threading.Event()
        release = threading.Event()
        failure: list[BaseException] = []
        original_replace = feature_change_workspace._replace_json

        def blocked_replace(path: Path, value: dict[str, object], **conditions: object) -> None:
            entered.set()
            if not release.wait(10):
                raise AssertionError("selection contention gate timed out")
            original_replace(path, value, **conditions)

        def first_writer() -> None:
            try:
                with patch.object(
                    feature_change_workspace, "_replace_json", blocked_replace
                ):
                    select_material_fluid_recipe_session_context(
                        ROOT, str(first_created["session_id"])
                    )
            except BaseException as exc:  # retained for the main test thread
                failure.append(exc)

        context = copy_context()
        thread = threading.Thread(target=lambda: context.run(first_writer))
        thread.start()
        self.assertTrue(entered.wait(10))
        self.assertTrue(lock.is_file())
        try:
            with self.assertRaisesRegex(
                FeatureChangeWorkspaceError, "held by another writer"
            ):
                close_material_fluid_recipe_session_context(ROOT)
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], failure)
        self.assertEqual(
            first["context_id"],
            resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"],
        )


if __name__ == "__main__":
    unittest.main()
