"""Credential-free Prism/MultiMC setup binding.

Launcher Setup V1 is additive to User Setup V1. It records only the launcher
family and physical paths consumed by the existing runtime-launch owner.
Account contents and account selection remain launcher-owned.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

from .runtime_launch import RuntimeLaunchError, _probe_launcher
from workbench_core.setup_cli import SetupCancelled, _prompt, default_setup_record_path
from workbench_core.tooling_provision import inspect_tools
from workbench_api.state_paths import default_runtime_state_root


CHECK_FORMAT = "workbench-launcher-setup-check-v1"
PLAN_FORMAT = "workbench-launcher-setup-plan-v1"
RECORD_FORMAT = "workbench-launcher-setup-record-v1"
RESULT_FORMAT = "workbench-launcher-setup-result-v1"
SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 128 * 1024
FAMILIES = frozenset({"prism", "multimc"})


class LauncherSetupError(ValueError):
    """Launcher selection, retained state, or probe is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _digest(prefix: str, value: Any) -> str:
    return prefix + ":sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def default_launcher_record_path(
    *, environment: Mapping[str, str] | None = None
) -> Path:
    """Place the additive launcher binding beside User Setup V1."""

    return default_setup_record_path(environment=environment).with_name(
        "launcher-v1.json"
    )


