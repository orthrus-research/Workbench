"""Thin public adapters for the D01 and F01 journey owners."""

from __future__ import annotations

from workbench_crucible.runtime_pair import FeatureRuntimePairPorts, runtime_pair_owner

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence, TextIO

from .cleanroom_dev_loop import (
    CleanroomDevLoopError,
    execute_cleanroom_dev_loop,
    plan_cleanroom_dev_loop,
    recover_cleanroom_dev_loop,
    validate_cleanroom_dev_loop_plan,
)
from .feature_change_workspace import (
    FeatureChangeWorkspaceError,
    apply_feature_change,
    bind_material_fluid_recipe_session_context,
    close_material_fluid_recipe_session_context,
    open_feature_change,
    recover_feature_change,
    resolve_material_fluid_recipe_session_context,
    rollback_feature_change,
    run_feature_change_matrix,
    select_material_fluid_recipe_session_context,
    start_material_fluid_recipe_change,
    verify_feature_change,
)
from workbench_api.state_paths import default_product_spine_state_root


MAX_RECORD_BYTES = 64 * 1024 * 1024


class GoldenJourneyCliV2Error(RuntimeError):
    """A public journey adapter could not call its exact owner safely."""


def _json(value: Any, output: TextIO) -> None:
    output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _state(root: Path, supplied: Path | None) -> Path:
    if supplied is None:
        return default_product_spine_state_root(root)
    candidate = supplied.expanduser()
    if candidate.is_symlink():
        raise GoldenJourneyCliV2Error("state root cannot be a symbolic link")
    return candidate.resolve()


def _installed_supersymmetry_runtime_ports(
    root: Path,
    config_path: Path,
) -> FeatureRuntimePairPorts:
    from .runtime_composition import material_fluid_construction_ports, installed_runtime_services

    owner = runtime_pair_owner("supersymmetry")
    factory = getattr(owner, "runtime_pair_from_config", None)
    if not callable(factory):
        raise GoldenJourneyCliV2Error("Supersymmetry runtime pair factory is incomplete")
    ports = factory(config_path, construction=material_fluid_construction_ports(), services=installed_runtime_services())
    if not isinstance(ports, FeatureRuntimePairPorts):
        raise GoldenJourneyCliV2Error("Supersymmetry runtime owner returned the wrong port")
    return ports


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise GoldenJourneyCliV2Error(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= MAX_RECORD_BYTES
    ):
        raise GoldenJourneyCliV2Error(f"{label} is not a bounded ordinary file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(MAX_RECORD_BYTES + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise GoldenJourneyCliV2Error(f"cannot read {label}") from exc
    identity = lambda row: (
        row.st_dev,
        row.st_ino,
        row.st_mode,
        row.st_nlink,
        row.st_size,
        row.st_mtime_ns,
        row.st_ctime_ns,
    )
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or identity(metadata) != identity(before)
        or identity(before) != identity(after)
        or len(raw) != before.st_size
    ):
        raise GoldenJourneyCliV2Error(f"{label} changed while it was read")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key {key!r}")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise GoldenJourneyCliV2Error(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise GoldenJourneyCliV2Error(f"{label} is not one JSON object")
    return value


def _write_fresh_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_RECORD_BYTES:
        raise GoldenJourneyCliV2Error("journey plan exceeds its record bound")
    destination = path.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        parent = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError as exc:
        raise GoldenJourneyCliV2Error("journey plan output must be a fresh file") from exc


def _fixture_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workbench dev fixture")
    subparsers = parser.add_subparsers(dest="action", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--gradle-cmd", type=Path, required=True)
    plan.add_argument("--java-home", type=Path, required=True)
    plan.add_argument("--state-root", type=Path)
    plan.add_argument("--side", choices=("client", "server", "both"), default="both")
    plan.add_argument("--debug", action="store_true")
    plan.add_argument("--output", type=Path)
    plan.add_argument("--json", action="store_true")
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path)
    run.add_argument("--gradle-cmd", type=Path)
    run.add_argument("--java-home", type=Path)
    run.add_argument("--state-root", type=Path)
    run.add_argument("--side", choices=("client", "server", "both"), default="both")
    run.add_argument("--debug", action="store_true")
    run.add_argument("--json", action="store_true")
    recover = subparsers.add_parser("recover")
    recover.add_argument("receipt", type=Path)
    recover.add_argument("--json", action="store_true")
    return parser


def _render_fixture(value: Mapping[str, Any], output: TextIO) -> None:
    if "plan_id" in value:
        output.write(f"Cleanroom fixture plan {value['plan_id']} — {value.get('state', 'ready')}\n")
    elif "receipt_path" in value:
        output.write(f"Cleanroom fixture run — {value['outcome']}\nReceipt: {value['receipt_path']}\n")
    else:
        output.write(f"Cleanroom fixture recovery — {value.get('state', 'unknown')}\n")


