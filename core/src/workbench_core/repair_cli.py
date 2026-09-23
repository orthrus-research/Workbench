"""Reviewed, host-aware repair for Workbench system requirements."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import threading
from typing import Any, TextIO

from workbench_core.host_requirements import inspect_host_requirements, measure_executable
from workbench_core.human_presentation import HumanPresentation, human_presentation
from workbench_core.setup_cli import (
    SetupError,
    default_setup_record_path,
    load_setup_record,
    replace_setup_git_binding,
    setup_record_lock,
)


CHECK_FORMAT = "workbench-repair-check-v1"
PLAN_FORMAT = "workbench-repair-plan-v1"
RESULT_FORMAT = "workbench-repair-result-v1"
SCHEMA_VERSION = 1
MAX_INVALID_SETUP_BYTES = 1024 * 1024
MAX_PACKAGE_OUTPUT_BYTES = 64 * 1024
PACKAGE_COMMAND_TIMEOUT_SECONDS = 1800


class RepairError(ValueError):
    """A repair input, plan, or owned operation is invalid."""


class RepairPartialError(RepairError):
    """A host command ran, but the complete repair did not verify."""

    def __init__(self, message: str, result: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.result = dict(result)


class RepairCancelled(Exception):
    """The user cancelled interactive repair."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(prefix: str, value: Any) -> str:
    return prefix + ":sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _observe_invalid_setup_record(path: Path) -> dict[str, Any]:
    """Measure a bad setup path without following it or retaining its bytes."""

    try:
        before = path.lstat()
    except FileNotFoundError:
        return {
            "kind": "missing",
            "recoverable": False,
            "size_bytes": None,
            "device": None,
            "inode": None,
            "mode": None,
            "mtime_ns": None,
            "sha256": None,
            "observation_id": "workbench-invalid-setup:missing",
        }
    except OSError as exc:
        return {
            "kind": "unreadable",
            "recoverable": False,
            "size_bytes": None,
            "device": None,
            "inode": None,
            "mode": None,
            "mtime_ns": None,
            "sha256": None,
            "observation_id": f"workbench-invalid-setup:unreadable:{type(exc).__name__}",
        }
    kind = (
        "symlink"
        if stat.S_ISLNK(before.st_mode)
        else "regular-file"
        if stat.S_ISREG(before.st_mode)
        else "other"
    )
    content_digest: str | None = None
    recoverable = False
    descriptor: int | None = None
    try:
        if kind == "symlink":
            target = os.readlink(path)
            if len(os.fsencode(target)) <= 16 * 1024:
                content_digest = sha256(os.fsencode(target)).hexdigest()
                recoverable = True
        elif kind == "regular-file" and 0 <= before.st_size <= MAX_INVALID_SETUP_BYTES:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != before.st_size:
                raise OSError("setup record identity changed")
            digest = sha256()
            consumed = 0
            while True:
                block = os.read(descriptor, min(64 * 1024, MAX_INVALID_SETUP_BYTES - consumed + 1))
                if not block:
                    break
                consumed += len(block)
                if consumed > MAX_INVALID_SETUP_BYTES:
                    raise OSError("setup record exceeded recovery bound")
                digest.update(block)
            after = os.fstat(descriptor)
            current = path.lstat()
            fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
            if any(
                getattr(opened, field) != getattr(observed, field)
                for observed in (after, current)
                for field in fields
            ):
                raise OSError("setup record changed during observation")
            content_digest = digest.hexdigest()
            recoverable = True
    except OSError:
        recoverable = False
        content_digest = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    payload = {
        "kind": kind,
        "recoverable": recoverable,
        "size_bytes": before.st_size,
        "device": before.st_dev,
        "inode": before.st_ino,
        "mode": stat.S_IMODE(before.st_mode),
        "mtime_ns": before.st_mtime_ns,
        "sha256": content_digest,
    }
    return {
        **payload,
        "observation_id": _digest("workbench-invalid-setup", payload),
    }


def _invalid_setup_recovery_path(path: Path, observation: Mapping[str, Any]) -> Path:
    token = str(observation["observation_id"]).rsplit(":", 1)[-1][:12]
    return path.with_name(f"{path.name}.invalid-{token}.bak")


