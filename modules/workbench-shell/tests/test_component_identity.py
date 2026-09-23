"""Tests for the public Core identity consumed by source-derived registries."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import tomllib
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.component_identity import (  # noqa: E402
    CoreComponentIdentityError,
    load_core_component_identity,
)


class CoreComponentIdentityTests(unittest.TestCase):
    def test_repository_declares_the_current_public_core(self) -> None:
        project = tomllib.loads((REPOSITORY_ROOT / "core/pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(
            ("workbench-core", project["version"]),
            load_core_component_identity(REPOSITORY_ROOT),
        )

    def test_missing_or_wrong_distribution_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaises(CoreComponentIdentityError):
                load_core_component_identity(root)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "workbench-core"\nversion = "0.1.3"\n',
                encoding="utf-8",
            )
            with self.assertRaises(CoreComponentIdentityError):
                load_core_component_identity(root)
            (root / "core").mkdir()
            (root / "core/pyproject.toml").write_text(
                '[project]\nname = "workbench-shell"\nversion = "0.1.3"\n',
                encoding="utf-8",
            )
            with self.assertRaises(CoreComponentIdentityError):
                load_core_component_identity(root)

    def test_non_semantic_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "core").mkdir()
            (root / "core/pyproject.toml").write_text(
                '[project]\nname = "workbench-core"\nversion = "current"\n',
                encoding="utf-8",
            )
            with self.assertRaises(CoreComponentIdentityError):
                load_core_component_identity(root)

    def test_native_prerelease_version_is_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "core").mkdir()
            (root / "core/pyproject.toml").write_text(
                '[project]\nname = "workbench-core"\nversion = "1.0.0rc1"\n', encoding="utf-8",
            )
            self.assertEqual(load_core_component_identity(root), ("workbench-core", "1.0.0rc1"))


if __name__ == "__main__":
    unittest.main()
