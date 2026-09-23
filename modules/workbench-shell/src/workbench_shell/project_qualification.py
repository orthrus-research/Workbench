"""Consent-bound qualification of an existing project against a pack profile.

The qualification is a private association with suite-owned profile authority.
It never writes an identity marker, configuration file, or other byte into the
developer's checkout.  Git state is retained as provenance, while freshness is
based only on evidence relevant to pack-family conformance.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from typing import Any, TextIO
import unicodedata

from workbench_api.host_filesystem import (
    HostFilesystemError,
    fsync_directory,
    private_path,
    secure_private_path,
)
from workbench_project_intelligence import ProjectInspectionError
from workbench_project_intelligence.git_observation import (
    GitObservationError,
    run_git_observation,
)

from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_core.setup_cli import SetupError, setup_record_lock
from .workspace_dashboard import (
    WorkspaceHomeV2Error,
    _workspace_content_fingerprint,
)


STATUS_FORMAT = "workbench-project-qualification-status-v1"
PLAN_FORMAT = "workbench-project-qualification-plan-v1"
RESULT_FORMAT = "workbench-project-qualification-result-v1"
BINDING_FORMAT = "workbench-project-qualification-binding-v1"
SCHEMA_VERSION = 1
PROJECT_ID = "supersymmetry"
PROFILE_SELECTOR = "supersymmetry"
PACK_PROFILE_ID = "workbench-pack:supersymmetry"
STATE_DIRECTORY = "project-qualification-v1"
MAX_BINDING_BYTES = 2 * 1024 * 1024
MAX_DIRTY_ENTRIES = 100_000
MAX_TEXT_BYTES = 4096


class ProjectQualificationError(RuntimeError):
    """Qualification input, evidence, or retained state is unsafe or invalid."""


class ProjectQualificationCancelled(Exception):
    """The user declined a reviewed interactive qualification plan."""


class ProjectQualificationCommittedInterrupt(KeyboardInterrupt):
    """Cancellation arrived after the external binding became visible."""

    def __init__(self, path: Path, state_revision: str) -> None:
        super().__init__("qualification binding committed before cancellation")
        self.path = path
        self.state_revision = state_revision


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _identity(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def _terminal_control(character: str) -> bool:
    return unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}


def _escaped_terminal_text(value: object) -> str:
    rendered: list[str] = []
    for character in str(value):
        if not _terminal_control(character):
            rendered.append(character)
            continue
        codepoint = ord(character)
        rendered.append(
            f"\\u{codepoint:04x}"
            if codepoint <= 0xFFFF
            else f"\\U{codepoint:08x}"
        )
    return "".join(rendered)


def _bounded_text(value: object, *, maximum: int = MAX_TEXT_BYTES) -> str:
    """Return control-escaped strict UTF-8 text bounded for JSON/terminal custody."""

    rendered = _escaped_terminal_text(value)
    encoded = rendered.encode("utf-8", "backslashreplace")[:maximum]
    while encoded:
        try:
            return encoded.decode("utf-8", "strict")
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


def _require_terminal_safe_text(value: str, label: str) -> str:
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ProjectQualificationError(f"{label} must be strict UTF-8") from exc
    if len(encoded) > MAX_TEXT_BYTES:
        raise ProjectQualificationError(f"{label} exceeds its byte limit")
    if any(_terminal_control(character) for character in value):
        raise ProjectQualificationError(
            f"{label} contains terminal control characters"
        )
    return value


def _require_identity(value: object, prefix: str, label: str) -> str:
    expected = f"{prefix}:sha256:"
    if (
        type(value) is not str
        or not value.startswith(expected)
        or len(value) != len(expected) + 64
        or any(character not in "0123456789abcdef" for character in value[-64:])
    ):
        raise ProjectQualificationError(f"{label} identity is invalid")
    return value


def _require_digest(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ProjectQualificationError(f"{label} digest is invalid")
    return value


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _reject_symbolic_components(path: Path, *, label: str) -> None:
    for component in (*reversed(path.parents), path):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ProjectQualificationError(
                f"cannot inspect {label}: {component}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ProjectQualificationError(
                f"{label} must not contain symbolic or non-directory components"
            )


def _workspace(path: Path | str) -> Path:
    selected = _absolute(path)
    _require_terminal_safe_text(str(selected), "workspace path")
    try:
        metadata = selected.lstat()
    except OSError as exc:
        raise ProjectQualificationError(
            f"workspace is unavailable: {selected}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ProjectQualificationError(
            "workspace must be a regular non-symlink directory"
        )
    try:
        resolved = selected.resolve(strict=True)
    except OSError as exc:
        raise ProjectQualificationError(
            f"workspace is unavailable: {selected}"
        ) from exc
    _require_terminal_safe_text(str(resolved), "workspace path")
    return resolved


def _state_base(value: Path | str) -> Path:
    selected = _absolute(value)
    _require_terminal_safe_text(str(selected), "qualification state path")
    _reject_symbolic_components(selected, label="qualification state")
    missing: list[str] = []
    cursor = selected
    while True:
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            if cursor.parent == cursor:
                raise ProjectQualificationError(
                    "qualification state has no physical directory ancestor"
                )
            missing.append(cursor.name)
            cursor = cursor.parent
            continue
        except OSError as exc:
            raise ProjectQualificationError(
                "qualification state physical identity is unavailable"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise ProjectQualificationError(
                "qualification state physical ancestor is not a directory"
            )
        break
    try:
        physical_parent = cursor.resolve(strict=True)
    except OSError as exc:
        raise ProjectQualificationError(
            "qualification state physical identity is unavailable"
        ) from exc
    physical = physical_parent.joinpath(*reversed(missing))
    _require_terminal_safe_text(str(physical), "qualification state path")
    return physical


def _existing_directory_ancestor(path: Path) -> Path:
    cursor = path
    while True:
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            if cursor.parent == cursor:
                raise ProjectQualificationError(
                    "qualification state has no existing directory ancestor"
                )
            cursor = cursor.parent
            continue
        except OSError as exc:
            raise ProjectQualificationError(
                "qualification state directory identity is unavailable"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise ProjectQualificationError(
                "qualification state ancestor is not a directory"
            )
        return cursor


def _same_physical_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError) as exc:
        # Both operands are existing directories selected by the custody
        # checks immediately above this comparison. Disappearance, denied
        # access, or an unsupported identity query is uncertainty rather than
        # evidence that the paths differ, so the isolation guard fails closed.
        raise ProjectQualificationError(
            "qualification state physical identity cannot be compared safely"
        ) from exc


def _path_has_git_marker(path: Path) -> bool:
    """Return whether a state ancestor visibly identifies a Git repository."""

    for ancestor in (path, *path.parents):
        marker = ancestor / ".git"
        try:
            marker.lstat()
        except FileNotFoundError:
            try:
                ancestor_metadata = ancestor.lstat()
            except OSError as exc:
                raise ProjectQualificationError(
                    "qualification state Git marker identity is unavailable"
                ) from exc
            if not stat.S_ISDIR(ancestor_metadata.st_mode):
                raise ProjectQualificationError(
                    "qualification state Git marker ancestor is not a directory"
                )
        except OSError as exc:
            raise ProjectQualificationError(
                "qualification state Git marker identity is unavailable"
            ) from exc
        else:
            # A regular directory, worktree pointer file, symlink/reparse
            # marker, or special-file substitution all make repository identity
            # non-absent. Git must resolve it successfully before state is safe.
            return True

        bare_members = (
            (ancestor / "HEAD", "file"),
            (ancestor / "objects", "directory"),
            (ancestor / "refs", "directory"),
        )
        complete_bare_signature = True
        for candidate, expected_kind in bare_members:
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                complete_bare_signature = False
                break
            except OSError as exc:
                raise ProjectQualificationError(
                    "qualification state bare Git identity is unavailable"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode) or (
                expected_kind == "file" and not stat.S_ISREG(metadata.st_mode)
            ) or (
                expected_kind == "directory" and not stat.S_ISDIR(metadata.st_mode)
            ):
                complete_bare_signature = False
                break
        if complete_bare_signature:
            return True
    return False


def _git_directory(path: Path, *, required: bool) -> Path | None:
    try:
        observed = run_git_observation(path, ("rev-parse", "--absolute-git-dir"))
    except (GitObservationError, OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectQualificationError(
            ("workspace" if required else "qualification state")
            + " Git directory cannot be observed safely: "
            + _bounded_text(exc)
        ) from exc
    if observed.returncode:
        if (
            not required
            and observed.returncode == 128
            and not _path_has_git_marker(path)
        ):
            return None
        detail = _bounded_text(observed.stderr.strip() or "unknown Git failure")
        raise ProjectQualificationError(
            ("workspace" if required else "qualification state")
            + f" Git directory observation failed: {detail}"
        )
    raw = observed.stdout.strip()
    if not raw:
        raise ProjectQualificationError(
            ("workspace" if required else "qualification state")
            + " Git returned no directory identity"
        )
    _require_terminal_safe_text(raw, "Git directory path")
    selected = _absolute(raw)
    try:
        return selected.resolve(strict=True)
    except OSError as exc:
        raise ProjectQualificationError(
            ("workspace" if required else "qualification state")
            + " Git directory identity is unavailable"
        ) from exc


def _assert_state_outside_workspace(workspace: Path, state_root: Path) -> None:
    """Reject lexical, physical, and same-repository checkout aliases."""

    if state_root == workspace or state_root.is_relative_to(workspace):
        raise ProjectQualificationError(
            "qualification state must remain outside the target checkout"
        )
    state_anchor = _existing_directory_ancestor(state_root)
    for ancestor in (state_anchor, *state_anchor.parents):
        if _same_physical_path(ancestor, workspace):
            raise ProjectQualificationError(
                "qualification state physically aliases the target checkout"
            )
    workspace_git = _git_directory(workspace, required=True)
    state_git = _git_directory(state_anchor, required=False)
    if state_git is not None and (
        state_git == workspace_git
        or _same_physical_path(state_git, workspace_git)
    ):
        raise ProjectQualificationError(
            "qualification state aliases the target checkout Git directory"
        )


def _profile(configuration: WorkbenchConfiguration) -> dict[str, str]:
    return {
        "selector": PROFILE_SELECTOR,
        "pack_profile_id": configuration.pack_profile_id,
        "pack_variant": configuration.pack_variant,
        "platform_profile_id": configuration.platform_profile_id,
        "selection_digest": configuration.selection_digest,
    }


def _load_configuration(
    suite_root: Path,
    selector: str,
) -> WorkbenchConfiguration:
    if selector != PROFILE_SELECTOR:
        raise ProjectQualificationError(
            "--profile supersymmetry is required; pack selection is never implicit"
        )
    try:
        configuration = load_workbench_configuration(
            suite_root,
            CONFIGURATION_PATH,
        )
    except WorkbenchConfigurationError as exc:
        raise ProjectQualificationError(
            f"the suite-owned Workbench configuration is invalid: {exc}"
        ) from exc
    if configuration.pack_profile_id != PACK_PROFILE_ID:
        raise ProjectQualificationError(
            "the suite-owned configuration does not select the Supersymmetry family"
        )
    return configuration


def _git_provenance(workspace: Path) -> tuple[str, bool, list[str], str]:
    try:
        root_result = run_git_observation(workspace, ("rev-parse", "--show-toplevel"))
    except (GitObservationError, OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectQualificationError(
            "Git provenance cannot be observed safely: " + _bounded_text(exc)
        ) from exc
    if root_result.returncode:
        detail = _bounded_text(root_result.stderr.strip() or "unknown Git failure")
        raise ProjectQualificationError(
            f"Git repository root observation failed: {detail}"
        )
    repository_root_text = root_result.stdout.strip()
    if not repository_root_text:
        raise ProjectQualificationError("Git returned no repository root")
    _require_terminal_safe_text(repository_root_text, "Git repository root")
    repository_root = Path(repository_root_text).resolve(strict=True)
    if repository_root != workspace:
        raise ProjectQualificationError(
            "the selected pack workspace must be the Git repository root; "
            "nested pack paths are not inspected because repository-wide Git "
            "status could expose sibling paths or content"
        )
    try:
        revision_result = run_git_observation(workspace, ("rev-parse", "HEAD"))
        status_result = run_git_observation(
            workspace,
            (
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=all",
            ),
        )
    except (GitObservationError, OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectQualificationError(
            "Git provenance cannot be observed safely: " + _bounded_text(exc)
        ) from exc
    for label, completed in (("revision", revision_result), ("status", status_result)):
        if completed.returncode:
            detail = _bounded_text(completed.stderr.strip() or "unknown Git failure")
            raise ProjectQualificationError(
                f"Git {label} observation failed: {detail}"
            )
    revision = revision_result.stdout.strip()
    if (
        len(revision) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        raise ProjectQualificationError("Git returned an invalid workspace revision")
    entries = sorted(line for line in status_result.stdout.splitlines() if line)
    if len(entries) > MAX_DIRTY_ENTRIES:
        raise ProjectQualificationError(
            "Git status exceeds the qualification provenance entry limit"
        )
    for entry in entries:
        try:
            encoded = entry.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ProjectQualificationError(
                "Git status contains a path that is not strict UTF-8"
            ) from exc
        if len(encoded) > MAX_TEXT_BYTES:
            raise ProjectQualificationError(
                "Git status contains a path outside the qualification byte limit"
            )
        if any(_terminal_control(character) for character in entry):
            raise ProjectQualificationError(
                "Git status contains terminal control characters"
            )
    try:
        fingerprint = _workspace_content_fingerprint(
            {
                "repository": {
                    "state": "observed",
                    "root": str(repository_root),
                    "head": revision,
                }
            },
            workspace,
        )
    except (OSError, ValueError, WorkspaceHomeV2Error) as exc:
        raise ProjectQualificationError(
            "workspace provenance cannot be identity-bound: " + _bounded_text(exc)
        ) from exc
    return revision, bool(entries), entries, fingerprint


def _workspace_projection(workspace: Path) -> dict[str, Any]:
    revision, dirty, entries, fingerprint = _git_provenance(workspace)
    return {
        "root": str(workspace),
        "root_uri": workspace.as_uri(),
        "revision": revision,
        "dirty": dirty,
        "dirty_entries": entries,
        "dirty_fingerprint": fingerprint,
    }


def _required_markers(configuration: WorkbenchConfiguration) -> list[dict[str, str]]:
    workspace = configuration.pack_document.values.get("workspace")
    raw = workspace.get("required_paths") if isinstance(workspace, Mapping) else None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ProjectQualificationError(
            "the selected pack profile has no bounded workspace markers"
        )
    markers: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, Mapping):
            raise ProjectQualificationError("pack profile workspace marker is invalid")
        path = row.get("path")
        kind = row.get("kind")
        if (
            not isinstance(path, str)
            or not path
            or path in seen
            or kind not in {"file", "directory"}
        ):
            raise ProjectQualificationError("pack profile workspace marker is invalid")
        seen.add(path)
        markers.append({"path": path, "kind": str(kind)})
    return markers


def _qualification_observation(
    suite_root: Path,
    workspace: Path,
    configuration: WorkbenchConfiguration,
) -> dict[str, Any]:
    profile = _profile(configuration)
    workspace_value = _workspace_projection(workspace)
    markers = _required_markers(configuration)
    try:
        inspected = inspect_project(
            suite_root,
            workspace,
            configuration=configuration,
        )
    except (OSError, ProjectInspectionError, WorkbenchConfigurationError, ValueError) as exc:
        detail = _bounded_text(exc)
        checks = [
            {
                "id": "profile-conformance",
                "label": "Supersymmetry profile conformance",
                "state": "incompatible",
                "detail": detail or "Exact project inspection failed.",
            },
            {
                "id": "packwiz-index-integrity",
                "label": "Packwiz index integrity",
                "state": "incompatible",
                "detail": "Unavailable until profile conformance succeeds.",
            },
        ]
        evidence = {
            "project_id": PROJECT_ID,
            "profile": profile,
            "configuration_manifest_sha256": configuration.manifest.sha256,
            "workspace_root": str(workspace),
            "required_markers": markers,
            "inspection_failure": checks[0]["detail"],
        }
        return {
            "project_id": PROJECT_ID,
            "profile": profile,
            "workspace": workspace_value,
            "inspection_id": _identity("workbench-project-inspection", evidence),
            "state": "incompatible",
            "can_apply": False,
            "checks": checks,
            "limitations": [
                "No pack-family qualification is granted while exact profile inspection is incompatible."
            ],
        }

    context = inspected.get("workspace_context")
    inspected_workspace = context.get("workspace") if isinstance(context, Mapping) else None
    project = context.get("project") if isinstance(context, Mapping) else None
    pack = context.get("pack") if isinstance(context, Mapping) else None
    platform = context.get("platform") if isinstance(context, Mapping) else None
    if not all(
        isinstance(row, Mapping)
        for row in (context, inspected_workspace, project, pack, platform)
    ):
        raise ProjectQualificationError("exact project inspection returned no usable context")
    if any(
        inspected_workspace.get(key) != workspace_value[key]
        for key in ("root_uri", "revision", "dirty", "dirty_entries")
    ):
        raise ProjectQualificationError(
            "Git provenance changed during exact project inspection; retry qualification"
        )
    if (
        pack.get("profile_family_id") != profile["pack_profile_id"]
        or pack.get("selected_profile") != profile["pack_variant"]
        or platform.get("profile_id") != profile["platform_profile_id"]
    ):
        raise ProjectQualificationError(
            "exact project inspection differs from the selected profile authority"
        )
    if project.get("matched_paths") != [row["path"] for row in markers]:
        raise ProjectQualificationError(
            "exact project inspection did not retain every profile workspace marker"
        )
    index = project.get("index")
    if not isinstance(index, Mapping):
        raise ProjectQualificationError("exact project inspection returned no Packwiz index")
    index_matches = index.get("matches_declared_hash") is True
    checks = [
        {
            "id": "profile-conformance",
            "label": "Supersymmetry profile conformance",
            "state": "ready",
            "detail": (
                "Required marker types, Packwiz identity, Minecraft loader, and selected profile agree."
            ),
        },
        {
            "id": "packwiz-index-integrity",
            "label": "Packwiz index integrity",
            "state": "ready" if index_matches else "attention",
            "detail": (
                "The Packwiz index matches its declared SHA-256."
                if index_matches
                else (
                    "The current Packwiz index does not match its declared SHA-256; "
                    "family qualification remains available, but runtime payload integrity is separate."
                )
            ),
        },
    ]
    limitations = [
        "Qualification selects pack-family authority; it is not a stable support or runtime-success claim."
    ]
    state = "ready"
    if not index_matches:
        state = "attention"
        limitations.append(
            "The declared and observed Packwiz index differ; qualification does not establish Packwiz or runtime payload integrity."
        )
    relevant_project = {
        key: project.get(key)
        for key in (
            "kind",
            "name",
            "version",
            "author",
            "pack_format",
            "manifest_sha256",
            "minecraft_version",
            "loaders",
            "index",
            "matched_paths",
        )
    }
    evidence = {
        "project_id": PROJECT_ID,
        "profile": profile,
        "configuration_manifest_sha256": configuration.manifest.sha256,
        "workspace_root": str(workspace),
        "required_markers": markers,
        "project": relevant_project,
    }
    return {
        "project_id": PROJECT_ID,
        "profile": profile,
        "workspace": workspace_value,
        "inspection_id": _identity("workbench-project-inspection", evidence),
        "state": state,
        "can_apply": True,
        "checks": checks,
        "limitations": limitations,
    }


def _binding_id(workspace: Path) -> str:
    return _identity(
        "workbench-project-qualification-binding",
        {"workspace_root": str(workspace), "pack_profile_id": PACK_PROFILE_ID},
    )


def _binding_path(state_root: Path, binding_id: str) -> Path:
    digest = binding_id.rsplit(":", 1)[-1]
    return state_root / STATE_DIRECTORY / "bindings" / f"{digest}.json"


def _binding_body(qualification: Mapping[str, Any], binding_id: str) -> dict[str, Any]:
    return {
        "format": BINDING_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "binding_id": binding_id,
        "project_id": qualification["project_id"],
        "profile": qualification["profile"],
        "workspace": qualification["workspace"],
        "inspection_id": qualification["inspection_id"],
        "state": qualification["state"],
        "checks": qualification["checks"],
        "limitations": qualification["limitations"],
    }


def _binding_record(qualification: Mapping[str, Any], binding_id: str) -> dict[str, Any]:
    body = _binding_body(qualification, binding_id)
    return {
        **body,
        "state_revision": _identity("workbench-project-qualification-state", body),
    }


def _validate_profile(value: object) -> dict[str, str]:
    if type(value) is not dict or set(value) != {
        "selector",
        "pack_profile_id",
        "pack_variant",
        "platform_profile_id",
        "selection_digest",
    }:
        raise ProjectQualificationError("qualification profile is invalid")
    if (
        value.get("selector") != PROFILE_SELECTOR
        or value.get("pack_profile_id") != PACK_PROFILE_ID
    ):
        raise ProjectQualificationError("qualification profile identity is invalid")
    for key in ("pack_variant", "platform_profile_id"):
        member = value.get(key)
        if type(member) is not str or not member or len(member.encode("utf-8")) > 512:
            raise ProjectQualificationError(
                f"qualification profile {key} is invalid"
            )
        _require_terminal_safe_text(member, f"qualification profile {key}")
    _require_digest(value.get("selection_digest"), "qualification profile selection")
    return value


def _validate_workspace(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "root",
        "root_uri",
        "revision",
        "dirty",
        "dirty_entries",
        "dirty_fingerprint",
    }:
        raise ProjectQualificationError("qualification workspace is invalid")
    root_value = value.get("root")
    if type(root_value) is not str or not root_value:
        raise ProjectQualificationError("qualification workspace root is invalid")
    _require_terminal_safe_text(root_value, "qualification workspace root")
    root = Path(root_value)
    if not root.is_absolute() or str(_absolute(root)) != root_value:
        raise ProjectQualificationError("qualification workspace root is not canonical")
    try:
        expected_uri = root.as_uri()
    except ValueError as exc:
        raise ProjectQualificationError(
            "qualification workspace URI is invalid"
        ) from exc
    if value.get("root_uri") != expected_uri:
        raise ProjectQualificationError("qualification workspace URI changed")
    revision = value.get("revision")
    if (
        type(revision) is not str
        or len(revision) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        raise ProjectQualificationError("qualification Git revision is invalid")
    dirty = value.get("dirty")
    entries = value.get("dirty_entries")
    if type(dirty) is not bool or type(entries) is not list:
        raise ProjectQualificationError("qualification Git status is invalid")
    if len(entries) > MAX_DIRTY_ENTRIES or entries != sorted(set(entries)):
        raise ProjectQualificationError("qualification Git entries are invalid")
    for entry in entries:
        if (
            type(entry) is not str
            or not entry
            or len(entry.encode("utf-8")) > MAX_TEXT_BYTES
        ):
            raise ProjectQualificationError("qualification Git entry is invalid")
        _require_terminal_safe_text(entry, "qualification Git entry")
    if dirty is not bool(entries):
        raise ProjectQualificationError("qualification Git dirty state is inconsistent")
    _require_digest(value.get("dirty_fingerprint"), "qualification dirty fingerprint")
    return value


def _validate_checks(value: object, state: object) -> list[dict[str, str]]:
    expected = (
        ("profile-conformance", "Supersymmetry profile conformance"),
        ("packwiz-index-integrity", "Packwiz index integrity"),
    )
    if type(value) is not list or len(value) != len(expected):
        raise ProjectQualificationError("qualification checks are invalid")
    rows: list[dict[str, str]] = []
    for row, (expected_id, expected_label) in zip(value, expected, strict=True):
        if type(row) is not dict or set(row) != {"id", "label", "state", "detail"}:
            raise ProjectQualificationError("qualification check is invalid")
        if row.get("id") != expected_id or row.get("label") != expected_label:
            raise ProjectQualificationError("qualification check identity is invalid")
        check_state = row.get("state")
        detail = row.get("detail")
        if check_state not in {"ready", "attention", "incompatible"}:
            raise ProjectQualificationError("qualification check state is invalid")
        if (
            type(detail) is not str
            or not detail
            or len(detail.encode("utf-8")) > MAX_TEXT_BYTES
        ):
            raise ProjectQualificationError("qualification check detail is invalid")
        _require_terminal_safe_text(detail, "qualification check detail")
        rows.append(row)
    derived = (
        "incompatible"
        if any(row["state"] == "incompatible" for row in rows)
        else "attention"
        if any(row["state"] == "attention" for row in rows)
        else "ready"
    )
    if state != derived:
        raise ProjectQualificationError("qualification state disagrees with its checks")
    return rows


def _validate_limitations(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= 16:
        raise ProjectQualificationError("qualification limitations are invalid")
    if len(value) != len(set(value)):
        raise ProjectQualificationError("qualification limitations contain duplicates")
    for row in value:
        if (
            type(row) is not str
            or not row
            or len(row.encode("utf-8")) > MAX_TEXT_BYTES
        ):
            raise ProjectQualificationError("qualification limitation is invalid")
        _require_terminal_safe_text(row, "qualification limitation")
    return value


def _validate_binding(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProjectQualificationError("qualification binding must be one JSON object")
    expected = {
        "format",
        "schema_version",
        "binding_id",
        "project_id",
        "profile",
        "workspace",
        "inspection_id",
        "state",
        "checks",
        "limitations",
        "state_revision",
    }
    if set(value) != expected:
        raise ProjectQualificationError(
            "qualification binding has unsupported or missing fields"
        )
    if value.get("format") != BINDING_FORMAT or value.get("schema_version") != 1:
        raise ProjectQualificationError("qualification binding format is unsupported")
    if value.get("project_id") != PROJECT_ID:
        raise ProjectQualificationError("qualification binding project is invalid")
    profile = _validate_profile(value.get("profile"))
    workspace = _validate_workspace(value.get("workspace"))
    state = value.get("state")
    if state not in {"ready", "attention"}:
        raise ProjectQualificationError(
            "qualification binding cannot retain incompatible state"
        )
    _validate_checks(value.get("checks"), state)
    _validate_limitations(value.get("limitations"))
    _require_identity(
        value.get("inspection_id"),
        "workbench-project-inspection",
        "qualification inspection",
    )
    binding_id = _require_identity(
        value.get("binding_id"),
        "workbench-project-qualification-binding",
        "qualification binding",
    )
    if binding_id != _binding_id(Path(workspace["root"])):
        raise ProjectQualificationError("qualification binding targets another workspace")
    if profile["pack_profile_id"] != PACK_PROFILE_ID:
        raise ProjectQualificationError("qualification binding targets another profile")
    body = {key: value[key] for key in expected - {"state_revision"}}
    expected_revision = _identity("workbench-project-qualification-state", body)
    if value.get("state_revision") != expected_revision:
        raise ProjectQualificationError("qualification binding identity is invalid")
    return value


def _load_binding(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProjectQualificationError(
            f"cannot inspect qualification binding: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProjectQualificationError(
            "qualification binding must be a regular non-symlink file"
        )
    if not 1 <= metadata.st_size <= MAX_BINDING_BYTES:
        raise ProjectQualificationError("qualification binding is outside its byte limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProjectQualificationError(
            f"cannot open qualification binding safely: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not 1 <= opened.st_size <= MAX_BINDING_BYTES
            or (os.name != "nt" and opened.st_mode & 0o077)
            or (
                os.name != "nt"
                and hasattr(os, "geteuid")
                and opened.st_uid != os.geteuid()
            )
        ):
            raise ProjectQualificationError(
                "qualification binding is not an owner-private regular file"
            )
        if os.name == "nt" and not private_path(path, directory=False):
            raise ProjectQualificationError(
                "qualification binding is not owner-private"
            )
        chunks: list[bytes] = []
        consumed = 0
        while consumed <= MAX_BINDING_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, MAX_BINDING_BYTES + 1 - consumed),
            )
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        after = os.fstat(descriptor)
        if (
            consumed > MAX_BINDING_BYTES
            or opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or consumed != after.st_size
        ):
            raise ProjectQualificationError(
                "qualification binding changed while it was read"
            )
        raw = b"".join(chunks)
    except OSError as exc:
        raise ProjectQualificationError(
            f"cannot read qualification binding safely: {exc}"
        ) from exc
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectQualificationError(
            "qualification binding is not strict UTF-8 JSON"
        ) from exc
    return _validate_binding(value)


def _binding_view(
    qualification: Mapping[str, Any],
    *,
    state_root: Path,
) -> dict[str, Any]:
    workspace = Path(str(qualification["workspace"]["root"]))
    binding_id = _binding_id(workspace)
    path = _binding_path(state_root, binding_id)
    _reject_symbolic_components(path.parent, label="qualification binding state")
    retained = _load_binding(path)
    if retained is None:
        return {
            "state": "absent",
            "binding_id": binding_id,
            "path": str(path),
            "state_revision": None,
            "stale_reasons": [],
        }
    reasons: list[str] = []
    if retained.get("binding_id") != binding_id:
        reasons.append("BINDING_ID_CHANGED")
    if retained.get("project_id") != qualification["project_id"]:
        reasons.append("PROJECT_ID_CHANGED")
    if retained.get("profile") != qualification["profile"]:
        reasons.append("PROFILE_SELECTION_CHANGED")
    retained_workspace = retained.get("workspace")
    if (
        not isinstance(retained_workspace, Mapping)
        or retained_workspace.get("root") != qualification["workspace"]["root"]
    ):
        reasons.append("WORKSPACE_ROOT_CHANGED")
    if retained.get("inspection_id") != qualification["inspection_id"]:
        reasons.append("QUALIFICATION_EVIDENCE_CHANGED")
    if qualification["state"] == "incompatible":
        reasons.append("PROFILE_CONFORMANCE_INCOMPATIBLE")
    return {
        "state": "stale" if reasons else "current",
        "binding_id": binding_id,
        "path": str(path),
        "state_revision": retained["state_revision"],
        "stale_reasons": sorted(set(reasons)),
    }


def qualification_status(
    suite_root: Path | str,
    workspace: Path | str,
    *,
    profile_selector: str,
    state_root: Path | str,
) -> dict[str, Any]:
    """Read exact conformance and retained qualification without mutation."""

    suite = _absolute(suite_root).resolve(strict=True)
    target = _workspace(workspace)
    state = _state_base(state_root)
    _assert_state_outside_workspace(target, state)
    configuration = _load_configuration(suite, profile_selector)
    qualification = _qualification_observation(
        suite,
        target,
        configuration,
    )
    binding = _binding_view(qualification, state_root=state)
    qualified = (
        qualification["can_apply"] is True and binding["state"] == "current"
    )
    return {
        "format": STATUS_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        **qualification,
        "binding": binding,
        "qualified": qualified,
    }


def build_qualification_plan(status: Mapping[str, Any]) -> dict[str, Any]:
    """Build one deterministic consent unit from a read-only status."""

    binding = dict(status["binding"])
    if status["can_apply"] is not True:
        actions: list[dict[str, Any]] = []
        consent = {
            "required": False,
            "prompt": None,
            "non_interactive": None,
        }
        action_identity: Mapping[str, Any] | None = None
    else:
        operation = {
            "absent": "atomic-private-record-create",
            "stale": "atomic-private-record-replace",
            "current": "reuse-current-binding",
        }[binding["state"]]
        action = {
            "id": "persist-project-qualification",
            "operation": operation,
            "destination": binding["path"],
            "effect": (
                "Reuse the current external qualification binding without rewriting it."
                if operation == "reuse-current-binding"
                else "Persist the reviewed qualification in private external Workbench state without writing the checkout."
            ),
        }
        actions = [action]
        action_identity = action
        consent = {
            "required": True,
            "prompt": "Apply this qualification? [y/N]",
            "non_interactive": "Pass this exact plan_id with --apply.",
        }
    identity = {
        "project_id": status["project_id"],
        "profile": status["profile"],
        "workspace_root": status["workspace"]["root"],
        "inspection_id": status["inspection_id"],
        "qualification_state": status["state"],
        "binding": {
            key: binding[key]
            for key in ("state", "binding_id", "path", "state_revision")
        },
        "action": action_identity,
    }
    return {
        "format": PLAN_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "review-before-mutation",
        "plan_id": _identity("workbench-project-qualification-plan", identity),
        "state": status["state"],
        "can_apply": status["can_apply"],
        "project_id": status["project_id"],
        "profile": status["profile"],
        "workspace": status["workspace"],
        "inspection_id": status["inspection_id"],
        "binding": binding,
        "checks": status["checks"],
        "limitations": status["limitations"],
        "actions": actions,
        "consent": consent,
    }


def _ensure_private_state(
    path: Path,
    *,
    workspace: Path,
    state_root: Path,
) -> None:
    _reject_symbolic_components(path.parent.parent, label="qualification state")
    for directory in (path.parent.parent, path.parent):
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            secure_private_path(directory, directory=True)
        except (OSError, HostFilesystemError) as exc:
            raise ProjectQualificationError(
                f"cannot secure qualification state: {exc}"
            ) from exc
        if directory.is_symlink() or not private_path(directory, directory=True):
            raise ProjectQualificationError("qualification state directory is unsafe")
    _reject_symbolic_components(path.parent, label="qualification state")
    # Directory creation can cross a platform alias or race with an external
    # reparse/bind change. Rebind physical and Git identities before any file
    # or lock is created beneath them.
    rebound = _state_base(state_root)
    _assert_state_outside_workspace(workspace, rebound)
    if rebound != state_root:
        raise ProjectQualificationError(
            "qualification state physical identity changed during creation"
        )


def _write_binding(
    path: Path,
    value: Mapping[str, Any],
    *,
    workspace: Path,
    state_root: Path,
) -> None:
    _ensure_private_state(path, workspace=workspace, state_root=state_root)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise ProjectQualificationError("qualification staging path already exists")
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        secure_private_path(temporary, directory=False)
        os.replace(temporary, path)
        fsync_directory(path.parent)
        if not private_path(path, directory=False):
            raise ProjectQualificationError(
                "published qualification binding is not owner-private"
            )
    except KeyboardInterrupt as exc:
        # Atomic replace is the commit point. A signal immediately after it
        # must not be rendered as a rollback: verify the exact intended record
        # through the ordinary strict loader before reporting a committed
        # interruption.
        try:
            committed = _load_binding(path)
        except (OSError, ProjectQualificationError):
            committed = None
        if committed == dict(value):
            raise ProjectQualificationCommittedInterrupt(
                path,
                str(committed["state_revision"]),
            ) from exc
        raise
    except ProjectQualificationError:
        raise
    except (OSError, HostFilesystemError) as exc:
        raise ProjectQualificationError(
            f"cannot publish qualification binding: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _qualification_lock(path: Path):
    try:
        with setup_record_lock(path):
            yield
    except SetupError as exc:
        raise ProjectQualificationError(
            f"cannot lock qualification state: {exc}"
        ) from exc


def apply_qualification_plan(
    suite_root: Path | str,
    workspace: Path | str,
    *,
    profile_selector: str,
    state_root: Path | str,
    expected_plan_id: str,
) -> dict[str, Any]:
    """Reinspect exact evidence and apply only the current reviewed plan."""

    current_status = qualification_status(
        suite_root,
        workspace,
        profile_selector=profile_selector,
        state_root=state_root,
    )
    current_plan = build_qualification_plan(current_status)
    if current_plan["plan_id"] != expected_plan_id:
        raise ProjectQualificationError(
            "--apply does not match the current qualification plan; inspect and review it again"
        )
    if current_plan["can_apply"] is not True:
        raise ProjectQualificationError(
            "the current workspace is incompatible and cannot be qualified"
        )
    binding_path = Path(current_plan["binding"]["path"])
    target = Path(str(current_status["workspace"]["root"]))
    effective_state_root = _state_base(state_root)
    _assert_state_outside_workspace(target, effective_state_root)
    _ensure_private_state(
        binding_path,
        workspace=target,
        state_root=effective_state_root,
    )
    intended_record: dict[str, Any] | None = None
    try:
        with _qualification_lock(binding_path):
            locked_status = qualification_status(
                suite_root,
                workspace,
                profile_selector=profile_selector,
                state_root=effective_state_root,
            )
            locked_plan = build_qualification_plan(locked_status)
            if locked_plan["plan_id"] != expected_plan_id:
                raise ProjectQualificationError(
                    "qualification evidence or retained state changed while applying"
                )
            operation = locked_plan["actions"][0]["operation"]
            if operation == "reuse-current-binding":
                outcome = "reused"
                retained = _load_binding(binding_path)
                if retained is None:
                    raise ProjectQualificationError(
                        "qualification binding disappeared while it was reused"
                    )
            else:
                binding_id = locked_plan["binding"]["binding_id"]
                intended_record = _binding_record(locked_status, binding_id)
                retained = intended_record
                _write_binding(
                    binding_path,
                    retained,
                    workspace=target,
                    state_root=effective_state_root,
                )
                outcome = (
                    "qualified" if operation.endswith("create") else "requalified"
                )
        binding = {
            "state": "current",
            "binding_id": retained["binding_id"],
            "path": str(binding_path),
            "state_revision": retained["state_revision"],
            "stale_reasons": [],
        }
        qualification = {"qualified": True}
        qualification.update(
            {
                key: locked_status[key]
                for key in (
                    "project_id",
                    "profile",
                    "workspace",
                    "inspection_id",
                    "state",
                    "checks",
                    "limitations",
                )
            }
        )
        return {
            "format": RESULT_FORMAT,
            "schema_version": SCHEMA_VERSION,
            "outcome": outcome,
            "applied_plan_id": expected_plan_id,
            "binding": binding,
            "qualification": qualification,
            "next_commands": [
                [
                    "workbench",
                    "project",
                    "qualify",
                    locked_status["workspace"]["root"],
                    "--profile",
                    PROFILE_SELECTOR,
                    "--status",
                    "--state-root",
                    str(effective_state_root),
                ],
            ],
        }
    except ProjectQualificationCommittedInterrupt:
        raise
    except KeyboardInterrupt as exc:
        if intended_record is not None:
            try:
                committed = _load_binding(binding_path)
            except (OSError, ProjectQualificationError):
                committed = None
            if committed == intended_record:
                raise ProjectQualificationCommittedInterrupt(
                    binding_path,
                    str(committed["state_revision"]),
                ) from exc
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench project qualify",
        description=(
            "Inspect an existing checkout against suite-owned pack authority and "
            "persist only a private external qualification binding after consent."
        ),
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--profile", required=True)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--status", action="store_true")
    operation.add_argument("--plan", action="store_true")
    operation.add_argument("--apply", metavar="PLAN_ID")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def _render_checks(checks: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        f"- {_bounded_text(row['label'])}: "
        f"[{_bounded_text(str(row['state']).upper())}] "
        f"{_bounded_text(row['detail'])}"
        for row in checks
    ]


def _render_status(status: Mapping[str, Any]) -> str:
    lines = [
        "Workbench project qualification · Status",
        f"State: [{_bounded_text(str(status['state']).upper())}]",
        f"Qualified: {'yes' if status['qualified'] else 'no'}",
        f"Workspace: {_bounded_text(status['workspace']['root'])}",
        f"Profile: {_bounded_text(status['profile']['pack_profile_id'])}",
        "Binding: "
        f"{_bounded_text(status['binding']['state'])} · "
        f"{_bounded_text(status['binding']['binding_id'])}",
        "",
        "Checks",
        *_render_checks(status["checks"]),
    ]
    return "\n".join(lines) + "\n"


def _render_plan(plan: Mapping[str, Any]) -> str:
    lines = [
        "Workbench project qualification · Plan",
        f"State: [{_bounded_text(str(plan['state']).upper())}]",
        f"Workspace: {_bounded_text(plan['workspace']['root'])}",
        f"Profile: {_bounded_text(plan['profile']['pack_profile_id'])}",
        f"Revision: {_bounded_text(plan['workspace']['revision'])}"
        + (" · dirty" if plan["workspace"]["dirty"] else " · clean"),
        "Binding: "
        f"{_bounded_text(plan['binding']['state'])} · "
        f"{_bounded_text(plan['binding']['binding_id'])}",
        f"Plan: {_bounded_text(plan['plan_id'])}",
        "",
        "Checks",
        *_render_checks(plan["checks"]),
    ]
    if not plan["can_apply"]:
        lines.extend(("", "This workspace is incompatible; no qualification can be applied."))
    return "\n".join(lines) + "\n"


def _render_result(result: Mapping[str, Any]) -> str:
    qualification = result["qualification"]
    next_command = _bounded_text(shlex.join(result["next_commands"][0]))
    return (
        "Workbench project qualification · Saved\n"
        f"Outcome: {_bounded_text(result['outcome'])}\n"
        f"State: [{_bounded_text(str(qualification['state']).upper())}]\n"
        f"Workspace: {_bounded_text(qualification['workspace']['root'])}\n"
        f"Profile: {_bounded_text(qualification['profile']['pack_profile_id'])}\n"
        f"Binding: {_bounded_text(result['binding']['binding_id'])}\n"
        f"Next: {next_command}\n"
    )


def _committed_interrupt_after_apply(
    *,
    root: Path | str,
    workspace: Path | str,
    profile_selector: str,
    state_root: Path | str,
    reviewed_plan: Mapping[str, Any] | None,
) -> ProjectQualificationCommittedInterrupt | None:
    """Recognize the narrow post-return signal window without guessing.

    A signal can be delivered after ``apply_qualification_plan`` returns but
    before its result is assigned or rendered.  Only an absent/stale reviewed
    binding becoming the exact current binding proves that this invocation
    crossed the atomic commit point.
    """

    if (
        reviewed_plan is None
        or reviewed_plan.get("can_apply") is not True
        or reviewed_plan.get("binding", {}).get("state") not in {"absent", "stale"}
    ):
        return None
    try:
        current = qualification_status(
            root,
            workspace,
            profile_selector=profile_selector,
            state_root=state_root,
        )
    except (OSError, ProjectQualificationError, ValueError):
        return None
    binding = current.get("binding")
    reviewed_binding = reviewed_plan.get("binding")
    if (
        current.get("qualified") is not True
        or not isinstance(binding, Mapping)
        or not isinstance(reviewed_binding, Mapping)
        or binding.get("state") != "current"
        or binding.get("binding_id") != reviewed_binding.get("binding_id")
        or not isinstance(binding.get("path"), str)
        or not isinstance(binding.get("state_revision"), str)
    ):
        return None
    return ProjectQualificationCommittedInterrupt(
        Path(binding["path"]),
        binding["state_revision"],
    )


def _report_committed_interrupt(
    error: ProjectQualificationCommittedInterrupt,
    *,
    workspace: Path | str,
    profile_selector: str,
    state_root: Path | str,
    stderr: TextIO,
) -> None:
    verify = shlex.join(
        [
            "workbench",
            "project",
            "qualify",
            str(workspace),
            "--profile",
            profile_selector,
            "--status",
            "--state-root",
            str(state_root),
        ]
    )
    stderr.write(
        "\nProject qualification committed before cancellation.\n"
        f"Binding: {_bounded_text(error.path)}\n"
        f"State revision: {_bounded_text(error.state_revision)}\n"
        f"Verify: {_bounded_text(verify)}\n"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | str,
    default_state_root: Path | str,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    """Run the project qualification CLI with injectable physical ports."""

    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    try:
        for argument in raw_arguments:
            if type(argument) is not str:
                raise ProjectQualificationError("command arguments must be text")
            _require_terminal_safe_text(argument, "command argument")
    except ProjectQualificationError as exc:
        stderr.write(
            "Workbench project qualification failed: " + _bounded_text(exc) + "\n"
        )
        return 2
    args = _parser().parse_args(raw_arguments)
    selected_state_root = args.state_root or Path(default_state_root)
    effective_state_root: Path | None = None
    reviewed_plan: Mapping[str, Any] | None = None
    apply_started = False
    try:
        effective_state_root = _state_base(selected_state_root)
        status = qualification_status(
            root,
            args.workspace,
            profile_selector=args.profile,
            state_root=effective_state_root,
        )
        if args.status:
            stdout.write(
                json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_status(status)
            )
            # Status is a successful read even when it reports absent, stale,
            # or incompatible qualification. Consumers branch on the
            # structured state instead of losing useful evidence to a generic
            # non-zero transport failure.
            return 0
        plan = build_qualification_plan(status)
        reviewed_plan = plan
        if args.plan:
            stdout.write(
                json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_plan(plan)
            )
            # A non-applicable plan is still a complete review result. Its
            # can_apply/state/checks fields carry the fail-closed decision.
            return 0
        if args.apply is not None:
            expected_plan_id = args.apply
        else:
            stdout.write(
                json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                if args.json
                else _render_plan(plan)
            )
            if not plan["can_apply"]:
                return 1
            # JSON is a single-document machine transport. A bare JSON
            # invocation is therefore review-only; callers apply by making a
            # second request with the exact returned plan_id.
            if args.json:
                return 0
            interactive = bool(getattr(stdin, "isatty", lambda: False)()) and bool(
                getattr(stdout, "isatty", lambda: False)()
            )
            if not interactive:
                return 0
            stdout.write("Apply this qualification? [y/N] ")
            stdout.flush()
            answer = stdin.readline()
            if answer == "" or answer.strip().casefold() not in {"y", "yes"}:
                raise ProjectQualificationCancelled
            expected_plan_id = plan["plan_id"]
        apply_started = True
        result = apply_qualification_plan(
            root,
            args.workspace,
            profile_selector=args.profile,
            state_root=effective_state_root,
            expected_plan_id=expected_plan_id,
        )
        stdout.write(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.json
            else _render_result(result)
        )
        return 0
    except ProjectQualificationCancelled:
        stdout.write("Project qualification cancelled; nothing was changed.\n")
        return 0
    except ProjectQualificationCommittedInterrupt as exc:
        _report_committed_interrupt(
            exc,
            workspace=args.workspace,
            profile_selector=args.profile,
            state_root=effective_state_root or selected_state_root,
            stderr=stderr,
        )
        return 130
    except KeyboardInterrupt:
        committed = (
            _committed_interrupt_after_apply(
                root=root,
                workspace=args.workspace,
                profile_selector=args.profile,
                state_root=effective_state_root or selected_state_root,
                reviewed_plan=reviewed_plan,
            )
            if apply_started
            else None
        )
        if committed is not None:
            _report_committed_interrupt(
                committed,
                workspace=args.workspace,
                profile_selector=args.profile,
                state_root=effective_state_root or selected_state_root,
                stderr=stderr,
            )
            return 130
        stdout.write("\nProject qualification cancelled; nothing was changed.\n")
        return 130
    except (OSError, ProjectQualificationError, ValueError) as exc:
        stderr.write(
            "Workbench project qualification failed: " + _bounded_text(exc) + "\n"
        )
        return 2


__all__ = [
    "BINDING_FORMAT",
    "PLAN_FORMAT",
    "PROJECT_ID",
    "ProjectQualificationError",
    "RESULT_FORMAT",
    "STATUS_FORMAT",
    "apply_qualification_plan",
    "build_qualification_plan",
    "main",
    "qualification_status",
]
