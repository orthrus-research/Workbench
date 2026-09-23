#!/usr/bin/env python3

"""Conformance checks for profile-owned registration knowledge."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from _support import SOURCE_ROOT, WORKBENCH_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import registration_catalog  # noqa: E402


CATALOG_PATH = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/registration/catalog-v1.json"
)


class RegistrationCatalogTest(unittest.TestCase):

    def test_catalog_records_priority_and_all_investigated_surfaces(self) -> None:
        catalog = registration_catalog.load_registration_catalog(CATALOG_PATH)

        self.assertEqual(
            [row["id"] for row in catalog["authority_order"]],
            ["groovy", "susycore", "pack-data"],
        )
        self.assertEqual(len(catalog["families"]), 36)
        self.assertEqual(
            {row["key"] for row in catalog["patterns"]},
            {
                "material-backed-fluid",
                "machine-recipe",
                "ore-dictionary-entry",
            },
        )
        self.assertEqual(
            {
                row["id"]: row["selected_authority"]
                for row in catalog["families"]
                if row["id"] in {
                    "materials",
                    "recipe-maps-builders-properties",
                    "gregtech-worldgen",
                }
            },
            {
                "materials": "groovy",
                "recipe-maps-builders-properties": "susycore",
                "gregtech-worldgen": "pack-data",
            },
        )
        self.assertRegex(catalog["catalog_id"], r"^sha256:[0-9a-f]{64}$")

    def test_catalog_rejects_bypassing_the_first_constructor(self) -> None:
        value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        materials = next(
            row for row in value["families"] if row["id"] == "materials"
        )
        materials["selected_authority"] = "susycore"

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                registration_catalog.RegistrationCatalogError,
                "first constructor",
            ):
                registration_catalog.load_registration_catalog(path)


if __name__ == "__main__":
    unittest.main()
