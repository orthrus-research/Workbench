"""Profile-driven, consented acquisition of an exact project revision."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, TextIO
from urllib.parse import urlsplit

from workbench_api.source_checkouts import SourceCheckoutError, open_source_checkout

from .git_observation import GitObservationError, configured_git_executable


PROFILE_FORMAT = "workbench-project-acquisition-profile-v1"
PLAN_FORMAT = "workbench-project-acquisition-plan-v1"
PLAN_FORMAT_V2 = "workbench-project-acquisition-plan-v2"
PLAN_FORMAT_V3 = "workbench-project-acquisition-plan-v3"
RECEIPT_FORMAT = "workbench-project-acquisition-receipt-v1"
RECEIPT_FORMAT_V2 = "workbench-project-acquisition-receipt-v2"
RESULT_FORMAT = "workbench-project-acquisition-result-v1"
RESULT_FORMAT_V2 = "workbench-project-acquisition-result-v2"
RESULT_FORMAT_V3 = "workbench-project-acquisition-result-v3"
BRANCH_LIST_FORMAT = "workbench-project-branch-list-v1"
SCHEMA_VERSION = 1
MAX_PROFILE_BYTES = 256 * 1024
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_REF = re.compile(r"^refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class ProjectAcquisitionError(RuntimeError):
    """The acquisition profile, plan, remote, or checkout is unsafe or invalid."""


class ProjectAcquisitionCancelled(Exception):
    """The user declined an interactive acquisition plan."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _identity(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def _safe_relative_path(value: str, label: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ProjectAcquisitionError(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or value != "/".join(path.parts)
    ):
        raise ProjectAcquisitionError(f"{label} is not a safe relative path")
    return path.as_posix()


