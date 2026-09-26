"""Runtime state defaults honor the supplied environment."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

from workbench_api.state_paths import default_feature_state_root, default_runtime_state_root


class StatePathTests(unittest.TestCase):
    @unittest.skipUnless(os.name != "nt" and sys.platform != "darwin", "Linux state layout")
    def test_injected_home_selects_the_same_default_as_a_process_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            feature = home / ".local/state/workbench/developer-features"
            self.assertEqual(feature, default_feature_state_root(environment=environment))
            self.assertEqual(feature.parent / "runtime", default_runtime_state_root(environment=environment))


if __name__ == "__main__":
    unittest.main()
