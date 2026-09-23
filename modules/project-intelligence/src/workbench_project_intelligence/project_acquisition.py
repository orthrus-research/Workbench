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
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, TextIO

from .git_observation import GitObservationError, configured_git_executable


PROFILE_FORMAT = "workbench-project-acquisition-profile-v1"
PLAN_FORMAT = "workbench-project-acquisition-plan-v1"
PLAN_FORMAT_V2 = "workbench-project-acquisition-plan-v2"
RECEIPT_FORMAT = "workbench-project-acquisition-receipt-v1"
RESULT_FORMAT = "workbench-project-acquisition-result-v1"
RESULT_FORMAT_V2 = "workbench-project-acquisition-result-v2"
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
        selected = root.joinpath(*PurePosixPath(relative).parts)
        expected = requirement["kind"]
        matches = selected.is_file() if expected == "file" else selected.is_dir()
        if not matches or selected.is_symlink():
            raise ProjectAcquisitionError(
                f"acquired checkout is missing required {expected}: {relative}"
            )
        rows.append({"path": relative, "kind": expected})
    return rows


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    parent = path.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise ProjectAcquisitionError("acquisition receipt parent is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ProjectAcquisitionError(
            "acquisition receipt already exists; refusing to replace retained evidence"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise ProjectAcquisitionError(f"cannot publish acquisition receipt: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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

    channel = _channel(profile, str(plan.get("channel_id")))
    if plan.get("format") == PLAN_FORMAT_V2:
        expected = build_acquisition_plan_v2(
            profile,
            channel_name=channel["id"],
            destination=str(plan.get("destination")),
            git_executable=str(plan.get("git_executable")),
            environment=environment,
            network_timeout=network_timeout,
        )
    else:
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
            "acquisition plan changed; resolve and review the channel again"
        )
    destination = Path(plan["destination"])
    parent = destination.parent
    created_parents: list[tuple[Path, int, int]] = []
    staging: Path | None = None
    published = False
    try:
        if (
            plan.get("format") == PLAN_FORMAT_V2
            and plan.get("destination_parent_action") == "create"
        ):
            created_parents = _create_missing_parent_directories(parent)
        if not parent.is_dir() or parent.is_symlink():
            raise ProjectAcquisitionError(
                "acquisition destination parent changed after review"
            )
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".workbench-acquire-{destination.name}-",
                dir=parent,
            )
        )
        completed = _run_git(
            str(plan["git_executable"]),
            (
                "clone",
                "--no-tags",
                "--single-branch",
                "--branch",
                str(plan["checkout_branch"]),
                str(plan["remote_url"]),
                str(staging),
            ),
            environment=environment,
            timeout=clone_timeout,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[:4000]
            raise ProjectAcquisitionError(
                "Git clone failed"
                + (f": {detail}" if detail else f" (exit {completed.returncode})")
            )
        observed = _run_git(
            str(plan["git_executable"]),
            ("-C", str(staging), "rev-parse", "--verify", "HEAD^{commit}"),
            environment=environment,
            timeout=30.0,
        )
        head = observed.stdout.strip()
        if observed.returncode or head != plan["resolved_commit"]:
            raise ProjectAcquisitionError(
                "remote channel moved during acquisition; review a new exact plan"
            )
        required_paths = _verify_workspace(profile, staging)
        receipt_body = {
            "profile_id": plan["profile_id"],
            "profile_digest": plan["profile_digest"],
            "project_id": plan["project_id"],
            "channel_id": plan["channel_id"],
            "remote_url": plan["remote_url"],
            "remote_ref": plan["remote_ref"],
            "resolved_commit": plan["resolved_commit"],
            "checkout_branch": plan["checkout_branch"],
            "destination": plan["destination"],
            "required_paths": required_paths,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt = {
            "format": RECEIPT_FORMAT,
            "schema_version": SCHEMA_VERSION,
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
        receipt_path = (
            receipt_root
            / "evidence"
            / "project-acquisition"
            / f"{receipt['receipt_id'].rsplit(':', 1)[-1]}.json"
        )
        # The receipt is a required part of a successful acquisition. Publish it
        # before exposing the checkout so a receipt failure cannot leave an
        # apparently failed but usable destination behind. If the checkout's
        # final atomic rename then fails, remove this operation's exact receipt.
        _write_json_atomic(receipt_path, receipt)
        try:
            os.replace(staging, destination)
        except OSError as exc:
            try:
                receipt_path.unlink()
            except OSError as rollback_exc:
                raise ProjectAcquisitionError(
                    "cannot publish acquired checkout and cannot roll back its "
                    f"receipt: {rollback_exc}"
                ) from exc
            raise
        published = True
        v2 = plan.get("format") == PLAN_FORMAT_V2
        result = {
            "format": RESULT_FORMAT_V2 if v2 else RESULT_FORMAT,
            "schema_version": 2 if v2 else SCHEMA_VERSION,
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
        if v2:
            result["destination_parent"] = plan["destination_parent"]
            result["destination_parent_action"] = plan[
                "destination_parent_action"
            ]
        return result
    except OSError as exc:
        raise ProjectAcquisitionError(f"cannot publish acquired checkout: {exc}") from exc
    finally:
        if not published and staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
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
    parser.add_argument("--channel", help="profile channel or alias (default: profile current channel)")
    parser.add_argument("--destination", type=Path, required=True)
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
        f"Channel: {plan['channel_id']} ({plan['remote_ref']})\n"
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
    "PLAN_FORMAT",
    "PLAN_FORMAT_V2",
    "PROFILE_FORMAT",
    "ProjectAcquisitionError",
    "RECEIPT_FORMAT",
    "RESULT_FORMAT",
    "RESULT_FORMAT_V2",
    "apply_acquisition_plan",
    "build_acquisition_plan",
    "build_acquisition_plan_v2",
    "load_acquisition_profile",
    "main",
    "resolve_remote_commit",
    "resolve_acquisition_channel",
]
