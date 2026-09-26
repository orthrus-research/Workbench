"""The managed-tree API is bound for one admitted Core operation."""

import unittest

from workbench_api.managed_trees import ManagedTreeError, managed_trees, managed_trees_scope


class ManagedTreeScopeTests(unittest.TestCase):
    def test_nested_scope_restores_outer_provider(self) -> None:
        first, second = object(), object()
        with self.assertRaisesRegex(ManagedTreeError, "no managed tree host"):
            managed_trees()
        with managed_trees_scope(first):
            self.assertIs(first, managed_trees())
            with managed_trees_scope(second):
                self.assertIs(second, managed_trees())
            self.assertIs(first, managed_trees())
        with self.assertRaises(ManagedTreeError):
            managed_trees()


if __name__ == "__main__":
    unittest.main()