def inspect_repair(
    *,
    environment: Mapping[str, str] | None = None,
    record_path: Path | str | None = None,
    explicit_git: Path | str | None = None,
) -> dict[str, Any]:
    values = dict(os.environ if environment is None else environment)
    selected_record_path = Path(
        record_path
        if record_path is not None
        else default_setup_record_path(environment=values)
    ).expanduser().absolute()
    record_error: str | None = None
    invalid_observation: dict[str, Any] | None = None
    recovery_path: Path | None = None
    try:
        record = load_setup_record(selected_record_path)
    except SetupError as exc:
        record = None
        record_error = str(exc)
        invalid_observation = _observe_invalid_setup_record(selected_record_path)
        if invalid_observation["recoverable"]:
            recovery_path = _invalid_setup_recovery_path(
                selected_record_path,
                invalid_observation,
            )
    configured_git = (
        record["selection"].get("git_executable") if record is not None else None
    )
    host_check = inspect_host_requirements(
        environment=values,
        configured_git=configured_git,
        explicit_git=explicit_git,
    )
    git = host_check["requirements"]["git"]
    binding_state = "invalid" if record_error is not None else "not-configured"
    if record is not None:
        binding_state = (
            "current"
            if git["state"] == "ready" and configured_git == git["executable"]
            else "repair-needed"
        )
    state = host_check["state"]
    if record is not None and binding_state == "repair-needed" and state == "ready":
        state = "repairable"
    if record_error is not None:
        if invalid_observation is not None and invalid_observation["recoverable"]:
            if state != "attention":
                state = "repairable"
        else:
            state = "attention"
    return {
        "format": CHECK_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        "state": state,
        "host": host_check["host"],
        "setup": {
            "record_path": str(selected_record_path),
            "record_id": record["record_id"] if record is not None else None,
            "state": (
                "configured"
                if record is not None
                else "invalid"
                if record_error is not None
                else "not-configured"
            ),
            "git_binding": binding_state,
            "error": record_error,
            "invalid_observation": invalid_observation,
            "recovery_path": str(recovery_path) if recovery_path is not None else None,
        },
        "requirements": host_check["requirements"],
    }


def build_repair_plan(check: Mapping[str, Any]) -> dict[str, Any]:
    git = check["requirements"]["git"]
    setup = check["setup"]
    actions: list[dict[str, Any]] = []
    blockers: list[str] = []
    setup_recovery_action: dict[str, Any] | None = None
    if setup["state"] == "invalid":
        observation = setup.get("invalid_observation")
        if isinstance(observation, Mapping) and observation.get("recoverable") is True:
            setup_recovery_action = {
                "id": "quarantine-invalid-setup",
                "operation": "atomic-recoverable-move",
                "component": "workbench-setup",
                "source": setup["record_path"],
                "destination": setup["recovery_path"],
                "command": None,
                "executable_identities": [],
                "setup_observation": dict(observation),
                "effect": (
                    "Move the invalid setup entry to a content-bound sibling backup. "
                    "Its bytes or symlink text are preserved for recovery."
                ),
            }
        else:
            blockers.append("setup-record")
    if git["state"] == "ready":
        if setup["state"] == "configured" and setup["git_binding"] != "current":
            actions.append(
                {
                    "id": "bind-existing-git",
                    "operation": "atomic-setup-record-update",
                    "component": "git",
                    "source": git["executable"],
                    "destination": setup["record_path"],
                    "command": None,
                    "executable_identities": [],
                    "setup_observation": None,
                    "effect": (
                        "Save the validated Git executable in the existing Workbench "
                        "setup record. System PATH is unchanged."
                    ),
                }
            )
        else:
            actions.append(
                {
                    "id": "verify-existing-git",
                    "operation": "read-only-verification",
                    "component": "git",
                    "source": git["executable"],
                    "destination": setup["record_path"] if setup["state"] == "configured" else None,
                    "command": None,
                    "executable_identities": [],
                    "setup_observation": None,
                    "effect": "Reuse the validated Git installation without changing the host.",
                }
            )
    else:
        repair = git["repair"]
        if repair["state"] == "available":
            actions.append(
                {
                    "id": "install-git",
                    "operation": "host-package-install",
                    "component": "git",
                    "source": repair["manager"],
                    "destination": "host Git installation",
                    "command": repair["command"],
                    "executable_identities": repair["executable_identities"],
                    "setup_observation": None,
                    "effect": repair["detail"],
                }
            )
            if setup["state"] == "configured":
                actions.append(
                    {
                        "id": "bind-installed-git",
                        "operation": "atomic-setup-record-update",
                        "component": "git",
                        "source": "discovered after installation",
                        "destination": setup["record_path"],
                        "command": None,
                        "executable_identities": [],
                        "setup_observation": None,
                        "effect": (
                            "Validate the installed Git and save its exact executable "
                            "in the existing Workbench setup record."
                        ),
                    }
                )
        else:
            blockers.append("git")
    if setup_recovery_action is not None:
        actions.append(setup_recovery_action)
    identity = {
        "host": check["host"],
        "setup": check["setup"],
        "requirements": check["requirements"],
        "actions": actions,
        "blockers": blockers,
    }
    return {
        "format": PLAN_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "review-before-host-mutation",
        "plan_id": _digest("workbench-repair-plan", identity),
        "state": "blocked" if blockers else "ready",
        "host": check["host"],
        "setup": check["setup"],
        "requirements": check["requirements"],
        "actions": actions,
        "blockers": blockers,
        "consent": {
            "required": any(row["operation"] != "read-only-verification" for row in actions),
            "non_interactive": "Pass this exact plan_id with --apply.",
        },
    }


