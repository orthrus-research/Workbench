"""Named workspace choices stay in Core across Textual sessions."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from textual.widgets import Button, Checkbox, Input, Select

from workbench_tui.app import (
    WorkbenchApp, WorkspaceChoicesScreen, WorkspaceRegisterScreen,
    EnvironmentImportScreen,
)
from workbench_tui.core_client import CoreClient, CoreClientError


def _record(java: str | None = None, *, revision: str = "before", feature: int | None = None) -> dict:
    version = 3 if feature is not None else 2
    extra_alpha = {"managed_java_feature": None} if version == 3 else {}
    extra_beta = {"managed_java_feature": feature} if version == 3 else {}
    return {
        "format": f"workbench-user-workspaces-v{version}", "schema_version": version,
        "record_id": revision, "default": "alpha",
        "entries": [
            {"name": "alpha", "path": "/home/user/alpha", "workspace_id": "alpha-id",
             "profile_config": "/profiles/alpha.toml", "java_home": "/jdk/alpha", **extra_alpha},
            {"name": "beta", "path": "/home/user/beta", "workspace_id": "beta-id",
             "profile_config": "/profiles/beta.toml", "java_home": java, **extra_beta},
        ],
    }


def _core() -> Mock:
    core = Mock(spec=CoreClient)
    core.version = AsyncMock(return_value={"component_id": "workbench-core", "version": "test"})
    core.environment_resolve = AsyncMock(return_value={
        "format": "workbench-environment-resolution-v1",
        "resolution_id": "workbench-environment-resolution:sha256:" + "a" * 64,
        "workspace": {"path": "/home/user/alpha", "source": "user-workspaces"},
    })
    core.setup_check = AsyncMock(return_value={"format": "workbench-setup-check-v2", "dependencies": []})
    core.modules = AsyncMock(return_value=[])
    core.profiles = AsyncMock(return_value=[])
    core.catalog = AsyncMock(return_value={"commands": []})
    core.workspace_home = AsyncMock(return_value={"workspace": {}, "status": {}})
    core.workspace_choices = AsyncMock(return_value=_record())
    core.java_inventory = AsyncMock(return_value={
        "format": "workbench-java-inventory-v1", "candidates": [
            {"state": "available", "jdk": True, "feature_version": 25,
             "compatibility": "matches-profile", "probe": {"java_home": "/jdk/beta-25"}},
        ],
    })
    core.save_workspace_choice = AsyncMock(return_value=_record("/jdk/beta-25", revision="after"))
    core.acquire_workspace_java = AsyncMock(return_value={
        "format": "workbench-java-runtime-result-v2", "source": "managed", "outcome": "reused",
        "receipt": {"policy": {"feature_version": 8},
                    "target": {"java_home_uri": "file:///state/jdks/temurin-8"}},
    })
    core.export_environment_share = AsyncMock(return_value={
        "format": "workbench-environment-share-export-v1",
        "share": {"share_id": "share-id"},
        "resource": {"path": "/exports/share.json"},
    })
    core.plan_environment_import = AsyncMock(return_value={
        "format": "workbench-environment-import-plan-v1",
        "plan_id": "plan-id", "state": "ready", "action": "create",
        "blockers": [], "unresolved_inputs": ["managed-java-archive"],
    })
    core.import_environment_share = AsyncMock(return_value={
        "format": "workbench-environment-import-result-v1",
        "plan_id": "plan-id", "outcome": "bound",
        "resource": {"path": "/evidence/import.json"},
        "unresolved_inputs": ["managed-java-archive"],
    })
    return core


class WorkspaceChoiceClientTests(IsolatedAsyncioTestCase):
    async def test_client_registers_workspace_with_exact_revision(self) -> None:
        client = CoreClient(("workbench",))
        registered = _record(revision="after")
        registered["entries"].append({
            "name": "gamma", "path": "/home/user/gamma", "workspace_id": "gamma-id",
            "profile_config": None, "java_home": None,
        })
        registered["default"] = "gamma"
        client.json_record = AsyncMock(return_value=registered)
        self.assertEqual(registered, await client.register_workspace(
            "gamma", "/home/user/gamma", make_default=True,
            expected_record_id="before",
        ))
        client.json_record.assert_awaited_with(
            "settings", "workspace", "add", "gamma", "/home/user/gamma",
            "--default", "--expected-record-id", "before", "--json",
        )

    async def test_client_passes_reviewed_revision_and_clear_choice(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value=_record(revision="after"))
        await client.workspace_choices()
        client.json_record.assert_awaited_with("settings", "workspace", "list", "--json")
        await client.save_workspace_choice(
            "beta", profile_config="/profiles/beta.toml", java_home=None,
            expected_record_id="before",
        )
        client.json_record.assert_awaited_with(
            "settings", "workspace", "select", "beta", "--profile-config",
            "/profiles/beta.toml", "--clear-java", "--expected-record-id",
            "before", "--json",
        )

    async def test_client_selects_managed_java_8_without_a_path(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value=_record(revision="after", feature=8))
        await client.save_workspace_choice(
            "beta", profile_config="/profiles/beta.toml", java_home=None,
            managed_java_feature=8, expected_record_id="before",
        )
        client.json_record.assert_awaited_with(
            "settings", "workspace", "select", "beta", "--profile-config",
            "/profiles/beta.toml", "--java-feature", "8", "--expected-record-id",
            "before", "--json",
        )

    async def test_client_acquires_saved_java_with_reviewed_revision(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value={
            "format": "workbench-java-runtime-result-v2", "source": "managed", "outcome": "reused",
            "receipt": {"policy": {"feature_version": 8},
                        "target": {"java_home_uri": "file:///state/jdks/temurin-8"}},
        })
        await client.acquire_workspace_java("beta", expected_record_id="after")
        client.json_record.assert_awaited_with(
            "settings", "workspace", "acquire", "beta", "--expected-record-id", "after",
            "--json", timeout=600,
        )

    async def test_client_exports_and_imports_with_exact_core_plan(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(side_effect=[
            {"format": "workbench-environment-share-export-v1",
             "share": {"share_id": "share-id"}, "resource": {"path": "/share.json"}},
            {"format": "workbench-environment-import-plan-v1", "state": "ready",
             "plan_id": "plan-id", "blockers": [], "unresolved_inputs": []},
            {"format": "workbench-environment-import-result-v1", "outcome": "bound",
             "plan_id": "plan-id", "resource": {"path": "/receipt.json"},
             "unresolved_inputs": []},
        ])
        await client.export_environment_share("beta")
        client.json_record.assert_any_await(
            "settings", "environment", "export", "beta", "--json"
        )
        await client.plan_environment_import(
            "/share.json", "shared", "/workspace", config="/wb.toml",
        )
        client.json_record.assert_any_await(
            "settings", "environment", "plan", "/share.json",
            "--name", "shared", "--workspace", "/workspace",
            "--config", "/wb.toml", "--json", allowed_exit=(0, 1),
        )
        await client.import_environment_share(
            "/share.json", "shared", "/workspace", expected_plan_id="plan-id",
            config="/wb.toml",
        )
        client.json_record.assert_awaited_with(
            "settings", "environment", "import", "/share.json",
            "--name", "shared", "--workspace", "/workspace",
            "--config", "/wb.toml", "--plan-id", "plan-id", "--json",
        )

    async def test_client_rejects_unexpected_import_plan_identity(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value={
            "format": "workbench-environment-import-result-v1", "outcome": "bound",
            "plan_id": "another-plan", "resource": {"path": "/receipt.json"},
            "unresolved_inputs": [],
        })
        with self.assertRaisesRegex(CoreClientError, "exact environment import result"):
            await client.import_environment_share(
                "/share.json", "shared", "/workspace", expected_plan_id="plan-id",
            )

    async def test_client_passes_managed_acquisition_only_for_reviewed_choice(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(side_effect=[
            {"format": "workbench-environment-import-plan-v2", "state": "ready",
             "plan_id": "with-java", "acquire_managed_java": True,
             "blockers": [], "unresolved_inputs": ["managed-java-archive"]},
            {"format": "workbench-environment-import-result-v2", "outcome": "bound",
             "plan_id": "with-java", "resource": {"path": "/receipt.json"},
             "unresolved_inputs": [], "managed_java": {"runtime_id": "test", "outcome": "reused"}},
        ])
        await client.plan_environment_import(
            "/share.json", "shared", "/workspace", acquire_managed_java=True,
        )
        client.json_record.assert_awaited_with(
            "settings", "environment", "plan", "/share.json",
            "--name", "shared", "--workspace", "/workspace",
            "--acquire-managed-java", "--json", allowed_exit=(0, 1),
        )
        await client.import_environment_share(
            "/share.json", "shared", "/workspace", expected_plan_id="with-java",
            acquire_managed_java=True,
        )
        client.json_record.assert_awaited_with(
            "settings", "environment", "import", "/share.json",
            "--name", "shared", "--workspace", "/workspace",
            "--acquire-managed-java", "--plan-id", "with-java", "--json", timeout=600,
        )

    async def test_client_accepts_reviewed_v2_source_lock_share(self) -> None:
        client = CoreClient(("workbench",))
        source_lock = {
            "relative_path": "profiles/packs/example/source-lock.json",
            "sha256": "sha256:" + "a" * 64,
            "repository": "https://example.com/pack.git",
            "revision": "b" * 40, "tree": "c" * 40,
        }
        client.json_record = AsyncMock(side_effect=[
            {"format": "workbench-environment-share-export-v1",
             "share": {"format": "workbench-environment-share-v2", "share_id": "share-id",
                       "lock": {"project_source_lock": source_lock}},
             "resource": {"path": "/share.json"}},
            {"format": "workbench-environment-import-plan-v3", "state": "ready",
             "plan_id": "bound-plan", "project_source_lock": source_lock,
             "blockers": [], "unresolved_inputs": ["workspace-project-bytes"]},
            {"format": "workbench-environment-import-result-v3", "outcome": "bound",
             "plan_id": "bound-plan", "project_source_lock": source_lock,
             "resource": {"path": "/receipt.json"},
             "unresolved_inputs": ["workspace-project-bytes"]},
        ])
        await client.export_environment_share("beta", bind_project_source_lock=True)
        client.json_record.assert_any_await(
            "settings", "environment", "export", "beta", "--bind-project-source-lock", "--json",
        )
        plan = await client.plan_environment_import("/share.json", "shared", "/workspace")
        self.assertEqual(source_lock, plan["project_source_lock"])
        result = await client.import_environment_share(
            "/share.json", "shared", "/workspace", expected_plan_id="bound-plan",
        )
        self.assertEqual(source_lock, result["project_source_lock"])

    async def test_client_accepts_v3_managed_tool_lock_and_v4_review(self) -> None:
        client = CoreClient(("workbench",))
        source_lock = {
            "relative_path": "profiles/packs/example/source-lock.json",
            "sha256": "sha256:" + "a" * 64,
            "repository": "https://example.com/pack.git",
            "revision": "b" * 40, "tree": "c" * 40,
        }
        tool_lock = {
            "format": "workbench-managed-tool-policy-lock-v1",
            "lock_id": "workbench-managed-tool-policy:sha256:" + "d" * 64,
            "host_variant": {"os": "linux", "architecture": "x64"},
            "assets": {"prism": {}, "go": {}, "packwiz": {}},
        }
        client.json_record = AsyncMock(side_effect=[
            {"format": "workbench-environment-share-export-v1",
             "share": {"format": "workbench-environment-share-v3", "share_id": "share-id",
                       "lock": {"project_source_lock": source_lock,
                                "managed_tool_lock": tool_lock}},
             "resource": {"path": "/share.json"}},
            {"format": "workbench-environment-import-plan-v4", "state": "ready",
             "plan_id": "bound-plan", "project_source_lock": source_lock,
             "managed_tool_lock": tool_lock,
             "blockers": [], "unresolved_inputs": ["managed-tool-bytes"]},
            {"format": "workbench-environment-import-result-v4", "outcome": "bound",
             "plan_id": "bound-plan", "project_source_lock": source_lock,
             "managed_tool_lock": tool_lock,
             "resource": {"path": "/receipt.json"},
             "unresolved_inputs": ["managed-tool-bytes"]},
        ])
        await client.export_environment_share(
            "beta", bind_project_source_lock=True, bind_managed_tools=True,
        )
        client.json_record.assert_any_await(
            "settings", "environment", "export", "beta", "--bind-project-source-lock",
            "--bind-managed-tools", "--json",
        )
        plan = await client.plan_environment_import("/share.json", "shared", "/workspace")
        self.assertEqual(tool_lock, plan["managed_tool_lock"])
        result = await client.import_environment_share(
            "/share.json", "shared", "/workspace", expected_plan_id="bound-plan",
        )
        self.assertEqual(tool_lock, result["managed_tool_lock"])
        with self.assertRaisesRegex(CoreClientError, "requires a project source lock"):
            await client.export_environment_share("beta", bind_managed_tools=True)

    async def test_client_rejects_v4_without_managed_tool_lock(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value={
            "format": "workbench-environment-import-plan-v4", "state": "ready",
            "plan_id": "bound-plan", "blockers": [], "unresolved_inputs": [],
        })
        with self.assertRaisesRegex(CoreClientError, "compatible environment import plan"):
            await client.plan_environment_import("/share.json", "shared", "/workspace")


class WorkspaceChoiceScreenTests(IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(40):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

    async def test_empty_choices_can_register_a_workspace_in_textual(self) -> None:
        core = _core()
        empty = {"format": "workbench-user-workspaces-v2", "schema_version": 2,
                 "record_id": "before", "default": None, "entries": []}
        saved = {"format": "workbench-user-workspaces-v2", "schema_version": 2,
                 "record_id": "after", "default": "susy-dev", "entries": [
                     {"name": "susy-dev", "path": "/home/user/Supersymmetry",
                      "workspace_id": "workspace-id", "profile_config": None,
                      "java_home": None},
                 ]}
        core.workspace_choices = AsyncMock(return_value=empty)
        core.register_workspace = AsyncMock(return_value=saved)
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            app.open_workspace_choices()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen))
            choices = app.screen
            self.assertTrue(choices.query_one("#choice-save", Button).disabled)
            choices.query_one("#choice-register", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceRegisterScreen))
            form = app.screen
            form.query_one("#workspace-register-name", Input).value = "susy-dev"
            form.query_one("#workspace-register-path", Input).value = "/home/user/Supersymmetry"
            form.query_one("#workspace-register-save", Button).press()
            await self._settle(pilot, lambda: app.screen is choices)
            core.register_workspace.assert_awaited_once_with(
                "susy-dev", "/home/user/Supersymmetry", make_default=True,
                expected_record_id="before",
            )
            self.assertEqual("susy-dev", choices.query_one("#choice-workspace", Select).value)
            self.assertFalse(choices.query_one("#choice-save", Button).disabled)

    async def test_two_workspaces_save_independent_choices_and_reopen(self) -> None:
        core = _core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_workspace_choices()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-java"))
                               and app.screen.query_one("#choice-java", Input).value == "/jdk/alpha")
            screen = app.screen
            self.assertEqual("/jdk/alpha", screen.query_one("#choice-java", Input).value)
            screen.query_one("#choice-workspace", Select).value = "beta"
            await self._settle(pilot, lambda: screen.query_one("#choice-profile", Input).value == "/profiles/beta.toml")
            self.assertEqual("", screen.query_one("#choice-java", Input).value)
            screen.query_one("#choice-find-java", Button).press()
            await self._settle(pilot, lambda: len(screen.query_one("#choice-java-candidates", Select)._options) == 2)
            screen.query_one("#choice-java-candidates", Select).value = "/jdk/beta-25"
            await self._settle(pilot, lambda: screen.query_one("#choice-java", Input).value == "/jdk/beta-25")
            screen.query_one("#choice-save", Button).press()
            await self._settle(pilot, lambda: core.save_workspace_choice.await_count == 1)
            core.save_workspace_choice.assert_awaited_with(
                "beta", profile_config="/profiles/beta.toml", java_home="/jdk/beta-25",
                managed_java_feature=None, expected_record_id="before",
            )
            self.assertEqual("/jdk/alpha", screen.entries["alpha"]["java_home"])
            await self._settle(pilot, lambda: screen.record["record_id"] == "after")
            screen.query_one("#choice-back", Button).press()
            await self._settle(pilot, lambda: app.screen is not screen)
            core.workspace_choices.return_value = _record("/jdk/beta-25", revision="after")
            app.open_workspace_choices()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen))
            reopened = app.screen
            reopened.query_one("#choice-workspace", Select).value = "beta"
            await self._settle(pilot, lambda: reopened.query_one("#choice-java", Input).value == "/jdk/beta-25")

    async def test_managed_java_8_is_saved_without_inventory_or_path(self) -> None:
        core = _core()
        core.save_workspace_choice = AsyncMock(return_value=_record(revision="after", feature=8))
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            app.push_screen(WorkspaceChoicesScreen(_record()))
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-workspace")))
            screen = app.screen
            screen.query_one("#choice-workspace", Select).value = "beta"
            await self._settle(pilot, lambda: screen.query_one("#choice-profile", Input).value == "/profiles/beta.toml")
            screen.query_one("#choice-java-mode", Select).value = "managed-8"
            screen.query_one("#choice-save", Button).press()
            await self._settle(pilot, lambda: core.save_workspace_choice.await_count == 1)
            core.save_workspace_choice.assert_awaited_with(
                "beta", profile_config="/profiles/beta.toml", java_home=None,
                managed_java_feature=8, expected_record_id="before",
            )
            core.java_inventory.assert_not_awaited()
            await self._settle(pilot, lambda: screen.record["record_id"] == "after")
            self.assertEqual(8, screen.entries["beta"]["managed_java_feature"])
            screen.query_one("#choice-acquire", Button).press()
            await self._settle(pilot, lambda: core.acquire_workspace_java.await_count == 1)
            core.acquire_workspace_java.assert_awaited_with("beta", expected_record_id="after")

    async def test_manual_java_path_saves_without_inventory(self) -> None:
        core = _core()
        core.save_workspace_choice = AsyncMock(return_value=_record("/custom/jdk", revision="after"))
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            app.push_screen(WorkspaceChoicesScreen(_record()))
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-workspace")))
            screen = app.screen
            screen.query_one("#choice-workspace", Select).value = "beta"
            await self._settle(pilot, lambda: screen.query_one("#choice-profile", Input).value == "/profiles/beta.toml")
            screen.query_one("#choice-java-mode", Select).value = "path"
            screen.query_one("#choice-java", Input).value = "/custom/jdk"
            screen.query_one("#choice-save", Button).press()
            await self._settle(pilot, lambda: core.save_workspace_choice.await_count == 1)
            core.save_workspace_choice.assert_awaited_with(
                "beta", profile_config="/profiles/beta.toml", java_home="/custom/jdk",
                managed_java_feature=None, expected_record_id="before",
            )
            core.java_inventory.assert_not_awaited()

    async def test_stale_revision_remains_visible(self) -> None:
        core = _core()
        core.save_workspace_choice = AsyncMock(side_effect=CoreClientError("changed after review"))
        app = WorkbenchApp(core)
        async with app.run_test(size=(100, 35)) as pilot:
            app.push_screen(WorkspaceChoicesScreen(_record()))
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-save")))
            screen = app.screen
            screen.query_one("#choice-save", Button).press()
            await self._settle(pilot, lambda: core.save_workspace_choice.await_count == 1)
            await self._settle(pilot, lambda: "changed after review" in str(screen.query_one("#choice-status").render()))
            self.assertEqual("before", screen.record["record_id"])

    async def test_export_uses_saved_workspace_choice(self) -> None:
        core = _core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            app.push_screen(WorkspaceChoicesScreen(_record()))
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-export")))
            screen = app.screen
            screen.query_one("#choice-workspace", Select).value = "beta"
            await self._settle(pilot, lambda: screen.selected_name == "beta")
            screen.query_one("#choice-export", Button).press()
            await self._settle(pilot, lambda: core.export_environment_share.await_count == 1)
            core.export_environment_share.assert_awaited_with("beta")
            await self._settle(pilot, lambda: "/exports/share.json" in str(
                screen.query_one("#choice-status").render()
            ))

    async def test_export_can_bind_selected_project_source_lock(self) -> None:
        core = _core()
        core.export_environment_share.return_value = {
            "share": {"share_id": "bound-share", "lock": {
                "project_source_lock": {"sha256": "sha256:" + "a" * 64},
            }},
            "resource": {"path": "/exports/bound-share.json"},
        }
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            app.push_screen(WorkspaceChoicesScreen(_record()))
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-bind-source-lock")))
            screen = app.screen
            screen.query_one("#choice-bind-source-lock", Checkbox).value = True
            screen.query_one("#choice-export", Button).press()
            await self._settle(pilot, lambda: core.export_environment_share.await_count == 1)
            core.export_environment_share.assert_awaited_with(
                "alpha", bind_project_source_lock=True,
            )
            await self._settle(pilot, lambda: "Project source lock:" in str(
                screen.query_one("#choice-status").render()
            ))

    async def test_export_opt_in_managed_tools_also_binds_source_lock(self) -> None:
        core = _core()
        core.export_environment_share.return_value = {
            "share": {"share_id": "tool-share", "lock": {
                "project_source_lock": {"sha256": "sha256:" + "a" * 64},
                "managed_tool_lock": {"lock_id": "tool-policy-id"},
            }},
            "resource": {"path": "/exports/tool-share.json"},
        }
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 38)) as pilot:
            app.push_screen(WorkspaceChoicesScreen(_record()))
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and bool(app.screen.query("#choice-bind-managed-tools")))
            screen = app.screen
            screen.query_one("#choice-bind-managed-tools", Checkbox).value = True
            screen.query_one("#choice-export", Button).press()
            await self._settle(pilot, lambda: core.export_environment_share.await_count == 1)
            core.export_environment_share.assert_awaited_with(
                "alpha", bind_project_source_lock=True, bind_managed_tools=True,
            )
            await self._settle(pilot, lambda: "tool-policy-id" in str(
                screen.query_one("#choice-status").render()
            ))

    async def test_import_requires_current_ready_plan_and_review(self) -> None:
        core = _core()
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 44)) as pilot:
            app.push_screen(EnvironmentImportScreen())
            await self._settle(pilot, lambda: isinstance(app.screen, EnvironmentImportScreen)
                               and bool(app.screen.query("#import-share")))
            screen = app.screen
            screen.query_one("#import-share", Input).value = "/share.json"
            screen.query_one("#import-name", Input).value = "shared"
            screen.query_one("#import-workspace", Input).value = "/workspace"
            screen.query_one("#import-plan", Button).press()
            await self._settle(pilot, lambda: screen.plan is not None)
            core.plan_environment_import.assert_awaited_with(
                "/share.json", "shared", "/workspace", config="", java_home="",
            )
            self.assertFalse(screen.query_one("#import-apply", Button).disabled)
            screen.query_one("#import-config", Input).value = "/matching.toml"
            await self._settle(pilot, lambda: screen.plan is None)
            self.assertTrue(screen.query_one("#import-apply", Button).disabled)
            screen.query_one("#import-plan", Button).press()
            await self._settle(pilot, lambda: core.plan_environment_import.await_count == 2
                               and screen.plan is not None)
            screen.query_one("#import-apply", Button).press()
            await self._settle(pilot, lambda: bool(app.screen.query("#review-confirm")))
            self.assertEqual(0, core.import_environment_share.await_count)
            app.screen.query_one("#review-confirm", Button).press()
            await self._settle(pilot, lambda: core.import_environment_share.await_count == 1)
            core.import_environment_share.assert_awaited_with(
                "/share.json", "shared", "/workspace",
                expected_plan_id="plan-id", config="/matching.toml", java_home="",
            )
            await self._settle(pilot, lambda: screen.plan is None)

    async def test_import_reviews_v2_project_source_lock(self) -> None:
        core = _core()
        source_lock = {"sha256": "sha256:" + "a" * 64, "revision": "b" * 40}
        core.plan_environment_import.return_value = {
            "format": "workbench-environment-import-plan-v3", "state": "ready",
            "plan_id": "bound-plan", "action": "create", "blockers": [],
            "unresolved_inputs": ["workspace-project-bytes"],
            "project_source_lock": source_lock,
        }
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 44)) as pilot:
            app.push_screen(EnvironmentImportScreen())
            await self._settle(pilot, lambda: isinstance(app.screen, EnvironmentImportScreen)
                               and bool(app.screen.query("#import-share")))
            screen = app.screen
            screen.query_one("#import-share", Input).value = "/share.json"
            screen.query_one("#import-name", Input).value = "shared"
            screen.query_one("#import-workspace", Input).value = "/workspace"
            screen.query_one("#import-plan", Button).press()
            await self._settle(pilot, lambda: screen.plan is not None)
            self.assertIn(source_lock["sha256"], str(screen.query_one("#import-detail").render()))
            screen.query_one("#import-apply", Button).press()
            await self._settle(pilot, lambda: bool(app.screen.query("#review-body")))
            self.assertIn(source_lock["revision"], str(app.screen.query_one("#review-body").render()))

    async def test_import_reviews_v3_managed_tool_policy_and_unresolved_bytes(self) -> None:
        core = _core()
        tool_id = "workbench-managed-tool-policy:sha256:" + "d" * 64
        core.plan_environment_import.return_value = {
            "format": "workbench-environment-import-plan-v4", "state": "ready",
            "plan_id": "tool-plan", "action": "create", "blockers": [],
            "unresolved_inputs": ["managed-tool-bytes"],
            "managed_tool_lock": {"lock_id": tool_id},
        }
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 44)) as pilot:
            app.push_screen(EnvironmentImportScreen())
            await self._settle(pilot, lambda: isinstance(app.screen, EnvironmentImportScreen)
                               and bool(app.screen.query("#import-share")))
            screen = app.screen
            screen.query_one("#import-share", Input).value = "/share.json"
            screen.query_one("#import-name", Input).value = "shared"
            screen.query_one("#import-workspace", Input).value = "/workspace"
            screen.query_one("#import-plan", Button).press()
            await self._settle(pilot, lambda: screen.plan is not None)
            self.assertIn(tool_id, str(screen.query_one("#import-detail").render()))
            screen.query_one("#import-apply", Button).press()
            await self._settle(pilot, lambda: bool(app.screen.query("#review-body")))
            self.assertIn(tool_id, str(app.screen.query_one("#review-body").render()))
            self.assertIn("Tool bytes remain unresolved", str(app.screen.query_one("#review-body").render()))

    async def test_import_acquisition_choice_invalidates_plan_and_is_reviewed(self) -> None:
        core = _core()
        core.plan_environment_import.side_effect = [
            {"format": "workbench-environment-import-plan-v1", "plan_id": "plain-plan",
             "state": "ready", "action": "create", "blockers": [], "unresolved_inputs": []},
            {"format": "workbench-environment-import-plan-v2", "plan_id": "with-java",
             "state": "ready", "action": "create", "acquire_managed_java": True,
             "blockers": [], "unresolved_inputs": ["managed-java-archive"]},
        ]
        core.import_environment_share.return_value = {
            "format": "workbench-environment-import-result-v2", "plan_id": "with-java",
            "outcome": "bound", "resource": {"path": "/evidence/import.json"},
            "managed_java": {"runtime_id": "test", "outcome": "reused"},
            "unresolved_inputs": [],
        }
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 44)) as pilot:
            app.push_screen(EnvironmentImportScreen())
            await self._settle(pilot, lambda: isinstance(app.screen, EnvironmentImportScreen)
                               and bool(app.screen.query("#import-share")))
            screen = app.screen
            screen.query_one("#import-share", Input).value = "/share.json"
            screen.query_one("#import-name", Input).value = "shared"
            screen.query_one("#import-workspace", Input).value = "/workspace"
            screen.query_one("#import-plan", Button).press()
            await self._settle(pilot, lambda: screen.plan is not None)
            screen.query_one("#import-acquire-java", Checkbox).value = True
            await self._settle(pilot, lambda: screen.plan is None)
            self.assertTrue(screen.query_one("#import-apply", Button).disabled)
            screen.query_one("#import-plan", Button).press()
            await self._settle(pilot, lambda: screen.plan is not None
                               and core.plan_environment_import.await_count == 2)
            core.plan_environment_import.assert_awaited_with(
                "/share.json", "shared", "/workspace", config="", java_home="",
                acquire_managed_java=True,
            )
            screen.query_one("#import-apply", Button).press()
            await self._settle(pilot, lambda: bool(app.screen.query("#review-confirm")))
            self.assertIn("Acquire managed Java: yes", str(app.screen.query_one("#review-body").render()))
            app.screen.query_one("#review-confirm", Button).press()
            await self._settle(pilot, lambda: core.import_environment_share.await_count == 1)
            core.import_environment_share.assert_awaited_with(
                "/share.json", "shared", "/workspace", expected_plan_id="with-java",
                config="", java_home="", acquire_managed_java=True,
            )
