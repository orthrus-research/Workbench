"""Exact observation queries; graph edges are never promoted to gameplay claims."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from workbench_atlas_categorical_graph import CategoricalGraphQuery, inspect_query_index
from workbench_atlas_categorical_graph import AtlasCategoricalGraphError

from ._custody import GraphWitness


PREFIX = "workbench-atlas-observation-"
MAX_PAGE_SIZE = 1000
_NODE_COLUMNS = "id,kind,semantic_key,properties_json,evidence_json"
_EDGE_COLUMNS = "e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json"


class ObservationError(ValueError):
    """An observation request cannot be answered within its exact evidence context."""


class ObservationChangedError(ObservationError):
    """The view was invalidated and must be reopened with full verification."""


def _text(value: Any, label: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 262144 or any(ord(c) < 32 for c in value):
        raise ObservationError(f"{label} must be bounded, nonempty text without control characters")
    return value


def _limit(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_PAGE_SIZE:
        raise ObservationError(f"page limit must be in 1..{MAX_PAGE_SIZE}")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _context(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    partitions = [{key: part[key] for key in ("partition_id", "classification", "evidence_categories", "limitations")}
                  for part in manifest["partitions"]]
    declaration = manifest["evidence_binding"].get("coverage")
    return deepcopy({
        "format": PREFIX + "context-v1", "schema_version": 1,
        "root": str(root.resolve()), "graph_set_id": manifest["graph_set_id"],
        "graph_format": manifest["format"], "authority": manifest["authority"],
        "scope": manifest["scope"], "evidence_binding": manifest["evidence_binding"],
        "summary": manifest["summary"],
        "coverage": {"state": "declared" if declaration is not None else "not-declared",
                     "declaration": declaration, "partitions": partitions},
        "limitations": list(dict.fromkeys(item for part in partitions for item in part["limitations"])),
        "claim_boundary": "Retained observations and recorded relationships; no inferred matching, dependency, source causation or gameplay viability.",
    })


def describe_observations(path: Path, *, check_cancelled: Callable[[], None] | None = None) -> dict[str, Any]:
    cancel = check_cancelled or (lambda: None)
    cancel()
    status = inspect_query_index(Path(path), check_cancelled=cancel)
    cancel()
    return {**_context(Path(status["root"]), status["manifest"]), "query_index": status["query_index"]}


class ObservationView:
    """One verified immutable graph, reusable across linked observation queries."""

    def __init__(self, path: Path, *, check_cancelled: Callable[[], None] | None = None) -> None:
        if check_cancelled is not None and not callable(check_cancelled):
            raise ObservationError("cancellation callback must be callable")
        self._cancel = check_cancelled or (lambda: None)
        self._cancel()
        self._closed = False
        self._invalidated = False
        self._witness = GraphWitness(Path(path))
        self.query = CategoricalGraphQuery(self._witness.root, check_cancelled=self._cancel)
        try:
            self.root = self.query.bundle.resolve()
            self._manifest = deepcopy(self.query.manifest)
            self.check_current()
            if self._manifest != self._witness.manifest:
                raise ObservationChangedError("observation graph manifest changed during verification")
            self._cancel()
            self.query.connection.create_function("atlas_casefold", 1, lambda value: value.casefold(), deterministic=True)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._closed:
            self.query.close()
            self._closed = True

    @property
    def manifest(self) -> dict[str, Any]:
        return deepcopy(self._manifest)

    def check_current(self) -> None:
        """Reject reuse after file drift; full content verification occurs on open."""
        if self._invalidated:
            raise ObservationChangedError("observation graph changed; reopen and verify it")
        if self._closed:
            raise ObservationError("observation view is closed")
        try:
            self._witness.check()
        except AtlasCategoricalGraphError as error:
            self._invalidated = True
            self.close()
            raise ObservationChangedError(str(error)) from error

    def __enter__(self) -> "ObservationView":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def describe(self) -> dict[str, Any]:
        self._cancel()
        self.check_current()
        result = _context(self.root, self._manifest)
        self.check_current()
        return result

    def _rows(self, sql: str, parameters: list[Any]) -> list[Any]:
        self._cancel()
        self.check_current()
        cancelled: list[BaseException] = []

        def poll() -> int:
            try:
                self._cancel()
            except BaseException as error:
                cancelled.append(error)
                return 1
            return 0

        self.query.connection.set_progress_handler(poll, 1000)
        try:
            rows = list(self.query.connection.execute(sql, parameters))
        except sqlite3.OperationalError:
            if cancelled:
                raise cancelled[0]
            raise
        finally:
            self.query.connection.set_progress_handler(None, 0)
            self.check_current()
        self._cancel()
        return rows

    def _node(self, selection_id: str) -> dict[str, Any]:
        selection_id = _text(selection_id, "selection ID")
        rows = self._rows(f"SELECT {_NODE_COLUMNS} FROM nodes WHERE id=?", [selection_id])
        if not rows:
            raise ObservationError("selection does not exist in this graph")
        return self.query._node_row(rows[0])

    def _offset(self, cursor: str | None, request: dict[str, Any]) -> int:
        if cursor is None:
            return 0
        try:
            if type(cursor) is not str or len(cursor) > 4096:
                raise ValueError()
            raw = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
            value = json.loads(raw)
            if (type(value) is not dict or set(value) != {"format", "binding", "offset", "sha256"}
                    or value["format"] != PREFIX + "cursor-v1"
                    or type(value["offset"]) is not int or not 1 <= value["offset"] <= 2**63 - 1
                    or value["binding"] != _digest([self.manifest["graph_set_id"], request])
                    or value["sha256"] != _digest({key: row for key, row in value.items() if key != "sha256"})
                    or _canonical(value) != raw):
                raise ValueError()
            return value["offset"]
        except (ValueError, TypeError, UnicodeError, KeyError) as error:
            raise ObservationError("cursor is invalid or belongs to another graph or query") from error

    def _page(self, rows: list[Any], limit: int, offset: int, request: dict[str, Any]) -> dict[str, Any]:
        truncated = len(rows) > limit
        cursor = None
        if truncated:
            value = {"format": PREFIX + "cursor-v1", "binding": _digest([self.manifest["graph_set_id"], request]),
                     "offset": offset + limit}
            value["sha256"] = _digest(value)
            cursor = base64.urlsafe_b64encode(_canonical(value)).decode("ascii")
        self._cancel()
        self.check_current()
        return {"limit": limit, "offset": offset, "returned": min(len(rows), limit),
                "truncated": truncated, "next_cursor": cursor}

    def search(self, text: str, *, kind: str | None = None, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        text, limit = _text(text, "search text"), _limit(limit)
        if kind is not None:
            kind = _text(kind, "node kind")
        request = {"operation": "search", "query": text, "kind": kind, "limit": limit}
        offset = self._offset(cursor, request)
        escaped = text.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = "%" + escaped + "%"
        where = "(atlas_casefold(semantic_key) LIKE ? ESCAPE '\\' OR atlas_casefold(kind) LIKE ? ESCAPE '\\' OR atlas_casefold(properties_json) LIKE ? ESCAPE '\\')"
        parameters: list[Any] = [needle, needle, needle]
        if kind is not None:
            where += " AND kind=?"
            parameters.append(kind)
        rows = self._rows(f"SELECT {_NODE_COLUMNS} FROM nodes WHERE {where} "
                          "ORDER BY CASE WHEN atlas_casefold(semantic_key)=? THEN 0 "
                          "WHEN atlas_casefold(semantic_key) LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,kind,semantic_key,id LIMIT ? OFFSET ?",
                          [*parameters, text.casefold(), escaped + "%", limit + 1, offset])
        return {"format": PREFIX + "search-v1", "schema_version": 1, "context": self.describe(),
                "query": text, "kind": kind, "results": [self.query._node_row(row) for row in rows[:limit]],
                "page": self._page(rows, limit, offset, request)}

    def inspect(self, selection_id: str) -> dict[str, Any]:
        selected = self._node(selection_id)
        counts = {}
        for direction, endpoint in (("outgoing", "source_node"), ("incoming", "target_node")):
            rows = self._rows(f"SELECT e.relation,count(*) FROM edges e JOIN nodes n ON n.node_key=e.{endpoint} "
                              "WHERE n.id=? GROUP BY e.relation ORDER BY e.relation", [selection_id])
            counts[direction] = {row[0]: row[1] for row in rows}
        return {"format": PREFIX + "inspection-v1", "schema_version": 1, "context": self.describe(),
                "selection": selected, "relationships": counts}

    def relationships(self, selection_id: str, *, direction: str = "outgoing", relation: str | None = None,
                      limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        selected, limit = self._node(selection_id), _limit(limit)
        if type(direction) is not str or direction not in {"incoming", "outgoing"}:
            raise ObservationError("relationship direction must be incoming or outgoing")
        if relation is not None:
            relation = _text(relation, "relationship")
        request = {"operation": "relationships", "selection_id": selection_id,
                   "direction": direction, "relation": relation, "limit": limit}
        offset = self._offset(cursor, request)
        endpoint, other = ("s", "t") if direction == "outgoing" else ("t", "s")
        where, parameters = f"{endpoint}.id=?", [selection_id]
        if relation is not None:
            where += " AND e.relation=?"
            parameters.append(relation)
        columns = ",".join(f"{other}.{column}" for column in _NODE_COLUMNS.split(","))
        rows = self._rows(f"SELECT {_EDGE_COLUMNS},{columns} FROM edges e "
                          "JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node "
                          f"WHERE {where} ORDER BY e.relation,{other}.id,e.edge_key LIMIT ? OFFSET ?",
                          [*parameters, limit + 1, offset])
        return {"format": PREFIX + "relationships-v1", "schema_version": 1, "context": self.describe(),
                "selection": selected, "direction": direction, "relation": relation,
                "results": [{"edge": self.query._edge_row(row[:6]), "node": self.query._node_row(row[6:])}
                            for row in rows[:limit]], "page": self._page(rows, limit, offset, request)}

    def evidence(self, selection_id: str, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        selected, limit = self._node(selection_id), _limit(limit)
        request = {"operation": "evidence", "selection_id": selection_id, "limit": limit}
        offset = self._offset(cursor, request)
        rows = selected["evidence"][offset:offset + limit + 1]
        return {"format": PREFIX + "evidence-v1", "schema_version": 1, "context": self.describe(),
                "selection_id": selection_id, "state": "recorded" if selected["evidence"] else "not-recorded",
                "original_record_resolution": "unavailable-reader-not-selected" if selected["evidence"] else "not-recorded",
                "references": rows[:limit],
                "page": self._page(rows, limit, offset, request)}


def open_observations(path: Path, *, check_cancelled: Callable[[], None] | None = None) -> ObservationView:
    return ObservationView(path, check_cancelled=check_cancelled)
