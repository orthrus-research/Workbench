from __future__ import annotations

from workbench_crucible_service import DurableJobStore

from copy import deepcopy
from dataclasses import dataclass, replace
from io import BytesIO, StringIO
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest


ROOT = Path(__file__).resolve().parents[3]
CRUCIBLE_ROOT = ROOT / "modules/crucible"
SHELL_ROOT = ROOT / "modules/workbench-shell"
for source in (
    ROOT / "modules/runtime-explorer/src",
    ROOT / "modules/project-intelligence/src",
    SHELL_ROOT / "src",
    ROOT / "modules/atlas/src",
    CRUCIBLE_ROOT / "src",
):
    sys.path.insert(0, str(source))

import workbench_runtime_explorer as explorer_package  # noqa: E402
from workbench_crucible_context import (  # noqa: E402
    CONTEXT_REF_SCHEMA_ID,
    INPUT_BINDING_SCHEMA_ID,
    seal_context_ref,
    seal_input_binding,
)
from workbench_api.service import ServiceHandlerRegistration
from workbench_core.service.runtime import ServiceRuntimeV3
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id, parse_canonical_json
from workbench_crucible_observatory import (  # noqa: E402
    BundleBuilder,
    exact_actor,
    new_run,
    validate_bundle,
)
from workbench_crucible_worldgen import (  # noqa: E402
    WorldgenGraphStore,
    WorldgenStoredQueryService,
    WorldStudioProvingViewError,
    WorldStudioProvingViewHandler,
    validate_world_studio_proving_result,
)
from workbench_runtime_explorer.cli import main as explorer_main  # noqa: E402
from workbench_runtime_explorer.graph_query import (  # noqa: E402
    EmbeddedGraphQueryPresenterV2,
    EmbeddedGraphQueryResultV2,
    MAX_GRAPH_RESULT_BYTES,
    MAX_GRAPH_RESULT_DEPTH,
    MAX_GRAPH_RESULT_RECORDS,
    MAX_GRAPH_ROW_BYTES,
    MAX_GRAPH_TERMINAL_BYTES,
    _bound_request,
    _validated_owner_result,
    execute_embedded_graph_query_v2,
    graph_query_presenter_manifest_v2,
    render_embedded_graph_query_result_v2,
)
from workbench_runtime_explorer.model import ExplorerError  # noqa: E402
from workbench_runtime_explorer.providers import receipt_provider  # noqa: E402
from workbench_core.service.host import (  # noqa: E402
    posix_service_physical_lease_ports,
)
from workbench_shell.world_studio_registry import (  # noqa: E402
    build_world_studio_presenter_binding,
    build_world_studio_registry_v3,
    world_studio_service_registration,
)
from workbench_crucible_worldgen.registry_contract import WorldStudioPresenterBindingV3


QUERY_CASES = (
    {"query": "capture-health"},
    {"query": "known-absence"},
    {"query": "stale-pattern-conflicts"},
    {"category_id": "atlas.worldgen.realized.v1", "query": "drilldown"},
    {
        "category_id": "crucible.worldgen.occurrence.v1",
        "chunk": {"x": 3, "z": -2},
        "query": "site",
        "resolution_id": "worldgen.exact.v1",
    },
)

EVIDENCE_ROOT = ROOT / ".workbench/evidence/stage3/worldgen-w01-20260813"
ENVELOPE_PATH = (
    ROOT
    / ".workbench/iterations/worldgen/"
    "stage3-w01-proof-a-20260813/artifacts/worldgen-v2/"
    "execution-envelope-v2.json"
)
EXPECTED_W01_PROOF_ID = (
    "worldgen-w01-proof-index:sha256:"
    "2eb19f5d8f03b1bbdf9e9cf48ea9c7ae1c47663f6264f8bcf61badf351a38b92"
)
EXPECTED_W01_GRAPH_ID = (
    "graph-set-revision:sha256:"
    "276ffd93a9db8bcdcb1295e172f4f79d024d0d958ff2fd4278075ecf43e20881"
)


def _external(kind: str, key: str, **fields: object) -> str:
    value = {
        "canonicalizer": CANONICALIZER_ID,
        "format": "workbench-runtime-explorer-native-fixture-v1",
        "kind": kind,
        "semantic_key": key,
        **fields,
    }
    value["id"] = content_id(kind, value)
    return str(value["id"])


def _authority(key: str) -> dict[str, str]:
    return {
        "authority_adapter_id": _external("authority-adapter", f"{key}-adapter"),
        "owner_authority_id": _external("authority", f"{key}-authority"),
        "owner_revision_id": _external("owner-revision", f"{key}-revision"),
    }


def _query_key(query: object) -> bytes:
    return canonical_json_bytes(query)


@dataclass(frozen=True, slots=True)
class _DirectContext:
    context_ref_id: str
    input_binding_id: str
    context_bytes: bytes
    binding_bytes: bytes

    def exact_context_bytes(self) -> tuple[bytes, bytes]:
        return self.context_bytes, self.binding_bytes


@dataclass(frozen=True, slots=True)
class _SyntheticV01:
    context: object
    binding: object
    proof: dict
    action: dict
    graph_set_id: str
    query_results: dict[bytes, dict]
    handler: WorldStudioProvingViewHandler

    def request(self, query: dict) -> dict:
        return {
            "action_gate_receipt_id": self.action["id"],
            "comparison_graph_set_revision_id": None,
            "comparison_query": None,
            "context_ref_id": self.context.id,
            "format": "workbench-world-studio-proving-request-v1",
            "graph_set_revision_id": self.graph_set_id,
            "input_binding_id": self.binding.id,
            "operation": "query",
            "proof_index_id": self.proof["id"],
            "query": deepcopy(query),
            "schema_version": 1,
        }

    def result(self, query: dict) -> dict:
        context = _DirectContext(
            self.context.id,
            self.binding.id,
            self.context.canonical_bytes,
            self.binding.canonical_bytes,
        )
        return self.handler(context, self.request(query))


