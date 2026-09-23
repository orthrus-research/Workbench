"""Snapshot identity, declared coverage and reader/cursor boundary regressions."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from workbench_core import check_snapshot_contract as contract


def digest(label):
    return sha256(label.encode()).hexdigest()


def fixture():
    scope = {"name": "fixture-native-scope", "sections": {"recipes": True, "values": True}}
    blob = {"sha256": digest("fixture bytes"), "bytes": 13, "media_type": "application/json"}
    sections = [{"id": name, "schema": "fixture-" + name + "-v1", "required": True,
                 "state": "observed", "count": 1, "content": deepcopy(blob),
                 "dependencies": ["values"] if name == "recipes" else [], "reason": None}
                for name in scope["sections"]]
    body = {"format": "workbench-check-snapshot-v1", "schema_version": 1,
            "attempt_id": "fixture-attempt", "request_id": "fixture-request", "result_id": "fixture-result",
            "producer": {"id": "fixture", "build_sha256": digest("producer")},
            "bindings": {key: digest(key) for key in ("source", "configuration", "context", "engine", "runtime", "jvm")},
            "scope_id": contract.scope_identity(scope), "native_outcome": "native-failed",
            "coverage": "complete", "source_result": deepcopy(blob),
            "retained_inputs": [{"role": "saved-source", "content": deepcopy(blob)}], "sections": sections}
    return body, scope


def query(snapshot, operation="records"):
    return {"format": "workbench-check-snapshot-query-v1", "snapshot_id": snapshot["id"],
            "view_id": "view-generation-1", "operation": operation, "section_id": "recipes",
            "record_key": None, "blob_sha256": None, "preferred_bytes": 65536, "cursor": None}


class CheckSnapshotContractTests(unittest.TestCase):
    def setUp(self):
        self.body, self.scope = fixture()
        self.snapshot = contract.seal_snapshot(self.body, self.scope)

    def test_versioned_document_examples_are_executable_contracts(self):
        root = Path(__file__).parent / "fixtures/check-snapshot-v1"
        values = {name: json.loads((root / (name + ".json")).read_text())
                  for name in ("scope", "manifest", "query", "response")}
        contract.validate_snapshot(values["manifest"], values["scope"])
        contract.validate_query(values["query"], values["manifest"])
        contract.validate_response(values["response"], values["query"], values["manifest"])

    def test_complete_observation_preserves_native_failure_and_does_not_mutate_inputs(self):
        before = deepcopy(self.body)
        result = contract.seal_snapshot(self.body, self.scope)
        self.assertEqual(result["native_outcome"], "native-failed")
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(self.body, before)
        self.assertNotIn("id", self.body)

    def test_sealed_metadata_is_detached_from_mutable_builder_values(self):
        before = deepcopy(self.snapshot)
        self.body["sections"][0]["dependencies"].clear()
        self.body["bindings"]["context"] = digest("new context")
        self.assertEqual(self.snapshot, before)
        contract.validate_snapshot(self.snapshot, self.scope)

    def test_mutated_identity_and_resealed_missing_required_section_are_both_rejected(self):
        self.snapshot["native_outcome"] = "completed"
        with self.assertRaisesRegex(contract.SnapshotContractError, "identity"):
            contract.validate_snapshot(self.snapshot, self.scope)
        self.body["sections"].pop()
        with self.assertRaisesRegex(contract.SnapshotContractError, "selected scope"):
            contract.seal_snapshot(self.body, self.scope)

    def test_producer_cannot_downgrade_required_section_or_substitute_scope(self):
        self.body["sections"][1]["required"] = False
        with self.assertRaisesRegex(contract.SnapshotContractError, "requirement"):
            contract.seal_snapshot(self.body, self.scope)
        other = {"name": "different-pack", "sections": dict(self.scope["sections"])}
        with self.assertRaisesRegex(contract.SnapshotContractError, "selected scope"):
            contract.validate_snapshot(self.snapshot, other)

    def test_missing_values_cannot_leave_consuming_recipes_observed(self):
        self.body["coverage"] = "incomplete"
        self.body["sections"][1].update(state="unavailable", count=None, content=None, reason="observer failed")
        with self.assertRaisesRegex(contract.SnapshotContractError, "depends on unavailable"):
            contract.seal_snapshot(self.body, self.scope)
        self.body["sections"][0].update(state="incomplete", reason="required values unavailable")
        result = contract.seal_snapshot(self.body, self.scope)
        self.assertEqual(result["coverage"], "incomplete")
        self.assertEqual(result["native_outcome"], "native-failed")

    def test_partial_records_cannot_be_resealed_as_complete_coverage(self):
        self.body["sections"][0].update(state="incomplete", reason="interrupted observation")
        with self.assertRaisesRegex(contract.SnapshotContractError, "coverage"):
            contract.seal_snapshot(self.body, self.scope)

    def test_missing_payload_never_means_empty(self):
        self.body["sections"][0].update(content=None, count=0)
        with self.assertRaisesRegex(contract.SnapshotContractError, "requires content"):
            contract.seal_snapshot(self.body, self.scope)

    def test_profile_can_explicitly_retire_an_optional_section(self):
        self.scope["sections"]["retired"] = False
        self.body["scope_id"] = contract.scope_identity(self.scope)
        self.body["sections"].insert(1, {"id": "retired", "schema": "retired-v1", "required": False,
                                      "state": "not-applicable", "content": None, "count": None,
                                      "reason": "absent from selected profile scope", "dependencies": []})
        value = contract.seal_snapshot(self.body, self.scope)
        coverage = contract.reader_coverage(value, {"fixture-recipes-v1", "fixture-values-v1"})
        self.assertTrue(coverage["required_interpretation_complete"])
        self.assertEqual(coverage["unsupported_sections"], [])

    def test_unknown_value_schema_blocks_transitive_interpretation_without_rewriting_result(self):
        before = deepcopy(self.snapshot)
        coverage = contract.reader_coverage(self.snapshot, {"fixture-recipes-v1"})
        self.assertEqual(coverage["affected_sections"], ["recipes", "values"])
        self.assertFalse(coverage["required_interpretation_complete"])
        self.assertEqual(self.snapshot, before)

    def test_pack_and_producer_changes_create_new_run_identity_with_same_content(self):
        original = self.snapshot["sections"]
        for change in ("context", "producer"):
            body = deepcopy(self.body)
            if change == "producer":
                body["producer"]["build_sha256"] = digest("new observer")
            else:
                body["bindings"]["context"] = digest("new pack")
            changed = contract.seal_snapshot(body, self.scope)
            self.assertNotEqual(changed["id"], self.snapshot["id"])
            self.assertEqual(changed["sections"], original)

    def test_duplicate_and_unresolved_section_metadata_are_rejected(self):
        for mutate in (lambda b: b["sections"].append(deepcopy(b["sections"][0])),
                       lambda b: b["sections"][0]["dependencies"].append("missing")):
            body = deepcopy(self.body)
            mutate(body)
            with self.assertRaises(contract.SnapshotContractError):
                contract.seal_snapshot(body, self.scope)

    def test_cursor_cannot_cross_view_snapshot_or_query(self):
        value = query(self.snapshot)
        value["cursor"] = {"snapshot_id": self.snapshot["id"], "query_id": contract.query_identity(value), "offset": 16}
        contract.validate_query(value, self.snapshot)
        for key, replacement in (("view_id", "new-view"), ("section_id", "values"), ("preferred_bytes", 1024)):
            changed = deepcopy(value)
            changed[key] = replacement
            with self.assertRaisesRegex(contract.SnapshotContractError, "cursor"):
                contract.validate_query(changed, self.snapshot)
        value["snapshot_id"] = "check-snapshot:sha256:" + digest("other snapshot")
        with self.assertRaisesRegex(contract.SnapshotContractError, "another snapshot"):
            contract.validate_query(value, self.snapshot)

    def test_blob_reads_require_section_record_and_expected_digest(self):
        value = query(self.snapshot, "blob")
        value.update(record_key="example:recipe/with~characters", blob_sha256=digest("large record"))
        contract.validate_query(value, self.snapshot)
        value["section_id"] = None
        with self.assertRaisesRegex(contract.SnapshotContractError, "arguments"):
            contract.validate_query(value, self.snapshot)

    def test_query_rejects_negative_offsets_unknown_sections_and_shape_changes(self):
        value = query(self.snapshot)
        cursor = {"snapshot_id": self.snapshot["id"], "query_id": contract.query_identity(value), "offset": -1}
        for patch in ({"cursor": cursor}, {"section_id": "missing"}, {"filesystem_path": "/tmp/input"},
                      {"operation": "delete"}, {"preferred_bytes": True}):
            with self.assertRaises(contract.SnapshotContractError):
                contract.validate_query({**value, **patch}, self.snapshot)

    def test_summary_has_no_section_or_cursor_and_single_record_has_no_cursor(self):
        value = query(self.snapshot, "summary")
        value["section_id"] = None
        contract.validate_query(value, self.snapshot)
        value["cursor"] = {"snapshot_id": self.snapshot["id"], "query_id": contract.query_identity(value), "offset": 0}
        with self.assertRaisesRegex(contract.SnapshotContractError, "pagination"):
            contract.validate_query(value, self.snapshot)

    def test_reply_generation_and_progress_are_bound_to_the_original_query(self):
        request = query(self.snapshot)
        identity = contract.query_identity(request)
        response = {"format": "workbench-check-snapshot-response-v1", "snapshot_id": self.snapshot["id"],
                    "query_id": identity, "request_id": contract.read_identity(request),
                    "view_id": request["view_id"], "state": "ready",
                    "payload": {"records": [{"key": "recipe", "value": {}}], "total": 2},
                    "complete": False, "next_cursor": {"snapshot_id": self.snapshot["id"],
                    "query_id": identity, "offset": 1}, "reason": None}
        contract.validate_response(response, request, self.snapshot)
        later_page = {**request, "cursor": deepcopy(response["next_cursor"])}
        with self.assertRaisesRegex(contract.SnapshotContractError, "another snapshot, view or query"):
            contract.validate_response(response, later_page, self.snapshot)
        for patch in ({"view_id": "new-view"}, {"complete": True},
                      {"next_cursor": {**response["next_cursor"], "offset": 0}}):
            with self.assertRaises(contract.SnapshotContractError):
                contract.validate_response({**response, **patch}, request, self.snapshot)

    def test_expired_or_cancelled_reply_is_not_an_empty_success(self):
        request = query(self.snapshot)
        response = {"format": "workbench-check-snapshot-response-v1", "snapshot_id": self.snapshot["id"],
                    "query_id": contract.query_identity(request), "request_id": contract.read_identity(request),
                    "view_id": request["view_id"],
                    "state": "expired", "payload": None, "complete": False, "next_cursor": None,
                    "reason": "Details were retired by the selected policy."}
        for state in ("expired", "cancelled", "unsupported", "incomplete"):
            response["state"] = state
            contract.validate_response(response, request, self.snapshot)
            with self.assertRaises(contract.SnapshotContractError):
                contract.validate_response({**response, "payload": {"records": []}, "complete": True},
                                           request, self.snapshot)


if __name__ == "__main__":
    unittest.main()
