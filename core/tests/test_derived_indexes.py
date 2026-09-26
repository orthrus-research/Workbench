"""Atlas derived-index publication through Core's recoverable physical port."""

from __future__ import annotations

from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ATLAS = ROOT / "modules/atlas/src"
if str(ATLAS) not in sys.path:
    sys.path.insert(0, str(ATLAS))

from workbench_api.derived_indexes import (
    DerivedIndexError, derived_indexes, derived_indexes_scope,
)
from workbench_atlas_categorical_graph import (
    CategoricalGraphBundleBuilder, node_record, rebuild_query_index,
    validate_bundle_directory, verify_query_index,
)
from workbench_core import derived_indexes as core_module
from workbench_core.derived_indexes import CoreDerivedIndexes
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.storage.registered import ResourceCatalog


def _graph(root: Path, *, observation: bool = False) -> dict:
    builder = CategoricalGraphBundleBuilder(
        root, scope={"test": "derived-custody"}, evidence_binding={"fixture": "one"},
        evidence_authority=("retained-observations-v1" if observation else "crucible-occurrence-v1"),
    )
    builder.add_partition(
        "one", classification="fixture", dependencies=(),
        nodes=[node_record("fixture", "one", {})], edges=[], evidence_categories=("fixture",),
    )
    return builder.close()


def _candidate(manifest: dict, data: bytes) -> bytes:
    candidate = dict(manifest)
    candidate["query_index"] = {
        "role": "derived-disposable-index", "file": "query-index.sqlite3",
        "size": len(data), "sha256": sha256(data).hexdigest(),
        "derived_from_graph_set_id": manifest["graph_set_id"],
    }
    return json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"


class DerivedIndexCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.config = self.base / "config"
        self.host = CoreDerivedIndexes(configuration_home=self.config)

    def test_dispatch_registers_external_index_attempts_against_selected_workspace(self) -> None:
        workspace = self.base / "selected-workspace"
        workspace.mkdir()
        graph = self.base / "external-evidence" / "graph"
        graph.parent.mkdir()
        manifest = _graph(graph)
        host = CoreDerivedIndexes(configuration_home=self.config, workspace=workspace)
        with host.stage(graph, graph_set_id=manifest["graph_set_id"]) as stage:
            self.assertTrue(stage.path.parent.is_dir())
            rows = ResourceCatalog(self.config).inventory(workspace=workspace)["record_stores"]
            self.assertEqual(1, len(rows))
            self.assertEqual("atlas-derived-index-v1", rows[0]["family"])
            self.assertEqual("atlas", rows[0]["owner_id"])
            self.assertEqual(str(graph.parent / f".{graph.name}.derived-index-core"), rows[0]["path"])
            self.assertEqual([], ResourceCatalog(self.config).inventory(
                workspace=self.base / "unselected",
            )["record_stores"])
        self.assertEqual("available", rows[0]["status"])

    def test_missing_and_corrupt_prior_index_rebuild_preserves_manifest_mode(self) -> None:
        root = self.base / "graph"
        source = _graph(root)
        mode = (root / "manifest.json").stat().st_mode & 0o777
        (root / "query-index.sqlite3").unlink()
        with derived_indexes_scope(self.host):
            first = rebuild_query_index(root)
            self.assertEqual(source["graph_set_id"], first["graph_set_id"])
            (root / "query-index.sqlite3").write_bytes(b"corrupt SQLite")
            second = rebuild_query_index(root)
        self.assertEqual(first["graph_set_id"], second["graph_set_id"])
        self.assertEqual(mode, (root / "manifest.json").stat().st_mode & 0o777)
        self.assertEqual(second["graph_set_id"], verify_query_index(root)["graph_set_id"])
        self.assertEqual(["complete", "superseded"], sorted(row.state for row in self.host.inspect(root)))
        self.assertFalse(any(path.name.startswith(".query-index-rebuild-") for path in root.iterdir()))

    def _managed(self, *, derived_rule: bool, observation: bool) -> tuple[Path, dict, CoreManagedTrees]:
        workspace = self.base / ("workspace-v2" if derived_rule else "workspace-v1")
        workspace.mkdir()
        evidence = workspace / "evidence"
        owner = CoreManagedTrees(
            workspace=workspace, configuration_home=self.config,
            locations={"evidence": evidence}, owner_id="atlas",
        )
        target = evidence / ("graph-v2" if derived_rule else "graph-v1")
        with owner.stage("evidence", target.name, requested_path=target) as stage:
            manifest = _graph(stage.path, observation=observation)
            stage.publish(
                validate=validate_bundle_directory, domain_id=manifest["graph_set_id"],
                derived_members=("query-index.sqlite3",),
                derived_manifest_rule="atlas-categorical-query-index-v1" if derived_rule else None,
            )
        return target, manifest, owner

    def test_managed_v2_v3_rebuild_and_historical_v1_refusal(self) -> None:
        current, manifest, owner = self._managed(derived_rule=True, observation=True)
        (current / "query-index.sqlite3").unlink()
        selected = CoreDerivedIndexes(configuration_home=self.config, workspace=owner.workspace)
        with derived_indexes_scope(selected):
            rebuilt = rebuild_query_index(current)
        self.assertEqual(manifest["graph_set_id"], rebuilt["graph_set_id"])
        self.assertEqual(0o644, (current / "manifest.json").stat().st_mode & 0o777)
        self.assertEqual("current", owner.describe(next(
            row["tree_id"] for row in owner.catalog.trees.inventory()
            if row["path"] == str(current)
        )).derived_status)
        old, old_manifest, _ = self._managed(derived_rule=False, observation=False)
        with derived_indexes_scope(CoreDerivedIndexes(configuration_home=self.config)):
            with self.assertRaises(DerivedIndexError) as raised:
                rebuild_query_index(old)
        self.assertEqual("managed", raised.exception.code)
        self.assertFalse((old.parent / f".{old.name}.derived-index-core").exists())
        self.assertEqual(old_manifest, validate_bundle_directory(old))

    def test_owner_validation_detects_manifest_and_stage_mutation(self) -> None:
        root = self.base / "graph"
        manifest = _graph(root)
        data = b"replacement"
        with self.host.stage(root, graph_set_id=manifest["graph_set_id"]) as stage:
            stage.path.write_bytes(data)

            def change_manifest(path: Path) -> dict:
                selected = path / "manifest.json"
                selected.write_bytes(selected.read_bytes())
                return manifest

            with self.assertRaises(DerivedIndexError) as raised:
                stage.publish(manifest_bytes=_candidate(manifest, data),
                              expected_size=len(data), expected_sha256=sha256(data).hexdigest(),
                              validate_source=change_manifest)
            self.assertEqual("changed", raised.exception.code)
        self.assertEqual(manifest, validate_bundle_directory(root))
        with self.host.stage(root, graph_set_id=manifest["graph_set_id"]) as stage:
            stage.path.write_bytes(data)

            def change_stage(_: Path) -> dict:
                stage.path.write_bytes(b"mutated")
                return manifest

            with self.assertRaises(DerivedIndexError) as raised:
                stage.publish(manifest_bytes=_candidate(manifest, data),
                              expected_size=len(data), expected_sha256=sha256(data).hexdigest(),
                              validate_source=change_stage)
            self.assertEqual("stage", raised.exception.code)
        self.assertEqual(manifest, validate_bundle_directory(root))

    def test_unprepared_stage_keeps_unrecognized_child(self) -> None:
        root = self.base / "graph"
        manifest = _graph(root)
        with self.host.stage(root, graph_set_id=manifest["graph_set_id"]) as stage:
            child = stage.path.parent / "external-child"
            child.write_bytes(b"do not delete")
        self.assertEqual(b"do not delete", child.read_bytes())
        self.assertEqual(["allocated"], [attempt.state for attempt in self.host.inspect(root)])

    def test_restart_classifies_both_replacement_boundaries_and_explicit_repair(self) -> None:
        for stop_at in ("index", "manifest"):
            with self.subTest(stop_at=stop_at):
                root = self.base / f"graph-{stop_at}"
                manifest = _graph(root)
                data = b"replacement"
                with self.host.stage(root, graph_set_id=manifest["graph_set_id"]) as stage:
                    stage.path.write_bytes(data)
                    if stop_at == "index":
                        original = core_module.os.replace
                        calls = 0

                        def interrupt(source: Path, target: Path) -> None:
                            nonlocal calls
                            calls += 1
                            if calls == 2:
                                raise OSError("simulated stop before manifest replacement")
                            original(source, target)

                        patcher = mock.patch.object(core_module.os, "replace", side_effect=interrupt)
                    else:
                        original_record = core_module._write_record

                        def interrupt_record(path: Path, kind: str, body: dict) -> dict:
                            if path.name == "complete.json":
                                raise OSError("simulated stop after manifest replacement")
                            return original_record(path, kind, body)

                        patcher = mock.patch.object(core_module, "_write_record", side_effect=interrupt_record)
                    with patcher:
                        with self.assertRaises(OSError):
                            stage.publish(manifest_bytes=_candidate(manifest, data),
                                expected_size=len(data), expected_sha256=sha256(data).hexdigest(),
                                validate_source=lambda _: manifest)
                self.assertEqual(
                    ["index-replaced" if stop_at == "index" else "manifest-replaced"],
                    [item.state for item in self.host.inspect(root)],
                )
                with derived_indexes_scope(self.host):
                    rebuilt = rebuild_query_index(root)
                self.assertEqual(manifest["graph_set_id"], rebuilt["graph_set_id"])
                self.assertEqual(["complete", "superseded"],
                                 sorted(item.state for item in self.host.inspect(root)))
                verify_query_index(root)

    def test_graph_replacement_before_publication_is_refused(self) -> None:
        root = self.base / "graph"
        manifest = _graph(root)
        data = b"replacement"
        with self.host.stage(root, graph_set_id=manifest["graph_set_id"]) as stage:
            stage.path.write_bytes(data)

            def replace_root(_: Path) -> dict:
                root.rename(self.base / "old-graph")
                _graph(root)
                return manifest

            with self.assertRaises(DerivedIndexError) as raised:
                stage.publish(manifest_bytes=_candidate(manifest, data),
                    expected_size=len(data), expected_sha256=sha256(data).hexdigest(),
                    validate_source=replace_root)
            self.assertEqual("changed", raised.exception.code)
        self.assertEqual(manifest, validate_bundle_directory(root))

    def test_parent_replacement_before_publication_is_refused(self) -> None:
        parent = self.base / "selected"
        parent.mkdir()
        root = parent / "graph"
        manifest = _graph(root)
        data = b"replacement"
        with self.host.stage(root, graph_set_id=manifest["graph_set_id"]) as stage:
            stage.path.write_bytes(data)

            def replace_parent(_: Path) -> dict:
                parent.rename(self.base / "old-selected")
                parent.mkdir()
                shutil.copytree(self.base / "old-selected" / "graph", root)
                return manifest

            with self.assertRaises(DerivedIndexError) as raised:
                stage.publish(manifest_bytes=_candidate(manifest, data),
                    expected_size=len(data), expected_sha256=sha256(data).hexdigest(),
                    validate_source=replace_parent)
            self.assertEqual("changed", raised.exception.code)
        self.assertEqual(manifest, validate_bundle_directory(root))
        self.assertFalse((parent / f".{root.name}.derived-index-core").exists())

    def test_supported_direct_atlas_index_commands_compose_core(self) -> None:
        from workbench_atlas_observations.cli import main as observations_main
        from workbench_atlas_recipe_health.cli import main as recipes_main

        for name, entry in (("recipe", recipes_main), ("observation", observations_main)):
            with self.subTest(name=name):
                root = self.base / name
                original = _graph(root, observation=name == "observation")
                (root / "query-index.sqlite3").unlink()
                output, error = StringIO(), StringIO()
                with mock.patch.dict("os.environ", {"WORKBENCH_CONFIG_HOME": str(self.config)}):
                    status = entry(
                        ["index", str(root), "--max-source-bytes", "1000000",
                         "--max-index-bytes", "1000000"],
                        output=output, error=error,
                    )
                self.assertEqual(0, status, error.getvalue())
                self.assertEqual(original["graph_set_id"], verify_query_index(root)["graph_set_id"])
                self.assertEqual(["complete"], [attempt.state for attempt in self.host.inspect(root)])

    def test_explicit_unavailable_dispatch_scope_does_not_use_direct_fallback(self) -> None:
        from workbench_core.host_services import direct_atlas_derived_index_scope

        with derived_indexes_scope(None), direct_atlas_derived_index_scope():
            with self.assertRaises(DerivedIndexError) as raised:
                derived_indexes()
        self.assertEqual("unavailable", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
