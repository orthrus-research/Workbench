"""Core retains one owner-validated JAR snapshot after exact fixture execution."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core.environment_fixture_artifact_admission import (
    admit_fixture_artifact, inspect_fixture_artifact_admission,
    plan_fixture_artifact_admission, reopen_fixture_artifact_admission,
)
import workbench_core.environment_fixture_artifact_admission as admission_module
from workbench_core.environment_fixture_execution import (
    apply_fixture_execution, plan_fixture_execution,
)
from workbench_core.environment_reconstruction import ReconstructionError, _canonical

import test_environment_fixture_execution as execution_tests


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "profiles/platforms/cleanroom/src"))
from workbench_profile_cleanroom import fixture_artifact  # noqa: E402


def _jar() -> bytes:
    members = {
        "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\r\nMixinConfigs: mixins.workbench_daily_loop.json\r\n\r\n",
        "mcmod.info": b'[{"modid":"workbench_daily_loop","version":"1.0.0"}]',
        "pack.mcmeta": b"{}",
        "mixins.workbench_daily_loop.json": b"{}",
        "mixins.workbench_daily_loop.refmap.json": b'{"mappings":{"probe":"target"}}',
        "assets/workbench_daily_loop/lang/en_us.lang": b"probe",
        "assets/workbench_daily_loop/blockstates/probe_block.json": b"{}",
        "assets/workbench_daily_loop/models/block/probe_block.json": b"{}",
        "assets/workbench_daily_loop/models/item/probe_block.json": b"{}",
        "dev/workbench/dailyloop/DailyLoopMod.class": b"\xca\xfe\xba\xbeclass",
        "dev/workbench/dailyloop/DailyLoopContent.class": b"\xca\xfe\xba\xbeclass",
        "dev/workbench/dailyloop/DailyLoopProbe.class": b"\xca\xfe\xba\xbeclass",
        "dev/workbench/dailyloop/mixin/MixinBlock.class": b"\xca\xfe\xba\xbeclass",
    }
    output = BytesIO()
    with ZipFile(output, "w") as bundle:
        for name, raw in members.items():
            bundle.writestr(name, raw)
    return output.getvalue()


@skipIf(not sys.platform.startswith("linux"), "fixture admission uses Linux/WSL custody")
class EnvironmentFixtureArtifactAdmissionTests(TestCase):
    setUp = execution_tests.EnvironmentFixtureExecutionTests.setUp
    _refresh = execution_tests.EnvironmentFixtureExecutionTests._refresh
    _selection = execution_tests.EnvironmentFixtureExecutionTests._selection
    _retain_fixture_and_gradle = execution_tests.EnvironmentFixtureExecutionTests._retain_fixture_and_gradle
    _inputs = execution_tests.EnvironmentFixtureExecutionTests._inputs
    _arguments = execution_tests.EnvironmentFixtureExecutionTests._arguments
    _fake_gradle = execution_tests.EnvironmentFixtureExecutionTests._fake_gradle
    _ready = execution_tests.EnvironmentFixtureExecutionTests._ready
    _execution_kwargs = execution_tests.EnvironmentFixtureExecutionTests._kwargs

    def _add_locked_properties(self) -> None:
        source = self.owner.root / "gradle.properties"
        source.write_bytes((ROOT / "profiles/platforms/cleanroom/fixtures/"
                            "generic-mod-daily-loop/gradle.properties").read_bytes())
        row = {"path": "gradle.properties",
               "sha256": "sha256:" + sha256(source.read_bytes()).hexdigest(),
               "size": source.stat().st_size}
        files = sorted([*self.owner.lock["declared_values"]["files"], row],
                       key=lambda item: item["path"].encode("utf-8"))
        digest = "sha256:" + sha256(_canonical({
            "algorithm": "sha256-file-tree-v1", "files": files,
        })).hexdigest()
        self.owner.lock["declared_values"]["files"] = files
        self.owner.lock["declared_values"]["tree_digest"] = digest
        self.owner.lock["declared_values"]["identity"]["digest"] = digest
        self.owner.lock_path.write_bytes(_canonical(self.owner.lock) + b"\n")
        policy = self.owner.read_execution_policy()
        policy["fixture"]["tree_digest"] = digest
        self.owner.policy_path.write_text(json.dumps(policy), encoding="utf-8")

        def validate(lock):
            if lock != self.owner.lock:
                raise ValueError("owner lock changed")
            for locked in files:
                raw = (self.owner.root / locked["path"]).read_bytes()
                if (len(raw) != locked["size"]
                        or "sha256:" + sha256(raw).hexdigest() != locked["sha256"]):
                    raise ValueError("owner source changed")
            return lock
        self.owner.validate_owner_lock = validate

    def _start(self, *, exit_code: int = 0, real_filesystem: bool = False) -> None:
        self._add_locked_properties()
        self._fake_gradle(f"#!/bin/sh\nexit {exit_code}\n".encode())
        self._ready()
        self.command_owner.portable_artifact_spec = fixture_artifact.artifact_spec
        self.command_owner.inspect_portable_artifact = fixture_artifact.inspect_artifact_bytes
        bindings = [
            ("profile_extension_identity", self.candidate["profile_fixture"]["owner_code"]),
            ("require_profile_extension", self.command_owner),
        ]
        if not real_filesystem:
            bindings.append(("_qualified_filesystem", True))
        for name, value in bindings:
            replacement = patch(
                f"workbench_core.environment_fixture_artifact_admission.{name}",
                return_value=value,
            )
            replacement.start()
            self.addCleanup(replacement.stop)
        planned = plan_fixture_execution(
            self.suite, self.share, self.candidate, **self._execution_kwargs(),
        )
        self.execution = apply_fixture_execution(
            self.suite, self.share, self.candidate,
            expected_plan_id=planned["plan_id"], **self._execution_kwargs(),
        )
        self.execution_id = self.execution["resource"]["resource_id"]

    def _args(self) -> dict:
        return {**self._execution_kwargs(),
                "execution_result_resource_id": self.execution_id}

    def _write_jar(self, raw: bytes | None = None) -> Path:
        target = (
            self.project.parents[4] / ".workbench/build/cleanroom/0.6.8-alpha/"
            "generic-mod-daily-loop/libs/workbench-daily-loop-1.0.0.jar"
        )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(_jar() if raw is None else raw)
        return target

    def test_snapshot_admission_reopens_after_mutable_output_changes(self) -> None:
        self._start()
        self.assertEqual("not-started", inspect_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])
        target = self._write_jar()
        plan = plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )
        self.assertEqual("ready", plan["state"], plan["blockers"])
        result = admit_fixture_artifact(
            self.suite, self.share, self.candidate,
            expected_plan_id=plan["plan_id"], **self._args(),
        )
        self.assertEqual("owner-artifact-snapshot-admitted", result["state"])
        self.assertEqual("sha256:" + sha256(target.read_bytes()).hexdigest(),
                         result["artifact"]["sha256"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        target.write_bytes(b"later mutable output")
        reopened = reopen_fixture_artifact_admission(
            self.suite, self.share, self.candidate,
            admission_resource_id=result["resource"]["resource_id"], **self._args(),
        )
        self.assertEqual(result, reopened)
        self.assertEqual("blocked", plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])

    def test_zero_exit_required(self) -> None:
        self._start(exit_code=7)
        self._write_jar()
        with self.assertRaisesRegex(ReconstructionError, "zero-exit"):
            plan_fixture_artifact_admission(
                self.suite, self.share, self.candidate, **self._args(),
            )

    def test_redirected_output_and_invalid_jar_refuse_before_preparation(self) -> None:
        self._start()
        target = self._write_jar()
        other = target.with_name("redirected.jar")
        target.replace(other)
        target.symlink_to(other.name)
        with self.assertRaises(ReconstructionError):
            plan_fixture_artifact_admission(
                self.suite, self.share, self.candidate, **self._args(),
            )
        target.unlink()
        target.write_bytes(b"not a ZIP")
        with self.assertRaises(ReconstructionError):
            plan_fixture_artifact_admission(
                self.suite, self.share, self.candidate, **self._args(),
            )
        self.assertEqual("not-started", inspect_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])

    def test_changed_bytes_after_review_refuse_before_preparation(self) -> None:
        self._start()
        target = self._write_jar()
        plan = plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )
        target.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            admit_fixture_artifact(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._args(),
            )
        self.assertEqual("not-started", inspect_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])

    def test_interrupted_after_prepared_resource_stays_unknown(self) -> None:
        self._start()
        self._write_jar()
        plan = plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )
        original = admission_module._pinned_artifact
        calls = 0
        def interrupted(path):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("interrupted snapshot")
            return original(path)
        with patch.object(admission_module, "_pinned_artifact", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted snapshot"):
                admit_fixture_artifact(
                    self.suite, self.share, self.candidate,
                    expected_plan_id=plan["plan_id"], **self._args(),
                )
        self.assertEqual("unknown-after-preparation", inspect_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])
        self.assertEqual("blocked", plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])

    def test_interrupted_after_snapshot_publication_stays_unknown(self) -> None:
        self._start()
        self._write_jar()
        plan = plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )
        original = admission_module._pinned_artifact
        calls = 0
        def interrupted(path):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("interrupted after snapshot")
            return original(path)
        with patch.object(admission_module, "_pinned_artifact", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted after snapshot"):
                admit_fixture_artifact(
                    self.suite, self.share, self.candidate,
                    expected_plan_id=plan["plan_id"], **self._args(),
                )
        self.assertEqual("unknown-after-preparation", inspect_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])
        self.assertEqual("blocked", plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )["state"])

    def test_changed_retained_snapshot_refuses_reopen(self) -> None:
        self._start()
        self._write_jar()
        plan = plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )
        result = admit_fixture_artifact(
            self.suite, self.share, self.candidate,
            expected_plan_id=plan["plan_id"], **self._args(),
        )
        context = admission_module._context(
            self.suite, self.share, self.candidate, self._args(),
        )
        artifact = context["service"].describe(result["artifact_resource_id"])
        artifact.path.write_bytes(b"changed retained bytes")
        with self.assertRaises(ReconstructionError):
            reopen_fixture_artifact_admission(
                self.suite, self.share, self.candidate,
                admission_resource_id=result["resource"]["resource_id"], **self._args(),
            )

    def test_malformed_owner_spec_refuses_without_raw_type_errors(self) -> None:
        self._start()
        self._write_jar()
        self.command_owner.portable_artifact_spec = lambda **_: {"format": "bad"}
        with self.assertRaisesRegex(ReconstructionError, "invalid spec"):
            plan_fixture_artifact_admission(
                self.suite, self.share, self.candidate, **self._args(),
            )


@skipIf(not sys.platform.startswith("linux"), "fixture admission uses Linux/WSL custody")
class EnvironmentFixtureArtifactExt4Tests(TestCase):
    """Exercise the existing regular-file output on a qualified real mount."""

    _refresh = EnvironmentFixtureArtifactAdmissionTests._refresh
    _selection = EnvironmentFixtureArtifactAdmissionTests._selection
    _retain_fixture_and_gradle = EnvironmentFixtureArtifactAdmissionTests._retain_fixture_and_gradle
    _inputs = EnvironmentFixtureArtifactAdmissionTests._inputs
    _arguments = EnvironmentFixtureArtifactAdmissionTests._arguments
    _fake_gradle = EnvironmentFixtureArtifactAdmissionTests._fake_gradle
    _ready = EnvironmentFixtureArtifactAdmissionTests._ready
    _execution_kwargs = EnvironmentFixtureArtifactAdmissionTests._execution_kwargs
    _add_locked_properties = EnvironmentFixtureArtifactAdmissionTests._add_locked_properties
    _start = EnvironmentFixtureArtifactAdmissionTests._start
    _args = EnvironmentFixtureArtifactAdmissionTests._args
    _write_jar = EnvironmentFixtureArtifactAdmissionTests._write_jar

    def setUp(self) -> None:
        if not admission_module._qualified_filesystem(ROOT):
            self.skipTest("test checkout is not on a qualified Linux filesystem")
        private_tests = ROOT / ".workbench/test-tmp"
        private_tests.mkdir(parents=True, exist_ok=True)
        with patch("test_environment_input_candidates.TemporaryDirectory",
                   new=lambda: TemporaryDirectory(dir=private_tests)):
            EnvironmentFixtureArtifactAdmissionTests.setUp(self)

    def test_regular_output_on_ext4_admits_without_filesystem_mock(self) -> None:
        self._start(real_filesystem=True)
        target = self._write_jar()
        self.assertTrue(target.is_file())
        self.assertTrue(admission_module._qualified_filesystem(target.parent))
        self.assertFalse(admission_module._qualified_filesystem(target))
        plan = plan_fixture_artifact_admission(
            self.suite, self.share, self.candidate, **self._args(),
        )
        self.assertEqual("ready", plan["state"], plan["blockers"])
        result = admit_fixture_artifact(
            self.suite, self.share, self.candidate,
            expected_plan_id=plan["plan_id"], **self._args(),
        )
        self.assertEqual("owner-artifact-snapshot-admitted", result["state"])


if __name__ == "__main__":
    import unittest
    unittest.main()
