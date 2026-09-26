"""Archive transport is selected by the host operation, never by archive data."""

from types import SimpleNamespace
import unittest

from workbench_api.archive_exchange import (
    ArchiveExchangeUnavailable,
    archive_exchange_scope,
    current_archive_exchange,
)


def _provider():
    def unused(*args, **kwargs):
        raise AssertionError("transport should not be invoked")

    return SimpleNamespace(
        build_manifest=unused,
        verify_directory=unused,
        import_archive=unused,
        export_archive=unused,
    )


class ArchiveExchangeScopeTests(unittest.TestCase):
    def test_unbound_and_incomplete_hosts_fail_closed(self):
        with self.assertRaises(ArchiveExchangeUnavailable):
            current_archive_exchange()
        with self.assertRaises(ArchiveExchangeUnavailable):
            with archive_exchange_scope(object()):
                self.fail("incomplete host was admitted")

    def test_nested_operation_restores_prior_host(self):
        first, second = _provider(), _provider()
        with archive_exchange_scope(first):
            self.assertIs(current_archive_exchange(), first)
            with archive_exchange_scope(second):
                self.assertIs(current_archive_exchange(), second)
            self.assertIs(current_archive_exchange(), first)
        with self.assertRaises(ArchiveExchangeUnavailable):
            current_archive_exchange()


if __name__ == "__main__":
    unittest.main()
