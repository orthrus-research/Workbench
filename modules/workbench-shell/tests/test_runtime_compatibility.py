"""Focused tests for explicit disposable compatibility patches."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.runtime_compatibility import (  # noqa: E402
    RuntimeCompatibilityError,
    apply_compatibility_patches,
)


class RuntimeCompatibilityTest(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        *,
        entry: bytes = b"prefix flag suffix",
    ) -> tuple[Path, Path, str, str]:
        projection = root / "projection"
        target = projection / ".minecraft/mods/example.jar"
        target.parent.mkdir(parents=True)
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(
                "example/Target.class",
                entry,
            )
        with zipfile.ZipFile(target) as archive:
            class_bytes = archive.read("example/Target.class")
        patch_path = root / "patch.json"
        patch_path.write_text(
            json.dumps({
                "format": "workbench-runtime-compatibility-patch-v1",
                "schema_version": 1,
                "patch_id": "test:flag-to-arg3",
                "target": {
                    "path": ".minecraft/mods/example.jar",
                    "sha256": sha256(target.read_bytes()).hexdigest(),
                    "entry": "example/Target.class",
                    "entry_sha256": sha256(class_bytes).hexdigest(),
                    "find_utf8": "flag",
                    "replace_utf8": "arg3",
                    "expected_matches": 1,
                },
            }),
            encoding="utf-8",
        )
        return (
            projection,
            patch_path,
            sha256(class_bytes).hexdigest(),
            sha256(target.read_bytes()).hexdigest(),
        )

    def test_applies_identity_bound_patch_to_projection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            projection, patch, before_entry, before_jar = self._fixture(
                Path(temporary)
            )
            records = apply_compatibility_patches(projection, [patch])

            self.assertEqual(records[0]["patch_id"], "test:flag-to-arg3")
            self.assertEqual(records[0]["jar_sha256_before"], before_jar)
            self.assertEqual(records[0]["entry_sha256_before"], before_entry)
            self.assertEqual(records[0]["matches"], 1)
            with zipfile.ZipFile(
                projection / ".minecraft/mods/example.jar"
            ) as archive:
                self.assertEqual(
                    archive.read("example/Target.class"),
                    b"prefix arg3 suffix",
                )

    def test_rejects_target_drift_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            projection, patch, _before_entry, _before_jar = self._fixture(
                Path(temporary)
            )
            target = projection / ".minecraft/mods/example.jar"
            target.write_bytes(target.read_bytes() + b"drift")

            with self.assertRaisesRegex(
                RuntimeCompatibilityError,
                "target identity changed",
            ):
                apply_compatibility_patches(projection, [patch])

    def test_pack_patch_spec_is_present_and_identity_bound(self) -> None:
        spec = (
            REPOSITORY_ROOT
            / "profiles/packs/supersymmetry/compatibility/"
            "recurrent-complex-1.4.8.6-structure-context-arg3-v1.json"
        )
        payload = json.loads(spec.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["target"]["sha256"],
            "253226e6c7efe61ae255df0cc2e19d1420945cb7e86f7cd79f51d5db10fd9de8",
        )
        self.assertEqual(payload["target"]["find_utf8"], "flag")
        self.assertEqual(payload["target"]["replace_utf8"], "arg3")

        text_spec = (
            REPOSITORY_ROOT
            / "profiles/packs/supersymmetry/compatibility/"
            "rtg-warm-overworld-selector-for-biomes-v1.json"
        )
        text_payload = json.loads(text_spec.read_text(encoding="utf-8"))
        self.assertEqual(
            text_payload["target"]["sha256"],
            "3575fdaba0827aad2e1c4b581b4744a2d08e49e99a6f1ca50295b8964c261de6",
        )
        self.assertIn(
            "forAllBiomesExcept(",
            text_payload["target"]["find_utf8"],
        )
        self.assertIn("forBiomes(", text_payload["target"]["replace_utf8"])

    def test_applies_identity_bound_text_patch_to_projection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projection = root / "projection"
            target = projection / ".minecraft/config/example.cfg"
            target.parent.mkdir(parents=True)
            target.write_text(
                "warm = forAllBiomesExcept(plains, forest)\n",
                encoding="utf-8",
            )
            original = target.read_bytes()
            patch_path = root / "text-patch.json"
            patch_path.write_text(
                json.dumps({
                    "format": (
                        "workbench-runtime-compatibility-text-patch-v1"
                    ),
                    "schema_version": 1,
                    "patch_id": "test:warm-selector",
                    "target": {
                        "path": ".minecraft/config/example.cfg",
                        "sha256": sha256(original).hexdigest(),
                        "find_utf8": "warm = forAllBiomesExcept(",
                        "replace_utf8": "warm = forBiomes(",
                        "expected_matches": 1,
                    },
                }),
                encoding="utf-8",
            )

            records = apply_compatibility_patches(projection, [patch_path])

            self.assertEqual(records[0]["operation"], "text-file-replacement")
            self.assertEqual(records[0]["file_sha256_before"], sha256(original).hexdigest())
            self.assertEqual(records[0]["matches"], 1)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "warm = forBiomes(plains, forest)\n",
            )

    def test_text_patch_rejects_unexpected_match_count_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projection = root / "projection"
            target = projection / ".minecraft/config/example.cfg"
            target.parent.mkdir(parents=True)
            target.write_text("warm = forBiomes(plains)\n", encoding="utf-8")
            original = target.read_bytes()
            patch_path = root / "text-patch.json"
            patch_path.write_text(
                json.dumps({
                    "format": (
                        "workbench-runtime-compatibility-text-patch-v1"
                    ),
                    "schema_version": 1,
                    "patch_id": "test:warm-selector",
                    "target": {
                        "path": ".minecraft/config/example.cfg",
                        "sha256": sha256(original).hexdigest(),
                        "find_utf8": "forAllBiomesExcept(",
                        "replace_utf8": "forBiomes(",
                        "expected_matches": 1,
                    },
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeCompatibilityError,
                "match count changed",
            ):
                apply_compatibility_patches(projection, [patch_path])

            self.assertEqual(target.read_bytes(), original)

    def test_installs_identity_bound_file_overlay_into_projection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projection = root / "projection"
            mods = projection / ".minecraft/mods"
            mods.mkdir(parents=True)
            source = root / "experiment.jar"
            source.write_bytes(b"qualified experiment")
            patch_path = root / "file-overlay.json"
            patch_path.write_text(
                json.dumps({
                    "format": (
                        "workbench-runtime-compatibility-file-overlay-v1"
                    ),
                    "schema_version": 1,
                    "patch_id": "test:file-overlay",
                    "source": {
                        "path": "experiment.jar",
                        "sha256": sha256(source.read_bytes()).hexdigest(),
                    },
                    "target": {
                        "path": ".minecraft/mods/experiment.jar",
                        "must_be_absent": True,
                    },
                }),
                encoding="utf-8",
            )

            records = apply_compatibility_patches(projection, [patch_path])

            target = mods / "experiment.jar"
            self.assertEqual(target.read_bytes(), b"qualified experiment")
            self.assertEqual(records[0]["operation"], "file-overlay")
            self.assertEqual(records[0]["source_path"], "experiment.jar")
            self.assertEqual(
                records[0]["source_sha256"],
                sha256(source.read_bytes()).hexdigest(),
            )

    def test_file_overlay_refuses_to_replace_an_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projection = root / "projection"
            target = projection / ".minecraft/mods/experiment.jar"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing")
            source = root / "experiment.jar"
            source.write_bytes(b"replacement")
            patch_path = root / "file-overlay.json"
            patch_path.write_text(
                json.dumps({
                    "format": (
                        "workbench-runtime-compatibility-file-overlay-v1"
                    ),
                    "schema_version": 1,
                    "patch_id": "test:file-overlay",
                    "source": {
                        "path": "experiment.jar",
                        "sha256": sha256(source.read_bytes()).hexdigest(),
                    },
                    "target": {
                        "path": ".minecraft/mods/experiment.jar",
                        "must_be_absent": True,
                    },
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                RuntimeCompatibilityError,
                "already exists",
            ):
                apply_compatibility_patches(projection, [patch_path])

            self.assertEqual(target.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
