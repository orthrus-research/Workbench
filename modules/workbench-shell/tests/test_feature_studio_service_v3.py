"""Live Feature Studio Service V3 acceptance tests."""

from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
    SUITE_ROOT / "modules/atlas/src",
    SUITE_ROOT / "modules/blueprints/src",
    SUITE_ROOT / "modules/crucible/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_crucible_jobs.synthetic import (  # noqa: E402
    build_synthetic_job_publication,
)
from workbench_api.canonical import canonical_json_bytes
from workbench_api.profiles import profile_scope, profiles
from workbench_shell.material_fluid_flow import (
    MaterialFluidFlowError,
    _profile_observation_authority,
    _profile_runtime_policy_authority,
    material_fluid_feature_profile,
)
from workbench_shell.feature_studio import (  # noqa: E402
    execute_feature_request,
    feature_studio_capabilities,
    preview_feature,
)
from workbench_shell.feature_studio_registry import (  # noqa: E402
    FEATURE_CAPABILITY_KEYS,
    build_feature_studio_registry_v3,
)
from workbench_shell.feature_studio_service import (  # noqa: E402
    FeatureStudioServiceClientV3,
    FeatureStudioServiceV3Error,
    REGISTRY_SCHEMA_ID,
    _schema_resources,
    compose_feature_studio_service_v3,
    owner_result_from_feature_service_result_v1,
)
from workbench_shell.service_contract import validate_registry_v3  # noqa: E402
from workbench_shell.cli import main as shell_main  # noqa: E402
from workbench_core.service.host import (  # noqa: E402
    LocalServiceClientV3,
    LocalServiceEndpointV3,
)


PATHS = (
    "groovy/material/PetrochemistryMaterials.groovy",
    "groovy/material/SuSyMaterials.groovy",
    "resources/langfiles/lang/en_us.lang",
)


def _owner_plan(workspace: Path) -> dict:
    operations = []
    for index, relative in enumerate(PATHS, start=1):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        before = f"before {index}\n".encode()
        target.write_bytes(before)
        after = f"after {index}\n".encode()
        operations.append(
            {
                "operation": "update",
                "path": relative,
                "before_sha256": sha256(before).hexdigest(),
                "content_sha256": sha256(after).hexdigest(),
                "size": len(after),
                "diff": (
                    f"--- a/{relative}\n+++ b/{relative}\n"
                    f"@@ -1 +1 @@\n-before {index}\n+after {index}\n"
                ),
            }
        )
    parameters = {
        "color": "0x425d73",
        "material_id": 20008,
        "name": "Pilot Coolant",
        "registry_name": "pilot_coolant",
        "symbol_name": "PilotCoolant",
        "translation": "Pilot Coolant",
    }
    return {
        "format": "workbench-material-fluid-flow-plan-v2",
        "schema_version": 2,
        "plan_id": "sha256:" + "1" * 64,
        "state": "ready",
        "source": {
            "workspace_uri": workspace.as_uri(),
            "revision": "a" * 40,
        },
        "source_snapshot": {"snapshot_id": "sha256:" + "2" * 64},
        "blueprint": {
            "format": "workbench-material-backed-fluid-plan-v1",
            "plan_id": "sha256:" + "3" * 64,
            "profile_family_id": "workbench-pack:supersymmetry",
            "blueprint": {"effective_parameters": parameters},
        },
        "operations": operations,
        "execution": {
            "launcher": "prism",
            "target_policy": "fresh-unique-disposable-projection",
        },
        "assertions": {
            key: {"state": "pending", "meaning": key}
            for key in (
                "fml_client_loaded",
                "groovy_compilation",
                "material_identity",
                "fluid_identity",
                "localization",
            )
        },
        "limitations": ["owner limitation"],
    }


def _request(
    workspace: Path,
    operation: str,
    *,
    reviewed_plan_id: str | None = None,
) -> dict:
    runtime = None
    if operation == "verify":
        runtime = {
            "launcher_executable_uri": (workspace.parent / "launcher").as_uri(),
            "launcher_root_uri": (workspace.parent / "launcher-root").as_uri(),
            "launcher_java_uri": None,
            "launcher_java_state_uri": None,
            "launcher_profile": None,
            "packwiz_uri": None,
            "seed_uris": [],
            "memory_mib": 8192,
            "offline_name": "Workbench",
            "launch_timeout_milliseconds": 600000,
            "attach_timeout_milliseconds": 120000,
            "session_timeout_milliseconds": 21600000,
            "state_root_uri": None,
        }
    return {
        "format": "workbench-feature-studio-request-v2",
        "schema_version": 2,
        "canonicalizer": "workbench-canonical-json-v2",
        "operation": operation,
        "feature_kind": "material-backed-fluid",
        "selection_mode": "source-plan",
        "workspace_uri": workspace.as_uri(),
        "receipt_uri": None,
        "name": "Pilot Coolant",
        "color": "0x425d73",
        "translation": None,
        "symbol": None,
        "launcher": "prism",
        "reviewed_plan_id": reviewed_plan_id,
        "export_uri": None,
        "runtime": runtime,
    }


