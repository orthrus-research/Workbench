"""Source-custody helpers only; these tests make no native parity claim."""

from io import BytesIO
import copy
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from workbench_axiom.material_checks import (program_snapshot, intent, verify_native_sources, findings,
    source_acknowledgement, archive_inventory, project_source_comparison, saved_source_comparison)


class SavedMaterialInputTests(unittest.TestCase):
    def sources(self):
        return {"program/groovy/runConfig.json": b"{}", "program/groovy/material/A.groovy": b"// A\r\n",
                "program/groovy/postInit/Unsupported.groovy": b"// retained for native refusal\n",
                "unrelated.groovy": b"// outside explicit program"}

    def test_complete_deterministic_archive_never_filters_unsupported_loaders(self):
        sources = self.sources()
        raw, record = program_snapshot(sources, "program")
        self.assertEqual((raw, record), program_snapshot(dict(reversed(list(sources.items()))), "program"))
        with ZipFile(BytesIO(raw)) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            self.assertEqual(archive.read("groovy/material/A.groovy"), b"// A\r\n")
            self.assertIn("groovy/postInit/Unsupported.groovy", archive.namelist())
        self.assertEqual(record["paths"]["groovy/material/A.groovy"], "program/groovy/material/A.groovy")

    def test_program_requires_config_and_source_and_portable_explicit_root(self):
        for root in ("../program", "/program", "program/", "program\\other"):
            with self.subTest(root=root), self.assertRaises(ValueError):
                program_snapshot(self.sources(), root)
        with self.assertRaises(ValueError):
            program_snapshot({"groovy/A.groovy": b""}, ".")
        with self.assertRaises(ValueError):
            program_snapshot({"groovy/runConfig.json": b"{}"}, ".")

    def test_configuration_bytes_are_complete_deterministic_and_bound_to_saved_inputs(self):
        sources = {**self.sources(), "program/config/supercritical.cfg": b"B:disableAllMaterials=true\r\n",
                   "program/config/assets/binary.png": b"\x89PNG\xff\x00",
                   "program/config/helper.groovy": b"not a program script",
                   "config/outside.cfg": b"not selected", "program/configuration/other.cfg": b"not selected"}
        raw, record = program_snapshot(sources, "program")
        self.assertEqual((raw, record), program_snapshot(dict(reversed(list(sources.items()))), "program"))
        with ZipFile(BytesIO(raw)) as archive:
            self.assertEqual(6, len(archive.namelist()))
            for path in ("config/supercritical.cfg", "config/assets/binary.png", "config/helper.groovy"):
                self.assertEqual(sources["program/" + path], archive.read(path))
                self.assertEqual("program/" + path, record["paths"][path])
        sources["program/config/supercritical.cfg"] = b"B:disableAllMaterials=false\n"
        _, changed = program_snapshot(sources, "program")
        self.assertNotEqual(record["sha256"], changed["sha256"])
        sources.pop("program/config/supercritical.cfg")
        _, removed = program_snapshot(sources, "program")
        self.assertNotEqual(changed["sha256"], removed["sha256"])
        self.assertNotIn("config/supercritical.cfg", removed["paths"])

    def test_configuration_is_not_a_groovy_program_or_an_escape_from_input_bounds(self):
        with self.assertRaises(ValueError):
            program_snapshot({"groovy/runConfig.json": b"{}", "config/NotAScript.groovy": b"class Fake {}"}, ".")
        for path in ("config/../outside.cfg", "config/.git/config", "config//bad.cfg", "config/a\\b.cfg"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                program_snapshot({**self.sources(), "program/" + path: b"x"}, "program")
        for path, size in (("groovy/large.txt", 1024**2 + 1), ("config/large.bin", 4 * 1024**2 + 1)):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "bounds"):
                program_snapshot({**self.sources(), "program/" + path: b"x" * size}, "program")
        # Native config resources exceed the old per-script size without making
        # the permitted Groovy source program larger.
        program_snapshot({**self.sources(), "program/config/asset.bin": b"x" * (1024**2 + 1)}, "program")
        for extra in ({f"program/groovy/{i}.txt": b"x" for i in range(512)},
                      {f"program/config/{i}.cfg": b"x" for i in range(4096)},
                      {f"program/config/{i}.bin": b"x" * (4 * 1024**2) for i in range(6)}):
            with self.assertRaisesRegex(ValueError, "bounds"):
                program_snapshot({**self.sources(), **extra}, "program")

    def test_intent_cannot_replace_profile_policy_or_have_duplicate_fields(self):
        self.assertEqual(intent(b'{}'), {})
        self.assertEqual(intent(b'{"expectations":[]}'), {"expectations": []})
        for raw in (b'', b'[]', b'null', b'{"context":"fake"}', b'{"expectations":[],"expectations":[]}', b'{"expectations":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                intent(raw)

    def test_native_inventory_and_both_paired_observations_bind_exact_sources(self):
        _, program = program_snapshot(self.sources(), "program")
        response = {"status": "incomplete", "result": {"sourceProgram": source_acknowledgement(program)}}
        pair = {"status": "incomplete", "result": {"baseline": response, "candidate": response}}
        verify_native_sources(response, program)
        verify_native_sources(pair, program, program)
        response["result"]["sourceProgram"]["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "inventory"):
            verify_native_sources(pair, program, program)

    def test_acknowledgement_rejects_wrong_schema_count_types_scope_encoding_and_extra_fields(self):
        _, program = program_snapshot(self.sources(), "program")
        ack = source_acknowledgement(program)
        cases = [{**ack, key: value} for key, value in (("schema", "old"), ("sha256", "0" * 64),
            ("fileCount", True), ("fileCount", 3.0), ("fileCount", 2), ("fileCount", "3"),
            ("scope", "only-selected-files"), ("inventoryEncoding", "unknown"), ("files", program["files"]))]
        cases += [{key: value for key, value in ack.items() if key != missing} for missing in ack]
        cases.append({key: program[key] for key in ("sha256", "files")})
        for observed in cases:
            with self.subTest(observed=observed), self.assertRaisesRegex(ValueError, "inventory"):
                verify_native_sources({"status": "incomplete", "result": {"sourceProgram": observed}}, program)
        altered = copy.deepcopy(program)
        altered["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "inventory"):
            source_acknowledgement(altered)

    def test_missing_ack_is_allowed_only_for_explicit_failures_before_source_intake(self):
        _, program = program_snapshot(self.sources(), "program")
        for status in ("accepted", "rejected", "source-error", "unknown"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "custody"):
                verify_native_sources({"status": status, "result": {}}, program)
        for status in ("incomplete", "execution-error", "request-error", "requires-context", "unsupported"):
            verify_native_sources({"status": status, "result": {}}, program)
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "custody"):
                verify_native_sources({"status": status, "result": {"execution": {}}}, program)
        for side in ("baseline", "candidate"):
            with self.assertRaisesRegex(ValueError, "observations"):
                verify_native_sources({"status": "incomplete", "result": {side: {}}}, program, program)

    def test_archive_inventory_preserves_unicode_crlf_binary_and_unknown_loader_bytes(self):
        sources = {**self.sources(), 'program/config/😀é".cfg': b"binary\xff\r\n",
                   "program/config/\ue000.cfg": b"private unicode"}
        raw, program = program_snapshot(sources, "program")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program.zip"
            path.write_bytes(raw)
            observed = archive_inventory(path)
        self.assertEqual({key: program[key] for key in ("sha256", "files")}, observed)
        self.assertEqual(source_acknowledgement(program), source_acknowledgement(observed))
        self.assertLess([row["path"] for row in observed["files"]].index("config/\ue000.cfg"),
                        [row["path"] for row in observed["files"]].index('config/😀é".cfg'))
        rows = [{"path": path, "sha256": "1c3a649ff74e226cf2385db565d6071cb49f42e51c15b45404fb72556b88d74f", "size": 10}
                for path in ("groovy/é.groovy", "groovy/\ue000.groovy", 'groovy/😀".groovy')]
        self.assertEqual("ee083647789646428b3ac1ec1fc647ff9d43e40e4158db342b44720cdee44c4d",
                         source_acknowledgement({"files": rows,
                             "sha256": "ee083647789646428b3ac1ec1fc647ff9d43e40e4158db342b44720cdee44c4d"})["sha256"])

    def test_saved_pair_projection_preserves_native_descriptor_and_rejects_wrong_baseline_and_delta(self):
        _, before = program_snapshot(self.sources(), "program")
        sources = self.sources()
        sources.pop("program/groovy/postInit/Unsupported.groovy")
        sources.update({"program/groovy/material/A.groovy": b"changed", "program/config/added.cfg": b"new"})
        _, after = program_snapshot(sources, "program")
        native = {"status": "requires-retained-inventories", "baselineSourceSha256": before["sha256"],
                  "candidateSourceSha256": after["sha256"]}
        pair = {"status": "incomplete", "result": {"sourceComparison": native,
            "baseline": {"status": "incomplete", "result": {"sourceProgram": source_acknowledgement(before)}},
            "candidate": {"status": "incomplete", "result": {"sourceProgram": source_acknowledgement(after)}}}}
        with self.assertRaisesRegex(ValueError, "inventory"):
            verify_native_sources(pair, after, after)
        project_source_comparison(pair, after, before)
        body = pair["result"]
        self.assertEqual(native, body["nativeSourceComparison"])
        self.assertEqual(saved_source_comparison(after, before), body["sourceComparison"])
        self.assertEqual(["config/added.cfg"], body["sourceComparison"]["added"])
        self.assertEqual(["groovy/material/A.groovy"], body["sourceComparison"]["modified"])
        self.assertEqual(["groovy/postInit/Unsupported.groovy"], body["sourceComparison"]["removed"])
        self.assertEqual("core-verified-saved-inputs", body["sourceComparison"]["owner"])
        verify_native_sources(pair, after, before)
        body["sourceComparison"]["removed"] = []
        with self.assertRaisesRegex(ValueError, "comparison"):
            verify_native_sources(pair, after, before)

    def test_configuration_reference_resolves_only_to_complete_retained_program(self):
        _, program = program_snapshot(self.sources(), "program")
        reference = {"owner": "core-retained-material-program", "sourceProgramSha256": program["sha256"], "filesPointer": "/files"}
        response = {"status": "incomplete", "result": {"sourceProgram": source_acknowledgement(program),
            "sourceScope": {"configuration": {"fileCount": 0,
                "sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
                "inventoryReference": reference}}}}
        verify_native_sources(response, program)
        for key, wrong in (("owner", "native-response"), ("sourceProgramSha256", "0" * 64), ("filesPointer", "/sourceProgram/files")):
            invalid = copy.deepcopy(response)
            invalid["result"]["sourceScope"]["configuration"]["inventoryReference"][key] = wrong
            with self.assertRaisesRegex(ValueError, "reference"):
                verify_native_sources(invalid, program)

    def test_malformed_nested_native_evidence_fails_as_value_error_before_projection(self):
        _, program = program_snapshot(self.sources(), "program")
        valid = {"status": "incomplete", "result": {"sourceProgram": source_acknowledgement(program)}}
        for owner in ("sourceScope", "sourceAdmission", "execution", "bootstrap", "initialization",
                      "sourceComparison", "nativeSourceComparison", "comparison", "effectComparison", "sourceProgram"):
            for malformed in (None, [], "malformed"):
                invalid = copy.deepcopy(valid)
                invalid["result"][owner] = malformed
                with self.subTest(owner=owner, value=malformed), self.assertRaisesRegex(ValueError, "JSON object"):
                    verify_native_sources(invalid, program)
        for malformed in (None, [], "malformed"):
            invalid = copy.deepcopy(valid)
            invalid["result"]["sourceScope"] = {"configuration": malformed}
            with self.assertRaisesRegex(ValueError, "sourceScope.configuration.*JSON object"):
                verify_native_sources(invalid, program)
            with self.assertRaisesRegex(ValueError, "JSON object"):
                verify_native_sources({"status": "incomplete", "result": {"baseline": malformed, "candidate": valid}}, program, program)
        for owner, field in (("sourceAdmission", "findings"), ("execution", "diagnostics")):
            for malformed in (None, "bad", {}, ["bad"]):
                invalid = copy.deepcopy(valid)
                invalid["result"][owner] = {field: malformed}
                with self.subTest(owner=owner, field=field), self.assertRaisesRegex(ValueError, "must contain JSON objects"):
                    verify_native_sources(invalid, program)

    def test_findings_keep_unlocated_errors_and_exact_native_pointers(self):
        native = {"result": {"sourceAdmission": {"findings": [{"code": "source.syntax", "path": "groovy/A.groovy", "line": 2, "message": "syntax"}]},
                             "execution": {"diagnostics": [{"channel": "groovy-log", "severity": "ERROR", "message": "native error", "locations": []}]}}}
        result = findings({"result": {"baseline": native, "candidate": native}})
        self.assertEqual([row["side"] for row in result], ["baseline", "baseline", "candidate", "candidate"])
        self.assertEqual(result[0]["pointer"], "/result/baseline/result/sourceAdmission/findings/0")
        self.assertIsNone(result[-1]["nativeLocation"])

    def test_early_log4j_events_remain_unlocated_with_exact_side_pointers(self):
        diagnostic = {"severity": "warning", "logger": "FML", "message": "original native warning",
                      "trace": "NativeFailure\n at groovy.material.A.run(A.groovy:8)",
                      "locationStatus": "unlocated"}
        native = {"result": {"execution": {"diagnostics": [diagnostic]}}}
        pair = {"result": {"baseline": native, "candidate": copy.deepcopy(native)}}
        before = copy.deepcopy(pair)
        for side, row in zip(("baseline", "candidate"), findings(pair), strict=True):
            self.assertEqual("log4j", row["channel"])
            self.assertEqual("warning", row["severity"])
            self.assertEqual(diagnostic["message"], row["message"])
            self.assertIsNone(row["nativeLocation"])
            self.assertEqual(f"/result/{side}/result/execution/diagnostics/0", row["pointer"])
            self.assertNotIn("sourceRelationships", row)
        self.assertEqual(before, pair)
        del diagnostic["logger"]
        with self.assertRaisesRegex(ValueError, "no recognized channel"):
            findings(native)

    def test_shared_frames_keep_each_exception_and_logging_site_relationship(self):
        frame = {"path": "groovy/classes/A.groovy", "line": 8, "precision": "native-stack"}
        diagnostic = {"channel": "log4j", "severity": "error", "message": "wrapper log", "locations": [frame],
            "causality": {"root": 0, "exceptions": [
                {"type": "Wrapper", "message": "outer", "locations": [frame], "cause": 1, "suppressed": []},
                {"type": "NativeFailure", "message": None, "locations": [frame, frame], "cause": None, "suppressed": []}]},
            "observationLocations": [frame]}
        native = {"result": {"execution": {"diagnostics": [diagnostic]}}}
        result = findings({"result": {"baseline": native, "candidate": native}})
        self.assertEqual(2, len(result))  # navigation index stays unique, membership does not flatten
        for side, row in zip(("baseline", "candidate"), result):
            refs = row["sourceRelationships"]
            self.assertEqual(["exception-frame"] * 3 + ["observation-site"], [r["kind"] for r in refs])
            self.assertEqual([0, 1, 1], [r["exceptionIndex"] for r in refs[:3]])
            self.assertIsNone(refs[1]["message"])
            self.assertEqual(f"/result/{side}/result/execution/diagnostics/0/causality/exceptions/1/locations/1", refs[2]["locationPointer"])

    def test_compiler_location_is_linked_to_its_exception_not_a_guessed_stack_frame(self):
        location = {"path": "groovy/material/A.groovy", "line": 3, "column": 2, "precision": "native-compiler"}
        native = {"result": {"execution": {"diagnostics": [{"channel": "log4j", "severity": "error", "message": "compile",
            "locations": [location], "causality": {"exceptions": [{"type": "CompilerFailure", "message": "missing type", "locations": []}]},
            "compilerFindings": [{"exceptionIndex": 0, "message": "missing type", "location": location}], "observationLocations": []}]}}}
        row, = findings(native)
        self.assertEqual([{"kind": "compiler-finding", "exceptionIndex": 0,
            "exceptionPointer": "/result/execution/diagnostics/0/causality/exceptions/0",
            "locationPointer": "/result/execution/diagnostics/0/compilerFindings/0/location"}], row["sourceRelationships"])
