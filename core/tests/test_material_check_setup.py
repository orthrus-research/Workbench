"""Core setup custody and fresh owner verification; no native execution."""

import copy
from contextlib import chdir
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_core import material_check_setup as setup
from workbench_core import setup_cli


class MaterialEnginePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="material-engine-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "state"
        self.archive = self.base / "engine.zip"
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("test-engine/lib/engine.jar", b"engine fixture")
            archive.writestr("test-engine/LICENSE", b"original notice")

    def verify(self, home):
        self.assertEqual(b"engine fixture", (home / "lib/engine.jar").read_bytes())
        self.assertEqual(b"original notice", (home / "LICENSE").read_bytes())
        return {"engine": "fixture-not-executable"}

    def test_core_retains_complete_distribution_and_reuses_exact_bytes(self):
        home = setup.prepare_engine(self.root, self.archive, self.verify)
        self.assertTrue(home.is_relative_to(self.root))
        with patch.object(setup, "fetch_verified_artifact", side_effect=AssertionError("reuse must not fetch")):
            self.assertEqual(home, setup.prepare_engine(self.root, self.archive, self.verify))
        self.assertFalse(list((self.root / ".workbench/material-check-setups").glob("*.json")))
        (home / "LICENSE").write_bytes(b"changed notice")
        with self.assertRaisesRegex(ValueError, "prepared native input files differ"):
            setup.prepare_engine(self.root, self.archive, self.verify)

    def test_distribution_failure_is_retained_without_setup_publication(self):
        def refuse(home):
            raise ValueError("fixture distribution is incompatible")
        with self.assertRaisesRegex(ValueError, "distribution is incompatible"):
            setup.prepare_engine(self.root, self.archive, refuse)
        failure, = self.root.glob(".workbench/material-check-setups/engines/.engine-*/failure.json")
        self.assertEqual("incomplete", json.loads(failure.read_bytes())["state"])
        self.assertTrue((failure.parent / "content/test-engine/LICENSE").is_file())
        self.assertFalse(list(self.root.glob("**/engine.json")))

    def test_cancellation_after_extraction_cannot_publish_engine(self):
        cancelled = False
        def verify(home):
            nonlocal cancelled
            cancelled = True
            return self.verify(home)
        with self.assertRaisesRegex(ValueError, "engine preparation cancelled"):
            setup.prepare_engine(self.root, self.archive, verify, cancelled=lambda: cancelled)
        self.assertFalse(list(self.root.glob("**/engine.json")))
        self.assertEqual(1, len(list(self.root.glob("**/failure.json"))))


class MaterialCheckSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="material-setup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "state"
        self.paths = {key: str(Path(temporary.name) / key) for key in setup.PATH_KEYS}
        self.binding = {"owner": "fixture", "digest": "initial"}
        self.calls = []
        self.environment = {"WORKBENCH_CONFIG_HOME": str(Path(temporary.name) / "configuration")}

    def core_setup(self, *, java_home=None, managed_java_home=None):
        path = setup_cli.default_setup_record_path(environment=self.environment)
        setup_cli._write_setup_record(path, {
            "workspace": str(self.root.parent / "workspace"), "state_root": str(self.root),
            "profile_config": None, "profile_selection_digest": None,
            "java_home": java_home, "managed_java_home": managed_java_home, "git_executable": None,
        })
        return path

    def verify(self, paths, context):
        self.calls.append((copy.deepcopy(paths), context))
        return copy.deepcopy(self.binding)

    def configure(self, context="pack:materials"):
        return setup.configure(self.root, "selection", context, self.paths, ".", self.verify)

    def test_missing_status_does_not_create_state_or_call_owner(self):
        status = setup.status(self.root, "selection", "pack:materials", self.verify)
        self.assertEqual("missing", status["state"])
        self.assertIsNone(status["setup_id"])
        self.assertFalse(self.root.exists())
        self.assertEqual([], self.calls)

    def test_java_reuse_requires_saved_core_selection_without_ambient_discovery(self):
        environment = {**self.environment, "JAVA_HOME": "/ambient/jdk", "WORKBENCH_JAVA_HOME": "/ambient/workbench-jdk",
                       "PATH": "/ambient/bin"}
        with self.assertRaisesRegex(ValueError, "Core setup has no selected Java"):
            setup.selected_java(environment=environment)
        self.assertFalse(Path(self.environment["WORKBENCH_CONFIG_HOME"]).exists())
        self.core_setup()
        with self.assertRaisesRegex(ValueError, "Core setup has no selected Java"):
            setup.selected_java(environment=environment)
        self.assertFalse(self.root.exists())

    def test_java_reuse_uses_saved_external_or_managed_selection(self):
        external, managed = str(self.root.parent / "external-jdk"), str(self.root.parent / "managed-jdk")
        for java_home, managed_java_home, expected in ((external, None, external),
                                                       (None, managed, managed),
                                                       (external, managed, external)):
            with self.subTest(java_home=java_home, managed_java_home=managed_java_home):
                self.core_setup(java_home=java_home, managed_java_home=managed_java_home)
                executable = "java.exe" if os.name == "nt" else "java"
                self.assertEqual(Path(expected) / "bin" / executable, setup.selected_java(environment=self.environment))

    def test_java_reuse_uses_core_host_executable_name(self):
        home = self.root.parent / "managed-jdk"
        self.core_setup(managed_java_home=str(home))
        for host, executable in (("linux", "java"), ("windows", "java.exe"), ("mac", "java")):
            with self.subTest(host=host), patch("workbench_core.runtime_java.host_platform", return_value={"os": host}):
                self.assertEqual(home / "bin" / executable, setup.selected_java(environment=self.environment))

    def test_java_reuse_reads_validated_core_record(self):
        path = self.core_setup(java_home=str(self.root.parent / "jdk"))
        value = json.loads(path.read_bytes())
        value["selection"]["java_home"] = str(self.root.parent / "replacement")
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "identity does not match"):
            setup.selected_java(environment=self.environment)

    def test_java_reuse_refuses_nonabsolute_or_noncanonical_core_path(self):
        for home in ("relative-jdk", "/jdk/../replacement", "/jdk\nreplacement"):
            with self.subTest(home=home):
                self.core_setup(java_home=home)
                with self.assertRaisesRegex(ValueError, "explicit absolute path"):
                    setup.selected_java(environment=self.environment)

    def test_configure_resolve_and_status_reobserve_owner_every_time(self):
        record = self.configure()
        self.assertEqual("configured-not-run", record["state"])
        self.assertEqual(record, setup.resolve(self.root, "selection", "pack:materials", self.verify))
        status = setup.status(self.root, "selection", "pack:materials", self.verify)
        self.assertEqual("ready", status["state"])
        self.assertEqual(record["id"], status["setup_id"])
        self.assertEqual("saved-input-and-profile-bindings-only", status["readiness_scope"])
        self.assertEqual(3, len(self.calls))

    def test_changed_bindings_require_explicit_reconfiguration(self):
        old = self.configure()
        self.binding["digest"] = "replacement"
        status = setup.status(self.root, "selection", "pack:materials", self.verify)
        self.assertEqual("stale", status["state"])
        self.assertEqual(old["id"], status["setup_id"])
        with self.assertRaisesRegex(ValueError, "stale"):
            setup.resolve(self.root, "selection", "pack:materials", self.verify)
        new = self.configure()
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(new, setup.resolve(self.root, "selection", "pack:materials", self.verify))

    def test_context_and_selection_do_not_fall_back_to_other_setup(self):
        self.configure()
        for selection, context in (("other", "pack:materials"), ("selection", "pack:other")):
            self.assertEqual("missing", setup.status(self.root, selection, context, self.verify)["state"])
            with self.assertRaisesRegex(ValueError, "missing"):
                setup.resolve(self.root, selection, context, self.verify)

    def test_invalid_saved_record_is_stale_and_not_used(self):
        self.configure()
        path = setup._location(self.root, "selection", "pack:materials")
        value = json.loads(path.read_bytes())
        value["paths"]["java"] = "/replaced/java"
        path.write_text(json.dumps(value))
        self.calls.clear()
        self.assertEqual("stale", setup.status(self.root, "selection", "pack:materials", self.verify)["state"])
        with self.assertRaisesRegex(ValueError, "identity"):
            setup.resolve(self.root, "selection", "pack:materials", self.verify)
        self.assertEqual([], self.calls)

    def test_missing_input_is_actionable_without_native_run(self):
        self.configure()
        def missing(*args):
            raise FileNotFoundError("selected Java was removed")
        status = setup.status(self.root, "selection", "pack:materials", missing)
        self.assertEqual("stale", status["state"])
        self.assertEqual("setup-unavailable-not-source-invalidity", status["failure"]["meaning"])
        self.assertIn("removed", status["failure"]["message"])

    def test_partial_relative_or_unsafe_setup_never_reaches_owner(self):
        for paths, program in (({"java": "/jdk/bin/java"}, "."),
                               ({**self.paths, "java": "relative/bin/java"}, "."),
                               (self.paths, "../outside")):
            with self.subTest(paths=paths, program=program), self.assertRaises(ValueError):
                setup.configure(self.root, "selection", "pack:materials", paths, program, self.verify)
        self.assertEqual([], self.calls)

    def test_linked_setup_cannot_be_read_or_overwritten(self):
        self.configure()
        path = setup._location(self.root, "selection", "pack:materials")
        target = self.root / "unrelated.json"
        target.write_bytes(path.read_bytes())
        original = target.read_bytes()
        path.unlink()
        path.symlink_to(target)
        self.assertEqual("stale", setup.status(self.root, "selection", "pack:materials", self.verify)["state"])
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.configure()
        self.assertEqual(original, target.read_bytes())


class MaterialInputPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="material-input-preparation-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "state"
        self.originals = self.base / "originals"
        self.originals.mkdir()
        self.requirements = {
            "schema": "axiom.native-input-preparation.v1", "profile": "cleanroom", "side": "server",
            "inputStage": "raw-original-artifacts", "policySha256": {
                "native-root-class-space.json": "a" * 64, "native-identity-runtime.json": "b" * 64,
            }, "artifacts": [],
        }
        for index, path in enumerate(("net/minecraft/server/1.12.2/server.jar", "org/example/library/1/library.jar")):
            source = self.originals / str(index)
            raw = ("original artifact " + str(index)).encode()
            source.write_bytes(raw)
            self.requirements["artifacts"].append({"path": path, "url": source.as_uri(),
                "size": len(raw), "sha256": sha256(raw).hexdigest()})
        self.binding = {"profileOwner": "fixture-owner", "context": {"id": "pack:materials", "side": "server"}}

    def verify(self, requirements, context):
        return copy.deepcopy(self.binding)

    def prepare(self, **kwargs):
        return setup.prepare_inputs(self.root, "selection", "pack:materials", self.requirements, self.verify, **kwargs)

    def assert_incomplete(self):
        self.assertEqual("missing", setup.status(self.root, "selection", "pack:materials", self.verify)["state"])
        parent = setup._location(self.root, "selection", "pack:materials").parent / "inputs"
        if parent.exists():
            self.assertEqual([], [path for path in parent.iterdir() if path.is_dir() and not path.name.startswith(".")])

    def test_local_acquisition_preserves_original_paths_without_configuring_runtime(self):
        result = self.prepare()
        self.assertEqual("inputs-prepared", result["state"])
        self.assertEqual("incomplete", result["setup_state"])
        self.assertEqual(["engine", "native-runtime", "intended-jvm-qualification"], result["pending"])
        self.assertEqual(self.requirements, result["requirements"])
        artifact_root = Path(result["paths"]["artifact_root"])
        for index, row in enumerate(self.requirements["artifacts"]):
            staged = artifact_root / row["path"]
            self.assertEqual((self.originals / str(index)).read_bytes(), staged.read_bytes())
            self.assertEqual(1, staged.stat().st_nlink)
            self.assertTrue((self.root / ".workbench/artifacts/sha256" / row["sha256"]).is_file())
        self.assertFalse((self.root / "artifacts").exists())
        self.assertEqual(result, json.loads((artifact_root.parent / "preparation.json").read_bytes()))
        self.assertEqual("missing", setup.status(self.root, "selection", "pack:materials", self.verify)["state"])
        with self.assertRaisesRegex(ValueError, "setup is missing"):
            setup.resolve(self.root, "selection", "pack:materials", self.verify)

    def test_retained_inputs_are_reverified_without_acquiring_again(self):
        result = self.prepare()
        for source in self.originals.iterdir():
            source.unlink()
        with patch.object(setup, "fetch_verified_artifact", side_effect=AssertionError("verified staging is reusable")):
            self.assertEqual(result, self.prepare())
            (Path(result["paths"]["artifact_root"]) / self.requirements["artifacts"][0]["path"]).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "files differ"):
                self.prepare()

    def test_cancelled_acquisition_retains_verified_cache_for_retry_without_publishing(self):
        fetch = setup.fetch_verified_artifact
        stopped = False
        def acquire(**kwargs):
            nonlocal stopped
            result = fetch(**kwargs)
            stopped = True
            return result
        with patch.object(setup, "fetch_verified_artifact", side_effect=acquire), self.assertRaisesRegex(ValueError, "cancelled"):
            self.prepare(cancelled=lambda: stopped)
        self.assert_incomplete()
        (self.originals / "0").unlink()
        results = []
        def resume(**kwargs):
            result = fetch(**kwargs)
            results.append(result[1])
            return result
        with patch.object(setup, "fetch_verified_artifact", side_effect=resume):
            self.assertEqual("inputs-prepared", self.prepare()["state"])
        self.assertEqual(["reused", "downloaded"], results)
        failures = list((self.root / ".workbench/material-check-setups/inputs").glob(".prepare-*/failure.json"))
        self.assertEqual(1, len(failures))
        self.assertEqual("incomplete", json.loads(failures[0].read_bytes())["state"])

    def test_cancelled_copy_or_final_binding_check_does_not_publish(self):
        original_copy = setup.shutil.copyfile
        for boundary in ("copy", "final-binding"):
            with self.subTest(boundary=boundary):
                self.root = self.base / boundary
                stopped = False
                calls = 0
                def copy_file(*args):
                    nonlocal stopped
                    result = original_copy(*args)
                    stopped = boundary == "copy"
                    return result
                def verify(*args):
                    nonlocal calls, stopped
                    calls += 1
                    if calls == 2 and boundary == "final-binding":
                        stopped = True
                    return self.binding
                with patch.object(setup.shutil, "copyfile", side_effect=copy_file), \
                        patch.object(self, "verify", side_effect=verify), self.assertRaisesRegex(ValueError, "cancelled"):
                    self.prepare(cancelled=lambda: stopped)
                self.assert_incomplete()

    def test_cancelled_before_start_does_not_acquire_or_create_state(self):
        with patch.object(setup, "fetch_verified_artifact", side_effect=AssertionError("cancelled before acquisition")), \
                self.assertRaisesRegex(ValueError, "cancelled"):
            self.prepare(cancelled=lambda: True)
        self.assertFalse(self.root.exists())

    def test_changed_owner_during_preparation_does_not_publish(self):
        with patch.object(self, "verify", side_effect=[self.binding, {**self.binding, "profileOwner": "replacement"}]), \
                self.assertRaisesRegex(ValueError, "owner bindings changed"):
            self.prepare()
        self.assert_incomplete()

    def test_wrong_artifact_bytes_leave_setup_incomplete(self):
        (self.originals / "0").write_bytes(b"different artifact!")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.prepare()
        self.assert_incomplete()

    def test_changed_policy_has_separate_preparation_and_preserves_existing_setup(self):
        paths = {key: str(self.base / key) for key in setup.PATH_KEYS}
        configured = setup.configure(self.root, "selection", "pack:materials", paths, ".", self.verify)
        first = self.prepare()
        self.requirements["policySha256"]["native-root-class-space.json"] = "c" * 64
        second = self.prepare()
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["paths"], second["paths"])
        self.assertTrue(Path(first["paths"]["artifact_root"]).is_dir())
        self.assertEqual(configured, setup.resolve(self.root, "selection", "pack:materials", self.verify))

    def test_relative_state_root_uses_absolute_retained_paths(self):
        with chdir(self.base):
            result = setup.prepare_inputs(Path("state"), "selection", "pack:materials", self.requirements, self.verify)
        self.assertTrue(Path(result["paths"]["artifact_root"]).is_absolute())
        self.assertTrue(result["receipt_uri"].startswith(self.root.as_uri()))


