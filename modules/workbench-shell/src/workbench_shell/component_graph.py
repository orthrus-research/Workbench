"""Load and enforce the Workbench module and client dependency graph."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


KINDS = frozenset({"authority", "foundation", "client"})


class ComponentGraphError(ValueError):
    """Raised when the declared component topology violates its contract."""


@dataclass(frozen=True)
class Component:
    id: str
    kind: str
    path: str
    state: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class ComponentGraph:
    registry_id: str
    lifecycle_states: tuple[str, ...]
    shell_component: str
    project_context_component: str
    authority_components: tuple[str, ...]
    profile_roots: tuple[str, ...]
    components: tuple[Component, ...]

    def by_id(self) -> dict[str, Component]:
        return {component.id: component for component in self.components}

    def dependency_order(self) -> tuple[str, ...]:
        """Return dependencies before their consumers."""

        components = self.by_id()
        visited: set[str] = set()
        visiting: list[str] = []
        ordered: list[str] = []

        def visit(component_id: str) -> None:
            if component_id in visited:
                return
            if component_id in visiting:
                cycle_start = visiting.index(component_id)
                cycle = visiting[cycle_start:] + [component_id]
                raise ComponentGraphError(
                    "component dependency cycle: " + " -> ".join(cycle)
                )
            visiting.append(component_id)
            for dependency in components[component_id].depends_on:
                visit(dependency)
            visiting.pop()
            visited.add(component_id)
            ordered.append(component_id)

        for component in self.components:
            visit(component.id)
        return tuple(ordered)

    def dependency_edges(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (component.id, dependency)
            for component in self.components
            for dependency in component.depends_on
        )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComponentGraphError(f"{label} cannot be loaded: {path}") from exc
    if not isinstance(parsed, dict):
        raise ComponentGraphError(f"{label} must be a JSON object: {path}")
    return parsed


def _strings(
    value: Any,
    label: str,
    *,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ComponentGraphError(f"{label} must be a string array")
    if len(value) != len(set(value)):
        raise ComponentGraphError(f"{label} contains duplicates")
    return tuple(value)


def _inside(root: Path, relative: str, label: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ComponentGraphError(f"{label} escapes the repository: {relative}")
    return candidate


def load_component_graph(
    registry_path: Path | str,
    repository_root: Path | str,
) -> ComponentGraph:
    """Load and validate the repository component graph."""

    root = Path(repository_root).resolve()
    payload = _read_json_object(Path(registry_path), "component registry")
    if payload.get("format") != "workbench-component-registry-v2":
        raise ComponentGraphError("unsupported component registry format")
    if payload.get("schema_version") != 2:
        raise ComponentGraphError("unsupported component registry schema version")
    if payload.get("registry_id") != "workbench-components":
        raise ComponentGraphError("unexpected component registry identity")

    lifecycle_states = _strings(
        payload.get("lifecycle_states"),
        "lifecycle_states",
        nonempty=True,
    )
    constraints = payload.get("constraints")
    if not isinstance(constraints, dict):
        raise ComponentGraphError("constraints must be an object")
    shell_id = constraints.get("shell_component")
    project_context_id = constraints.get("project_context_component")
    if not isinstance(shell_id, str) or not shell_id:
        raise ComponentGraphError("shell_component must be a component ID")
    if not isinstance(project_context_id, str) or not project_context_id:
        raise ComponentGraphError(
            "project_context_component must be a component ID"
        )
    authority_ids = _strings(
        constraints.get("authority_components"),
        "authority_components",
        nonempty=True,
    )
    profile_roots = _strings(
        constraints.get("profile_roots"),
        "profile_roots",
        nonempty=True,
    )

    rows = payload.get("components")
    if not isinstance(rows, list) or not rows:
        raise ComponentGraphError("components must be a non-empty array")
    components: list[Component] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for index, row in enumerate(rows):
        label = f"components[{index}]"
        if not isinstance(row, dict):
            raise ComponentGraphError(f"{label} must be an object")
        component_id = row.get("id")
        kind = row.get("kind")
        path = row.get("path")
        state = row.get("state")
        if not isinstance(component_id, str) or not component_id:
            raise ComponentGraphError(f"{label}.id must be a string")
        if component_id in ids:
            raise ComponentGraphError(f"duplicate component ID: {component_id}")
        ids.add(component_id)
        if kind not in KINDS:
            raise ComponentGraphError(f"{component_id} has unknown kind: {kind}")
        if not isinstance(path, str) or not path:
            raise ComponentGraphError(f"{component_id} lacks path")
        if path in paths:
            raise ComponentGraphError(f"duplicate component path: {path}")
        paths.add(path)
        resolved_path = _inside(root, path, f"{component_id} path")
        if not resolved_path.is_dir():
            raise ComponentGraphError(
                f"{component_id} path does not exist: {path}"
            )
        if state not in lifecycle_states:
            raise ComponentGraphError(
                f"{component_id} has unknown lifecycle state: {state}"
            )
        dependencies = _strings(
            row.get("depends_on"),
            f"{component_id}.depends_on",
        )
        components.append(
            Component(
                id=component_id,
                kind=kind,
                path=path,
                state=state,
                depends_on=dependencies,
            )
        )

    graph = ComponentGraph(
        registry_id="workbench-components",
        lifecycle_states=lifecycle_states,
        shell_component=shell_id,
        project_context_component=project_context_id,
        authority_components=authority_ids,
        profile_roots=profile_roots,
        components=tuple(components),
    )
    by_id = graph.by_id()
    for component in graph.components:
        if component.id in component.depends_on:
            raise ComponentGraphError(
                f"{component.id} depends on itself"
            )
        unknown = sorted(set(component.depends_on) - set(by_id))
        if unknown:
            raise ComponentGraphError(
                f"{component.id} has unknown dependencies: {', '.join(unknown)}"
            )

    if shell_id not in by_id or by_id[shell_id].kind != "foundation":
        raise ComponentGraphError("shell_component is not a foundation component")
    if (
        project_context_id not in by_id
        or by_id[project_context_id].kind != "foundation"
    ):
        raise ComponentGraphError(
            "project_context_component is not a foundation component"
        )
    for authority_id in authority_ids:
        if authority_id not in by_id or by_id[authority_id].kind != "authority":
            raise ComponentGraphError(
                f"declared authority is not an authority component: {authority_id}"
            )

    required_shell_dependencies = {project_context_id, *authority_ids}
    if set(by_id[shell_id].depends_on) != required_shell_dependencies:
        raise ComponentGraphError(
            "Workbench Shell dependencies must be Project Intelligence "
            "plus every declared authority"
        )
    if by_id[project_context_id].depends_on:
        raise ComponentGraphError(
            "Project Intelligence cannot import another module in this bootstrap"
        )
    for authority_id in authority_ids:
        if set(by_id[authority_id].depends_on) - {project_context_id}:
            raise ComponentGraphError(
                f"authority may consume source observation, not another authority or Shell: {authority_id}"
            )
    for component in graph.components:
        if component.kind == "client" and component.depends_on != (shell_id,):
            raise ComponentGraphError(
                f"client must depend only on Workbench Shell: {component.id}"
            )

    for profile_root in profile_roots:
        resolved = _inside(root, profile_root, "profile root")
        if not resolved.is_dir():
            raise ComponentGraphError(
                f"profile root does not exist: {profile_root}"
            )

    graph.dependency_order()
    return graph
