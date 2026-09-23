"""Provider-bound pull-request preparation with a stable V2 record identity.

V2 treats a Git hosting provider response as reviewed input.  The plan binds
the provider's pull-request URL, state, original base/head identities, and
merge identity before any Git fetch.  Apply observes the provider again,
fetches only those exact objects, and retains immutable Workbench refs.  The
receipt verifier is deliberately offline.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .git_observation import GitObservationError, configured_git_executable
from ._pr_preparation_core import (
    MAX_CHANGED_PATH_BYTES,
    MAX_CHANGED_PATHS,
    MAX_PULL_REQUEST_NUMBER,
    MAX_RECEIPT_BYTES,
    PullRequestPreparationCancelled,
    PullRequestPreparationError,
    _changed_paths,
    _git_environment,
    _git_text,
    _identity,
    _repository_root,
    _run_git,
)
from .project_acquisition import resolve_acquisition_channel


PROVIDER_PROFILE_FORMAT = "workbench-pull-request-provider-profile-v1"
PLAN_FORMAT = "workbench-pr-preparation-plan-v2"
RECEIPT_FORMAT = "workbench-pr-preparation-receipt-v2"
RESULT_FORMAT = "workbench-pr-preparation-result-v2"
SCHEMA_VERSION = 2
MAX_PROVIDER_PROFILE_BYTES = 256 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_NETWORK_TIMEOUT_SECONDS = 600.0
_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_FULL_REF = re.compile(r"^refs/[A-Za-z0-9][A-Za-z0-9._/-]*$")
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PROFILE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_WINDOWS_RESERVED_PROJECT_ID = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)",
    re.IGNORECASE,
)
_RECEIPT_ID = re.compile(r"^workbench-pr-preparation-v2:sha256:[0-9a-f]{64}$")
_ACQUISITION_DIGEST = re.compile(
    r"^workbench-project-acquisition-profile:sha256:[0-9a-f]{64}$"
)
_PROVIDER_DIGEST = re.compile(
    r"^workbench-pull-request-provider-profile:sha256:[0-9a-f]{64}$"
)
_PLAN_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_OWNED_REF_TOKEN_CHARACTERS = 32
_GIT_TRANSACTION_OUTPUT_BYTES = 64 * 1024
_GIT_DIAGNOSTIC_BYTES = 1000

ProviderObserver = Callable[[Mapping[str, Any], int, float], Mapping[str, Any]]


def _full_ref(value: object) -> bool:
    if type(value) is not str or _FULL_REF.fullmatch(value) is None:
        return False
    components = value.split("/")
    return (
        len(components) >= 3
        and all(components)
        and ".." not in value
        and not value.endswith(("/", "."))
        and all(
            not component.startswith(".")
            and not component.casefold().endswith(".lock")
            for component in components[1:]
        )
    )


def _project_id(value: object) -> str:
    if (
        type(value) is not str
        or not _PROJECT_ID.fullmatch(value)
        or ".." in value
        or value.casefold().endswith((".", ".lock"))
        or _WINDOWS_RESERVED_PROJECT_ID.match(value) is not None
    ):
        raise PullRequestPreparationError(
            "pull-request project identity must be one bounded safe component"
        )
    return value


def _windows_reparse_component(path: Path, info: os.stat_result) -> bool:
    """Identify Windows directory aliases that ``Path.is_symlink`` omits."""

    if os.name != "nt":
        return False
    is_junction = getattr(os.path, "isjunction", None)
    junction = False
    if is_junction is not None:
        try:
            junction = bool(is_junction(path))
        except OSError:
            junction = False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(
        junction
        or attributes & reparse_flag
        or getattr(info, "st_reparse_tag", 0)
    )


def _state_root_components(path: Path) -> tuple[Path, ...]:
    return (*reversed(path.parents), path)


def _reject_aliased_state_root_components(path: Path) -> None:
    """Reject symbolic/reparse custody and non-directory path prefixes."""

    missing = False
    for component in _state_root_components(path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            missing = True
            continue
        except OSError as exc:
            raise PullRequestPreparationError(
                f"cannot inspect pull-request state path component: {component}"
            ) from exc
        if missing:
            raise PullRequestPreparationError(
                "pull-request state path changed while its custody was inspected"
            )
        if stat.S_ISLNK(info.st_mode) or _windows_reparse_component(component, info):
            raise PullRequestPreparationError(
                "pull-request state root cannot contain symbolic or reparse components"
            )
        if not stat.S_ISDIR(info.st_mode):
            raise PullRequestPreparationError(
                "pull-request state path components must be directories or absent"
            )


def _canonical_state_root(value: Path | str) -> Path:
    """Return one physical state-root spelling without accepting path aliases."""

    selected = Path(value).expanduser()
    if not selected.is_absolute():
        selected = Path.cwd() / selected
    selected = Path(os.path.abspath(os.fspath(selected)))
    _reject_aliased_state_root_components(selected)
    try:
        canonical = selected.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PullRequestPreparationError(
            "pull-request state root cannot be resolved to one physical path"
        ) from exc
    # Close an alias-insertion race around ``resolve`` and verify that its
    # physical result is itself composed only of ordinary directory custody.
    _reject_aliased_state_root_components(selected)
    _reject_aliased_state_root_components(canonical)
    try:
        rebound = selected.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PullRequestPreparationError(
            "pull-request state root cannot be resolved to one physical path"
        ) from exc
    if rebound != canonical:
        raise PullRequestPreparationError(
            "pull-request state root changed while its custody was inspected"
        )
    return canonical


def _revalidate_planned_state_root(plan: Mapping[str, Any]) -> Path:
    expected = plan.get("state_root")
    if type(expected) is not str or not expected:
        raise PullRequestPreparationError(
            "provider-bound plan state root is invalid"
        )
    current = _canonical_state_root(expected)
    if str(current) != expected:
        raise PullRequestPreparationError(
            "pull-request state root changed after plan review"
        )
    return current


def _validated_network_timeout(value: object) -> float:
    if isinstance(value, bool):
        raise PullRequestPreparationError("network timeout is invalid")
    try:
        selected = float(value)
    except (TypeError, ValueError) as exc:
        raise PullRequestPreparationError("network timeout is invalid") from exc
    if (
        not math.isfinite(selected)
        or selected <= 0
        or selected > MAX_NETWORK_TIMEOUT_SECONDS
    ):
        raise PullRequestPreparationError(
            "network timeout must be finite, positive, and no greater than "
            f"{MAX_NETWORK_TIMEOUT_SECONDS:g} seconds"
        )
    return selected


def _network_timeout_argument(value: str) -> float:
    try:
        return _validated_network_timeout(value)
    except PullRequestPreparationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _bounded_json_file(
    path_value: Path | str,
    *,
    label: str,
    maximum: int,
) -> Any:
    path = Path(path_value).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        raise PullRequestPreparationError(f"cannot inspect {label}: {exc}") from exc
    if path.is_symlink() or not path.is_file() or not 1 <= info.st_size <= maximum:
        raise PullRequestPreparationError(
            f"{label} must be a bounded regular non-symlink file"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PullRequestPreparationError(f"{label} is not strict UTF-8 JSON") from exc


def _template(value: object, label: str, *, scheme: str | None = None) -> str:
    if type(value) is not str or value.count("{number}") != 1:
        raise PullRequestPreparationError(f"provider profile {label} is invalid")
    rendered = value.format(number=1)
    if scheme is not None:
        parsed = urlsplit(rendered)
        if parsed.scheme != scheme or not parsed.netloc or parsed.username is not None:
            raise PullRequestPreparationError(
                f"provider profile {label} must be an absolute {scheme.upper()} URL"
            )
    elif not _full_ref(rendered):
        raise PullRequestPreparationError(
            f"provider profile {label} is not a full Git ref template"
        )
    return value


def load_pull_request_provider_profile(path_value: Path | str) -> dict[str, Any]:
    """Load the strict GitHub PR provider profile without changing acquisition V1."""

    value = _bounded_json_file(
        path_value,
        label="pull-request provider profile",
        maximum=MAX_PROVIDER_PROFILE_BYTES,
    )
    expected = {
        "format",
        "schema_version",
        "profile_id",
        "project_id",
        "provider",
        "repository",
        "api",
        "web",
        "git_refs",
    }
    if type(value) is not dict or set(value) != expected:
        raise PullRequestPreparationError(
            "pull-request provider profile has unsupported or missing fields"
        )
    if (
        value.get("format") != PROVIDER_PROFILE_FORMAT
        or value.get("schema_version") != 1
        or value.get("provider") != "github"
    ):
        raise PullRequestPreparationError(
            "pull-request provider profile format or provider is unsupported"
        )
    if type(value.get("profile_id")) is not str or not value["profile_id"]:
        raise PullRequestPreparationError(
            "pull-request provider profile profile_id must be text"
        )
    _project_id(value.get("project_id"))
    repository = value.get("repository")
    if type(repository) is not dict or set(repository) != {"owner", "name"}:
        raise PullRequestPreparationError("provider repository identity is invalid")
    if any(
        type(repository.get(field)) is not str
        or not _PROFILE_COMPONENT.fullmatch(repository[field])
        for field in ("owner", "name")
    ):
        raise PullRequestPreparationError("provider repository identity is invalid")
    api = value.get("api")
    web = value.get("web")
    refs = value.get("git_refs")
    if type(api) is not dict or set(api) != {"pull_request_url_template"}:
        raise PullRequestPreparationError("provider API declaration is invalid")
    if type(web) is not dict or set(web) != {"pull_request_url_template"}:
        raise PullRequestPreparationError("provider web declaration is invalid")
    if type(refs) is not dict or set(refs) != {"head_template", "merge_template"}:
        raise PullRequestPreparationError("provider Git-ref declaration is invalid")
    _template(api.get("pull_request_url_template"), "API template", scheme="https")
    _template(web.get("pull_request_url_template"), "web template", scheme="https")
    _template(refs.get("head_template"), "head ref")
    _template(refs.get("merge_template"), "merge ref")
    return value


def _provider_profile_digest(profile: Mapping[str, Any]) -> str:
    return _identity("workbench-pull-request-provider-profile", profile)


def _read_provider_response(
    profile: Mapping[str, Any],
    pull_request: int,
    *,
    metadata_path: Path | str | None,
    observer: ProviderObserver | None,
    timeout: float,
) -> tuple[Mapping[str, Any], str]:
    if metadata_path is not None and observer is not None:
        raise PullRequestPreparationError(
            "provider metadata path and injected observer are mutually exclusive"
        )
    if observer is not None:
        value = observer(profile, pull_request, timeout)
        mode = "injected"
    elif metadata_path is not None:
        value = _bounded_json_file(
            metadata_path,
            label="injected provider metadata",
            maximum=MAX_PROVIDER_RESPONSE_BYTES,
        )
        mode = "local-json"
    else:
        url = str(profile["api"]["pull_request_url_template"]).format(
            number=pull_request
        )
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "CleanroomMC-Workbench/PR-Preparation-V2",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                final = urlsplit(response.geturl())
                requested = urlsplit(url)
                if (
                    final.scheme != "https"
                    or final.netloc != requested.netloc
                    or response.status != 200
                ):
                    raise PullRequestPreparationError(
                        "GitHub provider response changed origin or status"
                    )
                payload = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise PullRequestPreparationError(
                "GitHub pull-request provider observation failed"
            ) from exc
        if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
            raise PullRequestPreparationError(
                "GitHub provider response exceeds its byte limit"
            )
        try:
            value = json.loads(payload.decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PullRequestPreparationError(
                "GitHub provider response is not strict UTF-8 JSON"
            ) from exc
        mode = "github-api"
    if not isinstance(value, Mapping):
        raise PullRequestPreparationError(
            "GitHub provider response must be one JSON object"
        )
    return value, mode


def _branch_name(value: object, label: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 1024:
        raise PullRequestPreparationError(f"provider {label} is invalid")
    full = f"refs/heads/{value}"
    if (
        not _full_ref(full)
        or ".." in value
        or "@{" in value
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or "//" in value
    ):
        raise PullRequestPreparationError(f"provider {label} is invalid")
    return value


def _provider_side(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise PullRequestPreparationError(f"GitHub provider {label} identity is absent")
    oid = value.get("sha")
    reference = _branch_name(value.get("ref"), f"{label} ref")
    repository = value.get("repo")
    full_name = repository.get("full_name") if isinstance(repository, Mapping) else None
    if type(oid) is not str or not _OBJECT_ID.fullmatch(oid):
        raise PullRequestPreparationError(
            f"GitHub provider {label} object ID is invalid"
        )
    if type(full_name) is not str or not _REPOSITORY_NAME.fullmatch(full_name):
        raise PullRequestPreparationError(
            f"GitHub provider {label} repository identity is invalid"
        )
    return {"repository": full_name, "ref": reference, "oid": oid}


def observe_github_pull_request(
    profile: Mapping[str, Any],
    pull_request: int,
    *,
    metadata_path: Path | str | None = None,
    observer: ProviderObserver | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Normalize one bounded provider response into exact review identity."""

    timeout = _validated_network_timeout(timeout)
    if isinstance(pull_request, bool) or not 1 <= pull_request <= MAX_PULL_REQUEST_NUMBER:
        raise PullRequestPreparationError("pull request number is outside its limit")
    value, mode = _read_provider_response(
        profile,
        pull_request,
        metadata_path=metadata_path,
        observer=observer,
        timeout=timeout,
    )
    expected_url = str(profile["web"]["pull_request_url_template"]).format(
        number=pull_request
    )
    if value.get("number") != pull_request or value.get("html_url") != expected_url:
        raise PullRequestPreparationError(
            "GitHub provider response does not identify the requested pull request"
        )
    state = value.get("state")
    merged = value.get("merged")
    if state not in {"open", "closed"} or type(merged) is not bool:
        raise PullRequestPreparationError("GitHub provider PR state is invalid")
    if merged and state != "closed":
        raise PullRequestPreparationError(
            "GitHub provider merged state is inconsistent"
        )
    base = _provider_side(value.get("base"), "base")
    head = _provider_side(value.get("head"), "head")
    expected_repository = (
        f"{profile['repository']['owner']}/{profile['repository']['name']}"
    )
    if base["repository"].casefold() != expected_repository.casefold():
        raise PullRequestPreparationError(
            "GitHub provider base repository is outside the selected profile"
        )
    merge_oid = value.get("merge_commit_sha")
    if merge_oid is not None and (
        type(merge_oid) is not str or not _OBJECT_ID.fullmatch(merge_oid)
    ):
        raise PullRequestPreparationError(
            "GitHub provider merge object identity is invalid"
        )
    return {
        "provider_observation_mode": mode,
        "pull_request_url": expected_url,
        "pull_request_state": state,
        "pull_request_merged": merged,
        "base_repository": base["repository"],
        "base_name": base["ref"],
        "base_oid": base["oid"],
        "head_repository": head["repository"],
        "head_name": head["ref"],
        "head_oid": head["oid"],
        "provider_merge_oid": merge_oid,
    }


