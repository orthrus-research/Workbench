"""A working allocation is accessible only through an explicit Core host scope."""

from pathlib import Path
import unittest

from workbench_api.working_allocations import (
    WorkingAllocationError, WorkingAllocationReference,
    working_allocations, working_allocations_scope,
)


class WorkingAllocationScopeTests(unittest.TestCase):
    def test_scope_restores_host_after_nested_failure(self) -> None:
        first = object()
        second = object()
        with self.assertRaisesRegex(WorkingAllocationError, "no managed working-allocation host"):
            working_allocations()
        with working_allocations_scope(first):
            self.assertIs(first, working_allocations())
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with working_allocations_scope(second):
                    self.assertIs(second, working_allocations())
                    raise RuntimeError("interrupted")
            self.assertIs(first, working_allocations())
        with self.assertRaises(WorkingAllocationError):
            working_allocations()

    def test_reference_carries_identity_and_stable_path(self) -> None:
        reference = WorkingAllocationReference(
            "workbench-working-allocation-v1:" + "a" * 32,
            "worldgen-iteration", "trial", Path("/evidence/trial"),
            Path("/workspace"), "crucible",
        )
        self.assertEqual("trial", reference.path.name)
        self.assertEqual("crucible", reference.owner_id)


if __name__ == "__main__":
    unittest.main()
