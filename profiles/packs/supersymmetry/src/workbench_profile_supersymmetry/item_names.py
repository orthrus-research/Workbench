"""Validate observed Groovy metaitem lookups without deriving item identities."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from .finite_item_matching import ARTIFACT_SHA256, FiniteItemMatchingError, _validate_nbt


NAMES_ADAPTER = "gt-item-names"
NAMES_CATEGORY = "reference-item-names"
RESOLVER_AUTHORITY = "GroovyScriptModule.getMetaItem"
# Original class entry in the pinned GT archive; not JVM-transformed bytes.
RESOLVER_CLASS_SHA256 = "22b4f9b8261594346cbc635b415fc1a3b2b6667cfd65cf4484680ef72c55a891"
_FIELDS = {"record_type", "query", "namespace", "name", "authority", "resolution", "stack",
           "gregtech_sha256", "resolver_class_sha256"}
_STACK_FIELDS = {"registry_name", "metadata", "item_damage", "count", "tag"}


class ItemNameObservationError(ValueError):
    """Observed name bindings do not meet the supported resolver contract."""


@dataclass(frozen=True)
class ItemNameBinding:
    ordinal: int
    record: dict[str, Any]


def _text(value: Any, label: str) -> str:
    if type(value) is not str or not value or any(ord(c) < 32 for c in value):
        raise ItemNameObservationError(f"invalid item name {label}")
    return value


def validate_item_names(
    records: list[Any], *, requested_queries: Any = None,
    check_cancelled: Callable[[], None] | None = None,
) -> tuple[ItemNameBinding, ...]:
    """Admit exact native resolver results, including explicitly null results.

    Namespace splitting follows the pinned resolver, including its unusual
    single-character-prefix behavior. This validates the declared query/result
    structure; it does not execute the resolver or independently prove capture.
    """
    cancel = check_cancelled or (lambda: None)
    cancel()
    if type(records) is not list:
        raise ItemNameObservationError("invalid item name records")
    requested: set[str] = set()
    if requested_queries is not None:
        if type(requested_queries) is not list:
            raise ItemNameObservationError("invalid requested item name queries")
        for query in requested_queries:
            cancel()
            _text(query, "requested query")
            if query in requested:
                raise ItemNameObservationError("duplicate requested item name query")
            requested.add(query)
    seen: set[str] = set()
    selected: dict[tuple[str, str], str] = {}
    bindings = []
    for ordinal, row in enumerate(records):
        cancel()
        if type(row) is not dict or set(row) != _FIELDS:
            raise ItemNameObservationError("invalid item name binding fields")
        if (row["record_type"] != "gt-item-name-binding"
                or row["authority"] != RESOLVER_AUTHORITY
                or row["gregtech_sha256"] != ARTIFACT_SHA256["gregtech_sha256"]
                or row["resolver_class_sha256"] != RESOLVER_CLASS_SHA256):
            raise ItemNameObservationError("unsupported item name resolver authority")
        query = _text(row["query"], "query")
        namespace, name = "gregtech", query
        separator = query.find(":")
        if separator >= 0:
            name = query[separator + 1:]
            if separator > 1:
                namespace = query[:separator]
        if (_text(row["namespace"], "namespace"), _text(row["name"], "name")) != (namespace, name):
            raise ItemNameObservationError("item name query and native split differ")
        if query in seen:
            raise ItemNameObservationError("duplicate item name query")
        seen.add(query)
        resolution = row["resolution"]
        stack = row["stack"]
        if resolution == "unresolved":
            if stack is not None:
                raise ItemNameObservationError("unresolved item name has a stack")
        elif resolution in ("cache", "meta-tile-entity"):
            if type(stack) is not dict or set(stack) != _STACK_FIELDS:
                raise ItemNameObservationError("invalid resolved item name stack")
            _text(stack["registry_name"], "stack registry")
            for field in ("count", "metadata", "item_damage"):
                if type(stack[field]) is not int or stack[field] < (1 if field == "count" else 0):
                    raise ItemNameObservationError(f"invalid resolved item name {field}")
            try:
                _validate_nbt(stack["tag"], cancel)
            except FiniteItemMatchingError as exc:
                raise ItemNameObservationError(str(exc)) from exc
        else:
            raise ItemNameObservationError("invalid item name resolution")
        identity = (namespace, name)
        result = json.dumps([resolution, stack], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if identity in selected and selected[identity] != result:
            raise ItemNameObservationError("equivalent item name queries have different results")
        selected[identity] = result
        bindings.append(ItemNameBinding(ordinal, row))
    if not requested.issubset(seen):
        raise ItemNameObservationError("requested item name query has no observation")
    cancel()
    return tuple(bindings)
