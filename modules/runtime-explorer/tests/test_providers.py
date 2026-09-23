from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/runtime-explorer/src",
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/workbench-shell/src",
    ROOT / "modules/atlas/src",
    ROOT / "modules/pack-program-studio/src",
    ROOT / "modules/crucible/src",
):
    sys.path.insert(0, str(source))
sys.path.insert(0, str(ROOT / "modules/project-intelligence/tests"))
sys.path.insert(0, str(ROOT / "modules/crucible/tests"))

from workbench_runtime_explorer.providers import (  # noqa: E402
    artifact_provider,
    atlas_runtime_provider,
    console_session_provider,
    raw_log_provider,
    receipt_provider,
    workspace_provider,
)
from workbench_runtime_explorer.model import ExplorerError  # noqa: E402
from workbench_runtime_explorer.query import Explorer, parse_query  # noqa: E402
from workbench_crucible_mixin_custody import (  # noqa: E402
    COMPONENT_TOPOLOGY_RECEIPT_PREFIX,
    PROVIDER_ENUMERATION_EVIDENCE_PREFIX,
    SERVICE_LOADER_ITERATOR_MECHANISM,
    build_ledger,
    build_runtime_service_receipt,
)
from workbench_crucible_mixins import (  # noqa: E402
    RAW_SERVICE_COMPONENTS_FORMAT,
    REQUIRED_COMPONENT_ROLES,
    build_service_components_receipt,
    parse_raw_service_components,
)
from workbench_crucible_runtime_snapshot import (  # noqa: E402
    bind_known_receipt,
    build_runtime_snapshot,
    capability_from_receipts,
    capability_status,
)
from test_jvm_class_surface import _class_bytes  # noqa: E402
from test_mixin_config_lifecycle import _receipt as _config_lifecycle_receipt  # noqa: E402
from test_mixin_transformer_chain import _receipt as _transformer_chain_receipt  # noqa: E402
from test_mixin_final_definition import _receipt as _final_definition_receipt  # noqa: E402
from workbench_api.events import (  # noqa: E402
    EventNormalizer,
    RawLocator as EventRawLocator,
)
from workbench_core.sessions import RetainedSession  # noqa: E402
from workbench_project_intelligence.runtime_surface import (  # noqa: E402
    scan_runtime_surface,
)
from workbench_pack_program_studio import (  # noqa: E402
    AnalysisContext,
    build_report as build_groovy_program_report,
    load_profile as load_groovy_program_profile,
)
from workbench_pack_program_studio.language_model import (  # noqa: E402
    diagnostic_identity as groovy_diagnostic_identity,
    language_result_identity,
)
from workbench_pack_program_studio.model import content_id as groovy_content_id  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _service_components_receipt() -> dict[str, object]:
    capture_id = "capture:service-components-explorer"
    uri = "file:/runtime/cleanmix.jar"
    rows: list[dict[str, object]] = []

    def append(event: str, payload: dict[str, object]) -> None:
        rows.append(
            {
                "format": RAW_SERVICE_COMPONENTS_FORMAT,
                "capture_id": capture_id,
                "sequence": len(rows),
                "event": event,
                "payload": payload,
            }
        )

    append(
        "capture_start",
        {
            "agent_id": "fixture-agent-v1",
            "java_version": "25.0.4",
            "target_class": "org.spongepowered.asm.service.MixinService",
            "expected_input_sha256": "1" * 64,
        },
    )
    append(
        "transformer_installed",
        {
            "target_class": "org.spongepowered.asm.service.MixinService",
            "expected_input_sha256": "1" * 64,
            "retransform_requested": False,
        },
    )
    append(
        "transform_applied",
        {
            "defining_loader_class": "example.FoundationLoader",
            "defining_loader_identity": "example.FoundationLoader@1",
            "target_code_source_uri": uri,
            "input_sha256": "1" * 64,
            "output_sha256": "2" * 64,
            "reason": None,
        },
    )
    for role in REQUIRED_COMPONENT_ROLES:
        append(
            "component_observed",
            {
                "role": role,
                "implementation_class": "example." + role.title().replace("_", ""),
                "object_identity": "example.Object@" + str(len(rows)),
                "implementation_loader_class": "example.FoundationLoader",
                "implementation_loader_identity": "example.FoundationLoader@1",
                "code_source_uri": uri,
                "reported_name": "CleanMix" if role in {"service", "logger"} else None,
            },
        )
    append(
        "capture_end",
        {
            "health": "healthy",
            "transform_applied_count": 1,
            "observed_roles": sorted(REQUIRED_COMPONENT_ROLES),
            "failed_roles": [],
            "write_failure": False,
        },
    )
    encoded = b"".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
        for row in rows
    )
    return build_service_components_receipt(
        session={
            "capture_id": capture_id,
            "launch_id": "launch:service-components-explorer",
            "profile_id": "workbench-platform:cleanroom:test",
            "side": "dedicated_server",
            "candidate_lock_sha256": "3" * 64,
            "toolchain_lock_sha256": "4" * 64,
        },
        inputs={
            "agent_artifact": {"label": "agent.jar", "sha256": "5" * 64, "size_bytes": 1},
            "candidate_lock": {"label": "candidate.json", "sha256": "3" * 64, "size_bytes": 1},
            "fixture_result": {"label": "result.json", "sha256": "6" * 64, "size_bytes": 1},
            "launch_log": {"label": "launch.log", "sha256": "7" * 64, "size_bytes": 1},
            "raw_trace": {"label": "raw.ndjson", "sha256": "8" * 64, "size_bytes": len(encoded)},
            "toolchain_lock": {"label": "toolchain.json", "sha256": "4" * 64, "size_bytes": 1},
        },
        artifacts=[
            {
                "artifact_sha256": "9" * 64,
                "size_bytes": 100,
                "label": "cleanmix.jar",
                "code_source_uri": uri,
            }
        ],
        raw_events=parse_raw_service_components(encoded),
        limitations=["Synthetic Explorer component fixture."],
    )


