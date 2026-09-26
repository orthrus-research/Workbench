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

from workbench_api.state_root_policies import (
    StateRootPolicyError, state_root_policies_scope,
)
from workbench_shell.cleanroom_new_project_cli import new_project_main


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
        with patch("workbench_shell.cleanroom_new_project_cli._profile", return_value=owner):
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


if __name__ == "__main__":
    unittest.main()
