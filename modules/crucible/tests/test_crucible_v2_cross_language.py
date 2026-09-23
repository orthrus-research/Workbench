from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
CONFORMANCE = ROOT / "modules/crucible/conformance/canonical-v2"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_api.canonical import CANONICALIZER_ID, CONTENT_DOMAIN, MAX_NESTING_DEPTH, CanonicalJsonError, canonical_json_bytes, content_id, parse_canonical_json, parse_json_strict, record_content_id, validate_content_id


INVALID_ERROR_PATTERNS = {
    "content-id-mismatch": r"mismatch",
    "duplicate-key": r"duplicate object key",
    "floating-point-forbidden": r"floating-point",
    "integer-out-of-range": r"signed 64-bit",
    "invalid-number": r"invalid JSON",
    "invalid-utf8": r"valid UTF-8",
    "noncanonical-bytes": r"not canonical V2 JSON",
    "record-canonicalizer-invalid": r"canonicalizer must equal",
    "record-id-invalid": r"mismatch",
    "record-kind-invalid": r"kind must match",
    "trailing-data": r"invalid JSON",
    "unescaped-control": r"invalid JSON",
    "unexpected-token": r"non-finite",
    "unpaired-surrogate": r"surrogate code point",
}


def load_manifest(name: str) -> dict[str, object]:
    return json.loads((CONFORMANCE / name).read_text(encoding="utf-8"))


def vector_bytes(vector: dict[str, object]) -> bytes:
    has_json = "input_json" in vector
    has_hex = "input_utf8_hex" in vector
    if has_json == has_hex:
        raise AssertionError(
            f"{vector.get('name')}: exactly one input_json or input_utf8_hex is required"
        )
    if has_json:
        value = vector["input_json"]
        if type(value) is not str:
            raise AssertionError(f"{vector.get('name')}: input_json must be a string")
        return value.encode("utf-8")
    value = vector["input_utf8_hex"]
    if type(value) is not str:
        raise AssertionError(f"{vector.get('name')}: input_utf8_hex must be a string")
    return bytes.fromhex(value)


