"""Versioned launch policy for managed GroovyScript language sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .language_profile import LoadedLanguageProfile
from .model import PackProgramError, sha256_bytes
from .profile import strict_json_file


MANAGED_PROFILE_FORMAT = "workbench-groovyscript-managed-session-profile-v1"
_KNOWN_PROFILES = {
    "workbench-platform:cleanroom:groovyscript:1.4.3-language-service-v1": (
        "profiles/platforms/cleanroom/groovyscript/"
        "groovyscript-1.4.3-prism-managed-session-v1.json"
    ),
}


@dataclass(frozen=True, slots=True)
class LoadedManagedSessionProfile:
    path: Path
    sha256: str
    value: Mapping[str, Any]

    @property
    def profile_id(self) -> str:
        return str(self.value["profile_id"])

    @property
    def language_service_profile_id(self) -> str:
        return str(self.value["language_service_profile_id"])


def resolve_managed_session_profile(
    root: Path,
    language_profile: LoadedLanguageProfile,
    explicit: Path | None = None,
) -> LoadedManagedSessionProfile:
    if explicit is not None:
        loaded = load_managed_session_profile(explicit)
    else:
        relative = _KNOWN_PROFILES.get(language_profile.profile_id)
        if relative is None:
            raise PackProgramError(
                "no managed language-session profile is registered for "
                f"{language_profile.profile_id}; pass --session-profile"
            )
        loaded = load_managed_session_profile(root / relative)
    if loaded.language_service_profile_id != language_profile.profile_id:
        raise PackProgramError(
            "managed session profile does not match the language-service profile"
        )
    if loaded.value["platform_profile_id"] != language_profile.platform_profile_id:
        raise PackProgramError(
            "managed session profile does not match the language-service platform"
        )
    return loaded


def load_managed_session_profile(path: Path) -> LoadedManagedSessionProfile:
    requested = path.expanduser().resolve()
    value, raw = strict_json_file(requested)
    if not isinstance(value, dict):
        raise PackProgramError("managed language-session profile must be an object")
    _validate(value)
    return LoadedManagedSessionProfile(
        path=requested,
        sha256=sha256_bytes(raw),
        value=value,
    )


def _validate(value: Mapping[str, Any]) -> None:
    required = {
        "format",
        "schema_version",
        "profile_id",
        "platform_profile_id",
        "language_service_profile_id",
        "launcher",
        "overlay",
        "lifecycle",
        "ide_handoff",
        "limitations",
    }
    if set(value) != required:
        raise PackProgramError("managed language-session profile has unexpected keys")
    if value["format"] != MANAGED_PROFILE_FORMAT or value["schema_version"] != 1:
        raise PackProgramError("unsupported managed language-session profile")
    for key in ("profile_id", "platform_profile_id", "language_service_profile_id"):
        _text(value[key], f"managed session {key}", 1024)

    launcher = _mapping(
        value["launcher"],
        {
            "family",
            "accepted_receipt_formats",
            "launch_flag",
            "data_root_flag",
            "instance_config",
            "jvm_arguments_key",
            "override_jvm_arguments_key",
            "disposable_instance_prefix",
            "windows_process_tracker",
        },
        "managed session launcher",
    )
    if launcher["family"] != "prism":
        raise PackProgramError("managed session launcher family is unsupported")
    receipts = launcher["accepted_receipt_formats"]
    if receipts != ["workbench-runtime-launch-receipt-v3"]:
        raise PackProgramError("managed session launch-receipt policy is unsupported")
    constants = {
        "launch_flag": "--launch",
        "data_root_flag": "--dir",
        "jvm_arguments_key": "JvmArgs",
        "override_jvm_arguments_key": "OverrideJavaArgs",
        "windows_process_tracker": "windows-cim-instance-id-or-upstream-port",
    }
    for key, expected in constants.items():
        if launcher[key] != expected:
            raise PackProgramError(f"managed session launcher {key} is unsupported")
    _relative(launcher["instance_config"], "managed session instance config")
    prefix = _text(
        launcher["disposable_instance_prefix"],
        "managed session disposable prefix",
        128,
    )
    if not prefix.startswith("workbench-"):
        raise PackProgramError("managed sessions require an explicit Workbench projection")

    overlay = _mapping(
        value["overlay"],
        {
            "runtime_config",
            "port_property_type",
            "port_property_key",
            "start_jvm_argument",
        },
        "managed session overlay",
    )
    _relative(overlay["runtime_config"], "managed session runtime config")
    if overlay["port_property_type"] != "forge-int":
        raise PackProgramError("managed session port property type is unsupported")
    if overlay["port_property_key"] != "languageServerPort":
        raise PackProgramError("managed session port property key is unsupported")
    start = _text(overlay["start_jvm_argument"], "managed session start argument", 1024)
    if start != "-Dgroovyscript.run_ls=true":
        raise PackProgramError("managed session start argument is unsupported")

    lifecycle = _mapping(
        value["lifecycle"],
        {
            "default_readiness_timeout_seconds",
            "default_session_timeout_seconds",
            "readiness_poll_interval_seconds",
            "client_attach_timeout_seconds",
            "graceful_shutdown_seconds",
            "force_shutdown_seconds",
        },
        "managed session lifecycle",
    )
    ranges = {
        "default_readiness_timeout_seconds": (1, 3600),
        "default_session_timeout_seconds": (1, 86400),
        "readiness_poll_interval_seconds": (0.05, 5),
        "client_attach_timeout_seconds": (1, 600),
        "graceful_shutdown_seconds": (0.1, 120),
        "force_shutdown_seconds": (0.1, 120),
    }
    for key, (minimum, maximum) in ranges.items():
        item = lifecycle[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not minimum <= item <= maximum:
            raise PackProgramError(f"managed session lifecycle {key} is out of bounds")

    handoff = _mapping(
        value["ide_handoff"],
        {"descriptor_format", "transport", "connection_model", "consumers"},
        "managed session IDE handoff",
    )
    if handoff["descriptor_format"] != "workbench-groovy-language-session-descriptor-v1":
        raise PackProgramError("managed session descriptor format is unsupported")
    if handoff["transport"] != "lsp-jsonrpc-tcp":
        raise PackProgramError("managed session IDE transport is unsupported")
    if handoff["connection_model"] != "one-active-client-sequential-reaccept":
        raise PackProgramError("managed session connection model is unsupported")
    if handoff["consumers"] != ["terminal", "intellij", "vscode"]:
        raise PackProgramError("managed session IDE consumers are unsupported")

    limitations = value["limitations"]
    if not isinstance(limitations, list) or not limitations or len(limitations) > 64:
        raise PackProgramError("managed session limitations are malformed")
    if len(limitations) != len(set(limitations)):
        raise PackProgramError("managed session limitations must be unique")
    for item in limitations:
        _text(item, "managed session limitation", 8192)


def _mapping(value: Any, keys: set[str], context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PackProgramError(f"{context} is malformed")
    return value


def _text(value: Any, context: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise PackProgramError(f"{context} must be bounded non-empty text")
    if any(ord(character) < 32 and character not in "\t" for character in value):
        raise PackProgramError(f"{context} contains control characters")
    return value


def _relative(value: Any, context: str) -> PurePosixPath:
    text = _text(value, context, 1024)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise PackProgramError(f"{context} must be a safe relative path")
    return path


__all__ = [
    "MANAGED_PROFILE_FORMAT",
    "LoadedManagedSessionProfile",
    "load_managed_session_profile",
    "resolve_managed_session_profile",
]
