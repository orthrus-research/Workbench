"""Versioned GroovyScript language-service profile loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .model import PackProgramError, sha256_bytes
from .profile import LoadedProfile, strict_json_file


LANGUAGE_PROFILE_FORMAT = "workbench-groovyscript-language-service-profile-v1"
_KNOWN_PROFILES = {
    "workbench-platform:cleanroom:groovyscript:1.4.3": (
        "profiles/platforms/cleanroom/groovyscript/"
        "groovyscript-1.4.3-language-service-v1.json"
    ),
}


@dataclass(frozen=True, slots=True)
class LoadedLanguageProfile:
    path: Path
    sha256: str
    value: Mapping[str, Any]

    @property
    def profile_id(self) -> str:
        return str(self.value["profile_id"])

    @property
    def platform_profile_id(self) -> str:
        return str(self.value["platform_profile_id"])


def resolve_language_profile(
    root: Path,
    pack_profile: LoadedProfile,
    explicit: Path | None = None,
) -> LoadedLanguageProfile:
    if explicit is not None:
        loaded = load_language_profile(explicit)
    else:
        relative = _KNOWN_PROFILES.get(pack_profile.platform_profile_id)
        if relative is None:
            raise PackProgramError(
                "no language-service profile is registered for platform "
                f"{pack_profile.platform_profile_id}; pass --language-profile"
            )
        loaded = load_language_profile(root / relative)
    if loaded.platform_profile_id != pack_profile.platform_profile_id:
        raise PackProgramError(
            "language-service profile platform does not match the pack-program profile"
        )
    return loaded


def load_language_profile(path: Path) -> LoadedLanguageProfile:
    requested = path.expanduser().resolve()
    value, raw = strict_json_file(requested)
    if not isinstance(value, dict):
        raise PackProgramError("GroovyScript language-service profile must be an object")
    _validate_language_profile(value)
    return LoadedLanguageProfile(
        path=requested,
        sha256=sha256_bytes(raw),
        value=value,
    )


def _validate_language_profile(value: Mapping[str, Any]) -> None:
    required = {
        "format",
        "schema_version",
        "profile_id",
        "platform_profile_id",
        "groovyscript",
        "server",
        "runtime_layout",
        "bounds",
        "canary",
        "limitations",
    }
    if set(value) != required:
        raise PackProgramError(
            "GroovyScript language-service profile has unexpected or missing keys"
        )
    if value["format"] != LANGUAGE_PROFILE_FORMAT or value["schema_version"] != 1:
        raise PackProgramError("unsupported GroovyScript language-service profile")
    for key in ("profile_id", "platform_profile_id"):
        if not isinstance(value[key], str) or not value[key]:
            raise PackProgramError(f"language-service profile {key} must be text")

    groovy = value["groovyscript"]
    if not isinstance(groovy, dict) or set(groovy) != {
        "version",
        "artifact_filename",
        "artifact_sha256",
        "artifact_size",
        "source_commit",
    }:
        raise PackProgramError("language-service GroovyScript identity is malformed")
    if not isinstance(groovy["artifact_size"], int) or isinstance(
        groovy["artifact_size"], bool
    ) or groovy["artifact_size"] <= 0:
        raise PackProgramError("language-service artifact size must be positive")
    if not _hex(groovy["artifact_sha256"], 64) or not _hex(
        groovy["source_commit"], 40
    ):
        raise PackProgramError("language-service artifact/source identity is malformed")
    if any(not isinstance(groovy[key], str) or not groovy[key] for key in (
        "version",
        "artifact_filename",
    )):
        raise PackProgramError("language-service GroovyScript version/name is malformed")

    server = value["server"]
    expected_server = {
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
    }
    if not isinstance(server, dict) or set(server) != expected_server:
        raise PackProgramError("language-service server policy is malformed")
    constants = {
        "transport": "lsp-jsonrpc-tcp",
        "runtime_side": "client",
        "compile_trigger": "textDocument/documentSymbol",
        "diagnostic_method": "textDocument/publishDiagnostics",
        "text_document_sync": "full",
        "compilation_phase": "canonicalization",
        "endpoint_identity_protocol": "unavailable",
    }
    if any(server[key] != expected for key, expected in constants.items()):
        raise PackProgramError("language-service server policy is unsupported")
    if (
        not isinstance(server["default_port"], int)
        or isinstance(server["default_port"], bool)
        or not 1 <= server["default_port"] <= 65535
    ):
        raise PackProgramError("language-service default port is invalid")
    for key in ("default_host", "start_property"):
        if not isinstance(server[key], str) or not server[key]:
            raise PackProgramError(f"language-service server {key} must be text")

    layout = value["runtime_layout"]
    expected_layout = {
        "artifact_relative_path",
        "mods_directory",
        "groovy_directory",
        "run_config",
        "cache_directory",
    }
    if not isinstance(layout, dict) or set(layout) != expected_layout:
        raise PackProgramError("language-service runtime layout is malformed")
    for key, raw in layout.items():
        if not isinstance(raw, str) or not raw:
            raise PackProgramError(f"language-service runtime layout {key} must be text")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise PackProgramError(
                f"language-service runtime layout {key} must be a safe relative path"
            )

    bounds = value["bounds"]
    expected_bounds = {
        "max_files",
        "max_source_bytes",
        "max_total_source_bytes",
        "max_header_bytes",
        "max_message_bytes",
        "max_transcript_messages",
        "max_diagnostics",
        "max_runtime_artifacts",
        "max_runtime_artifact_bytes",
    }
    if not isinstance(bounds, dict) or set(bounds) != expected_bounds:
        raise PackProgramError("language-service bounds are malformed")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item <= 0
        for item in bounds.values()
    ):
        raise PackProgramError("language-service bounds must be positive integers")
    if (
        bounds["max_files"] > 4096
        or bounds["max_source_bytes"] > 64 * 1024 * 1024
        or bounds["max_total_source_bytes"] > 512 * 1024 * 1024
        or bounds["max_total_source_bytes"] < bounds["max_source_bytes"]
        or not 1024 <= bounds["max_header_bytes"] <= 1024 * 1024
        or not 1024 <= bounds["max_message_bytes"] <= 256 * 1024 * 1024
        or not 16 <= bounds["max_transcript_messages"] <= 1_000_000
        or bounds["max_diagnostics"] > 1_000_000
        or bounds["max_runtime_artifacts"] > 4096
        or bounds["max_runtime_artifact_bytes"] > 4 * 1024 * 1024 * 1024
    ):
        raise PackProgramError("language-service bounds are excessive")

    canary = value["canary"]
    if not isinstance(canary, dict) or set(canary) != {"prefix", "required_severity"}:
        raise PackProgramError("language-service canary is malformed")
    if (
        not isinstance(canary["prefix"], str)
        or not canary["prefix"]
        or len(canary["prefix"].encode("utf-8")) > 1024
        or canary["required_severity"] != 1
    ):
        raise PackProgramError("language-service canary policy is unsupported")
    limitations = value["limitations"]
    if not isinstance(limitations, list) or not limitations or any(
        not isinstance(item, str) or not item for item in limitations
    ):
        raise PackProgramError("language-service limitations are malformed")


def _hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "LANGUAGE_PROFILE_FORMAT",
    "LoadedLanguageProfile",
    "load_language_profile",
    "resolve_language_profile",
]
