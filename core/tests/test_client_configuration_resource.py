"""The installed client carries the default pack and Java policy selection."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
import tomllib
import unittest

from workbench_core.configuration import load_workbench_configuration
from workbench_core.runtime_java import load_java_runtime_policy, select_managed_java_policy


ROOT = Path(__file__).resolve().parents[2]


class ClientConfigurationResourceTests(unittest.TestCase):
    def test_packaged_selection_matches_source_and_supports_java_25_and_8(self) -> None:
        manifest = Path(str(files("workbench_core").joinpath("data/client-workbench.toml")))
        packaged = tomllib.loads(manifest.read_text(encoding="utf-8"))
        source = tomllib.loads((ROOT / "workbench.toml").read_text(encoding="utf-8"))
        self.assertEqual(source["selection"], packaged["selection"])
        self.assertEqual({}, packaged["bindings"])

        configuration = load_workbench_configuration(ROOT, manifest)
        policy = load_java_runtime_policy(ROOT, configuration=configuration)
        self.assertEqual(25, select_managed_java_policy(policy, None)["feature_version"])
        self.assertEqual(8, select_managed_java_policy(policy, 8)["feature_version"])


if __name__ == "__main__":
    unittest.main()