def load_acquisition_profile(path_value: Path | str) -> dict[str, Any]:
    """Load one strict, suite-owned project acquisition profile."""

    path = Path(path_value).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProjectAcquisitionError(f"cannot inspect acquisition profile: {exc}") from exc
    if path.is_symlink() or not path.is_file():
        raise ProjectAcquisitionError("acquisition profile must be a regular non-symlink file")
    if not 1 <= info.st_size <= MAX_PROFILE_BYTES:
        raise ProjectAcquisitionError("acquisition profile is outside its byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectAcquisitionError("acquisition profile is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ProjectAcquisitionError("acquisition profile must be one JSON object")
    expected = {
        "format",
        "schema_version",
        "profile_id",
        "project_id",
        "display_name",
        "remote",
        "default_channel",
        "channels",
        "workspace",
    }
    if set(value) != expected:
        raise ProjectAcquisitionError("acquisition profile has unsupported or missing fields")
    if value.get("format") != PROFILE_FORMAT or value.get("schema_version") != 1:
        raise ProjectAcquisitionError("acquisition profile format is unsupported")
    for field in ("profile_id", "project_id", "display_name", "default_channel"):
        if type(value.get(field)) is not str or not value[field]:
            raise ProjectAcquisitionError(f"acquisition profile {field} must be text")

    remote = value.get("remote")
    if type(remote) is not dict or set(remote) != {
        "kind",
        "url",
        "provider",
        "pull_request_ref_template",
    }:
        raise ProjectAcquisitionError("acquisition profile remote is invalid")
    if remote.get("kind") != "git":
        raise ProjectAcquisitionError("only Git acquisition profiles are supported")
    for field in ("url", "provider"):
        if type(remote.get(field)) is not str or not remote[field]:
            raise ProjectAcquisitionError(f"acquisition remote {field} must be text")
    template = remote.get("pull_request_ref_template")
    if template is not None and (
        type(template) is not str or template.count("{number}") != 1
    ):
        raise ProjectAcquisitionError("pull-request ref template must contain {number} once")

    channels = value.get("channels")
    if type(channels) is not list or not channels:
        raise ProjectAcquisitionError("acquisition profile must declare channels")
    identifiers: set[str] = set()
    aliases: set[str] = set()
    for row in channels:
        if type(row) is not dict or set(row) != {
            "id",
            "aliases",
            "remote_ref",
            "checkout_branch",
        }:
            raise ProjectAcquisitionError("acquisition channel is invalid")
        identifier = row.get("id")
        if (
            type(identifier) is not str
            or not identifier
            or identifier in identifiers
            or identifier in aliases
        ):
            raise ProjectAcquisitionError("acquisition channel ID is invalid or repeated")
        identifiers.add(identifier)
        row_aliases = row.get("aliases")
        if type(row_aliases) is not list or any(
            type(alias) is not str or not alias for alias in row_aliases
        ):
            raise ProjectAcquisitionError("acquisition channel aliases are invalid")
        for alias in row_aliases:
            if alias in aliases or alias in identifiers:
                raise ProjectAcquisitionError("acquisition channel alias is repeated")
            aliases.add(alias)
        if type(row.get("remote_ref")) is not str or not _REF.fullmatch(row["remote_ref"]):
            raise ProjectAcquisitionError("acquisition channel remote_ref is invalid")
        if type(row.get("checkout_branch")) is not str or not _BRANCH.fullmatch(
            row["checkout_branch"]
        ):
            raise ProjectAcquisitionError("acquisition channel checkout_branch is invalid")
    if value["default_channel"] not in identifiers:
        raise ProjectAcquisitionError("default acquisition channel is not declared")

    workspace = value.get("workspace")
    if type(workspace) is not dict or set(workspace) != {"required_paths"}:
        raise ProjectAcquisitionError("acquisition workspace probe is invalid")
    required = workspace.get("required_paths")
    if type(required) is not list or not required:
        raise ProjectAcquisitionError("acquisition workspace probe must require paths")
    seen_paths: set[str] = set()
    for row in required:
        if type(row) is not dict or set(row) != {"path", "kind"}:
            raise ProjectAcquisitionError("acquisition workspace path row is invalid")
        if type(row.get("path")) is not str:
            raise ProjectAcquisitionError("acquisition workspace path must be text")
        normalized = _safe_relative_path(row["path"], "workspace path")
        if normalized in seen_paths:
            raise ProjectAcquisitionError("acquisition workspace path is repeated")
        seen_paths.add(normalized)
        if row.get("kind") not in {"file", "directory"}:
            raise ProjectAcquisitionError("acquisition workspace path kind is invalid")
    return value


def _channel(profile: Mapping[str, Any], requested: str | None) -> dict[str, Any]:
    selected = requested or str(profile["default_channel"])
    for row in profile["channels"]:
        if selected == row["id"] or selected in row["aliases"]:
            return dict(row)
    raise ProjectAcquisitionError(
        f"unknown {profile['project_id']} channel {selected!r}"
    )


def resolve_acquisition_channel(
    profile: Mapping[str, Any], requested: str | None = None
) -> dict[str, Any]:
    """Return one validated channel from an already loaded profile."""

    return _channel(profile, requested)


def _git_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    values = dict(os.environ if environment is None else environment)
    values["GIT_TERMINAL_PROMPT"] = "0"
    values["GIT_CONFIG_NOSYSTEM"] = "1"
    values.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    values.setdefault("LC_ALL", "C")
    return values


def _selected_git(executable: str | None, environment: Mapping[str, str] | None) -> str:
    try:
        selected = configured_git_executable(executable, environment=environment)
    except GitObservationError as exc:
        raise ProjectAcquisitionError(str(exc)) from exc
    if selected is None:
        raise ProjectAcquisitionError(
            "Git is unavailable; run workbench setup and select a Git executable"
        )
    return selected


def _github_repository(profile: Mapping[str, Any], repository_url: str | None) -> str:
    """Keep an explicit fork on GitHub HTTPS and bind its exact canonical URL."""

    if repository_url is None:
        return str(profile["remote"]["url"])
    if profile["remote"]["provider"] != "github" or type(repository_url) is not str:
        raise ProjectAcquisitionError("a repository override needs a GitHub acquisition profile")
    if any(ord(character) <= 32 or ord(character) == 127 for character in repository_url):
        raise ProjectAcquisitionError("repository URL contains whitespace or controls")
    try:
        selected = urlsplit(repository_url)
        has_credentials = selected.username is not None or selected.password is not None
        has_port = selected.port is not None
    except ValueError as exc:
        raise ProjectAcquisitionError("repository URL is malformed") from exc
    if (selected.scheme != "https" or selected.netloc != "github.com"
            or selected.query or selected.fragment or has_credentials or has_port):
        raise ProjectAcquisitionError("repository must be an HTTPS github.com owner/repository URL")
    path = selected.path[:-1] if selected.path.endswith("/") else selected.path
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "":
        raise ProjectAcquisitionError("repository must name one GitHub owner and repository")
    owner, repository = parts[1], parts[2]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner) is None
            or owner.endswith("-")
            or re.fullmatch(r"[A-Za-z0-9_.-]+", repository) is None
            or repository in {".", ".."}):
        raise ProjectAcquisitionError("repository must name one GitHub owner and repository")
    return f"https://github.com/{owner}/{repository}.git"