def _groovy_language_result() -> dict[str, object]:
    source_path = "postInit/Recipes.groovy"
    source_sha256 = "a" * 64
    diagnostic: dict[str, object] = {
        "range": {
            "start": {"line": 4, "character": 7},
            "end": {"line": 4, "character": 8},
        },
        "severity": 1,
        "code": None,
        "source": "GroovyScript",
        "message": "fixture compiler rejected the recipe",
    }
    diagnostic["diagnostic_id"] = groovy_diagnostic_identity(
        source_path, source_sha256, diagnostic
    )
    artifacts = [
        {
            "path": "mods/groovyscript-1.4.3.jar",
            "sha256": "b" * 64,
            "size": 123,
        }
    ]
    mod_graph_id = groovy_content_id(
        "workbench-groovy-runtime-mod-graph:sha256:", artifacts
    )
    runtime_identity = {
        "run_config_sha256": "c" * 64,
        "mod_graph_id": mod_graph_id,
        "cache_tree_sha256": None,
        "java_sha256": None,
        "receipt_sha256": None,
    }
    transcript_messages = [
        {
            "sequence": 1,
            "direction": "outbound",
            "kind": "request",
            "method": "initialize",
            "id": 1,
            "size": 128,
            "sha256": "d" * 64,
        }
    ]
    transcript_sha256 = hashlib.sha256(
        json.dumps(
            transcript_messages,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    result: dict[str, object] = {
        "format": "workbench-groovy-language-service-result-v1",
        "schema_version": 1,
        "result_id": "",
        "operation_class": "read-only-network-observation",
        "observed_at": "2026-08-05T00:00:00Z",
        "authority": {
            "orchestrator": "Workbench Pack Program Studio",
            "compiler_observation": "GroovyScript embedded language server",
            "runtime_inventory": "Workbench exact local byte inventory",
            "endpoint_identity": "unavailable in the upstream protocol",
            "runtime_effects": "none; Atlas observation not implied",
            "construction": "none; Blueprints remains construction authority",
        },
        "profile": {
            "pack_program_profile_id": "fixture-pack-program-profile-v1",
            "pack_program_profile_sha256": "1" * 64,
            "language_service_profile_id": "fixture-language-profile-v1",
            "language_service_profile_path": "/recorded/language-profile.json",
            "language_service_profile_sha256": "2" * 64,
            "platform_profile_id": "workbench-platform:cleanroom:groovyscript:1.4.3",
            "groovyscript": {
                "version": "1.4.3",
                "artifact_filename": "groovyscript-1.4.3.jar",
                "artifact_sha256": "b" * 64,
                "artifact_size": 123,
                "source_commit": "3" * 40,
            },
            "server_semantics": {
                "transport": "lsp-jsonrpc-tcp",
                "default_host": "127.0.0.1",
                "default_port": 25564,
                "runtime_side": "client",
                "start_property": "-Dgroovyscript.run_ls=true",
                "compile_trigger": "textDocument/documentSymbol",
                "diagnostic_method": "textDocument/publishDiagnostics",
                "text_document_sync": "full",
                "compilation_phase": "canonicalization",
                "endpoint_identity_protocol": "unavailable",
            },
            "runtime_layout": {
                "artifact_relative_path": "mods/groovyscript-1.4.3.jar",
                "mods_directory": "mods",
                "groovy_directory": "groovy",
                "run_config": "groovy/runConfig.json",
                "cache_directory": "cache/groovy",
            },
            "bounds": {
                "max_files": 512,
                "max_source_bytes": 4194304,
                "max_total_source_bytes": 134217728,
                "max_header_bytes": 65536,
                "max_message_bytes": 16777216,
                "max_transcript_messages": 65536,
                "max_diagnostics": 10000,
                "max_runtime_artifacts": 1024,
                "max_runtime_artifact_bytes": 536870912,
            },
            "canary": {
                "prefix": "def __workbench_diagnostic_canary__ = )\n",
                "required_severity": 1,
            },
        },
        "request": {
            "source": "/recorded/source",
            "runtime_root": "/recorded/runtime",
            "selected_paths": [source_path],
            "all": False,
            "host": "127.0.0.1",
            "port": 25564,
            "server_workspace_uri": "file:///runtime/groovy",
            "allow_remote": False,
            "connect_timeout_seconds": 1,
            "diagnostic_timeout_seconds": 1,
            "java": None,
            "runtime_receipt": None,
        },
        "program": {
            "program_id": "workbench-groovy-static-program:sha256:" + "e" * 64,
            "source_sha256": "f" * 64,
            "run_config_sha256": "c" * 64,
            "pack_profile_id": "supersymmetry",
            "platform_profile_id": "workbench-platform:cleanroom:groovyscript:1.4.3",
            "physical_side": "client",
            "packmode": None,
            "debug": False,
            "selected_files": [
                {
                    "path": source_path,
                    "absolute_path": "/recorded/groovy/postInit/Recipes.groovy",
                    "server_uri": "file:///runtime/groovy/postInit/Recipes.groovy",
                    "sha256": source_sha256,
                    "size": 42,
                    "stage": "postInit",
                    "execution_state": "enabled",
                }
            ],
        },
        "runtime": {
            "runtime_id": groovy_content_id(
                "workbench-groovy-language-runtime:sha256:", runtime_identity
            ),
            "root": "/recorded/runtime",
            "run_config_path": "/recorded/runtime/groovy/runConfig.json",
            "run_config_sha256": "c" * 64,
            "groovyscript_artifact": artifacts[0],
            "mod_graph": {
                "mod_graph_id": mod_graph_id,
                "artifacts": artifacts,
                "artifact_count": 1,
                "total_bytes": 123,
            },
            "class_cache": {
                "state": "absent",
                "path": "/recorded/runtime/cache/groovy",
                "file_count": 0,
                "total_bytes": 0,
                "tree_sha256": None,
                "cache_version": 4,
                "content_addressed_by_upstream": False,
                "upstream_identity_inputs": ["source last-modified time", "Java version"],
            },
            "java": {
                "state": "not-supplied",
                "sha256": None,
                "size": None,
                "path": None,
                "version_output": None,
            },
            "launch_receipt": {
                "state": "not-supplied",
                "path": None,
                "sha256": None,
                "size": None,
                "format": None,
                "receipt_id": None,
                "semantic_validation": "not-performed",
            },
            "endpoint_binding": "caller-context-only; upstream endpoint has no identity challenge",
        },
        "service": {
            "state": "completed",
            "endpoint": {
                "host": "127.0.0.1",
                "port": 25564,
                "transport": "lsp-jsonrpc-tcp",
                "connect_ms": 1,
                "identity_binding": "unavailable-upstream-protocol",
            },
            "workspace_uri": "file:///runtime/groovy",
            "capabilities": {"documentSymbolProvider": True},
            "files": [
                {
                    "path": source_path,
                    "server_uri": "file:///runtime/groovy/postInit/Recipes.groovy",
                    "sha256": source_sha256,
                    "size": 42,
                    "stage": "postInit",
                    "execution_state": "enabled",
                    "state": "diagnostics",
                    "canary": {
                        "state": "confirmed",
                        "diagnostic_count": 1,
                        "latency_ms": 1,
                    },
                    "diagnostics": [diagnostic],
                    "diagnostic_count": 1,
                    "symbol_count": 0,
                    "request_error": None,
                    "compile_latency_ms": 2,
                }
            ],
            "failure": None,
            "transcript": {
                "messages": transcript_messages,
                "message_count": 1,
                "method_counts": {"initialize": 1},
                "transcript_sha256": transcript_sha256,
            },
        },
        "summary": {
            "status": "attention",
            "compiler_state": "diagnostics",
            "selected_files": 1,
            "checked_files": 1,
            "diagnostics": 1,
            "severity_counts": {"1": 1},
            "file_states": {"diagnostics": 1},
            "runtime_binding": "unavailable-upstream-protocol",
        },
        "limitations": [
            "Canonicalization is not script execution or effective registry state."
        ],
        "horizons": [],
    }
    result["result_id"] = language_result_identity(result)
    return result


def _project(base: Path) -> Path:
    project = base / "example"
    _write(project / "settings.gradle", "rootProject.name = 'example'\n")
    _write(project / "build.gradle", "plugins { id 'java' }\n")
    _write(
        project / "src/main/resources/mcmod.info",
        '[{"modid":"example","name":"Example","version":"1.0"}]\n',
    )
    _write(
        project / "src/main/java/example/ExampleMod.java",
        """package example;
@Mod(modid="example")
public class ExampleMod {
  public void register() { setRegistryName("example:machine"); }
}
""",
    )
    _write(project / "config/example.cfg", "enabled=true\n")
    return project


def _runtime_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA application_id = 1398098247;
        PRAGMA user_version = 2;
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            physical_side TEXT NOT NULL,
            adapter TEXT NOT NULL,
            kind TEXT NOT NULL,
            json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE edges (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            physical_side TEXT NOT NULL,
            adapter TEXT NOT NULL,
            predicate TEXT NOT NULL,
            subject TEXT NOT NULL,
            object TEXT NOT NULL,
            json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE diagnostics (
            id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            physical_side TEXT NOT NULL,
            adapter TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE reconciliations (
            edge_id TEXT PRIMARY KEY,
            hei_id TEXT NOT NULL,
            common_id TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE node_keys (
            node_id TEXT NOT NULL,
            key_kind TEXT NOT NULL,
            key_value TEXT NOT NULL,
            profile TEXT NOT NULL,
            physical_side TEXT NOT NULL,
            PRIMARY KEY (key_kind, key_value, profile, physical_side, node_id)
        ) WITHOUT ROWID;
        CREATE INDEX node_keys_node ON node_keys(node_id);
        CREATE INDEX node_keys_lookup ON node_keys(key_kind, key_value);
        """
    )
    scope = {
        "snapshot_id": "fixture",
        "profile": "COMMON_FINAL_STATE",
        "physical_side": "CLIENT",
        "adapter": "fixture",
    }
    mod_id = "rg:common_final_state_client:fixture:mod/example"
    machine_id = "rg:common_final_state_client:fixture:machine/example"
    mod = {
        "record_type": "node",
        "id": mod_id,
        "kind": "mod",
        "scope": scope,
        "attributes": {"mod_id": "example", "version": "1.0"},
    }
    machine = {
        "record_type": "node",
        "id": machine_id,
        "kind": "machine",
        "scope": scope,
        "attributes": {"registry_name": "example:machine", "tier": 2},
    }
    for node in (mod, machine):
        connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
            (
                node["id"],
                "COMMON_FINAL_STATE",
                "CLIENT",
                "fixture",
                node["kind"],
                json.dumps(node, separators=(",", ":"), sort_keys=True),
            ),
        )
    connection.executemany(
        "INSERT INTO node_keys VALUES (?, ?, ?, ?, ?)",
        [
            (mod_id, "mod-id", "example", "COMMON_FINAL_STATE", "CLIENT"),
            (
                machine_id,
                "registry-name",
                "example:machine",
                "COMMON_FINAL_STATE",
                "CLIENT",
            ),
        ],
    )
    edge = {
        "record_type": "edge",
        "id": "rge:" + hashlib.sha256(b"belongs").hexdigest(),
        "predicate": "belongs_to_mod",
        "subject": machine_id,
        "object": mod_id,
        "scope": scope,
        "attributes": {},
    }
    connection.execute(
        "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            edge["id"],
            "COMMON_FINAL_STATE",
            "CLIENT",
            "fixture",
            edge["predicate"],
            machine_id,
            mod_id,
            json.dumps(edge, separators=(",", ":"), sort_keys=True),
        ),
    )
    connection.commit()
    connection.close()


class ProviderTests(unittest.TestCase):
    def test_artifact_input_count_is_bounded_before_any_file_read(self) -> None:
        with self.assertRaisesRegex(ExplorerError, "at most 32"):
            artifact_provider(
                tuple(Path(f"missing-{index}.jar") for index in range(33))
            )

    def test_artifact_provider_searches_exact_classes_methods_and_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example.jar"
            with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "META-INF/MANIFEST.MF",
                    "Manifest-Version: 1.0\nFMLCorePlugin: example.Thing\n\n",
                )
                archive.writestr("example/Thing.class", _class_bytes())
                archive.writestr("assets/example/config.json", b"{}\n")
                archive.writestr("assets/example/recipes/machine.json", b"{}\n")
            provider = artifact_provider((path,))
            explorer = Explorer((provider.source,), provider.records)
            class_result = explorer.search(parse_query("example.Thing"))
            method_result = explorer.search(parse_query("example.Thing#run"))
            field_result = explorer.search(parse_query("field:value"))
            recipe_result = explorer.search(
                parse_query("recipe:example:machine")
            )
            registration_result = explorer.search(
                parse_query("kind:registration")
            )

        self.assertEqual(1, class_result["summary"]["entities"])
        class_facet = next(
            facet
            for facet in class_result["matches"][0]["facets"]
            if facet["kind"] == "class"
        )
        self.assertEqual(52, class_facet["runtime_form"]["classfile"]["classfile_version"]["major"])
        self.assertEqual("static-possible", class_facet["state"])
        method_facet = next(
            facet
            for match in method_result["matches"]
            for facet in match["facets"]
            if facet["kind"] == "method"
        )
        method = method_facet["runtime_form"]["classfile_member"]
        self.assertEqual("(Ljava/lang/String;)I", method["descriptor"])
        self.assertEqual(1, field_result["summary"]["entities"])
        self.assertEqual("recipe", recipe_result["matches"][0]["kind"])
        self.assertTrue(
            any(
                identity["kind"] == "recipe-id"
                and identity["value"] == "example:machine"
                for identity in recipe_result["matches"][0]["identities"]
            )
        )
        self.assertEqual(1, registration_result["summary"]["entities"])
        registration = registration_result["matches"][0]["facets"][0]
        self.assertEqual("declared", registration["state"])
        self.assertEqual("unobserved", registration["scope"]["runtime_selection"])
        self.assertTrue(registration["relationships"])

    def test_workspace_and_atlas_join_exact_owner_version_and_runtime_form(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = _project(base)
            database = base / "runtime.sqlite"
            _runtime_database(database)
            static = workspace_provider(project)
            runtime = atlas_runtime_provider(database, "example:machine")
            explorer = Explorer(
                (static.source, runtime.source),
                (*static.records, *runtime.records),
            )
            result = explorer.search(parse_query("example:machine"))
            typed_machine = explorer.search(
                parse_query("machine:example:machine")
            )

        self.assertEqual(1, result["summary"]["entities"])
        match = result["matches"][0]
        self.assertIn("observed", match["states"])
        self.assertIn("static-possible", match["states"])
        observed = next(
            facet for facet in match["facets"] if facet["state"] == "observed"
        )
        self.assertEqual("example:machine", observed["runtime_form"]["attributes"]["registry_name"])
        self.assertEqual("example", observed["owner"]["actors"][0]["id"])
        self.assertEqual("1.0", observed["owner"]["actors"][0]["version"])
        self.assertEqual("COMMON_FINAL_STATE", observed["scope"]["profile"])
        self.assertEqual(1, typed_machine["summary"]["entities"])
        self.assertEqual(["observed"], typed_machine["matches"][0]["states"])
        self.assertTrue(
            any(
                identity["kind"] == "machine-id"
                and identity["value"] == "example:machine"
                for identity in typed_machine["matches"][0]["identities"]
            )
        )

    def test_generic_receipt_searches_identity_fields_without_secrets_or_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            _write(
                path,
                json.dumps(
                    {
                        "format": "example-module-receipt-v1",
                        "record": {
                            "class_name": "example.RuntimeThing",
                            "registry_name": "example:thing",
                            "api_key": "must-not-appear",
                        },
                    }
                )
                + "\n",
            )
            provider = receipt_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("example.RuntimeThing")
            )

        self.assertEqual("unvalidated", provider.source.state)
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual("verbatim-evidence", result["matches"][0]["states"][0])
        self.assertNotIn(
            "must-not-appear",
            json.dumps([record.public() for record in provider.records]),
        )

    def test_validated_groovy_program_report_exposes_static_effects_without_runtime_promotion(self) -> None:
        profile = load_groovy_program_profile(
            ROOT
            / "profiles/packs/supersymmetry/groovy/groovy-program-profile-v1.json"
        )
        report = build_groovy_program_report(
            source=ROOT / "modules/pack-program-studio/tests/fixtures/baseline",
            profile=profile,
            context=AnalysisContext(side="dedicated-server"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "groovy-program.json"
            _write(path, json.dumps(report) + "\n")
            provider = receipt_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("material:alpha")
            )

        self.assertEqual("groovy-pack-program-report", provider.source.source_kind)
        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual(["static-possible"], result["matches"][0]["states"])
        facet = result["matches"][0]["facets"][0]
        self.assertEqual("not-observed", facet["runtime_form"]["state"])
        self.assertEqual("receipt", facet["navigation"][0]["kind"])

    def test_validated_groovy_language_result_exposes_compiler_evidence_without_runtime_promotion(self) -> None:
        receipt = _groovy_language_result()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "groovy-language-result.json"
            _write(path, json.dumps(receipt) + "\n")
            provider = receipt_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("kind:groovy-compiler-diagnostic rejected")
            )

        self.assertEqual("groovy-language-service-result", provider.source.source_kind)
        self.assertEqual("Workbench Pack Program Studio", provider.source.authority)
        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual(["verbatim-evidence"], result["matches"][0]["states"])
        facet = result["matches"][0]["facets"][0]
        self.assertEqual("groovy-compiler-diagnostic", facet["kind"])
        self.assertEqual("not-observed", facet["runtime_form"]["state"])
        self.assertEqual(
            "canonicalization", facet["runtime_form"]["compiler_phase"]
        )
        self.assertEqual("receipt", facet["navigation"][0]["kind"])

    def test_groovy_language_result_rejects_rebound_outer_identity_with_stale_diagnostic(self) -> None:
        receipt = _groovy_language_result()
        forged = deepcopy(receipt)
        forged["service"]["files"][0]["diagnostics"][0]["message"] = "forged"
        forged["result_id"] = language_result_identity(forged)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "forged-groovy-language-result.json"
            _write(path, json.dumps(forged) + "\n")
            with self.assertRaisesRegex(RuntimeError, "diagnostic identity"):
                receipt_provider(path)

    def test_serialized_runtime_surface_retains_declarations_without_stale_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = _project(root)
            surface = scan_runtime_surface(project)
            path = root / "runtime-surface.json"
            _write(path, json.dumps(surface) + "\n")
            provider = receipt_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("class:example.ExampleMod")
            )
            current = workspace_provider(project)
            combined = Explorer(
                (current.source, provider.source),
                (*current.records, *provider.records),
            ).search(parse_query("class:example.ExampleMod"))

        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, result["summary"]["entities"])
        facet = result["matches"][0]["facets"][0]
        self.assertEqual("declared", facet["state"])
        self.assertEqual("receipt", facet["navigation"][0]["kind"])
        self.assertIn("not revalidated", facet["limitations"][0])
        self.assertEqual(1, combined["summary"]["entities"])
        self.assertEqual(2, len(combined["matches"][0]["facets"]))

    def test_validated_mixin_ledger_exposes_observed_stages_and_final_bytes(self) -> None:
        ledger = build_ledger(
            session={
                "session_id": "crucible-session:explorer",
                "launch_id": "launch:explorer",
                "profile_id": "workbench-platform:cleanroom:test",
                "side": "dedicated_server",
                "component_receipt_sha256": "a" * 64,
            },
            launch_state="complete",
            observations=[
                {
                    "sequence": 1,
                    "stage": "apply_started",
                    "outcome": "observed",
                    "phase": "DEFAULT",
                    "subject": {
                        "artifact_sha256": "a" * 64,
                        "owner_label": "example",
                        "config": "mixins.example.json",
                        "mixin": "example.MixinTarget",
                        "target_class": "example.Target",
                    },
                    "evidence": {
                        "kind": "cleanmix_audit",
                        "source_sha256": "b" * 64,
                        "source_record": "line:10",
                        "limitations": [],
                    },
                },
                {
                    "sequence": 2,
                    "stage": "final_class_defined",
                    "outcome": "observed",
                    "phase": "UNKNOWN",
                    "subject": {"target_class": "example.Target"},
                    "evidence": {
                        "kind": "foundation_final_bytes",
                        "source_sha256": "c" * 64,
                        "source_record": "class:example.Target",
                        "limitations": [],
                    },
                    "final_bytecode_sha256": "d" * 64,
                },
            ],
            limitations=["Synthetic explorer fixture."],
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.json"
            _write(path, json.dumps(ledger) + "\n")
            provider = receipt_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("class:example.Target")
            )

        self.assertEqual(
            "crucible-mixin-transformation-ledger",
            provider.source.source_kind,
        )
        self.assertEqual("complete", provider.source.state)
        self.assertEqual("exact", result["summary"]["status"])
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual(["observed"], result["matches"][0]["states"])
        self.assertEqual(
            {"mixin-apply-started", "mixin-final-class-defined"},
            {facet["kind"] for facet in result["matches"][0]["facets"]},
        )
        self.assertTrue(
            any(
                identity["kind"] == "final-class-sha256"
                and identity["value"] == "d" * 64
                for identity in result["matches"][0]["identities"]
            )
        )

    def test_validated_runtime_service_receipt_separates_reports_and_enumeration(self) -> None:
        receipt = build_runtime_service_receipt(
            session={
                "session_id": "crucible-session:service-explorer",
                "launch_id": "launch:service-explorer",
                "profile_id": "workbench-platform:cleanroom:test",
                "side": "dedicated_server",
                "candidate_toolchain_lock_sha256": "a" * 64,
                "component_topology_receipt_id": (
                    COMPONENT_TOPOLOGY_RECEIPT_PREFIX + "b" * 64
                ),
                "component_topology_receipt_sha256": "c" * 64,
            },
            artifacts=[
                {
                    "artifact_sha256": "d" * 64,
                    "size_bytes": 100,
                    "role": "mixin_runtime",
                    "label": "mixin-runtime.jar",
                },
                {
                    "artifact_sha256": "e" * 64,
                    "size_bytes": 200,
                    "role": "service_provider",
                    "label": "provider.jar",
                },
            ],
            evidence=[
                {
                    "source_sha256": "f" * 64,
                    "size_bytes": 300,
                    "kind": "runtime_log",
                    "label": "debug.log",
                },
                {
                    "source_sha256": "1" * 64,
                    "size_bytes": 400,
                    "kind": "service_loader_enumeration",
                    "label": "providers.json",
                },
            ],
            observations=[
                {
                    "sequence": 1,
                    "kind": "selection_report",
                    "service_name": "NativeService",
                    "evidence_sha256": "f" * 64,
                    "source_record": "line:20",
                }
            ],
            provider_enumeration={
                "state": "enumerated",
                "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
                "providers": [
                    {
                        "service_class": "example.NativeService",
                        "service_name": "NativeService",
                        "provider_artifact_sha256": "e" * 64,
                    }
                ],
                "evidence_sha256": "1" * 64,
                "evidence_id": PROVIDER_ENUMERATION_EVIDENCE_PREFIX + "2" * 64,
                "source_record": PROVIDER_ENUMERATION_EVIDENCE_PREFIX + "2" * 64,
                "failure": None,
            },
            limitations=["Synthetic explorer fixture."],
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime-service.json"
            _write(path, json.dumps(receipt) + "\n")
            provider = receipt_provider(path)
            by_class = Explorer((provider.source,), provider.records).search(
                parse_query("class:example.NativeService")
            )
            by_name = Explorer((provider.source,), provider.records).search(
                parse_query("NativeService")
            )

        self.assertEqual(
            "crucible-mixin-runtime-service-receipt",
            provider.source.source_kind,
        )
        self.assertEqual("mixin-service-provider", by_class["matches"][0]["kind"])
        self.assertIn(
            "ServiceLoader enumeration proves provider visibility during the probe, not runtime selection.",
            by_class["matches"][0]["limitations"],
        )
        self.assertEqual(2, by_name["summary"]["entities"])
        self.assertTrue(
            all(len(match["facets"]) == 1 for match in by_name["matches"])
        )

    def test_selected_service_component_receipt_exposes_exact_roles_and_classes(self) -> None:
        receipt = _service_components_receipt()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-components.json"
            _write(path, json.dumps(receipt) + "\n")
            provider = receipt_provider(path)
            by_role = Explorer((provider.source,), provider.records).search(
                parse_query("bytecode_provider")
            )
            by_class = Explorer((provider.source,), provider.records).search(
                parse_query("class:example.BytecodeProvider")
            )

        self.assertEqual(
            "crucible-mixin-selected-service-components-receipt",
            provider.source.source_kind,
        )
        self.assertEqual("complete", provider.source.state)
        self.assertEqual(8, provider.source.coverage["components"])
        self.assertEqual("mixin-service-component", by_role["matches"][0]["kind"])
        self.assertEqual("example.BytecodeProvider", by_class["matches"][0]["name"])
        self.assertTrue(
            any(
                identity["kind"] == "artifact-sha256"
                and identity["value"] == "9" * 64
                for identity in by_class["matches"][0]["identities"]
            )
        )

    def test_config_lifecycle_receipt_exposes_owner_phase_and_terminal_state(self) -> None:
        receipt = _config_lifecycle_receipt()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config-lifecycle.json"
            _write(path, json.dumps(receipt) + "\n")
            provider = receipt_provider(path)
            by_config = Explorer((provider.source,), provider.records).search(
                parse_query("mixins.fixture.early.json")
            )
            by_owner = Explorer((provider.source,), provider.records).search(
                parse_query("fixture")
            )

        self.assertEqual(
            "crucible-mixin-config-lifecycle-receipt",
            provider.source.source_kind,
        )
        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, provider.source.coverage["configurations"])
        match = by_config["matches"][0]
        self.assertEqual("mixin-config-lifecycle", match["kind"])
        self.assertEqual("active", match["scopes"][0]["terminal_outcome"])
        self.assertEqual("DEFAULT", match["scopes"][0]["queued_phase"])
        self.assertTrue(
            any(
                identity["kind"] == "mod-id"
                and identity["value"] == "fixture"
                for identity in by_owner["matches"][0]["identities"]
            )
        )

    def test_transformer_chain_receipt_exposes_epoch_order_and_exclusions(self) -> None:
        receipt = _transformer_chain_receipt()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transformer-chain.json"
            _write(path, json.dumps(receipt) + "\n")
            provider = receipt_provider(path)
            by_transformer = Explorer((provider.source,), provider.records).search(
                parse_query("class:example.Transformer")
            )
            by_exclusion = Explorer((provider.source,), provider.records).search(
                parse_query("org.spongepowered.asm.")
            )

        self.assertEqual(
            "crucible-mixin-transformer-chain-epoch-receipt",
            provider.source.source_kind,
        )
        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, provider.source.coverage["epochs"])
        match = by_transformer["matches"][0]
        self.assertTrue(match["exact"])
        self.assertEqual("mixin-transformer-chain-entry", match["kind"])
        self.assertEqual(1, match["scopes"][0]["epoch"])
        self.assertEqual({"live", "delegated"}, {row["chain"] for row in match["scopes"]})
        self.assertEqual("mixin-transformer-chain-epoch", by_exclusion["matches"][0]["kind"])

    def test_final_definition_receipt_exposes_exact_final_bytes(self) -> None:
        receipt = _final_definition_receipt()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "final-definition.json"
            _write(path, json.dumps(receipt) + "\n")
            provider = receipt_provider(path)
            by_class = Explorer((provider.source,), provider.records).search(
                parse_query("class:example.Target")
            )
            by_digest = Explorer((provider.source,), provider.records).search(
                parse_query("c" * 64)
            )

        self.assertEqual(
            "crucible-mixin-final-class-definition-receipt",
            provider.source.source_kind,
        )
        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, provider.source.coverage["definitions"])
        self.assertTrue(by_class["matches"][0]["exact"])
        self.assertEqual(
            "mixin-final-class-definition", by_class["matches"][0]["kind"]
        )
        self.assertTrue(
            any(
                identity["kind"] == "final-class-sha256"
                for identity in by_digest["matches"][0]["identities"]
            )
        )

    def test_validated_runtime_snapshot_exposes_epoch_capability_coverage(self) -> None:
        receipt = build_runtime_service_receipt(
            session={
                "session_id": "crucible-session:snapshot-explorer",
                "launch_id": "launch:snapshot-explorer",
                "profile_id": "workbench-platform:cleanroom:test",
                "side": "dedicated_server",
                "candidate_toolchain_lock_sha256": "a" * 64,
                "component_topology_receipt_id": (
                    COMPONENT_TOPOLOGY_RECEIPT_PREFIX + "b" * 64
                ),
                "component_topology_receipt_sha256": "c" * 64,
            },
            artifacts=[
                {
                    "artifact_sha256": "d" * 64,
                    "size_bytes": 100,
                    "role": "mixin_runtime",
                    "label": "mixin-runtime.jar",
                }
            ],
            evidence=[
                {
                    "source_sha256": "e" * 64,
                    "size_bytes": 300,
                    "kind": "runtime_log",
                    "label": "debug.log",
                }
            ],
            observations=[
                {
                    "sequence": 1,
                    "kind": "selection_report",
                    "service_name": "CleanMix",
                    "evidence_sha256": "e" * 64,
                    "source_record": "line:20",
                }
            ],
            provider_enumeration={
                "state": "not_enumerated",
                "mechanism": None,
                "providers": [],
                "evidence_sha256": None,
                "evidence_id": None,
                "source_record": None,
                "failure": None,
            },
            limitations=["Synthetic explorer fixture."],
        )
        receipt_bytes = json.dumps(
            receipt,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        binding = bind_known_receipt(
            receipt_bytes,
            source_label="receipts/runtime-service.json",
        )
        snapshot = build_runtime_snapshot(
            runtime_identity={
                "launch_id": "launch:snapshot-explorer",
                "platform_profile_id": "workbench-platform:cleanroom:test",
                "pack_profile_id": None,
                "physical_side": "dedicated_server",
                "process_outcome": "complete",
                "candidate_lock_sha256": "1" * 64,
                "transformer_toolchain_lock_sha256": "a" * 64,
                "java_runtime_id": "eclipse-temurin-25.0.4+7",
                "session_ids": ["crucible-session:snapshot-explorer"],
                "capture_ids": [],
            },
            epoch={
                "profile_epoch_id": "cleanroom:0.6.8-alpha-foundation",
                "adapter_id": "workbench-cleanroom-runtime-adapter:foundation",
                "adapter_version": "2.0.0",
                "axes": [
                    {"name": "cleanmix.distribution", "value": "0.7.0"},
                    {"name": "cleanroom.version", "value": "0.6.8-alpha"},
                ],
                "capabilities": [
                    "mixin.final-class-definition",
                    "mixin.runtime-service",
                ],
            },
            receipt_bindings=[binding],
            capabilities=[
                capability_status(
                    "mixin.final-class-definition",
                    state="not_observed",
                    reason_code="producer-not-run",
                ),
                capability_from_receipts(
                    "mixin.runtime-service",
                    state="observed",
                    receipt_bindings=[binding],
                ),
            ],
            capture_health={
                "observer_state": "healthy",
                "started": True,
                "completed": True,
                "errors": [],
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime-snapshot.json"
            _write(path, json.dumps(snapshot) + "\n")
            provider = receipt_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("mixin.runtime-service")
            )

        self.assertEqual("crucible-runtime-snapshot-v2", provider.source.source_kind)
        self.assertEqual("partial", provider.source.state)
        self.assertEqual("runtime-capability", result["matches"][0]["kind"])
        self.assertEqual("mixin.runtime-service", result["matches"][0]["name"])
        self.assertEqual("observed", result["matches"][0]["states"][0])

    def test_semantic_receipt_tampering_fails_closed(self) -> None:
        ledger = build_ledger(
            session={
                "session_id": "crucible-session:explorer",
                "launch_id": "launch:explorer",
                "profile_id": "profile:test",
                "side": "dedicated_server",
                "component_receipt_sha256": "a" * 64,
            },
            launch_state="complete",
            observations=[],
        )
        ledger["summary"]["observation_count"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tampered.json"
            _write(path, json.dumps(ledger) + "\n")
            with self.assertRaisesRegex(ExplorerError, "semantic validation failed"):
                receipt_provider(path)

    def test_raw_log_exposes_stack_frame_as_presentation_not_causality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "latest.log"
            path.write_bytes(
                b"java.lang.IllegalStateException: broken\n"
                b"\tat example.ExampleMod.register(ExampleMod.java:42)\n"
            )
            provider = raw_log_provider(path)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("example.ExampleMod#register")
            )

        self.assertGreaterEqual(result["summary"]["entities"], 1)
        facet = result["matches"][0]["facets"][0]
        self.assertEqual("presentation-observation", facet["state"])
        self.assertEqual("Workbench Shell", facet["authority"])
        self.assertEqual("unresolved", facet["owner"]["state"])

    def test_console_session_is_semantically_checked_against_its_events(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            session = RetainedSession(
                root=root,
                command_id="fixture.console",
                argv=["fixture"],
                cwd=root,
                intent="inspect",
                session_id="explorer-console-session",
            )
            message = "at example.ExampleMod.register(ExampleMod.java:42)"
            retained = session.write_raw("stdout", message.encode("utf-8"))
            event = EventNormalizer(root=root).normalize(
                message,
                source="fixture",
                stream="stdout",
                raw_locator=EventRawLocator(
                    artifact=retained.path,
                    byte_start=retained.byte_start,
                    byte_end=retained.byte_end,
                    line=1,
                    boundary="eof",
                ),
            )
            session.record_event(event)
            session.finish(
                state="complete",
                process_exit_code=0,
                effective_exit_code=0,
                outcome="complete",
            )
            provider = console_session_provider(session.directory)
            result = Explorer((provider.source,), provider.records).search(
                parse_query("example.ExampleMod#register")
            )
            manifest_path = session.directory / "session-v1.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["summary"]["event_count"] = 2
            _write(manifest_path, json.dumps(manifest) + "\n")
            with self.assertRaisesRegex(ExplorerError, "disagrees"):
                console_session_provider(session.directory)

        self.assertEqual("complete", provider.source.state)
        self.assertEqual(1, result["summary"]["entities"])
        self.assertEqual(
            "presentation-observation",
            result["matches"][0]["states"][0],
        )


if __name__ == "__main__":
    unittest.main()
