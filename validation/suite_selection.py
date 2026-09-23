"""Conservative owner selection with an explanation for every Python suite.

This is an opt-in focused workflow. CI's canonical sweeps remain independent
of this selector. Missing ownership or dependency information broadens the run.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Sequence
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from suite_catalog import PYTHON_TEST_SUITES, ROOT


@dataclass(frozen=True)
class SuiteDecision:
    suite: str
    selected: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SelectionPlan:
    changed_paths: tuple[str, ...]
    selection_kind: str
    selected_suites: tuple[str, ...]
    decisions: tuple[SuiteDecision, ...]
    broadening_reasons: tuple[str, ...] = ()

    def to_document(self) -> dict[str, object]:
        return {
            "format": "workbench-python-suite-selection-v1",
            "selection_kind": self.selection_kind,
            "changed_paths": list(self.changed_paths),
            "selected_suites": list(self.selected_suites),
            "broadening_reasons": list(self.broadening_reasons),
            "decisions": [
                {"suite": row.suite, "selected": row.selected, "reasons": list(row.reasons)}
                for row in self.decisions
            ],
            "limitations": [
                "Selection is a plan, not evidence that any test ran.",
                "A proper subset is focused validation, not a canonical pass.",
                "Native, IDE, platform and required physical checks remain separate obligations.",
            ],
        }


def _owner(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if parts and parts[0] in {"api", "core", "validation"}:
        return parts[0]
    if len(parts) >= 2 and parts[0] == "modules":
        return "/".join(parts[:2])
    if len(parts) >= 3 and parts[0] == "profiles" and parts[1] in {"packs", "platforms"}:
        return "/".join(parts[:3])
    return None


def _path_boundary(path: object) -> tuple[str, str | None]:
    if not isinstance(path, str) or not path or "\0" in path:
        return repr(path), "changed path is missing or invalid"
    parts = path.split("/")
    if path.startswith("/") or "\\" in path or ":" in path or any(part in {"", ".", ".."} for part in parts):
        return path, "changed path is not an unambiguous repository-relative POSIX path"
    if parts[0] in {"api", "core", "validation", "tools", "tests", "shared", ".github"}:
        return path, "shared API, Core, tooling, conformance or validation boundary changed"
    if path.startswith("modules/material-semantics/"):
        return path, "shared material semantics changed"
    basename = parts[-1].lower()
    if any(part.lower() in {"schemas", "schema", "contracts", "protocol", "protocols", "services", "shared"} for part in parts) or any(token in basename for token in ("protocol", "service_host", "host_services", "registration")) or basename.endswith(".schema.json"):
        return path, "schema, contract or protocol boundary changed"
    if basename in {"pyproject.toml", "package.json", "package-lock.json", "pixi.toml", "pixi.lock", "setup.cfg", "setup.py", "build.gradle", "settings.gradle", "gradle.properties", "profile.yaml", "provisional.yaml"} or basename.endswith((".lock", ".toml")):
        return path, "package, build or environment metadata changed"
    return path, None


def _dependency_graph(root: Path) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Combine package requirements, test roots and static Python imports.

    Package dependencies cover native imports; suite paths add test-only owner
    integrations. Do not infer independence from a runtime package manifest.
    """
    suite_owners = {suite.name: _owner(suite.start_dir) for suite in PYTHON_TEST_SUITES}
    graph = {owner: set() for owner in suite_owners.values() if owner is not None}
    graph["modules/material-semantics"] = set()
    manifests = [root / "api/pyproject.toml", root / "core/pyproject.toml"]
    manifests.extend(sorted((root / "modules").glob("*/pyproject.toml")))
    manifests.extend(sorted((root / "profiles").glob("*/*/pyproject.toml")))
    distributions: dict[str, str] = {}
    imports: dict[str, str] = {}
    documents = []
    for manifest in manifests:
        with manifest.open("rb") as stream:
            document = tomllib.load(stream)
        owner = _owner(manifest.relative_to(root).as_posix())
        raw_name = document["project"]["name"]
        if not isinstance(raw_name, str) or owner is None:
            raise ValueError(f"ambiguous package ownership: {manifest.relative_to(root)}")
        name = canonicalize_name(raw_name)
        if name in distributions:
            raise ValueError(f"ambiguous package ownership: {manifest.relative_to(root)}")
        distributions[name] = owner
        setuptools = document["tool"]["setuptools"]
        for field in ("packages", "py-modules"):
            names = setuptools.get(field, [])
            if not isinstance(names, list) or any(not isinstance(item, str) or not item for item in names):
                raise ValueError(f"invalid import ownership for {owner}: {field}")
            for module in names:
                if module in imports and imports[module] != owner:
                    raise ValueError(f"ambiguous Python import ownership: {module}")
                imports[module] = owner
        graph.setdefault(owner, set())
        documents.append((owner, document))
    # All executable owners must remain represented by their package metadata.
    # Manuals and validation are source-only test/policy owners.
    packaged = set(distributions.values())
    required = set(graph) - {"modules/manuals", "validation"}
    if required - packaged:
        raise ValueError("missing package ownership: " + ", ".join(sorted(required - packaged)))
    # Repository tools are shared inputs (their changes always broaden). They
    # are imported by source tests but are intentionally not native packages.
    for source in (root / "tools").glob("*.py"):
        imports.setdefault(source.stem, "validation")
    for owner, document in documents:
        requirements = document["project"].get("dependencies", [])
        if not isinstance(requirements, list):
            raise ValueError(f"invalid package requirements for {owner}")
        for requirement in requirements:
            if not isinstance(requirement, str):
                raise ValueError(f"invalid package requirement for {owner}")
            # Parse the complete requirement, including extras/markers, and
            # retain conditional local edges conservatively on every platform.
            name = canonicalize_name(Requirement(requirement).name)
            if name in distributions:
                graph[owner].add(distributions[name])
            elif name.startswith("workbench-"):
                raise ValueError(f"unmapped Workbench dependency for {owner}: {name}")
    for suite in PYTHON_TEST_SUITES:
        owner = suite_owners[suite.name]
        if owner is None:
            continue
        for source in suite.python_paths:
            dependency = _owner(source)
            if dependency is None or dependency not in graph:
                raise ValueError(f"unmapped test dependency for {suite.name}: {source}")
            if dependency != owner:
                graph[owner].add(dependency)
    # Tests can import another owner even when runtime package requirements and
    # configured import roots do not mention it. Scan helpers as well as test
    # files, without importing them or executing their module-level code.
    for owner in graph:
        for directory in (root / owner / "src", root / owner / "tests"):
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.py")):
                if any(part in {"__pycache__", ".venv", "node_modules", "build", "dist"} for part in path.relative_to(directory).parts):
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except (SyntaxError, UnicodeError) as exc:
                    raise ValueError(f"cannot inspect Python dependencies: {path.relative_to(root)}") from exc
                for module in _static_imports(tree):
                    prefix = module
                    while prefix and prefix not in imports:
                        prefix = prefix.rpartition(".")[0]
                    if prefix:
                        dependency = imports[prefix]
                        if dependency != owner:
                            graph[owner].add(dependency)
                    elif module.startswith("workbench_") and not any(name.startswith(module + ".") for name in imports):
                        raise ValueError(f"unmapped Python import in {path.relative_to(root)}: {module}")
    # Source checkout routing and product CLI tests consume installed profiles
    # through discovery rather than explicit runtime Python imports.
    graph["modules/workbench-shell"].update(owner for owner in graph if owner.startswith("profiles/"))
    return graph, {name: owner for name, owner in suite_owners.items() if owner is not None}


