import unittest
from workbench_pack_program_studio.source_locations import (
    JsonSource,
    SourceLocationError,
    character_location,
    source_location,
    verify_source_location,
)


class SourceLocationTests(unittest.TestCase):
    def test_insertion_points_include_empty_files_and_eof(self):
        for raw in (b"", b"line\r\n", "// 😀\n".encode()):
            location = source_location(raw, "groovy/point.groovy", len(raw), len(raw))
            self.assertEqual(location, verify_source_location(raw, location))
            self.assertEqual(location["start"], location["end"])

    def test_utf8_bytes_and_utf16_coordinates_are_distinct(self):
        raw = '/* 😀 */ fluid("水")\r\n'.encode()
        location = character_location(raw, "groovy/水.groovy", 8, 18)
        self.assertEqual(10, location["start"]["column"])
        self.assertEqual(11, location["byte_start"])
        self.assertEqual(location, verify_source_location(raw, location))
        with self.assertRaises(SourceLocationError):
            verify_source_location(raw.replace(b"fluid", b"other"), location)

    def test_json_spans_preserve_escapes_and_reject_duplicate_keys(self):
        raw = b' { "a/b~": {"questID:3": 41} }\n'
        parsed = JsonSource(raw, "quests/41.json")
        location = parsed.location("/a~1b~0/questID:3")
        self.assertEqual(b"41", raw[location["byte_start"] : location["byte_end"]])
        with self.assertRaises(SourceLocationError):
            JsonSource(b'{"id":1,"id":2}', "q.json")
        with self.assertRaises(SourceLocationError):
            JsonSource(b"[" * 70 + b"0" + b"]" * 70, "q.json")

    def test_empty_reversed_or_mid_codepoint_spans_fail(self):
        raw = "😀".encode()
        location = character_location(raw, "a.groovy", 0, 1)
        for update in ({"byte_start": 1}, {"byte_end": 0}, {"path": "../escape"}):
            with self.assertRaises(ValueError):
                verify_source_location(raw, {**location, **update})
