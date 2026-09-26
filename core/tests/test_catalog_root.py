"""Resource catalog root identity and conservative V1 migration."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import DurableResourceError
from workbench_core.durable_records import publish_immutable_bytes
from workbench_core.storage import issuance
from workbench_core.storage.registered import CoreDurableResources, ResourceCatalog
from workbench_core.temporary_leases import CoreTemporaryLeases


class CatalogRootTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.evidence = self.home / "selected-evidence"
        self.config = self.home / "config"
        self.resources = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="sample",
            policy_id="fixture-policy", location_sources={"evidence": "user-settings"},
        )
        self.witnessed = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="sample",
            policy_id="fixture-policy", location_sources={"evidence": "user-settings"},
            post_birth_issuance=True,
        )

    def test_catalog_root_manifest_marks_first_install_and_survives_restart(self) -> None:
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.verify_root(), "unproven-empty-home")
        reference = self.resources.publish_bytes("evidence", "first.json", b"first\n")
        outer = self.config / "resource-catalog-root-v1.json"
        inner = self.config / "resources-v1/.resource-catalog-root-v1.json"
        original = outer.read_bytes()
        self.assertEqual(original, inner.read_bytes())
        marker = json.loads(original)
        self.assertEqual(marker["format"], "workbench-resource-catalog-root-v1")
        self.assertEqual(marker["schema_version"], 1)
        self.assertEqual(marker["generation"], 1)
        self.assertEqual(marker["migration_origin"], "empty-home-first-use")
        self.assertEqual(marker["catalog_format"], "workbench-resource-catalog-v1")
        self.assertIn("working-allocations", marker["known_namespaces"])
        self.assertEqual(ResourceCatalog(self.config).verify_root(), "ready-unproven")
        self.assertEqual(ResourceCatalog(self.config).read_bytes(reference.resource_id), b"first\n")
        self.resources.publish_bytes("evidence", "second.json", b"second\n")
        self.assertEqual(outer.read_bytes(), original)

    def test_fresh_root_issues_exact_file_before_publication_and_reopens_candidate(self) -> None:
        first = self.witnessed.publish_bytes("evidence", "first.json", b"first\n")
        second = self.witnessed.publish_bytes("evidence", "second.json", b"second\n")
        catalog = ResourceCatalog(self.config)
        marker = catalog.fresh_root_epoch()
        self.assertIsNotNone(marker)
        self.assertEqual("workbench-resource-catalog-root-v2", marker["format"])
        self.assertRegex(marker["root_epoch"], r"^[0-9a-f]{32}$")
        workspace_rows = self.workspace / ".workbench/resource-issuance-v2" / marker["root_epoch"]
        config_rows = (self.config / "resource-issuance-v2" / marker["root_epoch"]
                       / sha256(os.fsencode(self.workspace)).hexdigest())
        self.assertEqual(["0000000000000001.json", "0000000000000002.json"],
                         sorted(path.name for path in workspace_rows.iterdir()))
        for name in ("0000000000000001.json", "0000000000000002.json"):
            self.assertEqual((workspace_rows / name).read_bytes(), (config_rows / name).read_bytes())
        for reference in (first, second):
            self.assertEqual("post-birth-covered", ResourceCatalog(self.config).post_birth_coverage(
                reference.resource_id, workspace=self.workspace, owner_id="sample", target=reference.path,
            ))
        self.assertEqual({
            "status": "current-paired-prefix", "paired_rows": 2,
            "issue_id": None, "resource_id": None, "historical_completeness": "unproven",
        }, catalog.inspect_post_birth_issue_gap(workspace=self.workspace))
        self.assertEqual("unproven", catalog.post_birth_coverage(
            first.resource_id, workspace=self.workspace, owner_id="other", target=first.path,
        ))
        self.assertEqual("ready-unproven", catalog.inventory(workspace=self.workspace)["root_state"])

    def test_default_file_route_does_not_claim_post_birth_coverage(self) -> None:
        reference = self.resources.publish_bytes("evidence", "ordinary.json", b"ordinary\n")
        self.assertIsNone(ResourceCatalog(self.config).fresh_root_epoch())
        with self.assertRaises(DurableResourceError) as unsupported:
            ResourceCatalog(self.config).inspect_post_birth_issue_gap(workspace=self.workspace)
        self.assertEqual("resource.unsupported", unsupported.exception.code)
        self.assertEqual("unproven", ResourceCatalog(self.config).post_birth_coverage(
            reference.resource_id, workspace=self.workspace, owner_id="sample", target=reference.path,
        ))

    def test_missing_or_empty_v2_witness_does_not_prove_no_prior_issue(self) -> None:
        catalog = ResourceCatalog(self.config)
        catalog._ensure(fresh_epoch=True)
        with self.assertRaises(DurableResourceError) as missing:
            catalog.inspect_post_birth_issue_gap(workspace=self.workspace)
        self.assertEqual("resource.unavailable", missing.exception.code)
        with patch.object(issuance, "publish_immutable_bytes", side_effect=RuntimeError("before issue row")):
            with self.assertRaisesRegex(RuntimeError, "before issue row"):
                self.witnessed.publish_bytes("evidence", "pending.json", b"pending\n")
        with self.assertRaises(DurableResourceError) as empty:
            catalog.inspect_post_birth_issue_gap(workspace=self.workspace)
        self.assertEqual("resource.unavailable", empty.exception.code)
        self.assertEqual("ready-unproven", catalog.inventory(workspace=self.workspace)["root_state"])

    def test_unissued_file_under_v2_root_remains_unproven(self) -> None:
        covered = self.witnessed.publish_bytes("evidence", "covered.json", b"covered\n")
        ordinary = self.resources.publish_bytes("evidence", "ordinary.json", b"ordinary\n")
        catalog = ResourceCatalog(self.config)
        self.assertEqual("post-birth-covered", catalog.post_birth_coverage(
            covered.resource_id, workspace=self.workspace, owner_id="sample", target=covered.path,
        ))
        self.assertEqual("unproven", catalog.post_birth_coverage(
            ordinary.resource_id, workspace=self.workspace, owner_id="sample", target=ordinary.path,
        ))

    def test_issuance_gap_after_workspace_row_blocks_future_publication(self) -> None:
        catalog = ResourceCatalog(self.config)
        script = """
