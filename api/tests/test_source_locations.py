"""Source-location contracts work without a language or knowledge module."""

from hashlib import sha256
import unittest

from workbench_api.source_locations import (
    SourceLocationError,
    character_location,
    portable_relative_path,
    source_location,
    validate_location_shape,
    verify_source_location,
)


class SourceLocationContractTests(unittest.TestCase):
    def test_exact_unicode_bytes_and_utf16_editor_coordinates(self):
        raw = '/* 😀 */ fluid("水")\r\n'.encode()
        location = character_location(raw, "groovy/水.groovy", 8, 18)
        self.assertEqual({
            "path": "groovy/水.groovy", "sha256": sha256(raw).hexdigest(),
            "byte_start": 11, "byte_end": 23,
            "start": {"line": 1, "column": 10},
            "end": {"line": 1, "column": 20},
            "coordinate_system": "one-based-utf16", "interval": "half-open",
        }, location)
        self.assertEqual(location, verify_source_location(raw, location))
        with self.assertRaises(SourceLocationError):
            verify_source_location(raw.replace(b"fluid", b"other"), location)

    def test_insertion_points_and_codepoint_boundaries(self):
        for raw in (b"", b"line\r\n", "😀\n".encode()):
            location = source_location(raw, "source.groovy", len(raw), len(raw))
            self.assertEqual(location, verify_source_location(raw, location))
            self.assertEqual(location["start"], location["end"])
        with self.assertRaises(SourceLocationError):
            source_location("😀".encode(), "a.groovy", 1, 4)
        location = source_location(b"hello", "a.groovy", 0, 5)
        for update in (
            {"path": "../escape"}, {"byte_start": 6},
            {"start": {"line": 2, "column": 1}},
            {"coordinate_system": "one-based-utf8"},
        ):
            with self.subTest(update=update), self.assertRaises(SourceLocationError):
                validate_location_shape({**location, **update})

    def test_portable_paths_reject_cross_host_aliases(self):
        for path in (
            "../outside", "a/../b", "a//b", "a/./b", "/absolute", "C:relative",
            "C:/absolute", "a\\b", "NUL.txt", "a/COM1", "a/.git/config",
            "a/trailing.", "a/trailing ", "a/colon:name", "a/new\nline",
            "e\u0301.groovy", "a/" + "x" * 256,
        ):
            with self.subTest(path=path), self.assertRaises(SourceLocationError):
                portable_relative_path(path, "source location")
        self.assertEqual("groovy/水.groovy", str(portable_relative_path(
            "groovy/水.groovy", "source location"
        )))
        self.assertEqual("groovy", str(portable_relative_path(
            "groovy/", "source directory", allow_directory_marker=True
        )))
        with self.assertRaises(SourceLocationError):
            portable_relative_path("groovy/", "source location")
