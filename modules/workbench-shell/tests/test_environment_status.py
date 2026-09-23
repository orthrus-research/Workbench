"""Focused tests for the read-only Workbench environment status surface."""

from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from importlib import metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_SOURCE = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell.catalog import build_catalog  # noqa: E402
from workbench_shell.cli import main as cli_main  # noqa: E402
from workbench_core.environment_status import (  # noqa: E402
    inspect_environment_status,
    render_environment_status,
)


MANIFEST = b"""\
[workspace]
name = "fixture"
requires-pixi = "==0.75.0"

[environments]
workspace = { features = [] }
"""
LOCK = b"version: 7\nenvironments:\n  default: {}\n  workspace: {}\n"


def _pixi_probe(*, available: bool = True) -> dict[str, object]:
    return {
        "state": "available" if available else "unavailable",
        "available": available,
        "executable": "/tools/pixi" if available else None,
        "discovery": "PATH",
        "version": "0.75.0" if available else None,
        "required_constraint": "==0.75.0",
        "matches_required_constraint": True if available else None,
    }


def _source_fixture(parent: Path) -> Path:
    root = parent / "workbench"
    root.mkdir()
    (root / "pixi.toml").write_bytes(MANIFEST)
    (root / "pixi.lock").write_bytes(LOCK)
    python = root / ".pixi/envs/workspace/bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python-fixture")
    package = root / ".pixi/envs/default/payload.bin"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"default-fixture")
    return root


class _Distribution:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read_text(self, filename: str) -> str | None:
        return self.files.get(filename)


class _TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def _human_status_result() -> dict[str, object]:
    return {
        "format": "workbench-environment-status-v2",
        "schema_version": 2,
        "operation_class": "read-only",
        "state": "ready",
        "execution": {
            "mode": "native-installed",
            "source_checkout": False,
        },
        "python": {
            "implementation": "CPython",
            "version": "3.13.14",
            "executable": "/runtime/python",
        },
        "pixi": {
            "available": False,
        },
        "identities": {
            "pixi_manifest": {
                "state": "present",
                "sha256": "1" * 64,
                "path": "/suite/pixi.toml",
            },
            "pixi_lock": {
                "state": "present",
                "sha256": "2" * 64,
                "path": "/suite/pixi.lock",
            },
            "installed_provenance": {
                "selected": "native-wheel",
            },
        },
        "source_environments": None,
        "findings": [],
        "guidance": {
            "repair": {
                "state": "reinstall-artifact",
                "command": None,
            },
            "garbage_collection": {
                "state": "not-applicable",
                "review_command": None,
            },
        },
    }


