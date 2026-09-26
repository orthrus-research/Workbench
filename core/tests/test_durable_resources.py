"""Core resource publication, inventory, recovery and module binding."""

from __future__ import annotations

from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api import Capability, DurableResourceError, ExecutionContext, Module, ModuleError
from workbench_core.durable_files import StagedFile
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.storage.registered import CoreDurableResources, ResourceCatalog
from workbench_core.storage.record_stores import CoreRecordStores


class DurableResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.evidence = self.home / "selected-evidence"
        self.config = self.home / "config"
        self.resources = self._provider(self.evidence)

    def _provider(self, evidence: Path) -> CoreDurableResources:
        return CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": evidence}, owner_id="sample",
            policy_id="fixture-policy", location_sources={"evidence": "user-settings"},
        )

    def test_mutable_record_store_registration_is_stable_and_workspace_bound(self) -> None:
        first = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="workbench-shell",
        )
        with patch.dict(os.environ, {"WORKBENCH_STATE_ROOT": str(self.home / "state")}):
            session = first.open("work-session-v2", self.home / "session-state")
            feature = first.open("feature-change-session-context-v1", self.workspace)
            restarted = CoreRecordStores(
                workspace=self.workspace, configuration_home=self.config,
                owner_id="workbench-shell",
            )
            self.assertEqual(session, restarted.open("work-session-v2", self.home / "session-state"))
            self.assertEqual(feature, restarted.open("feature-change-session-context-v1", self.workspace))
        self.assertTrue(session.root.is_relative_to(self.home / "session-state"))
        self.assertEqual(self.home / "state/product-spine/feature-change-session-context-v1", feature.root)
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["record_stores"]
        self.assertEqual({session.store_id, feature.store_id}, {row["store_id"] for row in rows})
        self.assertTrue(all(row["status"] == "available" for row in rows))
        other_workspace = self.home / "other-workspace"
        other_workspace.mkdir()
        other = CoreRecordStores(
            workspace=other_workspace, configuration_home=self.config,
            owner_id="workbench-shell",
        ).open("work-session-v2", self.home / "other-state")
        self.assertNotEqual(session.store_id, other.store_id)
        self.assertEqual(
            [other.store_id],
            [row["store_id"] for row in ResourceCatalog(self.config).inventory(workspace=other_workspace)["record_stores"]],
        )

    def test_concurrent_record_store_open_keeps_one_registration(self) -> None:
        def opened() -> str:
            return CoreRecordStores(
                workspace=self.workspace, configuration_home=self.config,
                owner_id="workbench-shell",
            ).open("work-session-v2", self.workspace).store_id

        with ThreadPoolExecutor(max_workers=4) as workers:
            ids = list(workers.map(lambda _: opened(), range(4)))
        self.assertEqual(1, len(set(ids)))
        self.assertEqual(1, len(ResourceCatalog(self.config).inventory()["record_stores"]))
        self.assertEqual([], list((self.config / "resources-v1/stores").glob(".*.pending")))

    def test_validation_timing_store_uses_historical_workspace_root(self) -> None:
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="validation",
        )
        selected = provider.open("validation-timings-v1", self.workspace)
        self.assertEqual(
            self.workspace / ".workbench/validation/test-timings", selected.root,
        )
        self.assertEqual(selected, provider.open("validation-timings-v1", self.workspace))
        self.assertEqual(
            [selected.store_id],
            [row["store_id"] for row in ResourceCatalog(self.config).inventory(
                workspace=self.workspace,
            )["record_stores"]],
        )
        with self.assertRaises(DurableResourceError):
            provider.open("validation-timings-v1", self.home)

    def test_blueprints_sealed_store_uses_its_existing_namespace_and_owner(self) -> None:
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        root = self.workspace / ".workbench/blueprints/blueprints-session/sealed"
        with self.assertRaises(DurableResourceError):
            provider.open("work-session-v2", root)
        self.assertFalse(root.exists())
        self.assertFalse((self.config / "resources-v1/stores").exists())

        opened = provider.open("blueprints-sealed-v1", root)
        self.assertEqual(opened.root, root)
        self.assertEqual(opened, provider.open("blueprints-sealed-v1", root))
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["record_stores"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["store_id"], opened.store_id)
        self.assertEqual(rows[0]["owner_id"], "blueprints")

    def test_blueprints_dependency_cache_registers_selected_historical_root(self) -> None:
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        root = self.workspace / ".workbench/blueprints/blueprints-session/dependencies"
        opened = provider.open("blueprints-dependency-cache-v1", root)
        self.assertEqual(root, opened.root)
        self.assertEqual(opened, provider.open("blueprints-dependency-cache-v1", root))
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["record_stores"]
        self.assertEqual([(opened.store_id, str(root), "blueprints-dependency-cache-v1")],
                         [(row["store_id"], row["path"], row["family"]) for row in rows])
        with self.assertRaises(DurableResourceError):
            CoreRecordStores(workspace=self.workspace, configuration_home=self.config,
                             owner_id="workbench-shell").open("blueprints-dependency-cache-v1", root)

    def test_blueprints_simulation_evidence_registers_selected_v1_root(self) -> None:
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        root = self.workspace / ".workbench/blueprints/blueprints-session/simulation-evidence"
        opened = provider.open("blueprints-simulation-evidence-v1", root)
        self.assertEqual(root, opened.root)
        self.assertEqual(opened, provider.open("blueprints-simulation-evidence-v1", root))
        records = list((self.config / "resources-v1/stores").glob("*.json"))
        self.assertEqual(1, len(records))
        row = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual((opened.store_id, str(root), "blueprints-simulation-evidence-v1"),
                         (row["store_id"], row["root"], row["family"]))
        with self.assertRaises(DurableResourceError):
            CoreRecordStores(workspace=self.workspace, configuration_home=self.config,
                             owner_id="workbench-shell").open("blueprints-simulation-evidence-v1", root)

    def test_blueprints_artifacts_register_each_selected_v1_cas_root(self) -> None:
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        roots = [
            self.workspace / ".workbench/blueprints/release",
            self.workspace / ".workbench/blueprints/history/cas",
            self.workspace / ".workbench/blueprints/interface/session-cas",
        ]
        opened = [provider.open("blueprints-artifact-v1", root) for root in roots]
        self.assertEqual(roots, [row.root for row in opened])
        self.assertEqual(opened, [provider.open("blueprints-artifact-v1", root) for root in roots])
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.config / "resources-v1/stores").glob("*.json")
        ]
        self.assertEqual(
            {(row.store_id, str(row.root), "blueprints-artifact-v1") for row in opened},
            {(row["store_id"], row["root"], row["family"]) for row in records},
        )
        with self.assertRaises(DurableResourceError):
            CoreRecordStores(workspace=self.workspace, configuration_home=self.config,
                             owner_id="workbench-shell").open("blueprints-artifact-v1", roots[0])

    def test_blueprints_session_pointer_registers_its_v1_directory(self) -> None:
        root = self.workspace / ".workbench/blueprints/session-one"
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        opened = provider.open("blueprints-session-pointer-v1", root)
        self.assertEqual(root, opened.root)
        self.assertEqual(opened, provider.open("blueprints-session-pointer-v1", root))
        with self.assertRaises(DurableResourceError):
            CoreRecordStores(workspace=self.workspace, configuration_home=self.config,
                             owner_id="workbench-shell").open("blueprints-session-pointer-v1", root)

    def test_blueprints_history_transaction_registers_exact_recovery_root(self) -> None:
        root = self.workspace / ".workbench/blueprints/history"
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        opened = provider.open("blueprints-history-transaction-v1", root)
        self.assertEqual(root, opened.root)
        self.assertEqual(opened, provider.open("blueprints-history-transaction-v1", root))
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["record_stores"]
        self.assertEqual([(opened.store_id, str(root), "blueprints-history-transaction-v1")],
                         [(row["store_id"], row["path"], row["family"]) for row in rows])
        with self.assertRaises(DurableResourceError):
            CoreRecordStores(workspace=self.workspace, configuration_home=self.config,
                             owner_id="workbench-shell").open("blueprints-history-transaction-v1", root)

    def test_blueprints_store_rejects_other_target_before_catalog_registration(self) -> None:
        other = self.home / "other-workspace"
        other.mkdir()
        provider = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="blueprints",
        )
        families = {
            "blueprints-session-pointer-v1": "session",
            "blueprints-sealed-v1": "session/sealed",
            "blueprints-dependency-cache-v1": "session/dependencies",
            "blueprints-simulation-evidence-v1": "session/simulation-evidence",
            "blueprints-artifact-v1": "session/session-cas",
            "blueprints-history-transaction-v1": "session/history",
        }
        for family, suffix in families.items():
            with self.subTest(family=family):
                foreign = other / ".workbench/blueprints" / suffix
                with self.assertRaises(DurableResourceError) as caught:
                    provider.open(family, foreign)
                self.assertEqual(caught.exception.code, "resource.policy")
                self.assertFalse(foreign.exists())
        self.assertFalse((self.config / "resources-v1").exists())

    def test_dispatch_binds_core_and_inventory_sees_external_root(self) -> None:
        captured = []

        def run(argv, *, context):
            self.assertEqual(argv, ["now"])
            captured.append(context.publish_bytes("evidence", "result.json", b'{"ok":true}\n'))
            return 0

        context = ExecutionContext(
            self.workspace, self.home / "state",
            locations={"evidence": self.evidence, "logs": self.home / "logs"},
            configuration_home=self.config, environment_resolution_id="resolved-policy",
            location_sources={"evidence": "user-settings"},
        )
        module = InstalledModule(
            "sample", "workbench-sample", "0.1.0", "available",
            module=Module("sample", "0.1.0", (
                Capability("sample.run", ("sample",), "sample_plugin:run", "run"),
            )),
        )
        with patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}):
            self.assertEqual(dispatch(["sample", "now"], context, (module,)), 0)
        reference = captured[0]
        self.assertTrue(reference.path.is_relative_to(self.evidence))
        self.assertEqual(reference.policy_id, "resolved-policy")
        self.assertEqual(ResourceCatalog(self.config).read_bytes(reference.resource_id), b'{"ok":true}\n')
        inventory = ResourceCatalog(self.config).inventory(workspace=self.workspace)
        self.assertEqual(inventory["resources"][0]["status"], "committed")
        self.assertEqual(inventory["resources"][0]["role_source"], "user-settings")

    def test_create_new_never_clobbers_existing_explicit_output(self) -> None:
        target = self.workspace / "reports/result.json"
        target.parent.mkdir()
        target.write_bytes(b"winner\n")
        with self.assertRaises(DurableResourceError) as caught:
            self.resources.publish_bytes(
                "evidence", "result.json", b"loser\n",
                requested_path=Path("reports/result.json"),
            )
        self.assertEqual(caught.exception.code, "output.exists")
        self.assertEqual(target.read_bytes(), b"winner\n")
        self.assertEqual(list(target.parent.glob(".workbench-resource-*.pending")), [])
        self.assertEqual(ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]["status"], "failed")

    def test_post_publish_interruption_is_reconciled_from_original_inode(self) -> None:
        original = self.resources.catalog._write

        def interrupted(name, *args, **keywords):
            if name == "commits":
                raise OSError("simulated process interruption after link")
            return original(name, *args, **keywords)

        with patch.object(self.resources.catalog, "_write", side_effect=interrupted):
            with self.assertRaises(DurableResourceError) as caught:
                self.resources.publish_bytes("evidence", "result.json", b"retained\n")
        self.assertEqual(caught.exception.code, "output.write")
        row = ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]
        self.assertEqual(row["status"], "published-uncommitted")
        with self.assertRaises(DurableResourceError):
            self.resources.read_bytes(row["resource_id"])
        reference = ResourceCatalog(self.config).reconcile(row["resource_id"])
        self.assertEqual(reference.path.read_bytes(), b"retained\n")
        self.assertEqual(self.resources.read_bytes(reference.resource_id), b"retained\n")
        self.assertEqual(ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]["status"], "committed")

    def test_commit_survives_interrupted_temporary_cleanup(self) -> None:
        original_close = StagedFile.close

        def interrupted_cleanup(stage, *, cleanup):
            original_close(stage, cleanup=False)

        with patch.object(StagedFile, "close", interrupted_cleanup):
            reference = self.resources.publish_bytes("evidence", "result.json", b"committed\n")
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.inventory(workspace=self.workspace)["resources"][0]["status"], "committed-needs-reconcile")
        catalog.reconcile(reference.resource_id)
        self.assertEqual(catalog.read_bytes(reference.resource_id), b"committed\n")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["resources"][0]["status"], "committed")

    def test_committed_reference_survives_cleanup_io_error(self) -> None:
        original_close = StagedFile.close

        def failed_cleanup(stage, *, cleanup):
            original_close(stage, cleanup=False)
            raise OSError("simulated temporary cleanup error")

        with patch.object(StagedFile, "close", failed_cleanup):
            reference = self.resources.publish_bytes("evidence", "result.json", b"committed\n")
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.inventory(workspace=self.workspace)["resources"][0]["status"], "committed-needs-reconcile")
        catalog.reconcile(reference.resource_id)
        self.assertEqual(catalog.read_bytes(reference.resource_id), b"committed\n")

    def test_cancellation_after_intent_retains_failed_record_without_output(self) -> None:
        calls = 0

        def cancellation():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ModuleError("operation cancelled")

        provider = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="sample",
            check_cancelled=cancellation,
        )
        with self.assertRaisesRegex(ModuleError, "cancelled"):
            provider.publish_bytes("evidence", "result.json", b"never published\n")
        row = ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]
        self.assertEqual(row["status"], "failed")
        self.assertFalse(Path(row["path"]).exists())

    def test_process_death_after_staging_leaves_visible_incomplete_reservation(self) -> None:
        source = """
import os
from pathlib import Path
import sys
from workbench_core.storage import registered
original = registered.ResourceCatalog._write
def interrupted(self, name, *args, **kwargs):
    if name == 'intents':
        os._exit(73)
    return original(self, name, *args, **kwargs)
registered.ResourceCatalog._write = interrupted
provider = registered.CoreDurableResources(
    workspace=Path(sys.argv[1]), configuration_home=Path(sys.argv[2]),
    locations={'evidence': Path(sys.argv[3])}, owner_id='sample',
)
provider.publish_bytes('evidence', 'result.json', b'incomplete\\n')
"""
        completed = subprocess.run(
            [sys.executable, "-c", source, str(self.workspace), str(self.config), str(self.evidence)],
            env=os.environ.copy(), capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 73, completed.stderr)
        row = ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]
        self.assertEqual(row["status"], "incomplete")
        self.assertFalse(Path(row["path"]).exists())
        with self.assertRaises(DurableResourceError):
            ResourceCatalog(self.config).reconcile(row["resource_id"])

    def test_old_store_reopens_after_role_location_changes(self) -> None:
        reference = self.resources.publish_bytes("evidence", "result.json", b"old store\n")
        newly_resolved = self._provider(self.home / "later-evidence")
        self.assertEqual(newly_resolved.describe(reference.resource_id).path, reference.path)
        self.assertEqual(newly_resolved.read_bytes(reference.resource_id), b"old store\n")
        self.assertEqual(ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]["store_root"], str(self.evidence))

    def test_dependencies_are_verified_and_bound_at_publication(self) -> None:
        source = self.resources.publish_bytes("evidence", "source.json", b"source\n")
        result = self.resources.publish_bytes(
            "evidence", "result.json", b"derived\n",
            references=(source.resource_id,),
        )
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"]
        relation = next(row for row in rows if row["resource_id"] == result.resource_id)
        self.assertEqual(relation["references"], [source.resource_id])
        source.path.write_bytes(b"mutated\n")
        with self.assertRaises(DurableResourceError) as caught:
            self.resources.publish_bytes(
                "evidence", "other.json", b"other\n",
                references=(source.resource_id,),
            )
        self.assertEqual(caught.exception.code, "resource.changed")
        self.assertEqual(len(ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"]), 2)

    def test_bound_port_refuses_another_workspace_resource(self) -> None:
        reference = self.resources.publish_bytes("evidence", "result.json", b"private workspace\n")
        other = self.home / "other-workspace"
        other.mkdir()
        foreign_port = CoreDurableResources(
            workspace=other, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="sample",
        )
        with self.assertRaises(DurableResourceError) as caught:
            foreign_port.read_bytes(reference.resource_id)
        self.assertEqual(caught.exception.code, "resource.scope")

    def test_verified_read_rejects_changed_bytes_and_explicit_escape(self) -> None:
        with self.assertRaises(DurableResourceError) as escape:
            self.resources.publish_bytes(
                "evidence", "outside.json", b"unsafe\n",
                requested_path=self.home / "outside.json",
            )
        self.assertEqual(escape.exception.code, "output.path")
        reference = self.resources.publish_bytes("evidence", "result.json", b"expected\n")
        reference.path.write_bytes(b"changed!\n")
        with self.assertRaises(DurableResourceError) as changed:
            self.resources.read_bytes(reference.resource_id)
        self.assertEqual(changed.exception.code, "resource.changed")
        self.assertEqual(ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]["status"], "changed")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "symlink"), "POSIX directory custody")
    def test_parent_swap_does_not_publish_into_redirected_directory(self) -> None:
        reports = self.workspace / "reports"
        reports.mkdir()
        retired = self.workspace / "retired"
        attacker = self.workspace / "attacker"
        attacker.mkdir()
        original_link = os.link
        swapped = False

        def racing_link(source, destination, *args, **keywords):
            nonlocal swapped
            if destination == "result.json" and not swapped:
                swapped = True
                reports.rename(retired)
                reports.symlink_to(attacker, target_is_directory=True)
            return original_link(source, destination, *args, **keywords)

        with patch("workbench_core.durable_files.os.link", side_effect=racing_link):
            with self.assertRaises(DurableResourceError) as caught:
                self.resources.publish_bytes(
                    "evidence", "result.json", b"trusted\n",
                    requested_path=Path("reports/result.json"),
                )
        self.assertTrue(swapped)
        self.assertEqual(caught.exception.code, "output.changed")
        self.assertFalse((attacker / "result.json").exists())
        self.assertEqual((retired / "result.json").read_bytes(), b"trusted\n")
        row = ResourceCatalog(self.config).inventory(workspace=self.workspace)["resources"][0]
        with self.assertRaises(DurableResourceError):
            self.resources.read_bytes(row["resource_id"])

    def test_storage_cli_exposes_registered_resources_without_cleanup_authority(self) -> None:
        from workbench_core import cli

        reference = self.resources.publish_bytes("evidence", "result.json", b"listed\n")
        another_workspace = self.home / "another-workspace"
        another_workspace.mkdir()
        other_reference = CoreDurableResources(
            workspace=another_workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="sample",
        ).publish_bytes("evidence", "other.json", b"another\n")
        output = io.StringIO()
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            with patch.object(cli, "resolve_environment", side_effect=ValueError("broken settings")):
                with redirect_stdout(output):
                    self.assertEqual(cli._dispatch_available(["storage", "resources", "list", "--json"], self.workspace, ()), 0)
        record = json.loads(output.getvalue())
        self.assertEqual({row["resource_id"] for row in record["resources"]}, {reference.resource_id, other_reference.resource_id})
        self.assertTrue(all(row["retention"] == "protected-until-reviewed-policy" for row in record["resources"]))

        self.config.joinpath("setup-v1.json").write_text("{", encoding="utf-8")
        output = io.StringIO()
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            with redirect_stdout(output):
                self.assertEqual(cli._main(["storage", "resources", "list", "--json"]), 0)
        self.assertEqual({row["resource_id"] for row in json.loads(output.getvalue())["resources"]}, {reference.resource_id, other_reference.resource_id})

    def test_existing_cleanup_plan_protects_registered_child(self) -> None:
        from workbench_core.storage.manager import inventory_storage, plan_cleanup

        output = Path(".workbench/cache/report.json")
        reference = self.resources.publish_bytes(
            "evidence", output.name, b"retained\n", requested_path=output,
        )
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            report = inventory_storage(self.workspace)
            item = next(row for row in report["items"] if row["path"] == str(reference.path))
            self.assertEqual(item["deletion"]["state"], "protected")
            self.assertIn("registered-resource", item["deletion"]["reason_codes"])
            plan = plan_cleanup(self.workspace, selector=item["item_id"])
            self.assertEqual(plan["status"], "blocked")

    def test_cleanup_protects_registered_mutable_store(self) -> None:
        from workbench_core.storage.manager import inventory_storage

        store = CoreRecordStores(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="workbench-shell",
        ).open("work-session-v2", self.workspace)
        (store.root / "retained.json").write_bytes(b"retained\n")
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            report = inventory_storage(self.workspace)
        containing = [
            row for row in report["items"]
            if store.root == Path(row["path"]) or store.root.is_relative_to(Path(row["path"]))
        ]
        self.assertTrue(containing)
        self.assertTrue(all(row["deletion"]["state"] == "protected" for row in containing))
        self.assertTrue(all("registered-resource" in row["deletion"]["reason_codes"] for row in containing))

    def test_cleanup_fails_closed_when_catalog_record_is_corrupt(self) -> None:
        from workbench_core.storage.manager import inventory_storage

        output = Path(".workbench/cache/report.json")
        reference = self.resources.publish_bytes(
            "evidence", output.name, b"retained\n", requested_path=output,
        )
        nonce = reference.resource_id.rsplit(":", 1)[1]
        (self.config / "resources-v1/intents" / f"{nonce}.json").write_bytes(b"corrupted\n")
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            report = inventory_storage(self.workspace)
        item = next(row for row in report["items"] if row["path"] == str(reference.path))
        self.assertEqual(item["deletion"]["state"], "protected")
        self.assertIn("registered-catalog-unavailable", item["deletion"]["reason_codes"])

    def test_cleanup_fails_closed_when_reservation_is_missing(self) -> None:
        from workbench_core.storage.manager import inventory_storage

        reference = self.resources.publish_bytes(
            "evidence", "report.json", b"retained\n",
            requested_path=Path(".workbench/cache/report.json"),
        )
        nonce = reference.resource_id.rsplit(":", 1)[1]
        (self.config / "resources-v1/reservations" / f"{nonce}.json").unlink()
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            report = inventory_storage(self.workspace)
        item = next(row for row in report["items"] if row["path"] == str(reference.path))
        self.assertEqual(item["deletion"]["state"], "protected")
        self.assertIn("registered-catalog-unavailable", item["deletion"]["reason_codes"])
        self.assertTrue(any("catalog is unavailable" in value for value in report["limitations"]))


if __name__ == "__main__":
    unittest.main()
