"""Exact-input broker for GroovyScript's embedded language service."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import ipaddress
import math
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlsplit

from .analyzer import AnalysisContext, analyze_program
from .language_model import (
    LANGUAGE_RESULT_FORMAT,
    LANGUAGE_RESULT_SCHEMA_VERSION,
    diagnostic_identity,
    language_result_identity,
    validate_language_result,
)
from .language_profile import LoadedLanguageProfile
from .lsp import LspLimits, probe_language_server
from .model import PackProgramError, canonical_bytes, content_id
from .profile import LoadedProfile, safe_regular_bytes, strict_json_file


_MAX_CACHE_FILES = 250_000
_MAX_CACHE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_RUNTIME_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024


def build_language_service_result(
    *,
    source: Path,
    pack_profile: LoadedProfile,
    language_profile: LoadedLanguageProfile,
    context: AnalysisContext,
    runtime_root: Path,
    selected_paths: Sequence[str] | None,
    select_all: bool,
    host: str | None,
    port: int | None,
    server_workspace_uri: str | None,
    allow_remote: bool,
    connect_timeout: float,
    diagnostic_timeout: float,
    java: Path | None = None,
    runtime_receipt: Path | None = None,
) -> dict[str, Any]:
    if context.side != "client":
        raise PackProgramError(
            "GroovyScript 1.4.3 language-service checks require client side"
        )
    if not isinstance(select_all, bool) or not isinstance(allow_remote, bool):
        raise PackProgramError("language-service selection/disclosure flags must be boolean")
    connect_timeout = _validated_timeout(connect_timeout, "connect timeout")
    diagnostic_timeout = _validated_timeout(
        diagnostic_timeout, "diagnostic timeout"
    )
    program = analyze_program(source, pack_profile, context=context)
    profile_value = language_profile.value
    bounds = profile_value["bounds"]
    if pack_profile.platform_profile_id != language_profile.platform_profile_id:
        raise PackProgramError(
            "pack-program and language-service platform profiles do not match"
        )
    selected = _select_files(
        program,
        selected_paths=selected_paths,
        select_all=select_all,
        maximum=int(bounds["max_files"]),
    )
    workspace_uri = _workspace_uri(
        server_workspace_uri,
        Path(program["binding"]["groovy_root"]),
    )
    prepared = _prepare_sources(
        selected,
        workspace_uri=workspace_uri,
        maximum_file=int(bounds["max_source_bytes"]),
        maximum_total=int(bounds["max_total_source_bytes"]),
    )
    runtime = inventory_language_runtime(
        runtime_root,
        program=program,
        language_profile=language_profile,
        java=java,
        runtime_receipt=runtime_receipt,
    )
    requested_host = (
        str(profile_value["server"]["default_host"]) if host is None else host
    )
    resolved_port = (
        int(profile_value["server"]["default_port"]) if port is None else port
    )
    resolved_host = _validated_endpoint_host(
        requested_host, resolved_port, allow_remote=allow_remote
    )
    service = probe_language_server(
        host=resolved_host,
        port=resolved_port,
        workspace_uri=workspace_uri,
        files=prepared,
        canary_prefix=str(profile_value["canary"]["prefix"]),
        connect_timeout=connect_timeout,
        diagnostic_timeout=diagnostic_timeout,
        limits=LspLimits(
            max_header_bytes=int(bounds["max_header_bytes"]),
            max_message_bytes=int(bounds["max_message_bytes"]),
            max_transcript_messages=int(bounds["max_transcript_messages"]),
            max_diagnostics=int(bounds["max_diagnostics"]),
        ),
    )
    _bind_diagnostics(service)
    selected_public = [
        {key: value for key, value in row.items() if key != "text"}
        for row in prepared
    ]
    diagnostic_rows = [
        diagnostic
        for row in service["files"]
        for diagnostic in row["diagnostics"]
    ]
    severity_counts = Counter(str(row["severity"]) for row in diagnostic_rows)
    file_states = Counter(str(row["state"]) for row in service["files"])
    status, compiler_state = _summary_state(service, diagnostic_rows)
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result: dict[str, Any] = {
        "format": LANGUAGE_RESULT_FORMAT,
        "schema_version": LANGUAGE_RESULT_SCHEMA_VERSION,
        "result_id": "",
        "operation_class": "read-only-network-observation",
        "observed_at": observed_at,
        "authority": {
            "orchestrator": "Workbench Pack Program Studio",
            "compiler_observation": "GroovyScript embedded language server",
            "runtime_inventory": "Workbench exact local byte inventory",
            "endpoint_identity": "unavailable in the upstream protocol",
            "runtime_effects": "none; Atlas observation not implied",
            "construction": "none; Blueprints remains construction authority",
        },
        "profile": {
            "pack_program_profile_id": pack_profile.profile_id,
            "pack_program_profile_sha256": pack_profile.sha256,
            "language_service_profile_id": language_profile.profile_id,
            "language_service_profile_path": str(language_profile.path),
            "language_service_profile_sha256": language_profile.sha256,
            "platform_profile_id": language_profile.platform_profile_id,
            "groovyscript": dict(profile_value["groovyscript"]),
            "server_semantics": dict(profile_value["server"]),
            "runtime_layout": dict(profile_value["runtime_layout"]),
            "bounds": dict(profile_value["bounds"]),
            "canary": dict(profile_value["canary"]),
        },
        "request": {
            "source": str(source.expanduser().resolve()),
            "runtime_root": str(runtime_root.expanduser().resolve()),
            "selected_paths": (
                None
                if select_all
                else [str(row["path"]) for row in selected_public]
            ),
            "all": select_all,
            "host": resolved_host,
            "port": resolved_port,
            "server_workspace_uri": workspace_uri,
            "allow_remote": allow_remote,
            "connect_timeout_seconds": connect_timeout,
            "diagnostic_timeout_seconds": diagnostic_timeout,
            "java": None if java is None else str(java.expanduser().resolve()),
            "runtime_receipt": (
                None
                if runtime_receipt is None
                else str(runtime_receipt.expanduser().resolve())
            ),
        },
        "program": {
            "program_id": program["program_id"],
            "source_sha256": program["binding"]["source_sha256"],
            "run_config_sha256": program["binding"]["run_config_sha256"],
            "pack_profile_id": program["binding"]["pack_profile_id"],
            "platform_profile_id": program["binding"]["platform_profile_id"],
            "physical_side": program["binding"]["physical_side"],
            "packmode": program["binding"]["packmode"],
            "debug": program["binding"]["debug"],
            "selected_files": selected_public,
        },
        "runtime": runtime,
        "service": service,
        "summary": {
            "status": status,
            "compiler_state": compiler_state,
            "selected_files": len(selected_public),
            "checked_files": len(service["files"]),
            "diagnostics": len(diagnostic_rows),
            "severity_counts": dict(sorted(severity_counts.items())),
            "file_states": dict(sorted(file_states.items())),
            "runtime_binding": service["endpoint"]["identity_binding"],
        },
        "limitations": list(
            dict.fromkeys(
                [
                    *profile_value["limitations"],
                    "The diagnostic canary proves that this connection emitted and then cleared a syntax diagnostic for each checked URI.",
                    "A no-diagnostics result is canonicalization evidence for the exact in-memory source bytes, not script execution or registry acceptance.",
                    "The exact local runtime inventory is not authenticated by the upstream TCP endpoint.",
                ]
            )
        ),
        "horizons": [
            {
                "capability": "managed-client-language-service-launch",
                "state": "available-separate-session",
                "next_contract": "Use workbench groovy session to launch a receipt-bound disposable Prism client, prove readiness, hand off its endpoint, and retain shutdown/restoration custody.",
            },
            {
                "capability": "completion-hover-signature-broker",
                "state": "profile-advertised",
                "next_contract": "Expose the validated upstream capabilities through terminal queries and IDE transports without a second semantic implementation.",
            },
            {
                "capability": "observed-effect-ledger",
                "state": "planned",
                "next_contract": "Compare effective registries before and after accepted script stages in a disposable runtime.",
            },
        ],
    }
    result["result_id"] = language_result_identity(result)
    return validate_language_result(result)


def inventory_language_runtime(
    root: Path,
    *,
    program: Mapping[str, Any],
    language_profile: LoadedLanguageProfile,
    java: Path | None,
    runtime_receipt: Path | None,
) -> dict[str, Any]:
    runtime_root = _safe_directory(root, "language-service runtime root")
    profile = language_profile.value
    layout = profile["runtime_layout"]
    bounds = profile["bounds"]
    run_config_path = runtime_root.joinpath(*PurePosixPath(layout["run_config"]).parts)
    run_config = _hash_regular(
        run_config_path,
        maximum=int(bounds["max_runtime_artifact_bytes"]),
    )
    if run_config["sha256"] != program["binding"]["run_config_sha256"]:
        raise PackProgramError(
            "runtime runConfig bytes do not match the candidate program binding"
        )

    mods_root = _safe_directory(
        runtime_root.joinpath(*PurePosixPath(layout["mods_directory"]).parts),
        "language-service mods directory",
    )
    artifacts: list[dict[str, Any]] = []
    for path in sorted(
        mods_root.iterdir(), key=lambda item: (item.name.casefold(), item.name)
    ):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise PackProgramError(f"runtime mod artifact cannot be a symlink: {path}")
        if not stat.S_ISREG(mode) or path.suffix.casefold() != ".jar":
            continue
        if len(artifacts) >= int(bounds["max_runtime_artifacts"]):
            raise PackProgramError("runtime mod artifact count exceeds the language profile")
        identity = _hash_regular(
            path,
            maximum=int(bounds["max_runtime_artifact_bytes"]),
        )
        identity["path"] = path.relative_to(runtime_root).as_posix()
        artifacts.append(identity)
    total_artifact_bytes = sum(row["size"] for row in artifacts)
    if total_artifact_bytes > _MAX_RUNTIME_TOTAL_BYTES:
        raise PackProgramError("runtime mod graph exceeds the hard total-byte bound")
    expected_path = PurePosixPath(layout["artifact_relative_path"]).as_posix()
    expected = next((row for row in artifacts if row["path"] == expected_path), None)
    if expected is None:
        raise PackProgramError(f"runtime is missing exact GroovyScript artifact {expected_path}")
    groovy = profile["groovyscript"]
    if expected["sha256"] != groovy["artifact_sha256"] or expected["size"] != groovy["artifact_size"]:
        raise PackProgramError(
            "runtime GroovyScript artifact does not match the language-service profile lock"
        )
    mod_graph_id = content_id(
        "workbench-groovy-runtime-mod-graph:sha256:", artifacts
    )
    cache_path = runtime_root.joinpath(*PurePosixPath(layout["cache_directory"]).parts)
    cache = _tree_summary(cache_path)
    java_identity = _java_identity(java, int(bounds["max_runtime_artifact_bytes"]))
    receipt = _receipt_identity(runtime_receipt)
    identity_payload = {
        "run_config_sha256": run_config["sha256"],
        "mod_graph_id": mod_graph_id,
        "cache_tree_sha256": cache["tree_sha256"],
        "java_sha256": java_identity.get("sha256"),
        "receipt_sha256": receipt.get("sha256"),
    }
    return {
        "runtime_id": content_id(
            "workbench-groovy-language-runtime:sha256:", identity_payload
        ),
        "root": str(runtime_root),
        "run_config_path": str(run_config_path.resolve()),
        "run_config_sha256": run_config["sha256"],
        "groovyscript_artifact": expected,
        "mod_graph": {
            "mod_graph_id": mod_graph_id,
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "total_bytes": total_artifact_bytes,
        },
        "class_cache": {
            **cache,
            "cache_version": 4,
            "content_addressed_by_upstream": False,
            "upstream_identity_inputs": ["source last-modified time", "Java version"],
        },
        "java": java_identity,
        "launch_receipt": receipt,
        "endpoint_binding": "caller-context-only; upstream endpoint has no identity challenge",
    }


def _select_files(
    program: Mapping[str, Any],
    *,
    selected_paths: Sequence[str] | None,
    select_all: bool,
    maximum: int,
) -> list[Mapping[str, Any]]:
    if bool(selected_paths) == select_all:
        raise PackProgramError("select either repeatable --file or --all")
    by_path = {row["path"]: row for row in program["files"]}
    if select_all:
        selected = [
            row
            for row in program["files"]
            if row["stage"] != "unconfigured" and row["execution_state"] != "excluded"
        ]
    else:
        normalized: list[str] = []
        for raw in selected_paths or ():
            if not isinstance(raw, str) or not raw:
                raise PackProgramError("Groovy check paths must be non-empty text")
            path = PurePosixPath(raw.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
                raise PackProgramError(f"unsafe Groovy check path: {raw}")
            normalized.append(path.as_posix())
        duplicates = [path for path, count in Counter(normalized).items() if count > 1]
        if duplicates:
            raise PackProgramError("duplicate Groovy check paths: " + ", ".join(sorted(duplicates)))
        unknown = sorted(set(normalized) - set(by_path))
        if unknown:
            raise PackProgramError("unknown Groovy check paths: " + ", ".join(unknown))
        selected = [by_path[path] for path in normalized]
    if not selected:
        raise PackProgramError("no Groovy files are selected for language-service checking")
    if len(selected) > maximum:
        raise PackProgramError(
            f"selected {len(selected)} Groovy files; language profile limit is {maximum}"
        )
    return selected


def _prepare_sources(
    selected: Sequence[Mapping[str, Any]],
    *,
    workspace_uri: str,
    maximum_file: int,
    maximum_total: int,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    total = 0
    for row in selected:
        identity = _hash_regular(Path(row["absolute_path"]), maximum=maximum_file)
        if identity["sha256"] != row["sha256"] or identity["size"] != row["size"]:
            raise PackProgramError(f"Groovy source changed after program analysis: {row['path']}")
        raw = safe_regular_bytes(Path(row["absolute_path"]), maximum=maximum_file)
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            raise PackProgramError(f"Groovy source is not UTF-8: {row['path']}: {exc}") from exc
        total += len(raw)
        if total > maximum_total:
            raise PackProgramError("selected Groovy source exceeds the total-byte bound")
        prepared.append(
            {
                "path": row["path"],
                "absolute_path": row["absolute_path"],
                "server_uri": _join_workspace_uri(workspace_uri, row["path"]),
                "sha256": row["sha256"],
                "size": row["size"],
                "stage": row["stage"],
                "execution_state": row["execution_state"],
                "text": text,
            }
        )
    return prepared


def _workspace_uri(explicit: str | None, groovy_root: Path) -> str:
    if explicit is not None and not isinstance(explicit, str):
        raise PackProgramError("--server-workspace-uri must be text")
    value = groovy_root.resolve().as_uri() if explicit is None else explicit.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "file"
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or any(character in value for character in "\r\n\x00")
        or len(value) > 16384
    ):
        raise PackProgramError("--server-workspace-uri must be an absolute file URI")
    return value if parsed.path == "/" else value.rstrip("/")


def _join_workspace_uri(workspace_uri: str, relative: str) -> str:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise PackProgramError(f"unsafe Groovy server URI path: {relative}")
    separator = "" if workspace_uri.endswith("/") else "/"
    return workspace_uri + separator + quote(path.as_posix(), safe="/@:+")


def _validated_endpoint_host(host: str, port: int, *, allow_remote: bool) -> str:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise PackProgramError("language-service port must be between 1 and 65535")
    if (
        not isinstance(host, str)
        or not host
        or len(host) > 1024
        or any(character in host for character in "\r\n\x00")
    ):
        raise PackProgramError("language-service host is invalid")
    if allow_remote:
        return host
    folded = host.casefold().rstrip(".")
    if folded == "localhost":
        # Do not leave this security boundary to mutable resolver configuration.
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if address.is_loopback:
            return host
    raise PackProgramError(
        "refusing to send source to a non-loopback language server; pass --allow-remote explicitly"
    )


def _validated_timeout(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.05 <= value <= 300
    ):
        raise PackProgramError(f"language-service {label} must be between 0.05 and 300 seconds")
    return float(value)


def _bind_diagnostics(service: Mapping[str, Any]) -> None:
    for row in service["files"]:
        for diagnostic in row["diagnostics"]:
            diagnostic["diagnostic_id"] = diagnostic_identity(
                row["path"], row["sha256"], diagnostic
            )


def _summary_state(
    service: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]
) -> tuple[str, str]:
    if service["state"] == "blocked" or not service["files"]:
        return "blocked", "blocked"
    if service["state"] != "completed" or any(
        row["state"] == "inconclusive" for row in service["files"]
    ):
        return "attention", "inconclusive"
    if diagnostics:
        return "attention", "diagnostics"
    return "ready", "no-diagnostics"


def _safe_directory(path: Path, label: str) -> Path:
    requested = path.expanduser()
    try:
        mode = requested.lstat().st_mode
    except OSError as exc:
        raise PackProgramError(f"cannot inspect {label} {requested}: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise PackProgramError(f"{label} must be a non-symlink directory: {requested}")
    return requested.resolve()


def _hash_regular(path: Path, *, maximum: int) -> dict[str, Any]:
    requested = path.expanduser()
    try:
        before = requested.lstat()
    except OSError as exc:
        raise PackProgramError(f"cannot inspect file {requested}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PackProgramError(f"path is not a regular non-symlink file: {requested}")
    if before.st_size > maximum:
        raise PackProgramError(f"file exceeds {maximum} bytes: {requested}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    digest = hashlib.sha256()
    total = 0
    try:
        descriptor = os.open(requested, flags | getattr(os, "O_BINARY", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise PackProgramError(f"file changed identity while opening: {requested}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise PackProgramError(f"file exceeds {maximum} bytes: {requested}")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise PackProgramError(f"file changed while hashing: {requested}")
    except OSError as exc:
        raise PackProgramError(f"cannot hash file {requested}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return {"sha256": digest.hexdigest(), "size": total}


def _tree_summary(path: Path) -> dict[str, Any]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {
            "state": "absent",
            "path": str(path.resolve()),
            "file_count": 0,
            "total_bytes": 0,
            "tree_sha256": None,
        }
    except OSError as exc:
        raise PackProgramError(f"cannot inspect Groovy class cache {path}: {exc}") from exc
    if stat.S_ISLNK(mode):
        raise PackProgramError(f"Groovy class cache cannot be a symlink: {path}")
    root = _safe_directory(path, "Groovy class cache")
    rows: list[dict[str, Any]] = []
    total = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        retained: list[str] = []
        for name in sorted(directories):
            child = base / name
            if child.is_symlink():
                raise PackProgramError(f"Groovy class cache contains a symlink: {child}")
            retained.append(name)
        directories[:] = retained
        for name in sorted(files):
            child = base / name
            identity = _hash_regular(child, maximum=_MAX_CACHE_BYTES)
            total += identity["size"]
            if len(rows) >= _MAX_CACHE_FILES or total > _MAX_CACHE_BYTES:
                raise PackProgramError("Groovy class cache exceeds the hard inventory bound")
            rows.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "sha256": identity["sha256"],
                    "size": identity["size"],
                }
            )
    rows.sort(key=lambda row: row["path"])
    return {
        "state": "present",
        "path": str(root),
        "file_count": len(rows),
        "total_bytes": total,
        "tree_sha256": hashlib.sha256(canonical_bytes(rows)).hexdigest(),
    }


def _java_identity(path: Path | None, maximum: int) -> dict[str, Any]:
    if path is None:
        return {"state": "not-supplied", "sha256": None, "size": None, "path": None, "version_output": None}
    requested = path.expanduser()
    identity = _hash_regular(requested, maximum=maximum)
    resolved = requested.resolve()
    try:
        completed = subprocess.run(
            [str(resolved), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackProgramError(f"cannot probe Java executable {resolved}: {exc}") from exc
    output = completed.stdout[:8192]
    if completed.returncode != 0:
        raise PackProgramError(
            f"Java version probe failed with exit {completed.returncode}: {output[:1024]}"
        )
    return {
        "state": "caller-supplied-exact-bytes",
        "path": str(resolved),
        "sha256": identity["sha256"],
        "size": identity["size"],
        "version_output": output,
    }


def _receipt_identity(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "state": "not-supplied",
            "path": None,
            "sha256": None,
            "size": None,
            "format": None,
            "receipt_id": None,
            "semantic_validation": "not-performed",
        }
    value, raw = strict_json_file(path, maximum=_MAX_RECEIPT_BYTES)
    if not isinstance(value, dict):
        raise PackProgramError("runtime launch receipt must be a JSON object")
    receipt_id = next(
        (
            value[key]
            for key in ("launch_id", "receipt_id", "report_id", "diagnosis_id")
            if isinstance(value.get(key), str)
        ),
        None,
    )
    return {
        "state": "caller-supplied-context",
        "path": str(path.expanduser().resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "format": value.get("format") if isinstance(value.get("format"), str) else None,
        "receipt_id": receipt_id,
        "semantic_validation": "not-performed; endpoint protocol cannot present this receipt",
    }


__all__ = ["build_language_service_result", "inventory_language_runtime"]