def _render_check(
    check: Mapping[str, Any],
    *,
    presentation: HumanPresentation | None = None,
) -> str:
    view = human_presentation() if presentation is None else presentation
    host = check["host"]
    git = check["requirements"]["git"]
    state = str(check["state"])
    check_label = view.label(
        "ready" if state == "ready" else "attention",
        "good" if state == "ready" else "attention",
    )
    setup = check["setup"]
    setup_state = str(setup["state"])
    setup_detail = setup_state
    if setup_state == "configured":
        binding = str(setup.get("git_binding", "unknown"))
        if binding == "current":
            setup_label = view.label("ready", "good")
        else:
            setup_detail += " · Git binding " + binding.replace("-", " ")
            setup_label = view.label("attention", "attention")
    elif setup_state == "invalid":
        setup_label = view.label("blocked", "blocked")
    else:
        setup_label = view.label("optional", "attention")
    lines = [
        f"Workbench system repair check {check_label}",
        f"  Host: {host['system']} {host['machine']} ({host['family']})",
        f"  Setup: {setup_detail} {setup_label}",
    ]
    if git["state"] == "ready":
        lines.append(
            f"  Git: {git['version']} — {git['executable']} "
            f"{view.label('ready', 'good')}"
        )
    else:
        git_state = str(git["state"])
        git_label = view.label(
            "missing" if git_state == "missing" else "blocked",
            "blocked",
        )
        repair = git["repair"]
        repair_available = repair["state"] == "available"
        repair_label = view.label(
            "available" if repair_available else "blocked",
            "good" if repair_available else "blocked",
        )
        lines.append(f"  Git: {git_state} — {git['detail']} {git_label}")
        lines.append(f"    Repair: {repair['detail']} {repair_label}")
    return "\n".join(lines) + "\n"


