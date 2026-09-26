"""Scoped check port cannot be used outside an admitted Core dispatch."""

import unittest

from workbench_api.check_attempts import CheckAttemptError, check_attempts, check_attempts_scope


class CheckAttemptScopeTests(unittest.TestCase):
    def test_nested_scope_restores_outer_provider(self):
        outer, inner = object(), object()
        with self.assertRaises(CheckAttemptError):
            check_attempts()
        with check_attempts_scope(outer):
            self.assertIs(outer, check_attempts())
            with check_attempts_scope(inner):
                self.assertIs(inner, check_attempts())
            self.assertIs(outer, check_attempts())
        with self.assertRaises(CheckAttemptError):
            check_attempts()


if __name__ == "__main__":
    unittest.main()
