from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/tools/import_cleanmix_runtime_service.py"
)
LOCK_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "transformer-toolchain-lock-v1.json"
)
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"
for source in (PROJECT_INTELLIGENCE_SOURCE, CRUCIBLE_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_project_intelligence import (  # noqa: E402
    build_topology_receipt,
    render_receipt,
)


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "import_cleanmix_runtime_service",
        TOOL_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _topology_receipt_bytes() -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as container:
        container.writestr(
            "META-INF/MANIFEST.MF",
            "Manifest-Version: 1.0\r\n\r\n",
        )
    receipt = build_topology_receipt([("fixture.jar", archive.getvalue())])
    return render_receipt(receipt)


def _lock_bytes(artifact_bytes: bytes, *, profile_id: str) -> bytes:
    value = {
        "format": "workbench-cleanroom-transformer-toolchain-lock-v1",
        "schema_version": 1,
        "lock_id": "test-cleanroom-transformer-toolchain",
        "binding": {"candidate_id": profile_id},
        "native_service_expectations": {
            "mixin_service": "com.cleanroommc.cleanmix.service.CleanMixService",
            "mixin_service_name": "CleanMix",
        },
        "platform_versions": {
            "cleanmix": "0.7.0",
            "cleanmix_upstream_sponge_mixin": "0.8.7",
        },
        "artifacts": [
            {
                "coordinate": "com.cleanroommc:cleanmix:0.7.0",
                "filename": "cleanmix-0.7.0.jar",
                "role": "mixin-engine",
                "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
                "size": len(artifact_bytes),
            }
        ],
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _find_locked_cleanmix_artifact(lock: dict) -> Path | None:
    record = next(
        row for row in lock["artifacts"] if row["role"] == "mixin-engine"
    )
    version = record["coordinate"].rsplit(":", 1)[-1]
    cache_root = (
        Path.home()
        / ".gradle/caches/modules-2/files-2.1/com.cleanroommc/cleanmix"
        / version
    )
    if not cache_root.is_dir():
        return None
    for candidate in sorted(cache_root.rglob(record["filename"])):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        payload = candidate.read_bytes()
        if (
            len(payload) == record["size"]
            and hashlib.sha256(payload).hexdigest() == record["sha256"]
        ):
            return candidate
    return None


def _debug_log(artifact: Path) -> bytes:
    source = (
        f"jar:{artifact.resolve().as_uri()}!"
        "/org/spongepowered/asm/mixin/MixinEnvironment.class"
    )
    return (
        "[09:22:56] [main/INFO] [FML]: unrelated\n"
        "[09:22:57] [main/INFO] [Cleanroom]: Initializing CleanMix...\n"
        "[09:22:57] [main/DEBUG] [CleanMix]: MixinService [CleanMix] "
        "was successfully booted in "
        "net.minecraft.launchwrapper.LaunchClassLoader@4b013c76\n"
        f"[09:22:57] [main/INFO] [CleanMix]: SpongePowered MIXIN Subsystem "
        f"Version=0.8.7 Source={source} Service=CleanMix Env=SERVER\n"
    ).encode("utf-8")


def _latest_log(artifact: Path) -> bytes:
    source = (
        f"jar:{artifact.resolve().as_uri()}!"
        "/org/spongepowered/asm/mixin/MixinEnvironment.class"
    )
    return (
        "[09:22:57] [main/INFO]: unrelated\n"
        "[09:22:57] [main/INFO] [Cleanroom]: Initializing CleanMix...\n"
        f"[09:22:57] [main/INFO] [CleanMix]: SpongePowered MIXIN Subsystem "
        f"Version=0.8.7 Source={source} Service=CleanMix Env=SERVER\n"
    ).encode("utf-8")


class CleanMixRuntimeServiceImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def _fixture(self, root: Path):
        artifact_bytes = b"synthetic exact CleanMix container bytes"
        artifact = root / "cleanmix-0.7.0.jar"
        artifact.write_bytes(artifact_bytes)
        profile_id = "workbench-platform:cleanroom:test"
        return {
            "artifact": artifact,
            "artifact_bytes": artifact_bytes,
            "profile_id": profile_id,
            "lock_bytes": _lock_bytes(artifact_bytes, profile_id=profile_id),
            "topology_bytes": _topology_receipt_bytes(),
            "session": {
                "session_id": "session:test",
                "launch_id": "launch:test",
                "profile_id": profile_id,
                "side": "dedicated_server",
            },
        }

    def _build(self, fixture, logs):
        return self.tool.build_observation_set(
            logs=logs,
            cleanmix_artifact_bytes=fixture["artifact_bytes"],
            cleanmix_artifact_path=fixture["artifact"].resolve(),
            toolchain_lock_bytes=fixture["lock_bytes"],
            component_topology_receipt_bytes=fixture["topology_bytes"],
            session=fixture["session"],
        )

    def test_exact_debug_and_latest_reports_bind_complete_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            debug = _debug_log(fixture["artifact"])
            latest = _latest_log(fixture["artifact"])
            value = self._build(fixture, {"latest": latest, "debug": debug})

        self.assertEqual(value["format"], self.tool.OBSERVATION_SET_FORMAT)
        self.assertEqual(
            [row["label"] for row in value["evidence"]],
            ["debug.log", "latest.log"],
        )
        self.assertEqual(
            value["evidence"][0],
            {
                "source_sha256": hashlib.sha256(debug).hexdigest(),
                "size_bytes": len(debug),
                "kind": "runtime_log",
                "label": "debug.log",
            },
        )
        self.assertEqual(
            [row["kind"] for row in value["observations"]],
            [
                "initialization_report",
                "selection_report",
                "subsystem_report",
                "initialization_report",
                "subsystem_report",
            ],
        )
        self.assertEqual(
            [row["sequence"] for row in value["observations"]],
            [1, 2, 3, 4, 5],
        )
        subsystem = value["observations"][2]
        self.assertEqual(subsystem["subsystem_version"], "0.8.7")
        self.assertEqual(subsystem["environment"], "SERVER")
        self.assertEqual(
            subsystem["source_artifact_sha256"],
            hashlib.sha256(fixture["artifact_bytes"]).hexdigest(),
        )
        self.assertNotIn("service_class", subsystem)
        self.assertEqual(
            value["provider_enumeration"],
            {
                "state": "not_enumerated",
                "mechanism": None,
                "providers": [],
                "evidence_sha256": None,
                "evidence_id": None,
                "source_record": None,
                "failure": None,
            },
        )
        self.assertIn(
            "not proof of the CleanMixService implementation class",
            " ".join(value["limitations"]),
        )
        self.assertNotIn("final", json.dumps(value["observations"]).lower())

    def test_topology_identity_and_file_sha_are_derived_from_validated_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            value = self._build(
                fixture,
                {"debug": _debug_log(fixture["artifact"])},
            )
            topology = json.loads(fixture["topology_bytes"])
        self.assertEqual(
            value["session"]["component_topology_receipt_id"],
            topology["receipt_id"],
        )
        self.assertEqual(
            value["session"]["component_topology_receipt_sha256"],
            hashlib.sha256(fixture["topology_bytes"]).hexdigest(),
        )

    def test_java_thread_name_may_contain_slashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            debug = _debug_log(fixture["artifact"]).replace(
                b"[main/", b"[worker/io/"
            )
            value = self._build(fixture, {"debug": debug})
        self.assertTrue(value["observations"])
        self.assertTrue(
            all(row["thread"] == "worker/io" for row in value["observations"])
        )

    def test_tampered_topology_receipt_fails_semantic_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            topology = json.loads(fixture["topology_bytes"])
            topology["summary"]["artifact_count"] += 1
            fixture["topology_bytes"] = json.dumps(topology).encode("utf-8")
            with self.assertRaisesRegex(
                self.tool.RuntimeServiceImportError,
                "semantic validation",
            ):
                self._build(
                    fixture,
                    {"debug": _debug_log(fixture["artifact"])},
                )

    def test_reported_source_must_be_existing_same_measured_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            other_root = root / "other"
            other_root.mkdir()
            other = other_root / "cleanmix-0.7.0.jar"
            other.write_bytes(fixture["artifact_bytes"])
            with self.assertRaisesRegex(
                self.tool.RuntimeServiceImportError,
                "not the measured artifact",
            ):
                self._build(fixture, {"debug": _debug_log(other)})

    def test_missing_or_nonlocal_reported_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            missing = root / "missing" / "cleanmix-0.7.0.jar"
            with self.assertRaisesRegex(
                self.tool.RuntimeServiceImportError,
                "source is unavailable",
            ):
                self._build(fixture, {"debug": _debug_log(missing)})

            debug = _debug_log(fixture["artifact"]).replace(
                fixture["artifact"].resolve().as_uri().encode("utf-8"),
                b"file:/C:/Cleanroom/cleanmix-0.7.0.jar",
            )
            with self.assertRaisesRegex(
                self.tool.RuntimeServiceImportError,
                "not host-local",
            ):
                self._build(fixture, {"debug": debug})

    def test_lock_artifact_and_reported_version_mismatches_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = self._fixture(Path(directory))
            with self.assertRaisesRegex(
                self.tool.RuntimeServiceImportError,
                "artifact SHA-256",
            ):
                self.tool.build_observation_set(
                    logs={"debug": _debug_log(fixture["artifact"])},
                    cleanmix_artifact_bytes=b"different",
                    cleanmix_artifact_path=fixture["artifact"].resolve(),
                    toolchain_lock_bytes=fixture["lock_bytes"],
                    component_topology_receipt_bytes=fixture["topology_bytes"],
                    session=fixture["session"],
                )

            wrong_version = _debug_log(fixture["artifact"]).replace(
                b"Version=0.8.7",
                b"Version=0.8.6",
            )
            with self.assertRaisesRegex(
                self.tool.RuntimeServiceImportError,
                "version does not match",
            ):
                self._build(fixture, {"debug": wrong_version})

    def test_cli_output_is_accepted_by_generic_crucible_assembler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            debug = root / "debug.log"
            latest = root / "latest.log"
            lock = root / "transformer-toolchain-lock-v1.json"
            topology = root / "topology-receipt.json"
            observations = root / "observations.json"
            receipt = root / "runtime-service-receipt.json"
            debug.write_bytes(_debug_log(fixture["artifact"]))
            latest.write_bytes(_latest_log(fixture["artifact"]))
            lock.write_bytes(fixture["lock_bytes"])
            topology.write_bytes(fixture["topology_bytes"])

            imported = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--debug-log",
                    str(debug),
                    "--latest-log",
                    str(latest),
                    "--cleanmix-artifact",
                    str(fixture["artifact"]),
                    "--toolchain-lock",
                    str(lock),
                    "--component-topology-receipt",
                    str(topology),
                    "--output",
                    str(observations),
                    "--session-id",
                    fixture["session"]["session_id"],
                    "--launch-id",
                    fixture["session"]["launch_id"],
                    "--profile-id",
                    fixture["profile_id"],
                    "--side",
                    fixture["session"]["side"],
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(imported.stdout.strip(), "5")
            assembled = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "modules/crucible/tools"
                        / "assemble_mixin_runtime_service_receipt.py"
                    ),
                    "--input",
                    str(observations),
                    "--output",
                    str(receipt),
                ],
                cwd=ROOT,
                env={"PYTHONPATH": str(CRUCIBLE_SOURCE)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(assembled.returncode, 0, assembled.stderr)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(
                value["summary"]["reported_selection_state"],
                "one_name_reported",
            )
            self.assertEqual(
                value["summary"]["enumerated_provider_uniqueness"],
                "unknown",
            )
            self.assertFalse(value["boundaries"]["final_transformations_proved"])
            self.assertTrue(
                value["boundaries"][
                    "mixin_runtime_code_source_is_not_service_provider_attribution"
                ]
            )

    def test_real_retained_aa1_logs_when_available(self) -> None:
        debug = (
            ROOT
            / ".workbench/evidence/worldgen-observatory/exact-runtime-v2/aa-1"
            / "server/logs/debug.log"
        )
        latest = debug.with_name("latest.log")
        if not all(path.is_file() for path in (debug, latest, LOCK_PATH)):
            self.skipTest("retained exact-runtime aa-1 service evidence is unavailable")

        lock_bytes = LOCK_PATH.read_bytes()
        lock = json.loads(lock_bytes)
        artifact = _find_locked_cleanmix_artifact(lock)
        if artifact is None:
            self.skipTest("the exact locked CleanMix artifact is unavailable")
        value = self.tool.build_observation_set(
            logs={"debug": debug.read_bytes(), "latest": latest.read_bytes()},
            cleanmix_artifact_bytes=artifact.read_bytes(),
            cleanmix_artifact_path=artifact.resolve(),
            toolchain_lock_bytes=lock_bytes,
            component_topology_receipt_bytes=_topology_receipt_bytes(),
            session={
                "session_id": "session:retained-aa-1-test",
                "launch_id": "launch:retained-aa-1-test",
                "profile_id": lock["binding"]["candidate_id"],
                "side": "dedicated_server",
            },
        )
        debug_rows = [
            row
            for row in value["observations"]
            if row["evidence_sha256"] == hashlib.sha256(debug.read_bytes()).hexdigest()
        ]
        latest_rows = [
            row
            for row in value["observations"]
            if row["evidence_sha256"]
            == hashlib.sha256(latest.read_bytes()).hexdigest()
        ]
        self.assertEqual(
            [(row["kind"], row["source_record"]) for row in debug_rows],
            [
                ("initialization_report", "line:157"),
                ("selection_report", "line:158"),
                ("subsystem_report", "line:161"),
            ],
        )
        self.assertEqual(
            [(row["kind"], row["source_record"]) for row in latest_rows],
            [
                ("initialization_report", "line:13"),
                ("subsystem_report", "line:14"),
            ],
        )
        self.assertEqual(value["artifacts"][0]["size_bytes"], 1_094_214)
        self.assertEqual(
            value["artifacts"][0]["artifact_sha256"],
            "a41daa71398e948bc09fa578ae2460be639df0fa82fccdb630b316418d5a4a36",
        )
        self.assertEqual(value["provider_enumeration"]["state"], "not_enumerated")


if __name__ == "__main__":
    unittest.main()
