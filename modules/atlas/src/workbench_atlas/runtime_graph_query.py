#!/usr/bin/env python3

"""Read-only queries over a normalized runtime graph SQLite index."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any, Iterable, Sequence


QUERY_DATABASE_APPLICATION_ID = 1398098247
QUERY_DATABASE_USER_VERSION = 2
RUNTIME_NODE_ID_KEY = "runtime-node-id"
MAX_QUERY_PAGE_SIZE = 1000
MAX_SEARCH_TEXT_CHARACTERS = 8192
MAX_NODE_IDENTITY_KEYS = 256
RUNTIME_PROJECTION_FORMAT = "susy-runtime-graph-query-projection-v1"
_PROJECTION_IDENTITY_CACHE: dict[
    tuple[int, int, int, int, int],
    dict[str, Any],
] = {}


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


class RuntimeGraphQueryError(ValueError):
    """Raised when an Atlas runtime graph database or exact query is invalid."""


@dataclass(frozen=True)
class NodeSelector:
    """An exact runtime-node selector.

    ``runtime-node-id`` is universal and addresses the graph's canonical node
    ID directly. Other key kinds are resolved through the generated
    ``node_keys`` table. Optional fields narrow an exact key when the same
    semantic identity was observed in more than one scope or node kind.
    """

    key_kind: str
    key_value: str
    kind: str | None = None
    profile: str | None = None
    physical_side: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("key kind", self.key_kind),
            ("key value", self.key_value),
        ):
            if not isinstance(value, str) or not value:
                raise RuntimeGraphQueryError(f"node selector {label} must be a non-empty string")
        for label, value in (
            ("kind", self.kind),
            ("profile", self.profile),
            ("physical side", self.physical_side),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise RuntimeGraphQueryError(
                    f"node selector {label} must be a non-empty string when provided"
                )

    @classmethod
    def by_id(
        cls,
        node_id: str,
        *,
        kind: str | None = None,
        profile: str | None = None,
        physical_side: str | None = None,
    ) -> NodeSelector:
        """Select one canonical runtime graph node ID."""

        return cls(
            RUNTIME_NODE_ID_KEY,
            node_id,
            kind=kind,
            profile=profile,
            physical_side=physical_side,
        )

    @classmethod
    def by_key(
        cls,
        key_kind: str,
        key_value: str,
        *,
        kind: str | None = None,
        profile: str | None = None,
        physical_side: str | None = None,
    ) -> NodeSelector:
        """Select nodes by one typed exact-identity key."""

        return cls(
            key_kind,
            key_value,
            kind=kind,
            profile=profile,
            physical_side=physical_side,
        )


@dataclass(frozen=True)
class PageRequest:
    """A bounded, zero-based query page request."""

    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= MAX_QUERY_PAGE_SIZE
        ):
            raise RuntimeGraphQueryError(
                f"query page limit must be an integer from 1 through {MAX_QUERY_PAGE_SIZE}"
            )
        if (
            isinstance(self.offset, bool)
            or not isinstance(self.offset, int)
            or self.offset < 0
        ):
            raise RuntimeGraphQueryError(
                "query page offset must be a non-negative integer"
            )


@dataclass(frozen=True)
class QueryPage:
    """One stable page of query items and its unbounded result count."""

    items: tuple[dict[str, Any], ...]
    total: int
    limit: int
    offset: int
    truncated: bool

    @property
    def returned(self) -> int:
        """Return the number of rows in this page."""

        return len(self.items)


def _strict_json_loads(value: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON number: {token}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = item
        return result

    return json.loads(
        value,
        parse_float=Decimal,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def decode_record(encoded: str, label: str) -> dict[str, Any]:
    """Decode one stored canonical graph record with strict JSON semantics."""

    try:
        value = _strict_json_loads(encoded)
    except (TypeError, ValueError) as exc:
        raise RuntimeGraphQueryError(
            f"query database contains invalid {label} JSON: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeGraphQueryError(
            f"query database contains non-object {label} JSON"
        )
    return value


def _bounded_query_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeGraphQueryError(f"{label} must be a non-empty string")
    if len(value) > MAX_SEARCH_TEXT_CHARACTERS:
        raise RuntimeGraphQueryError(
            f"{label} exceeds {MAX_SEARCH_TEXT_CHARACTERS} characters"
        )
    if any(character in value for character in "\r\n\x00"):
        raise RuntimeGraphQueryError(f"{label} must be single-line text")
    return value


def _like_literal(value: str) -> str:
    """Escape one literal for SQLite LIKE ... ESCAPE '\\'."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class RuntimeGraphReader:
    """A validated, context-managed, read-only runtime graph connection."""

    def __init__(self, database: Path | str):
        self.database = Path(database)
        self._resolved_database: Path | None = None
        self._database_descriptor = -1
        self._database_file_identity: (
            tuple[int, int, int, int, int] | None
        ) = None
        self._projection_identity: dict[str, Any] | None = None
        self._connection: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        try:
            metadata = self.database.lstat()
        except OSError as exc:
            raise RuntimeGraphQueryError(
                f"cannot inspect runtime graph query database: {self.database}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeGraphQueryError(
                "runtime graph query database is not a regular unlinked file: "
                f"{self.database}"
            )
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        descriptor = -1
        connection: sqlite3.Connection | None = None
        try:
            descriptor = os.open(self.database, flags)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _file_identity(opened) != _file_identity(metadata)
            ):
                raise RuntimeGraphQueryError(
                    "runtime graph query database changed while it was opened"
                )
            resolved = self.database.resolve(strict=True)
            visible = resolved.stat()
            if _file_identity(visible) != _file_identity(opened):
                raise RuntimeGraphQueryError(
                    "runtime graph query database path changed while it was "
                    "opened"
                )
            for suffix in ("-journal", "-wal"):
                transient = Path(str(resolved) + suffix)
                try:
                    transient_metadata = transient.stat()
                except FileNotFoundError:
                    continue
                if transient_metadata.st_size:
                    raise RuntimeGraphQueryError(
                        "runtime graph projection has mutable SQLite "
                        f"transient state: {transient}"
                    )
            connection = sqlite3.connect(
                (
                    f"file:/proc/self/fd/{descriptor}"
                    "?mode=ro&immutable=1"
                ),
                uri=True,
            )
            connection.execute("PRAGMA query_only = ON")
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if (
                application_id != QUERY_DATABASE_APPLICATION_ID
                or user_version != QUERY_DATABASE_USER_VERSION
            ):
                raise RuntimeGraphQueryError(
                    "database is not a supported normalized runtime graph query index"
                )
            after = os.fstat(descriptor)
            visible = resolved.stat()
            if (
                _file_identity(after) != _file_identity(opened)
                or _file_identity(visible) != _file_identity(opened)
            ):
                raise RuntimeGraphQueryError(
                    "runtime graph query database changed while its SQLite "
                    "reader was opened"
                )
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            if descriptor >= 0:
                os.close(descriptor)
            raise RuntimeGraphQueryError(
                f"cannot query runtime graph database: {exc}"
            ) from exc
        except RuntimeGraphQueryError:
            if connection is not None:
                connection.close()
            if descriptor >= 0:
                os.close(descriptor)
            raise
        self._resolved_database = resolved
        self._database_descriptor = descriptor
        self._database_file_identity = _file_identity(opened)
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open query-only connection for composed readers."""

        if self._connection is None:
            raise RuntimeGraphQueryError("runtime graph reader is closed")
        return self._connection

    def __enter__(self) -> RuntimeGraphReader:
        self.connection
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close this reader. Repeated closes are harmless."""

        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        descriptor = self._database_descriptor
        self._database_descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)

    def projection_identity(self) -> dict[str, Any]:
        """Return a content identity for the exact immutable query projection."""

        self.connection
        resolved = self._resolved_database
        descriptor = self._database_descriptor
        expected_identity = self._database_file_identity
        if (
            resolved is None
            or descriptor < 0
            or expected_identity is None
        ):
            raise RuntimeGraphQueryError(
                "runtime graph projection file binding is unavailable"
            )
        try:
            before = os.fstat(descriptor)
            visible = resolved.stat()
        except OSError as exc:
            raise RuntimeGraphQueryError(
                "cannot revalidate runtime graph projection identity: "
                f"{resolved}: {exc}"
            ) from exc
        if (
            _file_identity(before) != expected_identity
            or _file_identity(visible) != expected_identity
        ):
            raise RuntimeGraphQueryError(
                "runtime graph projection path no longer names the open "
                "SQLite file"
            )
        if self._projection_identity is not None:
            return dict(self._projection_identity)
        cached = _PROJECTION_IDENTITY_CACHE.get(expected_identity)
        if cached is None:
            digest = hashlib.sha256()
            offset = 0
            while offset < before.st_size:
                block = os.pread(
                    descriptor,
                    min(8 * 1024 * 1024, before.st_size - offset),
                    offset,
                )
                if not block:
                    raise RuntimeGraphQueryError(
                        "runtime graph projection ended before its bound size"
                    )
                digest.update(block)
                offset += len(block)
            after = os.fstat(descriptor)
            visible = resolved.stat()
            if (
                _file_identity(after) != expected_identity
                or _file_identity(visible) != expected_identity
            ):
                raise RuntimeGraphQueryError(
                    "runtime graph projection changed while its identity "
                    "was computed"
                )
            cached = {
                "format": RUNTIME_PROJECTION_FORMAT,
                "bytes": before.st_size,
                "sha256": digest.hexdigest(),
                "sqlite_application_id": (
                    QUERY_DATABASE_APPLICATION_ID
                ),
                "sqlite_user_version": QUERY_DATABASE_USER_VERSION,
            }
            _PROJECTION_IDENTITY_CACHE[expected_identity] = cached
        self._projection_identity = dict(cached)
        return dict(cached)

    def _execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Cursor:
        try:
            return self.connection.execute(statement, parameters)
        except sqlite3.Error as exc:
            raise RuntimeGraphQueryError(
                f"cannot query runtime graph database: {exc}"
            ) from exc

    def resolve_exact(self, selector: NodeSelector) -> tuple[dict[str, Any], ...]:
        """Return every deterministically ordered node matching an exact key."""

        if not isinstance(selector, NodeSelector):
            raise RuntimeGraphQueryError("exact node query requires a NodeSelector")

        parameters: list[object]
        filters: list[str]
        if selector.key_kind == RUNTIME_NODE_ID_KEY:
            statement = "SELECT node.json FROM nodes AS node"
            filters = ["node.id = ?"]
            parameters = [selector.key_value]
        else:
            statement = (
                "SELECT node.json FROM node_keys AS node_key "
                "JOIN nodes AS node ON node.id = node_key.node_id"
            )
            filters = [
                "node_key.key_kind = ?",
                "node_key.key_value = ?",
            ]
            parameters = [selector.key_kind, selector.key_value]
        if selector.kind is not None:
            filters.append("node.kind = ?")
            parameters.append(selector.kind)
        if selector.profile is not None:
            filters.append("node.profile = ?")
            parameters.append(selector.profile)
        if selector.physical_side is not None:
            filters.append("node.physical_side = ?")
            parameters.append(selector.physical_side)
        rows = self._execute(
            statement
            + " WHERE "
            + " AND ".join(filters)
            + " ORDER BY node.profile, node.physical_side, node.kind, node.id",
            parameters,
        ).fetchall()
        return tuple(decode_record(encoded, "node") for (encoded,) in rows)

    def resolve_one(
        self,
        selector: NodeSelector,
        *,
        label: str = "node",
    ) -> dict[str, Any]:
        """Resolve exactly one candidate or report absence/ambiguity."""

        matches = self.resolve_exact(selector)
        rendered = f"{selector.key_kind}={selector.key_value}"
        if not matches:
            raise RuntimeGraphQueryError(
                f"{label} not found for exact selector: {rendered}"
            )
        if len(matches) != 1:
            raise RuntimeGraphQueryError(
                f"exact {label} selector is ambiguous: {rendered} "
                f"matched {len(matches)} nodes"
            )
        return matches[0]

    def search_nodes(
        self,
        text: str,
        *,
        kinds: Iterable[str] = (),
        profile: str | None = None,
        physical_side: str | None = None,
        page: PageRequest = PageRequest(),
    ) -> QueryPage:
        """Search canonical IDs, typed keys, and exact node projections.

        The returned score is presentation-only.  Each item retains the
        complete Atlas node, the exact typed keys that matched, and the match
        basis.  Nothing in this method upgrades static or partial evidence.
        """

        query = _bounded_query_text(text, "runtime graph search text")
        if not isinstance(page, PageRequest):
            raise RuntimeGraphQueryError(
                "runtime graph search requires a PageRequest"
            )
        if isinstance(kinds, (str, bytes)):
            raise RuntimeGraphQueryError(
                "runtime graph kind filter must be an iterable of names"
            )
        try:
            selected_kinds = tuple(kinds)
        except TypeError as exc:
            raise RuntimeGraphQueryError(
                "runtime graph kind filter must be an iterable of names"
            ) from exc
        if any(not isinstance(kind, str) or not kind for kind in selected_kinds):
            raise RuntimeGraphQueryError(
                "runtime graph kind filters must be non-empty strings"
            )
        if len(set(selected_kinds)) != len(selected_kinds):
            raise RuntimeGraphQueryError(
                "runtime graph kind filter contains duplicates"
            )
        for label, value in (
            ("profile", profile),
            ("physical side", physical_side),
        ):
            if value is not None:
                _bounded_query_text(value, f"runtime graph search {label}")

        folded = query.lower()
        escaped = _like_literal(folded)
        prefix = escaped + "%"
        contains = "%" + escaped + "%"
        rank_parameters: list[object] = [
            query,
            query,
            folded,
            folded,
            prefix,
            prefix,
            contains,
            contains,
        ]
        match_parameters: list[object] = [
            query,
            query,
            folded,
            folded,
            prefix,
            prefix,
            contains,
            contains,
        ]
        filters = [
            "(node.id = ? OR node_key.key_value = ? "
            "OR lower(node.id) = ? OR lower(node_key.key_value) = ? "
            "OR lower(node_key.key_value) LIKE ? ESCAPE '\\' "
            "OR lower(node.id) LIKE ? ESCAPE '\\' "
            "OR lower(node_key.key_value) LIKE ? ESCAPE '\\' "
            "OR lower(node.json) LIKE ? ESCAPE '\\')"
        ]
        if selected_kinds:
            filters.append(
                "node.kind IN (" + ",".join("?" for _ in selected_kinds) + ")"
            )
            match_parameters.extend(selected_kinds)
        if profile is not None:
            filters.append("node.profile = ?")
            match_parameters.append(profile)
        if physical_side is not None:
            filters.append("node.physical_side = ?")
            match_parameters.append(physical_side)
        ranked = (
            "WITH ranked AS ("
            "SELECT node.id AS node_id, min(CASE "
            "WHEN node.id = ? THEN 0 "
            "WHEN node_key.key_value = ? THEN 1 "
            "WHEN lower(node.id) = ? THEN 2 "
            "WHEN lower(node_key.key_value) = ? THEN 3 "
            "WHEN lower(node_key.key_value) LIKE ? ESCAPE '\\' THEN 4 "
            "WHEN lower(node.id) LIKE ? ESCAPE '\\' THEN 5 "
            "WHEN lower(node_key.key_value) LIKE ? ESCAPE '\\' THEN 6 "
            "WHEN lower(node.json) LIKE ? ESCAPE '\\' THEN 7 ELSE 8 END) AS rank "
            "FROM nodes AS node LEFT JOIN node_keys AS node_key "
            "ON node_key.node_id = node.id WHERE "
            + " AND ".join(filters)
            + " GROUP BY node.id) "
        )
        parameters = (*rank_parameters, *match_parameters)
        total = int(
            self._execute(ranked + "SELECT count(*) FROM ranked", parameters)
            .fetchone()[0]
        )
        rows = self._execute(
            ranked
            + "SELECT ranked.rank, node.json FROM ranked "
            "JOIN nodes AS node ON node.id = ranked.node_id "
            "ORDER BY ranked.rank, node.profile, node.physical_side, "
            "node.kind, node.id LIMIT ? OFFSET ?",
            (*parameters, page.limit, page.offset),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for rank, encoded_node in rows:
            node = decode_record(encoded_node, "node")
            keys_page = self.node_identity_keys(
                str(node["id"]),
                page=PageRequest(limit=MAX_NODE_IDENTITY_KEYS),
            )
            matched_keys = []
            for key in keys_page.items:
                value = str(key["key_value"])
                value_folded = value.lower()
                if (
                    value == query
                    or value_folded == folded
                    or value_folded.startswith(folded)
                    or folded in value_folded
                ):
                    matched_keys.append(key)
            reasons: list[str] = []
            node_id = str(node["id"])
            if node_id == query:
                reasons.append("runtime-node-id-exact")
            elif node_id.lower() == folded:
                reasons.append("runtime-node-id-case-insensitive")
            elif node_id.lower().startswith(folded):
                reasons.append("runtime-node-id-prefix")
            elif folded in node_id.lower():
                reasons.append("runtime-node-id-substring")
            if matched_keys:
                reasons.append("typed-identity-key")
            if not reasons:
                reasons.append("runtime-projection-text")
            items.append(
                {
                    "node": node,
                    "rank": int(rank),
                    "match_reasons": reasons,
                    "matched_keys": matched_keys,
                    "identity_keys_truncated": keys_page.truncated,
                }
            )
        return QueryPage(
            items=tuple(items),
            total=total,
            limit=page.limit,
            offset=page.offset,
            truncated=page.offset + len(items) < total,
        )

    def node_identity_keys(
        self,
        node_id: str,
        *,
        page: PageRequest = PageRequest(limit=MAX_NODE_IDENTITY_KEYS),
    ) -> QueryPage:
        """Return one bounded page of typed identities for an exact node."""

        identifier = _bounded_query_text(node_id, "runtime node ID")
        if not isinstance(page, PageRequest):
            raise RuntimeGraphQueryError(
                "runtime node identity query requires a PageRequest"
            )
        total = int(
            self._execute(
                "SELECT count(*) FROM node_keys WHERE node_id = ?",
                (identifier,),
            ).fetchone()[0]
        )
        rows = self._execute(
            "SELECT key_kind, key_value FROM node_keys WHERE node_id = ? "
            "ORDER BY key_kind, key_value LIMIT ? OFFSET ?",
            (identifier, page.limit, page.offset),
        ).fetchall()
        items = tuple(
            {"key_kind": str(key_kind), "key_value": str(key_value)}
            for key_kind, key_value in rows
        )
        return QueryPage(
            items=items,
            total=total,
            limit=page.limit,
            offset=page.offset,
            truncated=page.offset + len(items) < total,
        )

    def related_nodes(
        self,
        node_id: str,
        *,
        predicates: Iterable[str] = (),
        page: PageRequest = PageRequest(),
    ) -> QueryPage:
        """Return bounded incoming and outgoing relationships for one node."""

        identifier = _bounded_query_text(node_id, "runtime node ID")
        if not isinstance(page, PageRequest):
            raise RuntimeGraphQueryError(
                "runtime relationship query requires a PageRequest"
            )
        if isinstance(predicates, (str, bytes)):
            raise RuntimeGraphQueryError(
                "runtime relationship predicate filter must be an iterable"
            )
        try:
            selected_predicates = tuple(predicates)
        except TypeError as exc:
            raise RuntimeGraphQueryError(
                "runtime relationship predicate filter must be an iterable"
            ) from exc
        if any(
            not isinstance(predicate, str) or not predicate
            for predicate in selected_predicates
        ):
            raise RuntimeGraphQueryError(
                "runtime relationship predicates must be non-empty strings"
            )
        if len(set(selected_predicates)) != len(selected_predicates):
            raise RuntimeGraphQueryError(
                "runtime relationship predicate filter contains duplicates"
            )
        predicate_clause = ""
        if selected_predicates:
            predicate_clause = " AND predicate IN (" + ",".join(
                "?" for _ in selected_predicates
            ) + ")"
        total = int(
            self._execute(
                "SELECT count(*) FROM edges WHERE (subject = ? OR object = ?)"
                + predicate_clause,
                (identifier, identifier, *selected_predicates),
            ).fetchone()[0]
        )
        joined_predicate_clause = ""
        if selected_predicates:
            joined_predicate_clause = " AND edge.predicate IN (" + ",".join(
                "?" for _ in selected_predicates
            ) + ")"
        rows = self._execute(
            "SELECT edge.json, other.json, "
            "CASE WHEN edge.subject = ? THEN 'outgoing' ELSE 'incoming' END "
            "FROM edges AS edge JOIN nodes AS other ON other.id = "
            "CASE WHEN edge.subject = ? THEN edge.object ELSE edge.subject END "
            "WHERE (edge.subject = ? OR edge.object = ?)"
            + joined_predicate_clause
            + " "
            "ORDER BY CASE WHEN edge.subject = ? THEN 0 ELSE 1 END, "
            "edge.predicate, other.kind, other.id, edge.id LIMIT ? OFFSET ?",
            (
                identifier,
                identifier,
                identifier,
                identifier,
                *selected_predicates,
                identifier,
                page.limit,
                page.offset,
            ),
        ).fetchall()
        items = tuple(
            {
                "direction": str(direction),
                "relationship": decode_record(encoded_edge, "edge"),
                "node": decode_record(encoded_node, "node"),
            }
            for encoded_edge, encoded_node, direction in rows
        )
        return QueryPage(
            items=items,
            total=total,
            limit=page.limit,
            offset=page.offset,
            truncated=page.offset + len(items) < total,
        )

    def recipe_slots(
        self,
        recipe_id: str,
        predicates: Iterable[str],
    ) -> tuple[dict[str, Any], ...]:
        """Return ordered ingredient slots without expanding their alternatives."""

        predicate_values = tuple(predicates)
        if not predicate_values:
            return ()
        placeholders = ",".join("?" for _ in predicate_values)
        rows = self._execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.object "
            f"WHERE edge.subject = ? AND edge.predicate IN ({placeholders}) "
            "AND node.kind = 'ingredient_slot'",
            (recipe_id, *predicate_values),
        ).fetchall()
        slots: list[dict[str, Any]] = []
        for encoded_relationship, encoded_slot in rows:
            relationship = decode_record(encoded_relationship, "edge")
            slot = decode_record(encoded_slot, "node")
            alternatives = tuple(
                {
                    "relationship": decode_record(encoded_edge, "edge"),
                    "node": decode_record(encoded_node, "node"),
                }
                for encoded_edge, encoded_node in self._execute(
                    "SELECT edge.json, node.json "
                    "FROM edges AS edge "
                    "JOIN nodes AS node ON node.id = edge.object "
                    "WHERE edge.subject = ? "
                    "AND edge.predicate = 'accepts_alternative' "
                    "ORDER BY edge.object, edge.id",
                    (slot["id"],),
                )
            )
            slots.append(
                {
                    "relationship": relationship,
                    "slot": slot,
                    "alternatives": alternatives,
                }
            )

        def slot_order(row: dict[str, Any]) -> tuple[int, str, str]:
            attributes = row["relationship"].get("attributes")
            ordinal = attributes.get("ordinal") if isinstance(attributes, dict) else None
            return (
                (
                    ordinal
                    if isinstance(ordinal, int) and not isinstance(ordinal, bool)
                    else sys.maxsize
                ),
                str(row["relationship"].get("predicate", "")),
                str(row["slot"].get("id", "")),
            )

        slots.sort(key=slot_order)
        return tuple(slots)

    def _machine(self, selector: NodeSelector) -> dict[str, Any]:
        matches = self.resolve_exact(selector)
        if not matches:
            if selector.key_kind == RUNTIME_NODE_ID_KEY:
                raise RuntimeGraphQueryError(
                    f"machine not found in runtime graph: {selector.key_value}"
                )
            raise RuntimeGraphQueryError(
                "machine not found for exact selector: "
                f"{selector.key_kind}={selector.key_value}"
            )
        if len(matches) != 1:
            raise RuntimeGraphQueryError(
                "exact machine selector is ambiguous: "
                f"{selector.key_kind}={selector.key_value} "
                f"matched {len(matches)} nodes"
            )
        machine = matches[0]
        if machine.get("kind") != "machine":
            if selector.key_kind == RUNTIME_NODE_ID_KEY:
                raise RuntimeGraphQueryError(
                    "runtime graph identifier is not a machine: "
                    f"{selector.key_value}"
                )
            raise RuntimeGraphQueryError(
                "runtime graph exact selector does not identify a machine: "
                f"{selector.key_kind}={selector.key_value}"
            )
        return machine

    def machine_recipes(
        self,
        machine: NodeSelector,
        page: PageRequest = PageRequest(),
    ) -> QueryPage:
        """Return one page of lookup-active recipes associated with a machine.

        Each item is one recipe row with its recipe-map relationship, recipe
        relationship, and grouped input/output slots. This flat page shape
        makes ``len(items)`` match the pagination count. The legacy CLI adapter
        regroups adjacent rows by recipe map without changing its execution/
        consultation behavior or JSON contract.
        """

        if not isinstance(page, PageRequest):
            raise RuntimeGraphQueryError(
                "machine recipe query requires a PageRequest"
            )
        machine_record = self._machine(machine)
        machine_id = str(machine_record["id"])
        join = (
            " FROM edges AS map_edge "
            "JOIN nodes AS recipe_map ON recipe_map.id = map_edge.object "
            "JOIN edges AS recipe_edge ON recipe_edge.subject = recipe_map.id "
            "JOIN nodes AS recipe ON recipe.id = recipe_edge.object "
            "WHERE map_edge.subject = ? "
            "AND map_edge.predicate IN ('executes_recipe_map', 'consults_recipe_map') "
            "AND recipe_map.kind = 'recipe_map' "
            "AND recipe_edge.predicate = 'has_recipe' "
            "AND json_extract(recipe_edge.json, '$.attributes.lookup_active') = 1 "
            "AND recipe.kind = 'recipe'"
        )
        total = int(
            self._execute("SELECT count(*)" + join, (machine_id,)).fetchone()[0]
        )
        rows = self._execute(
            "SELECT map_edge.json, recipe_map.json, recipe_edge.json, recipe.json"
            + join
            + " ORDER BY CASE map_edge.predicate "
            "WHEN 'executes_recipe_map' THEN 0 ELSE 1 END, "
            "recipe_map.id, recipe.id, recipe_edge.id LIMIT ? OFFSET ?",
            (machine_id, page.limit, page.offset),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for (
            encoded_map_relationship,
            encoded_recipe_map,
            encoded_recipe_relationship,
            encoded_recipe,
        ) in rows:
            recipe = decode_record(encoded_recipe, "node")
            items.append(
                {
                    "recipe_map_relationship": decode_record(
                        encoded_map_relationship,
                        "edge",
                    ),
                    "recipe_map": decode_record(encoded_recipe_map, "node"),
                    "recipe_relationship": decode_record(
                        encoded_recipe_relationship,
                        "edge",
                    ),
                    "recipe": recipe,
                    "inputs": self.recipe_slots(
                        str(recipe["id"]),
                        ("consumes", "may_consume", "requires"),
                    ),
                    "outputs": self.recipe_slots(
                        str(recipe["id"]),
                        ("produces", "may_produce"),
                    ),
                }
            )
        result_items = tuple(items)
        return QueryPage(
            items=result_items,
            total=total,
            limit=page.limit,
            offset=page.offset,
            truncated=page.offset + len(result_items) < total,
        )


def legacy_machine_recipes_payload(
    machine: dict[str, Any],
    page: QueryPage,
) -> dict[str, Any]:
    """Regroup a reader page into the original ``machine-recipes`` payload."""

    def legacy_slots(
        slots: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "relationship": slot["relationship"],
                "slot": slot["slot"],
                "alternatives": list(slot["alternatives"]),
            }
            for slot in slots
        ]

    grouped_maps: dict[tuple[str, str], dict[str, Any]] = {}
    for item in page.items:
        map_relationship = item["recipe_map_relationship"]
        recipe_map = item["recipe_map"]
        key = (str(map_relationship["id"]), str(recipe_map["id"]))
        map_result = grouped_maps.setdefault(
            key,
            {
                "relationship": map_relationship,
                "recipe_map": recipe_map,
                "recipes": [],
            },
        )
        map_result["recipes"].append(
            {
                "relationship": item["recipe_relationship"],
                "recipe": item["recipe"],
                "inputs": legacy_slots(item["inputs"]),
                "outputs": legacy_slots(item["outputs"]),
            }
        )
    return {
        "machine": machine,
        "offset": page.offset,
        "limit": page.limit,
        "total_recipes": page.total,
        "returned_recipes": page.returned,
        "truncated": page.truncated,
        "recipe_maps": list(grouped_maps.values()),
    }