def _selected_git(
    value: str | None,
    environment: Mapping[str, str] | None,
) -> str:
    try:
        executable = configured_git_executable(value, environment=environment)
    except GitObservationError as exc:
        raise PullRequestPreparationError(str(exc)) from exc
    if executable is None:
        raise PullRequestPreparationError(
            "Git is unavailable; run workbench setup and select a Git executable"
        )
    return executable


def build_pr_preparation_plan_v2(
    acquisition_profile: Mapping[str, Any],
    provider_profile: Mapping[str, Any],
    *,
    pull_request: int,
    repository: Path | str,
    state_root: Path | str,
    channel_name: str | None = None,
    git_executable: str | None = None,
    environment: Mapping[str, str] | None = None,
    provider_metadata_path: Path | str | None = None,
    provider_observer: ProviderObserver | None = None,
    network_timeout: float = 120.0,
) -> dict[str, Any]:
    """Build a provider-bound plan without fetching or changing Git refs."""

    network_timeout = _validated_network_timeout(network_timeout)
    acquisition_project_id = _project_id(acquisition_profile.get("project_id"))
    provider_project_id = _project_id(provider_profile.get("project_id"))
    if provider_project_id != acquisition_project_id:
        raise PullRequestPreparationError(
            "pull-request provider profile does not match acquisition authority"
        )
    if acquisition_profile.get("remote", {}).get("provider") != "github":
        raise PullRequestPreparationError(
            "provider-bound V2 requires a GitHub acquisition authority"
        )
    expected_remote = (
        f"https://github.com/{provider_profile['repository']['owner']}/"
        f"{provider_profile['repository']['name']}.git"
    )
    if (
        provider_metadata_path is None
        and provider_observer is None
        and acquisition_profile["remote"].get("url") != expected_remote
    ):
        raise PullRequestPreparationError(
            "GitHub provider and acquisition remote identities disagree"
        )
    executable = _selected_git(git_executable, environment)
    root = _repository_root(
        repository,
        git_executable=executable,
        environment=environment,
    )
    channel = resolve_acquisition_channel(acquisition_profile, channel_name)
    observed = observe_github_pull_request(
        provider_profile,
        pull_request,
        metadata_path=provider_metadata_path,
        observer=provider_observer,
        timeout=network_timeout,
    )
    if observed["base_name"] != channel["checkout_branch"]:
        raise PullRequestPreparationError(
            "provider PR base branch does not match the selected acquisition channel"
        )
    configured_head = acquisition_profile["remote"].get(
        "pull_request_ref_template"
    )
    provider_head = provider_profile["git_refs"]["head_template"]
    if configured_head != provider_head:
        raise PullRequestPreparationError(
            "provider and acquisition PR-head ref authorities disagree"
        )
    head_remote_ref = str(provider_head).format(number=pull_request)
    provider_merge_remote_ref = str(
        provider_profile["git_refs"]["merge_template"]
    ).format(number=pull_request)
    local_head = _git_text(
        executable,
        root,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        environment=environment,
    )
    if not _OBJECT_ID.fullmatch(local_head):
        raise PullRequestPreparationError("candidate repository HEAD is malformed")
    selected_state = _canonical_state_root(state_root)
    identity = {
        "acquisition_profile_id": acquisition_profile["profile_id"],
        "acquisition_profile_digest": _identity(
            "workbench-project-acquisition-profile", acquisition_profile
        ),
        "provider_profile_id": provider_profile["profile_id"],
        "provider_profile_digest": _provider_profile_digest(provider_profile),
        "provider_kind": provider_profile["provider"],
        "project_id": acquisition_project_id,
        "pull_request": pull_request,
        **observed,
        "channel_id": channel["id"],
        "remote_url": acquisition_profile["remote"]["url"],
        "base_remote_ref": channel["remote_ref"],
        "head_remote_ref": head_remote_ref,
        "provider_merge_remote_ref": provider_merge_remote_ref,
        "delta_kind": "provider-base-to-head",
        "repository_root": str(root),
        "repository_head_before_prepare": local_head,
        "state_root": str(selected_state),
        "git_executable": executable,
    }
    return {
        "format": PLAN_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "review-provider-state-before-network-write",
        "plan_id": _identity("workbench-pr-preparation-plan-v2", identity),
        **identity,
        "effects": [
            "Revalidate the exact GitHub pull-request response before fetching.",
            "Fetch provider-bound base, head, and declared merge objects into temporary refs.",
            "Retain immutable Workbench-only refs and a provider-bound V2 receipt.",
        ],
    }