def _validate_selection(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != {
        "family",
        "executable",
        "root",
    }:
        raise LauncherSetupError(
            "launcher selection has unsupported or missing fields"
        )
    if value.get("family") not in FAMILIES:
        raise LauncherSetupError("launcher family must be prism or multimc")
    for field in ("executable", "root"):
        if type(value.get(field)) is not str or not value[field]:
            raise LauncherSetupError(f"launcher {field} must be a path")
        if not Path(value[field]).is_absolute():
            raise LauncherSetupError(f"launcher {field} must be absolute")
    return dict(value)


def _record_payload(selection: Mapping[str, str]) -> dict[str, Any]:
    return {
        "format": RECORD_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "selection": dict(selection),
    }


def _validate_record(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "format",
        "schema_version",
        "record_id",
        "selection",
    }:
        raise LauncherSetupError(
            "launcher setup record has unsupported or missing fields"
        )
    if (
        value.get("format") != RECORD_FORMAT
        or value.get("schema_version") != SCHEMA_VERSION
    ):
        raise LauncherSetupError("launcher setup record is not V1")
    selection = _validate_selection(value.get("selection"))
    payload = _record_payload(selection)
    expected = _digest("workbench-launcher-setup", payload)
    if value.get("record_id") != expected:
        raise LauncherSetupError(
            "launcher setup record identity does not match its selection"
        )
    return {**payload, "record_id": expected}


def load_launcher_record(path: Path | str) -> dict[str, Any] | None:
    """Load one bounded, non-symlink launcher binding."""

    selected = Path(path).expanduser()
    try:
        info = selected.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LauncherSetupError(f"cannot inspect launcher setup record: {exc}") from exc
    if selected.is_symlink() or not selected.is_file():
        raise LauncherSetupError(
            "launcher setup record must be a regular non-symlink file"
        )
    if not 1 <= info.st_size <= MAX_RECORD_BYTES:
        raise LauncherSetupError("launcher setup record is outside its byte limit")
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LauncherSetupError(
            "launcher setup record is not strict UTF-8 JSON"
        ) from exc
    return _validate_record(value)


def _write_launcher_record(
    path: Path, selection: Mapping[str, str]
) -> dict[str, Any]:
    normalized = _validate_selection(dict(selection))
    payload = _record_payload(normalized)
    record = {
        **payload,
        "record_id": _digest("workbench-launcher-setup", payload),
    }
    parent = path.parent
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise LauncherSetupError(
            "launcher setup destination must be a regular file"
        )
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise LauncherSetupError(
            "launcher setup parent must be a regular directory"
        )
    parent.mkdir(parents=True, exist_ok=True)
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise LauncherSetupError("launcher setup staging path already exists")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        raw = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise LauncherSetupError(f"cannot publish launcher setup: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return record


def _dependency(
    dependency_id: str,
    label: str,
    state: str,
    detail: str,
    *,
    source: str | None = None,
    repair: str | None = None,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": dependency_id,
        "label": label,
        "required": required,
        "state": state,
        "source": source,
        "detail": detail,
        "repair": repair,
        "managed_install": False,
    }


def _probe_root(root: Path, family: str) -> list[dict[str, Any]]:
    """Inspect launcher initialization and account presence without reading it."""

    selected = _absolute(root)
    if selected.is_symlink() or not selected.is_dir():
        return [
            _dependency(
                "launcher-root",
                "Launcher data root",
                "incompatible",
                "The selected launcher data root is not a regular directory.",
                source=str(selected),
                repair=(
                    "Choose the initialized Prism/MultiMC data directory, not an "
                    "instance or executable directory."
                ),
            ),
            _dependency(
                "launcher-account",
                "Launcher account setup",
                "missing-manual",
                "Account readiness cannot be checked until the launcher root is valid.",
                source=str(selected),
                repair="Open the launcher and finish its own setup/login flow.",
                required=False,
            ),
        ]
    config_name = "prismlauncher.cfg" if family == "prism" else "multimc.cfg"
    config = selected / config_name
    instances = selected / "instances"
    if (
        not config.is_file()
        or config.is_symlink()
        or (
            instances.exists()
            and (instances.is_symlink() or not instances.is_dir())
        )
    ):
        root_row = _dependency(
            "launcher-root",
            "Launcher data root",
            "incompatible",
            f"The selected root is not initialized for {family}.",
            source=str(selected),
            repair=f"Open {family} once with this data root, then rerun setup.",
        )
    else:
        root_row = _dependency(
            "launcher-root",
            "Launcher data root",
            "ready",
            (
                "The initialized launcher root can receive a fresh Workbench "
                "instance after payload verification."
            ),
            source=str(selected),
        )
    accounts = selected / "accounts.json"
    if accounts.is_file() and not accounts.is_symlink():
        account_row = _dependency(
            "launcher-account",
            "Launcher account setup",
            "ready",
            (
                "The launcher account store is present. Workbench did not open, "
                "parse, copy, hash, or retain it."
            ),
            source=str(accounts),
            required=False,
        )
    else:
        account_row = _dependency(
            "launcher-account",
            "Launcher account setup",
            "missing-manual",
            (
                "The launcher has not completed its own account/setup boundary. "
                "Workbench cannot create or inspect launcher credentials."
            ),
            source=str(accounts),
            repair=(
                "Open the launcher, complete Quick Setup and account or offline "
                "configuration, close it, then retry runtime-launch."
            ),
            required=False,
        )
    return [root_row, account_row]


def inspect_launcher_setup(
    selection: Mapping[str, str],
    *,
    record_path: Path | str,
    record_present: bool,
) -> dict[str, Any]:
    """Build a credential-free read-only launcher inventory."""

    normalized = _validate_selection(dict(selection))
    dependencies: list[dict[str, Any]] = []
    try:
        _path, launcher, host = _probe_launcher(
            Path(normalized["executable"]), normalized["family"]
        )
    except RuntimeLaunchError as exc:
        dependencies.append(
            _dependency(
                "launcher-executable",
                "Launcher executable",
                "incompatible",
                str(exc),
                source=normalized["executable"],
                repair="Choose a working Prism Launcher or MultiMC executable.",
            )
        )
        launcher = None
        host = None
    else:
        dependencies.append(
            _dependency(
                "launcher-executable",
                "Launcher executable",
                "ready",
                str(launcher["version_output"]),
                source=normalized["executable"],
            )
        )
    dependencies.extend(_probe_root(Path(normalized["root"]), normalized["family"]))
    blockers = [
        row["id"]
        for row in dependencies
        if row["required"] and row["state"] in {"missing-manual", "incompatible"}
    ]
    return {
        "format": CHECK_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        "state": "attention" if blockers else "ready",
        "configured": record_present,
        "record_path": str(_absolute(record_path)),
        "selection": normalized,
        "dependencies": dependencies,
        "blockers": blockers,
        "tooling": "initialized" if not blockers else "attention",
        "account": next(
            row["state"] for row in dependencies if row["id"] == "launcher-account"
        ),
        "launcher_identity": launcher,
        "launcher_host": host,
        "boundary": {
            "instance": "not-created-by-setup",
            "account": (
                "presence-only; contents and profile selection remain launcher-owned"
            ),
            "launch": "not-attempted-by-setup",
        },
    }


def build_launcher_setup_plan(
    check: Mapping[str, Any],
    *,
    current_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    matches = False
    if current_record is not None:
        matches = (
            _validate_record(dict(current_record))["selection"]
            == check["selection"]
        )
    action = {
        "id": "verify-launcher-binding" if matches else "save-launcher-binding",
        "operation": "read-only-verification" if matches else "atomic-record-write",
        "source": check["record_path"] if matches else None,
        "destination": check["record_path"],
        "effect": (
            "Reuse the matching launcher binding without rewriting it."
            if matches
            else "Save only the reviewed launcher family and physical paths."
        ),
    }
    identity = {
        "selection": check["selection"],
        "dependencies": check["dependencies"],
        "actions": [action],
        "blockers": check["blockers"],
    }
    return {
        "format": PLAN_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "review-before-mutation",
        "plan_id": _digest("workbench-launcher-setup-plan", identity),
        "state": "blocked" if check["blockers"] else "ready",
        "selection": check["selection"],
        "dependencies": check["dependencies"],
        "actions": [action],
        "blockers": check["blockers"],
        "consent": {
            "required": True,
            "non_interactive": "Pass this exact plan_id with --apply.",
        },
    }


def _apply_plan(plan: Mapping[str, Any], *, record_path: Path) -> dict[str, Any]:
    if plan["blockers"]:
        raise LauncherSetupError(
            "launcher setup has manual blockers: " + ", ".join(plan["blockers"])
        )
    verification = inspect_launcher_setup(
        plan["selection"],
        record_path=record_path,
        record_present=record_path.is_file(),
    )
    if verification["state"] != "ready":
        raise LauncherSetupError(
            "launcher setup changed after planning: "
            + ", ".join(verification["blockers"])
        )
    action = plan["actions"][0]["id"]
    if action == "save-launcher-binding":
        record = _write_launcher_record(record_path, plan["selection"])
        outcome = "configured"
    else:
        record = load_launcher_record(record_path)
        if record is None or record["selection"] != plan["selection"]:
            raise LauncherSetupError("saved launcher binding changed after review")
        outcome = "reused"
    return {
        "format": RESULT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "applied_plan_id": plan["plan_id"],
        "record": record,
        "verification": verification,
        "readiness": {
            "tooling": "initialized",
            "instance": "not-materialized",
            "account": verification["account"],
            "launch": "not-attempted",
        },
        "next_command": ["workbench", "runtime-launch", str(Path.cwd())],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench launcher setup",
        description=(
            "Check and save a Prism Launcher or MultiMC installation without "
            "reading account data. Instance projection remains runtime-launch owned."
        ),
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--check", action="store_true")
    operation.add_argument("--plan", action="store_true")
    operation.add_argument("--apply", metavar="PLAN_ID")
    parser.add_argument("--family", choices=tuple(sorted(FAMILIES)))
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--launcher-root", type=Path)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit structured output with --check, --plan, or --apply",
    )
    return parser


def _selection_from_args(
    args: argparse.Namespace,
    prior: Mapping[str, Any] | None,
    *,
    interactive: bool,
    input_stream: TextIO,
    output: TextIO,
) -> dict[str, str]:
    previous = None if prior is None else dict(prior["selection"])
    managed_prism = None
    if (
        args.executable is None
        and (args.family or (previous or {}).get("family") or "prism") == "prism"
    ):
        try:
            managed_prism = inspect_tools(default_runtime_state_root())["tools"][
                "prism"
            ]["executable"]
        except (OSError, ValueError):
            managed_prism = None
    if not interactive:
        if (
            args.family is None
            and args.executable is None
            and args.launcher_root is None
            and previous is not None
        ):
            return _validate_selection(previous)
        if (
            args.family is None
            or (args.executable is None and managed_prism is None)
            or args.launcher_root is None
        ):
            raise LauncherSetupError(
                "select --family, --executable, and --launcher-root, or save a "
                "launcher binding first; `workbench tooling` can prepare Prism"
            )
        return _validate_selection(
            {
                "family": args.family,
                "executable": str(_absolute(args.executable or managed_prism)),
                "root": str(_absolute(args.launcher_root)),
            }
        )
    family_default = args.family or (previous or {}).get("family") or "prism"
    raw_family = _prompt(
        input_stream,
        output,
        f"Launcher family prism/multimc [{family_default}]: ",
    ).strip().casefold()
    family = raw_family or family_default
    if family not in FAMILIES:
        raise LauncherSetupError("launcher family must be prism or multimc")
    executable_default = str(
        args.executable
        or (previous or {}).get("executable")
        or (managed_prism if family == "prism" else None)
        or ""
    )
    executable = _prompt(
        input_stream,
        output,
        "Launcher executable"
        + (f" [{executable_default}]" if executable_default else "")
        + ": ",
    ).strip() or executable_default
    root_default = str(args.launcher_root or (previous or {}).get("root") or "")
    root = _prompt(
        input_stream,
        output,
        "Initialized launcher data root"
        + (f" [{root_default}]" if root_default else "")
        + ": ",
    ).strip() or root_default
    if not executable or not root:
        raise LauncherSetupError(
            "launcher executable and initialized data root are required"
        )
    return _validate_selection(
        {
            "family": family,
            "executable": str(_absolute(executable)),
            "root": str(_absolute(root)),
        }
    )


def _render_check(check: Mapping[str, Any]) -> str:
    lines = [
        "Workbench launcher setup",
        f"Status: {check['state']}",
        f"Launcher tooling: {check['tooling']}",
        f"Saved binding: {'yes' if check['configured'] else 'no'}",
        f"Launcher: {check['selection']['family']}",
        "",
        "Dependencies",
    ]
    for row in check["dependencies"]:
        lines.append(f"- {row['label']}: {row['state']}")
        lines.append(f"  {row['detail']}")
        if row["repair"]:
            lines.append(f"  Next: {row['repair']}")
    lines.extend(
        (
            "",
            "Setup does not create an instance or launch the game.",
            "After a complete payload exists, runtime-launch creates one fresh instance.",
        )
    )
    return "\n".join(lines) + "\n"


def _render_plan(plan: Mapping[str, Any]) -> str:
    lines = [
        "Workbench launcher setup plan",
        f"Plan: {plan['plan_id']}",
        f"State: {plan['state']}",
        f"Change: {plan['actions'][0]['effect']}",
        f"Destination: {plan['actions'][0]['destination']}",
    ]
    if plan["blockers"]:
        lines.append("Blockers: " + ", ".join(plan["blockers"]))
    return "\n".join(lines) + "\n"


def launcher_defaults_for_runtime(
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Fill only omitted runtime-launch physical options from a saved binding."""

    values = list(arguments)
    if not values or values[0] != "runtime-launch" or any(
        option in values for option in ("--help", "-h")
    ):
        return values

    def option_value(option: str) -> tuple[bool, str | None]:
        for index, token in enumerate(values):
            if token == option:
                if index + 1 >= len(values):
                    raise LauncherSetupError(f"{option} requires a value")
                return True, values[index + 1]
            prefix = option + "="
            if token.startswith(prefix):
                selected = token[len(prefix) :]
                if not selected:
                    raise LauncherSetupError(f"{option} requires a value")
                return True, selected
        return False, None

    has_executable, _explicit_executable = option_value("--launcher-executable")
    has_root, _explicit_root = option_value("--launcher-root")
    has_family, explicit_family = option_value("--launcher")
    if has_executable and has_root:
        return values
    record = load_launcher_record(
        default_launcher_record_path(environment=environment)
    )
    if record is None:
        return values
    selection = record["selection"]
    if explicit_family is not None and explicit_family != selection["family"]:
        raise LauncherSetupError(
            "saved launcher paths belong to a different family; pass both "
            "--launcher-executable and --launcher-root or reconfigure them"
        )
    if not has_family:
        values.extend(("--launcher", selection["family"]))
    if not has_executable:
        values.extend(("--launcher-executable", selection["executable"]))
    if not has_root:
        values.extend(("--launcher-root", selection["root"]))
    return values


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | str,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    record_path: Path | str | None = None,
) -> int:
    """Run the additive launcher setup journey."""

    del root  # Launcher setup owns no suite/profile authority.
    parser = _parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    selected_record = _absolute(
        record_path
        if record_path is not None
        else default_launcher_record_path(environment=environment)
    )
    try:
        prior = load_launcher_record(selected_record)
        explicit_operation = args.check or args.plan or args.apply is not None
        interactive = bool(getattr(stdin, "isatty", lambda: False)()) and bool(
            getattr(stdout, "isatty", lambda: False)()
        )
        if args.json and not explicit_operation:
            raise LauncherSetupError(
                "--json requires --check, --plan, or --apply PLAN_ID"
            )
        if not explicit_operation and not interactive:
            raise LauncherSetupError(
                "interactive launcher setup requires a TTY; use --plan with "
                "explicit paths, then --apply PLAN_ID"
            )
        selection = _selection_from_args(
            args,
            prior,
            interactive=interactive and not explicit_operation,
            input_stream=stdin,
            output=stdout,
        )
        check = inspect_launcher_setup(
            selection,
            record_path=selected_record,
            record_present=prior is not None,
        )
        if args.check:
            if args.json:
                stdout.write(
                    json.dumps(check, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            else:
                stdout.write(_render_check(check))
            return 0 if check["state"] == "ready" and check["configured"] else 1
        plan = build_launcher_setup_plan(check, current_record=prior)
        if args.plan:
            if args.json:
                stdout.write(
                    json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            else:
                stdout.write(_render_check(check) + "\n" + _render_plan(plan))
            return 0
        if args.apply is not None:
            if args.apply != plan["plan_id"]:
                raise LauncherSetupError(
                    "--apply does not match the current plan; rerun --plan"
                )
        else:
            stdout.write(_render_check(check) + "\n" + _render_plan(plan))
            if plan["blockers"]:
                return 1
            token = plan["plan_id"].rsplit(":", 1)[-1][:12]
            answer = _prompt(
                stdin,
                stdout,
                f"Type apply {token} to save this binding, or Enter to cancel: ",
            ).strip()
            if answer != f"apply {token}":
                stdout.write("Launcher setup cancelled; nothing was changed.\n")
                return 0
        result = _apply_plan(plan, record_path=selected_record)
        if args.json:
            stdout.write(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
        else:
            stdout.write(
                "Workbench launcher binding is saved.\n"
                f"Record: {result['record']['record_id']}\n"
                "Launcher tooling: initialized\n"
                f"Account: {result['readiness']['account']}\n"
                "Instance: not materialized\n"
                "Launch: not attempted\n"
                "Next: workbench runtime-launch WORKSPACE\n"
            )
        return 0
    except SetupCancelled:
        stdout.write("\nLauncher setup cancelled; nothing was changed.\n")
        return 0
    except (LauncherSetupError, OSError, ValueError) as exc:
        stderr.write(f"Workbench launcher setup failed: {exc}\n")
        return 2


__all__ = [
    "CHECK_FORMAT",
    "PLAN_FORMAT",
    "RECORD_FORMAT",
    "RESULT_FORMAT",
    "LauncherSetupError",
    "build_launcher_setup_plan",
    "default_launcher_record_path",
    "inspect_launcher_setup",
    "launcher_defaults_for_runtime",
    "load_launcher_record",
    "main",
]
