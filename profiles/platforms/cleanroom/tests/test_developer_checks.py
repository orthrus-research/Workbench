"""Native platform image admission, separate from real-game qualification."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from workbench_profile_cleanroom import axiom
from workbench_profile_cleanroom import developer_checks as policy
from workbench_profile_cleanroom import profile


class NativeRuntimePolicyTests(unittest.TestCase):
    def test_provenance_reports_observed_jdk_and_selected_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary)/"jdk/bin/java"
            executable.parent.mkdir(parents=True)
            (executable.parent.parent/"release").write_text('JAVA_RUNTIME_VERSION="25.0.4+7"\nIMPLEMENTOR="Eclipse Adoptium"\nOS_ARCH="x86_64"\n')
            result = policy.provenance(executable)
            self.assertEqual(result["version"], "0.6.12-alpha")
            self.assertEqual(result["java"]["JAVA_RUNTIME_VERSION"], "25.0.4+7")
            self.assertEqual(result["toolchain_lock_sha256"], sha256(profile().resource("runtime-toolchain").read_bytes()).hexdigest())
    def test_active_native_runtime_is_version_bound_and_not_historical_qualification(self):
        selected = profile()
        lock = json.loads(selected.resource("runtime-toolchain").read_bytes())
        requirements = policy.environment_requirements()
        self.assertEqual(lock["format"], "workbench-cleanroom-native-runtime-lock-v1")
        self.assertEqual(lock["cleanroom_version"], "0.6.12-alpha")
        self.assertEqual(lock["bootstrap"]["sha256"], requirements["bootstrap"]["sha256"])
        self.assertEqual(lock["source_revision"], requirements["bootstrap"]["source_revision"])
        self.assertFalse(lock["qualification_granted"])
        self.assertEqual(selected.resource("runtime-toolchain").name, "runtime-toolchain.json")
        artifacts = {row["coordinate"]: row for row in requirements["artifacts"]}
        runtime = artifacts["com.cleanroommc:cleanroom:0.6.12-alpha"]
        self.assertEqual(runtime["filename"], "cleanroom-0.6.12-alpha.jar")
        self.assertEqual(runtime["sha256"], "48043f4ea69605ec9b1250b9bd01106b07023b78614bac734a8a296e690e7893")
        self.assertIn("com.cleanroommc:cleanmix:0.7.2", artifacts)
        self.assertNotIn("com.cleanroommc:cleanmix:0.7.0", artifacts)


class NativeInputPreparationTests(unittest.TestCase):
    def test_registered_packaged_resources_select_exact_original_server_scope(self):
        selected = profile()
        root_raw = selected.resource("axiom-native-root").read_bytes()
        library_raw = selected.resource("axiom-native-libraries").read_bytes()
        root, library = json.loads(root_raw), json.loads(library_raw)
        package = tomllib.loads((selected.root / "pyproject.toml").read_text())
        resources = package["tool"]["setuptools"]["package-data"]["workbench_resources.profiles.platforms.cleanroom"]
        for name in ("native-root-class-space.json", "native-identity-runtime.json"):
            self.assertEqual(1, resources.count(name))
        prepared = axiom.preparation_inputs()
        self.assertEqual({
            "schema": "axiom.native-input-preparation.v1", "profile": "cleanroom",
            "side": "server", "inputStage": "raw-original-artifacts",
            "policySha256": {
                "native-root-class-space.json": sha256(root_raw).hexdigest(),
                "native-identity-runtime.json": sha256(library_raw).hexdigest(),
            },
            "artifacts": [{key: row[key] for key in ("path", "url", "sha256", "size")}
                          for row in root["inputs"] + library["libraries"]],
        }, prepared)
        self.assertEqual(115, len(prepared["artifacts"]))
        self.assertEqual(129684210, sum(row["size"] for row in prepared["artifacts"]))
        self.assertEqual("com/mojang/minecraft/1.12.2/minecraft-1.12.2-server.jar", prepared["artifacts"][1]["path"])
        self.assertEqual("fe1f9274e6dad9191bf6e6e8e36ee6ebc737f373603df0946aafcded0d53167e",
                         prepared["artifacts"][1]["sha256"])
        selected_paths = {row["path"] for row in prepared["artifacts"]}
        self.assertNotIn(library["inputs"][1]["path"], selected_paths)
        self.assertTrue(selected_paths.isdisjoint(row["path"] for row in library["images"]))
        self.assertEqual({"materialInitializationComplete": False, "fullLauncherCompositionQualified": False,
                          "wholePackParity": False}, root["qualification"])

    def test_preparation_reads_the_owned_installed_resource_layout(self):
        selected = profile()
        expected = axiom.preparation_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            namespace = Path(temporary) / "workbench_resources"
            owner = namespace / "profiles/platforms/cleanroom"
            owner.mkdir(parents=True)
            for role in ("axiom-native-root", "axiom-native-libraries"):
                source = selected.resource(role)
                (owner / source.name).write_bytes(source.read_bytes())
            with patch("workbench_profile_cleanroom.repository_root", return_value=namespace):
                self.assertEqual(expected, axiom.preparation_inputs())

    def test_changed_policy_binding_or_root_selection_is_not_prepared(self):
        selected = profile()
        root = json.loads(selected.resource("axiom-native-root").read_bytes())
        library_raw = selected.resource("axiom-native-libraries").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            owner = Path(temporary)
            resources = {"axiom-native-root": owner / "root.json", "axiom-native-libraries": owner / "library.json"}
            resources["axiom-native-libraries"].write_bytes(library_raw)
            installed = type("Profile", (), {"resource": staticmethod(resources.__getitem__)})()
            changes = [lambda value: value.update(libraryPolicySha256="0" * 64),
                       lambda value: value.update(side="CLIENT"),
                       lambda value: value["inputs"][0].update(sha256="0" * 64)]
            for change in changes:
                altered = json.loads(json.dumps(root)); change(altered)
                resources["axiom-native-root"].write_text(json.dumps(altered))
                with self.subTest(altered=altered), patch.object(axiom, "profile", return_value=installed):
                    with self.assertRaisesRegex(ValueError, "Native SERVER preparation"):
                        axiom.preparation_inputs()


class ClientImageAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.java = self.root / "jdk/bin/java"
        self.java.parent.mkdir(parents=True)
        self.java.write_bytes(b"fixture")
        self.release = self.java.parent.parent / "release"
        self.release.write_text(
            'JAVA_RUNTIME_VERSION="25.0.4+7"\nIMPLEMENTOR="Eclipse Adoptium"\n'
        )
        self.profile = self.root / "profile.json"
        self.profile.write_text(
            json.dumps(
                {
                    "java": {
                        "runtime_provision": {
                            "release_name": "jdk-25.0.4+7",
                            "java_vendor": "Eclipse Adoptium",
                        }
                    }
                }
            )
        )
        self.lock = self.root / "lock.json"
        self.lock.write_text(
            json.dumps(
                {
                    "native_service_expectations": {
                        "launch_main_class": "top.outlands.foundation.boot.Foundation"
                    },
                    "artifacts": [
                        {
                            "filename": "foundation.jar",
                            "size": 7,
                            "sha256": sha256(b"fixture").hexdigest(),
                        }
                    ],
                }
            )
        )
        (self.runtime / "foundation.jar").write_bytes(b"fixture")
        resources = {"profile": self.profile, "runtime-toolchain": self.lock}
        selected = type(
            "Profile", (), {"resource": staticmethod(resources.__getitem__)}
        )()
        patcher = patch.object(policy, "profile", return_value=selected)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.args = [
            "-cp",
            "foundation.jar",
            "top.outlands.foundation.boot.Foundation",
            "--gameDir",
            ".",
            "--accessToken",
            "0",
        ]

    def test_admits_exact_installed_toolchain_and_artifact(self):
        policy.validate_client_image(self.runtime, self.java, self.args)

    def test_rejects_toolchain_and_artifact_drift(self):
        self.release.write_text(
            'JAVA_RUNTIME_VERSION="21"\nIMPLEMENTOR="Eclipse Adoptium"\n'
        )
        with self.assertRaisesRegex(ValueError, "toolchain"):
            policy.validate_client_image(self.runtime, self.java, self.args)
        self.release.write_text(
            'JAVA_RUNTIME_VERSION="25.0.4+7"\nIMPLEMENTOR="Eclipse Adoptium"\n'
        )
        (self.runtime / "foundation.jar").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact differs"):
            policy.validate_client_image(self.runtime, self.java, self.args)

    def test_rejects_external_paths_duplicate_game_dirs_and_credentials(self):
        for extra in (
            ["--gameDir", "."],
            ["--accessToken", "secret"],
            ["--accessToken=secret"],
            ["-Dpath=/outside"],
            ["-cp", "lib/a.jar:../outside.jar"],
            ["@args.txt"],
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                policy.validate_client_image(self.runtime, self.java, self.args + extra)

    def test_rejects_duplicate_platform_artifacts(self):
        (self.runtime / "old").mkdir()
        (self.runtime / "old/foundation.jar").write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError, "one exact"):
            policy.validate_client_image(self.runtime, self.java, self.args)
