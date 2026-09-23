"""Focused probe tests for the flat capability-based Host Adapter V3."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/project-intelligence/src"))
sys.path.insert(0, str(MODULE_ROOT / "src"))

import workbench_core.host_adapter as host_adapter  # noqa: E402
from workbench_core.host_adapter import (  # noqa: E402
    CAPABILITY_IDS,
    CORE_HOST_CAPABILITY_IDS,
    HostAdapterV3Error,
    compute_host_adapter_v3_receipt_id,
    inspect_local_host_adapter_v3,
    validate_host_adapter_v3_receipt,
)
from workbench_shell.cli import main as cli_main  # noqa: E402


class HostAdapterV3ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.receipt = inspect_local_host_adapter_v3(scratch_parent=self.parent)

    def test_local_probe_is_complete_disposable_and_identity_bound(self) -> None:
        self.assertEqual(
            [row["id"] for row in self.receipt["capabilities"]],
            list(CAPABILITY_IDS),
        )
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assertEqual(
            self.receipt["receipt_id"],
            compute_host_adapter_v3_receipt_id(self.receipt),
        )
        reopened = validate_host_adapter_v3_receipt(self.receipt)
        self.assertEqual(reopened, self.receipt)
        self.assertIsNot(reopened, self.receipt)

    def test_eligibility_comes_from_capabilities_not_system_label(self) -> None:
        with patch.object(
            host_adapter.platform,
            "system",
            return_value="An Unfamiliar Developer OS",
        ):
            probed = inspect_local_host_adapter_v3(scratch_parent=self.parent)
        self.assertEqual(
            probed["adapter"]["system_label"], "An Unfamiliar Developer OS"
        )
        self.assertEqual(
            probed["core_host_eligible"], self.receipt["core_host_eligible"]
        )
        self.assertEqual(
            probed["missing_core_capabilities"],
            self.receipt["missing_core_capabilities"],
        )

        changed = deepcopy(self.receipt)
        changed["adapter"]["system_label"] = "An Unfamiliar Developer OS"
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        validated = validate_host_adapter_v3_receipt(changed)
        self.assertEqual(
            validated["core_host_eligible"], self.receipt["core_host_eligible"]
        )
        self.assertFalse(validated["release_qualified"])
        self.assertNotIn(
            "system_label", " ".join(validated["missing_core_capabilities"])
        )

    def test_windows_file_uri_codec_round_trips_without_windows_host(self) -> None:
        cases = (
            (
                PureWindowsPath(r"C:\Users\Dev User\資料\feature #1%.groovy"),
                "file:///C:/Users/Dev%20User/%E8%B3%87%E6%96%99/feature%20%231%25.groovy",
            ),
            (
                PureWindowsPath(r"\\build-host\Workbench Share\Dev User\feature.json"),
                "file://build-host/Workbench%20Share/Dev%20User/feature.json",
            ),
        )
        for path, expected_uri in cases:
            with self.subTest(path=str(path)):
                uri = host_adapter._path_to_file_uri(path, flavour="windows")
                self.assertEqual(uri, expected_uri)
                decoded = host_adapter._file_uri_to_path(
                    uri, flavour="windows"
                )
                self.assertIsInstance(decoded, PureWindowsPath)
                self.assertEqual(decoded, path)

    def test_file_uri_codec_rejects_aliases_controls_and_windows_devices(self) -> None:
        encoded_cases = (
            ("posix", "/workbench/safe/../escape"),
            ("posix", "/workbench/safe/bad\x00name"),
            ("windows", r"C:\workbench\safe\..\escape"),
            ("windows", r"C:\workbench\CON.txt"),
            ("windows", "C:\\workbench\\control\x1f.txt"),
            ("windows", r"C:\workbench\trailing."),
            ("windows", r"C:\workbench\alternate:stream"),
            ("windows", r"C:\workbench\question?.txt"),
        )
        for flavour, path in encoded_cases:
            with self.subTest(direction="encode", flavour=flavour, path=path):
                with self.assertRaises(ValueError):
                    host_adapter._path_to_file_uri(path, flavour=flavour)

        decoded_cases = (
            ("posix", "file:///workbench/safe/../escape"),
            ("posix", "file:///workbench/safe/%2e%2e/escape"),
            ("posix", "file:///workbench/safe/bad%00name"),
            ("windows", "file:///C:/workbench/safe/../escape"),
            ("windows", "file:///C:/workbench/safe/%2e%2e/escape"),
            ("windows", "file:///C:/workbench/safe%5C..%5Cescape"),
            ("windows", "file:///C:/workbench/CON.txt"),
            ("windows", "file:///C:/workbench/control%1f.txt"),
            ("windows", "file:///C:/workbench/trailing."),
            ("windows", "file:///C:/workbench/alternate%3Astream"),
            ("windows", "file://server/share/../escape"),
        )
        for flavour, uri in decoded_cases:
            with self.subTest(direction="decode", flavour=flavour, uri=uri):
                with self.assertRaises(ValueError):
                    host_adapter._file_uri_to_path(uri, flavour=flavour)

    def test_local_path_uri_probe_creates_and_reopens_exact_bytes(self) -> None:
        result = host_adapter._probe_path_uri(self.parent)
        self.assertEqual(result.state, "available")
        self.assertEqual(result.limitation, None)
        self.assertIn("reopened", result.evidence[0])
        self.assertTrue((self.parent / "path target-資料.txt").is_file())

    def test_core_eligibility_exactly_tracks_required_capabilities(self) -> None:
        by_id = {row["id"]: row for row in self.receipt["capabilities"]}
        expected = sorted(
            capability_id
            for capability_id in CORE_HOST_CAPABILITY_IDS
            if by_id[capability_id]["state"] != "available"
        )
        self.assertEqual(self.receipt["missing_core_capabilities"], expected)
        self.assertEqual(self.receipt["core_host_eligible"], not expected)

        changed = deepcopy(self.receipt)
        target = next(
            row
            for row in changed["capabilities"]
            if row["id"] == "exact-argv-spawn"
        )
        target["state"] = "unavailable"
        target["evidence"] = []
        target["limitation"] = "synthetic adapter process boundary"
        changed["operation_states"] = host_adapter._operation_rows(
            changed["capabilities"]
        )
        changed["missing_core_capabilities"] = ["exact-argv-spawn"]
        changed["core_host_eligible"] = False
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        validate_host_adapter_v3_receipt(changed)

    def test_unproved_platform_services_remain_explicit(self) -> None:
        by_id = {row["id"]: row for row in self.receipt["capabilities"]}
        for capability_id in (
            "process-tree-termination",
            "credential-storage",
            "desktop-notifications",
            "runtime-delegation",
        ):
            self.assertEqual(by_id[capability_id]["state"], "unverified")
            self.assertTrue(by_id[capability_id]["limitation"])
            self.assertTrue(by_id[capability_id]["next_safe_action"])

    def test_missing_socketpair_is_unverified_without_hiding_later_probes(self) -> None:
        with patch.object(
            host_adapter.socket,
            "socketpair",
            side_effect=NotImplementedError("synthetic missing socketpair"),
            create=True,
        ):
            receipt = inspect_local_host_adapter_v3(scratch_parent=self.parent)

        by_id = {row["id"]: row for row in receipt["capabilities"]}
        self.assertEqual(by_id["local-ipc"]["state"], "unverified")
        self.assertEqual(by_id["local-ipc"]["evidence"], [])
        self.assertIn("NotImplementedError", by_id["local-ipc"]["limitation"])
        self.assertEqual(by_id["monotonic-clock"]["state"], "available")
        self.assertEqual(by_id["runtime-delegation"]["state"], "unverified")
        self.assertEqual(len(receipt["capabilities"]), len(CAPABILITY_IDS))
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_popen_failure_is_unavailable_without_hiding_other_probes(self) -> None:
        with patch.object(
            host_adapter.subprocess,
            "Popen",
            side_effect=OSError("synthetic spawn failure"),
        ):
            receipt = inspect_local_host_adapter_v3(scratch_parent=self.parent)

        by_id = {row["id"]: row for row in receipt["capabilities"]}
        self.assertEqual(by_id["exact-argv-spawn"]["state"], "unavailable")
        self.assertEqual(
            by_id["direct-process-termination"]["state"], "unavailable"
        )
        self.assertEqual(by_id["monotonic-clock"]["state"], "available")
        self.assertEqual(by_id["local-ipc"]["state"], "available")
        self.assertEqual(len(receipt["capabilities"]), len(CAPABILITY_IDS))
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_malformed_internal_probe_results_fail_closed(self) -> None:
        malformed = (
            host_adapter._ProbeResult("available", (), None),
            host_adapter._ProbeResult("available", ("evidence",), "conflict"),
            host_adapter._ProbeResult("unavailable", ("evidence",), None),
            host_adapter._ProbeResult("unverified", (), None),
            host_adapter._ProbeResult("available", ("\ud800",), None),
            host_adapter._ProbeResult("unavailable", (), "\ud800"),
            host_adapter._ProbeResult(["available"], ("evidence",), None),
            host_adapter._ProbeResult("available", ["evidence"], None),
            object(),
        )
        for candidate in malformed:
            with self.subTest(candidate=type(candidate).__name__):
                result = host_adapter._run_probe(
                    "path-uri-round-trip", lambda _: candidate, self.parent
                )
                self.assertEqual(result.state, "unavailable")
                self.assertEqual(result.evidence, ())
                self.assertIn("malformed internal result", result.limitation)

        with patch.dict(
            host_adapter._PROBES,
            {
                "path-uri-round-trip": lambda _: host_adapter._ProbeResult(
                    "available", (), None
                )
            },
        ):
            receipt = inspect_local_host_adapter_v3(scratch_parent=self.parent)
        by_id = {row["id"]: row for row in receipt["capabilities"]}
        self.assertEqual(by_id["path-uri-round-trip"]["state"], "unavailable")
        self.assertEqual(by_id["unicode-path-round-trip"]["state"], "available")
        self.assertEqual(by_id["local-ipc"]["state"], "available")

    def test_platform_diagnostics_fail_independently_to_safe_labels(self) -> None:
        with (
            patch.object(
                host_adapter.platform,
                "python_implementation",
                side_effect=RuntimeError("synthetic diagnostic failure"),
            ),
            patch.object(
                host_adapter.platform,
                "python_version",
                return_value="3.test",
            ),
            patch.object(
                host_adapter.platform,
                "system",
                side_effect=NotImplementedError("synthetic missing API"),
            ),
            patch.object(host_adapter.platform, "release", return_value="bad\n"),
            patch.object(
                host_adapter.platform,
                "machine",
                return_value="mystery-machine",
            ),
        ):
            receipt = inspect_local_host_adapter_v3(scratch_parent=self.parent)
        adapter = receipt["adapter"]
        self.assertEqual(adapter["python_implementation"], "unreported")
        self.assertEqual(adapter["python_version"], "3.test")
        self.assertEqual(adapter["system_label"], "unreported")
        self.assertEqual(adapter["system_release"], "unreported")
        self.assertEqual(adapter["machine_label"], "mystery-machine")
        self.assertEqual(len(receipt["capabilities"]), len(CAPABILITY_IDS))

    def test_tamper_and_contradictory_claims_fail_closed(self) -> None:
        changed = deepcopy(self.receipt)
        changed["adapter"]["machine_label"] = "forged"
        with self.assertRaisesRegex(HostAdapterV3Error, "content identity mismatch"):
            validate_host_adapter_v3_receipt(changed)

        changed = deepcopy(self.receipt)
        changed["release_qualified"] = True
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "cannot qualify release"):
            validate_host_adapter_v3_receipt(changed)

        changed = deepcopy(self.receipt)
        changed["capabilities"][0]["state"] = "available"
        changed["capabilities"][0]["evidence"] = []
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "available requires evidence"):
            validate_host_adapter_v3_receipt(changed)

    def test_hostile_receipt_scalars_have_stable_validation_errors(self) -> None:
        for schema_version in (True, 1.0):
            changed = deepcopy(self.receipt)
            changed["schema_version"] = schema_version
            with self.subTest(schema_version=repr(schema_version)):
                with self.assertRaisesRegex(
                    HostAdapterV3Error, "unsupported Host Adapter V3 receipt"
                ):
                    validate_host_adapter_v3_receipt(changed)

        for state in (["available"], {"available": True}, 1, True):
            changed = deepcopy(self.receipt)
            changed["capabilities"][0]["state"] = state
            with self.subTest(state=repr(state)):
                with self.assertRaisesRegex(
                    HostAdapterV3Error, "unknown capability state"
                ):
                    validate_host_adapter_v3_receipt(changed)

        for mutate in (
            lambda value: value["adapter"].__setitem__("machine_label", "\ud800"),
            lambda value: value["capabilities"][0]["evidence"].__setitem__(
                0, "\ud800"
            ),
            lambda value: value.__setitem__("next_safe_action", "\ud800"),
        ):
            changed = deepcopy(self.receipt)
            mutate(changed)
            with self.assertRaisesRegex(
                HostAdapterV3Error, "must contain valid UTF-8 Unicode"
            ):
                validate_host_adapter_v3_receipt(changed)

    def test_invalid_scratch_parent_has_actionable_boundary(self) -> None:
        with self.assertRaisesRegex(HostAdapterV3Error, "existing directory"):
            inspect_local_host_adapter_v3(scratch_parent=self.parent / "missing")

    def test_host_status_cli_is_human_readable_and_machine_complete(self) -> None:
        human = io.StringIO()
        with redirect_stdout(human):
            status = cli_main(
                [
                    "host-status",
                    "--suite-root",
                    str(REPOSITORY_ROOT),
                    "--scratch-parent",
                    str(self.parent),
                ]
            )
        self.assertEqual(status, 0)
        self.assertIn("Workbench core host:", human.getvalue())
        self.assertIn("Release qualified: NO", human.getvalue())
        self.assertIn("Next safe action:", human.getvalue())

        completed = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "tools/workbench.py"),
                "host-status",
                "--scratch-parent",
                str(self.parent),
                "--json",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["format"], "workbench-host-adapter-conformance-v3")
        self.assertNotIn("predecessor_receipt", result)
        self.assertFalse(result["release_qualified"])
        self.assertEqual(list(self.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
