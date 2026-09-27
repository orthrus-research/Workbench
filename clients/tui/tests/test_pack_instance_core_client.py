"""Textual sends exact release and recovery decisions through Core."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from workbench_tui.core_client import CoreClient, CoreClientError


POLICY_ID = "workbench-pack-release-derived-policies-plan:sha256:" + "a" * 64
ROOT_ID = "workbench-prism-data-root-plan:sha256:" + "b" * 64
INSTALL_ID = "workbench-pack-release-client-install-plan:sha256:" + "c" * 64
SOURCE_ID = "workbench-pack-release-client-composition-plan:sha256:" + "d" * 64


class PackInstanceCoreClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = CoreClient(("unused-workbench",))
        self.client.json_record = AsyncMock()  # type: ignore[method-assign]

    async def test_provider_preflight_validates_core_status(self) -> None:
        self.client.json_record.return_value = {  # type: ignore[attr-defined]
            "schema": "workbench.pack-instance.v1", "action": "fresh-provider-status",
            "provider": {"status": "unavailable", "reason": "access is not configured"},
        }
        result = await self.client.pack_instance_fresh_provider_status()
        self.assertEqual("unavailable", result["provider"]["status"])
        self.client.json_record.assert_awaited_once_with(  # type: ignore[attr-defined]
            "pack", "instance", "fresh-provider-status", "--profile", "supersymmetry",
            "--json", timeout=15,
        )
        self.client.json_record.return_value = {  # type: ignore[attr-defined]
            "schema": "workbench.pack-instance.v1", "action": "fresh-provider-status",
            "provider": {"status": "available", "reason": None},
        }
        with self.assertRaises(CoreClientError):
            await self.client.pack_instance_fresh_provider_status()

    async def test_policy_plan_and_apply_bind_exact_pairs_and_plan(self) -> None:
        self.client.json_record.side_effect = [  # type: ignore[attr-defined]
            {"schema": "workbench.pack-instance.v1", "action": "fresh-policy-plan",
             "policy": {"schema": "workbench.pack-release.fresh-setup.v1",
                        "status": "planned", "policy_plan": {"plan_id": POLICY_ID}}},
            {"schema": "workbench.pack-instance.v1", "action": "fresh-policy-apply",
             "policy": {"schema": "workbench.pack-release.fresh-setup.v1",
                        "status": "ready", "policy_plan_id": POLICY_ID}},
        ]
        pairs, optional = ((20, 200), (99, 100)), ((50, 60),)
        await self.client.pack_instance_fresh_policy_plan(pairs, optional)
        await self.client.pack_instance_fresh_policy_apply(pairs, optional, POLICY_ID)
        expected = ("pack", "instance", "fresh-policy-plan", "--profile", "supersymmetry",
                    "--resourcepack-pair", "20:200", "--resourcepack-pair", "99:100",
                    "--optional-pair", "50:60")
        first, second = self.client.json_record.await_args_list  # type: ignore[attr-defined]
        self.assertEqual((*expected, "--json"), first.args)
        self.assertEqual((*expected[:2], "fresh-policy-apply", *expected[3:],
                          "--expected-policy-plan-id", POLICY_ID, "--json"), second.args)

    async def test_recovery_accepts_retained_stage_without_instance_path(self) -> None:
        self.client.json_record.side_effect = [  # type: ignore[attr-defined]
            {"schema": "workbench.pack-instance.v1", "action": "root-plan",
             "prism_root": {"plan_id": ROOT_ID, "action": "reconcile", "state": "blocked",
                            "blockers": ["interrupted-prism-root-initialization"]}},
            {"schema": "workbench.pack-instance.v1", "action": "root-abandon",
             "prism_root": {"plan_id": ROOT_ID, "outcome": "abandoned",
                            "retained_stage_path": "/home/test/retained-root"}},
            {"schema": "workbench.pack-instance.v1", "action": "install-abandon",
             "source_plan_id": SOURCE_ID,
             "installation": {"plan_id": INSTALL_ID, "outcome": "abandoned",
                              "retained_stage_path": "/home/test/retained-install"}},
        ]
        planned = await self.client.pack_instance_root_plan()
        self.assertEqual("reconcile", planned["prism_root"]["action"])
        await self.client.pack_instance_root_recover(ROOT_ID, "abandon")
        abandoned = await self.client.pack_instance_install_recover(INSTALL_ID, "abandon")
        self.assertEqual("/home/test/retained-install",
                         abandoned["installation"]["retained_stage_path"])

    async def test_invalid_policy_pair_is_rejected_before_core_invocation(self) -> None:
        with self.assertRaises(CoreClientError):
            await self.client.pack_instance_fresh_policy_plan(((20, 200), (20, 200)), ())
        self.client.json_record.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_installed_instances_reopen_after_restart(self) -> None:
        self.client.json_record.return_value = {  # type: ignore[attr-defined]
            "schema": "workbench.pack-instance.v1", "action": "install-status",
            "installations": [{"plan_id": INSTALL_ID,
                               "instance_path": "/home/test/PrismLauncher/instances/susy"}],
            "count": 1,
        }
        result = await self.client.pack_instance_install_status()
        self.assertEqual(INSTALL_ID, result["installations"][0]["plan_id"])


if __name__ == "__main__":
    unittest.main()
