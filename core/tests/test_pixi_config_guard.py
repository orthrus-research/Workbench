"""Pixi project-local configuration is never ambient exact-build input."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.pixi_config_guard import (  # noqa: E402
    PixiProjectConfigError,
    require_project_local_config_absent,
)


class PixiConfigGuardTests(unittest.TestCase):
    def test_absent_project_local_config_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            require_project_local_config_absent(Path(temporary))

    def test_every_existing_entry_type_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".pixi/config.toml"
            config.parent.mkdir()

            config.write_text("[mirrors]\n", encoding="utf-8")
            with self.assertRaisesRegex(PixiProjectConfigError, "regular file"):
                require_project_local_config_absent(root)

            config.unlink()
            config.mkdir()
            with self.assertRaisesRegex(
                PixiProjectConfigError, "non-regular filesystem entry"
            ):
                require_project_local_config_absent(root)

            config.rmdir()
            try:
                config.symlink_to(root / "missing-config.toml")
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symbolic links are unavailable: {error}")
            with self.assertRaisesRegex(PixiProjectConfigError, "symbolic link"):
                require_project_local_config_absent(root)


if __name__ == "__main__":
    unittest.main()
