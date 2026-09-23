#!/usr/bin/env python3

"""Query normalized runtime-registration graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

SNAPSHOT_ID = "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
QUERY_DATABASE_APPLICATION_ID = 1398098247
QUERY_DATABASE_USER_VERSION = 2


class RuntimeGraphError(ValueError):
    """Raised when a normalized runtime graph query is invalid."""


def canonical_json_payload(value: Any) -> bytes:
    """Return canonical JSON bytes without the JSONL record delimiter.

    The Java producer's ``CanonicalJson.sha256(JsonElement)`` hashes this exact
    payload.  Keep the newline separate so content identities do not
    accidentally include the JSONL framing byte.
    """

    fragments: list[str] = []
    _write_canonical_json(value, fragments)
    return "".join(fragments).encode("utf-8")


def canonical_json(value: Any) -> bytes:
    """Return one newline-delimited canonical JSON record."""

    return canonical_json_payload(value) + b"\n"


def _write_canonical_json(value: Any, fragments: list[str]) -> None:
    """Encode the subset emitted by the Java producer's CanonicalJson writer."""

    if value is None:
        fragments.append("null")
        return
    if value is True:
        fragments.append("true")
        return
    if value is False:
        fragments.append("false")
        return
    if isinstance(value, int):
        fragments.append(str(value))
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical JSON number is not finite")
        # java.math.BigDecimal does not retain a negative sign on zero, but it
        # does retain scale and exponent notation.
        fragments.append(str(value.copy_abs() if value.is_zero() else value))
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON number is not finite")
        # Parsed runtime JSON uses Decimal above.  This branch supports
        # programmatically constructed graph fixtures without routing a
        # binary float through the permissive stdlib NaN/Infinity encoder.
        fragments.append(str(Decimal(str(value))))
        return
    if isinstance(value, str):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # Gson's JsonWriter always escapes these two JavaScript line separators,
        # even when HTML-safe escaping is disabled.
        fragments.append(encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
        return
    if isinstance(value, list):
        fragments.append("[")
        for index, element in enumerate(value):
            if index:
                fragments.append(",")
            _write_canonical_json(element, fragments)
        fragments.append("]")
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        fragments.append("{")
        for index, key in enumerate(sorted(value)):
            if index:
                fragments.append(",")
            _write_canonical_json(key, fragments)
            fragments.append(":")
            _write_canonical_json(value[key], fragments)
        fragments.append("}")
        return
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def edge_id(record: dict[str, Any]) -> str:
    projection = {
        "predicate": record["predicate"],
        "subject": record["subject"],
        "object": record["object"],
        "attributes": record["attributes"],
    }
    return "rge:" + sha256_bytes(canonical_json_payload(projection))


def query_machine_recipes(
    database: Path,
    machine_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Adapt the stable reader API to the original machine-recipes JSON."""

    from workbench_atlas.runtime_graph_query import (
        NodeSelector,
        PageRequest,
        RuntimeGraphQueryError,
        RuntimeGraphReader,
        legacy_machine_recipes_payload,
    )

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise RuntimeGraphError("recipe query limit must be an integer from 1 through 1000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise RuntimeGraphError("recipe query offset must be a non-negative integer")
    try:
        page_request = PageRequest(limit=limit, offset=offset)
        selector = NodeSelector.by_id(machine_id)
        with RuntimeGraphReader(database) as reader:
            page = reader.machine_recipes(selector, page_request)
            machine = reader.resolve_one(selector, label="machine")
            return legacy_machine_recipes_payload(machine, page)
    except RuntimeGraphQueryError as exc:
        raise RuntimeGraphError(str(exc)) from exc


def _query_selector(
    key_kind: str,
    key_value: str,
    kind: str,
) -> Any:
    """Build one exact typed selector without importing query code at startup."""

    from workbench_atlas.runtime_graph_query import NodeSelector, RuntimeGraphQueryError

    try:
        return NodeSelector.by_key(key_kind, key_value, kind=kind)
    except RuntimeGraphQueryError as exc:
        raise RuntimeGraphError(str(exc)) from exc


def _query_scope(scope_values: Iterable[str]) -> Any:
    """Parse repeatable ``PROFILE:PHYSICAL_SIDE`` query scopes fail-closed."""

    from workbench_atlas.runtime_graph_domain_query import (
        GraphScope,
        ProfileScope,
    )
    from workbench_atlas.runtime_graph_query import RuntimeGraphQueryError

    scopes = []
    seen: set[tuple[str, str]] = set()
    for value in scope_values:
        parts = value.split(":")
        if (
            len(parts) != 2
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", parts[0])
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", parts[1])
        ):
            raise RuntimeGraphError(
                "query scope must use PROFILE:PHYSICAL_SIDE with uppercase "
                f"runtime identifiers: {value}"
            )
        pair = (parts[0], parts[1])
        if pair in seen:
            raise RuntimeGraphError(f"query scope is duplicated: {value}")
        seen.add(pair)
        try:
            scopes.append(ProfileScope(profile=pair[0], physical_side=pair[1]))
        except RuntimeGraphQueryError as exc:
            raise RuntimeGraphError(str(exc)) from exc
    if not scopes:
        raise RuntimeGraphError("at least one explicit query scope is required")
    try:
        return GraphScope.of(*scopes)
    except RuntimeGraphQueryError as exc:
        raise RuntimeGraphError(str(exc)) from exc


def query_domain_occurrences(
    database: Path,
    relationship: str,
    *,
    key_kind: str,
    key_value: str,
    kind: str,
    scopes: Iterable[str],
    limit: int = 100,
    offset: int = 0,
    predicates: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Query one bounded page of exact producer or consumer occurrences."""

    from workbench_atlas.runtime_graph_domain_query import (
        find_consumers,
        find_producers,
    )
    from workbench_atlas.runtime_graph_query import (
        PageRequest,
        RuntimeGraphQueryError,
        RuntimeGraphReader,
    )

    if relationship not in {"producers", "consumers"}:
        raise RuntimeGraphError(
            f"unsupported runtime graph domain relationship: {relationship}"
        )
    if relationship != "consumers" and predicates is not None:
        raise RuntimeGraphError(
            "predicate filtering is supported only for consumer occurrences"
        )
    try:
        selector = _query_selector(key_kind, key_value, kind)
        graph_scope = _query_scope(scopes)
        page = PageRequest(limit=limit, offset=offset)
        with RuntimeGraphReader(database) as reader:
            result = (
                find_producers(reader, selector, page, graph_scope)
                if relationship == "producers"
                else find_consumers(
                    reader,
                    selector,
                    page,
                    graph_scope,
                    predicates=predicates,
                )
            )
            return result.to_dict()
    except RuntimeGraphQueryError as exc:
        raise RuntimeGraphError(str(exc)) from exc


def query_process_chain(
    database: Path,
    *,
    key_kind: str,
    key_value: str,
    kind: str,
    scopes: Iterable[str],
    max_depth: int = 8,
    max_routes: int = 50,
    max_alternatives_per_slot: int = 25,
    max_visited_nodes: int = 10000,
    include_chanced_outputs: bool = True,
    include_procedural_rules: bool = True,
) -> dict[str, Any]:
    """Build one bounded, exact process-chain route DAG."""

    from workbench_atlas.runtime_graph_chain_query import ChainOptions, build_process_chain
    from workbench_atlas.runtime_graph_query import RuntimeGraphQueryError, RuntimeGraphReader

    try:
        selector = _query_selector(key_kind, key_value, kind)
        graph_scope = _query_scope(scopes)
        options = ChainOptions(
            max_depth=max_depth,
            max_routes=max_routes,
            max_alternatives_per_slot=max_alternatives_per_slot,
            include_chanced_outputs=include_chanced_outputs,
            include_procedural_rules=include_procedural_rules,
            max_visited_nodes=max_visited_nodes,
        )
        with RuntimeGraphReader(database) as reader:
            return build_process_chain(
                reader,
                selector,
                options=options,
                graph_scope=graph_scope,
            )
    except RuntimeGraphQueryError as exc:
        raise RuntimeGraphError(str(exc)) from exc


def _add_domain_selector_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--key-kind",
        required=True,
        help="exact normalized node-key kind (for example material-resource-location)",
    )
    parser.add_argument("--key-value", required=True, help="exact node-key value")
    parser.add_argument("--kind", required=True, help="expected runtime node kind")
    parser.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="PROFILE:PHYSICAL_SIDE",
        help="exact graph scope to consult; repeat for additional scopes",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    machine_recipes = subparsers.add_parser(
        "machine-recipes",
        help="query a normalized database for a machine's recipes and slot alternatives",
    )
    machine_recipes.add_argument("database", type=Path)
    machine_recipes.add_argument("machine")
    machine_recipes.add_argument("--limit", type=int, default=100)
    machine_recipes.add_argument("--offset", type=int, default=0)
    for command, noun in (
        ("producers", "producer"),
        ("consumers", "consumer"),
    ):
        domain = subparsers.add_parser(
            command,
            help=(
                f"query exact, scope-bounded mechanical {noun} occurrences "
                "in a normalized database"
            ),
        )
        _add_domain_selector_arguments(domain)
        domain.add_argument("--limit", type=int, default=100)
        domain.add_argument("--offset", type=int, default=0)
        if command == "consumers":
            domain.add_argument(
                "--predicate",
                action="append",
                metavar="PREDICATE",
                help=(
                    "input predicate to include; repeat to combine consumes, "
                    "may_consume, and requires (default: all three)"
                ),
            )
    process_chain = subparsers.add_parser(
        "process-chain",
        help="build a bounded process-chain route DAG for one exact target",
    )
    _add_domain_selector_arguments(process_chain)
    process_chain.add_argument("--max-depth", type=int, default=8)
    process_chain.add_argument("--max-routes", type=int, default=50)
    process_chain.add_argument("--max-alternatives-per-slot", type=int, default=25)
    process_chain.add_argument("--max-visited-nodes", type=int, default=10000)
    process_chain.add_argument(
        "--exclude-chanced-outputs",
        action="store_true",
        help="exclude routes and byproducts carrying explicit chance metadata",
    )
    process_chain.add_argument(
        "--exclude-procedural-rules",
        action="store_true",
        help="exclude recipe_rule and process_rule producer routes explicitly",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "machine-recipes":
            result = query_machine_recipes(
                args.database,
                args.machine,
                limit=args.limit,
                offset=args.offset,
            )
            print(canonical_json_payload(result).decode("utf-8"))
        elif args.command in {"producers", "consumers"}:
            result = query_domain_occurrences(
                args.database,
                args.command,
                key_kind=args.key_kind,
                key_value=args.key_value,
                kind=args.kind,
                scopes=args.scope,
                limit=args.limit,
                offset=args.offset,
                predicates=getattr(args, "predicate", None),
            )
            print(canonical_json_payload(result).decode("utf-8"))
        elif args.command == "process-chain":
            result = query_process_chain(
                args.database,
                key_kind=args.key_kind,
                key_value=args.key_value,
                kind=args.kind,
                scopes=args.scope,
                max_depth=args.max_depth,
                max_routes=args.max_routes,
                max_alternatives_per_slot=args.max_alternatives_per_slot,
                max_visited_nodes=args.max_visited_nodes,
                include_chanced_outputs=not args.exclude_chanced_outputs,
                include_procedural_rules=not args.exclude_procedural_rules,
            )
            print(canonical_json_payload(result).decode("utf-8"))
    except RuntimeGraphError as exc:
        print(f"runtime graph invalid: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
