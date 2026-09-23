"""Focused tests for exact Temurin discovery and provisioning."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parent
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.runtime_java import (  # noqa: E402
    JavaRuntimeError,
    discover_java_runtime,
    ensure_java_runtime,
    load_java_runtime_policy,
    materialize_temurin_runtime,
)
from workbench_core import runtime_java as runtime_java_module  # noqa: E402


POLICY = {
    "profile_id": "workbench-platform:cleanroom:test",
    "runtime_identity": "eclipse-temurin-25.0.4+7",
    "distribution": "eclipse-temurin",
    "feature_version": 25,
    "release_name": "jdk-25.0.4+7",
    "release_type": "ga",
    "image_type": "jdk",
    "jvm_impl": "hotspot",
    "heap_size": "normal",
    "project": "jdk",
    "vendor": "eclipse",
    "java_vendor": "Eclipse Adoptium",
    "api_base_url": "https://api.adoptium.net/v3",
    "policy_sha256": "9" * 64,
}

HOST = {
    "os": "linux",
    "architecture": "x64",
    "system": "Linux",
    "machine": "x86_64",
}


def _java_script(
    *,
    runtime_version: str = "25.0.4+7-LTS",
) -> bytes:
    return f"""\
#!/bin/sh
java_home_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cat >&2 <<EOF
Property settings:
    java.version = 25.0.4
    java.runtime.version = {runtime_version}
    java.vendor = Eclipse Adoptium
    java.vendor.version = Temurin-25.0.4+7
    java.home = $java_home_dir
    java.vm.name = OpenJDK 64-Bit Server VM
    java.vm.version = 25.0.4+7-LTS
    os.arch = amd64
