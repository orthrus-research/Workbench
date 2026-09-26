"""Core supervises the V1 fixture child while retaining the projection lease."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
RUNNER = ROOT / "profiles/platforms/cleanroom/tools/run_generic_mod_fixture_build.py"


class SupervisedFixtureBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.state = self.base / "state"
        self.config = self.base / "config"
        self.java = self.base / "jdk25"
        (self.java / "bin").mkdir(parents=True)
        (self.java / "release").write_text('JAVA_VERSION="25.0.1"\n', encoding="utf-8")
        executable = self.java / "bin/java"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        self.gradle = self.base / "gradle"
        self._gradle_script(exit_code=0)

    def _gradle_script(self, *, exit_code: int, tamper: bool = False) -> None:
        self.gradle.write_text(
            f"#!{sys.executable}\n"
            "import fcntl, os, pathlib, sys\n"
            "project = pathlib.Path(sys.argv[sys.argv.index('-p') + 1])\n"
            "state = pathlib.Path(os.environ['GRADLE_USER_HOME']).parents[1]\n"
            "assert pathlib.Path(os.environ['JAVA_HOME']).name == 'jdk25'\n"
            "assert os.environ['TZ'] == 'UTC'\n"
            "assert pathlib.Path(sys.argv[sys.argv.index('--project-cache-dir') + 1]).is_dir()\n"
            "with (state / '.cleanroom-fixture-build.lock').open('rb') as lease:\n"
            "    try:\n"
            "        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    except BlockingIOError:\n"
            "        print('projection-parent-lease-held')\n"
            "    else:\n"
            "        raise SystemExit(42)\n"
            "projection_locks = list((pathlib.Path(os.environ['WORKBENCH_CONFIG_HOME']) / "
            "'resources-v1/reusable-projections/leases').glob('*.lock'))\n"
            "assert len(projection_locks) == 1\n"
            "with projection_locks[0].open('rb') as lease:\n"
            "    try:\n"
            "        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    except BlockingIOError:\n"
            "        print('projection-catalog-lease-held')\n"
            "    else:\n"
            "        raise SystemExit(43)\n"
            "target = (project / '../../../../../.workbench/build/cleanroom/0.6.8-alpha/"
            "generic-mod-daily-loop/fixture-marker.txt').resolve()\n"
            "target.parent.mkdir(parents=True, exist_ok=True)\n"
            "target.write_text('generated build state', encoding='utf-8')\n"
            + ("(project / 'build.gradle').write_text('tampered', encoding='utf-8')\n" if tamper else "")
            + "print('fixture-child-stderr', file=sys.stderr)\n"
            + f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        self.gradle.chmod(0o700)

    def _run(self, *extra: str, supervised: bool = True) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["WORKBENCH_CONFIG_HOME"] = str(self.config)
        return subprocess.run(
            [sys.executable, str(RUNNER), "--gradle-cmd", str(self.gradle),
             "--java-home", str(self.java), "--state-root", str(self.state),
             "--core-supervised" if supervised else "--legacy-exec", *extra],
            cwd=ROOT, env=environment, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )

    def test_child_observes_parent_lease_and_reuses_fixed_cache(self) -> None:
        self.state.mkdir(mode=0o700)
        old_home = self.state / "gradle-home/generic-mod-daily-loop"
        old_home.mkdir(parents=True, mode=0o755)
        old_home.chmod(0o755)
        sentinel = old_home / "reuse.txt"
        sentinel.write_bytes(b"retained cache")
        first = self._run()
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertIn("projection-parent-lease-held", first.stdout)
        self.assertIn("projection-catalog-lease-held", first.stdout)
        self.assertIn("fixture-child-stderr", first.stderr)
        cache = self.state / "gradle-project-cache/generic-mod-daily-loop"
        home = self.state / "gradle-home/generic-mod-daily-loop"
        self.assertTrue(cache.is_dir())
        self.assertTrue(home.is_dir())
        from workbench_profile_cleanroom import fixture_build as owner
        digest = owner._validate_fixture()
        generated = (
            self.state / "source-projections/cleanroom" / digest[7:]
            / owner.GENERATED_ROOTS[0] / "fixture-marker.txt"
        )
        self.assertEqual("generated build state", generated.read_text(encoding="utf-8"))
        self.assertEqual(b"retained cache", sentinel.read_bytes())
        registered = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.config / "resources-v1/stores").glob("*.json")
        ]
        self.assertEqual(
            {
                str(self.state / "source-projections/cleanroom"),
                str(cache), str(home), str(self.state / "fixture-build-attempts"),
            },
            {row["root"] for row in registered},
        )
        if os.name == "posix":
            self.assertEqual(0o700, home.stat().st_mode & 0o777)
        second = self._run()
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(b"retained cache", sentinel.read_bytes())
        attempts = sorted((self.state / "fixture-build-attempts").iterdir())
        self.assertEqual(2, len(attempts))
        for attempt in attempts:
            receipt = json.loads((attempt / "capture.json").read_text(encoding="utf-8"))
            self.assertEqual("complete", receipt["state"])
            self.assertEqual(0, receipt["exit_code"])

    def test_failure_exit_and_source_tamper_are_read_back(self) -> None:
        self._gradle_script(exit_code=7)
        failed = self._run()
        self.assertEqual(7, failed.returncode, failed.stderr)
        self.assertIn("projection-parent-lease-held", failed.stdout)
        self._gradle_script(exit_code=0, tamper=True)
        tampered = self._run()
        self.assertEqual(2, tampered.returncode)
        self.assertIn("projection source changed", tampered.stderr)
        attempts = sorted((self.state / "fixture-build-attempts").iterdir())
        self.assertEqual(2, len(attempts))
        self.assertEqual(
            [0, 7], sorted(json.loads((attempt / "capture.json").read_text())["exit_code"]
                           for attempt in attempts),
        )

    def test_java_8_is_rejected_before_state_creation(self) -> None:
        (self.java / "release").write_text('JAVA_VERSION="1.8.0_442"\n', encoding="utf-8")
        result = self._run()
        self.assertEqual(2, result.returncode)
        self.assertIn("requires Java 25", result.stderr)
        self.assertFalse(self.state.exists())

    def test_supervised_preflight_is_read_only_and_legacy_mode_remains_explicit(self) -> None:
        inspected = self._run("--check-only")
        self.assertEqual(0, inspected.returncode, inspected.stderr)
        self.assertIn("workbench-cleanroom-fixture-build-preflight-v1", inspected.stdout)
        self.assertFalse(self.state.exists())

        self.gradle.write_text(
            f"#!{sys.executable}\nprint('legacy-fixture-child')\n",
            encoding="utf-8",
        )
        self.gradle.chmod(0o700)
        legacy = self._run(supervised=False)
        self.assertEqual(0, legacy.returncode, legacy.stderr)
        self.assertIn("legacy-fixture-child", legacy.stdout)
        self.assertFalse((self.state / "fixture-build-attempts").exists())


if __name__ == "__main__":
    unittest.main()
