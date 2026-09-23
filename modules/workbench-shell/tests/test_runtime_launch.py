"""Focused tests for guarded Prism and MultiMC client launch projection."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import stat
import tempfile
import textwrap
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from packwiz_v2_fixture import (  # noqa: E402
    optional_file,
    seal_packwiz_v2_receipt,
)

from workbench_shell import (  # noqa: E402
    RuntimeLaunchError,
    host_platform,
    launch_materialized_client,
    probe_java,
)
from workbench_shell.runtime_launch import (  # noqa: E402
    _materialized_instance,
    _monitor_launch,
    _probe_launcher_root,
    _windows_launcher_blocker,
)
import workbench_shell.runtime_launch as runtime_launch  # noqa: E402


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_executable(path: Path, source: str) -> None:
    path.write_text(
        textwrap.dedent(source).lstrip(),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _java_result(root: Path) -> dict[str, object]:
    java = root / "jdk/bin/java"
    java.parent.mkdir(parents=True)
    _write_executable(
        java,
        r"""
        #!/bin/sh
        java_home_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
        cat >&2 <<EOF
        Property settings:
            java.version = 25.0.4
            java.runtime.version = 25.0.4+7-LTS
            java.vendor = Eclipse Adoptium
            java.vendor.version = Temurin-25.0.4+7
            java.home = $java_home_dir
            java.vm.name = OpenJDK 64-Bit Server VM
            java.vm.version = 25.0.4+7-LTS
            os.arch = amd64
        openjdk version "25.0.4"
        EOF
        exit 0
        """,
    )
    probe = probe_java(java)
    return {
        "format": "workbench-java-runtime-result-v1",
        "schema_version": 1,
        "outcome": "discovered",
        "source": "external",
        "policy": {
            "runtime_identity": "eclipse-temurin-25.0.4+7",
        },
        "host": host_platform(),
        "runtime": {
            "origin": "test",
            "java_uri": java.as_uri(),
            "probe": probe,
            "state": "compatible",
        },
    }


def _payload_identity(root: Path) -> dict[str, object]:
    entries = []
    total = 0
    for path in sorted(
        (root / ".minecraft").rglob("*"),
        key=lambda item: item.relative_to(root / ".minecraft").as_posix(),
    ):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        entries.append({
            "mode": stat.S_IMODE(path.stat().st_mode),
            "path": path.relative_to(root / ".minecraft").as_posix(),
            "sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        })
        total += len(payload)
    return {
        "tree_sha256": "sha256:" + sha256(
            _canonical_bytes(entries)
        ).hexdigest(),
        "file_count": len(entries),
        "total_bytes": total,
    }


def _materialization(
    root: Path,
    *,
    launcher: str = "prism",
) -> dict[str, object]:
    instance = root / "fixture/instance"
    (instance / ".minecraft/config").mkdir(parents=True)
    (instance / "instance.cfg").write_text(
        "JavaPath=Replace this with your java path\n"
        "IgnoreJavaCompatibility=true\n"
        "InstanceType=OneSix\n"
        "ManagedPack=false\n"
        "name=Cleanroom\n",
        encoding="utf-8",
    )
    manifest = b'{"components":[{"uid":"net.minecraft","version":"1.12.2"}]}\n'
    (instance / "mmc-pack.json").write_bytes(manifest)
    (instance / ".minecraft/config/example.cfg").write_text(
        "useful=true\n",
        encoding="utf-8",
    )
    payload = _payload_identity(instance)
    receipt = {
        "format": "workbench-packwiz-materialization-receipt-v2",
        "schema_version": 2,
        "materialization_id": "sha256:" + ("1" * 64),
        "operation_class": "local-mutation",
        "state": "materialized",
        "readiness": "pack-payload-installed",
        "plan_id": "sha256:" + ("2" * 64),
        "request": {"side": "client", "launcher": launcher},
        "project": {
            "name": "Supersymmetry",
            "version": "test",
        },
        "launcher": {
            "manifest_sha256_after": sha256(manifest).hexdigest(),
        },
        "payload": {
            **payload,
            "root_uri": (instance / ".minecraft").as_uri(),
        },
        "target": {
            "instance_root_uri": instance.as_uri(),
        },
    }
    receipt = seal_packwiz_v2_receipt(
        receipt,
        files=[optional_file(enabled=False)],
    )
    return {
        "format": "workbench-packwiz-materialization-result-v2",
        "schema_version": 2,
        "outcome": "reused",
        "receipt": receipt,
    }


def _launcher(
    path: Path,
    *,
    crash: bool = False,
    family: str = "prism",
) -> None:
    if crash:
        action = r"""
        crash_root = instance / ".minecraft/crash-reports"
        crash_root.mkdir(parents=True, exist_ok=True)
        (crash_root / "crash-test-client.txt").write_text(
            "---- Minecraft Crash Report ----\ndeliberate test crash\n",
            encoding="utf-8",
        )
        raise SystemExit(7)
        """
    else:
        action = r"""
        log_root = instance / ".minecraft/logs"
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "latest.log").write_text(
            f"[Client thread/INFO] [Minecraft]: "
            f"Setting user: {selected_name}\n"
            "[Client thread/INFO] [FML]: "
            "Forge Mod Loader has successfully loaded 188 mods\n",
            encoding="utf-8",
        )
        """
    version = (
        "PrismLauncher 11.0.2-test"
        if family == "prism"
        else "MultiMC 0.7.0-test"
    )
    source = f"""\
