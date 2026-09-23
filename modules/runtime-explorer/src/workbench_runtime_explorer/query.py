"""Omnibox parsing, high-signal ranking, and cross-authority composition."""

from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
import shlex
from typing import Any, Iterable, Mapping

from .model import (
    ExplorerError,
    ExplorerRecord,
    ExplorerSource,
    RESULT_FORMAT,
    RESULT_SCHEMA_VERSION,
    canonical_bytes,
    content_id,
    unique_dicts,
    validate_result,
)


MAX_QUERY_CHARACTERS = 8192
MAX_RESULTS = 100
MAX_MANUAL_LINKS = 3

_FILTERS = frozenset({"kind", "owner", "state", "profile", "side", "source"})
_TYPED_PREFIXES: dict[str, str] = {
    "id": "any-id",
    "mod": "mod-id",
    "class": "class-name",
    "member": "source-member",
    "method": "method-name",
    "field": "field-name",
    "mixin": "mixin-class",
    "transformer": "transformer-class",
    "registry": "registry-name",
    "resource": "resource-location",
    "recipe": "recipe-id",
    "block": "block-id",
    "blockstate": "blockstate-id",
    "item": "item-id",
    "stack": "item-stack",
    "itemstack": "item-stack",
    "metadata": "metadata-value",
    "loot": "loot-table-id",
    "loottable": "loot-table-id",
    "biome": "biome-id",
    "structure": "structure-id",
    "generator": "generator-id",
    "config": "config-key",
    "groovy": "groovy-key",
    "fluid": "fluid-id",
    "material": "material-id",
    "machine": "machine-id",
    "ore": "ore-prefix",
    "dimension": "dimension-id",
    "world": "world-type-id",
    "event": "event-name",
    "capability": "capability-name",
    "profiler": "profiler-event",
    "coordinate": "coordinate",
}
_MINECRAFT_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_STACK_FRAME_RE = re.compile(
    r"^(?:at\s+)?"
    r"(?P<class>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)"
    r"\.(?P<method>[A-Za-z_$][A-Za-z0-9_$<>]*)"
    r"\((?P<file>[^():\r\n]+)(?::(?P<line>[1-9][0-9]*))?\)$"
)
_MEMBER_RE = re.compile(
    r"^(?P<class>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)"
    r"[#.](?P<member>[A-Za-z_$][A-Za-z0-9_$<>]*)$"
)
_CLASS_NAME_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$"
)
_SIMPLE_CLASS_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_SOURCE_LOCATOR_RE = re.compile(r"^(?P<path>[^\r\n:]+\.[A-Za-z0-9]+):(?P<line>[1-9][0-9]*)$")
_COORDINATE_RE = re.compile(
    r"^(?:(?P<dimension>[A-Za-z0-9_.:-]+)@)?"
    r"(?P<x>-?[0-9]+)[, ]+(?:(?P<y>-?[0-9]+)[, ]+)?(?P<z>-?[0-9]+)$"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_$:./#<>-]+")
_STATE_BONUS = {
    "observed": 45,
    "declared": 25,
    "verbatim-evidence": 12,
    "static-possible": 5,
    "presentation-observation": 0,
    "teaching": -20,
    "unresolved": -25,
}
_STRONG_JOIN_KINDS = frozenset(
    {
        "artifact-id",
        "artifact-sha256",
        "biome-id",
        "block-id",
        "blockstate-id",
        "capability-name",
        "class-name",
        "config-path-key",
        "coordinate",
        "dependency-coordinate",
        "dimension-id",
        "event-id",
        "event-name",
        "file-path",
        "fluid-id",
        "generator-id",
        "item-id",
        "item-stack",
        "loot-table-id",
        "manual-id",
        "machine-id",
        "material-id",
        "mixin-class",
        "mod-id",
        "ore-prefix",
        "recipe-id",
        "registration-id",
        "registry-name",
        "resource-location",
        "runtime-node-id",
        "source-member",
        "structure-id",
        "target-class",
        "transformer-class",
        "world-type-id",
    }
)


@dataclass(frozen=True, slots=True)
class InterpretedIdentity:
    kind: str
    value: str
    basis: str

    def public(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "basis": self.basis}


@dataclass(frozen=True, slots=True)
class ExplorerRequest:
    raw: str
    text: str
    terms: tuple[str, ...]
    filters: Mapping[str, tuple[str, ...]]
    interpreted: tuple[InterpretedIdentity, ...]
    limit: int = 20

    def with_filters(
        self,
        *,
        kinds: Iterable[str] = (),
        owners: Iterable[str] = (),
        states: Iterable[str] = (),
        profiles: Iterable[str] = (),
        sides: Iterable[str] = (),
        sources: Iterable[str] = (),
        limit: int | None = None,
    ) -> "ExplorerRequest":
        additions = {
            "kind": tuple(kinds),
            "owner": tuple(owners),
            "state": tuple(states),
            "profile": tuple(profiles),
            "side": tuple(sides),
            "source": tuple(sources),
        }
        filters = {key: list(values) for key, values in self.filters.items()}
        for key, values in additions.items():
            for value in values:
                _bounded(value, f"{key} filter")
                filters.setdefault(key, []).append(value)
        normalized = {
            key: tuple(dict.fromkeys(values))
            for key, values in filters.items()
            if values
        }
        chosen_limit = self.limit if limit is None else limit
        if isinstance(chosen_limit, bool) or not isinstance(chosen_limit, int) or not 1 <= chosen_limit <= MAX_RESULTS:
            raise ExplorerError(f"result limit must be an integer from 1 through {MAX_RESULTS}")
        return replace(self, filters=normalized, limit=chosen_limit)


def _bounded(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_QUERY_CHARACTERS
        or any(character in value for character in "\r\n\x00")
    ):
        raise ExplorerError(f"{label} must be bounded single-line text")
    return value


def _interpreted_from_text(text: str) -> list[InterpretedIdentity]:
    result: list[InterpretedIdentity] = []
    if _MINECRAFT_ID_RE.fullmatch(text):
        result.extend(
            (
                InterpretedIdentity("registry-name", text, "namespaced Minecraft identity"),
                InterpretedIdentity("resource-location", text, "namespaced Minecraft identity"),
            )
        )
    if stack := _STACK_FRAME_RE.fullmatch(text):
        result.extend(
            (
                InterpretedIdentity("class-name", stack.group("class"), "Java stack frame"),
                InterpretedIdentity("method-name", stack.group("method"), "Java stack frame"),
                InterpretedIdentity("source-member", f"{stack.group('class')}#{stack.group('method')}", "Java stack frame"),
                InterpretedIdentity("file-path", stack.group("file"), "Java stack frame"),
            )
        )
    elif member := _MEMBER_RE.fullmatch(text):
        result.extend(
            (
                InterpretedIdentity("class-name", member.group("class"), "qualified JVM member"),
                InterpretedIdentity("method-name", member.group("member"), "qualified JVM member"),
                InterpretedIdentity("source-member", f"{member.group('class')}#{member.group('member')}", "qualified JVM member"),
            )
        )
    elif _CLASS_NAME_RE.fullmatch(text) and any(
        character.isupper() or character == "$"
        for character in text.rsplit(".", 1)[-1]
    ):
        result.append(
            InterpretedIdentity("class-name", text, "qualified JVM class")
        )
    elif _SIMPLE_CLASS_RE.fullmatch(text) and any(
        character.isupper() or character == "$" for character in text
    ):
        result.append(
            InterpretedIdentity("class-simple-name", text, "simple JVM class")
        )
    if source := _SOURCE_LOCATOR_RE.fullmatch(text):
        result.append(InterpretedIdentity("file-path", source.group("path"), "source locator"))
    if coordinate := _COORDINATE_RE.fullmatch(text):
        value = ",".join(
            part
            for part in (coordinate.group("x"), coordinate.group("y"), coordinate.group("z"))
            if part is not None
        )
        result.append(InterpretedIdentity("coordinate", value, "Minecraft coordinate"))
        if coordinate.group("dimension"):
            result.append(InterpretedIdentity("dimension-id", coordinate.group("dimension"), "Minecraft coordinate"))
    unique = {(row.kind, row.value, row.basis): row for row in result}
    return [unique[key] for key in sorted(unique)]


def parse_query(raw: str, *, limit: int = 20) -> ExplorerRequest:
    value = _bounded(raw.strip(), "explorer query")
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ExplorerError(f"explorer query quoting is invalid: {exc}") from exc
    filters: dict[str, list[str]] = {}
    text_tokens: list[str] = []
    interpreted: list[InterpretedIdentity] = []
    for token in tokens:
        prefix, separator, remainder = token.partition(":")
        folded = prefix.casefold()
        if separator and folded in _FILTERS:
            if not remainder:
                raise ExplorerError(f"{folded} filter requires a value")
            filters.setdefault(folded, []).extend(
                part for part in remainder.split(",") if part
            )
            continue
        if separator and folded in _TYPED_PREFIXES:
            if not remainder:
                raise ExplorerError(f"{folded} identity requires a value")
            identity_kind = _TYPED_PREFIXES[folded]
            if folded == "class" and "." not in remainder:
                identity_kind = "class-simple-name"
            interpreted.append(
                InterpretedIdentity(identity_kind, remainder, f"explicit {folded}: query")
            )
            text_tokens.append(remainder)
            continue
        text_tokens.append(token)
    text = " ".join(text_tokens).strip()
    if not text and not filters:
        raise ExplorerError("explorer query must contain text or a filter")
    if text:
        interpreted.extend(_interpreted_from_text(text))
    unique_interpreted = {
        (row.kind, row.value, row.basis): row for row in interpreted
    }
    request = ExplorerRequest(
        raw=value,
        text=text,
        terms=tuple(token.casefold() for token in _TOKEN_RE.findall(text) if token),
        filters={
            key: tuple(dict.fromkeys(values))
            for key, values in filters.items()
        },
        interpreted=tuple(unique_interpreted[key] for key in sorted(unique_interpreted)),
        limit=20,
    )
    return request.with_filters(limit=limit)


def _owner_values(record: ExplorerRecord) -> tuple[str, ...]:
    values: list[str] = []
    actors = record.owner.get("actors")
    if isinstance(actors, list):
        for actor in actors:
            if not isinstance(actor, Mapping):
                continue
            for key in ("id", "name", "runtime_node_id", "sha256"):
                value = actor.get(key)
                if isinstance(value, str) and value:
                    values.append(value)
    return tuple(values)


def _mod_owner_ids(owners: Iterable[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    for owner in owners:
        actors = owner.get("actors")
        if not isinstance(actors, list):
            continue
        for actor in actors:
            if not isinstance(actor, Mapping):
                continue
            kind = str(actor.get("kind", "")).casefold()
            if kind not in {"mod", "mod-id", "mod-id-candidate", "mod-candidate"}:
                continue
            value = actor.get("normalized_id") or actor.get("id")
            if isinstance(value, str) and value:
                result.add(value.casefold())
    return result


def _scope_values(record: ExplorerRecord, key: str) -> tuple[str, ...]:
    aliases = {
        "profile": ("profile", "profile_id", "pack_profile_id"),
        "side": ("physical_side", "side"),
    }[key]
    values: list[str] = []
    for alias in aliases:
        value = record.scope.get(alias)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _matches_filter(record: ExplorerRecord, sources: Mapping[str, ExplorerSource], request: ExplorerRequest) -> bool:
    filters = request.filters
    if "kind" in filters and not any(
        requested.casefold() == record.kind.casefold()
        or requested.casefold() in record.kind.casefold().split("-")
        for requested in filters["kind"]
    ):
        return False
    if "state" in filters and not any(
        requested.casefold() == record.state.casefold()
        for requested in filters["state"]
    ):
        return False
    if "owner" in filters:
        owner_values = tuple(value.casefold() for value in _owner_values(record))
        if not any(
            requested.casefold() == value or requested.casefold() in value
            for requested in filters["owner"]
            for value in owner_values
        ):
            return False
    for filter_name in ("profile", "side"):
        if filter_name in filters:
            scope_values = tuple(value.casefold() for value in _scope_values(record, filter_name))
            if not any(requested.casefold() == value for requested in filters[filter_name] for value in scope_values):
                return False
    if "source" in filters:
        source = sources.get(record.source_id)
        values = [record.source_id, record.authority]
        if source is not None:
            values.append(source.source_kind)
        folded = tuple(value.casefold() for value in values)
        if not any(requested.casefold() in value for requested in filters["source"] for value in folded):
            return False
    return True


def _trigrams(value: str) -> frozenset[str]:
    folded = "  " + value.casefold() + "  "
    return frozenset(folded[index : index + 3] for index in range(max(0, len(folded) - 2)))


def _fuzzy_score(query: str, candidate: str) -> int:
    if len(query) < 3 or len(candidate) > 8192:
        return 0
    query_folded = query.casefold()
    candidate_folded = candidate.casefold()
    if not any(separator in query_folded for separator in (".", ":", "/", "#")):
        terminal = re.split(r"[.:/#]", candidate_folded)[-1]
        if terminal:
            candidate_folded = terminal
    if query_folded[0] not in candidate_folded:
        return 0
    query_grams = _trigrams(query_folded)
    candidate_grams = _trigrams(candidate_folded)
    overlap = len(query_grams & candidate_grams)
    union = len(query_grams | candidate_grams)
    jaccard = overlap / union if union else 0.0
    if jaccard < 0.12:
        return 0
    ratio = SequenceMatcher(None, query_folded, candidate_folded, autojunk=False).ratio()
    if ratio < 0.42:
        return 0
    return int(180 + 220 * max(jaccard, ratio))


def _typed_candidate(
    expected_kind: str,
    actual_kind: str,
    value: str,
) -> str | None:
    if expected_kind == "any-id":
        return value
    jvm_kinds = {
        "class-name",
        "mixin-class",
        "target-class",
        "transformer-class",
    }
    if expected_kind == "class-simple-name" and actual_kind in jvm_kinds:
        return value.rsplit(".", 1)[-1]
    if expected_kind == actual_kind:
        return value
    if expected_kind in jvm_kinds and actual_kind in jvm_kinds:
        return value
    return None


def _score_explicit_identities(
    record: ExplorerRecord,
    explicit: tuple[InterpretedIdentity, ...],
) -> tuple[int, bool, tuple[str, ...]]:
    best = 0
    exact = False
    reasons: set[str] = set()
    for interpreted in explicit:
        expected = interpreted.value.casefold()
        for identity in record.identities:
            actual_kind = str(identity["kind"])
            candidate_value = _typed_candidate(
                interpreted.kind,
                actual_kind,
                str(identity["value"]),
            )
            if candidate_value is None:
                continue
            candidate = candidate_value.casefold()
            score = 0
            if candidate_value == interpreted.value:
                score = 1220
                exact = True
                reasons.add(f"typed-exact:{actual_kind}")
            elif candidate == expected:
                score = 1160
                exact = True
                reasons.add(f"typed-case-insensitive-exact:{actual_kind}")
            elif candidate.startswith(expected):
                score = 900
                reasons.add(f"typed-prefix:{actual_kind}")
            elif expected in candidate:
                score = 760
                reasons.add(f"typed-substring:{actual_kind}")
            else:
                score = _fuzzy_score(interpreted.value, candidate_value)
                if score:
                    reasons.add(f"typed-fuzzy:{actual_kind}")
            best = max(best, score)
    if best == 0:
        return 0, False, ()
    return (
        best + _STATE_BONUS[record.state] + record.rank_hint,
        exact,
        tuple(sorted(reasons)),
    )


def _score_record(record: ExplorerRecord, request: ExplorerRequest) -> tuple[int, bool, tuple[str, ...]]:
    if not request.text:
        return 100 + _STATE_BONUS[record.state] + record.rank_hint, False, ("filter-only",)
    explicit = tuple(
        row for row in request.interpreted if row.basis.startswith("explicit ")
    )
    if explicit:
        return _score_explicit_identities(record, explicit)
    query = request.text
    folded = query.casefold()
    best = 0
    exact = False
    reasons: set[str] = set()
    interpreted = tuple((row.kind, row.value.casefold()) for row in request.interpreted)
    for value_kind, value in record.searchable_values():
        candidate = value.casefold()
        score = 0
        if value == query:
            score = 1050 if value_kind != "name" else 1000
            exact = True
            reasons.add(f"exact:{value_kind}")
        elif candidate == folded:
            score = 980 if value_kind != "name" else 940
            exact = True
            reasons.add(f"case-insensitive-exact:{value_kind}")
        elif candidate.startswith(folded):
            score = 820
            reasons.add(f"prefix:{value_kind}")
        elif folded in candidate:
            score = 690
            reasons.add(f"substring:{value_kind}")
        elif request.terms and all(term in candidate for term in request.terms):
            score = 620
            reasons.add(f"all-terms:{value_kind}")
        elif not request.interpreted:
            score = _fuzzy_score(query, value)
            if score:
                reasons.add(f"fuzzy:{value_kind}")
        for expected_kind, expected_value in interpreted:
            if expected_value != candidate:
                continue
            if expected_kind == "any-id" or expected_kind == value_kind:
                score = max(score, 1220)
                exact = True
                reasons.add(f"interpreted-exact:{value_kind}")
            elif (
                expected_kind in {"class-name", "mixin-class", "target-class"}
                and value_kind in {"class-name", "mixin-class", "target-class"}
            ):
                score = max(score, 1160)
                exact = True
                reasons.add(f"interpreted-class:{value_kind}")
        best = max(best, score)
    if best == 0:
        return 0, False, ()
    return best + _STATE_BONUS[record.state] + record.rank_hint, exact, tuple(sorted(reasons))


def _join_key(kind: str, value: str) -> tuple[str, str] | None:
    if kind not in _STRONG_JOIN_KINDS:
        return None
    canonical_kind = (
        "jvm-class"
        if kind
        in {"class-name", "mixin-class", "target-class", "transformer-class"}
        else kind
    )
    return canonical_kind, value


def _merged_identities(records: Iterable[ExplorerRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for record in records:
        for identity in record.identities:
            key = (str(identity["kind"]), str(identity["value"]))
            basis = identity.get("basis")
            if isinstance(basis, str) and basis:
                grouped.setdefault(key, set()).add(basis)
            else:
                grouped.setdefault(key, set())
    return [
        {
            "kind": kind,
            "value": value,
            "basis": "; ".join(sorted(bases)) if bases else "unspecified",
        }
        for (kind, value), bases in sorted(grouped.items())
    ]


def _manual_links(
    manuals: tuple[ExplorerRecord, ...],
    request: ExplorerRequest,
    facets: list[ExplorerRecord],
) -> list[dict[str, Any]]:
    if not manuals:
        return []
    needles = [request.text, *(record.name for record in facets)]
    for record in facets:
        needles.extend(
            str(identity["value"])
            for identity in record.identities
            if identity.get("kind") in {"class-name", "mixin-class", "registry-name", "mod-id", "resource-location"}
        )
    needles = [needle.casefold() for needle in needles if needle and len(needle) >= 3]
    scored: list[tuple[int, ExplorerRecord]] = []
    for manual in manuals:
        haystack = "\n".join(value for _, value in manual.searchable_values()).casefold()
        score = sum(1 for needle in set(needles) if needle in haystack)
        if score:
            scored.append((score, manual))
    scored.sort(key=lambda row: (-row[0], row[1].name.casefold(), row[1].record_id))
    return [
        {
            "record_id": manual.record_id,
            "title": manual.name,
            "score": score,
            "navigation": [dict(value) for value in manual.navigation],
            "state": "teaching",
        }
        for score, manual in scored[:MAX_MANUAL_LINKS]
    ]


class Explorer:
    """One immutable in-memory projection over explicitly supplied sources."""

    def __init__(
        self,
        sources: Iterable[ExplorerSource],
        records: Iterable[ExplorerRecord],
    ) -> None:
        source_rows = tuple(sources)
        record_rows = tuple(records)
        source_ids = [source.source_id for source in source_rows]
        if len(source_ids) != len(set(source_ids)):
            raise ExplorerError("explorer sources repeat an exact source ID")
        source_map = {source.source_id: source for source in source_rows}
        record_ids: set[str] = set()
        for record in record_rows:
            if record.source_id not in source_map:
                raise ExplorerError(f"explorer record references unknown source: {record.record_id}")
            if record.record_id in record_ids:
                raise ExplorerError(f"explorer records repeat an exact record ID: {record.record_id}")
            record_ids.add(record.record_id)
        self.sources = source_rows
        self.records = record_rows
        self._source_map = source_map

    def search(self, request: ExplorerRequest) -> dict[str, Any]:
        if not isinstance(request, ExplorerRequest):
            raise ExplorerError("explorer search requires an ExplorerRequest")
        scored: list[tuple[int, bool, tuple[str, ...], ExplorerRecord]] = []
        manuals = tuple(record for record in self.records if record.kind == "manual")
        for record in self.records:
            if not _matches_filter(record, self._source_map, request):
                continue
            score, exact, reasons = _score_record(record, request)
            if score:
                scored.append((score, exact, reasons, record))
        if request.interpreted and any(row[1] for row in scored):
            # Once a structured identity (class, namespaced ID, stack member,
            # coordinate, or explicit typed query) resolves exactly, weaker
            # substring neighbors are navigation context rather than peer
            # results.  Exact facets and their relationships retain that
            # context without polluting the primary list.
            scored = [row for row in scored if row[1]]
        scored.sort(
            key=lambda row: (
                -row[0],
                not row[1],
                row[3].name.casefold(),
                row[3].kind,
                row[3].record_id,
            )
        )

        parent = list(range(len(scored)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        first_by_key: dict[tuple[str, str], int] = {}
        for index, (_, _, _, record) in enumerate(scored):
            for identity in record.identities:
                key = _join_key(str(identity["kind"]), str(identity["value"]))
                if key is None:
                    continue
                previous = first_by_key.setdefault(key, index)
                union(previous, index)
        groups: dict[int, list[int]] = {}
        for index in range(len(scored)):
            groups.setdefault(find(index), []).append(index)

        entities: list[dict[str, Any]] = []
        for indices in groups.values():
            rows = [scored[index] for index in indices]
            rows.sort(key=lambda row: (-row[0], not row[1], row[3].record_id))
            primary = rows[0][3]
            facets = [row[3] for row in rows]
            score = rows[0][0] + min(60, sum(max(0, row[0] // 40) for row in rows[1:]))
            exact = any(row[1] for row in rows)
            reasons = sorted({reason for row in rows for reason in row[2]})
            identities = _merged_identities(facets)
            owners = unique_dicts(record.owner for record in facets)
            relationships = unique_dicts(
                relationship for record in facets for relationship in record.relationships
            )
            navigation = unique_dicts(
                navigation for record in facets for navigation in record.navigation
            )
            limitations = sorted(
                {limitation for record in facets for limitation in record.limitations}
            )
            record_ids = sorted(record.record_id for record in facets)
            entity_id = content_id(
                "workbench-runtime-explorer-entity:sha256:", record_ids
            )
            ambiguity: list[str] = []
            observed_record_ids = {
                record.record_id
                for record in facets
                if record.state == "observed"
                and record.authority == "Atlas"
                and self._source_map[record.source_id].source_kind
                == "atlas-runtime-graph"
            }
            if len(observed_record_ids) > 1:
                ambiguity.append("multiple-observed-runtime-nodes")
            artifact_ids = {
                str(record.scope["artifact_id"])
                for record in facets
                if isinstance(record.scope.get("artifact_id"), str)
            }
            if len(artifact_ids) > 1:
                ambiguity.append("multiple-static-artifacts")
            if any("ambiguous" in str(owner.get("state", "")) for owner in owners):
                ambiguity.append("owner-ambiguous")
            if len(_mod_owner_ids(owners)) > 1:
                ambiguity.append("owner-conflicted")
            versions = sorted({record.version for record in facets if record.version})
            if len(versions) > 1:
                ambiguity.append("version-ambiguous")
            observed_scopes = {
                (
                    tuple(_scope_values(record, "profile")),
                    tuple(_scope_values(record, "side")),
                )
                for record in facets
                if record.state == "observed"
                and (
                    _scope_values(record, "profile")
                    or _scope_values(record, "side")
                )
            }
            if len(observed_scopes) > 1:
                ambiguity.append("observed-scope-conflicted")
            entities.append(
                {
                    "entity_id": entity_id,
                    "score": score,
                    "exact": exact,
                    "ambiguous": bool(ambiguity),
                    "ambiguity": ambiguity,
                    "matched_by": reasons,
                    "kind": primary.kind,
                    "name": primary.name,
                    "states": sorted({record.state for record in facets}),
                    "identities": identities,
                    "owners": list(owners),
                    "versions": versions,
                    "scopes": [dict(value) for value in unique_dicts(record.scope for record in facets)],
                    "facets": [record.public() for record in facets],
                    "relationships": list(relationships),
                    "manuals": _manual_links(manuals, request, facets),
                    "navigation": list(navigation),
                    "limitations": limitations,
                }
            )
        entities.sort(
            key=lambda row: (
                -int(row["score"]),
                not bool(row["exact"]),
                str(row["name"]).casefold(),
                str(row["entity_id"]),
            )
        )
        total = len(entities)
        returned = entities[: request.limit]
        exact_count = sum(1 for entity in entities if entity["exact"])
        ambiguous_count = sum(1 for entity in entities if entity["ambiguous"])
        atlas_sources = [source for source in self.sources if source.authority == "Atlas" and source.source_kind == "atlas-runtime-graph"]
        observed_matches = sum(
            1 for entity in entities if "observed" in entity["states"]
        )
        atlas_observed_matches = sum(
            1
            for entity in entities
            if any(
                facet["state"] == "observed"
                and facet["authority"] == "Atlas"
                and self._source_map[facet["source_id"]].source_kind
                == "atlas-runtime-graph"
                for facet in entity["facets"]
            )
        )
        if not entities:
            status = "not-found"
        elif exact_count == 1:
            status = (
                "ambiguous-exact"
                if next(entity["ambiguous"] for entity in entities if entity["exact"])
                else "exact"
            )
        elif exact_count > 1:
            status = "ambiguous-exact"
        else:
            status = "matches"
        uncertainty: list[str] = []
        if not atlas_sources:
            uncertainty.append(
                "No Atlas runtime projection was supplied; static and receipt hits do not establish assembled-game presence."
            )
        elif atlas_observed_matches == 0:
            uncertainty.append(
                "The supplied Atlas runtime projection produced no returned runtime facet for this query."
            )
        if any(source.state in {"partial", "unavailable", "invalid", "unvalidated"} for source in self.sources):
            uncertainty.append(
                "One or more sources are partial, unavailable, invalid, or semantically unvalidated; inspect source coverage."
            )
        result: dict[str, Any] = {
            "format": RESULT_FORMAT,
            "schema_version": RESULT_SCHEMA_VERSION,
            "read_only": True,
            "query": {
                "raw": request.raw,
                "text": request.text,
                "terms": list(request.terms),
                "filters": {key: list(values) for key, values in sorted(request.filters.items())},
                "interpreted": [row.public() for row in request.interpreted],
            },
            "summary": {
                "status": status,
                "entities": total,
                "returned": len(returned),
                "truncated": total > len(returned),
                "exact_entities": exact_count,
                "ambiguous_entities": ambiguous_count,
                "observed_entities": observed_matches,
                "runtime_coverage": "supplied" if atlas_sources else "unavailable",
                "source_count": len(self.sources),
                "record_count": len(self.records),
            },
            "sources": [source.public() for source in self.sources],
            "matches": returned,
            "uncertainty": uncertainty,
            "limitations": [
                "Explorer ranking, grouping, and cross-links are a Workbench Shell projection; they do not replace source authorities.",
                "Shared literal identities join presentation facets but do not by themselves prove causality or runtime equivalence.",
            ],
        }
        result["result_id"] = content_id(
            "workbench-runtime-explorer-result:sha256:", result
        )
        validate_result(result)
        return result
