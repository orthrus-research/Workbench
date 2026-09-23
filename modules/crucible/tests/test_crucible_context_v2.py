from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
FIXTURES = ROOT / "modules/crucible/tests/fixtures/crucible-context-v2"
CONTRACT = ROOT / "modules/crucible/contracts/crucible-context-and-input-binding-v2.md"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_context import (  # noqa: E402
    CONTEXT_REF_SCHEMA_ID,
    INPUT_BINDING_SCHEMA_ID,
    ContextAuthorityScope,
    ContextRelationResolvers,
    ValidatedContextRecord,
    ValidatedContextReference,
    assert_context_schema_registry_ready,
    load_context_ref,
    load_input_binding,
    seal_context_ref,
    seal_input_binding,
    validate_context_publication,
)
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id, parse_canonical_json, record_content_id
from workbench_crucible import RecordValidationError


def _external_record(kind: str, semantic_key: str, **fields) -> dict:
    record = {
        "kind": kind,
        "format": f"workbench-context-test-{kind}-v1",
        "canonicalizer": CANONICALIZER_ID,
        "semantic_key": semantic_key,
        **fields,
    }
    record["id"] = record_content_id(record)
    return record


class _Catalog:
    """A deterministic stand-in for the application's pinned external registry."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.scopes: dict[str, ContextAuthorityScope | None] = {}

    def add(
        self,
        kind: str,
        semantic_key: str,
        *,
        scope: ContextAuthorityScope | None = None,
        **fields,
    ) -> str:
        record = _external_record(kind, semantic_key, **fields)
        self.records[record["id"]] = record
        self.scopes[record["id"]] = scope
        return record["id"]

    def set_scope(
        self, record_ids: tuple[str, ...], scope: ContextAuthorityScope
    ) -> None:
        for record_id in record_ids:
            self.scopes[record_id] = scope

    def resolve(self, expectation):
        record = self.records.get(expectation.record_id)
        if record is None:
            return None
        scope = self.scopes[expectation.record_id]
        return ValidatedContextReference(
            record_id=record["id"],
            kind=record["kind"],
            canonical_bytes=canonical_json_bytes(record),
            owner_authority_id=(None if scope is None else scope.owner_authority_id),
            owner_revision_id=(None if scope is None else scope.owner_revision_id),
            authority_adapter_id=(None if scope is None else scope.authority_adapter_id),
        )


def _authority(catalog: _Catalog, key: str) -> ContextAuthorityScope:
    authority_id = catalog.add("authority", f"{key}-authority")
    revision_id = catalog.add("owner-revision", f"{key}-owner-revision")
    adapter_id = catalog.add("authority-adapter", f"{key}-authority-adapter")
    result = ContextAuthorityScope(authority_id, revision_id, adapter_id)
    catalog.set_scope((authority_id, revision_id, adapter_id), result)
    return result


def _binding(scope: ContextAuthorityScope) -> dict:
    return {
        "owner_authority_id": scope.owner_authority_id,
        "owner_revision_id": scope.owner_revision_id,
        "authority_adapter_id": scope.authority_adapter_id,
    }


def _sorted_items(values: list[dict]) -> list[dict]:
    return sorted(values, key=canonical_json_bytes)


class _Fixture:
    def __init__(self) -> None:
        self.catalog = _Catalog()
        c = self.catalog
        self.store_id = c.add("store", "primary-store")
        self.workspace_id = c.add("workspace", "supersymmetry-workspace")
        self.workspace_revision_id = c.add(
            "workspace-registration-revision",
            "supersymmetry-workspace-r4",
            store_id=self.store_id,
            workspace_id=self.workspace_id,
        )

        self.platform_authority = _authority(c, "cleanroom-platform")
        self.platform_profile_id = c.add(
            "platform-profile-revision",
            "cleanroom-0.6.8-alpha-profile",
            scope=self.platform_authority,
        )
        self.platform_adapter_id = c.add(
            "profile-adapter",
            "cleanroom-profile-adapter",
            scope=self.platform_authority,
            applicable_profile_revision_id=self.platform_profile_id,
        )
        self.platform_support_id = c.add(
            "support-decision",
            "cleanroom-tested-support",
            scope=self.platform_authority,
            profile_kind="platform",
            profile_revision_id=self.platform_profile_id,
            profile_adapter_id=self.platform_adapter_id,
            support_state="tested-supported",
            **_binding(self.platform_authority),
        )

        self.pack_authority = _authority(c, "supersymmetry-pack")
        self.pack_profile_id = c.add(
            "pack-profile-revision",
            "supersymmetry-profile-r7",
            scope=self.pack_authority,
        )
        self.pack_adapter_id = c.add(
            "profile-adapter",
            "supersymmetry-profile-adapter",
            scope=self.pack_authority,
            applicable_profile_revision_id=self.pack_profile_id,
        )
        self.pack_support_id = c.add(
            "support-decision",
            "supersymmetry-tested-support",
            scope=self.pack_authority,
            profile_kind="pack",
            profile_revision_id=self.pack_profile_id,
            profile_adapter_id=self.pack_adapter_id,
            support_state="tested-supported",
            **_binding(self.pack_authority),
        )

        lock_scope = {
            "workspace_id": self.workspace_id,
            "platform_profile_revision_id": self.platform_profile_id,
            "pack_profile_revision_id": self.pack_profile_id,
        }
        self.source_lock_id = c.add("source-lock", "source-lock-r3", **lock_scope)
        self.dependency_lock_id = c.add(
            "dependency-lock", "dependency-lock-r9", **lock_scope
        )
        self.artifact_lock_id = c.add(
            "artifact-lock", "artifact-lock-r5", **lock_scope
        )
        self.configuration_lock_id = c.add(
            "configuration-lock", "server-configuration-r11", **lock_scope
        )
        self.runtime_target_id = c.add(
            "runtime-target", "cleanroom-server-target", **lock_scope
        )
        self.installed_runtime_id = c.add(
            "installed-runtime", "cleanroom-server-install-r2", **lock_scope
        )
        self.runtime_epoch_id = c.add(
            "runtime-epoch",
            "runtime-epoch-27",
            workspace_id=self.workspace_id,
            platform_profile_revision_id=self.platform_profile_id,
            pack_profile_revision_id=self.pack_profile_id,
            runtime_target_id=self.runtime_target_id,
            installed_runtime_id=self.installed_runtime_id,
            side="dedicated-server",
            configuration_lock_ids=[self.configuration_lock_id],
        )

        self.world_instance_id = c.add("world-instance", "world-instance-a")
        self.save_lineage_id = c.add(
            "save-lineage",
            "world-a-lineage",
            world_instance_id=self.world_instance_id,
        )
        self.world_creation_receipt_id = c.add(
            "world-creation-receipt",
            "world-a-creation",
            world_instance_id=self.world_instance_id,
            save_lineage_id=self.save_lineage_id,
            runtime_epoch_id=self.runtime_epoch_id,
        )
        self.world_type_id = c.add("world-type", "default-world-type")
        self.generator_id = c.add("world-generator", "cleanroom-world-generator")
        self.generator_configuration_id = c.add(
            "object-descriptor", "world-generator-config-r1"
        )
        self.seed = 8675309
        self.world_epoch_id = c.add(
            "world-epoch",
            "world-a-epoch-4",
            runtime_epoch_id=self.runtime_epoch_id,
            world_instance_id=self.world_instance_id,
            world_creation_receipt_id=self.world_creation_receipt_id,
            save_lineage_id=self.save_lineage_id,
            seed=self.seed,
        )

        self.dimension_registry_id = c.add(
            "dimension-registry", "dimension-registry-r2"
        )
        self.provider_id = c.add("world-provider", "overworld-provider")
        self.dimension_instance_id = c.add(
            "dimension-instance",
            "world-a-overworld-epoch-4",
            world_epoch_id=self.world_epoch_id,
            numeric_dimension_id=0,
            dimension_registry_id=self.dimension_registry_id,
            provider_id=self.provider_id,
        )
        operation_scope = {
            "workspace_id": self.workspace_id,
            "platform_profile_revision_id": self.platform_profile_id,
            "pack_profile_revision_id": self.pack_profile_id,
        }
        self.experiment_id = c.add(
            "experiment", "c02-context-experiment", **operation_scope
        )
        self.fixture_id = c.add("fixture", "c02-context-fixture", **operation_scope)
        self.action_id = c.add(
            "action", "materialize-realized-world", **operation_scope
        )
        self.capture_scope_id = c.add(
            "capture-scope", "overworld-capture", **operation_scope
        )
        self.privacy_class_id = c.add(
            "privacy-class", "local-development", workspace_id=self.workspace_id
        )
        self.resource_budget_id = c.add(
            "resource-budget-class",
            "interactive-materialization",
            workspace_id=self.workspace_id,
        )

        self.context_candidate = {
            "kind": "context-ref",
            "format": "workbench-crucible-context-ref-v2",
            "schema_version": 2,
            "schema_id": CONTEXT_REF_SCHEMA_ID,
            "canonicalizer": CANONICALIZER_ID,
            "store_id": self.store_id,
            "workspace_binding": {
                "workspace_id": self.workspace_id,
                "workspace_registration_revision_id": self.workspace_revision_id,
            },
            "profile_scope": {
                "platform": {
                    "platform_profile_revision_id": self.platform_profile_id,
                    "profile_adapter_id": self.platform_adapter_id,
                    "profile_authority": _binding(self.platform_authority),
                    "support_decision_ids": [self.platform_support_id],
                    "support_state": "tested-supported",
                },
                "pack": {
                    "kind": "selected",
                    "pack_profile_revision_id": self.pack_profile_id,
                    "profile_adapter_id": self.pack_adapter_id,
                    "profile_authority": _binding(self.pack_authority),
                    "support_decision_ids": [self.pack_support_id],
                    "support_state": "tested-supported",
                },
            },
            "source_lock_ids": [self.source_lock_id],
            "dependency_lock_ids": [self.dependency_lock_id],
            "artifact_lock_ids": [self.artifact_lock_id],
            "runtime_scope": {
                "kind": "selected",
                "runtime_target_id": self.runtime_target_id,
                "installed_runtime_id": self.installed_runtime_id,
                "runtime_epoch_id": self.runtime_epoch_id,
                "side": "dedicated-server",
                "configuration_lock_ids": [self.configuration_lock_id],
            },
            "world_scope": {
                "kind": "selected",
                "runtime_epoch_id": self.runtime_epoch_id,
                "world_instance_id": self.world_instance_id,
                "world_creation_receipt_id": self.world_creation_receipt_id,
                "save_lineage_id": self.save_lineage_id,
                "world_epoch_id": self.world_epoch_id,
                "generation": {
                    "kind": "applicable",
                    "seed": self.seed,
                    "world_type_id": self.world_type_id,
                    "generator_id": self.generator_id,
                    "generator_configuration_object_descriptor_id": self.generator_configuration_id,
                },
            },
            "dimension_scope": {
                "kind": "selected",
                "world_epoch_id": self.world_epoch_id,
                "dimension_instance_id": self.dimension_instance_id,
                "numeric_dimension_id": 0,
                "dimension_registry_id": self.dimension_registry_id,
                "provider_id": self.provider_id,
            },
            "region_scope": {
                "kind": "chunk-bounds",
                "dimension_instance_id": self.dimension_instance_id,
                "minimum_chunk_inclusive": {"x": -8, "z": -4},
                "maximum_chunk_exclusive": {"x": 8, "z": 12},
            },
            "operation_scopes": [
                {"scope_kind": "experiment", "scope_id": self.experiment_id},
                {"scope_kind": "fixture", "scope_id": self.fixture_id},
                {"scope_kind": "action", "scope_id": self.action_id},
                {"scope_kind": "capture-scope", "scope_id": self.capture_scope_id},
            ],
            "privacy_class_id": self.privacy_class_id,
            "resource_budget_class_id": self.resource_budget_id,
        }
        self.context = seal_context_ref(self.context_candidate)

        self.evidence_set_id = c.add(
            "evidence-set-revision", "effective-evidence-r8", context_ref_id=self.context.id
        )
        self.graph_id = c.add(
            "graph-revision", "realized-overworld-r3", context_ref_id=self.context.id
        )
        self.graph_set_id = c.add(
            "graph-set-revision", "worldgen-graph-set-r3", context_ref_id=self.context.id
        )
        self.recipe_id = c.add(
            "materialization-recipe", "worldgen-recipe-r6", context_ref_id=self.context.id
        )
        self.schema_uri = "workbench://schemas/example/worldgen-node-v1.schema.json"
        self.schema_descriptor_id = c.add(
            "object-descriptor",
            "worldgen-node-schema-descriptor",
            context_ref_id=self.context.id,
            described_schema_id=self.schema_uri,
        )
        self.ontology_id = c.add(
            "ontology",
            "worldgen-ontology-r4",
            scope=self.pack_authority,
            applicable_profile_revision_id=self.pack_profile_id,
        )
        self.policy_id = c.add(
            "policy",
            "worldgen-partition-policy-r2",
            scope=self.platform_authority,
            applicable_profile_revision_id=self.platform_profile_id,
        )
        self.index_id = c.add(
            "query-index-descriptor",
            "worldgen-query-index-r2",
            context_ref_id=self.context.id,
        )
        self.input_candidate = {
            "kind": "input-binding",
            "format": "workbench-crucible-input-binding-v2",
            "schema_version": 2,
            "schema_id": INPUT_BINDING_SCHEMA_ID,
            "canonicalizer": CANONICALIZER_ID,
            "context_ref_id": self.context.id,
            "evidence_set_bindings": _sorted_items(
                [{"input_key": "effective-evidence", "evidence_set_revision_id": self.evidence_set_id}]
            ),
            "graph_bindings": _sorted_items(
                [{"input_key": "realized-graph", "graph_revision_id": self.graph_id}]
            ),
            "graph_set_bindings": _sorted_items(
                [{"input_key": "worldgen-graphs", "graph_set_revision_id": self.graph_set_id}]
            ),
            "recipe_bindings": _sorted_items(
                [{"input_key": "worldgen-recipe", "materialization_recipe_id": self.recipe_id}]
            ),
            "schema_bindings": _sorted_items(
                [{"input_key": "node-schema", "schema_id": self.schema_uri, "schema_object_descriptor_id": self.schema_descriptor_id}]
            ),
            "ontology_bindings": _sorted_items(
                [{"input_key": "worldgen-ontology", "ontology_id": self.ontology_id, "authority_binding": _binding(self.pack_authority)}]
            ),
            "adapter_bindings": _sorted_items(
                [{"input_key": "pack-profile-adapter", "adapter_kind": "profile-adapter", "adapter_id": self.pack_adapter_id, "authority_binding": _binding(self.pack_authority)}]
            ),
            "policy_bindings": _sorted_items(
                [{"input_key": "partition-policy", "policy_id": self.policy_id, "authority_binding": _binding(self.platform_authority)}]
            ),
            "index_bindings": _sorted_items(
                [{"input_key": "worldgen-index", "query_index_descriptor_id": self.index_id}]
            ),
        }
        self.input_binding = seal_input_binding(self.input_candidate)

    @staticmethod
    def _record(reference: ValidatedContextReference) -> dict:
        return parse_canonical_json(reference.canonical_bytes)

    def validate_support(self, request) -> bool:
        if request.support_state not in {
            "experimental",
            "provisional",
            "tested-supported",
            "unsupported",
        }:
            return False
        revision = request.external_references.get(request.profile_revision_id)
        adapter = request.external_references.get(request.profile_adapter_id)
        if revision is None or adapter is None:
            return False
        for decision_id in request.support_decision_ids:
            decision = request.external_references.get(decision_id)
            if decision is None:
                return False
            value = self._record(decision)
            if value.get("profile_kind") != request.profile_kind:
                return False
            if value.get("profile_revision_id") != request.profile_revision_id:
                return False
            if value.get("profile_adapter_id") != request.profile_adapter_id:
                return False
            if value.get("support_state") != request.support_state:
                return False
            if any(
                value.get(field) != getattr(request.authority_scope, field)
                for field in (
                    "owner_authority_id",
                    "owner_revision_id",
                    "authority_adapter_id",
                )
            ):
                return False
        return True

    def validate_ancestry(self, request) -> bool:
        context = request.context_ref.to_dict()
        refs = request.external_references
        runtime = context["runtime_scope"]
        if runtime["kind"] == "none":
            return context["world_scope"]["kind"] == "none"
        runtime_record = self._record(refs[runtime["runtime_epoch_id"]])
        pack = context["profile_scope"]["pack"]
        expected_pack = None if pack["kind"] == "none" else pack["pack_profile_revision_id"]
        if any(
            runtime_record.get(field) != expected
            for field, expected in (
                ("workspace_id", context["workspace_binding"]["workspace_id"]),
                ("platform_profile_revision_id", context["profile_scope"]["platform"]["platform_profile_revision_id"]),
                ("pack_profile_revision_id", expected_pack),
                ("runtime_target_id", runtime["runtime_target_id"]),
                ("installed_runtime_id", runtime["installed_runtime_id"]),
                ("side", runtime["side"]),
                ("configuration_lock_ids", runtime["configuration_lock_ids"]),
            )
        ):
            return False
        world = context["world_scope"]
        if world["kind"] == "none":
            return context["dimension_scope"]["kind"] == "none"
        world_epoch = self._record(refs[world["world_epoch_id"]])
        creation = self._record(refs[world["world_creation_receipt_id"]])
        lineage = self._record(refs[world["save_lineage_id"]])
        for record in (world_epoch, creation):
            if record.get("runtime_epoch_id") != runtime["runtime_epoch_id"]:
                return False
            if record.get("world_instance_id") != world["world_instance_id"]:
                return False
            if record.get("save_lineage_id") != world["save_lineage_id"]:
                return False
        if lineage.get("world_instance_id") != world["world_instance_id"]:
            return False
        if world_epoch.get("world_creation_receipt_id") != world["world_creation_receipt_id"]:
            return False
        dimension = context["dimension_scope"]
        if dimension["kind"] == "none":
            return context["region_scope"]["kind"] == "none"
        dimension_record = self._record(refs[dimension["dimension_instance_id"]])
        return all(
            dimension_record.get(field) == dimension[field]
            for field in (
                "world_epoch_id",
                "numeric_dimension_id",
                "dimension_registry_id",
                "provider_id",
            )
        )

    def validate_context_binding(self, request) -> bool:
        context = request.context_ref.to_dict()
        refs = request.external_references
        workspace = context["workspace_binding"]
        registration = self._record(
            refs[workspace["workspace_registration_revision_id"]]
        )
        if registration.get("store_id") != context["store_id"]:
            return False
        if registration.get("workspace_id") != workspace["workspace_id"]:
            return False
        platform_id = context["profile_scope"]["platform"][
            "platform_profile_revision_id"
        ]
        pack = context["profile_scope"]["pack"]
        pack_id = None if pack["kind"] == "none" else pack["pack_profile_revision_id"]
        exact_scope = {
            "workspace_id": workspace["workspace_id"],
            "platform_profile_revision_id": platform_id,
            "pack_profile_revision_id": pack_id,
        }
        lock_ids = [
            *context["source_lock_ids"],
            *context["dependency_lock_ids"],
            *context["artifact_lock_ids"],
        ]
        runtime = context["runtime_scope"]
        if runtime["kind"] == "selected":
            lock_ids.extend(runtime["configuration_lock_ids"])
            for field in ("runtime_target_id", "installed_runtime_id"):
                value = self._record(refs[runtime[field]])
                if any(value.get(key) != expected for key, expected in exact_scope.items()):
                    return False
        for lock_id in lock_ids:
            value = self._record(refs[lock_id])
            if any(value.get(key) != expected for key, expected in exact_scope.items()):
                return False
        for operation in context["operation_scopes"]:
            value = self._record(refs[operation["scope_id"]])
            if any(value.get(key) != expected for key, expected in exact_scope.items()):
                return False
        for field in ("privacy_class_id", "resource_budget_class_id"):
            value = self._record(refs[context[field]])
            if value.get("workspace_id") != workspace["workspace_id"]:
                return False
        return True

    def validate_inputs(self, request) -> bool:
        context_id = request.context_ref.id
        value = request.input_binding.to_dict()
        if value["context_ref_id"] != context_id:
            return False
        identity_fields = {
            "evidence_set_bindings": "evidence_set_revision_id",
            "graph_bindings": "graph_revision_id",
            "graph_set_bindings": "graph_set_revision_id",
            "recipe_bindings": "materialization_recipe_id",
            "schema_bindings": "schema_object_descriptor_id",
            "index_bindings": "query_index_descriptor_id",
        }
        for array_name, field in identity_fields.items():
            for item in value[array_name]:
                reference = request.external_references.get(item[field])
                if reference is None:
                    return False
                if self._record(reference).get("context_ref_id") != context_id:
                    return False
        profiles = request.context_ref.to_dict()["profile_scope"]
        pack = profiles["pack"]
        pack_profile_id = (
            None if pack["kind"] == "none" else pack["pack_profile_revision_id"]
        )
        for item in value["ontology_bindings"]:
            record = self._record(request.external_references[item["ontology_id"]])
            if record.get("applicable_profile_revision_id") != pack_profile_id:
                return False
        platform_profile_id = profiles["platform"]["platform_profile_revision_id"]
        for item in value["policy_bindings"]:
            record = self._record(request.external_references[item["policy_id"]])
            if record.get("applicable_profile_revision_id") != platform_profile_id:
                return False
        for item in value["adapter_bindings"]:
            record = self._record(request.external_references[item["adapter_id"]])
            if item["adapter_kind"] == "profile-adapter":
                authority = item["authority_binding"]
                if authority == profiles["platform"]["profile_authority"]:
                    expected_profile_id = platform_profile_id
                elif pack["kind"] == "selected" and authority == pack["profile_authority"]:
                    expected_profile_id = pack_profile_id
                else:
                    return False
                if record.get("applicable_profile_revision_id") != expected_profile_id:
                    return False
        return True

    def relations(self, **overrides) -> ContextRelationResolvers:
        values = {
            "reference_resolver": self.catalog.resolve,
            "support_validator": self.validate_support,
            "context_binding_validator": self.validate_context_binding,
            "ancestry_validator": self.validate_ancestry,
            "input_binding_validator": self.validate_inputs,
        }
        values.update(overrides)
        return ContextRelationResolvers(**values)


class CrucibleContextV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()

    def assertDiagnostic(self, error, phase: str, code: str) -> None:
        actual = {(item.phase.value, item.code) for item in error.diagnostics}
        self.assertIn((phase, code), actual, actual)

    def test_registry_is_finite_closed_and_separate_from_c01(self) -> None:
        self.assertEqual(
            (
                CONTEXT_REF_SCHEMA_ID,
                "workbench://schemas/crucible/crucible-context-v2-common.schema.json",
                INPUT_BINDING_SCHEMA_ID,
            ),
            assert_context_schema_registry_ready(),
        )

    def test_exact_identity_oracle_and_canonical_round_trip(self) -> None:
        oracle = json.loads((FIXTURES / "identity-oracle.json").read_text("utf-8"))
        self.assertEqual(oracle["context_ref_id"], self.fixture.context.id)
        self.assertEqual(oracle["input_binding_id"], self.fixture.input_binding.id)
        self.assertEqual(
            self.fixture.context.canonical_bytes,
            load_context_ref(self.fixture.context.canonical_bytes).canonical_bytes,
        )
        self.assertEqual(
            self.fixture.input_binding.canonical_bytes,
            load_input_binding(self.fixture.input_binding.canonical_bytes).canonical_bytes,
        )

    def test_contract_identity_domain_matches_the_executable_c01_domain(self) -> None:
        contract = CONTRACT.read_text(encoding="utf-8")
        self.assertIn(
            'sha256("workbench-content-v2\\n" || kind || "\\n" || canonical-body)',
            contract,
        )
        self.assertNotIn("workbench/crucible/content-id/v2", contract)

    def test_valid_context_publication_resolves_every_exact_input(self) -> None:
        context, binding = validate_context_publication(
            self.fixture.context,
            self.fixture.input_binding,
            relations=self.fixture.relations(),
        )
        self.assertEqual(self.fixture.context.id, context.id)
        self.assertEqual(self.fixture.input_binding.id, binding.id)

    def test_unknown_context_member_is_rejected_before_identity(self) -> None:
        candidate = deepcopy(self.fixture.context_candidate)
        candidate["workspace_label"] = "supersymmetry"
        with mock.patch(
            "workbench_crucible_context.records.record_content_id",
            side_effect=AssertionError("identity must not run"),
        ) as identity:
            with self.assertRaises(RecordValidationError) as caught:
                seal_context_ref(candidate)
        identity.assert_not_called()
        self.assertDiagnostic(caught.exception, "schema", "schema.additionalProperties")

    def test_service_availability_cannot_be_encoded_as_profile_support(self) -> None:
        candidate = deepcopy(self.fixture.context_candidate)
        candidate["profile_scope"]["platform"]["support_state"] = "unavailable"
        with self.assertRaises(RecordValidationError) as caught:
            seal_context_ref(candidate)
        self.assertDiagnostic(caught.exception, "schema", "schema.enum")

    def test_publication_ports_are_mandatory_and_fail_closed(self) -> None:
        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                self.fixture.context,
                self.fixture.input_binding,
                relations=ContextRelationResolvers(),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.context-reference-port"
        )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.profile-support-port"
        )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.context-binding-port"
        )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.context-ancestry-port"
        )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.input-binding-applicability-port",
        )

    def test_callback_exceptions_become_typed_relation_diagnostics(self) -> None:
        def fail(_request):
            raise RuntimeError("host callback detail must not escape")

        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                self.fixture.context,
                self.fixture.input_binding,
                relations=self.fixture.relations(
                    support_validator=fail,
                    context_binding_validator=fail,
                    ancestry_validator=fail,
                    input_binding_validator=fail,
                ),
            )
        for code in (
            "relation.profile-support-failure",
            "relation.context-binding-failure",
            "relation.context-ancestry-failure",
            "relation.input-binding-applicability-failure",
        ):
            self.assertDiagnostic(caught.exception, "relation", code)
        self.assertNotIn("host callback detail", str(caught.exception))

    def test_hostile_resolver_metadata_cannot_escape_shape_validation(self) -> None:
        class EqualityBomb(str):
            def __eq__(self, other):
                raise RuntimeError("hostile equality")

        def hostile_resolver(expectation):
            resolved = self.fixture.catalog.resolve(expectation)
            if resolved is None:
                return None
            return replace(resolved, record_id=EqualityBomb(resolved.record_id))

        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                self.fixture.context,
                self.fixture.input_binding,
                relations=self.fixture.relations(
                    reference_resolver=hostile_resolver,
                    support_validator=lambda request: True,
                    context_binding_validator=lambda request: True,
                    ancestry_validator=lambda request: True,
                    input_binding_validator=lambda request: True,
                ),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.context-reference-invalid"
        )

    def test_reference_resolver_cannot_mutate_its_expectation(self) -> None:
        replacement = self.fixture.catalog.add(
            "source-lock",
            "different-source-lock",
            workspace_id=self.fixture.workspace_id,
            platform_profile_revision_id=self.fixture.platform_profile_id,
            pack_profile_revision_id=self.fixture.pack_profile_id,
        )

        def mutating_resolver(expectation):
            if expectation.record_id == self.fixture.source_lock_id:
                object.__setattr__(expectation, "record_id", replacement)
            return self.fixture.catalog.resolve(expectation)

        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                self.fixture.context,
                self.fixture.input_binding,
                relations=self.fixture.relations(
                    reference_resolver=mutating_resolver
                ),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.context-reference-mutation",
        )

    def test_validation_ports_cannot_mutate_isolated_context_records(self) -> None:
        def mutating_validator(request):
            object.__setattr__(
                request.context_ref,
                "id",
                "context-ref:sha256:" + "f" * 64,
            )
            return True

        cases = (
            ("support_validator", "relation.profile-support-mutation"),
            (
                "context_binding_validator",
                "relation.context-binding-mutation",
            ),
            ("ancestry_validator", "relation.context-ancestry-mutation"),
            (
                "input_binding_validator",
                "relation.input-binding-applicability-mutation",
            ),
        )
        for port, code in cases:
            with self.subTest(port=port):
                with self.assertRaises(RecordValidationError) as caught:
                    validate_context_publication(
                        self.fixture.context,
                        self.fixture.input_binding,
                        relations=self.fixture.relations(
                            **{port: mutating_validator}
                        ),
                    )
                self.assertDiagnostic(caught.exception, "relation", code)

        self.assertEqual(
            self.fixture.context.id,
            load_context_ref(self.fixture.context.canonical_bytes).id,
        )

    def test_validated_record_convenience_metadata_is_rederived_from_bytes(self) -> None:
        forged_references = replace(self.fixture.context, references=())
        self.assertIs(type(forged_references), ValidatedContextRecord)
        with self.assertRaisesRegex(ValueError, "references disagree"):
            validate_context_publication(
                forged_references,
                self.fixture.input_binding,
                relations=self.fixture.relations(),
            )

        class StringSubclass(str):
            pass

        forged_id = replace(
            self.fixture.context, id=StringSubclass(self.fixture.context.id)
        )
        with self.assertRaisesRegex(TypeError, "metadata is malformed"):
            validate_context_publication(
                forged_id,
                self.fixture.input_binding,
                relations=self.fixture.relations(),
            )

    def test_support_decisions_require_exact_authority_and_applicability(self) -> None:
        with self.assertRaises(RecordValidationError) as rejected:
            validate_context_publication(
                self.fixture.context,
                self.fixture.input_binding,
                relations=self.fixture.relations(support_validator=lambda request: False),
            )
        self.assertDiagnostic(rejected.exception, "relation", "relation.profile-support")

        original_scope = self.fixture.catalog.scopes[self.fixture.platform_support_id]
        self.fixture.catalog.scopes[self.fixture.platform_support_id] = self.fixture.pack_authority
        try:
            with self.assertRaises(RecordValidationError) as wrong_scope:
                validate_context_publication(
                    self.fixture.context,
                    self.fixture.input_binding,
                    relations=self.fixture.relations(),
                )
            self.assertDiagnostic(
                wrong_scope.exception,
                "relation",
                "relation.context-reference-authority",
            )
        finally:
            self.fixture.catalog.scopes[self.fixture.platform_support_id] = original_scope

    def test_runtime_world_dimension_and_region_parent_ids_are_exact(self) -> None:
        cases = (
            ("world_scope", "runtime_epoch_id", "runtime-epoch", "semantic.world-runtime-ancestry"),
            ("dimension_scope", "world_epoch_id", "world-epoch", "semantic.dimension-world-ancestry"),
            ("region_scope", "dimension_instance_id", "dimension-instance", "semantic.region-dimension-ancestry"),
        )
        for scope_name, field, kind, code in cases:
            with self.subTest(scope=scope_name):
                candidate = deepcopy(self.fixture.context_candidate)
                candidate[scope_name][field] = content_id(kind, f"foreign-{scope_name}")
                with self.assertRaises(RecordValidationError) as caught:
                    seal_context_ref(candidate)
                self.assertDiagnostic(caught.exception, "semantic", code)

    def test_external_ancestry_record_mismatch_is_rejected(self) -> None:
        bad_dimension_id = self.fixture.catalog.add(
            "dimension-instance",
            "foreign-world-overworld",
            world_epoch_id=content_id("world-epoch", "foreign-world"),
            numeric_dimension_id=0,
            dimension_registry_id=self.fixture.dimension_registry_id,
            provider_id=self.fixture.provider_id,
        )
        candidate = deepcopy(self.fixture.context_candidate)
        candidate["dimension_scope"]["dimension_instance_id"] = bad_dimension_id
        candidate["region_scope"]["dimension_instance_id"] = bad_dimension_id
        context = seal_context_ref(candidate)
        binding_candidate = deepcopy(self.fixture.input_candidate)
        binding_candidate["context_ref_id"] = context.id
        binding = seal_input_binding(binding_candidate)
        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                context,
                binding,
                relations=self.fixture.relations(input_binding_validator=lambda request: True),
            )
        self.assertDiagnostic(caught.exception, "relation", "relation.context-ancestry")

    def test_whole_context_binding_rejects_workspace_registration_and_lock_drift(self) -> None:
        foreign_store = content_id("store", "foreign-store")
        wrong_registration = self.fixture.catalog.add(
            "workspace-registration-revision",
            "workspace-registration-wrong-store",
            store_id=foreign_store,
            workspace_id=self.fixture.workspace_id,
        )
        wrong_lock = self.fixture.catalog.add(
            "source-lock",
            "source-lock-wrong-workspace",
            workspace_id=content_id("workspace", "foreign-workspace"),
            platform_profile_revision_id=self.fixture.platform_profile_id,
            pack_profile_revision_id=self.fixture.pack_profile_id,
        )
        cases = {
            "workspace-registration": (
                "/workspace_binding/workspace_registration_revision_id",
                wrong_registration,
            ),
            "lock-applicability": ("/source_lock_ids/0", wrong_lock),
        }
        for name, (path, replacement) in cases.items():
            with self.subTest(case=name):
                candidate = deepcopy(self.fixture.context_candidate)
                if path.startswith("/workspace_binding"):
                    candidate["workspace_binding"][
                        "workspace_registration_revision_id"
                    ] = replacement
                else:
                    candidate["source_lock_ids"][0] = replacement
                context = seal_context_ref(candidate)
                binding_candidate = deepcopy(self.fixture.input_candidate)
                binding_candidate["context_ref_id"] = context.id
                binding = seal_input_binding(binding_candidate)
                with self.assertRaises(RecordValidationError) as caught:
                    validate_context_publication(
                        context,
                        binding,
                        relations=self.fixture.relations(
                            input_binding_validator=lambda request: True
                        ),
                    )
                self.assertDiagnostic(
                    caught.exception, "relation", "relation.context-binding"
                )

    def test_region_bounds_are_explicitly_half_open_and_nonempty(self) -> None:
        candidate = deepcopy(self.fixture.context_candidate)
        candidate["region_scope"]["maximum_chunk_exclusive"]["x"] = -8
        with self.assertRaises(RecordValidationError) as caught:
            seal_context_ref(candidate)
        self.assertDiagnostic(caught.exception, "semantic", "semantic.region-bounds")

    def test_identity_sets_and_binding_sets_are_canonically_ordered(self) -> None:
        context_candidate = deepcopy(self.fixture.context_candidate)
        extra_lock = content_id("source-lock", "a-lock")
        context_candidate["source_lock_ids"] = [self.fixture.source_lock_id, extra_lock]
        if context_candidate["source_lock_ids"] == sorted(
            context_candidate["source_lock_ids"], key=lambda value: value.encode("utf-8")
        ):
            context_candidate["source_lock_ids"].reverse()
        with self.assertRaises(RecordValidationError) as context_error:
            seal_context_ref(context_candidate)
        self.assertDiagnostic(
            context_error.exception, "semantic", "semantic.context-set-order"
        )

        input_candidate = deepcopy(self.fixture.input_candidate)
        input_candidate["graph_bindings"] = [
            {"input_key": "z-graph", "graph_revision_id": self.fixture.graph_id},
            {"input_key": "a-graph", "graph_revision_id": self.fixture.graph_id},
        ]
        with self.assertRaises(RecordValidationError) as input_error:
            seal_input_binding(input_candidate)
        self.assertDiagnostic(
            input_error.exception, "semantic", "semantic.input-binding-order"
        )

    def test_input_keys_are_globally_unique(self) -> None:
        candidate = deepcopy(self.fixture.input_candidate)
        candidate["graph_bindings"][0]["input_key"] = candidate[
            "evidence_set_bindings"
        ][0]["input_key"]
        with self.assertRaises(RecordValidationError) as caught:
            seal_input_binding(candidate)
        self.assertDiagnostic(caught.exception, "semantic", "semantic.input-key")

    def test_cross_context_input_reference_is_rejected_by_applicability_port(self) -> None:
        foreign_context_id = content_id("context-ref", "foreign-context")
        foreign_graph_id = self.fixture.catalog.add(
            "graph-revision", "foreign-context-graph", context_ref_id=foreign_context_id
        )
        candidate = deepcopy(self.fixture.input_candidate)
        candidate["graph_bindings"][0]["graph_revision_id"] = foreign_graph_id
        binding = seal_input_binding(candidate)
        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                self.fixture.context,
                binding,
                relations=self.fixture.relations(),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.input-binding-applicability",
        )

    def test_authority_bound_inputs_require_exact_profile_applicability(self) -> None:
        foreign_profile_id = content_id(
            "pack-profile-revision", "foreign-applicability-profile"
        )
        cases = {
            "ontology": (
                "ontology_bindings",
                "ontology_id",
                self.fixture.catalog.add(
                    "ontology",
                    "foreign-profile-ontology",
                    scope=self.fixture.pack_authority,
                    applicable_profile_revision_id=foreign_profile_id,
                ),
            ),
            "policy": (
                "policy_bindings",
                "policy_id",
                self.fixture.catalog.add(
                    "policy",
                    "foreign-profile-policy",
                    scope=self.fixture.platform_authority,
                    applicable_profile_revision_id=foreign_profile_id,
                ),
            ),
            "adapter": (
                "adapter_bindings",
                "adapter_id",
                self.fixture.catalog.add(
                    "profile-adapter",
                    "foreign-profile-adapter",
                    scope=self.fixture.pack_authority,
                    applicable_profile_revision_id=foreign_profile_id,
                ),
            ),
        }
        for name, (array_name, field, record_id) in cases.items():
            with self.subTest(kind=name):
                candidate = deepcopy(self.fixture.input_candidate)
                candidate[array_name][0][field] = record_id
                binding = seal_input_binding(candidate)
                with self.assertRaises(RecordValidationError) as caught:
                    validate_context_publication(
                        self.fixture.context,
                        binding,
                        relations=self.fixture.relations(),
                    )
                self.assertDiagnostic(
                    caught.exception,
                    "relation",
                    "relation.input-binding-applicability",
                )

    def test_cross_scope_identity_isolation_and_same_seed_different_world(self) -> None:
        base_id = self.fixture.context.id
        oracle = json.loads((FIXTURES / "identity-oracle.json").read_text("utf-8"))[
            "isolation_context_ids"
        ]
        cases: dict[str, dict] = {}

        workspace = deepcopy(self.fixture.context_candidate)
        workspace["workspace_binding"]["workspace_id"] = content_id(
            "workspace", "other-workspace"
        )
        cases["cross_workspace"] = workspace

        profile = deepcopy(self.fixture.context_candidate)
        profile["profile_scope"]["platform"]["platform_profile_revision_id"] = content_id(
            "platform-profile-revision", "other-platform-profile"
        )
        cases["cross_profile"] = profile

        runtime = deepcopy(self.fixture.context_candidate)
        new_runtime_epoch = content_id("runtime-epoch", "other-runtime-epoch")
        runtime["runtime_scope"]["runtime_epoch_id"] = new_runtime_epoch
        runtime["world_scope"]["runtime_epoch_id"] = new_runtime_epoch
        cases["cross_runtime"] = runtime

        world = deepcopy(self.fixture.context_candidate)
        new_world_epoch = content_id("world-epoch", "other-world-same-seed")
        new_dimension = content_id("dimension-instance", "other-world-overworld")
        world["world_scope"].update(
            {
                "world_instance_id": content_id("world-instance", "other-world"),
                "world_creation_receipt_id": content_id(
                    "world-creation-receipt", "other-world-creation"
                ),
                "save_lineage_id": content_id("save-lineage", "other-world-lineage"),
                "world_epoch_id": new_world_epoch,
            }
        )
        world["dimension_scope"]["world_epoch_id"] = new_world_epoch
        world["dimension_scope"]["dimension_instance_id"] = new_dimension
        world["region_scope"]["dimension_instance_id"] = new_dimension
        self.assertEqual(
            self.fixture.context_candidate["world_scope"]["generation"]["seed"],
            world["world_scope"]["generation"]["seed"],
        )
        cases["same_seed_different_world"] = world

        dimension = deepcopy(self.fixture.context_candidate)
        new_dimension = content_id("dimension-instance", "nether-instance")
        dimension["dimension_scope"]["dimension_instance_id"] = new_dimension
        dimension["dimension_scope"]["numeric_dimension_id"] = -1
        dimension["region_scope"]["dimension_instance_id"] = new_dimension
        cases["cross_dimension"] = dimension

        for name, candidate in cases.items():
            with self.subTest(scope=name):
                isolated = seal_context_ref(candidate)
                self.assertNotEqual(base_id, isolated.id)
                self.assertEqual(oracle[name], isolated.id)
                with self.assertRaises(RecordValidationError) as caught:
                    validate_context_publication(
                        isolated,
                        self.fixture.input_binding,
                        relations=self.fixture.relations(),
                    )
                self.assertDiagnostic(caught.exception, "relation", "relation.input-context")

    def test_same_label_cannot_replace_exact_world_identity(self) -> None:
        candidate = deepcopy(self.fixture.context_candidate)
        candidate["world_scope"].pop("world_instance_id")
        candidate["world_scope"]["world_label"] = "world"
        with self.assertRaises(RecordValidationError) as caught:
            seal_context_ref(candidate)
        self.assertDiagnostic(caught.exception, "schema", "schema.oneOf")

    def test_input_binding_context_must_equal_exact_context_id(self) -> None:
        candidate = deepcopy(self.fixture.input_candidate)
        candidate["context_ref_id"] = content_id("context-ref", "other-context")
        binding = seal_input_binding(candidate)
        with self.assertRaises(RecordValidationError) as caught:
            validate_context_publication(
                self.fixture.context,
                binding,
                relations=self.fixture.relations(),
            )
        self.assertDiagnostic(caught.exception, "relation", "relation.input-context")


if __name__ == "__main__":
    unittest.main()
