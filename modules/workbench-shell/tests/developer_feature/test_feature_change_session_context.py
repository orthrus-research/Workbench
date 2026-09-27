"""Failure-first tests for the selected Work Session feature-change context."""

from __future__ import annotations

from copy import deepcopy
from contextvars import copy_context
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import shutil
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
import workbench_core.session_owner_allocations as core_session_owner_allocations

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
        self.record_provider = CoreRecordStores(
            workspace=ROOT, configuration_home=self.root / "config",
            owner_id="workbench-shell",
        )
        self.record_scope = record_store_scope(self.record_provider)
        self.record_scope.__enter__()
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
        self.record_scope.__exit__(None, None, None)
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

    def _cli_start(self, record: Path, label: str) -> list[str]:
        request = self._request(label)
        arguments = [
            "material-fluid-recipe", "start",
            "--session-record", str(record),
            "--runtime-config", str(self.runtime_config),
        ]
        for key, value in request.items():
            arguments.extend(("--" + key.replace("_", "-"), str(value)))
        return [*arguments, "--json"]

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

    def test_failed_setup_retains_owner_and_refuses_unproven_retry(self) -> None:
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
        owner = self._context_root / "session-owners" / session_id
        self.assertTrue(owner.is_dir())
        allocation = self._context_root / "owner-allocations" / f"{session_id}.json"
        self.assertTrue(allocation.is_file())
        self.assertFalse(
            (self._context_root / "selected-context-v1.json").exists()
        )
        before = allocation.read_bytes()
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "no Core start binding"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config,
                **self._request("Thermal Solvent Retry"),
            )
        self.assertEqual(before, allocation.read_bytes())
        self.assertTrue(owner.is_dir())

    def test_legacy_context_stage_is_retained_and_blocks_new_setup(self) -> None:
        contexts = self.record_provider.open(
            "feature-change-session-context-v1", ROOT,
        ).root / "contexts"
        contexts.mkdir(mode=0o700, exist_ok=True)
        record, created = self._session("legacy-stage-directory")
        session_id = str(created["session_id"])
        stage = contexts / f".{session_id}.staging-{'a' * 32}"
        stage.mkdir(mode=0o700)
        payload = stage / "unknown-payload"
        payload.write_bytes(b"keep unknown stage bytes\n")
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "staging requires Core review"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config,
                **self._request("Thermal Solvent Legacy Stage Directory"),
            )
        self.assertEqual(b"keep unknown stage bytes\n", payload.read_bytes())
        self.assertFalse((self._context_root / "session-owners" / session_id).exists())

        if os.name != "posix":
            return  # POSIX and WSL can create this redirect without host privileges.
        record, created = self._session("legacy-stage-redirect")
        session_id = str(created["session_id"])
        foreign = self.root / "foreign-stage"
        foreign.mkdir()
        (foreign / "unknown-payload").write_bytes(b"foreign stays\n")
        redirect = contexts / f".{session_id}.staging-{'b' * 32}"
        redirect.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "staging requires Core review"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config,
                **self._request("Thermal Solvent Legacy Stage Redirect"),
            )
        self.assertTrue(redirect.is_symlink())
        self.assertEqual(b"foreign stays\n", (foreign / "unknown-payload").read_bytes())
        self.assertFalse((self._context_root / "session-owners" / session_id).exists())

    def test_completed_context_reader_ignores_retained_legacy_stage(self) -> None:
        context, created = self._bind("completedlegacy")
        stage = (
            self._context_root / "contexts"
            / f".{created['session_id']}.staging-{'c' * 32}"
        )
        stage.mkdir(mode=0o700)
        (stage / "unknown-payload").write_bytes(b"retain\n")
        self.assertEqual(
            context["context_id"],
            resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"],
        )
        record = feature_change_workspace._local_uri(
            context["session_record_uri"], "Work Session record",
        )
        reopened = bind_material_fluid_recipe_session_context(
            ROOT, record,
            self.runtime_config,
            **self._request("Thermal Solvent completedlegacy"),
        )
        self.assertEqual(context["context_id"], reopened["context_id"])
        self.assertEqual(b"retain\n", (stage / "unknown-payload").read_bytes())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_exit_after_started_binding_forward_completes_exact_setup(self) -> None:
        record, created = self._session("started-retry")
        session_id = str(created["session_id"])
        request = self._request("Thermal Solvent Started Retry")
        original = core_session_owner_allocations._HeldSessionOwner.record_started

        def exit_after_started(held: object, raw: bytes) -> object:
            result = original(held, raw)
            os._exit(71)
            return result

        child = os.fork()
        if child == 0:
            with patch.object(core_session_owner_allocations._HeldSessionOwner, "record_started", exit_after_started):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config, **request,
                )
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        owner = self._context_root / "session-owners" / session_id
        setup = self._context_root / "owner-allocations" / f"{session_id}.setup.json"
        started = self._context_root / "owner-allocations" / f"{session_id}.started.json"
        self.assertTrue(setup.is_file())
        self.assertTrue(started.is_file())
        self.assertFalse((self._context_root / "contexts" / session_id).exists())
        before = {
            path.relative_to(self._context_root): path.read_bytes()
            for path in (*owner.rglob("*"), setup, started) if path.is_file()
        }
        with patch.object(feature_change_workspace, "start_material_fluid_recipe_change", side_effect=AssertionError("domain start repeated")):
            context = bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config, **request,
            )
        self.assertEqual(session_id, context["session_id"])
        self.assertEqual(before, {
            path.relative_to(self._context_root): path.read_bytes()
            for path in (*owner.rglob("*"), setup, started) if path.is_file()
        })
        self.assertEqual(context["context_id"], resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"])

    def test_started_owner_refuses_changed_inputs_and_retains_bytes(self) -> None:
        record, created = self._session("changed-started-retry")
        session_id = str(created["session_id"])
        request = self._request("Thermal Solvent Changed Started Retry")
        with patch.object(self.tree_provider, "stage", side_effect=RuntimeError("stop after start")):
            with self.assertRaisesRegex(RuntimeError, "stop after start"):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config, **request,
                )
        setup = self._context_root / "owner-allocations" / f"{session_id}.setup.json"
        before = setup.read_bytes()
        changed = dict(request)
        changed["name"] = "Thermal Solvent Other Request"
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "setup intent differs"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config, **changed,
            )
        self.runtime_config.write_text(
            self.runtime_config.read_text(encoding="utf-8").replace(
                '"schema_version": 1', '"schema_version": 1, "note": "changed"',
            ), encoding="utf-8",
        )
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "setup intent differs"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config, **request,
            )
        self.assertEqual(before, setup.read_bytes())
        self.assertFalse((self._context_root / "contexts" / session_id).exists())

    def test_started_owner_refuses_changed_live_plan(self) -> None:
        record, created = self._session("changed-plan-retry")
        session_id = str(created["session_id"])
        request = self._request("Thermal Solvent Changed Plan Retry")
        with patch.object(self.tree_provider, "stage", side_effect=RuntimeError("stop after start")):
            with self.assertRaisesRegex(RuntimeError, "stop after start"):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config, **request,
                )
        workspace = Path(json.loads(record.read_text(encoding="utf-8"))["workspace"]["canonical_root"])
        dependency = workspace / "groovy/preInit/MaterialChanges.groovy"
        dependency.write_bytes(dependency.read_bytes() + b"\n// changed after start\n")
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "plan differs"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config, **request,
            )
        self.assertFalse((self._context_root / "contexts" / session_id).exists())

    def test_earlier_core_started_owner_reads_context_but_cannot_gain_retry(self) -> None:
        def remove_new_setup_witness(session_id: str) -> None:
            records = self._context_root / "owner-allocations"
            started = records / f"{session_id}.started.json"
            row = json.loads(started.read_text(encoding="utf-8"))
            for key in ("setup_device", "setup_inode", "setup_size", "setup_sha256"):
                row.pop(key)
            started.write_bytes(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                + b"\n"
            )
            (records / f"{session_id}.setup.json").unlink()

        completed, created = self._bind("earliercorereadable")
        remove_new_setup_witness(str(created["session_id"]))
        self.assertEqual(
            completed["context_id"],
            resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"],
        )

        record, interrupted = self._session("earlier-core-incomplete")
        request = self._request("Thermal Solvent Earlier Core")
        with patch.object(self.tree_provider, "stage", side_effect=RuntimeError("stop after start")):
            with self.assertRaisesRegex(RuntimeError, "stop after start"):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config, **request,
                )
        session_id = str(interrupted["session_id"])
        remove_new_setup_witness(session_id)
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "no retained setup intent"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config, **request,
            )
        self.assertFalse((self._context_root / "contexts" / session_id).exists())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_exit_after_owner_mkdir_retains_unclaimed_path_and_refuses_retry(self) -> None:
        record, created = self._session("owner-mkdir-crash")
        original_sync = core_session_owner_allocations.fsync_directory

        def exit_after_owner_mkdir(path: Path) -> None:
            if path.name == "session-owners":
                os._exit(71)
            original_sync(path)

        child = os.fork()
        if child == 0:
            with patch.object(core_session_owner_allocations, "fsync_directory", side_effect=exit_after_owner_mkdir):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config,
                    **self._request("Thermal Solvent Owner Crash"),
                )
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        session_id = str(created["session_id"])
        owner = self._context_root / "session-owners" / session_id
        self.assertTrue(owner.is_dir())
        self.assertFalse((owner / "core-owner-allocation-v1.json").exists())
        self.assertFalse((self._context_root / "owner-allocations" / f"{session_id}.json").exists())
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "already exists"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config,
                **self._request("Thermal Solvent Owner Crash"),
            )
        self.assertTrue(owner.is_dir())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX crash injection")
    def test_exit_after_owner_marker_retains_core_witness_without_record(self) -> None:
        record, created = self._session("owner-marker-crash")
        original_publish = core_session_owner_allocations.publish_immutable_bytes

        def exit_after_marker(path: Path, data: bytes, *, byte_limit: int) -> None:
            original_publish(path, data, byte_limit=byte_limit)
            if path.name == "core-owner-allocation-v1.json":
                os._exit(71)

        child = os.fork()
        if child == 0:
            with patch.object(core_session_owner_allocations, "publish_immutable_bytes", side_effect=exit_after_marker):
                bind_material_fluid_recipe_session_context(
                    ROOT, record, self.runtime_config,
                    **self._request("Thermal Solvent Marker Crash"),
                )
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        session_id = str(created["session_id"])
        owner = self._context_root / "session-owners" / session_id
        marker = owner / "core-owner-allocation-v1.json"
        self.assertTrue(marker.is_file())
        before = marker.read_bytes()
        self.assertFalse((self._context_root / "owner-allocations" / f"{session_id}.json").exists())
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "already exists"):
            bind_material_fluid_recipe_session_context(
                ROOT, record, self.runtime_config,
                **self._request("Thermal Solvent Marker Crash"),
            )
        self.assertEqual(before, marker.read_bytes())

    def test_cataloged_context_refuses_lost_owner_allocation(self) -> None:
        context, created = self._bind("lostallocation")
        session_id = str(created["session_id"])
        owner = self._context_root / "session-owners" / session_id
        allocation = self._context_root / "owner-allocations" / f"{session_id}.json"
        allocation.unlink()
        self.assertTrue((owner / "core-owner-allocation-v1.json").is_file())
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "Core review"):
            resolve_material_fluid_recipe_session_context(ROOT)
        with self.assertRaisesRegex(FeatureChangeWorkspaceError, "Core review"):
            select_material_fluid_recipe_session_context(ROOT, session_id)
        # Loss of the tree catalog row as well cannot make a marked owner legacy.
        legacy = CoreManagedTrees(
            workspace=ROOT, configuration_home=self.root / "lost-tree-config",
            locations={"artifacts": ROOT}, owner_id="workbench-shell",
        )
        with managed_trees_scope(legacy):
            with self.assertRaisesRegex(FeatureChangeWorkspaceError, "Core review"):
                resolve_material_fluid_recipe_session_context(ROOT)
        self.assertEqual(context["session_id"], session_id)
        self.assertTrue(allocation.parent.is_dir())

    def test_historical_v1_readback_without_any_core_witness(self) -> None:
        context, created = self._bind("legacyv1")
        session_id = str(created["session_id"])
        owner = self._context_root / "session-owners" / session_id
        # Model a fully validated pre-Core V1 context in this disposable
        # fixture. Total external witness loss is indistinguishable here and
        # remains a retention/cleanup gate rather than adoption authority.
        (owner / "core-owner-allocation-v1.json").unlink()
        (self._context_root / "owner-allocations" / f"{session_id}.json").unlink()
        (self._context_root / "owner-allocations" / f"{session_id}.setup.json").unlink()
        (self._context_root / "owner-allocations" / f"{session_id}.started.json").unlink()
        old_stage = self._context_root / "contexts" / f".{session_id}.staging-{'d' * 32}"
        old_stage.mkdir(mode=0o700)
        (old_stage / "unknown-payload").write_bytes(b"historical stage retained\n")
        legacy = CoreManagedTrees(
            workspace=ROOT, configuration_home=self.root / "legacy-v1-config",
            locations={"artifacts": ROOT}, owner_id="workbench-shell",
        )
        with managed_trees_scope(legacy):
            reopened = resolve_material_fluid_recipe_session_context(ROOT)[0]
        self.assertEqual(context["context_id"], reopened["context_id"])
        self.assertEqual(b"historical stage retained\n", (old_stage / "unknown-payload").read_bytes())

    def test_core_owner_rejects_same_byte_state_and_start_replacements(self) -> None:
        for label, target_name in (("statereplaced", "owner-state"), ("startreplaced", "start-result-v1.json")):
            with self.subTest(target_name=target_name):
                _, created = self._bind(label)
                owner = self._context_root / "session-owners" / str(created["session_id"])
                target = owner / target_name
                displaced = owner / (target_name + "-old")
                target.rename(displaced)
                if target.is_dir() or displaced.is_dir():
                    shutil.copytree(displaced, target)
                else:
                    target.write_bytes(displaced.read_bytes())
                    target.chmod(0o600)
                with self.assertRaisesRegex(FeatureChangeWorkspaceError, "Core review"):
                    resolve_material_fluid_recipe_session_context(ROOT)
                self.assertTrue(displaced.exists())

    def test_direct_cli_start_binds_session_owner_through_core(self) -> None:
        record, created = self._session("direct-cli")
        output, error = StringIO(), StringIO()
        code = change_main(
            self._cli_start(record, "Thermal Solvent Direct CLI"),
            root=ROOT, output=output, error=error,
            configuration_home=self.root / "config",
        )
        self.assertEqual(0, code, error.getvalue())
        context = json.loads(output.getvalue())
        self.assertEqual(created["session_id"], context["session_id"])
        owner = self._context_root / "session-owners" / str(created["session_id"])
        self.assertEqual((owner / "owner-state").as_uri(), context["state_root_uri"])
        self.assertEqual((owner / "start-result-v1.json").as_uri(), context["start_result_uri"])
        self.assertTrue((owner / "core-owner-allocation-v1.json").is_file())
        self.assertEqual(
            context["context_id"],
            resolve_material_fluid_recipe_session_context(ROOT)[0]["context_id"],
        )

    def test_external_dispatch_start_binds_suite_owner(self) -> None:
        record, created = self._session("external-start")
        external_workspace = self.root / "selected-project-start"
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
                    self._cli_start(record, "Thermal Solvent External Start"),
                    context=ExecutionContext(
                        external_workspace, self.root / "external-state",
                        configuration_home=self.root / "config",
                    ),
                )
        self.assertEqual(0, code, error.getvalue())
        context = json.loads(output.getvalue())
        self.assertEqual(created["session_id"], context["session_id"])
        owner = self._context_root / "session-owners" / str(created["session_id"])
        self.assertTrue((owner / "core-owner-allocation-v1.json").is_file())
        catalog = ResourceCatalog(self.root / "config")
        self.assertFalse(any(
            row["family"] == "feature-change-session-context-v1"
            for row in catalog.inventory(workspace=external_workspace)["record_stores"]
        ))

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
        catalog = ResourceCatalog(self.root / "config")
        suite_rows = catalog.inventory(workspace=ROOT)["record_stores"]
        self.assertEqual(
            [str(self._context_root)],
            [row["path"] for row in suite_rows if row["family"] == "feature-change-session-context-v1"],
        )
        self.assertFalse(any(
            row["family"] == "feature-change-session-context-v1"
            for row in catalog.inventory(workspace=external_workspace)["record_stores"]
        ))

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
