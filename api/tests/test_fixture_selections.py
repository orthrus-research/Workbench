"""Fixture locations require an explicit Core host binding."""

from pathlib import Path
import unittest

from workbench_api.fixture_selections import (
    FixtureSelectionHostError, fixture_selections, fixture_selections_scope,
)


class _Host:
    def register(self, profile, workspace, runtime, java_home):
        return {"profile": profile, "workspace": str(workspace)}

    def resolve(self, profile, workspace=None, *, runtime=None, java_home=None):
        return {"profile": profile, "runtime": str(runtime)}


class FixtureSelectionPortTests(unittest.TestCase):
    def test_scope_is_required_and_restored(self):
        with self.assertRaises(FixtureSelectionHostError):
            fixture_selections()
        host = _Host()
        with fixture_selections_scope(host):
            self.assertIs(fixture_selections(), host)
            self.assertEqual("/runtime", fixture_selections().resolve(
                "fixture:pack", Path("/workspace"), runtime=Path("/runtime"),
            )["runtime"])
        with self.assertRaises(FixtureSelectionHostError):
            fixture_selections()


if __name__ == "__main__":
    unittest.main()
