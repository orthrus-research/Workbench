"""Failure-first tests for the selected Work Session feature-change context."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
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


SCHEMA_ROOT = ROOT / "modules/workbench-shell/schemas"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


class FeatureChangeSessionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.machine_state = self.root / "machine-state"
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

    def test_selection_lock_contention_rejects_second_writer(self) -> None:
        first, first_created = self._bind("first")
        self._bind("second")
        lock = self._context_root / "selection.lock"
        entered = threading.Event()
        release = threading.Event()
        failure: list[BaseException] = []
        original_replace = feature_change_workspace._replace_json

        def blocked_replace(path: Path, value: dict[str, object]) -> None:
            entered.set()
            if not release.wait(10):
                raise AssertionError("selection contention gate timed out")
            original_replace(path, value)

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

        thread = threading.Thread(target=first_writer)
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
