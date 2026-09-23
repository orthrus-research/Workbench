from pathlib import Path
import tempfile
import unittest

from workbench_api.checks import observation
from workbench_core.check_execution import CheckProgress, capture_logs, read_progress
from workbench_core.check_storage import CheckStorageError
from workbench_core.check_execution import host_observation


class CheckProgressTests(unittest.TestCase):
    def test_host_binding_ignores_unadmitted_secrets_but_tracks_launch_environment(self):
        first = host_observation({"DISPLAY": ":0", "WORKBENCH_SECRET": "first"})
        self.assertEqual(first, host_observation({"DISPLAY": ":0", "WORKBENCH_SECRET": "second", "JAVA_TOOL_OPTIONS": "injection"}))
        self.assertNotEqual(first, host_observation({"DISPLAY": ":1"}))
        self.assertNotIn(":0", str(first))
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.attempt = self.root / "check-attempt"
        self.runtime = self.root / "runtime"
        self.attempt.mkdir()
        self.runtime.mkdir()
        (self.runtime / "logs").mkdir()
        (self.runtime / "logs/example.txt").write_text("owner confirmed failure\n")

    def progress(self, observe):
        return CheckProgress(attempt=self.attempt, request_id="request", runtime=self.runtime, logs=["logs/example.txt"], optional_logs=[], observe=observe, timeout=3, cancelled=lambda: False)

    def test_failure_is_not_user_cancellation_and_progress_is_request_bound(self):
        expected = observation("failure", "owner-failure", "Failure observed", [{"log": "logs/example.txt", "line": 1}])
        renderer = self.progress(lambda _: expected)
        renderer.pulse()
        self.assertEqual(renderer.stop_reason, "failure-observed")
        self.assertTrue(renderer.cancellation_requested)
        status = read_progress(self.attempt, "request")
        self.assertEqual(status["progress"]["observation"], expected)
        with self.assertRaises(CheckStorageError):
            read_progress(self.attempt, "other-request")

    def test_observer_cannot_claim_uncaptured_evidence(self):
        renderer = self.progress(lambda _: observation("checkpoint", "done", "Done", [{"log": "logs/example.txt", "line": 2}]))
        with self.assertRaisesRegex(CheckStorageError, "unavailable evidence"):
            renderer.pulse()

    def test_unchanged_progress_does_not_append_repeated_events(self):
        renderer = self.progress(lambda _: observation("running", "waiting", "Waiting"))
        renderer.sample()
        renderer.sample()
        self.assertEqual(len(renderer.observations), 2)

    def test_optional_reports_are_bounded_and_do_not_follow_symlinks(self):
        self.assertEqual(capture_logs(self.runtime, [], optional_globs=["reports/*.txt"]), {})
        (self.runtime / "reports").mkdir()
        for index in range(33):
            (self.runtime / "reports" / f"report-{index}.txt").write_text("report")
        self.assertEqual(capture_logs(self.runtime, [], optional_globs=["reports/*.txt"])["reports/*.txt"]["state"], "over-bound")
        (self.runtime / "linked").symlink_to(self.runtime / "reports", target_is_directory=True)
        with self.assertRaises(CheckStorageError):
            capture_logs(self.runtime, [], optional_globs=["linked/*.txt"])
        with self.assertRaises(CheckStorageError):
            capture_logs(self.runtime, [], optional_globs=["../reports/*.txt"])
