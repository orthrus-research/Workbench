from __future__ import annotations

from contextlib import contextmanager
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
    build_language_service_result,
    load_language_profile,
    load_profile,
    validate_language_result,
)
from workbench_pack_program_studio.cli import run as cli_run  # noqa: E402
from workbench_pack_program_studio.language_model import (  # noqa: E402
    language_result_identity,
)
from workbench_pack_program_studio.lsp import (  # noqa: E402
    LspConnection,
    LspLimits,
    LspProtocolError,
)


PACK_PROFILE = ROOT / "profiles/packs/supersymmetry/groovy/groovy-program-profile-v1.json"
LANGUAGE_PROFILE = (
    ROOT
    / "profiles/platforms/cleanroom/groovyscript/"
    "groovyscript-1.4.3-language-service-v1.json"
)
BASELINE = ROOT / "modules/pack-program-studio/tests/fixtures/baseline"


class _FakeLanguageServer:
    def __init__(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(5)
        self.port = int(self.listener.getsockname()[1])
        self.errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.thread.join(timeout=5)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("fake language server did not stop")
        if self.errors:
            raise self.errors[0]

    def _run(self) -> None:
        try:
            connection, _ = self.listener.accept()
            connection.settimeout(5)
            with connection:
                stream = connection.makefile("rb")
                while True:
                    message = _read_message(stream)
                    method = message.get("method")
                    if method == "initialize":
                        _send_message(
                            connection,
                            {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "result": {
                                    "capabilities": {
                                        "textDocumentSync": 1,
                                        "documentSymbolProvider": True,
                                        "completionProvider": {"triggerCharacters": ["."]},
                                    }
                                },
                            },
                        )
                    elif method == "textDocument/didOpen":
                        document = message["params"]["textDocument"]
                        _send_diagnostics(
                            connection,
                            document["uri"],
                            document["text"],
                        )
                    elif method == "textDocument/didChange":
                        params = message["params"]
                        _send_diagnostics(
                            connection,
                            params["textDocument"]["uri"],
                            params["contentChanges"][0]["text"],
                        )
                    elif method == "textDocument/documentSymbol":
                        _send_message(
                            connection,
                            {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "result": [{"name": "fixture"}],
                            },
                        )
                    elif method == "shutdown":
                        _send_message(
                            connection,
                            {"jsonrpc": "2.0", "id": message["id"], "result": None},
                        )
                    elif method == "exit":
                        return
        except BaseException as exc:  # pragma: no cover - surfaced by close
            self.errors.append(exc)


def _read_message(stream: object) -> dict[str, object]:
    content_length = None
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("client closed fake LSP connection")
        if line == b"\r\n":
            break
        key, value = line.decode("ascii").split(":", 1)
        if key.casefold() == "content-length":
            content_length = int(value.strip())
    if content_length is None:
        raise ValueError("missing Content-Length")
    return json.loads(stream.read(content_length).decode("utf-8"))


def _send_message(connection: socket.socket, value: dict[str, object]) -> None:
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    connection.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)


def _send_diagnostics(connection: socket.socket, uri: str, text: str) -> None:
    diagnostics = []
    if "__workbench_diagnostic_canary__" in text:
        diagnostics.append(_diagnostic("unexpected token: )"))
    elif "BROKEN_TOKEN" in text:
        diagnostics.append(_diagnostic("fixture compiler rejected BROKEN_TOKEN"))
    _send_message(
        connection,
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": diagnostics},
        },
    )


def _diagnostic(message: str) -> dict[str, object]:
    return {
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1},
        },
        "severity": 1,
        "source": "fake-groovyscript",
        "message": message,
    }


def _frame(raw_json: bytes) -> bytes:
    return f"Content-Length: {len(raw_json)}\r\n\r\n".encode("ascii") + raw_json


@contextmanager
def _server() -> object:
    server = _FakeLanguageServer()
    server.start()
    try:
        yield server
    finally:
        server.close()


class LanguageServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack_profile = load_profile(PACK_PROFILE)

    def _environment(self, directory: str, *, broken: bool = False) -> tuple[Path, Path, Path]:
        root = Path(directory)
        source = root / "source"
        shutil.copytree(BASELINE, source)
        if broken:
            recipe = source / "groovy/postInit/Recipes.groovy"
            recipe.write_text(recipe.read_text(encoding="utf-8") + "\nBROKEN_TOKEN\n", encoding="utf-8")
        runtime = root / "runtime"
        (runtime / "groovy").mkdir(parents=True)
        (runtime / "mods").mkdir()
        (runtime / "cache/groovy").mkdir(parents=True)
        shutil.copyfile(source / "groovy/runConfig.json", runtime / "groovy/runConfig.json")
        artifact = runtime / "mods/groovyscript-1.4.3.jar"
        artifact.write_bytes(b"fixture-groovyscript-artifact")
        (runtime / "mods/example.jar").write_bytes(b"fixture-mod")
        (runtime / "cache/groovy/example.clz").write_bytes(b"compiled-cache")

        profile_value = json.loads(LANGUAGE_PROFILE.read_text(encoding="utf-8"))
        profile_value["groovyscript"]["artifact_sha256"] = hashlib.sha256(
            artifact.read_bytes()
        ).hexdigest()
        profile_value["groovyscript"]["artifact_size"] = artifact.stat().st_size
        profile_path = root / "language-profile.json"
        profile_path.write_text(json.dumps(profile_value), encoding="utf-8")
        return source, runtime, profile_path

    def _result(self, directory: str, *, broken: bool = False) -> dict[str, object]:
        source, runtime, profile_path = self._environment(directory, broken=broken)
        with _server() as server:
            return build_language_service_result(
                source=source,
                pack_profile=self.pack_profile,
                language_profile=load_language_profile(profile_path),
                context=AnalysisContext(side="client"),
                runtime_root=runtime,
                selected_paths=["postInit/Recipes.groovy"],
                select_all=False,
                host="127.0.0.1",
                port=server.port,
                server_workspace_uri="file:///fixture/groovy",
                allow_remote=False,
                connect_timeout=2,
                diagnostic_timeout=2,
            )

    def test_checked_in_profile_and_result_schema_are_valid(self) -> None:
        schema_root = ROOT / "modules/pack-program-studio/schemas"
        jsonschema.Draft202012Validator(
            json.loads(
                (schema_root / "workbench-groovyscript-language-service-profile-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        ).validate(json.loads(LANGUAGE_PROFILE.read_text(encoding="utf-8")))
        with tempfile.TemporaryDirectory() as directory:
            result = self._result(directory)
        jsonschema.Draft202012Validator(
            json.loads(
                (schema_root / "workbench-groovy-language-service-result-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
        ).validate(result)

    def test_canary_produces_explicit_clean_result_and_exact_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._result(directory)
        self.assertEqual(result, validate_language_result(result))
        self.assertEqual("ready", result["summary"]["status"])
        self.assertEqual("no-diagnostics", result["summary"]["compiler_state"])
        checked = result["service"]["files"][0]
        self.assertEqual("confirmed", checked["canary"]["state"])
        self.assertEqual([], checked["diagnostics"])
        self.assertEqual(2, result["runtime"]["mod_graph"]["artifact_count"])
        self.assertEqual("present", result["runtime"]["class_cache"]["state"])
        self.assertEqual(
            "unavailable-upstream-protocol",
            result["summary"]["runtime_binding"],
        )
        serialized = json.dumps(result["service"]["transcript"])
        self.assertNotIn("fixture compiler", serialized)
        self.assertNotIn("recipeBuilder", serialized)

    def test_compiler_diagnostic_is_source_bound_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._result(directory, broken=True)
        self.assertEqual("attention", result["summary"]["status"])
        self.assertEqual("diagnostics", result["summary"]["compiler_state"])
        diagnostic = result["service"]["files"][0]["diagnostics"][0]
        self.assertTrue(diagnostic["diagnostic_id"].startswith("workbench-groovy-diagnostic:sha256:"))
        forged = deepcopy(result)
        forged["service"]["files"][0]["diagnostics"][0]["message"] = "forged"
        forged["result_id"] = language_result_identity(forged)
        with self.assertRaisesRegex(PackProgramError, "diagnostic identity"):
            validate_language_result(forged)

        forged_runtime = deepcopy(result)
        forged_runtime["runtime"]["mod_graph"]["total_bytes"] += 1
        forged_runtime["result_id"] = language_result_identity(forged_runtime)
        with self.assertRaisesRegex(PackProgramError, "mod graph identity or counts"):
            validate_language_result(forged_runtime)

    def test_localhost_is_pinned_to_numeric_loopback_before_source_disclosure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, runtime, profile_path = self._environment(directory)
            with _server() as server:
                result = build_language_service_result(
                    source=source,
                    pack_profile=self.pack_profile,
                    language_profile=load_language_profile(profile_path),
                    context=AnalysisContext(side="client"),
                    runtime_root=runtime,
                    selected_paths=["postInit/Recipes.groovy"],
                    select_all=False,
                    host="localhost",
                    port=server.port,
                    server_workspace_uri="file:///fixture/groovy",
                    allow_remote=False,
                    connect_timeout=2,
                    diagnostic_timeout=2,
                )
        self.assertEqual("127.0.0.1", result["request"]["host"])
        self.assertEqual("127.0.0.1", result["service"]["endpoint"]["host"])

    def test_runtime_mismatch_and_remote_source_disclosure_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, runtime, profile_path = self._environment(directory)
            (runtime / "groovy/runConfig.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(PackProgramError, "runConfig bytes"):
                build_language_service_result(
                    source=source,
                    pack_profile=self.pack_profile,
                    language_profile=load_language_profile(profile_path),
                    context=AnalysisContext(side="client"),
                    runtime_root=runtime,
                    selected_paths=["postInit/Recipes.groovy"],
                    select_all=False,
                    host="127.0.0.1",
                    port=25564,
                    server_workspace_uri=None,
                    allow_remote=False,
                    connect_timeout=1,
                    diagnostic_timeout=1,
                )
        with tempfile.TemporaryDirectory() as directory:
            source, runtime, profile_path = self._environment(directory)
            with self.assertRaisesRegex(PackProgramError, "non-loopback"):
                build_language_service_result(
                    source=source,
                    pack_profile=self.pack_profile,
                    language_profile=load_language_profile(profile_path),
                    context=AnalysisContext(side="client"),
                    runtime_root=runtime,
                    selected_paths=["postInit/Recipes.groovy"],
                    select_all=False,
                    host="example.invalid",
                    port=25564,
                    server_workspace_uri=None,
                    allow_remote=False,
                    connect_timeout=1,
                    diagnostic_timeout=1,
                )

    def test_unavailable_endpoint_returns_valid_blocked_result_and_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, runtime, profile_path = self._environment(directory)
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
            probe.close()
            result = build_language_service_result(
                source=source,
                pack_profile=self.pack_profile,
                language_profile=load_language_profile(profile_path),
                context=AnalysisContext(side="client"),
                runtime_root=runtime,
                selected_paths=["postInit/Recipes.groovy"],
                select_all=False,
                host="127.0.0.1",
                port=port,
                server_workspace_uri=None,
                allow_remote=False,
                connect_timeout=0.2,
                diagnostic_timeout=0.2,
            )
            self.assertEqual("blocked", result["summary"]["status"])
            self.assertEqual(result, validate_language_result(result))
            output = StringIO()
            error = StringIO()
            code = cli_run(
                [
                    "check",
                    "--profile",
                    "supersymmetry",
                    "--language-profile",
                    str(profile_path),
                    "--source",
                    str(source),
                    "--runtime-root",
                    str(runtime),
                    "--file",
                    "postInit/Recipes.groovy",
                    "--port",
                    str(port),
                    "--connect-timeout",
                    "0.2",
                    "--diagnostic-timeout",
                    "0.2",
                    "--json",
                ],
                root=ROOT,
                output=output,
                error=error,
            )
            self.assertEqual(2, code, error.getvalue())
            self.assertEqual("blocked", json.loads(output.getvalue())["summary"]["status"])

    def test_cli_and_language_profile_platform_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, runtime, profile_path = self._environment(directory)
            with _server() as server:
                output = StringIO()
                error = StringIO()
                code = cli_run(
                    [
                        "check",
                        "--profile",
                        "supersymmetry",
                        "--language-profile",
                        str(profile_path),
                        "--source",
                        str(source),
                        "--runtime-root",
                        str(runtime),
                        "--file",
                        "postInit/Recipes.groovy",
                        "--port",
                        str(server.port),
                        "--server-workspace-uri",
                        "file:///fixture/groovy",
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                )
            self.assertEqual(0, code, error.getvalue())
            self.assertEqual("ready", json.loads(output.getvalue())["summary"]["status"])
            value = json.loads(profile_path.read_text(encoding="utf-8"))
            value["platform_profile_id"] = "wrong-platform"
            profile_path.write_text(json.dumps(value), encoding="utf-8")
            code = cli_run(
                [
                    "check",
                    "--profile",
                    "supersymmetry",
                    "--language-profile",
                    str(profile_path),
                    "--runtime-root",
                    str(runtime),
                    "--file",
                    "postInit/Recipes.groovy",
                ],
                root=ROOT,
                output=StringIO(),
                error=error,
            )
            self.assertEqual(2, code)
            self.assertIn("platform does not match", error.getvalue())


class LspProtocolTests(unittest.TestCase):
    def _connection(
        self, *, header: int = 1024, messages: int = 16
    ) -> tuple[LspConnection, socket.socket]:
        client, peer = socket.socketpair()
        connection = LspConnection(
            "fixture",
            1,
            connect_timeout=1,
            limits=LspLimits(
                max_header_bytes=header,
                max_message_bytes=4096,
                max_transcript_messages=messages,
                max_diagnostics=16,
            ),
        )
        connection.socket = client
        return connection, peer

    def test_header_bound_is_enforced_when_terminator_arrives_in_same_chunk(self) -> None:
        connection, peer = self._connection(header=32)
        try:
            peer.sendall(b"X-Filler: " + b"a" * 64 + b"\r\n\r\n{}")
            with self.assertRaisesRegex(LspProtocolError, "header exceeds"):
                connection.await_response(1, timeout=1)
        finally:
            connection.close()
            peer.close()

    def test_duplicate_json_keys_are_rejected(self) -> None:
        connection, peer = self._connection()
        try:
            peer.sendall(
                _frame(b'{"jsonrpc":"2.0","id":1,"result":null,"id":1}')
            )
            with self.assertRaisesRegex(LspProtocolError, "duplicate JSON key"):
                connection.await_response(1, timeout=1)
        finally:
            connection.close()
            peer.close()

    def test_transcript_message_flood_is_bounded(self) -> None:
        connection, peer = self._connection(messages=1)
        try:
            peer.sendall(
                _frame(b'{"jsonrpc":"2.0","id":1,"result":null}')
                + _frame(b'{"jsonrpc":"2.0","id":2,"result":null}')
            )
            response, _ = connection.await_response(1, timeout=1)
            self.assertEqual(1, response["id"])
            with self.assertRaisesRegex(LspProtocolError, "transcript messages"):
                connection.await_response(2, timeout=1)
        finally:
            connection.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
