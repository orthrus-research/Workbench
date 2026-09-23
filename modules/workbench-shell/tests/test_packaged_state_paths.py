from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPOSITORY_ROOT / "modules/workbench-shell/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_api.state_paths import (  # noqa: E402
    PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE,
    default_suite_state_root,
)


class PackagedStatePathsTests(unittest.TestCase):
    def test_source_suite_keeps_ignored_checkout_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary) / "source-suite"
            suite.mkdir()
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(suite / ".workbench", default_suite_state_root(suite))

    def test_installed_suite_uses_external_platform_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = root / "installed-suite"
            platform_state = root / "user-state" / "developer-features"
            suite.mkdir()
            with (
                mock.patch.dict(
                    os.environ,
                    {PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE: str(suite)},
                    clear=True,
                ),
                mock.patch(
                    "workbench_api.state_paths.default_feature_state_root",
                    return_value=platform_state,
                ),
            ):
                self.assertEqual(
                    root / "user-state" / "runtime",
                    default_suite_state_root(suite),
                )

    def test_explicit_state_root_remains_the_installed_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = root / "installed-suite"
            selected = root / "selected-state"
            suite.mkdir()
            with mock.patch.dict(
                os.environ,
                {
                    PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE: str(suite),
                    "WORKBENCH_STATE_ROOT": str(selected),
                },
                clear=True,
            ):
                self.assertEqual(selected, default_suite_state_root(suite))


if __name__ == "__main__":
    unittest.main()
