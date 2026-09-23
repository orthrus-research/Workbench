"""Focused tests for the current Pixi platform and bootstrap authority."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from jsonschema.validators import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import workbench_platforms as platforms  # noqa: E402


EXPECTED_TARGETS = (
    "linux-x86-64",
    "linux-arm64",
    "windows-x86-64",
    "windows-arm64",
    "macos-x86-64",
    "macos-arm64",
)


class PlatformMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = platforms.validate_repository_contract(ROOT)
        self.rows = {
            row["target"]: row for row in platforms.platform_rows(self.matrix)
        }

    def test_schema_and_strict_parser_accept_the_current_matrix(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "packaging/pixi/schemas/workbench-platform-matrix-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(self.matrix)
        self.assertEqual("workbench-platform-matrix-v1", self.matrix["format"])
        self.assertEqual(EXPECTED_TARGETS, tuple(self.rows))
        self.assertNotIn("claims", self.matrix)
        self.assertNotIn("qualification_authority", self.matrix)

    def test_parser_rejects_ambiguous_or_unpinned_inputs(self) -> None:
        for label, mutate in (
            (
                "duplicate target",
                lambda value: value["platforms"][1].update(
                    target=value["platforms"][0]["target"]
                ),
            ),
            (
                "moving URL",
                lambda value: value["platforms"][0]["bootstrap"].update(
                    url="https://github.com/prefix-dev/pixi/releases/latest/download/pixi.tar.gz"
                ),
            ),
            (
                "extra authority",
                lambda value: value.update(qualification_authority={}),
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                changed = deepcopy(self.matrix)
                mutate(changed)
                path = Path(temporary) / "matrix.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(platforms.PlatformMatrixError):
                    platforms.load_platform_matrix(path)

    def test_packaging_and_pixi_manifest_use_the_same_platform_rows(self) -> None:
        all_platforms = platforms.pixi_platforms(self.matrix)
        native_targets = platforms.implementation_targets(
            self.matrix, "compatibility_environment"
        )
        self.assertEqual(set(EXPECTED_TARGETS), set(all_platforms))
        self.assertNotIn("windows-arm64", native_targets)

        manifest = tomllib.loads((ROOT / "pixi.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            list(
                platforms.implementation_targets(
                    self.matrix, "workspace_environment"
                )
            ),
            manifest["feature"]["workspace-tools"]["platforms"],
        )
        self.assertEqual(
            list(native_targets), manifest["feature"]["compatibility-runtime"]["platforms"]
        )

    def test_bootstrap_assets_are_exact_and_cli_validates_repository(self) -> None:
        pixi = self.matrix["pixi"]
        self.assertEqual("0.75.0", pixi["version"])
        self.assertEqual("==0.75.0", pixi["requirement"])
        for row in self.rows.values():
            bootstrap = row["bootstrap"]
            self.assertIn("/releases/download/v0.75.0/", bootstrap["url"])
            self.assertNotIn("/latest/", bootstrap["url"])
            self.assertEqual(64, len(bootstrap["archive_sha256"]))
            self.assertEqual(64, len(bootstrap["executable_sha256"]))

        completed = subprocess.run(
            [sys.executable, "tools/workbench_platforms.py"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("internally consistent", completed.stdout)


if __name__ == "__main__":
    unittest.main()