def dev_fixture_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    args = _fixture_parser().parse_args(list(argv))
    try:
        if args.action == "plan":
            sides = ("client", "server") if args.side == "both" else (args.side,)
            value = plan_cleanroom_dev_loop(
                root,
                gradle_cmd=args.gradle_cmd,
                java_home=args.java_home,
                state_root=_state(root, args.state_root),
                sides=sides,
                debug=args.debug,
            )
            if args.output is not None:
                _write_fresh_json(args.output, value)
        elif args.action == "run":
            direct_inputs = args.gradle_cmd is not None or args.java_home is not None
            if args.plan is not None and direct_inputs:
                raise GoldenJourneyCliV2Error(
                    "--plan cannot be combined with --gradle-cmd or --java-home"
                )
            if args.plan is not None:
                plan = validate_cleanroom_dev_loop_plan(
                    _read_json(args.plan, "Cleanroom dev-loop plan")
                )
            else:
                if args.gradle_cmd is None or args.java_home is None:
                    raise GoldenJourneyCliV2Error(
                        "fixture run requires --plan or both --gradle-cmd and --java-home"
                    )
                sides = (
                    ("client", "server") if args.side == "both" else (args.side,)
                )
                plan = plan_cleanroom_dev_loop(
                    root,
                    gradle_cmd=args.gradle_cmd,
                    java_home=args.java_home,
                    state_root=_state(root, args.state_root),
                    sides=sides,
                    debug=args.debug,
                )
            value = execute_cleanroom_dev_loop(root, plan)
        elif args.action == "recover":
            value = recover_cleanroom_dev_loop(args.receipt)
        else:  # pragma: no cover
            raise GoldenJourneyCliV2Error("unsupported fixture action")
        if args.json:
            _json(value, output)
        else:
            _render_fixture(value, output)
        if args.action == "run" and value.get("outcome") != "passed":
            return 1
        return 0
    except BrokenPipeError:
        raise
    except (CleanroomDevLoopError, GoldenJourneyCliV2Error, OSError, ValueError) as exc:
        error.write(f"Workbench dev fixture failed: {exc}\n")
        return 2


def _change_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workbench change")
    families = parser.add_subparsers(dest="family", required=True)
    material = families.add_parser("material-fluid-recipe")
    actions = material.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start")
    start.add_argument("workspace", type=Path, nargs="?")
    start.add_argument("--state-root", type=Path)
    start.add_argument(
        "--session-record",
        type=Path,
        help="immutable Work Session V2 record accepted once at context setup",
    )
    start.add_argument(
        "--runtime-config",
        type=Path,
        help="installed profile runtime configuration accepted once at context setup",
    )
    for key in ("name", "color", "translation", "symbol", "recipe_script", "recipe_map", "input_fluid", "voltage_tier"):
        start.add_argument("--" + key.replace("_", "-"), required=True)
    for key in ("input_amount", "output_amount", "duration"):
        start.add_argument("--" + key.replace("_", "-"), type=int, required=True)
    start.add_argument("--json", action="store_true")
    for action in ("open", "test", "apply", "verify", "rollback", "recover"):
        child = actions.add_parser(action)
        child.add_argument("change_id", nargs="?")
        child.add_argument("--state-root", type=Path)
        if action in {"test", "verify", "rollback"}:
            child.add_argument(
                "--runtime-config",
                type=Path,
                help=(
                    "profile-owned URI-only installed client/server runtime "
                    "configuration"
                ),
            )
        if action == "apply":
            child.add_argument("--consent-plan-id")
        child.add_argument("--json", action="store_true")
    select = actions.add_parser("select-context")
    select.add_argument("session_id")
    select.add_argument("--json", action="store_true")
    close = actions.add_parser("close-context")
    close.add_argument("--json", action="store_true")
    return parser


def _render_change(value: Mapping[str, Any], output: TextIO) -> None:
    identity = value.get("change_id") or value.get("matrix_id") or value.get("plan_id")
    state = value.get("lifecycle") or value.get("state") or value.get("outcome")
    output.write(f"Feature change {identity or 'result'} — {state or 'retained'}\n")


