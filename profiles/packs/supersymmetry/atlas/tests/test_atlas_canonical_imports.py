"""Import-boundary coverage for the retained Atlas causal provenance chain."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[5]
ATLAS_SOURCE = ROOT / "modules/atlas/src"
PROFILE_SOURCE = ROOT / "profiles/packs/supersymmetry/atlas/src"
LEGACY_MODULE_SOURCE = ATLAS_SOURCE / "workbench_atlas"


class AtlasCanonicalImportTests(unittest.TestCase):
    def _run_isolated(self, paths: list[Path], modules: tuple[str, ...], *, supported: bool = True) -> None:
        script = f"""
import importlib
import sys

sys.path[:0] = {list(map(str, paths))!r}
if not {supported!r}:
    # Model an absent canonical installation even when the test interpreter
    # itself has editable packages installed.
    class MissingCanonicalAtlas:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "workbench_atlas":
                raise ModuleNotFoundError("Canonical Atlas package is absent", name=fullname)
    sys.meta_path.insert(0, MissingCanonicalAtlas())
for module_name in {modules!r}:
    importlib.import_module(module_name)
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if supported:
            self.assertEqual(0, completed.returncode, completed.stderr)
        else:
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("ModuleNotFoundError", completed.stderr)

    def test_public_source_roots_load_retained_causal_chain(self) -> None:
        self._run_isolated(
            [PROFILE_SOURCE, ROOT / "profiles/packs/supersymmetry/src", ATLAS_SOURCE,
             ROOT / "api/src", ROOT / "modules/pack-program-studio/src",
             ROOT / "modules/project-intelligence/src", ROOT / "modules/material-semantics/src"],
            (
                "atlas_source_index",
                "atlas_pack_mutations",
                "workbench_atlas.atlas_causal_provenance_contract",
                "workbench_atlas.atlas_provenance_normalizer",
                "workbench_atlas.atlas_causal_query",
                "workbench_atlas.atlas_causal_projection",
            ),
        )

    def test_legacy_bare_module_path_is_not_a_supported_installation(self) -> None:
        self._run_isolated(
            [PROFILE_SOURCE, LEGACY_MODULE_SOURCE],
            (
                "atlas_source_index",
                "atlas_pack_mutations",
                "atlas_provenance_normalizer",
                "atlas_causal_query",
                "atlas_causal_projection",
            ),
            supported=False,
        )


if __name__ == "__main__":
    unittest.main()
