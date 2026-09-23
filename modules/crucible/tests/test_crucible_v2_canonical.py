from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_api.canonical import CANONICALIZER_ID, MAX_NESTING_DEPTH, CanonicalJsonError, canonical_json_bytes, content_id, parse_canonical_json, parse_json_strict, record_content_id, validate_content_id


class CrucibleV2CanonicalTests(unittest.TestCase):
    def test_canonicalizes_the_closed_value_domain(self) -> None:
        value = {
            "z": None,
            "array": [True, False, -9223372036854775808, 9223372036854775807],
            "text": "quote\" slash\\ newline\n snowman ☃",
        }
        self.assertEqual(
            b'{"array":[true,false,-9223372036854775808,9223372036854775807],'
            b'"text":"quote\\\" slash\\\\ newline\\u000a snowman \xe2\x98\x83","z":null}',
            canonical_json_bytes(value),
        )

    def test_sorts_object_keys_by_unsigned_utf8_bytes(self) -> None:
        value = OrderedDict([("\U00010000", 4), ("é", 3), ("a", 2), ("A", 1)])
        self.assertEqual(
            '{"A":1,"a":2,"é":3,"𐀀":4}'.encode("utf-8"),
            canonical_json_bytes(dict(value)),
        )

    def test_preserves_code_points_without_unicode_normalization(self) -> None:
        composed = canonical_json_bytes({"value": "é"})
        decomposed = canonical_json_bytes({"value": "e\u0301"})
        self.assertNotEqual(composed, decomposed)

    def test_every_control_character_uses_the_v2_hexadecimal_escape(self) -> None:
        self.assertEqual(
            ('"' + "".join(f"\\u00{value:02x}" for value in range(32)) + '"').encode("ascii"),
            canonical_json_bytes("".join(chr(value) for value in range(32))),
        )

    def test_all_unicode_scalars_preserve_exact_utf8_in_bounded_chunks(self) -> None:
        for lower, upper in ((32, 0xD800), (0xE000, 0x110000)):
            for start in range(lower, upper, 4096):
                with self.subTest(first_codepoint=start):
                    value = "".join(chr(point) for point in range(start, min(start + 4096, upper)))
                    expected = ('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"').encode("utf-8")
                    self.assertEqual(expected, canonical_json_bytes(value))

    def test_surrogate_errors_retain_nested_paths_and_scalar_indices(self) -> None:
        for point in range(0xD800, 0xE000):
            with self.subTest(codepoint=point):
                with self.assertRaises(CanonicalJsonError) as caught:
                    canonical_json_bytes({"outer": ["\U0001f9f1a" + chr(point)]})
                self.assertEqual(
                    f"$.outer[0][2]: surrogate code point U+{point:04X} is not a Unicode scalar",
                    str(caught.exception),
                )
        with self.assertRaises(CanonicalJsonError) as caught:
            canonical_json_bytes({"outer": [{"\U0001f9f1\ud800\udc00": 0}]})
        self.assertEqual(
            "$.outer[0].<key>[1]: surrogate code point U+D800 is not a Unicode scalar",
            str(caught.exception),
        )

    def test_rejects_values_outside_the_semantic_domain(self) -> None:
        invalid = [
            1.0,
            2**63,
            -(2**63) - 1,
            (1, 2),
            {1: "not-a-string-key"},
            {"value": "\ud800"},
            b"bytes",
        ]
        for value in invalid:
            with self.subTest(value=repr(value)):
                with self.assertRaises(CanonicalJsonError):
                    canonical_json_bytes(value)

    def test_rejects_cycles_with_a_contract_error(self) -> None:
        cycle: list[object] = []
        cycle.append(cycle)
        with self.assertRaisesRegex(CanonicalJsonError, "cyclic array"):
            canonical_json_bytes(cycle)

    def test_rejects_values_beyond_the_shared_nesting_limit(self) -> None:
        value: object = None
        for _ in range(MAX_NESTING_DEPTH + 1):
            value = [value]
        with self.assertRaisesRegex(CanonicalJsonError, "container nesting exceeds"):
            canonical_json_bytes(value)

        raw = (
            b"[" * (MAX_NESTING_DEPTH + 1)
            + b"null"
            + b"]" * (MAX_NESTING_DEPTH + 1)
        )
        with self.assertRaisesRegex(CanonicalJsonError, "container nesting exceeds"):
            parse_json_strict(raw)

    def test_strict_parser_rejects_duplicate_float_nonfinite_and_range(self) -> None:
        invalid = [
            b'{"a":1,"a":2}',
            b'{"value":1.0}',
            b'{"value":1e2}',
            b'{"value":NaN}',
            b'{"value":9223372036854775808}',
            b'{"value":"\\ud800"}',
            b'\xff',
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(CanonicalJsonError):
                    parse_json_strict(raw)

    def test_canonical_parser_rejects_alternate_valid_json_spellings(self) -> None:
        invalid = [
            b'{ "a": 1}',
            b'{"b":2,"a":1}',
            b'{"a":-0}',
            b'{"a":"\\n"}',
            b'{"a":"\\u0061"}',
            b'{"a":1}\n',
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(CanonicalJsonError):
                    parse_canonical_json(raw)
        canonical = b'{"a":"a","b":2}'
        self.assertEqual({"a": "a", "b": 2}, parse_canonical_json(canonical))

    def test_content_identity_is_domain_separated_and_omits_only_id(self) -> None:
        record = {
            "kind": "evidence-record",
            "format": "workbench-evidence-record-v2",
            "canonicalizer": CANONICALIZER_ID,
            "owner": "Crucible",
        }
        expected_digest = hashlib.sha256(
            b"workbench-content-v2\nevidence-record\n" + canonical_json_bytes(record)
        ).hexdigest()
        expected = f"evidence-record:sha256:{expected_digest}"
        self.assertEqual(expected, content_id("evidence-record", record))
        self.assertEqual(expected, record_content_id(record))
        sealed = {**record, "id": expected}
        self.assertEqual(expected, validate_content_id(sealed))
        self.assertEqual(expected, record_content_id({**sealed, "id": "wrong"}))
        with self.assertRaisesRegex(CanonicalJsonError, "mismatch"):
            validate_content_id({**sealed, "owner": "Atlas"})

    def test_record_identity_requires_declared_v2_domain(self) -> None:
        base = {
            "kind": "evidence-record",
            "format": "workbench-evidence-record-v2",
            "canonicalizer": CANONICALIZER_ID,
        }
        with self.assertRaises(CanonicalJsonError):
            record_content_id({**base, "kind": "Evidence_Record"})
        with self.assertRaises(CanonicalJsonError):
            record_content_id({**base, "canonicalizer": "legacy-json"})
        with self.assertRaises(CanonicalJsonError):
            record_content_id({**base, "format": ""})

    def test_default_json_serializer_is_not_an_identity_oracle(self) -> None:
        value = {"newline": "\n", "non_ascii": "☃"}
        default = json.dumps(value, sort_keys=True).encode("utf-8")
        self.assertNotEqual(default, canonical_json_bytes(value))


if __name__ == "__main__":
    unittest.main()
