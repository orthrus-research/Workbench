"""Standalone scan commands preserve the domain result and stored audit scope."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import ExecutionContext
from workbench_atlas_recipe_health import scan_cli as cli
import workbench_registration_atlas as registration


def view_record():
    summary = {"recipe_count": 3, "missing_producer_candidate_count": 2,
               "no_output_use_candidate_count": 1, "both_sides_candidate_count": 1,
               "unresolved_recipe_count": 1, "cycle_component_count": 0}
    coverage = {"status": "captured-gt-recipes-only", "other_recipe_families": "unassessed",
                "interpretation": "Missing captured links are review candidates."}
    return {"format": "workbench-atlas-completed-scan-view-v1", "state": "verified",
            "manifest_id": "archive:original", "root": "Imported 資料", "graph_path": "Imported 資料/graph",
            "metadata": {"format": "workbench-atlas-completed-scan-v1", "domain": {
                "original_result_id": "result:original", "graph_set_id": "graph:original",
                "summary": summary, "coverage": coverage,
                "scope": {"pack_profile_id": "fixture-profile", "lifecycle_checkpoint_id": "post-start"}}},
            "policy_status": "different-implementation", "recomputed": False,
            "trust": {"content_integrity": "verified", "publisher_authenticity": "unverified",
                      "original_execution_custody": "not-included", "current_target_match": "unassessed"}}


def audit_record(*, rows=True):
    view = view_record()
    domain = view["metadata"]["domain"]
    return {**view, "format": "workbench-atlas-cached-recipe-audit-page-v1", "summary": domain["summary"],
            "coverage": domain["coverage"], "policy": {"id": "stored-policy"},
            "audit_sha256": "a" * 64, "context": {"graph_set_id": "graph:original"},
            "items": [{"selection_id": "recipe:original", "semantic_key": "assembly|circuit|0",
                       "lookup_state": "active", "findings": ["both-sides-candidate"]}] if rows else [],
            "page": {"offset": 10, "limit": 1 if rows else 0, "returned": 1 if rows else 0,
                     "total_matching": 12, "next_offset": 11 if rows else None}}


class CompletedScanCliTests(unittest.TestCase):
    def invoke(self, arguments, *, context=None):
        out, err = StringIO(), StringIO()
        status = cli.main(arguments, context=context, output=out, error=err)
        return status, out.getvalue(), err.getvalue()

    def test_import_show_and_export_delegate_explicit_paths_without_changing_records(self):
        cases = (("import", "import_scan", ["--destination", "New import 資料"], ["Scan 資料.zip", "New import 資料"]),
                 ("show", "show_scan", [], ["Imported 資料"]),
                 ("export", "export_scan", ["--output", "New archive 資料.zip"], ["Imported 資料", "New archive 資料.zip"]))
        for action, method, flags, paths in cases:
            with self.subTest(action=action):
                result = view_record()
                result["archive"] = {"path": "New archive 資料.zip", "sha256": "b" * 64}
                original = deepcopy(result)
                with patch.object(cli.completed_scan, method, return_value=result) as owner:
                    status, raw, err = self.invoke([action, paths[0], *flags, "--json"])
                self.assertEqual(0, status, err)
                self.assertEqual(original, json.loads(raw))
                self.assertEqual(original, result)
                self.assertTrue(raw.isascii())  # Redirected Windows JSON preserves Unicode through escapes.
                owner.assert_called_once_with(*map(Path, paths), check_cancelled=None)

    def test_default_audit_is_a_stored_summary_in_both_output_formats(self):
        for flags in ([], ["--json"], ["--summary", "--json"]):
            with self.subTest(flags=flags), patch.object(cli.completed_scan, "read_cached_recipe_audit", return_value=audit_record(rows=False)) as owner:
                status, raw, err = self.invoke(["audit", "Imported scan", *flags])
                self.assertEqual(0, status, err)
                owner.assert_called_once_with(Path("Imported scan"), finding=None, lookup_state=None,
                                              text=None, offset=0, limit=0, check_cancelled=None)
                if "--json" in flags:
                    self.assertFalse(json.loads(raw)["recomputed"])
                else:
                    self.assertIn("Stored rows: 0 returned", raw)
                    self.assertIn("Saved audit totals", raw)

    def test_page_filters_preserve_complete_stored_scope_and_paging(self):
        result = audit_record()
        with patch.object(cli.completed_scan, "read_cached_recipe_audit", return_value=result) as owner:
            status, raw, err = self.invoke(["audit", "Imported scan", "--finding", "both-sides-candidate",
                "--lookup-state", "active", "--text", "circuit 資料", "--offset", "10", "--limit", "1", "--json"])
        self.assertEqual(0, status, err)
        self.assertEqual(result, json.loads(raw))
        owner.assert_called_once_with(Path("Imported scan"), finding="both-sides-candidate", lookup_state="active",
                                      text="circuit 資料", offset=10, limit=1, check_cancelled=None)
        for flags, limit in ((["--finding", "structural-cycle"], 100),
                             (["--summary", "--finding", "structural-cycle"], 0)):
            with self.subTest(flags=flags), patch.object(cli.completed_scan, "read_cached_recipe_audit", return_value=audit_record(rows=False)) as owner:
                self.assertEqual(0, self.invoke(["audit", "Imported scan", *flags])[0])
                self.assertEqual(limit, owner.call_args.kwargs["limit"])

    def test_human_output_includes_provenance_scope_graph_and_next_page(self):
        with patch.object(cli.completed_scan, "read_cached_recipe_audit", return_value=audit_record()):
            status, raw, err = self.invoke(["audit", "Imported scan", "--limit", "1", "--offset", "10"])
        self.assertEqual(0, status, err)
        for required in ("Historical snapshot", "not been recomputed", "Graph path: Imported 資料/graph",
                         "Original result: result:original", "captured-gt-recipes-only", "different-implementation",
                         "publisher: unverified", "current target: unassessed", "assembly|circuit|0",
                         "recipe:original", "Next offset: 11"):
            self.assertIn(required, raw)

    def test_domain_refusal_has_no_successful_or_partial_json(self):
        for failure in (ValueError("archive member changed"), OSError("destination exists")):
            with self.subTest(failure=failure), patch.object(cli.completed_scan, "import_scan", side_effect=failure):
                status, raw, err = self.invoke(["import", "scan.zip", "--destination", "new scan", "--json"])
            self.assertEqual(2, status)
            self.assertEqual("", raw)
            self.assertIn("Atlas scans failed", err)
            self.assertIn(str(failure), err)

    def test_human_verification_notice_precedes_domain_work_and_json_is_quiet(self):
        for flags in ([], ["--json"]):
            with self.subTest(flags=flags):
                out, err = StringIO(), StringIO()

                def verify(*args, **kwargs):
                    self.assertEqual("", out.getvalue())
                    self.assertEqual(bool(flags), not bool(err.getvalue()))
                    return view_record()

                with patch.object(cli.completed_scan, "show_scan", side_effect=verify):
                    self.assertEqual(0, cli.main(["show", "Imported", *flags], output=out, error=err))
                if flags:
                    self.assertEqual(view_record(), json.loads(out.getvalue()))
                    self.assertEqual("", err.getvalue())
                else:
                    self.assertIn("Verifying saved scan", err.getvalue())

    def test_human_page_prefers_observed_names_then_map_labels(self):
        for names, expected in ((["Basic Circuit"], "Basic Circuit"), ([], "Circuit Assembler")):
            with self.subTest(names=names):
                result = audit_record()
                result["items"][0].update(observed_names=names, recipe_maps=[{
                    "semantic_key": "gregtech:assembler", "label": "Circuit Assembler"}])
                with patch.object(cli.completed_scan, "read_cached_recipe_audit", return_value=result):
                    status, raw, err = self.invoke(["audit", "Imported", "--limit", "1"])
                self.assertEqual(0, status, err)
                self.assertIn(expected + " [active]", raw)
                self.assertIn("recipe:original", raw)

    def test_cancellation_is_forwarded_and_never_prints_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = ExecutionContext(Path(temporary), Path(temporary))
            context.cancelled.set()
            with patch.object(cli.completed_scan, "show_scan") as owner:
                status, raw, err = self.invoke(["show", "Imported scan", "--json"], context=context)
            owner.assert_not_called()
            self.assertEqual(2, status)
            self.assertEqual("", raw)
            self.assertIn("cancelled", err)
            context.cancelled.clear()

            def cancelled(path, *, check_cancelled):
                self.assertEqual(context.check_cancelled, check_cancelled)
                context.cancelled.set()
                check_cancelled()

            with patch.object(cli.completed_scan, "export_scan", side_effect=lambda path, target, **kwargs: cancelled(path, **kwargs)):
                status, raw, err = self.invoke(["export", "Imported scan", "--output", "new.zip", "--json"], context=context)
            self.assertEqual(2, status)
            self.assertEqual("", raw)
            self.assertIn("cancelled", err)

    def test_summary_and_page_controls_cannot_conflict(self):
        for flag in ("--offset", "--limit"):
            with self.subTest(flag=flag), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as failure:
                self.invoke(["audit", "Imported scan", "--summary", flag, "1"])
            self.assertEqual(2, failure.exception.code)

    def test_registered_standalone_capability_and_parent_help(self):
        with patch.object(registration, "version", return_value="0.1.5"):
            module = registration.module()
        capability = next(item for item in module.capabilities if item.id == "atlas.scans")
        self.assertEqual(("atlas", "scans"), capability.command)
        self.assertEqual((), capability.requires_profiles)
        self.assertEqual(("crucible",), module.requires)
        with tempfile.TemporaryDirectory() as temporary:
            context = ExecutionContext(Path(temporary), Path(temporary))
            with patch.object(cli, "main", return_value=0) as handler:
                self.assertEqual(0, registration.scans(["show", "selected"], context=context))
            handler.assert_called_once_with(["show", "selected"], context=context)
            out = StringIO()
            with redirect_stdout(out):
                registration.recipes([], context=context)
            self.assertIn("scans", out.getvalue())


if __name__ == "__main__":
    unittest.main()
