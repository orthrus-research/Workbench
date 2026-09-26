"""Core private record publication, recovery, and host boundary."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from workbench_api.host_filesystem import (
    DurableRecordError, private_record_lock, publish_immutable_bytes,
    read_bounded_bytes, read_private_bytes, replace_private_bytes,
)
from workbench_core.host_services import install_local_host_services
from workbench_core import durable_records


class DurableRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "current.json"

    def test_immutable_collision_and_revisioned_pointer_survive_restart(self) -> None:
        immutable = self.root / "00000000000000000000.json"
        publish_immutable_bytes(immutable, b'{"sequence":0}\n', byte_limit=1024)
        publish_immutable_bytes(immutable, b'{"sequence":0}\n', byte_limit=1024, idempotent=True)
        with self.assertRaises(DurableRecordError) as collision:
            publish_immutable_bytes(immutable, b'{"sequence":1}\n', byte_limit=1024)
        self.assertEqual("collision", collision.exception.code)
        self.assertEqual(b'{"sequence":0}\n', immutable.read_bytes())

        replace_private_bytes(self.path, b'{"current":0}\n', byte_limit=1024, require_absent=True)
        previous = "sha256:" + sha256(self.path.read_bytes()).hexdigest()
        replace_private_bytes(self.path, b'{"current":1}\n', byte_limit=1024,
                              expected_sha256=previous)
        with self.assertRaises(DurableRecordError) as stale:
            replace_private_bytes(self.path, b'{"current":2}\n', byte_limit=1024,
                                  expected_sha256=previous)
        self.assertEqual("stale", stale.exception.code)
        self.assertEqual(b'{"current":1}\n', self.path.read_bytes())
        code = (
            "from pathlib import Path; from workbench_core.host_services import install_local_host_services; "
            "from workbench_api.host_filesystem import read_private_bytes; "
            "install_local_host_services(); "
            "print(read_private_bytes(Path(__import__('sys').argv[1]), byte_limit=1024).decode().strip())"
        )
        roots = Path(__file__).resolve().parents
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join((
            str(roots[2] / "api/src"), str(roots[1] / "src"),
        ))}
        reopened = subprocess.run([sys.executable, "-c", code, str(self.path)],
                                  env=environment, capture_output=True, text=True, check=True)
        self.assertEqual('{"current":1}', reopened.stdout.strip())

    def test_private_read_rejects_ordinary_external_file_and_symlink(self) -> None:
        self.path.write_bytes(b"input\n")
        self.path.chmod(0o644)
        self.assertEqual(b"input\n", read_bounded_bytes(self.path, byte_limit=1024))
        with self.assertRaises(DurableRecordError) as unsafe:
            read_private_bytes(self.path, byte_limit=1024)
        self.assertEqual("unsafe", unsafe.exception.code)
        link = self.root / "link.json"
        link.symlink_to(self.path)
        with self.assertRaises(DurableRecordError) as redirected:
            read_bounded_bytes(link, byte_limit=1024)
        self.assertEqual("unsafe", redirected.exception.code)

    def test_unprivate_mount_refuses_publication_without_output(self) -> None:
        original = durable_records.private_path

        def simulated_mount(path: Path, *, directory: bool) -> bool:
            if directory and path == self.root:
                return False
            return original(path, directory=directory)

        with patch.object(durable_records, "private_path", side_effect=simulated_mount):
            with self.assertRaises(DurableRecordError) as unsafe:
                publish_immutable_bytes(self.path, b"private\n", byte_limit=1024)
        self.assertEqual("unsafe", unsafe.exception.code)
        self.assertFalse(self.path.exists())
        self.assertEqual([], list(self.root.iterdir()))

    def test_domain_transition_lease_excludes_second_writer(self) -> None:
        errors: list[DurableRecordError] = []

        def competing_writer() -> None:
            try:
                with private_record_lock(self.root / "selection.lock"):
                    pass
            except DurableRecordError as exc:
                errors.append(exc)

        with private_record_lock(self.root / "selection.lock"):
            thread = threading.Thread(target=competing_writer)
            thread.start()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(["busy"], [error.code for error in errors])
