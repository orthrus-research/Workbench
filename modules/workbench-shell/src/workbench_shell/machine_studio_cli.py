"""Read-only Machine Studio inspection over Atlas and Explorer authority."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, TextIO

from workbench_atlas.runtime_graph_query import (
    NodeSelector,
    PageRequest,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
)
from workbench_runtime_explorer.model import ExplorerError
from workbench_runtime_explorer.providers import atlas_runtime_provider
from workbench_runtime_explorer.query import (
    Explorer,
    ExplorerRequest,
    InterpretedIdentity,
)


FORMAT = "workbench-machine-inspection-v1"
SCHEMA_VERSION = 1
MAX_RECIPE_ROWS = 1000
MAX_RELATIONSHIP_ROWS = 1000
MAX_IDENTITY_ROWS = 256


class MachineStudioError(ValueError):
    """The explicit Atlas projection or exact machine selection is invalid."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _report_id(value: Mapping[str, Any]) -> str:
    material = deepcopy(dict(value))
    material.pop("report_id", None)
    return "workbench-machine-inspection:sha256:" + sha256(
        _canonical_bytes(material)
    ).hexdigest()


def _selector(
    identity: str,
    *,
    key_kind: str,
    profile: str | None,
    physical_side: str | None,
) -> NodeSelector:
    if not identity or any(character in identity for character in "\r\n\x00"):
        raise MachineStudioError("machine identity must be bounded single-line text")
    if key_kind == "runtime-node-id":
        return NodeSelector.by_id(
            identity,
            kind="machine",
            profile=profile,
            physical_side=physical_side,
        )
    return NodeSelector.by_key(
        key_kind,
        identity,
        kind="machine",
        profile=profile,
        physical_side=physical_side,
    )


