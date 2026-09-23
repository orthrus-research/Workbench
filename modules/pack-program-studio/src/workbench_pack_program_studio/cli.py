"""CLI for GroovyScript Pack Program Studio's working vertical slices."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
from tempfile import NamedTemporaryFile
import threading
from typing import Any, Callable, Mapping, TextIO

from .analyzer import AnalysisContext
from .language_profile import resolve_language_profile
from .language_render import render_language_result
from .language_service import build_language_service_result
from .ide_bridge import proxy_descriptor_stdio
from .managed_profile import resolve_managed_session_profile
from .managed_render import render_session_event, render_session_receipt
from .managed_session import run_managed_language_session
from .model import PackProgramError
from .profile import load_profile, resolve_named_profile
from .render import render_report
from .studio import build_report


def build_parser(*, prog: str = "workbench groovy") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Inspect an exact staged GroovyScript pack program, query its upstream "
            "compiler, or supervise an explicitly requested disposable client session."
        ),
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    dev = commands.add_parser(
        "dev",
        help="build a source-linked static program/effect report and reload recommendation",
    )
    profile = dev.add_mutually_exclusive_group(required=True)
    profile.add_argument(
        "--profile",
        help="explicit named pack adapter; currently supersymmetry",
    )
    profile.add_argument(
        "--profile-file",
        type=Path,
        help="explicit workbench-groovy-pack-profile-v1 JSON",
    )
    dev.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="pack root or Groovy root to inspect (defaults to current directory)",
    )
    dev.add_argument(
        "--baseline",
        type=Path,
        help="optional exact baseline pack/Groovy root for semantic comparison",
    )
    dev.add_argument(
        "--side",
        choices=("dedicated-server", "integrated-server", "client"),
        default="dedicated-server",
        help="exact runtime side used to evaluate source preprocessors",
    )
    dev.add_argument(
        "--packmode",
        help="effective packmode; defaults to runConfig when declared",
    )
    dev.add_argument(
        "--debug-state",
        choices=("auto", "on", "off"),
        default="auto",
        help="effective GroovyScript debug state (default: runConfig)",
    )
    dev.add_argument(
        "--mod",
        action="append",
        default=[],
        help="installed mod ID for mods_loaded evaluation; repeatable; omit to preserve uncertainty",
    )
    dev.add_argument(
        "--changed",
        action="append",
        default=None,
        help="changed path relative to the Groovy root; repeatable; otherwise derived from --baseline",
    )
    dev.add_argument(
        "--groovy-log",
        type=Path,
        help="optional exact groovy.log/groovy_server.log observation",
    )
    dev.add_argument(
        "--runtime-diagnosis",
        type=Path,
        help="optional Workbench runtime diagnosis JSON to correlate without causal attribution",
    )
    dev.add_argument(
        "--json",
        action="store_true",
        help="emit the complete source-linked V1 JSON report",
    )
    dev.add_argument(
        "--recipe-review",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    dev.add_argument(
        "--verbose",
        action="store_true",
        help="include full program, lifecycle, and provenance detail",
    )
    dev.add_argument(
        "--output",
        type=Path,
        help="also write the complete JSON report atomically to a fresh path",
    )
    dev.add_argument(
        "--strict",
        action="store_true",
        help="return exit 1 when the report requires attention",
    )
    check = commands.add_parser(
        "check",
        help="check exact in-memory source through a live GroovyScript 1.4.3 language server",
    )
    check_profile = check.add_mutually_exclusive_group(required=True)
    check_profile.add_argument(
        "--profile",
        help="explicit named pack adapter; currently supersymmetry",
    )
    check_profile.add_argument(
        "--profile-file",
        type=Path,
        help="explicit workbench-groovy-pack-profile-v1 JSON",
    )
    check.add_argument(
        "--language-profile",
        type=Path,
        help="explicit language-service profile; inferred for registered platforms",
    )
    check.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="pack root or Groovy root containing the candidate source",
    )
    check.add_argument(
        "--runtime-root",
        type=Path,
        required=True,
        help="exact client .minecraft/runtime root whose mods, runConfig, and cache are inventoried",
    )
    selection = check.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--file",
        action="append",
        dest="files",
        help="Groovy path relative to the source root; repeatable",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        dest="all_files",
        help="explicitly check every configured, non-excluded script",
    )
    check.add_argument(
        "--packmode",
        help="effective packmode; defaults to runConfig when declared",
    )
    check.add_argument(
        "--debug-state",
        choices=("auto", "on", "off"),
        default="auto",
        help="effective GroovyScript debug state (default: runConfig)",
    )
    check.add_argument(
        "--mod",
        action="append",
        default=[],
        help="installed mod ID for preprocessor evaluation; repeatable",
    )
    check.add_argument(
        "--host",
        help="language-server host (default from profile; loopback only unless allowed)",
    )
    check.add_argument(
        "--port",
        type=int,
        help="language-server port (default from profile)",
    )
    check.add_argument(
        "--server-workspace-uri",
        help="file URI visible to the server, for cross-host/path-mapped workspaces",
    )
    check.add_argument(
        "--allow-remote",
        action="store_true",
        help="explicitly allow sending exact source bytes to a non-loopback server",
    )
    check.add_argument(
        "--connect-timeout",
        type=_positive_float,
        default=5.0,
        help="TCP/initialize timeout seconds (default: 5)",
    )
    check.add_argument(
        "--diagnostic-timeout",
        type=_positive_float,
        default=20.0,
        help="per-protocol-step diagnostic timeout seconds (default: 20)",
    )
    check.add_argument(
        "--java",
        type=Path,
        help="optional exact Java executable to hash and probe as caller context",
    )
    check.add_argument(
        "--runtime-receipt",
        type=Path,
        help="optional runtime launch receipt retained as unauthenticated endpoint context",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="emit the complete source/runtime/compiler-bound V1 JSON result",
    )
    check.add_argument(
        "--output",
        type=Path,
        help="also write the complete JSON result atomically to a fresh path",
    )
    check.add_argument(
        "--strict",
        action="store_true",
        help="return exit 1 for diagnostics or inconclusive checks",
    )
    session = commands.add_parser(
        "session",
        help="launch and supervise an exact disposable GroovyScript language session for terminal or IDE clients",
    )
    session_profile = session.add_mutually_exclusive_group(required=True)
    session_profile.add_argument(
        "--profile",
        help="explicit named pack adapter; currently supersymmetry",
    )
    session_profile.add_argument(
        "--profile-file",
        type=Path,
        help="explicit workbench-groovy-pack-profile-v1 JSON",
    )
    session.add_argument(
        "--language-profile",
        type=Path,
        help="explicit language-service profile; inferred for registered platforms",
    )
    session.add_argument(
        "--session-profile",
        type=Path,
        help="explicit managed-session profile; inferred from the language profile",
    )
    session.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="pack root or Groovy root exposed as the IDE workspace",
    )
    session.add_argument(
        "--runtime-root",
        type=Path,
        required=True,
        help="exact disposable client .minecraft root bound by the launch receipt",
    )
    session.add_argument(
        "--launch-receipt",
        type=Path,
        required=True,
        help="completed Workbench runtime-launch V3 receipt for the disposable Prism projection",
    )
    session.add_argument(
        "--session-storage",
        type=Path,
        help="retained session store (default: .workbench/sessions/groovy-language-service)",
    )
    session.add_argument(
        "--port",
        type=int,
        help="explicit free loopback port; otherwise Workbench reserves a random port",
    )
    session.add_argument(
        "--packmode",
        help="effective packmode; defaults to runConfig when declared",
    )
    session.add_argument(
        "--debug-state",
        choices=("auto", "on", "off"),
        default="auto",
        help="effective GroovyScript debug state (default: runConfig)",
    )
    session.add_argument(
        "--mod",
        action="append",
        default=[],
        help="installed mod ID for preprocessor evaluation; repeatable",
    )
    session.add_argument(
        "--readiness-timeout",
        type=_readiness_float,
        help="physical-client and exact LSP readiness timeout seconds",
    )
    session.add_argument(
        "--session-timeout",
        type=_session_float,
        help="maximum ready-session lifetime; defaults to the managed profile",
    )
    session.add_argument(
        "--connect-timeout",
        type=_positive_float,
        default=5.0,
        help="per-attempt TCP timeout seconds (default: 5)",
    )
    session.add_argument(
        "--diagnostic-timeout",
        type=_positive_float,
        default=20.0,
        help="readiness canary protocol timeout seconds (default: 20)",
    )
    session_output = session.add_mutually_exclusive_group()
    session_output.add_argument(
        "--json-events",
        action="store_true",
        help="emit lifecycle JSONL, including the ready IDE descriptor",
    )
    session_output.add_argument(
        "--json",
        action="store_true",
        help="emit the complete final managed-session V1 receipt",
    )
    session.add_argument(
        "--output",
        type=Path,
        help="also copy the complete final receipt atomically to a fresh path",
    )
    proxy = commands.add_parser(
        "proxy",
        help="bridge stdio to one validated ready-session descriptor for native IDE LSP clients",
    )
    proxy.add_argument(
        "--descriptor",
        type=Path,
        required=True,
        help="exact workbench-groovy-language-session-descriptor-v1 JSON",
    )
    proxy.add_argument(
        "--connect-timeout",
        type=_positive_float,
        default=5.0,
        help="loopback connection timeout seconds (default: 5)",
    )
    return parser


def run(
    argv: list[str],
    *,
    root: Path,
    output: TextIO,
    error: TextIO,
    candidate_git_binding: Mapping[str, Any] | None = None,
    baseline_git_binding: Mapping[str, Any] | None = None,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.operation == "proxy":
            proxy_descriptor_stdio(
                args.descriptor,
                input_stream=sys.stdin.buffer,
                output_stream=sys.stdout.buffer,
                connect_timeout=args.connect_timeout,
            )
            return 0
        profile_path = (
            resolve_named_profile(root, args.profile)
            if args.profile is not None
            else args.profile_file
        )
        loaded_profile = load_profile(profile_path)
        debug = None if args.debug_state == "auto" else args.debug_state == "on"
        installed_mods = (
            None
            if not args.mod
            else frozenset(_mod_id(value) for value in args.mod)
        )
        if args.operation == "dev":
            value = build_report(
                source=args.source,
                profile=loaded_profile,
                context=AnalysisContext(
                    side=args.side,
                    packmode=args.packmode,
                    debug=debug,
                    installed_mods=installed_mods,
                ),
                baseline=args.baseline,
                changed_paths=args.changed,
                groovy_log=args.groovy_log,
                runtime_diagnosis=args.runtime_diagnosis,
                candidate_git_binding=candidate_git_binding,
                baseline_git_binding=baseline_git_binding,
            )
            rendered = render_report(
                value,
                recipe_review=args.recipe_review,
                verbose=args.verbose,
            )
        elif args.operation == "check":
            language_profile = resolve_language_profile(
                root,
                loaded_profile,
                args.language_profile,
            )
            value = build_language_service_result(
                source=args.source,
                pack_profile=loaded_profile,
                language_profile=language_profile,
                context=AnalysisContext(
                    side="client",
                    packmode=args.packmode,
                    debug=debug,
                    installed_mods=installed_mods,
                ),
                runtime_root=args.runtime_root,
                selected_paths=args.files,
                select_all=args.all_files,
                host=args.host,
                port=args.port,
                server_workspace_uri=args.server_workspace_uri,
                allow_remote=args.allow_remote,
                connect_timeout=args.connect_timeout,
                diagnostic_timeout=args.diagnostic_timeout,
                java=args.java,
                runtime_receipt=args.runtime_receipt,
            )
            rendered = render_language_result(value)
        else:
            language_profile = resolve_language_profile(
                root,
                loaded_profile,
                args.language_profile,
            )
            managed_profile = resolve_managed_session_profile(
                root,
                language_profile,
                args.session_profile,
            )
            stop = threading.Event()

            def event_callback(event: dict[str, object]) -> None:
                if args.json:
                    return
                if args.json_events:
                    output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
                else:
                    output.write(render_session_event(event))
                    output.flush()

            storage = (
                root / ".workbench/sessions/groovy-language-service"
                if args.session_storage is None
                else args.session_storage
            )
            with _session_signal_handlers(stop):
                value = run_managed_language_session(
                    source=args.source,
                    pack_profile=loaded_profile,
                    language_profile=language_profile,
                    managed_profile=managed_profile,
                    context=AnalysisContext(
                        side="client",
                        packmode=args.packmode,
                        debug=debug,
                        installed_mods=installed_mods,
                    ),
                    runtime_root=args.runtime_root,
                    launch_receipt=args.launch_receipt,
                    session_storage=storage,
                    requested_port=args.port,
                    readiness_timeout=args.readiness_timeout,
                    session_timeout=args.session_timeout,
                    connect_timeout=args.connect_timeout,
                    diagnostic_timeout=args.diagnostic_timeout,
                    stop_event=stop,
                    on_event=event_callback,
                )
            rendered = render_session_receipt(value)
        if result_callback is not None:
            result_callback(value)
        written = None
        if args.output is not None:
            written = _write_fresh_json(args.output, value)
        if args.json:
            # Full owner reports can contain hundreds of thousands of static
            # rows. Stream the unchanged JSON shape instead of allocating a
            # second whole-report string before writing it.
            json.dump(value, output, indent=2, ensure_ascii=False, sort_keys=True)
            output.write("\n")
        elif args.operation != "session" or not args.json_events:
            output.write(rendered)
            if written is not None:
                output.write(f"Report written: {written}\n")
        if args.operation == "session":
            return 2 if value["state"] == "blocked" else 0
        if value["summary"]["status"] == "blocked":
            return 2
        return 1 if args.strict and value["summary"]["status"] == "attention" else 0
    except (OSError, ValueError, PackProgramError) as exc:
        error.write(f"Groovy Pack Program Studio failed: {exc}\n")
        return 2


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    repository = (
        _repository_resource_root(__file__)
        if root is None
        else root.expanduser().resolve()
    )
    return run(
        list(sys.argv[1:] if argv is None else argv),
        root=repository,
        output=sys.stdout,
        error=sys.stderr,
    )


def _mod_id(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in normalized):
        raise PackProgramError(f"invalid mod ID for --mod: {value!r}")
    return normalized


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0.05 <= parsed <= 300:
        raise argparse.ArgumentTypeError("must be between 0.05 and 300 seconds")
    return parsed


def _readiness_float(value: str) -> float:
    return _bounded_float(value, maximum=3600)


def _session_float(value: str) -> float:
    return _bounded_float(value, maximum=86400)


def _bounded_float(value: str, *, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0.05 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"must be between 0.05 and {maximum:g} seconds"
        )
    return parsed


@contextmanager
def _session_signal_handlers(stop: threading.Event):
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: dict[signal.Signals, object] = {}

    def request_stop(_number: int, _frame: object) -> None:
        stop.set()

    selected = [signal.SIGINT, signal.SIGTERM]
    try:
        for item in selected:
            previous[item] = signal.getsignal(item)
            signal.signal(item, request_stop)
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


def _write_fresh_json(path: Path, report: dict[str, object]) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise PackProgramError(f"output cannot be a symlink: {requested}")
    destination = requested.resolve()
    if destination.exists():
        raise PackProgramError(f"output already exists; choose a fresh path: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
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
            json.dump(
                report,
                temporary,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError as exc:
            raise PackProgramError(
                f"output already exists; choose a fresh path: {destination}"
            ) from exc
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return destination


__all__ = ["build_parser", "main", "run"]
