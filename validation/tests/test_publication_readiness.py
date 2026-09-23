"""Local publication gates do not invent hosted controls, qualifications or consent."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import publication_readiness as publication


class PublicationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workbench-publication-gates-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "source"
        (self.root / publication.SCHEMA).parent.mkdir(parents=True)
        shutil.copyfile(ROOT / publication.SCHEMA, self.root / publication.SCHEMA)
        self.revision = "1" * 40
        self.tree = "2" * 40
        self.license_digest = hashlib.sha256(b"fixture license").hexdigest()
        self.plan = {"source_revision": self.revision, "git_tree_oid": self.tree,
                     "source_clean": True, "reviewed_revision_is_head": True,
                     "destination": "orthrus-research/workbench", "secret_scan": {"verified": True},
                     "files": [{"path": "LICENSE", "sha256": self.license_digest}]}
        self.inventory = {"workbench-core": {"kind": "python", "version": "0.1.3", "artifacts": [{"filename_template": "workbench_core-{version}-py3-none-any.whl"}]},
                          "workbench-vscode": {"kind": "client", "version": "0.1.3", "artifacts": [{"filename_template": "workbench-vscode-{version}.vsix"}]}}
        self.addCleanup(patch.stopall)
        self.export = patch.object(publication, "public_export_plan", return_value=self.plan).start()
        patch.object(publication, "load_authority", return_value=({}, self.inventory)).start()
        self.assemblies = {}
        patch.object(publication, "verify_wheelhouse", side_effect=lambda path: self.assemblies[path]).start()
        self.path = self.base / "evidence.json"

    def file(self, name, raw=b"reviewed fixture evidence\n"):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}

    def check(self, *, full=False):
        return {"result": "pass", "command": ["python", "validation/validate.py", "--full"] if full else ["test-fixture"],
                "exit_code": 0, "log": self.file("full.log" if full else "check.log", b"Workbench full validation passed; run artifacts: fixture.\n" if full else b"successful fixture check\n"),
                "limitations": ["Synthetic acceptance fixture, not publication evidence."]}

    def evidence(self):
        return {"format": "workbench-publication-evidence-v1", "schema_version": 1,
                "source_revision": self.revision, "git_tree_oid": self.tree}

    def report(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return publication.readiness(self.revision, self.path, components=["workbench-core"], root=self.root)

    def complete(self):
        value = self.evidence()
        value["source_scan"] = self.file("scan.json", b"{}\n")
        value["full_validation"] = self.check(full=True)
        artifact = {"component": "workbench-core", "target": "linux/x86_64/python-3.14", "file": self.file("workbench_core-0.1.3-py3-none-any.whl")}
        self.assembly(artifact)
        artifact.update({key: self.check() for key in ("ownership", "functional", "secret_scan")})
        value["artifacts"] = [artifact]
        value["hosted"] = {"repository": "orthrus-research/workbench", "observed_at": "2026-09-07T20:00:00Z",
                           "destination_head": "4" * 40, "destination_license_sha256": "5" * 64,
                           "evidence": self.file("hosted.json"), "checks": {key: True for key in (*publication.SOURCE_CONTROLS, *publication.RELEASE_CONTROLS)}}
        value["hosted"]["checks"].update(independent_review_required=False, community_intake_disabled=True)
        value["security_reporting"] = {"owner": "security-fixture-owner", "route": "https://example.invalid/private-security", "tested_at": "2026-09-07T20:00:00Z", "confidential": True, "evidence": self.file("reporting.log")}
        value["approval"] = {"actor": "zestehl", "source_author": "zestehl", "mode": "bootstrap-same-author", "actions": ["publish-source", "publish-artifacts"],
                             "artifact_sha256": [artifact["file"]["sha256"]], "evidence": self.file("approval.log")}
        value["migration_decision"] = {"destination_head": "4" * 40, "strategy": "preserve-existing-public-history", "evidence": self.file("migration-decision.log")}
        value["license_decision"] = {"source_license_sha256": self.license_digest, "destination_license_sha256": "5" * 64,
                                    "intended_spdx": "LGPL-3.0-only", "evidence": self.file("license-decision.log")}
        return value

    def assembly(self, artifact, *, platform="linux"):
        reference = self.file(f"{platform}/wheelhouse.json", b"fixture assembly identity\n")
        artifact["assembly_manifest"] = reference
        source_identity = hashlib.sha256()
        for row in sorted(self.plan["files"], key=lambda item: item["path"].encode("utf-8")):
            source_identity.update(row["path"].encode("utf-8") + b"\0" + bytes.fromhex(row["sha256"]))
        self.assemblies[Path(reference["path"]).parent] = {
            "source_sha256": source_identity.hexdigest(),
            "target": {"python": "3.14", "platform": platform, "machine": "x86_64"},
            "wheels": [{"filename": Path(artifact["file"]["path"]).name, "name": "workbench-core", "version": "0.1.3", "size": len(Path(artifact["file"]["path"]).read_bytes()), "sha256": artifact["file"]["sha256"]}],
        }

    def conduct_policy(self, value, *, reviewed=True):
        policy = self.root / "CODE_OF_CONDUCT.md"
        policy.write_text("Test governing policy\n", encoding="utf-8")
        digest = hashlib.sha256(policy.read_bytes()).hexdigest()
        value["conduct_policy"] = {"path": str(policy), "sha256": digest}
        if reviewed:
            self.plan["files"].append({"path": "CODE_OF_CONDUCT.md", "sha256": digest})
            self.assembly(value["artifacts"][0])

    def test_default_is_read_only_pending_in_all_scopes(self):
        result = publication.readiness(self.revision, root=self.root)
        self.assertFalse(result["mutations_performed"])
        self.assertTrue(all(not row["ready"] for row in result["scopes"].values()))
        self.assertIn("full-validation", result["scopes"]["local"]["blockers"])
        self.assertIn("approval:publish-source", result["scopes"]["source"]["blockers"])

    def test_bound_results_satisfy_only_the_selected_declared_target(self):
        result = self.report(self.complete())
        self.assertTrue(result["scopes"]["release"]["ready"])
        self.assertFalse(result["scopes"]["community"]["ready"])
        self.assertEqual(["linux/x86_64/python-3.14"], [row["target"] for row in result["qualified_targets"]])
        self.assertIn("operator", " ".join(result["limitations"]))

    def test_public_visibility_is_not_a_hosted_controls_receipt(self):
        value = self.complete()
        del value["hosted"]
        result = self.report(value)
        self.assertTrue(result["scopes"]["local"]["ready"])
        self.assertFalse(result["scopes"]["source"]["ready"])

    def test_different_source_revision_or_tree_is_rejected(self):
        for key in ("source_revision", "git_tree_oid"):
            with self.subTest(key=key):
                value = self.evidence()
                value[key] = "3" * 40
                with self.assertRaisesRegex(publication.ReadinessError, "another source"):
                    self.report(value)

    def test_stale_artifact_and_log_hashes_are_rejected(self):
        for target in ("artifact", "log"):
            value = self.complete()
            row = value["artifacts"][0]["file"] if target == "artifact" else value["full_validation"]["log"]
            Path(row["path"]).write_bytes(b"changed bytes")
            with self.assertRaisesRegex(publication.ReadinessError, "digest differs"):
                self.report(value)

    def test_python_success_manifest_does_not_qualify_full_validation(self):
        value = self.complete()
        value["full_validation"]["log"] = self.file("run.json", b'{"state":"passed","tier":"canonical"}')
        result = self.report(value)
        self.assertIn("full-validation", result["scopes"]["local"]["blockers"])

    def test_a_later_failure_cannot_reuse_an_earlier_success_log(self):
        value = self.complete()
        value["full_validation"]["log"] = self.file("reused.log", b"Workbench full validation passed; run artifacts: old.\nVALIDATION FAILED: current run\n")
        self.assertFalse(self.report(value)["scopes"]["local"]["ready"])

    def test_failed_command_and_boolean_exit_code_cannot_pass(self):
        for exit_code in (1, False):
            value = self.complete()
            value["full_validation"]["exit_code"] = exit_code
            if exit_code is False:
                with self.assertRaises(publication.ReadinessError):
                    self.report(value)
            else:
                self.assertFalse(self.report(value)["scopes"]["local"]["ready"])

    def test_any_failed_selected_artifact_target_blocks_release(self):
        value = self.complete()
        failed = deepcopy(value["artifacts"][0])
        failed["target"] = "win32/x86_64/python-3.14"
        self.assembly(failed, platform="win32")
        failed["functional"]["result"] = "fail"
        value["artifacts"].append(failed)
        self.assertIn("selected-artifacts", self.report(value)["scopes"]["release"]["blockers"])

    def test_client_requires_host_qualification(self):
        value = self.complete()
        value["artifacts"][0]["component"] = "workbench-vscode"
        value["artifacts"][0]["file"] = self.file("workbench-vscode-0.1.3.vsix")
        self.path.write_text(json.dumps(value), encoding="utf-8")
        result = publication.readiness(self.revision, self.path, components=["workbench-vscode"], root=self.root)
        self.assertIn("selected-artifacts", result["scopes"]["release"]["blockers"])

    def test_missing_approval_and_wrong_artifact_approval_remain_pending(self):
        value = self.complete()
        del value["approval"]
        self.assertIn("approval:publish-source", self.report(value)["scopes"]["source"]["blockers"])
        value = self.complete()
        value["approval"]["artifact_sha256"] = ["f" * 64]
        self.assertIn("approval:publish-artifacts", self.report(value)["scopes"]["release"]["blockers"])

    def test_same_author_is_not_independent_approval(self):
        value = self.complete()
        value["hosted"]["checks"]["independent_review_required"] = True
        self.assertIn("approval:publish-source", self.report(value)["scopes"]["source"]["blockers"])

        value["approval"]["mode"] = "independent"
        value["approval"]["actor"] = " ZESTEHL "
        self.assertIn("approval:publish-source", self.report(value)["scopes"]["source"]["blockers"])
        value["approval"]["actor"] = "second-fixture-maintainer"
        self.assertTrue(self.report(value)["scopes"]["source"]["ready"])

    def test_native_assembly_is_required_and_cannot_borrow_source_or_target(self):
        value = self.complete()
        del value["artifacts"][0]["assembly_manifest"]
        self.assertIn("selected-artifacts", self.report(value)["scopes"]["release"]["blockers"])
        value = self.complete()
        value["artifacts"][0]["target"] = "win32/x86_64/python-3.14"
        with self.assertRaisesRegex(publication.ReadinessError, "target differs"):
            self.report(value)
        value = self.complete()
        self.assemblies[self.base / "linux"]["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(publication.ReadinessError, "source, wheel record"):
            self.report(value)

    def test_native_artifact_must_match_its_exact_wheel_record(self):
        value = self.complete()
        self.assemblies[self.base / "linux"]["wheels"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(publication.ReadinessError, "wheel record"):
            self.report(value)

    def test_open_community_without_conduct_policy_blocks_source(self):
        value = self.complete()
        value["hosted"]["checks"]["community_intake_disabled"] = False
        self.assertIn("community-safe-state", self.report(value)["scopes"]["source"]["blockers"])

    def test_conduct_cannot_borrow_security_route(self):
        value = self.complete()
        value["conduct_reporting"] = deepcopy(value["security_reporting"])
        self.conduct_policy(value)
        value["approval"]["actions"].append("enable-community")
        self.assertIn("conduct_reporting", self.report(value)["scopes"]["community"]["blockers"])

    def test_separate_adopted_conduct_route_has_its_own_approval(self):
        value = self.complete()
        value["conduct_reporting"] = deepcopy(value["security_reporting"])
        value["conduct_reporting"]["route"] = "mailto:conduct@example.invalid"
        self.conduct_policy(value)
        self.assertFalse(self.report(value)["scopes"]["community"]["ready"])
        value["approval"]["actions"].append("enable-community")
        self.assertTrue(self.report(value)["scopes"]["community"]["ready"])

    def test_conduct_route_cannot_disguise_security_route_with_spacing_or_case(self):
        value = self.complete()
        value["conduct_reporting"] = deepcopy(value["security_reporting"])
        value["conduct_reporting"]["route"] = " \t" + value["security_reporting"]["route"].upper() + " \n"
        self.conduct_policy(value)
        value["approval"]["actions"].append("enable-community")
        self.assertIn("conduct_reporting", self.report(value)["scopes"]["community"]["blockers"])

    def test_unreviewed_conduct_policy_file_cannot_enable_community(self):
        value = self.complete()
        value["conduct_reporting"] = deepcopy(value["security_reporting"])
        value["conduct_reporting"]["route"] = "mailto:conduct@example.invalid"
        self.conduct_policy(value, reviewed=False)
        value["hosted"]["checks"]["community_intake_disabled"] = False
        value["approval"]["actions"].append("enable-community")
        result = self.report(value)
        self.assertIn("conduct-policy", result["scopes"]["community"]["blockers"])
        self.assertIn("community-safe-state", result["scopes"]["source"]["blockers"])

    def test_conduct_policy_must_match_reviewed_tree_hash(self):
        value = self.complete()
        value["conduct_reporting"] = deepcopy(value["security_reporting"])
        value["conduct_reporting"]["route"] = "mailto:conduct@example.invalid"
        self.conduct_policy(value)
        self.plan["files"][-1]["sha256"] = "e" * 64
        self.assembly(value["artifacts"][0])
        value["approval"]["actions"].append("enable-community")
        self.assertIn("conduct-policy", self.report(value)["scopes"]["community"]["blockers"])

    def test_cli_reporting_is_not_a_silent_require_ready_check(self):
        result = {"scopes": {"local": {"ready": False}}}
        with patch.object(publication, "readiness", return_value=result), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, publication.main(["--revision", self.revision]))
            self.assertEqual(1, publication.main(["--revision", self.revision, "--require-ready", "local"]))
        with patch.object(publication, "readiness", side_effect=publication.ReadinessError("fixture")), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(2, publication.main(["--revision", self.revision]))

    def test_evidence_cannot_be_placed_in_public_source(self):
        self.path = self.root / "evidence.json"
        with self.assertRaisesRegex(publication.ReadinessError, "ignored"):
            self.report(self.evidence())

    def test_unknown_fields_and_foreign_host_are_rejected(self):
        value = self.evidence()
        value["ready"] = True
        with self.assertRaises(publication.ReadinessError):
            self.report(value)
        value = self.complete()
        value["hosted"]["repository"] = "someone-else/workbench"
        with self.assertRaises(publication.ReadinessError):
            self.report(value)

    def test_migration_and_license_decisions_are_required(self):
        for field, gate in (("migration_decision", "migration-decision"), ("license_decision", "license-decision")):
            value = self.complete()
            del value[field]
            self.assertIn(gate, self.report(value)["scopes"]["source"]["blockers"])

    def test_migration_decision_must_match_destination_and_replacement_scope(self):
        value = self.complete()
        value["migration_decision"]["destination_head"] = "6" * 40
        self.assertIn("migration-decision", self.report(value)["scopes"]["source"]["blockers"])
        value = self.complete()
        value["migration_decision"]["strategy"] = "new-root-empty-destination"
        self.assertIn("migration-decision", self.report(value)["scopes"]["source"]["blockers"])
        value["migration_decision"]["strategy"] = "replace-existing-public-history"
        self.assertIn("migration-decision", self.report(value)["scopes"]["source"]["blockers"])
        value["approval"]["actions"].append("replace-public-history")
        self.assertTrue(self.report(value)["scopes"]["source"]["ready"])

    def test_license_decision_cannot_override_native_spdx_or_observed_bytes(self):
        for field, replacement in (("intended_spdx", "GPL-3.0-only"), ("source_license_sha256", "7" * 64), ("destination_license_sha256", "8" * 64)):
            value = self.complete()
            value["license_decision"][field] = replacement
            self.assertIn("license-decision", self.report(value)["scopes"]["source"]["blockers"])


if __name__ == "__main__":
    unittest.main()
