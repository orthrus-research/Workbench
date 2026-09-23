#!/usr/bin/env python3

"""Inspect the independent public component release authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import component_versions  # noqa: E402
import release_track  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate authority and version projections")
    subparsers.add_parser("show", help="show the complete public release authority")
    component = subparsers.add_parser("component", help="show one component authority")
    component.add_argument("component_id")
    tag = subparsers.add_parser("tag", help="render one namespaced component tag")
    tag.add_argument("component_id")
    tag.add_argument("--candidate", type=int)
    artifact = subparsers.add_parser("artifact", help="render one component artifact name")
    artifact.add_argument("artifact_id")
    github = subparsers.add_parser(
        "github-output", help="emit one component's namespaced workflow outputs"
    )
    github.add_argument("component_id")
    github.add_argument("--candidate", type=int)
    return parser


def _render_tag(component: dict[str, Any], candidate: int | None) -> str:
    if candidate is not None and candidate < 1:
        raise component_versions.ComponentVersionError(
            "candidate number must be positive"
        )
    key = "candidate" if candidate is not None else "final"
    return component["tags"][key].format(
        version=component["version"], candidate=candidate
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        authority, components = component_versions.load_authority()
        failures = component_versions.check_projections(components)
        if failures:
            raise component_versions.ComponentVersionError(
                "version projection drift:\n  " + "\n  ".join(failures)
            )
        if arguments.command == "validate":
            print(
                "public component release authority valid: "
                + ", ".join(
                    f"{identifier}={component['version']}"
                    for identifier, component in components.items()
                )
            )
        elif arguments.command == "show":
            print(json.dumps(authority, ensure_ascii=False, indent=2, sort_keys=True))
        elif arguments.command == "component":
            print(
                json.dumps(
                    components[arguments.component_id],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        elif arguments.command == "tag":
            print(_render_tag(components[arguments.component_id], arguments.candidate))
        elif arguments.command == "artifact":
            print(release_track.artifact_filename(ROOT, arguments.artifact_id))
        elif arguments.command == "github-output":
            component = components[arguments.component_id]
            print(f"component={component['id']}")
            print(f"kind={component['kind']}")
            print(f"version={component['version']}")
            print(f"tag={_render_tag(component, arguments.candidate)}")
            print(f"release_track={authority['package']['release_track']}")
            print(f"release_descriptor_id={authority['release_descriptor_id']}")
            for artifact in component["artifacts"]:
                key = artifact["id"].replace("-", "_").replace(".", "_")
                filename = artifact["filename_template"].format(
                    version=component["version"]
                )
                print(f"artifact_{key}={filename}")
        return 0
    except (
        component_versions.ComponentVersionError,
        release_track.ReleaseTrackError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
    ) as exc:
        print(f"release authority error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