openjdk version "25.0.4"
EOF
exit 0
""".encode("utf-8")


def _add_tar_file(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mode: int = 0o644,
) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    archive.addfile(info, BytesIO(content))


def _archive(
    path: Path,
    *,
    runtime_version: str = "25.0.4+7-LTS",
    unsafe_member: str | None = None,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        if unsafe_member is not None:
            _add_tar_file(
                archive,
                unsafe_member,
                b"must not escape",
            )
        _add_tar_file(
            archive,
            "jdk-25.0.4+7/bin/java",
            _java_script(runtime_version=runtime_version),
            mode=0o755,
        )
        _add_tar_file(
            archive,
            "jdk-25.0.4+7/release",
            b'JAVA_VERSION="25.0.4"\nIMPLEMENTOR="Eclipse Adoptium"\n',
        )


def _asset(
    archive: Path,
    *,
    digest: str | None = None,
) -> dict[str, object]:
    return {
        "api_query_url": "https://api.example.invalid/temurin",
        "release_name": "jdk-25.0.4+7",
        "release_link": "https://example.invalid/jdk-25.0.4+7",
        "release_timestamp": "2026-07-27T17:54:20Z",
        "semver": "25.0.4+7.0.LTS",
        "openjdk_version": "25.0.4+7-LTS",
        "scm_ref": "jdk-25.0.4+7_adopt",
        "package_name": "OpenJDK25U-jdk_x64_linux_hotspot_25.0.4_7.tar.gz",
        "url": archive.as_uri(),
        "sha256": digest or sha256(archive.read_bytes()).hexdigest(),
        "size": archive.stat().st_size,
    }


def _uri_path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


class JavaRuntimeTest(unittest.TestCase):
    def test_repository_policy_selects_exact_temurin_25_ga(self) -> None:
        policy = load_java_runtime_policy(REPOSITORY_ROOT)

        self.assertEqual(
            policy["runtime_identity"],
            "eclipse-temurin-25.0.4+7",
        )
        self.assertEqual(policy["feature_version"], 25)
        self.assertEqual(policy["release_type"], "ga")
        self.assertEqual(policy["image_type"], "jdk")
        self.assertRegex(policy["policy_sha256"], r"^[0-9a-f]{64}$")

    def test_policy_version_comes_from_the_selected_platform_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            pack = suite / "profiles/packs/example/profile.yaml"
            platform = suite / "profiles/platforms/cleanroom/example.yaml"
            pack.parent.mkdir(parents=True)
            platform.parent.mkdir(parents=True)
            pack.write_text(
                "schema_version: 1\n"
                "profile_family_id: workbench-pack:example\n"
                "profiles:\n"
                "  selected:\n"
                "    platform_profile_id: workbench-platform:cleanroom:example\n",
                encoding="utf-8",
            )
            platform.write_text(
                "schema_version: 1\n"
                "profile_id: workbench-platform:cleanroom:example\n"
                "kind: cleanroom\n"
                "java:\n"
                "  runtime: eclipse-temurin-21.0.8+9\n"
                "  runtime_provision:\n"
                "    distribution: eclipse-temurin\n"
                "    feature_version: 21\n"
                "    release_name: jdk-21.0.8+9\n"
                "    release_type: ga\n"
                "    image_type: jdk\n"
                "    jvm_impl: hotspot\n"
                "    heap_size: normal\n"
                "    project: jdk\n"
                "    vendor: eclipse\n"
                "    java_vendor: Eclipse Adoptium\n"
                "    api_base_url: https://api.adoptium.net/v3\n",
                encoding="utf-8",
            )
            (suite / "workbench.toml").write_text(
                'schema = "workbench/config/v1"\n'
                "\n[selection]\n"
                'pack_document = "profiles/packs/example/profile.yaml"\n'
                'pack_variant = "selected"\n'
                'platform_document = "profiles/platforms/cleanroom/example.yaml"\n'
                "\n[bindings]\n",
                encoding="utf-8",
            )

            policy = load_java_runtime_policy(suite)

        self.assertEqual(21, policy["feature_version"])
        self.assertEqual("jdk-21.0.8+9", policy["release_name"])
        self.assertEqual(
            "eclipse-temurin-21.0.8+9",
            policy["runtime_identity"],
        )

    def test_materializes_probes_and_reuses_exact_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "temurin.tar.gz"
            _archive(archive)
            state_root = root / "state"

            created = materialize_temurin_runtime(
                deepcopy(POLICY),
                deepcopy(HOST),
                _asset(archive),
                state_root=state_root,
            )

            self.assertEqual(created["outcome"], "provisioned")
            self.assertEqual(created["artifact_outcome"], "downloaded")
            receipt = created["receipt"]
            self.assertEqual(
                receipt["format"],
                "workbench-java-runtime-receipt-v2",
            )
            self.assertEqual(
                receipt["target"]["custody_java_uri"],
                receipt["target"]["java_uri"],
            )
            self.assertEqual(receipt["state"], "ready")
            self.assertEqual(
                receipt["portability"],
                {
                    "cds": {
                        "state": "vendor-default",
                        "transform_id": None,
                        "removed_archives": [],
                    }
                },
            )
            self.assertEqual(
                receipt["probe"]["runtime_version"],
                "25.0.4+7-LTS",
            )
            self.assertTrue(
                receipt["probe"]["java_home"].startswith("@runtime/")
            )
            self.assertGreater(receipt["tree"]["file_count"], 0)
            java_path = _uri_path(receipt["target"]["java_uri"])
            self.assertTrue(java_path.is_file())

            reused = materialize_temurin_runtime(
                deepcopy(POLICY),
                deepcopy(HOST),
                _asset(archive),
                state_root=state_root,
            )
            self.assertEqual(reused["outcome"], "reused")
            self.assertEqual(
                reused["receipt"]["runtime_id"],
                receipt["runtime_id"],
            )

    def test_stale_managed_target_without_v2_receipt_requires_reprovision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "temurin.tar.gz"
            _archive(archive)
            state_root = root / "state"
            target = runtime_java_module._target_root(
                state_root,
                POLICY,
                HOST,
            )
            stale_receipt = target / "receipts/java-runtime-v1.json"
            stale_receipt.parent.mkdir(parents=True)
            stale_receipt.write_text("{}\n", encoding="utf-8")
            retained = stale_receipt.read_bytes()

            with self.assertRaisesRegex(
                JavaRuntimeError,
                "lacks the current V2 runtime receipt",
            ):
                runtime_java_module.inspect_managed_java_runtime(
                    deepcopy(POLICY),
                    deepcopy(HOST),
                    state_root=state_root,
                )

            with (
                patch.object(
                    runtime_java_module,
                    "fetch_verified_artifact",
                ) as fetch,
                self.assertRaisesRegex(
                    JavaRuntimeError,
                    "remove the stale target before provisioning again",
                ),
            ):
                materialize_temurin_runtime(
                    deepcopy(POLICY),
                    deepcopy(HOST),
                    _asset(archive),
                    state_root=state_root,
                )

            fetch.assert_not_called()
            self.assertEqual(retained, stale_receipt.read_bytes())

    def test_probe_preserves_lexical_path_and_scrubs_java_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real/bin/java"
            real.parent.mkdir(parents=True)
            real.write_bytes(b"java")
            alias = root / "alias-java"
            alias.symlink_to(real)
            output = _java_script().decode("utf-8").split("cat >&2 <<EOF\n", 1)[1]
            output = output.split("\nEOF", 1)[0]
            completed = __import__("subprocess").CompletedProcess(
                [],
                0,
                "",
                output,
            )
            hostile = {
                "PaTh": "/required/path",
                "SystemRoot": "C:\\Windows",
                "JaVa_ToOl_OpTiOnS": "-javaagent:hostile.jar",
                "_JaVa_OpTiOnS": "-Xbootclasspath/a:hostile.jar",
                "JdK_JaVa_OpTiOnS": "-XX:StartFlightRecording",
            }
            with patch.dict(runtime_java_module.os.environ, hostile, clear=True):
                with patch.object(
                    runtime_java_module.subprocess,
                    "run",
                    return_value=completed,
                ) as run:
                    probe = runtime_java_module.probe_java(alias)

            self.assertEqual("25.0.4+7-LTS", probe["runtime_version"])
            arguments = run.call_args.args[0]
            self.assertEqual(str(alias.absolute()), arguments[0])
            child = run.call_args.kwargs["env"]
            self.assertEqual("/required/path", child["PaTh"])
            self.assertEqual("C:\\Windows", child["SystemRoot"])
            self.assertFalse({
                key.casefold()
                for key in child
            } & {
                "java_tool_options",
                "_java_options",
                "jdk_java_options",
            })

    def test_probe_rejects_an_unusable_cds_archive_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            java = Path(temporary) / "bin/java"
            java.parent.mkdir(parents=True)
            java.write_bytes(b"java")
            output = _java_script().decode("utf-8").split("cat >&2 <<EOF\n", 1)[1]
            output = output.split("\nEOF", 1)[0]
            warning = (
                "[0.014s][warning][cds] Required classpath entry does not "
                "exist: C:\\Users\\dev\\Workbench State ??\\lib\\modules\n"
            )
            for channel in ("stdout", "stderr"):
                with self.subTest(channel=channel):
                    completed = __import__("subprocess").CompletedProcess(
                        [],
                        0,
                        warning if channel == "stdout" else "",
                        output + (warning if channel == "stderr" else ""),
                    )
                    with patch.object(
                        runtime_java_module.subprocess,
                        "run",
                        return_value=completed,
                    ):
                        with self.assertRaisesRegex(
                            JavaRuntimeError,
                            "unusable class-data-sharing archive",
                        ):
                            runtime_java_module.probe_java(java)

    def test_windows_unicode_portability_removes_only_default_cds_archives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "extracted"
            java_home = extracted / "jdk-25.0.4+7"
            server = java_home / "bin/server"
            server.mkdir(parents=True)
            archives = {
                "classes.jsa": b"default",
                "classes_coh.jsa": b"coh",
                "classes_nocoops.jsa": b"nocoops",
                "classes_nocoops_coh.jsa": b"nocoops-coh",
            }
            for name, content in archives.items():
                (server / name).write_bytes(content)
            retained = server / "jvm.dll"
            retained.write_bytes(b"runtime")

            portability = runtime_java_module._prepare_managed_java_portability(
                extracted_root=extracted,
                java_home=java_home,
                host={"os": "windows"},
                execution={"kind": "windows-short-path"},
            )

            cds = portability["cds"]
            self.assertEqual(
                "disabled-for-windows-unicode-custody",
                cds["state"],
            )
            self.assertEqual(
                runtime_java_module.WINDOWS_UNICODE_CDS_TRANSFORM_ID,
                cds["transform_id"],
            )
            self.assertEqual(
                sorted(archives),
                sorted(Path(row["path"]).name for row in cds["removed_archives"]),
            )
            self.assertTrue(all(
                len(row["sha256"]) == 64 and row["size"] > 0
                for row in cds["removed_archives"]
            ))
            self.assertFalse(any(server.glob("classes*.jsa")))
            self.assertEqual(b"runtime", retained.read_bytes())
            self.assertEqual(
                portability,
                runtime_java_module._validate_managed_java_portability(
                    portability,
                    extracted_root=extracted,
                    java_home=java_home,
                    execution_kind="windows-short-path",
                ),
            )

            nested = deepcopy(portability)
            nested["cds"]["removed_archives"][0]["path"] = (
                "jdk-25.0.4+7/bin/server/nested/classes.jsa"
            )
            nested["cds"]["removed_archives"].sort(key=lambda row: row["path"])
            with self.assertRaisesRegex(
                JavaRuntimeError,
                "removed CDS archive identity is invalid",
            ):
                runtime_java_module._validate_managed_java_portability(
                    nested,
                    extracted_root=extracted,
                    java_home=java_home,
                    execution_kind="windows-short-path",
                )

            original_id = runtime_java_module._runtime_identity_v2(
                deepcopy(POLICY),
                {"os": "windows"},
                {"release_name": "jdk-25.0.4+7"},
                {"tree_sha256": "sha256:" + "1" * 64},
                portability,
            )
            changed = deepcopy(portability)
            changed["cds"]["transform_id"] = "changed-transform-v1"
            self.assertNotEqual(
                original_id,
                runtime_java_module._runtime_identity_v2(
                    deepcopy(POLICY),
                    {"os": "windows"},
                    {"release_name": "jdk-25.0.4+7"},
                    {"tree_sha256": "sha256:" + "1" * 64},
                    changed,
                ),
            )

    def test_canonical_java_portability_retains_vendor_cds_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "extracted"
            java_home = extracted / "jdk"
            archive = java_home / "bin/server/classes.jsa"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"default")

            portability = runtime_java_module._prepare_managed_java_portability(
                extracted_root=extracted,
                java_home=java_home,
                host={"os": "windows"},
                execution={"kind": "canonical-path"},
            )

            self.assertEqual(
                {
                    "cds": {
                        "state": "vendor-default",
                        "transform_id": None,
                        "removed_archives": [],
                    }
                },
                portability,
            )
            self.assertEqual(b"default", archive.read_bytes())

    def test_unicode_windows_plan_without_short_name_fails_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state-验证"
            state.mkdir()
            marker = state / "keep.txt"
            marker.write_text("unchanged\n", encoding="utf-8")
            before = sorted(path.name for path in state.iterdir())
            windows = {
                "os": "windows",
                "architecture": "x64",
                "system": "Windows",
                "machine": "AMD64",
            }

            with patch.object(
                runtime_java_module,
                "_windows_short_path",
                return_value=None,
            ):
                with self.assertRaisesRegex(
                    JavaRuntimeError,
                    "8.3 ASCII DOS short-name alias is unavailable",
                ):
                    runtime_java_module.plan_managed_java_execution(
                        deepcopy(POLICY),
                        windows,
                        state_root=state,
                    )

            self.assertEqual(before, sorted(path.name for path in state.iterdir()))
            self.assertEqual("unchanged\n", marker.read_text(encoding="utf-8"))

    def test_nonexistent_unicode_windows_state_fails_before_fetch_or_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state-验证"
            archive = root / "temurin.tar.gz"
            archive.write_bytes(b"not reached")
            windows = {
                "os": "windows",
                "architecture": "x64",
                "system": "Windows",
                "machine": "AMD64",
            }

            with (
                patch.object(
                    runtime_java_module,
                    "_windows_short_path",
                    return_value=None,
                ) as short_path,
                patch.object(
                    runtime_java_module,
                    "fetch_verified_artifact",
                ) as fetch,
                self.assertRaisesRegex(
                    JavaRuntimeError,
                    "read-only for the nonexistent Unicode portion",
                ),
            ):
                materialize_temurin_runtime(
                    deepcopy(POLICY),
                    windows,
                    _asset(archive),
                    state_root=state,
                )

            short_path.assert_not_called()
            fetch.assert_not_called()
            self.assertFalse(state.exists())
            self.assertEqual([archive], list(root.iterdir()))

    def test_missing_ascii_suffix_uses_verified_unicode_ancestor_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            anchor = Path(temporary) / "state-验证"
            anchor.mkdir()
            state = anchor / "new-state"
            windows = {
                "os": "windows",
                "architecture": "x64",
                "system": "Windows",
                "machine": "AMD64",
            }

            with patch.object(
                runtime_java_module,
                "_windows_short_path",
                return_value=Path("C:/STATE~1"),
            ) as short_path:
                plan = runtime_java_module.plan_managed_java_execution(
                    deepcopy(POLICY),
                    windows,
                    state_root=state,
                )

            self.assertEqual(
                {
                    "kind": "windows-short-path",
                    "portability_transform_id": (
                        runtime_java_module.WINDOWS_UNICODE_CDS_TRANSFORM_ID
                    ),
                },
                plan,
            )
            short_path.assert_called_once_with(anchor)
            self.assertFalse(state.exists())

    def test_discovers_an_exact_external_temurin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            java_home = Path(temporary) / "jdk"
            java = java_home / "bin" / "java"
            java.parent.mkdir(parents=True)
            java.write_bytes(_java_script())
            java.chmod(0o755)

            discovery = discover_java_runtime(
                deepcopy(POLICY),
                deepcopy(HOST),
                candidates=[("test", java)],
            )

            self.assertEqual(discovery["state"], "found")
            self.assertEqual(
                discovery["selected"]["origin"],
                "test",
            )
            self.assertEqual(
                discovery["selected"]["probe"]["vendor"],
                "Eclipse Adoptium",
            )

    def test_explicit_candidate_precedes_an_existing_managed_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "temurin.tar.gz"
            _archive(archive)
            state_root = root / "state"
            policy = load_java_runtime_policy(REPOSITORY_ROOT)
            materialize_temurin_runtime(
                policy,
                deepcopy(HOST),
                _asset(archive),
                state_root=state_root,
            )

            external_home = root / "external-jdk"
            external_java = external_home / "bin/java"
            external_java.parent.mkdir(parents=True)
            external_java.write_bytes(_java_script())
            external_java.chmod(0o755)

            selected = ensure_java_runtime(
                REPOSITORY_ROOT,
                state_root=state_root,
                host=deepcopy(HOST),
                candidates=(("explicit", external_java),),
            )

            self.assertEqual("discovered", selected["outcome"])
            self.assertEqual("external", selected["source"])
            self.assertEqual(
                "explicit",
                selected["runtime"]["origin"],
            )

    def test_saved_managed_binding_reopens_receipt_and_rejects_tree_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "temurin.tar.gz"
            _archive(archive)
            state_root = root / "state"
            policy = load_java_runtime_policy(REPOSITORY_ROOT)
            created = materialize_temurin_runtime(
                policy,
                deepcopy(HOST),
                _asset(archive),
                state_root=state_root,
            )
            managed_home = _uri_path(
                created["receipt"]["target"]["java_home_uri"]
            )
            custody_home = _uri_path(
                created["receipt"]["target"]["custody_java_home_uri"]
            )
            (custody_home / "release").write_text(
                "tampered\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(JavaRuntimeError, "drifted"):
                ensure_java_runtime(
                    REPOSITORY_ROOT,
                    environment={"WORKBENCH_JAVA_HOME": str(managed_home)},
                    host=deepcopy(HOST),
                    state_root=state_root,
                )

    def test_hash_mismatch_never_publishes_a_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "temurin.tar.gz"
            _archive(archive)
            state_root = root / "state"

            with self.assertRaisesRegex(
                JavaRuntimeError,
                "SHA-256 mismatch",
            ):
                materialize_temurin_runtime(
                    deepcopy(POLICY),
                    deepcopy(HOST),
                    _asset(archive, digest="0" * 64),
                    state_root=state_root,
                )

            self.assertFalse((state_root / "jdks").exists())

    def test_unsafe_archive_never_escapes_or_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "temurin.tar.gz"
            _archive(archive, unsafe_member="../escaped")
            state_root = root / "state"

            with self.assertRaisesRegex(
                JavaRuntimeError,
                "unsafe Java archive path",
            ):
                materialize_temurin_runtime(
                    deepcopy(POLICY),
                    deepcopy(HOST),
                    _asset(archive),
                    state_root=state_root,
                )

            self.assertFalse((root / "escaped").exists())
            self.assertEqual(
                list(state_root.rglob("java-runtime-v2.json")),
                [],
            )

    def test_wrong_runtime_and_tree_drift_are_hard_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrong_archive = root / "wrong.tar.gz"
            _archive(
                wrong_archive,
                runtime_version="25.0.3+9-LTS",
            )
            with self.assertRaisesRegex(
                JavaRuntimeError,
                "violates its policy",
            ):
                materialize_temurin_runtime(
                    deepcopy(POLICY),
                    deepcopy(HOST),
                    _asset(wrong_archive),
                    state_root=root / "wrong-state",
                )

            archive = root / "temurin.tar.gz"
            _archive(archive)
            state_root = root / "state"
            created = materialize_temurin_runtime(
                deepcopy(POLICY),
                deepcopy(HOST),
                _asset(archive),
                state_root=state_root,
            )
            java_path = _uri_path(
                created["receipt"]["target"]["java_uri"]
            )
            (java_path.parent.parent / "release").write_text(
                "tampered\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                JavaRuntimeError,
                "drifted",
            ):
                materialize_temurin_runtime(
                    deepcopy(POLICY),
                    deepcopy(HOST),
                    _asset(archive),
                    state_root=state_root,
                )


if __name__ == "__main__":
    unittest.main()
