from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import tomllib
from types import MappingProxyType
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SHELL_SOURCE = ROOT / "core/src"
if str(SHELL_SOURCE) not in sys.path:
    sys.path.insert(0, str(SHELL_SOURCE))

from workbench_core.configuration import (
    CONFIGURATION_PATH,
    PROFILE_SCHEMA_VERSION,
    SCHEMA,
    SUPPORTED_BINDINGS,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)


SCHEMA_PATH = (
    ROOT
    / "modules/workbench-shell/schemas/workbench-configuration-v1.schema.json"
)


def _manifest(
    *,
    schema: str = SCHEMA,
    pack_document: str = "profiles/packs/example/profile.yaml",
    pack_variant: str = "cleanroom-test",
    platform_document: str = "profiles/platforms/cleanroom/test.yaml",
    bindings: str = (
        'java_candidate_home = { env = "WORKBENCH_JAVA_HOME" }\n'
    ),
    root_extra: str = "",
    selection_extra: str = "",
) -> str:
    return (
        f'schema = "{schema}"\n'
        f"{root_extra}"
        "\n[selection]\n"
        f'pack_document = "{pack_document}"\n'
        f'pack_variant = "{pack_variant}"\n'
        f'platform_document = "{platform_document}"\n'
        f"{selection_extra}"
        "\n[bindings]\n"
        f"{bindings}"
    )


def _write_suite(
    root: Path,
    *,
    manifest: str | None = None,
    pack: str | None = None,
    platform: str | None = None,
) -> None:
    pack_path = root / "profiles/packs/example/profile.yaml"
    platform_path = root / "profiles/platforms/cleanroom/test.yaml"
    pack_path.parent.mkdir(parents=True)
    platform_path.parent.mkdir(parents=True)
    pack_path.write_text(
        pack
        or (
            "schema_version: 1\n"
            "profile_family_id: workbench-pack:example\n"
            "profiles:\n"
            "  cleanroom-test:\n"
            "    platform_profile_id: workbench-platform:cleanroom:test\n"
        ),
        encoding="utf-8",
    )
    platform_path.write_text(
        platform
        or (
            "schema_version: 1\n"
            "profile_id: workbench-platform:cleanroom:test\n"
            "kind: cleanroom\n"
        ),
        encoding="utf-8",
    )
    (root / CONFIGURATION_PATH).write_text(
        manifest or _manifest(),
        encoding="utf-8",
    )


