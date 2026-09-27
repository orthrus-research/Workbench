"""The installed client carries the default pack and Java policy selection."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from workbench_core.configuration import (
    WorkbenchConfigurationError,
    default_client_configuration_path,
    load_workbench_configuration,
)
from workbench_core.runtime_java import (
    JavaRuntimeError,
    ensure_java_runtime,
    load_java_runtime_policy,
    select_managed_java_policy,
)


ROOT = Path(__file__).resolve().parents[2]


class ClientConfigurationResourceTests(unittest.TestCase):
    def test_client_default_uses_source_manifest_or_bundled_manifest(self) -> None:
        self.assertEqual(Path("workbench.toml"), default_client_configuration_path(ROOT))
        self.assertEqual(25, load_java_runtime_policy(ROOT)["feature_version"])
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary) / "site-packages/workbench_resources"
            for relative in (
                "profiles/packs/supersymmetry/profile.yaml",
                "profiles/platforms/cleanroom/provisional.yaml",
            ):
                destination = suite / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            bundled = default_client_configuration_path(suite)
            self.assertEqual("client-workbench.toml", bundled.name)
            self.assertTrue(bundled.is_file())
            self.assertEqual(25, load_java_runtime_policy(suite)["feature_version"])
            with patch(
                "workbench_core.runtime_java.resolve_temurin_asset",
                side_effect=JavaRuntimeError("asset request reached"),
            ):
                with self.assertRaisesRegex(JavaRuntimeError, "asset request reached"):
                    ensure_java_runtime(
                        suite, state_root=suite / "state", candidates=(),
                        host={"os": "linux", "architecture": "x64", "system": "Linux", "machine": "x86_64"},
                    )
            with self.assertRaisesRegex(JavaRuntimeError, "cannot be opened safely"):
                ensure_java_runtime(suite, config_path=suite / "missing.toml", candidates=())

            # A present but invalid source manifest must still fail closed.
            (suite / "workbench.toml").write_text("invalid = [", encoding="utf-8")
            self.assertEqual(Path("workbench.toml"), default_client_configuration_path(suite))
            with self.assertRaises(WorkbenchConfigurationError):
                load_workbench_configuration(suite, default_client_configuration_path(suite))
            with self.assertRaisesRegex(JavaRuntimeError, "strict UTF-8 TOML"):
                load_java_runtime_policy(suite)

            incomplete_source = Path(temporary) / "incomplete-source"
            incomplete_source.mkdir()
            self.assertEqual(
                Path("workbench.toml"),
                default_client_configuration_path(incomplete_source),
            )
            with self.assertRaisesRegex(JavaRuntimeError, "cannot be opened safely"):
                load_java_runtime_policy(incomplete_source)

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
