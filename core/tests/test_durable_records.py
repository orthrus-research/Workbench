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
    DurableRecordError, acquire_private_owned_marker, append_private_line,
    count_interrupted_create_once_stages, count_uncertain_record_stages,
    inspect_private_journal, measure_ordinary_single_link_file,
    private_exclusive_marker, private_record_lock,
    publish_commit_witness_bytes, publish_create_once_bytes,
    publish_immutable_bytes, publish_immutable_stream,
    read_bounded_bytes, read_bounded_single_link_bytes,
    read_private_bytes,
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

    def test_commit_witness_removes_stage_only_after_parent_flush(self) -> None:
        original_flush = durable_records.fsync_directory
        flushed: list[tuple[Path, bool]] = []

        def observe_flush(directory: Path) -> None:
            stages = list(self.root.glob('.current.json.*.tmp'))
            self.assertEqual(1, len(stages))
            flushed.append((directory, self.path.exists()))
            original_flush(directory)

        with patch.object(durable_records, 'fsync_directory', side_effect=observe_flush):
            publish_commit_witness_bytes(self.path, b'committed\n', byte_limit=1024)
        self.assertEqual([(self.root, False), (self.root, True)], flushed)
        self.assertEqual(0, count_uncertain_record_stages(self.root, targets=('current.json',)))
        self.assertEqual(b'committed\n', read_private_single_link_bytes(self.path, byte_limit=1024))

    def test_commit_witness_retains_stage_before_link_and_after_failed_flush(self) -> None:
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-stage-flush', 'before-link', 'after-link'):
            with self.subTest(moment=moment):
                def interrupt_link(source: Path, target: Path, **kwargs: object) -> None:
                    if moment == 'before-link' and target == self.path:
                        raise OSError('before witness link')
                    original_link(source, target, **kwargs)

                def interrupt_flush(directory: Path) -> None:
                    if moment == 'before-stage-flush' or (moment == 'after-link' and self.path.exists()):
                        raise OSError('witness directory flush interrupted')
                    original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(DurableRecordError):
                        publish_commit_witness_bytes(self.path, b'pending\n', byte_limit=1024)
                stages = list(self.root.glob('.current.json.*.tmp'))
                self.assertEqual(1, len(stages))
                self.assertEqual(moment == 'after-link', self.path.exists())
                self.assertEqual(1, count_uncertain_record_stages(self.root, targets=('current.json',)))
                self.assertEqual(b'pending\n', stages[0].read_bytes())
                stages[0].unlink()
                self.path.unlink(missing_ok=True)

    def test_commit_witness_hard_exit_after_link_retains_stage(self) -> None:
        code = (
            "import os,sys; from pathlib import Path; "
            "from workbench_core import durable_records as records; "
            "original=records.fsync_directory; "
            "records.fsync_directory=lambda directory: os._exit(74) if Path(sys.argv[1]).exists() else original(directory); "
            "records.publish_commit_witness_bytes(Path(sys.argv[1]), b'pending\\n', byte_limit=1024)"
        )
        roots = Path(__file__).resolve().parents
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join((
            str(roots[2] / "api/src"), str(roots[1] / "src"),
        ))}
        stopped = subprocess.run([sys.executable, "-c", code, str(self.path)],
                                 env=environment, capture_output=True, text=True)
        self.assertEqual(74, stopped.returncode)
        self.assertEqual(b'pending\n', self.path.read_bytes())
        stages = list(self.root.glob('.current.json.*.tmp'))
        self.assertEqual(1, len(stages))
        self.assertEqual(1, count_uncertain_record_stages(self.root, targets=('current.json',)))
        self.assertEqual(b'pending\n', stages[0].read_bytes())

    def test_streamed_immutable_record_exceeds_small_record_bound_without_buffering(self) -> None:
        block = b"x" * (1024 * 1024)
        expected = sha256()
        for _ in range(33):
            expected.update(block)
        expected.update(b"\n")
        # The iterable is consumed once; the report is larger than the usual
        # 32 MiB record limit without constructing a second whole-file value.
        def chunks():
            for _ in range(33):
                yield block
            yield b"\n"
        receipt = publish_immutable_stream(self.path, chunks())
        self.assertEqual({"path": str(self.path), "size": 33 * len(block) + 1,
                          "sha256": expected.hexdigest()}, receipt)
        self.assertEqual([], list(self.root.glob('.current.json.*.tmp')))
        if os.name != 'nt':
            self.path.chmod(0o644)
        self.assertEqual(receipt, measure_ordinary_single_link_file(
            self.path, expected_size=receipt['size'], expected_sha256=receipt['sha256'],
        ))
        with self.assertRaises(DurableRecordError):
            measure_ordinary_single_link_file(
                self.path, expected_size=receipt['size'] - 1, expected_sha256=receipt['sha256'],
            )
        with self.assertRaises(DurableRecordError):
            measure_ordinary_single_link_file(
                self.path, expected_size=receipt['size'], expected_sha256='0' * 64,
            )

    def test_streamed_immutable_interruption_preserves_stage_and_final_state(self) -> None:
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-stage-flush', 'before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                def interrupt_link(source, target, *args, **kwargs):
                    if moment == 'before-link' and Path(target) == self.path:
                        raise OSError('stream link interrupted')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if (Path(directory) == self.root
                            and (moment == 'before-stage-flush'
                                 or moment == 'after-link-before-flush' and self.path.exists())):
                        raise OSError('stream flush interrupted')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(DurableRecordError):
                        publish_immutable_stream(self.path, (b'whole', b' record\n'))
                stages = list(self.root.glob('.current.json.*.tmp'))
                self.assertEqual(1, len(stages))
                self.assertEqual(moment == 'after-link-before-flush', self.path.exists())
                self.assertEqual(1, count_uncertain_record_stages(self.root, targets=('current.json',)))
                self.assertEqual(b'' if moment == 'before-stage-flush' else b'whole record\n',
                                 stages[0].read_bytes())
                stages[0].unlink()
                self.path.unlink(missing_ok=True)

    def test_streamed_immutable_cancellation_retains_uncertain_stage(self) -> None:
        for moment in ('before-link', 'after-link'):
            with self.subTest(moment=moment):
                def cancel() -> None:
                    stages = list(self.root.glob('.current.json.*.tmp'))
                    if stages and (moment == 'before-link' or self.path.exists()):
                        raise ValueError('stream cancelled')

                with self.assertRaisesRegex(ValueError, 'stream cancelled'):
                    publish_immutable_stream(self.path, (b'whole record\n',),
                                             check_cancelled=cancel)
                stages = list(self.root.glob('.current.json.*.tmp'))
                self.assertEqual(1, len(stages))
                self.assertEqual(moment == 'after-link', self.path.exists())
                self.assertEqual(1, count_uncertain_record_stages(self.root, targets=('current.json',)))
                self.assertEqual(b'' if moment == 'before-link' else b'whole record\n',
                                 stages[0].read_bytes())
                stages[0].unlink()
                self.path.unlink(missing_ok=True)

    def test_streamed_immutable_cleanup_failures_keep_final_and_report_write_error(self) -> None:
        original_unlink = Path.unlink
        original_flush = durable_records.fsync_directory
        for moment in ('stage-unlink', 'final-parent-flush'):
            with self.subTest(moment=moment):
                flushes = 0

                def interrupt_unlink(path, *args, **kwargs):
                    if moment == 'stage-unlink' and path.name.startswith('.current.json.'):
                        raise OSError('stream stage unlink interrupted')
                    return original_unlink(path, *args, **kwargs)

                def interrupt_flush(directory):
                    nonlocal flushes
                    if Path(directory) == self.root:
                        flushes += 1
                        if moment == 'final-parent-flush' and flushes == 3:
                            raise OSError('stream final flush interrupted')
                    return original_flush(directory)

                with (patch.object(Path, 'unlink', interrupt_unlink),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(DurableRecordError) as raised:
                        publish_immutable_stream(self.path, (b'whole record\n',))
                self.assertEqual('write', raised.exception.code)
                self.assertEqual(b'whole record\n', self.path.read_bytes())
                self.assertEqual(moment == 'stage-unlink',
                                 bool(list(self.root.glob('.current.json.*.tmp'))))
                with self.assertRaises(DurableRecordError):
                    publish_immutable_stream(self.path, (b'never replayed\n',))
                for stage in self.root.glob('.current.json.*.tmp'):
                    stage.unlink()
                self.path.unlink()

    def test_streamed_immutable_secure_failure_retains_stage_as_unsafe(self) -> None:
        with patch.object(durable_records, 'secure_private_path',
                          side_effect=durable_records.HostFilesystemError('cannot protect stage')):
            with self.assertRaises(DurableRecordError) as raised:
                publish_immutable_stream(self.path, (b'never written\n',))
        self.assertEqual('unsafe', raised.exception.code)
        self.assertFalse(self.path.exists())
        self.assertEqual(1, len(list(self.root.glob('.current.json.*.tmp'))))

    def test_streamed_ordinary_measure_rejects_link_and_redirect(self) -> None:
        receipt = publish_immutable_stream(self.path, (b'historical\n',))
        if os.name != 'nt':
            self.path.chmod(0o644)
        other = self.root / 'other.json'
        os.link(self.path, other)
        with self.assertRaises(DurableRecordError) as linked:
            measure_ordinary_single_link_file(
                self.path, expected_size=receipt['size'], expected_sha256=receipt['sha256'],
            )
        self.assertEqual('unsafe', linked.exception.code)
        other.unlink()
        redirected = self.root / 'redirected.json'
        redirected.symlink_to(self.path)
        with self.assertRaises(DurableRecordError) as redirected_error:
            measure_ordinary_single_link_file(
                redirected, expected_size=receipt['size'], expected_sha256=receipt['sha256'],
            )
        self.assertEqual('unsafe', redirected_error.exception.code)

    def test_uncertain_record_stage_inventory_preserves_unknown_legacy_and_redirects(self) -> None:
        old = self.root / ('.record-' + 'a' * 32)
        named = self.root / '.current.json.unknown'
        old.write_bytes(b'legacy')
        named.write_bytes(b'named')
        self.assertEqual(2, count_uncertain_record_stages(self.root, targets=('current.json',)))
        self.assertEqual((b'legacy', b'named'), (old.read_bytes(), named.read_bytes()))
        redirected = self.root / '.current.json.redirect'
        redirected.symlink_to(old)
        with self.assertRaises(DurableRecordError) as unsafe:
            count_uncertain_record_stages(self.root, targets=('current.json',))
        self.assertEqual('unsafe', unsafe.exception.code)

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

    def test_external_single_link_read_preserves_historical_shared_parent(self) -> None:
        self.path.write_bytes(b'{"plan":1}\n')
        self.path.chmod(0o644)
        self.assertEqual(
            read_bounded_single_link_bytes(self.path, byte_limit=1024),
            b'{"plan":1}\n',
        )
        alternate = self.root / "alternate.json"
        os.link(self.path, alternate)
        with self.assertRaises(DurableRecordError) as linked:
            read_bounded_single_link_bytes(self.path, byte_limit=1024)
        self.assertEqual(linked.exception.code, "unsafe")
        self.assertEqual(alternate.read_bytes(), b'{"plan":1}\n')

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

    def test_exclusive_marker_excludes_legacy_writer_and_releases_exact_path(self) -> None:
        marker = self.root / "interface.lock"
        with private_exclusive_marker(marker):
            self.assertEqual(b"", marker.read_bytes())
            with self.assertRaises(DurableRecordError) as busy:
                with private_exclusive_marker(marker):
                    pass
            self.assertEqual("busy", busy.exception.code)
        self.assertFalse(marker.exists())
        marker.write_bytes(b"")
        marker.chmod(0o600)
        with self.assertRaises(DurableRecordError) as interrupted:
            with private_exclusive_marker(marker):
                pass
        self.assertEqual("busy", interrupted.exception.code)
        self.assertEqual(b"", marker.read_bytes())

    def test_exclusive_marker_preserves_replaced_path_on_release(self) -> None:
        marker = self.root / "interface.lock"
        with self.assertRaises(DurableRecordError) as changed:
            with private_exclusive_marker(marker):
                marker.unlink()
                marker.write_bytes(b"other owner")
                marker.chmod(0o600)
        self.assertEqual("changed", changed.exception.code)
        self.assertEqual(b"other owner", marker.read_bytes())

    def test_exclusive_marker_retains_uncertain_creation_for_recovery(self) -> None:
        marker = self.root / "interface.lock"
        with patch.object(durable_records, "fsync_directory", side_effect=OSError("barrier failed")):
            with self.assertRaises(DurableRecordError) as failed:
                with private_exclusive_marker(marker):
                    pass
        self.assertEqual("write", failed.exception.code)
        self.assertEqual(b"", marker.read_bytes())
        with self.assertRaises(DurableRecordError) as busy:
            with private_exclusive_marker(marker):
                pass
        self.assertEqual("busy", busy.exception.code)

    def test_exclusive_marker_refuses_unprivate_parent_without_residue(self) -> None:
        marker = self.root / "interface.lock"
        original = durable_records.private_path

        def simulated_mount(path: Path, *, directory: bool) -> bool:
            if directory and path == self.root:
                return False
            return original(path, directory=directory)

        with patch.object(durable_records, "private_path", side_effect=simulated_mount):
            with self.assertRaises(DurableRecordError) as unsafe:
                with private_exclusive_marker(marker):
                    pass
        self.assertEqual("unsafe", unsafe.exception.code)
        self.assertFalse(marker.exists())

    def test_owned_marker_excludes_old_writer_and_preserves_exact_recovery_bytes(self) -> None:
        marker = self.root / "active-transaction.lock"
        raw = b'{"binding":"plan:one","pid":123,"token":"abc"}'
        lease = acquire_private_owned_marker(marker, raw)
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(raw, read_private_single_link_bytes(marker, byte_limit=1024))
        self.assertIsNone(acquire_private_owned_marker(marker, b"other"))
        lease.preserve()
        self.assertEqual(raw, marker.read_bytes())
        self.assertIsNone(acquire_private_owned_marker(marker, b"other"))
        remove_private_bytes(
            marker, expected_sha256="sha256:" + sha256(raw).hexdigest(),
            byte_limit=1024,
        )
        reopened = acquire_private_owned_marker(marker, raw)
        self.assertIsNotNone(reopened)
        assert reopened is not None
        reopened.release()
        self.assertFalse(marker.exists())

    def test_owned_marker_release_preserves_replaced_path(self) -> None:
        marker = self.root / "active-transaction.lock"
        lease = acquire_private_owned_marker(marker, b"original")
        assert lease is not None
        marker.unlink()
        marker.write_bytes(b"replacement")
        marker.chmod(0o600)
        with self.assertRaises(DurableRecordError) as changed:
            lease.release()
        self.assertEqual("changed", changed.exception.code)
        self.assertEqual(b"replacement", marker.read_bytes())

    def test_owned_marker_release_preserves_changed_bytes(self) -> None:
        marker = self.root / "active-transaction.lock"
        lease = acquire_private_owned_marker(marker, b"original")
        assert lease is not None
        marker.write_bytes(b"changed!")
        with self.assertRaises(DurableRecordError) as changed:
            lease.release()
        self.assertEqual("changed", changed.exception.code)
        self.assertEqual(b"changed!", marker.read_bytes())

    def test_owned_marker_retains_uncertain_creation(self) -> None:
        marker = self.root / "active-transaction.lock"
        with patch.object(durable_records, "fsync_directory", side_effect=OSError("barrier failed")):
            with self.assertRaises(DurableRecordError) as failed:
                acquire_private_owned_marker(marker, b"recoverable")
        self.assertEqual("write", failed.exception.code)
        self.assertEqual(b"recoverable", marker.read_bytes())
        self.assertIsNone(acquire_private_owned_marker(marker, b"later"))

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