def _fetch_refspecs(plan: Mapping[str, Any], token: str) -> dict[str, tuple[str, str]]:
    selected_token = _short_owned_ref_token(token)
    prefix = f"refs/workbench/staging/pr-v2/{selected_token}"
    refs: dict[str, tuple[str, str]] = {
        "base": (str(plan["base_oid"]), f"{prefix}/base"),
        "head": (str(plan["head_oid"]), f"{prefix}/head"),
    }
    if plan.get("provider_merge_oid") is not None:
        refs["provider_merge"] = (
            str(plan["provider_merge_oid"]),
            f"{prefix}/provider-merge",
        )
    return refs


def _short_owned_ref_token(token: str) -> str:
    """Keep owned paths short while retaining 128 bits of collision resistance."""

    if type(token) is not str or not _PLAN_TOKEN.fullmatch(token):
        raise PullRequestPreparationError("provider-bound plan token is invalid")
    return token[:_OWNED_REF_TOKEN_CHARACTERS]


def _resolve_owned_ref(
    executable: str,
    repository: Path,
    ref: str,
    *,
    environment: Mapping[str, str] | None,
) -> str | None:
    symbolic = _run_git(
        executable,
        (
            "-c",
            "core.longpaths=true",
            "-C",
            str(repository),
            "symbolic-ref",
            "--quiet",
            ref,
        ),
        environment=environment,
        timeout=30.0,
    )
    if symbolic.returncode == 0:
        raise PullRequestPreparationError(
            f"owned Workbench ref is symbolic and was not followed: {ref}"
        )
    if symbolic.returncode != 1:
        raise PullRequestPreparationError(
            f"cannot inspect owned Workbench ref custody: {ref}"
        )
    completed = _run_git(
        executable,
        (
            "-c",
            "core.longpaths=true",
            "-C",
            str(repository),
            "rev-parse",
            "--verify",
            f"{ref}^{{commit}}",
        ),
        environment=environment,
        timeout=30.0,
    )
    if completed.returncode:
        return None
    value = completed.stdout.strip()
    if not _OBJECT_ID.fullmatch(value):
        raise PullRequestPreparationError(f"prepared ref is malformed: {ref}")
    return value


