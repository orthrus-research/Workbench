"""Terminal rendering for Exact Runtime Explorer results."""

from __future__ import annotations

import json
from typing import Any, Mapping, TextIO

from workbench_api.events import sanitize_terminal


def _safe(value: object) -> str:
    return sanitize_terminal(str(value))


def _owner_label(owners: list[Mapping[str, Any]]) -> str:
    labels: list[str] = []
    states: set[str] = set()
    for owner in owners:
        states.add(str(owner.get("state", "unresolved")))
        actors = owner.get("actors")
        if not isinstance(actors, list):
            continue
        for actor in actors:
            if not isinstance(actor, Mapping):
                continue
            identifier = (
                actor.get("id")
                or actor.get("name")
                or actor.get("sha256")
                or actor.get("artifact_sha256")
                or actor.get("runtime_node_id")
                or actor.get("uri")
            )
            version = actor.get("version")
            if identifier:
                labels.append(
                    str(identifier) + (f"@{version}" if version else "")
                )
    if labels:
        return ", ".join(dict.fromkeys(labels))
    return "/".join(sorted(states)) if states else "unresolved"


def _scope_label(scopes: list[Mapping[str, Any]]) -> str:
    labels: list[str] = []
    for scope in scopes:
        profile = scope.get("profile") or scope.get("profile_id")
        side = scope.get("physical_side") or scope.get("side")
        if profile or side:
            labels.append("/".join(str(value) for value in (profile, side) if value))
    return ", ".join(dict.fromkeys(labels))


def render_result(
    result: Mapping[str, Any],
    output: TextIO,
    *,
    details: bool = False,
) -> None:
    summary = result["summary"]
    output.write(
        "Exact Runtime Explorer · "
        f"{summary['status']} · {summary['entities']} result"
        f"{'s' if summary['entities'] != 1 else ''}"
        f" · Atlas runtime {summary['runtime_coverage']}"
        f" · {summary['observed_entities']} observed\n"
    )
    interpreted = result["query"].get("interpreted", [])
    if interpreted:
        output.write(
            "Interpreted: "
            + ", ".join(
                f"{_safe(row['kind'])}={_safe(row['value'])}"
                for row in interpreted
            )
            + "\n"
        )
    for warning in result.get("uncertainty", []):
        output.write(f"! {_safe(warning)}\n")
    matches = result.get("matches", [])
    if not matches:
        output.write("\nNo matching identity was found in the supplied sources.\n")
        return
    output.write("\n")
    for index, match in enumerate(matches, 1):
        states = ", ".join(match["states"])
        exact = (
            "ambiguous exact"
            if match.get("ambiguous") and match["exact"]
            else "exact"
            if match["exact"]
            else "ranked"
        )
        output.write(
            f"{index:>2}. {_safe(match['name'])}  "
            f"[{_safe(match['kind'])} · {_safe(states)} · {exact}]\n"
        )
        output.write(f"    Owner: {_safe(_owner_label(match['owners']))}\n")
        scope = _scope_label(match["scopes"])
        if scope:
            output.write(f"    Scope: {_safe(scope)}\n")
        preferred = [
            identity
            for identity in match["identities"]
            if identity["kind"]
            in {
                "runtime-node-id",
                "mod-id",
                "registry-name",
                "resource-location",
                "class-name",
                "mixin-class",
                "source-member",
                "config-path-key",
                "recipe-id",
                "artifact-sha256",
                "coordinate",
            }
        ]
        for identity in preferred[:5]:
            output.write(
                f"    {_safe(identity['kind'])}: {_safe(identity['value'])}\n"
        )
        for navigation in match.get("navigation", [])[:3]:
            line = navigation.get("line")
            suffix = f":{_safe(line)}" if line else ""
            output.write(
                f"    Open: {_safe(navigation.get('path'))}{suffix} "
                f"({_safe(navigation.get('resolution'))})\n"
            )
            if details and navigation.get("json_pointer"):
                output.write(
                    f"          JSON {_safe(navigation.get('json_pointer'))}\n"
                )
        if match.get("manuals"):
            output.write(
                "    Manuals: "
                + "; ".join(_safe(manual["title"]) for manual in match["manuals"])
                + "\n"
            )
        if details:
            if match.get("ambiguity"):
                output.write(
                    "    Ambiguity: "
                    + ", ".join(_safe(value) for value in match["ambiguity"])
                    + "\n"
                )
            output.write(
                "    Matched by: "
                + ", ".join(_safe(value) for value in match["matched_by"])
                + "\n"
            )
            output.write(
                "    Facets: "
                + ", ".join(
                    f"{_safe(facet['authority'])}:{_safe(facet['kind'])}:"
                    f"{_safe(facet['state'])}"
                    for facet in match["facets"]
                )
                + "\n"
            )
            for relationship in match.get("relationships", [])[:8]:
                other = relationship.get("node")
                other_label = ""
                if isinstance(other, Mapping):
                    other_label = f" -> {other.get('kind')} {other.get('id')}"
                target = relationship.get("target")
                if not other_label and isinstance(target, Mapping):
                    target_value = (
                        target.get("value")
                        or target.get("id")
                        or target.get("name")
                    )
                    if target_value is None:
                        target_value = json.dumps(
                            target, ensure_ascii=False, sort_keys=True
                        )
                    other_label = (
                        f" -> {target.get('kind', 'context')} {target_value}"
                    )
                source = relationship.get("source")
                if not other_label and isinstance(source, Mapping):
                    source_value = (
                        source.get("value")
                        or source.get("id")
                        or source.get("name")
                    )
                    if source_value is None:
                        source_value = json.dumps(
                            source, ensure_ascii=False, sort_keys=True
                        )
                    other_label = (
                        f" <- {source.get('kind', 'context')} {source_value}"
                    )
                direction = str(relationship.get("direction", "")).strip()
                prefix = f"{direction} " if direction else ""
                output.write(
                    f"    {_safe(prefix)}"
                    f"{_safe(str(relationship.get('predicate', '')) + other_label)}\n"
                )
            if len(match.get("relationships", [])) > 8:
                output.write(
                    f"    … {len(match['relationships']) - 8} more relationships\n"
                )
            for limitation in match.get("limitations", []):
                output.write(f"    Limit: {_safe(limitation)}\n")
        output.write("\n")
    if summary.get("truncated"):
        output.write(
            f"Showing {summary['returned']} of {summary['entities']} entities; "
            "raise --limit to inspect more.\n"
        )


def render_sources(result: Mapping[str, Any], output: TextIO) -> None:
    output.write("Explorer sources\n\n")
    for source in result.get("sources", []):
        output.write(
            f"- {_safe(source['authority'])} · {_safe(source['source_kind'])} · "
            f"{_safe(source['state'])}\n"
        )
        output.write(f"  {_safe(source['source_id'])}\n")
        coverage = source.get("coverage")
        if coverage:
            output.write(
                "  coverage: "
                + _safe(json.dumps(coverage, ensure_ascii=False, sort_keys=True))
                + "\n"
            )
        for limitation in source.get("limitations", []):
            output.write(f"  limit: {_safe(limitation)}\n")


def render_entity_json(result: Mapping[str, Any], index: int, output: TextIO) -> None:
    matches = result.get("matches", [])
    if not isinstance(index, int) or not 1 <= index <= len(matches):
        raise IndexError("result selection is out of range")
    output.write(json.dumps(matches[index - 1], indent=2, ensure_ascii=False, sort_keys=True) + "\n")