def _validated_branch(
    branch_name: str, *, git_executable: str, environment: Mapping[str, str] | None,
) -> str:
    if type(branch_name) is not str or not branch_name:
        raise ProjectAcquisitionError("branch name is invalid")
    try:
        branch_bytes = branch_name.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ProjectAcquisitionError("branch name is not valid UTF-8") from exc
    if (len(branch_bytes) > 512 or branch_name.startswith("-")
            or "\x00" in branch_name):
        raise ProjectAcquisitionError("branch name is invalid")
    checked = _run_git(
        git_executable, ("check-ref-format", "--branch", branch_name),
        environment=environment, timeout=10.0,
    )
    if checked.returncode:
        raise ProjectAcquisitionError("branch name is not a valid Git branch")
    return branch_name


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
        raise ProjectAcquisitionError(
            f"Git acquisition command failed before completion ({type(exc).__name__})"
        ) from exc


def resolve_remote_commit(
    profile: Mapping[str, Any],
    channel: Mapping[str, Any],
    *,
    git_executable: str,
    environment: Mapping[str, str] | None = None,
    timeout: float = 120.0,
) -> str:
    """Resolve one moving channel ref to one immutable remote object ID."""

    completed = _run_git(
        git_executable,
        (
            "ls-remote",
            "--refs",
            "--exit-code",
            str(profile["remote"]["url"]),
            str(channel["remote_ref"]),
        ),
        environment=environment,
        timeout=timeout,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[:2000]
        raise ProjectAcquisitionError(
            "cannot resolve acquisition channel"
            + (f": {detail}" if detail else f" (Git exit {completed.returncode})")
        )
    rows = [row for row in completed.stdout.splitlines() if row.strip()]
    if len(rows) != 1:
        raise ProjectAcquisitionError("acquisition channel did not resolve uniquely")
    fields = rows[0].split("\t", 1)
    if (
        len(fields) != 2
        or fields[1] != channel["remote_ref"]
        or not _GIT_OBJECT.fullmatch(fields[0])
    ):
        raise ProjectAcquisitionError("acquisition channel returned malformed identity")
    return fields[0]


def list_remote_branches(
    profile: Mapping[str, Any], *, repository_url: str | None = None,
    git_executable: str | None = None, environment: Mapping[str, str] | None = None,
    network_timeout: float = 120.0, limit: int = 200,
) -> dict[str, Any]:
    """List bounded upstream names for a picker; typed branches remain available."""

    if type(limit) is not int or not 1 <= limit <= 500:
        raise ProjectAcquisitionError("branch list limit must be between 1 and 500")
    if type(network_timeout) not in {int, float} or not 0 < network_timeout <= 3600:
        raise ProjectAcquisitionError("branch list timeout is invalid")
    selected_git = _selected_git(git_executable, environment)
    repository = _github_repository(profile, repository_url)
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(
                [selected_git, "ls-remote", "--heads", "--refs", repository],
                check=False, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                timeout=network_timeout, env=_git_environment(environment),
            )
            stdout.seek(0)
            stderr.seek(0)
            raw = stdout.read(2 * 1024 * 1024 + 1)
            detail = stderr.read(4096).decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectAcquisitionError(
            f"cannot list remote branches ({type(exc).__name__}); enter a branch name instead"
        ) from exc
    if completed.returncode:
        raise ProjectAcquisitionError(
            "cannot list remote branches"
            + (f": {detail[:2000]}" if detail else f" (Git exit {completed.returncode})")
        )
    if len(raw) > 2 * 1024 * 1024:
        raise ProjectAcquisitionError("remote branch list is too large; enter a branch name instead")
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise ProjectAcquisitionError("remote branch list is not UTF-8") from exc
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in lines:
        fields = line.split("\t", 1)
        if (len(fields) != 2 or _GIT_OBJECT.fullmatch(fields[0]) is None
                or not fields[1].startswith("refs/heads/")):
            raise ProjectAcquisitionError("remote branch list returned malformed identities")
        name = fields[1][len("refs/heads/"):]
        if not name or name in seen:
            raise ProjectAcquisitionError("remote branch list returned a repeated or empty name")
        seen.add(name)
        rows.append({"name": name, "commit": fields[0]})
    rows.sort(key=lambda row: row["name"].casefold())
    return {
        "format": BRANCH_LIST_FORMAT, "schema_version": 1,
        "project_id": profile["project_id"], "repository": repository,
        "branches": rows[:limit], "truncated": len(rows) > limit,
    }


def build_acquisition_plan(
    profile: Mapping[str, Any],
    *,
    channel_name: str | None,
    destination: Path | str,
    git_executable: str | None = None,
    environment: Mapping[str, str] | None = None,
    network_timeout: float = 120.0,
) -> dict[str, Any]:
    """Resolve and render one exact acquisition consent unit."""

    channel = _channel(profile, channel_name)
    try:
        selected_git = configured_git_executable(
            git_executable,
            environment=environment,
        )
    except GitObservationError as exc:
        raise ProjectAcquisitionError(str(exc)) from exc
    if selected_git is None:
        raise ProjectAcquisitionError(
            "Git is unavailable; run workbench setup and select a Git executable"
        )
    target = Path(destination).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = Path(os.path.abspath(os.fspath(target)))
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ProjectAcquisitionError("acquisition destination parent must be a regular directory")
    if target.exists() or target.is_symlink():
        raise ProjectAcquisitionError("acquisition destination must not already exist")
    commit = resolve_remote_commit(
        profile,
        channel,
        git_executable=selected_git,
        environment=environment,
        timeout=network_timeout,
    )
    identity = {
        "profile_id": profile["profile_id"],
        "profile_digest": _identity("workbench-project-acquisition-profile", profile),
        "project_id": profile["project_id"],
        "channel_id": channel["id"],
        "remote_url": profile["remote"]["url"],
        "remote_ref": channel["remote_ref"],
        "resolved_commit": commit,
        "checkout_branch": channel["checkout_branch"],
        "destination": str(target),
        "git_executable": selected_git,
    }
    return {
        "format": PLAN_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "review-before-network-write",
        "plan_id": _identity("workbench-project-acquisition-plan", identity),
        **identity,
        "effects": [
            "Clone the declared remote into a fresh sibling staging directory.",
            "Verify the resolved commit and profile-required workspace paths.",
            "Publish an acquisition receipt under Workbench state.",
            "Atomically publish the verified checkout only after its receipt is durable.",
        ],
    }


def build_acquisition_plan_v2(
    profile: Mapping[str, Any],
    *,
    channel_name: str | None,
    destination: Path | str,
    git_executable: str | None = None,
    environment: Mapping[str, str] | None = None,
    network_timeout: float = 120.0,
) -> dict[str, Any]:
    """Resolve an acquisition plan that may create missing parent directories.

    Acquisition Plan V1 requires the destination parent to exist.  V2 keeps
    that contract intact and adds an explicit, identity-bound parent action so
    a normal first-run destination can be reviewed without changing the file
    system before consent.
    """

    channel = _channel(profile, channel_name)
    try:
        selected_git = configured_git_executable(
            git_executable,
            environment=environment,
        )
    except GitObservationError as exc:
        raise ProjectAcquisitionError(str(exc)) from exc
    if selected_git is None:
        raise ProjectAcquisitionError(
            "Git is unavailable; run workbench setup and select a Git executable"
        )
    target = Path(destination).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = Path(os.path.abspath(os.fspath(target)))
    if target.exists() or target.is_symlink():
        raise ProjectAcquisitionError("acquisition destination must not already exist")

    parent = target.parent
    create_parent = not parent.exists()
    ancestor = parent
    while not ancestor.exists() and not ancestor.is_symlink():
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    if ancestor.is_symlink() or not ancestor.is_dir():
        raise ProjectAcquisitionError(
            "acquisition destination parent must descend from a regular directory"
        )
    if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
        raise ProjectAcquisitionError(
            "acquisition destination parent must be a regular directory"
        )

    commit = resolve_remote_commit(
        profile,
        channel,
        git_executable=selected_git,
        environment=environment,
        timeout=network_timeout,
    )
    identity = {
        "profile_id": profile["profile_id"],
        "profile_digest": _identity("workbench-project-acquisition-profile", profile),
        "project_id": profile["project_id"],
        "channel_id": channel["id"],
        "remote_url": profile["remote"]["url"],
        "remote_ref": channel["remote_ref"],
        "resolved_commit": commit,
        "checkout_branch": channel["checkout_branch"],
        "destination": str(target),
        "destination_parent": str(parent),
        "destination_parent_action": "create" if create_parent else "reuse",
        "git_executable": selected_git,
    }
    effects = []
    if create_parent:
        effects.append(
            "Create the missing destination parent directories after consent."
        )
    effects.extend(
        (
            "Clone the declared remote into a fresh sibling staging directory.",
            "Verify the resolved commit and profile-required workspace paths.",
            "Publish an acquisition receipt under Workbench state.",
            "Atomically publish the verified checkout only after its receipt is durable.",
        )
    )
    return {
        "format": PLAN_FORMAT_V2,
        "schema_version": 2,
        "operation_class": "review-before-network-write",
        "plan_id": _identity("workbench-project-acquisition-plan-v2", identity),
        **identity,
        "effects": effects,
    }


def build_branch_acquisition_plan(
    profile: Mapping[str, Any], *, branch_name: str,
    destination: Path | str, repository_url: str | None = None,
    source_only: bool = False, git_executable: str | None = None,
    environment: Mapping[str, str] | None = None,
    network_timeout: float = 120.0,
) -> dict[str, Any]:
    """Review one user-selected branch, including an explicit GitHub fork."""

    if type(source_only) is not bool:
        raise ProjectAcquisitionError("source-only choice must be true or false")
    selected_git = _selected_git(git_executable, environment)
    branch = _validated_branch(
        branch_name, git_executable=selected_git, environment=environment,
    )
    repository = _github_repository(profile, repository_url)
    target = Path(destination).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = Path(os.path.abspath(os.fspath(target)))
    if target.exists() or target.is_symlink():
        raise ProjectAcquisitionError("acquisition destination must not already exist")
    parent = target.parent
    create_parent = not parent.exists()
    ancestor = parent
    while not ancestor.exists() and not ancestor.is_symlink():
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    if ancestor.is_symlink() or not ancestor.is_dir():
        raise ProjectAcquisitionError(
            "acquisition destination parent must descend from a regular directory"
        )
    if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
        raise ProjectAcquisitionError(
            "acquisition destination parent must be a regular directory"
        )
    remote_ref = f"refs/heads/{branch}"
    commit = resolve_remote_commit(
        {"remote": {"url": repository}}, {"remote_ref": remote_ref},
        git_executable=selected_git, environment=environment,
        timeout=network_timeout,
    )
    identity = {
        "profile_id": profile["profile_id"],
        "profile_digest": _identity("workbench-project-acquisition-profile", profile),
        "project_id": profile["project_id"],
        "source_kind": "branch", "branch_name": branch,
        "remote_url": repository, "remote_ref": remote_ref,
        "resolved_commit": commit, "checkout_branch": branch,
        "destination": str(target), "destination_parent": str(parent),
        "destination_parent_action": "create" if create_parent else "reuse",
        "git_executable": selected_git, "source_only": source_only,
    }
    effects = []
    if create_parent:
        effects.append("Create the missing destination parent directories after consent.")
    effects.extend((
        "Clone the selected branch into a fresh sibling staging directory.",
        "Verify the resolved commit and inspect profile-required workspace paths.",
        "Publish an acquisition receipt under Workbench state.",
        "Atomically publish the verified checkout only after its receipt is durable.",
    ))
    return {
        "format": PLAN_FORMAT_V3, "schema_version": 3,
        "operation_class": "review-before-network-write",
        "plan_id": _identity("workbench-project-acquisition-plan-v3", identity),
        **identity, "effects": effects,
    }


def _create_missing_parent_directories(parent: Path) -> list[tuple[Path, int, int]]:
    """Create a reviewed parent chain and retain only identities we created."""

    missing: list[Path] = []
    cursor = parent
    while not cursor.exists() and not cursor.is_symlink():
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise ProjectAcquisitionError(
            "acquisition destination parent changed after review"
        )

    created: list[tuple[Path, int, int]] = []
    try:
        for directory in reversed(missing):
            try:
                directory.mkdir(exist_ok=False)
            except FileExistsError as exc:
                raise ProjectAcquisitionError(
                    "acquisition destination parent changed while it was created"
                ) from exc
            try:
                measured = directory.lstat()
            except OSError as exc:
                raise ProjectAcquisitionError(
                    "cannot verify a created acquisition destination parent"
                ) from exc
            if not stat.S_ISDIR(measured.st_mode) or directory.is_symlink():
                raise ProjectAcquisitionError(
                    "acquisition destination parent changed while it was created"
                )
            created.append((directory, measured.st_dev, measured.st_ino))
    except BaseException:
        _remove_created_parent_directories(created)
        raise
    return created


def _remove_created_parent_directories(
    created: Sequence[tuple[Path, int, int]],
) -> None:
    """Best-effort rollback without removing a path whose identity changed."""

    for directory, expected_device, expected_inode in reversed(created):
        try:
            measured = directory.lstat()
            if (
                stat.S_ISDIR(measured.st_mode)
                and not directory.is_symlink()
                and measured.st_dev == expected_device
                and measured.st_ino == expected_inode
            ):
                directory.rmdir()
        except OSError:
            pass


def _verify_workspace(profile: Mapping[str, Any], root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for requirement in profile["workspace"]["required_paths"]:
        relative = _safe_relative_path(requirement["path"], "workspace path")
        selected = root
        for part in PurePosixPath(relative).parts:
            selected = selected / part
            if selected.is_symlink() or getattr(selected, "is_junction", lambda: False)():
                raise ProjectAcquisitionError(
                    f"acquired checkout required path traverses a redirect: {relative}"
                )
        expected = requirement["kind"]
        matches = selected.is_file() if expected == "file" else selected.is_dir()
        if not matches:
            raise ProjectAcquisitionError(
                f"acquired checkout is missing required {expected}: {relative}"
            )
        rows.append({"path": relative, "kind": expected})
    return rows


def apply_acquisition_plan(
    profile: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    state_root: Path | str,
    environment: Mapping[str, str] | None = None,
    network_timeout: float = 120.0,
    clone_timeout: float = 1800.0,
) -> dict[str, Any]:
    """Acquire, verify, atomically publish, and retain one exact checkout."""

    branch_plan = plan.get("format") == PLAN_FORMAT_V3
    if branch_plan:
        requested_repository = str(plan.get("remote_url"))
        expected = build_branch_acquisition_plan(
            profile, branch_name=str(plan.get("branch_name")),
            destination=str(plan.get("destination")),
            repository_url=(None if requested_repository == profile["remote"]["url"]
                            else requested_repository),
            source_only=plan.get("source_only"),
            git_executable=str(plan.get("git_executable")),
            environment=environment, network_timeout=network_timeout,
        )
    elif plan.get("format") == PLAN_FORMAT_V2:
        channel = _channel(profile, str(plan.get("channel_id")))
        expected = build_acquisition_plan_v2(
            profile,
            channel_name=channel["id"],
            destination=str(plan.get("destination")),
            git_executable=str(plan.get("git_executable")),
            environment=environment,
            network_timeout=network_timeout,
        )
    else:
        channel = _channel(profile, str(plan.get("channel_id")))
        expected = build_acquisition_plan(
            profile,
            channel_name=channel["id"],
            destination=str(plan.get("destination")),
            git_executable=str(plan.get("git_executable")),
            environment=environment,
            network_timeout=network_timeout,
        )
    if expected != dict(plan):
        raise ProjectAcquisitionError(
            "acquisition plan changed; resolve and review the source again"
        )
    destination = Path(plan["destination"])
    parent = destination.parent
    created_parents: list[tuple[Path, int, int]] = []
    published = False
    try:
        if (
            plan.get("format") in {PLAN_FORMAT_V2, PLAN_FORMAT_V3}
            and plan.get("destination_parent_action") == "create"
        ):
            created_parents = _create_missing_parent_directories(parent)
        if not parent.is_dir() or parent.is_symlink():
            raise ProjectAcquisitionError(
                "acquisition destination parent changed after review"
            )
        with open_source_checkout(
            destination,
            git_executable=str(plan["git_executable"]),
            remote_url=str(plan["remote_url"]),
            checkout_branch=str(plan["checkout_branch"]),
            expected_commit=str(plan["resolved_commit"]),
            # Developer acquisition plans have no reviewed tree lock. Core observes the tree,
            # but that observation does not upgrade these plans to W7 import.
            expected_tree=None,
            environment=_git_environment(environment),
            timeout_seconds=clone_timeout,
        ) as checkout:
            profile_diagnostic: str | None = None
            try:
                required_paths = _verify_workspace(profile, checkout.staging_root)
            except ProjectAcquisitionError as exc:
                if not branch_plan or not plan["source_only"]:
                    raise
                required_paths = []
                profile_diagnostic = str(exc)
            receipt_body = {
                "profile_id": plan["profile_id"],
                "profile_digest": plan["profile_digest"],
                "project_id": plan["project_id"],
                **({"source_kind": "branch", "branch_name": plan["branch_name"],
                    "source_only": plan["source_only"],
                    "profile_compatible": profile_diagnostic is None,
                    "profile_diagnostic": profile_diagnostic} if branch_plan
                   else {"channel_id": plan["channel_id"]}),
                "remote_url": plan["remote_url"],
                "remote_ref": plan["remote_ref"],
                "resolved_commit": plan["resolved_commit"],
                "checkout_branch": plan["checkout_branch"],
                "destination": plan["destination"],
                "required_paths": required_paths,
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
            receipt = {
                "format": RECEIPT_FORMAT_V2 if branch_plan else RECEIPT_FORMAT,
                "schema_version": 2 if branch_plan else SCHEMA_VERSION,
                "receipt_id": _identity(
                    "workbench-project-acquisition",
                    {key: value for key, value in receipt_body.items() if key != "acquired_at"},
                ),
                **receipt_body,
            }
            receipt_root = Path(state_root).expanduser()
            if not receipt_root.is_absolute():
                receipt_root = Path.cwd() / receipt_root
            receipt_root = Path(os.path.abspath(os.fspath(receipt_root)))
            receipt_bytes = (
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            receipt_path = checkout.publish(
                state_root=receipt_root,
                receipt_id=receipt["receipt_id"],
                receipt_bytes=receipt_bytes,
                byte_limit=len(receipt_bytes),
            )
        published = True
        v2 = plan.get("format") == PLAN_FORMAT_V2
        result = {
            "format": RESULT_FORMAT_V3 if branch_plan else RESULT_FORMAT_V2 if v2 else RESULT_FORMAT,
            "schema_version": 3 if branch_plan else 2 if v2 else SCHEMA_VERSION,
            "outcome": "acquired",
            "plan_id": plan["plan_id"],
            "destination": plan["destination"],
            "resolved_commit": plan["resolved_commit"],
            "receipt_id": receipt["receipt_id"],
            "receipt_path": str(receipt_path),
            "next_commands": [
                ["workbench", "setup", "--workspace", plan["destination"]],
                ["workbench", "open", plan["destination"]],
            ],
        }
        if branch_plan:
            result.update({
                "source_kind": "branch", "branch_name": plan["branch_name"],
                "remote_url": plan["remote_url"], "source_only": plan["source_only"],
                "profile_compatible": profile_diagnostic is None,
                "profile_diagnostic": profile_diagnostic,
                "profile_compatibility_scope": "Profile-required paths only; runtime and pack installation are not assessed.",
                "pack_readiness": "not-assessed",
            })
        if v2 or branch_plan:
            result["destination_parent"] = plan["destination_parent"]
            result["destination_parent_action"] = plan[
                "destination_parent_action"
            ]
        return result
    except SourceCheckoutError as exc:
        raise ProjectAcquisitionError(str(exc)) from exc
    except OSError as exc:
        raise ProjectAcquisitionError(f"cannot publish acquired checkout: {exc}") from exc
    finally:
        if not published:
            _remove_created_parent_directories(created_parents)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench project acquire",
        description=(
            "Resolve a profile-owned project channel to an immutable Git commit, "
            "review the plan, and acquire it into a fresh destination."
        ),
    )
    parser.add_argument("project", help="declared project profile, such as supersymmetry")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--channel", help="profile channel or alias (default: profile current channel)")
    selector.add_argument("--branch", help="exact GitHub branch name to acquire")
    parser.add_argument("--repository", "--source", dest="repository",
                        help="explicit HTTPS GitHub fork repository for --branch")
    parser.add_argument("--source-only", action="store_true",
                        help="retain source even when it lacks profile-required pack files")
    parser.add_argument("--list-branches", action="store_true",
                        help="list available branches; --destination is not required")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--plan", action="store_true", help="resolve and print the exact plan only")
    parser.add_argument("--apply", metavar="PLAN_ID", help="apply one freshly resolved exact plan")
    parser.add_argument("--state-root", type=Path, help="external Workbench state for the retained receipt")
    parser.add_argument("--git-executable", help="explicit Git executable; setup selection is used otherwise")
    parser.add_argument("--network-timeout", type=float, default=120.0)
    parser.add_argument("--clone-timeout", type=float, default=1800.0)
    parser.add_argument("--json", action="store_true")
    return parser


def _render_plan(plan: Mapping[str, Any]) -> str:
    return (
        "Workbench project acquisition plan\n"
        f"Project: {plan['project_id']}\n"
        + (f"Branch: {plan['branch_name']} ({plan['remote_ref']})\n"
           if plan.get("format") == PLAN_FORMAT_V3 else
           f"Channel: {plan['channel_id']} ({plan['remote_ref']})\n")
        + f"Repository: {plan['remote_url']}\n"
        f"Commit: {plan['resolved_commit']}\n"
        f"Destination: {plan['destination']}\n"
        f"Plan: {plan['plan_id']}\n"
    )


def _render_result(result: Mapping[str, Any]) -> str:
    return (
        "Workbench project acquired.\n"
        f"Destination: {result['destination']}\n"
        f"Commit: {result['resolved_commit']}\n"
        f"Receipt: {result['receipt_path']}\n"
        f"Next: workbench setup --workspace {result['destination']}\n"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    profiles: Mapping[str, Path | str],
    default_state_root: Path | str,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run the user-facing acquisition flow with injected profile ownership."""

    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    stdin = sys.stdin if input_stream is None else input_stream
    try:
        profile_path = profiles.get(args.project)
        if profile_path is None:
            raise ProjectAcquisitionError(
                f"unknown project {args.project!r}; available: "
                + ", ".join(sorted(profiles))
            )
        profile = load_acquisition_profile(profile_path)
        if args.list_branches:
            if (args.destination is not None or args.branch is not None
                    or args.channel is not None or args.source_only or args.plan
                    or args.apply is not None):
                raise ProjectAcquisitionError(
                    "branch listing takes only the project, optional repository and Git choice"
                )
            listing = list_remote_branches(
                profile, repository_url=args.repository,
                git_executable=args.git_executable, environment=environment,
                network_timeout=args.network_timeout,
            )
            stdout.write(
                json.dumps(listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json else "\n".join(row["name"] for row in listing["branches"]) + "\n"
            )
            return 0
        if args.destination is None:
            raise ProjectAcquisitionError("project acquisition needs --destination PATH")
        if args.repository is not None and args.branch is None:
            raise ProjectAcquisitionError("--repository needs --branch")
        if args.source_only and args.branch is None:
            raise ProjectAcquisitionError("--source-only needs --branch")
        if args.branch is not None:
            plan = build_branch_acquisition_plan(
                profile, branch_name=args.branch, destination=args.destination,
                repository_url=args.repository, source_only=args.source_only,
                git_executable=args.git_executable, environment=environment,
                network_timeout=args.network_timeout,
            )
        else:
            plan = build_acquisition_plan_v2(
                profile,
                channel_name=args.channel,
                destination=args.destination,
                git_executable=args.git_executable,
                environment=environment,
                network_timeout=args.network_timeout,
            )
        if args.plan:
            if args.apply is not None:
                raise ProjectAcquisitionError("--plan and --apply are mutually exclusive")
            stdout.write(
                json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_plan(plan)
            )
            return 0
        if args.apply is not None:
            if args.apply != plan["plan_id"]:
                raise ProjectAcquisitionError(
                    "--apply does not match the current remote plan; review it again"
                )
        else:
            interactive = bool(getattr(stdin, "isatty", lambda: False)()) and bool(
                getattr(stdout, "isatty", lambda: False)()
            )
            if not interactive:
                stdout.write(
                    json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    if args.json
                    else _render_plan(plan)
                )
                return 0
            stdout.write(_render_plan(plan))
            token = plan["plan_id"].rsplit(":", 1)[-1][:12]
            stdout.write(f"Type acquire {token} to continue, or Enter to cancel: ")
            stdout.flush()
            answer = stdin.readline()
            if answer == "" or answer.strip() != f"acquire {token}":
                raise ProjectAcquisitionCancelled
        result = apply_acquisition_plan(
            profile,
            plan,
            state_root=args.state_root or default_state_root,
            environment=environment,
            network_timeout=args.network_timeout,
            clone_timeout=args.clone_timeout,
        )
        stdout.write(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.json
            else _render_result(result)
        )
        return 0
    except ProjectAcquisitionCancelled:
        stdout.write("Project acquisition cancelled; nothing was changed.\n")
        return 0
    except (OSError, ProjectAcquisitionError, ValueError) as exc:
        stderr.write(f"Workbench project acquisition failed: {exc}\n")
        return 2


__all__ = [
    "BRANCH_LIST_FORMAT",
    "PLAN_FORMAT",
    "PLAN_FORMAT_V2",
    "PLAN_FORMAT_V3",
    "PROFILE_FORMAT",
    "ProjectAcquisitionError",
    "RECEIPT_FORMAT",
    "RECEIPT_FORMAT_V2",
    "RESULT_FORMAT",
    "RESULT_FORMAT_V2",
    "RESULT_FORMAT_V3",
    "apply_acquisition_plan",
    "build_acquisition_plan",
    "build_acquisition_plan_v2",
    "build_branch_acquisition_plan",
    "list_remote_branches",
    "load_acquisition_profile",
    "main",
    "resolve_remote_commit",
    "resolve_acquisition_channel",
]
