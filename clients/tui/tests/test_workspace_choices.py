"""Named workspace choices stay in Core across Textual sessions."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from textual.widgets import Button, Input, Select

from workbench_tui.app import WorkbenchApp, WorkspaceChoicesScreen, EnvironmentImportScreen
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


class WorkspaceChoiceScreenTests(IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(40):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected state")

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
