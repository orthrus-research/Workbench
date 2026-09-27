"""Core-selected state and policy hold for Cleanroom construction commands."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api.host_filesystem import bind_host_filesystem
from workbench_api.git_bootstrap import git_bootstrap_scope
from workbench_api.record_stores import record_store_scope
from workbench_api.source_transactions import source_transactions_scope
from workbench_api.state_root_policies import (
    StateRootPolicyError, state_root_policies_scope,
)
from workbench_core import host_filesystem
from workbench_core.git_bootstrap import HOST as GIT_BOOTSTRAP_HOST
from workbench_core.durable_records import read_bounded_single_link_bytes
from workbench_core.source_transactions import CoreSourceTransactions
from workbench_core.storage.record_stores import CoreRecordStores
from workbench_shell.cleanroom_new_project_cli import new_project_main


ROOT = Path(__file__).resolve().parents[3]


class _SelectedPolicy:
    def __init__(self, suite: Path, selected: Path, *, stale: bool = False) -> None:
        self.suite = suite
        self.selected = selected
        self.stale = stale
        self.held = False

    def resolve(self, workspace: Path, role: str) -> dict[str, str]:
        assert (workspace, role) == (self.suite, "product-spine")
        return {"state_root": str(self.selected), "policy_id": "policy:test"}

    @contextmanager
    def hold(self, workspace: Path, role: str, state_root: Path, expected_policy_id: str):
        assert (workspace, role, state_root, expected_policy_id) == (
            self.suite, "product-spine", self.selected, "policy:test",
        )
        if self.stale:
            raise StateRootPolicyError("state-root policy changed after review")
        self.held = True
        try:
            yield
        finally:
            self.held = False


class CleanroomNewProjectStatePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.suite = Path(temporary.name)
        self.plan = self.suite / "plan.json"
        self.plan.write_text(json.dumps({"id": "plan:test"}) + "\n", encoding="utf-8")
        self.plan.chmod(0o600)
        self.selected = self.suite / "selected-state"
        self.expected = (
            self.selected / "new-project-v2"
            / sha256(b"plan:test").hexdigest()
        )

    def _run(self, action: str, owner: object, *, extra: tuple[str, ...] = ()) -> tuple[int, str]:
        arguments = ["cleanroom-mod", action, str(self.plan), *extra]
        if action == "apply":
            arguments.extend(("--consent-plan-id", "plan:test"))
        output, error = StringIO(), StringIO()
        with patch("workbench_shell.cleanroom_new_project_cli._profile", return_value=owner), patch(
            "workbench_shell.cleanroom_new_project_cli.read_bounded_single_link_bytes",
            side_effect=read_bounded_single_link_bytes,
        ):
            status = new_project_main(
                arguments, root=self.suite, output=output, error=error,
            )
        return status, error.getvalue()

    def test_default_apply_and_recover_hold_core_selection_through_owner_call(self) -> None:
        for action in ("apply", "recover"):
            with self.subTest(action=action):
                policy = _SelectedPolicy(self.suite, self.selected)
                observed: list[Path] = []

                def owner_call(_suite, _plan, state_root, **_kwargs):
                    self.assertTrue(policy.held)
                    observed.append(state_root)
                    return {"state": "complete"}

                owner = SimpleNamespace(
                    apply_cleanroom_mod_construction=owner_call,
                    recover_cleanroom_mod_construction=owner_call,
                )
                with state_root_policies_scope(policy):
                    status, error = self._run(action, owner)
                self.assertEqual((status, error), (0, ""))
                self.assertEqual(observed, [self.expected])
                self.assertFalse(policy.held)

    def test_stale_core_selection_refuses_before_owner_mutation(self) -> None:
        policy = _SelectedPolicy(self.suite, self.selected, stale=True)
        called: list[bool] = []
        owner = SimpleNamespace(
            apply_cleanroom_mod_construction=lambda *_args, **_kwargs: called.append(True),
        )
        with state_root_policies_scope(policy):
            status, error = self._run("apply", owner)
        self.assertEqual(status, 2)
        self.assertIn("state-root policy changed after review", error)
        self.assertEqual(called, [])

    def test_explicit_state_root_keeps_one_command_override(self) -> None:
        override = self.suite / "explicit-state"
        observed: list[Path] = []
        owner = SimpleNamespace(
            apply_cleanroom_mod_construction=lambda _suite, _plan, state_root, **_kwargs: (
                observed.append(state_root) or {"state": "complete"}
            ),
        )
        with state_root_policies_scope(None):
            status, error = self._run(
                "apply", owner, extra=("--state-root", str(override)),
            )
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(observed, [override])

    def test_historical_hardlinked_plan_refuses_before_owner_mutation(self) -> None:
        alternate = self.suite / "another-plan.json"
        alternate.hardlink_to(self.plan)
        called: list[bool] = []
        owner = SimpleNamespace(
            apply_cleanroom_mod_construction=lambda *_args, **_kwargs: called.append(True),
        )
        with state_root_policies_scope(None):
            status, error = self._run(
                "apply", owner, extra=("--state-root", str(self.selected)),
            )
        self.assertEqual(status, 2)
        self.assertIn("cannot read Cleanroom construction plan", error)
        self.assertEqual(called, [])


class CleanroomNewProjectCoreRouteTests(unittest.TestCase):
    def test_shell_apply_and_interrupted_core_git_recovery_retains_v2_state(self) -> None:
        bind_host_filesystem(host_filesystem)
        with TemporaryDirectory() as temporary:
            home = Path(temporary)
            provider = CoreRecordStores(
                workspace=ROOT, configuration_home=home / "config",
                owner_id="workbench-shell",
            )

            def invoke(*arguments: str) -> dict[str, object]:
                output, error = StringIO(), StringIO()
                status = new_project_main(
                    ["cleanroom-mod", *arguments, "--json"],
                    root=ROOT, output=output, error=error,
                )
                self.assertEqual((status, error.getvalue()), (0, ""))
                return json.loads(output.getvalue())

            with (
                record_store_scope(provider),
                source_transactions_scope(CoreSourceTransactions(owner_id="workbench-shell")),
                git_bootstrap_scope(GIT_BOOTSTRAP_HOST),
            ):
                target = home / "applied"
                plan_path = home / "applied-plan.json"
                invoke("preview", str(target), "--output-mode", "direct-apply",
                       "--output", str(plan_path))
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                state = home / "applied-state"
                applied = invoke(
                    "apply", str(plan_path), "--state-root", str(state),
                    "--consent-plan-id", plan["id"],
                )
                self.assertEqual("applied", applied["state"])
                self.assertFalse((state / "fresh-bootstrap-v2.json").exists())
                self.assertEqual(1, len(list((state / "bootstrap-receipts").glob("*.json"))))

                interrupted = home / "interrupted"
                interrupted_plan = home / "interrupted-plan.json"
                invoke("preview", str(interrupted), "--output-mode", "direct-apply",
                       "--output", str(interrupted_plan))
                reviewed = json.loads(interrupted_plan.read_text(encoding="utf-8"))
                recovery_state = home / "recovery-state"
                from workbench_blueprints import fresh_project

                fresh_project.prepare_fresh_target(
                    interrupted, reviewed["target_observation"],
                    recovery_state, plan_id=reviewed["id"],
                )
                output, error = StringIO(), StringIO()
                status = new_project_main(
                    ["cleanroom-mod", "recover", str(interrupted_plan),
                     "--state-root", str(recovery_state), "--json"],
                    root=ROOT, output=output, error=error,
                )
                self.assertEqual(status, 2)
                self.assertIn("Core Git initialization", error.getvalue())
                self.assertEqual("", output.getvalue())
                self.assertTrue(interrupted.is_dir())
                self.assertTrue((recovery_state / "fresh-bootstrap-v2.json").is_file())
                self.assertTrue(GIT_BOOTSTRAP_HOST.has_init_attempt(
                    recovery_state, plan_id=reviewed["id"],
                ))


if __name__ == "__main__":
    unittest.main()
