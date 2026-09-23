"""Adversarial data exchange and unchanged-original custody across hosts."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from workbench_core import archive_exchange as exchange
from workbench_core import check_storage as storage
from workbench_core.filesystem_paths import native_path
from workbench_core.host_filesystem import private_path


class ArchiveExchangeTests(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ["LOCALAPPDATA"]) / "Temp" if os.name == "nt" else None
        if parent is not None:
            parent.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="workbench-exchange-", dir=parent))
        cleanup = Path("\\\\?\\" + str(self.root)) if os.name == "nt" else self.root
        self.addCleanup(shutil.rmtree, cleanup)
        self.source = self.root / "original 資料.bin"
        self.raw = b"exact binary\r\n\x00\xff\x1a\n" * 100
        self.source.write_bytes(self.raw)
        self.archive = self.root / "completed.zip"
        self.destination = self.root / "imported 資料"
        self.metadata = {"format": "test-data", "original": "immutable authority"}

    def export(self, **kwargs):
        return exchange.export_archive(self.archive, {"payload/資料.bin": self.source},
                                       metadata=self.metadata, **kwargs)

    def assert_no_partial(self):
        self.assertEqual(list(self.root.glob(".archive-*")), [])
        self.assertEqual(list(self.root.glob(".import-*")), [])

    def rewrite(self, transform):
        with zipfile.ZipFile(self.archive) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
        entries = transform(entries)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w") as target:
                for info, raw in entries:
                    target.writestr(info, raw)

    def refuse_import(self):
        with self.assertRaises(exchange.ArchiveError):
            exchange.import_archive(self.archive, self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_no_partial()

    def test_round_trip_preserves_bytes_and_separates_local_custody(self):
        before = self.source.stat()
        exported = self.export()
        def validate(root, manifest):
            self.assertNotEqual(root, self.destination)
            self.assertFalse(self.destination.exists())
            self.assertEqual((root / "payload/資料.bin").read_bytes(), self.raw)
            self.assertEqual(manifest, exported["manifest"])
            return {"domain": "verified"}
        imported = exchange.import_archive(self.archive, self.destination, validate=validate)
        self.assertEqual(imported["validation"], {"domain": "verified"})
        self.assertEqual(imported["manifest_id"], exported["manifest"]["id"])
        self.assertEqual(imported["archive"], exported["archive"])
        self.assertEqual(exchange.verify_directory(self.destination), exported["manifest"])
        self.assertEqual(exchange.inspect_archive(self.archive), exported["manifest"])
        self.assertEqual((self.destination / "payload/資料.bin").read_bytes(), self.raw)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.source.stat().st_ctime_ns, before.st_ctime_ns)
        self.assertTrue(private_path(self.destination, directory=True))
        self.assertTrue(private_path(self.destination / "payload/資料.bin", directory=False))
        self.assert_no_partial()

    def test_reexport_has_identical_identity_and_zip_bytes_without_local_receipt(self):
        original = self.export()
        exchange.import_archive(self.archive, self.destination)
        manifest = exchange.verify_directory(self.destination)
        repeated = exchange.export_archive(self.root / "repeat.zip",
            {row["path"]: self.destination / row["path"] for row in manifest["members"]},
            metadata=manifest["metadata"], expected_manifest=manifest)
        self.assertEqual(repeated["manifest"], original["manifest"])
        self.assertEqual(repeated["archive"]["sha256"], original["archive"]["sha256"])
        with zipfile.ZipFile(repeated["archive"]["path"]) as archive:
            self.assertNotIn(exchange.RECEIPT_NAME, archive.namelist())

    def test_reexport_refuses_changed_originals_against_expected_identity(self):
        expected = self.export()["manifest"]
        self.archive.unlink()
        self.source.write_bytes(b"changed after domain verification")
        with self.assertRaisesRegex(exchange.ArchiveError, "expected manifest"):
            self.export(expected_manifest=expected)
        self.assertFalse(self.archive.exists())
        self.assert_no_partial()

    def test_destinations_never_clobber_files_or_empty_directories(self):
        self.archive.write_bytes(b"existing user file")
        with self.assertRaises(exchange.ArchiveError):
            self.export()
        self.assertEqual(self.archive.read_bytes(), b"existing user file")
        self.archive.unlink()
        self.export()
        self.destination.mkdir()
        before = self.destination.stat()
        with self.assertRaises(exchange.ArchiveError):
            exchange.import_archive(self.archive, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertEqual(self.destination.stat().st_mtime_ns, before.st_mtime_ns)

    def test_destination_created_during_validation_is_not_replaced(self):
        self.export()
        def race(_root, _manifest):
            self.destination.mkdir()
            return {}
        with self.assertRaises(exchange.ArchiveError):
            exchange.import_archive(self.archive, self.destination, validate=race)
        self.assertTrue(self.destination.is_dir())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_no_partial()

    def test_export_refuses_unsafe_and_nonportable_member_names(self):
        for name in ("../escape", "/absolute", "C:/drive", "back\\slash", "a//b", "a/./b",
                     "a/../b", "CON", "aux.txt", "COM¹.dat", "file.", "file ", "a:b",
                     "control\x01", "wild*card", "manifest.json", "IMPORT-RECEIPT.JSON/x",
                     "x" * 256, "nul.txt/path", "\ud800"):
            with self.subTest(name=repr(name)), self.assertRaises(exchange.ArchiveError):
                exchange.export_archive(self.archive, {name: self.source}, metadata={})
            self.assertFalse(self.archive.exists())
        self.assert_no_partial()

    def test_export_refuses_case_unicode_and_file_directory_collisions(self):
        for names in (("A", "a"), ("é", "e\u0301"), ("a", "A/b"), ("a/b", "A")):
            with self.subTest(names=names), self.assertRaises(exchange.ArchiveError):
                exchange.export_archive(self.archive, dict.fromkeys(names, self.source), metadata={})
        self.assert_no_partial()

    def test_import_refuses_unsafe_and_unexpected_archive_members(self):
        self.export()
        original = self.archive.read_bytes()
        for name in ("../escaped", "C:/escaped", "payload\\escaped", "CON", "extra.bin",
                     "payload/資料.bin", "PAYLOAD/資料.bin", "payload", "a\x00suffix"):
            with self.subTest(name=repr(name)):
                self.archive.write_bytes(original)
                self.rewrite(lambda rows: [*rows, (name, b"unlisted")])
                self.refuse_import()

    def test_import_refuses_links_special_files_directories_and_reparse_members(self):
        self.export()
        original = self.archive.read_bytes()
        for mode, windows_attributes in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFIFO | 0o600, 0),
                                         (stat.S_IFDIR | 0o700, 0), (stat.S_IFREG | 0o600, 0x400)):
            with self.subTest(mode=mode, windows_attributes=windows_attributes):
                self.archive.write_bytes(original)
                def change(rows):
                    rows[0][0].external_attr = (mode << 16) | windows_attributes
                    return rows
                self.rewrite(change)
                self.refuse_import()

    def test_import_refuses_changed_payload_and_missing_payload(self):
        self.export()
        original = self.archive.read_bytes()
        self.rewrite(lambda rows: [(info, b"x" * len(raw) if info.filename != exchange.MANIFEST_NAME else raw)
                                  for info, raw in rows])
        self.refuse_import()
        self.archive.write_bytes(original)
        self.rewrite(lambda rows: rows[1:])
        self.refuse_import()

    def test_import_refuses_truncated_zip_and_broken_crc(self):
        self.export()
        original = self.archive.read_bytes()
        self.archive.write_bytes(original[:-30])
        self.refuse_import()
        damaged = bytearray(original)
        # The first local entry's compressed payload begins after its local name/extra.
        name_length = int.from_bytes(damaged[26:28], "little")
        extra_length = int.from_bytes(damaged[28:30], "little")
        damaged[30 + name_length + extra_length + 3] ^= 0x80
        self.archive.write_bytes(damaged)
        self.refuse_import()

    def test_zip64_streaming_round_trip(self):
        # Exercise real ZIP64 end records without allocating several GiB.
        with patch.object(zipfile, "ZIP64_LIMIT", 100):
            exported = self.export()
        exchange.import_archive(self.archive, self.destination)
        self.assertEqual(exchange.verify_directory(self.destination), exported["manifest"])

    def test_central_directory_is_bounded_before_zipfile_allocates_entries(self):
        self.export()
        data = bytearray(self.archive.read_bytes())
        struct.pack_into("<H", data, len(data) - 12, 65534)
        self.archive.write_bytes(data)
        with patch.object(exchange.zipfile, "ZipFile", side_effect=AssertionError("must not allocate")):
            with self.assertRaises(exchange.ArchiveError):
                exchange.import_archive(self.archive, self.destination)
        self.assert_no_partial()

    def test_archive_prefix_comments_and_trailing_bytes_are_refused(self):
        self.export()
        original = self.archive.read_bytes()
        for raw in (b"MZ-executable-prefix" + original, original + b"trailing"):
            self.archive.write_bytes(raw)
            self.refuse_import()
        self.archive.write_bytes(original)
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.comment = b"unsupported alternate container"
        self.refuse_import()

    def test_import_refuses_noncanonical_duplicate_or_invalid_manifest(self):
        self.export()
        original = self.archive.read_bytes()
        for mutation in (lambda raw: raw + b" ", lambda raw: b'{"schema":"a","schema":"b"}\n',
                         lambda raw: raw.replace(b'"payload_bytes":', b'"unknown":0,"payload_bytes":'),
                         lambda raw: raw.replace(b'"size":' + str(len(self.raw)).encode(), b'"size":true'),
                         lambda raw: raw.replace(b'"format":"test-data"', b'"format":"changed"')):
            self.archive.write_bytes(original)
            self.rewrite(lambda rows: [(info, mutation(raw) if info.filename == exchange.MANIFEST_NAME else raw)
                                      for info, raw in rows])
            self.refuse_import()

    def test_configured_bounds_apply_before_publication(self):
        for limits in (exchange.ArchiveLimits(max_payload_bytes=10),
                       exchange.ArchiveLimits(max_manifest_bytes=10)):
            with self.subTest(limits=limits), self.assertRaises(exchange.ArchiveError):
                self.export(limits=limits)
            self.assertFalse(self.archive.exists())
        self.export()
        for limits in (exchange.ArchiveLimits(max_payload_bytes=10),
                       exchange.ArchiveLimits(max_manifest_bytes=10)):
            with self.subTest(limits=limits), self.assertRaises(exchange.ArchiveError):
                exchange.import_archive(self.archive, self.destination, limits=limits)
            self.assertFalse(self.destination.exists())
        self.assert_no_partial()

    def test_cancellation_and_keyboard_interrupt_do_not_publish(self):
        with self.assertRaisesRegex(exchange.ArchiveError, "cancelled"):
            self.export(cancelled=lambda: True)
        self.assertFalse(self.archive.exists())
        self.export()
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 4
        with self.assertRaises(exchange.ArchiveError):
            exchange.import_archive(self.archive, self.destination, cancelled=cancelled)
        def interrupt(_root, _manifest):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            exchange.import_archive(self.archive, self.destination, validate=interrupt)
        self.assertFalse(self.destination.exists())
        self.assert_no_partial()

    def test_domain_rejection_and_domain_mutation_never_publish(self):
        self.export()
        def reject(_root, _manifest):
            raise ValueError("domain mismatch")
        def mutate(root, _manifest):
            (root / "payload/資料.bin").write_bytes(b"rewritten original")
            return {}
        for validator in (reject, mutate):
            with self.subTest(validator=validator), self.assertRaises(exchange.ArchiveError):
                exchange.import_archive(self.archive, self.destination, validate=validator)
            self.assertFalse(self.destination.exists())
            self.assert_no_partial()

    def test_export_refuses_source_changed_during_streaming(self):
        original = exchange._copy_hash
        def mutate(source, destination, **kwargs):
            value = original(source, destination, **kwargs)
            self.source.write_bytes(b"changed")
            return value
        with patch.object(exchange, "_copy_hash", mutate), self.assertRaises(exchange.ArchiveError):
            self.export()
        self.assertFalse(self.archive.exists())
        self.assert_no_partial()

    def test_verify_refuses_unexpected_missing_and_changed_originals(self):
        self.export()
        exchange.import_archive(self.archive, self.destination)
        for extra in ("unexpected.bin", "unexpected-directory"):
            path = self.destination / extra
            if extra.endswith("directory"):
                path.mkdir()
            else:
                path.write_bytes(b"extra")
            with self.assertRaises(exchange.ArchiveError):
                exchange.verify_directory(self.destination)
            path.rmdir() if path.is_dir() else path.unlink()
        path = self.destination / "payload/資料.bin"
        path.unlink()
        with self.assertRaises(exchange.ArchiveError):
            exchange.verify_directory(self.destination)
        path.write_bytes(b"changed")
        with self.assertRaises(exchange.ArchiveError):
            exchange.verify_directory(self.destination)

    def test_verify_refuses_changed_local_receipt(self):
        self.export()
        exchange.import_archive(self.archive, self.destination)
        path = self.destination / exchange.RECEIPT_NAME
        receipt = json.loads(path.read_bytes())
        receipt["manifest_id"] = "different object"
        path.write_bytes(storage.canonical(receipt) + b"\n")
        with self.assertRaises(exchange.ArchiveError):
            exchange.verify_directory(self.destination)

    def test_hardlinked_source_is_not_an_independent_original(self):
        os.link(self.source, self.root / "linked")
        with self.assertRaises(exchange.ArchiveError):
            self.export()
        self.assertFalse(self.archive.exists())

    @unittest.skipIf(os.name == "nt", "POSIX symbolic-link capability")
    def test_symlink_source_and_destination_ancestors_are_refused(self):
        link = self.root / "alias"
        link.symlink_to(self.source)
        with self.assertRaises(exchange.ArchiveError):
            exchange.export_archive(self.archive, {"payload": link}, metadata={})
        directory_link = self.root / "directory-alias"
        directory_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(exchange.ArchiveError):
            exchange.export_archive(directory_link / "archive.zip", {"payload": self.source}, metadata={})
        self.assertFalse(self.archive.exists())

    @unittest.skipUnless(os.name == "nt", "native Windows junction semantics")
    def test_windows_junction_ancestor_is_refused(self):
        junction = self.root / "junction"
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(self.root)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            with self.assertRaises(exchange.ArchiveError):
                exchange.export_archive(junction / "archive.zip", {"payload": self.source}, metadata={})
        finally:
            junction.rmdir()
        self.assertFalse((self.root / "archive.zip").exists())

    def test_deep_unicode_paths_use_native_io_without_device_paths_in_records(self):
        relative = "/".join(["資料", "a" * 70, "b" * 70, "c" * 70, "binary.dat"])
        exported = exchange.export_archive(self.archive, {relative: self.source}, metadata={})
        destination = self.root / ("deep-" + "d" * 70)
        imported = exchange.import_archive(self.archive, destination)
        self.assertGreater(len(str(destination / relative)), 300)
        self.assertEqual(native_path(destination / relative).read_bytes(), self.raw)
        self.assertEqual(exchange.verify_directory(destination), exported["manifest"])
        self.assertFalse(imported["destination"].startswith("\\\\?\\"))

    def test_sha256_reports_exact_archive_bytes(self):
        result = self.export()
        self.assertEqual(result["archive"]["size"], self.archive.stat().st_size)
        self.assertEqual(result["archive"]["sha256"], sha256(self.archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
