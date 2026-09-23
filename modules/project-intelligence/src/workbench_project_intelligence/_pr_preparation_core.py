"""Private Git mechanics shared by the current PR preparation workflow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .git_tree import display_git_path


MAX_RECEIPT_BYTES = 512 * 1024
MAX_PULL_REQUEST_NUMBER = 2_147_483_647
MAX_CHANGED_PATHS = 100_000
MAX_CHANGED_PATH_BYTES = 64 * 1024 * 1024
MAX_SINGLE_PATH_BYTES = 4_096


class PullRequestPreparationError(RuntimeError):
    """The pull-request preparation plan, fetch, or receipt is invalid."""


class PullRequestPreparationCancelled(Exception):
    """The user declined the exact network-write plan."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _identity(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def _git_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    values = dict(os.environ if environment is None else environment)
    values.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    values.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    for key in (
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    ):
        values.pop(key, None)
    return values


def _run_git(
    executable: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [executable, *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_git_environment(environment),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PullRequestPreparationError(
            f"Git pull-request preparation did not complete ({type(exc).__name__})"
        ) from exc


def _run_git_bytes(
    executable: str,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [executable, *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=_git_environment(environment),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PullRequestPreparationError(
            f"Git pull-request preparation did not complete ({type(exc).__name__})"
        ) from exc


def _git_text(
    executable: str,
    repository: Path,
    arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
    timeout: float = 30.0,
) -> str:
    completed = _run_git(
        executable,
        ("-c", "core.longpaths=true", "-C", str(repository), *arguments),
        environment=environment,
        timeout=timeout,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[:2000]
        raise PullRequestPreparationError(
            f"Git {' '.join(arguments)} failed"
            + (f": {detail}" if detail else f" (exit {completed.returncode})")
        )
    value = completed.stdout.strip()
    if not value or any(character in value for character in "\x00\r\n"):
        raise PullRequestPreparationError(
            f"Git {' '.join(arguments)} returned malformed text"
        )
    return value


def _changed_paths(
    executable: str,
    repository: Path,
    before_oid: str,
    after_oid: str,
    *,
    environment: Mapping[str, str] | None,
) -> tuple[str, ...]:
    """Observe exact committed scope without filters, textconv, or renames."""

    completed = _run_git_bytes(
        executable,
        (
            "-c",
            "core.longpaths=true",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-C",
            str(repository),
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            before_oid,
            after_oid,
            "--",
        ),
        environment=environment,
        timeout=60.0,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", "replace"
        ).strip()[:2000]
        raise PullRequestPreparationError(
            "cannot observe prepared pull-request changed paths"
            + (f": {detail}" if detail else f" (exit {completed.returncode})")
        )
    raw = completed.stdout
    if len(raw) > MAX_CHANGED_PATH_BYTES:
        raise PullRequestPreparationError(
            "prepared pull-request changed-path output exceeds its byte limit"
        )
    if raw and not raw.endswith(b"\0"):
        raise PullRequestPreparationError(
            "prepared pull-request changed-path output is malformed"
        )
    parts = raw[:-1].split(b"\0") if raw else []
    if len(parts) > MAX_CHANGED_PATHS:
        raise PullRequestPreparationError(
            "prepared pull request exceeds its changed-path count limit"
        )
    if any(not path or len(path) > MAX_SINGLE_PATH_BYTES for path in parts):
        raise PullRequestPreparationError(
            "prepared pull-request changed-path output contains an invalid path"
        )
    return tuple(display_git_path(path) for path in parts)


def _repository_root(
    repository: Path | str,
    *,
    git_executable: str,
    environment: Mapping[str, str] | None,
) -> Path:
    selected = Path(repository).expanduser()
    try:
        selected.lstat()
    except OSError as exc:
        raise PullRequestPreparationError(f"cannot inspect --source: {exc}") from exc
    if selected.is_symlink() or not selected.is_dir():
        raise PullRequestPreparationError(
            "--source must be a regular non-symlink directory"
        )
    rendered = _git_text(
        git_executable,
        selected,
        ("rev-parse", "--show-toplevel"),
        environment=environment,
    )
    try:
        root = Path(rendered).resolve(strict=True)
        root.lstat()
    except OSError as exc:
        raise PullRequestPreparationError("Git repository root is unavailable") from exc
    if root.is_symlink() or not root.is_dir():
        raise PullRequestPreparationError("Git repository root is unsafe")
    return root