def _static_imports(tree: ast.AST):
    """Include imports inside functions and literal dynamic imports."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
            yield from (node.module + "." + alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            dynamic = isinstance(function, ast.Name) and function.id == "__import__"
            dynamic |= isinstance(function, ast.Attribute) and function.attr == "import_module"
            if dynamic and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                yield node.args[0].value


def select_impacted_suites(changed_paths: Sequence[str], *, root: Path | None = None) -> SelectionPlan:
    """Select declared owners and transitive consumers, or broaden canonical.

    Callers must pass both old and new paths for renames and include deletions.
    Paths are classified lexically so a deleted file retains its owner.
    """
    normalized: list[str] = []
    broaden: list[str] = []
    for supplied in changed_paths:
        path, boundary = _path_boundary(supplied)
        if path not in normalized:
            normalized.append(path)
        if boundary:
            broaden.append(f"{path}: {boundary}")
    if not normalized:
        broaden.append("no changed-path evidence supplied")
    if broaden:
        # The mapping cannot narrow a boundary already known to require every
        # suite. Avoid parsing the whole repository just to repeat that answer.
        reasons = tuple(dict.fromkeys(broaden))
        decisions = tuple(SuiteDecision(suite.name, True, ("canonical broadening: " + "; ".join(reasons),))
                          for suite in PYTHON_TEST_SUITES)
        return SelectionPlan(tuple(normalized), "canonical", tuple(suite.name for suite in PYTHON_TEST_SUITES), decisions, reasons)
    try:
        graph, suite_owners = _dependency_graph(Path(root) if root is not None else ROOT)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        graph, suite_owners = {}, {}
        broaden.append(f"owner dependency map could not be validated: {exc}")
    affected: dict[str, set[str]] = {}
    for path in normalized:
        owner = _owner(path)
        if owner is None or owner not in graph or owner not in set(suite_owners.values()) | {"modules/material-semantics"}:
            broaden.append(f"{path}: unknown or unregistered owner boundary")
        else:
            affected.setdefault(owner, set()).add(path)
    # Reverse dependency closure; cycles in profile/test compositions are legal.
    changed = True
    while changed:
        changed = False
        for consumer, dependencies in graph.items():
            reasons = set().union(*(affected.get(dependency, set()) for dependency in dependencies))
            if reasons - affected.get(consumer, set()):
                affected.setdefault(consumer, set()).update(reasons)
                changed = True
    broadening = tuple(dict.fromkeys(broaden))
    decisions = []
    for suite in PYTHON_TEST_SUITES:
        if broadening:
            reasons = ("canonical broadening: " + "; ".join(broadening),)
        elif suite.name in {"validation", "service-conformance", "validation-authority"}:
            reasons = ("always include runner/policy, conformance and product-open checks in focused plans",)
        elif suite_owners.get(suite.name) in affected:
            owner = suite_owners[suite.name]
            reasons = tuple(f"owner {owner} or its declared dependency affected by {path}" for path in sorted(affected[owner]))
        else:
            reasons = ()
        decisions.append(SuiteDecision(suite.name, bool(reasons), reasons or ("outside declared affected-owner/dependency closure; canonical sweeps still cover this suite",)))
    selected = tuple(row.suite for row in decisions if row.selected)
    return SelectionPlan(tuple(normalized), "canonical" if len(selected) == len(PYTHON_TEST_SUITES) else "focused", selected, tuple(decisions), broadening)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-file", action="append", default=[], help="repository-relative changed path; include both sides of renames")
    parser.add_argument("--json", action="store_true", help="emit the complete selected/unselected plan as JSON")
    args = parser.parse_args()
    plan = select_impacted_suites(args.changed_file)
    if args.json:
        print(json.dumps(plan.to_document(), indent=2, sort_keys=True))
    else:
        print(f"{plan.selection_kind} Python selection: {len(plan.selected_suites)} suites")
        for row in plan.decisions:
            print(f"{'selected' if row.selected else 'omitted'} {row.suite}: {'; '.join(row.reasons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
