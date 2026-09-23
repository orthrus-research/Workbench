"""Shell transport for Atlas-owned stopped-world fingerprints and comparisons."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable


MAX_FINGERPRINT_ARTIFACT_BYTES = 128 * 1024 * 1024


class RuntimeWorldgenFingerprintError(ValueError):
    """Raised when fingerprint evidence cannot be transported safely."""


def _atlas_authority(
    suite_root: Path,
) -> tuple[
    Callable[[Path], dict[str, Any]],
    Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    type[ValueError],
]:
    atlas_source = suite_root / "modules/atlas/src"
    source_text = str(atlas_source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from workbench_atlas.experimental_anvil_worldgen_fingerprint import (
            AtlasAnvilWorldgenFingerprintError,
            compare_anvil_worldgen_fingerprints,
            observe_anvil_worldgen_fingerprint,
        )
    except ImportError as exc:
        raise RuntimeWorldgenFingerprintError(
            f"Atlas worldgen fingerprint authority is unavailable: {exc}"
        ) from exc
    return (
        observe_anvil_worldgen_fingerprint,
        compare_anvil_worldgen_fingerprints,
        AtlasAnvilWorldgenFingerprintError,
    )


def _atlas_block_delta_authority(
    suite_root: Path,
) -> tuple[Callable[[Path, Path], dict[str, Any]], type[ValueError]]:
    atlas_source = suite_root / "modules/atlas/src"
    source_text = str(atlas_source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from workbench_atlas.experimental_anvil_block_delta import (
            AtlasAnvilBlockDeltaError,
            observe_anvil_block_delta,
        )
    except ImportError as exc:
        raise RuntimeWorldgenFingerprintError(
            f"Atlas block-delta authority is unavailable: {exc}"
        ) from exc
    return observe_anvil_block_delta, AtlasAnvilBlockDeltaError


def _read_json_artifact(path: Path | str, label: str) -> dict[str, Any]:
    selected = Path(path).expanduser()
    if selected.is_symlink():
        raise RuntimeWorldgenFingerprintError(
            f"{label} cannot be a symbolic link"
        )
    resolved = selected.resolve()
    if not resolved.is_file():
        raise RuntimeWorldgenFingerprintError(
            f"{label} is not a regular file: {resolved}"
        )
    try:
        size = resolved.stat().st_size
        if size > MAX_FINGERPRINT_ARTIFACT_BYTES:
            raise RuntimeWorldgenFingerprintError(
                f"{label} exceeds the artifact byte limit"
            )
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeWorldgenFingerprintError(
            f"{label} cannot be read as JSON: {resolved}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeWorldgenFingerprintError(f"{label} must be a JSON object")
    return value


def _retain_json(path: Path | str, value: dict[str, Any]) -> None:
    selected = Path(path).expanduser()
    raw_parent = selected.parent
    if raw_parent.is_symlink():
        raise RuntimeWorldgenFingerprintError(
            "fingerprint output parent cannot be a symbolic link"
        )
    parent = raw_parent.resolve()
    target = parent / selected.name
    if target.exists() or target.is_symlink():
        raise RuntimeWorldgenFingerprintError(
            f"fingerprint output already exists: {target}"
        )
    if not parent.is_dir():
        raise RuntimeWorldgenFingerprintError(
            f"fingerprint output parent is not a regular directory: {parent}"
        )
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_FINGERPRINT_ARTIFACT_BYTES:
        raise RuntimeWorldgenFingerprintError(
            "fingerprint output exceeds the artifact byte limit"
        )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{selected.name}.",
            suffix=".tmp",
            dir=parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_name, target)
        Path(temporary_name).unlink()
        temporary_name = None
    except OSError as exc:
        raise RuntimeWorldgenFingerprintError(
            f"fingerprint output cannot be retained: {target}"
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def fingerprint_runtime_worldgen(
    suite_root: Path | str,
    world_root: Path | str,
    *,
    output: Path | str | None = None,
) -> dict[str, Any]:
    """Run Atlas fingerprinting and optionally retain the exact result."""

    suite = Path(suite_root).resolve()
    observe, _compare, authority_error = _atlas_authority(suite)
    try:
        result = observe(Path(world_root))
    except authority_error as exc:
        raise RuntimeWorldgenFingerprintError(str(exc)) from exc
    if output is not None:
        _retain_json(output, result)
    return result


def compare_runtime_worldgen(
    suite_root: Path | str,
    left: Path | str,
    right: Path | str,
    *,
    output: Path | str | None = None,
) -> dict[str, Any]:
    """Identity-verify and compare two retained Atlas fingerprints."""

    suite = Path(suite_root).resolve()
    _observe, compare, authority_error = _atlas_authority(suite)
    first = _read_json_artifact(left, "left fingerprint")
    second = _read_json_artifact(right, "right fingerprint")
    try:
        result = compare(first, second)
    except authority_error as exc:
        raise RuntimeWorldgenFingerprintError(str(exc)) from exc
    if output is not None:
        _retain_json(output, result)
    return result


def attribute_runtime_worldgen_blocks(
    suite_root: Path | str,
    left_world: Path | str,
    right_world: Path | str,
    *,
    output: Path | str | None = None,
) -> dict[str, Any]:
    """Attribute exact block-state changes between two stopped worlds."""

    suite = Path(suite_root).resolve()
    observe, authority_error = _atlas_block_delta_authority(suite)
    try:
        result = observe(Path(left_world), Path(right_world))
    except authority_error as exc:
        raise RuntimeWorldgenFingerprintError(str(exc)) from exc
    if output is not None:
        _retain_json(output, result)
    return result


__all__ = [
    "RuntimeWorldgenFingerprintError",
    "attribute_runtime_worldgen_blocks",
    "compare_runtime_worldgen",
    "fingerprint_runtime_worldgen",
]
