"""Regression tests for cheap repository preflight checks."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

VALIDATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VALIDATION_ROOT))

import validate as validator  # noqa: E402
from validate import (  # noqa: E402
    ValidationFailure,
    _validate_migration_destinations,
    _validate_migration_privacy,
    validate_checked_in_schema_instances,
    validate_repository_preflight,
    validate_serialized_files,
)


class MigrationDestinationTests(unittest.TestCase):
    @staticmethod
    def _group(destination: str = "current") -> dict[str, object]:
        return {
            "id": "current-import",
            "source_roots": ["upstream/source"],
            "destination_roots": [destination],
            "disposition": "adapted",
        }

    def test_current_regular_destination_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "current").write_text("current\n", encoding="utf-8")
            _validate_migration_destinations([self._group()], root=root)

    def test_missing_or_escaping_destination_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValidationFailure, "destination is missing"):
                _validate_migration_destinations([self._group()], root=root)
            with self.assertRaisesRegex(ValidationFailure, "escapes the repository"):
                _validate_migration_destinations(
                    [self._group("../private")], root=root
                )

    def test_duplicate_import_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "current").mkdir()
            with self.assertRaisesRegex(ValidationFailure, "duplicated"):
                _validate_migration_destinations(
                    [self._group(), self._group()], root=root
                )


class MigrationPrivacyTests(unittest.TestCase):
    @staticmethod
    def _manifest() -> dict[str, object]:
        return {
            "source_repository": {
                "origin": "private predecessor snapshot",
                "publication": "not retained",
                "license": "LGPL-3.0-only",
            },
            "imports": [
                {
                    "id": "workbench-product-definition",
                    "source": "private predecessor snapshot (not published)",
                    "source_roots": ["unpublished product-definition sources"],
                    "destination_roots": ["docs/product"],
                    "disposition": "adapted",
                },
                {
                    "id": "atlas-v1",
                    "source_roots": ["unpublished predecessor Atlas sources"],
                    "destination_roots": ["modules/atlas"],
                    "disposition": "adapted",
                },
            ],
        }

    def test_private_predecessor_is_described_without_coordinates(self) -> None:
        _validate_migration_privacy(self._manifest())

    def test_private_repository_object_ids_are_rejected(self) -> None:
        manifest = self._manifest()
        source = manifest["source_repository"]
        assert isinstance(source, dict)
        source["developed_product_source"] = {"commit": "a" * 40}
        with self.assertRaisesRegex(ValidationFailure, "omit private"):
            _validate_migration_privacy(manifest)

    def test_private_source_paths_are_rejected(self) -> None:
        manifest = self._manifest()
        imports = manifest["imports"]
        assert isinstance(imports, list)
        imports[0]["source_roots"] = ["docs/deconstruction/private"]
        with self.assertRaisesRegex(ValidationFailure, "private predecessor path"):
            _validate_migration_privacy(manifest)

    def test_private_import_commits_are_rejected(self) -> None:
        manifest = self._manifest()
        imports = manifest["imports"]
        assert isinstance(imports, list)
        imports[1]["source_commit"] = "b" * 40
        with self.assertRaisesRegex(ValidationFailure, "exposes source coordinates"):
            _validate_migration_privacy(manifest)


class RepositoryPreflightTests(unittest.TestCase):
    def test_product_preflight_works_without_installed_workbench_metadata(self) -> None:
        script = '''
import sys
from importlib import metadata
from unittest.mock import patch
sys.path.insert(0, "validation")
import validate
from workbench_core.development import SourceFinder
sys.meta_path[:] = [finder for finder in sys.meta_path if not isinstance(finder, SourceFinder)]
with patch.object(metadata.MetadataPathFinder, "find_distributions", return_value=iter(())):
    validate.validate_product_capabilities()
'''
        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=validator.ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def _run(self, *, authority: bool, policy: bool):
        names = (
            "validate_serialized_files",
            "validate_checked_in_schema_instances",
            "load_build_paths",
            "load_public_repository",
            "load_release_units",
            "validate_file_policy",
            "validate_product_capabilities",
            "validate_profiles",
            "validate_python_sources",
            "validate_links",
            "validate_migration",
            "run",
        )
        with patch.multiple(
            validator,
            **{name: DEFAULT for name in names},
        ) as mocked:
            validate_repository_preflight(
                [],
                authority=authority,
                policy=policy,
            )
        return mocked

    def test_quick_preflight_omits_global_authority_proofs(self) -> None:
        mocked = self._run(authority=False, policy=False)
        mocked["run"].assert_not_called()
        mocked["validate_links"].assert_not_called()
        mocked["validate_migration"].assert_not_called()
        mocked["validate_python_sources"].assert_called_once_with([])

    def test_canonical_preflight_does_not_run_private_compilers(self) -> None:
        mocked = self._run(authority=True, policy=False)
        mocked["run"].assert_not_called()
        mocked["validate_product_capabilities"].assert_called_once_with()

    def test_policy_checks_are_independent_of_authority_proofs(self) -> None:
        mocked = self._run(authority=False, policy=True)
        mocked["validate_links"].assert_called_once_with([])
        mocked["validate_migration"].assert_called_once_with()
        mocked["run"].assert_not_called()


class ValidationCliTests(unittest.TestCase):
    def test_failed_preflight_writes_terminal_result_and_unrun_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "result.json"
            with (
                patch.object(sys, "argv", ["validate.py", "--full", "--result", str(result)]),
                patch.object(validator, "repository_files", return_value=[]),
                patch.object(validator, "fingerprint_paths", return_value="sha256:source"),
                patch.object(validator, "validate_repository_preflight", side_effect=ValidationFailure("broken schema")),
                patch.object(validator, "run_python_suites") as suites,
            ):
                with self.assertRaisesRegex(ValidationFailure, "broken schema"):
                    validator.main()
            document = json.loads(result.read_text())
            self.assertEqual("failed", document["state"])
            self.assertEqual({"preflight": "failed", "python": "not-run", "ide": "not-run"},
                             {name: phase["state"] for name, phase in document["phases"].items()})
            suites.assert_not_called()
            before = result.read_bytes()
            with patch.object(sys, "argv", ["validate.py", "--result", str(result)]):
                with self.assertRaisesRegex(ValidationFailure, "fresh invocation"):
                    validator.main()
            self.assertEqual(before, result.read_bytes())

    def test_changed_path_collection_includes_deleted_renamed_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*arguments):
                return subprocess.check_output(["git", *arguments], cwd=root, stderr=subprocess.DEVNULL)
            git("init", "--quiet")
            (root / "before.py").write_text("original\n")
            (root / "deleted.py").write_text("removed\n")
            git("add", ".")
            git("-c", "user.name=Validation", "-c", "user.email=validation@example.invalid", "commit", "--quiet", "-m", "reference")
            git("mv", "before.py", "after.py")
            (root / "deleted.py").unlink()
            (root / "untracked.py").write_text("new\n")
            self.assertEqual(["after.py", "before.py", "deleted.py", "untracked.py"], validator.changed_paths_since(root, "HEAD"))

    def _run_main(self, *arguments: str):
        run_paths = SimpleNamespace(root=validator.ROOT / ".workbench/test-run")
        with (
            patch.object(sys, "argv", ["validate.py", *arguments]),
            patch.object(validator, "repository_files", return_value=[]),
            patch.object(
                validator,
                "fingerprint_paths",
                return_value="sha256:" + "a" * 64,
            ),
            patch.object(validator, "validate_repository_preflight") as preflight,
            patch.object(
                validator,
                "run_python_suites",
                return_value=run_paths,
            ) as run_suites,
            patch.object(validator, "run") as run_command,
        ):
            result = validator.main()
        self.assertEqual(0, result)
        return preflight, run_suites, run_command

    def test_quick_and_focused_cli_paths_use_cheap_preflight(self) -> None:
        cases = (
            ((), ()),
            (("--tier", "quick"), ()),
            (("--suite", "validation-authority"), ("validation-authority",)),
        )
        for arguments, selected in cases:
            with self.subTest(arguments=arguments):
                preflight, run_suites, _ = self._run_main(*arguments)
                preflight.assert_called_once_with(
                    [],
                    authority=False,
                    policy=False,
                )
                self.assertEqual("quick", run_suites.call_args.kwargs["tier"])
                self.assertEqual(selected, run_suites.call_args.kwargs["selected"])
                self.assertEqual(2, run_suites.call_args.kwargs["jobs"])
                self.assertEqual(
                    "sha256:" + "a" * 64,
                    run_suites.call_args.kwargs["source_fingerprint"],
                )

    def test_canonical_and_full_cli_paths_use_authority_preflight(self) -> None:
        cases = (
            (("--tier", "canonical"), (), False),
            (
                ("--tier", "canonical", "--suite", "validation-authority"),
                ("validation-authority",),
                False,
            ),
            (("--full",), (), True),
        )
        for arguments, selected, policy in cases:
            with self.subTest(arguments=arguments):
                preflight, run_suites, _ = self._run_main(*arguments)
                preflight.assert_called_once_with(
                    [],
                    authority=True,
                    policy=policy,
                )
                self.assertEqual("canonical", run_suites.call_args.kwargs["tier"])
                self.assertEqual(selected, run_suites.call_args.kwargs["selected"])

    def test_source_drift_during_preflight_fails_before_suites(self) -> None:
        with (
            patch.object(sys, "argv", ["validate.py"]),
            patch.object(validator, "repository_files", return_value=[]),
            patch.object(
                validator,
                "fingerprint_paths",
                side_effect=("sha256:before", "sha256:after"),
            ),
            patch.object(validator, "validate_repository_preflight"),
            patch.object(validator, "run_python_suites") as run_suites,
        ):
            with self.assertRaisesRegex(
                ValidationFailure,
                "fingerprint drifted during repository preflight",
            ):
                validator.main()
        run_suites.assert_not_called()


class CheckedInSchemaInstanceTests(unittest.TestCase):
    def _roots(self, temporary: str) -> tuple[Path, Path]:
        root = Path(temporary)
        data = root / "data"
        schemas = root / "schemas"
        data.mkdir()
        schemas.mkdir()
        return data, schemas

    def test_matching_checked_in_instance_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data, schemas = self._roots(temporary)
            (data / "catalog.json").write_text(
                json.dumps({"profiles": [{"id": "alpha"}, {"id": "beta"}]}),
                encoding="utf-8",
            )
            (schemas / "catalog.schema.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "required": ["profiles"],
                        "properties": {
                            "profiles": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 2,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            validate_checked_in_schema_instances(data, schemas)

    def test_schema_cardinality_drift_fails_in_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data, schemas = self._roots(temporary)
            (data / "catalog.json").write_text(
                json.dumps({"profiles": [{"id": "alpha"}, {"id": "beta"}]}),
                encoding="utf-8",
            )
            (schemas / "catalog.schema.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "properties": {
                            "profiles": {"type": "array", "maxItems": 1}
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValidationFailure, "checked-in schema instance failed"
            ):
                validate_checked_in_schema_instances(data, schemas)

    def test_missing_schema_pairs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data, schemas = self._roots(temporary)
            (data / "orphan.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ValidationFailure, "no checked-in shell data/schema pairs"
            ):
                validate_checked_in_schema_instances(data, schemas)


class SerializedFileTests(unittest.TestCase):
    def test_duplicate_json_and_yaml_keys_fail_in_preflight(self) -> None:
        scratch = VALIDATION_ROOT.parent / ".workbench"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            fixtures = {
                "duplicate.json": '{"release": 1, "release": 2}\n',
                "duplicate.yml": "job:\n  timeout: 1\n  timeout: 2\n",
            }
            for name, payload in fixtures.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaisesRegex(
                        ValidationFailure, "duplicate.*(?:key|JSON key)"
                    ):
                        validate_serialized_files([path])

    def test_duplicate_toml_keys_fail_in_preflight(self) -> None:
        scratch = VALIDATION_ROOT.parent / ".workbench"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            path = Path(temporary) / "duplicate.toml"
            path.write_text("release = 1\nrelease = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ValidationFailure, "cannot parse.*overwrite"
            ):
                validate_serialized_files([path])

    def test_unique_json_yaml_and_toml_still_pass(self) -> None:
        scratch = VALIDATION_ROOT.parent / ".workbench"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            json_path = root / "release.json"
            yaml_path = root / "workflow.yml"
            toml_path = root / "workbench.toml"
            json_path.write_text('{"release": 1}\n', encoding="utf-8")
            yaml_path.write_text("job:\n  timeout: 1\n", encoding="utf-8")
            toml_path.write_text(
                'schema = "workbench/config/v1"\n',
                encoding="utf-8",
            )
            validate_serialized_files([json_path, yaml_path, toml_path])


class SelectedProfileValidationTests(unittest.TestCase):
    def test_profile_validation_uses_one_production_configuration_snapshot(
        self,
    ) -> None:
        configuration = validator.load_workbench_configuration(validator.ROOT)
        production_java_policy = validator.load_java_runtime_policy
        with (
            patch.object(
                validator,
                "load_workbench_configuration",
                return_value=configuration,
            ) as load_configuration,
            patch.object(
                validator,
                "load_java_runtime_policy",
                wraps=production_java_policy,
            ) as load_java_policy,
        ):
            validator.validate_profiles()

        load_configuration.assert_called_once_with(validator.ROOT)
        load_java_policy.assert_called_once_with(
            validator.ROOT,
            configuration=configuration,
        )

    def test_configuration_failure_is_reported_as_validation_failure(
        self,
    ) -> None:
        with patch.object(
            validator,
            "load_workbench_configuration",
            side_effect=validator.WorkbenchConfigurationError("bad manifest"),
        ):
            with self.assertRaisesRegex(
                ValidationFailure,
                "selected Workbench configuration.*bad manifest",
            ):
                validator.validate_profiles()


if __name__ == "__main__":
    unittest.main()
