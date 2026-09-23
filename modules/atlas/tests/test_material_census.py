#!/usr/bin/env python3

"""Focused tests for the live Atlas material census."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src/workbench_atlas"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import workbench_atlas.material_census as material_census


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", message)
    return _git(root, "rev-parse", "HEAD")


class MaterialCensusTest(unittest.TestCase):

    def _repository(self, parent: Path) -> Path:
        root = parent / "pack"
        root.mkdir()
        _git(root, "init", "--quiet")
        _git(root, "config", "user.name", "Atlas Test")
        _git(root, "config", "user.email", "atlas@example.invalid")
        return root

    def test_census_uses_current_tracked_bytes_and_masks_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(Path(temporary))
            source = root / "groovy/material/Materials.groovy"
            source.parent.mkdir(parents=True)
            source.write_text(
                "// new Material.Builder(20000, SuSyUtility.susyId('fake'))\n"
                "new Material.Builder(20001, SuSyUtility.susyId('one'))\n"
                "/* new Material.Builder(20002, 'also_fake') */\n"
                "new Material.Builder(20003, \"two\")\n",
                encoding="utf-8",
            )
            _commit(root, "baseline")
            source.write_text(
                source.read_text(encoding="utf-8")
                + "new Material.Builder(20004, oddExpression())\n",
                encoding="utf-8",
            )
            (root / "groovy/untracked.groovy").write_text(
                "new Material.Builder(20005, 'untracked')\n",
                encoding="utf-8",
            )

            result = material_census.census_material_builders(root)

            self.assertEqual(result["occupied_values"], [20001, 20003, 20004])
            self.assertEqual(result["registry_names"], ["one", "two"])
            self.assertEqual(len(result["uncertainties"]), 1)
            self.assertEqual(
                result["uncertainties"][0]["kind"],
                "unparsed-material-name",
            )
            self.assertEqual(result["workspace"]["root_uri"], root.as_uri())
            self.assertTrue(
                result["census_id"].startswith(
                    "atlas-material-census:sha256:"
                )
            )

    def test_resolves_pinned_material_and_localization_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = self._repository(parent)
            material_path = root / "groovy/material/Materials.groovy"
            language_path = root / "resources/lang/en_us.lang"
            material_path.parent.mkdir(parents=True)
            language_path.parent.mkdir(parents=True)
            material_bytes = (
                "new Material.Builder(20000, SuSyUtility.susyId('oil'))\n"
                "        .liquid()\n"
                "        .build()\n"
            ).encode("utf-8")
            language_bytes = b"susy.material.oil=Oil\n"
            material_path.write_bytes(material_bytes)
            language_path.write_bytes(language_bytes)
            revision = _commit(root, "pinned")
            material_digest = sha256(material_bytes).hexdigest()
            language_digest = sha256(language_bytes).hexdigest()
            authority_path = parent / "authorities.json"
            authority_path.write_text(json.dumps({
                "citations": [{
                    "id": "CIT-PACK-OIL-MATERIAL",
                    "source_id": "SRC-PACK",
                    "revision": revision,
                    "path": "groovy/material/Materials.groovy",
                    "file_sha256": material_digest,
                    "anchor": (
                        "new Material.Builder(20000, "
                        "SuSyUtility.susyId('oil'))"
                    ),
                }],
            }), encoding="utf-8")
            queries = [
                {
                    "query_id": "CIT-PACK-OIL-MATERIAL",
                    "result_id": (
                        f"SRC-PACK@{revision}:CIT-PACK-OIL-MATERIAL"
                    ),
                },
                {
                    "query_id": (
                        f"SRC-PACK@{revision}:resources/lang/en_us.lang"
                        "#susy.material.oil"
                    ),
                    "result_id": f"source-file:sha256:{language_digest}",
                },
            ]

            results = material_census.resolve_standard_queries(
                root,
                queries,
                authority_registry_path=authority_path,
            )

            self.assertEqual(
                results[0]["result"]["registration"],
                {
                    "form": "liquid",
                    "material_id": 20000,
                    "namespace_helper": "SuSyUtility.susyId",
                    "registry_name": "oil",
                },
            )
            self.assertEqual(
                results[1]["result"]["localization"],
                {"key": "susy.material.oil", "value": "Oil"},
            )

    def test_census_installed_runtime_without_creating_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "minecraft"
            material = root / "groovy/material/RuntimeMaterials.groovy"
            material.parent.mkdir(parents=True)
            material.write_text(
                "// new Material.Builder(20000, 'commented')\n"
                "new Material.Builder(20001, SuSyUtility.susyId('runtime_oil'))\n"
                "new Material.Builder(20002, dynamicName())\n",
                encoding="utf-8",
            )

            first = material_census.census_material_directory(root)
            second = material_census.census_material_directory(root)

            self.assertEqual(first, second)
            self.assertEqual(first["occupied_values"], [20001, 20002])
            self.assertEqual(first["registry_names"], ["runtime_oil"])
            self.assertEqual(len(first["uncertainties"]), 1)
            self.assertTrue(
                first["workspace"]["revision"].startswith(
                    "content-set:sha256:"
                )
            )
            self.assertEqual(
                first["workspace"]["selection"],
                "current-runtime-groovy-regular-files",
            )
            self.assertFalse((root / ".git").exists())


if __name__ == "__main__":
    unittest.main()
