from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/pack-program-studio/src"
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
if str(PROJECT_INTELLIGENCE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_pack_program_studio.ide_bridge import (  # noqa: E402
    load_live_session_descriptor,
    proxy_descriptor_stdio,
)
from workbench_pack_program_studio.managed_model import descriptor_identity  # noqa: E402
from workbench_pack_program_studio.model import PackProgramError  # noqa: E402


class IdeBridgeTests(unittest.TestCase):
    def test_proxy_preserves_arbitrary_lsp_bytes(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        request = b'Content-Length: 41\r\n\r\n{"jsonrpc":"2.0","method":"initialized"}'
        response = b'Content-Length: 38\r\n\r\n{"jsonrpc":"2.0","id":1,"result":null}'
        received: list[bytes] = []

        def serve() -> None:
            connection, _ = listener.accept()
            with connection:
                chunks: list[bytes] = []
                while chunk := connection.recv(65536):
                    chunks.append(chunk)
                received.append(b"".join(chunks))
                connection.sendall(response)
            listener.close()

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        with tempfile.TemporaryDirectory() as directory:
            descriptor_path = Path(directory) / "session-descriptor-v1.json"
            self._write_descriptor(descriptor_path, port)
            output = BytesIO()
            proxy_descriptor_stdio(
                descriptor_path,
                input_stream=BytesIO(request),
                output_stream=output,
            )
        server.join(timeout=2)
        self.assertEqual([request], received)
        self.assertEqual(response, output.getvalue())

    def test_loader_rejects_stale_identity_and_relocated_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descriptor_path = root / "session-descriptor-v1.json"
            descriptor = self._write_descriptor(descriptor_path, 25564)
            descriptor["endpoint"]["port"] = 25565
            descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
            with self.assertRaisesRegex(PackProgramError, "identity is stale"):
                load_live_session_descriptor(descriptor_path)

            original = root / "original.json"
            descriptor = self._write_descriptor(original, 25564)
            relocated = root / "relocated.json"
            relocated.write_text(json.dumps(descriptor), encoding="utf-8")
            with self.assertRaisesRegex(PackProgramError, "artifact binding"):
                load_live_session_descriptor(relocated)

    @staticmethod
    def _write_descriptor(path: Path, port: int) -> dict[str, object]:
        digest = "0" * 64
        descriptor: dict[str, object] = {
            "format": "workbench-groovy-language-session-descriptor-v1",
            "schema_version": 1,
            "descriptor_id": "pending",
            "session_id": "workbench-groovy-language-session:11111111-2222-3333-4444-555555555555",
            "state": "ready",
            "emitted_at": "2026-08-05T00:00:00Z",
            "profile": {
                "pack_program_profile_id": "pack",
                "pack_program_profile_sha256": digest,
                "language_service_profile_id": "language",
                "language_service_profile_sha256": digest,
                "managed_session_profile_id": "managed",
                "managed_session_profile_sha256": digest,
                "platform_profile_id": "platform",
            },
            "program": {
                "program_id": "program",
                "source_sha256": digest,
                "source_root": str(path.parent),
                "workspace_uri": path.parent.as_uri(),
                "server_workspace_uri": "file:///C:/fixture",
            },
            "runtime": {
                "runtime_id": "runtime",
                "root": str(path.parent / "runtime"),
                "mod_graph_id": "mod-graph",
                "java_sha256": digest,
                "launch_receipt_path": str(path.parent / "launch.json"),
                "launch_receipt_sha256": digest,
                "launch_receipt_size": 1,
            },
            "endpoint": {
                "host": "127.0.0.1",
                "port": port,
                "transport": "lsp-jsonrpc-tcp",
                "allocation": "explicit-free-loopback",
                "connection_model": "one-active-client-sequential-reaccept",
                "identity_binding": "managed-launch-custody; no-upstream-identity-challenge",
                "route": "direct-loopback",
                "upstream": {"host": "127.0.0.1", "port": port},
            },
            "readiness": {
                "state": "confirmed",
                "port": port,
                "attempts": 1,
                "latency_ms": 1,
                "canary_state": "confirmed",
                "capabilities": {"hoverProvider": True},
                "transcript_sha256": digest,
                "failure": None,
            },
            "clients": {
                "consumers": ["terminal", "intellij", "vscode"],
                "connection_ownership": "client-connects-directly",
                "reconnect_policy": "retry-bounded-after-readiness-handoff",
                "shutdown_owner": "workbench-session-process",
            },
            "artifacts": {
                "descriptor_path": str(path.resolve()),
                "pending_receipt_path": str(path.parent / "pending.json"),
                "events_path": str(path.parent / "events.jsonl"),
            },
            "limitations": ["fixture"],
        }
        descriptor["descriptor_id"] = descriptor_identity(descriptor)
        path.write_text(json.dumps(descriptor), encoding="utf-8")
        return descriptor


if __name__ == "__main__":
    unittest.main()
