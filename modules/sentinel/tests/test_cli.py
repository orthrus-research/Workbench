from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZIP_STORED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/workbench-shell/src",
    ROOT / "modules/sentinel/src",
    ROOT / "profiles/platforms/cleanroom/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_sentinel.cli import main  # noqa: E402
from workbench_cleanroom_mixin_doctor import (  # noqa: E402
    inspect_artifact_paths,
)


POLICY = (
    ROOT
    / "profiles/platforms/cleanroom/mixins"
    / "cleanroom-mixin-doctor-policy-v1.json"
)


def _empty_archive(path: Path) -> None:
    with ZipFile(path, "w", compression=ZIP_STORED) as archive:
        info = ZipInfo("META-INF/MANIFEST.MF", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_STORED
        archive.writestr(info, b"Manifest-Version: 1.0\r\n\r\n")


class SentinelCliTests(unittest.TestCase):
    def test_human_view_uses_owner_finding_and_actionable_language(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "example.jar"
            _empty_archive(artifact)
            output = StringIO()
            code = main([str(artifact)], root=ROOT, output=output, error=StringIO())

        self.assertEqual(0, code)
        rendered = output.getvalue()
        self.assertIn("Sentinel · Cleanroom Mixin check", rendered)
        self.assertIn("Result: review", rendered)
        self.assertIn("cleanmix_version_compatibility.json", rendered)
        self.assertIn("CleanMix 0.7.0 silently falls back", rendered)
        self.assertIn("Next: Review the listed findings", rendered)
        self.assertIn(
            "Static registration does not prove configuration preparation or mixin application",
            rendered,
        )

    def test_json_is_the_complete_profile_owner_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "example.jar"
            _empty_archive(artifact)
            expected = inspect_artifact_paths([str(artifact)], policy_path=POLICY)
            output = StringIO()
            code = main(
                [str(artifact), "--json"],
                root=ROOT,
                output=output,
                error=StringIO(),
            )

        self.assertEqual(0, code)
        self.assertEqual(expected, json.loads(output.getvalue()))
        self.assertEqual(
            "workbench-cleanroom-mixin-doctor-report-v1",
            json.loads(output.getvalue())["format"],
        )

    def test_strict_maps_review_to_exit_one_after_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "example.jar"
            _empty_archive(artifact)
            output = StringIO()
            code = main(
                [str(artifact), "--strict"],
                root=ROOT,
                output=output,
                error=StringIO(),
            )

        self.assertEqual(1, code)
        self.assertIn("Result: review", output.getvalue())

    def test_invalid_archive_fails_without_traceback(self) -> None:
        output = StringIO()
        error = StringIO()
        code = main(
            ["missing.jar"],
            root=ROOT,
            output=output,
            error=error,
        )
        self.assertEqual(2, code)
        self.assertEqual("", output.getvalue())
        self.assertIn("Sentinel Mixin check failed:", error.getvalue())


if __name__ == "__main__":
    unittest.main()
