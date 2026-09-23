from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_diagnostics import (  # noqa: E402
    DIAGNOSTIC_HEALTH_RECEIPT_PREFIX,
    LITERAL_DIAGNOSTIC_POLICY_FORMAT,
    DiagnosticHealthValidationError,
    build_external_diagnostic_health_receipt,
    canonical_diagnostic_health_json_bytes,
    parse_external_diagnostic_health_receipt,
    parse_literal_diagnostic_policy,
    write_external_diagnostic_health_receipt,
)


TOOL = ROOT / "modules/crucible/tools/assemble_external_diagnostic_health_receipt.py"
RECEIPT_SCHEMA = ROOT / "modules/crucible/schemas/external-diagnostic-health-receipt-v1.schema.json"
POLICY_SCHEMA = ROOT / "modules/crucible/schemas/literal-diagnostic-policy-v1.schema.json"
SUBJECT_SHA = "a" * 64
OTHER_SHA = "b" * 64


def policy(*rules: dict, subject_sha: str = SUBJECT_SHA) -> dict:
    return {
        "format": LITERAL_DIAGNOSTIC_POLICY_FORMAT,
        "schema_version": 1,
        "policy_id": "test.external-diagnostic-health",
        "owner_profile_id": "test.profile",
        "subject_artifact_sha256": subject_sha,
        "log_format": "forge-log4j2-bracketed-v1",
        "match_semantics": "literal-logger-level-message-prefix-v1",
        "zero_match_state": "inconclusive",
        "rules": list(rules),
    }


def rule(
    rule_id: str,
    disposition: str,
    message_prefix: str,
    *,
    logger: str = "fixture",
    level: str = "WARN",
) -> dict:
    return {
        "rule_id": rule_id,
        "disposition": disposition,
        "logger": logger,
        "level": level,
        "message_prefix": message_prefix,
    }


