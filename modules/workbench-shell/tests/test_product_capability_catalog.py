"""Tests for the live public product capability catalog."""

from __future__ import annotations

from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.catalog import build_catalog  # noqa: E402
from workbench_shell.product_capability_catalog import (  # noqa: E402
    DIRECT_ACTION_FORMAT,
    FORMAT,
    SCHEMA_RELATIVE,
    ProductCapabilityCatalogError,
    build_product_capability_catalog,
    load_product_capability_catalog,
    validate_product_capability_catalog,
)


DIRECT_ACTION_KEYS = (
    "project.acquire",
    "review.pull-request",
    "review.recipes",
)


class ProductCapabilityCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_product_capability_catalog(ROOT)

    def test_catalog_is_the_live_command_catalog(self) -> None:
        command_catalog = build_catalog(ROOT)
        self.assertEqual(FORMAT, self.catalog["format"])
        self.assertEqual(
            command_catalog.catalog_digest,
            self.catalog["command_catalog_digest"],
        )
        self.assertEqual(
            [command.command_id for command in command_catalog.commands],
            [
                row["capability_key"]
                for row in self.catalog["capabilities"][: len(command_catalog.commands)]
            ],
        )
        self.assertEqual(
            list(DIRECT_ACTION_KEYS),
            [
                row["capability_key"]
                for row in self.catalog["capabilities"][len(command_catalog.commands) :]
            ],
        )
        self.assertEqual(self.catalog, load_product_capability_catalog(ROOT))
        self.assertNotIn("projection_id", self.catalog)

    def test_suite_counts_include_console_and_direct_handlers(self) -> None:
        counts = Counter(
            row["catalog_action"]["suite_id"]
            for row in self.catalog["capabilities"]
        )
        self.assertEqual(
            counts,
            {
                row["suite_id"]: row["command_count"]
                for row in self.catalog["suites"]
            },
        )

    def test_public_schema_accepts_generated_catalog(self) -> None:
        schema = json.loads((ROOT / SCHEMA_RELATIVE).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(self.catalog)

    def test_catalog_contains_no_development_coordination_axes(self) -> None:
        rendered = json.dumps(self.catalog, sort_keys=True)
        for forbidden in (
            "owner_state",
            "evidence_level",
            "task_evidence_refs",
            '"project_id"',
            "/program/",
            "AGENTS.md",
            '"placement"',
            '"projection_id"',
        ):
            self.assertNotIn(forbidden, rendered)

    def test_registered_handler_state_is_exact(self) -> None:
        command_catalog = build_catalog(ROOT)
        rows = {
            row["capability_key"]: row for row in self.catalog["capabilities"]
        }
        for command in command_catalog.commands:
            row = rows[command.command_id]
            self.assertTrue(row["handler"]["registered"])
            self.assertEqual(
                "process" if command.executable else "document",
                row["handler"]["kind"],
            )
            self.assertEqual(
                command.executable and command.availability != "unavailable",
                row["handler"]["executable"],
            )
            self.assertEqual(command.availability, row["availability"])

        direct = {key: rows[key] for key in DIRECT_ACTION_KEYS}
        self.assertEqual("mutating", direct["project.acquire"]["risk"])
        self.assertEqual("mutating", direct["review.pull-request"]["risk"])
        self.assertEqual("writes-output", direct["review.recipes"]["risk"])
        for row in direct.values():
            self.assertEqual("process", row["handler"]["kind"])
            self.assertTrue(row["handler"]["registered"])
            self.assertTrue(row["handler"]["executable"])
            self.assertRegex(
                row["catalog_action"]["action_digest"],
                r"^sha256:[0-9a-f]{64}$",
            )

    def test_direct_handler_intents_are_searchable(self) -> None:
        self.assertEqual(
            "workbench-direct-cli-action-binding-v1",
            DIRECT_ACTION_FORMAT,
        )
        cases = {
            "acquire": "project.acquire",
            "prepare pull request": "review.pull-request",
            "recipe review": "review.recipes",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools/workbench.py"),
                        "capabilities",
                        query,
                        "--json",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual("", completed.stderr)
                result = json.loads(completed.stdout)
                self.assertIn(
                    expected,
                    [row["capability_key"] for row in result["capabilities"]],
                )

    def test_stale_or_promoted_catalog_is_rejected(self) -> None:
        stale = deepcopy(self.catalog)
        stale["capabilities"][0]["availability"] = "available"
        with self.assertRaises(ProductCapabilityCatalogError):
            validate_product_capability_catalog(
                stale,
                repository_root=ROOT,
            )

    def test_explicit_snapshot_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps(self.catalog), encoding="utf-8")
            self.assertEqual(
                self.catalog,
                load_product_capability_catalog(ROOT, path=path),
            )


if __name__ == "__main__":
    unittest.main()
