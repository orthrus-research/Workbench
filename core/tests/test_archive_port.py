"""Admitted module dispatch supplies Core archive transport for one operation."""

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from workbench_api.archive_exchange import (
    ArchiveExchangeUnavailable,
    current_archive_exchange,
)
from workbench_core.archive_port import CoreArchiveExchange
from workbench_core.modules import InstalledModule, dispatch


class ArchivePortDispatchTests(unittest.TestCase):
    def test_dispatch_binds_and_releases_transport_without_configuration_home(self):
        seen = []

        def run(argv, *, context):
            seen.append(current_archive_exchange())
            return 0

        descriptor = Module("sample", "0.1.0", (
            Capability("sample.archive", ("archive",), "archive_fixture:run", "archive"),
        ))
        installed = InstalledModule("sample", "workbench-sample", "0.1.0", "available", module=descriptor)
        with tempfile.TemporaryDirectory() as temporary:
            context = ExecutionContext(Path(temporary), Path(temporary))
            with patch.dict(sys.modules, {"archive_fixture": SimpleNamespace(run=run)}):
                self.assertEqual(0, dispatch(["archive"], context, (installed,)))
        self.assertEqual(1, len(seen))
        self.assertIsInstance(seen[0], CoreArchiveExchange)
        with self.assertRaises(ArchiveExchangeUnavailable):
            current_archive_exchange()

    def test_host_cancellation_is_enforced_during_transport(self):
        def run(argv, *, context):
            context.cancelled.set()
            current_archive_exchange().build_manifest([], metadata={})
            return 0

        descriptor = Module("sample", "0.1.0", (
            Capability("sample.archive", ("archive",), "archive_fixture:run", "archive"),
        ))
        installed = InstalledModule("sample", "workbench-sample", "0.1.0", "available", module=descriptor)
        with tempfile.TemporaryDirectory() as temporary:
            context = ExecutionContext(Path(temporary), Path(temporary))
            with patch.dict(sys.modules, {"archive_fixture": SimpleNamespace(run=run)}):
                with self.assertRaisesRegex(ModuleError, "cancelled"):
                    dispatch(["archive"], context, (installed,))
        with self.assertRaises(ArchiveExchangeUnavailable):
            current_archive_exchange()


if __name__ == "__main__":
    unittest.main()
