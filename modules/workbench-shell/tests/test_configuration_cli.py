#!/usr/bin/env python3

"""Focused tests for the current-only Workbench configuration CLI."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_SOURCE = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.cli import (  # noqa: E402
    CONFIGURATION_COMMAND_BINDINGS,
    main as cli_main,
)
from workbench_core.configuration import (  # noqa: E402
    load_workbench_configuration,
)


def _write_suite(root: Path, *, manifest_name: str = "workbench.toml") -> Path:
    pack = root / "profiles/packs/example/profile.yaml"
    platform = root / "profiles/platforms/cleanroom/test.yaml"
    pack.parent.mkdir(parents=True)
    platform.parent.mkdir(parents=True)
    pack.write_text(
        "schema_version: 1\n"
        "profile_family_id: workbench-pack:example\n"
        "profiles:\n"
        "  cleanroom-test:\n"
        "    platform_profile_id: workbench-platform:cleanroom:test\n",
        encoding="utf-8",
    )
    platform.write_text(
        "schema_version: 1\n"
        "profile_id: workbench-platform:cleanroom:test\n"
        "kind: cleanroom\n",
        encoding="utf-8",
    )
    manifest = root / manifest_name
    manifest.write_text(
        'schema = "workbench/config/v1"\n'
        "\n[selection]\n"
        'pack_document = "profiles/packs/example/profile.yaml"\n'
        'pack_variant = "cleanroom-test"\n'
        'platform_document = "profiles/platforms/cleanroom/test.yaml"\n'
        "\n[bindings]\n"
        'java_candidate_home = { env = "CLI_JAVA_HOME" }\n',
        encoding="utf-8",
    )
    return manifest


def _run(*arguments: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        status = cli_main(list(arguments))
    return status, stdout.getvalue(), stderr.getvalue()


class WorkbenchConfigurationCliTests(unittest.TestCase):
    def test_validate_does_not_resolve_or_require_host_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            with patch.dict(
                os.environ,
                {
                    "CLI_JAVA_HOME": "relative/is/invalid/when/resolved",
                },
                clear=True,
            ):
                status, output, error = _run(
                    "config",
                    "validate",
                    "--suite-root",
                    str(suite),
                    "--json",
                )

            self.assertEqual(0, status, error)
            result = json.loads(output)
            self.assertEqual("workbench-configuration-validation-v1", result["format"])
            self.assertEqual("valid", result["outcome"])
            self.assertEqual("workbench/config/v1", result["configuration_schema"])
            self.assertEqual(
                "workbench-pack:example",
                result["selection"]["pack"]["profile_id"],
            )
            self.assertEqual(1, len(result["binding_declarations"]))
            self.assertNotIn("bindings", result)

    def test_resolve_snapshots_provenance_and_exact_consumed_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            environment = {"CLI_JAVA_HOME": "/opt/workbench/java"}
            with patch.dict(os.environ, environment, clear=True):
                status, output, error = _run(
                    "config",
                    "resolve",
                    "--for",
                    "runtime-java",
                    "--suite-root",
                    str(suite),
                    "--json",
                )

            self.assertEqual(0, status, error)
            result = json.loads(output)
            self.assertEqual("workbench-configuration-resolution-v1", result["format"])
            self.assertEqual("runtime/java-v1", result["command"]["operation_id"])
            self.assertEqual(
                ["java_candidate_home"],
                result["command"]["consumed_bindings"],
            )
            bindings = {row["name"]: row for row in result["bindings"]}
            self.assertEqual("/opt/workbench/java", bindings["java_candidate_home"]["value"])
            self.assertEqual(
                {"kind": "environment", "variable": "CLI_JAVA_HOME"},
                bindings["java_candidate_home"]["source"],
            )
            self.assertTrue(bindings["java_candidate_home"]["consumed"])

            configuration = load_workbench_configuration(suite)
            expected = configuration.resolve_bindings(
                environment,
                names=("java_candidate_home",),
            ).operation_digest(
                "runtime/java-v1",
                ["java_candidate_home"],
            )
            self.assertEqual(expected, result["operation_binding_digest"])

    def test_undeclared_values_do_not_change_java_operation_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)

            def resolve(unrelated: str) -> str:
                with patch.dict(
                    os.environ,
                    {
                        "CLI_JAVA_HOME": "/opt/workbench/java",
                        "UNDECLARED_HOST_VALUE": unrelated,
                    },
                    clear=True,
                ):
                    status, output, error = _run(
                        "config",
                        "resolve",
                        "--for",
                        "runtime-java",
                        "--suite-root",
                        str(suite),
                        "--json",
                    )
                self.assertEqual(0, status, error)
                return json.loads(output)["operation_binding_digest"]

            self.assertEqual(
                resolve("unrelated-host-a"),
                resolve("unrelated-host-b"),
            )

    def test_explicit_config_path_and_closed_command_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite, manifest_name="selected.toml")
            status, output, error = _run(
                "config",
                "validate",
                "--suite-root",
                str(suite),
                "--config",
                "selected.toml",
                "--json",
            )
            self.assertEqual(0, status, error)
            self.assertEqual(
                "selected.toml",
                json.loads(output)["manifest"]["suite_relative_path"],
            )
            self.assertEqual(
                {"inspect", "runtime-plan", "runtime-java"},
                set(CONFIGURATION_COMMAND_BINDINGS),
            )

            with self.assertRaises(SystemExit) as rejected, redirect_stderr(io.StringIO()):
                cli_main(
                    [
                        "config",
                        "resolve",
                        "--for",
                        "invented-command",
                        "--suite-root",
                        str(suite),
                    ]
                )
            self.assertEqual(2, rejected.exception.code)


if __name__ == "__main__":
    unittest.main()