def _bounded_git_output(stream: Any) -> tuple[int, str]:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    detail = stream.read(_GIT_DIAGNOSTIC_BYTES + 1)
    rendered = detail[:_GIT_DIAGNOSTIC_BYTES].decode("utf-8", "replace").strip()
    if size > _GIT_DIAGNOSTIC_BYTES and rendered:
        rendered += "..."
    return size, rendered


def _run_owned_ref_transaction(
    executable: str,
    repository: Path,
    operations: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
) -> tuple[int, int, str, int, str]:
    transaction = (
        "start\n"
        + "".join(f"{operation}\n" for operation in operations)
        + "prepare\ncommit\n"
    ).encode("utf-8", "strict")
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
            completed = subprocess.run(
                [
                    executable,
                    "-c",
                    "core.longpaths=true",
                    "-C",
                    str(repository),
                    "update-ref",
                    "--no-deref",
                    "--stdin",
                ],
                input=transaction,
                check=False,
                stdout=output,
                stderr=error,
                timeout=30.0,
                env=_git_environment(environment),
            )
            output_size, output_detail = _bounded_git_output(output)
            error_size, error_detail = _bounded_git_output(error)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PullRequestPreparationError(
            "cannot update owned Workbench refs atomically"
        ) from exc
    return (
        completed.returncode,
        output_size,
        output_detail,
        error_size,
        error_detail,
    )


def _delete_owned_refs_if_exact(
    executable: str,
    repository: Path,
    values: Sequence[tuple[str, str]],
    *,
    environment: Mapping[str, str] | None,
) -> None:
    if not values:
        return
    if any(
        not _full_ref(ref) or not _OBJECT_ID.fullmatch(oid)
        for ref, oid in values
    ):
        raise PullRequestPreparationError(
            "temporary Workbench ref cleanup transaction is invalid"
        )
    returncode, output_size, output_detail, error_size, error_detail = (
        _run_owned_ref_transaction(
            executable,
            repository,
            [f"delete {ref} {oid}" for ref, oid in values],
            environment=environment,
        )
    )
    if max(output_size, error_size) > _GIT_TRANSACTION_OUTPUT_BYTES:
        raise PullRequestPreparationError(
            "cannot remove exact temporary Workbench refs atomically: "
            "Git output exceeded its byte limit"
        )
    if returncode:
        detail = error_detail or output_detail
        raise PullRequestPreparationError(
            "cannot remove exact temporary Workbench refs atomically"
            + (f": {detail}" if detail else "")
        )


