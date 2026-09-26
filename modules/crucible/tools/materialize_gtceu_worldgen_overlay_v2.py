#!/usr/bin/env python3
"""Publish or review an opt-in Core-managed GTCEu worldgen overlay envelope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
for SOURCE in (ROOT / "api/src", ROOT / "core/src", ROOT / "modules/crucible/src"):
    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))

from workbench_api.managed_trees import ManagedTreeError  # noqa: E402
from workbench_api.managed_trees import managed_trees_scope  # noqa: E402
from workbench_api.overlay_envelopes import overlay_envelopes_scope  # noqa: E402
from workbench_core.managed_trees import CoreManagedTrees  # noqa: E402
from workbench_core.overlay_envelope_inputs import (  # noqa: E402
    CoreOverlayEnvelopeInputs, OverlayEnvelopeInputError,
)
from workbench_core.user_config_home import default_user_config_home  # noqa: E402
from workbench_crucible_gtceu_worldgen import GtceuWorldgenValidationError  # noqa: E402
from workbench_crucible_gtceu_worldgen.core_overlay import (  # noqa: E402
    materialize_core_overlay,
    reconcile_core_overlay,
    review_core_overlays,
)


def _absolute(path: Path) -> Path:
    selected = path.expanduser()
    return selected if selected.is_absolute() else Path.cwd() / selected


def _output_path(path: Path, workspace: Path) -> Path:
    selected = path.expanduser()
    return selected if selected.is_absolute() else workspace / selected


def _json_object(path: Path, context: str) -> dict:
    try:
        value = json.loads(path.expanduser().read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GtceuWorldgenValidationError(f"cannot read {context}: {exc}") from exc
    if not isinstance(value, dict):
        raise GtceuWorldgenValidationError(f"{context} must be a JSON object")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path, default=ROOT,
        help="Workbench workspace that owns this overlay (default: this checkout).",
    )
    parser.add_argument(
        "--configuration-home", type=Path, default=None,
        help="Core record home (default: stable per-user Workbench configuration home).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("materialize", help="Publish one fresh Core overlay envelope.")
    create.add_argument("--jar", type=Path, required=True)
    create.add_argument("--config-root", type=Path, required=True)
    create.add_argument("--inventory", type=Path, required=True)
    create.add_argument("--plan", type=Path, required=True)
    create.add_argument(
        "--out-config-root", type=Path, required=True,
        help="Fresh output ending in config/gregtech; Core owns the entire config envelope.",
    )
    commands.add_parser("review", help="List retained Core attempts without changing them.")
    recover = commands.add_parser(
        "reconcile", help="Finish one exact prepared Core publication after review.",
    )
    recover.add_argument("--attempt-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workspace = _absolute(args.workspace).resolve(strict=True)
    configuration_home = (
        default_user_config_home() if args.configuration_home is None
        else _absolute(args.configuration_home)
    )
    trees = CoreManagedTrees(
        workspace=workspace, configuration_home=configuration_home,
        locations={"artifacts": workspace}, owner_id="crucible",
        location_sources={"artifacts": "workspace"},
    )
    inputs = CoreOverlayEnvelopeInputs(
        workspace=workspace, configuration_home=configuration_home,
        owner_id="crucible",
    )
    with managed_trees_scope(trees), overlay_envelopes_scope(inputs):
        if args.command == "review":
            print(json.dumps({"attempts": review_core_overlays()}, indent=2, sort_keys=True))
            return 0
        if args.command == "reconcile":
            reference = reconcile_core_overlay(attempt_id=args.attempt_id)
        else:
            try:
                plan_bytes = args.plan.expanduser().read_bytes()
            except OSError as exc:
                raise GtceuWorldgenValidationError(f"cannot read overlay plan: {exc}") from exc
            reference = materialize_core_overlay(
                jar_path=args.jar, config_root=args.config_root,
                inventory=_json_object(args.inventory, "source inventory"),
                plan_bytes=plan_bytes,
                output_config_root=_output_path(args.out_config_root, workspace),
            )
    print(f"config: {reference.path / 'gregtech'}")
    print(f"receipt: {reference.path / 'overlay-materialization-v1.json'}")
    print(f"attempt: {reference.domain_id}")
    print(f"tree: {reference.tree_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OverlayEnvelopeInputError as exc:
        if exc.code == "overlay.unsupported":
            print(f"GTCEu Core overlay unsupported: {exc}", file=sys.stderr)
            raise SystemExit(2) from None
        print(f"GTCEu Core overlay failed ({exc.code}): {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, ManagedTreeError, GtceuWorldgenValidationError) as exc:
        print(f"GTCEu Core overlay failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