def _explorer_projection(
    database: Path,
    machine: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    scope = machine.get("scope")
    if not isinstance(scope, Mapping):
        raise MachineStudioError("Atlas machine node has no exact runtime scope")
    profile = scope.get("profile")
    physical_side = scope.get("physical_side")
    if not isinstance(profile, str) or not isinstance(physical_side, str):
        raise MachineStudioError("Atlas machine node runtime scope is incomplete")
    node_id = str(machine["id"])
    provider = atlas_runtime_provider(
        database,
        node_id,
        kinds=("machine",),
        profile=profile,
        physical_side=physical_side,
        limit=1,
    )
    observed_projection = provider.source.identity
    for key, value in projection.items():
        if observed_projection.get(key) != value:
            raise MachineStudioError(
                "Atlas projection changed between machine and Explorer inspection"
            )
    request = ExplorerRequest(
        raw=f"runtime-node-id:{node_id}",
        text="",
        terms=(),
        filters={"kind": ("machine",)},
        interpreted=(
            InterpretedIdentity(
                "runtime-node-id",
                node_id,
                "exact Atlas machine selection",
            ),
        ),
        limit=1,
    )
    result = Explorer((provider.source,), provider.records).search(request)
    if result["summary"]["returned"] != 1:
        raise MachineStudioError(
            "Exact Runtime Explorer did not retain the selected Atlas machine"
        )
    return result


def inspect_machine(
    database: Path,
    identity: str,
    *,
    key_kind: str = "registry-name",
    profile: str | None = None,
    physical_side: str | None = None,
    recipe_limit: int = 100,
    relationship_limit: int = 250,
) -> dict[str, Any]:
    """Inspect one exact machine while retaining Atlas and Explorer records."""

    if isinstance(recipe_limit, bool) or not 1 <= recipe_limit <= MAX_RECIPE_ROWS:
        raise MachineStudioError(
            f"recipe limit must be an integer from 1 through {MAX_RECIPE_ROWS}"
        )
    if (
        isinstance(relationship_limit, bool)
        or not 1 <= relationship_limit <= MAX_RELATIONSHIP_ROWS
    ):
        raise MachineStudioError(
            "relationship limit must be an integer from 1 through "
            f"{MAX_RELATIONSHIP_ROWS}"
        )
    selector = _selector(
        identity,
        key_kind=key_kind,
        profile=profile,
        physical_side=physical_side,
    )
    with RuntimeGraphReader(database) as reader:
        projection = reader.projection_identity()
        machine = reader.resolve_one(selector, label="machine")
        identity_page = reader.node_identity_keys(
            str(machine["id"]),
            page=PageRequest(limit=MAX_IDENTITY_ROWS),
        )
        relationship_page = reader.related_nodes(
            str(machine["id"]),
            page=PageRequest(limit=relationship_limit),
        )
        recipe_page = reader.machine_recipes(
            NodeSelector.by_id(
                str(machine["id"]),
                kind="machine",
                profile=str(machine["scope"]["profile"]),
                physical_side=str(machine["scope"]["physical_side"]),
            ),
            PageRequest(limit=recipe_limit),
        )

    explorer = _explorer_projection(database, machine, projection)
    relationships = [dict(row) for row in relationship_page.items]
    form_paths = [
        row
        for row in relationships
        if row["direction"] == "outgoing"
        and row["relationship"].get("predicate") == "has_form"
    ]
    recipe_maps = [
        row
        for row in relationships
        if row["direction"] == "outgoing"
        and row["relationship"].get("predicate")
        in {"executes_recipe_map", "consults_recipe_map"}
    ]
    structure_candidates = [
        row
        for row in relationships
        if row["relationship"].get("predicate") == "has_constraint"
        or row["node"].get("kind") in {"structure", "constraint"}
    ]
    ability_candidates = [
        row
        for row in relationships
        if row["node"].get("kind") in {"capability", "energy"}
        or row["relationship"].get("predicate")
        in {"uses_energy", "produces_energy"}
    ]
    report: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "report_id": "",
        "authority": {
            "knowledge_owner": "Atlas",
            "navigation_owner": "Exact Runtime Explorer",
            "composition_owner": "Machine Studio",
        },
        "input": {
            "runtime_database": str(database.resolve()),
            "selector": {
                "key_kind": key_kind,
                "key_value": identity,
                "profile": profile,
                "physical_side": physical_side,
            },
        },
        "atlas": {
            "projection": projection,
            "machine": machine,
            "identity_keys": [dict(row) for row in identity_page.items],
            "identity_keys_truncated": identity_page.truncated,
            "relationships": relationships,
            "relationships_total": relationship_page.total,
            "relationships_truncated": relationship_page.truncated,
            "recipes": [dict(row) for row in recipe_page.items],
            "recipes_total": recipe_page.total,
            "recipes_truncated": recipe_page.truncated,
        },
        "explorer": explorer,
        "views": {
            "registered_forms": form_paths,
            "recipe_maps": recipe_maps,
            "structure_candidates": structure_candidates,
            "ability_candidates": ability_candidates,
        },
        "evidence_states": {
            "registered_machine": {
                "state": "observed",
                "basis": "accepted Atlas runtime-graph machine node",
            },
            "static_structure": {
                "state": (
                    "candidate-paths-present"
                    if structure_candidates
                    else "not-present-in-selected-projection"
                ),
                "basis": (
                    "exact Atlas has_constraint or structure/constraint adjacency; "
                    "Machine Studio does not reinterpret it as a formed machine"
                ),
            },
            "formed_world_machine": {
                "state": "not-supplied",
                "basis": (
                    "the selected Atlas registry graph contains no retained world "
                    "occurrence proving that this machine formed at a position"
                ),
            },
        },
        "summary": {
            "status": (
                "limited"
                if relationship_page.truncated
                or recipe_page.truncated
                or identity_page.truncated
                else "complete-without-world-proof"
            ),
            "registered_forms": len(form_paths),
            "recipe_maps": len(recipe_maps),
            "recipes": recipe_page.total,
            "structure_candidates": len(structure_candidates),
            "ability_candidates": len(ability_candidates),
        },
        "claims": {
            "registered_machine_observed": True,
            "static_structure_formed": False,
            "world_machine_formed": False,
            "machine_operational": False,
            "recipe_execution_observed": False,
        },
        "limitations": [
            "Atlas owns the machine and relationship records; Machine Studio only groups exact rows for review.",
            "Explorer owns identity grouping, ownership projection, and source navigation; its ranking is presentation only.",
            "Registered forms, recipe maps, constraints, energy, or capability rows do not prove that a multiblock formed in a world.",
            "Listed recipes are lookup-active registry observations, not observed executions or playability claims.",
        ],
    }
    report["report_id"] = _report_id(report)
    return report