class FeatureStudioServiceV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.publication = build_synthetic_job_publication()

    def _register(self, client: FeatureStudioServiceClientV3) -> dict:
        result = client.register_context(
            self.publication.context_ref.to_dict(),
            self.publication.input_binding.to_dict(),
        )
        value = result["outcome"]["value"]
        self.assertTrue(value["registered"])
        self.assertEqual(
            result["binding"]["context_ref_id"],
            self.publication.context_ref.id,
        )
        self.assertIsNone(result["binding"]["input_binding_id"])
        self.assertEqual(
            result["binding"]["states"]["profile_support"],
            "experimental",
        )
        return value

    def test_registry_and_additive_live_projection_are_closed(self) -> None:
        bundle = build_feature_studio_registry_v3(SUITE_ROOT)
        resources = _schema_resources(SUITE_ROOT)
        self.assertFalse(
            validate_registry_v3(bundle.registry, resources, REGISTRY_SCHEMA_ID)
        )
        self.assertEqual(len(bundle.registry["capability_descriptors"]), 12)
        self.assertEqual(
            {row["provider_key"] for row in bundle.registry["providers"]},
            {
                "workbench.provider.crucible.service-control",
                "workbench.provider.shell.feature-studio",
            },
        )
        self.assertEqual(
            {row["component_key"] for row in bundle.registry["components"]},
            {
                "workbench.component.crucible.service-control",
                "workbench.component.shell.feature-studio-service",
            },
        )
        self.assertFalse(bundle.registry["descriptor_authority_admissions"])
        self.assertFalse(bundle.registry["profile_policy_admissions"])
        self.assertEqual(
            {
                row["capability_key"]
                for row in bundle.registry["capability_descriptors"]
                if row["capability_key"].startswith(
                    "workbench.feature-studio."
                )
                and row["capability_key"]
                not in {
                    "workbench.feature-studio.context-register",
                    "workbench.feature-studio.job-result",
                }
            },
            set(FEATURE_CAPABILITY_KEYS.values()),
        )
        schema = json.loads(
            (
                MODULE_ROOT
                / "schemas/feature-studio-service-bindings-v3.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(bundle.live_binding_projection)
        self.assertTrue(feature_studio_capabilities())

    def test_embedded_and_hosted_plan_preserve_identical_owner_bytes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _owner_plan(workspace)
            request = _request(workspace, "plan")
            with patch(
                "workbench_shell.feature_studio.plan_material_fluid_trial",
                return_value=owner_plan,
            ):
                direct = execute_feature_request(
                    SUITE_ROOT, request, expected_operation="plan"
                )
                composition = compose_feature_studio_service_v3(
                    SUITE_ROOT, (root / "service").resolve()
                )
                try:
                    embedded = FeatureStudioServiceClientV3.embedded(
                        composition
                    )
                    embedded.initialize()
                    self._register(embedded)
                    embedded_result = embedded.submit(
                        request,
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                    )
                    embedded_wrapper = embedded_result["outcome"]["value"]
                    self.assertEqual(
                        canonical_json_bytes(
                            owner_result_from_feature_service_result_v1(
                                embedded_wrapper, suite_root=SUITE_ROOT
                            )
                        ),
                        canonical_json_bytes(direct),
                    )

                    run_root = root / "run"
                    run_root.mkdir(mode=0o700)
                    os.chmod(run_root, 0o700)
                    endpoint_path = run_root / "service-v3.sock"
                    with LocalServiceEndpointV3(
                        composition.host, endpoint_path=endpoint_path
                    ):
                        local = LocalServiceClientV3(
                            endpoint_path, composition.authenticator.token
                        )
                        with local.connect() as connection:
                            hosted = FeatureStudioServiceClientV3(
                                composition.registry,
                                connection.call,
                                transport="local-endpoint",
                            )
                            hosted.initialize()
                            hosted_result = hosted.submit(
                                request,
                                context_ref_id=self.publication.context_ref.id,
                                input_binding_id=self.publication.input_binding.id,
                            )
                    hosted_wrapper = hosted_result["outcome"]["value"]
                    self.assertEqual(
                        hosted_wrapper["owner_result_canonical_json"],
                        embedded_wrapper["owner_result_canonical_json"],
                    )
                    self.assertEqual(
                        hosted_wrapper["owner_result_id"], direct["result_id"]
                    )
                finally:
                    composition.close()

    def test_disabled_or_unavailable_profile_cannot_execute_through_service(self) -> None:
        for policy in (
            {"disabled": ("supersymmetry",)},
            {"unavailable_distributions": ("workbench-crucible",)},
        ):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory(dir="/tmp") as temporary:
                root = Path(temporary)
                workspace = root / "source"
                workspace.mkdir()
                request = _request(workspace, "plan")
                execution_port = Mock(side_effect=AssertionError("denied owner was called"))
                with profile_scope(**policy):
                    self.assertNotIn("supersymmetry", {profile.id for profile in profiles()})
                    composition = compose_feature_studio_service_v3(
                        SUITE_ROOT,
                        root / "service",
                        execution_ports={"plan": execution_port},
                    )
                # Host admission belongs to the service environment, even when
                # the caller and transport threads have another default scope.
                try:
                    embedded = FeatureStudioServiceClientV3.embedded(composition)
                    embedded.initialize()
                    self._register(embedded)
                    with self.assertRaises(FeatureStudioServiceV3Error):
                        embedded.submit(
                            request,
                            context_ref_id=self.publication.context_ref.id,
                            input_binding_id=self.publication.input_binding.id,
                        )
                    run_root = root / "run"
                    run_root.mkdir(mode=0o700)
                    with LocalServiceEndpointV3(composition.host, endpoint_path=run_root / "service.sock"):
                        local = LocalServiceClientV3(run_root / "service.sock", composition.authenticator.token)
                        with local.connect() as connection:
                            hosted = FeatureStudioServiceClientV3(
                                composition.registry, connection.call, transport="local-endpoint"
                            )
                            hosted.initialize()
                            with self.assertRaises(FeatureStudioServiceV3Error):
                                hosted.submit(
                                    request,
                                    context_ref_id=self.publication.context_ref.id,
                                    input_binding_id=self.publication.input_binding.id,
                                )
                    execution_port.assert_not_called()
                finally:
                    composition.close()

    def test_profile_owner_readers_recheck_admission_before_cached_code(self) -> None:
        self.assertEqual(material_fluid_feature_profile(SUITE_ROOT)["registry_namespace"], "susy")
        _profile_observation_authority(SUITE_ROOT)
        for policy in (
            {"disabled": ("supersymmetry",)},
            {"disabled": ("cleanroom",)},
            {"unavailable_distributions": ("workbench-crucible",)},
        ):
            with self.subTest(policy=policy), profile_scope(**policy):
                for owner in (_profile_runtime_policy_authority, _profile_observation_authority):
                    with self.assertRaisesRegex(MaterialFluidFlowError, "admitted and enabled"):
                        owner(SUITE_ROOT)
                with self.assertRaisesRegex(MaterialFluidFlowError, "admitted and enabled"):
                    execute_feature_request(SUITE_ROOT, {})

    def test_optional_profile_source_binding_does_not_depend_on_enable_state(self) -> None:
        available = build_feature_studio_registry_v3(SUITE_ROOT)
        with profile_scope(disabled=("supersymmetry",)):
            disabled = build_feature_studio_registry_v3(SUITE_ROOT)
        self.assertEqual(available.source_tree_manifest, disabled.source_tree_manifest)
        profile_prefix = "profiles/packs/supersymmetry/"
        self.assertEqual(
            len([row for row in available.source_tree_manifest["files"] if row["path"].startswith(profile_prefix)]),
            2,
        )
        self.assertEqual("supersymmetry", available.source_tree_manifest["native_profile_owners"][0]["profile_id"])
        original_is_file = Path.is_file
        missing = SUITE_ROOT / profile_prefix / "pyproject.toml"
        with patch.object(Path, "is_file", lambda path: path != missing and original_is_file(path)):
            incomplete = build_feature_studio_registry_v3(SUITE_ROOT)
        self.assertFalse(any(row["path"].startswith(profile_prefix) for row in incomplete.source_tree_manifest["files"]))
        self.assertEqual(
            {row["capability_key"] for row in available.registry["capability_descriptors"]},
            {row["capability_key"] for row in incomplete.registry["capability_descriptors"]},
        )

    def test_cli_submits_the_same_closed_request_to_the_installed_shape(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _owner_plan(workspace)
            request = _request(workspace, "plan")
            context_path = root / "context.json"
            input_path = root / "input.json"
            request_path = root / "request.json"
            context_path.write_text(
                json.dumps(self.publication.context_ref.to_dict()),
                encoding="utf-8",
            )
            input_path.write_text(
                json.dumps(self.publication.input_binding.to_dict()),
                encoding="utf-8",
            )
            request_path.write_text(json.dumps(request), encoding="utf-8")
            with patch(
                "workbench_shell.feature_studio.plan_material_fluid_trial",
                return_value=owner_plan,
            ):
                direct = execute_feature_request(
                    SUITE_ROOT, request, expected_operation="plan"
                )
                composition = compose_feature_studio_service_v3(
                    SUITE_ROOT, (root / "service").resolve()
                )
                run_root = root / "run"
                run_root.mkdir(mode=0o700)
                os.chmod(run_root, 0o700)
                endpoint_path = run_root / "service-v3.sock"
                try:
                    with LocalServiceEndpointV3(
                        composition.host, endpoint_path=endpoint_path
                    ):
                        output = io.StringIO()
                        with redirect_stdout(output):
                            code = shell_main(
                                [
                                    "feature-service",
                                    "submit",
                                    "--suite-root",
                                    str(SUITE_ROOT),
                                    "--endpoint",
                                    str(endpoint_path),
                                    "--credential",
                                    str(composition.authenticator.path),
                                    "--context-ref",
                                    str(context_path),
                                    "--input-binding",
                                    str(input_path),
                                    "--request",
                                    str(request_path),
                                    "--json",
                                ]
                            )
                    self.assertEqual(code, 0)
                    value = json.loads(output.getvalue())
                    wrapper = value["result"]["outcome"]["value"]
                    self.assertEqual(
                        wrapper["owner_result_id"], direct["result_id"]
                    )
                finally:
                    composition.close()

    def test_durable_verify_detaches_resumes_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _owner_plan(workspace)
            request = _request(
                workspace,
                "verify",
                reviewed_plan_id=owner_plan["plan_id"],
            )
            entered = threading.Event()
            release = threading.Event()

            def execute_port(_root, _request, _operation, _context):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test owner release timed out")
                return preview_feature(
                    SUITE_ROOT,
                    workspace,
                    operation="verify",
                    name="Pilot Coolant",
                    color="0x425d73",
                )

            service_root = (root / "service").resolve()
            with patch(
                "workbench_shell.feature_studio.plan_material_fluid_trial",
                return_value=owner_plan,
            ):
                direct = preview_feature(
                    SUITE_ROOT,
                    workspace,
                    operation="verify",
                    name="Pilot Coolant",
                    color="0x425d73",
                )
                composition = compose_feature_studio_service_v3(
                    SUITE_ROOT,
                    service_root,
                    execution_ports={"verify": execute_port},
                )
                try:
                    first = FeatureStudioServiceClientV3.embedded(composition)
                    first.initialize()
                    self._register(first)
                    job = first.submit(
                        request,
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                    )["outcome"]["job"]
                    self.assertTrue(entered.wait(3))
                    subscription = first.subscribe(
                        job,
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                    )["outcome"]["value"]
                    first_page = first.event_page(
                        job,
                        subscription,
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                        last_cursor=-1,
                    )["outcome"]["value"]
                    first_cursor = first_page["events"][-1]["event_ordinal"]

                    resumed_client = FeatureStudioServiceClientV3.embedded(
                        composition
                    )
                    resumed_client.initialize()
                    resumed = resumed_client.subscribe(
                        job,
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                        position={
                            "kind": "resume",
                            "subscription_id": subscription["subscription_id"],
                            "stream_generation": subscription[
                                "stream_generation"
                            ],
                            "last_cursor": first_cursor,
                        },
                    )["outcome"]["value"]
                    self.assertEqual(
                        resumed["subscription_id"],
                        subscription["subscription_id"],
                    )
                    self.assertEqual(resumed["next_cursor"], first_cursor + 1)
                    release.set()
                    resumed_events, final_cursor = (
                        resumed_client.wait_for_terminal(
                            job,
                            resumed,
                            context_ref_id=self.publication.context_ref.id,
                            input_binding_id=self.publication.input_binding.id,
                            timeout_seconds=10,
                            last_cursor=first_cursor,
                        )
                    )
                    combined_ordinals = [
                        row["event_ordinal"] for row in first_page["events"]
                    ] + [row["event_ordinal"] for row in resumed_events]
                    self.assertEqual(
                        combined_ordinals,
                        list(range(final_cursor + 1)),
                    )
                    handle = composition.runtime.store.handle(job["job_id"])
                    deadline = time.monotonic() + 2
                    while (
                        handle.terminal_outcome is None
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                        handle = composition.runtime.store.handle(job["job_id"])
                    self.assertEqual(handle.terminal_outcome, "succeeded")
                    wrapper = resumed_client.result(
                        job["job_id"],
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                    )["outcome"]["value"]
                    self.assertEqual(
                        owner_result_from_feature_service_result_v1(
                            wrapper, suite_root=SUITE_ROOT
                        ),
                        direct,
                    )
                    registry_id = composition.registry["registry_id"]
                finally:
                    release.set()
                    composition.close()

                reopened = compose_feature_studio_service_v3(
                    SUITE_ROOT, service_root
                )
                try:
                    self.assertEqual(reopened.registry["registry_id"], registry_id)
                    restarted_client = FeatureStudioServiceClientV3.embedded(
                        reopened
                    )
                    restarted_client.initialize()
                    restarted_wrapper = restarted_client.result(
                        job["job_id"],
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                    )["outcome"]["value"]
                    self.assertEqual(
                        restarted_wrapper["result_id"], wrapper["result_id"]
                    )
                finally:
                    reopened.close()

    def test_cancellation_is_resumable_and_honest_after_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            request = _request(
                workspace,
                "verify",
                reviewed_plan_id="sha256:" + "1" * 64,
            )
            entered = threading.Event()
            checkpoint = threading.Event()

            def blocking_port(_root, _request, _operation, context):
                entered.set()
                while True:
                    checkpoint.wait(0.05)
                    checkpoint.clear()
                    context.checkpoint("test.feature-studio.blocking-owner")

            composition = compose_feature_studio_service_v3(
                SUITE_ROOT,
                (root / "service").resolve(),
                execution_ports={"verify": blocking_port},
            )
            try:
                first = FeatureStudioServiceClientV3.embedded(composition)
                first.initialize()
                self._register(first)
                job = first.submit(
                    request,
                    context_ref_id=self.publication.context_ref.id,
                    input_binding_id=self.publication.input_binding.id,
                )["outcome"]["job"]
                self.assertTrue(entered.wait(3))
                subscription = first.subscribe(
                    job,
                    context_ref_id=self.publication.context_ref.id,
                    input_binding_id=self.publication.input_binding.id,
                )["outcome"]["value"]
                page = first.event_page(
                    job,
                    subscription,
                    context_ref_id=self.publication.context_ref.id,
                    input_binding_id=self.publication.input_binding.id,
                    last_cursor=-1,
                )["outcome"]["value"]
                head = page["events"][-1]
                self.assertEqual(
                    head["mutation_state"], "external-mutation-started"
                )

                resumed_client = FeatureStudioServiceClientV3.embedded(
                    composition
                )
                resumed_client.initialize()
                resumed = resumed_client.subscribe(
                    job,
                    context_ref_id=self.publication.context_ref.id,
                    input_binding_id=self.publication.input_binding.id,
                    position={
                        "kind": "resume",
                        "subscription_id": subscription["subscription_id"],
                        "stream_generation": subscription["stream_generation"],
                        "last_cursor": head["event_ordinal"],
                    },
                )["outcome"]["value"]
                cancellation = resumed_client.cancel(
                    job,
                    context_ref_id=self.publication.context_ref.id,
                    input_binding_id=self.publication.input_binding.id,
                    expected_event_id=head["event_id"],
                    expected_event_ordinal=head["event_ordinal"],
                    reason="Feature Studio acceptance cancellation",
                )["outcome"]["value"]
                self.assertEqual(
                    cancellation["mutation_state"],
                    "external-mutation-started",
                )
                checkpoint.set()
                events, _ = resumed_client.wait_for_terminal(
                    job,
                    resumed,
                    context_ref_id=self.publication.context_ref.id,
                    input_binding_id=self.publication.input_binding.id,
                    timeout_seconds=5,
                    last_cursor=head["event_ordinal"],
                )
                self.assertEqual(events[-1]["lifecycle_state"], "terminal")
                deadline = time.monotonic() + 2
                handle = composition.runtime.store.handle(job["job_id"])
                while (
                    handle.terminal_outcome is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                    handle = composition.runtime.store.handle(job["job_id"])
                self.assertEqual(
                    handle.terminal_outcome, "cancelled-after-mutation"
                )
                self.assertEqual(
                    handle.mutation_state, "external-mutation-started"
                )
                with self.assertRaises(FeatureStudioServiceV3Error):
                    resumed_client.result(
                        job["job_id"],
                        context_ref_id=self.publication.context_ref.id,
                        input_binding_id=self.publication.input_binding.id,
                    )
            finally:
                checkpoint.set()
                composition.close()


if __name__ == "__main__":
    unittest.main()
