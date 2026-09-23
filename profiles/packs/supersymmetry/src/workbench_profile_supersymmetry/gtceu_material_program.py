#!/usr/bin/env python3

"""Bounded Supersymmetry material generator and mutation projection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from workbench_pack_program_studio.material_program import (
    MaterialMutationHelper,
    MaterialMutationMethod,
    MaterialProgramPolicy,
    normalize_material_program_v2,
)

from workbench_profile_supersymmetry.gtceu_material_declarations import (
    GTCEU_MATERIAL_DECLARATION_POLICY_V2,
    load_groovy_sources,
    normalize_supersymmetry_material_declarations_v2,
)
from workbench_profile_supersymmetry.gtceu_material_semantics import (
    GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
)


GTCEU_MATERIAL_PROGRAM_POLICY_V2 = MaterialProgramPolicy(
    policy_id=(
        "workbench://profiles/supersymmetry/groovy/material-program/"
        "gtceu-2.8.10-susycore-b5ee1120-v2"
    ),
    mutation_methods=tuple(sorted((
        MaterialMutationMethod("addDust", "add-property", "dust"),
        MaterialMutationMethod("addFlags", "add-flags"),
        MaterialMutationMethod("addGem", "add-property", "gem"),
        MaterialMutationMethod("addIngot", "add-property", "ingot"),
        MaterialMutationMethod("addOre", "add-property", "ore"),
        MaterialMutationMethod("setFormula", "set-formula"),
        MaterialMutationMethod("setMaterialIconSet", "set-presentation", "icon_set"),
        MaterialMutationMethod("setMaterialRGB", "set-presentation", "rgb"),
        MaterialMutationMethod("setProperty", "set-property"),
    ), key=lambda method: method.terminal)),
    mutation_helpers=tuple(sorted((
        MaterialMutationHelper(
            "addBlastProperty",
            ("blast",),
            target_mode="receiver",
        ),
        MaterialMutationHelper(
            "addFluidPipes",
            ("fluid_pipe",),
            target_mode="receiver",
        ),
        MaterialMutationHelper(
            "addMillBall",
            ("mill_ball",),
            target_mode="receiver",
        ),
        MaterialMutationHelper(
            "setupFluidType",
            ("fluid",),
            fluid_storage_argument=1,
        ),
        MaterialMutationHelper(
            "setupFluidTypes",
            ("fluid",),
            fluid_storage_argument=1,
            target_mode="receiver",
        ),
        MaterialMutationHelper(
            "setupSlurries",
            ("fluid",),
            ("impure_slurry", "slurry"),
            target_mode="receiver",
        ),
    ), key=lambda helper: helper.callee)),
    lifecycle_overrides=(
        ("classes/ChangeFlags.groovy", "init", "material-event"),
    ),
)


def default_material_program_paths(source_root: Path) -> tuple[str, ...]:
    """Select the material registry program without unrelated recipe scripts."""

    root = source_root.resolve()
    paths = {
        path.relative_to(root).as_posix()
        for path in (root / "material").rglob("*.groovy")
        if path.is_file()
    }
    for relative in (
        "classes/ChangeFlags.groovy",
        "preInit/MaterialChanges.groovy",
    ):
        if (root / relative).is_file():
            paths.add(relative)
    return tuple(sorted(paths, key=lambda value: value.encode("utf-8")))


def normalize_supersymmetry_material_program_v2(
    source_root: Path,
    relative_paths: Sequence[str] | None = None,
) -> dict[str, object]:
    """Normalize the selected exact Groovy bytes as one material program."""

    selected_paths = (
        default_material_program_paths(source_root)
        if relative_paths is None
        else tuple(relative_paths)
    )
    sources = load_groovy_sources(source_root, selected_paths)
    declarations = normalize_supersymmetry_material_declarations_v2(
        source_root, selected_paths
    )
    return normalize_material_program_v2(
        sources,
        declarations,
        GTCEU_MATERIAL_DECLARATION_POLICY_V2,
        GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        GTCEU_MATERIAL_PROGRAM_POLICY_V2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize the bounded Supersymmetry Groovy material registry "
            "program without claiming runtime execution."
        )
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = normalize_supersymmetry_material_program_v2(
        arguments.source_root,
        arguments.paths,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        sys.stdout.write(rendered)
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GTCEU_MATERIAL_PROGRAM_POLICY_V2",
    "default_material_program_paths",
    "main",
    "normalize_supersymmetry_material_program_v2",
]
