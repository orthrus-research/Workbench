"""Core attempt-bound execution workspace adapter preserves V1 physical custody."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import capture_workspaces as port
from workbench_api.managed_attempts import ManagedAttemptError, managed_attempts_scope
from workbench_core import capture_workspace, check_storage
from workbench_core.capture_workspace_port import HOST
from workbench_core.managed_attempts import CoreManagedAttempts


class CaptureWorkspacePortTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.evidence = self.root / "evidence"
        self.attempts = self._attempts("workbench-shell")
        self.reference = self.attempts.allocate("recipe-capture-v1", "recipe-capture")
        self.execution = self.reference.path / "execution"
        self.execution.mkdir(mode=0o700)

    def _attempts(self, owner: str) -> CoreManagedAttempts:
        return CoreManagedAttempts(
            workspace=self.workspace, configuration_home=self.config,
            state_root=self.state, locations={"evidence": self.evidence}, owner_id=owner,
        )

    def _open(self):
        return port.capture_execution_workspace(self.reference)

    def test_reopened_historical_ordinary_bytes_and_modes_match_v1_inventory(self) -> None:
        server = self.execution / "server.properties"
        server.write_bytes(b"original=true\n")
        server.chmod(0o644)
        historical = capture_workspace.replace_file(
            self.execution, "historical.txt", b"prior direct Core writer\n",
        )
        with (managed_attempts_scope(self.attempts), patch.object(port, "_host", HOST)):
            workspace = self._open()
            with patch.object(check_storage, "read_bytes", wraps=check_storage.read_bytes) as read:
                original = workspace.read_optional("server.properties")
            read.assert_called_once_with(server)
            self.assertEqual(b"original=true\n", original)
            self.assertEqual(b"prior direct Core writer\n", workspace.read_optional("historical.txt"))
            self.assertIn(historical, workspace.inventory())
            row = workspace.replace_file(
                "server.properties", b"selected=true\n",
                expected_sha256=sha256(original).hexdigest(),
            )
            self.assertEqual(0o644, row["mode"])
            self.assertEqual({"path": "eula.txt", "size": 10,
                              "sha256": sha256(b"eula=true\n").hexdigest(), "mode": 0o644},
                             workspace.replace_file("eula.txt", b"eula=true\n"))
            original_inventory = workspace.inventory()
        reopened_attempts = self._attempts("workbench-shell")
        with (managed_attempts_scope(reopened_attempts), patch.object(port, "_host", HOST)):
            reopened = port.capture_execution_workspace(
                reopened_attempts.open("recipe-capture-v1", "recipe-capture", self.reference.attempt_id),
            )
            self.assertEqual(b"selected=true\n", reopened.read_optional("server.properties"))
            self.assertEqual(b"eula=true\n", reopened.read_optional("eula.txt"))
            self.assertEqual(b"prior direct Core writer\n", reopened.read_optional("historical.txt"))
            self.assertEqual(original_inventory, reopened.inventory())
            self.assertEqual(capture_workspace.inventory(self.execution), reopened.inventory())

    def test_runtime_copy_uses_exact_attempt_child_and_reopens_historical_tree(self) -> None:
        self.execution.rmdir()
        runtime = self.reference.path / "runtime"
        (runtime / "groovy").mkdir(parents=True)
        (runtime / "groovy/recipe.groovy").write_bytes(b"selected recipe\n")
        (runtime / "tool").write_bytes(b"selected executable\n")
        (runtime / "tool").chmod(0o755)
        rows = capture_workspace.inventory(runtime)
        with (managed_attempts_scope(self.attempts), patch.object(port, "_host", HOST)):
            workspace = self._open()
            with patch.object(check_storage, "copy_manifest", wraps=check_storage.copy_manifest) as copied:
                execution = workspace.copy_runtime(rows)
            copied.assert_called_once()
            self.assertEqual((runtime, self.execution, rows), copied.call_args.args)
            self.assertEqual(self.execution, execution)
            self.assertEqual(rows, workspace.inventory())
        reopened_attempts = self._attempts("workbench-shell")
        with (managed_attempts_scope(reopened_attempts), patch.object(port, "_host", HOST)):
            reopened = port.capture_execution_workspace(
                reopened_attempts.open("recipe-capture-v1", "recipe-capture", self.reference.attempt_id),
            )
            self.assertEqual(rows, reopened.inventory())
            for row in rows:
                self.assertEqual((runtime / row["path"]).read_bytes(),
                                 reopened.read_optional(row["path"]))

    def test_cancelled_runtime_copy_retains_partial_execution_tree(self) -> None:
        self.execution.rmdir()
        runtime = self.reference.path / "runtime"
        runtime.mkdir()
        (runtime / "one.txt").write_bytes(b"first file\n")
        (runtime / "two.txt").write_bytes(b"second file\n")
        rows = capture_workspace.inventory(runtime)
        polls = 0

        def cancel_after_first_file() -> bool:
            nonlocal polls
            polls += 1
            return polls >= 3

        with (managed_attempts_scope(self.attempts), patch.object(port, "_host", HOST)):
            with self.assertRaisesRegex(check_storage.CheckStorageError,
                                        "managed copy cancelled; partial files retained"):
                self._open().copy_runtime(rows, cancelled=cancel_after_first_file)
        self.assertEqual(b"first file\n", (self.execution / "one.txt").read_bytes())
        self.assertFalse((self.execution / "two.txt").exists())
        self.assertEqual(b"second file\n", (runtime / "two.txt").read_bytes())

    def test_foreign_or_retargeted_attempt_refuses_before_write(self) -> None:
        foreign_attempts = self._attempts("other-owner")
        foreign = foreign_attempts.allocate("recipe-capture-v1", "recipe-capture")
        (foreign.path / "execution").mkdir(mode=0o700)
        with (managed_attempts_scope(self.attempts), patch.object(port, "_host", HOST),
              patch.object(check_storage, "copy_manifest") as copied):
            with self.assertRaises(ManagedAttemptError):
                port.capture_execution_workspace(foreign).copy_runtime([])
            with self.assertRaises(ManagedAttemptError):
                port.capture_execution_workspace(
                    replace(self.reference, path=foreign.path),
                ).copy_runtime([])
            copied.assert_not_called()
        self.assertEqual([], list((foreign.path / "execution").iterdir()))


if __name__ == "__main__":
    unittest.main()