class WorkbenchConfigurationTests(unittest.TestCase):
    def test_canonical_manifest_selects_exact_immutable_authority(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        parsed = tomllib.loads((ROOT / CONFIGURATION_PATH).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(parsed)

        configuration = load_workbench_configuration(ROOT)

        self.assertEqual(SCHEMA, configuration.schema)
        self.assertEqual(
            "workbench-pack:supersymmetry",
            configuration.pack_profile_id,
        )
        self.assertEqual(
            "workbench-platform:cleanroom:provisional",
            configuration.platform_profile_id,
        )
        self.assertEqual("cleanroom-provisional", configuration.pack_variant)
        self.assertRegex(configuration.selection_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            sha256(configuration.manifest.source_bytes).hexdigest(),
            configuration.manifest.sha256,
        )
        self.assertEqual("workbench.toml", configuration.manifest.relative_path)
        self.assertEqual(
            PROFILE_SCHEMA_VERSION,
            configuration.pack_document.schema_version,
        )
        self.assertIsInstance(configuration.pack_document.values, MappingProxyType)
        with self.assertRaises(TypeError):
            configuration.pack_document.values["status"] = "changed"  # type: ignore[index]

    def test_binding_resolution_snapshots_literal_environment_and_missing_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(
                suite,
                manifest=_manifest(
                    bindings='java_candidate_home = { env = "SELECTED_JAVA" }\n'
                ),
            )
            configuration = load_workbench_configuration(suite)
            environment = {"SELECTED_JAVA": "/opt/workbench/jdk"}

            resolved = configuration.resolve_bindings(environment)
            environment["SELECTED_JAVA"] = "/changed/after-resolution"

            self.assertEqual("/opt/workbench/jdk", resolved.get("java_candidate_home"))
            self.assertEqual("environment", resolved.binding("java_candidate_home").source)
            self.assertEqual(
                "SELECTED_JAVA",
                resolved.binding("java_candidate_home").environment_variable,
            )
            missing = configuration.resolve_bindings({})
            self.assertIsNone(missing.get("java_candidate_home"))
            self.assertFalse(missing.binding("java_candidate_home").is_bound)
            self.assertEqual("environment", missing.binding("java_candidate_home").source)

            (suite / CONFIGURATION_PATH).write_text(
                _manifest(bindings='java_candidate_home = "/opt/workbench/jdk"\n'),
                encoding="utf-8",
            )
            literal = load_workbench_configuration(suite).resolve_bindings({})
            self.assertEqual("/opt/workbench/jdk", literal.get("java_candidate_home"))
            self.assertEqual("literal", literal.binding("java_candidate_home").source)

    def test_undeclared_bindings_are_explicitly_unbound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite, manifest=_manifest(bindings=""))

            configuration = load_workbench_configuration(suite)
            resolved = configuration.resolve_bindings(
                {
                    "WORKBENCH_JAVA_HOME": "/ambient/java",
                }
            )

            self.assertEqual(SUPPORTED_BINDINGS, tuple(row.name for row in resolved.entries))
            for row in resolved.entries:
                self.assertEqual("undeclared", row.source)
                self.assertIsNone(row.value)

    def test_operation_resolution_ignores_unconsumed_ambient_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            configuration = load_workbench_configuration(suite)

            resolved = configuration.resolve_bindings(
                {
                    "WORKBENCH_JAVA_HOME": "invalid/relative/java",
                },
                names=(),
            )

            self.assertEqual((), resolved.entries)

    def test_operation_digest_binds_only_ordered_explicit_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            configuration = load_workbench_configuration(suite)
            first = configuration.resolve_bindings(
                {
                    "WORKBENCH_JAVA_HOME": "/opt/java-a",
                    "UNDECLARED_HOST_VALUE": "/opt/host-a",
                }
            )
            changed_unconsumed = configuration.resolve_bindings(
                {
                    "WORKBENCH_JAVA_HOME": "/opt/java-a",
                    "UNDECLARED_HOST_VALUE": "/opt/host-b",
                }
            )
            changed_consumed = configuration.resolve_bindings(
                {
                    "WORKBENCH_JAVA_HOME": "/opt/java-b",
                }
            )

            java_digest = first.operation_digest(
                "runtime/java-v1",
                ["java_candidate_home"],
            )
            self.assertEqual(
                java_digest,
                changed_unconsumed.operation_digest(
                    "runtime/java-v1",
                    ["java_candidate_home"],
                ),
            )
            self.assertNotEqual(
                java_digest,
                changed_consumed.operation_digest(
                    "runtime/java-v1",
                    ["java_candidate_home"],
                ),
            )
            with self.assertRaisesRegex(WorkbenchConfigurationError, "duplicate"):
                first.operation_digest(
                    "runtime/java-v1",
                    ["java_candidate_home", "java_candidate_home"],
                )

            undeclared_suite = suite / "undeclared"
            undeclared_suite.mkdir()
            _write_suite(undeclared_suite, manifest=_manifest(bindings=""))
            undeclared = load_workbench_configuration(
                undeclared_suite
            ).resolve_bindings({})
            self.assertRegex(
                undeclared.operation_digest(
                    "runtime/java-v1",
                    ["java_candidate_home"],
                ),
                r"^sha256:[0-9a-f]{64}$",
            )
            self.assertNotEqual(
                java_digest,
                undeclared.operation_digest(
                    "runtime/java-v1",
                    ["java_candidate_home"],
                ),
            )
            with self.assertRaisesRegex(WorkbenchConfigurationError, "unsupported"):
                first.operation_digest("runtime/java-v1", ["state_root"])

    def test_resolved_bindings_cannot_cross_configuration_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            first = load_workbench_configuration(suite)
            resolved = first.resolve_bindings(
                {"WORKBENCH_JAVA_HOME": "/opt/java"},
                names=("java_candidate_home",),
            )
            first.require_binding_snapshot(
                resolved,
                names=("java_candidate_home",),
            )

            manifest_path = suite / CONFIGURATION_PATH
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8") + "\n# changed source\n",
                encoding="utf-8",
            )
            second = load_workbench_configuration(suite)
            with self.assertRaisesRegex(
                WorkbenchConfigurationError,
                "does not belong",
            ):
                second.require_binding_snapshot(
                    resolved,
                    names=("java_candidate_home",),
                )

    def test_imported_and_literal_values_must_be_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(
                suite,
                manifest=_manifest(
                    bindings='java_candidate_home = { env = "SELECTED_JAVA" }\n'
                ),
            )
            configuration = load_workbench_configuration(suite)
            with self.assertRaisesRegex(WorkbenchConfigurationError, "absolute path"):
                configuration.resolve_bindings({"SELECTED_JAVA": "relative/jdk"})

            (suite / CONFIGURATION_PATH).write_text(
                _manifest(bindings='java_candidate_home = "relative/java"\n'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkbenchConfigurationError, "absolute path"):
                load_workbench_configuration(suite)

            (suite / CONFIGURATION_PATH).write_text(
                _manifest(bindings='java_candidate_home = "C:\\\\Java\\\\jdk"\n'),
                encoding="utf-8",
            )
            windows = load_workbench_configuration(suite).resolve_bindings({})
            self.assertEqual(
                "C:\\Java\\jdk",
                windows.get("java_candidate_home"),
            )

    def test_latest_only_and_closed_tables_fail_on_drift(self) -> None:
        cases = {
            "older schema": _manifest(schema="workbench/config/v0"),
            "newer schema": _manifest(schema="workbench/config/v2"),
            "root field": _manifest(root_extra="schema_version = 1\n"),
            "selection field": _manifest(selection_extra="default = true\n"),
            "binding field": _manifest(bindings='secret = { env = "TOKEN" }\n'),
        }
        for label, manifest in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                suite = Path(temporary)
                _write_suite(suite, manifest=manifest)
                with self.assertRaises(WorkbenchConfigurationError):
                    load_workbench_configuration(suite)

    def test_binding_import_is_one_closed_uppercase_env_table(self) -> None:
        bindings = (
            'java_candidate_home = { env = "selected_java" }\n',
            'java_candidate_home = { env = "SELECTED_JAVA", required = true }\n',
            "java_candidate_home = 25\n",
        )
        for value in bindings:
            with self.subTest(binding=value), tempfile.TemporaryDirectory() as temporary:
                suite = Path(temporary)
                _write_suite(suite, manifest=_manifest(bindings=value))
                with self.assertRaises(WorkbenchConfigurationError):
                    load_workbench_configuration(suite)

    def test_profile_paths_are_confined_canonical_suite_paths(self) -> None:
        paths = (
            "/profiles/packs/example/profile.yaml",
            "profiles/../outside.yaml",
            "profiles/packs/example/../../outside.yaml",
            "profiles\\packs\\example\\profile.yaml",
            "profiles/packs/example/profile.json",
            "other/profiles/example.yaml",
        )
        for value in paths:
            with self.subTest(path=value), tempfile.TemporaryDirectory() as temporary:
                suite = Path(temporary)
                _write_suite(suite, manifest=_manifest(pack_document=value))
                with self.assertRaises(WorkbenchConfigurationError):
                    load_workbench_configuration(suite)

    def test_profile_schema_version_and_embedded_ids_are_current(self) -> None:
        packs = (
            (
                "missing schema",
                "profile_family_id: workbench-pack:example\nprofiles: {}\n",
            ),
            (
                "boolean schema",
                "schema_version: true\n"
                "profile_family_id: workbench-pack:example\nprofiles: {}\n",
            ),
            (
                "newer schema",
                "schema_version: 2\n"
                "profile_family_id: workbench-pack:example\nprofiles: {}\n",
            ),
            (
                "foreign ID",
                "schema_version: 1\n"
                "profile_family_id: example\nprofiles: {}\n",
            ),
        )
        for label, pack in packs:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                suite = Path(temporary)
                _write_suite(suite, pack=pack)
                with self.assertRaises(WorkbenchConfigurationError):
                    load_workbench_configuration(suite)

    def test_variant_must_exist_and_bind_selected_platform(self) -> None:
        cases = (
            (
                "missing variant",
                "schema_version: 1\n"
                "profile_family_id: workbench-pack:example\n"
                "profiles: {}\n",
            ),
            (
                "missing binding",
                "schema_version: 1\n"
                "profile_family_id: workbench-pack:example\n"
                "profiles:\n  cleanroom-test: {}\n",
            ),
            (
                "mismatched binding",
                "schema_version: 1\n"
                "profile_family_id: workbench-pack:example\n"
                "profiles:\n"
                "  cleanroom-test:\n"
                "    platform_profile_id: workbench-platform:cleanroom:other\n",
            ),
        )
        for label, pack in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                suite = Path(temporary)
                _write_suite(suite, pack=pack)
                with self.assertRaises(WorkbenchConfigurationError):
                    load_workbench_configuration(suite)

    def test_profile_bytes_and_selection_digest_detect_semantic_neutral_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            before = load_workbench_configuration(suite)

            platform = suite / "profiles/platforms/cleanroom/test.yaml"
            platform.write_bytes(platform.read_bytes() + b"# retained observation\n")
            after = load_workbench_configuration(suite)

            self.assertNotEqual(
                before.platform_document.source.sha256,
                after.platform_document.source.sha256,
            )
            self.assertNotEqual(before.selection_digest, after.selection_digest)
            self.assertEqual(before.platform_profile_id, after.platform_profile_id)

    def test_duplicate_profile_keys_and_symlinked_sources_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(
                suite,
                platform=(
                    "schema_version: 1\n"
                    "profile_id: workbench-platform:cleanroom:test\n"
                    "profile_id: workbench-platform:cleanroom:other\n"
                ),
            )
            with self.assertRaisesRegex(WorkbenchConfigurationError, "strict UTF-8 YAML"):
                load_workbench_configuration(suite)

        with tempfile.TemporaryDirectory() as temporary:
            suite = Path(temporary)
            _write_suite(suite)
            real = suite / "real-pack.yaml"
            pack = suite / "profiles/packs/example/profile.yaml"
            pack.rename(real)
            try:
                pack.symlink_to(real)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaises(WorkbenchConfigurationError):
                load_workbench_configuration(suite)

    def test_external_manifest_is_explicit_but_profiles_remain_suite_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = root / "suite"
            suite.mkdir()
            _write_suite(suite)
            external = root / "developer.toml"
            external.write_text(_manifest(), encoding="utf-8")

            configuration = load_workbench_configuration(suite, external)

            self.assertIsNone(configuration.manifest.relative_path)
            self.assertEqual(
                "profiles/packs/example/profile.yaml",
                configuration.pack_document.source.relative_path,
            )


if __name__ == "__main__":
    unittest.main()
