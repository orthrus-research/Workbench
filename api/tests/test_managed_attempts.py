"""The attempt API is available only during an admitted Core dispatch."""

from pathlib import Path
import unittest

from workbench_api.managed_attempts import (
    ManagedAttemptError,
    ManagedAttemptReference,
    managed_attempts,
    managed_attempts_scope,
)


class ManagedAttemptScopeTests(unittest.TestCase):
    def test_scope_restores_previous_provider_even_after_failure(self) -> None:
        outer = object()
        inner = object()
        with self.assertRaisesRegex(ManagedAttemptError, "no managed attempt host"):
            managed_attempts()
        with managed_attempts_scope(outer):
            self.assertIs(outer, managed_attempts())
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with managed_attempts_scope(inner):
                    self.assertIs(inner, managed_attempts())
                    raise RuntimeError("interrupted")
            self.assertIs(outer, managed_attempts())
        with self.assertRaises(ManagedAttemptError):
            managed_attempts()

    def test_reference_is_an_identity_and_path_handle(self) -> None:
        reference = ManagedAttemptReference(
            "recipe-capture-v1", "recipe-capture-" + "a" * 32,
            Path("/evidence"), Path("/evidence/attempt"), "store-id",
        )
        self.assertEqual("recipe-capture-v1", reference.family)
        self.assertEqual("store-id", reference.store_id)


if __name__ == "__main__":
    unittest.main()
