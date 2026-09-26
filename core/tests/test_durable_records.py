"""Core private record publication, recovery, and host boundary."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from workbench_api.host_filesystem import (
    DurableRecordError, append_private_line, count_interrupted_create_once_stages,
    inspect_private_journal, private_record_lock, publish_create_once_bytes,
    publish_immutable_bytes, read_bounded_bytes, read_private_bytes,
    read_private_single_link_bytes, remove_private_bytes, replace_private_bytes,
    update_preference_bytes,
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

    def test_create_once_keeps_historical_crash_stage_visible(self) -> None:
        orphan = self.root / ".current.json.interrupted.tmp"
        orphan.write_bytes(b"partial")
        orphan.chmod(0o600)
        self.assertEqual(1, count_interrupted_create_once_stages(self.path))
        linked_stages: list[str] = []
        original_link = os.link

        def observe_link(source: Path, target: Path, **kwargs: object) -> None:
            linked_stages.append(source.name)
            original_link(source, target, **kwargs)

        with patch.object(durable_records.os, "link", side_effect=observe_link):
            publish_create_once_bytes(self.path, b'{}\n', byte_limit=1024)
        self.assertEqual(1, len(linked_stages))
        self.assertRegex(linked_stages[0], r"^\.current\.json\.[0-9a-f]{16}\.tmp$")
        self.assertFalse((self.root / linked_stages[0]).exists())
        self.assertTrue(orphan.exists())
        self.assertEqual(1, count_interrupted_create_once_stages(self.path))
        self.assertEqual(b'{}\n', read_private_single_link_bytes(self.path, byte_limit=1024))
        with self.assertRaises(DurableRecordError) as collision:
            publish_create_once_bytes(self.path, b'{"other":true}\n', byte_limit=1024)
        self.assertEqual("collision", collision.exception.code)
        self.assertEqual(b'{}\n', self.path.read_bytes())

    def test_interrupted_stage_inventory_rejects_redirect(self) -> None:
        stage = self.root / ".current.json.redirect.tmp"
        stage.symlink_to(self.path)
        with self.assertRaises(DurableRecordError) as unsafe:
            count_interrupted_create_once_stages(self.path)
        self.assertEqual("unsafe", unsafe.exception.code)

    def test_create_once_stage_name_collision_preserves_existing_residue(self) -> None:
        stage = self.root / ".current.json.0000000000000000.tmp"
        stage.write_bytes(b"recoverable")
        stage.chmod(0o600)
        with patch.object(durable_records.secrets, "token_hex", return_value="0" * 16):
            with self.assertRaises(DurableRecordError) as collision:
                publish_create_once_bytes(self.path, b'{}\n', byte_limit=1024)
        self.assertEqual("write", collision.exception.code)
        self.assertEqual(b"recoverable", stage.read_bytes())
        self.assertFalse(self.path.exists())

    def test_single_link_private_read_rejects_hardlink(self) -> None:
        replace_private_bytes(self.path, b'{}\n', byte_limit=1024, require_absent=True)
        other = self.root / "other.json"
        os.link(self.path, other)
        with self.assertRaises(DurableRecordError) as unsafe:
            read_private_single_link_bytes(self.path, byte_limit=1024)
        self.assertEqual("unsafe", unsafe.exception.code)
        self.assertEqual(b'{}\n', other.read_bytes())

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

    @unittest.skipUnless(os.name == "posix", "POSIX historical mode fixture")
    def test_preference_update_privately_upgrades_ordinary_legacy_file(self) -> None:
        self.path.write_bytes(b"legacy\n")
        self.path.chmod(0o644)
        observed = []
        update_preference_bytes(
            self.path, lambda previous: observed.append(previous) or b"current\n",
            byte_limit=1024,
        )
        self.assertEqual([b"legacy\n"], observed)
        self.assertEqual(b"current\n", read_private_bytes(self.path, byte_limit=1024))
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

    @unittest.skipUnless(os.name == "posix", "POSIX historical link fixture")
    def test_preference_update_refuses_legacy_hardlink(self) -> None:
        self.path.write_bytes(b"legacy\n")
        self.path.chmod(0o644)
        outside = self.root / "outside.json"
        os.link(self.path, outside)
        with self.assertRaisesRegex(ValueError, "single regular file"):
            update_preference_bytes(self.path, lambda _previous: b"current\n", byte_limit=1024)
        self.assertEqual(b"legacy\n", outside.read_bytes())
        self.assertEqual(0o644, outside.stat().st_mode & 0o777)

    def test_private_removal_requires_exact_reviewed_bytes(self) -> None:
        content = b'{"active":true}\n'
        replace_private_bytes(self.path, content, byte_limit=1024, require_absent=True)
        reviewed = "sha256:" + sha256(content).hexdigest()
        with self.assertRaises(DurableRecordError) as stale:
            remove_private_bytes(
                self.path, expected_sha256="sha256:" + "0" * 64,
                byte_limit=1024,
            )
        self.assertEqual("stale", stale.exception.code)
        self.assertEqual(content, self.path.read_bytes())
        with self.assertRaises(DurableRecordError) as bounded:
            remove_private_bytes(self.path, expected_sha256=reviewed, byte_limit=3)
        self.assertEqual("unsafe", bounded.exception.code)
        self.assertEqual(content, self.path.read_bytes())
        remove_private_bytes(self.path, expected_sha256=reviewed, byte_limit=1024)
        self.assertFalse(self.path.exists())
        with self.assertRaises(DurableRecordError) as absent:
            remove_private_bytes(self.path, expected_sha256=reviewed, byte_limit=1024)
        self.assertEqual("unavailable", absent.exception.code)

    def test_private_removal_preserves_symlink_target(self) -> None:
        other = self.root / "other.json"
        replace_private_bytes(other, b"other\n", byte_limit=1024, require_absent=True)
        self.path.symlink_to(other)
        with self.assertRaises(DurableRecordError) as unsafe:
            remove_private_bytes(
                self.path, expected_sha256="sha256:" + sha256(b"other\n").hexdigest(),
                byte_limit=1024,
            )
        self.assertEqual("unsafe", unsafe.exception.code)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(b"other\n", other.read_bytes())

    def test_unprivate_mount_refuses_publication_without_output(self) -> None:
        original = durable_records.private_path

        def simulated_mount(path: Path, *, directory: bool) -> bool:
            if directory and path == self.root:
                return False
            return original(path, directory=directory)

        with patch.object(durable_records, "private_path", side_effect=simulated_mount):
            with self.assertRaises(DurableRecordError) as unsafe:
                publish_immutable_bytes(self.path, b"private\n", byte_limit=1024)
            with self.assertRaises(DurableRecordError) as create_once:
                publish_create_once_bytes(self.path, b"private\n", byte_limit=1024)
            with self.assertRaises(DurableRecordError) as inventory:
                count_interrupted_create_once_stages(self.path)
        self.assertEqual("unsafe", unsafe.exception.code)
        self.assertEqual("unsafe", create_once.exception.code)
        self.assertEqual("unsafe", inventory.exception.code)
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

    def test_append_journal_reopens_exact_bytes_and_refuses_stale_or_torn_tail(self) -> None:
        first = b'{"sequence":1}\n'
        second = b'{"sequence":2}\n'
        size = append_private_line(self.path, first, expected_size=0, byte_limit=1024)
        self.assertEqual(len(first), size)
        with self.assertRaises(DurableRecordError) as aggregate:
            append_private_line(
                self.path, second, expected_size=size, byte_limit=1024,
                journal_byte_limit=size + len(second) - 1,
            )
        self.assertEqual("bounds", aggregate.exception.code)
        self.assertEqual(first, self.path.read_bytes())
        size = append_private_line(self.path, second, expected_size=size, byte_limit=1024)
        self.assertEqual(first + second, read_private_bytes(self.path, byte_limit=size))
        self.assertEqual({
            "format": "workbench-private-journal-inspection-v1",
            "size": size,
            "sha256": sha256(first + second).hexdigest(),
            "complete_size": size,
            "complete_sha256": sha256(first + second).hexdigest(),
            "incomplete_size": 0,
            "incomplete_sha256": None,
        }, inspect_private_journal(self.path, byte_limit=size))
        with self.assertRaises(DurableRecordError) as stale:
            append_private_line(self.path, b'{"sequence":3}\n', expected_size=len(first), byte_limit=1024)
        self.assertEqual("stale", stale.exception.code)
        with self.assertRaises(DurableRecordError) as collision:
            append_private_line(self.path, first, expected_size=0, byte_limit=1024)
        self.assertEqual("collision", collision.exception.code)

        # Simulate a process dying after a partial write. Inspection preserves
        # all bytes, but the next append must not disguise the incomplete event.
        with self.path.open("ab") as stream:
            stream.write(b'{"sequence":')
            stream.flush()
            os.fsync(stream.fileno())
        partial = inspect_private_journal(self.path, byte_limit=1024)
        self.assertEqual(size, partial["complete_size"])
        self.assertEqual(len(b'{"sequence":'), partial["incomplete_size"])
        with self.assertRaises(DurableRecordError) as incomplete:
            append_private_line(
                self.path, b'{"sequence":3}\n', expected_size=partial["size"], byte_limit=1024,
            )
        self.assertEqual("incomplete", incomplete.exception.code)
        self.assertEqual(first + second + b'{"sequence":', self.path.read_bytes())
        code = (
            "import json, sys; from pathlib import Path; "
            "from workbench_core.host_services import install_local_host_services; "
            "from workbench_api.host_filesystem import inspect_private_journal; "
            "install_local_host_services(); "
            "print(json.dumps(inspect_private_journal(Path(sys.argv[1]), byte_limit=1024)))"
        )
        roots = Path(__file__).resolve().parents
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join((
            str(roots[2] / "api/src"), str(roots[1] / "src"),
        ))}
        reopened = subprocess.run(
            [sys.executable, "-c", code, str(self.path)], env=environment,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(partial, json.loads(reopened.stdout))

    def test_append_journal_is_bounded_and_refuses_redirects(self) -> None:
        with self.assertRaises(DurableRecordError) as malformed:
            append_private_line(self.path, b"one\ntwo\n", expected_size=0, byte_limit=1024)
        self.assertEqual("bounds", malformed.exception.code)
        with self.assertRaises(DurableRecordError) as oversized:
            append_private_line(self.path, b"1234\n", expected_size=0, byte_limit=4)
        self.assertEqual("bounds", oversized.exception.code)
        self.assertFalse(self.path.exists())
        other = self.root / "other"
        other.write_bytes(b"outside\n")
        self.path.symlink_to(other)
        with self.assertRaises(DurableRecordError) as redirected:
            append_private_line(self.path, b"inside\n", expected_size=0, byte_limit=1024)
        self.assertEqual("unsafe", redirected.exception.code)
        self.assertEqual(b"outside\n", other.read_bytes())
        self.path.unlink()
        original = durable_records.private_path

        def simulated_mount(path: Path, *, directory: bool) -> bool:
            return False if directory and path == self.root else original(path, directory=directory)

        with patch.object(durable_records, "private_path", side_effect=simulated_mount):
            with self.assertRaises(DurableRecordError) as unsafe:
                append_private_line(self.path, b"inside\n", expected_size=0, byte_limit=1024)
        self.assertEqual("unsafe", unsafe.exception.code)
        self.assertFalse(self.path.exists())

    def test_append_journal_expected_length_excludes_racing_writer(self) -> None:
        size = append_private_line(self.path, b"0\n", expected_size=0, byte_limit=1024)
        barrier = threading.Barrier(3)
        outcomes: list[str] = []

        def competing_writer(value: bytes) -> None:
            barrier.wait()
            try:
                append_private_line(self.path, value, expected_size=size, byte_limit=1024)
                outcomes.append("written")
            except DurableRecordError as exc:
                outcomes.append(exc.code)

        threads = [threading.Thread(target=competing_writer, args=(value,))
                   for value in (b"a\n", b"b\n")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, outcomes.count("written"))
        self.assertEqual(1, sum(code in {"busy", "stale"} for code in outcomes))
        self.assertIn(self.path.read_bytes(), {b"0\na\n", b"0\nb\n"})
