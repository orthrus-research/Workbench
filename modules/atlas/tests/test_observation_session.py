"""One verified graph serves linked requests without changing evidence meaning."""

from __future__ import annotations

from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for source in (ROOT / "api/src", ROOT / "core/src", ROOT / "modules/atlas/src"):
    sys.path.insert(0, str(source))

from workbench_api import ExecutionContext, ModuleError
from workbench_atlas_observations import open_observations
from workbench_atlas_observations.cli import main
from workbench_atlas_observations.session import MAX_REQUEST_BYTES, PREFIX, SessionFramingError, serve_observation_session
from test_observation_queries import graph


class ObservationSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "graph"
        self.manifest, self.nodes, self.edges = graph(self.path)
        self.graph_id = self.manifest["graph_set_id"]
        self.opened = []

    def request(self, operation="context", arguments=None, **changes):
        return {"format": PREFIX + "request-v1", "schema_version": 1,
                "request_id": "request-1", "graph_set_id": self.graph_id,
                "operation": operation, "arguments": arguments or {}, **changes}

    def stream(self, *requests):
        return BytesIO(b"".join(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n" for value in requests))

    def observe_open(self, *args, **kwargs):
        view = open_observations(*args, **kwargs)
        self.opened.append(view)
        return view

    def assert_closed(self):
        self.assertEqual(1, len(self.opened))
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            self.opened[0].query.connection.execute("SELECT 1")

    def run_session(self, source, *, output=None, cancel=None):
        target = output if output is not None else StringIO()
        with patch("workbench_atlas_observations.session.open_observations", side_effect=self.observe_open) as opened:
            result = serve_observation_session(self.path, input=source, output=target, check_cancelled=cancel)
        self.assertEqual(1, opened.call_count)
        self.assert_closed()
        return result, [json.loads(line) for line in target.getvalue().splitlines()]

    def test_linked_queries_equal_owner_api_with_one_verified_view(self):
        selected = self.nodes[0]["id"]
        with open_observations(self.path) as view:
            first = view.search("CAFÉ", limit=2)
            expected = [view.describe(), first, view.search("CAFÉ", limit=2, cursor=first["page"]["next_cursor"]),
                        view.inspect(selected), view.relationships(selected, direction="outgoing", limit=2),
                        view.evidence(selected, limit=2)]
        requests = [self.request(), self.request("search", {"query": "CAFÉ", "limit": 2}),
                    self.request("search", {"query": "CAFÉ", "limit": 2, "cursor": first["page"]["next_cursor"]}),
                    self.request("inspect", {"selection_id": selected}),
                    self.request("relationships", {"selection_id": selected, "direction": "outgoing", "limit": 2}),
                    self.request("evidence", {"selection_id": selected, "limit": 2}), self.request("close")]
        result, records = self.run_session(self.stream(*requests))
        self.assertEqual(0, result)
        self.assertEqual("ready", records[0]["state"])
        self.assertEqual(expected[0], records[0]["context"])
        self.assertEqual(expected, [row["result"] for row in records[1:-1]])
        self.assertEqual("closed", records[-1]["state"])
        self.assertTrue(all(row["graph_set_id"] == self.graph_id for row in records))
        self.assertEqual(9007199254740993, records[4]["result"]["selection"]["properties"]["stored"]["large"])

    def test_graph_binding_bad_arguments_and_missing_selection_recover(self):
        requests = [self.request(graph_set_id="other"), self.request(operation="index"),
                    self.request("inspect", {"selection_id": "absent"}),
                    self.request("search", {"query": "Observation", "limit": True}),
                    self.request("context", {"unexpected": 1}), self.request(schema_version=True),
                    self.request("evidence", {"selection_id": self.nodes[0]["id"], "snapshot": "/source"}),
                    self.request("search", {"query": "Observation"})]
        _, records = self.run_session(self.stream(*requests))
        self.assertTrue(all(row["state"] == "error" for row in records[1:-1]))
        self.assertEqual("complete", records[-1]["state"])
        self.assertEqual(5, records[-1]["result"]["page"]["returned"])

    def test_empty_eof_and_explicit_close_release_view_without_reading_more_requests(self):
        _, records = self.run_session(BytesIO())
        self.assertEqual(["ready"], [row["state"] for row in records])
        self.opened.clear()
        source = BytesIO(self.stream(self.request("close")).read() + b"not-json\n")
        _, records = self.run_session(source)
        self.assertEqual(["ready", "closed"], [row["state"] for row in records])

    def test_invalid_frames_terminate_and_close_without_answering_later_requests(self):
        for invalid in (b"{broken}\n", b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":1e9999}\n',
                        b'{"x":"\\ud800"}\n', b"\xff\n", b"x" * (MAX_REQUEST_BYTES + 1) + b"\n", b"\n"):
            with self.subTest(invalid=invalid[:40]):
                self.opened.clear()
                output = StringIO()
                with self.assertRaises(SessionFramingError):
                    self.run_session(BytesIO(invalid + self.stream(self.request()).read()), output=output)
                self.assert_closed()
                self.assertEqual(["ready"], [json.loads(line)["state"] for line in output.getvalue().splitlines()])
        self.opened.clear()
        with self.assertRaisesRegex(SessionFramingError, "unterminated"):
            self.run_session(BytesIO(self.stream(self.request()).read().rstrip(b"\n")))
        self.assert_closed()

    def test_idle_and_partial_pipe_reads_observe_core_cancellation_and_restore_fd(self):
        for partial in (b"", b'{"format":'):
            with self.subTest(partial=partial):
                self.opened.clear()
                context = ExecutionContext(self.root, self.root)
                read_fd, write_fd = os.pipe()
                with os.fdopen(read_fd, "rb", buffering=0) as source:
                    if partial:
                        os.write(write_fd, partial)
                    timer = threading.Timer(0.2, context.cancelled.set)
                    class ReadyOutput(StringIO):
                        def flush(self):
                            super().flush()
                            if not timer.is_alive():
                                timer.start()
                    target = ReadyOutput()
                    started = time.monotonic()
                    try:
                        with self.assertRaisesRegex(ModuleError, "cancelled"):
                            self.run_session(source, output=target, cancel=context.check_cancelled)
                        self.assertTrue(os.get_blocking(read_fd))
                        self.assertLess(time.monotonic() - started, 2)
                    finally:
                        os.close(write_fd)
                        timer.cancel()
                        if timer.ident is not None:
                            timer.join()
                    self.assert_closed()
                    self.assertEqual(1, len(target.getvalue().splitlines()))

    def test_short_output_writes_are_completed_and_zero_write_terminates(self):
        class ShortOutput(StringIO):
            def write(self, value):
                return super().write(value[:7])
        _, records = self.run_session(self.stream(self.request(), self.request("close")), output=ShortOutput())
        self.assertEqual(["ready", "complete", "closed"], [row["state"] for row in records])
        self.opened.clear()
        class StalledOutput(StringIO):
            def write(self, value):
                return 0
        with self.assertRaisesRegex(SessionFramingError, "output stopped"):
            self.run_session(self.stream(self.request()), output=StalledOutput())
        self.assert_closed()

    def test_full_output_pipe_remains_cancellable_and_restores_fd(self):
        context = ExecutionContext(self.root, self.root)
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        try:
            while True:
                os.write(write_fd, b"x" * 65536)
        except BlockingIOError:
            pass
        os.set_blocking(write_fd, True)
        timer = threading.Timer(0.2, context.cancelled.set)
        with os.fdopen(write_fd, "w", encoding="utf-8") as target:
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(ModuleError, "cancelled"):
                    self.run_session(self.stream(self.request()), output=target, cancel=context.check_cancelled)
                self.assertTrue(os.get_blocking(write_fd))
                self.assertLess(time.monotonic() - started, 2)
            finally:
                timer.cancel()
                timer.join()
                os.close(read_fd)
        self.assert_closed()

    def test_cancellation_inside_query_does_not_become_a_recoverable_error(self):
        cancelled = threading.Event()
        def cancel():
            if cancelled.is_set():
                raise ModuleError("operation cancelled")
        def opened(*args, **kwargs):
            view = self.observe_open(*args, **kwargs)
            def casefold(value):
                cancelled.set()
                return value.casefold()
            view.query.connection.create_function("atlas_casefold", 1, casefold)
            return view
        output = StringIO()
        with patch("workbench_atlas_observations.session.open_observations", side_effect=opened):
            with self.assertRaisesRegex(ModuleError, "cancelled"):
                serve_observation_session(self.path, input=self.stream(self.request("search", {"query": "Observation"})),
                                          output=output, check_cancelled=cancel)
        self.assert_closed()
        self.assertEqual(["ready"], [json.loads(line)["state"] for line in output.getvalue().splitlines()])

    def test_evidence_resolution_preserves_owner_records_and_rejects_misbound_results(self):
        references = self.nodes[0]["evidence"]
        adapter = SimpleNamespace(OBSERVATION_GRAPH_API_VERSION=1,
            resolve_evidence=lambda path, selected, **kwargs: {"state": "resolved", "snapshot_id": "one",
                "records": [{"reference": reference, "value": {"source": "original"}} for reference in selected]})
        request = self.request("evidence", {"selection_id": self.nodes[0]["id"], "limit": 2,
                                            "snapshot": str(self.root / "snapshot"), "pack_profile": "fixture"})
        with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter):
            _, records = self.run_session(self.stream(request))
        report = records[-1]["result"]
        self.assertEqual("resolved", report["original_record_resolution"])
        self.assertEqual(references[:2], [row["reference"] for row in report["resolution"]["records"]])
        self.opened.clear()
        adapter.resolve_evidence = lambda *args, **kwargs: {"state": "resolved", "snapshot_id": "wrong", "records": []}
        with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter):
            _, records = self.run_session(self.stream(request, self.request()))
        self.assertEqual("error", records[1]["state"])
        self.assertIn("misbound", records[1]["error"]["message"])
        self.assertEqual("complete", records[-1]["state"])

    def test_graph_drift_after_ready_ends_session_before_successful_query(self):
        manifest = self.path / "manifest.json"
        class ChangedOutput(StringIO):
            def flush(self):
                super().flush()
                manifest.write_bytes(manifest.read_bytes() + b" ")
        output = ChangedOutput()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_session(self.stream(self.request()), output=output)
        self.assert_closed()
        self.assertEqual(["ready"], [json.loads(line)["state"] for line in output.getvalue().splitlines()])

    def test_drift_during_result_serialization_is_checked_before_publication(self):
        original = json.dumps
        def serialize(value, **kwargs):
            encoded = original(value, **kwargs)
            if type(value) is dict and value.get("state") == "complete":
                manifest = self.path / "manifest.json"
                manifest.write_bytes(manifest.read_bytes() + b" ")
            return encoded
        source = self.stream(self.request())
        output = StringIO()
        with patch("workbench_atlas_observations.session.json.dumps", side_effect=serialize):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.run_session(source, output=output)
        self.assert_closed()
        self.assertEqual(["ready"], [json.loads(line)["state"] for line in output.getvalue().splitlines()])

    def test_crafting_exposure_cli_and_session_retain_exact_paths_and_explicit_frontiers(self):
        from test_crafting_reference_exposure import graph as crafting_graph
        from workbench_atlas_observations.exposure import derive_crafting_reference_exposure
        self.path = self.root / "crafting"
        nodes, _ = crafting_graph(self.path, {"catalog": (["ore-value", "wrapper"], ["fixture:machine"],
            [("wrapper", "ore-value"), ("fixture:machine", "wrapper")])}, incomplete=True)
        selected = nodes[("catalog", "ore-value")]["id"]
        with open_observations(self.path) as view:
            self.graph_id = view.manifest["graph_set_id"]
            complete = derive_crafting_reference_exposure(view, selected)
            bounded = derive_crafting_reference_exposure(view, selected, max_depth=1)
        _, records = self.run_session(self.stream(
            self.request("crafting-exposure", {"selection_id": selected}),
            self.request("crafting-exposure", {"selection_id": selected, "max_depth": 1})))
        self.assertEqual([complete, bounded], [record["result"] for record in records[1:]])
        output, error = StringIO(), StringIO()
        self.assertEqual(0, main(["crafting-exposure", str(self.path), selected, "--json"], output=output, error=error))
        self.assertEqual(complete, json.loads(output.getvalue()))
        human = StringIO()
        self.assertEqual(0, main(["crafting-exposure", str(self.path), selected], output=human, error=error))
        self.assertIn("fixture:machine", human.getvalue())
        self.assertIn("Traversal: complete", human.getvalue())
        self.assertIn("Stored-reference evidence: incomplete", human.getvalue())
        self.assertIn("Witness:", human.getvalue())
        self.assertIn("does not establish ingredient acceptance", human.getvalue())
        bounded_human = StringIO()
        self.assertEqual(0, main(["crafting-exposure", str(self.path), selected, "--max-depth", "1"],
                                 output=bounded_human, error=error))
        self.assertIn("Traversal: truncated", bounded_human.getvalue())
        self.assertIn("Frontier: max-depth", bounded_human.getvalue())

    def test_cli_session_uses_jsonl_and_reports_cold_verification_on_stderr(self):
        output, error = StringIO(), StringIO()
        with patch("sys.stdin", SimpleNamespace(buffer=self.stream(self.request("close")))):
            result = main(["session", str(self.path)], output=output, error=error)
        self.assertEqual(0, result)
        self.assertEqual("Verifying observation graph...\n", error.getvalue())
        self.assertEqual(["ready", "closed"], [json.loads(line)["state"] for line in output.getvalue().splitlines()])


if __name__ == "__main__":
    unittest.main()
