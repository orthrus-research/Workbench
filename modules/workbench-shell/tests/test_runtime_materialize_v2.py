"""Behavior tests for Packwiz-declared-default client materialization V2."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse
from zipfile import ZipFile


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell import (  # noqa: E402
    PackwizMaterializationError,
    build_runtime_plan,
    materialize_packwiz_workspace_v2,
    verify_packwiz_materialization_receipt_identity,
)
from workbench_core.host_services import install_local_host_services  # noqa: E402
from workbench_core.packwiz_scratch import CorePackwizScratch  # noqa: E402
from workbench_core.packwiz_tree_scope import direct_packwiz_tree_scope  # noqa: E402
from workbench_api.temporary_leases import temporary_scratch_scope  # noqa: E402
from workbench_core.storage.registered import ResourceCatalog  # noqa: E402
from workbench_core.storage.tree_catalog import TreeCatalog  # noqa: E402


JAVA_IDENTITY = {
    "source": "test",
    "runtime_id": "test-java-25",
}


def _run(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def _write_executable(path: Path, source: str) -> None:
    path.write_text(
        textwrap.dedent(source).lstrip(),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _metadata(
    *,
    name: str,
    filename: str,
    payload: bytes,
    option: str = "",
) -> str:
    return textwrap.dedent(
        f'''\
        name = "{name}"
        filename = "{filename}"
        side = "client"

        [download]
        hash-format = "sha256"
        hash = "{sha256(payload).hexdigest()}"
        url = "https://example.invalid/{filename}"
        {option}
        '''
    )


def _workspace(root: Path) -> Path:
    workspace = root / "pack"
    (workspace / "config").mkdir(parents=True)
    (workspace / "mods").mkdir()
    (workspace / "pack.toml").write_text(
        '''\
name = "Packwiz V2 Fixture"
author = "Workbench"
version = "test"
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = "0000000000000000000000000000000000000000000000000000000000000000"

[versions]
forge = "14.23.5.2860"
minecraft = "1.12.2"
''',
        encoding="utf-8",
    )
    (workspace / "index.toml").write_text("\n", encoding="utf-8")
    (workspace / "config/example.cfg").write_text(
        "required=true\n",
        encoding="utf-8",
    )
    (workspace / "mods/required.pw.toml").write_text(
        _metadata(
            name="Required",
            filename="required.jar",
            payload=b"required artifact\n",
        ),
        encoding="utf-8",
    )
    (workspace / "mods/default-on.pw.toml").write_text(
        _metadata(
            name="Default on",
            filename="default-on.jar",
            payload=b"default-on artifact\n",
            option="[option]\noptional = true\ndefault = true",
        ),
        encoding="utf-8",
    )
    (workspace / "mods/default-off.pw.toml").write_text(
        _metadata(
            name="Default off",
            filename="default-off.jar",
            payload=b"default-off artifact\n",
            option="[option]\noptional = true\ndefault = false",
        ),
        encoding="utf-8",
    )
    (workspace / ".packwizignore").write_text(
        "untracked.txt\n",
        encoding="utf-8",
    )
    _run(workspace, "init", "--quiet")
    _run(workspace, "config", "user.name", "Workbench Test")
    _run(
        workspace,
        "config",
        "user.email",
        "workbench@example.invalid",
    )
    _run(workspace, "add", ".")
    _run(workspace, "commit", "--quiet", "-m", "fixture")
    (workspace / "untracked.txt").write_text(
        "must not enter the staged pack\n",
        encoding="utf-8",
    )
    return workspace


def _fake_packwiz(path: Path) -> None:
    _write_executable(
        path,
        r'''
        #!/usr/bin/env python3
        from hashlib import sha256
        from pathlib import Path
        import re
        import sys

        arguments = sys.argv[1:]
        config = Path(arguments[arguments.index("--config") + 1])
        mode_path = config.with_name("test-refresh-mode")
        mode = (
            mode_path.read_text(encoding="utf-8").strip()
            if mode_path.exists()
            else "normal"
        )
        root = Path.cwd()
        if mode == "pack-drift":
            (root / "config/example.cfg").write_text(
                "required=true\nrefresh-drift=true\n",
                encoding="utf-8",
            )

        records = []
        for relative, metafile in (
            ("config/example.cfg", False),
            ("mods/default-off.pw.toml", True),
            ("mods/default-on.pw.toml", True),
            ("mods/required.pw.toml", True),
        ):
            digest = sha256((root / relative).read_bytes()).hexdigest()
            record = (
                "[[files]]\n"
                f'file = "{relative}"\n'
                f'hash = "{digest}"\n'
            )
            if metafile:
                record += "metafile = true\n"
            records.append(record)
        index = (
            'hash-format = "sha256"\n\n' + "\n".join(records)
        ).encode("utf-8")
        (root / "index.toml").write_bytes(index)
        index_hash = sha256(index).hexdigest()
        manifest = (root / "pack.toml").read_text(encoding="utf-8")
        manifest = re.sub(
            r'(?m)^hash = "[0-9a-f]{64}"$',
            f'hash = "{index_hash}"',
            manifest,
            count=1,
        )
        (root / "pack.toml").write_text(manifest, encoding="utf-8")
        print("refreshed Packwiz fixture")
        ''',
    )


def _fake_java(path: Path) -> None:
    _write_executable(
        path,
        r'''
        #!/usr/bin/env python3
        from hashlib import sha256
        import json
        from pathlib import Path
        import shutil
        import sys
        import tomllib
        from urllib.parse import unquote, urlparse

        arguments = sys.argv[1:]
        target = Path(arguments[arguments.index("--pack-folder") + 1])
        source = Path(unquote(urlparse(arguments[-1]).path)).parent
        state_path = target / "packwiz.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        expected = {
            "cachedFiles": {
                "mods/default-off.pw.toml": {
                    "isOptional": True,
                    "optionValue": False,
                },
                "mods/default-on.pw.toml": {
                    "isOptional": True,
                    "optionValue": True,
                },
            },
            "cachedSide": "client",
        }
        if state != expected:
            print(
                "incomplete native Packwiz option state: "
                + json.dumps(state, sort_keys=True),
                file=sys.stderr,
            )
            raise SystemExit(23)

        mode_path = Path(__file__).with_name("installer-mode")
        mode = (
            mode_path.read_text(encoding="utf-8").strip()
            if mode_path.exists()
            else "normal"
        )
        (target / "config").mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            source / "config/example.cfg",
            target / "config/example.cfg",
        )
        (target / "mods").mkdir(parents=True, exist_ok=True)
        (target / "mods/required.jar").write_bytes(
            b"required artifact\n"
        )
        (target / "mods/default-on.jar").write_bytes(
            b"default-on artifact\n"
        )
        if mode == "fail":
            print("deliberate post-write failure", file=sys.stderr)
            raise SystemExit(7)

        pack = tomllib.loads(
            (source / "pack.toml").read_text(encoding="utf-8")
        )
        cached_files = {
            "mods/default-off.pw.toml": {
                "isOptional": True,
                "optionValue": False,
            },
            "mods/default-on.pw.toml": {
                "isOptional": True,
                "optionValue": True,
                "cachedLocation": "mods/default-on.jar",
            },
            "mods/required.pw.toml": {
                "isOptional": False,
                "cachedLocation": "mods/required.jar",
            },
        }
        if mode == "partial-state":
            del cached_files["mods/default-off.pw.toml"]
        elif mode == "mutated-choice":
            cached_files["mods/default-off.pw.toml"] = {
                "isOptional": True,
                "optionValue": True,
                "cachedLocation": "mods/default-off.jar",
            }
            (target / "mods/default-off.jar").write_bytes(
                b"default-off artifact\n"
            )
        final_state = {
            "cachedFiles": cached_files,
            "cachedSide": "client",
            "packFileHash": {
                "type": "sha256",
                "value": sha256(
                    (source / "pack.toml").read_bytes()
                ).hexdigest(),
            },
            "indexFileHash": {
                "type": pack["index"]["hash-format"],
                "value": pack["index"]["hash"],
            },
        }
        state_path.write_text(
            json.dumps(final_state, sort_keys=True),
            encoding="utf-8",
        )
        print("Finished successfully!")
        ''',
    )


def _installer(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "link/infra/packwiz/installer/Main.class",
            b"test",
        )


def _local_path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _plan_and_bootstrap_fixture(
    root: Path,
    workspace: Path,
    installer: Path,
) -> tuple[dict[str, object], dict[str, object], Path]:
    revision = _run(workspace, "rev-parse", "HEAD").strip()
    manifest = (workspace / "pack.toml").read_bytes()
    index = (workspace / "index.toml").read_bytes()
    context = {
        "workspace": {
            "root_uri": workspace.as_uri(),
            "revision": revision,
            "dirty": True,
            "dirty_entries": ["?? untracked.txt"],
        },
        "project": {
            "kind": "packwiz-modpack",
            "name": "Packwiz V2 Fixture",
            "version": "test",
            "author": "Workbench",
            "pack_format": "packwiz:1.1.0",
            "manifest_sha256": sha256(manifest).hexdigest(),
            "minecraft_version": "1.12.2",
            "loaders": [
                {"id": "forge", "version": "14.23.5.2860"}
            ],
            "index": {
                "file": "index.toml",
                "hash_format": "sha256",
                "declared_hash": "0" * 64,
                "actual_sha256": sha256(index).hexdigest(),
                "matches_declared_hash": False,
            },
            "matched_paths": ["pack.toml", "index.toml"],
        },
        "platform": {
            "profile_id": "workbench-platform:cleanroom:test",
            "status": "provisional",
            "minecraft_version": "1.12.2",
            "cleanroom_version": "0.6.8-alpha",
            "document_sha256": "4" * 64,
        },
        "pack": {
            "profile_family_id": "workbench-pack:test",
            "display_name": "Packwiz V2 Fixture",
            "status": "active",
            "selected_profile": "cleanroom-test",
            "maturity": "experimental",
            "platform_profile_id": "workbench-platform:cleanroom:test",
            "permitted_operations": ["observe", "construct"],
            "document_sha256": "5" * 64,
        },
    }
    installer_digest = sha256(installer.read_bytes()).hexdigest()
    lock = {
        "id": "packwiz_installer",
        "url": installer.as_uri(),
        "source_revision": "9" * 40,
        "sha256": installer_digest,
        "size": installer.stat().st_size,
    }
    profile = {
        "java": {"runtime": "test-java-25"},
        "runtime_artifacts": {
            "packwiz_installer": {
                "url": lock["url"],
                "sha256": lock["sha256"],
            },
            "cleanroom_client": {
                "url": "https://example.invalid/cleanroom.zip",
                "sha256": "7" * 64,
            },
            "cleanroom_server": {
                "url": "https://example.invalid/cleanroom.jar",
                "sha256": "8" * 64,
            },
        },
    }
    state = root / "state"
    plan = build_runtime_plan(
        context,
        profile,
        state_root=state,
        side="client",
        launcher="prism",
    )
    fixture = _local_path(str(plan["target"]["fixture_root_uri"]))
    instance = fixture / "instance"
    (fixture / "receipts").mkdir(parents=True)
    instance.mkdir(parents=True)
    (instance / "mmc-pack.json").write_text(
        json.dumps({
            "formatVersion": 1,
            "components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "0.6.8-alpha"},
            ],
        }),
        encoding="utf-8",
    )
    (fixture / "receipts/cleanroom-client-bootstrap-v1.json").write_text(
        json.dumps({
            "format": "workbench-runtime-bootstrap-receipt-v1",
            "schema_version": 1,
            "state": "materialized",
            "plan_id": plan["plan_id"],
        }),
        encoding="utf-8",
    )
    return plan, lock, fixture


def _case(root: Path) -> dict[str, object]:
    workspace = _workspace(root)
    tools = root / "tools"
    tools.mkdir()
    packwiz = tools / "packwiz"
    java = tools / "java"
    installer = tools / "installer.jar"
    _fake_packwiz(packwiz)
    _fake_java(java)
    _installer(installer)
    plan, lock, bootstrap_fixture = _plan_and_bootstrap_fixture(
        root,
        workspace,
        installer,
    )
    return {
        "root": root,
        "workspace": workspace,
        "packwiz": packwiz,
        "java": java,
        "installer": installer,
        "plan": plan,
        "lock": lock,
        "bootstrap_fixture": bootstrap_fixture,
    }


def _materialize(
    case: dict[str, object],
    *,
    seed_roots: tuple[Path, ...] = (),
    configuration_home: Path | None = None,
) -> dict[str, object]:
    selected_home = configuration_home or Path(case["root"]) / "config"
    with temporary_scratch_scope(CorePackwizScratch(
        configuration_home=selected_home,
    )), direct_packwiz_tree_scope(
        workspace=Path(case["workspace"]),
        state_root=Path(case["root"]) / "state",
        configuration_home=selected_home,
    ):
        return materialize_packwiz_workspace_v2(
            case["plan"],
            workspace_root=case["workspace"],
            state_root=Path(case["root"]) / "state",
            packwiz_executable=case["packwiz"],
            java_executable=case["java"],
            java_identity=JAVA_IDENTITY,
            installer_path=case["installer"],
            installer_lock=case["lock"],
            seed_roots=seed_roots,
            refresh_timeout_seconds=10,
            install_timeout_seconds=10,
        )


class RuntimeMaterializeV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()

    def test_seed_root_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            seed = Path(temporary) / "seed"
            seed.mkdir()
            seed_link = Path(temporary) / "seed-link"
            seed_link.symlink_to(seed, target_is_directory=True)

            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "seed root is not a regular directory",
            ):
                _materialize(case, seed_roots=(seed_link,))

    def test_receipt_identity_rejects_mutated_v2_byte_and_path_bindings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            receipt = _materialize(case)["receipt"]

            self.assertTrue(
                verify_packwiz_materialization_receipt_identity(receipt)
            )

            final_size = json.loads(json.dumps(receipt))
            final_size["packwiz_options"]["installer_state"]["final"][
                "size"
            ] += 1

            bootstrap_size = json.loads(json.dumps(receipt))
            bootstrap_size["bootstrap_source"]["receipt_size"] += 1

            payload_count = json.loads(json.dumps(receipt))
            payload_count["payload"]["file_count"] += 1

            relocated = json.loads(json.dumps(receipt))
            target = relocated["target"]
            relocated_root = target["fixture_root_uri"] + "-relocated"
            target.update({
                "fixture_root_uri": relocated_root,
                "variant_root_uri": relocated_root,
                "instance_root_uri": relocated_root + "/instance",
                "receipt_uri": relocated_root
                + "/receipts/packwiz-materialization-v2.json",
            })
            relocated["payload"]["root_uri"] = (
                target["instance_root_uri"] + "/.minecraft"
            )
            relocated["launcher"]["manifest_uri"] = (
                target["instance_root_uri"] + "/mmc-pack.json"
            )
            relocated["packwiz_options"]["installer_state"]["uri"] = (
                relocated["payload"]["root_uri"] + "/packwiz.json"
            )

            policy_side = json.loads(json.dumps(receipt))
            policy_side["option_policy"]["side"] = "server"
            policy_defaults = json.loads(json.dumps(receipt))
            policy_defaults["option_policy"]["optional_files"] = (
                "enabled-by-installer-cli"
            )
            policy_launcher = json.loads(json.dumps(receipt))
            policy_launcher["option_policy"]["launcher_metadata"] = (
                "modified-prism"
            )

            for label, mutation in (
                ("final installer-state size", final_size),
                ("bootstrap receipt size", bootstrap_size),
                ("payload file count", payload_count),
                ("target URI relocation", relocated),
                ("derived option-policy side", policy_side),
                ("derived option-policy defaults", policy_defaults),
                ("derived option-policy launcher", policy_launcher),
            ):
                with self.subTest(label=label):
                    self.assertFalse(
                        verify_packwiz_materialization_receipt_identity(
                            mutation
                        )
                    )

    def test_installs_declared_defaults_in_a_distinct_variant_and_reuses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            bootstrap_fixture = Path(case["bootstrap_fixture"])
            bootstrap_before = _tree_bytes(bootstrap_fixture)

            created = _materialize(case)

            self.assertEqual(created["outcome"], "installed")
            receipt = created["receipt"]
            self.assertEqual(
                receipt["format"],
                "workbench-packwiz-materialization-receipt-v2",
            )
            self.assertEqual(
                receipt["option_policy"]["optional_files"],
                "pack-declared-defaults",
            )
            target = _local_path(receipt["target"]["fixture_root_uri"])
            payload = target / "instance/.minecraft"
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            trees = catalog.inventory(workspace=Path(case["workspace"]))["trees"]
            self.assertEqual(1, len(trees))
            self.assertEqual("committed", trees[0]["status"])
            self.assertEqual(str(target), trees[0]["path"])
            self.assertGreater(trees[0]["member_count"], 1)
            self.assertEqual(2, len(trees[0]["references"]))
            witness = json.loads(catalog.read_bytes(trees[0]["references"][0]))
            self.assertEqual(
                "workbench-packwiz-v2-dependencies-v4", witness["format"],
            )
            self.assertEqual(receipt["materialization_id"], witness["materialization_id"])
            self.assertEqual(
                sha256((target / "receipts/packwiz-materialization-v2.json").read_bytes()).hexdigest(),
                witness["receipt_sha256"],
            )
            self.assertEqual(receipt["bootstrap_source"], witness["bootstrap_source"])
            self.assertEqual(receipt["source_snapshot"], witness["source_snapshot"])
            self.assertEqual(receipt["tools"], witness["tools"])
            self.assertEqual(receipt["payload"], witness["payload"])
            source_lease = _local_path(witness["source_scratch_uri"]).parent
            self.assertEqual(witness["source_scratch_lease_id"], trees[0]["references"][1])
            self.assertEqual(
                [(witness["source_scratch_lease_id"], str(source_lease), "retained-unproven")],
                [(row["lease_id"], row["path"], row["status"]) for row in catalog.inventory(
                    workspace=Path(case["workspace"]),
                )["temporary_leases"]],
            )
            for row in witness["logs"].values():
                self.assertEqual(
                    row["sha256"],
                    sha256(catalog.read_bytes(row["resource_id"])).hexdigest(),
                )
            bootstrap = witness["bootstrap_receipt"]
            self.assertEqual(
                receipt["bootstrap_source"]["receipt_uri"],
                bootstrap["historical_uri"],
            )
            self.assertEqual(
                _local_path(bootstrap["historical_uri"]).read_bytes(),
                catalog.read_bytes(bootstrap["resource_id"]),
            )
            self.assertEqual(
                receipt["bootstrap_source"]["receipt_sha256"],
                bootstrap["sha256"],
            )
            for label, key in (("packwiz", "source_uri"), ("installer", "cache_uri")):
                source = witness["tool_sources"][label]
                self.assertEqual(
                    receipt["tools"][label][key], source["historical_uri"],
                )
                self.assertEqual(
                    _local_path(source["historical_uri"]).read_bytes(),
                    catalog.read_bytes(source["resource_id"]),
                )
                self.assertEqual(
                    receipt["tools"][label]["sha256"], source["sha256"],
                )
            self.assertNotEqual(target, bootstrap_fixture)
            self.assertTrue(
                target.is_relative_to(
                    Path(case["root"]) / "state/fixtures/packwiz-v2"
                )
            )
            self.assertEqual(
                (payload / "config/example.cfg").read_text(
                    encoding="utf-8"
                ),
                "required=true\n",
            )
            self.assertEqual(
                (payload / "mods/required.jar").read_bytes(),
                b"required artifact\n",
            )
            self.assertEqual(
                (payload / "mods/default-on.jar").read_bytes(),
                b"default-on artifact\n",
            )
            self.assertFalse((payload / "mods/default-off.jar").exists())
            self.assertEqual(
                _tree_bytes(bootstrap_fixture),
                bootstrap_before,
            )

            rows = {
                row["metadata_path"]: row
                for row in receipt["packwiz_options"]["files"]
            }
            self.assertTrue(rows["mods/default-on.pw.toml"]["applied"])
            self.assertTrue(rows["mods/default-on.pw.toml"]["present"])
            self.assertFalse(rows["mods/default-off.pw.toml"]["applied"])
            self.assertFalse(rows["mods/default-off.pw.toml"]["present"])
            final_state = json.loads(
                (payload / "packwiz.json").read_text(encoding="utf-8")
            )
            self.assertEqual(final_state["cachedSide"], "client")
            self.assertIn("packFileHash", final_state)
            self.assertIn("indexFileHash", final_state)
            self.assertTrue(
                verify_packwiz_materialization_receipt_identity(receipt)
            )
            forged = json.loads(json.dumps(receipt))
            forged["packwiz_options"]["disabled_count"] = 99
            self.assertFalse(
                verify_packwiz_materialization_receipt_identity(forged)
            )

            reused = _materialize(case)

            self.assertEqual(reused["outcome"], "reused")
            self.assertEqual(
                reused["receipt"]["materialization_id"],
                receipt["materialization_id"],
            )
            self.assertEqual(
                _tree_bytes(bootstrap_fixture),
                bootstrap_before,
            )

    def test_uncataloged_historical_target_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            created = _materialize(case)
            target = _local_path(created["receipt"]["target"]["fixture_root_uri"])
            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "historical ownership requires review",
            ):
                _materialize(
                    case, configuration_home=Path(temporary) / "other-config",
                )
            self.assertTrue(target.is_dir())

    def test_interrupted_post_move_publication_reconciles_without_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            original_write = TreeCatalog._write

            def interrupt_commit(self, name, nonce, kind, body):
                if name == "commits":
                    raise RuntimeError("simulated exit after Core moved the fixture")
                return original_write(self, name, nonce, kind, body)

            with patch.object(TreeCatalog, "_write", interrupt_commit):
                with self.assertRaisesRegex(RuntimeError, "simulated exit"):
                    _materialize(case)
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            trees = catalog.inventory(workspace=Path(case["workspace"]))["trees"]
            self.assertEqual("published-uncommitted", trees[0]["status"])
            target = Path(trees[0]["path"])
            receipt_bytes = (target / "receipts/packwiz-materialization-v2.json").read_bytes()

            reused = _materialize(case)
            self.assertEqual("reused", reused["outcome"])
            self.assertEqual(
                receipt_bytes,
                (target / "receipts/packwiz-materialization-v2.json").read_bytes(),
            )
            self.assertEqual(
                "committed",
                catalog.inventory(workspace=Path(case["workspace"]))["trees"][0]["status"],
            )

    def test_changed_retained_log_reference_blocks_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            _materialize(case)
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            inventory = catalog.inventory(workspace=Path(case["workspace"]))
            witness_id = inventory["trees"][0]["references"][0]
            witness = json.loads(catalog.read_bytes(witness_id))
            refresh_id = witness["logs"]["refresh"]["resource_id"]
            retained = next(
                row for row in inventory["resources"] if row["resource_id"] == refresh_id
            )
            Path(retained["path"]).write_bytes(b"changed retained log\n")

            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "earlier Core Packwiz V2 result requires review",
            ):
                _materialize(case)

    def test_changed_retained_bootstrap_receipt_blocks_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            _materialize(case)
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            inventory = catalog.inventory(workspace=Path(case["workspace"]))
            witness = json.loads(
                catalog.read_bytes(inventory["trees"][0]["references"][0])
            )
            bootstrap_id = witness["bootstrap_receipt"]["resource_id"]
            retained = next(
                row for row in inventory["resources"]
                if row["resource_id"] == bootstrap_id
            )
            Path(retained["path"]).write_bytes(b"changed retained bootstrap receipt\n")

            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "earlier Core Packwiz V2 result requires review",
            ):
                _materialize(case)

    def test_changed_retained_packwiz_tool_blocks_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            _materialize(case)
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            inventory = catalog.inventory(workspace=Path(case["workspace"]))
            witness = json.loads(
                catalog.read_bytes(inventory["trees"][0]["references"][0])
            )
            tool_id = witness["tool_sources"]["packwiz"]["resource_id"]
            retained = next(
                row for row in inventory["resources"]
                if row["resource_id"] == tool_id
            )
            Path(retained["path"]).write_bytes(b"changed retained Packwiz tool\n")

            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "earlier Core Packwiz V2 result requires review",
            ):
                _materialize(case)

    def test_changed_retained_source_lease_blocks_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            created = _materialize(case)
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            tree = catalog.inventory(workspace=Path(case["workspace"]))["trees"][0]
            witness = json.loads(catalog.read_bytes(tree["references"][0]))
            self.assertEqual(witness["source_scratch_lease_id"], tree["references"][1])
            retained = (
                _local_path(witness["source_scratch_uri"]).parent
                / ".workbench-temporary-retained.json"
            )
            original = retained.read_bytes()
            retained.write_bytes(b"{}\n")
            try:
                with self.assertRaisesRegex(
                    PackwizMaterializationError,
                    "earlier Core Packwiz V2 result requires review",
                ):
                    _materialize(case)
            finally:
                retained.write_bytes(original)
            target = _local_path(created["receipt"]["target"]["fixture_root_uri"])
            self.assertTrue(target.is_dir())

    def test_reuse_rejects_decision_pack_tool_and_payload_drift(self) -> None:
        for drift in ("decision", "pack", "tool", "payload"):
            with self.subTest(drift=drift):
                with tempfile.TemporaryDirectory() as temporary:
                    case = _case(Path(temporary))
                    created = _materialize(case)
                    receipt = created["receipt"]
                    target = _local_path(
                        receipt["target"]["fixture_root_uri"]
                    )
                    if drift == "decision":
                        receipt_path = target / (
                            "receipts/packwiz-materialization-v2.json"
                        )
                        recorded = json.loads(
                            receipt_path.read_text(encoding="utf-8")
                        )
                        row = recorded["packwiz_options"]["files"][0]
                        row["declared_default"] = not row[
                            "declared_default"
                        ]
                        row["applied"] = row["declared_default"]
                        receipt_path.write_text(
                            json.dumps(recorded),
                            encoding="utf-8",
                        )
                    elif drift == "pack":
                        mode = (
                            Path(case["root"])
                            / "state/cache/packwiz/test-refresh-mode"
                        )
                        mode.write_text("pack-drift\n", encoding="utf-8")
                    elif drift == "tool":
                        packwiz = Path(case["packwiz"])
                        packwiz.write_text(
                            packwiz.read_text(encoding="utf-8")
                            + "\n# tool identity drift\n",
                            encoding="utf-8",
                        )
                    else:
                        payload = target / "instance/.minecraft"
                        (payload / "mods/required.jar").write_bytes(
                            b"payload drift\n"
                        )

                    expected_error = (
                        "does not bind the refreshed pack"
                        if drift == "pack"
                        else "different materialization|drifted|Core cannot inspect Packwiz V2 result custody"
                    )
                    with self.assertRaisesRegex(
                        PackwizMaterializationError,
                        expected_error,
                    ):
                        _materialize(case)

    def test_partial_or_mutated_installer_option_state_is_rejected(self) -> None:
        for mode, message in (
            ("partial-state", "changed the declared option default"),
            ("mutated-choice", "changed the declared option default"),
        ):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as temporary:
                    case = _case(Path(temporary))
                    (Path(case["java"]).with_name("installer-mode")).write_text(
                        mode + "\n",
                        encoding="utf-8",
                    )
                    bootstrap_fixture = Path(case["bootstrap_fixture"])
                    bootstrap_before = _tree_bytes(bootstrap_fixture)

                    with self.assertRaisesRegex(
                        PackwizMaterializationError,
                        message,
                    ):
                        _materialize(case)

                    variants = (
                        Path(case["root"])
                        / "state/fixtures/packwiz-v2"
                    )
                    prepared = list(variants.iterdir())
                    self.assertEqual(len(prepared), 1)
                    self.assertTrue(prepared[0].name.startswith("."))
                    self.assertTrue((prepared[0] / "payload/instance/.minecraft/packwiz.json").is_file())
                    self.assertEqual(
                        _tree_bytes(bootstrap_fixture),
                        bootstrap_before,
                    )

    def test_post_write_installer_failure_has_no_partial_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = _case(Path(temporary))
            (Path(case["java"]).with_name("installer-mode")).write_text(
                "fail\n",
                encoding="utf-8",
            )
            bootstrap_fixture = Path(case["bootstrap_fixture"])
            bootstrap_before = _tree_bytes(bootstrap_fixture)

            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "exited with 7",
            ):
                _materialize(case)

            variants = Path(case["root"]) / "state/fixtures/packwiz-v2"
            prepared = list(variants.iterdir())
            self.assertEqual(len(prepared), 1)
            self.assertTrue(prepared[0].name.startswith("."))
            self.assertTrue((prepared[0] / "payload/instance/.minecraft/packwiz.json").is_file())
            (Path(case["java"]).with_name("installer-mode")).write_text(
                "normal\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PackwizMaterializationError,
                "earlier Core Packwiz V2 result requires review",
            ):
                _materialize(case)
            self.assertEqual(prepared, list(variants.iterdir()))
            staging = Path(case["root"]) / "state/staging/packwiz-v2"
            retained = list(staging.iterdir())
            self.assertEqual(2, len(retained))
            self.assertTrue(all(
                (path / ".workbench-temporary-retained.json").is_file()
                for path in retained
            ))
            catalog = ResourceCatalog(Path(case["root"]) / "config")
            self.assertEqual(["retained-unproven", "retained-unproven"], [
                row["status"] for row in catalog.inventory(workspace=Path(case["workspace"]))["temporary_leases"]
            ])
            self.assertEqual(
                _tree_bytes(bootstrap_fixture),
                bootstrap_before,
            )
            log = (
                Path(case["root"])
                / "state/evidence/runtime"
                / str(case["plan"]["plan_id"])
                .removeprefix("sha256:")[:16]
                / "packwiz-materialization-v2/packwiz-installer.log"
            )
            self.assertIn(
                "deliberate post-write failure",
                log.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
