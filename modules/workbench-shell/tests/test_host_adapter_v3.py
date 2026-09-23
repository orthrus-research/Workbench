"""Focused executable-custody and hostile tests for Host Adapter V3."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/project-intelligence/src"))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.host_adapter import (  # noqa: E402
    CAPABILITY_IDS,
    HostAdapterV3Error,
    compute_executable_measurement_id,
    compute_host_adapter_v3_receipt_id,
    discover_executable,
    inspect_local_host_adapter_v3,
    validate_executable_measurement,
    validate_host_adapter_v3_receipt,
)
import workbench_core.host_adapter as host_adapter  # noqa: E402
from workbench_shell.cli import _human_host_status, main as cli_main  # noqa: E402


class HostAdapterV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.receipt = inspect_local_host_adapter_v3(scratch_parent=self.root)

    def test_v3_is_flat_complete_and_identity_bound(self) -> None:
        self.assertEqual(
            self.receipt["format"],
            "workbench-host-adapter-conformance-v3",
        )
        self.assertEqual(self.receipt["schema_version"], 3)
        self.assertNotIn("predecessor_receipt", self.receipt)
        self.assertEqual(
            [row["id"] for row in self.receipt["capabilities"]],
            list(CAPABILITY_IDS),
        )
        self.assertEqual(
            self.receipt["receipt_id"],
            compute_host_adapter_v3_receipt_id(self.receipt),
        )
        self.assertFalse(self.receipt["release_qualified"])

    def test_running_executable_is_descriptor_and_digest_bound(self) -> None:
        measurement = validate_executable_measurement(
            self.receipt["active_executable"]
        )
        self.assertEqual(len(measurement["sha256"]), 64)
        self.assertGreater(measurement["size_bytes"], 0)
        discovery = self.receipt["capabilities"][7]
        self.assertEqual(discovery["state"], "available")
        self.assertEqual(discovery["evidence"], [measurement["measurement_id"]])

    def test_explicit_search_roots_reject_zero_and_ambiguous_matches(self) -> None:
        with self.assertRaisesRegex(HostAdapterV3Error, "requires explicit"):
            discover_executable("missing-tool")
        roots = (self.root / "one", self.root / "two")
        for root in roots:
            root.mkdir()
            candidate = root / "tool"
            candidate.write_bytes(b"#!/bin/sh\nexit 0\n")
            candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR)
        with self.assertRaisesRegex(HostAdapterV3Error, "ambiguous"):
            discover_executable("tool", search_roots=roots)

    def test_distinct_hardlink_paths_remain_ambiguous(self) -> None:
        roots = (self.root / "hardlink-one", self.root / "hardlink-two")
        for root in roots:
            root.mkdir()
        first = roots[0] / "tool"
        second = roots[1] / "tool"
        first.write_bytes(b"#!/bin/sh\nexit 0\n")
        first.chmod(0o700)
        try:
            os.link(first, second)
        except OSError as exc:
            self.skipTest(f"hard links unavailable on this test filesystem: {exc}")
        with self.assertRaisesRegex(HostAdapterV3Error, "ambiguous"):
            discover_executable("tool", search_roots=roots)

    def test_search_boundary_and_byte_bound_fail_before_claim(self) -> None:
        with self.assertRaisesRegex(HostAdapterV3Error, "filename only"):
            discover_executable("nested/tool", search_roots=(self.root,))
        with self.assertRaisesRegex(HostAdapterV3Error, "exact bounded tuple"):
            discover_executable("tool", search_roots=[self.root])  # type: ignore[arg-type]
        with self.assertRaisesRegex(HostAdapterV3Error, "declared byte bound"):
            discover_executable(Path(sys.executable), maximum_bytes=1)

    def test_python_312_resolve_loop_failure_is_stable_no_match(self) -> None:
        with patch.object(Path, "resolve", side_effect=RuntimeError("symlink loop")):
            with self.assertRaisesRegex(HostAdapterV3Error, "no regular executable"):
                discover_executable("loop-tool", search_roots=(self.root,))

    def test_missing_executable_disables_only_dependent_operations(self) -> None:
        with patch(
            "workbench_core.host_adapter.discover_executable",
            side_effect=HostAdapterV3Error("synthetic missing executable"),
        ):
            receipt = inspect_local_host_adapter_v3(scratch_parent=self.root)
        discovery = receipt["capabilities"][7]
        self.assertEqual(discovery["state"], "unavailable")
        self.assertIsNone(receipt["active_executable"])
        self.assertFalse(receipt["core_host_eligible"])
        self.assertIn(
            "executable-identity-discovery", receipt["missing_core_capabilities"]
        )
        self.assertTrue(
            all(row["state"] == "unavailable" for row in receipt["operation_states"])
        )
        human = _human_host_status(receipt)
        self.assertIn("why: synthetic missing executable", human)
        self.assertIn("next: Select one explicit regular executable", human)

    def test_missing_running_interpreter_locator_is_a_stable_diagnostic(self) -> None:
        with patch.object(host_adapter.os.sys, "executable", None):
            receipt = inspect_local_host_adapter_v3(scratch_parent=self.root)
        discovery = receipt["capabilities"][7]
        self.assertEqual(discovery["state"], "unavailable")
        self.assertIn("does not expose", discovery["limitation"])
        self.assertIsNone(receipt["active_executable"])

    def test_operation_states_are_exact_and_explain_missing_capabilities(self) -> None:
        states = {row["id"]: row for row in self.receipt["operation_states"]}
        self.assertEqual(states["core-host"]["state"], "available")
        self.assertEqual(states["core-host"]["missing_capability_ids"], [])
        for operation in (
            "hosted-local-service",
            "credential-backed-operation",
            "desktop-notification",
            "delegated-runtime",
        ):
            self.assertEqual(states[operation]["state"], "unavailable")
            self.assertTrue(states[operation]["missing_capability_ids"])

    def test_capability_shape_and_derived_state_tamper_fail(self) -> None:
        changed = deepcopy(self.receipt)
        changed["capabilities"][0]["evidence"] = []
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "available requires evidence"):
            validate_host_adapter_v3_receipt(changed)

        changed = deepcopy(self.receipt)
        changed["operation_states"][0]["state"] = "unavailable"
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "derived operation"):
            validate_host_adapter_v3_receipt(changed)

    def test_measurement_and_release_claim_tamper_fail(self) -> None:
        changed = deepcopy(self.receipt)
        changed["active_executable"]["sha256"] = "0" * 64
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "identity mismatch"):
            validate_host_adapter_v3_receipt(changed)

        changed = deepcopy(self.receipt)
        changed["release_qualified"] = True
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "cannot qualify"):
            validate_host_adapter_v3_receipt(changed)

    def test_resealed_non_file_non_filename_and_non_executable_claims_fail(self) -> None:
        mutations = (
            ("requested_name", "../../not-a-filename"),
            ("resolved_uri", "https://example.invalid/workbench"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                changed = deepcopy(self.receipt)
                measurement = changed["active_executable"]
                measurement[field] = value
                measurement["measurement_id"] = compute_executable_measurement_id(
                    measurement
                )
                changed["capabilities"][7]["evidence"] = [
                    measurement["measurement_id"]
                ]
                changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
                with self.assertRaises(HostAdapterV3Error):
                    validate_host_adapter_v3_receipt(changed)

        changed = deepcopy(self.receipt)
        measurement = changed["active_executable"]
        measurement["descriptor_identity"]["mode"] = 0
        measurement["measurement_id"] = compute_executable_measurement_id(
            measurement
        )
        changed["capabilities"][7]["evidence"] = [measurement["measurement_id"]]
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "executable permission"):
            validate_host_adapter_v3_receipt(changed)

    def test_permission_removed_between_resolution_and_open_fails_closed(self) -> None:
        candidate = self.root / "permission-race-tool"
        candidate.write_bytes(b"#!/bin/sh\nexit 0\n")
        candidate.chmod(0o700)
        measured = candidate.stat()
        descriptor_without_execute = SimpleNamespace(
            st_dev=measured.st_dev,
            st_ino=measured.st_ino,
            st_mode=stat.S_IFREG | 0o600,
            st_size=measured.st_size,
            st_mtime_ns=measured.st_mtime_ns,
            st_ctime_ns=measured.st_ctime_ns,
        )

        with patch(
            "workbench_core.host_adapter._resolve_candidates",
            return_value=candidate.resolve(),
        ), patch(
            "workbench_core.host_adapter.os.fstat",
            return_value=descriptor_without_execute,
        ):
            with self.assertRaisesRegex(HostAdapterV3Error, "no executable permission"):
                discover_executable(candidate)

    def test_same_size_restored_mtime_mutation_during_final_path_check_fails(self) -> None:
        candidate = self.root / "final-path-race-tool"
        original = b"#!/bin/sh\nexit 0\n"
        replacement = b"#!/bin/sh\nexit 9\n"
        self.assertEqual(len(original), len(replacement))
        candidate.write_bytes(original)
        candidate.chmod(0o700)
        metadata = candidate.stat()
        resolved_candidate = candidate.resolve()
        mutated = False

        def mutate_then_lstat(path: Path):
            nonlocal mutated
            if path == resolved_candidate and not mutated:
                mutated = True
                descriptor = os.open(path, os.O_WRONLY)
                try:
                    os.write(descriptor, replacement)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.utime(
                    path,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                )
            # Model a filesystem whose pathname identity fields do not expose
            # the same-size, restored-mtime rewrite. The final descriptor
            # fstat must still close the measurement interval via ctime.
            return metadata

        with patch.object(Path, "lstat", autospec=True, side_effect=mutate_then_lstat):
            with self.assertRaisesRegex(
                HostAdapterV3Error,
                "changed during final pathname verification",
            ):
                discover_executable(candidate)
        self.assertTrue(mutated)
        self.assertEqual(candidate.read_bytes(), replacement)

    def test_parent_namespace_swap_during_final_access_check_fails(self) -> None:
        try:
            namespace_temp = tempfile.TemporaryDirectory(
                prefix="workbench-v3-namespace-",
                dir="/dev/shm",
            )
        except OSError as exc:
            self.skipTest(f"no rename-capable temporary filesystem: {exc}")
        self.addCleanup(namespace_temp.cleanup)
        namespace_root = Path(namespace_temp.name)
        current = namespace_root / "current"
        old = namespace_root / "old"
        current.mkdir()
        candidate = current / "tool"
        original = b"#!/bin/sh\nexit 0\n"
        replacement = b"#!/bin/sh\nexit 9\n"
        candidate.write_bytes(original)
        candidate.chmod(0o700)
        resolved_candidate = candidate.resolve()
        real_access = os.access
        access_calls = 0

        def swap_on_final_access(path, mode, *args, **kwargs):
            nonlocal access_calls
            if Path(path) == resolved_candidate:
                access_calls += 1
                if access_calls == 3:
                    current.rename(old)
                    current.mkdir()
                    replacement_path = current / "tool"
                    replacement_path.write_bytes(replacement)
                    replacement_path.chmod(0o700)
            return real_access(path, mode, *args, **kwargs)

        with patch(
            "workbench_core.host_adapter.os.access",
            side_effect=swap_on_final_access,
        ):
            with self.assertRaisesRegex(
                HostAdapterV3Error,
                "namespace changed during final verification",
            ):
                discover_executable(candidate)
        self.assertEqual(access_calls, 3)
        self.assertEqual((current / "tool").read_bytes(), replacement)
        self.assertEqual((old / "tool").read_bytes(), original)

    def test_windows_measurement_does_not_require_posix_execute_bits(self) -> None:
        measurement = deepcopy(self.receipt["active_executable"])
        measurement["path_flavour"] = "windows"
        measurement["requested_name"] = "python.exe"
        measurement["resolved_name"] = "python.exe"
        measurement["resolved_uri"] = "file:///C:/Python314/python.exe"
        measurement["executable_format"] = "windows-pe-candidate"
        measurement["descriptor_identity"]["mode"] = 0
        measurement["measurement_id"] = compute_executable_measurement_id(
            measurement
        )
        self.assertEqual(
            validate_executable_measurement(measurement),
            measurement,
        )

    def test_measurement_path_semantics_cannot_bypass_adapter_boundary(self) -> None:
        changed = deepcopy(self.receipt)
        measurement = changed["active_executable"]
        measurement["path_flavour"] = "windows"
        measurement["requested_name"] = "forged.exe"
        measurement["resolved_name"] = "forged.exe"
        measurement["resolved_uri"] = "file:///C:/forged.exe"
        measurement["executable_format"] = "windows-pe-candidate"
        measurement["descriptor_identity"]["mode"] = 0
        measurement["measurement_id"] = compute_executable_measurement_id(
            measurement
        )
        changed["capabilities"][7]["evidence"] = [measurement["measurement_id"]]
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "adapter boundary"):
            validate_host_adapter_v3_receipt(changed)

    def test_windows_measurement_rejects_non_candidate_name_and_format(self) -> None:
        measurement = deepcopy(self.receipt["active_executable"])
        measurement["path_flavour"] = "windows"
        measurement["requested_name"] = "plain.txt"
        measurement["resolved_name"] = "plain.txt"
        measurement["resolved_uri"] = "file:///C:/plain.txt"
        measurement["executable_format"] = "windows-pe-candidate"
        measurement["descriptor_identity"]["mode"] = 0
        measurement["measurement_id"] = compute_executable_measurement_id(
            measurement
        )
        with self.assertRaisesRegex(HostAdapterV3Error, "PE-candidate"):
            validate_executable_measurement(measurement)

    def test_unhashable_path_flavours_fail_with_stable_v3_errors(self) -> None:
        measurement = deepcopy(self.receipt["active_executable"])
        measurement["path_flavour"] = []
        measurement["measurement_id"] = compute_executable_measurement_id(
            measurement
        )
        with self.assertRaisesRegex(HostAdapterV3Error, "path_flavour"):
            validate_executable_measurement(measurement)

        changed = deepcopy(self.receipt)
        changed["adapter"]["path_flavour"] = {}
        changed["receipt_id"] = compute_host_adapter_v3_receipt_id(changed)
        with self.assertRaisesRegex(HostAdapterV3Error, "path_flavour"):
            validate_host_adapter_v3_receipt(changed)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink test requires host support")
    def test_safe_symlink_to_unsafe_resolved_name_fails_closed(self) -> None:
        target = self.root / "unsafe\nresolved-tool"
        target.write_bytes(b"#!/bin/sh\nexit 0\n")
        target.chmod(0o700)
        link = self.root / "safe-tool"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable on this test filesystem: {exc}")
        with self.assertRaisesRegex(HostAdapterV3Error, "safe bounded"):
            discover_executable(link)

    def test_huge_descriptor_identity_fails_with_stable_v3_error(self) -> None:
        for field in ("device", "inode"):
            with self.subTest(field=field):
                measurement = deepcopy(self.receipt["active_executable"])
                measurement["descriptor_identity"][field] = 10**5000
                with self.assertRaisesRegex(HostAdapterV3Error, "bounded"):
                    validate_executable_measurement(measurement)

    def test_executable_is_opened_with_binary_flag_when_host_exposes_it(self) -> None:
        candidate = Path(sys.executable)
        real_open = os.open
        synthetic_binary_flag = 1 << 28
        seen_flags: list[int] = []

        def binary_aware_open(path, flags):
            seen_flags.append(flags)
            return real_open(path, flags & ~synthetic_binary_flag)

        with patch.object(
            host_adapter.os,
            "O_BINARY",
            synthetic_binary_flag,
            create=True,
        ), patch(
            "workbench_core.host_adapter.os.open",
            side_effect=binary_aware_open,
        ):
            discover_executable(candidate)
        self.assertTrue(seen_flags)
        self.assertTrue(seen_flags[0] & synthetic_binary_flag)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO race requires POSIX")
    def test_replaced_fifo_is_rejected_without_blocking(self) -> None:
        candidates = (Path("/dev/shm"), self.root)
        for parent in candidates:
            try:
                temporary = tempfile.TemporaryDirectory(
                    prefix="workbench-v3-fifo-",
                    dir=parent,
                )
            except OSError:
                continue
            self.addCleanup(temporary.cleanup)
            fifo = Path(temporary.name) / "replacement-fifo"
            try:
                os.mkfifo(fifo)
            except OSError:
                continue
            with patch(
                "workbench_core.host_adapter._resolve_candidates",
                return_value=fifo,
            ):
                with self.assertRaisesRegex(HostAdapterV3Error, "not a regular file"):
                    discover_executable(fifo)
            return
        self.skipTest("no FIFO-capable temporary filesystem is available")

    def test_host_status_uses_v3_and_rejects_retired_version_selector(self) -> None:
        human = io.StringIO()
        with redirect_stdout(human):
            result = cli_main(
                [
                    "host-status",
                    "--suite-root",
                    str(REPOSITORY_ROOT),
                    "--scratch-parent",
                    str(self.root),
                ]
            )
        self.assertEqual(result, 0)
        self.assertIn("workbench.local-python-host-adapter:v3", human.getvalue())
        self.assertIn("Executable identity: sha256:", human.getvalue())
        self.assertIn("Operations:", human.getvalue())
        self.assertIn("core-host: AVAILABLE", human.getvalue())

        completed = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "tools/workbench.py"),
                "host-status",
                "--adapter-version",
                "1",
                "--scratch-parent",
                str(self.root),
                "--json",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("unrecognized arguments: --adapter-version 1", completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