def _display_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def _run_bounded_package_command(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
) -> tuple[int, bytes, bool, bool]:
    """Drain a package command while retaining only its fixed-size output tail."""

    process = subprocess.Popen(
        list(command),
        env=None if environment is None else dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    retained = bytearray()
    total_bytes = 0
    guard = threading.Lock()

    def drain() -> None:
        nonlocal total_bytes
        stream = process.stdout
        if stream is None:
            return
        try:
            while True:
                block = stream.read(8192)
                if not block:
                    break
                with guard:
                    total_bytes += len(block)
                    if len(block) >= MAX_PACKAGE_OUTPUT_BYTES:
                        retained[:] = block[-MAX_PACKAGE_OUTPUT_BYTES:]
                    else:
                        retained.extend(block)
                        overflow = len(retained) - MAX_PACKAGE_OUTPUT_BYTES
                        if overflow > 0:
                            del retained[:overflow]
        except (OSError, ValueError):
            return

    reader = threading.Thread(
        target=drain,
        name="workbench-package-output",
        daemon=True,
    )
    reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=PACKAGE_COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait()
    reader.join(timeout=2)
    if reader.is_alive() and process.stdout is not None:
        try:
            process.stdout.close()
        except OSError:
            pass
        reader.join(timeout=1)
    elif process.stdout is not None:
        process.stdout.close()
    with guard:
        output = bytes(retained)
        truncated = total_bytes > MAX_PACKAGE_OUTPUT_BYTES
    return returncode, output, truncated, timed_out


def _render_plan(
    plan: Mapping[str, Any],
    *,
    presentation: HumanPresentation | None = None,
) -> str:
    view = human_presentation() if presentation is None else presentation
    blocked = bool(plan["blockers"])
    plan_label = view.label(
        "blocked" if blocked else "ready",
        "blocked" if blocked else "good",
    )
    lines = [
        f"Workbench repair plan {plan_label}",
        f"  Plan: {plan['plan_id']}",
        f"  Actions ({len(plan['actions'])})",
    ]
    for action in plan["actions"]:
        read_only = action["operation"] == "read-only-verification"
        action_label = view.label(
            "check" if read_only else "change",
            "good" if read_only else "attention",
        )
        lines.append(f"    {action_label} {action['effect']}")
        if action["command"] is not None:
            lines.append("      Command: " + _display_command(action["command"]))
        if action["destination"] is not None:
            lines.append(f"      Destination: {action['destination']}")
    if not plan["actions"]:
        lines.append(f"    {view.label('ready', 'good')} No changes required.")
    if plan["blockers"]:
        lines.append(
            "    "
            + view.label("blocked", "blocked")
            + " Manual repair required: "
            + ", ".join(plan["blockers"])
        )
    return "\n".join(lines) + "\n"


def _apply_plan(
    plan: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None,
    explicit_git: Path | str | None,
    output: TextIO,
    structured_output: bool = False,
) -> dict[str, Any]:
    if plan["blockers"]:
        raise RepairError("repair plan has manual blockers: " + ", ".join(plan["blockers"]))
    installed: list[dict[str, Any]] = []
    record_id: str | None = plan["setup"].get("record_id")
    recovered_setup_record: str | None = None

    planned_requirements = plan.get("requirements")
    planned_git = (
        planned_requirements.get("git")
        if isinstance(planned_requirements, Mapping)
        else None
    )
    planned_git_identity = (
        planned_git.get("executable_identity")
        if isinstance(planned_git, Mapping)
        else None
    )

    def require_planned_git_identity(observed: Mapping[str, Any]) -> None:
        """Close the plan/apply gap for a Git executable reused by the plan."""

        if planned_git_identity is None:
            return
        if observed.get("executable_identity") != planned_git_identity:
            raise RepairError("the Git executable changed after plan review")

    def partial_result(
        message: str,
        *,
        git_observation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed = dict(git_observation or {})
        return {
            "format": RESULT_FORMAT,
            "schema_version": SCHEMA_VERSION,
            "outcome": "partial",
            "applied_plan_id": plan["plan_id"],
            "installed": installed,
            "git": {
                "state": observed.get("state", "unknown"),
                "executable": observed.get("executable"),
                "version": observed.get("version"),
            },
            "setup_record_id": record_id,
            "recovered_setup_record": recovered_setup_record,
            "failure": message,
            "next_commands": [["workbench", "repair", "--check"]],
        }

    def fail_after_host_command(
        message: str,
        *,
        git_observation: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        error = RepairPartialError(
            message,
            partial_result(message, git_observation=git_observation),
        )
        if cause is not None:
            raise error from cause
        raise error
    for action in plan["actions"]:
        if action["id"] != "install-git":
            continue
        command = action["command"]
        if not isinstance(command, list) or not command:
            raise RepairError("Git install action has no exact command")
        identities = action.get("executable_identities")
        if not isinstance(identities, list) or not identities:
            raise RepairError("Git install action has no executable identities")
        for expected in identities:
            if not isinstance(expected, dict) or not isinstance(expected.get("path"), str):
                raise RepairError("Git install action has an invalid executable identity")
            try:
                observed = measure_executable(
                    expected["path"],
                    allow_windows_winget_alias=expected.get("size_bytes") == 0,
                )
            except ValueError as exc:
                raise RepairError("a repair executable is no longer usable") from exc
            if observed != expected:
                raise RepairError("a repair executable changed after plan review")
        output.write("Running: " + _display_command(command) + "\n")
        output.flush()
        try:
            if structured_output:
                returncode, raw_output, truncated, timed_out = (
                    _run_bounded_package_command(
                        command,
                        environment=environment,
                    )
                )
                child_output = raw_output.decode("utf-8", errors="replace")
                if child_output:
                    if truncated:
                        output.write("[earlier package-manager output omitted]\n")
                    output.write(child_output)
                    if not child_output.endswith("\n"):
                        output.write("\n")
                    output.flush()
                if timed_out:
                    raise subprocess.TimeoutExpired(
                        command,
                        PACKAGE_COMMAND_TIMEOUT_SECONDS,
                    )
                completed = subprocess.CompletedProcess(command, returncode)
            else:
                completed = subprocess.run(
                    command,
                    check=False,
                    env=None if environment is None else dict(environment),
                    timeout=PACKAGE_COMMAND_TIMEOUT_SECONDS,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            installed.append(
                {
                    "component": "git",
                    "manager": action["source"],
                    "outcome": "timed-out" if isinstance(exc, subprocess.TimeoutExpired) else "spawn-failed",
                }
            )
            fail_after_host_command(
                f"Git installation did not complete ({type(exc).__name__})",
                cause=exc,
            )
        if completed.returncode:
            installed.append(
                {
                    "component": "git",
                    "manager": action["source"],
                    "outcome": f"failed-exit-{completed.returncode}",
                }
            )
            fail_after_host_command(
                f"Git installation failed with exit {completed.returncode}"
            )
        installed.append({"component": "git", "manager": action["source"], "outcome": "installed"})

    verification = inspect_repair(
        environment=environment,
        record_path=plan["setup"]["record_path"],
        explicit_git=explicit_git,
    )
    git = verification["requirements"]["git"]
    if git["state"] != "ready":
        if installed:
            fail_after_host_command(
                "Git is still unavailable after the host package command",
                git_observation=git,
            )
        raise RepairError("Git is still unavailable after repair")
    require_planned_git_identity(git)
    record_id = verification["setup"]["record_id"]
    if verification["setup"]["state"] == "configured" and any(
        row["id"] in {"bind-existing-git", "bind-installed-git"}
        for row in plan["actions"]
    ):
        try:
            record = replace_setup_git_binding(
                plan["setup"]["record_path"],
                expected_record_id=plan["setup"]["record_id"],
                git_executable=git["executable"],
                expected_git_identity=git["executable_identity"],
            )
        except (OSError, SetupError, ValueError) as exc:
            if installed:
                fail_after_host_command(
                    "Git was installed, but its setup binding failed",
                    git_observation=git,
                    cause=exc,
                )
            raise
        record_id = record["record_id"]
    quarantine = next(
        (row for row in plan["actions"] if row["id"] == "quarantine-invalid-setup"),
        None,
    )
    if quarantine is not None:
        source = Path(quarantine["source"])
        destination = Path(quarantine["destination"])
        expected_observation = quarantine.get("setup_observation")
        if not isinstance(expected_observation, Mapping):
            if installed:
                fail_after_host_command(
                    "Git was installed, but setup recovery lost its observation",
                    git_observation=git,
                )
            raise RepairError("invalid setup recovery lacks its reviewed observation")
        try:
            with setup_record_lock(source):
                if _observe_invalid_setup_record(source) != dict(expected_observation):
                    raise RepairError("the invalid setup entry changed after plan review")
                parent = source.parent
                parent_info = parent.lstat()
                if parent.is_symlink() or not stat.S_ISDIR(parent_info.st_mode):
                    raise RepairError("the setup record parent is not a regular directory")
                try:
                    destination.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise RepairError("the setup recovery destination already exists")
                os.replace(source, destination)
        except (OSError, RepairError, SetupError) as exc:
            if installed:
                fail_after_host_command(
                    "Git was installed, but setup recovery could not complete",
                    git_observation=git,
                    cause=exc,
                )
            if isinstance(exc, RepairError):
                raise
            raise RepairError("the invalid setup entry could not be quarantined") from exc
        recovered_setup_record = str(destination)
    final = inspect_repair(
        environment=environment,
        record_path=plan["setup"]["record_path"],
        explicit_git=explicit_git,
    )
    final_git = final["requirements"]["git"]
    if final_git["state"] != "ready":
        if installed:
            fail_after_host_command(
                "Git changed before final repair verification",
                git_observation=final_git,
            )
        raise RepairError("Git changed before final repair verification")
    require_planned_git_identity(final_git)
    return {
        "format": RESULT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "outcome": (
            "repaired"
            if installed
            or recovered_setup_record is not None
            or record_id != plan["setup"]["record_id"]
            else "ready"
        ),
        "applied_plan_id": plan["plan_id"],
        "installed": installed,
        "git": {
            "state": "ready",
            "executable": final_git["executable"],
            "version": final_git["version"],
        },
        "setup_record_id": record_id,
        "recovered_setup_record": recovered_setup_record,
        "failure": None,
        "next_commands": (
            [["workbench", "setup"]]
            if final["setup"]["state"] == "not-configured"
            else [["workbench", "setup", "--check"], ["workbench", "open"]]
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench repair",
        description=(
            "Check and repair baseline Workbench system requirements. Workbench "
            "searches standard Linux and Windows Git locations before proposing an "
            "OS-native package-manager command, and never runs it without consent."
        ),
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--check", action="store_true", help="inspect the host without changing it")
    operation.add_argument("--plan", action="store_true", help="show the exact repair plan without applying it")
    operation.add_argument("--apply", metavar="PLAN_ID", help="apply one exact reviewed repair plan")
    parser.add_argument("--git-executable", type=Path, help="validate and bind this exact Git executable")
    parser.add_argument("--json", action="store_true", help="emit structured output (requires --check, --plan, or --apply)")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    record_path: Path | str | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    stdout_view = human_presentation(stdout, environment=environment)
    stderr_view = human_presentation(stderr, environment=environment)
    explicit_operation = args.check or args.plan or args.apply is not None
    interactive = bool(getattr(stdin, "isatty", lambda: False)()) and bool(
        getattr(stdout, "isatty", lambda: False)()
    )
    try:
        if args.json and not explicit_operation:
            raise RepairError("--json requires --check, --plan, or --apply PLAN_ID")
        if not explicit_operation and not interactive:
            raise RepairError(
                "interactive repair requires a TTY; use --check, --plan, or "
                "review --plan --json and pass its exact plan_id with --apply"
            )
        check = inspect_repair(
            environment=environment,
            record_path=record_path,
            explicit_git=args.git_executable,
        )
        if args.check:
            stdout.write(
                json.dumps(check, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_check(check, presentation=stdout_view)
            )
            return 0 if check["state"] == "ready" else 1
        plan = build_repair_plan(check)
        if args.plan:
            stdout.write(
                json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_check(check, presentation=stdout_view)
                + "\n"
                + _render_plan(plan, presentation=stdout_view)
            )
            return 0
        if args.apply is not None:
            if args.apply != plan["plan_id"]:
                raise RepairError(
                    "--apply does not match the current plan; rerun --plan and review it"
                )
        else:
            stdout.write(
                _render_check(check, presentation=stdout_view)
                + "\n"
                + _render_plan(plan, presentation=stdout_view)
            )
            if plan["blockers"]:
                return 1
            if not plan["consent"]["required"]:
                stdout.write(
                    stdout_view.label("ready", "good")
                    + " No repair is needed.\n"
                )
                return 0
            token = plan["plan_id"].rsplit(":", 1)[-1][:12]
            stdout.write(f"Type apply {token} to make these changes, or Enter to cancel: ")
            stdout.flush()
            answer = stdin.readline()
            if answer == "":
                raise RepairCancelled
            if answer.rstrip("\r\n") != f"apply {token}":
                stdout.write(
                    stdout_view.label("cancelled", "attention")
                    + " Repair cancelled; nothing was changed.\n"
                )
                return 0
        result = _apply_plan(
            plan,
            environment=environment,
            explicit_git=args.git_executable,
            output=stderr if args.json else stdout,
            structured_output=args.json,
        )
        stdout.write(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.json
            else (
                f"Workbench repair {stdout_view.label('ready', 'good')} "
                f"{result['outcome']}.\n"
                f"  Git: {result['git']['version']} — "
                f"{result['git']['executable']}\n"
            )
        )
        return 0
    except RepairCancelled:
        stdout.write(
            "\n"
            + stdout_view.label("cancelled", "attention")
            + " Repair cancelled; nothing was changed.\n"
        )
        return 0
    except RepairPartialError as exc:
        if args.json:
            stdout.write(
                json.dumps(
                    exc.result,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        stderr.write(
            stderr_view.label("blocked", "blocked")
            + f" Workbench repair incomplete: {exc}\n"
        )
        return 2
    except (OSError, RepairError, SetupError, ValueError) as exc:
        stderr.write(
            stderr_view.label("blocked", "blocked")
            + f" Workbench repair failed: {exc}\n"
        )
        return 2


__all__ = [
    "CHECK_FORMAT",
    "PLAN_FORMAT",
    "RESULT_FORMAT",
    "RepairError",
    "build_repair_plan",
    "inspect_repair",
    "main",
]
