"""Bind stopped projected worlds to Atlas-owned Anvil observations."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Callable, Iterator


MAX_PROJECTED_WORLDS = 128
MAX_AGGREGATE_REGION_BYTES = 512 * 1024 * 1024
MAX_AGGREGATE_CHUNKS = 65_536


class RuntimeAnvilObserveError(ValueError):
    """Raised when stopped-world evidence cannot be bound safely."""


def _atlas_authority(
    suite_root: Path,
) -> tuple[Callable[[Path], dict[str, Any]], type[ValueError]]:
    atlas_source = suite_root / "modules/atlas/src"
    source_text = str(atlas_source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from workbench_atlas.experimental_anvil_region_observation import (
            AtlasAnvilRegionObservationError,
            observe_anvil_region_world,
        )
    except ImportError as exc:
        raise RuntimeAnvilObserveError(
            f"Atlas Anvil observation authority is unavailable: {exc}"
        ) from exc
    return observe_anvil_region_world, AtlasAnvilRegionObservationError


def _projected_worlds(runtime_root: Path) -> list[Path]:
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise RuntimeAnvilObserveError(
            "projected Minecraft root is unsafe or unavailable"
        )
    saves = runtime_root / "saves"
    if saves.is_symlink():
        raise RuntimeAnvilObserveError(
            "projected saves root cannot be a symbolic link"
        )
    if not saves.exists():
        return []
    if not saves.is_dir():
        raise RuntimeAnvilObserveError(
            "projected saves root is not a directory"
        )
    worlds: list[Path] = []
    try:
        entries = sorted(saves.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise RuntimeAnvilObserveError(
            "projected saves root cannot be enumerated"
        ) from exc
    for entry in entries:
        if entry.is_symlink():
            raise RuntimeAnvilObserveError(
                f"projected saves root contains a symbolic link: {entry.name}"
            )
        if entry.is_dir():
            worlds.append(entry)
            if len(worlds) > MAX_PROJECTED_WORLDS:
                raise RuntimeAnvilObserveError(
                    "projected saves root exceeds the world-count limit"
                )
        elif not entry.is_file():
            raise RuntimeAnvilObserveError(
                f"projected saves root contains a special entry: {entry.name}"
            )
    return worlds


def iter_runtime_anvil_worlds(
    suite_root: Path | str,
    runtime_root: Path | str,
) -> Iterator[dict[str, Any]]:
    """Yield Atlas observations for stopped projected save directories.

    The caller owns the lifecycle proof that no process can still mutate the
    selected runtime. This wrapper only selects direct save roots and transports
    Atlas results without interpreting their findings.
    """

    suite = Path(suite_root).resolve()
    selected_runtime = Path(runtime_root).expanduser()
    if selected_runtime.is_symlink():
        raise RuntimeAnvilObserveError(
            "projected Minecraft root cannot be a symbolic link"
        )
    runtime = selected_runtime.resolve()
    worlds = _projected_worlds(runtime)
    if not worlds:
        return
    authority, authority_error = _atlas_authority(suite)
    aggregate_region_bytes = 0
    aggregate_chunks = 0
    for world in worlds:
        try:
            observation = authority(world)
        except authority_error as exc:
            yield {
                "world_name": world.name,
                "world_uri": world.as_uri(),
                "error": {
                    "kind": "atlas-observation-failed",
                    "reason": str(exc)[:1000],
                },
            }
            continue
        if (
            not isinstance(observation, dict)
            or observation.get("format")
            != "atlas-experimental-anvil-region-observation-v1"
            or observation.get("schema_version") != 1
            or not isinstance(observation.get("observation_id"), str)
        ):
            yield {
                "world_name": world.name,
                "world_uri": world.as_uri(),
                "error": {
                    "kind": "unsupported-atlas-observation",
                    "reason": (
                        "Atlas Anvil authority returned an unsupported "
                        "observation"
                    ),
                },
            }
            continue
        facts = observation.get("facts")
        summary = facts.get("summary") if isinstance(facts, dict) else None
        region_bytes = (
            summary.get("region_bytes") if isinstance(summary, dict) else None
        )
        chunks = (
            summary.get("allocated_chunk_count")
            if isinstance(summary, dict)
            else None
        )
        if (
            type(region_bytes) is not int
            or region_bytes < 0
            or type(chunks) is not int
            or chunks < 0
        ):
            yield {
                "world_name": world.name,
                "world_uri": world.as_uri(),
                "error": {
                    "kind": "unsupported-atlas-observation",
                    "reason": "Atlas Anvil summary lacks bounded counts",
                },
            }
            continue
        aggregate_region_bytes += region_bytes
        aggregate_chunks += chunks
        if (
            aggregate_region_bytes > MAX_AGGREGATE_REGION_BYTES
            or aggregate_chunks > MAX_AGGREGATE_CHUNKS
        ):
            raise RuntimeAnvilObserveError(
                "projected saves exceed the aggregate Anvil observation limit"
            )
        yield {
            "world_name": world.name,
            "world_uri": world.as_uri(),
            "observation": observation,
        }


def observe_runtime_anvil_worlds(
    suite_root: Path | str,
    runtime_root: Path | str,
) -> list[dict[str, Any]]:
    """Collect the bounded streaming interface for direct API callers."""

    return list(iter_runtime_anvil_worlds(suite_root, runtime_root))


__all__ = [
    "RuntimeAnvilObserveError",
    "iter_runtime_anvil_worlds",
    "observe_runtime_anvil_worlds",
]