if __name__ == "__main__":
    unittest.main()


class MaterialRuntimePreparationTests(unittest.TestCase):
    setUp = MaterialInputPreparationTests.setUp
    verify = MaterialInputPreparationTests.verify
    prepare = MaterialInputPreparationTests.prepare

    def build(self, artifact_root, output, work):
        output.mkdir()
        (output / "runtime.json").write_bytes(b'{"fixture":"not executable"}')
        (work / "retained-native-output.log").write_bytes(b"original tool output")

    def runtime(self, prepared, build=None, cancelled=lambda: False):
        return setup.prepare_runtime(self.root, "selection", "pack:materials", prepared,
            lambda: copy.deepcopy(self.binding), build or self.build, cancelled=cancelled)

    def test_assembled_runtime_reuses_verified_bytes_without_reexecuting_builder(self):
        prepared = self.prepare()
        first = self.runtime(prepared)
        with patch.object(self, "build", side_effect=AssertionError("reuse must not rebuild")):
            self.assertEqual(first, self.runtime(prepared))
        (first / "runtime.json").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "prepared native input files differ"):
            self.runtime(prepared)
        self.assertEqual("missing", setup.status(self.root, "selection", "pack:materials", self.verify)["state"])

    def test_failed_builder_preserves_output_without_publishing_runtime(self):
        prepared = self.prepare()
        failure = ValueError("original compiler failed")

        def build(artifact_root, output, work):
            (work / "compiler.log").write_text("original native compiler message")
            raise failure

        with self.assertRaises(ValueError) as caught:
            self.runtime(prepared, build)
        self.assertIs(failure, caught.exception)
        self.assertEqual(1, len(list(self.root.rglob("compiler.log"))))
        self.assertEqual(1, len(list(self.root.rglob("failure.json"))))
        self.assertFalse(list(self.root.rglob("assembly.json")))

    def test_changed_owner_or_cancellation_after_build_cannot_publish(self):
        for outcome in ("changed-owner", "cancelled"):
            prepared = self.prepare()
            cancelled = False

            def build(artifact_root, output, work):
                nonlocal cancelled
                self.build(artifact_root, output, work)
                if outcome == "changed-owner":
                    self.binding["changed"] = True
                else:
                    cancelled = True

            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, "inputs changed|cancelled"):
                self.runtime(prepared, build, lambda: cancelled)
        self.assertFalse(list(self.root.rglob("assembly.json")))
