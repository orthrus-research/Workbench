from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_core.storage import (  # noqa: E402
    RuntimeManagerError,
    execute_cleanup,
    execute_purge_trash,
    execute_restore_trash,
    execute_runtime_create,
    execute_world_restore,
    execute_world_snapshot,
    inventory_storage,
    plan_cleanup,
    plan_purge_trash,
    plan_restore_trash,
    plan_runtime_create,
    plan_world_restore,
    plan_world_snapshot,
    resolve_inventory_item,
    validate_managed_runtime,
    validate_operation_plan,
    validate_operation_receipt,
    validate_storage_inventory,
    validate_world_snapshot,
)
from workbench_core.storage.cli import main as manager_cli_main  # noqa: E402
import workbench_core.storage.manager as runtime_manager_module  # noqa: E402


NOW = datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)


def _write(path: Path, payload: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_mod(path: Path, mod_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mcmod.info",
            json.dumps([{"modid": mod_id, "name": mod_id, "version": "1"}]),
        )


def _identity(prefix: str, value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return prefix + hashlib.sha256(canonical).hexdigest()


def _snapshot_tree(root: Path) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if path.is_symlink():
            snapshot[relative] = ("symlink", mode, os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", mode)
        else:
            snapshot[relative] = ("file", mode, path.read_bytes())
    return snapshot


def _iteration_report(
    path: Path,
    *,
    label: str,
    status: str,
    runtime: Path | None,
) -> None:
    completed = None if status == "running" else "2026-08-04T20:30:00Z"
    _write_json(
        path,
        {
            "format": "workbench-worldgen-iteration-report-v1",
            "schema_version": 1,
            "label": label,
            "profile": "fixture",
            "mode": "fast",
            "status": status,
            "started_at": "2026-08-04T20:00:00Z",
            "completed_at": completed,
            "invocation": ["--profile", "fixture", "--mode", "fast"],
            "reproduction_command": (
                "python3 tools/workbench.py worldgen dev --profile fixture "
                "--mode fast"
            ),
            "inputs": {
                "runtime_template": str(
                    path.parents[3] / "templates" / "runtime-template"
                )
            },
            "outputs": {} if runtime is None else {"runtime": str(runtime)},
            "stages": [],
            "failure": (
                None
                if status != "failed"
                else {
                    "stage": "capture",
                    "message": "fixture failure",
                    "type": "RuntimeError",
                }
            ),
        },
    )


def _profile(root: Path) -> Path:
    fixture = root / "profiles/platforms/cleanroom/candidates/test/fixture"
    plan = fixture / "plan.groovy"
    fixture.mkdir(parents=True)
    _write(plan, "mods.worldStudio.profile = 'runtime-manager-test'\n")
    path = (
        root
        / "profiles/packs/fixture/worldgen/worldgen-iteration-profile-v2.json"
    )
    _write_json(
        path,
        {
            "artifact": {
                "exclude_suffixes": ["-dev.jar", "-sources.jar"],
                "glob": ".workbench/build/libs/fixture-*.jar",
                "mod_id": "fixture_mod",
            },
            "cleanroom": "test",
            "defaults": {
                "debug_region": [0, 0, 1, 1],
                "diagnostic_sample_modulo": 1,
                "fast_region": [0, 0, 1, 1],
                "halo_chunks": 0,
                "heap": "512M",
                "seed": 1234,
            },
            "fixture": fixture.relative_to(root).as_posix(),
            "format": "workbench-worldgen-iteration-profile-v2",
            "minecraft_version": "1.12.2",
            "plan": plan.relative_to(root).as_posix(),
            "platform_profile_id": "workbench-platform:cleanroom:test",
            "profile_id": "workbench-pack:fixture:worldgen-test",
            "runtime": {
                "discovery_roots": [".workbench/templates"],
                "passthrough_integrations": [],
                "required_mod_filename_tokens": ["RequiredMod"],
                "required_mod_ids": ["requiredmod"],
                "server_jar_glob": "cleanroom-test.jar",
            },
            "schema_version": 2,
            "toolchains": {
                "minimum_cleanroom_java_major": 25,
                "proven_cleanroom_java": "25.0.4",
                "proven_gradle": "9.6.1",
            },
            "world_type": "fixture_world",
        },
    )
    return path


def _runtime_template(root: Path) -> Path:
    template = root / ".workbench/templates/runtime-template"
    _write_mod(template / "cleanroom-test.jar", "cleanroom_server")
    _write_mod(template / "mods/RequiredMod.jar", "requiredmod")
    _write(template / "config/pack.cfg", "enabled=true\n")
    _write(template / "world/level.dat", b"old template world")
    _write(template / "logs/latest.log", "old log\n")
    return template


def _json_records(root: Path, expected_format: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.json")):
        if path.stat().st_size > 4 * 1024 * 1024:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("format") == expected_format:
            records.append(value)
    return records


class RuntimeManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        from workbench_crucible_worldgen_iteration import iteration
        provider = patch("workbench_core.storage.manager.runtime_provider", return_value=iteration)
        provider.start()
        self.addCleanup(provider.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Workbench"
        self.root.mkdir()
        self.storage = self.root / ".workbench"
        self.storage.mkdir()

    def _inventory(self) -> dict[str, object]:
        report = inventory_storage(self.root, now=NOW)
        validate_storage_inventory(report)
        return report

    def _create_runtime(
        self,
        label: str,
        *,
        seed: int = 1234,
        level_name: str = "world",
    ) -> Path:
        profile_path = (
            self.root
            / "profiles/packs/fixture/worldgen/worldgen-iteration-profile-v2.json"
        )
        if not profile_path.is_file():
            _profile(self.root)
        template = self.storage / "templates/runtime-template"
        if not template.is_dir():
            _runtime_template(self.root)
        plan = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label=label,
            runtime_template=template,
            seed=seed,
            level_name=level_name,
            now=NOW,
        )
        validate_operation_plan(plan)
        self.assertEqual("ready", plan["status"])
        receipt = execute_runtime_create(self.root, plan, now=NOW)
        validate_operation_receipt(receipt)
        self.assertEqual("complete", receipt["status"])
        return self.storage / f"fixtures/managed-runtime--fixture--{label}"

    def _item_with_suffix(
        self, report: dict[str, object], suffix: str
    ) -> dict[str, object]:
        matches = [
            item
            for item in report["items"]
            if item["relative_path"] == suffix
            or item["relative_path"].endswith("/" + suffix)
        ]
        self.assertEqual(
            1,
            len(matches),
            f"expected one inventory item ending in {suffix!r}: {matches!r}",
        )
        return matches[0]

    def test_inventory_is_read_only_conservative_and_counts_hardlinks_once(self) -> None:
        template = _runtime_template(self.root)
        iteration = self.storage / "iterations/worldgen/complete"
        runtime = iteration / "runtime"
        runtime.mkdir(parents=True)
        shared = template / "libraries/shared.bin"
        shared_payload = b"x" * (1024 * 1024)
        _write(shared, shared_payload)
        (runtime / "libraries").mkdir()
        os.link(shared, runtime / "libraries/shared.bin")
        _write(runtime / "world/level.dat", b"generated world")
        _iteration_report(
            iteration / "iteration-report-v1.json",
            label="complete",
            status="complete",
            runtime=runtime,
        )
        _write(self.storage / "evidence/protected/capture.bin", b"evidence")
        _write(self.storage / "mystery/unowned/data.bin", b"unknown")
        outside = self.root / "personal-world"
        _write(outside / "level.dat", b"personal")
        link = self.storage / "mystery/unowned/personal-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        before_storage = _snapshot_tree(self.storage)
        before_outside = _snapshot_tree(outside)

        first = self._inventory()
        second = self._inventory()

        self.assertEqual(first, second)
        self.assertTrue(first["read_only"])
        self.assertEqual(str(self.root.resolve()), first["workspace_root"])
        self.assertEqual(str(self.storage.resolve()), first["storage_root"])
        self.assertEqual(before_storage, _snapshot_tree(self.storage))
        self.assertEqual(before_outside, _snapshot_tree(outside))
        self.assertGreater(
            first["totals"]["logical_bytes"],
            first["totals"]["unique_allocated_bytes"],
        )
        summed_item_allocation = sum(
            item["size"]["unique_allocated_bytes"] for item in first["items"]
        )
        shared_allocated = shared.stat().st_blocks * 512
        self.assertGreaterEqual(
            summed_item_allocation - first["totals"]["unique_allocated_bytes"],
            shared_allocated,
        )

        unknown = self._item_with_suffix(first, "mystery/unowned")
        by_id = resolve_inventory_item(first, unknown["item_id"])
        self.assertEqual(unknown, by_id)
        self.assertEqual("unknown", unknown["custody"]["state"])
        self.assertEqual("protected", unknown["deletion"]["state"])
        self.assertGreaterEqual(unknown["size"]["symlink_count"], 1)
        blocked = plan_cleanup(self.root, selector=unknown["item_id"], now=NOW)
        validate_operation_plan(blocked)
        self.assertEqual("blocked", blocked["status"])
        self.assertTrue(blocked["blockers"])
        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, blocked, now=NOW)
        self.assertEqual(before_storage, _snapshot_tree(self.storage))
        self.assertEqual(before_outside, _snapshot_tree(outside))

    def test_inventory_keeps_evidence_and_running_iterations_protected(self) -> None:
        evidence = self.storage / "evidence/worldgen/proof"
        _write(evidence / "capture.bin", b"proof")
        iteration = self.storage / "iterations/worldgen/active"
        runtime = iteration / "runtime"
        _write(runtime / "world/level.dat", b"running")
        _iteration_report(
            iteration / "iteration-report-v1.json",
            label="active",
            status="running",
            runtime=runtime,
        )
        report = self._inventory()

        for suffix in ("evidence/worldgen/proof", "iterations/worldgen/active"):
            item = self._item_with_suffix(report, suffix)
            plan = plan_cleanup(
                self.root, selector=item["item_id"], now=NOW
            )
            validate_operation_plan(plan)
            self.assertEqual("blocked", plan["status"])
            self.assertTrue(plan["blockers"])
            with self.assertRaises(RuntimeManagerError):
                execute_cleanup(self.root, plan, now=NOW)

    def test_invalid_self_declared_runtime_never_gains_managed_custody(self) -> None:
        lookalike = self.storage / "fixtures/personal-lookalike"
        _write(lookalike / "world/level.dat", b"personal world")
        _write_json(
            lookalike / ".workbench-managed-runtime-v1.json",
            {
                "format": "workbench-managed-runtime-v1",
                "state": "ready",
            },
        )

        inventory = self._inventory()
        item = self._item_with_suffix(inventory, "fixtures/personal-lookalike")
        self.assertNotEqual("managed", item["custody"]["state"])
        self.assertEqual("protected", item["deletion"]["state"])
        self.assertTrue(item["problems"])
        plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        self.assertEqual("blocked", plan["status"])
        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, plan, now=NOW)
        self.assertEqual(b"personal world", (lookalike / "world/level.dat").read_bytes())

    def test_world_shaped_cache_is_never_treated_as_regenerable(self) -> None:
        personal = self.storage / "cache/personal-save"
        _write(personal / "level.dat", b"personal world")

        inventory = self._inventory()
        item = self._item_with_suffix(inventory, "cache/personal-save")
        self.assertEqual("protected", item["deletion"]["state"])
        self.assertIn("unmanaged-world", item["deletion"]["reason_codes"])
        self.assertNotEqual("regenerable", item["reproducibility"]["state"])

        plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        self.assertEqual("blocked", plan["status"])
        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, plan, now=NOW)
        self.assertTrue((personal / "level.dat").is_file())

    def test_cleanup_category_root_that_is_a_file_stays_protected(self) -> None:
        category_root = self.storage / "cache"
        _write(category_root, b"not one addressable cache resource")

        inventory = self._inventory()
        item = self._item_with_suffix(inventory, "cache")
        self.assertEqual("protected", item["deletion"]["state"])
        self.assertIn("broad-category-root", item["deletion"]["reason_codes"])

        plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        validate_operation_plan(plan)
        self.assertEqual("blocked", plan["status"])
        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, plan, now=NOW)
        self.assertEqual(
            b"not one addressable cache resource", category_root.read_bytes()
        )

    def test_nested_regular_file_mount_boundary_blocks_cleanup(self) -> None:
        source = self.storage / "cache/foreign-file-mount"
        mounted_file = source / "mounted.bin"
        _write(mounted_file, b"foreign device fixture")
        real_lstat = Path.lstat

        def lstat_with_foreign_device(path: Path) -> object:
            observed = real_lstat(path)
            if path != mounted_file:
                return observed
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_size=observed.st_size,
                st_blocks=observed.st_blocks,
                st_dev=observed.st_dev + 1,
                st_ino=observed.st_ino,
                st_nlink=observed.st_nlink,
                st_mtime_ns=observed.st_mtime_ns,
                st_uid=observed.st_uid,
                st_gid=observed.st_gid,
            )

        with patch.object(Path, "lstat", new=lstat_with_foreign_device):
            item = self._item_with_suffix(
                self._inventory(), "cache/foreign-file-mount"
            )
            self.assertEqual("protected", item["deletion"]["state"])
            self.assertIn(
                "filesystem-boundary",
                {problem["code"] for problem in item["problems"]},
            )
            plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
            self.assertEqual("blocked", plan["status"])
        self.assertEqual(b"foreign device fixture", mounted_file.read_bytes())

    def test_item_root_mount_boundary_blocks_cleanup_preview(self) -> None:
        mounted_root = self.storage / "cache/foreign-root-mount"
        _write(mounted_root / "payload.bin", b"mounted root fixture")
        real_lstat = Path.lstat

        def lstat_with_foreign_root(path: Path) -> object:
            observed = real_lstat(path)
            if path != mounted_root:
                return observed
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_size=observed.st_size,
                st_blocks=observed.st_blocks,
                st_dev=observed.st_dev + 1,
                st_ino=observed.st_ino,
                st_nlink=observed.st_nlink,
                st_mtime_ns=observed.st_mtime_ns,
                st_uid=observed.st_uid,
                st_gid=observed.st_gid,
            )

        with patch.object(Path, "lstat", new=lstat_with_foreign_root):
            item = self._item_with_suffix(
                self._inventory(), "cache/foreign-root-mount"
            )
            self.assertEqual("protected", item["deletion"]["state"])
            self.assertIn(
                "filesystem-boundary",
                {problem["code"] for problem in item["problems"]},
            )
            plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
            self.assertEqual("blocked", plan["status"])
        self.assertEqual(b"mounted root fixture", (mounted_root / "payload.bin").read_bytes())

    def test_iteration_report_outside_iterations_cannot_downgrade_a_world(self) -> None:
        personal = self.storage / "cache/self-declared-iteration"
        _write(personal / "level.dat", b"personal world")
        _iteration_report(
            personal / "iteration-report-v1.json",
            label="self-declared-iteration",
            status="complete",
            runtime=personal,
        )

        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory, "cache/self-declared-iteration"
        )
        self.assertEqual("protected", item["deletion"]["state"])
        self.assertIn("unmanaged-world", item["deletion"]["reason_codes"])

        plan = plan_cleanup(
            self.root,
            selector=item["item_id"],
            allow_review=True,
            now=NOW,
        )
        self.assertEqual("blocked", plan["status"])
        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, plan, now=NOW)
        self.assertEqual(b"personal world", (personal / "level.dat").read_bytes())

    def test_process_cwd_inside_item_marks_it_active_without_absolute_argv(self) -> None:
        if not Path("/proc").is_dir():
            self.skipTest("live process path observation requires /proc")
        runtime = self.storage / "cache/live-relative-runtime"
        _write(runtime / "payload.bin", b"active")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=runtime,
        )
        try:
            inventory = self._inventory()
            item = self._item_with_suffix(inventory, "cache/live-relative-runtime")
            self.assertEqual("active", item["deletion"]["state"])
            self.assertIn("observed-active-use", item["deletion"]["reason_codes"])
            plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
            self.assertEqual("blocked", plan["status"])
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_resolver_rejects_storage_roots_categories_and_outside_paths(self) -> None:
        _write(self.storage / "iterations/worldgen/one/marker", b"owned")
        report = self._inventory()
        outside = self.root / "personal-world"
        _write(outside / "level.dat", b"personal")

        for selector in (
            str(self.root),
            str(self.storage),
            str(self.storage / "iterations"),
            str(outside),
            "../personal-world",
            "*",
            "iterations",
        ):
            with self.subTest(selector=selector):
                with self.assertRaises(RuntimeManagerError):
                    resolve_inventory_item(report, selector)

    def test_runtime_create_preview_then_clones_independently_with_absent_world(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)
        template_before = _snapshot_tree(template)
        destination = self.storage / "fixtures/managed-runtime--fixture--fresh"

        plan = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label="fresh",
            runtime_template=template,
            seed=987654321,
            level_name="world",
            server_port=25591,
            now=NOW,
        )

        validate_operation_plan(plan)
        self.assertEqual("runtime-create", plan["operation"])
        self.assertEqual("ready", plan["status"])
        self.assertTrue(plan["read_only_preview"])
        self.assertFalse(destination.exists())
        self.assertEqual(template_before, _snapshot_tree(template))

        receipt = execute_runtime_create(self.root, plan, now=NOW)

        validate_operation_receipt(receipt)
        self.assertEqual("runtime-create", receipt["operation"])
        self.assertEqual("complete", receipt["status"])
        self.assertTrue(destination.is_dir())
        self.assertFalse((destination / "world").exists())
        self.assertFalse((destination / "logs").exists())
        self.assertEqual(
            "enabled=true\n",
            (destination / "config/pack.cfg").read_text(encoding="utf-8"),
        )
        self.assertNotEqual(
            (template / "config/pack.cfg").stat().st_ino,
            (destination / "config/pack.cfg").stat().st_ino,
        )
        (destination / "config/pack.cfg").write_text(
            "enabled=false\n", encoding="utf-8"
        )
        self.assertEqual(template_before, _snapshot_tree(template))
        properties = (destination / "server.properties").read_text(encoding="utf-8")
        self.assertIn("level-name=world\n", properties)
        self.assertIn("level-seed=987654321\n", properties)
        self.assertIn("level-type=fixture_world\n", properties)
        self.assertIn("server-port=25591\n", properties)

        manifests = _json_records(
            self.storage, "workbench-managed-runtime-v1"
        )
        self.assertEqual(1, len(manifests))
        validate_managed_runtime(manifests[0])
        self.assertEqual("reserved-absent", manifests[0]["world"]["state"])
        self.assertEqual(
            ".workbench/fixtures/managed-runtime--fixture--fresh",
            manifests[0]["relative_path"],
        )

    def test_runtime_create_rejects_dot_level_names(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)

        for level_name in (".", ".."):
            with self.subTest(level_name=level_name):
                with self.assertRaises(RuntimeManagerError):
                    plan_runtime_create(
                        self.root,
                        profile_name="fixture",
                        label=f"invalid-level-{len(level_name)}",
                        runtime_template=template,
                        level_name=level_name,
                        now=NOW,
                    )

        fixtures = self.storage / "fixtures"
        self.assertFalse(fixtures.exists())

    def test_runtime_create_rejects_tampered_provisioning_staging(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)
        plan = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label="tampered-staging",
            runtime_template=template,
            now=NOW,
        )
        destination = (
            self.storage / "fixtures/managed-runtime--fixture--tampered-staging"
        )
        original_provision = runtime_manager_module.provision_runtime

        def provision_then_tamper(
            source: Path, staging: Path, profile: dict[str, object]
        ) -> object:
            result = original_provision(source, staging, profile)
            _write(staging / "config/pack.cfg", "tampered-after-copy=true\n")
            return result

        with patch.object(
            runtime_manager_module,
            "provision_runtime",
            side_effect=provision_then_tamper,
        ):
            with self.assertRaises(RuntimeManagerError):
                execute_runtime_create(self.root, plan, now=NOW)

        self.assertFalse(destination.exists())
        fixtures = destination.parent
        if fixtures.exists():
            self.assertFalse(
                any(path.name.endswith(".partial") for path in fixtures.iterdir())
            )
        receipts = _json_records(
            self.storage, "workbench-storage-operation-receipt-v1"
        )
        receipt = next(row for row in receipts if row["plan_id"] == plan["plan_id"])
        validate_operation_receipt(receipt, plan=plan)
        self.assertEqual("failed", receipt["status"])

    def test_runtime_create_rejects_stale_tampered_and_colliding_plans(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)
        outside = self.root / "personal-world"
        _write(outside / "level.dat", b"personal")

        stale = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label="stale",
            runtime_template=template,
            now=NOW,
        )
        _write(template / "config/pack.cfg", "changed-after-preview=true\n")
        with self.assertRaises(RuntimeManagerError):
            execute_runtime_create(self.root, stale, now=NOW)
        self.assertFalse(
            (self.storage / "fixtures/managed-runtime--fixture--stale").exists()
        )
        self.assertEqual(b"personal", (outside / "level.dat").read_bytes())

        fresh = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label="tampered",
            runtime_template=template,
            now=NOW,
        )
        tampered = deepcopy(fresh)
        tampered["operation"] = "purge-trash"
        with self.assertRaises(RuntimeManagerError):
            validate_operation_plan(tampered)
        with self.assertRaises(RuntimeManagerError):
            execute_runtime_create(self.root, tampered, now=NOW)
        self.assertFalse(
            (self.storage / "fixtures/managed-runtime--fixture--tampered").exists()
        )

        stale_nested_identity = deepcopy(fresh)
        stale_nested_identity["inputs"][0]["binding_sha256"] = "0" * 64
        plan_material = dict(stale_nested_identity)
        plan_material.pop("plan_id")
        stale_nested_identity["plan_id"] = _identity(
            "workbench-storage-operation-plan:sha256:", plan_material
        )
        with self.assertRaises(RuntimeManagerError):
            validate_operation_plan(stale_nested_identity)

        incomplete_action_sequence = deepcopy(fresh)
        incomplete_action_sequence["actions"] = incomplete_action_sequence[
            "actions"
        ][:1]
        plan_material = dict(incomplete_action_sequence)
        plan_material.pop("plan_id")
        incomplete_action_sequence["plan_id"] = _identity(
            "workbench-storage-operation-plan:sha256:", plan_material
        )
        with self.assertRaises(RuntimeManagerError):
            validate_operation_plan(incomplete_action_sequence)

        collision = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label="collision",
            runtime_template=template,
            now=NOW,
        )
        destination = self.storage / "fixtures/managed-runtime--fixture--collision"
        _write(destination / "sentinel", b"do not overwrite")
        with self.assertRaises(RuntimeManagerError):
            execute_runtime_create(self.root, collision, now=NOW)
        self.assertEqual(b"do not overwrite", (destination / "sentinel").read_bytes())

    def test_runtime_create_refuses_an_outside_receipt_ledger_symlink(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)
        outside = self.root / "outside-ledger"
        outside.mkdir()
        ledger_parent = self.storage / "runtime-manager"
        ledger_parent.mkdir()
        try:
            (ledger_parent / "operations").symlink_to(
                outside, target_is_directory=True
            )
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        plan = plan_runtime_create(
            self.root,
            profile_name="fixture",
            label="unsafe-ledger",
            runtime_template=template,
            now=NOW,
        )

        with self.assertRaises(RuntimeManagerError):
            execute_runtime_create(self.root, plan, now=NOW)

        self.assertFalse(
            (
                self.storage
                / "fixtures/managed-runtime--fixture--unsafe-ledger"
            ).exists()
        )
        self.assertEqual({}, _snapshot_tree(outside))

    def test_world_snapshot_and_restore_make_two_independent_copies(self) -> None:
        source_runtime = self._create_runtime("source", seed=424242)
        source_world = source_runtime / "world"
        _write(source_world / "level.dat", b"fixture level")
        _write(source_world / "region/r.0.0.mca", b"fixture chunks")
        source_before = _snapshot_tree(source_world)
        report = self._inventory()
        source_item = self._item_with_suffix(
            report, "fixtures/managed-runtime--fixture--source"
        )

        snapshot_plan = plan_world_snapshot(
            self.root,
            selector=source_item["item_id"],
            label="before-risk",
            world="world",
            now=NOW,
        )

        validate_operation_plan(snapshot_plan)
        self.assertEqual("world-snapshot", snapshot_plan["operation"])
        self.assertEqual("ready", snapshot_plan["status"])
        snapshot_root = self.storage / "fixtures/managed-snapshot--before-risk"
        self.assertFalse(snapshot_root.exists())
        snapshot_receipt = execute_world_snapshot(
            self.root, snapshot_plan, now=NOW
        )
        validate_operation_receipt(snapshot_receipt)
        self.assertEqual("complete", snapshot_receipt["status"])
        self.assertEqual(source_before, _snapshot_tree(source_world))

        snapshots = _json_records(self.storage, "workbench-world-snapshot-v1")
        self.assertEqual(1, len(snapshots))
        snapshot = snapshots[0]
        validate_world_snapshot(snapshot)
        self.assertEqual("complete", snapshot["state"])
        self.assertEqual("manager-quiesced", snapshot["consistency"]["state"])
        self.assertTrue(snapshot["consistency"]["source_stable"])
        self.assertTrue(snapshot["content"]["independent_copy"])
        inconsistent_snapshot = deepcopy(snapshot)
        inconsistent_snapshot["content"]["file_count"] += 1
        snapshot_material = dict(inconsistent_snapshot)
        snapshot_material.pop("snapshot_id")
        inconsistent_snapshot["snapshot_id"] = _identity(
            "workbench-world-snapshot:sha256:", snapshot_material
        )
        with self.assertRaises(RuntimeManagerError):
            validate_world_snapshot(inconsistent_snapshot)
        snapshot_payload = self.root / snapshot["content"]["payload_relative_path"]
        self.assertEqual(source_before, _snapshot_tree(snapshot_payload))
        self.assertNotEqual(
            (source_world / "level.dat").stat().st_ino,
            (snapshot_payload / "level.dat").stat().st_ino,
        )

        snapshot_inventory = self._inventory()
        snapshot_item = next(
            item
            for item in snapshot_inventory["items"]
            if item["resource_id"] == snapshot["snapshot_id"]
        )
        restore_plan = plan_world_restore(
            self.root,
            snapshot_selector=snapshot_item["item_id"],
            profile_name="fixture",
            label="restored",
            runtime_template=self.storage / "templates/runtime-template",
            level_name="world",
            server_port=25592,
            now=NOW,
        )

        validate_operation_plan(restore_plan)
        self.assertEqual("world-restore", restore_plan["operation"])
        self.assertEqual("ready", restore_plan["status"])
        restored_runtime = (
            self.storage / "fixtures/managed-runtime--fixture--restored"
        )
        self.assertFalse(restored_runtime.exists())
        restore_receipt = execute_world_restore(self.root, restore_plan, now=NOW)
        validate_operation_receipt(restore_receipt)
        self.assertEqual("complete", restore_receipt["status"])
        restored_world = restored_runtime / "world"
        self.assertEqual(source_before, _snapshot_tree(restored_world))
        self.assertNotEqual(
            (snapshot_payload / "level.dat").stat().st_ino,
            (restored_world / "level.dat").stat().st_ino,
        )
        _write(restored_world / "level.dat", b"changed restored world")
        self.assertEqual(b"fixture level", (source_world / "level.dat").read_bytes())
        self.assertEqual(
            b"fixture level", (snapshot_payload / "level.dat").read_bytes()
        )

    def test_snapshot_does_not_pin_disposable_source_runtime(self) -> None:
        source_runtime = self._create_runtime("snapshot-then-reclaim", seed=9090)
        _write(source_runtime / "world/level.dat", b"independent saved world")
        _write(source_runtime / "world/region/r.0.0.mca", b"independent chunks")
        source_item = self._item_with_suffix(
            self._inventory(),
            "fixtures/managed-runtime--fixture--snapshot-then-reclaim",
        )
        snapshot_plan = plan_world_snapshot(
            self.root,
            selector=source_item["item_id"],
            label="reclaim-source",
            world="world",
            now=NOW,
        )
        execute_world_snapshot(self.root, snapshot_plan, now=NOW)

        after_snapshot = self._inventory()
        current_source = self._item_with_suffix(
            after_snapshot,
            "fixtures/managed-runtime--fixture--snapshot-then-reclaim",
        )
        self.assertEqual("eligible", current_source["deletion"]["state"])
        provenance = [
            reference
            for item in after_snapshot["items"]
            if item["kind"] == "snapshot"
            for reference in item["references"]
            if reference["target_item_id"] == current_source["item_id"]
        ]
        self.assertEqual(1, len(provenance))
        self.assertEqual("snapshot-of", provenance[0]["relation"])
        self.assertFalse(provenance[0]["required"])

        cleanup = plan_cleanup(
            self.root, selector=current_source["item_id"], now=NOW
        )
        self.assertEqual("ready", cleanup["status"])
        execute_cleanup(self.root, cleanup, now=NOW)
        self.assertFalse(source_runtime.exists())

        snapshot_item = self._item_with_suffix(
            self._inventory(), "fixtures/managed-snapshot--reclaim-source"
        )
        restore = plan_world_restore(
            self.root,
            snapshot_selector=snapshot_item["item_id"],
            profile_name="fixture",
            label="restored-after-reclaim",
            runtime_template=self.storage / "templates/runtime-template",
            now=NOW,
        )
        self.assertEqual("ready", restore["status"])
        execute_world_restore(self.root, restore, now=NOW)
        restored_world = (
            self.storage
            / "fixtures/managed-runtime--fixture--restored-after-reclaim/world"
        )
        self.assertEqual(
            b"independent saved world", (restored_world / "level.dat").read_bytes()
        )
        self.assertEqual(
            b"independent chunks",
            (restored_world / "region/r.0.0.mca").read_bytes(),
        )

    def test_snapshot_payload_mutation_blocks_restore_during_planning(self) -> None:
        runtime = self._create_runtime("snapshot-mutation")
        _write(runtime / "world/level.dat", b"original level")
        runtime_item = self._item_with_suffix(
            self._inventory(),
            "fixtures/managed-runtime--fixture--snapshot-mutation",
        )
        snapshot_plan = plan_world_snapshot(
            self.root,
            selector=runtime_item["item_id"],
            label="mutated-payload",
            world="world",
            now=NOW,
        )
        execute_world_snapshot(self.root, snapshot_plan, now=NOW)
        snapshot = _json_records(
            self.storage, "workbench-world-snapshot-v1"
        )[0]
        payload = self.root / snapshot["content"]["payload_relative_path"]
        _write(payload / "level.dat", b"changed after snapshot publication")
        snapshot_item = next(
            item
            for item in self._inventory()["items"]
            if item["resource_id"] == snapshot["snapshot_id"]
        )

        restore = plan_world_restore(
            self.root,
            snapshot_selector=snapshot_item["item_id"],
            profile_name="fixture",
            label="must-not-restore",
            runtime_template=self.storage / "templates/runtime-template",
            now=NOW,
        )

        validate_operation_plan(restore)
        self.assertEqual("blocked", restore["status"])
        self.assertIn(
            "snapshot-payload-changed",
            {blocker["code"] for blocker in restore["blockers"]},
        )
        with self.assertRaises(RuntimeManagerError):
            execute_world_restore(self.root, restore, now=NOW)
        self.assertFalse(
            (
                self.storage
                / "fixtures/managed-runtime--fixture--must-not-restore"
            ).exists()
        )

    def test_snapshot_refuses_symlinks_and_restore_refuses_collisions(self) -> None:
        runtime = self._create_runtime("unsafe-source")
        world = runtime / "world"
        _write(world / "level.dat", b"level")
        outside = self.root / "personal-world"
        _write(outside / "secret.dat", b"personal")
        try:
            (world / "external").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        report = self._inventory()
        runtime_item = self._item_with_suffix(
            report, "fixtures/managed-runtime--fixture--unsafe-source"
        )

        plan = plan_world_snapshot(
            self.root,
            selector=runtime_item["item_id"],
            label="unsafe",
            world="world",
            now=NOW,
        )

        validate_operation_plan(plan)
        self.assertEqual("blocked", plan["status"])
        with self.assertRaises(RuntimeManagerError):
            execute_world_snapshot(self.root, plan, now=NOW)
        self.assertFalse(
            (self.storage / "fixtures/managed-snapshot--unsafe").exists()
        )
        self.assertEqual(b"personal", (outside / "secret.dat").read_bytes())

        (world / "external").unlink()
        clean_inventory = self._inventory()
        runtime_item = self._item_with_suffix(
            clean_inventory,
            "fixtures/managed-runtime--fixture--unsafe-source",
        )
        safe_plan = plan_world_snapshot(
            self.root,
            selector=runtime_item["item_id"],
            label="collision-source",
            world="world",
            now=NOW,
        )
        execute_world_snapshot(self.root, safe_plan, now=NOW)
        snapshot = _json_records(self.storage, "workbench-world-snapshot-v1")[0]
        snapshot_inventory = self._inventory()
        snapshot_item = next(
            item
            for item in snapshot_inventory["items"]
            if item["resource_id"] == snapshot["snapshot_id"]
        )
        restore_plan = plan_world_restore(
            self.root,
            snapshot_selector=snapshot_item["item_id"],
            profile_name="fixture",
            label="restore-collision",
            runtime_template=self.storage / "templates/runtime-template",
            now=NOW,
        )
        destination = (
            self.storage
            / "fixtures/managed-runtime--fixture--restore-collision"
        )
        _write(destination / "sentinel", b"do not overwrite")
        with self.assertRaises(RuntimeManagerError):
            execute_world_restore(self.root, restore_plan, now=NOW)
        self.assertEqual(b"do not overwrite", (destination / "sentinel").read_bytes())

    def test_regular_file_can_be_cleaned_up_and_purged_exactly(self) -> None:
        source = self.storage / "cache/single-cache.bin"
        _write(source, b"one regular cache file")
        item = self._item_with_suffix(
            self._inventory(), "cache/single-cache.bin"
        )
        self.assertEqual("eligible", item["deletion"]["state"])

        cleanup = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        execute_cleanup(self.root, cleanup, now=NOW)
        self.assertFalse(source.exists())
        trash = next(
            row
            for row in self._inventory()["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path") == ".workbench/cache/single-cache.bin"
                for reference in row["references"]
            )
        )
        trash_path = Path(trash["path"])
        self.assertTrue(trash_path.is_file())
        self.assertEqual(b"one regular cache file", trash_path.read_bytes())

        purge = plan_purge_trash(
            self.root,
            selector=trash["item_id"],
            confirmation=trash["resource_id"],
            now=NOW,
        )
        self.assertEqual("ready", purge["status"])
        receipt = execute_purge_trash(self.root, purge, now=NOW)
        validate_operation_receipt(receipt, plan=purge)
        self.assertEqual("complete", receipt["status"])
        self.assertFalse(trash_path.exists())

    def test_resigned_purge_with_forged_output_is_rejected_before_deletion(self) -> None:
        source = self.storage / "cache/forged-purge-output"
        _write(source / "payload.bin", b"do not delete from a contradictory plan")
        item = self._item_with_suffix(
            self._inventory(), "cache/forged-purge-output"
        )
        execute_cleanup(
            self.root,
            plan_cleanup(self.root, selector=item["item_id"], now=NOW),
            now=NOW,
        )
        trash = next(
            row
            for row in self._inventory()["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path")
                == ".workbench/cache/forged-purge-output"
                for reference in row["references"]
            )
        )
        trash_path = Path(trash["path"])
        purge = plan_purge_trash(
            self.root,
            selector=trash["item_id"],
            confirmation=trash["resource_id"],
            now=NOW,
        )
        forged = deepcopy(purge)
        forged["effects"]["creates"] = [".workbench/cache/never-created"]
        plan_material = dict(forged)
        plan_material.pop("plan_id")
        forged["plan_id"] = _identity(
            "workbench-storage-operation-plan:sha256:", plan_material
        )

        with self.assertRaises(RuntimeManagerError):
            validate_operation_plan(forged)
        with self.assertRaises(RuntimeManagerError):
            execute_purge_trash(self.root, forged, now=NOW)
        self.assertTrue(trash_path.is_dir())
        self.assertEqual(
            b"do not delete from a contradictory plan",
            (trash_path / "payload.bin").read_bytes(),
        )

    def test_resigned_cleanup_plan_with_tampered_endpoint_is_rejected(self) -> None:
        source = self.storage / "cache/exact-source"
        _write(source / "payload.bin", b"stay put")
        item = self._item_with_suffix(self._inventory(), "cache/exact-source")
        plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        tampered = deepcopy(plan)
        outside = self.root / "outside-cleanup-target"
        tampered["actions"][0]["destination"]["path"] = str(outside)
        action_material = dict(tampered["actions"][0])
        action_material.pop("action_id")
        tampered["actions"][0]["action_id"] = _identity(
            "workbench-storage-action:sha256:", action_material
        )
        plan_material = dict(tampered)
        plan_material.pop("plan_id")
        tampered["plan_id"] = _identity(
            "workbench-storage-operation-plan:sha256:", plan_material
        )

        with self.assertRaises(RuntimeManagerError):
            validate_operation_plan(tampered)
        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, tampered, now=NOW)
        self.assertEqual(b"stay put", (source / "payload.bin").read_bytes())
        self.assertFalse(outside.exists())

    def test_interrupted_cleanup_is_restorable_but_cannot_be_purged(self) -> None:
        source = self.storage / "cache/interrupted-cleanup"
        _write(source / "payload.bin", b"recover me")
        original = _snapshot_tree(source)
        item = self._item_with_suffix(
            self._inventory(), "cache/interrupted-cleanup"
        )
        cleanup = plan_cleanup(self.root, selector=item["item_id"], now=NOW)

        class SimulatedInterruption(BaseException):
            pass

        with patch.object(
            runtime_manager_module._OperationJournal,
            "finish",
            side_effect=SimulatedInterruption("process stopped after atomic move"),
        ):
            with self.assertRaises(SimulatedInterruption):
                execute_cleanup(self.root, cleanup, now=NOW)

        self.assertFalse(source.exists())
        trash = next(
            row
            for row in self._inventory()["items"]
            if row["kind"] == "trash"
            and "interrupted-cleanup" in row["deletion"]["reason_codes"]
        )
        purge = plan_purge_trash(
            self.root,
            selector=trash["item_id"],
            confirmation=trash["resource_id"],
            now=NOW,
        )
        self.assertEqual("blocked", purge["status"])
        self.assertIn(
            "cleanup-interrupted",
            {blocker["code"] for blocker in purge["blockers"]},
        )
        with self.assertRaises(RuntimeManagerError):
            execute_purge_trash(self.root, purge, now=NOW)
        self.assertTrue(Path(trash["path"]).exists())

        restore = plan_restore_trash(
            self.root, selector=trash["item_id"], now=NOW
        )
        self.assertEqual("ready", restore["status"])
        receipt = execute_restore_trash(self.root, restore, now=NOW)
        validate_operation_receipt(receipt, plan=restore)
        self.assertEqual("complete", receipt["status"])
        self.assertEqual(original, _snapshot_tree(source))

    def test_live_process_inside_trash_blocks_restore_and_purge(self) -> None:
        if not Path("/proc").is_dir():
            self.skipTest("live process path observation requires /proc")
        source = self.storage / "cache/live-trash"
        _write(source / "payload.bin", b"in active use")
        item = self._item_with_suffix(self._inventory(), "cache/live-trash")
        cleanup = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        execute_cleanup(self.root, cleanup, now=NOW)
        trash = next(
            row
            for row in self._inventory()["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path") == ".workbench/cache/live-trash"
                for reference in row["references"]
            )
        )
        trash_path = Path(trash["path"])
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=trash_path,
        )
        try:
            active_trash = self._item_with_suffix(
                self._inventory(), trash_path.name
            )
            self.assertEqual("active", active_trash["deletion"]["state"])
            self.assertIn(
                "observed-active-use", active_trash["deletion"]["reason_codes"]
            )
            restore = plan_restore_trash(
                self.root, selector=active_trash["item_id"], now=NOW
            )
            purge = plan_purge_trash(
                self.root,
                selector=active_trash["item_id"],
                confirmation=active_trash["resource_id"],
                now=NOW,
            )
            for plan in (restore, purge):
                self.assertEqual("blocked", plan["status"])
            with self.assertRaises(RuntimeManagerError):
                execute_restore_trash(self.root, restore, now=NOW)
            with self.assertRaises(RuntimeManagerError):
                execute_purge_trash(self.root, purge, now=NOW)
            self.assertTrue(trash_path.is_dir())
            self.assertFalse(source.exists())
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_cleanup_rolls_back_when_terminal_receipt_completion_fails(self) -> None:
        source = self.storage / "cache/receipt-failure"
        _write(source / "payload.bin", b"must roll back")
        original = _snapshot_tree(source)
        item = self._item_with_suffix(self._inventory(), "cache/receipt-failure")
        cleanup = plan_cleanup(self.root, selector=item["item_id"], now=NOW)

        with patch.object(
            runtime_manager_module._OperationJournal,
            "complete",
            side_effect=RuntimeManagerError("simulated terminal receipt failure"),
        ):
            with self.assertRaises(RuntimeManagerError):
                execute_cleanup(self.root, cleanup, now=NOW)

        self.assertEqual(original, _snapshot_tree(source))
        transaction_records = list(
            (self.storage / "runtime-manager/trash").glob("*.json")
        )
        self.assertEqual([], transaction_records)
        inventory = self._inventory()
        restored = self._item_with_suffix(inventory, "cache/receipt-failure")
        self.assertEqual("eligible", restored["deletion"]["state"])
        self.assertFalse(any(row["kind"] == "trash" for row in inventory["items"]))

        receipts = [
            row
            for row in _json_records(
                self.storage, "workbench-storage-operation-receipt-v1"
            )
            if row["plan_id"] == cleanup["plan_id"]
        ]
        self.assertEqual(1, len(receipts))
        validate_operation_receipt(receipts[0], plan=cleanup)
        self.assertEqual("failed", receipts[0]["status"])

    def test_cleanup_is_previewed_then_trash_can_be_restored_and_purged(self) -> None:
        runtime = self._create_runtime("cleanup")
        _write(runtime / "world/level.dat", b"cleanup world")
        original = _snapshot_tree(runtime)
        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory, "fixtures/managed-runtime--fixture--cleanup"
        )
        self.assertEqual("eligible", item["deletion"]["state"])

        cleanup = plan_cleanup(
            self.root, selector=item["item_id"], now=NOW
        )

        validate_operation_plan(cleanup)
        self.assertEqual("cleanup", cleanup["operation"])
        self.assertEqual("ready", cleanup["status"])
        self.assertTrue(cleanup["read_only_preview"])
        self.assertEqual(original, _snapshot_tree(runtime))
        cleanup_receipt = execute_cleanup(self.root, cleanup, now=NOW)
        validate_operation_receipt(cleanup_receipt)
        self.assertEqual("complete", cleanup_receipt["status"])
        self.assertFalse(runtime.exists())

        trashed_inventory = self._inventory()
        trash_items = [
            row
            for row in trashed_inventory["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path")
                == ".workbench/fixtures/managed-runtime--fixture--cleanup"
                for reference in row["references"]
            )
        ]
        self.assertEqual(1, len(trash_items))
        trash = trash_items[0]
        trash_path = Path(trash["path"])
        self.assertTrue(trash_path.exists())

        restore = plan_restore_trash(
            self.root, selector=trash["item_id"], now=NOW
        )
        validate_operation_plan(restore)
        self.assertEqual("restore-trash", restore["operation"])
        self.assertEqual("ready", restore["status"])
        restore_receipt = execute_restore_trash(self.root, restore, now=NOW)
        validate_operation_receipt(restore_receipt)
        self.assertEqual("complete", restore_receipt["status"])
        self.assertEqual(original, _snapshot_tree(runtime))
        self.assertFalse(trash_path.exists())

        restored_inventory = self._inventory()
        restored_item = self._item_with_suffix(
            restored_inventory, "fixtures/managed-runtime--fixture--cleanup"
        )
        second_cleanup = plan_cleanup(
            self.root, selector=restored_item["item_id"], now=NOW
        )
        execute_cleanup(self.root, second_cleanup, now=NOW)
        second_trash_inventory = self._inventory()
        second_trash = next(
            row
            for row in second_trash_inventory["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path")
                == ".workbench/fixtures/managed-runtime--fixture--cleanup"
                for reference in row["references"]
            )
        )

        wrong_confirmation = plan_purge_trash(
            self.root,
            selector=second_trash["item_id"],
            confirmation="wrong-trash-id",
            now=NOW,
        )
        validate_operation_plan(wrong_confirmation)
        self.assertEqual("blocked", wrong_confirmation["status"])
        with self.assertRaises(RuntimeManagerError):
            execute_purge_trash(self.root, wrong_confirmation, now=NOW)
        self.assertTrue(Path(second_trash["path"]).exists())

        purge = plan_purge_trash(
            self.root,
            selector=second_trash["item_id"],
            confirmation=second_trash["resource_id"],
            now=NOW,
        )
        validate_operation_plan(purge)
        self.assertEqual("purge-trash", purge["operation"])
        self.assertEqual("ready", purge["status"])
        purge_receipt = execute_purge_trash(self.root, purge, now=NOW)
        validate_operation_receipt(purge_receipt)
        self.assertEqual("complete", purge_receipt["status"])
        self.assertFalse(Path(second_trash["path"]).exists())
        self.assertFalse(runtime.exists())

    def test_cleanup_and_trash_restore_recheck_staleness_and_collision(self) -> None:
        runtime = self._create_runtime("freshness")
        _write(runtime / "marker", b"before")
        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory, "fixtures/managed-runtime--fixture--freshness"
        )
        stale = plan_cleanup(self.root, selector=item["item_id"], now=NOW)
        _write(runtime / "marker", b"changed after preview")

        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, stale, now=NOW)
        self.assertEqual(b"changed after preview", (runtime / "marker").read_bytes())

        current = self._inventory()
        current_item = self._item_with_suffix(
            current, "fixtures/managed-runtime--fixture--freshness"
        )
        cleanup = plan_cleanup(
            self.root, selector=current_item["item_id"], now=NOW
        )
        execute_cleanup(self.root, cleanup, now=NOW)
        trashed = self._inventory()
        trash = next(
            row
            for row in trashed["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path")
                == ".workbench/fixtures/managed-runtime--fixture--freshness"
                for reference in row["references"]
            )
        )
        restore = plan_restore_trash(
            self.root, selector=trash["item_id"], now=NOW
        )
        _write(runtime / "sentinel", b"collision")

        with self.assertRaises(RuntimeManagerError):
            execute_restore_trash(self.root, restore, now=NOW)
        self.assertEqual(b"collision", (runtime / "sentinel").read_bytes())
        self.assertTrue(Path(trash["path"]).exists())

    def test_trash_restore_checks_symlink_ancestors_before_creating_parents(self) -> None:
        iteration = self.storage / "iterations/worldgen/symlink-restore"
        runtime = iteration / "runtime"
        _write(runtime / "world/level.dat", b"retained")
        _iteration_report(
            iteration / "iteration-report-v1.json",
            label="symlink-restore",
            status="complete",
            runtime=runtime,
        )
        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory, "iterations/worldgen/symlink-restore"
        )
        cleanup = plan_cleanup(
            self.root,
            selector=item["item_id"],
            allow_review=True,
            now=NOW,
        )
        execute_cleanup(self.root, cleanup, now=NOW)
        trashed = self._inventory()
        trash = next(
            row
            for row in trashed["items"]
            if row["kind"] == "trash"
            and any(
                reference.get("target_path")
                == ".workbench/iterations/worldgen/symlink-restore"
                for reference in row["references"]
            )
        )
        restore = plan_restore_trash(
            self.root, selector=trash["item_id"], now=NOW
        )
        (self.storage / "iterations/worldgen").rmdir()
        (self.storage / "iterations").rmdir()
        outside = self.root / "outside-restore-parent"
        outside.mkdir()
        try:
            (self.storage / "iterations").symlink_to(
                outside, target_is_directory=True
            )
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        with self.assertRaises(RuntimeManagerError):
            execute_restore_trash(self.root, restore, now=NOW)

        self.assertTrue(Path(trash["path"]).is_dir())
        self.assertEqual({}, _snapshot_tree(outside))

    def test_cleanup_refuses_an_outside_trash_ledger_symlink(self) -> None:
        runtime = self._create_runtime("unsafe-trash-ledger")
        outside = self.root / "outside-trash-ledger"
        outside.mkdir()
        ledger_link = self.storage / "runtime-manager/trash"
        try:
            ledger_link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory,
            "fixtures/managed-runtime--fixture--unsafe-trash-ledger",
        )
        plan = plan_cleanup(self.root, selector=item["item_id"], now=NOW)

        with self.assertRaises(RuntimeManagerError):
            execute_cleanup(self.root, plan, now=NOW)

        self.assertTrue(runtime.is_dir())
        self.assertEqual({}, _snapshot_tree(outside))

    def test_trash_restore_rejects_a_forged_category_root_destination(self) -> None:
        payload = self.storage / "trash/forged-payload"
        _write(payload / "sentinel", b"must remain quarantined")
        original_relative = ".workbench/fixtures"
        material = {
            "created_at": "2026-08-04T21:00:00Z",
            "original_item_id": _identity(
                "workbench-storage-item:sha256:",
                {
                    "storage_root": str(self.storage),
                    "relative_path": original_relative,
                },
            ),
            "original_resource_id": None,
            "original_relative_path": original_relative,
            "quarantine_relative_path": ".workbench/trash/forged-payload",
            "original_observation_sha256": "0" * 64,
        }
        record = {
            "format": "workbench-storage-trash-transaction-v1",
            "schema_version": 1,
            "canonicalization_id": "workbench-canonical-json-v1",
            "trash_id": _identity("workbench-storage-trash:sha256:", material),
            **material,
        }
        ledger = (
            self.storage
            / "runtime-manager/trash"
            / f"{record['trash_id'].split(':')[-1]}.json"
        )
        _write_json(ledger, record)
        inventory = self._inventory()
        item = self._item_with_suffix(inventory, "trash/forged-payload")
        self.assertNotEqual("managed", item["custody"]["state"])

        with self.assertRaises(RuntimeManagerError):
            plan_restore_trash(self.root, selector=item["item_id"], now=NOW)

        self.assertTrue(payload.is_dir())
        self.assertFalse((self.storage / "fixtures").exists())

    def test_valid_looking_trash_record_without_cleanup_receipt_stays_protected(self) -> None:
        original_relative = ".workbench/cache/forged-resource"
        original_item_id = _identity(
            "workbench-storage-item:sha256:",
            {
                "storage_root": str(self.storage),
                "relative_path": original_relative,
            },
        )
        quarantine_relative = (
            ".workbench/trash/runtime-manager--20260804210000--"
            f"{original_item_id.split(':')[-1][:12]}--forged-resource"
        )
        payload = self.root / quarantine_relative
        _write(payload / "sentinel", b"no completed cleanup receipt")
        material = {
            "created_at": "2026-08-04T21:00:00Z",
            "original_item_id": original_item_id,
            "original_resource_id": None,
            "original_relative_path": original_relative,
            "quarantine_relative_path": quarantine_relative,
            "original_observation_sha256": "1" * 64,
        }
        record = {
            "format": "workbench-storage-trash-transaction-v1",
            "schema_version": 1,
            "canonicalization_id": "workbench-canonical-json-v1",
            "trash_id": _identity("workbench-storage-trash:sha256:", material),
            **material,
        }
        ledger = (
            self.storage
            / "runtime-manager/trash"
            / f"{record['trash_id'].split(':')[-1]}.json"
        )
        _write_json(ledger, record)

        inventory = self._inventory()
        item = self._item_with_suffix(inventory, Path(quarantine_relative).name)
        self.assertEqual("protected", item["deletion"]["state"])
        self.assertNotEqual("managed", item["custody"]["state"])
        self.assertIn("legacy-trash-no-receipt", item["deletion"]["reason_codes"])
        with self.assertRaises(RuntimeManagerError):
            plan_restore_trash(self.root, selector=item["item_id"], now=NOW)

        self.assertTrue(payload.is_dir())

    def test_legacy_complete_iteration_requires_explicit_review_override(self) -> None:
        iteration = self.storage / "iterations/worldgen/legacy-complete"
        runtime = iteration / "runtime"
        _write(runtime / "world/level.dat", b"legacy")
        _iteration_report(
            iteration / "iteration-report-v1.json",
            label="legacy-complete",
            status="complete",
            runtime=runtime,
        )
        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory, "iterations/worldgen/legacy-complete"
        )
        self.assertEqual("recognized-legacy", item["custody"]["state"])
        self.assertEqual("review", item["deletion"]["state"])

        blocked = plan_cleanup(
            self.root,
            selector=item["item_id"],
            allow_review=False,
            now=NOW,
        )
        allowed = plan_cleanup(
            self.root,
            selector=item["item_id"],
            allow_review=True,
            now=NOW,
        )
        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("ready", allowed["status"])
        self.assertTrue(iteration.exists())

    def test_cli_json_inventory_and_runtime_preview_are_read_only(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)
        before = _snapshot_tree(self.storage)

        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = manager_cli_main(["storage", "list", "--json"], root=self.root)
        self.assertEqual(0, code, errors.getvalue())
        self.assertEqual("", errors.getvalue())
        inventory = json.loads(output.getvalue())
        validate_storage_inventory(inventory)
        self.assertEqual(before, _snapshot_tree(self.storage))

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = manager_cli_main(
                [
                    "runtime",
                    "create",
                    "--profile",
                    "fixture",
                    "--runtime-template",
                    str(template),
                    "--label",
                    "cli-preview",
                    "--json",
                ],
                root=self.root,
            )
        self.assertEqual(0, code, errors.getvalue())
        plan = json.loads(output.getvalue())
        validate_operation_plan(plan)
        self.assertEqual("runtime-create", plan["operation"])
        self.assertFalse(
            (
                self.storage
                / "fixtures/managed-runtime--fixture--cli-preview"
            ).exists()
        )
        self.assertEqual(before, _snapshot_tree(self.storage))

    def test_cli_uses_setup_workspace_for_state_and_suite_root_for_profiles(self) -> None:
        _profile(self.root)
        external = self.root.parent / "Selected Pack"
        external.mkdir()
        (external / ".workbench").mkdir()
        template = _runtime_template(external)
        before_authority = _snapshot_tree(self.root)
        before_state = _snapshot_tree(external / ".workbench")

        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch.dict(os.environ, {"WORKBENCH_WORKSPACE": str(external)}),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            code = manager_cli_main(
                [
                    "runtime",
                    "create",
                    "--profile",
                    "fixture",
                    "--runtime-template",
                    str(template),
                    "--label",
                    "external-preview",
                    "--json",
                ],
                root=self.root,
            )

        self.assertEqual(0, code, errors.getvalue())
        plan = json.loads(output.getvalue())
        self.assertEqual(str(external.resolve()), plan["workspace_root"])
        self.assertEqual(
            str((external / ".workbench").resolve()), plan["storage_root"]
        )
        self.assertTrue(plan["profile"]["profile_path"].startswith(str(self.root)))
        self.assertEqual(before_authority, _snapshot_tree(self.root))
        self.assertEqual(before_state, _snapshot_tree(external / ".workbench"))

    def test_cli_runtime_create_executes_by_default_and_cleanup_requires_apply(self) -> None:
        _profile(self.root)
        template = _runtime_template(self.root)
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = manager_cli_main(
                [
                    "runtime",
                    "create",
                    "--profile",
                    "fixture",
                    "--runtime-template",
                    str(template),
                    "--label",
                    "cli-execute",
                ],
                root=self.root,
            )
        self.assertEqual(0, code, errors.getvalue())
        self.assertEqual("", errors.getvalue())
        runtime = (
            self.storage / "fixtures/managed-runtime--fixture--cli-execute"
        )
        self.assertTrue(runtime.is_dir())

        inventory = self._inventory()
        item = self._item_with_suffix(
            inventory, "fixtures/managed-runtime--fixture--cli-execute"
        )
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = manager_cli_main(
                ["storage", "cleanup", item["item_id"]], root=self.root
            )
        self.assertEqual(0, code)
        self.assertTrue(runtime.exists())
        self.assertIn("cleanup", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = manager_cli_main(
                ["storage", "cleanup", item["item_id"], "--apply"],
                root=self.root,
            )
        self.assertEqual(0, code)
        self.assertFalse(runtime.exists())


if __name__ == "__main__":
    unittest.main()
