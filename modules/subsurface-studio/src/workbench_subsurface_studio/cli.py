"""Terminal-first command surface for GTCEu Subsurface Studio V1."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence, TextIO

try:
    from workbench_api.events import sanitize_terminal
except ImportError:
    def sanitize_terminal(value: str) -> str:
        return "".join(character if ord(character) >= 32 else "�" for character in value)

from .model import SubsurfaceStudioError, load_profile
from .render import render_report
from .studio import (
    LAYER_DEFINITIONS,
    StudioDataset,
    chunk_map,
    compare,
    definitions,
    explain,
    fluids,
    layers,
    load_dataset,
    section,
    summary,
)


PROFILE_PATHS = {
    "supersymmetry": Path("profiles/packs/supersymmetry/subsurface/gtceu-2.8.10-v1.json")
}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {sanitize_terminal(message)}\n")


def build_parser(*, prog: str = "workbench subsurface") -> argparse.ArgumentParser:
    parser = _Parser(
        prog=prog,
        description=(
            "Inspect exact GTCEu ore, lithology, final cave-space, and virtual-fluid "
            "state without editing or launching a world."
        ),
    )
    profile = parser.add_mutually_exclusive_group(required=True)
    profile.add_argument(
        "--profile",
        choices=tuple(PROFILE_PATHS),
        help="explicit pack profile; Supersymmetry is never assumed universally",
    )
    profile.add_argument(
        "--profile-file",
        type=Path,
        help="explicit workbench-subsurface-pack-profile-v1 JSON",
    )
    parser.add_argument("--inventory", type=Path, help="exact Crucible GTCEu inventory V1")
    impact = parser.add_mutually_exclusive_group()
    impact.add_argument("--impact", type=Path, help="exact Crucible GTCEu impact inventory V2")
    impact.add_argument(
        "--without-impact",
        action="store_true",
        help="deliberately omit full control metadata and expose the result as partial",
    )
    parser.add_argument("--manifest", type=Path, help="exact Strata region V2 manifest")
    parser.add_argument("--trace", type=Path, help="optional controlled GTCEu subsurface trace V1")
    parser.add_argument(
        "--require-trace",
        action="store_true",
        help="fail if exact controlled deposit/position attribution is unavailable",
    )
    parser.add_argument("--json", action="store_true", help="emit the exact result JSON")
    parser.add_argument(
        "--sources",
        action="store_true",
        help="include exact source bindings and result identity in the terminal view",
    )

    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("summary", help="summarize exact ore, lithology, cave-space, and fluids")
    commands.add_parser("layers", help="list available layers and evidence states")

    map_parser = commands.add_parser("map", help="render one deterministic chunk layer")
    map_parser.add_argument(
        "--layer",
        required=True,
        choices=tuple(row["layer_id"] for row in LAYER_DEFINITIONS),
    )
    map_parser.add_argument("--material", help="exact ore material token")

    explain_parser = commands.add_parser(
        "explain", help="explain one exact final coordinate and any matching trace decision"
    )
    explain_parser.add_argument(
        "--position", nargs=3, type=int, metavar=("X", "Y", "Z"), required=True
    )
    explain_parser.add_argument("--material", help="ask about one ore material at this coordinate")
    explain_parser.add_argument(
        "--cave-radius", type=int, default=8, help="nearest final subsurface-air radius, 0..32"
    )

    section_parser = commands.add_parser(
        "section", help="render an exact vertical lithology/ore/cave section"
    )
    plane = section_parser.add_mutually_exclusive_group(required=True)
    plane.add_argument("--x", type=int, help="fixed world X; horizontal axis is Z")
    plane.add_argument("--z", type=int, help="fixed world Z; horizontal axis is X")
    section_parser.add_argument("--min-y", type=int, default=0)
    section_parser.add_argument("--max-y", type=int, default=255)
    section_parser.add_argument("--min-axis", type=int)
    section_parser.add_argument("--max-axis", type=int)
    section_parser.add_argument("--material", help="highlight one ore material")

    definitions_parser = commands.add_parser(
        "definitions", help="inspect exact version-bound GTCEu ore controls"
    )
    definitions_parser.add_argument("--material", help="exact material token")
    definitions_parser.add_argument("--query", help="case-insensitive definition/path search")

    commands.add_parser("fluids", help="inspect virtual GTCEu bedrock-fluid cells")

    compare_parser = commands.add_parser(
        "compare", help="compare an aligned baseline with the selected candidate capture"
    )
    compare_parser.add_argument("--baseline-manifest", type=Path, required=True)
    compare_parser.add_argument("--baseline-inventory", type=Path)
    baseline_impact = compare_parser.add_mutually_exclusive_group()
    baseline_impact.add_argument("--baseline-impact", type=Path)
    baseline_impact.add_argument("--baseline-without-impact", action="store_true")
    compare_parser.add_argument("--baseline-trace", type=Path)
    compare_parser.add_argument("--material", help="compare one ore material token")
    return parser


def _selected_profile(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], Any]:
    path = args.profile_file if args.profile_file is not None else root / PROFILE_PATHS[args.profile]
    profile, binding = load_profile(path)
    if args.profile is not None and profile["pack_profile"] != args.profile:
        raise SubsurfaceStudioError(
            f"selected profile {args.profile!r} resolves to pack {profile['pack_profile']!r}"
        )
    return profile, binding


def _profile_default(root: Path, profile: dict[str, Any], key: str) -> Path:
    return root / profile["defaults"][key]


def _optional_impact(
    *,
    explicit: Path | None,
    omit: bool,
    root: Path,
    profile: dict[str, Any],
) -> Path | None:
    if omit:
        return None
    if explicit is not None:
        return explicit
    default = _profile_default(root, profile, "impact")
    return default if default.exists() or default.is_symlink() else None


def _load_selected_dataset(
    args: argparse.Namespace,
    *,
    root: Path,
    profile: dict[str, Any],
    profile_binding: Any,
) -> StudioDataset:
    dataset = load_dataset(
        root=root,
        profile=profile,
        profile_binding=profile_binding,
        inventory_path=args.inventory or _profile_default(root, profile, "inventory"),
        impact_path=_optional_impact(
            explicit=args.impact,
            omit=args.without_impact,
            root=root,
            profile=profile,
        ),
        manifest_path=args.manifest or _profile_default(root, profile, "manifest"),
        trace_path=args.trace,
    )
    if args.require_trace and dataset.trace is None:
        raise SubsurfaceStudioError(
            "--require-trace was selected, but no validated controlled GTCEu trace was supplied"
        )
    return dataset


def _run_operation(
    args: argparse.Namespace,
    *,
    dataset: StudioDataset,
    root: Path,
    profile: dict[str, Any],
    profile_binding: Any,
) -> dict[str, Any]:
    if args.operation == "summary":
        return summary(dataset)
    if args.operation == "layers":
        return layers(dataset)
    if args.operation == "map":
        return chunk_map(dataset, layer=args.layer, material=args.material)
    if args.operation == "explain":
        return explain(
            dataset,
            position=tuple(args.position),
            material=args.material,
            cave_radius=args.cave_radius,
        )
    if args.operation == "section":
        return section(
            dataset,
            x=args.x,
            z=args.z,
            min_y=args.min_y,
            max_y=args.max_y,
            min_axis=args.min_axis,
            max_axis=args.max_axis,
            material=args.material,
        )
    if args.operation == "definitions":
        return definitions(dataset, material=args.material, query=args.query)
    if args.operation == "fluids":
        return fluids(dataset)
    if args.operation == "compare":
        baseline = load_dataset(
            root=root,
            profile=profile,
            profile_binding=profile_binding,
            inventory_path=(
                args.baseline_inventory
                or args.inventory
                or _profile_default(root, profile, "inventory")
            ),
            impact_path=_optional_impact(
                explicit=args.baseline_impact,
                omit=args.baseline_without_impact,
                root=root,
                profile=profile,
            ),
            manifest_path=args.baseline_manifest,
            trace_path=args.baseline_trace,
        )
        return compare(baseline, dataset, material=args.material)
    raise SubsurfaceStudioError(f"unsupported operation: {args.operation}")


def run(
    argv: Sequence[str] | None = None,
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        resolved_root = root.expanduser().resolve(strict=True)
        profile, profile_binding = _selected_profile(args, resolved_root)
        dataset = _load_selected_dataset(
            args,
            root=resolved_root,
            profile=profile,
            profile_binding=profile_binding,
        )
        report = _run_operation(
            args,
            dataset=dataset,
            root=resolved_root,
            profile=profile,
            profile_binding=profile_binding,
        )
        if args.json:
            output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            output.write(render_report(report, show_sources=args.sources))
        return 0
    except (OSError, SubsurfaceStudioError, ValueError) as exc:
        error.write(f"GTCEu Subsurface Studio failed: {sanitize_terminal(str(exc))}\n")
        return 2


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    selected_root = root or _repository_resource_root(__file__)
    return run(argv, root=selected_root)


__all__ = ["build_parser", "main", "run"]
