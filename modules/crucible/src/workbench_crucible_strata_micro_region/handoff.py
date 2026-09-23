"""Validate and launch an exact Strata viewer handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse


HANDOFF_FIELDS = {
    "cwd",
    "externalArtifactRoot",
    "manifest",
    "port",
    "url",
}


class StrataViewerHandoffValidationError(ValueError):
    """Raised when a viewer handoff is incomplete, stale, or out of custody."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StrataViewerHandoffValidationError(message)


def parse_strata_viewer_handoff(
    path: Path, *, workbench_root: Path
) -> dict[str, Any]:
    root = workbench_root.expanduser().resolve(strict=True)
    operational = (root / ".workbench").resolve(strict=True)
    handoff_path = path.expanduser().resolve(strict=True)
    try:
        handoff_path.relative_to(operational)
    except ValueError as exc:
        raise StrataViewerHandoffValidationError(
            f"viewer handoff must be under {operational}: {handoff_path}"
        ) from exc
    try:
        value = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StrataViewerHandoffValidationError(
            f"cannot parse viewer handoff: {exc}"
        ) from exc
    _require(isinstance(value, Mapping), "viewer handoff must be an object")
    fields = set(value)
    _require(
        fields == HANDOFF_FIELDS,
        f"viewer handoff fields mismatch: missing={sorted(HANDOFF_FIELDS - fields)!r}, unknown={sorted(fields - HANDOFF_FIELDS)!r}",
    )
    cwd_value = value.get("cwd")
    artifact_value = value.get("externalArtifactRoot")
    manifest_value = value.get("manifest")
    url_value = value.get("url")
    port = value.get("port")
    _require(isinstance(cwd_value, str) and cwd_value, "viewer cwd must be text")
    _require(
        isinstance(artifact_value, str) and artifact_value,
        "external artifact root must be text",
    )
    _require(
        isinstance(manifest_value, str) and manifest_value,
        "viewer manifest must be text",
    )
    _require(isinstance(url_value, str) and url_value, "viewer URL must be text")
    _require(
        isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535,
        "viewer port must be in 1..65535",
    )

    cwd = Path(cwd_value).expanduser().resolve(strict=True)
    artifact_root = Path(artifact_value).expanduser().resolve(strict=True)
    manifest = Path(manifest_value).expanduser().resolve(strict=True)
    _require(cwd.is_dir(), f"viewer cwd is not a directory: {cwd}")
    _require((cwd / "package.json").is_file(), f"viewer cwd lacks package.json: {cwd}")
    _require(artifact_root.is_dir(), f"external artifact root is not a directory: {artifact_root}")
    _require(manifest.is_file(), f"viewer manifest is not a file: {manifest}")
    try:
        manifest.relative_to(artifact_root)
    except ValueError as exc:
        raise StrataViewerHandoffValidationError(
            "viewer manifest is outside its external artifact root"
        ) from exc
    try:
        artifact_root.relative_to(operational)
        manifest.relative_to(operational)
    except ValueError as exc:
        raise StrataViewerHandoffValidationError(
            "viewer artifacts must remain under Workbench .workbench custody"
        ) from exc

    parsed = urlparse(url_value)
    _require(parsed.scheme == "http", "viewer URL must use http")
    _require(parsed.hostname == "127.0.0.1", "viewer URL must use 127.0.0.1")
    _require(parsed.port == port, "viewer URL port differs from handoff port")
    query = parse_qs(parsed.query)
    _require(query.get("view") == ["region"], "viewer URL must select the region view")
    _require(bool(query.get("manifest")), "viewer URL lacks its manifest query")

    return {
        "handoff": str(handoff_path),
        "cwd": str(cwd),
        "external_artifact_root": str(artifact_root),
        "manifest": str(manifest),
        "port": port,
        "url": url_value,
        "command": [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
    }
