"""Failure-driven tests for the Supersymmetry FeatureRuntimePairPorts owner."""

from __future__ import annotations

from workbench_shell.runtime_composition import material_fluid_construction_ports, installed_runtime_services

import base64
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[5]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
RUNTIME_SOURCE = ROOT / "profiles/packs/supersymmetry/runtime/src"
FEATURE_TEST_SOURCE = ROOT / "modules/workbench-shell/tests/developer_feature"
for source in (RUNTIME_SOURCE, FEATURE_TEST_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from test_developer_feature import _bytes, _checkout  # noqa: E402
from workbench_shell.developer_feature import (  # noqa: E402
    apply_material_fluid_recipe_plan,
    build_material_fluid_recipe_plan,
)
from workbench_shell.feature_change_workspace import (  # noqa: E402
    PAIR_REQUEST_FORMAT,
)
from workbench_profile_supersymmetry.material_fluid_runtime_pair import (  # noqa: E402
    EXECUTION_RESULT_FORMAT,
    INSTALLED_RUNTIME_CONFIG_FORMAT,
    InstalledSupersymmetryRuntimeConfig,
    InstalledSupersymmetryStagedPackExecutionPorts,
    PAIR_OWNER_FORMAT,
    STAGE_FORMAT,
    MaterialFluidRuntimePairError,
    StagedPackRuntimeExecutionPorts,
    SupersymmetryMaterialFluidRuntimePairPorts,
    runtime_pair,
    runtime_pair_from_config,
    interpret_material_fluid_runtime_marker,
    load_installed_supersymmetry_runtime_config,
    validate_material_fluid_runtime_pair_owner,
)


SCHEMA_ROOT = ROOT / "profiles/packs/supersymmetry/runtime/schemas"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _request(plan: dict, *, comparison: str, side: str, order: str) -> dict:
    if comparison == "aa":
        roles = ["baseline", "baseline"]
    elif comparison == "post-apply":
        roles = ["candidate", "candidate"]
    elif comparison == "post-rollback":
        roles = ["baseline", "baseline"]
    else:
        roles = (
            ["baseline", "candidate"]
            if order == "baseline-first"
            else ["candidate", "baseline"]
        )
    required = [
        "material_registration",
        "fluid_registration",
        "recipe_registration",
        "unification_identity",
        "localization" if side == "client" else "dedicated_server_safe",
        "forbidden_delta_absent",
    ]
    body = {
        "format": PAIR_REQUEST_FORMAT,
        "schema_version": 1,
        "change_id": "workbench-feature-change:sha256:" + "a" * 64,
        "plan_id": plan["id"],
        "comparison": comparison,
        "side": side,
        "order": order,
        "attempt_id": "matrix-test",
        "roles": roles,
        "required_assertions": required,
    }
    return {
        **body,
        "request_id": "workbench-feature-runtime-pair-request:sha256:"
        + sha256(_canonical(body)).hexdigest(),
    }


def _owner_ref(path: Path, *, record_id: str, outcome: str) -> dict:
    raw = path.read_bytes()
    return {
        "owner_id": "supersymmetry-staged-pack-runtime-execution",
        "record_id": record_id,
        "record_kind": EXECUTION_RESULT_FORMAT,
        "uri": path.as_uri(),
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "size": len(raw),
        "outcome": outcome,
    }


class _ExecutionPorts(StagedPackRuntimeExecutionPorts):
    def __init__(self, *, fail_ordinal: int | None = None) -> None:
        self.calls: list[tuple[str, str, Path]] = []
        self.fail_ordinal = fail_ordinal

    def execute(
        self,
        request: dict,
        *,
        suite_root: Path,
        plan: dict,
        stage: dict,
        execution_root: Path,
    ) -> dict:
        del suite_root
        workspace = Path(stage["workspace_uri"].removeprefix("file://"))
        role = request["role"]
        for operation in plan["operations"]:
            expected = base64.b64decode(
                operation[("before" if role == "baseline" else "after") + "_base64"],
                validate=True,
            )
            if (workspace / operation["path"]).read_bytes() != expected:
                raise AssertionError("execution received the wrong staged role")
        self.calls.append((role, request["side"], workspace))
        states = {name: "observed" for name in request["required_assertions"]}
        outcome = "passed"
        state = "complete"
        if self.fail_ordinal == request["ordinal"]:
            states[request["required_assertions"][0]] = "failed"
            outcome = "failed"
            state = "failed"
        observation_material = {
            "role": role,
            "side": request["side"],
            "semantic_state": "absent" if role == "baseline" else "present",
        }
        observation_body = {
            "format": "workbench-supersymmetry-material-fluid-runtime-observation-v1",
            "schema_version": 1,
            "plan_id": plan["id"],
            "role": role,
            "side": request["side"],
            "state": "observed" if outcome == "passed" else "failed",
            "semantic_fingerprint": "sha256:"
            + sha256(role.encode("ascii")).hexdigest(),
            "assertions": states,
            "observed": observation_material,
            "source_refs": [],
            "limitations": ["Synthetic execution-port observation for owner composition."],
        }
        observation = {
            **observation_body,
            "observation_id": "workbench-supersymmetry-material-fluid-runtime-observation:sha256:"
            + sha256(_canonical(observation_body)).hexdigest(),
        }
        process_path = execution_root / "synthetic-process-owner.json"
        process_path.write_bytes(
            json.dumps(
                {"outcome": outcome, "request_id": request["request_id"]},
                indent=2,
                sort_keys=True,
            ).encode()
            + b"\n"
        )
        process_ref = _owner_ref(
            process_path,
            record_id="synthetic-process:" + request["request_id"],
            outcome=outcome,
        )
        body = {
            "format": EXECUTION_RESULT_FORMAT,
            "schema_version": 1,
            "request_id": request["request_id"],
            "plan_id": plan["id"],
            "stage_id": stage["stage_id"],
            "role": role,
            "side": request["side"],
            "state": state,
            "outcome": outcome,
            "observation": observation,
            "cleanup": {
                "contained": True,
                "owned_processes_running": False,
            },
            "owner_refs": [process_ref],
            "limitations": ["Synthetic process owner used only by the focused test."],
        }
        result = {
            **body,
            "execution_id": "workbench-supersymmetry-staged-pack-execution:sha256:"
            + sha256(_canonical(body)).hexdigest(),
        }
        return result


class MaterialFluidRuntimePairTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.checkout = _checkout(self.root)
        self.plan = build_material_fluid_recipe_plan(
            ROOT,
            self.checkout,
            name="Thermal Solvent",
            color="#Aa44Cc",
            translation="Thermal Solvent Localized",
            symbol="ThermalSolventX",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="batch_reactor",
            input_fluid="steam",
            input_amount=750,
            output_amount=500,
            duration=320,
            voltage_tier="MV",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stages_exact_ordered_roles_and_retains_schema_valid_pair_owner(self) -> None:
        source_before = _bytes(self.checkout)
        execution = _ExecutionPorts()
        ports = SupersymmetryMaterialFluidRuntimePairPorts(execution, construction=material_fluid_construction_ports())
        attempt = self.root / "ab-pair"
        attempt.mkdir()

        result = ports.run_pair(
            _request(
                self.plan,
                comparison="ab",
                side="server",
                order="candidate-first",
            ),
            suite_root=ROOT,
            plan=self.plan,
            attempt_root=attempt,
        )

        self.assertEqual("passed", result["outcome"])
        self.assertEqual("observed-change", result["comparison_state"])
        self.assertEqual(["candidate", "baseline"], [row[0] for row in execution.calls])
        self.assertEqual(source_before, _bytes(self.checkout))
        pair_path = Path(result["owner_refs"][0]["uri"].removeprefix("file://"))
        owner = json.loads(pair_path.read_text(encoding="utf-8"))
        self.assertEqual(PAIR_OWNER_FORMAT, owner["format"])
        self.assertEqual(
            owner,
            validate_material_fluid_runtime_pair_owner(
                owner,
                request=_request(
                    self.plan,
                    comparison="ab",
                    side="server",
                    order="candidate-first",
                ),
                plan=self.plan,
            construction=material_fluid_construction_ports()),
        )
        self.assertEqual([STAGE_FORMAT, STAGE_FORMAT], [row["format"] for row in owner["stages"]])
        for schema_name, value in (
            ("workbench-supersymmetry-staged-material-fluid-pack-v1.schema.json", owner["stages"][0]),
            ("workbench-supersymmetry-material-fluid-runtime-pair-owner-v1.schema.json", owner),
        ):
            schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(value)

        tampered = json.loads(json.dumps(owner))
        tampered["executions"][0]["cleanup"]["owned_processes_running"] = True
        execution_body = dict(tampered["executions"][0])
        execution_body.pop("execution_id")
        tampered["executions"][0]["execution_id"] = (
            "workbench-supersymmetry-staged-pack-execution:sha256:"
            + sha256(_canonical(execution_body)).hexdigest()
        )
        owner_body = dict(tampered)
        owner_body.pop("pair_owner_id")
        tampered["pair_owner_id"] = (
            "workbench-supersymmetry-material-fluid-runtime-pair-owner:sha256:"
            + sha256(_canonical(owner_body)).hexdigest()
        )
        with self.assertRaisesRegex(MaterialFluidRuntimePairError, "outcome"):
            validate_material_fluid_runtime_pair_owner(
                tampered,
                request=_request(
                    self.plan,
                    comparison="ab",
                    side="server",
                    order="candidate-first",
                ),
                plan=self.plan,
            construction=material_fluid_construction_ports())

    def test_candidate_can_stage_from_applied_source_but_baseline_cannot(self) -> None:
        apply_material_fluid_recipe_plan(
            ROOT,
            self.plan,
            self.root / "transaction",
            consent_plan_id=self.plan["id"],
        )
        ports = SupersymmetryMaterialFluidRuntimePairPorts(_ExecutionPorts(), construction=material_fluid_construction_ports())
        post_apply = self.root / "post-apply"
        post_apply.mkdir()

        result = ports.run_pair(
            _request(
                self.plan,
                comparison="post-apply",
                side="client",
                order="baseline-first",
            ),
            suite_root=ROOT,
            plan=self.plan,
            attempt_root=post_apply,
        )

        self.assertEqual("passed", result["outcome"])
        forbidden = self.root / "baseline-from-after"
        forbidden.mkdir()
        with self.assertRaisesRegex(MaterialFluidRuntimePairError, "baseline.*after"):
            ports.run_pair(
                _request(
                    self.plan,
                    comparison="aa",
                    side="client",
                    order="baseline-first",
                ),
                suite_root=ROOT,
                plan=self.plan,
                attempt_root=forbidden,
            )

    def test_failed_execution_is_retained_as_no_go_with_contained_cleanup(self) -> None:
        ports = SupersymmetryMaterialFluidRuntimePairPorts(
            _ExecutionPorts(fail_ordinal=1)
        , construction=material_fluid_construction_ports())
        attempt = self.root / "failed"
        attempt.mkdir()

        result = ports.run_pair(
            _request(
                self.plan,
                comparison="ab",
                side="client",
                order="baseline-first",
            ),
            suite_root=ROOT,
            plan=self.plan,
            attempt_root=attempt,
        )

        self.assertEqual("failed", result["outcome"])
        self.assertEqual("unavailable", result["comparison_state"])
        self.assertEqual(
            {"contained": True, "owned_processes_running": False},
            result["cleanup"],
        )
        self.assertEqual("failed", result["observations"][1]["assertions"]["material_registration"])

    def test_role_aware_marker_interpretation_distinguishes_absent_and_present(self) -> None:
        request = self.plan["request"]
        common = {
            "recipe_map_alias_identity": True,
            "recipe_map_alias_registry_name": request["recipe_map_registry_name"],
            "recipe_map_registry_name": request["recipe_map_registry_name"],
            "manager_phase": "FROZEN",
        }
        absent = {name: None for name in (
            "chanced_fluid_output_count", "chanced_item_output_count", "color_rgb",
            "duration", "eut", "find_recipe_identity", "fluid_input_count",
            "fluid_name", "fluid_output_count", "forge_registry_roundtrip",
            "groovy_recipe", "has_flammable_flag", "has_fluid_property",
            "input_amount", "input_fluid", "item_input_count", "item_output_count",
            "localized_name", "material_id", "material_resource", "output_amount",
            "output_fluid",
        )}
        absent.update(common)
        absent["has_flammable_flag"] = False
        absent["has_fluid_property"] = False
        absent["forge_registry_roundtrip"] = False
        absent["find_recipe_identity"] = False
        absent["groovy_target_count"] = 0
        absent["exact_recipe_match_count"] = 0
        marker = _marker(self.plan, absent)

        observation = interpret_material_fluid_runtime_marker(
            self.plan,
            role="baseline",
            side="server",
            groovy_log_bytes=marker,
            source_refs=[],
            dedicated_server_ready=True,
        construction=material_fluid_construction_ports())

        self.assertEqual("observed", observation["state"])
        self.assertEqual("observed", observation["assertions"]["dedicated_server_safe"])
        with self.assertRaisesRegex(MaterialFluidRuntimePairError, "candidate"):
            interpret_material_fluid_runtime_marker(
                self.plan,
                role="candidate",
                side="server",
                groovy_log_bytes=marker,
                source_refs=[],
                dedicated_server_ready=True,
            construction=material_fluid_construction_ports())

    def test_concrete_factory_dispatches_both_physical_sides(self) -> None:
        config = InstalledSupersymmetryRuntimeConfig(
            client_launcher_executable=self.root / "PrismLauncher.exe",
            client_launcher_root=self.root / "PrismLauncher",
            server_seed_receipt=self.root / "seed.json",
            server_java=self.root / "java",
            packwiz_installer_jar=self.root / "installer.jar",
        )
        pair = runtime_pair(config, construction=material_fluid_construction_ports(), services=installed_runtime_services())
        self.assertIsInstance(pair, SupersymmetryMaterialFluidRuntimePairPorts)
        physical = pair.execution_ports
        self.assertIsInstance(
            physical, InstalledSupersymmetryStagedPackExecutionPorts
        )
        sentinel = {"physical": "owner-result"}
        context = {
            "suite_root": ROOT,
            "plan": self.plan,
            "stage": {"stage_id": "stage"},
            "execution_root": self.root,
        }
        with patch(
            "workbench_profile_supersymmetry.installed_material_fluid_runtime.execute_installed_client",
            return_value=sentinel,
        ) as client:
            self.assertIs(
                sentinel,
                physical.execute({"side": "client"}, **context),
            )
            client.assert_called_once()
        with patch(
            "workbench_profile_supersymmetry.installed_material_fluid_runtime.execute_installed_server",
            return_value=sentinel,
        ) as server:
            self.assertIs(
                sentinel,
                physical.execute({"side": "server"}, **context),
            )
            server.assert_called_once()

    def test_uri_only_installed_config_loads_without_relative_path_semantics(self) -> None:
        config_path = self.root / "installed-runtime-v1.json"
        document = {
            "format": INSTALLED_RUNTIME_CONFIG_FORMAT,
            "schema_version": 1,
            "memory_mib": 8192,
            "client": {
                "launcher": "prism",
                "launcher_executable_uri": (self.root / "PrismLauncher.exe").as_uri(),
                "launcher_root_uri": (self.root / "PrismLauncher").as_uri(),
                "launcher_profile": None,
                "launcher_java_uri": None,
                "launcher_java_state_uri": (self.root / "launcher-java").as_uri(),
                "seed_root_uris": [(self.root / "seed-client").as_uri()],
                "timeout_seconds": 600,
                "attach_timeout_seconds": 120,
                "session_timeout_seconds": 900,
            },
            "server": {
                "seed_receipt_uri": (self.root / "seed-server.json").as_uri(),
                "java_uri": (self.root / "java").as_uri(),
                "packwiz_installer_jar_uri": (self.root / "installer.jar").as_uri(),
                "timeout_seconds": 900,
                "shutdown_timeout_seconds": 180,
            },
        }
        config_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = load_installed_supersymmetry_runtime_config(config_path)

        self.assertEqual(self.root / "PrismLauncher", loaded.client_launcher_root)
        self.assertEqual((self.root / "seed-client",), loaded.client_seed_roots)
        pair = runtime_pair_from_config(
            config_path
        , construction=material_fluid_construction_ports(), services=installed_runtime_services())
        self.assertIsInstance(pair, SupersymmetryMaterialFluidRuntimePairPorts)

        document["client"]["launcher_root_uri"] = "relative/PrismLauncher"
        config_path.unlink()
        config_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(MaterialFluidRuntimePairError, "local file URI"):
            load_installed_supersymmetry_runtime_config(config_path)

    def test_current_staged_pack_uses_native_client_compatibility_only(self) -> None:
        from workbench_profile_supersymmetry.installed_material_fluid_runtime import (
            CLIENT_COMPATIBILITY_FORMAT,
            _staged_client_compatibility_policy,
        )
        from workbench_shell.material_fluid_flow import (
            _validate_compatibility_records,
        )

        workspace = self.root / "current-pack"
        (workspace / "mods").mkdir(parents=True)
        (workspace / "mods/recurrent-complex.pw.toml").write_text(
            'name = "Recurrent Complex"\n'
            'filename = "RecurrentComplex-1.4.8.7.jar"\n'
            'side = "both"\n\n'
            '[download]\n'
            'hash-format = "sha1"\n'
            'hash = "9b7eee3f3a71cb20a27490860812cd3ababc4258"\n',
            encoding="utf-8",
        )
        (workspace / "mods/susycore.pw.toml").write_text(
            'name = "SusyCore"\n'
            'filename = "Susy-Core-0.1.116.jar"\n'
            'side = "both"\n\n'
            '[download]\n'
            'hash-format = "sha1"\n'
            'hash = "1b530a4b5a32fc4d4c47559cb558644a9796390e"\n',
            encoding="utf-8",
        )

        authorized, policy = _staged_client_compatibility_policy(
            ROOT,
            workspace,
            plan_id=self.plan["id"],
            stage_id="stage-current",
            revision="revision-current",
        services=installed_runtime_services())

        self.assertEqual((), authorized)
        self.assertEqual(CLIENT_COMPATIBILITY_FORMAT, policy["format"])
        self.assertEqual("exact-native-compatible-pair", policy["mode"])
        self.assertEqual([], policy["patches"])
        probe = {
            "script_sha256": "1" * 64,
            "script_size": 123,
            "overlay_id": "probe-id",
            "overlay_spec_sha256": "2" * 64,
            "projection_target": ".minecraft/groovy/probe.groovy",
        }
        observation_record = {
            "file_sha256_after": "1" * 64,
            "file_size_after": 123,
            "operation": "file-overlay",
            "patch_id": "probe-id",
            "source_sha256": "1" * 64,
            "source_size": 123,
            "spec_sha256": "2" * 64,
            "target_path": ".minecraft/groovy/probe.groovy",
        }
        launch = {
            "compatibility_patches": [observation_record],
            "projection": {"compatibility_patches": [observation_record]},
        }
        profile_records, observed = _validate_compatibility_records(
            launch, policy, probe
        )
        self.assertEqual([], profile_records)
        self.assertEqual(observation_record, observed)

        recurrent = workspace / "mods/recurrent-complex.pw.toml"
        recurrent.write_text(
            recurrent.read_text(encoding="utf-8").replace(
                "9b7eee3f3a71cb20a27490860812cd3ababc4258",
                "0" * 40,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            MaterialFluidRuntimePairError, "lacks reviewed client compatibility"
        ):
            _staged_client_compatibility_policy(
                ROOT,
                workspace,
                plan_id=self.plan["id"],
                stage_id="stage-drifted",
                revision="revision-drifted",
            services=installed_runtime_services())

    def test_dynamic_probe_is_plan_bound_and_not_stage5_authority(self) -> None:
        from workbench_profile_supersymmetry.installed_material_fluid_runtime import _probe_material

        (self.root / "client-probe").mkdir()
        (self.root / "server-probe").mkdir()
        client_overlay, client = _probe_material(
            ROOT,
            self.plan,
            role="candidate",
            side="client",
            root=self.root / "client-probe",
        )
        server_overlay, server = _probe_material(
            ROOT,
            self.plan,
            role="baseline",
            side="server",
            root=self.root / "server-probe",
        )
        client_source = Path(client["script_uri"].removeprefix("file://"))
        server_source = Path(server["script_uri"].removeprefix("file://"))
        self.assertIn(b"Workbench-F01-Client-Close", client_source.read_bytes())
        self.assertNotIn(b"Stage5", client_source.read_bytes())
        self.assertIn(
            b"susy.material.thermal_solvent", server_source.read_bytes()
        )
        self.assertNotIn(
            b"Thermal Solvent Localized", server_source.read_bytes()
        )
        self.assertEqual(self.plan["id"], client["source_plan_id"])
        self.assertEqual(self.plan["id"], server["source_plan_id"])
        self.assertTrue(client_overlay.is_file())
        self.assertTrue(server_overlay.is_file())

    def test_all_profile_owner_schemas_are_valid_draft_2020_12(self) -> None:
        expected = {
            "workbench-supersymmetry-installed-material-fluid-runtime-config-v1.schema.json",
            "workbench-supersymmetry-material-fluid-runtime-observation-v1.schema.json",
            "workbench-supersymmetry-material-fluid-runtime-pair-owner-v1.schema.json",
            "workbench-supersymmetry-staged-client-compatibility-policy-v1.schema.json",
            "workbench-supersymmetry-staged-material-fluid-pack-v1.schema.json",
            "workbench-supersymmetry-staged-pack-execution-request-v1.schema.json",
            "workbench-supersymmetry-staged-pack-execution-v1.schema.json",
            "workbench-supersymmetry-staged-pack-server-launch-v1.schema.json",
            "workbench-supersymmetry-staged-pack-server-materialization-v1.schema.json",
        }
        self.assertEqual(expected, {path.name for path in SCHEMA_ROOT.glob("*.json")})
        for path in sorted(SCHEMA_ROOT.glob("*.json")):
            Draft202012Validator.check_schema(
                json.loads(path.read_text(encoding="utf-8"))
            )

    def test_server_property_projection_preserves_required_world_generator_binding(self) -> None:
        from workbench_profile_supersymmetry.installed_material_fluid_runtime import _server_properties

        template = self.root / "server-seed"
        runtime = self.root / "fresh-server"
        template.mkdir()
        runtime.mkdir()
        (template / "server.properties").write_text(
            "# seed\n"
            "defaultworldgenerator-port=a55790f1-609f-11ee-b9f3-80e82ceaaf53\n"
            "level-type=RTG\n"
            "online-mode=true\n",
            encoding="ascii",
        )

        record = _server_properties(template, runtime)

        projected = (runtime / "server.properties").read_text(encoding="ascii")
        self.assertIn("defaultworldgenerator-port=a55790f1-609f-11ee-b9f3-80e82ceaaf53\n", projected)
        self.assertIn("level-type=RTG\n", projected)
        self.assertIn("online-mode=false\n", projected)
        self.assertEqual("RTG", record["level_type"])


def _marker(plan: dict, actual: dict) -> bytes:
    # The owner recomputes the producer's candidate-oriented check set.  This
    # helper intentionally asks the owner for that closed calculation.
    from workbench_profile_supersymmetry.material_fluid_runtime_pair import _candidate_marker_checks, _probe_spec

    spec = plan["request"]
    checks = _candidate_marker_checks(spec, actual, None)
    body = {
        "actual": actual,
        "checks": checks,
        "error_kind": None,
        "format": "workbench-material-fluid-recipe-observation-v1",
        "probe_id": _probe_spec(ROOT, plan)[1].probe_id,
        "source_plan_id": plan["id"],
        "stage": "postInit",
        "state": "observed" if all(checks.values()) else "mismatch",
    }
    token = base64.urlsafe_b64encode(_canonical(body)).decode("ascii").rstrip("=")
    return ("[WORKBENCH-MATERIAL-FLUID-RECIPE-V1]" + token + "\n").encode()


if __name__ == "__main__":
    unittest.main()
