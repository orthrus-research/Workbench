#!/usr/bin/env python3

"""Focused S01 tests for strict standard compilation and admission."""

from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
import yaml

from _support import (
    CONTRACT_ROOT,
    EXAMPLE_ROOT,
    FIXTURE_ROOT,
    LEDGER_PATH,
    SCHEMA_ROOT,
    SOURCE_ROOT,
    WORKBENCH_ROOT,
)

REPO_ROOT = WORKBENCH_ROOT
BLUEPRINTS_TOOLS = SOURCE_ROOT
STANDARD_SCHEMA = SCHEMA_ROOT / "blueprints-standard-v1.schema.json"
REGISTRY_SCHEMA = (
    SCHEMA_ROOT / "blueprints-standard-registry-v1.schema.json"
)
LEDGER_SCHEMA = SCHEMA_ROOT / "blueprints-allocation-ledger-v1.schema.json"
LEDGER = LEDGER_PATH
CONTRACT = CONTRACT_ROOT / "implementation-standard-v1.md"
EXAMPLES = EXAMPLE_ROOT / "implementation-standard-examples-v1.json"

if str(BLUEPRINTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(BLUEPRINTS_TOOLS))

from workbench_blueprints import standards  # noqa: E402


class SchemaFreshnessTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "schema.json"
        self.value = {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        self.path.write_text(json.dumps(self.value), encoding="utf-8")

    def assertDiagnostic(self, code: str, action: Callable[[], Any]) -> standards.StandardDiagnostic:
        with self.assertRaises(standards.StandardDiagnostic) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_same_size_and_mtime_schema_replacement_is_observed(self) -> None:
        standards._validate_schema({"value": 1}, self.path, "instance")
        self.assertDiagnostic(
            "BPS200_SCHEMA",
            lambda: standards._validate_schema({"value": True}, self.path, "instance"),
        )
        before = self.path.stat()
        self.value["properties"]["value"]["type"] = "boolean"
        self.path.write_text(json.dumps(self.value), encoding="utf-8")
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(before.st_size, self.path.stat().st_size)
        standards._validate_schema({"value": True}, self.path, "instance")
        diagnostic = self.assertDiagnostic(
            "BPS200_SCHEMA",
            lambda: standards._validate_schema({"value": 1}, self.path, "instance"),
        )
        self.assertEqual("instance#/value", diagnostic.location)

    def test_returned_schema_mutation_cannot_change_later_validation(self) -> None:
        loaded = standards._schema(self.path)
        loaded["properties"]["value"]["type"] = "string"
        loaded["required"].clear()
        self.assertEqual(self.value, standards._schema(self.path))
        standards._validate_schema({"value": 1}, self.path, "instance")
        self.assertDiagnostic(
            "BPS200_SCHEMA",
            lambda: standards._validate_schema({}, self.path, "instance"),
        )

    def test_warmed_schema_does_not_hide_invalid_source_or_schema_errors(self) -> None:
        for raw, code in (
            (b'{"type":"object","type":"string"}', "BPS107_DUPLICATE_JSON_KEY"),
            (b"[]", "BPS106_JSON_ROOT"),
            (b"{", "BPS105_JSON_READ"),
            (b"\xff", "BPS105_JSON_READ"),
            (b'{"type":"not-a-json-type"}', "BPS108_INVALID_SCHEMA"),
        ):
            with self.subTest(code=code):
                self.path.write_text(json.dumps(self.value), encoding="utf-8")
                standards._schema(self.path)
                self.path.write_bytes(raw)
                diagnostic = self.assertDiagnostic(code, lambda: standards._schema(self.path))
                if code != "BPS107_DUPLICATE_JSON_KEY":
                    self.assertEqual(str(self.path), diagnostic.location)

    def test_warmed_schema_still_requires_readable_source(self) -> None:
        standards._schema(self.path)
        self.path.unlink()
        self.assertDiagnostic("BPS105_JSON_READ", lambda: standards._schema(self.path))
        self.path.write_text(json.dumps(self.value), encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            diagnostic = self.assertDiagnostic("BPS105_JSON_READ", lambda: standards._schema(self.path))
        self.assertEqual(str(self.path), diagnostic.location)

    def test_invalid_schema_diagnostic_matches_the_current_validator(self) -> None:
        value = {"type": "invalid", "required": 12, "properties": []}
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(standards.SchemaError) as original:
            Draft202012Validator.check_schema(value)
        diagnostic = self.assertDiagnostic("BPS108_INVALID_SCHEMA", lambda: standards._schema(self.path))
        self.assertEqual(original.exception.message, diagnostic.message)


class StandardCompilerTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("material-backed-fluid.yaml", "localization-component.yaml"):
            shutil.copyfile(FIXTURE_ROOT / name, self.root / name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(self, name: str = "material-backed-fluid.yaml") -> dict[str, Any]:
        value = yaml.safe_load((self.root / name).read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def _write(
        self,
        value: dict[str, Any],
        name: str = "material-backed-fluid.yaml",
    ) -> None:
        (self.root / name).write_text(
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _diagnostic(
        self,
        action: Callable[[], Any],
        code: str,
    ) -> standards.StandardDiagnostic:
        with self.assertRaises(standards.StandardDiagnostic) as context:
            action()
        self.assertEqual(context.exception.code, code)
        return context.exception

    def test_schema_definitions_and_contract_are_present(self) -> None:
        for path in (STANDARD_SCHEMA, REGISTRY_SCHEMA, LEDGER_SCHEMA):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        contract = CONTRACT.read_text(encoding="utf-8")
        for phrase in (
            "Directory placement is the human approval act",
            "apparent naming conventions cannot serve as allocation authority",
            "never a developer’s live world",
            "changed-in-place",
            "blueprints-ledger",
        ):
            self.assertIn(phrase, contract)
        ledger = standards.validate_allocation_ledger(
            LEDGER,
            registry_root=self.root,
            asset_root=REPO_ROOT,
        )
        self.assertEqual(ledger["generation"], 0)
        self.assertEqual(ledger["reservations"], [])

    def test_fixture_manifest_is_exact_and_non_authoritative(self) -> None:
        examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))
        self.assertEqual(examples["fixture_authority"], "synthetic-non-admitted")
        registry = standards.compile_registry(self.root, asset_root=REPO_ROOT)
        self.assertEqual(
            registry["registry_id"], examples["synthetic_registry_id"]
        )
        expected = {
            Path(row["path"]).name: row for row in examples["sources"]
        }
        for entry in registry["standards"]:
            fixture = expected[entry["source_path"]]
            for field in (
                "source_sha256",
                "standard_id",
                "standard_sha256",
            ):
                self.assertEqual(entry[field], fixture[field])
        codes = {
            row["diagnostic"] for row in examples["fail_closed_cases"]
        }
        self.assertGreaterEqual(len(codes), 12)

    def test_compile_is_canonical_and_identity_is_recomputable(self) -> None:
        self.assertEqual(
            standards.canonical_json([1.0, 0.00012, 0.0000001]),
            "[1,1.2e-4,1e-7]",
        )
        source = self.root / "material-backed-fluid.yaml"
        first, _source_bytes = standards.compile_file(
            source, registry_root=self.root, asset_root=REPO_ROOT
        )
        record = self._record()
        record["parameters"].reverse()
        record["targets"][0]["allowed_paths"].reverse()
        record["allocation"]["domains"][0]["evidence"].reverse()
        self._write(record)
        second, _ = standards.compile_file(
            source, registry_root=self.root, asset_root=REPO_ROOT
        )
        self.assertEqual(standards.canonical_json(first), standards.canonical_json(second))
        self.assertEqual(
            [row["name"] for row in second["parameters"]],
            sorted(row["name"] for row in second["parameters"]),
        )
        self.assertEqual(
            second["format"], standards.COMPILED_FORMAT
        )
        digest = hashlib.sha256(
            standards.canonical_json(second).encode("utf-8")
        ).hexdigest()
        registry = standards.compile_registry(
            self.root, asset_root=REPO_ROOT
        )
        entry = next(
            row
            for row in registry["standards"]
            if row["standard_key"] == "material-backed-fluid"
        )
        self.assertEqual(entry["standard_sha256"], digest)
        self.assertEqual(
            entry["standard_id"], "blueprints-standard:sha256:" + digest
        )
        output = io.StringIO()
        with redirect_stdout(output):
            return_code = standards.main(
                [
                    "compile",
                    str(source),
                    "--registry-root",
                    str(self.root),
                    "--asset-root",
                    str(REPO_ROOT),
                ]
            )
        self.assertEqual(return_code, 0)
        self.assertEqual(output.getvalue(), standards.canonical_json(second))

    def test_registry_build_check_and_component_resolution(self) -> None:
        built = standards.build_registry(self.root, asset_root=REPO_ROOT)
        self.assertEqual(len(built["standards"]), 2)
        checked = standards.check_registry(self.root, asset_root=REPO_ROOT)
        self.assertEqual(checked, built)
        lock_bytes = (self.root / "registry.json").read_bytes()
        self.assertFalse(lock_bytes.endswith(b"\n"))
        self.assertEqual(
            lock_bytes.decode("utf-8"), standards.canonical_json(built)
        )
        projected = copy.deepcopy(built)
        identity = projected.pop("registry_id")
        self.assertEqual(
            identity,
            "blueprints-standard-registry:sha256:"
            + hashlib.sha256(
                standards.canonical_json(projected).encode("utf-8")
            ).hexdigest(),
        )

    def test_yaml_ambiguity_and_closed_schema_fail(self) -> None:
        source = self.root / "material-backed-fluid.yaml"
        original = source.read_text(encoding="utf-8")
        source.write_text(original + "\nstandard_key: duplicate\n", encoding="utf-8")
        self._diagnostic(
            lambda: standards.compile_file(
                source, registry_root=self.root, asset_root=REPO_ROOT
            ),
            "BPS104_DUPLICATE_KEY",
        )

        source.write_text(
            original.replace(
                "priority: 100", "priority: &shared 100\nspecificity: *shared"
            ).replace("specificity: 50\n", ""),
            encoding="utf-8",
        )
        self._diagnostic(
            lambda: standards.compile_file(
                source, registry_root=self.root, asset_root=REPO_ROOT
            ),
            "BPS102_YAML_ANCHOR",
        )

        record = yaml.safe_load(original)
        record["unapproved"] = True
        self._write(record)
        self._diagnostic(
            lambda: standards.compile_file(
                source, registry_root=self.root, asset_root=REPO_ROOT
            ),
            "BPS200_SCHEMA",
        )

    def test_paths_assets_parameters_and_allocation_fail_closed(self) -> None:
        mutations: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
            (
                "path traversal",
                lambda record: record["targets"][0]["allowed_paths"].append("../escape/"),
                "BPS202_UNSAFE_PATH",
            ),
            (
                "asset drift",
                lambda record: record["mandatory_core"].update(
                    {"sha256": "0" * 64}
                ),
                "BPS213_ASSET_DIGEST",
            ),
            (
                "class conflict",
                lambda record: record["parameters"][0].update({"default": "x"}),
                "BPS217_PARAMETER_CLASS",
            ),
            (
                "unknown allocation",
                lambda record: next(
                    row
                    for row in record["parameters"]
                    if row["name"] == "material_id"
                ).update({"allocation_domain": "missing-domain"}),
                "BPS231_ALLOCATION_DOMAIN",
            ),
            (
                "source gap allocation",
                lambda record: record["allocation"]["domains"][0].update(
                    {"mode": "existing-authority"}
                ),
                "BPS233_ALLOCATION_POOL",
            ),
            (
                "undeclared render target",
                lambda record: record["rendering"]["outputs"][0].update(
                    {"repository_id": "missing"}
                ),
                "BPS243_RENDER_TARGET",
            ),
            (
                "render operation missing source",
                lambda record: record["rendering"]["outputs"][0].update(
                    {"source": None}
                ),
                "BPS248_RENDER_SOURCE",
            ),
            (
                "invalid Atlas invariant expectation",
                lambda record: record["atlas"]["queries"][0]["invariants"][
                    0
                ].update({"operator": "exists", "expected": "yes"}),
                "BPS249_INVARIANT_EXPECTED",
            ),
        ]
        pristine = self._record()
        for label, mutate, code in mutations:
            with self.subTest(label=label):
                record = copy.deepcopy(pristine)
                mutate(record)
                self._write(record)
                self._diagnostic(
                    lambda: standards.compile_file(
                        self.root / "material-backed-fluid.yaml",
                        registry_root=self.root,
                        asset_root=REPO_ROOT,
                    ),
                    code,
                )

    def test_registry_drift_removal_and_component_fail_closed(self) -> None:
        standards.build_registry(self.root, asset_root=REPO_ROOT)
        record = self._record()
        record["priority"] += 1
        self._write(record)
        self._diagnostic(
            lambda: standards.check_registry(self.root, asset_root=REPO_ROOT),
            "BPS311_REGISTRY_STALE",
        )
        self._diagnostic(
            lambda: standards.build_registry(self.root, asset_root=REPO_ROOT),
            "BPS313_IMMUTABLE_VERSION",
        )

        self._write(self._record("localization-component.yaml"))
        (self.root / "material-backed-fluid.yaml").unlink()
        self._diagnostic(
            lambda: standards.build_registry(self.root, asset_root=REPO_ROOT),
            "BPS312_IMMUTABLE_REMOVAL",
        )

        shutil.copyfile(
            FIXTURE_ROOT / "material-backed-fluid.yaml",
            self.root / "material-backed-fluid.yaml",
        )
        (self.root / "localization-component.yaml").unlink()
        self._diagnostic(
            lambda: standards.compile_registry(self.root, asset_root=REPO_ROOT),
            "BPS307_UNADMITTED_COMPONENT",
        )

    def test_reservation_ledger_binds_domain_and_never_reuses_value(self) -> None:
        registry = standards.compile_registry(self.root, asset_root=REPO_ROOT)
        primary = next(
            row
            for row in registry["standards"]
            if row["standard_key"] == "material-backed-fluid"
        )
        reservation = {
            "domain_name": "susy-material-id",
            "value": 9000,
            "status": "active",
            "feature_identity": "susy:synthetic_material",
            "standard_id": primary["standard_id"],
            "request_id": "blueprints-request:sha256:" + "1" * 64,
            "release_id": "blueprints-release:sha256:" + "2" * 64,
            "retired_by_release_id": None,
        }
        reservation["reservation_id"] = (
            "blueprints-allocation-reservation:sha256:"
            + hashlib.sha256(
                standards.canonical_json(reservation).encode("utf-8")
            ).hexdigest()
        )
        ledger = {
            "schema_version": 1,
            "format": "susy-blueprints-allocation-ledger-v1",
            "contract_id": standards.CONTRACT_ID,
            "generation": 1,
            "parent_ledger_id": json.loads(
                LEDGER.read_text(encoding="utf-8")
            )["ledger_id"],
            "reservations": [reservation],
        }
        ledger["ledger_id"] = (
            "blueprints-allocation-ledger:sha256:"
            + hashlib.sha256(
                standards.canonical_json(ledger).encode("utf-8")
            ).hexdigest()
        )
        ledger_path = self.root / "ledger.json"
        ledger_path.write_text(
            standards.canonical_json(ledger), encoding="utf-8"
        )
        self.assertEqual(
            standards.validate_allocation_ledger(
                ledger_path,
                registry_root=self.root,
                asset_root=REPO_ROOT,
                registry=registry,
            ),
            ledger,
        )
        initial = json.loads(LEDGER.read_text(encoding="utf-8"))
        standards.validate_ledger_transition(initial, ledger)
        removed = copy.deepcopy(ledger)
        removed["generation"] = 2
        removed["parent_ledger_id"] = ledger["ledger_id"]
        removed["reservations"] = []
        self._diagnostic(
            lambda: standards.validate_ledger_transition(ledger, removed),
            "BPS328_RESERVATION_REMOVAL",
        )

        duplicate = copy.deepcopy(reservation)
        duplicate["feature_identity"] = "susy:other_material"
        duplicate.pop("reservation_id")
        duplicate["reservation_id"] = (
            "blueprints-allocation-reservation:sha256:"
            + hashlib.sha256(
                standards.canonical_json(duplicate).encode("utf-8")
            ).hexdigest()
        )
        ledger["reservations"].append(duplicate)
        ledger["reservations"].sort(
            key=lambda row: (
                row["domain_name"],
                row["value"],
                row["reservation_id"],
            )
        )
        ledger.pop("ledger_id")
        ledger["ledger_id"] = (
            "blueprints-allocation-ledger:sha256:"
            + hashlib.sha256(
                standards.canonical_json(ledger).encode("utf-8")
            ).hexdigest()
        )
        ledger_path.write_text(
            standards.canonical_json(ledger), encoding="utf-8"
        )
        self._diagnostic(
            lambda: standards.validate_allocation_ledger(
                ledger_path,
                registry_root=self.root,
                asset_root=REPO_ROOT,
                registry=registry,
            ),
            "BPS320_ALLOCATION_REUSE",
        )

    def test_unadmitted_and_unrecognized_files_fail(self) -> None:
        outside = Path(self.temporary.name).parent / "outside-standard.yaml"
        shutil.copyfile(FIXTURE_ROOT / "material-backed-fluid.yaml", outside)
        try:
            self._diagnostic(
                lambda: standards.compile_file(
                    outside, registry_root=self.root, asset_root=REPO_ROOT
                ),
                "BPS302_SOURCE_OUTSIDE_REGISTRY",
            )
        finally:
            outside.unlink(missing_ok=True)

        (self.root / "alias.yml").write_text("not: admitted\n", encoding="utf-8")
        self._diagnostic(
            lambda: standards.compile_registry(self.root, asset_root=REPO_ROOT),
            "BPS305_REGISTRY_FILE",
        )


if __name__ == "__main__":
    unittest.main()
