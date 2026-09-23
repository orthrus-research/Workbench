#!/usr/bin/env python3
"""Inspect native package authorities and check the module dependency graph."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path = ROOT) -> list[dict[str, object]]:
    manifests = [root / "api/pyproject.toml", root / "core/pyproject.toml"]
    manifests += sorted(root.glob("modules/*/pyproject.toml"))
    manifests += sorted(root.glob("profiles/*/*/pyproject.toml"))
    result = []
    for path in manifests:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        project = document["project"]
        configuration = document.get("tool", {}).get("workbench", {})
        result.append(
            {
                "distribution": project["name"],
                "version": project["version"],
                "path": path.relative_to(root).as_posix(),
                "module_id": configuration.get("module-id"),
                "requires": configuration.get("requires", []),
                "optional_requires": configuration.get("optional-requires", []),
                "optional_dependencies": project.get("optional-dependencies", {}),
                "dependencies": project.get("dependencies", []),
            }
        )
    return result


def check(root: Path = ROOT) -> list[str]:
    rows = inventory(root)
    failures = []
    names = [row["distribution"] for row in rows]
    if len(names) != len(set(names)):
        failures.append("native distributions must have unique names")
    modules = {row["module_id"]: row for row in rows if row["module_id"]}
    owners = {}
    for source in root.glob("modules/*/src"):
        for path in source.iterdir():
            if path.is_dir() and (path / "__init__.py").is_file():
                owners[path.name] = source.parent.name
    for owner, row in modules.items():
        declared = set(row["requires"])
        optional = set(row["optional_requires"])
        extras = {
            re.split(r"[<>=!~\[; ]", value, maxsplit=1)[0]
            for values in row["optional_dependencies"].values()
            for value in values
        }
        if not optional <= modules.keys() or optional & declared:
            failures.append(f"{owner}: invalid optional module dependencies")
        for dependency in optional & modules.keys():
            if modules[dependency]["distribution"] not in extras:
                failures.append(
                    f"{owner}: {dependency} has no native optional dependency"
                )
        if not declared <= modules.keys():
            failures.append(f"{owner}: missing declared module")
        package_dependencies = {
            re.split(r"[<>=!~\[; ]", value, maxsplit=1)[0]
            for value in row["dependencies"]
        }
        for dependency in declared & modules.keys():
            if modules[dependency]["distribution"] not in package_dependencies:
                failures.append(f"{owner}: {dependency} has no native dependency")
        source = root / "modules" / owner / "src"
        for path in source.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                imported = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom) and not node.level
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for value in imported:
                    dependency = owners.get((value or "").split(".")[0])
                    if (
                        dependency
                        and dependency != owner
                        and dependency not in declared | optional
                    ):
                        failures.append(
                            f"{path.relative_to(root)}:{node.lineno}: undeclared module {dependency}"
                        )
    admitted = set()
    while True:
        ready = {
            name
            for name, row in modules.items()
            if (set(row["requires"]) | set(row["optional_requires"])) <= admitted
        }
        if ready <= admitted:
            break
        admitted |= ready
    if admitted != modules.keys():
        failures.append(
            "cyclic or unresolved module graph: "
            + ", ".join(sorted(modules.keys() - admitted))
        )
    for source in (root / "core/src", root / "api/src"):
        for path in source.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                imported = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom) and not node.level
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                for value in imported:
                    if (
                        source == root / "api/src"
                        and (value or "").split(".")[0] == "workbench_core"
                    ):
                        failures.append(
                            f"{path.relative_to(root)}:{node.lineno}: API imports Core"
                        )
                    if (value or "").split(".")[0] in owners:
                        failures.append(
                            f"{path.relative_to(root)}:{node.lineno}: Core/API imports a product module"
                        )
    for source in (
        root / "core/src",
        root / "api/src",
        *root.glob("modules/*/src"),
        *root.glob("profiles/*/*/src"),
    ):
        for path in source.rglob("*.py"):
            if any(
                re.search(r"_v\d+$", part.removesuffix(".py"))
                for part in path.relative_to(source).parts
            ):
                failures.append(
                    f"{path.relative_to(root)}: source names must be stable; versions belong in metadata"
                )
    for source in root.glob("profiles/*/*/src"):
        for path in source.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                imported = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom) and not node.level
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                if any(
                    (value or "").split(".")[0] in {"workbench_shell", "workbench_core"}
                    for value in imported
                ):
                    failures.append(
                        f"{path.relative_to(root)}:{node.lineno}: native profiles must consume domain contracts, not Shell/Core implementations"
                    )
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        failures = check()
        print(
            "\n".join(failures)
            if failures
            else "Native metadata and module dependency graph: PASS"
        )
        return int(bool(failures))
    print(json.dumps(inventory(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
