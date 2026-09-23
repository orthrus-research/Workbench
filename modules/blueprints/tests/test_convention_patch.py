#!/usr/bin/env python3

"""Focused checks for profile-owned aggregate-file construction."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from _support import SOURCE_ROOT, WORKBENCH_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import convention_patch  # noqa: E402


PATTERN_PATH = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/blueprints/experimental/patterns/"
    "material-backed-fluid-v1.json"
)


def _fixture(root: Path) -> None:
    material = root / "groovy/material"
    material.mkdir(parents=True)
    (material / "SuSyMaterials.groovy").write_text(
        "package material\n\n"
        "class SuSyMaterials {\n\n"
        "    // Petrochem Materials\n\n"
        "    public static Material ExistingFluid\n\n"
        "    // First Degree Materials A\n"
        "}\n",
        encoding="utf-8",
    )
    (material / "PetrochemistryMaterials.groovy").write_text(
        "package material\n\n"
        "import static material.SuSyMaterials.*\n\n"
        "class PetrochemistryMaterials {\n\n"
        "    static void register() {\n\n"
        "        ExistingFluid = new Material.Builder(20000, "
        "SuSyUtility.susyId('existing_fluid'))\n"
        "                .liquid()\n"
        "                .color(0x111111)\n"
        "                .build()\n"
        "    }\n"
        "}",
        encoding="utf-8",
    )
    language = root / "resources/langfiles/lang/en_us.lang"
    language.parent.mkdir(parents=True)
    language.write_text(
        "# Fluids\n\n"
        "susy.material.existing_fluid=Existing Fluid\n"
        "\n# Thermodynamics\n",
        encoding="utf-8",
    )


class ConventionPatchTest(unittest.TestCase):

    def setUp(self) -> None:
        self.pattern = convention_patch.load_pattern(PATTERN_PATH)
        self.parameters = {
            "color": "0x425d73",
            "material_id": 20008,
            "name": "Pilot Coolant",
            "registry_name": "pilot_coolant",
            "symbol_name": "PilotCoolant",
            "translation": "Pilot Coolant",
        }

    def test_renders_the_three_current_supersymmetry_aggregate_updates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _fixture(root)

            first = convention_patch.render_updates(
                self.pattern, root, self.parameters
            )
            second = convention_patch.render_updates(
                self.pattern, root, self.parameters
            )

            self.assertEqual(first, second)
            self.assertEqual(
                [row["path"] for row in first],
                [
                    "groovy/material/PetrochemistryMaterials.groovy",
                    "groovy/material/SuSyMaterials.groovy",
                    "resources/langfiles/lang/en_us.lang",
                ],
            )
            rendered = {
                row["path"]: row["content"].decode("utf-8") for row in first
            }
            self.assertIn(
                "public static Material PilotCoolant\n\n"
                "    // First Degree Materials A",
                rendered["groovy/material/SuSyMaterials.groovy"],
            )
            self.assertIn(
                "PilotCoolant = new Material.Builder(20008, "
                "SuSyUtility.susyId('pilot_coolant'))\n"
                "                .liquid()\n"
                "                .color(0x425d73)\n"
                "                .flags(FLAMMABLE)\n"
                "                .build()",
                rendered["groovy/material/PetrochemistryMaterials.groovy"],
            )
            self.assertIn(
                "susy.material.pilot_coolant=Pilot Coolant\n\n"
                "# Thermodynamics",
                rendered["resources/langfiles/lang/en_us.lang"],
            )

    def test_crlf_targets_render_only_crlf_update_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _fixture(root)
            for edit in self.pattern["edits"]:
                path = root / edit["path"]
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

            rendered = convention_patch.render_updates(
                self.pattern, root, self.parameters
            )

            self.assertEqual(len(rendered), 3)
            for operation in rendered:
                with self.subTest(path=operation["path"]):
                    content = operation["content"]
                    self.assertIn(b"\r\n", content)
                    without_crlf = content.replace(b"\r\n", b"")
                    self.assertNotIn(b"\r", without_crlf)
                    self.assertNotIn(b"\n", without_crlf)

    def test_rejects_mixed_or_bare_cr_target_line_endings(self) -> None:
        relative = Path("groovy/material/PetrochemistryMaterials.groovy")
        expected = (
            "edit target groovy/material/PetrochemistryMaterials.groovy "
            "has mixed or bare-CR line endings"
        )
        for variant in ("mixed", "bare-cr"):
            with self.subTest(variant=variant):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    _fixture(root)
                    path = root / relative
                    content = path.read_bytes()
                    if variant == "mixed":
                        content = content.replace(b"\n", b"\r\n", 1)
                    else:
                        content = content.replace(b"\n", b"\r")
                    path.write_bytes(content)

                    with self.assertRaises(
                        convention_patch.ConventionPatchError
                    ) as raised:
                        convention_patch.render_updates(
                            self.pattern, root, self.parameters
                        )
                    self.assertEqual(str(raised.exception), expected)

    def test_rejects_existing_symbol_and_structural_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _fixture(root)
            declaration = root / "groovy/material/SuSyMaterials.groovy"
            declaration.write_text(
                declaration.read_text(encoding="utf-8").replace(
                    "    // First Degree Materials A\n",
                    "    public static Material PilotCoolant\n\n"
                    "    // First Degree Materials A\n",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                convention_patch.ConventionPatchError,
                "identity already exists",
            ):
                convention_patch.render_updates(
                    self.pattern, root, self.parameters
                )

            declaration.write_text(
                declaration.read_text(encoding="utf-8").replace(
                    "    // First Degree Materials A\n", ""
                ).replace("    public static Material PilotCoolant\n\n", ""),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                convention_patch.ConventionPatchError,
                "required guard is not unique",
            ):
                convention_patch.render_updates(
                    self.pattern, root, self.parameters
                )

    def test_allocates_only_inside_the_profile_petrochemistry_envelope(
        self,
    ) -> None:
        occupied = list(range(20000, 20008)) + [20009, 31000]
        self.assertEqual(
            convention_patch.allocate_first_free(self.pattern, occupied),
            20008,
        )


if __name__ == "__main__":
    unittest.main()