def _record_for_query(query: dict, index: int) -> dict:
    query_kind = query["query"]
    categories = {
        "capture-health": "crucible.worldgen.capture-health.v1",
        "known-absence": "crucible.worldgen.occurrence.v1",
        "stale-pattern-conflicts": "atlas.worldgen.generative.v1",
        "drilldown": "atlas.worldgen.realized.v1",
        "site": "crucible.worldgen.occurrence.v1",
    }
    logical_key = f"fixture.{query_kind}.{index}"
    if query_kind == "stale-pattern-conflicts":
        logical_key += "\x1b]8;;https://invalid.example\x07unsafe\x1b]8;;\x07\nnext"
    return {
        "category_id": categories[query_kind],
        "graph_record_id": _external("graph-record", f"{query_kind}-{index}"),
        "graph_revision_id": _external("graph-revision", query_kind),
        "identity": {
            "query_kind": query_kind,
            "site_state": (
                "known-absent" if query_kind == "known-absence" else "available"
            ),
        },
        "logical_key": logical_key,
        "record_kind": (
            "refinement-recipe" if query_kind == "drilldown" else "node"
        ),
        "resolution_id": "worldgen.exact.v1",
        "subject_id": _external("worldgen-subject", f"{query_kind}-{index}"),
    }


def _query_result(graph_set_id: str, query: dict) -> dict:
    row_count = 2 if query["query"] == "known-absence" else 1
    body = {
        "format": "workbench-worldgen-query-result-v1",
        "graph_set_revision_id": graph_set_id,
        "kind": "worldgen-query-result",
        "query": deepcopy(query),
        "raw_archive_open_count": 0,
        "results": [_record_for_query(query, index) for index in range(row_count)],
        "schema_version": 1,
        "truncated": query["query"] == "site",
    }
    body["id"] = content_id("worldgen-query-result", body)
    return body


def _synthetic_v01() -> _SyntheticV01:
    store_id = _external("store", "native-store")
    workspace_id = _external("workspace", "native-workspace")
    context = seal_context_ref(
        {
            "kind": "context-ref",
            "format": "workbench-crucible-context-ref-v2",
            "schema_version": 2,
            "schema_id": CONTEXT_REF_SCHEMA_ID,
            "canonicalizer": CANONICALIZER_ID,
            "store_id": store_id,
            "workspace_binding": {
                "workspace_id": workspace_id,
                "workspace_registration_revision_id": _external(
                    "workspace-registration-revision",
                    "native-workspace-r1",
                    store_id=store_id,
                    workspace_id=workspace_id,
                ),
            },
            "profile_scope": {
                "platform": {
                    "platform_profile_revision_id": _external(
                        "platform-profile-revision", "native-cleanroom"
                    ),
                    "profile_adapter_id": _external(
                        "profile-adapter", "native-cleanroom"
                    ),
                    "profile_authority": _authority("cleanroom"),
                    "support_decision_ids": [
                        _external("support-decision", "native-provisional")
                    ],
                    "support_state": "provisional",
                },
                "pack": {
                    "kind": "selected",
                    "pack_profile_revision_id": _external(
                        "pack-profile-revision", "native-pack"
                    ),
                    "profile_adapter_id": _external(
                        "profile-adapter", "native-pack"
                    ),
                    "profile_authority": _authority("pack"),
                    "support_decision_ids": [
                        _external("support-decision", "native-experimental")
                    ],
                    "support_state": "experimental",
                },
            },
            "source_lock_ids": [],
            "dependency_lock_ids": [],
            "artifact_lock_ids": [],
            "runtime_scope": {"kind": "none"},
            "world_scope": {"kind": "none"},
            "dimension_scope": {"kind": "none"},
            "region_scope": {"kind": "none"},
            "operation_scopes": [],
            "privacy_class_id": _external("privacy-class", "native-private"),
            "resource_budget_class_id": _external(
                "resource-budget-class", "native-bounded"
            ),
        }
    )
    binding = seal_input_binding(
        {
            "kind": "input-binding",
            "format": "workbench-crucible-input-binding-v2",
            "schema_version": 2,
            "schema_id": INPUT_BINDING_SCHEMA_ID,
            "canonicalizer": CANONICALIZER_ID,
            "context_ref_id": context.id,
            "evidence_set_bindings": [],
            "graph_bindings": [],
            "graph_set_bindings": [],
            "recipe_bindings": [],
            "schema_bindings": [],
            "ontology_bindings": [],
            "adapter_bindings": [],
            "policy_bindings": [
                {
                    "input_key": "worldgen.action-policy",
                    "policy_id": _external("policy", "native-worldgen-policy"),
                    "authority_binding": _authority("pack"),
                }
            ],
            "index_bindings": [],
        }
    )
    graph_set_id = _external("graph-set-revision", "native-graph-set")
    proof = {
        "acceptance": {
            "S01-authority": {
                "owner_categories": {
                    "atlas.worldgen.generative.v1": "Atlas",
                    "atlas.worldgen.realized.v1": "Atlas",
                    "crucible.worldgen.capture-health.v1": "Crucible",
                    "crucible.worldgen.occurrence.v1": "Crucible",
                    "crucible.worldgen.stability.v1": "Crucible",
                },
                "state": "pass",
            }
        },
        "canonicalizer": CANONICALIZER_ID,
        "format": "workbench-worldgen-w01-proof-index-v1",
        "graph_set_revision_id": graph_set_id,
        "kind": "worldgen-w01-proof-index",
        "profile_state": "experimental-draft",
        "schema_version": 1,
    }
    proof["id"] = content_id("worldgen-w01-proof-index", proof)
    action = {
        "action": "read-query-visualize",
        "canonicalizer": CANONICALIZER_ID,
        "context_ref_id": context.id,
        "disposition": "allow",
        "format": "workbench-worldgen-action-gate-receipt-v1",
        "input_binding_id": binding.id,
        "kind": "worldgen-action-gate-receipt",
        "policy_sha256": hashlib.sha256(b"native-policy").hexdigest(),
        "schema_version": 1,
    }
    action["id"] = content_id("worldgen-action-gate-receipt", action)
    results = {
        _query_key(query): _query_result(graph_set_id, query)
        for query in QUERY_CASES
    }

    def resolve_query(graph_id: str, query: dict) -> dict:
        if graph_id != graph_set_id or _query_key(query) not in results:
            raise KeyError("unavailable native query")
        return deepcopy(results[_query_key(query)])

    handler = WorldStudioProvingViewHandler(
        query_resolver=resolve_query,
        proof_index_resolver=lambda proof_id: (
            deepcopy(proof)
            if proof_id == proof["id"]
            else (_ for _ in ()).throw(KeyError("proof unavailable"))
        ),
        action_gate_resolver=lambda receipt_id: (
            deepcopy(action)
            if receipt_id == action["id"]
            else (_ for _ in ()).throw(KeyError("action unavailable"))
        ),
    )
    return _SyntheticV01(
        context,
        binding,
        proof,
        action,
        graph_set_id,
        results,
        handler,
    )


