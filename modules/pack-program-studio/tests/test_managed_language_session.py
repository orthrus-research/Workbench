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
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
if str(PROJECT_INTELLIGENCE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

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
from workbench_pack_program_studio.cli import run as cli_run  # noqa: E402
from workbench_pack_program_studio.managed_model import (  # noqa: E402
    descriptor_identity,
    receipt_identity,
)
from workbench_pack_program_studio.managed_session import (  # noqa: E402
    _parse_windows_processes,
    _windows_helper_environment,
    _windows_path_file_uri,
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
            "session_storage": root / "sessions",
        }

    def _run(self, environment: dict[str, Path], *, ready_stop: bool = True) -> dict[str, object]:
        stop = threading.Event()
        events: list[dict[str, object]] = []

        def on_ready(_descriptor: object) -> None:
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
            session_storage=environment["session_storage"],
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
                    "--session-storage",
                    str(environment["session_storage"]),
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
            )
        self.assertEqual(0, code, error.getvalue())
        self.assertEqual("complete", json.loads(output.getvalue())["state"])


if __name__ == "__main__":
    unittest.main()
