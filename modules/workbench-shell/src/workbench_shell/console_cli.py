"""Command-line and interactive entry point for the Workbench live console."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Callable, Mapping, Sequence, TextIO

from .catalog import (
    Catalog,
    CatalogError,
    CommandSpec,
    FieldSpec,
    build_catalog,
    parse_assignments,
    redact_argv,
)
from workbench_core.render import (
    CursesRenderer,
    EventFilter,
    RenderError,
    make_renderer,
    render_replay,
)
from workbench_core.runner import RunnerError, ingest_files, supervise_process
from workbench_core.sessions import (
    EphemeralSession,
    RetainedSession,
    SessionError,
    iter_events,
    list_sessions,
    resolve_session,
)


class ConsoleError(RuntimeError):
    """The console request cannot be completed safely."""


class ConsoleInputClosed(ConsoleError):
    """Interactive input closed before an action was authorized."""


COMMAND_REVIEW_FORMAT = "workbench-live-console-command-review-v2"
COMMAND_REVIEW_BINDING_FORMAT = "workbench-live-console-command-review-binding-v2"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass
class ConsolePreferences:
    renderer_mode: str = "auto"
    color: str = "auto"
    retain: bool = True
    view: str = "signal"
    search: str | None = None
    minimum_severity: str | None = None
    subsystems: list[str] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench console",
        description=(
            "Run, retain, search, and replay Workbench tool output through one "
            "high-signal terminal surface."
        ),
    )
    commands = parser.add_subparsers(dest="action")

    catalog = commands.add_parser("catalog", help="list tool suites and exact wizard options")
    catalog.add_argument("--suite")
    catalog.add_argument("--search")
    catalog.add_argument("--json", action="store_true")

    wizard = commands.add_parser("wizard", help="open an interactive command wizard")
    wizard.add_argument("command_id", nargs="?")

    run = commands.add_parser("run", help="preview or execute one catalog command")
    run.add_argument("command_id")
    run.add_argument(
        "--set",
        dest="definitions",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set a typed wizard input; KEY:=JSON is also accepted",
    )
    run.add_argument(
        "--execute",
        action="store_true",
        help="cross the console consent boundary; owner validation still applies",
    )
    run.add_argument("--print-command", action="store_true")
    run.add_argument(
        "--review-json",
        action="store_true",
        help="emit a digest-bound preview/execution review without launching either argv",
    )
    run.add_argument("--expect-catalog-digest")
    run.add_argument("--expect-action-digest")
    run.add_argument("--expect-review-digest")
    _add_live_options(run)

    watch = commands.add_parser(
        "watch", help="run one exact argv without a shell and retain its output"
    )
    watch.add_argument("--cwd", type=Path)
    watch.add_argument("--allow-external-cwd", action="store_true")
    watch.add_argument("--label")
    _add_live_options(watch)
    watch.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="exact command after --; shell syntax is never evaluated",
    )

    ingest = commands.add_parser(
        "ingest", help="retain and normalize one or more existing launch streams"
    )
    ingest.add_argument("paths", nargs="+", type=Path)
    ingest.add_argument("--label")
    _add_live_options(ingest)

    sessions = commands.add_parser("sessions", help="list retained console sessions")
    sessions.add_argument("--json", action="store_true")

    replay = commands.add_parser("replay", help="search and replay a retained timeline")
    replay.add_argument("selector", nargs="?", default="latest")
    _add_render_options(replay)
    replay.add_argument("--json", action="store_true", help="alias for --console jsonl")

    return parser


def _add_live_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-retain",
        action="store_true",
        help="explicitly discard raw streams and event projection after display",
    )
    parser.add_argument("--label") if not any(
        action.dest == "label" for action in parser._actions
    ) else None
    _add_render_options(parser)


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--console",
        dest="renderer_mode",
        choices=("auto", "tui", "plain", "jsonl", "messages"),
        default="auto",
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto"
    )
    parser.add_argument("--search", help="literal event search")
    parser.add_argument(
        "--minimum-severity",
        choices=("trace", "debug", "info", "notice", "warning", "error", "fatal"),
    )
    parser.add_argument(
        "--subsystem",
        action="append",
        default=[],
        help="show only this normalized subsystem; repeatable",
    )
    parser.add_argument(
        "--view",
        choices=("signal", "all"),
        default="signal",
        help="signal hides parser-identified noise but never drops retained events",
    )


def run(
    argv: Sequence[str],
    *,
    root: Path,
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    root = root.expanduser().resolve()
    catalog = build_catalog(root)
    parser = build_parser()
    args = parser.parse_args(list(argv))
    try:
        if args.action is None:
            if input_stream.isatty() and output.isatty():
                return TerminalApplication(
                    root=root,
                    catalog=catalog,
                    input_stream=input_stream,
                    output=output,
                    error=error,
                ).run()
            _render_catalog(catalog, output=output)
            error.write(
                "workbench console: interactive menus require a TTY; use catalog, run, watch, ingest, or replay\n"
            )
            return 2
        if args.action == "catalog":
            return _catalog_action(catalog, args, output)
        if args.action == "wizard":
            if not input_stream.isatty() or not output.isatty():
                raise ConsoleError("wizard requires an interactive terminal")
            app = TerminalApplication(
                root=root,
                catalog=catalog,
                input_stream=input_stream,
                output=output,
                error=error,
            )
            return app.command_wizard(args.command_id)
        if args.action == "run":
            _validate_digest_expectation(
                "catalog", args.expect_catalog_digest, catalog.catalog_digest
            )
            command = catalog.command(args.command_id)
            _validate_digest_expectation(
                "action",
                args.expect_action_digest,
                command.action_digest(root=root),
            )
            if command.document:
                if args.review_json or args.expect_review_digest:
                    raise ConsoleError(
                        "document actions do not produce executable review bindings"
                    )
                _render_document(root, command, output)
                return 0
            values = parse_assignments(command, args.definitions)
            binding_requested = any(
                value is not None
                for value in (
                    args.expect_catalog_digest,
                    args.expect_action_digest,
                    args.expect_review_digest,
                )
            )
            if args.review_json:
                if args.execute or args.print_command or args.expect_review_digest:
                    raise ConsoleError(
                        "--review-json cannot be combined with --execute, --print-command, or --expect-review-digest"
                    )
                if not args.expect_catalog_digest or not args.expect_action_digest:
                    raise ConsoleError(
                        "--review-json requires --expect-catalog-digest and --expect-action-digest"
                    )
                review = _command_review(catalog, command, values, root=root)
                output.write(json.dumps(review, indent=2, sort_keys=True) + "\n")
                return 0
            if binding_requested:
                if not all(
                    (
                        args.expect_catalog_digest,
                        args.expect_action_digest,
                        args.expect_review_digest,
                    )
                ):
                    raise ConsoleError(
                        "a bound run requires catalog, action, and review digest expectations"
                    )
                review = _command_review(catalog, command, values, root=root)
                _validate_digest_expectation(
                    "review", args.expect_review_digest, review["review_digest"]
                )
            command_argv, intent = command.build_argv(
                values, root=root, execute=args.execute
            )
            if args.print_command or (
                intent == "inert" and not args.execute
            ) or (
                command.needs_execute_consent
                and command.preview == "none"
                and not args.execute
            ):
                output.write(_display_command(command_argv, root) + "\n")
                if command.needs_execute_consent and not args.execute:
                    output.write("Inert command review only; add --execute to run it.\n")
                return 0
            return _run_argv(
                command_argv,
                root=root,
                command_id=command.command_id,
                intent=intent,
                fields=command.fields,
                args=args,
                input_stream=input_stream,
                output=output,
            )
        if args.action == "watch":
            command = list(args.command)
            if command[:1] == ["--"]:
                command = command[1:]
            if not command:
                raise ConsoleError("watch requires an exact command after --")
            cwd = (args.cwd or root).expanduser().resolve(strict=True)
            if not args.allow_external_cwd and cwd != root and root not in cwd.parents:
                raise ConsoleError(
                    "watch cwd is outside this Workbench checkout; pass --allow-external-cwd explicitly"
                )
            return _run_argv(
                command,
                root=root,
                command_id="external.watch",
                intent="execute",
                fields=(),
                args=args,
                input_stream=input_stream,
                output=output,
                cwd=cwd,
                redact_generic=True,
            )
        if args.action == "ingest":
            renderer = _renderer(args, input_stream=input_stream, output=output)
            session = _session(
                root,
                args,
                command_id="console.ingest",
                argv=["ingest", *(str(path) for path in args.paths)],
                intent="inspect",
                cwd=root,
            )
            result = ingest_files(
                args.paths,
                root=root,
                session=session,
                renderer=renderer,
            )
            return result.effective_exit_code
        if args.action == "sessions":
            values = list_sessions(root)
            if args.json:
                output.write(json.dumps(values, indent=2, sort_keys=True) + "\n")
            else:
                _render_sessions(values, output)
            return 0
        if args.action == "replay":
            directory, manifest = resolve_session(root, args.selector)
            if args.json:
                args.renderer_mode = "jsonl"
            renderer = _renderer(args, input_stream=input_stream, output=output)
            exit_value = manifest.get("exit", {})
            summary = {
                "outcome": exit_value.get("outcome", manifest.get("state")),
                "effective_exit_code": exit_value.get("effective_exit_code"),
                "retained": str(directory),
            }
            render_replay(
                iter_events(directory),
                renderer=renderer,
                context={
                    "command": manifest.get("command", {}).get("command_id", "retained session"),
                    "retained": str(directory),
                },
                summary=summary,
            )
            return 0
        raise ConsoleError(f"unsupported console action: {args.action}")
    except ConsoleInputClosed:
        error.write("Workbench console: input closed; no pending action was run.\n")
        return 0
    except (CatalogError, ConsoleError, RenderError, RunnerError, SessionError, OSError, ValueError) as exc:
        error.write(f"Workbench console failed: {exc}\n")
        return 2


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    selected_root = (
        root.expanduser().resolve()
        if root is not None
        else _repository_resource_root(__file__)
    )
    return run(arguments, root=selected_root)


def _catalog_action(catalog: Catalog, args: argparse.Namespace, output: TextIO) -> int:
    commands = catalog.search(args.search or "", suite_id=args.suite)
    if args.suite and not any(item.suite_id == args.suite for item in catalog.suites):
        raise ConsoleError(f"unknown suite: {args.suite}")
    if args.json:
        value = catalog.public_dict()
        if args.suite or args.search:
            value["commands"] = [item.public_dict(root=catalog.root) for item in commands]
            if args.suite:
                value["suites"] = [
                    item.public_dict(len(catalog.for_suite(item.suite_id)))
                    for item in catalog.suites
                    if item.suite_id == args.suite
                ]
        output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    else:
        _render_catalog(catalog, output=output, commands=commands, suite_id=args.suite)
    return 0


def _command_review(
    catalog: Catalog,
    command: CommandSpec,
    values: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    """Build the cross-process binding reviewed by native IDE clients."""

    preview_argv, preview_intent = command.build_argv(
        values, root=root, execute=False
    )
    execute_argv, execute_intent = command.build_argv(
        values, root=root, execute=True
    )
    action_digest = command.action_digest(root=root)
    preview_command = _display_command(preview_argv, root)
    execute_command = _display_command(execute_argv, root)
    binding = {
        "format_version": COMMAND_REVIEW_BINDING_FORMAT,
        "catalog_digest": catalog.catalog_digest,
        "action_digest": action_digest,
        "command_id": command.command_id,
        "risk": command.risk,
        "preview": command.preview,
        "preview_intent": preview_intent,
        "preview_argv": preview_argv,
        "preview_command": preview_command,
        "execute_intent": execute_intent,
        "execute_argv": execute_argv,
        "execute_command": execute_command,
    }
    return {
        "format_version": COMMAND_REVIEW_FORMAT,
        "catalog_digest": catalog.catalog_digest,
        "action_digest": action_digest,
        "review_digest": "sha256:" + sha256(
            _canonical_bytes(binding)
        ).hexdigest(),
        "command_id": command.command_id,
        "risk": command.risk,
        "preview": command.preview,
        "preview_intent": preview_intent,
        "execute_intent": execute_intent,
        "preview_command": preview_command,
        "execute_command": execute_command,
    }


def build_command_review(
    catalog: Catalog,
    command: CommandSpec,
    values: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    """Build the exact existing console/IDE consent binding."""

    return _command_review(catalog, command, values, root=root)


def _validate_digest_expectation(
    label: str,
    expected: str | None,
    actual: str,
) -> None:
    if expected is None:
        return
    if not _DIGEST_RE.fullmatch(expected):
        raise ConsoleError(f"expected {label} digest is not a canonical SHA-256 ID")
    if not hmac.compare_digest(expected, actual):
        raise ConsoleError(
            f"{label} changed after review; reload the catalog and review the action again"
        )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _run_argv(
    argv: Sequence[str],
    *,
    root: Path,
    command_id: str,
    intent: str,
    fields: Sequence[FieldSpec],
    args: argparse.Namespace,
    input_stream: TextIO,
    output: TextIO,
    cwd: Path | None = None,
    redact_generic: bool = False,
) -> int:
    renderer = _renderer(args, input_stream=input_stream, output=output)
    retained_argv = (
        _redact_generic(argv) if redact_generic else redact_argv(argv, fields)
    )
    selected_cwd = cwd or root
    session = _session(
        root,
        args,
        command_id=command_id,
        argv=retained_argv,
        intent=intent,
        cwd=selected_cwd,
    )
    child_environment = dict(os.environ)
    binding_values = {
        "WORKBENCH_CONSOLE_CATALOG_DIGEST": getattr(args, "expect_catalog_digest", None),
        "WORKBENCH_CONSOLE_ACTION_DIGEST": getattr(args, "expect_action_digest", None),
        "WORKBENCH_CONSOLE_REVIEW_DIGEST": getattr(args, "expect_review_digest", None),
        "WORKBENCH_CONSOLE_COMMAND_ID": command_id,
    }
    # A direct/unbound invocation must not inherit a stale consent token from
    # the console's own parent process.  Only the review verified for this
    # exact invocation may cross the child boundary.
    for key in binding_values:
        child_environment.pop(key, None)
    if all(binding_values[key] is not None for key in (
        "WORKBENCH_CONSOLE_CATALOG_DIGEST",
        "WORKBENCH_CONSOLE_ACTION_DIGEST",
        "WORKBENCH_CONSOLE_REVIEW_DIGEST",
    )):
        child_environment.update(
            {key: str(value) for key, value in binding_values.items()}
        )
    result = supervise_process(
        argv,
        cwd=selected_cwd,
        root=root,
        session=session,
        renderer=renderer,
        source=command_id,
        environment=child_environment,
    )
    return result.effective_exit_code


def _renderer(
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output: TextIO,
):
    event_filter = EventFilter(
        search=getattr(args, "search", None),
        minimum_severity=getattr(args, "minimum_severity", None),
        subsystems=frozenset(getattr(args, "subsystem", ())),
        signal_only=(
            getattr(args, "view", "signal") == "signal"
            and getattr(args, "renderer_mode", "auto") != "messages"
        ),
    )
    renderer = make_renderer(
        args.renderer_mode,
        input_stream=input_stream,
        output=output,
        event_filter=event_filter,
        color=args.color,
    )
    return renderer


def _session(
    root: Path,
    args: argparse.Namespace,
    *,
    command_id: str,
    argv: list[str],
    intent: str,
    cwd: Path,
):
    if getattr(args, "no_retain", False):
        return EphemeralSession()
    return RetainedSession(
        root=root,
        command_id=command_id,
        argv=argv,
        cwd=cwd,
        intent=intent,
        label=getattr(args, "label", None),
    )


class TerminalApplication:
    """Line-safe command palette and wizards used before the live TUI starts."""

    def __init__(
        self,
        *,
        root: Path,
        catalog: Catalog,
        input_stream: TextIO,
        output: TextIO,
        error: TextIO,
    ) -> None:
        self.root = root
        self.catalog = catalog
        self.input = input_stream
        self.output = output
        self.error = error
        self.preferences = ConsolePreferences()

    def run(self) -> int:
        while True:
            self._clear()
            self.output.write("WORKBENCH · HIGH-SIGNAL LIVE CONSOLE\n")
            self.output.write("One retained timeline · exact owner commands · no shell evaluation\n\n")
            for index, suite in enumerate(self.catalog.suites, 1):
                count = len(self.catalog.for_suite(suite.suite_id))
                state = suite.availability.upper() if suite.availability != "available" else "READY"
                self.output.write(
                    f"{index:2d}. {suite.title:<23} {state:<12} {count:2d} actions\n"
                )
            self.output.write(
                "\n/ search · w watch · i ingest · r replay "
                "· s settings · ? help · q quit\n"
            )
            selected = self._prompt("console> ").strip()
            if selected.casefold() in {"q", "quit", "exit"}:
                return 0
            if selected.casefold() in {"?", "help"}:
                self._help()
                continue
            if selected.casefold() in {"r", "recent"}:
                self._interactive_operation(self._recent)
                continue
            if selected.casefold() in {"w", "watch"}:
                self._interactive_operation(self._watch)
                continue
            if selected.casefold() in {"i", "ingest"}:
                self._interactive_operation(self._ingest)
                continue
            if selected.casefold() in {"s", "settings"}:
                self._interactive_operation(self._settings)
                continue
            if selected.startswith("/"):
                command = self._select_command(self.catalog.search(selected[1:]))
                if command:
                    self._interactive_operation(lambda: self._wizard(command))
                continue
            try:
                number = int(selected)
                if number < 1 or number > len(self.catalog.suites):
                    raise IndexError
                suite = self.catalog.suites[number - 1]
            except (ValueError, IndexError):
                self._pause(
                    "Choose a suite number, /search, w, i, r, s, ?, or q."
                )
                continue
            if suite.availability == "unavailable":
                self._pause(f"{suite.title} is visible but unavailable: {suite.summary}")
                continue
            command = self._select_command(self.catalog.for_suite(suite.suite_id))
            if command:
                self._interactive_operation(lambda: self._wizard(command))

    def command_wizard(self, command_id: str | None) -> int:
        if command_id:
            command = self.catalog.command(command_id)
        else:
            query = self._prompt("Command palette search: ")
            command = self._select_command(self.catalog.search(query))
            if command is None:
                return 0
        return self._wizard(command)

    def _select_command(self, commands: Sequence[CommandSpec]) -> CommandSpec | None:
        if not commands:
            self._pause("No commands match.")
            return None
        self._clear()
        self.output.write("COMMAND PALETTE\n\n")
        for index, command in enumerate(commands, 1):
            self.output.write(
                f"{index:2d}. {command.title:<34} {command.availability:<12} "
                f"{command.risk:<13} {command.command_id}\n"
            )
        selected = self._prompt("\nnumber, command ID, or blank to go back> ").strip()
        if not selected:
            return None
        try:
            number = int(selected)
            if number < 1 or number > len(commands):
                raise IndexError
            return commands[number - 1]
        except (ValueError, IndexError):
            try:
                return self.catalog.command(selected)
            except CatalogError as exc:
                self._pause(str(exc))
                return None

    def _wizard(self, command: CommandSpec) -> int:
        self._clear()
        self.output.write(f"{command.title}\n{command.summary}\n\n")
        self.output.write(f"Authority: {command.authority}\n")
        self.output.write(f"Risk: {command.risk} · availability: {command.availability}\n")
        if command.documentation:
            self.output.write(f"Docs: {command.documentation}\n")
        for limitation in command.limitations:
            self.output.write(f"Boundary: {limitation}\n")
        if command.availability == "unavailable":
            self._pause("This route is visible for discovery but cannot be run yet.")
            return 0
        if command.document:
            self.output.write("\n")
            _render_document(self.root, command, self.output)
            self._pause()
            return 0
        self.output.write("\nEnter values. Blank keeps the owning CLI default; type :cancel to leave.\n")
        values: dict[str, Any] = {}
        for field in command.fields:
            if field.console_managed:
                continue
            value = self._ask_field(field)
            if value is _CANCEL:
                return 0
            if value is not _SKIP:
                values[field.key] = value
        try:
            preview_argv, intent = command.build_argv(
                values, root=self.root, execute=False
            )
        except CatalogError as exc:
            self._pause(f"Cannot compose command: {exc}")
            return 2
        self.output.write("\nExact argv (no shell):\n  " + _display_command(preview_argv, self.root) + "\n")
        self.output.write(f"Console action: {intent}\n")
        if intent == "inert" or (
            command.needs_execute_consent and command.preview == "none"
        ):
            answer = self._prompt("Type execute to run this exact command, or Enter to cancel> ")
            if answer != "execute":
                return 0
            execute_argv, _ = command.build_argv(
                values, root=self.root, execute=True
            )
            code = self._interactive_run(command, execute_argv, "execute")
            self._pause(f"Command finished with exit {code}.")
            return code

        answer = self._prompt("Run this command? [Y/n] ").strip().casefold()
        if answer not in {"", "y", "yes"}:
            return 0
        code = self._interactive_run(command, preview_argv, intent)
        if code != 0 or intent != "preview":
            self._pause(f"Command finished with exit {code}.")
            return code
        answer = self._prompt(
            "Preview passed. Type execute to invoke the owner’s freshly revalidated execution path> "
        )
        if answer != "execute":
            return 0
        execute_argv, _ = command.build_argv(
            values, root=self.root, execute=True
        )
        code = self._interactive_run(command, execute_argv, "execute")
        self._pause(f"Command finished with exit {code}.")
        return code

    def _interactive_run(
        self, command: CommandSpec, argv: Sequence[str], intent: str
    ) -> int:
        namespace = self._console_namespace()
        return _run_argv(
            argv,
            root=self.root,
            command_id=command.command_id,
            intent=intent,
            fields=command.fields,
            args=namespace,
            input_stream=self.input,
            output=self.output,
        )

    def _watch(self) -> None:
        self._clear()
        self.output.write(
            "WATCH EXACT COMMAND\n\n"
            "Quotes only group argv tokens. No shell expansion, pipes, redirects, "
            "or command substitution are evaluated. Child stdin is closed.\n"
        )
        raw = self._prompt("Exact command and arguments (blank cancels)> ").strip()
        if not raw:
            return
        try:
            argv = shlex.split(raw)
        except ValueError as exc:
            self._pause(f"Cannot parse argv: {exc}")
            return
        if not argv:
            return
        cwd_text = self._prompt(
            f"Working directory [{self.root}] (relative is inside checkout)> "
        ).strip()
        cwd = (
            self.root
            if not cwd_text
            else (self.root / cwd_text).expanduser().resolve()
            if not Path(cwd_text).expanduser().is_absolute()
            else Path(cwd_text).expanduser().resolve()
        )
        if not cwd.is_dir():
            self._pause(f"Working directory does not exist: {cwd}")
            return
        if cwd != self.root and self.root not in cwd.parents:
            answer = self._prompt(
                "Working directory is outside this checkout. Type external to allow it> "
            )
            if answer != "external":
                return
        label = self._prompt("Optional retained-session label> ").strip() or None
        self.output.write("\nExact argv (no shell):\n  " + _display_command(argv, self.root) + "\n")
        if self._prompt("Type run to launch this exact command group> ") != "run":
            return
        namespace = self._console_namespace(label=label)
        code = _run_argv(
            argv,
            root=self.root,
            command_id="external.watch",
            intent="execute",
            fields=(),
            args=namespace,
            input_stream=self.input,
            output=self.output,
            cwd=cwd,
            redact_generic=True,
        )
        self._pause(f"Command finished with exit {code}.")

    def _ingest(self) -> None:
        self._clear()
        self.output.write(
            "INGEST EXISTING STREAMS\n\n"
            "Enter up to 15 exact paths. Quotes group paths containing spaces; "
            "globs are not expanded.\n"
        )
        raw = self._prompt("Log paths (blank cancels)> ").strip()
        if not raw:
            return
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            self._pause(f"Cannot parse paths: {exc}")
            return
        paths = [
            path.expanduser() if path.is_absolute() else self.root / path
            for path in map(Path, tokens)
        ]
        label = self._prompt("Optional retained-session label> ").strip() or None
        self.output.write("\nFiles:\n")
        for path in paths:
            self.output.write(f"  {path}\n")
        if self._prompt("Ingest these exact files? [Y/n] ").strip().casefold() not in {
            "",
            "y",
            "yes",
        }:
            return
        namespace = self._console_namespace(label=label)
        renderer = _renderer(
            namespace, input_stream=self.input, output=self.output
        )
        session = _session(
            self.root,
            namespace,
            command_id="console.ingest",
            argv=["ingest", *(str(path) for path in paths)],
            intent="inspect",
            cwd=self.root,
        )
        result = ingest_files(
            paths,
            root=self.root,
            session=session,
            renderer=renderer,
        )
        self._pause(f"Ingestion finished with exit {result.effective_exit_code}.")

    def _settings(self) -> None:
        self._clear()
        self.output.write("CONSOLE SETTINGS · current interactive session\n\n")
        mode = self._prompt(
            "Renderer auto|tui|plain|jsonl|messages "
            f"[{self.preferences.renderer_mode}]> "
        ).strip()
        if mode:
            if mode not in {"auto", "tui", "plain", "jsonl", "messages"}:
                self._pause("Unknown renderer mode.")
                return
            self.preferences.renderer_mode = mode
        retain = self._prompt(
            f"Retain sessions y|n [{'y' if self.preferences.retain else 'n'}]> "
        ).strip().casefold()
        if retain:
            if retain not in {"y", "yes", "n", "no"}:
                self._pause("Enter y or n for retention.")
                return
            self.preferences.retain = retain in {"y", "yes"}
        view = self._prompt(f"View signal|all [{self.preferences.view}]> ").strip()
        if view:
            if view not in {"signal", "all"}:
                self._pause("View must be signal or all.")
                return
            self.preferences.view = view
        color = self._prompt(
            f"Color auto|always|never [{self.preferences.color}]> "
        ).strip()
        if color:
            if color not in {"auto", "always", "never"}:
                self._pause("Unknown color mode.")
                return
            self.preferences.color = color
        search = self._prompt(
            f"Literal search [{self.preferences.search or 'none'}]; '-' clears> "
        )
        if search == "-":
            self.preferences.search = None
        elif search:
            self.preferences.search = search
        severity = self._prompt(
            "Minimum severity trace|debug|info|notice|warning|error|fatal "
            f"[{self.preferences.minimum_severity or 'none'}]; '-' clears> "
        ).strip()
        if severity == "-":
            self.preferences.minimum_severity = None
        elif severity:
            if severity not in {
                "trace", "debug", "info", "notice", "warning", "error", "fatal"
            }:
                self._pause("Unknown severity.")
                return
            self.preferences.minimum_severity = severity
        subsystems = self._prompt(
            "Subsystems comma-separated "
            f"[{','.join(self.preferences.subsystems) or 'all'}]; '-' clears> "
        ).strip()
        if subsystems == "-":
            self.preferences.subsystems = []
        elif subsystems:
            self.preferences.subsystems = [
                value.strip() for value in subsystems.split(",") if value.strip()
            ]
        self._pause("Settings updated.")

    def _console_namespace(self, *, label: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(
            renderer_mode=self.preferences.renderer_mode,
            color=self.preferences.color,
            search=self.preferences.search,
            minimum_severity=self.preferences.minimum_severity,
            subsystem=list(self.preferences.subsystems),
            view=self.preferences.view,
            no_retain=not self.preferences.retain,
            label=label,
        )

    def _interactive_operation(self, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except ConsoleInputClosed:
            raise
        except (
            CatalogError,
            ConsoleError,
            RenderError,
            RunnerError,
            SessionError,
            OSError,
            ValueError,
        ) as exc:
            self._pause(f"Operation failed safely: {exc}")

    def _ask_field(self, field: FieldSpec):
        required = "required" if field.required else "optional"
        choices = f" ({' | '.join(field.choices)})" if field.choices else ""
        default = f" [{field.default}]" if field.default is not None else ""
        prompt = f"{field.label}{choices} · {required}{default}: "
        if field.kind == "boolean":
            prompt = f"{field.label} [y/N]: "
        while True:
            raw = self._prompt(prompt)
            if raw == ":cancel":
                return _CANCEL
            if raw == "":
                if field.required:
                    self.output.write("A value is required by the owning CLI.\n")
                    continue
                return _SKIP
            if field.kind == "boolean":
                if raw.casefold() in {"y", "yes", "true", "1"}:
                    return True
                if raw.casefold() in {"n", "no", "false", "0"}:
                    return False
                self.output.write("Enter y or n.\n")
                continue
            if field.repeat and isinstance(field.nargs, int):
                groups: list[list[str]] = []
                current = raw
                while current:
                    try:
                        group = shlex.split(current.replace(",", " "))
                    except ValueError as exc:
                        self.output.write(f"Cannot parse values: {exc}\n")
                        group = []
                    if len(group) != field.nargs:
                        self.output.write(
                            f"Enter exactly {field.nargs} values per occurrence.\n"
                        )
                    else:
                        groups.append(group)
                    current = self._prompt(
                        f"Another {field.label} occurrence (blank ends): "
                    )
                return groups
            if field.repeat:
                values = [raw]
                while True:
                    extra = self._prompt(f"Another {field.label} (blank ends): ")
                    if not extra:
                        break
                    values.append(extra)
                return values
            if field.nargs not in {"one", "optional"}:
                try:
                    values = shlex.split(raw.replace(",", " "))
                except ValueError as exc:
                    self.output.write(f"Cannot parse values: {exc}\n")
                    continue
                if isinstance(field.nargs, int) and len(values) != field.nargs:
                    self.output.write(f"Enter exactly {field.nargs} values.\n")
                    continue
                return values
            if field.kind == "integer":
                try:
                    return int(raw)
                except ValueError:
                    self.output.write("Enter an integer.\n")
                    continue
            if field.kind == "choice" and raw not in field.choices:
                self.output.write("Choose one of: " + ", ".join(field.choices) + "\n")
                continue
            return raw

    def _recent(self) -> None:
        values = list_sessions(self.root)
        self._clear()
        _render_sessions(values[:20], self.output)
        if values:
            selected = self._prompt("Session prefix to replay, or blank to return> ").strip()
            if selected:
                directory, manifest = resolve_session(self.root, selected)
                namespace = self._console_namespace()
                renderer = _renderer(
                    namespace, input_stream=self.input, output=self.output
                )
                exit_value = manifest.get("exit", {})
                render_replay(
                    iter_events(directory),
                    renderer=renderer,
                    context={"command": manifest.get("command", {}).get("command_id"), "retained": str(directory)},
                    summary={"outcome": exit_value.get("outcome", manifest.get("state")), "effective_exit_code": exit_value.get("effective_exit_code"), "retained": str(directory)},
                )
        else:
            self._pause()

    def _help(self) -> None:
        self._clear()
        self.output.write(
            "The command palette exposes exact options from admitted user-facing tools.\n"
            "Mutations are previewed when the owner supports plans, then require an\n"
            "explicit execute action. The downstream tool still validates freshness,\n"
            "confirmation, lifecycle, policy, and authority.\n\n"
            "During a full-screen run: j/k navigate, space pauses the viewport only,\n"
            "/ searches literally, f filters severity, Enter opens details, x requests\n"
            "a bounded stop, and ? shows keyboard help. Every byte continues to spool.\n\n"
            "Top-level w watches an exact argv, i ingests existing logs, r replays\n"
            "retained sessions, and s changes renderer, retention, and filters for\n"
            "this interactive console session.\n"
        )
        self._pause()

    def _clear(self) -> None:
        if (
            self.output.isatty()
            and os.environ.get("TERM", "").casefold() != "dumb"
            and not os.environ.get("NO_COLOR")
        ):
            self.output.write("\x1b[2J\x1b[H")
        self.output.flush()

    def _prompt(self, value: str) -> str:
        self.output.write(value)
        self.output.flush()
        line = self.input.readline()
        if line == "":
            raise ConsoleInputClosed
        return line.rstrip("\r\n")

    def _pause(self, message: str = "Press Enter to continue.") -> None:
        self._prompt(message + " ")


_SKIP = object()
_CANCEL = object()


def _render_catalog(
    catalog: Catalog,
    *,
    output: TextIO,
    commands: Sequence[CommandSpec] | None = None,
    suite_id: str | None = None,
) -> None:
    selected = list(catalog.commands if commands is None else commands)
    output.write("Workbench console tool suites\n")
    for suite in catalog.suites:
        if suite_id and suite.suite_id != suite_id:
            continue
        output.write(
            f"  {suite.suite_id:<14} {suite.availability:<12} "
            f"{len(catalog.for_suite(suite.suite_id)):2d}  {suite.title}\n"
        )
    output.write("\nCommands\n")
    for command in selected:
        output.write(
            f"  {command.command_id:<38} {command.availability:<12} "
            f"{command.risk:<13} {command.title}\n"
        )
        output.write(f"      {command.summary}\n")
        if command.fields:
            option_text = ", ".join(
                field.primary_flag + ("*" if field.required else "")
                for field in command.fields
            )
            output.write(f"      options: {option_text}\n")


def _render_sessions(values: Sequence[Mapping[str, Any]], output: TextIO) -> None:
    if not values:
        output.write("No retained live-console sessions.\n")
        return
    output.write("Retained live-console sessions\n")
    for value in values:
        command = value.get("command", {}).get("command_id", "unknown")
        count = value.get("summary", {}).get("event_count", "?")
        output.write(
            f"  {value.get('session_id')}  {value.get('state', 'unknown'):<10} "
            f"{str(command):<28} {count} events\n"
        )


def _render_document(root: Path, command: CommandSpec, output: TextIO) -> None:
    assert command.document
    path = (root / command.document).resolve()
    if root != path and root not in path.parents:
        raise ConsoleError("manual path escapes the Workbench checkout")
    content = path.read_text(encoding="utf-8")
    output.write(content)
    if not content.endswith("\n"):
        output.write("\n")


def _display_command(argv: Sequence[str], root: Path) -> str:
    prefix = str(root) + os.sep
    portable = [item[len(prefix) :] if item.startswith(prefix) else item for item in argv]
    return shlex.join(portable)


def _redact_generic(argv: Sequence[str]) -> list[str]:
    result: list[str] = []
    sensitive = False
    for argument in argv:
        lowered = argument.casefold()
        if sensitive:
            result.append("<redacted>")
            sensitive = False
        elif argument.startswith("-") and any(
            word in lowered for word in ("password", "passwd", "secret", "token", "api-key", "apikey")
        ):
            if "=" in argument:
                result.append(argument.split("=", 1)[0] + "=<redacted>")
            else:
                result.append(argument)
                sensitive = True
        else:
            result.append(argument)
    return result


__all__ = [
    "ConsoleError",
    "ConsoleInputClosed",
    "TerminalApplication",
    "build_command_review",
    "build_parser",
    "main",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
