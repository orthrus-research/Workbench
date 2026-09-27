"""Textual reviews a complete Prism ZIP before Core source and install mutations."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from textual.widgets import Button, Input, Select, Static

from test_setup_screen import fake_core
from workbench_tui.app import (
    InterruptedSetupModal, PackInstanceScreen, ResourcepackMappingModal,
    ReviewModal, WorkbenchApp, WorkspaceChoicesScreen, WorkspaceRegisterScreen,
)
from workbench_tui.keyboard_form import ChoicePicker


SOURCE_ID = "workbench-pack-release-client-composition-plan:sha256:" + "a" * 64
INSTALL_ID = "workbench-pack-release-client-install-plan:sha256:" + "b" * 64
ROOT_ID = "workbench-prism-data-root-plan:sha256:" + "d" * 64
POLICY_ID = "workbench-pack-release-derived-policies-plan:sha256:" + "e" * 64
LAUNCH_ID = "workbench-pack-release-client-launch-plan:sha256:" + "f" * 64
RECORD_ID = "workbench-pack-instance-choice:sha256:" + "c" * 64


class PackInstanceInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(60):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected instance setup state")

    async def test_keyboard_navigation_edits_zip_path_without_tab(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        choice = {"choice": {"source_plan_id": None, "launcher_root": None,
                             "workspace_name": None}, "source_state": "none"}
        workspaces = {"default": "dev", "entries": [
            {"name": "dev", "path": "/home/test/workspace"},
        ]}
        async with app.run_test(size=(110, 40)) as pilot:
            app.push_screen(PackInstanceScreen(choice, workspaces))
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen)
                               and bool(app.screen.query("#pack-source-mode"))
                               and app.screen.query_one("#pack-source-mode").has_class(
                                   "keyboard-selected"))
            screen = app.screen
            self.assertIsNone(app.focused)
            await pilot.press("enter")
            await self._settle(pilot, lambda: isinstance(app.screen, ChoicePicker))
            await pilot.press("down", "enter")
            await self._settle(pilot, lambda: app.screen is screen)
            await pilot.press("down", "enter")
            zip_path = screen.query_one("#pack-zip-path", Input)
            self.assertIs(app.focused, zip_path)
            zip_path.value = "/home/test/Supersymmetry.zip"
            await pilot.press("enter")
            self.assertIsNone(app.focused)
            self.assertEqual("/home/test/Supersymmetry.zip", zip_path.value)
            self.assertTrue(zip_path.has_class("keyboard-selected"))

    async def test_java_choice_uses_arrows_and_enter_without_tab(self) -> None:
        core = fake_core("/home/test/workspace")
        core.workspace_choices = AsyncMock(return_value={
            "record_id": "workbench-user-workspaces:sha256:" + "e" * 64,
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_workspace_choices()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen)
                               and app.screen.query_one("#choice-workspace", Select).has_class(
                                   "keyboard-selected"))
            screen = app.screen
            await pilot.press("down", "down", "enter")
            await self._settle(pilot, lambda: isinstance(app.screen, ChoicePicker))
            await pilot.press("down", "enter")
            await self._settle(pilot, lambda: app.screen is screen)
            self.assertEqual("managed-8", screen.query_one("#choice-java-mode", Select).value)
            self.assertIsNone(app.focused)

    async def test_register_workspace_edits_fields_without_tab(self) -> None:
        app = WorkbenchApp(fake_core("/home/test/workspace"))
        async with app.run_test(size=(110, 40)) as pilot:
            screen = WorkspaceRegisterScreen({"record_id": "initial", "entries": []},
                                             initial_path="/home/test/workspace")
            app.push_screen(screen)
            await self._settle(pilot, lambda: app.screen is screen
                               and bool(screen.query("#workspace-register-name"))
                               and screen.query_one("#workspace-register-name", Input).has_class(
                                   "keyboard-selected"))
            await pilot.press("enter", "s", "u", "s", "y", "enter")
            self.assertIsNone(app.focused)
            self.assertEqual("susy", screen.query_one("#workspace-register-name", Input).value)
            await pilot.press("down", "enter")
            self.assertIs(app.focused, screen.query_one("#workspace-register-path", Input))

    async def test_first_user_registers_workspace_without_leaving_textual(self) -> None:
        core = fake_core("/home/test/Supersymmetry")
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_kind": None,
                       "source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        initial = {"record_id": "workbench-user-workspaces:sha256:" + "e" * 64,
                   "default": None, "entries": []}
        saved = {"record_id": "workbench-user-workspaces:sha256:" + "f" * 64,
                 "default": "susy-dev", "entries": [
                     {"name": "susy-dev", "path": "/home/test/Supersymmetry"},
                 ]}
        core.workspace_choices = AsyncMock(return_value=initial)
        core.register_workspace = AsyncMock(return_value=saved)
        app = WorkbenchApp(core, initial_workspace="/home/test/Supersymmetry")
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            pack = app.screen
            self.assertTrue(pack.query_one("#pack-fresh-download", Button).disabled)
            self.assertTrue(pack.query_one("#pack-zip-import", Button).disabled)
            pack.query_one("#pack-workspace-register", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceRegisterScreen))
            registration = app.screen
            self.assertEqual("/home/test/Supersymmetry",
                             registration.query_one("#workspace-register-path", Input).value)
            registration.query_one("#workspace-register-name", Input).value = "susy-dev"
            registration.query_one("#workspace-register-save", Button).press()
            await self._settle(pilot, lambda: core.register_workspace.await_count == 1)
            core.register_workspace.assert_awaited_once_with(
                "susy-dev", "/home/test/Supersymmetry", make_default=True,
                expected_record_id=initial["record_id"],
            )
            await self._settle(pilot, lambda: app.screen is pack
                               and pack.query_one("#pack-workspace", Select).value == "susy-dev")
            self.assertEqual("susy-dev", pack.query_one("#pack-workspace", Select).value)
            self.assertFalse(pack.query_one("#pack-fresh-download", Button).disabled)
            self.assertFalse(pack.query_one("#pack-zip-import", Button).disabled)

    async def test_java_choice_is_reachable_from_instance_setup_and_refreshed(self) -> None:
        core = fake_core("/home/test/workspace")
        core.pack_release_check = AsyncMock(return_value={"status": "current"})
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_kind": None,
                       "source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        initial = {
            "record_id": "workbench-user-workspaces:sha256:" + "e" * 64,
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        }
        updated = {
            "record_id": "workbench-user-workspaces:sha256:" + "f" * 64,
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace",
                                           "managed_java_feature": 8}],
        }
        core.workspace_choices = AsyncMock(side_effect=[initial, updated])
        core.save_workspace_choice = AsyncMock(return_value=updated)
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            pack = app.screen
            pack.query_one("#pack-java-choice", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, WorkspaceChoicesScreen))
            java = app.screen
            self.assertEqual("dev", java.selected_name)
            await self._settle(pilot, lambda: "No other system JDKs" in str(
                java.query_one("#choice-java-hint", Static).render()
            ))
            java.query_one("#choice-java-mode", Select).value = "managed-8"
            await pilot.pause()
            java.query_one("#choice-save", Button).press()
            await self._settle(pilot, lambda: core.save_workspace_choice.await_count == 1)
            core.save_workspace_choice.assert_awaited_once_with(
                "dev", profile_config=None, java_home=None, managed_java_feature=8,
                expected_record_id=initial["record_id"],
            )
            java.query_one("#choice-back", Button).press()
            await self._settle(pilot, lambda: app.screen is pack and
                               core.workspace_choices.await_count == 2)
            self.assertEqual(updated["record_id"], pack.workspaces["record_id"])
            self.assertEqual("dev", pack.query_one("#pack-workspace", Select).value)

    async def test_complete_zip_and_install_are_separately_reviewed(self) -> None:
        source = {"plan_id": SOURCE_ID, "source_kind": "user-prism-zip",
                  "source_version": "developer-branch", "file_count": 193,
                  "total_bytes": 123456, "source_archive_sha256": "sha256:" + "d" * 64,
                  "source_platform": {"kind": "forge", "component_version": "14.23.5.2860"}}
        selected = {"record_id": RECORD_ID, "source_kind": None,
                    "source_plan_id": None, "launcher_root": None,
                    "workspace_name": None}
        saved = {**selected, "source_kind": "user-prism-zip",
                 "source_plan_id": SOURCE_ID,
                 "launcher_root": "/home/test/PrismLauncher",
                 "workspace_name": "dev"}
        plan = {"plan_id": INSTALL_ID, "state": "ready", "blockers": [],
                "instance_path": "/home/test/PrismLauncher/instances/supersymmetry",
                "source_version": "developer-branch", "java_selection_state": "managed-verified",
                "java_selected_feature": 25, "account_state": "launcher-setup-needed",
                "source_platform": source["source_platform"]}
        core = fake_core("/home/test/workspace")
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_release_check = AsyncMock(return_value={
            "schema": "workbench.pack-release.v1", "action": "check",
            "status": "current", "selected_version": "0.1.16.16",
            "candidate": None, "reason": None,
        })
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": selected, "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={
            "record_id": "workbench-user-workspaces:sha256:" + "e" * 64,
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_instance_zip_plan = AsyncMock(return_value={"source": source})
        core.pack_instance_zip_stage_plan = AsyncMock(return_value={
            "stage": {"plan_id": "workbench-prism-zip-stage-plan:sha256:" + "1" * 64,
                      "action": "direct", "source_path": "/home/test/susy.zip",
                      "archive_path": "/home/test/susy.zip", "source_size": 123456,
                      "source_sha256": "sha256:" + "d" * 64},
        })
        core.pack_instance_zip_import = AsyncMock(return_value={"source": source})
        core.pack_instance_choice_select = AsyncMock(return_value={
            "choice": saved, "source_state": "retained",
        })
        core.pack_instance_install_prepare = AsyncMock(return_value={"installation": plan})
        core.pack_instance_root_plan = AsyncMock(return_value={
            "prism_root": {"plan_id": "workbench-prism-data-root-plan:sha256:" + "a" * 64,
                           "action": "initialize", "state": "ready", "blockers": [],
                           "launcher_root": saved["launcher_root"]},
        })
        core.pack_instance_install_apply = AsyncMock(return_value={
            "installation": {"plan_id": INSTALL_ID,
                             "instance_path": plan["instance_path"]},
        })
        core.pack_instance_launch_plan = AsyncMock(return_value={
            "launch": {"plan_id": LAUNCH_ID, "instance_path": plan["instance_path"],
                       "prism_version": "11.1.0"},
        })
        core.pack_instance_launch_run = AsyncMock(return_value={
            "launch": {"launch_plan_id": LAUNCH_ID, "outcome": "completed"},
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            screen = app.screen
            self.assertTrue(screen.query_one("#pack-instance-install", Button).disabled)
            screen.query_one("#pack-source-mode", Select).value = "zip"
            await pilot.pause()
            screen.query_one("#pack-zip-path", Input).value = "/home/test/susy.zip"
            screen.query_one("#pack-prism-root", Input).value = saved["launcher_root"]
            screen.query_one("#pack-zip-import", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_zip_import.assert_not_awaited()
            self.assertIn("choose Java", app.screen.body)
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: not screen.query_one(
                "#pack-instance-install", Button,
            ).disabled)
            core.pack_instance_choice_select.assert_awaited_once()
            screen.query_one("#pack-source-mode", Select).value = "official"
            await pilot.pause()
            self.assertFalse(screen.query_one("#pack-instance-install", Button).display)
            screen.query_one("#pack-source-mode", Select).value = "zip"
            await pilot.pause()
            self.assertTrue(screen.query_one("#pack-instance-install", Button).display)
            screen.query_one("#pack-instance-install", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_install_prepare.assert_not_awaited()
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_install_apply.assert_not_awaited()
            self.assertIn("Forge and Java 25", app.screen.body)
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_install_apply.await_count == 1)
            core.pack_instance_install_apply.assert_awaited_once_with(INSTALL_ID)
            self.assertFalse(screen.query_one("#pack-instance-show", Button).disabled)
            screen.query_one("#pack-instance-show", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_launch_run.assert_not_awaited()
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_launch_run.await_count == 1)
            core.pack_instance_launch_run.assert_awaited_once_with(
                INSTALL_ID, LAUNCH_ID, "show",
            )

    async def test_missing_workbench_provider_keeps_zip_route_available(self) -> None:
        core = fake_core("/home/test/workspace")
        core.pack_instance_fresh_provider_status = AsyncMock(return_value={
            "provider": {"status": "unavailable", "reason": "not configured"},
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_release_check = AsyncMock(return_value={
            "schema": "workbench.pack-release.v1", "action": "check",
            "status": "current", "selected_version": "0.1.16.16",
            "candidate": None, "reason": None,
        })
        core.pack_release_show = AsyncMock(return_value={
            "artifact_state": "verified",
            "selected": {"version": "0.1.16.16", "release_id": "profile-release:sha256:" + "a" * 64,
                         "asset_size": 100, "artifact_path": "/home/test/release.zip"},
        })
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_kind": None,
                       "source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_instance_fresh_status = AsyncMock(return_value={
            "fresh": {"status": "pending", "provider_state": "unavailable",
                      "ready_file_count": 0, "selected_file_count": 193},
        })
        core.pack_instance_fresh_policy_status = AsyncMock(return_value={
            "policy": {"status": "ready", "source": "baseline"},
        })
        core.pack_instance_fresh_overrides = AsyncMock()
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen)
                               and app.screen.provider_checked)
            screen = app.screen
            self.assertEqual("zip", screen.query_one("#pack-source-mode", Select).value)
            self.assertIn("Official download is unavailable", str(
                screen.query_one("#pack-instance-status", Static).content,
            ))
            core.pack_instance_fresh_overrides.assert_not_awaited()
            self.assertFalse(screen.query_one("#pack-zip-import", Button).disabled)

    async def test_saved_official_files_can_finish_without_provider_access(self) -> None:
        core = fake_core("/home/test/workspace")
        core.pack_instance_fresh_provider_status = AsyncMock(return_value={
            "provider": {"status": "unavailable", "reason": "not configured"},
        })
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_plan_id": None,
                       "launcher_root": None, "workspace_name": None},
            "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_release_show = AsyncMock(return_value={
            "artifact_state": "verified", "selected": {"version": "0.1.16.16"},
        })
        core.pack_instance_fresh_policy_status = AsyncMock(return_value={
            "policy": {"status": "ready"},
        })
        core.pack_instance_fresh_status = AsyncMock(return_value={
            "fresh": {"status": "pending", "provider_state": "unavailable",
                      "ready_file_count": 193, "selected_file_count": 193,
                      "release_version": "0.1.16.16", "files": []},
        })
        core.pack_instance_fresh_overrides = AsyncMock(return_value={
            "fresh": {"override_plan_id": "saved-overrides"},
        })
        core.pack_instance_fresh_publish = AsyncMock(return_value={
            "fresh": {"composition_result": {"plan_id": SOURCE_ID}},
        })
        core.pack_instance_choice_select = AsyncMock(return_value={
            "choice": {"record_id": "saved", "source_plan_id": SOURCE_ID,
                       "source_kind": "published-release",
                       "launcher_root": "/home/test/.local/share/PrismLauncher",
                       "workspace_name": "dev"},
            "source_state": "retained",
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(100, 30)) as pilot:
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen)
                               and app.screen.provider_checked)
            screen = app.screen
            self.assertEqual("zip", screen.query_one("#pack-source-mode", Select).value)
            screen.query_one("#pack-source-mode", Select).value = "official"
            await pilot.pause()
            self.assertFalse(screen.query_one("#pack-fresh-download", Button).disabled)
            screen.query_one("#pack-fresh-download", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal))
            self.assertIn("cannot download missing game files", app.screen.body)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and "saved game files" in app.screen.heading)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: screen.choice["choice"]["source_plan_id"] == SOURCE_ID)
            self.assertTrue(screen.query_one("#pack-instance-install", Button).display)
            core.pack_release_prepare.assert_not_awaited()
            core.pack_instance_fresh_file.assert_not_awaited()

    async def test_wsl_windows_zip_is_copied_before_import_review(self) -> None:
        source_archive = "/mnt/c/Users/test/Downloads/susy.zip"
        retained_archive = "/home/test/.local/state/workbench/runtime/prism-zips/susy.zip"
        stage_id = "workbench-prism-zip-stage-plan:sha256:" + "1" * 64
        source = {"plan_id": SOURCE_ID, "source_kind": "user-prism-zip",
                  "source_version": "developer-branch", "file_count": 193,
                  "source_archive_sha256": "sha256:" + "d" * 64}
        core = fake_core("/home/test/workspace")
        core.pack_release_check = AsyncMock(return_value={
            "schema": "workbench.pack-release.v1", "action": "check",
            "status": "current", "selected_version": "0.1.16.16", "candidate": None,
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_kind": None,
                       "source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_instance_zip_stage_plan = AsyncMock(return_value={
            "stage": {"plan_id": stage_id, "action": "copy",
                      "source_path": source_archive, "archive_path": retained_archive,
                      "source_size": 123456, "source_sha256": "sha256:" + "d" * 64},
        })
        core.pack_instance_zip_stage_apply = AsyncMock(return_value={
            "stage": {"plan_id": stage_id, "outcome": "copied",
                      "archive_path": retained_archive},
        })
        core.pack_instance_zip_plan = AsyncMock(return_value={"source": source})
        core.pack_instance_zip_import = AsyncMock(return_value={"source": source})
        core.pack_instance_choice_select = AsyncMock(return_value={
            "choice": {"record_id": "workbench-pack-instance-choice:sha256:" + "2" * 64,
                       "source_kind": "user-prism-zip", "source_plan_id": SOURCE_ID,
                       "launcher_root": "/home/test/PrismLauncher", "workspace_name": "dev"},
            "source_state": "retained",
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            screen = app.screen
            screen.query_one("#pack-source-mode", Select).value = "zip"
            await pilot.pause()
            screen.query_one("#pack-zip-path", Input).value = source_archive
            screen.query_one("#pack-prism-root", Input).value = "/home/test/PrismLauncher"
            screen.query_one("#pack-zip-import", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_zip_plan.assert_not_awaited()
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_zip_stage_apply.await_count == 1)
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_zip_plan.assert_awaited_once_with(retained_archive)
            core.pack_instance_zip_import.assert_not_awaited()
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_zip_import.await_count == 1)
            core.pack_instance_zip_import.assert_awaited_once_with(retained_archive, SOURCE_ID)

    async def test_fresh_download_saves_one_core_composition_after_progress(self) -> None:
        core = fake_core("/home/test/workspace")
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_release_check = AsyncMock(return_value={
            "schema": "workbench.pack-release.v1", "action": "check",
            "status": "current", "selected_version": "0.1.16.16",
            "candidate": None, "reason": None,
        })
        core.pack_release_show = AsyncMock(return_value={
            "artifact_state": "verified",
            "selected": {"version": "0.1.16.16", "release_id": "profile-release:sha256:" + "a" * 64,
                         "asset_size": 100, "artifact_path": "/home/test/release.zip"},
        })
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_kind": None,
                       "source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_instance_fresh_status = AsyncMock(return_value={
            "fresh": {"status": "pending", "provider_state": "available",
                      "release_version": "0.1.16.16", "ready_file_count": 0,
                      "selected_file_count": 2,
                      "files": [{"project_id": 1, "file_id": 2, "status": "pending"},
                                {"project_id": 3, "file_id": 4, "status": "pending"}]},
        })
        core.pack_instance_fresh_policy_status = AsyncMock(return_value={
            "policy": {"status": "ready", "source": "baseline"},
        })
        override_id = "workbench-pack-release-override-custody-plan:sha256:" + "e" * 64
        core.pack_instance_fresh_overrides = AsyncMock(return_value={
            "fresh": {"override_plan_id": override_id},
        })
        core.pack_instance_fresh_file = AsyncMock(return_value={"fresh": {"status": "ready"}})
        core.pack_instance_fresh_publish = AsyncMock(return_value={
            "fresh": {"composition_result": {"plan_id": SOURCE_ID}},
        })
        core.pack_instance_choice_select = AsyncMock(return_value={
            "choice": {"record_id": "workbench-pack-instance-choice:sha256:" + "f" * 64,
                       "source_kind": "published-release", "source_plan_id": SOURCE_ID,
                       "launcher_root": "/home/test/PrismLauncher", "workspace_name": "dev"},
            "source_state": "retained",
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            screen = app.screen
            screen.query_one("#pack-prism-root", Input).value = "/home/test/PrismLauncher"
            screen.query_one("#pack-fresh-download", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_fresh_file.assert_not_awaited()
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_choice_select.await_count == 1)
            self.assertEqual(2, core.pack_instance_fresh_file.await_count)
            core.pack_instance_choice_select.assert_awaited_once_with(
                SOURCE_ID, expected_record_id=RECORD_ID,
                launcher_root="/home/test/PrismLauncher", workspace_name="dev",
                source_kind="published-release",
            )
            self.assertFalse(screen.query_one("#pack-instance-install", Button).disabled)

    async def test_newer_release_requires_reviewed_resourcepack_mapping(self) -> None:
        core = fake_core("/home/test/workspace")
        core.pack_release_check = AsyncMock(return_value={
            "schema": "workbench.pack-release.v1", "action": "check",
            "status": "current", "selected_version": "0.1.16.17", "candidate": None,
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": {"record_id": RECORD_ID, "source_kind": None,
                       "source_plan_id": None, "launcher_root": None,
                       "workspace_name": None}, "source_state": "none",
        })
        core.workspace_choices = AsyncMock(return_value={
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_release_show = AsyncMock(return_value={
            "artifact_state": "verified", "selected": {
                "version": "0.1.16.17", "release_id": "github-release:sha256:" + "a" * 64,
                "asset_size": 100, "artifact_path": "/home/test/release.zip",
            },
        })
        review = {
            "status": "review_required", "required_external_files": [
                {"project_id": 20, "file_id": 200},
                {"project_id": 99, "file_id": 100},
            ],
            "optional_external_files": [{"project_id": 50, "file_id": 60}],
            "suggestions": {"suggested": [{"project_id": 20, "file_id": 200}],
                            "unresolved_project_ids": []},
        }
        core.pack_instance_fresh_policy_status = AsyncMock(side_effect=[
            {"policy": review}, {"policy": {"status": "ready", "source": "reviewed"}},
        ])
        core.pack_instance_fresh_policy_plan = AsyncMock(return_value={
            "policy": {"policy_plan": {
                "plan_id": POLICY_ID, "version": "0.1.16.17", "action": "acquire",
                "layout_policy": {"override_file_count": 18},
            }},
        })
        core.pack_instance_fresh_policy_apply = AsyncMock(return_value={
            "policy": {"status": "ready", "policy_plan_id": POLICY_ID},
        })
        core.pack_instance_fresh_status = AsyncMock(return_value={
            "fresh": {"status": "pending", "provider_state": "unavailable",
                      "ready_file_count": 0, "selected_file_count": 3},
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            screen = app.screen
            screen.query_one("#pack-fresh-download", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ResourcepackMappingModal)
                               and bool(app.screen.query("#pack-policy-confirm")))
            core.pack_instance_fresh_policy_plan.assert_not_awaited()
            app.screen.query_one("#pack-policy-pairs", Input).value = "20:200, 99:100"
            await pilot.pause(0.05)
            await pilot.click("#pack-policy-confirm")
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_fresh_policy_apply.assert_not_awaited()
            core.pack_instance_fresh_policy_plan.assert_awaited_once_with(
                ((20, 200), (99, 100)), ((50, 60),),
            )
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_fresh_policy_apply.await_count == 1)
            core.pack_instance_fresh_policy_apply.assert_awaited_once_with(
                ((20, 200), (99, 100)), ((50, 60),), POLICY_ID,
            )
            await self._settle(pilot, lambda: "CurseForge provider access" in str(
                screen.query_one("#pack-instance-status", Static).content,
            ))

    async def test_interrupted_prism_folder_requires_recovery_choice(self) -> None:
        selected = {
            "record_id": RECORD_ID, "source_kind": "user-prism-zip",
            "source_plan_id": SOURCE_ID, "launcher_root": "/home/test/PrismLauncher",
            "workspace_name": "dev",
        }
        core = fake_core("/home/test/workspace")
        core.pack_release_check = AsyncMock(return_value={
            "schema": "workbench.pack-release.v1", "action": "check",
            "status": "current", "selected_version": "0.1.16.16", "candidate": None,
        })
        core.pack_instance_install_status = AsyncMock(return_value={"installations": []})
        core.pack_instance_choice_show = AsyncMock(return_value={
            "choice": selected, "source_state": "retained",
        })
        core.workspace_choices = AsyncMock(return_value={
            "default": "dev", "entries": [{"name": "dev", "path": "/home/test/workspace"}],
        })
        core.pack_instance_root_plan = AsyncMock(side_effect=[
            {"prism_root": {"plan_id": ROOT_ID, "action": "reconcile", "state": "blocked",
                            "blockers": ["interrupted-prism-root-initialization"],
                            "launcher_root": selected["launcher_root"]}},
            {"prism_root": {"plan_id": ROOT_ID, "action": "reuse", "state": "ready",
                            "blockers": [], "launcher_root": selected["launcher_root"]}},
        ])
        core.pack_instance_root_recover = AsyncMock(return_value={
            "prism_root": {"plan_id": ROOT_ID, "outcome": "reconciled"},
        })
        core.pack_instance_install_prepare = AsyncMock(return_value={
            "installation": {"plan_id": INSTALL_ID, "state": "ready", "blockers": [],
                             "instance_path": selected["launcher_root"] + "/instances/susy",
                             "source_version": "developer-branch"},
        })
        core.pack_instance_install_apply = AsyncMock(return_value={
            "installation": {"plan_id": INSTALL_ID,
                             "instance_path": selected["launcher_root"] + "/instances/susy"},
        })
        app = WorkbenchApp(core)
        async with app.run_test(size=(110, 40)) as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            app.open_pack_instance()
            await self._settle(pilot, lambda: isinstance(app.screen, PackInstanceScreen))
            screen = app.screen
            screen.query_one("#pack-instance-install", Button).press()
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: isinstance(app.screen, InterruptedSetupModal)
                               and bool(app.screen.query("#setup-recovery-reconcile")))
            core.pack_instance_install_prepare.assert_not_awaited()
            await pilot.pause(0.05)
            await pilot.click("#setup-recovery-reconcile")
            await self._settle(pilot, lambda: isinstance(app.screen, ReviewModal)
                               and bool(app.screen.query("#review-confirm")))
            core.pack_instance_root_recover.assert_awaited_once_with(ROOT_ID, "reconcile")
            await pilot.pause(0.05)
            await pilot.click("#review-confirm")
            await self._settle(pilot, lambda: core.pack_instance_install_apply.await_count == 1)


if __name__ == "__main__":
    unittest.main()