def encoded_policy(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def encoded_audit(
    launch: bytes,
    *,
    outcome: str = "completed",
    artifacts: list[dict] | None = None,
) -> bytes:
    installed = artifacts or [
        {
            "relative_path": "mods/fixture.jar",
            "sha256": "e" * 64,
            "size_bytes": 1000,
        },
        {
            "relative_path": "mods/other.jar",
            "sha256": OTHER_SHA,
            "size_bytes": 2048,
        },
        {
            "relative_path": "mods/subject.jar",
            "sha256": SUBJECT_SHA,
            "size_bytes": 4096,
        },
    ]
    material = {
        "schema": "workbench.crucible.exact-runtime-session-audit.v1",
        "case_id": "diagnostic-test",
        "outcome": outcome,
        "observer_enabled": False,
        "candidate_lock": {
            "file_sha256": "c" * 64,
            "canonical_sha256": "d" * 64,
        },
        "fixture_artifact": {
            "file_sha256": "e" * 64,
            "size_bytes": 1000,
        },
        "installed_mod_set": {
            "file_sha256": "f" * 64,
            "canonical_sha256": "1" * 64,
            "runtime_class_source_sha256": "2" * 64,
            "verified_artifacts": installed,
        },
        "configuration_set": {
            "file_sha256": "3" * 64,
            "canonical_sha256": "4" * 64,
            "runtime_settings_sha256": "5" * 64,
            "verified_files": [
                {
                    "relative_path": "server.properties",
                    "sha256": "6" * 64,
                    "size_bytes": 120,
                }
            ],
        },
        "launch_log": {
            "file_sha256": hashlib.sha256(launch).hexdigest(),
            "size_bytes": len(launch),
            "java_tool_options_occurrences": 2,
            "java_tool_options_line_sha256": "7" * 64,
            "java_properties_sha256": "8" * 64,
            "gradle": {
                "terminal_status": "BUILD SUCCESSFUL",
                "exit_code": 0,
                "exit_code_evidence": "gradle_success_terminal_record",
            },
        },
        "raw_capture": None,
        "fixture_result": {
            "file_sha256": "9" * 64,
            "canonical_document_sha256": "0" * 64,
            "completion_state": "complete",
            "save_state": "flushed",
            "shutdown_state": "requested",
            "route_order": "forward",
            "route_sha256": "a" * 64,
            "semantic_result_sha256": "b" * 64,
            "runtime_inventory_sha256": "c" * 64,
            "runtime_inventory_count": 2,
        },
        "foundation_class_dump": {
            "format": "workbench-foundation-class-dump-manifest-v1",
            "class_count": 100,
            "total_size_bytes": 4000,
            "manifest_sha256": "d" * 64,
        },
    }
    audit = {
        "audit_id": "crucible-exact-runtime-session-audit:sha256:"
        + hashlib.sha256(canonical_diagnostic_health_json_bytes(material)).hexdigest(),
        **material,
    }
    return canonical_diagnostic_health_json_bytes(audit) + b"\n"


def build(launch: bytes, policy_value: dict) -> dict:
    return build_external_diagnostic_health_receipt(
        session_audit_bytes=encoded_audit(launch),
        launch_log_bytes=launch,
        policy_bytes=encoded_policy(policy_value),
    )


def reidentify(receipt: dict) -> None:
    material = deepcopy(receipt)
    material.pop("receipt_id")
    receipt["receipt_id"] = DIAGNOSTIC_HEALTH_RECEIPT_PREFIX + hashlib.sha256(
        canonical_diagnostic_health_json_bytes(material)
    ).hexdigest()


class ExternalDiagnosticHealthReceiptTests(unittest.TestCase):
    def test_fail_match_dominates_review_and_binds_subject(self) -> None:
        launch = (
            b"Gradle context\n"
            b"[12:00:00] [Server thread/WARN] [fixture]: Review this alpha\n"
            b"stack trace continuation\n"
            b"[12:00:01] [Server thread/WARN] [fixture]: Required resource failed: one\n"
            b"[12:00:02] [Server thread/WARN] [fixture]: Required resource failed: two\n"
        )
        value = policy(
            rule("review.rule", "review", "Review this"),
            rule("fail.rule", "fail", "Required resource failed:"),
        )
        receipt = build(launch, value)
        self.assertEqual(receipt["summary"]["gate_state"], "fail")
        self.assertEqual(receipt["summary"]["fail_occurrence_count"], 2)
        self.assertEqual(receipt["summary"]["review_occurrence_count"], 1)
        self.assertEqual(receipt["summary"]["matched_log_line_count"], 3)
        self.assertEqual(receipt["summary"]["parsed_log_line_count"], 3)
        self.assertEqual(receipt["summary"]["unparsed_log_line_count"], 2)
        self.assertFalse(receipt["summary"]["clean_compatibility_seal_allowed"])
        self.assertEqual(receipt["subject"]["artifact_sha256"], SUBJECT_SHA)
        self.assertEqual(receipt["subject"]["installed_relative_path"], "mods/subject.jar")
        fail = receipt["rule_results"][0]
        self.assertEqual(fail["rule_id"], "fail.rule")
        self.assertEqual((fail["first_line"], fail["last_line"]), (4, 5))
        self.assertEqual(fail["occurrence_count"], 2)
        self.assertEqual(len(fail["message_sha256s"]), 2)
        self.assertEqual(parse_external_diagnostic_health_receipt(receipt), receipt)

    def test_review_match_yields_review(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Review this\n"
        receipt = build(
            launch,
            policy(
                rule("fail.rule", "fail", "Never"),
                rule("review.rule", "review", "Review this"),
            ),
        )
        self.assertEqual(receipt["summary"]["gate_state"], "review")
        self.assertEqual(receipt["summary"]["fail_occurrence_count"], 0)
        self.assertEqual(receipt["summary"]["review_occurrence_count"], 1)

    def test_zero_matches_are_inconclusive_not_healthy(self) -> None:
        launch = b"[12:00:00] [main/INFO] [fixture]: Everything looked quiet\n"
        receipt = build(launch, policy(rule("fail.rule", "fail", "Failure")))
        self.assertEqual(receipt["summary"]["gate_state"], "inconclusive")
        self.assertFalse(receipt["summary"]["clean_compatibility_seal_allowed"])
        self.assertFalse(receipt["boundaries"]["zero_matches_prove_health"])
        self.assertFalse(receipt["boundaries"]["log_absence_proves_diagnostic_absence"])
        result = receipt["rule_results"][0]
        self.assertEqual(result["occurrence_count"], 0)
        self.assertIsNone(result["first_line"])
        self.assertEqual(result["message_sha256s"], [])

    def test_policy_metacharacters_are_literal(self) -> None:
        launch = (
            b"[12:00:00] [main/WARN] [fixture]: Error x anything\n"
            b"[12:00:01] [main/WARN] [fixture]: Error [x].* literal\n"
        )
        receipt = build(
            launch,
            policy(rule("literal.rule", "fail", "Error [x].*")),
        )
        result = receipt["rule_results"][0]
        self.assertEqual(result["occurrence_count"], 1)
        self.assertEqual((result["first_line"], result["last_line"]), (2, 2))
        self.assertFalse(receipt["boundaries"]["arbitrary_executable_patterns_supported"])

    def test_logger_level_and_prefix_are_all_exact(self) -> None:
        launch = (
            b"[12:00:00] [main/ERROR] [fixture]: Failure exact\n"
            b"[12:00:01] [main/WARN] [Fixture]: Failure exact\n"
            b"[12:00:02] [main/WARN] [fixture]: failure exact\n"
            b"[12:00:03] [main/WARN] [fixture]: Failure exact\n"
        )
        receipt = build(launch, policy(rule("fail.rule", "fail", "Failure")))
        self.assertEqual(receipt["rule_results"][0]["occurrence_count"], 1)
        self.assertEqual(receipt["rule_results"][0]["first_line"], 4)

    def test_incomplete_session_is_rejected(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "outcome is not completed"):
            build_external_diagnostic_health_receipt(
                session_audit_bytes=encoded_audit(launch, outcome="crash"),
                launch_log_bytes=launch,
                policy_bytes=encoded_policy(policy(rule("fail.rule", "fail", "Failure"))),
            )

    def test_launch_log_must_match_audited_hash(self) -> None:
        audited = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "launch log SHA-256"):
            build_external_diagnostic_health_receipt(
                session_audit_bytes=encoded_audit(audited),
                launch_log_bytes=audited + b"changed\n",
                policy_bytes=encoded_policy(policy(rule("fail.rule", "fail", "Failure"))),
            )

    def test_session_audit_content_identity_is_recomputed(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        value = json.loads(encoded_audit(launch))
        value["case_id"] = "tampered"
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "content identity mismatch"):
            build_external_diagnostic_health_receipt(
                session_audit_bytes=canonical_diagnostic_health_json_bytes(value),
                launch_log_bytes=launch,
                policy_bytes=encoded_policy(policy(rule("fail.rule", "fail", "Failure"))),
            )

    def test_subject_must_be_in_audited_installed_set_exactly_once(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        absent_audit = encoded_audit(
            launch,
            artifacts=[
                {"relative_path": "mods/other.jar", "sha256": OTHER_SHA, "size_bytes": 2}
            ],
        )
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "not bound exactly once"):
            build_external_diagnostic_health_receipt(
                session_audit_bytes=absent_audit,
                launch_log_bytes=launch,
                policy_bytes=encoded_policy(policy(rule("fail.rule", "fail", "Failure"))),
            )

    def test_policy_rejects_regex_surface_and_duplicate_predicate(self) -> None:
        bad = policy(rule("fail.rule", "fail", "Failure"))
        bad["rules"][0]["regex"] = ".*"
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "unknown"):
            parse_literal_diagnostic_policy(bad)
        duplicate = policy(
            rule("one", "fail", "Failure"),
            rule("two", "review", "Failure"),
        )
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "repeats match predicate"):
            parse_literal_diagnostic_policy(duplicate)

    def test_policy_raw_and_canonical_hashes_are_both_bound(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        value = policy(rule("fail.rule", "fail", "Failure"))
        raw = encoded_policy(value)
        receipt = build_external_diagnostic_health_receipt(
            session_audit_bytes=encoded_audit(launch),
            launch_log_bytes=launch,
            policy_bytes=raw,
        )
        normalized = parse_literal_diagnostic_policy(value)
        self.assertEqual(receipt["inputs"]["policy"]["file_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(
            receipt["inputs"]["policy"]["canonical_sha256"],
            hashlib.sha256(canonical_diagnostic_health_json_bytes(normalized)).hexdigest(),
        )

    def test_receipt_identity_and_summary_are_fail_closed(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        receipt = build(launch, policy(rule("fail.rule", "fail", "Failure")))
        tampered = deepcopy(receipt)
        tampered["subject"]["installed_size_bytes"] += 1
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "content identity mismatch"):
            parse_external_diagnostic_health_receipt(tampered)

        tampered = deepcopy(receipt)
        tampered["summary"]["gate_state"] = "review"
        reidentify(tampered)
        with self.assertRaisesRegex(DiagnosticHealthValidationError, "summary is not derived"):
            parse_external_diagnostic_health_receipt(tampered)

    def test_schema_accepts_policy_and_receipt(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        policy_schema = json.loads(POLICY_SCHEMA.read_text(encoding="utf-8"))
        receipt_schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(policy_schema)
        Draft202012Validator.check_schema(receipt_schema)
        value = policy(rule("fail.rule", "fail", "Failure"))
        Draft202012Validator(policy_schema).validate(value)
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        Draft202012Validator(receipt_schema).validate(build(launch, value))

    def test_cli_writes_receipt_and_reports_gate(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit_path = root / "audit.json"
            launch_path = root / "launch.log"
            policy_path = root / "policy.json"
            output_path = root / "receipt.json"
            audit_path.write_bytes(encoded_audit(launch))
            launch_path.write_bytes(launch)
            policy_path.write_bytes(encoded_policy(policy(rule("fail.rule", "fail", "Failure"))))
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--session-audit",
                    str(audit_path),
                    "--launch-log",
                    str(launch_path),
                    "--policy",
                    str(policy_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn(output["receipt_id"], result.stdout)
            self.assertTrue(result.stdout.rstrip().endswith("fail"))
            self.assertEqual(parse_external_diagnostic_health_receipt(output), output)

    def test_writer_rejects_symlink_output(self) -> None:
        launch = b"[12:00:00] [main/WARN] [fixture]: Failure\n"
        receipt = build(launch, policy(rule("fail.rule", "fail", "Failure")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("unchanged", encoding="utf-8")
            link = root / "receipt.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(DiagnosticHealthValidationError, "cannot be a symlink"):
                write_external_diagnostic_health_receipt(link, receipt)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")


if __name__ == "__main__":
    unittest.main()
