"""Identity and semantic validation for language-service result V1."""

from __future__ import annotations

from collections import Counter
import hashlib
import math
from pathlib import PurePosixPath
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .model import PackProgramError, canonical_bytes, content_id


LANGUAGE_RESULT_FORMAT = "workbench-groovy-language-service-result-v1"
LANGUAGE_RESULT_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PROGRAM_ID_RE = re.compile(
    r"^workbench-groovy-static-program:sha256:[0-9a-f]{64}$"
)
_MAX_RESULT_FILES = 4096
_MAX_RESULT_DIAGNOSTICS = 1_000_000
_MAX_TRANSCRIPT_MESSAGES = 1_000_000


def language_result_identity(result: Mapping[str, Any]) -> str:
    payload = dict(result)
    payload.pop("result_id", None)
    return content_id("workbench-groovy-language-service-result:sha256:", payload)


def diagnostic_identity(path: str, source_sha256: str, diagnostic: Mapping[str, Any]) -> str:
    payload = {
        "path": path,
        "source_sha256": source_sha256,
        "range": diagnostic["range"],
        "severity": diagnostic["severity"],
        "code": diagnostic["code"],
        "source": diagnostic["source"],
        "message": diagnostic["message"],
    }
    return content_id("workbench-groovy-diagnostic:sha256:", payload)


