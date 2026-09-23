#!/usr/bin/env python3

"""Canonical command-line adapter for the deterministic Blueprints core."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, NoReturn, TextIO

from workbench_blueprints import interface, lifecycle, planner, simulation, standards
from workbench_blueprints.layout import WORKBENCH_ROOT


REPO_ROOT = WORKBENCH_ROOT


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise interface.InterfaceDiagnostic(
            "BPI100_USAGE", "/arguments", message, exit_code=2
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="blueprints",
        description="Workbench Blueprints construction lifecycle",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="one protected .workbench/blueprints session directory",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="capture target and initialize")
    init.add_argument("--target-repository", type=Path, required=True)
    init.add_argument("--repository-id", required=True)
    intake = init.add_mutually_exclusive_group(required=True)
    intake.add_argument("--intake", type=Path)
    intake.add_argument("--feature-family")
    init.add_argument("--sequence", type=int, default=0)
    init.add_argument(
        "--operation", choices=("create", "update", "reconcile")
    )
    init.add_argument("--target-key")
    init.add_argument("--desired-outcome")
    init.add_argument(
        "--parameter",
        action="append",
        default=[],
        metavar="NAME=JSON",
    )
    init.add_argument("--variant", action="append", default=[])
    init.add_argument(
        "--output-mode",
        choices=("instructions", "patch-bundle", "direct-apply"),
    )
    init.add_argument(
        "--accept-compliant-revision", action="store_true"
    )
    init.add_argument("--allow-direct-apply", action="store_true")
    init.add_argument(
        "--registry-root",
        type=Path,
        required=True,
    )
    init.add_argument("--asset-root", type=Path, default=REPO_ROOT)
    init.add_argument(
        "--ledger",
        type=Path,
        required=True,
    )

    plan = commands.add_parser("plan", help="select and synthesize")
    plan.add_argument("--planning-evidence", type=Path, required=True)
    plan.add_argument("--choices", type=Path)

    simulate = commands.add_parser(
        "simulate", help="execute isolated validation"
    )
    simulate.add_argument("--environment-lock", type=Path, required=True)
    simulate.add_argument(
        "--dependency-source",
        type=Path,
        help="local dependency files keyed by locked dependency id",
    )

    commands.add_parser("generate", help="release the passing candidate")
    commands.add_parser("apply", help="apply a consented direct release")
    commands.add_parser("verify", help="verify the applied target")
    commands.add_parser("history", help="record and return local history")
    commands.add_parser(
        "export-proof", help="export an admitted portable proof"
    )
    return parser


def _domain_diagnostic(exc: Exception) -> interface.InterfaceDiagnostic:
    if isinstance(exc, interface.InterfaceDiagnostic):
        return exc
    if isinstance(
        exc,
        (
            planner.PlannerDiagnostic,
            simulation.SimulationDiagnostic,
            lifecycle.LifecycleDiagnostic,
            standards.StandardDiagnostic,
        ),
    ):
        return interface.InterfaceDiagnostic(
            exc.code, exc.location, exc.message, exit_code=4
        )
    return interface.InterfaceDiagnostic(
        "BPI199_INTERNAL",
        "/",
        f"unexpected {type(exc).__name__}",
        exit_code=6,
    )


def _merged_adapters(
    base: interface.AdapterSet | None,
    dependency_source: Path | None,
) -> interface.AdapterSet:
    value = interface.AdapterSet() if base is None else copy.copy(base)
    value.post_checks = dict(value.post_checks)
    if dependency_source is not None:
        if value.dependency_provider is not None:
            raise interface.InterfaceDiagnostic(
                "BPI113_ADAPTER_CONFLICT",
                "/dependency-source",
                "dependency provider was supplied twice",
                exit_code=2,
            )
        value.dependency_provider = interface.dependency_directory_provider(
            dependency_source
        )
    return value


def _inline_intake(arguments: argparse.Namespace) -> dict[str, Any]:
    required = {
        "--operation": arguments.operation,
        "--target-key": arguments.target_key,
        "--desired-outcome": arguments.desired_outcome,
        "--output-mode": arguments.output_mode,
    }
    missing = [flag for flag, value in required.items() if value is None]
    if missing:
        raise interface.InterfaceDiagnostic(
            "BPI100_USAGE",
            "/arguments",
            "inline intake requires " + ", ".join(missing),
            exit_code=2,
        )
    parameters = []
    names: set[str] = set()
    for index, definition in enumerate(arguments.parameter):
        name, separator, encoded = definition.partition("=")
        if (
            separator != "="
            or not name
            or name in names
        ):
            raise interface.InterfaceDiagnostic(
                "BPI100_USAGE",
                f"/parameter/{index}",
                "parameters must be unique NAME=JSON values",
                exit_code=2,
            )
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise interface.InterfaceDiagnostic(
                "BPI100_USAGE",
                f"/parameter/{index}",
                f"parameter value is not JSON: {exc.msg}",
                exit_code=2,
            ) from exc
        names.add(name)
        parameters.append({"name": name, "value": value})
    return {
        "sequence": arguments.sequence,
        "feature_family": arguments.feature_family,
        "intent": {
            "operation": arguments.operation,
            "target_key": arguments.target_key,
            "desired_outcome": arguments.desired_outcome,
        },
        "parameters": parameters,
        "requested_variants": arguments.variant,
        "output_mode": arguments.output_mode,
        "consent": {
            "accept_compliant_revision": (
                arguments.accept_compliant_revision
            ),
            "allow_direct_apply": arguments.allow_direct_apply,
        },
    }


def _execute(
    arguments: argparse.Namespace,
    adapters: interface.AdapterSet | None,
) -> dict[str, Any]:
    dependency_source = getattr(arguments, "dependency_source", None)
    core = interface.BlueprintsCore(
        arguments.workspace,
        adapters=_merged_adapters(adapters, dependency_source),
    )
    if arguments.command == "init":
        intake = (
            interface.load_json(arguments.intake)
            if arguments.intake is not None
            else _inline_intake(arguments)
        )
        return core.init(
            target_repository=arguments.target_repository,
            repository_id=arguments.repository_id,
            registry_root=arguments.registry_root,
            asset_root=arguments.asset_root,
            ledger_path=arguments.ledger,
            intake=intake,
        )
    if arguments.command == "plan":
        choices = (
            None
            if arguments.choices is None
            else interface.load_json(arguments.choices)
        )
        return core.plan(
            interface.load_json(arguments.planning_evidence),
            choices=choices,
        )
    if arguments.command == "simulate":
        return core.simulate(interface.load_json(arguments.environment_lock))
    if arguments.command == "generate":
        return core.generate()
    if arguments.command == "apply":
        return core.apply()
    if arguments.command == "verify":
        return core.verify()
    if arguments.command == "history":
        return core.history()
    if arguments.command == "export-proof":
        return core.export_proof()
    raise interface.InterfaceDiagnostic(
        "BPI100_USAGE",
        "/command",
        "unknown command",
        exit_code=2,
    )


def main(
    argv: list[str] | None = None,
    *,
    adapters: interface.AdapterSet | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one CLI command; injectable streams/adapters support exact tests."""

    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    command = "init"
    supplied = sys.argv[1:] if argv is None else argv
    command = next(
        (token for token in supplied if token in interface.COMMANDS),
        command,
    )
    workspace: Path | None = None
    try:
        arguments = _parser().parse_args(argv)
        command = arguments.command
        workspace = arguments.workspace
        result = _execute(arguments, adapters)
    except Exception as exc:
        diagnostic = _domain_diagnostic(exc)
        run = None
        if workspace is not None:
            try:
                run = interface.SessionStore(workspace).load()["run"]
            except Exception:
                pass
        result = interface.rejected_result(command, diagnostic, run=run)
        err.write(standards.canonical_json(result) + "\n")
        return diagnostic.exit_code
    out.write(standards.canonical_json(result) + "\n")
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
