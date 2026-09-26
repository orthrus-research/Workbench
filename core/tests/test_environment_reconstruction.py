"""Portable environment selections bind only after exact local admission."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
import tomllib
from unittest import TestCase
from unittest.mock import patch

from jsonschema import Draft202012Validator

from workbench_core import settings_cli
from workbench_core.environment_reconstruction import (
    ReconstructionError,
    apply_import,
    assess_reconstruction_feasibility,
    build_share,
    export_share,
    load_share,
    plan_import,
)
from workbench_core.environment_resolution import resolve_environment
from workbench_core.runtime_java import (
    JavaRuntimeError, host_platform, load_java_runtime_policy,
    select_managed_java_policy,
)
from workbench_core.storage.registered import CoreDurableResources, ResourceCatalog
from workbench_core.configuration import load_workbench_configuration
from workbench_core.user_preferences import (
    UserPreferencesError,
    bind_workspace_selection,
    load_workspaces,
    register_workspace,
    set_location,
    set_workspace_selection,
)


SOURCE_SUITE = Path(__file__).resolve().parents[2]


def _suite(destination: Path) -> Path:
    destination.mkdir(parents=True)
    config = tomllib.loads((SOURCE_SUITE / "workbench.toml").read_text(encoding="utf-8"))
    files = ["workbench.toml", config["selection"]["pack_document"],
             config["selection"]["platform_document"]]
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(SOURCE_SUITE / relative, target)
    return destination


def _environment(root: Path) -> dict[str, str]:
    return {
        "HOME": str(root),
        "WORKBENCH_CONFIG_HOME": str(root / "config"),
        "WORKBENCH_STATE_ROOT": str(root / "state"),
    }


def _reseal(record: dict, prefix: str, field: str) -> None:
    body = {key: value for key, value in record.items() if key != field}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record[field] = prefix + ":sha256:" + sha256(encoded).hexdigest()


class EnvironmentReconstructionTests(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_suite = _suite(self.root / "source-suite")
        self.target_suite = _suite(self.root / "target-suite")
        self.source_root = self.root / "source-user"
        self.target_root = self.root / "target-user"
        self.source_workspace = self.source_root / "workspace"
        self.target_workspace = self.target_root / "workspace"
        self.source_workspace.mkdir(parents=True)
        self.target_workspace.mkdir(parents=True)
        self.source_environment = _environment(self.source_root)
        self.target_environment = _environment(self.target_root)
        register_workspace("pack", str(self.source_workspace), environment=self.source_environment)

    def _source_choice(self, *, java_home: str | None = None, feature: int | None = None) -> None:
        set_workspace_selection(
            "pack", profile_config=str(self.source_suite / "workbench.toml"),
            java_home=java_home, managed_java_feature=feature,
            environment=self.source_environment,
        )

    def _attach_local_source_lock(self) -> None:
        for suite in (self.source_suite, self.target_suite):
            profile = suite / "profiles/packs/supersymmetry/profile.yaml"
            original = profile.read_text(encoding="utf-8")
            updated = original.replace(
                "    maturity: experimental\n",
                "    source_lock: source-locks/legacy-forge/source-lock.json\n"
                "    maturity: experimental\n",
                1,
            )
            self.assertNotEqual(original, updated)
            profile.write_text(updated, encoding="utf-8")
            source = SOURCE_SUITE / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
            destination = suite / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
            destination.parent.mkdir(parents=True)
            copy2(source, destination)

    def _managed_result(self, *, outcome: str, registered: int = 0):
        def acquire(_suite, **options):
            self.assertEqual((), options["candidates"])
            self.assertEqual(self.target_root / "state", options["state_root"])
            self.assertEqual(8, options["managed_feature_version"])
            self.assertEqual(
                registered, len(load_workspaces(environment=self.target_environment)["entries"]),
            )
            policy = select_managed_java_policy(
                load_java_runtime_policy(self.target_suite, configuration=options["configuration"]),
                options["managed_feature_version"],
            )
            return {
                "format": "workbench-java-runtime-result-v2",
                "source": "managed", "outcome": outcome,
                "receipt": {
                    "format": "workbench-java-runtime-receipt-v2",
                    "state": "ready", "policy": policy,
                    "host": host_platform(),
                    "runtime_id": "workbench-java-runtime-v2:test",
                    "target": {"receipt_uri": "file:///local/state/receipts/java-runtime-v2.json"},
                },
            }
        return acquire

    def test_reviewed_import_acquires_locked_java_before_binding(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        (self.target_suite / "workbench.toml").unlink()
        ordinary = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=True, environment=self.target_environment,
        )
        self.assertEqual("ready", plan["state"])
        self.assertEqual("workbench-environment-import-plan-v1", ordinary["format"])
        self.assertNotIn("acquire_managed_java", ordinary)
        self.assertEqual("workbench-environment-import-plan-v2", plan["format"])
        self.assertTrue(plan["acquire_managed_java"])
        self.assertNotEqual(ordinary["plan_id"], plan["plan_id"])
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_import(
                self.target_suite, share, expected_plan_id=ordinary["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                acquire_managed_java=True, environment=self.target_environment,
            )
        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=self._managed_result(outcome="provisioned"),
        ) as acquire:
            result = apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                acquire_managed_java=True, environment=self.target_environment,
            )
        self.assertEqual(1, acquire.call_count)
        self.assertEqual("workbench-environment-import-result-v2", result["format"])
        self.assertEqual("provisioned", result["managed_java"]["outcome"])
        self.assertEqual(share["lock"]["java_policy"]["selected_policy_sha256"],
                         result["managed_java"]["policy_sha256"])
        self.assertNotIn("managed-java-archive", result["unresolved_inputs"])
        self.assertIn("workspace-project-bytes", result["unresolved_inputs"])
        self.assertEqual(8, load_workspaces(environment=self.target_environment)["entries"][0]["managed_java_feature"])
        retained = json.loads(Path(result["resource"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(result["managed_java"], retained["managed_java"])
        repeat_plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=True, environment=self.target_environment,
        )
        self.assertEqual("reuse", repeat_plan["action"])
        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=self._managed_result(outcome="reused", registered=1),
        ):
            repeated = apply_import(
                self.target_suite, share, expected_plan_id=repeat_plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                acquire_managed_java=True, environment=self.target_environment,
            )
        self.assertEqual(("reused", "reused"),
                         (repeated["outcome"], repeated["managed_java"]["outcome"]))

    def test_failed_managed_acquisition_keeps_attempt_and_does_not_bind(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        (self.target_suite / "workbench.toml").unlink()
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=True, environment=self.target_environment,
        )
        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=JavaRuntimeError("archive unavailable"),
        ):
            with self.assertRaisesRegex(JavaRuntimeError, "archive unavailable"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    acquire_managed_java=True, environment=self.target_environment,
                )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])
        attempts = list((self.target_root / "state/evidence/outputs/workbench-core").glob(
            "*-environment-import-attempt.json"
        ))
        self.assertEqual(1, len(attempts))
        attempt = json.loads(attempts[0].read_text(encoding="utf-8"))
        self.assertEqual("workbench-environment-import-attempt-v2", attempt["format"])
        self.assertTrue(attempt["acquire_managed_java"])
        retry = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=True, environment=self.target_environment,
        )
        self.assertNotEqual(plan["plan_id"], retry["plan_id"])
        self.assertEqual("reuse-generated", retry["configuration_manifest_action"])
        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=self._managed_result(outcome="reused"),
        ):
            recovered = apply_import(
                self.target_suite, share, expected_plan_id=retry["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                acquire_managed_java=True, environment=self.target_environment,
            )
        self.assertEqual("reused", recovered["managed_java"]["outcome"])

    def test_foreign_managed_java_receipt_cannot_bind_workspace(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=True, environment=self.target_environment,
        )

        def foreign(suite, **options):
            value = self._managed_result(outcome="reused")(suite, **options)
            value["receipt"]["policy"] = {
                **value["receipt"]["policy"], "release_name": "jdk8u1-b01",
            }
            return value

        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=foreign,
        ):
            with self.assertRaisesRegex(ReconstructionError, "locked runtime"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    acquire_managed_java=True, environment=self.target_environment,
                )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

    def test_profile_drift_during_managed_acquisition_prevents_binding(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            acquire_managed_java=True, environment=self.target_environment,
        )

        def drift(suite, **options):
            result = self._managed_result(outcome="provisioned")(suite, **options)
            platform = self.target_suite / share["intent"]["selection"]["platform_document"]
            platform.write_bytes(platform.read_bytes() + b"\n")
            return result

        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=drift,
        ):
            with self.assertRaisesRegex(ReconstructionError, "changed during Java acquisition"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    acquire_managed_java=True, environment=self.target_environment,
                )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

    def test_supplied_java_path_cannot_be_managed_acquisition(self) -> None:
        self._source_choice(java_home=str(self.source_root / "unchecked-jdk"))
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        with self.assertRaisesRegex(ReconstructionError, "user-supplied Java path"):
            plan_import(
                self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
                java_home=self.target_root / "unchecked-jdk", acquire_managed_java=True,
                environment=self.target_environment,
            )

    def test_cli_import_acquires_managed_java_with_same_reviewed_flag(self) -> None:
        self._source_choice(feature=8)
        path = export_share(
            self.source_suite, "pack", environment=self.source_environment,
        )["resource"]["path"]
        with patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "plan", path, "--name", "shared",
                "--workspace", str(self.target_workspace), "--acquire-managed-java", "--json",
            ], suite_root=self.target_suite))
        plan = json.loads(stream.getvalue())
        self.assertTrue(plan["acquire_managed_java"])
        with patch(
            "workbench_core.environment_reconstruction.ensure_java_runtime",
            side_effect=self._managed_result(outcome="provisioned"),
        ), patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "import", path, "--name", "shared",
                "--workspace", str(self.target_workspace), "--acquire-managed-java",
                "--plan-id", plan["plan_id"], "--json",
            ], suite_root=self.target_suite))
        result = json.loads(stream.getvalue())
        self.assertEqual("provisioned", result["managed_java"]["outcome"])
        self.assertNotIn("managed-java-archive", result["unresolved_inputs"])

    def test_missing_manifest_is_generated_under_core_custody_and_reused(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        (self.target_suite / "workbench.toml").unlink()
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        manifest_path = Path(plan["profile_config"])
        self.assertEqual((plan["state"], plan["configuration_manifest_action"]), ("ready", "create"))
        self.assertTrue(manifest_path.is_relative_to(self.target_root / "state/artifacts"))
        self.assertFalse(manifest_path.exists())
        self.assertFalse((self.target_root / "config").exists())
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

        result = apply_import(
            self.target_suite, share, expected_plan_id=plan["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        payload = manifest_path.read_bytes()
        self.assertEqual(
            payload,
            b'schema = "workbench/config/v1"\n\n[selection]\n'
            b'pack_document = "profiles/packs/supersymmetry/profile.yaml"\n'
            b'pack_variant = "cleanroom-provisional"\n'
            b'platform_document = "profiles/platforms/cleanroom/provisional.yaml"\n\n'
            b'[bindings]\n',
        )
        self.assertNotIn(str(self.source_suite).encode(), payload)
        self.assertNotIn(str(self.source_workspace).encode(), payload)
        self.assertEqual(share["lock"]["selection_digest"], load_workbench_configuration(
            self.target_suite, manifest_path,
        ).selection_digest)
        self.assertEqual(str(manifest_path), load_workspaces(
            environment=self.target_environment,
        )["entries"][0]["profile_config"])
        resource_id = result["configuration_manifest"]["resource_id"]
        self.assertIsNotNone(resource_id)
        catalog = ResourceCatalog(self.target_root / "config")
        resources = catalog.inventory(workspace=self.target_workspace)["resources"]
        self.assertEqual(
            [(row["resource_id"], row["status"]) for row in resources if row["path"] == str(manifest_path)],
            [(resource_id, "committed")],
        )
        self.assertIn(resource_id, next(
            row["references"] for row in resources
            if row["resource_id"] == result["resource"]["resource_id"]
        ))
        repeat = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual((repeat["state"], repeat["action"], repeat["configuration_manifest_action"]),
                         ("ready", "reuse", "reuse-generated"))
        second = apply_import(
            self.target_suite, share, expected_plan_id=repeat["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("reused", second["outcome"])
        self.assertEqual(resource_id, second["configuration_manifest"]["resource_id"])

    def test_missing_manifest_requires_exact_profile_bytes_and_reviewed_plan(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        (self.target_suite / "workbench.toml").unlink()
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        platform = self.target_suite / share["intent"]["selection"]["platform_document"]
        platform.write_bytes(platform.read_bytes() + b"\n")
        changed = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("blocked", changed["state"])
        self.assertIn("differ", " ".join(changed["blockers"]))
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                environment=self.target_environment,
            )
        self.assertFalse(Path(plan["profile_config"]).exists())
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

    def test_different_suite_default_is_preserved_and_explicit_config_blocks(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        default = self.target_suite / "workbench.toml"
        alternate = self.target_suite / "profiles/packs/alternate/profile.yaml"
        alternate.parent.mkdir(parents=True)
        copy2(self.target_suite / "profiles/packs/supersymmetry/profile.yaml", alternate)
        default.write_text(default.read_text(encoding="utf-8").replace(
            "profiles/packs/supersymmetry/profile.yaml",
            "profiles/packs/alternate/profile.yaml",
        ), encoding="utf-8")
        before = default.read_bytes()
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual((plan["state"], plan["configuration_manifest_action"]), ("ready", "create"))
        explicit = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            config_path=default, environment=self.target_environment,
        )
        self.assertEqual("blocked", explicit["state"])
        self.assertIn("differ", " ".join(explicit["blockers"]))
        apply_import(
            self.target_suite, share, expected_plan_id=plan["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual(before, default.read_bytes())

    def test_unregistered_generated_manifest_cannot_be_adopted(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        (self.target_suite / "workbench.toml").unlink()
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        generated = Path(plan["profile_config"])
        generated.parent.mkdir(parents=True)
        generated.write_text(
            'schema = "workbench/config/v1"\n\n[selection]\n'
            'pack_document = "profiles/packs/supersymmetry/profile.yaml"\n'
            'pack_variant = "cleanroom-provisional"\n'
            'platform_document = "profiles/platforms/cleanroom/provisional.yaml"\n\n'
            '[bindings]\n', encoding="utf-8",
        )
        blocked = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("blocked", blocked["state"])
        self.assertIn("outside Core custody", " ".join(blocked["blockers"]))
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                environment=self.target_environment,
            )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

    def test_core_cli_plans_and_imports_generated_manifest(self) -> None:
        self._source_choice()
        exported = export_share(self.source_suite, "pack", environment=self.source_environment)
        share_path = exported["resource"]["path"]
        (self.target_suite / "workbench.toml").unlink()
        with patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "plan", share_path, "--name", "shared",
                "--workspace", str(self.target_workspace), "--json",
            ], suite_root=self.target_suite))
        plan = json.loads(stream.getvalue())
        self.assertEqual((plan["state"], plan["configuration_manifest_action"]), ("ready", "create"))
        with patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "import", share_path, "--name", "shared",
                "--workspace", str(self.target_workspace), "--plan-id", plan["plan_id"],
                "--json",
            ], suite_root=self.target_suite))
        result = json.loads(stream.getvalue())
        self.assertEqual("bound", result["outcome"])
        self.assertEqual(plan["profile_config"], result["configuration_manifest"]["path"])
        self.assertIsNotNone(result["configuration_manifest"]["resource_id"])

    def test_generated_manifest_survives_failed_binding_with_prepared_evidence(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        (self.target_suite / "workbench.toml").unlink()
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        with patch(
            "workbench_core.environment_reconstruction.bind_workspace_selection",
            side_effect=UserPreferencesError("injected binding failure"),
        ):
            with self.assertRaisesRegex(UserPreferencesError, "injected binding failure"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    environment=self.target_environment,
                )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])
        self.assertTrue(Path(plan["profile_config"]).is_file())
        attempts = list((self.target_root / "state/evidence/outputs/workbench-core").glob(
            "*-environment-import-attempt.json"
        ))
        self.assertEqual(len(attempts), 1)
        attempted = json.loads(attempts[0].read_text(encoding="utf-8"))
        self.assertEqual(attempted["state"], "prepared")
        self.assertEqual(attempted["configuration_manifest"]["sha256"], plan["configuration_manifest_sha256"])
        retry = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual((retry["state"], retry["configuration_manifest_action"]),
                         ("ready", "reuse-generated"))
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                environment=self.target_environment,
            )
        completed = apply_import(
            self.target_suite, share, expected_plan_id=retry["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("bound", completed["outcome"])

    def test_clean_root_import_reopens_exact_selection_and_reuses_registry(self) -> None:
        self._source_choice(feature=8)
        exported = export_share(self.source_suite, "pack", environment=self.source_environment)
        share_path = Path(exported["resource"]["path"])
        share = load_share(share_path)
        self.assertEqual(exported["share"], share)
        schema = json.loads((SOURCE_SUITE / "core/src/workbench_core/schemas/workbench-environment-share-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(share)
        self.assertEqual(share_path.read_bytes(), json.dumps(
            share, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n")
        portable = json.dumps(share)
        for local in (self.source_workspace, self.source_root, self.source_suite):
            self.assertNotIn(str(local), portable)
        self.assertEqual("managed-feature", share["intent"]["java"]["mode"])
        self.assertEqual(8, share["intent"]["java"]["feature_version"])

        self.assertFalse((self.target_root / "config").exists())
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual((plan["state"], plan["action"]), ("ready", "create"))
        self.assertFalse((self.target_root / "config").exists())
        result = apply_import(
            self.target_suite, share, expected_plan_id=plan["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("bound", result["outcome"])
        self.assertIn("managed-java-archive", result["unresolved_inputs"])
        self.assertTrue(Path(result["resource"]["path"]).is_file())
        registry = load_workspaces(environment=self.target_environment)
        self.assertEqual("workbench-user-workspaces-v3", registry["format"])
        self.assertEqual("shared", registry["default"])
        row = registry["entries"][0]
        self.assertEqual(8, row["managed_java_feature"])
        self.assertEqual(str(self.target_suite / "workbench.toml"), row["profile_config"])
        selected = resolve_environment(
            self.target_suite, workspace=self.target_workspace,
            environment=self.target_environment,
        ).operation_selection()
        self.assertEqual(8, selected.managed_java_feature)
        self.assertEqual(self.target_suite / "workbench.toml", selected.profile_configuration)

        repeat = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual((repeat["state"], repeat["action"]), ("ready", "reuse"))
        second = apply_import(
            self.target_suite, share, expected_plan_id=repeat["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("reused", second["outcome"])
        self.assertEqual(registry["record_id"], load_workspaces(environment=self.target_environment)["record_id"])

    def test_feasibility_reports_missing_project_and_artifact_locks_without_writing(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        before = deepcopy(share)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        report = assess_reconstruction_feasibility(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        schema = json.loads((
            SOURCE_SUITE / "core/src/workbench_core/schemas/workbench-environment-feasibility-v1.schema.json"
        ).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report)
        self.assertEqual("workbench-environment-feasibility-v1", report["format"])
        self.assertEqual(plan["plan_id"], report["import_plan_id"])
        self.assertEqual("exact-inputs-required", report["state"])
        self.assertEqual("missing-variant-lock", report["project_source"]["state"])
        self.assertIsNone(report["project_source"]["candidate"])
        self.assertIn("portable-source-lock-sha256", report["project_source"]["required"])
        self.assertEqual("missing-package-lock", report["optional_module_packages"]["state"])
        self.assertEqual("missing-artifact-lock", report["profile_fixture_tools"]["state"])
        self.assertEqual("managed-archive-unresolved", report["java"]["state"])
        self.assertEqual(before, share)
        self.assertFalse((self.target_root / "config").exists())
        self.assertFalse((self.target_root / "state").exists())

    def test_feasibility_marks_exact_git_lock_as_unbound_local_candidate(self) -> None:
        self._attach_local_source_lock()
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        report = assess_reconstruction_feasibility(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        project = report["project_source"]
        schema = json.loads((
            SOURCE_SUITE / "core/src/workbench_core/schemas/workbench-environment-feasibility-v1.schema.json"
        ).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(report)
        self.assertEqual("local-lock-unbound", project["state"])
        self.assertEqual(["portable-source-lock-sha256"], project["required"])
        lock = self.target_suite / project["candidate"]["relative_path"]
        self.assertEqual("sha256:" + sha256(lock.read_bytes()).hexdigest(), project["candidate"]["sha256"])
        self.assertEqual(40, len(project["candidate"]["revision"]))
        self.assertEqual(40, len(project["candidate"]["tree"]))
        document = json.loads(lock.read_text(encoding="utf-8"))
        document["pack"]["revision"] = "0" * 40
        lock.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        changed = assess_reconstruction_feasibility(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual(share["share_id"], changed["share_id"])
        self.assertEqual("local-lock-unbound", changed["project_source"]["state"])
        self.assertNotEqual(project["candidate"]["sha256"], changed["project_source"]["candidate"]["sha256"])
        self.assertNotEqual(report["feasibility_id"], changed["feasibility_id"])

    def test_feasibility_rejects_invalid_local_source_lock_candidate(self) -> None:
        self._attach_local_source_lock()
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        lock = self.target_suite / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
        lock.write_text('{"format":"workbench-source-lock-v3","pack":{}}\n', encoding="utf-8")
        report = assess_reconstruction_feasibility(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("referenced-lock-invalid", report["project_source"]["state"])
        self.assertIsNone(report["project_source"]["candidate"])

    def test_feasibility_cli_and_unchecked_java_path(self) -> None:
        self._source_choice(java_home=str(self.source_root / "unverified-java"))
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        path = self.root / "share.json"
        path.write_bytes(json.dumps(share, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        local_java = self.target_root / "unverified-java"
        with patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "feasibility", str(path), "--name", "shared",
                "--workspace", str(self.target_workspace), "--java-home", str(local_java), "--json",
            ], suite_root=self.target_suite))
        report = json.loads(stream.getvalue())
        self.assertEqual("ready", report["import_state"])
        self.assertEqual("local-binding-unchecked", report["java"]["state"])
        self.assertNotIn(str(local_java), stream.getvalue())
        self.assertFalse(local_java.exists())
        self.assertFalse((self.target_root / "config").exists())

    def test_missing_changed_and_cross_host_inputs_block_without_binding(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        target_platform = self.target_suite / share["intent"]["selection"]["platform_document"]
        original = target_platform.read_bytes()
        target_platform.unlink()
        missing = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("blocked", missing["state"])
        self.assertIn("unavailable", " ".join(missing["blockers"]))
        target_platform.write_bytes(original + b"\n")
        changed = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("blocked", changed["state"])
        self.assertIn("differ", " ".join(changed["blockers"]))
        target_platform.write_bytes(original)
        other_host = {"os": "windows" if share["lock"]["host_variant"]["os"] != "windows" else "linux",
                      "architecture": share["lock"]["host_variant"]["architecture"]}
        unsupported = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment, host=other_host,
        )
        self.assertEqual("blocked", unsupported["state"])
        self.assertIn("unsupported platform binding", " ".join(unsupported["blockers"]))
        self.assertFalse((self.target_root / "config").exists())

    def test_resealed_false_java_lock_fields_block_target_admission(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        for field, altered_value in (
            ("feature_version", 8),
            ("runtime_identity", "forged-runtime"),
            ("release_name", "forged-release"),
        ):
            with self.subTest(field=field):
                altered = deepcopy(share)
                altered["lock"]["java_policy"][field] = altered_value
                _reseal(altered["lock"], "workbench-environment-lock", "lock_id")
                _reseal(altered, "workbench-environment-share", "share_id")
                plan = plan_import(
                    self.target_suite, altered, workspace_name="shared", workspace=self.target_workspace,
                    environment=self.target_environment,
                )
                self.assertEqual("blocked", plan["state"])
                self.assertIn("Java policy differs", " ".join(plan["blockers"]))
        self.assertFalse((self.target_root / "config").exists())

    def test_stale_registry_plan_and_atomic_name_collision_preserve_choices(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        other = self.target_root / "other"
        other.mkdir()
        register_workspace("other", str(other), environment=self.target_environment)
        before = load_workspaces(environment=self.target_environment)
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                environment=self.target_environment,
            )
        self.assertEqual(before, load_workspaces(environment=self.target_environment))
        with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
            bind_workspace_selection(
                "shared", str(self.target_workspace),
                profile_config=str(self.target_suite / "workbench.toml"),
                java_home=None, managed_java_feature=8,
                expected_record_id=plan["expected_workspaces_record_id"],
                environment=self.target_environment,
            )
        collision = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=other,
            environment=self.target_environment,
        )
        self.assertEqual("blocked", collision["state"])
        self.assertIn("already uses", " ".join(collision["blockers"]))

    def test_failed_binding_retains_prepared_attempt_without_saved_choice(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        with patch(
            "workbench_core.environment_reconstruction.bind_workspace_selection",
            side_effect=UserPreferencesError("interrupted before binding"),
        ):
            with self.assertRaisesRegex(UserPreferencesError, "interrupted"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    environment=self.target_environment,
                )
        attempts = list((self.target_root / "state/evidence/outputs/workbench-core").glob(
            "*-environment-import-attempt.json"
        ))
        self.assertEqual(1, len(attempts))
        self.assertEqual("prepared", json.loads(attempts[0].read_text())["state"])
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

    def test_profile_drift_after_prepared_attempt_blocks_binding(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        target_platform = self.target_suite / share["intent"]["selection"]["platform_document"]
        publish = CoreDurableResources.publish_bytes

        def drift_after_preparation(service, role, name, data, **options):
            reference = publish(service, role, name, data, **options)
            if name == "environment-import-attempt.json":
                target_platform.write_bytes(target_platform.read_bytes() + b"\n")
            return reference

        with patch.object(CoreDurableResources, "publish_bytes", drift_after_preparation):
            with self.assertRaisesRegex(ReconstructionError, "changed after the prepared attempt"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    environment=self.target_environment,
                )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])
        attempts = list((self.target_root / "state/evidence/outputs/workbench-core").glob(
            "*-environment-import-attempt.json"
        ))
        self.assertEqual(1, len(attempts))

    def test_location_policy_change_invalidates_reviewed_plan(self) -> None:
        self._source_choice()
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        set_location(
            "evidence", str(self.target_root / "different-evidence"),
            environment=self.target_environment,
        )
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                environment=self.target_environment,
            )
        self.assertEqual([], load_workspaces(environment=self.target_environment)["entries"])

    def test_interrupted_completion_reuses_bound_choice_on_retry(self) -> None:
        self._source_choice(feature=8)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        publish = CoreDurableResources.publish_bytes

        def fail_completion(service, role, name, data, **options):
            if name == "environment-reconstruction.json":
                raise OSError("interrupted completion")
            return publish(service, role, name, data, **options)

        with patch.object(CoreDurableResources, "publish_bytes", fail_completion):
            with self.assertRaisesRegex(OSError, "interrupted completion"):
                apply_import(
                    self.target_suite, share, expected_plan_id=plan["plan_id"],
                    workspace_name="shared", workspace=self.target_workspace,
                    environment=self.target_environment,
                )
        registry = load_workspaces(environment=self.target_environment)
        self.assertEqual("shared", registry["entries"][0]["name"])
        retry = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("reuse", retry["action"])
        completed = apply_import(
            self.target_suite, share, expected_plan_id=retry["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("reused", completed["outcome"])
        self.assertEqual(registry, load_workspaces(environment=self.target_environment))

    def test_user_java_path_is_redacted_and_rebound_without_probe(self) -> None:
        source_java = str(self.source_root / "private-unused-jdk")
        self._source_choice(java_home=source_java)
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        self.assertNotIn(source_java, json.dumps(share))
        self.assertEqual("local-binding-required", share["intent"]["java"]["mode"])
        missing = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        self.assertEqual("blocked", missing["state"])
        self.assertIn("local Java home", " ".join(missing["blockers"]))
        target_java = str(self.target_root / "does-not-exist-jdk")
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            java_home=target_java, environment=self.target_environment,
        )
        self.assertEqual("ready", plan["state"])
        with patch("workbench_core.java_inventory.inspect_java_inventory", side_effect=AssertionError("inventory used")):
            result = apply_import(
                self.target_suite, share, expected_plan_id=plan["plan_id"],
                workspace_name="shared", workspace=self.target_workspace,
                java_home=target_java, environment=self.target_environment,
            )
        self.assertEqual("bound", result["outcome"])
        self.assertEqual(target_java, load_workspaces(environment=self.target_environment)["entries"][0]["java_home"])

    def test_v1_registry_migrates_without_losing_unrelated_workspace(self) -> None:
        self._source_choice()
        unrelated = self.target_root / "unrelated"
        unrelated.mkdir()
        register_workspace("other", str(unrelated), environment=self.target_environment)
        previous = load_workspaces(environment=self.target_environment)
        self.assertEqual(1, previous["schema_version"])
        share = build_share(self.source_suite, "pack", environment=self.source_environment)
        plan = plan_import(
            self.target_suite, share, workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        apply_import(
            self.target_suite, share, expected_plan_id=plan["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            environment=self.target_environment,
        )
        rows = {row["name"]: row for row in load_workspaces(environment=self.target_environment)["entries"]}
        self.assertEqual({"other", "shared"}, set(rows))
        self.assertEqual(str(unrelated), rows["other"]["path"])
        self.assertIsNone(rows["other"]["profile_config"])
        self.assertIsNone(rows["other"]["managed_java_feature"])
        self.assertTrue(rows["other"]["workspace_id"].startswith("workbench-workspace-v1:"))

    def test_cli_export_plan_import_and_strict_reader(self) -> None:
        self._source_choice()
        with patch.dict(os.environ, self.source_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main(
                ["environment", "export", "pack", "--json"], suite_root=self.source_suite,
            ))
        exported = json.loads(stream.getvalue())
        path = Path(exported["resource"]["path"])
        with patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "plan", str(path), "--name", "shared",
                "--workspace", str(self.target_workspace), "--json",
            ], suite_root=self.target_suite))
        plan = json.loads(stream.getvalue())
        with patch.dict(os.environ, self.target_environment, clear=False), redirect_stdout(StringIO()) as stream:
            self.assertEqual(0, settings_cli.main([
                "environment", "import", str(path), "--name", "shared",
                "--workspace", str(self.target_workspace), "--plan-id", plan["plan_id"],
                "--json",
            ], suite_root=self.target_suite))
        self.assertEqual("bound", json.loads(stream.getvalue())["outcome"])
        altered = dict(exported["share"])
        altered["intent"] = dict(altered["intent"])
        altered["intent"]["java"] = {"mode": "profile-default", "feature_version": 8}
        with self.assertRaises(ReconstructionError):
            plan_import(
                self.target_suite, altered, workspace_name="shared", workspace=self.target_workspace,
                environment=self.target_environment,
            )
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"format":"one","format":"two"}', encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "duplicate"):
            load_share(duplicate)
        alias = self.root / "share-link.json"
        alias.symlink_to(path)
        with self.assertRaisesRegex(ReconstructionError, "regular file"):
            load_share(alias)


if __name__ == "__main__":
    from unittest import main
    main()