def _record_secondary_cleanup_failure(primary: BaseException, cleanup: BaseException) -> None:
    note = f"Temporary Workbench ref cleanup also failed: {cleanup}"
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(note)


def _retain_immutable_refs(
    executable: str,
    repository: Path,
    values: Mapping[str, str],
    *,
    environment: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    existing: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []
    for ref, oid in values.items():
        if not _full_ref(ref) or not _OBJECT_ID.fullmatch(oid):
            raise PullRequestPreparationError(
                "provider-bound immutable ref transaction is invalid"
            )
        observed = _resolve_owned_ref(
            executable, repository, ref, environment=environment
        )
        if observed is None:
            missing.append((ref, oid))
        elif observed != oid:
            raise PullRequestPreparationError(
                f"immutable Workbench ref already has another identity: {ref}"
            )
        else:
            existing.append((ref, oid))
    returncode, output_size, output_detail, error_size, error_detail = (
        _run_owned_ref_transaction(
            executable,
            repository,
            [
                *(f"verify {ref} {oid}" for ref, oid in existing),
                *(f"create {ref} {oid}" for ref, oid in missing),
            ],
            environment=environment,
        )
    )
    if max(output_size, error_size) > _GIT_TRANSACTION_OUTPUT_BYTES:
        primary = PullRequestPreparationError(
            "cannot retain immutable Workbench refs atomically: "
            "Git output exceeded its byte limit"
        )
        if not returncode:
            try:
                _delete_owned_refs_if_exact(
                    executable,
                    repository,
                    missing,
                    environment=environment,
                )
            except BaseException as cleanup:
                _record_secondary_cleanup_failure(primary, cleanup)
        raise primary
    if returncode:
        detail = error_detail or output_detail
        raise PullRequestPreparationError(
            "cannot retain immutable Workbench refs atomically"
            + (f": {detail}" if detail else "")
        )
    return tuple(missing)


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    """Publish new evidence without replacing an existing identity path."""

    parent = path.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise PullRequestPreparationError("pull-request V2 receipt parent is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
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
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise PullRequestPreparationError(
                "pull-request V2 receipt already exists and was not overwritten"
            ) from exc
    except OSError as exc:
        raise PullRequestPreparationError(
            f"cannot publish pull-request V2 receipt: {exc}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def apply_pr_preparation_plan_v2(
    acquisition_profile: Mapping[str, Any],
    provider_profile: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
    provider_metadata_path: Path | str | None = None,
    provider_observer: ProviderObserver | None = None,
    network_timeout: float = 300.0,
) -> dict[str, Any]:
    """Revalidate the provider response, fetch exact objects, and issue V2."""

    network_timeout = _validated_network_timeout(network_timeout)
    expected = build_pr_preparation_plan_v2(
        acquisition_profile,
        provider_profile,
        pull_request=int(plan.get("pull_request", 0)),
        repository=str(plan.get("repository_root", "")),
        state_root=str(plan.get("state_root", "")),
        channel_name=str(plan.get("channel_id", "")),
        git_executable=str(plan.get("git_executable", "")),
        environment=environment,
        provider_metadata_path=provider_metadata_path,
        provider_observer=provider_observer,
        network_timeout=network_timeout,
    )
    if expected != dict(plan):
        raise PullRequestPreparationError(
            "provider pull-request state or local inputs changed; review a fresh V2 plan"
        )
    executable = str(plan["git_executable"])
    repository = Path(str(plan["repository_root"]))
    token = str(plan["plan_id"]).rsplit(":", 1)[-1]
    staging: dict[str, tuple[str, str]] | None = None
    for _attempt in range(4):
        candidate = _fetch_refspecs(plan, secrets.token_hex(32))
        if all(
            _resolve_owned_ref(
                executable,
                repository,
                ref,
                environment=environment,
            )
            is None
            for _remote, ref in candidate.values()
        ):
            staging = candidate
            break
    if staging is None:
        raise PullRequestPreparationError(
            "cannot reserve a private Workbench staging-ref namespace"
        )
    refspecs = [f"+{remote}:{local}" for remote, local in staging.values()]
    fetch = _run_git(
        executable,
        (
            "-c",
            "core.longpaths=true",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.ext.allow=never",
            "-C",
            str(repository),
            "fetch",
            "--atomic",
            "--force",
            "--no-recurse-submodules",
            "--no-tags",
            "--no-write-fetch-head",
            str(plan["remote_url"]),
            *refspecs,
        ),
        environment=environment,
        timeout=network_timeout,
    )
    if fetch.returncode:
        detail = (fetch.stderr or fetch.stdout).strip()[:3000]
        raise PullRequestPreparationError(
            "Git provider-bound pull-request fetch failed"
            + (f": {detail}" if detail else f" (exit {fetch.returncode})")
        )
    expected_oids = {
        "base": str(plan["base_oid"]),
        "head": str(plan["head_oid"]),
        **(
            {"provider_merge": str(plan["provider_merge_oid"])}
            if plan.get("provider_merge_oid") is not None
            else {}
        ),
    }
    observed_staging: dict[str, str] = {}
    created_refs: tuple[tuple[str, str], ...] = ()
    preparation_error: BaseException | None = None
    try:
        for key, (_, ref) in staging.items():
            observed = _resolve_owned_ref(
                executable, repository, ref, environment=environment
            )
            if observed is not None:
                observed_staging[ref] = observed
            if observed != expected_oids[key]:
                raise PullRequestPreparationError(
                    "provider-bound remote object changed during fetch; review a fresh V2 plan"
                )
        after_fetch = build_pr_preparation_plan_v2(
            acquisition_profile,
            provider_profile,
            pull_request=int(plan["pull_request"]),
            repository=str(plan["repository_root"]),
            state_root=str(plan["state_root"]),
            channel_name=str(plan["channel_id"]),
            git_executable=executable,
            environment=environment,
            provider_metadata_path=provider_metadata_path,
            provider_observer=provider_observer,
            network_timeout=network_timeout,
        )
        if after_fetch != dict(plan):
            raise PullRequestPreparationError(
                "provider pull-request state changed during fetch; review a fresh V2 plan"
            )
        namespace = (
            f"refs/workbench/review-v2/{plan['project_id']}/"
            f"pr-{plan['pull_request']}/{_short_owned_ref_token(token)}"
        )
        base_ref = f"{namespace}/base"
        head_ref = f"{namespace}/head"
        provider_merge_ref = (
            f"{namespace}/provider-merge"
            if plan.get("provider_merge_oid") is not None
            else None
        )
        immutable = {
            base_ref: str(plan["base_oid"]),
            head_ref: str(plan["head_oid"]),
            **(
                {str(provider_merge_ref): str(plan["provider_merge_oid"])}
                if provider_merge_ref is not None
                else {}
            ),
        }
        created_refs = _retain_immutable_refs(
            executable,
            repository,
            immutable,
            environment=environment,
        )
    except BaseException as error:
        preparation_error = error
        raise
    finally:
        try:
            _delete_owned_refs_if_exact(
                executable,
                repository,
                tuple(observed_staging.items()),
                environment=environment,
            )
        except BaseException as cleanup:
            if preparation_error is None:
                try:
                    _delete_owned_refs_if_exact(
                        executable,
                        repository,
                        created_refs,
                        environment=environment,
                    )
                except BaseException as immutable_cleanup:
                    _record_secondary_cleanup_failure(cleanup, immutable_cleanup)
                raise
            _record_secondary_cleanup_failure(preparation_error, cleanup)
    receipt_body = {
        key: plan[key]
        for key in (
            "acquisition_profile_id",
            "acquisition_profile_digest",
            "provider_profile_id",
            "provider_profile_digest",
            "provider_kind",
            "project_id",
            "pull_request",
            "pull_request_url",
            "pull_request_state",
            "pull_request_merged",
            "channel_id",
            "remote_url",
            "repository_root",
            "repository_head_before_prepare",
            "base_repository",
            "base_name",
            "base_remote_ref",
            "base_oid",
            "head_repository",
            "head_name",
            "head_remote_ref",
            "head_oid",
            "provider_merge_remote_ref",
            "provider_merge_oid",
            "delta_kind",
        )
    }
    receipt_body.update(
        {
            "base_ref": base_ref,
            "head_ref": head_ref,
            "provider_merge_ref": provider_merge_ref,
        }
    )
    receipt_id = _identity("workbench-pr-preparation-v2", receipt_body)
    proposed_receipt = {
        "format": RECEIPT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "receipt_id": receipt_id,
        **receipt_body,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        receipt_state = _revalidate_planned_state_root(plan)
        receipt_path = (
            receipt_state
            / "evidence"
            / "pr-preparation-v2"
            / str(plan["project_id"])
            / f"pr-{plan['pull_request']}"
            / f"{receipt_id.rsplit(':', 1)[-1]}.json"
        )
        if receipt_path.exists():
            receipt = load_pr_preparation_receipt_v2(receipt_path)
            if receipt["receipt_id"] != receipt_id:
                raise PullRequestPreparationError(
                    "existing V2 receipt path has another identity"
                )
        else:
            # Rebind physical custody immediately before the first receipt
            # directory or file mutation, after all network and Git-ref work.
            if _revalidate_planned_state_root(plan) != receipt_state:
                raise PullRequestPreparationError(
                    "pull-request state root changed before receipt publication"
                )
            _write_json_exclusive(receipt_path, proposed_receipt)
            receipt = proposed_receipt
    except BaseException as primary:
        try:
            _delete_owned_refs_if_exact(
                executable,
                repository,
                created_refs,
                environment=environment,
            )
        except BaseException as cleanup:
            _record_secondary_cleanup_failure(primary, cleanup)
        raise
    return {
        "format": RESULT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "outcome": "provider-bound-prepared",
        "plan_id": plan["plan_id"],
        "receipt_id": receipt_id,
        "receipt_path": str(receipt_path),
        "repository_root": plan["repository_root"],
        "pull_request": plan["pull_request"],
        "pull_request_url": plan["pull_request_url"],
        "pull_request_state": plan["pull_request_state"],
        "base_oid": plan["base_oid"],
        "head_oid": plan["head_oid"],
        "provider_merge_oid": plan["provider_merge_oid"],
        "delta_kind": plan["delta_kind"],
        "next_command": [
            "workbench",
            "review",
            "recipes",
            "--profile",
            str(plan["project_id"]),
            "--source",
            str(plan["repository_root"]),
            "--prepared-receipt",
            str(receipt_path),
        ],
    }


_RECEIPT_FIELDS = {
    "format",
    "schema_version",
    "receipt_id",
    "acquisition_profile_id",
    "acquisition_profile_digest",
    "provider_profile_id",
    "provider_profile_digest",
    "provider_kind",
    "project_id",
    "pull_request",
    "pull_request_url",
    "pull_request_state",
    "pull_request_merged",
    "channel_id",
    "remote_url",
    "repository_root",
    "repository_head_before_prepare",
    "base_repository",
    "base_name",
    "base_remote_ref",
    "base_oid",
    "base_ref",
    "head_repository",
    "head_name",
    "head_remote_ref",
    "head_oid",
    "head_ref",
    "provider_merge_remote_ref",
    "provider_merge_oid",
    "provider_merge_ref",
    "delta_kind",
    "prepared_at",
}


def _receipt_identity_body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in _RECEIPT_FIELDS
        if key not in {"format", "schema_version", "receipt_id", "prepared_at"}
    }


def load_pr_preparation_receipt_v2(path_value: Path | str) -> dict[str, Any]:
    """Load and authenticate a provider-bound V2 receipt."""

    value = _bounded_json_file(
        path_value,
        label="pull-request preparation V2 receipt",
        maximum=MAX_RECEIPT_BYTES,
    )
    if type(value) is not dict or set(value) != _RECEIPT_FIELDS:
        raise PullRequestPreparationError(
            "pull-request preparation V2 receipt has unsupported or missing fields"
        )
    if value.get("format") != RECEIPT_FORMAT or value.get("schema_version") != 2:
        raise PullRequestPreparationError(
            "pull-request preparation V2 receipt format is unsupported"
        )
    if (
        isinstance(value.get("pull_request"), bool)
        or not isinstance(value.get("pull_request"), int)
        or not 1 <= value["pull_request"] <= MAX_PULL_REQUEST_NUMBER
    ):
        raise PullRequestPreparationError("receipt pull_request is invalid")
    for field in (
        "receipt_id",
        "acquisition_profile_id",
        "acquisition_profile_digest",
        "provider_profile_id",
        "provider_profile_digest",
        "provider_kind",
        "project_id",
        "pull_request_url",
        "channel_id",
        "remote_url",
        "repository_root",
        "base_repository",
        "base_name",
        "head_repository",
        "head_name",
        "delta_kind",
        "prepared_at",
    ):
        if type(value.get(field)) is not str or not value[field]:
            raise PullRequestPreparationError(f"receipt {field} must be nonempty text")
    _project_id(value["project_id"])
    if value["provider_kind"] != "github":
        raise PullRequestPreparationError("receipt provider_kind is unsupported")
    if (
        not _RECEIPT_ID.fullmatch(value["receipt_id"])
        or not _ACQUISITION_DIGEST.fullmatch(value["acquisition_profile_digest"])
        or not _PROVIDER_DIGEST.fullmatch(value["provider_profile_digest"])
    ):
        raise PullRequestPreparationError("receipt identity syntax is invalid")
    parsed_url = urlsplit(value["pull_request_url"])
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise PullRequestPreparationError("receipt pull_request_url is invalid")
    if (
        not _REPOSITORY_NAME.fullmatch(value["base_repository"])
        or not _REPOSITORY_NAME.fullmatch(value["head_repository"])
    ):
        raise PullRequestPreparationError("receipt repository identity is invalid")
    _branch_name(value["base_name"], "receipt base ref")
    _branch_name(value["head_name"], "receipt head ref")
    if value["delta_kind"] != "provider-base-to-head":
        raise PullRequestPreparationError("receipt delta_kind is unsupported")
    if value["pull_request_state"] not in {"open", "closed"}:
        raise PullRequestPreparationError("receipt pull_request_state is invalid")
    if type(value["pull_request_merged"]) is not bool:
        raise PullRequestPreparationError("receipt pull_request_merged must be boolean")
    if value["pull_request_merged"] and value["pull_request_state"] != "closed":
        raise PullRequestPreparationError("receipt merged state is inconsistent")
    for field in (
        "base_remote_ref",
        "base_ref",
        "head_remote_ref",
        "head_ref",
        "provider_merge_remote_ref",
    ):
        if not _full_ref(value.get(field)):
            raise PullRequestPreparationError(f"receipt {field} is not a full Git ref")
    for field in (
        "repository_head_before_prepare",
        "base_oid",
        "head_oid",
    ):
        if type(value.get(field)) is not str or not _OBJECT_ID.fullmatch(value[field]):
            raise PullRequestPreparationError(f"receipt {field} is not an object ID")
    provider_merge_oid = value["provider_merge_oid"]
    provider_merge_ref = value["provider_merge_ref"]
    if (provider_merge_oid is None) != (provider_merge_ref is None):
        raise PullRequestPreparationError(
            "receipt provider merge object and immutable ref must both be present or absent"
        )
    if provider_merge_oid is not None and (
        type(provider_merge_oid) is not str
        or not _OBJECT_ID.fullmatch(provider_merge_oid)
        or type(provider_merge_ref) is not str
        or not _full_ref(provider_merge_ref)
    ):
        raise PullRequestPreparationError("receipt provider merge identity is invalid")
    expected_id = _identity(
        "workbench-pr-preparation-v2", _receipt_identity_body(value)
    )
    if value["receipt_id"] != expected_id:
        raise PullRequestPreparationError(
            "pull-request preparation V2 receipt identity is invalid"
        )
    return value


def verify_pr_preparation_receipt_v2(
    receipt: Mapping[str, Any],
    *,
    repository: Path | str,
    acquisition_profile: Mapping[str, Any] | None = None,
    provider_profile: Mapping[str, Any] | None = None,
    git_executable: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify V2 immutable refs and historical delta entirely offline."""

    if acquisition_profile is not None and (
        receipt.get("acquisition_profile_id") != acquisition_profile.get("profile_id")
        or receipt.get("project_id") != acquisition_profile.get("project_id")
        or receipt.get("acquisition_profile_digest")
        != _identity("workbench-project-acquisition-profile", acquisition_profile)
    ):
        raise PullRequestPreparationError(
            "V2 receipt does not match the selected acquisition profile"
        )
    if provider_profile is not None and (
        receipt.get("provider_profile_id") != provider_profile.get("profile_id")
        or receipt.get("project_id") != provider_profile.get("project_id")
        or receipt.get("provider_profile_digest")
        != _provider_profile_digest(provider_profile)
    ):
        raise PullRequestPreparationError(
            "V2 receipt does not match the selected provider profile"
        )
    executable = _selected_git(git_executable, environment)
    root = _repository_root(
        repository,
        git_executable=executable,
        environment=environment,
    )
    try:
        recorded_root = Path(str(receipt["repository_root"])).resolve(strict=True)
    except OSError as exc:
        raise PullRequestPreparationError(
            "prepared repository is no longer available"
        ) from exc
    if root != recorded_root:
        raise PullRequestPreparationError(
            "preparation V2 receipt belongs to another repository"
        )
    ref_pairs = [
        ("base_ref", "base_oid"),
        ("head_ref", "head_oid"),
    ]
    if receipt.get("provider_merge_ref") is not None:
        ref_pairs.append(("provider_merge_ref", "provider_merge_oid"))
    for ref_field, oid_field in ref_pairs:
        observed = _git_text(
            executable,
            root,
            (
                "-c",
                "core.longpaths=true",
                "rev-parse",
                "--verify",
                f"{receipt[ref_field]}^{{commit}}",
            ),
            environment=environment,
        )
        if observed != receipt[oid_field]:
            raise PullRequestPreparationError(
                f"provider-bound immutable ref no longer matches: {receipt[ref_field]}"
            )
    changed_paths = _changed_paths(
        executable,
        root,
        str(receipt["base_oid"]),
        str(receipt["head_oid"]),
        environment=environment,
    )
    if len(changed_paths) > MAX_CHANGED_PATHS or sum(
        len(path.encode("utf-8")) for path in changed_paths
    ) > MAX_CHANGED_PATH_BYTES:
        raise PullRequestPreparationError("historical PR delta exceeds V2 bounds")
    return {
        "repository_root": str(root),
        "git_executable": executable,
        "repository_changed_paths": list(changed_paths),
        "repository_changed_file_count": len(changed_paths),
        "historical_delta": {
            "kind": receipt["delta_kind"],
            "baseline_oid": receipt["base_oid"],
            "candidate_oid": receipt["head_oid"],
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench review prepare-pr",
        description=(
            "Observe a GitHub PR before fetch, revalidate provider state on apply, "
            "and retain an offline-verifiable V2 receipt."
        ),
    )
    parser.add_argument("pull_request", type=int)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--channel")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply", metavar="PLAN_ID")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--git-executable")
    parser.add_argument(
        "--network-timeout",
        type=_network_timeout_argument,
        default=300.0,
        metavar="SECONDS",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _render_plan(plan: Mapping[str, Any]) -> str:
    merge = plan["provider_merge_oid"] or "not supplied by provider"
    return (
        "Workbench provider-bound pull-request preparation plan V2\n"
        f"Project: {plan['project_id']}\n"
        f"Pull request: {plan['pull_request_url']} · {plan['pull_request_state']}\n"
        f"Original base: {plan['base_repository']}:{plan['base_name']} at {plan['base_oid']}\n"
        f"Original head: {plan['head_repository']}:{plan['head_name']} at {plan['head_oid']}\n"
        f"Provider merge: {merge}\n"
        f"Repository: {plan['repository_root']}\n"
        f"Plan: {plan['plan_id']}\n"
    )


def _render_result(result: Mapping[str, Any]) -> str:
    return (
        f"Pull request {result['pull_request_url']} is provider-bound and prepared.\n"
        f"State: {result['pull_request_state']}\n"
        f"Original base: {result['base_oid']}\n"
        f"Original head: {result['head_oid']}\n"
        f"Provider merge: {result['provider_merge_oid'] or 'not supplied'}\n"
        "Historical delta: provider-recorded base to provider-recorded head\n"
        f"Receipt: {result['receipt_path']}\n"
        f"Next: {' '.join(str(part) for part in result['next_command'])}\n"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    acquisition_profiles: Mapping[str, Path | str],
    provider_profiles: Mapping[str, Path | str],
    default_state_root: Path | str,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    from .project_acquisition import load_acquisition_profile

    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    stdin = sys.stdin if input_stream is None else input_stream
    try:
        acquisition_path = acquisition_profiles.get(args.profile)
        provider_path = provider_profiles.get(args.profile)
        if acquisition_path is None or provider_path is None:
            raise PullRequestPreparationError(
                f"profile {args.profile!r} has no provider-bound PR preparation authority"
            )
        acquisition = load_acquisition_profile(acquisition_path)
        provider = load_pull_request_provider_profile(provider_path)
        plan = build_pr_preparation_plan_v2(
            acquisition,
            provider,
            pull_request=args.pull_request,
            repository=args.source,
            state_root=args.state_root or default_state_root,
            channel_name=args.channel,
            git_executable=args.git_executable,
            environment=environment,
            network_timeout=args.network_timeout,
        )
        if args.plan:
            if args.apply is not None:
                raise PullRequestPreparationError(
                    "--plan and --apply are mutually exclusive"
                )
            stdout.write(
                json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_plan(plan)
            )
            return 0
        if args.apply is not None:
            if args.apply != plan["plan_id"]:
                raise PullRequestPreparationError(
                    "--apply does not match current provider identities; review V2 again"
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
            stdout.write(
                f"Type prepare-v2 {token} to fetch these exact objects, or Enter to cancel: "
            )
            stdout.flush()
            if stdin.readline().strip() != f"prepare-v2 {token}":
                raise PullRequestPreparationCancelled
        result = apply_pr_preparation_plan_v2(
            acquisition,
            provider,
            plan,
            environment=environment,
            network_timeout=args.network_timeout,
        )
        stdout.write(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.json
            else _render_result(result)
        )
        return 0
    except PullRequestPreparationCancelled:
        stdout.write("Provider-bound PR preparation cancelled; nothing was changed.\n")
        return 0
    except (OSError, PullRequestPreparationError, ValueError) as exc:
        stderr.write(f"Workbench provider-bound PR preparation failed: {exc}\n")
        return 2


__all__ = [
    "PLAN_FORMAT",
    "PROVIDER_PROFILE_FORMAT",
    "RECEIPT_FORMAT",
    "RESULT_FORMAT",
    "SCHEMA_VERSION",
    "apply_pr_preparation_plan_v2",
    "build_pr_preparation_plan_v2",
    "load_pr_preparation_receipt_v2",
    "load_pull_request_provider_profile",
    "main",
    "observe_github_pull_request",
    "verify_pr_preparation_receipt_v2",
]
