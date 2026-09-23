"""Git command construction that cannot execute configured worktree filters."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Mapping, Sequence


_FILTER_KEY = re.compile(
    rb"^filter\.(.+)\.(?:clean|process|required|smudge)$", re.IGNORECASE
)


class GitObservationError(RuntimeError):
    """Raised when Git cannot be made safe enough for read-only observation."""


def configured_git_executable(
    executable: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve an explicit/setup-selected Git before ordinary PATH discovery."""

    values = os.environ if environment is None else environment
    if executable:
        path = Path(executable).expanduser()
        if path.is_absolute():
            if not path.is_file():
                raise GitObservationError(
                    f"configured Git executable is unavailable: {path}"
                )
            return str(path)
        discovered = shutil.which(executable, path=values.get("PATH", ""))
        if discovered is None:
            raise GitObservationError(
                f"explicit Git executable is unavailable: {executable}"
            )
        return discovered
    selected = values.get("WORKBENCH_GIT_EXECUTABLE")
    if selected:
        path = Path(selected).expanduser()
        if not path.is_absolute():
            raise GitObservationError(
                "WORKBENCH_GIT_EXECUTABLE must be an absolute path"
            )
        if not path.is_file():
            raise GitObservationError(
                f"configured Git executable is unavailable: {path}"
            )
        return str(path)
    return shutil.which("git", path=values.get("PATH", ""))


def require_configured_git_executable(
    executable: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the selected Git executable or fail with one precise diagnostic.

    Mutation-capable callers must not use :func:`safe_git_prefix`, because that
    helper deliberately constructs a read-only observation command.  They can
    use this narrower binding primitive and retain their own command policy.
    """

    selected = configured_git_executable(executable, environment=environment)
    if selected is None:
        raise GitObservationError("Git executable is unavailable")
    return selected


def observation_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def safe_git_prefix(
    workspace: Path | str,
    *,
    executable: str | None = None,
    timeout: float = 5.0,
) -> list[str]:
    """Return Git argv prefix with every configured content filter disabled.

    `git status` may otherwise execute repository-defined clean/process filters
    while hashing a touched file.  Listing config keys is data-only; if that
    inventory cannot be completed, callers must not fall back to unsafe status.
    """

    root = Path(workspace).expanduser().resolve()
    selected = configured_git_executable(executable)
    if selected is None:
        raise GitObservationError("Git executable is unavailable")
    base = [
        selected,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
    ]
    try:
        configured = subprocess.run(
            [*base, "-C", str(root), "config", "--null", "--name-only", "--list"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=observation_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitObservationError(
            "Git configuration could not be inventoried without execution"
        ) from exc
    if configured.returncode:
        detail = (configured.stderr or configured.stdout).decode(
            "utf-8", "replace"
        ).strip()
        raise GitObservationError(
            f"Git configuration inventory failed: {detail or configured.returncode}"
        )

    if configured.stdout and not configured.stdout.endswith(b"\0"):
        raise GitObservationError(
            "Git configuration inventory returned malformed framing"
        )

    filters: set[str] = set()
    for key in configured.stdout.split(b"\0"):
        match = _FILTER_KEY.fullmatch(key)
        if match:
            try:
                filters.add(match.group(1).decode("utf-8", "strict"))
            except UnicodeDecodeError as exc:
                raise GitObservationError(
                    "Git configuration contains a non-UTF-8 filter name"
                ) from exc
    for name in sorted(filters):
        base.extend(
            [
                "-c",
                f"filter.{name}.clean=",
                "-c",
                f"filter.{name}.smudge=",
                "-c",
                f"filter.{name}.process=",
                "-c",
                f"filter.{name}.required=false",
            ]
        )
    return base


def run_git_observation(
    workspace: Path | str,
    arguments: Sequence[str],
    *,
    executable: str | None = None,
    timeout: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    prefix = safe_git_prefix(workspace, executable=executable, timeout=timeout)
    return subprocess.run(
        [*prefix, "-C", str(Path(workspace).expanduser().resolve()), *arguments],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        timeout=timeout,
        env=observation_environment(),
    )


__all__ = [
    "GitObservationError",
    "configured_git_executable",
    "observation_environment",
    "require_configured_git_executable",
    "run_git_observation",
    "safe_git_prefix",
]
