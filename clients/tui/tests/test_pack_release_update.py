"""Textual presents an exact Core release decision without managing pack bytes."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from test_setup_screen import fake_core
from workbench_tui.app import ReleaseUpdateModal, ResultScreen, WorkbenchApp
from workbench_tui.core_client import CoreClient, CoreClientError


RELEASE_ID = "github-release:sha256:" + "a" * 64


def _candidate() -> dict:
    return {
        "release_id": RELEASE_ID,
        "version": "0.1.16.17",
        "tag": "0.1.16.17",
        "asset_name": "supersymmetry-0.1.16.17.zip",
        "asset_size": 113125809,
        "release_url": "https://github.com/SymmetricDevs/Supersymmetry/releases/tag/0.1.16.17",
        "published_at": "2026-09-27T00:00:00Z",
    }


def _check(status: str = "update_available") -> dict:
    return {
        "schema": "workbench.pack-release.v1",
        "action": "check",
        "status": status,
        "selected_version": "0.1.16.16",
        "candidate": _candidate() if status == "update_available" else None,
        "reason": None,
    }


class PackReleaseClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_and_exact_accept_use_core_json_interface(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(side_effect=[
            _check(),
            {**_check(), "action": "accept", "status": "accepted",
             "selected_version": "0.1.16.17", "artifact_path": "/stable/artifacts/sha256/abc"},
        ])
        offered = await client.pack_release_check()
        accepted = await client.pack_release_accept(offered["candidate"]["release_id"])
        self.assertEqual("accepted", accepted["status"])
        self.assertEqual(
            ("pack", "release", "check", "--profile", "supersymmetry", "--json"),
            client.json_record.await_args_list[0].args,
        )
        self.assertEqual(
            ("pack", "release", "accept", "--profile", "supersymmetry",
             "--expected-release-id", RELEASE_ID, "--json"),
            client.json_record.await_args_list[1].args,
        )

    async def test_invalid_candidate_and_mismatched_result_are_rejected(self) -> None:
        client = CoreClient(("workbench",))
        client.json_record = AsyncMock(return_value={
            **_check(), "candidate": {**_candidate(), "release_id": "other"},
        })
        with self.assertRaisesRegex(CoreClientError, "incomplete pack release candidate"):
            await client.pack_release_check()
        client.json_record = AsyncMock(return_value={
            **_check(), "action": "ignore", "status": "ignored",
            "candidate": {**_candidate(), "release_id": "github-release:sha256:" + "b" * 64},
        })
        with self.assertRaisesRegex(CoreClientError, "different pack release"):
            await client.pack_release_ignore(RELEASE_ID)


class PackReleaseInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, pilot, predicate) -> None:
        for _ in range(40):
            if predicate():
                return
            await pilot.pause(0.05)
        self.fail("Textual did not reach the expected release state")

    async def test_startup_prompt_accepts_exact_release_and_shows_verified_archive(self) -> None:
        core = fake_core("/tmp/workbench-test-workspace")
        core.pack_release_check = AsyncMock(return_value=_check())
        core.pack_release_accept = AsyncMock(return_value={
            **_check(), "action": "accept", "status": "accepted",
            "selected_version": "0.1.16.17", "artifact_path": "/stable/artifacts/sha256/abc",
        })
        core.pack_release_ignore = AsyncMock()
        app = WorkbenchApp(core)
        async with app.run_test() as pilot:
            await self._settle(pilot, lambda: isinstance(app.screen, ReleaseUpdateModal)
                               and bool(app.screen.query("#release-accept")))
            self.assertIn("0.1.16.17", app.screen.body)
            core.pack_release_accept.assert_not_awaited()
            await pilot.click("#release-accept")
            await self._settle(pilot, lambda: isinstance(app.screen, ResultScreen))
            core.pack_release_accept.assert_awaited_once_with(RELEASE_ID)
            core.pack_release_ignore.assert_not_awaited()
            self.assertIn("/stable/artifacts/sha256/abc", app.screen.output)

    async def test_ignore_saves_only_offered_release_and_later_does_not_save(self) -> None:
        core = fake_core("/tmp/workbench-test-workspace")
        core.pack_release_check = AsyncMock(return_value=_check())
        core.pack_release_accept = AsyncMock()
        core.pack_release_ignore = AsyncMock(return_value={
            **_check(), "action": "ignore", "status": "ignored",
        })
        app = WorkbenchApp(core)
        async with app.run_test() as pilot:
            await self._settle(pilot, lambda: isinstance(app.screen, ReleaseUpdateModal)
                               and bool(app.screen.query("#release-later")))
            await pilot.click("#release-later")
            await self._settle(pilot, lambda: not isinstance(app.screen, ReleaseUpdateModal))
            core.pack_release_ignore.assert_not_awaited()
            app.action_refresh_environment()
            await self._settle(pilot, lambda: isinstance(app.screen, ReleaseUpdateModal)
                               and bool(app.screen.query("#release-ignore")))
            await pilot.click("#release-ignore")
            await self._settle(pilot, lambda: core.pack_release_ignore.await_count == 1)
            core.pack_release_ignore.assert_awaited_once_with(RELEASE_ID)
            core.pack_release_accept.assert_not_awaited()

    async def test_offline_check_leaves_home_usable(self) -> None:
        core = fake_core("/tmp/workbench-test-workspace")
        core.pack_release_check = AsyncMock(return_value=_check("unavailable"))
        app = WorkbenchApp(core)
        async with app.run_test() as pilot:
            await self._settle(pilot, lambda: app.view.version is not None)
            await self._settle(pilot, lambda: core.pack_release_check.await_count == 1)
            self.assertNotIsInstance(app.screen, ReleaseUpdateModal)


if __name__ == "__main__":
    unittest.main()
