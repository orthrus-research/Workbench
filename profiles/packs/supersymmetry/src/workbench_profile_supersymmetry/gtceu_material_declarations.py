#!/usr/bin/env python3

"""V2 Supersymmetry material source normalization entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Sequence


from workbench_pack_program_studio.material_declarations import (  # noqa: E402
    ExpectedMaterialClosurePolicy,
    MaterialBuilderOperation,
    MaterialDeclarationPolicy,
    normalize_material_declarations_v2,
)

from workbench_profile_supersymmetry.gtceu_material_semantics import (  # noqa: E402
    GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2,
    GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
)


def _operation(
    terminal: str,
    kind: str,
    *,
    properties: tuple[str, ...] = (),
    fluid_storage_key: str | None = None,
    arities: tuple[int, ...] = (),
    requires: tuple[str, ...] = (),
) -> MaterialBuilderOperation:
    return MaterialBuilderOperation(
        terminal=terminal,
        kind=kind,
        property_keys=properties,
        fluid_storage_key=fluid_storage_key,
        allowed_arities=arities,
        required_property_keys=requires,
    )


_OPERATIONS = (
    _operation("addDefaultEnchant", "metadata", arities=(2,), requires=("tool",)),
    _operation("addOreByproducts", "property", properties=("ore",)),
    _operation("arcSmeltInto", "property", properties=("ingot",), arities=(1,)),
    _operation("blast", "property", properties=("blast",), arities=(1, 2)),
    _operation("blastTemp", "property", properties=("blast",), arities=(1, 2, 3, 4)),
    _operation("build", "terminator", arities=(0,)),
    _operation("burnTime", "property", properties=("dust",), arities=(1,)),
    _operation("cableProperties", "property", properties=("wire",), arities=(3, 4, 5)),
    _operation("color", "metadata", arities=(1,)),
    _operation("colorAverage", "metadata", arities=(0,)),
    _operation("components", "composition"),
    _operation("dust", "property", properties=("dust",), arities=(0, 1, 2)),
    _operation("element", "composition", arities=(1,)),
    _operation("flags", "flags"),
    _operation(
        "fluid",
        "fluid-form",
        properties=("fluid",),
        fluid_storage_key="from-arguments",
        arities=(0, 2, 4),
    ),
    _operation(
        "fluidPipeProperties",
        "property",
        properties=("fluid_pipe",),
        arities=(3, 6),
    ),
    _operation(
        "gas",
        "fluid-form",
        properties=("fluid",),
        fluid_storage_key="gas",
        arities=(0, 1),
    ),
    _operation("gem", "property", properties=("gem",), arities=(0, 1, 2)),
    _operation("iconSet", "metadata", arities=(1,)),
    _operation("ingot", "property", properties=("ingot",), arities=(0, 1, 2)),
    _operation("ingotSmeltInto", "property", properties=("ingot",), arities=(1,)),
    _operation(
        "itemPipeProperties",
        "property",
        properties=("item_pipe",),
        arities=(2,),
    ),
    _operation(
        "liquid",
        "fluid-form",
        properties=("fluid",),
        fluid_storage_key="liquid",
        arities=(0, 1),
    ),
    _operation("macerateInto", "property", properties=("ingot",), arities=(1,)),
    _operation("ore", "property", properties=("ore",), arities=(0, 1, 2, 3)),
    _operation("oreSmeltInto", "property", properties=("ore",), arities=(1,)),
    _operation(
        "plasma",
        "fluid-form",
        properties=("fluid",),
        fluid_storage_key="plasma",
        arities=(0, 1),
    ),
    _operation("polarizesInto", "property", properties=("ingot",), arities=(1,)),
    _operation("polymer", "property", properties=("polymer",), arities=(0, 1)),
    _operation("rotorStats", "property", properties=("rotor",), arities=(3,)),
    _operation("separatedInto", "property", properties=("ore",)),
    _operation("toolStats", "property", properties=("tool",), arities=(1,)),
    _operation("washedIn", "property", properties=("ore",), arities=(1, 2)),
    _operation("wood", "property", properties=("wood",), arities=(0, 1, 2)),
)


GTCEU_MATERIAL_DECLARATION_POLICY_V2 = MaterialDeclarationPolicy(
    policy_id=(
        "workbench://profiles/supersymmetry/groovy/material-declarations/"
        "gtceu-2.8.10-susycore-b5ee1120-v2"
    ),
    pack_profile_id="workbench-pack:supersymmetry",
    builder_callee="Material.Builder",
    operations=tuple(sorted(_OPERATIONS, key=lambda operation: operation.terminal)),
    registry_helpers=(("SuSyUtility.susyId", "susy"),),
    registry_name_normalization="lowercase",
    expected_closure=ExpectedMaterialClosurePolicy.from_mapping(
        GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2.to_dict()
    ),
)


def load_groovy_sources(
    source_root: Path,
    relative_paths: Sequence[str] | None = None,
) -> dict[str, bytes]:
    """Load exact bytes below one root without following an escaping path."""

    root = source_root.resolve()
    if not root.is_dir():
        raise ValueError(f"source root is not a directory: {source_root}")
    if relative_paths is None:
        paths = sorted(
            path
            for path in root.rglob("*.groovy")
            if ".workbench" not in path.relative_to(root).parts
        )
    else:
        paths = []
        for raw in relative_paths:
            relative = PurePosixPath(raw)
            if relative.is_absolute() or ".." in relative.parts or str(relative) != raw:
                raise ValueError(f"source path is not portable and relative: {raw}")
            candidate = (root / Path(*relative.parts)).resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise ValueError(f"source path does not resolve to a file below root: {raw}")
            paths.append(candidate)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in paths
    }


def normalize_supersymmetry_material_declarations_v2(
    source_root: Path,
    relative_paths: Sequence[str] | None = None,
) -> dict[str, object]:
    return normalize_material_declarations_v2(
        load_groovy_sources(source_root, relative_paths),
        GTCEU_MATERIAL_DECLARATION_POLICY_V2,
        GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize exact Supersymmetry Groovy Material.Builder declarations "
            "under the V2 semantic policy without claiming runtime execution."
        )
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = normalize_supersymmetry_material_declarations_v2(
        args.source_root,
        args.paths,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GTCEU_MATERIAL_DECLARATION_POLICY_V2",
    "load_groovy_sources",
    "normalize_supersymmetry_material_declarations_v2",
]
