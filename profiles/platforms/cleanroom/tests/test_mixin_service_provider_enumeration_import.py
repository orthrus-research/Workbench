from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/tools"
    / "import_mixin_service_provider_enumeration.py"
)
PROBE_SOURCE = (
    ROOT
    / "modules/crucible/probes/mixin-service-provider-enumerator/src/main/java"
    / "dev/workbench/crucible/mixins/MixinServiceProviderEnumerationProbe.java"
)
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
if str(CRUCIBLE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CRUCIBLE_SOURCE))

from workbench_crucible_mixin_custody import (  # noqa: E402
    MIXIN_SERVICE_INTERFACE,
    PROVIDER_ENUMERATION_PROBE_ID,
    RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
    SERVICE_LOADER_ITERATOR_MECHANISM,
    build_provider_enumeration_evidence,
    build_runtime_service_receipt,
    parse_provider_enumeration_evidence,
    render_provider_enumeration_evidence,
)


PROFILE_ID = "workbench-platform:cleanroom:test"
TOPOLOGY_ID = "workbench-mixin-topology-receipt:sha256:" + "d" * 64
TOPOLOGY_SHA = "e" * 64


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "import_mixin_service_provider_enumeration",
        TOOL_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock_bytes() -> bytes:
    value = {
        "format": "workbench-cleanroom-transformer-toolchain-lock-v1",
        "schema_version": 1,
        "binding": {"candidate_id": PROFILE_ID},
        "native_service_expectations": {
            "mixin_service": "com.cleanroommc.cleanmix.service.CleanMixService"
        },
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _session(lock_bytes: bytes) -> dict:
    return {
        "session_id": "session:provider-enumeration",
        "launch_id": "launch:provider-enumeration",
        "profile_id": PROFILE_ID,
        "side": "dedicated_server",
        "candidate_toolchain_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "component_topology_receipt_id": TOPOLOGY_ID,
        "component_topology_receipt_sha256": TOPOLOGY_SHA,
    }


def _not_enumerated() -> dict:
    return {
        "state": "not_enumerated",
        "mechanism": None,
        "providers": [],
        "evidence_sha256": None,
        "evidence_id": None,
        "source_record": None,
        "failure": None,
    }


def _base_observation_set(lock_bytes: bytes) -> dict:
    runtime_bytes = b"synthetic Mixin runtime code source"
    log_bytes = b"runtime reported service evidence\n"
    return {
        "format": RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
        "session": _session(lock_bytes),
        "artifacts": [
            {
                "artifact_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
                "size_bytes": len(runtime_bytes),
                "role": "mixin_runtime",
                "label": "mixin-runtime.jar",
            }
        ],
        "evidence": [
            {
                "source_sha256": hashlib.sha256(log_bytes).hexdigest(),
                "size_bytes": len(log_bytes),
                "kind": "runtime_log",
                "label": "debug.log",
            }
        ],
        "observations": [
            {
                "sequence": 1,
                "kind": "selection_report",
                "service_name": "CleanMix",
                "evidence_sha256": hashlib.sha256(log_bytes).hexdigest(),
                "source_record": "line:1",
            }
        ],
        "provider_enumeration": _not_enumerated(),
        "limitations": ["Synthetic exact-profile fixture."],
    }


def _probe() -> dict:
    return {
        "probe_id": PROVIDER_ENUMERATION_PROBE_ID,
        "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
        "service_interface": MIXIN_SERVICE_INTERFACE,
        "class_loader_kind": "thread_context",
        "class_loader_class": "example.LaunchClassLoader",
    }


def _provider_jar(path: Path, classes: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "META-INF/services/org.spongepowered.asm.service.IMixinService",
            "\n".join(classes) + "\n",
        )
        for service_class in classes:
            archive.writestr(
                service_class.replace(".", "/") + ".class",
                b"\xca\xfe\xba\xbe synthetic provider " + service_class.encode("ascii"),
            )
    value = buffer.getvalue()
    path.write_bytes(value)
    return value


def _evidence_for(
    *,
    lock_bytes: bytes,
    artifact: Path,
    artifact_bytes: bytes,
    classes: list[str],
    names: list[str],
) -> bytes:
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    evidence = build_provider_enumeration_evidence(
        session=_session(lock_bytes),
        probe=_probe(),
        state="enumerated",
        providers=[
            {
                "service_class": service_class,
                "service_name": service_name,
                "source_uri": artifact.resolve().as_uri(),
                "provider_artifact_sha256": digest,
                "provider_artifact_size_bytes": len(artifact_bytes),
            }
            for service_class, service_name in zip(classes, names)
        ],
        failure=None,
    )
    return render_provider_enumeration_evidence(evidence)


def _find_jdk_tools() -> tuple[Path, Path] | None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac and java:
        return Path(javac), Path(java)
    candidates = sorted((ROOT / ".workbench/jdks").glob("**/bin/javac"))
    for candidate in candidates:
        runtime = candidate.with_name("java")
        if runtime.is_file():
            return candidate, runtime
    return None


def _jar_directory(root: Path, output: Path, descriptor: list[str] | None = None) -> None:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(root.rglob("*.class")):
            archive.write(path, path.relative_to(root).as_posix())
        if descriptor is not None:
            archive.writestr(
                "META-INF/services/org.spongepowered.asm.service.IMixinService",
                "\n".join(descriptor) + "\n",
            )


class CleanroomMixinServiceProviderEnumerationImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()
        cls.jdk = _find_jdk_tools()

    def test_duplicate_names_and_multiple_providers_are_admitted_without_selection_inference(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "providers.jar"
            classes = ["example.FirstProvider", "example.SecondProvider"]
            artifact_bytes = _provider_jar(artifact, classes)
            evidence_bytes = _evidence_for(
                lock_bytes=lock_bytes,
                artifact=artifact,
                artifact_bytes=artifact_bytes,
                classes=classes,
                names=["SharedName", "SharedName"],
            )
            imported = self.tool.import_provider_enumeration(
                base_observation_set=_base_observation_set(lock_bytes),
                provider_evidence_bytes=evidence_bytes,
                provider_artifact_paths=[artifact],
                toolchain_lock_bytes=lock_bytes,
            )

        receipt = build_runtime_service_receipt(
            session=imported["session"],
            artifacts=imported["artifacts"],
            evidence=imported["evidence"],
            observations=imported["observations"],
            provider_enumeration=imported["provider_enumeration"],
            limitations=imported["limitations"],
        )
        self.assertEqual(receipt["summary"]["enumerated_provider_count"], 2)
        self.assertEqual(
            receipt["summary"]["enumerated_provider_uniqueness"], "multiple"
        )
        self.assertEqual(
            receipt["summary"]["duplicate_enumerated_service_names"],
            ["SharedName"],
        )
        self.assertEqual(
            receipt["summary"]["reported_service_names"], ["CleanMix"]
        )
        self.assertTrue(
            receipt["boundaries"]["provider_enumeration_is_not_runtime_selection"]
        )

    def test_source_uri_must_name_the_independently_measured_artifact(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "providers.jar"
            other = root / "other.jar"
            classes = ["example.Provider"]
            artifact_bytes = _provider_jar(artifact, classes)
            _provider_jar(other, classes)
            evidence = json.loads(
                _evidence_for(
                    lock_bytes=lock_bytes,
                    artifact=artifact,
                    artifact_bytes=artifact_bytes,
                    classes=classes,
                    names=["Provider"],
                )
            )
            evidence["providers"][0]["source_uri"] = other.resolve().as_uri()
            forged = build_provider_enumeration_evidence(
                session=evidence["session"],
                probe=evidence["probe"],
                state=evidence["state"],
                providers=evidence["providers"],
                failure=None,
            )
            with self.assertRaisesRegex(
                self.tool.CleanroomProviderEnumerationImportError,
                "source URI does not name",
            ):
                self.tool.import_provider_enumeration(
                    base_observation_set=_base_observation_set(lock_bytes),
                    provider_evidence_bytes=render_provider_enumeration_evidence(forged),
                    provider_artifact_paths=[artifact],
                    toolchain_lock_bytes=lock_bytes,
                )

    def test_forged_evidence_identity_is_rejected(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "providers.jar"
            classes = ["example.Provider"]
            artifact_bytes = _provider_jar(artifact, classes)
            value = json.loads(
                _evidence_for(
                    lock_bytes=lock_bytes,
                    artifact=artifact,
                    artifact_bytes=artifact_bytes,
                    classes=classes,
                    names=["Provider"],
                )
            )
            value["providers"][0]["service_class"] = "forged.Provider"
            forged_bytes = (
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            with self.assertRaisesRegex(
                self.tool.CleanroomProviderEnumerationImportError,
                "identity mismatch",
            ):
                self.tool.import_provider_enumeration(
                    base_observation_set=_base_observation_set(lock_bytes),
                    provider_evidence_bytes=forged_bytes,
                    provider_artifact_paths=[artifact],
                    toolchain_lock_bytes=lock_bytes,
                )

    def test_reidentified_forged_provider_must_exist_in_exact_artifact(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "providers.jar"
            artifact_bytes = _provider_jar(artifact, ["example.RealProvider"])
            forged_bytes = _evidence_for(
                lock_bytes=lock_bytes,
                artifact=artifact,
                artifact_bytes=artifact_bytes,
                classes=["example.ForgedProvider"],
                names=["Provider"],
            )
            with self.assertRaisesRegex(
                self.tool.CleanroomProviderEnumerationImportError,
                "does not contain exactly one observed provider class",
            ):
                self.tool.import_provider_enumeration(
                    base_observation_set=_base_observation_set(lock_bytes),
                    provider_evidence_bytes=forged_bytes,
                    provider_artifact_paths=[artifact],
                    toolchain_lock_bytes=lock_bytes,
                )

    def test_failed_enumeration_is_admitted_without_partial_providers(self) -> None:
        lock_bytes = _lock_bytes()
        evidence = build_provider_enumeration_evidence(
            session=_session(lock_bytes),
            probe=_probe(),
            state="failed",
            providers=[],
            failure={
                "stage": "service_loader_iteration",
                "exception_class": "java.util.ServiceConfigurationError",
                "message": "provider construction failed",
            },
        )
        imported = self.tool.import_provider_enumeration(
            base_observation_set=_base_observation_set(lock_bytes),
            provider_evidence_bytes=render_provider_enumeration_evidence(evidence),
            provider_artifact_paths=[],
            toolchain_lock_bytes=lock_bytes,
        )
        receipt = build_runtime_service_receipt(
            session=imported["session"],
            artifacts=imported["artifacts"],
            evidence=imported["evidence"],
            observations=imported["observations"],
            provider_enumeration=imported["provider_enumeration"],
            limitations=imported["limitations"],
        )
        self.assertEqual(receipt["provider_enumeration"]["state"], "failed")
        self.assertEqual(receipt["provider_enumeration"]["providers"], [])
        self.assertEqual(
            receipt["summary"]["enumerated_provider_uniqueness"], "unknown"
        )
        self.assertFalse(
            receipt["boundaries"]["failed_enumeration_partial_set_published"]
        )

    def test_cli_output_is_accepted_by_generic_runtime_service_builder(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "providers.jar"
            classes = ["example.Provider"]
            artifact_bytes = _provider_jar(artifact, classes)
            evidence_bytes = _evidence_for(
                lock_bytes=lock_bytes,
                artifact=artifact,
                artifact_bytes=artifact_bytes,
                classes=classes,
                names=["Provider"],
            )
            base_path = root / "base.json"
            evidence_path = root / "enumeration.json"
            lock_path = root / "toolchain-lock.json"
            output_path = root / "imported.json"
            base_path.write_text(
                json.dumps(_base_observation_set(lock_bytes)), encoding="utf-8"
            )
            evidence_path.write_bytes(evidence_bytes)
            lock_path.write_bytes(lock_bytes)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--base-observation-set",
                    str(base_path),
                    "--probe-evidence",
                    str(evidence_path),
                    "--toolchain-lock",
                    str(lock_path),
                    "--provider-artifact",
                    str(artifact),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            imported = json.loads(output_path.read_text(encoding="utf-8"))
            receipt = build_runtime_service_receipt(
                session=imported["session"],
                artifacts=imported["artifacts"],
                evidence=imported["evidence"],
                observations=imported["observations"],
                provider_enumeration=imported["provider_enumeration"],
                limitations=imported["limitations"],
            )
            self.assertEqual(
                completed.stdout.strip(),
                receipt["provider_enumeration"]["evidence_id"],
            )

    def _compile_java_harness(self, root: Path) -> tuple[Path, Path, Path]:
        if self.jdk is None:
            self.skipTest("a JDK is not available")
        javac, _ = self.jdk
        api_source = root / "api-src/org/spongepowered/asm/service/IMixinService.java"
        first_source = root / "provider-src/example/FirstProvider.java"
        second_source = root / "provider-src/example/SecondProvider.java"
        api_source.parent.mkdir(parents=True)
        first_source.parent.mkdir(parents=True)
        api_source.write_text(
            "package org.spongepowered.asm.service; "
            "public interface IMixinService { String getName(); }\n",
            encoding="utf-8",
        )
        first_source.write_text(
            "package example; "
            "public final class FirstProvider implements "
            "org.spongepowered.asm.service.IMixinService { "
            "public FirstProvider() {} public String getName() { return \"Shared\"; } }\n",
            encoding="utf-8",
        )
        second_source.write_text(
            "package example; "
            "public final class SecondProvider implements "
            "org.spongepowered.asm.service.IMixinService { "
            "public SecondProvider() {} public String getName() { return \"Shared\"; } }\n",
            encoding="utf-8",
        )
        api_classes = root / "api-classes"
        provider_classes = root / "provider-classes"
        probe_classes = root / "probe-classes"
        for path in (api_classes, provider_classes, probe_classes):
            path.mkdir()
        api_compile = subprocess.run(
            [str(javac), "--release", "8", "-d", str(api_classes), str(api_source)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(api_compile.returncode, 0, api_compile.stderr)
        api_jar = root / "mixin-api.jar"
        _jar_directory(api_classes, api_jar)
        provider_compile = subprocess.run(
            [
                str(javac),
                "--release",
                "8",
                "-cp",
                str(api_jar),
                "-d",
                str(provider_classes),
                str(first_source),
                str(second_source),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(provider_compile.returncode, 0, provider_compile.stderr)
        provider_jar = root / "providers.jar"
        _jar_directory(
            provider_classes,
            provider_jar,
            ["example.FirstProvider", "example.SecondProvider"],
        )
        probe_compile = subprocess.run(
            [str(javac), "--release", "8", "-d", str(probe_classes), str(PROBE_SOURCE)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(probe_compile.returncode, 0, probe_compile.stderr)
        return probe_classes, api_jar, provider_jar

    def _probe_arguments(self, output: Path, lock_bytes: bytes) -> list[str]:
        session = _session(lock_bytes)
        return [
            "--output",
            str(output),
            "--session-id",
            session["session_id"],
            "--launch-id",
            session["launch_id"],
            "--profile-id",
            session["profile_id"],
            "--side",
            session["side"],
            "--candidate-toolchain-lock-sha256",
            session["candidate_toolchain_lock_sha256"],
            "--component-topology-receipt-id",
            session["component_topology_receipt_id"],
            "--component-topology-receipt-sha256",
            session["component_topology_receipt_sha256"],
        ]

    def test_standalone_java_probe_uses_real_service_loader_and_exact_provider_source(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe_classes, api_jar, provider_jar = self._compile_java_harness(root)
            evidence_path = root / "enumeration.json"
            _, java = self.jdk
            completed = subprocess.run(
                [
                    str(java),
                    "-cp",
                    os.pathsep.join(
                        [str(probe_classes), str(api_jar), str(provider_jar)]
                    ),
                    "dev.workbench.crucible.mixins.MixinServiceProviderEnumerationProbe",
                    *self._probe_arguments(evidence_path, lock_bytes),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            evidence_bytes = evidence_path.read_bytes()
            evidence = parse_provider_enumeration_evidence(json.loads(evidence_bytes))
            self.assertEqual(evidence["state"], "enumerated")
            self.assertEqual(evidence["summary"]["provider_count"], 2)
            self.assertEqual(evidence["summary"]["duplicate_service_names"], ["Shared"])
            provider_sha = hashlib.sha256(provider_jar.read_bytes()).hexdigest()
            self.assertEqual(
                {row["provider_artifact_sha256"] for row in evidence["providers"]},
                {provider_sha},
            )
            imported = self.tool.import_provider_enumeration(
                base_observation_set=_base_observation_set(lock_bytes),
                provider_evidence_bytes=evidence_bytes,
                provider_artifact_paths=[provider_jar],
                toolchain_lock_bytes=lock_bytes,
            )
            self.assertEqual(
                len(imported["provider_enumeration"]["providers"]), 2
            )

    def test_standalone_java_probe_publishes_typed_failure_without_partial_set(self) -> None:
        lock_bytes = _lock_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe_classes, _, _ = self._compile_java_harness(root)
            evidence_path = root / "failed-enumeration.json"
            _, java = self.jdk
            completed = subprocess.run(
                [
                    str(java),
                    "-cp",
                    str(probe_classes),
                    "dev.workbench.crucible.mixins.MixinServiceProviderEnumerationProbe",
                    *self._probe_arguments(evidence_path, lock_bytes),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            evidence_bytes = evidence_path.read_bytes()
            evidence = parse_provider_enumeration_evidence(json.loads(evidence_bytes))
            self.assertEqual(evidence["state"], "failed")
            self.assertEqual(evidence["providers"], [])
            self.assertEqual(evidence["failure"]["stage"], "service_interface_load")
            imported = self.tool.import_provider_enumeration(
                base_observation_set=_base_observation_set(lock_bytes),
                provider_evidence_bytes=evidence_bytes,
                provider_artifact_paths=[],
                toolchain_lock_bytes=lock_bytes,
            )
            self.assertEqual(imported["provider_enumeration"]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