class CrucibleV2CrossLanguageConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.valid_manifest = load_manifest("valid-vectors.json")
        cls.invalid_manifest = load_manifest("invalid-vectors.json")

    def test_python_production_api_matches_every_valid_vector(self) -> None:
        manifest = self.valid_manifest
        self.assertEqual(
            "workbench-canonical-json-v2-valid-vectors-v1",
            manifest["format"],
        )
        self.assertEqual(CANONICALIZER_ID, manifest["canonicalizer"])
        self.assertEqual(MAX_NESTING_DEPTH, manifest["max_nesting_depth"])
        self.assertEqual(CONTENT_DOMAIN.decode("utf-8"), manifest["content_domain_utf8"])
        self.assertEqual(CONTENT_DOMAIN.hex(), manifest["content_domain_utf8_hex"])

        vectors = manifest["vectors"]
        self.assertIs(type(vectors), list)
        self.assertGreater(len(vectors), 0)
        seen: set[str] = set()
        for vector in vectors:
            name = vector["name"]
            with self.subTest(vector=name):
                self.assertIs(type(vector), dict)
                self.assertIs(type(name), str)
                self.assertNotIn(name, seen)
                seen.add(name)

                record = parse_json_strict(vector_bytes(vector))
                self.assertIs(type(record), dict)
                body = dict(record)
                body.pop("id")

                expected_bytes = bytes.fromhex(
                    vector["expected_canonical_body_utf8_hex"]
                )
                self.assertEqual(expected_bytes, canonical_json_bytes(body))
                self.assertEqual(body, parse_canonical_json(expected_bytes))

                expected_body_digest = hashlib.sha256(expected_bytes).hexdigest()
                self.assertEqual(
                    vector["expected_canonical_body_sha256"],
                    expected_body_digest,
                )

                kind = record["kind"]
                expected_digest = hashlib.sha256(
                    CONTENT_DOMAIN
                    + kind.encode("utf-8")
                    + b"\n"
                    + expected_bytes
                ).hexdigest()
                expected_content_id = f"{kind}:sha256:{expected_digest}"
                self.assertEqual(vector["expected_content_id"], expected_content_id)
                self.assertEqual(record["id"], expected_content_id)
                self.assertNotEqual(expected_body_digest, expected_digest)
                self.assertEqual(expected_content_id, content_id(kind, body))
                self.assertEqual(expected_content_id, record_content_id(record))
                self.assertEqual(expected_content_id, validate_content_id(record))

        self.assertEqual(len(vectors), len(seen))

    def test_python_production_api_rejects_every_invalid_vector(self) -> None:
        manifest = self.invalid_manifest
        self.assertEqual(
            "workbench-canonical-json-v2-invalid-vectors-v1",
            manifest["format"],
        )
        self.assertEqual(CANONICALIZER_ID, manifest["canonicalizer"])
        self.assertEqual(MAX_NESTING_DEPTH, manifest["max_nesting_depth"])

        vectors = manifest["vectors"]
        self.assertIs(type(vectors), list)
        self.assertGreater(len(vectors), 0)
        seen: set[str] = set()
        for vector in vectors:
            name = vector["name"]
            with self.subTest(vector=name):
                self.assertIs(type(vector), dict)
                self.assertIs(type(name), str)
                self.assertNotIn(name, seen)
                seen.add(name)
                expected_error = vector["expected_error"]
                self.assertIn(expected_error, INVALID_ERROR_PATTERNS)
                pattern = INVALID_ERROR_PATTERNS[expected_error]
                raw = vector_bytes(vector)
                operation = vector.get("operation", "parse-and-identify-record")

                with self.assertRaisesRegex(CanonicalJsonError, pattern):
                    if operation == "validate-canonical-bytes":
                        parse_canonical_json(raw)
                    elif operation == "parse-and-identify-record":
                        record = parse_json_strict(raw)
                        self.assertIs(type(record), dict)
                        validate_content_id(record)
                    else:
                        self.fail(f"{name}: unknown invalid-vector operation {operation!r}")

        self.assertEqual(len(vectors), len(seen))

    def test_node_verifier_is_a_required_second_implementation(self) -> None:
        command = ["node", str(CONFORMANCE / "verify.mjs")]
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            self.fail(f"required Node.js runtime is unavailable: {exc}")

        self.assertEqual(
            0,
            completed.returncode,
            f"Node verifier failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        expected = (
            "canonical-v2 conformance passed: "
            f"{len(self.valid_manifest['vectors'])} valid vectors, "
            f"{len(self.invalid_manifest['vectors'])} invalid vectors\n"
        )
        self.assertEqual(expected, completed.stdout)
        self.assertEqual("", completed.stderr)

    def test_node_api_hardening_self_tests_are_required(self) -> None:
        module_url = (CONFORMANCE / "verify.mjs").resolve().as_uri()
        script = (
            f"import {{ verifyApiHardening }} from {json.dumps(module_url)};"
            "process.stdout.write(String(verifyApiHardening()));"
        )
        try:
            completed = subprocess.run(
                ["node", "--input-type=module", "--eval", script],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            self.fail(f"required Node.js runtime is unavailable: {exc}")

        self.assertEqual(
            0,
            completed.returncode,
            f"Node hardening tests failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertEqual("21", completed.stdout)
        self.assertEqual("", completed.stderr)

    def test_encoder_rejects_subclasses_of_supported_builtins(self) -> None:
        class IntegerSubclass(int):
            pass

        class StringSubclass(str):
            pass

        class ArraySubclass(list[object]):
            pass

        class ObjectSubclass(dict[str, object]):
            pass

        values = [
            IntegerSubclass(1),
            StringSubclass("text"),
            ArraySubclass(),
            ObjectSubclass(),
        ]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(CanonicalJsonError, "unsupported semantic type"):
                    canonical_json_bytes(value)

        record = ObjectSubclass(
            kind="canonical-vector",
            format="workbench-canonical-vector-v1",
            canonicalizer=CANONICALIZER_ID,
        )
        with self.assertRaisesRegex(CanonicalJsonError, "ordinary object"):
            record_content_id(record)


if __name__ == "__main__":
    unittest.main()