def _render(report: Mapping[str, Any]) -> str:
    machine = report["atlas"]["machine"]
    summary = report["summary"]
    states = report["evidence_states"]
    identities = report["atlas"]["identity_keys"]
    lines = [
        f"Machine inspection: {summary['status']}",
        f"Machine: {machine['id']}",
        (
            "Scope: "
            f"{machine['scope'].get('profile', 'unknown')} / "
            f"{machine['scope'].get('physical_side', 'unknown')}"
        ),
    ]
    if identities:
        lines.append(
            "Exact identities: "
            + ", ".join(
                f"{row['key_kind']}={row['key_value']}" for row in identities[:8]
            )
        )
    lines.extend(
        (
            (
                "Registered graph: observed; "
                f"{summary['registered_forms']} form(s), "
                f"{summary['recipe_maps']} recipe map(s), "
                f"{summary['recipes']} lookup-active recipe(s)"
            ),
            (
                "Static structure: "
                f"{states['static_structure']['state']} "
                f"({summary['structure_candidates']} candidate path(s))"
            ),
            "Formed in a world: not supplied",
            "Claim boundary: registration and static relationships do not prove a formed or operational machine.",
            (
                "Navigate the exact identity: workbench explore "
                f"id:{machine['id']} --runtime-db {report['input']['runtime_database']} "
                "--details"
            ),
        )
    )
    if report["atlas"]["relationships_truncated"] or report["atlas"]["recipes_truncated"]:
        lines.append("The selected bounds truncated details; use --json and larger limits before drawing conclusions.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench machine",
        description=(
            "Inspect one exact machine from an immutable Atlas runtime graph, "
            "with Explorer-owned identity navigation and explicit evidence gaps."
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect", help="inspect one exact machine identity")
    inspect.add_argument("identity")
    inspect.add_argument(
        "--runtime-db",
        required=True,
        type=Path,
        help="explicit immutable Atlas runtime-graph query database",
    )
    inspect.add_argument(
        "--key-kind",
        default="registry-name",
        help="exact Atlas key kind (default: registry-name; use runtime-node-id for canonical IDs)",
    )
    inspect.add_argument("--profile", help="optional exact Atlas profile")
    inspect.add_argument("--side", help="optional exact Atlas physical side")
    inspect.add_argument(
        "--recipe-limit",
        type=int,
        default=100,
        help=f"maximum recipe details from 1 through {MAX_RECIPE_ROWS}",
    )
    inspect.add_argument(
        "--relationship-limit",
        type=int,
        default=250,
        help=f"maximum relationship details from 1 through {MAX_RELATIONSHIP_ROWS}",
    )
    inspect.add_argument(
        "--json", action="store_true", help="emit the complete V1 inspection"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    output = sys.stdout if output is None else output
    error = sys.stderr if error is None else error
    try:
        report = inspect_machine(
            args.runtime_db,
            args.identity,
            key_kind=args.key_kind,
            profile=args.profile,
            physical_side=args.side,
            recipe_limit=args.recipe_limit,
            relationship_limit=args.relationship_limit,
        )
        if args.json:
            json.dump(report, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        else:
            output.write(_render(report))
        return 0
    except (ExplorerError, MachineStudioError, RuntimeGraphQueryError, OSError, ValueError) as exc:
        print(f"Machine inspection failed: {exc}", file=error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
