"""Compatibility surface for the public material-classification input contract.

Material Semantics still owns policy generation in :mod:`.policy`. Consumers
can import the immutable input type from Workbench API without this product.
The legacy private helpers remain for metadata-compatible Atlas 0.1.0 installs;
new consumers use their own result helpers and the public policy contract.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from workbench_api.material_classification import MaterialClassificationPolicy
from .errors import RuntimeGraphQueryError


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeGraphQueryError(
            f"material classification is not canonical JSON: {exc}"
        ) from exc


def _json_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_tree(child) for child in value]
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeGraphQueryError(f"{label} must be a non-empty string")
    return value


def _sorted_text_tuple(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RuntimeGraphQueryError(f"{label} must be a sequence of names")
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise RuntimeGraphQueryError(f"{label} contains an invalid name")
    expected = tuple(sorted(set(result), key=lambda value: value.encode("utf-8")))
    if result != expected:
        raise RuntimeGraphQueryError(
            f"{label} must be unique and canonically sorted"
        )
    return result


def _mapping_rows(
    values: Sequence[tuple[str, Sequence[str]]],
    label: str,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    for key, children in values:
        key = _required_text(key, f"{label} key")
        rows.append((key, _sorted_text_tuple(children, f"{label} {key}")))
    expected = sorted(rows, key=lambda row: row[0].encode("utf-8"))
    if rows != expected or len({key for key, _ in rows}) != len(rows):
        raise RuntimeGraphQueryError(
            f"{label} must have unique, canonically sorted keys"
        )
    return tuple(rows)


__all__ = ["MaterialClassificationPolicy"]