#!/usr/bin/env python3
from pathlib import Path
import sys

if "--version" in sys.argv:
    print({version!r})
    raise SystemExit(0)
root = Path(sys.argv[sys.argv.index("--dir") + 1])
instance_id = sys.argv[sys.argv.index("--launch") + 1]
instance = root / "instances" / instance_id
selected_name = "Workbench"
if "--profile" in sys.argv:
    profile = sys.argv[sys.argv.index("--profile") + 1]
    selected_name = profile
    print(f'Launching with account "{{profile}}"', flush=True)
    print(
        'Processing account "UnrelatedAccount" '
        'with internal ID "private-test-id"',
        flush=True,
    )
"""
    source += textwrap.dedent(action).lstrip()
    _write_executable(path, source)


def _launcher_root(root: Path, family: str = "prism") -> Path:
    data = root / "launcher-data"
    data.mkdir()
    config = (
        data / "prismlauncher.cfg"
        if family == "prism"
        else data / "multimc.cfg"
    )
    config.write_text(
        "[General]\nConfigVersion=1.3\n",
        encoding="utf-8",
    )
    (data / "accounts.json").write_text(
        '{"formatVersion":3,"accounts":[]}\n',
        encoding="utf-8",
    )
    return data


class RuntimeLaunchTest(unittest.TestCase):
    def test_managed_java_selection_preserves_execution_alias_lexically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            custody_home = root / "custody/jdk"
            custody_java = custody_home / "bin/java"
            custody_java.parent.mkdir(parents=True)
            custody_java.write_bytes(b"java")
            alias_home = root / "execution-alias"
            alias_home.symlink_to(custody_home, target_is_directory=True)
            alias_java = alias_home / "bin/java"
            host = host_platform()
            result = {
                "format": "workbench-java-runtime-result-v2",
                "schema_version": 2,
                "source": "managed",
                "receipt": {
                    "format": "workbench-java-runtime-receipt-v2",
                    "schema_version": 2,
                    "host": host,
                    "runtime_id": "sha256:" + "1" * 64,
                    "policy": {},
                    "probe": {
                        "java_home": "@runtime/jdk",
                        "runtime_version": "25.0.4+7-LTS",
                    },
                    "target": {"java_uri": alias_java.as_uri()},
                },
            }

            with patch.object(
                runtime_launch,
                "probe_java",
                return_value={
                    "java_home": str(custody_home),
                    "runtime_version": "25.0.4+7-LTS",
                },
            ):
                selected, identity = runtime_launch._selected_java(result, host)

            self.assertEqual(alias_java.absolute(), selected)
            self.assertNotEqual(custody_java.resolve(), selected)
            self.assertEqual(alias_java.as_uri(), identity["java_uri"])

    def test_projection_hashes_source_payload_once_for_both_identity_views(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _materialization(root)
            source = root / "fixture/instance"
            payload_root = source / ".minecraft"
            (payload_root / "mods").mkdir()
            (payload_root / "mods/example.jar").write_bytes(
                b"normal-projection-artifact"
            )
            expected_payload = _payload_identity(source)
            expected_portable = runtime_launch._regular_tree_identity(
                payload_root,
                include_directories=False,
            )
            launcher_data = _launcher_root(root)
            instances = launcher_data / "instances"
            instances.mkdir()
            destination = instances / "source-read-count"
            source_reads: list[str] = []
            staged_reads: list[str] = []
            real_sha256_file = runtime_launch.sha256_file

            def observe_sha256(path: Path) -> tuple[str, int]:
                result = real_sha256_file(path)
                selected = Path(path)
                if selected.is_relative_to(payload_root):
                    source_reads.append(
                        selected.relative_to(payload_root).as_posix()
                    )
                elif ".minecraft" in selected.parts:
                    index = selected.parts.index(".minecraft")
                    staged_reads.append(
                        Path(*selected.parts[index + 1 :]).as_posix()
                    )
                return result

            with patch.object(
                runtime_launch,
                "sha256_file",
                side_effect=observe_sha256,
            ):
                record = runtime_launch._project_instance(
                    source,
                    destination,
                    expected_payload=expected_payload,
                    java_path="/managed-jdk/bin/java",
                    java_probe={
                        "os_arch": "amd64",
                        "java_version": "25.0.4",
                    },
                    display_name="Workbench normal projection",
                    memory_mib=4096,
                )

            expected_paths = [
                "config/example.cfg",
                "mods/example.jar",
            ]
            self.assertEqual(sorted(source_reads), expected_paths)
            self.assertEqual(
                sorted(staged_reads),
                sorted(expected_paths * 2),
            )
            self.assertEqual(
                record["payload"]["materialized"],
                expected_payload,
            )
            self.assertEqual(
                record["payload"]["portable_projection"],
                expected_portable,
            )

    def test_excluded_top_level_tree_is_pruned_without_identity_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            retained = b"retained-base-file"
            configuration = b"InstanceType=OneSix\n"
            kept = root / "base/nested/kept.bin"
            kept.parent.mkdir(parents=True)
            kept.write_bytes(retained)
            (root / "instance.cfg").write_bytes(configuration)
            excluded = root / ".minecraft/deep/payload"
            excluded.parent.mkdir(parents=True)
            excluded.write_bytes(b"excluded-payload")
            expected_entries = [
                {"kind": "directory", "path": "base"},
                {"kind": "directory", "path": "base/nested"},
                {
                    "kind": "file",
                    "path": "base/nested/kept.bin",
                    "sha256": sha256(retained).hexdigest(),
                    "size": len(retained),
                },
                {
                    "kind": "file",
                    "path": "instance.cfg",
                    "sha256": sha256(configuration).hexdigest(),
                    "size": len(configuration),
                },
            ]
            expected = {
                "tree_sha256": "sha256:"
                + sha256(_canonical_bytes(expected_entries)).hexdigest(),
                "file_count": 2,
                "total_bytes": len(retained) + len(configuration),
            }
            visited: list[Path] = []
            real_scandir = runtime_launch.os.scandir

            def observe_scandir(path: Path):
                visited.append(Path(path))
                return real_scandir(path)

            with patch.object(
                runtime_launch.os,
                "scandir",
                side_effect=observe_scandir,
            ):
                observed = runtime_launch._regular_tree_identity(
                    root,
                    include_directories=True,
                    excluded_top_level=frozenset({".minecraft"}),
                )

            self.assertEqual(observed, expected)
            excluded_root = root / ".minecraft"
            self.assertFalse(
                any(
                    path == excluded_root
                    or path.is_relative_to(excluded_root)
                    for path in visited
                )
            )

    def test_accepts_native_v2_materialization_without_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialization = _materialization(root)

            receipt, instance = _materialized_instance(
                materialization,
                "prism",
            )

            self.assertEqual(
                receipt["format"],
                "workbench-packwiz-materialization-receipt-v2",
            )
            self.assertEqual(receipt, materialization["receipt"])
            self.assertEqual(instance, (root / "fixture/instance").resolve())
    def test_rejects_forged_v2_packwiz_option_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            materialization = _materialization(Path(temporary))
            materialization["receipt"]["packwiz_options"]["files"][0][
                "name"
            ] = "Forged optional fixture"

            with self.assertRaisesRegex(
                RuntimeLaunchError,
                "not an exact populated client materialization",
            ):
                _materialized_instance(materialization, "prism")

    def test_projects_configures_and_observes_client_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "external-runtime-state"
            materialization = _materialization(root)
            source = root / "fixture/instance"
            launcher = root / "prismlauncher"
            _launcher(launcher)
            launcher_data = _launcher_root(root)

            result = launch_materialized_client(
                materialization,
                suite_root=root,
                state_root=state_root,
                launcher="prism",
                launcher_executable=launcher,
                launcher_root=launcher_data,
                java_result=_java_result(root),
                launcher_profile="DeveloperProfile",
                memory_mib=4096,
                timeout_seconds=5,
                poll_interval_seconds=0.02,
            )

            self.assertEqual(result["outcome"], "checkpoint-reached")
            receipt = result["receipt"]
            self.assertEqual(
                receipt["format"],
                "workbench-runtime-launch-receipt-v1",
            )
            self.assertEqual(receipt["schema_version"], 1)
            self.assertEqual(
                receipt["observation"]["checkpoint"]["id"],
                "fml-client-loaded",
            )
            self.assertIn(
                "<redacted-profile>",
                receipt["launcher"]["command"],
            )
            self.assertNotIn(
                "--offline",
                receipt["launcher"]["command"],
            )
            self.assertEqual(
                receipt["launch_policy"]["account_mode"],
                "launcher-profile",
            )
            self.assertIsNone(
                receipt["launch_policy"]["offline_name"]
            )
            self.assertNotIn(
                "DeveloperProfile",
                json.dumps(receipt, sort_keys=True),
            )
            projection = Path(
                receipt["launcher"]["projection_uri"].removeprefix(
                    "file://"
                )
            )
            projected_config = (
                projection / "instance.cfg"
            ).read_text(encoding="utf-8")
            self.assertIn("OverrideJavaLocation=true", projected_config)
            self.assertIn("MaxMemAlloc=4096", projected_config)
            self.assertNotIn(
                "OverrideJavaLocation=true",
                (source / "instance.cfg").read_text(encoding="utf-8"),
            )
            self.assertTrue(
                Path(
                    receipt["target"]["receipt_uri"].removeprefix(
                        "file://"
                    )
                ).is_file()
            )
            self.assertTrue(
                Path(receipt["target"]["run_root_uri"].removeprefix("file://"))
                .is_relative_to(state_root / "evidence/runtime")
            )
            self.assertFalse((root / ".workbench").exists())
            captured = {
                item["label"]: item["state"]
                for item in receipt["evidence"]
            }
            self.assertEqual(
                captured["minecraft-latest-log"],
                "captured",
            )
            minecraft_log = next(
                item
                for item in receipt["evidence"]
                if item["label"] == "minecraft-latest-log"
            )
            minecraft_log_text = Path(
                minecraft_log["capture_uri"].removeprefix("file://")
            ).read_text(encoding="utf-8")
            self.assertNotIn("DeveloperProfile", minecraft_log_text)
            self.assertIn(
                "<redacted-launch-identity>",
                minecraft_log_text,
            )
            self.assertEqual(
                minecraft_log["content_treatment"],
                {
                    "kind": "exact-launch-identity-redaction",
                    "match_count": 1,
                    "replacement": "<redacted-launch-identity>",
                },
            )
            launcher_log = next(
                item
                for item in receipt["evidence"]
                if item["label"] == "launcher-command-log"
            )
            launcher_log_text = Path(
                launcher_log["capture_uri"].removeprefix("file://")
            ).read_text(encoding="utf-8")
            self.assertIn("<redacted-profile>", launcher_log_text)
            self.assertNotIn("DeveloperProfile", launcher_log_text)
            self.assertNotIn("UnrelatedAccount", launcher_log_text)
            self.assertNotIn("private-test-id", launcher_log_text)
            self.assertIn(
                '"launcher_output":"discarded-account-boundary"',
                launcher_log_text,
            )
            live_launcher_log = (
                Path(
                    receipt["target"]["run_root_uri"].removeprefix(
                        "file://"
                    )
                )
                / "launcher-command.live.log"
            ).read_text(encoding="utf-8")
            self.assertNotIn("DeveloperProfile", live_launcher_log)
            self.assertNotIn("UnrelatedAccount", live_launcher_log)

    def test_crash_report_is_a_failed_observation_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "prismlauncher"
            _launcher(launcher, crash=True)
            launcher_data = _launcher_root(root)

            result = launch_materialized_client(
                _materialization(root),
                suite_root=root,
                launcher="prism",
                launcher_executable=launcher,
                launcher_root=launcher_data,
                java_result=_java_result(root),
                timeout_seconds=5,
                poll_interval_seconds=0.02,
            )

            self.assertEqual(result["outcome"], "failed")
            receipt = result["receipt"]
            self.assertEqual(
                receipt["observation"]["failure_kind"],
                "minecraft-crash-report",
            )
            crashes = [
                item
                for item in receipt["evidence"]
                if item["label"] == "minecraft-crash-report"
            ]
            self.assertEqual(crashes[0]["state"], "captured")

    def test_multimc_uses_the_same_launch_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "multimc"
            _launcher(launcher, family="multimc")
            launcher_data = _launcher_root(root, "multimc")

            result = launch_materialized_client(
                _materialization(root, launcher="multimc"),
                suite_root=root,
                launcher="multimc",
                launcher_executable=launcher,
                launcher_root=launcher_data,
                java_result=_java_result(root),
                timeout_seconds=5,
                poll_interval_seconds=0.02,
            )

            self.assertEqual(result["outcome"], "checkpoint-reached")
            self.assertEqual(
                result["receipt"]["launcher"]["family"],
                "multimc",
            )
            self.assertIn(
                "--offline",
                result["receipt"]["launcher"]["command"],
            )
            self.assertNotIn(
                "--profile",
                result["receipt"]["launcher"]["command"],
            )
            self.assertTrue(
                (root / "launcher-data/multimc.cfg").is_file()
            )

    def test_launcher_family_mismatch_fails_before_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "wrong-launcher"
            _write_executable(
                launcher,
                """\
                #!/bin/sh
                echo 'MultiMC 0.7.0'
                """,
            )

            with self.assertRaisesRegex(
                RuntimeLaunchError,
                "not prism",
            ):
                launch_materialized_client(
                    _materialization(root),
                    suite_root=root,
                    launcher="prism",
                    launcher_executable=launcher,
                    launcher_root=root / "launcher-data",
                    java_result=_java_result(root),
                )

            self.assertFalse((root / "launcher-data").exists())

    def test_launcher_root_probe_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "launcher-data"
            root.mkdir()
            (root / "prismlauncher.cfg").write_text(
                "[General]\n",
                encoding="utf-8",
            )
            (root / "accounts.json").write_text("{}\n", encoding="utf-8")

            record = _probe_launcher_root(root, "prism")

            self.assertEqual(root.as_uri(), record["root_uri"])
            self.assertFalse((root / "instances").exists())

    def test_windows_account_modal_is_a_concrete_launch_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".minecraft").mkdir()
            process = subprocess.Popen([
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ])
            try:
                with patch(
                    "workbench_shell.runtime_launch."
                    "_windows_launcher_blocker",
                    return_value={
                        "failure_kind":
                            "launcher-account-refresh-required",
                        "windows_pid": 1234,
                    },
                ):
                    result = _monitor_launch(
                        process,
                        root,
                        launcher_host={
                            "os": "windows",
                            "architecture": "x64",
                            "system": "Windows",
                            "machine": "AMD64",
                        },
                        launcher_image_name="prismlauncher.exe",
                        launcher_processes_before={},
                        timeout_seconds=5,
                        poll_interval_seconds=0.02,
                    )
            finally:
                process.terminate()
                process.wait()

            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(
                result["failure_kind"],
                "launcher-account-refresh-required",
            )
            self.assertEqual(result["process_state"], "blocked-window")
            self.assertEqual(result["windows_pid"], 1234)

    def test_windows_blocker_is_correlated_to_launch_process_changes(
        self,
    ) -> None:
        before = {
            100: "Account refresh failed - Prism Launcher 11.0.2",
            200: "Prism Launcher 11.0.2",
        }
        with patch(
            "workbench_shell.runtime_launch."
            "_windows_launcher_processes",
            return_value={
                100: "Account refresh failed - Prism Launcher 11.0.2",
                200: "Account refresh failed - Prism Launcher 11.0.2",
                300: "Prism Launcher Quick Setup",
            },
        ):
            result = _windows_launcher_blocker(
                "prismlauncher.exe",
                before,
            )

        self.assertEqual(
            result,
            {
                "failure_kind":
                    "launcher-account-refresh-required",
                "windows_pid": 200,
            },
        )

        with patch(
            "workbench_shell.runtime_launch."
            "_windows_launcher_processes",
        ) as processes:
            self.assertIsNone(
                _windows_launcher_blocker(
                    "prismlauncher.exe",
                    None,
                )
            )
            processes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
