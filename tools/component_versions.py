#!/usr/bin/env python3
"""Read independent versions from their owning native package manifests.

There is deliberately no sync operation: release metadata is a derived view,
never an authority that writes package versions back into source manifests.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tomllib
from xml.etree import ElementTree

from module_packages import inventory

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)[1-9][0-9]*)?$")


class ComponentVersionError(ValueError):
    """A native authority or its necessary generated projection is invalid."""


def _content_id(kind, value, field):
    projection = deepcopy(value)
    projection.pop(field, None)
    raw = (json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    return f"{kind}:sha256:" + hashlib.sha256(raw).hexdigest()


def load_authority(root: Path | None = None):
    root = ROOT if root is None else root
    rows = inventory(root)
    components = {}
    for row in rows:
        identifier = row["distribution"]
        if identifier in components:
            raise ComponentVersionError(f"duplicate native distribution: {identifier}")
        project = tomllib.loads((root / row["path"]).read_text())["project"]
        components[identifier] = {
            "id": identifier, "version": row["version"], "kind": "python",
            "manifest": row["path"], "dependencies": row["dependencies"],
            "python_distribution_version": row["version"],
            "artifacts": [{"id": identifier + ".wheel", "filename_template": identifier.replace("-", "_") + "-{version}-py3-none-any.whl"}],
        }
        if project.get("license") != "LGPL-3.0-only" or project.get("license-files") != ["LICENSE", "NOTICE.md"]:
            raise ComponentVersionError(f"{identifier}: license metadata differs")
        for name in ("LICENSE", "NOTICE.md"):
            if (root / row["path"]).parent.joinpath(name).read_bytes() != (root / name).read_bytes():
                raise ComponentVersionError(f"{identifier}: {name} must match the repository notice")
    vscode = json.loads((root / "clients/vscode/package.json").read_text())
    gradle = (root / "clients/intellij-community/build.gradle.kts").read_text()
    matches = re.findall(r'^version\s*=\s*"([^"]+)"', gradle, re.MULTILINE)
    if len(matches) != 1:
        raise ComponentVersionError("IntelliJ requires one native Gradle version")
    for identifier, version, manifest, suffix, artifact in (
        ("workbench-vscode", vscode["version"], "clients/vscode/package.json", ".vsix", "vsix"),
        ("workbench-intellij-community", matches[0], "clients/intellij-community/build.gradle.kts", ".zip", "plugin-zip"),
    ):
        components[identifier] = {"id": identifier, "version": version, "kind": "client", "manifest": manifest,
                                  "artifacts": [{"id": identifier + "." + artifact, "filename_template": identifier + "-{version}" + suffix}]}
    engine_manifest = "modules/axiom/jvm/build.gradle.kts"
    engine_versions = re.findall(r'^version\s*=\s*"([^"]+)"', (root / engine_manifest).read_text(), re.MULTILINE)
    if len(engine_versions) != 1:
        raise ComponentVersionError("Axiom requires one native Gradle version")
    components["workbench-axiom-engine"] = {
        "id": "workbench-axiom-engine", "version": engine_versions[0], "kind": "jvm",
        "manifest": engine_manifest,
        "artifacts": [{"id": "workbench-axiom-engine.distribution", "filename_template": "workbench-axiom-engine-{version}.zip"}],
    }
    for identifier, component in components.items():
        if not SEMVER.fullmatch(component["version"]):
            raise ComponentVersionError(f"{identifier}: expected a semantic native version")
        component["tags"] = {"candidate": identifier + "/v{version}-rc.{candidate}", "final": identifier + "/v{version}"}
    authority = {"format": "workbench-native-release-v1", "schema_version": 1,
                 "repository": {"organization": "Orthrus Research", "name": "Workbench", "slug": "orthrus-research/workbench"},
                 "package": {"channel": "public", "release_track": "public-v1"}, "components": list(components.values())}
    authority["release_descriptor_id"] = _content_id("workbench-native-release", authority, "release_descriptor_id")
    return authority, components


def check_projections(components, root: Path | None = None):
    root = ROOT if root is None else root
    failures = []
    package_lock = json.loads((root / "clients/vscode/package-lock.json").read_text())
    version = components["workbench-vscode"]["version"]
    if package_lock.get("version") != version or package_lock.get("packages", {}).get("", {}).get("version") != version:
        failures.append("VS Code package-lock differs from package.json; regenerate its lock")
    # Gradle owns the version; the descriptor is patched when building the IDE plugin.
    xml = ElementTree.fromstring((root / "clients/intellij-community/src/main/resources/META-INF/plugin.xml").read_text())
    if xml.find("version") is not None:
        failures.append("IntelliJ source plugin.xml must not duplicate the Gradle version")
    if "project" in tomllib.loads((root / "pyproject.toml").read_text()):
        failures.append("workspace root must not define another distribution")
    from packaging.specifiers import SpecifierSet
    contract = json.loads((root / "modules/axiom/src/workbench_axiom/engine-contract.json").read_text())
    if components["workbench-axiom-engine"]["version"] not in SpecifierSet(contract["engineVersions"]):
        failures.append("Axiom integration does not admit its native engine version")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "show"))
    arguments = parser.parse_args(argv)
    try:
        authority, components = load_authority()
        failures = check_projections(components)
        if failures:
            raise ComponentVersionError("; ".join(failures))
        print(json.dumps(authority, indent=2, sort_keys=True) if arguments.command == "show" else f"Native version authorities: PASS ({len(components)} components)")
        return 0
    except (ComponentVersionError, OSError, ValueError) as exc:
        print(f"component versions: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