class EnvironmentStatusTests(unittest.TestCase):
    def test_pixi_probe_uses_the_executable_declared_by_an_active_pixi_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "pixi"
            executable.write_bytes(b"fixture")
            executable.chmod(0o755)
            completed = subprocess.CompletedProcess(
                [str(executable), "--version"],
                0,
                stdout="pixi 0.75.0\n",
                stderr="",
            )
            with patch(
                "workbench_core.environment_status.subprocess.run",
                return_value=completed,
            ) as run:
                from workbench_core.environment_status import _probe_pixi

                result = _probe_pixi(
                    "==0.75.0",
                    environ={"PIXI_EXE": str(executable), "PATH": ""},
                )
        self.assertEqual(result["state"], "available")
        self.assertEqual(result["discovery"], "PIXI_EXE")
        self.assertTrue(result["matches_required_constraint"])
        run.assert_called_once()

    def test_source_pixi_reports_identities_environments_and_footprint_read_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _source_fixture(Path(temporary))
            executable = root / ".pixi/envs/workspace/bin/python"
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            with (
                patch(
                    "workbench_core.environment_status._probe_pixi",
                    return_value=_pixi_probe(),
                ),
                patch("workbench_core.environment_status.sys.executable", str(executable)),
                patch(
                    "workbench_core.environment_status.sys.prefix",
                    str(executable.parent.parent),
                ),
            ):
                result = inspect_environment_status(root, environ={})

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual(result["state"], "ready")
            self.assertEqual(result["execution"]["mode"], "source-pixi")
            self.assertEqual(
                result["execution"]["active_pixi_environment"], "workspace"
            )
            self.assertEqual(
                result["identities"]["pixi_manifest"]["sha256"],
                sha256(MANIFEST).hexdigest(),
            )
            self.assertEqual(
                result["identities"]["pixi_lock"]["sha256"],
                sha256(LOCK).hexdigest(),
            )
            environments = {
                row["name"]: row
                for row in result["source_environments"]["environments"]
            }
            self.assertEqual(set(environments), {"default", "workspace"})
            self.assertTrue(environments["workspace"]["active"])
            self.assertEqual(
                environments["workspace"]["footprint"]["logical_bytes"],
                len(b"python-fixture"),
            )
            self.assertFalse(result["guidance"]["repair"]["performed"])
            self.assertFalse(
                result["guidance"]["garbage_collection"]["performed"]
            )
            self.assertFalse(result["authority_boundaries"]["java"]["inspected"])

    def test_source_alternate_is_attention_and_gives_non_mutating_guidance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _source_fixture(Path(temporary))
            with (
                patch(
                    "workbench_core.environment_status._probe_pixi",
                    return_value=_pixi_probe(available=False),
                ),
                patch("workbench_core.environment_status.sys.executable", "/usr/bin/python3"),
                patch("workbench_core.environment_status.sys.prefix", "/usr"),
            ):
                result = inspect_environment_status(root, environ={})

            self.assertEqual(result["state"], "attention")
            self.assertEqual(result["execution"]["mode"], "source-alternate")
            self.assertIn(
                "Workbench is running outside its declared Pixi environments",
                result["findings"],
            )
            self.assertEqual(
                result["guidance"]["repair"]["command"],
                ["pixi", "install", "--locked", "--no-config"],
            )
            candidates = result["guidance"]["garbage_collection"][
                "inactive_environment_candidates"
            ]
            self.assertEqual(
                {row["name"] for row in candidates}, {"default", "workspace"}
            )

    def test_native_install_needs_no_pixi_or_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch("workbench_core.environment_status._probe_pixi", return_value=_pixi_probe(available=False)),
                patch("workbench_core.environment_status._installed_provenance", return_value={
                    "selected": "native-wheel", "version": "0.1.3",
                    "direct_archive": {"state": "absent"}, "artifact_verified": False,
                }),
            ):
                result = inspect_environment_status(root, environ={})
            self.assertEqual("ready", result["state"])
            self.assertEqual("native-installed", result["execution"]["mode"])
            self.assertIsNone(result["source_environments"])
            self.assertFalse(result["pixi"]["required_for_packaged_runtime"])
            self.assertFalse(result["identities"]["installed_provenance"]["artifact_verified"])
            self.assertEqual("reinstall-artifact", result["guidance"]["repair"]["state"])
            self.assertEqual([], list(root.iterdir()))

    def test_old_environment_variable_cannot_claim_a_native_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch("workbench_core.environment_status._probe_pixi", return_value=_pixi_probe(available=False)),
                patch("workbench_core.environment_status._installed_provenance", return_value={
                    "selected": "absent", "direct_archive": {"state": "absent"},
                }),
            ):
                result = inspect_environment_status(root, environ={"WORKBENCH_PACKAGED_SUITE_ROOT": str(root)})
            self.assertEqual("unknown", result["state"])
            self.assertFalse(result["execution"]["packaged"])

    def test_invalid_native_metadata_is_not_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("workbench_core.environment_status._probe_pixi", return_value=_pixi_probe(available=False)),
                patch("workbench_core.environment_status._installed_provenance", return_value={
                    "selected": "native-wheel", "direct_archive": {"state": "invalid"},
                }),
            ):
                result = inspect_environment_status(Path(temporary), environ={})
            self.assertEqual("attention", result["state"])
            self.assertIn("Installed Core archive metadata is invalid", result["findings"])

    def test_unknown_mode_preserves_uncertainty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch(
                "workbench_core.environment_status._probe_pixi",
                return_value=_pixi_probe(available=False),
            ):
                result = inspect_environment_status(root, environ={})
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["execution"]["mode"], "unknown")
            self.assertFalse(result["authority"]["normative"])

    def test_human_status_uses_semantic_color_only_for_a_tty(self) -> None:
        ready = _human_status_result()
        colored = render_environment_status(
            ready,
            stream=_TtyBuffer(),
            environ={},
        )
        self.assertIn("\x1b[32m[READY]\x1b[0m", colored)
        self.assertIn("\x1b[33m[OPTIONAL]\x1b[0m", colored)
        self.assertNotIn("\x1b[31m", colored)

        missing = _human_status_result()
        missing["state"] = "unknown"
        missing["execution"]["mode"] = "unknown"
        missing["identities"]["pixi_manifest"] = {
            "state": "missing",
            "path": "/suite/pixi.toml",
        }
        missing["findings"] = ["Pixi manifest is missing"]
        blocked = render_environment_status(
            missing,
            stream=_TtyBuffer(),
            environ={},
        )
        self.assertIn("\x1b[31m[MISSING]\x1b[0m", blocked)

        redirected = render_environment_status(
            ready,
            stream=io.StringIO(),
            environ={},
        )
        self.assertNotIn("\x1b[", redirected)
        self.assertIn("[READY]", redirected)
        self.assertIn("[OPTIONAL]", redirected)

    def test_human_status_honors_no_color_on_a_tty(self) -> None:
        human = render_environment_status(
            _human_status_result(),
            stream=_TtyBuffer(),
            environ={"NO_COLOR": ""},
        )

        self.assertNotIn("\x1b[", human)
        self.assertIn("[READY]", human)
        self.assertIn("[OPTIONAL]", human)

    def test_cli_json_and_human_views_share_the_same_status_record(self) -> None:
        result = _human_status_result()
        json_output = _TtyBuffer()
        with (
            patch("workbench_shell.cli.inspect_environment_status", return_value=result) as inspect,
            redirect_stdout(json_output),
        ):
            status = cli_main(
                [
                    "environment",
                    "status",
                    "--suite-root",
                    str(REPOSITORY_ROOT),
                    "--json",
                ]
            )
        self.assertEqual(status, 0)
        self.assertNotIn("\x1b[", json_output.getvalue())
        self.assertEqual(json.loads(json_output.getvalue()), result)
        inspect.assert_called_once_with(REPOSITORY_ROOT.resolve())

        human = render_environment_status(result)
        self.assertIn("Mode: Installed Workbench", human)
        self.assertIn("Installed provenance: native-wheel", human)
        self.assertIn("No files, environments, caches, or Java runtimes were changed.", human)
        self.assertIn("sha256 " + "1" * 12 + "…", human)
        self.assertNotIn("1" * 64, human)
        self.assertNotIn("/suite/pixi.toml", human)

        redirected_human = io.StringIO()
        with (
            patch("workbench_shell.cli.inspect_environment_status", return_value=result),
            redirect_stdout(redirected_human),
        ):
            status = cli_main(
                [
                    "environment",
                    "status",
                    "--suite-root",
                    str(REPOSITORY_ROOT),
                ]
            )
        self.assertEqual(0, status)
        self.assertNotIn("\x1b[", redirected_human.getvalue())

    def test_catalog_exposes_one_read_only_environment_status_action(self) -> None:
        command = build_catalog(REPOSITORY_ROOT).command("environment.status")
        self.assertEqual(command.suite_id, "shell")
        self.assertEqual(command.risk, "read-only")
        argv, intent = command.build_argv(
            {"json": True}, root=REPOSITORY_ROOT, execute=True
        )
        self.assertEqual(intent, "execute")
        self.assertEqual(argv[2:], ["environment", "status", "--json"])
        self.assertTrue(any("Java remains separate" in row for row in command.limitations))

    def test_product_router_exposes_environment_status(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "tools/workbench.py"),
                "environment",
                "status",
                "--json",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["format"], "workbench-environment-status-v2")
        self.assertEqual(result["operation_class"], "read-only")


if __name__ == "__main__":
    unittest.main()
