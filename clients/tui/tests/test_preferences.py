"""Preferences survive a new client process without entering the install tree."""

import argparse
from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from textual.screen import Screen

from workbench_tui.app import WorkbenchApp, _core_command
from workbench_tui.core_client import CoreClientError
from workbench_tui.preferences import (
    PreferencesError,
    TuiPreferences,
    load_preferences,
    preferences_path,
    save_preferences,
)


def _concurrent_save(path: str, theme: str, ready, start, result) -> None:
    from workbench_tui import preferences as module

    current = module.load_preferences(Path(path))
    original_load = module.load_preferences

    def delayed_load(*args, **kwargs):
        value = original_load(*args, **kwargs)
        time.sleep(0.2)
        return value

    module.load_preferences = delayed_load
    ready.put(True)
    if not start.wait(5):
        result.put("start timeout")
        return
    try:
        module.save_preferences(replace(current, theme=theme), Path(path))
    except PreferencesError as exc:
        result.put(str(exc))
    else:
        result.put("saved")


class _UnavailableCore:
    async def version(self):
        raise CoreClientError("test Core is unavailable")


class TuiPreferencesTests(unittest.TestCase):
    def test_installed_client_finds_core_in_the_same_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scripts = Path(temporary) / ("Scripts" if os.name == "nt" else "bin")
            scripts.mkdir()
            core = scripts / ("workbench.exe" if os.name == "nt" else "workbench")
            core.write_text("", encoding="utf-8")
            selected = argparse.Namespace(source_root=None, workbench=None)
            with patch("workbench_tui.app.sys.executable", str(scripts / "python")), \
                 patch("workbench_tui.app.shutil.which", return_value=None), \
                 patch.dict("workbench_tui.app.os.environ", {"WORKBENCH_EXECUTABLE": ""}):
                self.assertEqual((str(core),), _core_command(selected))

    def test_stable_home_is_external_to_a_new_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            path = preferences_path(environment=environment)
            self.assertEqual(home / ".workbench/tui.json", path)
            self.assertEqual(TuiPreferences(), load_preferences(environment=environment))
            self.assertFalse(path.exists())
            saved = save_preferences(
                replace(TuiPreferences(), theme="nord", show_clock=False),
                environment=environment,
            )
            self.assertEqual(saved, load_preferences(environment=environment))
            self.assertFalse((home / "new-install/tui.json").exists())
            moved = home / "other-home/.workbench/tui.json"
            moved.parent.mkdir(parents=True)
            shutil.copyfile(path, moved)
            self.assertEqual(saved, load_preferences(environment={"HOME": str(moved.parent.parent)}))

    def test_absolute_config_home_override_moves_the_preferences_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            selected = Path(temporary) / "portable-config"
            environment = {"WORKBENCH_CONFIG_HOME": str(selected)}
            self.assertEqual(selected / "tui.json", preferences_path(environment=environment))
            saved = save_preferences(TuiPreferences(theme="gruvbox"), environment=environment)
            self.assertEqual(saved, load_preferences(environment=environment))
            with self.assertRaisesRegex(PreferencesError, "absolute"):
                preferences_path(environment={"WORKBENCH_CONFIG_HOME": "relative/config"})

    def test_unknown_or_changed_record_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tui.json"
            saved = save_preferences(TuiPreferences(), path)
            changed = save_preferences(replace(saved, theme="nord"), path)
            with self.assertRaisesRegex(PreferencesError, "another session"):
                save_preferences(replace(saved, theme="gruvbox"), path)
            self.assertEqual(changed, load_preferences(path))
            source = path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 2')
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(PreferencesError, "schema"):
                load_preferences(path)
            with self.assertRaises(PreferencesError):
                save_preferences(changed, path)
            self.assertEqual(source, path.read_text(encoding="utf-8"))

    def test_symlink_is_not_a_preference_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            destination = base / "destination.json"
            destination.write_text("{}", encoding="utf-8")
            alias = base / "tui.json"
            alias.symlink_to(destination)
            with self.assertRaisesRegex(PreferencesError, "regular file"):
                load_preferences(alias)

    def test_obstructed_config_directory_reports_a_preference_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obstruction = Path(temporary) / "config"
            obstruction.write_text("keep this file", encoding="utf-8")
            with self.assertRaisesRegex(PreferencesError, "cannot save"):
                save_preferences(TuiPreferences(), obstruction / "tui.json")
            self.assertEqual("keep this file", obstruction.read_text(encoding="utf-8"))

    def test_two_processes_cannot_silently_overwrite_one_another(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tui.json"
            initial = save_preferences(TuiPreferences(), path)
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            start = context.Event()
            result = context.Queue()
            processes = [
                context.Process(target=_concurrent_save, args=(str(path), theme, ready, start, result))
                for theme in ("nord", "gruvbox")
            ]
            try:
                for process in processes:
                    process.start()
                for _ in processes:
                    self.assertTrue(ready.get(timeout=5))
                start.set()
                outcomes = [result.get(timeout=5) for _ in processes]
                self.assertEqual(1, outcomes.count("saved"))
                self.assertEqual(1, sum("another session" in outcome for outcome in outcomes))
                self.assertNotEqual(initial.record_id, load_preferences(path).record_id)
            finally:
                for process in processes:
                    process.join(timeout=5)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    self.assertEqual(0, process.exitcode)


class TuiPreferenceInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_clock_toggle_survives_an_obstructed_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obstruction = Path(temporary) / "config"
            obstruction.write_text("keep this file", encoding="utf-8")
            app = WorkbenchApp(_UnavailableCore(), preference_path=obstruction / "tui.json")
            async with app.run_test() as pilot:
                await pilot.press("ctrl+t")
                await pilot.pause()
                self.assertTrue(app.preferences.show_clock)
                self.assertTrue(app.screen_stack[0].query_one("#home-header")._show_clock)
            self.assertEqual("keep this file", obstruction.read_text(encoding="utf-8"))

    async def test_textual_theme_change_is_reused_by_next_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tui.json"
            first = WorkbenchApp(_UnavailableCore(), preference_path=path)
            async with first.run_test() as pilot:
                self.assertEqual("workbench-dark", first.theme)
                first.theme = "nord"
                await pilot.pause()
                await pilot.press("ctrl+t")
                await pilot.pause()
            self.assertEqual("nord", load_preferences(path).theme)
            self.assertFalse(load_preferences(path).show_clock)

            second = WorkbenchApp(
                _UnavailableCore(),
                preferences=load_preferences(path),
                preference_path=path,
            )
            async with second.run_test() as pilot:
                self.assertEqual("nord", second.theme)
                self.assertFalse(second.preferences.show_clock)
                second.theme = "textual-light"
                await pilot.pause()
            self.assertEqual("textual-light", load_preferences(path).theme)

    async def test_clock_toggle_updates_home_while_another_screen_is_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tui.json"
            app = WorkbenchApp(_UnavailableCore(), preference_path=path)
            async with app.run_test() as pilot:
                app.push_screen(Screen())
                await pilot.pause()
                await pilot.press("ctrl+t")
                await pilot.pause()
                self.assertIsInstance(app.screen, Screen)
                self.assertFalse(load_preferences(path).show_clock)
                self.assertFalse(app.screen_stack[0].query_one("#home-header")._show_clock)