def _new_runtime(
    root: Path,
    synthetic: _SyntheticV01,
    bundle: object,
    *,
    handler: WorldStudioProvingViewHandler | None = None,
    registration: ServiceHandlerRegistration | None = None,
) -> ServiceRuntimeV3:
    binding = build_world_studio_presenter_binding(handler or synthetic.handler)
    selected = registration or binding.registration
    runtime = ServiceRuntimeV3((root / 'service').resolve(), registrations=(selected,), physical_leases=posix_service_physical_lease_ports(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda context, binding: context.id == synthetic.context.id and binding.id == synthetic.binding.id))
    runtime.world_studio_binding = binding
    runtime.store.register_context(
        synthetic.context.canonical_bytes,
        synthetic.binding.canonical_bytes,
    )
    return runtime


def _tree_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _reseal_owner_result(result: dict) -> None:
    projection = result["query_result"]
    query_body = {
        "format": "workbench-worldgen-query-result-v1",
        "graph_set_revision_id": projection["graph_set_revision_id"],
        "kind": "worldgen-query-result",
        "query": projection["query"],
        "raw_archive_open_count": projection["raw_archive_open_count"],
        "results": [
            parse_canonical_json(row["record_canonical_json"].encode("utf-8"))
            for row in projection["rows"]
        ],
        "schema_version": 1,
        "truncated": projection["truncated"],
    }
    projection["query_result_id"] = content_id("worldgen-query-result", query_body)
    result.pop("id", None)
    result["id"] = content_id("world-studio-proving-view-result", result)


def _retired_worldgen_bundle() -> dict:
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    run = new_run(
        capture_mode="lossless-fixture",
        capture_plan_sha256=digest("plan"),
        fixture_id="crucible-fixture:native-retirement",
        fixture_sha256=digest("fixture"),
        environment={
            "minecraft_version": "1.12.2",
            "platform_profile_id": "workbench-platform:cleanroom:test",
            "platform_profile_sha256": digest("platform"),
            "pack_profile_id": "workbench-pack:example",
            "pack_profile_sha256": digest("pack"),
            "snapshot_id": "snapshot:native-retirement",
            "physical_side": "DEDICATED_SERVER",
            "runtime_java": "25-test",
            "mapping_namespace": "mcp-stable_39",
            "transformed_runtime_sha256": digest("runtime"),
            "mod_set_sha256": digest("mods"),
            "configuration_set_sha256": digest("config"),
        },
        world={
            "world_instance_id": "crucible-world:native-retirement",
            "world_seed_sha256": digest("seed"),
            "world_type": "WORKBENCH_TEST",
            "generator_options_sha256": digest("options"),
            "dimension_ids": [0],
        },
    )
    actor = exact_actor(
        mod_id="example",
        code_source_sha256=digest("artifact"),
        class_name="example.WorldGenerator",
        method_name="generate",
        method_descriptor="(II)V",
        mapping_namespace="mcp-stable_39",
        transformed_class_sha256=digest("class"),
    )
    builder = BundleBuilder(
        run,
        selection_id="crucible-selection:native-retirement",
        actor=actor,
        monotonic_ns=lambda: 7,
    )
    builder.start()
    span = builder.enter_span(
        span_kind="generator_call",
        operation_id="generator:native-retirement",
        arguments={"x": 0, "z": 0},
        dimension_id=0,
        chunk=(0, 0),
    )
    builder.record(
        "block_write",
        {
            "write_chain_id": "write:native-retirement-0",
            "channel": "chunk_primer",
            "position": [3, 64, 5],
            "generation_chunk": {"x": 0, "z": 0},
            "target_chunk": {"x": 0, "z": 0},
            "before_state_sha256": digest("air"),
            "after_state_sha256": digest("stone"),
            "flags": None,
            "terminal": True,
        },
        dimension_id=0,
        chunk=(0, 0),
    )
    builder.checkpoint(
        checkpoint_id="checkpoint:native-retirement",
        stage_id="stage:terrain",
        canonicalization_id="canonicalizer:native-retirement-v1",
        semantic_state={"3,64,5": "minecraft:stone"},
        comparison_scope={"dimension": 0, "chunks": [[0, 0]]},
        dimension_id=0,
        chunk=(0, 0),
        included_domains=["block_states"],
    )
    builder.return_span(
        span,
        span_kind="generator_call",
        result={"generated": True},
        dimension_id=0,
        chunk=(0, 0),
    )
    return builder.complete()


