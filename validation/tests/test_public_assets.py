"""Public asset provenance and deterministic rendering checks."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PublicAssetTests(unittest.TestCase):
    def test_brand_declares_no_third_party_visual_input(self) -> None:
        source = json.loads(
            (ROOT / "assets/brand/workbench-brand-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "repository-native-geometric-construction",
            source["origin"]["kind"],
        )
        self.assertFalse(source["origin"]["third_party_visual_input"])
        self.assertEqual("LGPL-3.0-only", source["origin"]["license"])

    def test_client_marks_match_the_canonical_geometry(self) -> None:
        completed = subprocess.run(
            [sys.executable, "tools/render_brand_assets.py", "check"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_intellij_notice_uses_the_repository_notice(self) -> None:
        notice = ROOT / "NOTICE.md"
        build_script = (
            ROOT / "clients/intellij-community/build.gradle.kts"
        ).read_text(encoding="utf-8")

        self.assertTrue(notice.is_file())
        self.assertIn('from("../../NOTICE.md")', build_script)
        self.assertIn('into("META-INF")', build_script)
        self.assertFalse(
            (
                ROOT
                / "clients/intellij-community/src/main/resources/META-INF/NOTICE.md"
            ).exists(),
            "the IntelliJ client must not maintain a second NOTICE copy",
        )

if __name__ == "__main__":
    unittest.main()
