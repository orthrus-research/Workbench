"""Byte framing and subprocess tests for the Workbench stdio host."""

from __future__ import annotations

from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_SOURCE = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from supersymmetry_project_fixture import (  # noqa: E402
    create_supersymmetry_project,
)
from workbench_core.service.framing import read_message, write_message
from workbench_shell.stdio_host import serve


def _initialize_request(workspace: Path) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocol_version": {"major": 2, "minor": 0},
            "client": {
                "id": "workbench-client:subprocess-test",
                "kind": "test",
                "version": "0.0.0",
            },
            "workspace_roots": [workspace.as_uri()],
            "execution_host": {"kind": "test"},
            "required_methods": ["workspace/inspect", "shutdown"],
        },
    }


class StdioHostTest(unittest.TestCase):
    def test_unicode_frame_uses_utf8_byte_length(self) -> None:
        stream = BytesIO()
        payload = {
            "jsonrpc": "2.0",
            "id": "unicode",
            "result": {"name": "Cleanroom → Workbench"},
        }
        write_message(stream, payload)
        raw = stream.getvalue()
        header, body = raw.split(b"\r\n\r\n", 1)
        self.assertEqual(
            int(header.removeprefix(b"Content-Length: ")),
            len(body),
        )
        stream.seek(0)
        self.assertEqual(read_message(stream), payload)

    def test_large_frame_survives_partial_stream_reads_and_writes(self) -> None:
        class PartialStream(BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 97) if size >= 0 else 97)

            def write(self, value):
                return super().write(bytes(value[:113]))

        payload = {"large": "Cleanroom graph " * 30000}
        stream = PartialStream()
        write_message(stream, payload)
        stream.seek(0)
        self.assertEqual(read_message(stream), payload)

    def test_invalid_json_is_recoverable_but_bad_framing_is_fatal(self) -> None:
        invalid_json = b"{not json"
        source = BytesIO(
            f"Content-Length: {len(invalid_json)}\r\n\r\n".encode("ascii")
            + invalid_json
        )
        output = BytesIO()
        errors = StringIO()
        self.assertEqual(serve(source, output, errors), 0)
        output.seek(0)
        response = read_message(output)
        self.assertEqual(response["error"]["code"], -32700)
        self.assertIn("not valid JSON", errors.getvalue())

        output = BytesIO()
        errors = StringIO()
        self.assertEqual(
            serve(
                BytesIO(b"Content-Length: invalid\r\n\r\n"),
                output,
                errors,
            ),
            2,
        )
        self.assertEqual(output.getvalue(), b"")
        self.assertIn("fatal framing error", errors.getvalue())

    def test_module_entrypoint_runs_complete_stdio_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_supersymmetry_project(Path(temporary))
            requests = BytesIO()
            write_message(requests, _initialize_request(project))
            write_message(requests, {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "workspace/inspect",
                "params": {},
            })
            write_message(requests, {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "shutdown",
                "params": {},
            })
            environment = os.environ.copy()
            source_roots = [
                str(MODULE_ROOT / "src"),
                str(PROJECT_INTELLIGENCE_SOURCE),
            ]
            if environment.get("PYTHONPATH"):
                source_roots.append(environment["PYTHONPATH"])
            environment["PYTHONPATH"] = os.pathsep.join(source_roots)

            completed = subprocess.run(
                [sys.executable, "-m", "workbench_shell"],
                input=requests.getvalue(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
                env=environment,
                cwd=project,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr.decode("utf-8", errors="replace"),
            )
            output = BytesIO(completed.stdout)
            responses = []
            while True:
                response = read_message(output)
                if response is None:
                    break
                responses.append(response)
            self.assertEqual(
                [response["id"] for response in responses],
                [1, 2, 3],
            )
            self.assertEqual(
                responses[1]["result"]["workspace_context"]["project"][
                    "name"
                ],
                "Supersymmetry",
            )
            self.assertEqual(
                responses[1]["result"]["workspace_context"]["pack"][
                    "selected_profile"
                ],
                "cleanroom-provisional",
            )
            self.assertIsNone(responses[2]["result"])
            stderr = completed.stderr.decode("utf-8")
            self.assertIn("stdio host started", stderr)
            self.assertIn("shutdown requested", stderr)


if __name__ == "__main__":
    unittest.main()