import os
from pathlib import Path
import sys
from workbench_core.storage import issuance
from workbench_core.storage.registered import CoreDurableResources

original = issuance.publish_immutable_bytes
def interrupt_config(path, data, *, byte_limit, idempotent=False):
    if path.is_relative_to(Path(sys.argv[2]) / 'resource-issuance-v2'):
        os._exit(73)
    original(path, data, byte_limit=byte_limit, idempotent=idempotent)
issuance.publish_immutable_bytes = interrupt_config
provider = CoreDurableResources(
    workspace=Path(sys.argv[1]), configuration_home=Path(sys.argv[2]),
    locations={'evidence': Path(sys.argv[3])}, owner_id='sample',
    post_birth_issuance=True,
)
provider.publish_bytes('evidence', 'interrupted.json', b'pending\\n')
"""
        project = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(project / "api/src"), str(project / "core/src"),
            environment.get("PYTHONPATH", ""),
        ))
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.workspace), str(self.config), str(self.evidence)],
            env=environment, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(73, child.returncode, child.stderr)
        self.assertFalse(self.evidence.exists())
        epoch = catalog.fresh_root_epoch()["root_epoch"]
        workspace_issue = (self.workspace / ".workbench/resource-issuance-v2" / epoch
                           / "0000000000000001.json")
        retained_bytes = workspace_issue.read_bytes()
        orphan = json.loads(retained_bytes)
        self.assertEqual({
            "status": "current-workspace-only-unreserved-tail", "paired_rows": 0,
            "issue_id": orphan["id"], "resource_id": orphan["resource_id"],
            "historical_completeness": "unproven",
        }, catalog.inspect_post_birth_issue_gap(workspace=self.workspace))
        self.assertEqual(retained_bytes, workspace_issue.read_bytes())
        self.assertFalse((self.config / "resource-issuance-v2" / epoch
                          / sha256(os.fsencode(self.workspace)).hexdigest()
                          / workspace_issue.name).exists())
        with self.assertRaises(DurableResourceError) as unreadable:
            catalog.post_birth_coverage(
                orphan["resource_id"], workspace=self.workspace,
                owner_id="sample", target=Path(orphan["target"]),
            )
        self.assertEqual("resource.changed", unreadable.exception.code)
        with self.assertRaises(DurableResourceError) as gap:
            self.witnessed.publish_bytes("evidence", "later.json", b"later\n")
        self.assertEqual("resource.changed", gap.exception.code)
        self.assertEqual([], catalog.inventory(workspace=self.workspace)["resources"])

    def test_missing_earlier_issue_row_refuses_later_candidate(self) -> None:
        self.witnessed.publish_bytes("evidence", "first.json", b"first\n")
        second = self.witnessed.publish_bytes("evidence", "second.json", b"second\n")
        epoch = ResourceCatalog(self.config).fresh_root_epoch()["root_epoch"]
        row = self.workspace / ".workbench/resource-issuance-v2" / epoch / "0000000000000001.json"
        held = self.home / "held-issue.json"
        row.rename(held)
        try:
            with self.assertRaises(DurableResourceError) as gap:
                ResourceCatalog(self.config).post_birth_coverage(
                    second.resource_id, workspace=self.workspace, owner_id="sample", target=second.path,
                )
            self.assertEqual("resource.changed", gap.exception.code)
            with self.assertRaises(DurableResourceError) as inspection:
                ResourceCatalog(self.config).inspect_post_birth_issue_gap(workspace=self.workspace)
            self.assertEqual("resource.changed", inspection.exception.code)
        finally:
            held.rename(row)
        self.assertEqual("post-birth-covered", ResourceCatalog(self.config).post_birth_coverage(
            second.resource_id, workspace=self.workspace, owner_id="sample", target=second.path,
        ))

    def test_issued_without_reservation_blocks_later_opt_in_publication(self) -> None:
        original = ResourceCatalog._write

        def interrupt_reservation(catalog, name, *arguments):
            if name == "reservations":
                raise RuntimeError("simulated exit after write-ahead issue")
            return original(catalog, name, *arguments)

        with patch.object(ResourceCatalog, "_write", interrupt_reservation):
            with self.assertRaisesRegex(RuntimeError, "write-ahead issue"):
                self.witnessed.publish_bytes("evidence", "pending.json", b"pending\n")
        observed = ResourceCatalog(self.config).inspect_post_birth_issue_gap(workspace=self.workspace)
        self.assertEqual("current-paired-unreserved-tail", observed["status"])
        self.assertEqual(1, observed["paired_rows"])
        self.assertEqual("unproven", observed["historical_completeness"])
        with self.assertRaises(DurableResourceError) as incomplete:
            self.witnessed.publish_bytes("evidence", "later.json", b"later\n")
        self.assertEqual("resource.incomplete", incomplete.exception.code)
        self.assertFalse(self.evidence.exists())

    def test_one_sided_row_after_reservation_or_config_only_row_refuses_inspection(self) -> None:
        reference = self.witnessed.publish_bytes("evidence", "first.json", b"first\n")
        catalog = ResourceCatalog(self.config)
        epoch = catalog.fresh_root_epoch()["root_epoch"]
        workspace_issue = (self.workspace / ".workbench/resource-issuance-v2" / epoch
                           / "0000000000000001.json")
        config_issue = (self.config / "resource-issuance-v2" / epoch
                        / sha256(os.fsencode(self.workspace)).hexdigest()
                        / "0000000000000001.json")
        for issue in (config_issue, workspace_issue):
            with self.subTest(missing=issue):
                held = self.home / "held-issue.json"
                issue.rename(held)
                try:
                    with self.assertRaises(DurableResourceError) as gap:
                        catalog.inspect_post_birth_issue_gap(workspace=self.workspace)
                    self.assertEqual("resource.changed", gap.exception.code)
                    with self.assertRaises(DurableResourceError):
                        catalog.post_birth_coverage(
                            reference.resource_id, workspace=self.workspace,
                            owner_id="sample", target=reference.path,
                        )
                    with self.assertRaises(DurableResourceError):
                        self.witnessed.publish_bytes("evidence", "later.json", b"later\n")
                finally:
                    held.rename(issue)
        self.assertEqual("post-birth-covered", catalog.post_birth_coverage(
            reference.resource_id, workspace=self.workspace, owner_id="sample", target=reference.path,
        ))

    @unittest.skipIf(os.name == "nt", "POSIX workspace mode admission")
    def test_writable_workspace_refuses_opt_in_but_default_file_route_stays_available(self) -> None:
        self.workspace.chmod(0o777)
        with self.assertRaises(DurableResourceError) as unsafe:
            self.witnessed.publish_bytes("evidence", "witnessed.json", b"witnessed\n")
        self.assertEqual("resource.changed", unsafe.exception.code)
        self.assertFalse(self.config.exists())
        ordinary = self.resources.publish_bytes("evidence", "ordinary.json", b"ordinary\n")
        self.assertEqual(b"ordinary\n", ordinary.path.read_bytes())
        self.assertIsNone(ResourceCatalog(self.config).fresh_root_epoch())
        self.assertEqual("unproven", ResourceCatalog(self.config).post_birth_coverage(
            ordinary.resource_id, workspace=self.workspace, owner_id="sample", target=ordinary.path,
        ))

    def test_fresh_epoch_survives_inner_anchor_only_reconciliation(self) -> None:
        catalog = ResourceCatalog(self.config)
        outer = catalog._root_manifest()

        def interrupt_outer(path: Path, data: bytes, *, byte_limit: int,
                            idempotent: bool = False) -> None:
            if path == outer:
                raise RuntimeError("simulated exit before outer root marker")
            publish_immutable_bytes(path, data, byte_limit=byte_limit, idempotent=idempotent)

        with patch("workbench_core.storage.registered.publish_immutable_bytes", side_effect=interrupt_outer):
            with self.assertRaisesRegex(RuntimeError, "simulated exit"):
                catalog._ensure(fresh_epoch=True)
        anchored_epoch = json.loads(catalog._root_anchor().read_bytes())["root_epoch"]
        self.assertEqual("ready-unproven", catalog.reconcile_interrupted_root_publication())
        self.assertEqual(anchored_epoch, catalog.fresh_root_epoch()["root_epoch"])
        reference = self.witnessed.publish_bytes("evidence", "after.json", b"after\n")
        self.assertEqual("post-birth-covered", catalog.post_birth_coverage(
            reference.resource_id, workspace=self.workspace, owner_id="sample", target=reference.path,
        ))

    def test_post_birth_proof_refuses_alternate_home_and_prior_epoch(self) -> None:
        old = self.witnessed.publish_bytes("evidence", "old.json", b"old\n")
        alternate_home = self.home / "alternate-config"
        alternate = CoreDurableResources(
            workspace=self.workspace, configuration_home=alternate_home,
            locations={"evidence": self.evidence}, owner_id="sample",
            post_birth_issuance=True,
        )
        other = alternate.publish_bytes("evidence", "other.json", b"other\n")
        self.assertEqual("unproven", ResourceCatalog(alternate_home).post_birth_coverage(
            old.resource_id, workspace=self.workspace, owner_id="sample", target=old.path,
        ))
        self.assertEqual("post-birth-covered", ResourceCatalog(alternate_home).post_birth_coverage(
            other.resource_id, workspace=self.workspace, owner_id="sample", target=other.path,
        ))

        catalog = ResourceCatalog(self.config)
        catalog.root.rename(self.config / "resources-lost")
        catalog._root_manifest().rename(self.config / "resource-catalog-root-lost.json")
        new = self.witnessed.publish_bytes("evidence", "new.json", b"new\n")
        self.assertEqual("unproven", catalog.post_birth_coverage(
            old.resource_id, workspace=self.workspace, owner_id="sample", target=old.path,
        ))
        self.assertEqual("post-birth-covered", catalog.post_birth_coverage(
            new.resource_id, workspace=self.workspace, owner_id="sample", target=new.path,
        ))

    def test_post_birth_proof_refuses_same_bytes_replacement(self) -> None:
        reference = self.witnessed.publish_bytes("evidence", "first.json", b"first\n")
        replacement = self.home / "replacement.json"
        replacement.write_bytes(b"first\n")
        replacement.replace(reference.path)
        with self.assertRaises(DurableResourceError) as changed:
            ResourceCatalog(self.config).post_birth_coverage(
                reference.resource_id, workspace=self.workspace, owner_id="sample", target=reference.path,
            )
        self.assertEqual("resource.changed", changed.exception.code)

    def test_historical_v1_catalog_gains_root_manifest_without_rewriting_records(self) -> None:
        catalog = ResourceCatalog(self.config)
        historical_dirs = ("reservations", "intents", "commits", "aborts", "leases", "stores")

        def old_ensure(selected: ResourceCatalog) -> None:
            for directory in (selected.root, *(selected.root / name for name in historical_dirs)):
                directory.mkdir(parents=True, mode=0o700, exist_ok=True)

        store = self.home / "historical-store"
        store.mkdir(mode=0o700)
        with patch.object(ResourceCatalog, "_ensure", old_ensure):
            store_id = catalog.register_record_store(
                family="historical", owner_id="sample",
                workspace=self.workspace, root=store,
            )
        self.assertEqual(catalog.verify_root(), "legacy")
        historical = next((catalog.root / "stores").glob("*.json"))
        original = historical.read_bytes()
        catalog._ensure()
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertIsNone(catalog.fresh_root_epoch())
        self.assertEqual(historical.read_bytes(), original)
        self.assertEqual(
            json.loads((self.config / "resource-catalog-root-v1.json").read_bytes())["migration_origin"],
            "legacy-v1",
        )
        self.assertEqual(
            [row["store_id"] for row in catalog.inventory(workspace=self.workspace)["record_stores"]],
            [store_id],
        )
        with self.assertRaises(DurableResourceError) as unsupported:
            self.witnessed.publish_bytes("evidence", "requires-v2.json", b"no\n")
        self.assertEqual("resource.unsupported", unsupported.exception.code)
        issued_under_legacy = self.resources.publish_bytes("evidence", "legacy.json", b"legacy\n")
        self.assertEqual("unproven", catalog.post_birth_coverage(
            issued_under_legacy.resource_id, workspace=self.workspace,
            owner_id="sample", target=issued_under_legacy.path,
        ))

    def test_missing_or_split_root_binding_never_initializes_empty_catalog(self) -> None:
        reference = self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        root = catalog.root
        lost = self.config / "resources-removed"
        root.rename(lost)
        with self.assertRaises(DurableResourceError) as missing:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual(missing.exception.code, "resource.unavailable")
        with self.assertRaises(DurableResourceError):
            catalog._ensure()
        self.assertFalse(root.exists())
        self.assertEqual(reference.path.read_bytes(), b"retained\n")
        lost.rename(root)
        (self.config / "resource-catalog-root-v1.json").unlink()
        with self.assertRaises(DurableResourceError) as split:
            catalog.verify_root()
        self.assertEqual(split.exception.code, "resource.unavailable")
        with self.assertRaises(DurableResourceError):
            catalog._ensure()

    def test_interrupted_root_publication_recovers_only_from_surviving_anchor(self) -> None:
        catalog = ResourceCatalog(self.config)
        store = self.home / "historical-store"
        store.mkdir(mode=0o700)
        historical_dirs = ("reservations", "intents", "commits", "aborts", "leases", "stores")

        def old_ensure(selected: ResourceCatalog) -> None:
            for directory in (selected.root, *(selected.root / name for name in historical_dirs)):
                directory.mkdir(parents=True, mode=0o700, exist_ok=True)

        with patch.object(ResourceCatalog, "_ensure", old_ensure):
            store_id = catalog.register_record_store(
                family="historical", owner_id="sample",
                workspace=self.workspace, root=store,
            )
        registration = next((catalog.root / "stores").glob("*.json"))
        original = registration.read_bytes()
        outer = catalog._root_manifest()
        anchor = catalog._root_anchor()

        def interrupt_outer(path: Path, data: bytes, *, byte_limit: int,
                            idempotent: bool = False) -> None:
            if path == outer:
                raise RuntimeError("simulated interruption after inner anchor")
            publish_immutable_bytes(path, data, byte_limit=byte_limit, idempotent=idempotent)

        with patch("workbench_core.storage.registered.publish_immutable_bytes", side_effect=interrupt_outer):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                catalog._ensure()
        self.assertTrue(anchor.is_file())
        self.assertFalse(outer.exists())
        with self.assertRaises(DurableResourceError):
            catalog.inventory(workspace=self.workspace)
        self.assertEqual(catalog.reconcile_interrupted_root_publication(), "ready-unproven")
        self.assertEqual(anchor.read_bytes(), outer.read_bytes())
        self.assertEqual(registration.read_bytes(), original)
        self.assertEqual(
            [row["store_id"] for row in catalog.inventory(workspace=self.workspace)["record_stores"]],
            [store_id],
        )
        self.assertEqual(catalog.reconcile_interrupted_root_publication(), "ready-unproven")

    def test_root_reconcile_refuses_absent_anchor_or_changed_root(self) -> None:
        catalog = ResourceCatalog(self.config)
        with self.assertRaises(DurableResourceError) as absent:
            catalog.reconcile_interrupted_root_publication()
        self.assertEqual(absent.exception.code, "resource.unavailable")
        catalog._ensure()
        anchor = catalog._root_anchor()
        original = anchor.read_bytes()
        anchor.unlink()
        with self.assertRaises(DurableResourceError) as outer_only:
            catalog.reconcile_interrupted_root_publication()
        self.assertEqual(outer_only.exception.code, "resource.unavailable")
        self.assertFalse(anchor.exists())

        # A copied old anchor cannot bless a replacement catalog root.
        catalog._root_manifest().unlink()
        renamed = self.config / "old-resources"
        catalog.root.rename(renamed)
        catalog.root.mkdir(mode=0o700)
        for name in ("reservations", "intents", "commits", "aborts", "leases", "stores"):
            (catalog.root / name).mkdir(mode=0o700)
        anchor.write_bytes(original)
        anchor.chmod(0o600)
        with self.assertRaises(DurableResourceError) as replaced:
            catalog.reconcile_interrupted_root_publication()
        self.assertEqual(replaced.exception.code, "resource.changed")
        self.assertFalse(catalog._root_manifest().exists())

    def test_root_reconcile_refuses_home_without_private_custody(self) -> None:
        catalog = ResourceCatalog(self.config)
        outer = catalog._root_manifest()

        def interrupt_outer(path: Path, data: bytes, *, byte_limit: int,
                            idempotent: bool = False) -> None:
            if path == outer:
                raise RuntimeError("simulated interruption")
            publish_immutable_bytes(path, data, byte_limit=byte_limit, idempotent=idempotent)

        with patch("workbench_core.storage.registered.publish_immutable_bytes", side_effect=interrupt_outer):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                catalog._ensure()
        with patch("workbench_core.storage.registered.private_path", return_value=False):
            with self.assertRaises(DurableResourceError) as unsafe:
                catalog.reconcile_interrupted_root_publication()
        self.assertEqual(unsafe.exception.code, "resource.changed")
        self.assertFalse(outer.exists())

    def test_root_reconcile_does_not_publish_from_changed_anchor(self) -> None:
        catalog = ResourceCatalog(self.config)
        outer = catalog._root_manifest()

        def interrupt_outer(path: Path, data: bytes, *, byte_limit: int,
                            idempotent: bool = False) -> None:
            if path == outer:
                raise RuntimeError("simulated interruption")
            publish_immutable_bytes(path, data, byte_limit=byte_limit, idempotent=idempotent)

        with patch("workbench_core.storage.registered.publish_immutable_bytes", side_effect=interrupt_outer):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                catalog._ensure()
        anchor = catalog._root_anchor()
        anchor.write_bytes(b"{}\n")
        with self.assertRaises(DurableResourceError) as changed:
            catalog.reconcile_interrupted_root_publication()
        self.assertEqual(changed.exception.code, "resource.changed")
        self.assertFalse(outer.exists())

    def test_total_root_loss_remains_unproven_if_first_use_must_continue(self) -> None:
        retained = self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        catalog.root.rename(self.config / "resources-lost")
        (self.config / "resource-catalog-root-v1.json").rename(
            self.config / "resource-catalog-root-lost.json"
        )
        self.assertEqual(catalog.verify_root(), "unproven")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "unproven")
        fresh = self.resources.publish_bytes("evidence", "new.json", b"new\n")
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertEqual(catalog.read_bytes(fresh.resource_id), b"new\n")
        self.assertEqual(retained.path.read_bytes(), b"retained\n")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "ready-unproven")

    def test_corrupt_root_manifest_is_not_migrated_or_replaced(self) -> None:
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        marker = self.config / "resource-catalog-root-v1.json"
        marker.write_bytes(b"{}\n")
        with self.assertRaises(DurableResourceError) as corrupt:
            catalog.verify_root()
        self.assertEqual(corrupt.exception.code, "resource.changed")
        with self.assertRaises(DurableResourceError):
            catalog._ensure()
        self.assertEqual(marker.read_bytes(), b"{}\n")

    def test_preexisting_preferences_allow_first_resource_but_keep_unproven_coverage(self) -> None:
        self.config.mkdir(mode=0o700)
        (self.config / "setup-v1.json").write_bytes(b"{}\n")
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.verify_root(), "unproven")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "unproven")
        reference = self.resources.publish_bytes("evidence", "first.json", b"first\n")
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertEqual(catalog.read_bytes(reference.resource_id), b"first\n")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "ready-unproven")
        marker = json.loads((self.config / "resource-catalog-root-v1.json").read_bytes())
        self.assertEqual(marker["migration_origin"], "unproven-first-use")

    def test_partial_legacy_root_is_not_auto_completed(self) -> None:
        catalog = ResourceCatalog(self.config)
        catalog.root.mkdir(parents=True, mode=0o700)
        (catalog.root / "reservations").mkdir(mode=0o700)
        with self.assertRaises(DurableResourceError) as partial:
            catalog._ensure()
        self.assertEqual(partial.exception.code, "resource.changed")
        self.assertFalse((self.config / "resource-catalog-root-v1.json").exists())

    def test_private_mode_refusal_does_not_publish_root_manifest(self) -> None:
        catalog = ResourceCatalog(self.config)
        with patch("workbench_core.storage.registered.private_path", return_value=False):
            with self.assertRaises(DurableResourceError):
                self.resources.publish_bytes("evidence", "denied.json", b"denied\n")
        self.assertFalse((self.config / "resource-catalog-root-v1.json").exists())
        self.assertFalse(self.evidence.exists())

    def test_replaced_root_identity_is_rejected_even_with_copied_anchor(self) -> None:
        catalog = ResourceCatalog(self.config)
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        original = catalog.root
        renamed = self.config / "resources-renamed"
        original.rename(renamed)
        original.mkdir(mode=0o700)
        for name in ("reservations", "intents", "commits", "aborts", "leases", "stores"):
            (original / name).mkdir(mode=0o700)
        (original / ".resource-catalog-root-v1.json").write_bytes(
            (renamed / ".resource-catalog-root-v1.json").read_bytes()
        )
        with self.assertRaises(DurableResourceError) as changed:
            catalog.verify_root()
        self.assertEqual(changed.exception.code, "resource.changed")

    def test_child_catalogs_initialize_and_respect_parent_root_binding(self) -> None:
        temporary = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.config,
            locations={"tmp": self.home / "scratch"}, owner_id="validation",
        )
        temporary._ensure_catalog()
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertTrue((catalog.root / "temporary-leases").is_dir())
        catalog.root.rename(self.config / "resources-lost")
        with self.assertRaises(DurableResourceError):
            temporary._ensure_catalog()
        self.assertFalse(catalog.root.exists())

        from workbench_core.transport_trees import CoreTransportTrees

        transport_home = self.home / "transport-config"
        transport = CoreTransportTrees(
            workspace=self.workspace, configuration_home=transport_home,
            owner_id="public-export",
        )
        transport._ensure()
        self.assertEqual(ResourceCatalog(transport_home).verify_root(), "ready-unproven")
        self.assertTrue((transport_home / "resources-v1/transport-trees").is_dir())

    def test_inventory_refuses_unknown_and_redirected_catalog_entries(self) -> None:
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        reservations = catalog.root / "reservations"
        original = next(reservations.iterdir())
        candidates = (
            (reservations / "unrecognized.pending", lambda path: path.write_bytes(b"partial")),
            (catalog.root / "intents" / "unrecognized.pending", lambda path: path.write_bytes(b"partial")),
            (catalog.root / "commits" / ("a" * 32 + ".json"), lambda path: path.symlink_to(original)),
            (catalog.root / "aborts" / ("b" * 32 + ".json"), lambda path: os.link(original, path)),
        )
        for path, create in candidates:
            with self.subTest(path=path):
                create(path)
                with self.assertRaises(DurableResourceError) as changed:
                    catalog.inventory(workspace=self.workspace)
                self.assertEqual("resource.changed", changed.exception.code)
                path.unlink()
        self.assertEqual(1, len(catalog.inventory(workspace=self.workspace)["resources"]))

    def test_inventory_refuses_unknown_root_entries_without_claiming_lost_history(self) -> None:
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        unknown = catalog.root / "future-namespace"
        for create in (
            lambda: unknown.mkdir(),
            lambda: unknown.write_bytes(b"unrecognized\n"),
            lambda: unknown.symlink_to(catalog.root / "stores", target_is_directory=True),
        ):
            with self.subTest(create=create):
                create()
                with self.assertRaises(DurableResourceError) as changed:
                    catalog.inventory(workspace=self.workspace)
                self.assertEqual(changed.exception.code, "resource.changed")
                self.assertEqual(catalog.verify_root(), "ready-unproven")
                if unknown.is_dir() and not unknown.is_symlink():
                    unknown.rmdir()
                else:
                    unknown.unlink()

    def test_existing_registration_attempt_lease_root_remains_inventory_compatible(self) -> None:
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        original = catalog._root_manifest().read_bytes()
        lease_root = catalog.root / "registration-attempt-leases"
        lease_root.mkdir(mode=0o700)
        valid = lease_root / ("a" * 64 + ".lock")
        valid.write_bytes(b"")
        valid.chmod(0o600)
        self.assertEqual(1, len(catalog.inventory(workspace=self.workspace)["resources"]))
        self.assertEqual(original, catalog._root_manifest().read_bytes())
        unexpected = lease_root / "unexpected.lock"
        unexpected.write_bytes(b"")
        with self.assertRaises(DurableResourceError) as unknown:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual(unknown.exception.code, "resource.changed")
        unexpected.unlink()
        linked = lease_root / ("b" * 64 + ".lock")
        os.link(valid, linked)
        with self.assertRaises(DurableResourceError) as duplicate:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual(duplicate.exception.code, "resource.changed")
        linked.unlink()
        valid.unlink()
        lease_root.rmdir()
        lease_root.symlink_to(catalog.root / "leases", target_is_directory=True)
        with self.assertRaises(DurableResourceError) as redirected:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual(redirected.exception.code, "resource.changed")

    def test_resource_catalog_accepts_exact_pre_record_leases_without_adopting_history(self) -> None:
        reference = self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        original = catalog._root_manifest().read_bytes()
        leases = catalog.root / "leases"
        existing = leases / (reference.resource_id.rsplit(":", 1)[1] + ".lock")
        self.assertTrue(existing.is_file())
        for name in ("a" * 32 + ".lock", "b" * 64 + ".record-store.lock"):
            orphan = leases / name
            orphan.write_bytes(b"")
            orphan.chmod(0o600)
        foreign_workspace = self.home / "another-workspace"
        foreign_workspace.mkdir()
        self.assertEqual(
            [reference.resource_id],
            [row["resource_id"] for row in catalog.inventory(workspace=self.workspace)["resources"]],
        )
        self.assertEqual([], catalog.inventory(workspace=foreign_workspace)["resources"])
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertEqual(catalog._root_manifest().read_bytes(), original)

    def test_resource_catalog_refuses_unsafe_lease_children_across_workspaces(self) -> None:
        reference = self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        leases = catalog.root / "leases"
        source = leases / (reference.resource_id.rsplit(":", 1)[1] + ".lock")
        foreign_workspace = self.home / "another-workspace"
        foreign_workspace.mkdir()
        cases = (
            (leases / "unexpected.lock", lambda path: path.write_bytes(b"")),
            (leases / ("c" * 64 + ".lock"), lambda path: path.write_bytes(b"")),
            (leases / ("d" * 32 + ".lock"), lambda path: path.symlink_to(source)),
            (leases / ("e" * 32 + ".lock"), lambda path: os.link(source, path)),
            (leases / ("f" * 32 + ".lock"), lambda path: path.mkdir(mode=0o700)),
            (leases / ("1" * 32 + ".lock"),
             lambda path: (path.write_bytes(b""), path.chmod(0o644))),
        )
        for path, create in cases:
            with self.subTest(path=path.name):
                create(path)
                try:
                    with self.assertRaises(DurableResourceError) as changed:
                        catalog.inventory(workspace=foreign_workspace)
                    self.assertEqual(changed.exception.code, "resource.changed")
                    self.assertEqual(catalog.verify_root(), "ready-unproven")
                finally:
                    if path.is_dir() and not path.is_symlink():
                        path.rmdir()
                    else:
                        path.unlink()
        self.assertEqual(
            [reference.resource_id],
            [row["resource_id"] for row in catalog.inventory(workspace=self.workspace)["resources"]],
        )

    def test_cleanup_blocks_unregistered_workspace_items_with_unproven_history(self) -> None:
        from workbench_core.storage.manager import inventory_storage

        cache = self.workspace / ".workbench/cache"
        cache.mkdir(parents=True)
        unknown = cache / "unregistered.txt"
        unknown.write_bytes(b"unknown\n")
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            before = inventory_storage(self.workspace)
        selected = [row for row in before["items"] if Path(row["path"]) == unknown]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["deletion"]["state"], "protected")
        self.assertIn("registered-catalog-unproven", selected[0]["deletion"]["reason_codes"])
        self.resources.publish_bytes("evidence", "new.json", b"new\n")
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            after = inventory_storage(self.workspace)
        selected = [row for row in after["items"] if Path(row["path"]) == unknown]
        self.assertEqual(selected[0]["deletion"]["state"], "protected")
        self.assertIn("registered-catalog-unproven", selected[0]["deletion"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
