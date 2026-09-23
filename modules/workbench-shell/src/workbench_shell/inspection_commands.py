"""Shell-owned inspection commands."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile


from workbench_api.resources import repository_root

ROOT = repository_root(__file__)

from .command_context import _configured_workspace_default


def _doctor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench doctor",
        description=(
            "Read the exact workspace, Cleanroom, pack, toolchain, and bounded "
            "runtime state without building, provisioning, or launching Minecraft."
        ),
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        default=_configured_workspace_default(ROOT),
        help=(
            "workspace or subproject to inspect (defaults to the saved setup "
            "workspace, or this Workbench checkout when setup is absent)"
        ),
    )
    parser.add_argument(
        "--capability",
        choices=("workspace-context", "worldgen-dev"),
        help="operation to assess; selecting a pack profile implies worldgen-dev in V1",
    )
    parser.add_argument(
        "--profile",
        help="explicit pack profile name; Supersymmetry is never selected universally",
    )
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--runtime-template", type=Path)
    parser.add_argument("--strata-root", type=Path)
    parser.add_argument("--java-cmd")
    parser.add_argument("--gradle-cmd")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete V1 JSON report instead of the concise view",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="also write the JSON report atomically to this explicit path",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also return exit 1 when warnings leave the target at attention",
    )
    return parser

def _merge_worldgen_report(
    report: dict[str, object], fragment: dict[str, object]
) -> None:
    from workbench_project_intelligence.workspace_doctor import (
        merge_worldgen_capability_report,
    )

    merge_worldgen_capability_report(report, fragment)

def _worldgen_doctor_report(
    workspace: Path,
    *,
    profile: str | None = None,
    profile_file: Path | None = None,
    runtime_template: Path | None = None,
    strata_root: Path | None = None,
    java_cmd: str | None = None,
    gradle_cmd: str | None = None,
) -> dict[str, object]:
    """Compose Crucible preflight evidence into the Shell-owned Doctor report."""

    from workbench_crucible_worldgen_iteration.doctor import (
        inspect_worldgen_development_target,
    )
    from workbench_project_intelligence.workspace_doctor import (
        WorkspaceDoctorError,
        new_report,
    )

    fragment = inspect_worldgen_development_target(
        ROOT,
        profile_name=profile,
        profile_file=profile_file,
        runtime_template=runtime_template,
        strata_root=strata_root,
        java_cmd=java_cmd,
        gradle_cmd=gradle_cmd,
    )
    requested_workspace = workspace.expanduser().resolve(strict=True)
    selected_workspace = fragment.get("workspace") or requested_workspace
    selected_path = Path(selected_workspace).resolve(strict=True)
    if requested_workspace != ROOT.resolve() and not (
        requested_workspace == selected_path
        or selected_path in requested_workspace.parents
    ):
        raise WorkspaceDoctorError(
            "the requested workspace is outside the selected worldgen profile "
            f"fixture: requested {requested_workspace}; target {selected_path}"
        )
    report = new_report(selected_path, requested_path=requested_workspace)
    _merge_worldgen_report(report, fragment)
    return report

def _write_doctor_report(path: Path, report: dict[str, object]) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"doctor output cannot be a symlink: {requested}")
    destination = requested.resolve()
    if destination.exists():
        raise ValueError(
            f"doctor output already exists; choose a fresh path: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError as exc:
            raise ValueError(
                f"doctor output already exists; choose a fresh path: {destination}"
            ) from exc
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return destination

def _doctor_main(argv: list[str]) -> int:
    from workbench_project_intelligence.workspace_doctor import (
        WorkspaceDoctorError,
        new_report,
        render_workspace_doctor_report,
    )

    parser = _doctor_parser()
    args = parser.parse_args(argv)
    if args.profile and args.profile_file:
        parser.error("--profile and --profile-file are mutually exclusive")
    capability = args.capability or (
        "worldgen-dev" if args.profile or args.profile_file else "workspace-context"
    )
    try:
        if capability == "worldgen-dev":
            report = _worldgen_doctor_report(
                args.workspace,
                profile=args.profile,
                profile_file=args.profile_file,
                runtime_template=args.runtime_template,
                strata_root=args.strata_root,
                java_cmd=args.java_cmd,
                gradle_cmd=args.gradle_cmd,
            )
        else:
            if any(
                value is not None
                for value in (
                    args.profile,
                    args.profile_file,
                    args.runtime_template,
                    args.strata_root,
                    args.java_cmd,
                    args.gradle_cmd,
                )
            ):
                parser.error(
                    "profile, runtime, and toolchain options require --capability worldgen-dev"
                )
            report = new_report(args.workspace, requested_path=args.workspace)
        if args.output:
            written = _write_doctor_report(args.output, report)
        else:
            written = None
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            sys.stdout.write(render_workspace_doctor_report(report))
            if written:
                print(f"Report written: {written}")
        status = report["summary"]["status"]
        return 1 if status == "blocked" or (args.strict and status == "attention") else 0
    except (OSError, ValueError, WorkspaceDoctorError) as exc:
        print(f"Workbench doctor failed: {exc}", file=sys.stderr)
        return 2