def validate_language_result(result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise PackProgramError("Groovy language-service result must be an object")
    required = {
        "format",
        "schema_version",
        "result_id",
        "operation_class",
        "observed_at",
        "authority",
        "profile",
        "request",
        "program",
        "runtime",
        "service",
        "summary",
        "limitations",
        "horizons",
    }
    if set(result) != required:
        raise PackProgramError("Groovy language-service result has unexpected keys")
    if (
        result["format"] != LANGUAGE_RESULT_FORMAT
        or result["schema_version"] != LANGUAGE_RESULT_SCHEMA_VERSION
    ):
        raise PackProgramError("unsupported Groovy language-service result format")
    if result["operation_class"] != "read-only-network-observation":
        raise PackProgramError("Groovy language-service operation class is invalid")
    if result["result_id"] != language_result_identity(result):
        raise PackProgramError("Groovy language-service result identity is stale")

    _bounded_text(result["observed_at"], "observation time", 256)
    expected_authority = {
        "orchestrator": "Workbench Pack Program Studio",
        "compiler_observation": "GroovyScript embedded language server",
        "runtime_inventory": "Workbench exact local byte inventory",
        "endpoint_identity": "unavailable in the upstream protocol",
        "runtime_effects": "none; Atlas observation not implied",
        "construction": "none; Blueprints remains construction authority",
    }
    if result["authority"] != expected_authority:
        raise PackProgramError("Groovy language-service authority boundary is invalid")

    profile = _validate_profile(result["profile"])
    program = _validate_program(result["program"], profile["bounds"])
    runtime = _validate_runtime(result["runtime"], program, profile)
    service = _validate_service(
        result["service"], program["selected_files"], profile["bounds"]
    )
    summary = _exact_mapping(
        result["summary"],
        {
            "status",
            "compiler_state",
            "selected_files",
            "checked_files",
            "diagnostics",
            "severity_counts",
            "file_states",
            "runtime_binding",
        },
        "Groovy language-service summary",
    )
    _validate_request(result["request"], program, runtime, service)
    limitations = result["limitations"]
    if not isinstance(limitations, list) or not limitations or len(limitations) > 256:
        raise PackProgramError("Groovy language-service limitations are malformed")
    for limitation in limitations:
        _bounded_text(limitation, "language-service limitation", 8192)
    horizons = result["horizons"]
    if (
        not isinstance(horizons, list)
        or len(horizons) > 256
        or any(not isinstance(row, Mapping) for row in horizons)
    ):
        raise PackProgramError("Groovy language-service horizons are malformed")

    selected = program.get("selected_files")
    checked = service.get("files")
    selected_by_path: dict[str, Mapping[str, Any]] = {}
    for row in selected:
        if row["path"] in selected_by_path:
            raise PackProgramError("selected Groovy file path is duplicate")
        selected_by_path[row["path"]] = row
    checked_paths: set[str] = set()
    diagnostic_count = 0
    severity_counts: Counter[str] = Counter()
    states: Counter[str] = Counter()
    for row in checked:
        if row.get("path") not in selected_by_path:
            raise PackProgramError("checked Groovy file is not selected")
        path = row["path"]
        if path in checked_paths:
            raise PackProgramError("checked Groovy file path is duplicate")
        checked_paths.add(path)
        source = selected_by_path[path]
        if any(
            row.get(key) != source.get(key)
            for key in (
                "sha256",
                "server_uri",
                "size",
                "stage",
                "execution_state",
            )
        ):
            raise PackProgramError("checked Groovy source binding is stale")
        diagnostics = row.get("diagnostics")
        if not isinstance(diagnostics, list) or row.get("diagnostic_count") != len(diagnostics):
            raise PackProgramError("checked Groovy diagnostics count is stale")
        states[str(row.get("state"))] += 1
        for diagnostic in diagnostics:
            if diagnostic.get("diagnostic_id") != diagnostic_identity(
                path, str(row["sha256"]), diagnostic
            ):
                raise PackProgramError("Groovy diagnostic identity is stale")
            diagnostic_count += 1
            severity_counts[str(diagnostic.get("severity"))] += 1

    expected_status, expected_compiler_state = _expected_state(service, checked, diagnostic_count)
    expected = {
        "status": expected_status,
        "compiler_state": expected_compiler_state,
        "selected_files": len(selected),
        "checked_files": len(checked),
        "diagnostics": diagnostic_count,
        "severity_counts": dict(sorted(severity_counts.items())),
        "file_states": dict(sorted(states.items())),
        "runtime_binding": service.get("endpoint", {}).get("identity_binding"),
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            raise PackProgramError(f"Groovy language-service summary {key} is stale")
    if runtime.get("run_config_sha256") != program.get("run_config_sha256"):
        raise PackProgramError("runtime and candidate runConfig bindings differ")
    if profile["platform_profile_id"] != program["platform_profile_id"]:
        raise PackProgramError("language-service profile/platform binding is stale")
    return dict(result)


def _validate_profile(value: Any) -> Mapping[str, Any]:
    expected = {
        "pack_program_profile_id",
        "pack_program_profile_sha256",
        "language_service_profile_id",
        "language_service_profile_path",
        "language_service_profile_sha256",
        "platform_profile_id",
        "groovyscript",
        "server_semantics",
        "runtime_layout",
        "bounds",
        "canary",
    }
    profile = _exact_mapping(value, expected, "language-service result profile")
    for key in (
        "pack_program_profile_id",
        "language_service_profile_id",
        "language_service_profile_path",
        "platform_profile_id",
    ):
        _bounded_text(profile[key], f"language-service profile {key}", 8192)
    for key in ("pack_program_profile_sha256", "language_service_profile_sha256"):
        _digest(profile[key], f"language-service profile {key}")

    groovy = _exact_mapping(
        profile["groovyscript"],
        {"version", "artifact_filename", "artifact_sha256", "artifact_size", "source_commit"},
        "result GroovyScript identity",
    )
    _bounded_text(groovy["version"], "GroovyScript version", 256)
    filename = _bounded_text(
        groovy["artifact_filename"], "GroovyScript artifact filename", 1024
    )
    if PurePosixPath(filename).name != filename:
        raise PackProgramError("GroovyScript artifact filename must be a basename")
    _digest(groovy["artifact_sha256"], "GroovyScript artifact hash")
    _positive_integer(groovy["artifact_size"], "GroovyScript artifact size")
    if not isinstance(groovy["source_commit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}", groovy["source_commit"]
    ):
        raise PackProgramError("GroovyScript source commit is malformed")

    server = _exact_mapping(
        profile["server_semantics"],
        {
            "transport",
            "default_host",
            "default_port",
            "runtime_side",
            "start_property",
            "compile_trigger",
            "diagnostic_method",
            "text_document_sync",
            "compilation_phase",
            "endpoint_identity_protocol",
        },
        "result language-server semantics",
    )
    constants = {
        "transport": "lsp-jsonrpc-tcp",
        "runtime_side": "client",
        "compile_trigger": "textDocument/documentSymbol",
        "diagnostic_method": "textDocument/publishDiagnostics",
        "text_document_sync": "full",
        "compilation_phase": "canonicalization",
        "endpoint_identity_protocol": "unavailable",
    }
    if any(server.get(key) != expected_value for key, expected_value in constants.items()):
        raise PackProgramError("result language-server semantics are unsupported")
    _bounded_text(server["default_host"], "language-server default host", 1024)
    _bounded_text(server["start_property"], "language-server start property", 1024)
    _port(server["default_port"], "language-server default port")

    layout = _exact_mapping(
        profile["runtime_layout"],
        {
            "artifact_relative_path",
            "mods_directory",
            "groovy_directory",
            "run_config",
            "cache_directory",
        },
        "result language-service runtime layout",
    )
    for key, path in layout.items():
        _safe_relative_path(path, f"language-service runtime layout {key}")
    bounds = _exact_mapping(
        profile["bounds"],
        {
            "max_files",
            "max_source_bytes",
            "max_total_source_bytes",
            "max_header_bytes",
            "max_message_bytes",
            "max_transcript_messages",
            "max_diagnostics",
            "max_runtime_artifacts",
            "max_runtime_artifact_bytes",
        },
        "result language-service bounds",
    )
    for key, bound in bounds.items():
        _positive_integer(bound, f"language-service bound {key}")
    if (
        bounds["max_files"] > _MAX_RESULT_FILES
        or bounds["max_source_bytes"] > 64 * 1024 * 1024
        or bounds["max_total_source_bytes"] > 512 * 1024 * 1024
        or bounds["max_total_source_bytes"] < bounds["max_source_bytes"]
        or not 1024 <= bounds["max_header_bytes"] <= 1024 * 1024
        or not 1024 <= bounds["max_message_bytes"] <= 256 * 1024 * 1024
        or not 16 <= bounds["max_transcript_messages"] <= _MAX_TRANSCRIPT_MESSAGES
        or bounds["max_diagnostics"] > _MAX_RESULT_DIAGNOSTICS
        or bounds["max_runtime_artifacts"] > 4096
        or bounds["max_runtime_artifact_bytes"] > 4 * 1024 * 1024 * 1024
    ):
        raise PackProgramError("result language-service bounds are excessive")
    canary = _exact_mapping(
        profile["canary"],
        {"prefix", "required_severity"},
        "result language-service canary",
    )
    _text(canary["prefix"], "language-service canary prefix", 1024)
    if canary["required_severity"] != 1:
        raise PackProgramError("result language-service canary severity is unsupported")
    return profile


def _validate_program(
    value: Any, bounds: Mapping[str, Any]
) -> Mapping[str, Any]:
    expected = {
        "program_id",
        "source_sha256",
        "run_config_sha256",
        "pack_profile_id",
        "platform_profile_id",
        "physical_side",
        "packmode",
        "debug",
        "selected_files",
    }
    program = _exact_mapping(value, expected, "language-service candidate program")
    if not isinstance(program["program_id"], str) or not _PROGRAM_ID_RE.fullmatch(
        program["program_id"]
    ):
        raise PackProgramError("candidate Groovy program identity is malformed")
    _digest(program["source_sha256"], "candidate source hash")
    _digest(program["run_config_sha256"], "candidate runConfig hash")
    for key in ("pack_profile_id", "platform_profile_id"):
        _bounded_text(program[key], f"candidate {key}", 1024)
    if program["physical_side"] != "client":
        raise PackProgramError("Groovy language service requires the client physical side")
    if program["packmode"] is not None:
        _bounded_text(program["packmode"], "candidate packmode", 1024)
    if not isinstance(program["debug"], bool):
        raise PackProgramError("candidate debug state must be boolean")
    selected = program["selected_files"]
    if (
        not isinstance(selected, list)
        or not selected
        or len(selected) > bounds["max_files"]
    ):
        raise PackProgramError("selected Groovy file set is malformed or excessive")
    seen_paths: set[str] = set()
    seen_uris: set[str] = set()
    total_source_bytes = 0
    for row in selected:
        source = _exact_mapping(
            row,
            {
                "path",
                "absolute_path",
                "server_uri",
                "sha256",
                "size",
                "stage",
                "execution_state",
            },
            "selected Groovy file",
        )
        path = _safe_relative_path(source["path"], "selected Groovy path")
        if path in seen_paths:
            raise PackProgramError("selected Groovy file path is duplicate")
        seen_paths.add(path)
        _bounded_text(source["absolute_path"], "selected Groovy absolute path", 8192)
        uri = _file_uri(source["server_uri"], "selected Groovy server URI")
        if uri in seen_uris:
            raise PackProgramError("selected Groovy server URI is duplicate")
        seen_uris.add(uri)
        _digest(source["sha256"], "selected Groovy source hash")
        _nonnegative_integer(source["size"], "selected Groovy source size")
        if source["size"] > bounds["max_source_bytes"]:
            raise PackProgramError("selected Groovy source exceeds the profile bound")
        total_source_bytes += source["size"]
        _bounded_text(source["stage"], "selected Groovy stage", 1024)
        if source["execution_state"] not in {"enabled", "conditional", "excluded"}:
            raise PackProgramError("selected Groovy execution state is invalid")
    if total_source_bytes > bounds["max_total_source_bytes"]:
        raise PackProgramError("selected Groovy source set exceeds the profile bound")
    return program


def _validate_runtime(
    value: Any,
    program: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> Mapping[str, Any]:
    runtime = _exact_mapping(
        value,
        {
            "runtime_id",
            "root",
            "run_config_path",
            "run_config_sha256",
            "groovyscript_artifact",
            "mod_graph",
            "class_cache",
            "java",
            "launch_receipt",
            "endpoint_binding",
        },
        "language-service runtime inventory",
    )
    _bounded_text(runtime["root"], "runtime root", 8192)
    _bounded_text(runtime["run_config_path"], "runtime runConfig path", 8192)
    _digest(runtime["run_config_sha256"], "runtime runConfig hash")
    if runtime["run_config_sha256"] != program["run_config_sha256"]:
        raise PackProgramError("runtime and candidate runConfig bindings differ")
    if runtime["endpoint_binding"] != (
        "caller-context-only; upstream endpoint has no identity challenge"
    ):
        raise PackProgramError("runtime endpoint binding claim is invalid")
    layout = profile["runtime_layout"]
    normalized_run_config = str(runtime["run_config_path"]).replace("\\", "/")
    if not normalized_run_config.endswith("/" + layout["run_config"]):
        raise PackProgramError("runtime runConfig path/profile layout binding is stale")

    mod_graph = _exact_mapping(
        runtime["mod_graph"],
        {"mod_graph_id", "artifacts", "artifact_count", "total_bytes"},
        "runtime mod graph",
    )
    artifacts = mod_graph["artifacts"]
    bounds = profile["bounds"]
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or len(artifacts) > bounds["max_runtime_artifacts"]
    ):
        raise PackProgramError("runtime mod artifact set is malformed or excessive")
    paths: set[str] = set()
    total_bytes = 0
    for artifact in artifacts:
        admitted = _validate_artifact(artifact)
        if admitted["size"] > bounds["max_runtime_artifact_bytes"]:
            raise PackProgramError("runtime mod artifact exceeds the profile bound")
        if admitted["path"] in paths:
            raise PackProgramError("runtime mod artifact path is duplicate")
        paths.add(admitted["path"])
        total_bytes += admitted["size"]
    expected_graph_id = content_id(
        "workbench-groovy-runtime-mod-graph:sha256:", artifacts
    )
    if (
        mod_graph["mod_graph_id"] != expected_graph_id
        or mod_graph["artifact_count"] != len(artifacts)
        or mod_graph["total_bytes"] != total_bytes
    ):
        raise PackProgramError("runtime mod graph identity or counts are stale")
    exact_artifact = _validate_artifact(runtime["groovyscript_artifact"])
    if exact_artifact not in artifacts:
        raise PackProgramError("exact GroovyScript artifact is absent from the mod graph")
    groovy = profile["groovyscript"]
    if (
        exact_artifact["path"] != layout["artifact_relative_path"]
        or PurePosixPath(exact_artifact["path"]).name != groovy["artifact_filename"]
        or exact_artifact["sha256"] != groovy["artifact_sha256"]
        or exact_artifact["size"] != groovy["artifact_size"]
    ):
        raise PackProgramError("runtime GroovyScript artifact/profile binding is stale")

    cache = _exact_mapping(
        runtime["class_cache"],
        {
            "state",
            "path",
            "file_count",
            "total_bytes",
            "tree_sha256",
            "cache_version",
            "content_addressed_by_upstream",
            "upstream_identity_inputs",
        },
        "Groovy class-cache inventory",
    )
    if cache["state"] not in {"present", "absent"}:
        raise PackProgramError("Groovy class-cache state is invalid")
    _bounded_text(cache["path"], "Groovy class-cache path", 8192)
    _nonnegative_integer(cache["file_count"], "Groovy class-cache file count")
    _nonnegative_integer(cache["total_bytes"], "Groovy class-cache bytes")
    if cache["state"] == "present":
        _digest(cache["tree_sha256"], "Groovy class-cache tree hash")
    elif any(
        (cache["file_count"], cache["total_bytes"], cache["tree_sha256"])
    ):
        raise PackProgramError("absent Groovy class-cache counts are inconsistent")
    if (
        cache["cache_version"] != 4
        or cache["content_addressed_by_upstream"] is not False
        or cache["upstream_identity_inputs"]
        != ["source last-modified time", "Java version"]
    ):
        raise PackProgramError("Groovy class-cache identity policy is invalid")

    java = _exact_mapping(
        runtime["java"],
        {"state", "sha256", "size", "path", "version_output"},
        "runtime Java identity",
    )
    if java["state"] == "not-supplied":
        if any(java[key] is not None for key in ("sha256", "size", "path", "version_output")):
            raise PackProgramError("unsupplied Java identity contains values")
    elif java["state"] == "caller-supplied-exact-bytes":
        _digest(java["sha256"], "runtime Java hash")
        _positive_integer(java["size"], "runtime Java size")
        _bounded_text(java["path"], "runtime Java path", 8192)
        _text(java["version_output"], "runtime Java version output", 8192)
    else:
        raise PackProgramError("runtime Java identity state is invalid")

    receipt = _exact_mapping(
        runtime["launch_receipt"],
        {"state", "path", "sha256", "size", "format", "receipt_id", "semantic_validation"},
        "runtime launch-receipt context",
    )
    if receipt["state"] == "not-supplied":
        if any(receipt[key] is not None for key in ("path", "sha256", "size", "format", "receipt_id")):
            raise PackProgramError("unsupplied launch receipt contains values")
        if receipt["semantic_validation"] != "not-performed":
            raise PackProgramError("unsupplied launch-receipt state is inconsistent")
    elif receipt["state"] == "caller-supplied-context":
        _bounded_text(receipt["path"], "runtime launch-receipt path", 8192)
        _digest(receipt["sha256"], "runtime launch-receipt hash")
        _positive_integer(receipt["size"], "runtime launch-receipt size")
        for key in ("format", "receipt_id"):
            if receipt[key] is not None:
                _bounded_text(receipt[key], f"runtime launch-receipt {key}", 8192)
        if receipt["semantic_validation"] != (
            "not-performed; endpoint protocol cannot present this receipt"
        ):
            raise PackProgramError("runtime launch-receipt context claim is invalid")
    else:
        raise PackProgramError("runtime launch-receipt state is invalid")

    runtime_payload = {
        "run_config_sha256": runtime["run_config_sha256"],
        "mod_graph_id": mod_graph["mod_graph_id"],
        "cache_tree_sha256": cache["tree_sha256"],
        "java_sha256": java["sha256"],
        "receipt_sha256": receipt["sha256"],
    }
    expected_runtime_id = content_id(
        "workbench-groovy-language-runtime:sha256:", runtime_payload
    )
    if runtime["runtime_id"] != expected_runtime_id:
        raise PackProgramError("language-service runtime identity is stale")
    return runtime


def _validate_service(
    value: Any,
    selected: list[Mapping[str, Any]],
    bounds: Mapping[str, Any],
) -> Mapping[str, Any]:
    service = _exact_mapping(
        value,
        {"state", "endpoint", "workspace_uri", "capabilities", "files", "failure", "transcript"},
        "Groovy language-service observation",
    )
    if service["state"] not in {"completed", "partial", "blocked"}:
        raise PackProgramError("Groovy language-service state is invalid")
    endpoint = _exact_mapping(
        service["endpoint"],
        {"host", "port", "transport", "connect_ms", "identity_binding"},
        "Groovy language-service endpoint",
    )
    _bounded_text(endpoint["host"], "language-service endpoint host", 1024)
    _port(endpoint["port"], "language-service endpoint port")
    if endpoint["transport"] != "lsp-jsonrpc-tcp" or endpoint[
        "identity_binding"
    ] != "unavailable-upstream-protocol":
        raise PackProgramError("language-service endpoint semantics are invalid")
    if endpoint["connect_ms"] is not None:
        _nonnegative_integer(endpoint["connect_ms"], "language-service connect latency")
    _file_uri(service["workspace_uri"], "language-service workspace URI")
    if service["capabilities"] is not None and not isinstance(
        service["capabilities"], Mapping
    ):
        raise PackProgramError("language-service capabilities are malformed")

    files = service["files"]
    if not isinstance(files, list) or len(files) > len(selected):
        raise PackProgramError("checked Groovy file set is malformed")
    total_diagnostics = 0
    for index, row in enumerate(files):
        checked = _exact_mapping(
            row,
            {
                "path",
                "server_uri",
                "sha256",
                "size",
                "stage",
                "execution_state",
                "state",
                "canary",
                "diagnostics",
                "diagnostic_count",
                "symbol_count",
                "request_error",
                "compile_latency_ms",
            },
            "checked Groovy file",
        )
        source = selected[index]
        if any(
            checked[key] != source[key]
            for key in (
                "path",
                "server_uri",
                "sha256",
                "size",
                "stage",
                "execution_state",
            )
        ):
            raise PackProgramError("checked Groovy files are not the selected prefix")
        canary = _exact_mapping(
            checked["canary"],
            {"state", "diagnostic_count", "latency_ms"},
            "Groovy diagnostic canary",
        )
        if canary["state"] != "confirmed":
            raise PackProgramError("Groovy diagnostic canary is not confirmed")
        _positive_integer(canary["diagnostic_count"], "canary diagnostic count")
        _nonnegative_integer(canary["latency_ms"], "canary latency")
        diagnostics = checked["diagnostics"]
        if not isinstance(diagnostics, list):
            raise PackProgramError("checked Groovy diagnostics are malformed")
        for diagnostic in diagnostics:
            _validate_diagnostic(diagnostic)
        if checked["diagnostic_count"] != len(diagnostics):
            raise PackProgramError("checked Groovy diagnostic count is stale")
        total_diagnostics += len(diagnostics)
        total_diagnostics += canary["diagnostic_count"]
        if total_diagnostics > bounds["max_diagnostics"]:
            raise PackProgramError("Groovy language-service diagnostics are excessive")
        if checked["symbol_count"] is not None:
            _nonnegative_integer(checked["symbol_count"], "Groovy symbol count")
        if checked["request_error"] is not None:
            _text(checked["request_error"], "Groovy compiler request error", 2048)
        _nonnegative_integer(checked["compile_latency_ms"], "Groovy compile latency")
        expected_state = (
            "inconclusive"
            if checked["request_error"] is not None
            else "diagnostics"
            if diagnostics
            else "no-diagnostics"
        )
        if checked["state"] != expected_state:
            raise PackProgramError("checked Groovy compiler state is stale")

    failure = service["failure"]
    if failure is not None:
        admitted_failure = _exact_mapping(
            failure,
            {"kind", "message", "checked_files"},
            "language-service failure",
        )
        _bounded_text(admitted_failure["kind"], "language-service failure kind", 256)
        _text(admitted_failure["message"], "language-service failure message", 2048)
        if admitted_failure["checked_files"] != len(files):
            raise PackProgramError("language-service failure file count is stale")
    if service["state"] == "completed":
        if failure is not None or len(files) != len(selected):
            raise PackProgramError("completed language-service coverage is inconsistent")
    elif service["state"] == "partial":
        if failure is None or not files:
            raise PackProgramError("partial language-service coverage is inconsistent")
    elif failure is None or files:
        raise PackProgramError("blocked language-service coverage is inconsistent")
    if service["state"] != "blocked" and endpoint["connect_ms"] is None:
        raise PackProgramError("language-service connection state is inconsistent")
    _validate_transcript(service["transcript"], bounds)
    return service


def _validate_diagnostic(value: Any) -> Mapping[str, Any]:
    diagnostic = _exact_mapping(
        value,
        {"diagnostic_id", "range", "severity", "code", "source", "message"},
        "Groovy language-service diagnostic",
    )
    diagnostic_range = _exact_mapping(
        diagnostic["range"], {"start", "end"}, "Groovy diagnostic range"
    )
    positions: dict[str, tuple[int, int]] = {}
    for endpoint in ("start", "end"):
        position = _exact_mapping(
            diagnostic_range[endpoint], {"line", "character"}, "Groovy diagnostic position"
        )
        _nonnegative_integer(position["line"], "Groovy diagnostic line")
        _nonnegative_integer(position["character"], "Groovy diagnostic character")
        positions[endpoint] = (position["line"], position["character"])
    if positions["end"] < positions["start"]:
        raise PackProgramError("Groovy diagnostic range is reversed")
    severity = diagnostic["severity"]
    if severity is not None and (
        isinstance(severity, bool) or severity not in {1, 2, 3, 4}
    ):
        raise PackProgramError("Groovy diagnostic severity is invalid")
    code = diagnostic["code"]
    if isinstance(code, bool) or not isinstance(code, (str, int, type(None))):
        raise PackProgramError("Groovy diagnostic code is invalid")
    if isinstance(code, str):
        _text(code, "Groovy diagnostic code", 256)
    if diagnostic["source"] is not None:
        _text(diagnostic["source"], "Groovy diagnostic source", 256)
    _text(diagnostic["message"], "Groovy diagnostic message", 8192)
    return diagnostic


def _validate_transcript(value: Any, bounds: Mapping[str, Any]) -> None:
    transcript = _exact_mapping(
        value,
        {"messages", "message_count", "method_counts", "transcript_sha256"},
        "language-service transcript",
    )
    messages = transcript["messages"]
    if (
        not isinstance(messages, list)
        or len(messages) > bounds["max_transcript_messages"]
    ):
        raise PackProgramError("language-service transcript is malformed or excessive")
    method_counts: Counter[str] = Counter()
    for sequence, row in enumerate(messages, 1):
        message = _exact_mapping(
            row,
            {"sequence", "direction", "kind", "method", "id", "size", "sha256"},
            "language-service transcript message",
        )
        if message["sequence"] != sequence:
            raise PackProgramError("language-service transcript sequence is stale")
        if message["direction"] not in {"inbound", "outbound"}:
            raise PackProgramError("language-service transcript direction is invalid")
        if message["kind"] not in {"request", "notification", "response", "error-response"}:
            raise PackProgramError("language-service transcript message kind is invalid")
        if message["method"] is not None:
            method = _bounded_text(
                message["method"], "language-service transcript method", 1024
            )
            method_counts[method] += 1
        if message["id"] is not None and (
            isinstance(message["id"], bool)
            or not isinstance(message["id"], (int, str))
        ):
            raise PackProgramError("language-service transcript message id is invalid")
        _positive_integer(message["size"], "language-service transcript message size")
        if message["size"] > bounds["max_message_bytes"]:
            raise PackProgramError("language-service transcript message exceeds the profile bound")
        _digest(message["sha256"], "language-service transcript message hash")
    if transcript["message_count"] != len(messages):
        raise PackProgramError("language-service transcript message count is stale")
    if transcript["method_counts"] != dict(sorted(method_counts.items())):
        raise PackProgramError("language-service transcript method counts are stale")
    expected_sha256 = hashlib.sha256(canonical_bytes(messages)).hexdigest()
    if transcript["transcript_sha256"] != expected_sha256:
        raise PackProgramError("language-service transcript identity is stale")


def _validate_request(
    value: Any,
    program: Mapping[str, Any],
    runtime: Mapping[str, Any],
    service: Mapping[str, Any],
) -> None:
    request = _exact_mapping(
        value,
        {
            "source",
            "runtime_root",
            "selected_paths",
            "all",
            "host",
            "port",
            "server_workspace_uri",
            "allow_remote",
            "connect_timeout_seconds",
            "diagnostic_timeout_seconds",
            "java",
            "runtime_receipt",
        },
        "Groovy language-service request",
    )
    _bounded_text(request["source"], "language-service source path", 8192)
    _bounded_text(request["runtime_root"], "language-service runtime root", 8192)
    if request["runtime_root"] != runtime["root"]:
        raise PackProgramError("language-service request/runtime root binding is stale")
    if not isinstance(request["all"], bool) or not isinstance(
        request["allow_remote"], bool
    ):
        raise PackProgramError("language-service request flags are malformed")
    selected_paths = request["selected_paths"]
    if request["all"]:
        if selected_paths is not None:
            raise PackProgramError("all-files request also contains selected paths")
    else:
        if not isinstance(selected_paths, list) or selected_paths != [
            row["path"] for row in program["selected_files"]
        ]:
            raise PackProgramError("language-service requested path binding is stale")
    if request["host"] != service["endpoint"]["host"] or request["port"] != service[
        "endpoint"
    ]["port"]:
        raise PackProgramError("language-service request/endpoint binding is stale")
    if request["server_workspace_uri"] != service["workspace_uri"]:
        raise PackProgramError("language-service workspace URI binding is stale")
    for key in ("connect_timeout_seconds", "diagnostic_timeout_seconds"):
        number = request[key]
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or number <= 0
            or number > 3600
        ):
            raise PackProgramError(f"language-service request {key} is invalid")
    for key in ("java", "runtime_receipt"):
        if request[key] is not None:
            _bounded_text(request[key], f"language-service request {key}", 8192)
    if (request["java"] is None) != (runtime["java"]["state"] == "not-supplied"):
        raise PackProgramError("language-service request/Java binding is stale")
    if (request["runtime_receipt"] is None) != (
        runtime["launch_receipt"]["state"] == "not-supplied"
    ):
        raise PackProgramError("language-service request/launch-receipt binding is stale")


def _validate_artifact(value: Any) -> Mapping[str, Any]:
    artifact = _exact_mapping(
        value, {"path", "sha256", "size"}, "runtime mod artifact"
    )
    _safe_relative_path(artifact["path"], "runtime mod artifact path")
    _digest(artifact["sha256"], "runtime mod artifact hash")
    _positive_integer(artifact["size"], "runtime mod artifact size")
    return artifact


def _exact_mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PackProgramError(f"{label} has unexpected or missing keys")
    return value


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise PackProgramError(f"{label} must be bounded text")
    return value


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    text = _text(value, label, maximum)
    if "\r" in text or "\n" in text:
        raise PackProgramError(f"{label} must be single-line text")
    return text


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise PackProgramError(f"{label} is malformed")
    return value


def _nonnegative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PackProgramError(f"{label} must be a nonnegative integer")
    return value


def _positive_integer(value: Any, label: str) -> int:
    result = _nonnegative_integer(value, label)
    if result == 0:
        raise PackProgramError(f"{label} must be positive")
    return result


def _port(value: Any, label: str) -> int:
    result = _positive_integer(value, label)
    if result > 65535:
        raise PackProgramError(f"{label} must be at most 65535")
    return result


def _safe_relative_path(value: Any, label: str) -> str:
    text = _bounded_text(value, label, 8192)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise PackProgramError(f"{label} must be a safe relative path")
    return text


def _file_uri(value: Any, label: str) -> str:
    text = _bounded_text(value, label, 16384)
    parsed = urlsplit(text)
    if parsed.scheme.casefold() != "file" or not parsed.path or parsed.query or parsed.fragment:
        raise PackProgramError(f"{label} must be an absolute file URI")
    return text


def _expected_state(
    service: Mapping[str, Any],
    checked: list[Any],
    diagnostics: int,
) -> tuple[str, str]:
    service_state = service.get("state")
    if service_state == "blocked" or not checked:
        return "blocked", "blocked"
    if service_state != "completed" or any(
        isinstance(row, Mapping) and row.get("state") == "inconclusive"
        for row in checked
    ):
        return "attention", "inconclusive"
    if diagnostics:
        return "attention", "diagnostics"
    return "ready", "no-diagnostics"


__all__ = [
    "LANGUAGE_RESULT_FORMAT",
    "LANGUAGE_RESULT_SCHEMA_VERSION",
    "diagnostic_identity",
    "language_result_identity",
    "validate_language_result",
]
