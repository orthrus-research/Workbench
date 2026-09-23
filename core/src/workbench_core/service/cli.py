"""Bounded CLI adapter for the authenticated local Service Protocol V3 host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workbench_api.canonical import canonical_json_bytes, content_id, parse_canonical_json
from workbench_api.host_filesystem import private_path

from workbench_core.service.host import LocalServiceClientV3


class ServiceCliV3Error(ValueError):
    """CLI service input or authenticated exchange failed closed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ServiceCliV3Error(message)


def invoke_service_cli_v3(
    *,
    endpoint_path: Path,
    credential_path: Path,
    messages_path: Path,
) -> dict[str, Any]:
    """Send one exact initialize/request sequence through the shared client."""

    for path, label in (
        (endpoint_path, "endpoint"),
        (credential_path, "credential"),
        (messages_path, "message input"),
    ):
        _require(
            isinstance(path, Path) and path.is_absolute() and not path.is_symlink(),
            f"service {label} path must be absolute and must not be a symbolic link",
        )
    _require(
        endpoint_path.exists(),
        "service endpoint is unavailable",
    )
    _require(
        private_path(credential_path, directory=False),
        "service credential is absent or accessible outside its owner",
    )
    token = credential_path.read_text(encoding="ascii")
    _require(
        len(token) == 64
        and all(character in "0123456789abcdef" for character in token),
        "service credential is invalid",
    )
    try:
        source = json.loads(messages_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServiceCliV3Error("service message input is not valid JSON") from exc
    _require(
        type(source) is list
        and 2 <= len(source) <= 1024
        and all(type(message) is dict for message in source),
        "service message input must be a bounded JSON request array",
    )
    messages = tuple(
        parse_canonical_json(canonical_json_bytes(message)) for message in source
    )
    _require(
        messages[0].get("method") == "service/initialize"
        and messages[0].get("params", {}).get("transport") == "local-endpoint",
        "service CLI sequence must begin with local-endpoint initialization",
    )
    responses = LocalServiceClientV3(endpoint_path, token).exchange(messages)
    body = {
        "canonicalizer": "workbench-canonical-json-v2",
        "format": "workbench-service-cli-exchange-v3",
        "kind": "service-cli-exchange",
        "request_count": len(messages),
        "responses": list(responses),
        "schema_version": 3,
    }
    body["id"] = content_id("service-cli-exchange", body)
    return body


__all__ = ["ServiceCliV3Error", "invoke_service_cli_v3"]
