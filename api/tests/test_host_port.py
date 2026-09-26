"""The API has a host port, not a hidden host implementation or fallback."""
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api import host_filesystem as port


class HostPortTests(unittest.TestCase):
    def test_unbound_port_fails_closed(self):
        with patch.object(port, "_host", None):
            with self.assertRaisesRegex(port.HostFilesystemError, "no filesystem host"):
                port.fsync_directory(Path("unused"))
            with self.assertRaisesRegex(port.HostFilesystemError, "no filesystem host"):
                port.append_private_line(Path("unused"), b"event\n", expected_size=0, byte_limit=16)
            with self.assertRaisesRegex(port.HostFilesystemError, "no filesystem host"):
                port.update_preference_bytes(Path("unused"), lambda _old: b"new", byte_limit=16)
            with self.assertRaisesRegex(port.HostFilesystemError, "no filesystem host"):
                port.read_bounded_single_link_bytes(Path("unused"), byte_limit=16)

    def test_host_is_explicit_and_cannot_be_replaced(self):
        observed = []
        host = SimpleNamespace(
            private_path=lambda path, **kwargs: True,
            secure_private_path=lambda path, **kwargs: path,
            secure_private_endpoint=lambda path: None,
            fsync_directory=observed.append,
        )
        with patch.object(port, "_host", None):
            port.bind_host_filesystem(host)
            port.bind_host_filesystem(host)
            port.fsync_directory(Path("selected"))
            self.assertEqual([Path("selected")], observed)
            with self.assertRaisesRegex(port.HostFilesystemError, "does not provide file leases"):
                port.file_lease(7, exclusive=True)
            with self.assertRaisesRegex(port.HostFilesystemError, "does not provide private journals"):
                port.inspect_private_journal(Path("unused"), byte_limit=16)
            with self.assertRaisesRegex(port.HostFilesystemError, "does not provide preference updates"):
                port.update_preference_bytes(Path("unused"), lambda _old: b"new", byte_limit=16)
            with self.assertRaisesRegex(port.HostFilesystemError, "single-link bounded reads"):
                port.read_bounded_single_link_bytes(Path("unused"), byte_limit=16)
            with self.assertRaisesRegex(port.HostFilesystemError, "owned markers"):
                port.acquire_private_owned_marker(Path("unused"), b"token")

            host.read_bounded_single_link_bytes = lambda path, *, byte_limit: (
                observed.append((path, byte_limit)) or b"reviewed\n"
            )
            self.assertEqual(
                port.read_bounded_single_link_bytes(Path("selected"), byte_limit=16),
                b"reviewed\n",
            )
            self.assertEqual(observed[-1], (Path("selected"), 16))

            @contextmanager
            def lease(descriptor, *, exclusive):
                observed.append(("acquired", descriptor, exclusive))
                try:
                    yield
                finally:
                    observed.append("released")

            host.file_lease = lease
            with port.file_lease(7, exclusive=False):
                self.assertEqual(("acquired", 7, False), observed[-1])
            self.assertEqual("released", observed[-1])
            with self.assertRaisesRegex(port.HostFilesystemError, "different"):
                port.bind_host_filesystem(SimpleNamespace(**vars(host)))

    def test_invalid_host_is_not_bound(self):
        with patch.object(port, "_host", None):
            with self.assertRaises(port.HostFilesystemError):
                port.bind_host_filesystem(object())
            self.assertIsNone(port._host)
