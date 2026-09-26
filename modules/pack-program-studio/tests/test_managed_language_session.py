from __future__ import annotations

from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import jsonschema


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/pack-program-studio/src"
CORE_SOURCE = ROOT / "core/src"
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
if str(PROJECT_INTELLIGENCE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
if str(CORE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CORE_SOURCE))

from workbench_pack_program_studio import (  # noqa: E402
    AnalysisContext,
    PackProgramError,
    load_language_profile,
    load_managed_session_profile,
    load_profile,
    run_managed_language_session,
    validate_managed_session_receipt,
    validate_session_descriptor,
)
from workbench_pack_program_studio.cli import main as cli_main, run as cli_run  # noqa: E402
from workbench_pack_program_studio.managed_model import (  # noqa: E402
    descriptor_identity,
    receipt_identity,
)
from workbench_pack_program_studio.managed_session import (  # noqa: E402
    _parse_windows_processes,
    _windows_helper_environment,
    _windows_path_file_uri,
    run_in_core_session_allocation,
)
from workbench_core.host_services import install_local_host_services  # noqa: E402
from workbench_core.working_allocations import CoreWorkingAllocations  # noqa: E402
from workbench_core.working_allocations import resolve_direct_working_allocations  # noqa: E402
from workbench_api.host_filesystem import (  # noqa: E402
    inspect_private_journal, private_path, read_private_bytes,
)
from workbench_api.modules import ExecutionContext  # noqa: E402
from workbench_api.working_allocations import (  # noqa: E402
    WorkingAllocationReference, working_allocations_scope,
)


PACK_PROFILE = ROOT / "profiles/packs/supersymmetry/groovy/groovy-program-profile-v1.json"
MANAGED_PROFILE = (
    ROOT
    / "profiles/platforms/cleanroom/groovyscript/"
    "groovyscript-1.4.3-prism-managed-session-v1.json"
)
LANGUAGE_PROFILE = (
    ROOT
    / "profiles/platforms/cleanroom/groovyscript/"
    "groovyscript-1.4.3-language-service-v1.json"
)
BASELINE = ROOT / "modules/pack-program-studio/tests/fixtures/baseline"


FAKE_PRISM = r'''#!/usr/bin/env python3
import json
from pathlib import Path
import re
import signal
import socket
import sys


def frame(value):
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def read(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            raise EOFError
        if line == b"\r\n":
            break
        key, value = line.decode("ascii").split(":", 1)
        if key.casefold() == "content-length":
            length = int(value.strip())
    if length is None:
        raise ValueError("missing length")
    return json.loads(stream.read(length).decode("utf-8"))


def diagnostics(connection, uri, text):
    rows = []
    if "__workbench_diagnostic_canary__" in text:
        rows = [{
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
            "severity": 1,
            "source": "fake-groovyscript",
            "message": "unexpected token: )"
        }]
    connection.sendall(frame({
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "diagnostics": rows}
    }))


arguments = sys.argv[1:]
data_root = Path(arguments[arguments.index("--dir") + 1])
instance_id = arguments[arguments.index("--launch") + 1]
instance = data_root / "instances" / instance_id
instance_config = (instance / "instance.cfg").read_text(encoding="utf-8")
if "-Dgroovyscript.run_ls=true" not in instance_config or "OverrideJavaArgs=true" not in instance_config:
    raise SystemExit(5)
instance_config = instance_config.replace(
    "JvmArgs=-Dfixture=true -Dgroovyscript.run_ls=true",
    "JvmArgs=\"-Dfixture=true -Dgroovyscript.run_ls=true\""
)
instance_config = instance_config.replace(
    "QuitAfterGameStop=true",
    "QuitAfterGameStop=true\nlastLaunchTime=fixture"
)
(instance / "instance.cfg").write_text(instance_config, encoding="utf-8")
runtime_config = (instance / ".minecraft/config/groovyscript.cfg").read_text(encoding="utf-8")
match = re.search(r"I:languageServerPort=([0-9]+)", runtime_config)
if not match:
    raise SystemExit(6)
port = int(match.group(1))
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", port))
listener.listen(2)


def stop(_number, _frame):
    listener.close()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop)
while True:
    connection, _ = listener.accept()
    with connection:
        stream = connection.makefile("rb")
        while True:
            try:
                message = read(stream)
            except EOFError:
                break
            method = message.get("method")
            if method == "initialize":
                connection.sendall(frame({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"capabilities": {
                        "textDocumentSync": 1,
                        "documentSymbolProvider": True,
                        "completionProvider": {"triggerCharacters": ["."]},
                        "hoverProvider": True,
                        "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                        "experimental": {"textureDecorationProvider": True}
                    }}
                }))
            elif method == "textDocument/didOpen":
                document = message["params"]["textDocument"]
                diagnostics(connection, document["uri"], document["text"])
            elif method == "textDocument/didChange":
                params = message["params"]
                diagnostics(connection, params["textDocument"]["uri"], params["contentChanges"][0]["text"])
            elif method == "textDocument/documentSymbol":
                connection.sendall(frame({"jsonrpc": "2.0", "id": message["id"], "result": []}))
            elif method == "shutdown":
                connection.sendall(frame({"jsonrpc": "2.0", "id": message["id"], "result": None}))
            elif method == "exit":
                break
'''


class ManagedLanguageSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()
        cls.pack_profile = load_profile(PACK_PROFILE)
        cls.managed_profile = load_managed_session_profile(MANAGED_PROFILE)

    def _environment(self, directory: str) -> dict[str, Path]:
        root = Path(directory)
        source = root / "source"
        shutil.copytree(BASELINE, source)
        data_root = root / "prism"
        instance_id = "workbench-fixture-language-session"
        instance = data_root / "instances" / instance_id
        runtime = instance / ".minecraft"
        (runtime / "groovy").mkdir(parents=True)
        (runtime / "mods").mkdir()
        (runtime / "cache/groovy").mkdir(parents=True)
        (runtime / "config").mkdir()
        shutil.copyfile(source / "groovy/runConfig.json", runtime / "groovy/runConfig.json")
        artifact = runtime / "mods/groovyscript-1.4.3.jar"
        artifact.write_bytes(b"fixture-groovyscript-artifact")
        (runtime / "mods/example.jar").write_bytes(b"fixture-mod")
        (runtime / "cache/groovy/example.clz").write_bytes(b"fixture-cache")
        instance_config = (
            "[General]\n"
            "JvmArgs=-Dfixture=true\n"
            "OverrideJavaArgs=false\n"
            "QuitAfterGameStop=true\n"
        )
        groovy_config = (
            "# Configuration file\n\n"
            "general {\n"
            "    I:languageServerPort=25564\n"
            "}\n"
        )
        (instance / "instance.cfg").write_text(instance_config, encoding="utf-8")
        (runtime / "config/groovyscript.cfg").write_text(groovy_config, encoding="utf-8")

        language_value = json.loads(LANGUAGE_PROFILE.read_text(encoding="utf-8"))
        language_value["groovyscript"]["artifact_sha256"] = hashlib.sha256(
            artifact.read_bytes()
        ).hexdigest()
        language_value["groovyscript"]["artifact_size"] = artifact.stat().st_size
        language_path = root / "language-profile.json"
        language_path.write_text(json.dumps(language_value), encoding="utf-8")

        launcher = root / "fake-prism.py"
        launcher.write_text(FAKE_PRISM, encoding="utf-8")
        executable = Path(sys.executable).resolve()
        executable_raw = executable.read_bytes()
        command = [
            str(executable),
            str(launcher),
            "--dir",
            str(data_root),
            "--launch",
            instance_id,
        ]
        receipt = {
            "format": "workbench-runtime-launch-receipt-v3",
            "schema_version": 3,
            "launch_id": "sha256:" + "1" * 64,
            "operation_class": "local-mutation",
            "observation": {"session_exit": {"state": "exited"}},
            "launcher": {
                "family": "prism",
                "instance_id": instance_id,
                "command": command,
                "executable_uri": executable.as_uri(),
                "sha256": hashlib.sha256(executable_raw).hexdigest(),
                "size": len(executable_raw),
                "data_root": {"root_uri": data_root.resolve().as_uri()},
                "host": {"os": "linux"},
            },
            "projection": {"projection_uri": instance.resolve().as_uri()},
            "java": {},
        }
        receipt_path = root / "runtime-launch-v3.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return {
            "source": source,
            "runtime": runtime,
            "instance": instance,
            "language_profile": language_path,
            "launch_receipt": receipt_path,
        }

    def _custody(self, root: Path) -> CoreWorkingAllocations:
        return CoreWorkingAllocations(
            workspace=root, configuration_home=root / "core-config",
            locations={"evidence": root / "core-evidence"},
            owner_id="pack-program-studio",
        )

    def _run(
        self, environment: dict[str, Path], *, ready_stop: bool = True,
        allocation: WorkingAllocationReference | None = None,
        interrupt_on_ready: bool = False,
    ) -> dict[str, object]:
        if allocation is None:
            return run_in_core_session_allocation(
                self._custody(environment["source"].parent),
                requested_storage=None,
                run_session=lambda selected: self._run(
                    environment, ready_stop=ready_stop, allocation=selected,
                    interrupt_on_ready=interrupt_on_ready,
                ),
            )
        stop = threading.Event()
        events: list[dict[str, object]] = []

        def on_ready(_descriptor: object) -> None:
            if interrupt_on_ready:
                raise KeyboardInterrupt("simulated session owner interruption")
            if ready_stop:
                stop.set()

        return run_managed_language_session(
            source=environment["source"],
            pack_profile=self.pack_profile,
            language_profile=load_language_profile(environment["language_profile"]),
            managed_profile=self.managed_profile,
            context=AnalysisContext(side="client"),
            runtime_root=environment["runtime"],
            launch_receipt=environment["launch_receipt"],
            session_allocation=allocation,
            requested_port=None,
            readiness_timeout=5,
            session_timeout=0.15,
            connect_timeout=0.2,
            diagnostic_timeout=2,
            stop_event=stop,
            on_event=lambda event: events.append(dict(event)),
            on_ready=on_ready,
        )

    def test_profile_descriptor_and_receipt_schemas_are_valid(self) -> None:
        schema_root = ROOT / "modules/pack-program-studio/schemas"
        jsonschema.Draft202012Validator(
            json.loads(
                (schema_root / "workbench-groovyscript-managed-session-profile-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        ).validate(json.loads(MANAGED_PROFILE.read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(self._environment(directory))
        descriptor = result["handoff"]
        jsonschema.Draft202012Validator(
            json.loads(
                (schema_root / "workbench-groovy-language-session-descriptor-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        ).validate(descriptor)
        jsonschema.Draft202012Validator(
            json.loads(
                (schema_root / "workbench-groovy-managed-language-session-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        ).validate(result)

    def test_windows_teardown_identity_gap_is_retained_as_present(self) -> None:
        raw = json.dumps(
            [
                {
                    "pid": 42,
                    "creation_date": "",
                    "executable_path": "",
                    "tracker": "windows-pid-revalidation",
                }
            ]
        )
        with self.assertRaisesRegex(PackProgramError, "creation date"):
            _parse_windows_processes(raw)
        rows = _parse_windows_processes(raw, require_identity=False)
        self.assertEqual(42, rows[42]["pid"])

    def test_windows_server_workspace_uri_mapping_is_explicit(self) -> None:
        self.assertEqual(
            "file:///C:/Pack%20Root/groovy",
            _windows_path_file_uri(r"c:\Pack Root\groovy"),
        )
        self.assertEqual(
            "file://wsl.localhost/Ubuntu/workspace/Pack%20Root/groovy",
            _windows_path_file_uri(
                r"\\wsl.localhost\Ubuntu\workspace\Pack Root\groovy"
            ),
        )
        with self.assertRaisesRegex(PackProgramError, "unsupported"):
            _windows_path_file_uri("relative\\groovy")

    def test_wsl_forwards_every_windows_helper_identity(self) -> None:
        values = {
            "WORKBENCH_GROOVY_INSTANCE_TOKEN": "workbench-fixture",
            "WORKBENCH_GROOVY_PROCESS_IDS": "42",
        }
        with patch.dict(
            "os.environ",
            {"WSL_INTEROP": "/run/WSL/1_interop", "WSLENV": "EXISTING/u"},
            clear=True,
        ):
            environment = _windows_helper_environment("powershell.exe", values)
        self.assertEqual("workbench-fixture", environment["WORKBENCH_GROOVY_INSTANCE_TOKEN"])
        self.assertEqual("42", environment["WORKBENCH_GROOVY_PROCESS_IDS"])
        self.assertEqual(
            {"EXISTING", *values},
            {entry.split("/", 1)[0] for entry in environment["WSLENV"].split(":")},
        )

    def test_managed_session_proves_readiness_hands_off_and_restores_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(directory)
            instance_before = (environment["instance"] / "instance.cfg").read_bytes()
            config_before = (environment["runtime"] / "config/groovyscript.cfg").read_bytes()
            receipt_before = environment["launch_receipt"].read_bytes()
            result = self._run(environment)
            instance_after = (environment["instance"] / "instance.cfg").read_text(encoding="utf-8")
            self.assertIn("JvmArgs=-Dfixture=true\n", instance_after)
            self.assertIn("OverrideJavaArgs=false\n", instance_after)
            self.assertIn("lastLaunchTime=fixture\n", instance_after)
            self.assertEqual(config_before, (environment["runtime"] / "config/groovyscript.cfg").read_bytes())
            self.assertEqual(receipt_before, environment["launch_receipt"].read_bytes())
            self.assertFalse((environment["instance"] / ".workbench-groovy-language-service.lock").exists())
            events_path = Path(result["events"]["path"])
            events_raw = events_path.read_bytes()
            events = [json.loads(line) for line in events_raw.splitlines()]
            self.assertEqual(result["events"]["count"], len(events))
            self.assertEqual(list(range(1, len(events) + 1)), [row["sequence"] for row in events])
            self.assertEqual(hashlib.sha256(events_raw).hexdigest(), result["events"]["sha256"])
            inspection = inspect_private_journal(events_path, byte_limit=len(events_raw))
            self.assertEqual(len(events_raw), inspection["complete_size"])
            self.assertEqual(0, inspection["incomplete_size"])
            session_dir = events_path.parent
            self.assertTrue(private_path(session_dir, directory=True))
            self.assertTrue(private_path(session_dir / "overlay-originals", directory=True))
            self.assertEqual(
                result,
                json.loads(read_private_bytes(
                    session_dir / "session-receipt-v1.json", byte_limit=64 * 1024 * 1024,
                )),
            )
            self.assertEqual(
                result["handoff"],
                json.loads(read_private_bytes(
                    session_dir / "session-descriptor-v1.json", byte_limit=64 * 1024 * 1024,
                )),
            )
            originals = {
                "launcher-jvm": instance_before,
                "language-server-port": config_before,
            }
            for overlay in result["overlays"]:
                backup = Path(overlay["original"]["backup_path"])
                self.assertEqual(
                    originals[overlay["role"]],
                    read_private_bytes(backup, byte_limit=4 * 1024 * 1024),
                )

        self.assertEqual(result, validate_managed_session_receipt(result))
        self.assertEqual("complete", result["state"])
        self.assertEqual("confirmed", result["readiness"]["state"])
        self.assertEqual("confirmed", result["readiness"]["canary_state"])
        self.assertEqual("reserved-random-loopback", result["endpoint"]["allocation"])
        self.assertNotEqual(25564, result["endpoint"]["port"])
        self.assertEqual("session-owned", result["launch"]["ownership"])
        self.assertEqual([], result["shutdown"]["orphaned_pids"])
        self.assertTrue(all(row["restore"]["state"].startswith("restored-") for row in result["overlays"]))
        launcher_overlay = next(row for row in result["overlays"] if row["role"] == "launcher-jvm")
        self.assertEqual("restored-merged", launcher_overlay["restore"]["state"])
        self.assertTrue(launcher_overlay["restore"]["preserved_external_changes"])
        descriptor = result["handoff"]
        self.assertEqual(descriptor, validate_session_descriptor(descriptor))
        self.assertEqual(["terminal", "intellij", "vscode"], descriptor["clients"]["consumers"])
        self.assertEqual(result["endpoint"], descriptor["endpoint"])

    def test_core_session_allocation_retains_and_reopens_exact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self._environment(directory)
            custody = self._custody(root)
            result = run_in_core_session_allocation(
                custody, requested_storage=None,
                run_session=lambda allocation: self._run(
                    environment, allocation=allocation,
                ),
            )
            (description,) = custody.inventory()
            self.assertEqual("complete", description.status)
            self.assertTrue(description.reference.path.is_relative_to(root / "core-evidence"))
            self.assertEqual(
                f"workbench-groovy-language-session:{description.reference.path.name}",
                result["session_id"],
            )
            self.assertEqual(
                {"events-v1.jsonl", "session-descriptor-v1.json",
                 "session-receipt-v1.json", "overlay-originals/instance.cfg",
                 "overlay-originals/groovyscript.cfg"},
                {row["relative_path"] for row in description.evidence},
            )
            reopened = self._custody(root).verify(description.reference.allocation_id)
            self.assertEqual(description, reopened)

    def test_interrupted_core_session_retains_reopenable_partial_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self._environment(directory)
            custody = self._custody(root)
            with self.assertRaisesRegex(KeyboardInterrupt, "owner interruption"):
                run_in_core_session_allocation(
                    custody, requested_storage=None,
                    run_session=lambda allocation: self._run(
                        environment, allocation=allocation,
                        interrupt_on_ready=True,
                    ),
                )
            (description,) = custody.inventory()
            self.assertEqual("failed", description.status)
            self.assertIn("KeyboardInterrupt", description.failure)
            retained = {row["relative_path"] for row in description.evidence}
            self.assertIn("events-v1.jsonl", retained)
            self.assertIn("session-descriptor-v1.json", retained)
            self.assertNotIn("session-receipt-v1.json", retained)
            self.assertEqual(
                description, self._custody(root).verify(description.reference.allocation_id),
            )

    def test_core_session_rejects_unselected_explicit_storage_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custody = self._custody(root)
            with self.assertRaisesRegex(ValueError, "outside Core-selected stores"):
                run_in_core_session_allocation(
                    custody, requested_storage=root / "other-session-store",
                    run_session=lambda _allocation: self.fail("session was launched"),
                )
            self.assertFalse((root / "core-config").exists())
            self.assertFalse((root / "other-session-store").exists())

    def test_blocked_core_session_retains_failed_receipt_for_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self._environment(directory)
            (environment["instance"] / ".workbench-groovy-language-service.lock").write_bytes(
                b"external owner\n",
            )
            custody = self._custody(root)
            result = run_in_core_session_allocation(
                custody, requested_storage=None,
                run_session=lambda allocation: self._run(
                    environment, allocation=allocation,
                ),
            )
            self.assertEqual("blocked", result["state"])
            (description,) = custody.inventory()
            self.assertEqual("failed", description.status)
            self.assertIn(
                "session-receipt-v1.json",
                {row["relative_path"] for row in description.evidence},
            )
            self.assertEqual(
                description, self._custody(root).verify(description.reference.allocation_id),
            )

    def test_existing_lock_blocks_without_mutating_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(directory)
            lock = environment["instance"] / ".workbench-groovy-language-service.lock"
            lock.write_text("external owner\n", encoding="utf-8")
            before = (environment["instance"] / "instance.cfg").read_bytes()
            result = self._run(environment)
            self.assertEqual(before, (environment["instance"] / "instance.cfg").read_bytes())
            self.assertEqual("external owner\n", lock.read_text(encoding="utf-8"))
        self.assertEqual("blocked", result["state"])
        self.assertIsNone(result["launch"]["launcher_pid"])
        self.assertTrue(all(row["restore"]["state"] == "not-applied" for row in result["overlays"]))

    def test_semantic_validator_rejects_tampered_process_and_restore_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(self._environment(directory))
        forged = deepcopy(result)
        forged["shutdown"]["orphaned_pids"] = [999999]
        forged["receipt_id"] = receipt_identity(forged)
        with self.assertRaisesRegex(PackProgramError, "orphan"):
            validate_managed_session_receipt(forged)
        forged = deepcopy(result)
        forged["overlays"][0]["restore"]["preserved_external_changes"] = False
        forged["receipt_id"] = receipt_identity(forged)
        with self.assertRaisesRegex(PackProgramError, "merge"):
            validate_managed_session_receipt(forged)
        forged = deepcopy(result)
        forged["launch"]["client_processes"] = []
        forged["receipt_id"] = receipt_identity(forged)
        with self.assertRaisesRegex(PackProgramError, "owned client process"):
            validate_managed_session_receipt(forged)
        forged = deepcopy(result)
        forged["endpoint"]["route"] = "wsl-windows-stdio-bridge"
        forged["receipt_id"] = receipt_identity(forged)
        with self.assertRaisesRegex(PackProgramError, "distinct upstream"):
            validate_managed_session_receipt(forged)
        forged = deepcopy(result)
        forged["handoff"]["program"]["source_root"] += "-different"
        forged["handoff"]["descriptor_id"] = descriptor_identity(forged["handoff"])
        forged["receipt_id"] = receipt_identity(forged)
        with self.assertRaisesRegex(PackProgramError, "handoff program differs"):
            validate_managed_session_receipt(forged)

    def test_cli_emits_final_json_and_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(directory)
            custody = self._custody(Path(directory))
            output = StringIO()
            error = StringIO()
            code = cli_run(
                [
                    "session",
                    "--profile",
                    "supersymmetry",
                    "--language-profile",
                    str(environment["language_profile"]),
                    "--source",
                    str(environment["source"]),
                    "--runtime-root",
                    str(environment["runtime"]),
                    "--launch-receipt",
                    str(environment["launch_receipt"]),
                    "--readiness-timeout",
                    "5",
                    "--session-timeout",
                    "0.1",
                    "--connect-timeout",
                    "0.2",
                    "--diagnostic-timeout",
                    "2",
                    "--json",
                ],
                root=ROOT,
                output=output,
                error=error,
                session_custody=custody,
            )
        self.assertEqual(0, code, error.getvalue())
        self.assertEqual("complete", json.loads(output.getvalue())["state"])

    def test_installed_session_cli_uses_core_selected_evidence_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(directory)
            custody = self._custody(Path(directory))
            output = StringIO()
            error = StringIO()
            code = cli_run(
                [
                    "session", "--profile", "supersymmetry",
                    "--language-profile", str(environment["language_profile"]),
                    "--source", str(environment["source"]),
                    "--runtime-root", str(environment["runtime"]),
                    "--launch-receipt", str(environment["launch_receipt"]),
                    "--readiness-timeout", "5", "--session-timeout", "0.1",
                    "--connect-timeout", "0.2", "--diagnostic-timeout", "2",
                    "--json",
                ],
                root=ROOT, output=output, error=error, session_custody=custody,
            )
            self.assertEqual(0, code, error.getvalue())
            result = json.loads(output.getvalue())
            (description,) = custody.inventory()
            self.assertEqual("complete", description.status)
            self.assertEqual(
                description.reference.path, Path(result["events"]["path"]).parent,
            )
            self.assertTrue(description.reference.path.is_relative_to(Path(directory) / "core-evidence"))

    def test_installed_registration_passes_core_allocation_to_session(self) -> None:
        from workbench_registration_pack_program_studio import groovy

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custody = self._custody(root)
            context = ExecutionContext(workspace=root, state_root=root)
            with working_allocations_scope(custody), patch(
                "workbench_pack_program_studio.cli.main", return_value=0,
            ) as main:
                self.assertEqual(0, groovy(["session"], context=context))
            self.assertIs(custody, main.call_args.kwargs["session_custody"])

    def test_direct_session_entry_binds_core_host(self) -> None:
        sentinel = object()
        with patch(
            "workbench_core.host_services.install_local_host_services",
            wraps=install_local_host_services,
        ) as install, patch(
            "workbench_core.working_allocations.resolve_direct_working_allocations",
            return_value=sentinel,
        ) as resolve, patch(
            "workbench_pack_program_studio.cli.run", return_value=0,
        ) as run:
            self.assertEqual(0, cli_main(["session"], root=ROOT))
        install.assert_called_once_with()
        resolve.assert_called_once_with(ROOT, owner_id="pack-program-studio")
        self.assertEqual(["session"], run.call_args.args[0])
        self.assertIs(sentinel, run.call_args.kwargs["session_custody"])

    def test_direct_session_resolves_core_workspace_and_evidence_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {
                "WORKBENCH_CONFIG_HOME": str(root / "config"),
                "WORKBENCH_WORKSPACE": str(root),
                "WORKBENCH_STATE_ROOT": str(root / "state"),
            }):
                custody = resolve_direct_working_allocations(
                    ROOT, owner_id="pack-program-studio",
                )
            self.assertEqual(root, custody.workspace)
            self.assertEqual(root / "config/resources-v1", custody.catalog.resources.root)
            self.assertEqual(root / "state/evidence", custody.locations["evidence"])

    def test_direct_session_entry_retains_through_resolved_core_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self._environment(directory)
            output = StringIO()
            error = StringIO()
            with patch.dict("os.environ", {
                "WORKBENCH_CONFIG_HOME": str(root / "config"),
                "WORKBENCH_WORKSPACE": str(root),
                "WORKBENCH_STATE_ROOT": str(root / "state"),
            }), patch("sys.stdout", output), patch("sys.stderr", error):
                code = cli_main([
                    "session", "--profile", "supersymmetry",
                    "--language-profile", str(environment["language_profile"]),
                    "--source", str(environment["source"]),
                    "--runtime-root", str(environment["runtime"]),
                    "--launch-receipt", str(environment["launch_receipt"]),
                    "--readiness-timeout", "5", "--session-timeout", "0.1",
                    "--connect-timeout", "0.2", "--diagnostic-timeout", "2",
                    "--json",
                ], root=ROOT)
            self.assertEqual(0, code, error.getvalue())
            result = json.loads(output.getvalue())
            session_dir = Path(result["events"]["path"]).parent
            self.assertTrue(session_dir.is_relative_to(root / "state/evidence"))
            (description,) = CoreWorkingAllocations(
                workspace=root, configuration_home=root / "config",
                locations={"evidence": root / "state/evidence"},
                owner_id="pack-program-studio",
            ).inventory()
            self.assertEqual("complete", description.status)
            self.assertEqual(session_dir, description.reference.path)

    def test_session_api_fails_closed_without_core_allocation(self) -> None:
        output = StringIO()
        error = StringIO()
        code = cli_run(
            ["session", "--profile", "supersymmetry", "--runtime-root", "/missing",
             "--launch-receipt", "/missing"],
            root=ROOT, output=output, error=error,
        )
        self.assertEqual(2, code)
        self.assertIn("require a Core working allocation", error.getvalue())


if __name__ == "__main__":
    unittest.main()