class NativeGraphQueryV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.synthetic = _synthetic_v01()
        cls.binding = build_world_studio_presenter_binding(cls.synthetic.handler)
        cls.bundle = cls.binding.composition

    def test_manifest_binds_current_producer_and_is_exported_from_package(self) -> None:
        manifest = graph_query_presenter_manifest_v2(self.binding)
        route = dict(manifest["route"])
        route_id = route.pop("route_id")
        self.assertEqual(
            content_id("runtime-explorer-graph-query-route", route), route_id
        )
        self.assertEqual(manifest["route_id"], route_id)
        self.assertEqual(manifest, explorer_package.graph_query_presenter_manifest_v2(self.binding))
        self.assertFalse(hasattr(explorer_package, "GRAPH_QUERY_ROUTE_ID"))
        self.assertIs(
            execute_embedded_graph_query_v2,
            explorer_package.execute_embedded_graph_query_v2,
        )
        self.assertEqual("validated-owner-result", route["authority_source"])
        self.assertEqual("exact-owner-result-bytes-with-lf", route["json_output"])
        self.assertEqual("single-write-binary", route["json_sink"])
        self.assertEqual(
            MAX_GRAPH_TERMINAL_BYTES,
            route["terminal_output"]["maximum_bytes"],
        )
        self.assertEqual(
            MAX_GRAPH_TERMINAL_BYTES,
            explorer_package.MAX_GRAPH_TERMINAL_BYTES,
        )
        encoded = canonical_json_bytes(manifest)
        self.assertNotIn(b"accepted_category_owners", encoded)
        self.assertNotIn(b"legacy", encoded)
        self.assertEqual(
            route["registry_id"], self.bundle.registry["registry_id"]
        )
        self.assertEqual(
            route["service_distribution_id"],
            self.bundle.service_distribution["id"],
        )
        self.assertEqual(
            route["source_tree_id"],
            self.bundle.source_tree_manifest["id"],
        )
        self.assertEqual(
            route["capability_id"],
            self.bundle.registry["capability_descriptors"][0]["capability_id"],
        )

    def test_package_root_keeps_native_dependencies_lazy(self) -> None:
        source = ROOT / "modules/runtime-explorer/src"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"sys.path.insert(0, {str(source)!r}); "
                    "import workbench_runtime_explorer as package; "
                    "assert package.Explorer; "
                    "assert 'workbench_crucible_service' not in sys.modules"
                ),
            ],
            cwd="/tmp",
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_self_hashed_bundles_and_substituted_owners_cannot_mint_trust(self) -> None:
        forged = deepcopy(self.bundle)
        forged.registry["registry_generation"] = 99
        body = dict(forged.registry)
        body.pop("registry_id")
        forged.registry["registry_id"] = content_id("component-capability-registry", body)
        with self.assertRaisesRegex(ValueError, "trusted producer"):
            WorldStudioPresenterBindingV3(forged, self.binding.registration)
        with self.assertRaises(TypeError):
            build_world_studio_presenter_binding(self.synthetic.handler, composition=forged)
        with self.assertRaises(TypeError):
            build_world_studio_presenter_binding(self.synthetic.handler, repository_root=ROOT)
        with self.assertRaisesRegex(ValueError, "genuine owner registration"):
            build_world_studio_presenter_binding(lambda *_: {})
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            runtime = _new_runtime(Path(temporary), self.synthetic, self.bundle)
            try:
                with self.assertRaisesRegex(ExplorerError, "trusted composition"):
                    EmbeddedGraphQueryPresenterV2(runtime, forged)
                foreign = build_world_studio_presenter_binding(self.synthetic.handler)
                with self.assertRaisesRegex(ExplorerError, "trusted registration"):
                    EmbeddedGraphQueryPresenterV2(runtime, foreign)
                binding = runtime.world_studio_binding
                binding.composition.registry["registry_generation"] = 99
                body = dict(binding.composition.registry)
                body.pop("registry_id")
                binding.composition.registry["registry_id"] = content_id("component-capability-registry", body)
                with self.assertRaisesRegex(ExplorerError, "trusted composition"):
                    EmbeddedGraphQueryPresenterV2(runtime, binding)
            finally:
                runtime.close()

    def test_all_five_queries_return_exact_owner_json_and_safe_native_view(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            runtime = _new_runtime(root, self.synthetic, self.bundle)
            before = _tree_snapshot(root / "service")
            for index, query in enumerate(QUERY_CASES, 1):
                request = self.synthetic.request(query)
                expected = self.synthetic.result(query)
                native = execute_embedded_graph_query_v2(
                    runtime,
                    binding=runtime.world_studio_binding,
                    arguments=request,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                    request_id=f"request.native.query.{index}",
                )
                self.assertEqual(canonical_json_bytes(expected), native.owner_result_bytes)
                self.assertEqual(expected, native.to_dict())
                self.assertEqual(expected["id"], native.owner_result_id)
                self.assertEqual(0, native.to_dict()["query_result"]["raw_archive_open_count"])

                json_output = BytesIO()
                native.write_json(json_output)
                self.assertEqual(
                    native.owner_result_bytes + b"\n",
                    json_output.getvalue(),
                )

                first = StringIO()
                second = StringIO()
                native.render(first)
                render_embedded_graph_query_result_v2(native, second)
                self.assertEqual(first.getvalue(), second.getvalue())
                rendered = first.getvalue()
                self.assertIn(expected["id"], rendered)
                self.assertIn(expected["query_result"]["query_result_id"], rendered)
                self.assertIn(expected["proof_index_id"], rendered)
                self.assertIn(expected["action_gate"]["receipt_id"], rendered)
                self.assertIn(expected["context_ref_id"], rendered)
                self.assertIn(expected["input_binding_id"], rendered)
                self.assertIn("Raw archive opens: 0", rendered)
                for row in expected["query_result"]["rows"]:
                    self.assertIn(row["record_canonical_json"], rendered)
                row_positions = [
                    rendered.index(row["record_canonical_json"])
                    for row in expected["query_result"]["rows"]
                ]
                self.assertEqual(sorted(row_positions), row_positions)
                self.assertNotIn("\x1b", rendered)
                self.assertNotIn("\x07", rendered)
                if query["query"] == "site":
                    self.assertIn("truncated", rendered)
            self.assertEqual(before, _tree_snapshot(root / "service"))
            runtime.close()

    def test_bare_owner_bytes_cannot_mint_a_trusted_embedded_result(self) -> None:
        result = self.synthetic.result(QUERY_CASES[0])
        with self.assertRaisesRegex(ExplorerError, "only be minted"):
            EmbeddedGraphQueryResultV2(canonical_json_bytes(result), route_id="untrusted")
        with self.assertRaisesRegex(ExplorerError, "trusted embedded result"):
            render_embedded_graph_query_result_v2(result, StringIO())  # type: ignore[arg-type]

    def test_terminal_and_json_writes_are_atomic_bounded_and_control_safe(self) -> None:
        query = QUERY_CASES[0]
        owner_query_result = deepcopy(
            self.synthetic.query_results[_query_key(query)]
        )
        record = owner_query_result["results"][0]
        record["logical_key"] = (
            "name\x1b[31mCSI\x1b[0m\tTAB\nNL\x00NUL\x7fDEL\u202eBIDI"
        )
        record["record_kind"] = (
            "kind"
            "\x1b]0;osc-bel\x07BEL"
            "\x1b]0;osc-st\x1b\\ST"
            "\x9b31mC1"
            "\x1b]0;unterminated"
        )
        record["control_evidence"] = (
            "\x1b[2J\x1b]8;;https://invalid.example\x07link"
        )
        body = dict(owner_query_result)
        body.pop("id")
        owner_query_result["id"] = content_id("worldgen-query-result", body)

        handler = WorldStudioProvingViewHandler(
            query_resolver=lambda _graph, _query: deepcopy(owner_query_result),
            proof_index_resolver=lambda _proof: deepcopy(self.synthetic.proof),
            action_gate_resolver=lambda _action: deepcopy(self.synthetic.action),
        )

        class TextSink:
            def __init__(self) -> None:
                self.writes: list[str] = []

            def write(self, value: str) -> int:
                self.writes.append(value)
                return len(value)

        class BinarySink:
            def __init__(self) -> None:
                self.writes: list[bytes] = []

            def write(self, value: bytes) -> int:
                self.writes.append(value)
                return len(value)

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            runtime = _new_runtime(
                Path(temporary).resolve(),
                self.synthetic,
                self.bundle,
                handler=handler,
            )
            native = execute_embedded_graph_query_v2(
                runtime,
                binding=runtime.world_studio_binding,
                arguments=self.synthetic.request(query),
                context_ref_id=self.synthetic.context.id,
                input_binding_id=self.synthetic.binding.id,
                request_id="request.native.hostile-terminal",
            )

            first = TextSink()
            second = TextSink()
            native.render(first)  # type: ignore[arg-type]
            native.render(second)  # type: ignore[arg-type]
            self.assertEqual(1, len(first.writes))
            self.assertEqual(first.writes, second.writes)
            rendered = first.writes[0]
            self.assertLessEqual(
                len(rendered.encode("utf-8")),
                MAX_GRAPH_TERMINAL_BYTES,
            )
            for control in (
                "\x1b",
                "\x07",
                "\x9b",
                "\t",
                "\x00",
                "\x7f",
                "\u202e",
            ):
                self.assertNotIn(control, rendered)
            self.assertIn(r"\x09TAB\x0aNL\x00NUL\x7fDEL\u202eBIDI", rendered)
            row_heading = next(
                line for line in rendered.splitlines() if line.startswith(" 1.")
            )
            self.assertIn(r"nameCSI\x09TAB", row_heading)
            self.assertIn("kindBELSTC1", row_heading)
            self.assertNotIn("osc-bel", row_heading)
            self.assertNotIn("osc-st", row_heading)
            self.assertNotIn("unterminated", row_heading)

            binary = BinarySink()
            native.write_json(binary)  # type: ignore[arg-type]
            self.assertEqual(
                [native.owner_result_bytes + b"\n"],
                binary.writes,
            )
            text = StringIO()
            with self.assertRaisesRegex(ExplorerError, "binary sink"):
                native.write_json(text)  # type: ignore[arg-type]
            self.assertEqual("", text.getvalue())

            corrupt = deepcopy(native)
            object.__setattr__(
                corrupt,
                "_owner_result_bytes",
                b'{"hostile":"\\ud800"}',
            )
            failed = TextSink()
            with self.assertRaisesRegex(ExplorerError, "no longer canonical"):
                corrupt.render(failed)  # type: ignore[arg-type]
            self.assertEqual([], failed.writes)
            runtime.close()

    def test_composition_registration_request_and_replay_fail_closed(self) -> None:
        query = QUERY_CASES[0]
        request = self.synthetic.request(query)
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            runtime = _new_runtime(root, self.synthetic, self.bundle)
            registry = deepcopy(self.bundle.registry)
            registry["registry_generation"] = 99
            drifted = type(self.bundle)(
                registry,
                self.bundle.source_tree_manifest,
                self.bundle.dependency_lock_manifest,
                self.bundle.health_receipt,
                self.bundle.service_distribution,
            )
            with self.assertRaisesRegex(ExplorerError, "trusted composition"):
                EmbeddedGraphQueryPresenterV2(runtime, drifted)

            bad_request = deepcopy(request)
            bad_request["caller_authority"] = "forged"
            with self.assertRaisesRegex(ExplorerError, "exact binding"):
                execute_embedded_graph_query_v2(
                    runtime,
                    binding=runtime.world_studio_binding,
                    arguments=bad_request,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                    request_id="request.native.invalid",
                )
            runtime.close()

        good = world_studio_service_registration(
            self.bundle, self.synthetic.handler
        )
        wrong = ServiceHandlerRegistration(
            method=good.method,
            capability_id=good.capability_id,
            capability_version=good.capability_version,
            handler_id=good.handler_id,
            implementation_id=good.implementation_id,
            mutation_boundary=good.mutation_boundary,
            asynchronous=good.asynchronous,
            maximum_concurrency=good.maximum_concurrency + 1,
            handler=good.handler,
            request_validator=good.request_validator,
            result_validator=good.result_validator,
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            runtime = _new_runtime(
                Path(temporary).resolve(),
                self.synthetic,
                self.bundle,
                registration=wrong,
            )
            with self.assertRaisesRegex(ExplorerError, "trusted registration"):
                EmbeddedGraphQueryPresenterV2(runtime, runtime.world_studio_binding)
            runtime.close()

        result = self.synthetic.result(query)
        substitutions = {
            "context_ref_id": _external("context-ref", "replay-context"),
            "input_binding_id": _external("input-binding", "replay-input"),
            "graph_set_revision_id": _external(
                "graph-set-revision", "replay-graph"
            ),
            "proof_index_id": _external(
                "worldgen-w01-proof-index", "replay-proof"
            ),
            "action_gate_receipt_id": _external(
                "worldgen-action-gate-receipt", "replay-action"
            ),
            "query": deepcopy(QUERY_CASES[1]),
        }
        for field, substitute in substitutions.items():
            replayed_request = self.synthetic.request(query)
            replayed_request[field] = substitute
            with self.subTest(replay_field=field):
                with self.assertRaisesRegex(ExplorerError, "exact request"):
                    _validated_owner_result(result, request=replayed_request)

    def test_post_construction_registration_composition_and_resolver_drift_fail_closed(self) -> None:
        request = self.synthetic.request(QUERY_CASES[0])
        key = (self.binding.registration.capability_id, "graph/query")

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            runtime = _new_runtime(
                Path(temporary).resolve(), self.synthetic, self.bundle
            )
            presenter = EmbeddedGraphQueryPresenterV2(runtime, runtime.world_studio_binding)
            replacement = world_studio_service_registration(
                self.bundle, self.synthetic.handler
            )
            self.assertIsNot(replacement, presenter._registration)
            runtime.registrations = MappingProxyType({})
            with self.assertRaisesRegex(ExplorerError, "trusted registration"):
                presenter.query(
                    arguments=request,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                    request_id="request.native.registration-removed",
                )
            runtime.registrations = MappingProxyType({key: replacement})
            with self.assertRaisesRegex(ExplorerError, "trusted registration"):
                presenter.query(
                    arguments=request,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                    request_id="request.native.registration-drift",
                )
            hostile_registrations = (
                replace(
                    presenter._registration,
                    method="graph/hostile",
                ),
                replace(
                    presenter._registration,
                    capability_id=_external("capability", "hostile-capability"),
                ),
                replace(
                    presenter._registration,
                    maximum_concurrency=5,
                ),
            )
            for forged in hostile_registrations:
                with self.subTest(
                    method=forged.method,
                    capability=forged.capability_id,
                    concurrency=forged.maximum_concurrency,
                ):
                    runtime.registrations = MappingProxyType({key: forged})
                    with self.assertRaisesRegex(
                        ExplorerError, "exact trusted registration"
                    ):
                        presenter.query(
                            arguments=request,
                            context_ref_id=self.synthetic.context.id,
                            input_binding_id=self.synthetic.binding.id,
                            request_id="request.native.hostile-registration",
                        )
            runtime.close()

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            runtime = _new_runtime(
                Path(temporary).resolve(), self.synthetic, self.bundle
            )
            composition = runtime.world_studio_binding.composition
            presenter = EmbeddedGraphQueryPresenterV2(runtime, runtime.world_studio_binding)
            composition.registry["registry_generation"] = 99
            with self.assertRaisesRegex(ExplorerError, "trusted composition"):
                presenter.query(
                    arguments=request,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                    request_id="request.native.composition-drift",
                )
            runtime.close()

        for attribute in (
            "query_resolver",
            "proof_index_resolver",
            "action_gate_resolver",
        ):
            with self.subTest(resolver=attribute), tempfile.TemporaryDirectory(
                dir="/tmp"
            ) as temporary:
                runtime = _new_runtime(
                    Path(temporary).resolve(), self.synthetic, self.bundle
                )
                presenter = EmbeddedGraphQueryPresenterV2(runtime, runtime.world_studio_binding)
                original = getattr(self.synthetic.handler, attribute)
                setattr(self.synthetic.handler, attribute, lambda *_args: {})
                try:
                    with self.assertRaisesRegex(
                        ExplorerError, "registration or resolver drifted"
                    ):
                        presenter.query(
                            arguments=request,
                            context_ref_id=self.synthetic.context.id,
                            input_binding_id=self.synthetic.binding.id,
                            request_id=f"request.native.{attribute}-drift",
                        )
                finally:
                    setattr(self.synthetic.handler, attribute, original)
                    runtime.close()

    def test_surrogate_keys_values_and_wide_integers_fail_preflight(self) -> None:
        request = self.synthetic.request(QUERY_CASES[0])
        hostile_values = []

        surrogate_key = deepcopy(request)
        surrogate_key["\ud800"] = "hostile"
        hostile_values.append(("key", surrogate_key, "surrogate code point"))

        surrogate_value = deepcopy(request)
        surrogate_value["query"]["hostile"] = "\udfff"
        hostile_values.append(("value", surrogate_value, "surrogate code point"))

        wide_integer = deepcopy(request)
        wide_integer["query"]["hostile"] = 2**63
        hostile_values.append(("integer", wide_integer, "signed 64-bit"))

        for label, hostile, expected in hostile_values:
            with self.subTest(hostile=label), self.assertRaisesRegex(
                ExplorerError, expected
            ):
                _bound_request(
                    hostile,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                )

        owner_result = self.synthetic.result(QUERY_CASES[0])
        owner_result["query_result"]["rows"][0][
            "record_canonical_json"
        ] = "\ud800"
        with self.assertRaisesRegex(ExplorerError, "surrogate code point"):
            _validated_owner_result(owner_result, request=request)

    def test_owner_execution_failure_is_bounded(self) -> None:
        request = self.synthetic.request(QUERY_CASES[0])
        secret = "SECRET OWNER CALLBACK DETAIL"
        failing = WorldStudioProvingViewHandler(
            query_resolver=lambda _graph, _query: (_ for _ in ()).throw(
                RuntimeError(secret)
            ),
            proof_index_resolver=lambda proof_id: deepcopy(self.synthetic.proof),
            action_gate_resolver=lambda receipt_id: deepcopy(self.synthetic.action),
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            runtime = _new_runtime(
                Path(temporary).resolve(),
                self.synthetic,
                self.bundle,
                handler=failing,
            )
            with self.assertRaisesRegex(ExplorerError, "execution failed") as caught:
                execute_embedded_graph_query_v2(
                    runtime,
                    binding=runtime.world_studio_binding,
                    arguments=request,
                    context_ref_id=self.synthetic.context.id,
                    input_binding_id=self.synthetic.binding.id,
                    request_id="request.native.owner-failure",
                )
            self.assertNotIn(secret, str(caught.exception))
            runtime.close()

    def test_owner_preflight_rejects_excess_rows_depth_and_row_bytes_upstream(self) -> None:
        query = QUERY_CASES[0]
        request = self.synthetic.request(query)
        direct_context = _DirectContext(
            self.synthetic.context.id,
            self.synthetic.binding.id,
            self.synthetic.context.canonical_bytes,
            self.synthetic.binding.canonical_bytes,
        )
        base = self.synthetic.query_results[_query_key(query)]

        excess_rows = deepcopy(base)
        excess_rows["results"] = [
            deepcopy(base["results"][0])
            for _ in range(MAX_GRAPH_RESULT_RECORDS + 1)
        ]
        oversized_row = deepcopy(base)
        oversized_row["results"][0]["oversized"] = "x" * (
            MAX_GRAPH_ROW_BYTES + 1
        )
        deep_row = deepcopy(base)
        nested: object = "leaf"
        for _ in range(MAX_GRAPH_RESULT_DEPTH + 2):
            nested = [nested]
        deep_row["results"][0]["nested"] = nested

        for label, owner_result, owner_error in (
            ("rows", excess_rows, "declared row bound"),
            ("row-bytes", oversized_row, "row exceeds the byte bound"),
            ("depth", deep_row, "row exceeds the structural depth bound"),
        ):
            with self.subTest(bound=label):
                handler = WorldStudioProvingViewHandler(
                    query_resolver=lambda _graph, _query, result=owner_result: deepcopy(
                        result
                    ),
                    proof_index_resolver=lambda _proof: deepcopy(
                        self.synthetic.proof
                    ),
                    action_gate_resolver=lambda _action: deepcopy(
                        self.synthetic.action
                    ),
                )
                with self.assertRaisesRegex(
                    WorldStudioProvingViewError, owner_error
                ):
                    handler(direct_context, request)

                with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
                    runtime = _new_runtime(
                        Path(temporary).resolve(),
                        self.synthetic,
                        self.bundle,
                        handler=handler,
                    )
                    with self.assertRaisesRegex(ExplorerError, "execution failed"):
                        execute_embedded_graph_query_v2(
                            runtime,
                            binding=runtime.world_studio_binding,
                            arguments=request,
                            context_ref_id=self.synthetic.context.id,
                            input_binding_id=self.synthetic.binding.id,
                            request_id=f"request.native.owner-bound.{label}",
                        )
                    runtime.close()

    def test_owner_result_preflight_duplicate_and_authority_binding_fail_closed(self) -> None:
        request = self.synthetic.request(QUERY_CASES[1])
        result = self.synthetic.result(QUERY_CASES[1])

        malformed = deepcopy(result)
        malformed["query_result"] = 7
        with self.assertRaisesRegex(ExplorerError, "query_result must be an object"):
            _validated_owner_result(malformed, request=request)

        oversized = deepcopy(result)
        oversized["query_result"]["rows"][0]["record_canonical_json"] = (
            "x" * (MAX_GRAPH_ROW_BYTES + 1)
        )
        with self.assertRaisesRegex(ExplorerError, "row.*bound"):
            _validated_owner_result(oversized, request=request)

        too_many = deepcopy(result)
        too_many["query_result"]["rows"] = [
            deepcopy(result["query_result"]["rows"][0])
            for _ in range(MAX_GRAPH_RESULT_RECORDS + 1)
        ]
        with self.assertRaisesRegex(ExplorerError, "record bound"):
            _validated_owner_result(too_many, request=request)

        too_deep = deepcopy(result)
        nested: object = "leaf"
        for _ in range(MAX_GRAPH_RESULT_DEPTH + 2):
            nested = [nested]
        too_deep["unexpected"] = nested
        with self.assertRaisesRegex(ExplorerError, "depth bound"):
            _validated_owner_result(too_deep, request=request)

        too_large = deepcopy(result)
        too_large["unexpected"] = "x" * (MAX_GRAPH_RESULT_BYTES + 1)
        with self.assertRaisesRegex(ExplorerError, "byte preflight"):
            _validated_owner_result(too_large, request=request)

        duplicated = deepcopy(result)
        duplicated["query_result"]["rows"].append(
            deepcopy(duplicated["query_result"]["rows"][0])
        )
        _reseal_owner_result(duplicated)
        self.assertTrue(validate_world_studio_proving_result(duplicated))
        with self.assertRaisesRegex(ExplorerError, "repeats an exact query row"):
            _validated_owner_result(duplicated, request=request)

        unowned = deepcopy(result)
        row = unowned["query_result"]["rows"][0]
        record = parse_canonical_json(row["record_canonical_json"].encode("utf-8"))
        record["category_id"] = "unowned.worldgen.category.v1"
        row["category_id"] = record["category_id"]
        row["record_canonical_json"] = canonical_json_bytes(record).decode("utf-8")
        row["row_id"] = content_id("world-studio-view-row", record)
        _reseal_owner_result(unowned)
        self.assertTrue(validate_world_studio_proving_result(unowned))
        with self.assertRaisesRegex(ExplorerError, "authority binding"):
            _validated_owner_result(unowned, request=request)

    @unittest.skipUnless(
        os.name == "posix"
        and EVIDENCE_ROOT.is_dir()
        and ENVELOPE_PATH.is_file(),
        "exact retained W01 proof and POSIX local service runtime are required",
    )
    def test_retained_w01_store_is_read_only_for_all_native_queries(self) -> None:
        proof = json.loads(
            (EVIDENCE_ROOT / "worldgen-w01-proof-index-v1.json").read_text(
                encoding="utf-8"
            )
        )
        action = json.loads(
            (EVIDENCE_ROOT / "actions/read-query-visualize.json").read_text(
                encoding="utf-8"
            )
        )
        envelope = json.loads(ENVELOPE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(EXPECTED_W01_PROOF_ID, proof["id"])
        self.assertEqual(EXPECTED_W01_GRAPH_ID, proof["graph_set_revision_id"])

        context_ref = envelope["context_ref"]
        input_binding = envelope["input_binding"]
        self.assertEqual(context_ref["id"], action["context_ref_id"])
        self.assertEqual(input_binding["id"], action["input_binding_id"])
        retained_digest = EXPECTED_W01_GRAPH_ID.rsplit(":", 1)[1]
        retained_store = (
            EVIDENCE_ROOT
            / "clean-stores"
            / retained_digest
            / "control-1"
        )
        self.assertTrue(retained_store.is_dir())
        self.assertFalse(retained_store.is_symlink())

        with tempfile.TemporaryDirectory(
            prefix="workbench-native-w01-custody-", dir="/tmp"
        ) as temporary:
            root = Path(temporary).resolve()
            copied_store = root / "retained-store"
            shutil.copytree(retained_store, copied_store, symlinks=False)
            before = _tree_snapshot(copied_store)
            self.assertEqual(_tree_snapshot(retained_store), before)
            self.assertIn("references/current.json", {path for path, _ in before})

            store = WorldgenGraphStore(copied_store)
            current = store.resolve_current()
            self.assertEqual(
                EXPECTED_W01_GRAPH_ID,
                current["manifest"]["graph_set_revision_id"],
            )
            query_service = WorldgenStoredQueryService(
                store,
                graph_set_revision_id=EXPECTED_W01_GRAPH_ID,
            )
            query_invocations: list[bytes] = []

            def resolve_query(graph_id: str, query: dict) -> dict:
                if graph_id != EXPECTED_W01_GRAPH_ID:
                    raise KeyError(graph_id)
                query_invocations.append(canonical_json_bytes(query))
                return query_service.query(query).to_dict()

            handler = WorldStudioProvingViewHandler(
                query_resolver=resolve_query,
                proof_index_resolver=lambda proof_id: (
                    deepcopy(proof)
                    if proof_id == proof["id"]
                    else (_ for _ in ()).throw(KeyError(proof_id))
                ),
                action_gate_resolver=lambda receipt_id: (
                    deepcopy(action)
                    if receipt_id == action["id"]
                    else (_ for _ in ()).throw(KeyError(receipt_id))
                ),
            )
            trusted_binding = build_world_studio_presenter_binding(handler)
            runtime = ServiceRuntimeV3((root / 'service').resolve(), registrations=(trusted_binding.registration,), physical_leases=posix_service_physical_lease_ports(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda context, binding: context.id == context_ref['id'] and binding.id == input_binding['id']))
            runtime.world_studio_binding = trusted_binding
            runtime.store.register_context(
                canonical_json_bytes(context_ref),
                canonical_json_bytes(input_binding),
            )
            presenter = EmbeddedGraphQueryPresenterV2(runtime, runtime.world_studio_binding)

            try:
                for index, query in enumerate(QUERY_CASES, 1):
                    request = {
                        "action_gate_receipt_id": action["id"],
                        "comparison_graph_set_revision_id": None,
                        "comparison_query": None,
                        "context_ref_id": context_ref["id"],
                        "format": "workbench-world-studio-proving-request-v1",
                        "graph_set_revision_id": EXPECTED_W01_GRAPH_ID,
                        "input_binding_id": input_binding["id"],
                        "operation": "query",
                        "proof_index_id": proof["id"],
                        "query": deepcopy(query),
                        "schema_version": 1,
                    }
                    repeated: list[bytes] = []
                    for repeat in range(2):
                        native = presenter.query(
                            arguments=request,
                            context_ref_id=context_ref["id"],
                            input_binding_id=input_binding["id"],
                            request_id=(
                                f"request.native.retained.{index}.{repeat}"
                            ),
                        )
                        result = native.to_dict()
                        repeated.append(native.owner_result_bytes)
                        self.assertEqual(
                            EXPECTED_W01_GRAPH_ID,
                            result["query_result"]["graph_set_revision_id"],
                        )
                        self.assertEqual(
                            query,
                            result["query_result"]["query"],
                        )
                        self.assertEqual(
                            0,
                            result["query_result"]["raw_archive_open_count"],
                        )
                        self.assertEqual(0, query_service.raw_archive_open_count)
                    self.assertEqual(repeated[0], repeated[1])

                hostile = dict(request)
                hostile["caller_answer"] = "forged"
                with self.assertRaisesRegex(ExplorerError, "exact binding"):
                    presenter.query(
                        arguments=hostile,
                        context_ref_id=context_ref["id"],
                        input_binding_id=input_binding["id"],
                        request_id="request.native.retained.hostile",
                    )
                self.assertEqual(0, query_service.raw_archive_open_count)
                self.assertEqual(
                    sorted(_query_key(query) for query in QUERY_CASES),
                    sorted(set(query_invocations)),
                )
                self.assertTrue(
                    all(query_invocations.count(key) == 2 for key in set(query_invocations))
                )
                self.assertEqual(before, _tree_snapshot(copied_store))
                self.assertEqual(
                    EXPECTED_W01_GRAPH_ID,
                    store.resolve_current()["manifest"]["graph_set_revision_id"],
                )
            finally:
                runtime.close()

    def test_retired_worldgen_receipt_refuses_direct_and_cli_routes(self) -> None:
        retired = _retired_worldgen_bundle()
        self.assertEqual(retired, validate_bundle(retired))
        expected = (
            "workbench-crucible-worldgen-observatory-bundle-v1 is retired "
            "from Exact Runtime Explorer; use the embedded V2 graph/query presenter"
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            path = Path(temporary) / "retired-worldgen.json"
            path.write_text(json.dumps(retired) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ExplorerError, "is retired") as direct:
                receipt_provider(path)
            self.assertEqual(expected, str(direct.exception))

            output = StringIO()
            error = StringIO()
            self.assertEqual(
                2,
                explorer_main(
                    [
                        "coordinate:3,64,5",
                        "--no-project",
                        "--no-manuals",
                        "--receipt",
                        str(path),
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
            )
            self.assertEqual("", output.getvalue())
            self.assertIn(expected, error.getvalue())


if __name__ == "__main__":
    unittest.main()
