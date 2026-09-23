from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/runtime-explorer/src",
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/workbench-shell/src",
    ROOT / "modules/atlas/src",
):
    sys.path.insert(0, str(source))

from workbench_runtime_explorer.cli import main  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(base: Path) -> Path:
    project = base / "example"
    _write(project / "settings.gradle", "rootProject.name = 'example'\n")
    _write(project / "build.gradle", "plugins { id 'java' }\n")
    _write(
        project / "src/main/resources/mcmod.info",
        '[{"modid":"example","name":"Example","version":"1.0"}]\n',
    )
    _write(
        project / "src/main/java/example/ExampleMod.java",
        'package example;\npublic class ExampleMod { String id = "example:machine"; }\n',
    )
    return project


class CliTests(unittest.TestCase):
    def test_json_search_and_require_observed_exit_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _project(Path(temporary))
            output = StringIO()
            error = StringIO()
            code = main(
                [
                    "example:machine",
                    "--project",
                    str(project),
                    "--no-manuals",
                    "--json",
                ],
                root=ROOT,
                output=output,
                error=error,
            )
            value = json.loads(output.getvalue())
            required = main(
                [
                    "example:machine",
                    "--project",
                    str(project),
                    "--no-manuals",
                    "--require-observed",
                ],
                root=ROOT,
                output=StringIO(),
                error=StringIO(),
            )

        self.assertEqual(0, code)
        self.assertEqual("workbench-exact-runtime-explorer-result-v1", value["format"])
        self.assertEqual("unavailable", value["summary"]["runtime_coverage"])
        self.assertEqual(1, required)
        self.assertEqual("", error.getvalue())

    def test_noninteractive_empty_query_fails_safely(self) -> None:
        output = StringIO()
        error = StringIO()
        code = main(
            ["--no-project", "--no-manuals"],
            root=ROOT,
            input_stream=StringIO(),
            output=output,
            error=error,
        )
        self.assertEqual(2, code)
        self.assertIn("requires a terminal", error.getvalue())

    def test_option_filter_only_query_runs_without_a_tty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = _project(Path(temporary))
            output = StringIO()
            error = StringIO()
            code = main(
                [
                    "--project",
                    str(project),
                    "--no-manuals",
                    "--kind",
                    "class,resource-reference",
                    "--limit",
                    "1",
                    "--json",
                ],
                root=ROOT,
                input_stream=StringIO(),
                output=output,
                error=error,
            )
            value = json.loads(output.getvalue())

        self.assertEqual(0, code)
        self.assertEqual(
            ["class", "resource-reference"],
            value["query"]["filters"]["kind"],
        )
        self.assertGreaterEqual(value["summary"]["entities"], 2)
        self.assertEqual(1, value["summary"]["returned"])
        self.assertTrue(value["summary"]["truncated"])
        self.assertEqual("", error.getvalue())


if __name__ == "__main__":
    unittest.main()
