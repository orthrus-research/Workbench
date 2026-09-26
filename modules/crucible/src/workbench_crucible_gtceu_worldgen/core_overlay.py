"""Opt-in GTCEu overlay composition using Core's exact envelope custody."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from workbench_api.managed_trees import ManagedTreeReference, managed_trees
from workbench_api.overlay_envelopes import overlay_envelopes

from .inventory import (
    GtceuWorldgenValidationError,
    build_gtceu_worldgen_inventory,
    parse_gtceu_worldgen_inventory,
)
from .overlay import parse_overlay_plan, planned_overlay_effects, review_overlay_sibling_bytes
from .transport_inventory import (
    build_gtceu_overlay_copy_inventory,
    parse_gtceu_overlay_copy_inventory,
    verify_gtceu_overlay_copy_source,
)


_CHUNK_BYTES = 1024 * 1024


def _plan(chunks: Iterable[bytes], source_inventory: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(b"".join(chunks))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GtceuWorldgenValidationError("retained overlay plan is invalid JSON") from exc
    if not isinstance(value, dict):
        raise GtceuWorldgenValidationError("retained overlay plan must be a JSON object")
    return parse_overlay_plan(value, source_inventory)


def materialize_core_overlay(
    *, jar_path: Path, config_root: Path, inventory: Mapping[str, Any],
    plan_bytes: bytes, output_config_root: Path,
) -> ManagedTreeReference:
    """Publish one fresh `config` envelope, or retain a reviewable Core attempt."""
    if (not isinstance(output_config_root, Path) or not output_config_root.is_absolute()
            or output_config_root.name != "gregtech"
            or output_config_root.parent.name != "config"):
        raise GtceuWorldgenValidationError(
            "Core overlay output must end in config/gregtech"
        )
    if type(plan_bytes) is not bytes or not plan_bytes:
        raise GtceuWorldgenValidationError("overlay plan has no bytes")
    source = config_root.expanduser().resolve(strict=True)
    selected = parse_gtceu_worldgen_inventory(inventory)
    checked_plan = _plan((plan_bytes,), selected)
    current = build_gtceu_worldgen_inventory(jar_path=jar_path, config_root=source)
    if current["artifact"] != selected["artifact"]:
        raise GtceuWorldgenValidationError(
            "supplied GTCEu jar drifted from the source inventory"
        )
    if current["configuration"] != selected["configuration"]:
        raise GtceuWorldgenValidationError(
            "supplied GTCEu configuration drifted from the source inventory"
        )
    effects = planned_overlay_effects(plan=checked_plan, source_inventory=selected)
    trees = managed_trees()
    inputs = overlay_envelopes()

    def plan_chunks() -> Iterable[bytes]:
        for offset in range(0, len(plan_bytes), _CHUNK_BYTES):
            yield plan_bytes[offset:offset + _CHUNK_BYTES]

    def validate_output(staged_config_root: Path, chunks: Iterable[bytes]) -> tuple[bytes, bytes]:
        return review_overlay_sibling_bytes(
            jar_path=jar_path, staged_config_root=staged_config_root,
            source_inventory=selected, plan=_plan(chunks, selected),
        )

    with trees.stage(
        "artifacts", "config", requested_path=output_config_root.parent,
    ) as stage:
        attempt = inputs.start(
            stage=stage, source_root=source, plan_chunks=plan_chunks(),
        )
        manifest = build_gtceu_overlay_copy_inventory(
            config_root=source, source_inventory=selected,
            emit_chunk=attempt.emit_chunk,
        )
        attempt.seal_inputs(
            manifest, validate_inventory=parse_gtceu_overlay_copy_inventory,
        )
        # Core computes the V3 file, directory and byte upper bounds here,
        # before it creates any copied payload. Unsupported trees stay staged.
        attempt.seal_effects(
            effects, validate_plan=lambda chunks: planned_overlay_effects(
                plan=_plan(chunks, selected), source_inventory=selected,
            ),
        )
        copied = attempt.copy_source(
            verify_source=lambda chosen, chunks: verify_gtceu_overlay_copy_source(
                config_root=source, source_inventory=selected,
                manifest=chosen, chunks=chunks,
                expected_inventory_id=manifest["inventory_id"],
            ),
        )
        attempt.apply_effects(effects)
        inventory_bytes, materialization_bytes = review_overlay_sibling_bytes(
            jar_path=jar_path, staged_config_root=copied,
            source_inventory=selected, plan=checked_plan,
        )
        attempt.write_siblings(
            inventory_bytes=inventory_bytes,
            materialization_bytes=materialization_bytes,
            validate_output=validate_output,
        )
        return attempt.publish_envelope(validate_output=validate_output)


def review_core_overlays() -> tuple[dict[str, object], ...]:
    """Inspect retained attempts without publishing or discarding any of them."""
    return overlay_envelopes().inventory()


def reconcile_core_overlay(
    *, attempt_id: str,
) -> ManagedTreeReference:
    """Complete an exact prepared Core intent after explicit user selection."""
    return overlay_envelopes().reconcile_publication(
        attempt_id=attempt_id, trees=managed_trees(),
    )


__all__ = ["materialize_core_overlay", "review_core_overlays", "reconcile_core_overlay"]
