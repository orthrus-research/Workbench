"""Internal and serialized model for the Exact Runtime Explorer."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


RESULT_FORMAT = "workbench-exact-runtime-explorer-result-v1"
RESULT_SCHEMA_VERSION = 1
SOURCE_FORMAT = "workbench-exact-runtime-explorer-source-v1"
RECORD_STATES = frozenset(
    {
        "observed",
        "declared",
        "static-possible",
        "verbatim-evidence",
        "presentation-observation",
        "teaching",
        "unresolved",
    }
)
SOURCE_STATES = frozenset(
    {"complete", "partial", "unavailable", "invalid", "unvalidated"}
)
_SINGLE_LINE_RE = re.compile(r"^[^\r\n\x00]{1,8192}$")


class ExplorerError(RuntimeError):
    """An explorer request or evidence source is invalid."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(canonical_bytes(value)).hexdigest()


def single_line(value: object, label: str, *, maximum: int = 8192) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or _SINGLE_LINE_RE.fullmatch(value) is None
    ):
        raise ExplorerError(f"{label} must be bounded single-line text")
    return value


def unique_dicts(values: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    by_bytes: dict[bytes, dict[str, Any]] = {}
    for value in values:
        row = dict(value)
        by_bytes[canonical_bytes(row)] = row
    return tuple(by_bytes[key] for key in sorted(by_bytes))


@dataclass(frozen=True, slots=True)
class ExplorerSource:
    source_id: str
    source_kind: str
    authority: str
    state: str
    identity: Mapping[str, Any]
    scope: Mapping[str, Any] = field(default_factory=dict)
    coverage: Mapping[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        single_line(self.source_id, "explorer source ID")
        single_line(self.source_kind, "explorer source kind", maximum=128)
        single_line(self.authority, "explorer source authority", maximum=256)
        if self.state not in SOURCE_STATES:
            raise ExplorerError(f"unsupported explorer source state: {self.state}")
        for label, value in (
            ("identity", self.identity),
            ("scope", self.scope),
            ("coverage", self.coverage),
        ):
            if not isinstance(value, Mapping):
                raise ExplorerError(f"explorer source {label} must be an object")
        for limitation in self.limitations:
            single_line(limitation, "explorer source limitation")

    def public(self) -> dict[str, Any]:
        return {
            "format": SOURCE_FORMAT,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "authority": self.authority,
            "state": self.state,
            "identity": dict(self.identity),
            "scope": dict(self.scope),
            "coverage": dict(self.coverage),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class ExplorerRecord:
    record_id: str
    source_id: str
    authority: str
    state: str
    kind: str
    name: str
    identities: tuple[Mapping[str, Any], ...]
    owner: Mapping[str, Any]
    version: str | None = None
    scope: Mapping[str, Any] = field(default_factory=dict)
    declaration: Mapping[str, Any] | None = None
    runtime_form: Mapping[str, Any] | None = None
    relationships: tuple[Mapping[str, Any], ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    navigation: tuple[Mapping[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()
    rank_hint: int = 0

    def __post_init__(self) -> None:
        single_line(self.record_id, "explorer record ID")
        single_line(self.source_id, "explorer record source ID")
        single_line(self.authority, "explorer record authority", maximum=256)
        single_line(self.kind, "explorer record kind", maximum=128)
        single_line(self.name, "explorer record name")
        if self.state not in RECORD_STATES:
            raise ExplorerError(f"unsupported explorer record state: {self.state}")
        if self.version is not None:
            single_line(self.version, "explorer record version", maximum=512)
        if not self.identities:
            raise ExplorerError("explorer record must retain at least one identity")
        for identity in self.identities:
            if not isinstance(identity, Mapping):
                raise ExplorerError("explorer identity must be an object")
            single_line(identity.get("kind"), "explorer identity kind", maximum=128)
            single_line(identity.get("value"), "explorer identity value")
            basis = identity.get("basis")
            if basis is not None:
                single_line(basis, "explorer identity basis", maximum=512)
        for term in self.search_terms:
            single_line(term, "explorer search term")
        for limitation in self.limitations:
            single_line(limitation, "explorer record limitation")
        for label, value in (
            ("owner", self.owner),
            ("scope", self.scope),
        ):
            if not isinstance(value, Mapping):
                raise ExplorerError(f"explorer record {label} must be an object")
        for label, value in (
            ("declaration", self.declaration),
            ("runtime form", self.runtime_form),
        ):
            if value is not None and not isinstance(value, Mapping):
                raise ExplorerError(f"explorer record {label} must be an object or null")
        for label, values in (
            ("relationship", self.relationships),
            ("evidence", self.evidence),
            ("navigation", self.navigation),
        ):
            if any(not isinstance(value, Mapping) for value in values):
                raise ExplorerError(f"explorer record {label} rows must be objects")
        if isinstance(self.rank_hint, bool) or not isinstance(self.rank_hint, int):
            raise ExplorerError("explorer rank hint must be an integer")

    @property
    def exact_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (str(identity["kind"]), str(identity["value"]))
            for identity in self.identities
        )

    def searchable_values(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = [("name", self.name), ("kind", self.kind)]
        for identity in self.identities:
            values.append((str(identity["kind"]), str(identity["value"])))
        for term in self.search_terms:
            values.append(("context", term))
        return tuple(values)

    def public(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source_id": self.source_id,
            "authority": self.authority,
            "state": self.state,
            "kind": self.kind,
            "name": self.name,
            "identities": [dict(value) for value in self.identities],
            "owner": dict(self.owner),
            "version": self.version,
            "scope": dict(self.scope),
            "declaration": None if self.declaration is None else dict(self.declaration),
            "runtime_form": None if self.runtime_form is None else dict(self.runtime_form),
            "relationships": [dict(value) for value in self.relationships],
            "evidence": [dict(value) for value in self.evidence],
            "navigation": [dict(value) for value in self.navigation],
            "limitations": list(self.limitations),
        }


def unresolved_owner(reason: str = "no exact owner binding was supplied") -> dict[str, Any]:
    return {"state": "unresolved", "actors": [], "basis": reason}


def _facet_identity_projection(
    facets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for facet in facets:
        identities = facet.get("identities")
        if not isinstance(identities, list) or not identities:
            raise ExplorerError("explorer facet identities are malformed")
        for identity in identities:
            if not isinstance(identity, Mapping):
                raise ExplorerError("explorer facet identity is malformed")
            kind = single_line(
                identity.get("kind"), "explorer facet identity kind", maximum=128
            )
            identity_value = single_line(
                identity.get("value"), "explorer facet identity value"
            )
            basis = identity.get("basis")
            if basis is not None:
                basis = single_line(
                    basis, "explorer facet identity basis", maximum=512
                )
            grouped.setdefault((kind, identity_value), set())
            if basis:
                grouped[(kind, identity_value)].add(basis)
    return [
        {
            "kind": kind,
            "value": identity_value,
            "basis": "; ".join(sorted(bases)) if bases else "unspecified",
        }
        for (kind, identity_value), bases in sorted(grouped.items())
    ]


def _facet_scope_values(facet: Mapping[str, Any], key: str) -> tuple[str, ...]:
    scope = facet.get("scope")
    if not isinstance(scope, Mapping):
        raise ExplorerError("explorer facet scope is malformed")
    aliases = {
        "profile": ("profile", "profile_id", "pack_profile_id"),
        "side": ("physical_side", "side"),
    }[key]
    return tuple(
        str(scope[alias])
        for alias in aliases
        if isinstance(scope.get(alias), str) and scope.get(alias)
    )


def _facet_mod_owner_ids(owners: Iterable[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    for owner in owners:
        actors = owner.get("actors")
        if not isinstance(actors, list):
            raise ExplorerError("explorer facet owner actors are malformed")
        for actor in actors:
            if not isinstance(actor, Mapping):
                raise ExplorerError("explorer facet owner actor is malformed")
            kind = str(actor.get("kind", "")).casefold()
            if kind not in {"mod", "mod-id", "mod-id-candidate", "mod-candidate"}:
                continue
            identifier = actor.get("normalized_id") or actor.get("id")
            if isinstance(identifier, str) and identifier:
                result.add(identifier.casefold())
    return result


def _facet_ambiguity(
    facets: list[Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    owners: list[Mapping[str, Any]],
    versions: list[str],
) -> list[str]:
    ambiguity: list[str] = []
    observed_runtime_nodes = {
        str(facet["record_id"])
        for facet in facets
        if facet.get("state") == "observed"
        and facet.get("authority") == "Atlas"
        and sources[str(facet["source_id"])].get("source_kind")
        == "atlas-runtime-graph"
    }
    if len(observed_runtime_nodes) > 1:
        ambiguity.append("multiple-observed-runtime-nodes")
    artifact_ids = {
        str(facet["scope"]["artifact_id"])
        for facet in facets
        if isinstance(facet.get("scope"), Mapping)
        and isinstance(facet["scope"].get("artifact_id"), str)
    }
    if len(artifact_ids) > 1:
        ambiguity.append("multiple-static-artifacts")
    if any("ambiguous" in str(owner.get("state", "")) for owner in owners):
        ambiguity.append("owner-ambiguous")
    if len(_facet_mod_owner_ids(owners)) > 1:
        ambiguity.append("owner-conflicted")
    if len(versions) > 1:
        ambiguity.append("version-ambiguous")
    observed_scopes = {
        (
            _facet_scope_values(facet, "profile"),
            _facet_scope_values(facet, "side"),
        )
        for facet in facets
        if facet.get("state") == "observed"
        and (
            _facet_scope_values(facet, "profile")
            or _facet_scope_values(facet, "side")
        )
    }
    if len(observed_scopes) > 1:
        ambiguity.append("observed-scope-conflicted")
    return ambiguity


def validate_result(value: Mapping[str, Any]) -> None:
    """Validate the V1 envelope and its content identity."""

    if not isinstance(value, Mapping):
        raise ExplorerError("explorer result must be an object")
    required = {
        "format",
        "schema_version",
        "read_only",
        "result_id",
        "query",
        "summary",
        "sources",
        "matches",
        "uncertainty",
        "limitations",
    }
    if set(value) != required:
        raise ExplorerError("explorer result fields are unexpected or missing")
    if (
        value.get("format") != RESULT_FORMAT
        or value.get("schema_version") != RESULT_SCHEMA_VERSION
        or value.get("read_only") is not True
    ):
        raise ExplorerError("explorer result format is unsupported")
    query = value.get("query")
    summary = value.get("summary")
    sources = value.get("sources")
    matches = value.get("matches")
    if not isinstance(query, Mapping) or not isinstance(summary, Mapping):
        raise ExplorerError("explorer result query or summary is malformed")
    if not isinstance(sources, list) or not isinstance(matches, list):
        raise ExplorerError("explorer result sources or matches are malformed")
    single_line(query.get("raw"), "explorer result query")
    source_ids: set[str] = set()
    source_rows: dict[str, Mapping[str, Any]] = {}
    for source in sources:
        if not isinstance(source, Mapping) or source.get("format") != SOURCE_FORMAT:
            raise ExplorerError("explorer result contains a malformed source")
        source_id = single_line(source.get("source_id"), "explorer result source ID")
        if source_id in source_ids:
            raise ExplorerError("explorer result repeats a source ID")
        source_ids.add(source_id)
        source_rows[source_id] = source
        if source.get("state") not in SOURCE_STATES:
            raise ExplorerError("explorer result source has an unsupported state")
    entity_ids: set[str] = set()
    visible_record_ids: set[str] = set()
    visible_exact = 0
    visible_ambiguous = 0
    visible_observed = 0
    for entity in matches:
        if not isinstance(entity, Mapping):
            raise ExplorerError("explorer result contains a malformed entity")
        entity_id = single_line(entity.get("entity_id"), "explorer entity ID")
        if entity_id in entity_ids:
            raise ExplorerError("explorer result repeats an entity ID")
        entity_ids.add(entity_id)
        if not isinstance(entity.get("exact"), bool) or not isinstance(
            entity.get("ambiguous"), bool
        ):
            raise ExplorerError("explorer entity exact/ambiguous state is malformed")
        ambiguity = entity.get("ambiguity")
        states = entity.get("states")
        if not isinstance(ambiguity, list) or not isinstance(states, list):
            raise ExplorerError("explorer entity state or ambiguity is malformed")
        if entity["ambiguous"] != bool(ambiguity):
            raise ExplorerError("explorer entity ambiguity projection is inconsistent")
        facets = entity.get("facets")
        if not isinstance(facets, list) or not facets:
            raise ExplorerError("explorer entity has no source facets")
        facet_record_ids: list[str] = []
        facet_states: set[str] = set()
        for facet in facets:
            if not isinstance(facet, Mapping) or facet.get("source_id") not in source_ids:
                raise ExplorerError("explorer facet references an unknown source")
            if facet.get("state") not in RECORD_STATES:
                raise ExplorerError("explorer facet has an unsupported state")
            if not isinstance(facet.get("owner"), Mapping) or not isinstance(
                facet.get("scope"), Mapping
            ):
                raise ExplorerError("explorer facet owner or scope is malformed")
            for field in ("relationships", "navigation", "limitations"):
                rows = facet.get(field)
                if not isinstance(rows, list):
                    raise ExplorerError(
                        f"explorer facet {field} projection is malformed"
                    )
            if any(
                not isinstance(row, Mapping)
                for field in ("relationships", "navigation")
                for row in facet[field]
            ):
                raise ExplorerError(
                    "explorer facet relationship or navigation is malformed"
                )
            if any(not isinstance(row, str) for row in facet["limitations"]):
                raise ExplorerError("explorer facet limitations are malformed")
            record_id = single_line(
                facet.get("record_id"), "explorer facet record ID"
            )
            if record_id in visible_record_ids:
                raise ExplorerError("explorer result repeats a record facet")
            visible_record_ids.add(record_id)
            facet_record_ids.append(record_id)
            facet_states.add(str(facet["state"]))
            source = source_rows[str(facet["source_id"])]
            if facet.get("authority") != source.get("authority"):
                raise ExplorerError(
                    "explorer facet authority disagrees with its source"
                )
        facet_owners = [
            dict(value)
            for value in unique_dicts(
                facet.get("owner") for facet in facets
            )
        ]
        facet_versions = sorted(
            {
                str(facet["version"])
                for facet in facets
                if isinstance(facet.get("version"), str) and facet.get("version")
            }
        )
        if entity.get("identities") != _facet_identity_projection(facets):
            raise ExplorerError(
                "explorer entity identity projection disagrees with its facets"
            )
        if entity.get("owners") != facet_owners:
            raise ExplorerError(
                "explorer entity owner projection disagrees with its facets"
            )
        if entity.get("versions") != facet_versions:
            raise ExplorerError(
                "explorer entity version projection disagrees with its facets"
            )
        expected_scopes = [
            dict(row)
            for row in unique_dicts(facet.get("scope") for facet in facets)
        ]
        if entity.get("scopes") != expected_scopes:
            raise ExplorerError(
                "explorer entity scope projection disagrees with its facets"
            )
        expected_relationships = [
            dict(row)
            for row in unique_dicts(
                relationship
                for facet in facets
                for relationship in facet.get("relationships", [])
            )
        ]
        if entity.get("relationships") != expected_relationships:
            raise ExplorerError(
                "explorer entity relationship projection disagrees with its facets"
            )
        expected_navigation = [
            dict(row)
            for row in unique_dicts(
                navigation
                for facet in facets
                for navigation in facet.get("navigation", [])
            )
        ]
        if entity.get("navigation") != expected_navigation:
            raise ExplorerError(
                "explorer entity navigation projection disagrees with its facets"
            )
        expected_limitations = sorted(
            {
                str(limitation)
                for facet in facets
                for limitation in facet.get("limitations", [])
            }
        )
        if entity.get("limitations") != expected_limitations:
            raise ExplorerError(
                "explorer entity limitation projection disagrees with its facets"
            )
        expected_ambiguity = _facet_ambiguity(
            facets,
            source_rows,
            facet_owners,
            facet_versions,
        )
        if ambiguity != expected_ambiguity:
            raise ExplorerError(
                "explorer entity ambiguity projection disagrees with its facets"
            )
        expected_entity_id = content_id(
            "workbench-runtime-explorer-entity:sha256:",
            sorted(facet_record_ids),
        )
        if entity_id != expected_entity_id:
            raise ExplorerError("explorer entity identity is stale or invalid")
        if states != sorted(facet_states):
            raise ExplorerError("explorer entity states disagree with its facets")
        visible_exact += int(entity["exact"])
        visible_ambiguous += int(entity["ambiguous"])
        visible_observed += int("observed" in facet_states)
    returned = summary.get("returned")
    total = summary.get("entities")
    if (
        isinstance(returned, bool)
        or not isinstance(returned, int)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or returned != len(matches)
        or total < returned
    ):
        raise ExplorerError("explorer result counts are inconsistent")
    for key in (
        "exact_entities",
        "ambiguous_entities",
        "observed_entities",
        "source_count",
        "record_count",
    ):
        count = summary.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ExplorerError(f"explorer result {key} count is malformed")
    if summary["source_count"] != len(sources):
        raise ExplorerError("explorer result source count is inconsistent")
    expected_runtime_coverage = (
        "supplied"
        if any(
            source.get("authority") == "Atlas"
            and source.get("source_kind") == "atlas-runtime-graph"
            for source in sources
        )
        else "unavailable"
    )
    if summary.get("runtime_coverage") != expected_runtime_coverage:
        raise ExplorerError("explorer result runtime coverage is inconsistent")
    if summary.get("truncated") != (total > returned):
        raise ExplorerError("explorer result truncation state is inconsistent")
    if summary["record_count"] < len(visible_record_ids):
        raise ExplorerError("explorer result record count is inconsistent")
    if (
        summary["exact_entities"] < visible_exact
        or summary["ambiguous_entities"] < visible_ambiguous
        or summary["observed_entities"] < visible_observed
        or summary["exact_entities"] > total
        or summary["ambiguous_entities"] > total
        or summary["observed_entities"] > total
    ):
        raise ExplorerError("explorer result entity summary is inconsistent")
    if not summary["truncated"] and (
        summary["exact_entities"] != visible_exact
        or summary["ambiguous_entities"] != visible_ambiguous
        or summary["observed_entities"] != visible_observed
    ):
        raise ExplorerError("complete explorer result summary is inconsistent")
    exact_count = summary["exact_entities"]
    if exact_count and not visible_exact:
        raise ExplorerError("explorer result hides every exact entity")
    expected_status = (
        "not-found"
        if total == 0
        else "ambiguous-exact"
        if exact_count > 1
        or (
            exact_count == 1
            and any(
                bool(entity["ambiguous"])
                for entity in matches
                if entity["exact"]
            )
        )
        else "exact"
        if exact_count == 1
        else "matches"
    )
    if summary.get("status") != expected_status:
        raise ExplorerError("explorer result status is inconsistent")
    material = dict(value)
    actual_id = material.pop("result_id", None)
    expected_id = content_id("workbench-runtime-explorer-result:sha256:", material)
    if actual_id != expected_id:
        raise ExplorerError("explorer result identity is stale or invalid")