def change_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
    runtime_ports: FeatureRuntimePairPorts | None = None,
) -> int:
    arguments = list(argv)
    if (
        len(arguments) >= 2
        and arguments[0] in {
            "start", "open", "test", "apply", "verify", "rollback", "recover",
            "select-context", "close-context",
        }
        and arguments[1] == "material-fluid-recipe"
    ):
        arguments = [arguments[1], arguments[0], *arguments[2:]]
    args = _change_parser().parse_args(arguments)
    try:
        context = None
        if args.action == "select-context":
            value = select_material_fluid_recipe_session_context(
                root, args.session_id
            )
            if args.json:
                _json(value, output)
            else:
                _render_change(value, output)
            return 0
        if args.action == "close-context":
            value = close_material_fluid_recipe_session_context(root)
            if args.json:
                _json(value, output)
            else:
                _render_change(value, output)
            return 0
        if args.action == "start" and args.session_record is not None:
            if (
                args.workspace is not None
                or args.state_root is not None
                or args.runtime_config is None
            ):
                raise GoldenJourneyCliV2Error(
                    "Work Session setup accepts --session-record and --runtime-config "
                    "without re-entering workspace or state paths"
                )
            value = bind_material_fluid_recipe_session_context(
                root,
                args.session_record,
                args.runtime_config,
                name=args.name,
                color=args.color,
                translation=args.translation,
                symbol=args.symbol,
                recipe_script=args.recipe_script,
                recipe_map=args.recipe_map,
                input_fluid=args.input_fluid,
                input_amount=args.input_amount,
                output_amount=args.output_amount,
                duration=args.duration,
                voltage_tier=args.voltage_tier,
            )
            if args.json:
                _json(value, output)
            else:
                _render_change(value, output)
            return 0
        if args.action == "start":
            if args.workspace is None or args.runtime_config is not None:
                raise GoldenJourneyCliV2Error(
                    "legacy start requires a workspace and does not accept --runtime-config"
                )
            state = _state(root, args.state_root)
        elif args.change_id is None:
            if args.state_root is not None:
                raise GoldenJourneyCliV2Error(
                    "current Work Session actions do not accept a state path"
                )
            context, state, context_runtime = (
                resolve_material_fluid_recipe_session_context(root)
            )
            args.change_id = context["change_id"]
            if args.action == "apply":
                if args.consent_plan_id is not None:
                    raise GoldenJourneyCliV2Error(
                        "current Work Session apply derives exact consent from its immutable context"
                    )
                args.consent_plan_id = context["plan_id"]
            if args.action in {"test", "verify", "rollback"}:
                if args.runtime_config is not None:
                    raise GoldenJourneyCliV2Error(
                        "current Work Session actions do not accept a runtime path"
                    )
                args.runtime_config = context_runtime
        else:
            state = _state(root, args.state_root)
            if args.action == "apply" and args.consent_plan_id is None:
                raise GoldenJourneyCliV2Error(
                    "explicit feature change apply requires --consent-plan-id"
                )
        selected_runtime_ports = runtime_ports
        runtime_config = getattr(args, "runtime_config", None)
        if runtime_config is not None:
            if runtime_ports is not None:
                raise GoldenJourneyCliV2Error(
                    "--runtime-config cannot replace an injected runtime owner"
                )
            selected_runtime_ports = _installed_supersymmetry_runtime_ports(
                root, runtime_config
            )
        if args.action == "start":
            assert args.workspace is not None
            value = start_material_fluid_recipe_change(
                root,
                args.workspace,
                state,
                name=args.name,
                color=args.color,
                translation=args.translation,
                symbol=args.symbol,
                recipe_script=args.recipe_script,
                recipe_map=args.recipe_map,
                input_fluid=args.input_fluid,
                input_amount=args.input_amount,
                output_amount=args.output_amount,
                duration=args.duration,
                voltage_tier=args.voltage_tier,
            )
        elif args.action == "open":
            value = open_feature_change(state, args.change_id)
        elif args.action in {"test", "verify"}:
            if selected_runtime_ports is None:
                raise GoldenJourneyCliV2Error(
                    "feature change requires --runtime-config for the installed "
                    "client and dedicated-server runtime owner"
                )
            if args.action == "test":
                value = run_feature_change_matrix(
                    root, state, args.change_id, ports=selected_runtime_ports
                )
            else:
                value = verify_feature_change(
                    root, state, args.change_id, ports=selected_runtime_ports
                )
        elif args.action == "apply":
            value = apply_feature_change(
                root,
                state,
                args.change_id,
                consent_plan_id=args.consent_plan_id,
            )
        elif args.action == "rollback":
            value = rollback_feature_change(
                root, state, args.change_id, ports=selected_runtime_ports
            )
        elif args.action == "recover":
            value = recover_feature_change(state, args.change_id)
        else:  # pragma: no cover
            raise GoldenJourneyCliV2Error("unsupported feature-change action")
        if args.json:
            _json(value, output)
        else:
            _render_change(value, output)
        return 0
    except BrokenPipeError:
        raise
    except (
        FeatureChangeWorkspaceError,
        GoldenJourneyCliV2Error,
        OSError,
        ValueError,
    ) as exc:
        error.write(f"Workbench change failed: {exc}\n")
        return 2


__all__ = [
    "GoldenJourneyCliV2Error",
    "change_main",
    "dev_fixture_main",
]
