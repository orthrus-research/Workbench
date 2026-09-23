"""Completed scans retain original evidence without executing its evaluator."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder
from workbench_atlas_recipe_health import audit_recipe_dead_ends, open_recipe_health
from workbench_atlas_recipe_health import completed_scan as scan
from test_captured_recipe_routes import route_graph


def encoded(value, *, ascii=True):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii).encode()


def digest(value):
    return sha256(value).hexdigest()


def sealed(value, prefix, *, ascii=True):
    return {**value, "id": prefix + ":sha256:" + digest(encoded(value, ascii=ascii))}


def write(root, name, value):
    raw = encoded(value)
    (root / name).write_bytes(raw)
    return {"path": "Z:\\Original machine 資料\\" + name, "size": len(raw), "sha256": digest(raw)}


def scan_fixture(root, *, cyclic=False):
    """A small independent stored-record fixture, not native game evidence."""
    root.mkdir(parents=True)
    capture = root / "capture"
    capture.mkdir()
    source = {"id": "fixture-saved-source", "candidate": {"source": {"revision": "a" * 40}}}
    request = sealed({"format": "workbench-developer-recipe-capture-request-v1", "state": "planned",
                      "attempt_id": "fixture", "source_binding": source, "provider": {"id": "fixture"},
                      "descriptor": {"native_fixture": False}, "observer_sources": {}, "heap_mib": 2048},
                     "recipe-capture-request")
    prepared = sealed({"format": "workbench-developer-recipe-capture-prepared-v1", "state": "prepared",
                       "attempt_id": "fixture", "request_id": request["id"]}, "recipe-capture-prepared")
    protocol = {"format": "workbench-recipe-capture-protocol-v1", "profile": request["provider"],
                "descriptor": request["descriptor"], "observer_sources": {}, "heap_mib": 2048,
                "explicit_eula_acceptance": True}
    lock = {"format": "workbench-recipe-capture-runtime-lock-v1", "source_binding_id": source["id"]}
    inputs = {"capture_id": "fixture", "launch_id": "fixture-launch", "physical_side": "dedicated_server",
              "pack_binding_id": source["id"], "pack_source": source, "platform_binding_id": "fixture-platform",
              "candidate_lock_sha256": digest(encoded(lock)), "adapter_profile_sha256": digest(encoded(protocol))}
    input_descriptor = write(root, "input-manifest.json", inputs)
    binding = {k: inputs[k] for k in ("capture_id", "launch_id", "physical_side", "candidate_lock_sha256", "adapter_profile_sha256")}
    binding["input_manifest_sha256"] = input_descriptor["sha256"]
    records = [{"recipe": name} for name in ("fixture-1", "fixture-2", "fixture-3")]
    category = {"format": "workbench-crucible-runtime-category-result-v1", "schema_version": 1,
                **binding, "adapter_id": "gt-recipes", "category_id": "transformation-recipe",
                "checkpoint_id": "fixture-checkpoint", "status": "complete", "stable": True,
                "record_count": len(records), "unsupported_value_count": 0, "diagnostics": [],
                "records": records, "records_sha256": digest(encoded(records)),
                "samples": [{"ordinal": n, "record_count": len(records), "unsupported_value_count": 0,
                             "diagnostics": [], "records_sha256": digest(encoded(records))} for n in (1, 2)]}
    category["result_sha256"] = digest(encoded(category))
    raw_category = encoded(category)
    (capture / "gt-recipes.json").write_bytes(raw_category)
    capture_manifest = {"format": "workbench-runtime-graph-raw-bundle-v1", "schema_version": 1,
                        **binding, "producer": "synthetic-fixture",
                        "categories": [{"adapter_id": "gt-recipes", "file": "gt-recipes.json", "status": "complete"}],
                        "payloads": [{"file": "gt-recipes.json", "size": len(raw_category), "sha256": digest(raw_category)}]}
    capture_manifest["manifest_sha256"] = digest(encoded(capture_manifest))
    (capture / "manifest.json").write_bytes(encoded(capture_manifest))
    (capture / ".capture-complete").write_bytes(b"")
    original = route_graph(root / "fixture-graph", [
        {"name": "Missing Source", "inputs": [{"members": ["terminal" if cyclic else "feed"], "complete": True}], "outputs": ["stage", "fluid:water"]},
        {"name": "End Product", "inputs": [{"members": ["stage"], "complete": True},
                                              {"members": ["fluid:water"], "fluid": True, "complete": True}], "outputs": ["terminal"]},
        {"name": "Inactive", "active": False, "outputs": ["other"]}])
    scope = {key: inputs[key] for key in ("pack_binding_id", "platform_binding_id", "candidate_lock_sha256", "physical_side")}
    graph_binding = {"capture_manifest": capture_manifest, "capture_manifest_sha256": digest(encoded(capture_manifest)),
                     "input_manifest": inputs, "category_results": {"gt-recipes": {k: v for k, v in category.items() if k != "records"}}}
    builder = CategoricalGraphBundleBuilder(root / "graph", scope=scope, evidence_binding=graph_binding)
    original_manifest = json.loads((original["path"] / "manifest.json").read_bytes())
    part = original_manifest["partitions"][0]
    nodes = [json.loads(line) for line in (original["path"] / part["nodes"]["file"]).read_text().splitlines()]
    for node in nodes:
        if node["semantic_key"] == "stage":
            node["properties"]["observed_item_names"] = [{"name": "Purified Copper"}]
    builder.add_partition("fixture", classification="independent synthetic fixture", dependencies=(),
                          nodes=nodes,
                          edges=[json.loads(line) for line in (original["path"] / part["edges"]["file"]).read_text().splitlines()],
                          evidence_categories=("transformation-recipe",), limitations=("Not native evidence.",))
    manifest = builder.close()
    shutil.rmtree(original["path"])
    with open_recipe_health(root / "graph") as view:
        report = audit_recipe_dead_ends(view)
    # A sender's original path is portable provenance, never a local lookup.
    report["context"]["root"] = "Z:\\Original machine 資料\\graph"
    launch = sealed({"format": "workbench-supersymmetry-recipe-capture-launch-v1",
                     "input_manifest_sha256": input_descriptor["sha256"], "heap_mib": 2048},
                    "forge-recipe-capture-launch", ascii=False)
    docs = {"request.json": request, "prepared.json": prepared, "launch.json": launch,
            "runtime-lock.json": lock, "protocol.json": protocol, "audit.json": report}
    custody = {name: write(root, name, value) for name, value in docs.items()}
    custody["input-manifest.json"] = input_descriptor
    result = {"format": "workbench-developer-recipe-capture-result-v1", "state": "complete", "native_admitted": True,
              "request_id": request["id"], "prepared_id": prepared["id"], "launch_id": launch["id"], "attempt_id": "fixture",
              "input_manifest": input_descriptor, "audit": custody["audit.json"], "custody": custody,
              "capture_files": [{"path": p.name, "size": p.stat().st_size, "sha256": digest(p.read_bytes())}
                                for p in sorted(capture.iterdir())],
              "projection": {"state": "complete", "graph_set_id": manifest["graph_set_id"], "summary": manifest["summary"],
                             "recipe_count": 3, "recipe_map_count": 1,
                             "capture_manifest_sha256": digest(encoded(capture_manifest))},
              "summary": report["summary"], "coverage": report["coverage"]}
    write(root, "result.json", sealed(result, "recipe-capture-result"))
    return report


class CompletedRecipeScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-completed-scan-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "Original scan 資料"
        self.report = scan_fixture(self.source)

    def write_report(self, report):
        descriptor = write(self.source, "audit.json", report)
        result = json.loads((self.source / "result.json").read_bytes())
        del result["id"]
        result.update(audit=descriptor, summary=report["summary"], coverage=report["coverage"])
        result["custody"]["audit.json"] = descriptor
        write(self.source, "result.json", sealed(result, "recipe-capture-result"))

    def test_domain_validates_originals_without_resolving_sender_paths_or_evaluating(self):
        original = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        with patch.object(scan.dead_ends, "audit_recipe_dead_ends", side_effect=AssertionError("must not evaluate")):
            metadata = scan.validate_completed_scan(self.source)
        self.assertEqual(scan.SCAN_FORMAT, metadata["format"])
        self.assertEqual(3, metadata["domain"]["summary"]["recipe_count"])
        self.assertEqual(original, {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()})

    def test_roundtrip_portable_originals_cached_pages_and_reexport_identity(self):
        archive = self.root / "Completed scan.zip"
        exported = scan.export_completed_scan(self.source, archive)
        destination = self.root / "Imported other machine 資料"
        with patch.object(scan.dead_ends, "audit_recipe_dead_ends", side_effect=AssertionError("must not evaluate")):
            imported = scan.import_scan(archive, destination)
            summary = scan.read_cached_recipe_audit(destination, limit=0)
            first = scan.read_cached_recipe_audit(destination, limit=1)
            second = scan.read_cached_recipe_audit(destination, offset=1, limit=1)
            filtered = scan.read_cached_recipe_audit(destination, finding="missing-producer-candidate", text="missing")
            inactive = scan.read_cached_recipe_audit(destination, lookup_state="inactive")
            shown = scan.show_scan(destination)
            reexported = scan.export_scan(destination, self.root / "Reexport.zip")
        self.assertEqual(exported["manifest_id"], imported["manifest_id"])
        self.assertEqual(exported["manifest_id"], reexported["manifest_id"])
        self.assertEqual([], summary["items"])
        self.assertEqual(3, summary["page"]["total_matching"])
        self.assertEqual(1, first["page"]["next_offset"])
        self.assertNotEqual(first["items"][0], second["items"][0])
        self.assertEqual("Missing Source", filtered["items"][0]["semantic_key"])
        self.assertEqual(1, inactive["page"]["total_matching"])
        self.assertFalse(shown["recomputed"])
        self.assertEqual("not-included", shown["trust"]["original_execution_custody"])
        self.assertEqual("unassessed", shown["trust"]["current_target_match"])
        for name in scan._ORIGINALS:
            self.assertEqual((self.source / name).read_bytes(), (destination / name).read_bytes())

    def test_old_evaluator_is_read_as_historical_without_recomputing(self):
        self.report["policy"]["implementation_sha256"] = "a" * 64
        self.write_report(self.report)
        scan.export_completed_scan(self.source, self.root / "scan.zip")
        scan.import_scan(self.root / "scan.zip", self.root / "import")
        page = scan.read_cached_recipe_audit(self.root / "import")
        self.assertEqual("different-implementation", page["policy_status"])
        self.assertEqual("a" * 64, page["policy"]["implementation_sha256"])
        self.assertFalse(page["recomputed"])

    def test_resealed_bad_audit_format_policy_inventory_reference_summary_and_coverage_refused(self):
        mutations = [lambda r: r.update(format="future-unsupported"),
                     lambda r: r["policy"].update(id="claims-gameplay-proof"),
                     lambda r: r["context"].update(graph_set_id="foreign-graph"),
                     lambda r: r["recipes"].pop(),
                     lambda r: r["recipes"][0]["outputs"][0].update(resource_id="foreign-resource"),
                     lambda r: r["recipes"][0].update(lookup_state="unknown"),
                     lambda r: r["recipes"][0]["recipe_values"].update(eut=12345),
                     lambda r: r["resources"][0].update(observed_names=["invented name"]),
                     lambda r: r["summary"].update(recipe_count=True),
                     lambda r: r["coverage"].update(transitive_supply="proven"),
                     lambda r: r["coverage"].update(truncated=True)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                report = deepcopy(self.report)
                mutate(report)
                self.write_report(report)
                with self.assertRaises(scan.ScanError):
                    scan.validate_completed_scan(self.source)

    def test_capture_tampering_and_original_custody_tampering_refused(self):
        for name in ("capture/gt-recipes.json", "request.json", "input-manifest.json"):
            path = self.source / name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(original + b" ")
                with self.assertRaises(scan.ScanError):
                    scan.validate_completed_scan(self.source)
                path.write_bytes(original)

    def test_archive_metadata_swap_and_extra_payload_refused_before_publication(self):
        metadata, _, members, _ = scan._validate(self.source, lambda: None)
        for mode in ("metadata", "extra"):
            with self.subTest(mode=mode):
                altered = deepcopy(metadata)
                copied = dict(members)
                if mode == "metadata":
                    altered["domain"]["audit_policy"]["implementation_sha256"] = "0" * 64
                else:
                    copied["unclaimed.txt"] = self.source / "input-manifest.json"
                archive = self.root / (mode + ".zip")
                scan._transport().export_archive(archive, copied, metadata=altered)
                destination = self.root / mode
                with self.assertRaises(ValueError):
                    scan.import_scan(archive, destination)
                self.assertFalse(destination.exists())

    def test_payload_changed_between_domain_validation_and_export_refused(self):
        original_export = scan._transport().export_archive
        def changed(destination, members, **kwargs):
            (self.source / "audit.json").write_bytes(b"{}")
            return original_export(destination, members, **kwargs)
        with patch.object(scan._transport(), "export_archive", side_effect=changed):
            with self.assertRaisesRegex(scan.ScanError, "expected manifest"):
                scan.export_completed_scan(self.source, self.root / "changed.zip")
        self.assertFalse((self.root / "changed.zip").exists())

    def test_related_resource_names_and_ids_filter_stored_recipe_references(self):
        resource = next(row for row in self.report["resources"] if row["semantic_key"] == "stage")
        scan.export_completed_scan(self.source, self.root / "scan.zip")
        scan.import_scan(self.root / "scan.zip", self.root / "import")
        for text in ("water", "copper", "stage", resource["selection_id"]):
            with self.subTest(text=text):
                page = scan.read_cached_recipe_audit(self.root / "import", text=text, limit=1)
                self.assertEqual(2, page["page"]["total_matching"])
                self.assertEqual(1, page["page"]["next_offset"])

    def test_structural_cycle_keeps_unknown_seed_and_viability(self):
        cyclic = self.root / "cycle"
        report = scan_fixture(cyclic, cyclic=True)
        self.assertEqual(1, len(report["cycles"]))
        scan.validate_completed_scan(cyclic)
        with open_recipe_health(cyclic / "graph") as view:
            for key in ("seed_supply", "viability_effect"):
                changed = deepcopy(report)
                changed["cycles"][0][key] = "proven-trap"
                with self.assertRaisesRegex(scan.ScanError, "scope differs"):
                    scan.validate_cached_recipe_audit(changed, view)

    def test_json_bound_and_linked_metadata_are_refused_before_read(self):
        with patch.object(scan, "MAX_AUDIT_BYTES", 1):
            with self.assertRaisesRegex(scan.ScanError, "byte bound"):
                scan.validate_completed_scan(self.source)
        target = self.source / "request.json"
        original = target.read_bytes()
        other = self.root / "linked.json"
        other.write_bytes(original)
        target.unlink()
        os.link(other, target)
        with self.assertRaisesRegex(scan.ScanError, "independent"):
            scan.validate_completed_scan(self.source)

    def test_mutation_after_import_does_not_return_cached_success(self):
        scan.export_completed_scan(self.source, self.root / "scan.zip")
        destination = self.root / "import"
        scan.import_scan(self.root / "scan.zip", destination)
        (destination / "audit.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            scan.read_cached_recipe_audit(destination)

    def test_invalid_filters_and_cancel_are_refused_before_read(self):
        for kwargs in ({"offset": -1}, {"limit": True}, {"limit": 10001}, {"finding": "dead"}, {"text": " "}):
            with self.subTest(kwargs=kwargs), patch.object(scan, "_verified") as verify:
                with self.assertRaises(scan.ScanError):
                    scan.read_cached_recipe_audit(self.root / "missing", **kwargs)
                verify.assert_not_called()
        def cancel():
            raise ValueError("cancelled")
        with self.assertRaisesRegex(scan.ScanError, "cancelled"):
            scan.validate_completed_scan(self.source, check_cancelled=cancel)


if __name__ == "__main__":
    unittest.main()
