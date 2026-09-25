"""User-facing Workbench setup, dependency review, and repair wizard.

Setup is deliberately separate from Configuration V1.  It records only local
paths and an explicit reference to a configuration manifest; profile-owned
versions and policy remain in their existing documents.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, MutableMapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, TextIO
from urllib.parse import urlparse
from urllib.request import url2pathname

from workbench_core.configuration import (
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_core.environment_status import inspect_environment_status
from workbench_core.host_requirements import inspect_host_requirements, probe_git_executable
from workbench_core.human_presentation import (
    HumanPresentation,
    Tone,
    human_command,
    human_presentation,
)
from workbench_core.manual_artifacts import (
    ManualArtifactError,
    inspect_manual_artifacts,
    load_manual_artifact_profile,
    manual_artifact_profile_for_configuration,
)
from workbench_core.runtime_java import (
    JavaRuntimeError,
    WINDOWS_UNICODE_CDS_TRANSFORM_ID,
    discover_java_runtime,
    ensure_java_runtime,
    host_platform,
    inspect_managed_java_runtime,
    load_java_runtime_policy,
    plan_managed_java_execution,
)
from workbench_api.state_paths import default_runtime_state_root
from .user_config_home import default_user_record_path


CHECK_FORMAT = "workbench-setup-check-v1"
CHECK_FORMAT_V2 = "workbench-setup-check-v2"
PLAN_FORMAT = "workbench-setup-plan-v1"
PLAN_FORMAT_V2 = "workbench-setup-plan-v2"
PLAN_FORMAT_V3 = "workbench-setup-plan-v3"
RECORD_FORMAT = "workbench-user-setup-v1"
RESULT_FORMAT = "workbench-setup-result-v1"
RESULT_FORMAT_V2 = "workbench-setup-result-v2"
SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 256 * 1024


class SetupError(ValueError):
    """Setup input, retained state, or an owned operation is invalid."""


class SetupCancelled(Exception):
    """The user closed or cancelled the interactive wizard."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(prefix: str, value: Any) -> str:
    return prefix + ":sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def default_setup_record_path(
    *, environment: Mapping[str, str] | None = None
) -> Path:
    """Use the stable home for fresh setup and retain older saved selections."""

    return default_user_record_path("setup-v1.json", environment=environment)


def _record_payload(selection: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": RECORD_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "selection": dict(selection),
    }


def _validate_record(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise SetupError("setup record must be one JSON object")
    if set(value) != {"format", "schema_version", "record_id", "selection"}:
        raise SetupError("setup record has unsupported or missing fields")
    if value.get("format") != RECORD_FORMAT or value.get("schema_version") != 1:
        raise SetupError("setup record is not Workbench user setup V1")
    selection = value.get("selection")
    if type(selection) is not dict:
        raise SetupError("setup record selection must be an object")
    expected_fields = {
        "workspace",
        "state_root",
        "profile_config",
        "profile_selection_digest",
        "java_home",
        "managed_java_home",
        "git_executable",
    }
    if set(selection) != expected_fields:
        raise SetupError("setup record selection has unsupported or missing fields")
    for field in ("workspace", "state_root"):
        if type(selection.get(field)) is not str or not selection[field]:
            raise SetupError(f"setup record {field} must be a path")
    for field in expected_fields - {"workspace", "state_root"}:
        if selection.get(field) is not None and type(selection[field]) is not str:
            raise SetupError(f"setup record {field} must be a string or null")
    expected_id = _digest("workbench-user-setup", _record_payload(selection))
    if value.get("record_id") != expected_id:
        raise SetupError("setup record identity does not match its selection")
    return value


def load_setup_record(path: Path | str) -> dict[str, Any] | None:
    """Load a bounded, non-symlink user setup record without changing it."""

    selected = Path(path).expanduser()
    try:
        info = selected.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetupError(f"cannot inspect setup record: {exc}") from exc
    if selected.is_symlink() or not selected.is_file():
        raise SetupError("setup record must be a regular non-symlink file")
    if not 1 <= info.st_size <= MAX_RECORD_BYTES:
        raise SetupError("setup record is outside its byte limit")
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SetupError("setup record is not strict UTF-8 JSON") from exc
    return _validate_record(value)


def apply_setup_environment_defaults(
    record: Mapping[str, Any],
    *,
    environment: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply saved physical defaults without selecting profile authority.

    Explicit caller environment wins for tool and state bindings.  The saved
    workspace is different: it is the user's later, reviewed choice and must
    replace any bootstrap workspace inherited from the process that launched
    Workbench.  Explicit command targets still win at the command boundary.
    The selected profile remains an input to setup/Java validation only;
    action-specific profile arguments are never synthesized here.
    """

    validated = _validate_record(dict(record))
    selection = validated["selection"]
    defaults = {
        "WORKBENCH_WORKSPACE": selection["workspace"],
        "WORKBENCH_STATE_ROOT": selection["state_root"],
    }
    git_executable = selection.get("git_executable")
    if isinstance(git_executable, str):
        defaults["WORKBENCH_GIT_EXECUTABLE"] = git_executable
    java_home = selection.get("java_home") or selection.get("managed_java_home")
    if isinstance(java_home, str):
        defaults["WORKBENCH_JAVA_HOME"] = java_home
    target = os.environ if environment is None else environment
    target["WORKBENCH_WORKSPACE"] = defaults["WORKBENCH_WORKSPACE"]
    for key, value in defaults.items():
        if key != "WORKBENCH_WORKSPACE":
            target.setdefault(key, value)

    # Current owners consume the exact executable through
    # WORKBENCH_GIT_EXECUTABLE.  A few frozen identity-bearing V1/V2 owners
    # still invoke the conventional ``git`` program name, so also expose the
    # selected executable's directory to those unchanged contracts.  This is
    # process-local: setup never edits the user's shell configuration.
    active_git = target.get("WORKBENCH_GIT_EXECUTABLE")
    if active_git:
        git_parent = str(Path(active_git).expanduser().absolute().parent)
        path_parts = [part for part in target.get("PATH", "").split(os.pathsep) if part]
        if git_parent not in path_parts:
            target["PATH"] = os.pathsep.join([git_parent, *path_parts])
    return {key: target[key] for key in defaults}


@contextmanager
def setup_record_lock(path: Path | str):
    """Serialize cooperating setup writers on POSIX and native Windows."""

    selected = _absolute(path)
    parent = selected.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise SetupError("setup record parent must be a regular directory")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = parent / f".{selected.name}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise SetupError(f"cannot open setup record lock: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SetupError("setup record lock must be a regular file")
        if os.name == "nt":  # pragma: no cover - exercised by native Windows CI
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError as exc:
        raise SetupError(f"cannot lock setup record: {exc}") from exc
    finally:
        os.close(descriptor)


def _write_setup_record_unlocked(
    path: Path,
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _record_payload(selection)
    record = {
        **payload,
        "record_id": _digest("workbench-user-setup", payload),
    }
    parent = path.parent
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise SetupError("setup record destination must be a regular file")
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise SetupError("setup record parent must be a regular directory")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise SetupError("setup record staging path already exists")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        raw = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SetupError(f"cannot publish setup record: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return record


def _write_setup_record(path: Path, selection: Mapping[str, Any]) -> dict[str, Any]:
    with setup_record_lock(path):
        return _write_setup_record_unlocked(path, selection)


def _write_setup_record_compare_and_swap(
    path: Path,
    selection: Mapping[str, Any],
    *,
    expected_record_id: str | None,
) -> dict[str, Any]:
    """Publish setup only while the exact reviewed prior state still holds."""

    with setup_record_lock(path):
        current = load_setup_record(path)
        current_record_id = None if current is None else current["record_id"]
        if current_record_id != expected_record_id:
            raise SetupError(
                "saved setup changed after the setup plan was reviewed"
            )
        return _write_setup_record_unlocked(path, selection)


def replace_setup_git_binding(
    path: Path | str,
    *,
    expected_record_id: str,
    git_executable: Path | str,
    expected_git_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically replace only the Git binding in one exact setup record.

    The repair owner calls this after its own reviewed plan and Git probe.  The
    expected record identity prevents repair from overwriting concurrent setup
    changes, and every non-Git V1 field is preserved verbatim.
    """

    selected_path = _absolute(path)
    with setup_record_lock(selected_path):
        current = load_setup_record(selected_path)
        if current is None:
            raise SetupError("the setup record disappeared before Git repair")
        if current["record_id"] != expected_record_id:
            raise SetupError("the setup record changed after the repair plan was reviewed")
        executable = _absolute(git_executable)
        probe = probe_git_executable(executable)
        if probe["state"] != "ready":
            raise SetupError("the repaired Git executable failed setup verification")
        if (
            expected_git_identity is not None
            and probe.get("executable_identity") != dict(expected_git_identity)
        ):
            raise SetupError("the repaired Git executable changed after repair review")
        selection = dict(current["selection"])
        selection["git_executable"] = str(executable)
        return _write_setup_record_unlocked(selected_path, selection)


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _is_redirecting_path_component(path: Path, info: os.stat_result) -> bool:
    """Return whether *path* can redirect writes outside reviewed custody."""

    if stat.S_ISLNK(info.st_mode):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is not None:
        try:
            if is_junction(path):
                return True
        except OSError:
            # The lstat result remains authoritative for ordinary paths. A
            # failing junction probe is covered by the reparse bit where the
            # host exposes it.
            pass
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(
        reparse_flag
        and getattr(info, "st_file_attributes", 0) & reparse_flag
    )


def _same_physical_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
        os.path.abspath(os.fspath(right))
    )


def _workspace(path: Path | str) -> Path:
    selected = _absolute(path)
    try:
        info = selected.lstat()
    except FileNotFoundError:
        ancestor = selected.parent
        while True:
            try:
                ancestor_info = ancestor.lstat()
            except FileNotFoundError:
                parent = ancestor.parent
                if parent == ancestor:
                    raise SetupError("workspace has no available parent directory")
                ancestor = parent
                continue
            except OSError as exc:
                raise SetupError(f"workspace parent is unavailable: {ancestor}") from exc
            if stat.S_ISLNK(ancestor_info.st_mode) or not stat.S_ISDIR(
                ancestor_info.st_mode
            ):
                raise SetupError(
                    "nearest existing workspace parent must be a regular non-symlink directory"
                )
            break
        try:
            return selected.resolve(strict=False)
        except OSError as exc:
            raise SetupError(f"workspace is unavailable: {selected}") from exc
    except OSError as exc:
        raise SetupError(f"workspace is unavailable: {selected}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SetupError("workspace must be a regular non-symlink directory")
    try:
        return selected.resolve(strict=True)
    except OSError as exc:
        raise SetupError(f"workspace is unavailable: {selected}") from exc


def _state_root(path: Path | str) -> Path:
    selected = _absolute(path)
    cursor = selected
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SetupError(f"state root component is unavailable: {cursor}") from exc
        else:
            if _is_redirecting_path_component(cursor, info):
                raise SetupError(
                    "state root components must not be symlinks, junctions, "
                    f"or other reparse points: {cursor}"
                )
            if not stat.S_ISDIR(info.st_mode):
                raise SetupError(
                    f"state root component must be a directory: {cursor}"
                )
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    try:
        canonical = selected.resolve(strict=False)
    except OSError as exc:
        raise SetupError(f"state root is unavailable: {selected}") from exc
    if not _same_physical_path(canonical, selected):
        # A redirect appeared during inspection or escaped the component walk.
        raise SetupError(
            "state root resolved through a symlink, junction, or reparse point"
        )
    return canonical


def _configuration_path(suite_root: Path, value: Path | str) -> Path:
    selected = Path(value).expanduser()
    return _absolute(selected if selected.is_absolute() else suite_root / selected)


def _require_external_record(path: Path, *, workspace: Path, suite_root: Path) -> None:
    for label, root in (("workspace", workspace), ("installed/source suite", suite_root)):
        try:
            path.relative_to(root)
        except ValueError:
            continue
        raise SetupError(
            f"setup record must be outside the selected {label}: {path}"
        )


def _new_selection(
    suite_root: Path,
    *,
    workspace: Path | str,
    state_root: Path | str,
    profile_config: Path | str | None,
    java_home: Path | str | None,
    git_executable: Path | str | None,
) -> dict[str, Any]:
    return {
        "workspace": str(_workspace(workspace)),
        "state_root": str(_state_root(state_root)),
        "profile_config": (
            str(_configuration_path(suite_root, profile_config))
            if profile_config is not None
            else None
        ),
        "profile_selection_digest": None,
        "java_home": str(_absolute(java_home)) if java_home is not None else None,
        "managed_java_home": None,
        "git_executable": (
            str(_absolute(git_executable)) if git_executable is not None else None
        ),
    }


def _selection_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record["selection"])


def _dependency(
    dependency_id: str,
    label: str,
    state: str,
    detail: str,
    *,
    required: bool = True,
    source: str | None = None,
    repair: str | None = None,
    managed_install: bool = False,
) -> dict[str, Any]:
    return {
        "id": dependency_id,
        "label": label,
        "required": required,
        "state": state,
        "source": source,
        "detail": detail,
        "repair": repair,
        "managed_install": managed_install,
    }


def _probe_git(
    selected: str | None,
    environment: Mapping[str, str],
    *,
    required: bool = False,
) -> dict[str, Any]:
    executable = selected
    if executable is None:
        host_check = inspect_host_requirements(environment=environment)
        discovered_git = host_check["requirements"]["git"]
        executable = (
            discovered_git["executable"]
            if discovered_git["state"] == "ready"
            else None
        )
    if executable is None:
        return _dependency(
            "git",
            "Git",
            "missing-manual",
            (
                "Git is required for acquisition and prepared PR/ref review flows. "
                "Exact-directory review remains available without it."
            ),
            required=required,
            repair="Run workbench repair for an OS-aware Git repair plan.",
        )
    path = _absolute(executable)
    if not path.is_file():
        return _dependency(
            "git",
            "Git",
            "incompatible",
            f"The selected Git executable is not a file: {path}",
            source=str(path),
            required=required,
            repair="Choose an executable Git path in Customize setup.",
        )
    probe = probe_git_executable(path)
    if probe["state"] == "incompatible":
        return _dependency(
            "git",
            "Git",
            "incompatible",
            f"The selected Git executable could not be probed ({probe['detail']}).",
            source=str(path),
            required=required,
            repair="Choose a working Git executable in Customize setup.",
        )
    if probe["state"] != "ready":
        return _dependency(
            "git",
            "Git",
            "incompatible",
            "The selected executable did not report a Git version.",
            source=str(path),
            required=required,
            repair="Choose a working Git executable in Customize setup.",
        )
    return _dependency(
        "git", "Git", "ready", probe["version"], source=str(path), required=required
    )


def _git_dependency_from_host_check(
    host_check: Mapping[str, Any],
    *,
    required: bool,
) -> dict[str, Any]:
    """Reuse the exact first preflight observation when it found no Git."""

    git = host_check["requirements"]["git"]
    if git["state"] == "ready":
        return _dependency(
            "git",
            "Git",
            "ready",
            git["version"],
            source=git["executable"],
            required=required,
        )
    repair = git.get("repair", {})
    return _dependency(
        "git",
        "Git",
        "incompatible" if git["state"] == "incompatible" else "missing-manual",
        git.get(
            "detail",
            (
                "Git is required for acquisition and prepared PR/ref review flows. "
                "Exact-directory review remains available without it."
            ),
        ),
        required=required,
        repair=repair.get("detail", "Run workbench repair for an OS-aware Git repair plan."),
    )


def _file_uri_path(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise SetupError("managed Java receipt contains a non-local home URI")
    path = url2pathname(parsed.path)
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return _absolute(path)


def _profile_and_java(
    suite_root: Path,
    selection: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    WorkbenchConfiguration | None,
    dict[str, Any] | None,
    str | None,
]:
    configured = selection.get("profile_config")
    if configured is None:
        return [
            _dependency(
                "runtime-profile",
                "Runtime profile",
                "not-needed",
                "No runtime profile was selected. Recipe and source review remain available.",
                required=False,
            ),
            _dependency(
                "java",
                "Java runtime",
                "not-needed",
                "Java is needed only for build and runtime flows.",
                required=False,
            ),
        ], None, None, None
    try:
        configuration = load_workbench_configuration(suite_root, configured)
    except WorkbenchConfigurationError as exc:
        return [
            _dependency(
                "runtime-profile",
                "Runtime profile",
                "incompatible",
                f"The selected Configuration V1 manifest is invalid: {exc}",
                source=str(configured),
                repair="Choose another Configuration V1 manifest.",
            ),
            _dependency(
                "java",
                "Java runtime",
                "incompatible",
                "Java cannot be selected until the runtime profile is valid.",
                repair="Repair or replace the selected runtime profile first.",
            ),
        ], None, None, None
    expected = selection.get("profile_selection_digest")
    if expected is not None and expected != configuration.selection_digest:
        return [
            _dependency(
                "runtime-profile",
                "Runtime profile",
                "incompatible",
                "The explicitly selected profile documents changed since setup.",
                source=str(configured),
                repair="Run Customize setup and explicitly review the changed profile.",
            ),
            _dependency(
                "java",
                "Java runtime",
                "incompatible",
                "Java policy was not read from a drifted profile selection.",
                repair="Review the profile change before repairing Java.",
            ),
        ], None, None, None
    try:
        policy = load_java_runtime_policy(suite_root, configuration=configuration)
        host = host_platform()
    except JavaRuntimeError as exc:
        return [
            _dependency(
                "runtime-profile",
                "Runtime profile",
                "incompatible",
                f"The selected profile has no supported Java policy: {exc}",
                source=str(configured),
                repair="Choose a supported Cleanroom runtime profile.",
            ),
            _dependency(
                "java",
                "Java runtime",
                "incompatible",
                "Java cannot be checked without a supported profile policy.",
                repair="Choose a supported Cleanroom runtime profile.",
            ),
        ], None, None, None

    profile = _dependency(
        "runtime-profile",
        "Runtime profile",
        "ready",
        (
            f"{configuration.pack_profile_id} / {configuration.platform_profile_id} "
            f"({configuration.pack_variant})"
        ),
        source=str(configured),
    )
    java_home = selection.get("java_home") or selection.get("managed_java_home")
    if selection.get("managed_java_home") is not None:
        try:
            managed = inspect_managed_java_runtime(
                policy,
                host,
                state_root=str(selection["state_root"]),
            )
        except JavaRuntimeError as exc:
            java = _dependency(
                "java",
                "Java runtime",
                "incompatible",
                f"The retained Workbench-managed Java runtime is invalid: {exc}",
                source=str(selection["state_root"]),
                repair="Review the retained Java receipt before using runtime flows.",
            )
            return [profile, java], configuration, policy, None
        if managed is None:
            java = _dependency(
                "java",
                "Java runtime",
                "incompatible",
                "The saved Workbench-managed Java runtime is missing.",
                source=str(selection["state_root"]),
                repair="Remove the stale managed Java selection and run setup again.",
            )
            return [profile, java], configuration, policy, None
        managed_home = str(
            _file_uri_path(managed["receipt"]["target"]["java_home_uri"])
        )
        if os.path.normcase(os.path.abspath(str(java_home))) != os.path.normcase(
            os.path.abspath(managed_home)
        ):
            java = _dependency(
                "java",
                "Java runtime",
                "incompatible",
                "The saved managed Java path differs from its verified receipt.",
                source=str(java_home),
                repair="Review the retained Java receipt before using runtime flows.",
            )
            return [profile, java], configuration, policy, None
        java = _dependency(
            "java",
            "Java runtime",
            "ready",
            "A verified Workbench-managed Java runtime will be reused.",
            source=managed_home,
        )
        return [profile, java], configuration, policy, managed_home
    candidates: Sequence[tuple[str, Path]] = ()
    if isinstance(java_home, str):
        name = "user-selected"
        executable = Path(java_home) / "bin" / (
            "java.exe" if host["os"] == "windows" else "java"
        )
        candidates = ((name, executable),)
    discovery = discover_java_runtime(policy, host, candidates=candidates)
    selected_managed_home: str | None = None
    if discovery["selected"] is not None:
        selected = discovery["selected"]
        java = _dependency(
            "java",
            "Java runtime",
            "ready",
            (
                f"Compatible {selected['probe']['runtime_version']} "
                f"from {selected['origin']}."
            ),
            source=selected["probe"]["java_home"],
        )
    elif java_home is not None:
        reason = (
            discovery["candidates"][0].get("reason", "candidate is not compatible")
            if discovery["candidates"]
            else "candidate was not found"
        )
        java = _dependency(
            "java",
            "Java runtime",
            "incompatible",
            f"The selected Java home is not usable: {reason}",
            source=str(java_home),
            repair="Choose a compatible Java home or remove it to use managed Java.",
        )
    else:
        try:
            managed = inspect_managed_java_runtime(
                policy,
                host,
                state_root=str(selection["state_root"]),
            )
        except JavaRuntimeError as exc:
            java = _dependency(
                "java",
                "Java runtime",
                "incompatible",
                f"The retained Workbench-managed Java runtime is invalid: {exc}",
                source=str(selection["state_root"]),
                repair=(
                    "Review the retained Java receipt and run setup repair before "
                    "using runtime flows."
                ),
            )
        else:
            if managed is not None:
                selected_managed_home = str(
                    _file_uri_path(
                        managed["receipt"]["target"]["java_home_uri"]
                    )
                )
                java = _dependency(
                    "java",
                    "Java runtime",
                    "ready",
                    "A verified Workbench-managed Java runtime will be reused.",
                    source=selected_managed_home,
                )
            else:
                java = _dependency(
                    "java",
                    "Java runtime",
                    "missing-installable",
                    (
                        f"{policy['runtime_identity']} is required by the explicit "
                        "profile and can be installed into Workbench state."
                    ),
                    source=str(selection["state_root"]),
                    repair="Review and apply this setup plan to install managed Java.",
                    managed_install=True,
                )
    return [profile, java], configuration, policy, selected_managed_home


def _profile_workspace_dependency(
    configuration: WorkbenchConfiguration | None,
    workspace: Path,
) -> dict[str, Any]:
    if configuration is None:
        return _dependency(
            "profile-workspace",
            "Profile workspace",
            "not-needed",
            "No runtime profile was selected, so a pack-specific workspace is not required.",
            required=False,
        )
    declared = configuration.pack_document.values.get("workspace")
    required = declared.get("required_paths") if isinstance(declared, Mapping) else None
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes))
        or not required
    ):
        return _dependency(
            "profile-workspace",
            "Profile workspace",
            "incompatible",
            "The selected pack profile does not declare a usable workspace probe.",
            source=str(configuration.pack_document.source.path),
            repair="Repair or replace the selected pack profile.",
        )
    missing: list[str] = []
    for row in required:
        if not isinstance(row, Mapping):
            missing.append("<invalid profile path>")
            continue
        relative = row.get("path")
        kind = row.get("kind")
        if not isinstance(relative, str) or kind not in {"file", "directory"}:
            missing.append("<invalid profile path>")
            continue
        selected = workspace / relative
        present = selected.is_file() if kind == "file" else selected.is_dir()
        if not present or selected.is_symlink():
            missing.append(f"{relative} ({kind})")
    if missing:
        project = configuration.pack_profile_id.rsplit(":", 1)[-1]
        return _dependency(
            "profile-workspace",
            "Profile workspace",
            "incompatible",
            (
                "The selected workspace does not satisfy the explicit pack profile; "
                "missing: " + ", ".join(missing)
            ),
            source=str(workspace),
            repair=(
                f"Select a matching checkout or run workbench project acquire "
                f"{project} --destination PATH."
            ),
        )
    return _dependency(
        "profile-workspace",
        "Profile workspace",
        "ready",
        f"The workspace satisfies {configuration.pack_profile_id}.",
        source=str(workspace),
    )


def _manual_artifact_dependency(
    configuration: WorkbenchConfiguration | None,
    workspace: Path,
    *,
    workspace_ready: bool,
) -> dict[str, Any]:
    if configuration is None:
        return _dependency(
            "manual-pack-files",
            "Publisher-hosted pack files",
            "not-needed",
            "No runtime profile was selected.",
            required=False,
        )
    if not workspace_ready:
        return _dependency(
            "manual-pack-files",
            "Publisher-hosted pack files",
            "not-needed",
            "Select a matching pack workspace before checking its manual files.",
            required=False,
        )
    try:
        profile_path = manual_artifact_profile_for_configuration(configuration)
        if profile_path is None:
            return _dependency(
                "manual-pack-files",
                "Publisher-hosted pack files",
                "not-needed",
                "The selected pack profile declares no manual artifact preflight.",
                required=False,
            )
        profile = load_manual_artifact_profile(profile_path)
        report = inspect_manual_artifacts(workspace, profile)
    except ManualArtifactError as exc:
        return _dependency(
            "manual-pack-files",
            "Publisher-hosted pack files",
            "incompatible",
            f"Manual artifact authority cannot be checked: {exc}",
            source=str(configuration.pack_document.source.path),
            repair="Repair the selected pack profile before runtime provisioning.",
        )
    incompatible = [
        row["display_name"]
        for row in report["artifacts"]
        if row["state"] == "incompatible"
    ]
    if incompatible:
        return _dependency(
            "manual-pack-files",
            "Publisher-hosted pack files",
            "incompatible",
            "Packwiz metadata drifted for: " + ", ".join(incompatible),
            source=str(profile_path),
            repair="Review the pack/profile update before provisioning a runtime.",
        )
    missing = [row["display_name"] for row in report["artifacts"]]
    return _dependency(
        "manual-pack-files",
        "Publisher-hosted pack files",
        "missing-manual",
        (
            f"{len(missing)} file(s) must be downloaded by the user before full "
            "runtime materialization: " + ", ".join(missing)
        ),
        required=False,
        source=str(profile_path),
        repair=(
            f"Run workbench runtime preflight {workspace} --profile "
            f"{profile['project_id']} for exact publisher links and seed paths."
        ),
    )


def inspect_setup(
    suite_root: Path | str,
    selection: Mapping[str, Any],
    *,
    record_path: Path | str,
    record_present: bool,
    environment: Mapping[str, str] | None = None,
    require_git: bool = False,
    host_requirements_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the read-only setup inventory used by CLI and IDE clients."""

    root = _absolute(suite_root)
    selection = dict(selection)
    selection["state_root"] = str(_state_root(str(selection["state_root"])))
    values = dict(os.environ if environment is None else environment)
    workspace = _workspace(str(selection["workspace"]))
    workspace_present = workspace.is_dir()
    dependencies: list[dict[str, Any]] = [
        _dependency(
            "workbench-core",
            "Workbench CLI",
            "ready",
            f"Python {sys.version_info.major}.{sys.version_info.minor} core is callable.",
            source=sys.executable,
        ),
        _dependency(
            "workspace",
            "Workspace",
            "ready" if workspace_present else "missing-installable",
            (
                "The selected workspace is a readable directory."
                if workspace_present
                else "The selected workspace will be created only after this plan is accepted."
            ),
            source=str(workspace),
            repair=(
                None
                if workspace_present
                else "Review and apply this setup plan to create the workspace."
            ),
            managed_install=not workspace_present,
        ),
    ]
    selected_git = selection.get("git_executable")
    dependencies.append(
        _git_dependency_from_host_check(
            host_requirements_check,
            required=require_git,
        )
        if selected_git is None and host_requirements_check is not None
        else _probe_git(
            selected_git,
            values,
            required=require_git,
        )
    )

    environment_status = inspect_environment_status(root, environ=values)
    pixi = environment_status["pixi"]
    if environment_status["execution"]["source_checkout"]:
        if pixi["state"] != "available":
            pixi_row = _dependency(
                "pixi",
                "Pixi",
                "missing-manual",
                "This source checkout needs Pixi for reproducible environment repair.",
                repair="Install the manifest-required Pixi version, then rerun setup.",
            )
        elif pixi["matches_required_constraint"] is False:
            pixi_row = _dependency(
                "pixi",
                "Pixi",
                "incompatible",
                (
                    f"Pixi {pixi['version']} does not match "
                    f"{pixi['required_constraint']}."
                ),
                source=pixi["executable"],
                repair="Install the exact Pixi version declared by the manifest.",
            )
        else:
            pixi_row = _dependency(
                "pixi",
                "Pixi",
                "ready",
                f"Pixi {pixi['version']} is available for this source checkout.",
                source=pixi["executable"],
            )
    else:
        pixi_row = _dependency(
            "pixi",
            "Pixi",
            "not-needed",
            "The installed Workbench runtime does not require Pixi at runtime.",
            required=False,
            source=pixi.get("executable"),
        )
    dependencies.append(pixi_row)
    profile_rows, configuration, policy, managed_java_home = _profile_and_java(
        root, selection
    )
    dependencies.extend(profile_rows)
    profile_workspace = _profile_workspace_dependency(configuration, workspace)
    dependencies.append(profile_workspace)
    dependencies.append(
        _manual_artifact_dependency(
            configuration,
            workspace,
            workspace_ready=profile_workspace["state"] == "ready",
        )
    )

    try:
        if not workspace_present:
            workspace_observation = {
                "status": "attention",
                "kind": "not-created",
                "root": str(workspace),
                "platform": {"state": "unresolved", "kind": "unknown"},
                "findings": ["Workspace will be inspected after it is created."],
            }
        else:
            from workbench_api.workspace import inspect_workspace

            doctor = inspect_workspace(workspace)
            workspace_observation = {
                "status": doctor["summary"]["status"],
                "kind": doctor["target"]["workspace"]["kind"],
                "root": doctor["target"]["workspace"]["root"],
                "platform": doctor["target"]["platform"],
                "findings": [row["id"] for row in doctor["findings"]],
            }
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        workspace_observation = {
            "status": "attention",
            "kind": "unresolved",
            "root": str(workspace),
            "platform": {"state": "unresolved", "kind": "unknown"},
            "findings": [f"Doctor unavailable: {type(exc).__name__}"],
        }

    managed_java_execution: dict[str, Any] | None = None
    java_dependency = next(
        (row for row in dependencies if row["id"] == "java"),
        None,
    )
    if (
        policy is not None
        and java_dependency is not None
        and java_dependency["state"] == "missing-installable"
    ):
        try:
            managed_java_execution = plan_managed_java_execution(
                policy,
                host_platform(),
                state_root=selection["state_root"],
            )
        except JavaRuntimeError as exc:
            java_dependency["state"] = "incompatible"
            java_dependency["detail"] = (
                "Workbench cannot plan a safe Java execution path: " + str(exc)
            )
            java_dependency["repair"] = (
                "Choose a state location with an ASCII Windows short path."
            )
            java_dependency["managed_install"] = False

    required_states = [row["state"] for row in dependencies if row["required"]]
    blockers = [
        row["id"]
        for row in dependencies
        if row["required"] and row["state"] in {"missing-manual", "incompatible"}
    ]
    installable = [
        row["id"]
        for row in dependencies
        if row["required"] and row["state"] == "missing-installable"
    ]
    readiness = (
        "attention"
        if blockers
        else "installable"
        if installable
        else "ready"
        if all(state == "ready" for state in required_states)
        else "attention"
    )
    normalized = dict(selection)
    if configuration is not None and normalized.get("profile_selection_digest") is None:
        normalized["profile_selection_digest"] = configuration.selection_digest
    if managed_java_home is not None and normalized.get("java_home") is None:
        normalized["managed_java_home"] = managed_java_home
    return {
        "format": CHECK_FORMAT if workspace_present else CHECK_FORMAT_V2,
        "schema_version": SCHEMA_VERSION if workspace_present else 2,
        "operation_class": "read-only",
        "state": readiness,
        "configured": record_present,
        "record_path": str(_absolute(record_path)),
        "selection": normalized,
        "dependencies": dependencies,
        "blockers": blockers,
        "managed_installs_available": installable,
        "workspace_observation": workspace_observation,
        "environment": {
            "execution_mode": environment_status["execution"]["mode"],
            "state": environment_status["state"],
        },
        "profile_policy": (
            {
                "profile_id": policy["profile_id"],
                "runtime_identity": policy["runtime_identity"],
                "release_name": policy["release_name"],
            }
            if policy is not None
            else None
        ),
        "managed_java_execution": managed_java_execution,
    }


def build_setup_plan(
    check: Mapping[str, Any],
    *,
    current_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a stable consent unit from one exact read-only inventory."""

    actions: list[dict[str, Any]] = []
    if "workspace" in check["managed_installs_available"]:
        actions.append(
            {
                "id": "create-workspace",
                "operation": "directory-create",
                "component": "workspace",
                "source": None,
                "destination": check["selection"]["workspace"],
                "effect": (
                    "Create the selected workspace and any missing parent "
                    "directories. No project or pack is selected automatically."
                ),
            }
        )
    if "java" in check["managed_installs_available"]:
        policy = check["profile_policy"]
        execution = check.get("managed_java_execution")
        cds_transform = (
            isinstance(execution, Mapping)
            and execution.get("portability_transform_id")
            == WINDOWS_UNICODE_CDS_TRANSFORM_ID
        )
        actions.append(
            {
                "id": "install-managed-java",
                "operation": "managed-install",
                "component": "java",
                "source": policy["runtime_identity"],
                "destination": check["selection"]["state_root"],
                "execution": execution,
                "effect": (
                    "Download, verify, and unpack the profile-selected Temurin JDK "
                    "inside Workbench state. "
                    + (
                        "For this Unicode Windows path, remove only the JDK's "
                        "optional startup-cache archives in staging because "
                        "Windows cannot use them reliably; record every removal "
                        "in the runtime receipt. "
                        if cds_transform
                        else ""
                    )
                    + "System Java settings are unchanged."
                ),
            }
        )
    record_matches = False
    expected_record_id: str | None = None
    if current_record is not None:
        validated_record = _validate_record(dict(current_record))
        expected_record_id = validated_record["record_id"]
        record_matches = validated_record["selection"] == check["selection"]
    if record_matches:
        actions.append(
            {
                "id": "verify-user-setup",
                "operation": "read-only-verification",
                "component": "workbench-setup",
                "source": check["record_path"],
                "destination": check["record_path"],
                "effect": "Reuse the matching saved setup record without rewriting it.",
            }
        )
    else:
        actions.append(
            {
                "id": "save-user-setup",
                "operation": "atomic-record-write",
                "component": "workbench-setup",
                "source": None,
                "destination": check["record_path"],
                "effect": "Save the reviewed paths and profile reference for this user.",
            }
        )
    v2 = check.get("format") == CHECK_FORMAT_V2
    saves_record = not record_matches
    identity = {
        "selection": check["selection"],
        "dependencies": check["dependencies"],
        "actions": actions,
        "blockers": check["blockers"],
    }
    if saves_record:
        identity["expected_record_id"] = expected_record_id
    return {
        "format": (
            PLAN_FORMAT_V3
            if saves_record
            else PLAN_FORMAT_V2
            if v2
            else PLAN_FORMAT
        ),
        "schema_version": 3 if saves_record else 2 if v2 else SCHEMA_VERSION,
        "operation_class": "review-before-mutation",
        "plan_id": _digest(
            (
                "workbench-setup-plan-v3"
                if saves_record
                else "workbench-setup-plan-v2"
                if v2
                else "workbench-setup-plan"
            ),
            identity,
        ),
        "state": "blocked" if check["blockers"] else "ready",
        "selection": check["selection"],
        "dependencies": check["dependencies"],
        "actions": actions,
        "blockers": check["blockers"],
        **(
            {"expected_record_id": expected_record_id}
            if saves_record
            else {}
        ),
        "consent": {
            "required": True,
            "non_interactive": "Pass this exact plan_id with --apply.",
        },
    }


_STATUS_PRESENTATION: dict[str, tuple[str, str, Tone]] = {
    "ready": ("✓", "ready", "good"),
    "installable": ("●", "installable", "attention"),
    "attention": ("!", "attention", "attention"),
    "blocked": ("✗", "blocked", "blocked"),
    "missing-installable": ("●", "installable", "attention"),
    "missing-manual": ("!", "missing", "blocked"),
    "incompatible": ("✗", "incompatible", "blocked"),
    "not-needed": ("–", "optional", "attention"),
    "developer-required": ("!", "required for development", "attention"),
    "review-only": ("!", "review-only · developer setup incomplete", "attention"),
}


def _status_label(state: str, *, presentation: HumanPresentation) -> str:
    glyph, label, tone = _STATUS_PRESENTATION.get(
        state,
        ("?", state.replace("-", " "), "attention"),
    )
    visible = presentation.label(label, tone)
    return f"{glyph} {visible}" if presentation.color else visible


def _render_check(
    check: Mapping[str, Any],
    *,
    presentation: HumanPresentation | None = None,
    include_record_path: bool = True,
) -> str:
    view = human_presentation() if presentation is None else presentation
    display_state = (
        "review-only"
        if check["selection"].get("profile_config") is None
        else str(check["state"])
    )
    lines = [
        "Workbench setup · Check",
        f"Status: {_status_label(display_state, presentation=view)}"
        + (" · saved" if check["configured"] else " · not saved"),
        f"Workspace: {check['selection']['workspace']}",
        "",
        "Requirements",
    ]
    not_used: list[str] = []
    for row in check["dependencies"]:
        label = "Project setup" if row["id"] == "runtime-profile" else row["label"]
        if row["state"] == "not-needed" and row["id"] not in {
            "runtime-profile",
            "java",
        }:
            not_used.append(label)
            continue
        state = (
            "developer-required"
            if row["id"] in {"runtime-profile", "java"} and not row["required"]
            else "attention"
            if not row["required"]
            and row["state"] in {"missing-manual", "incompatible"}
            else row["state"]
        )
        requirement = (
            "required"
            if row["required"]
            else "developer dependency"
            if row["id"] in {"runtime-profile", "java"}
            else "optional"
        )
        lines.append(
            f"- {label}: {_status_label(str(state), presentation=view)} · {requirement}"
        )
        if row["state"] != "ready" or row["id"] in {"workspace", "java"}:
            detail = (
                "Choose a project setup file in Customize so Workbench can validate "
                "or manage the exact Java required for build and run flows."
                if row["id"] == "java" and not row["required"]
                else (
                    "Choose a project setup file in Customize to complete build and "
                    "run setup; review-only commands remain available."
                )
                if row["id"] == "runtime-profile" and not row["required"]
                else row["detail"]
            )
            lines.append(f"  {detail}")
        if row["repair"]:
            lines.append(f"  Next: {row['repair']}")
    if not_used:
        lines.append(
            "- Not used for this setup: "
            + " · ".join(not_used)
            + " "
            + _status_label("not-needed", presentation=view)
        )
    if include_record_path:
        lines.extend(("", f"Setup record: {check['record_path']}"))
    return "\n".join(lines) + "\n"


def _render_plan(
    plan: Mapping[str, Any],
    *,
    presentation: HumanPresentation | None = None,
    include_plan_id: bool = True,
) -> str:
    view = human_presentation() if presentation is None else presentation
    java = next(row for row in plan["dependencies"] if row["id"] == "java")
    java_state = java["state"] if java["required"] else "developer-required"
    java_detail = (
        "Choose a project setup file in Customize."
        if not java["required"]
        else java["detail"]
    )
    display_state = (
        "review-only"
        if plan["selection"].get("profile_config") is None
        else str(plan["state"])
    )
    lines = [
        "Workbench setup · Plan",
        f"Status: {_status_label(display_state, presentation=view)}",
        f"Workspace: {plan['selection']['workspace']}",
        (
            "Project setup: " + str(plan["selection"]["profile_config"])
            if plan["selection"]["profile_config"] is not None
            else (
                "Project setup: none (review-only; Customize selects the user's "
                "developer environment)"
            )
        ),
        f"State: {plan['selection']['state_root']}",
        f"Java: {_status_label(str(java_state), presentation=view)} · {java_detail}",
        "",
        f"Changes ({len(plan['actions'])})",
    ]
    if include_plan_id:
        lines.insert(2, f"Plan: {plan['plan_id']}")
    for action in plan["actions"]:
        lines.append(f"- {action['effect']}")
        if action["destination"] is not None:
            lines.append(f"  Destination: {action['destination']}")
    if plan["blockers"]:
        lines.extend(("", "Cannot apply until these are repaired: " + ", ".join(plan["blockers"])))
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench setup",
        description=(
            "Check or configure the local Workbench environment. Plain setup opens "
            "a Full developer / Review-only / Repair wizard and asks before changing "
            "anything. "
            "Use `workbench launcher setup` for a credential-free Prism/MultiMC "
            "binding after core setup."
        ),
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--list-java", action="store_true", help="inspect installed Java versions and optional profile compatibility without selecting or downloading")
    operation.add_argument("--check", action="store_true", help="read setup and dependencies without changing them")
    operation.add_argument("--plan", action="store_true", help="render the exact setup plan without applying it")
    operation.add_argument("--apply", metavar="PLAN_ID", help="apply one exact plan ID; intended for reviewed non-interactive use")
    parser.add_argument("--repair", action="store_true", help="reuse the saved selection in the interactive wizard")
    parser.add_argument("--workspace", type=Path, help="project or repository to use (default: current directory)")
    parser.add_argument(
        "--profile-config",
        type=Path,
        help=(
            "explicit Workbench Configuration V1 manifest for runtime tools; no pack "
            "profile is selected when omitted"
        ),
    )
    parser.add_argument("--state-root", type=Path, help="Workbench-owned runtime and download state")
    parser.add_argument("--java-home", type=Path, help="existing Java home to validate against the selected profile")
    parser.add_argument("--git-executable", type=Path, help="Git executable to validate instead of PATH discovery")
    parser.add_argument(
        "--require-git",
        action="store_true",
        help=(
            "also require Git when checking an existing setup; first setup already requires it"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit structured output (requires --list-java, --check, --plan, or --apply)")
    return parser


def _prompt(stream: TextIO, output: TextIO, text: str) -> str:
    output.write(text)
    output.flush()
    value = stream.readline()
    if value == "":
        raise SetupCancelled
    return value.rstrip("\r\n")


def _customize_selection(
    suite_root: Path,
    base: Mapping[str, Any],
    *,
    discovered_git: str | None,
    input_stream: TextIO,
    output: TextIO,
    require_profile: bool,
) -> dict[str, Any]:
    """Collect one revision while retaining the immediately preceding choices."""

    output.write("\nCustomize developer setup\n")
    workspace_default = str(base["workspace"])
    workspace = (
        _prompt(input_stream, output, f"Workspace [{workspace_default}]: ").strip()
        or workspace_default
    )
    profile_default = str(base.get("profile_config") or "")
    output.write(
        "The project setup file tells Workbench which Cleanroom and Java versions "
        "this project needs.\n"
    )
    while True:
        profile_answer = _prompt(
            input_stream,
            output,
            (
                "Project setup file (workbench.toml)"
                + (f" [{profile_default}]" if profile_default else " [required]")
                + " (enter 'review-only' to omit): "
            ),
        ).strip()
        if profile_answer.casefold() in {"none", "review-only"}:
            profile: str | None = None
            break
        profile = profile_answer or profile_default or None
        if profile is not None or not require_profile:
            break
        output.write(
            "A full developer setup requires a project setup file; choose one or "
            "enter 'review-only'.\n"
        )
    state_default = str(base["state_root"])
    state = (
        _prompt(
            input_stream,
            output,
            f"Workbench state root [{state_default}]: ",
        ).strip()
        or state_default
    )
    git_default = str(base.get("git_executable") or discovered_git or "")
    git = (
        _prompt(
            input_stream,
            output,
            "Git executable (blank uses the detected installation)"
            + (f" [{git_default}]" if git_default else "")
            + ": ",
        ).strip()
        or git_default
        or None
    )
    java: str | None = None
    if profile is not None:
        java_default = str(base.get("java_home") or "")
        from .java_inventory import inspect_java_inventory, render_java_inventory
        try:
            policy = load_java_runtime_policy(suite_root, config_path=profile)
            inventory = inspect_java_inventory(policy=policy, homes=[Path(java_default)] if java_default else [])
            output.write(render_java_inventory(inventory))
        except (JavaRuntimeError, WorkbenchConfigurationError) as error:
            inventory = {"candidates": []}
            output.write(f"Cannot assess Java choices until the profile is valid: {error}\n")
        java = (
            _prompt(
                input_stream,
                output,
                "Java installation number or existing Java home (blank keeps the default; 'managed' uses profile-managed Java)"
                + (f" [{java_default}]" if java_default else "")
                + ": ",
            ).strip()
            or java_default
            or None
        )
        if java == "managed":
            java = None
        elif java is not None and java.isdecimal():
            index = int(java) - 1
            choices = inventory["candidates"]
            if index < 0 or index >= len(choices) or choices[index]["state"] != "available":
                raise SetupError("Choose an available Java installation number or an existing Java home")
            java = choices[index]["probe"]["java_home"]
    return _new_selection(
        suite_root,
        workspace=workspace,
        state_root=state,
        profile_config=profile,
        java_home=java,
        git_executable=git,
    )


def _interactive_selection(
    suite_root: Path,
    args: argparse.Namespace,
    prior: Mapping[str, Any] | None,
    *,
    environment: Mapping[str, str] | None,
    host_check: Mapping[str, Any] | None,
    input_stream: TextIO,
    output: TextIO,
    presentation: HumanPresentation,
) -> dict[str, Any]:
    discovered_git = (
        host_check["requirements"]["git"]["executable"]
        if host_check is not None
        and host_check["requirements"]["git"]["state"] == "ready"
        else None
    )
    if host_check is not None:
        host = host_check["host"]
        git = host_check["requirements"]["git"]
        repair = git.get("repair", {})
        if git["state"] == "ready":
            git_line = (
                f"Git: {_status_label('ready', presentation=presentation)} · "
                f"{git['version']} at {git['executable']} "
                f"({git['discovery']})\n\n"
            )
        else:
            repair_detail = (
                "workbench repair has a reviewed, consent-gated OS package-manager plan."
                if repair.get("state") == "available"
                else repair.get("detail", "manual installation is required")
            )
            git_line = (
                f"Git: {_status_label('missing-manual', presentation=presentation)} · "
                f"{repair_detail}\n\n"
            )
        output.write(
            "Workbench setup · System\n"
            f"Host: {host['system']} {host['machine']}\n"
            + git_line
        )
    output.write(
        "Workbench setup · Choose\n"
        "Full developer setup is recommended and requires an explicit project "
        "profile so Workbench can select the exact Java runtime. Review-only "
        "setup does not complete the developer environment.\n\n"
    )
    if args.repair:
        choice = "repair"
    elif any(
        value is not None
        for value in (
            args.workspace,
            args.profile_config,
            args.state_root,
            args.java_home,
            args.git_executable,
        )
    ):
        choice = "customize"
    else:
        raw = _prompt(
            input_stream,
            output,
            (
                "Choose Full developer [1] (recommended), Review-only [2], "
                "or Repair [3] (default 1): "
            ),
        ).strip().casefold()
        choices = {
            "": "customize",
            "1": "customize",
            "full": "customize",
            "developer": "customize",
            "customize": "customize",
            "2": "quick",
            "quick": "quick",
            "review": "quick",
            "review-only": "quick",
            "3": "repair",
            "repair": "repair",
        }
        choice = choices.get(raw, "")
        if not choice:
            raise SetupError("choose Full developer, Review-only, or Repair")
    if choice == "repair":
        if prior is None:
            raise SetupError(
                "there is no saved setup to repair; choose Full developer or Review-only"
            )
        selection = _selection_from_record(prior)
        if args.git_executable is not None:
            selection["git_executable"] = str(_absolute(args.git_executable))
        elif discovered_git is not None:
            selection["git_executable"] = discovered_git
        return selection
    default_state = default_runtime_state_root(
        suite_root,
        environment=environment,
    )
    if choice == "quick":
        return _new_selection(
            suite_root,
            workspace=args.workspace or Path.cwd(),
            state_root=args.state_root or default_state,
            profile_config=None,
            java_home=None,
            git_executable=args.git_executable or discovered_git,
        )
    base = _selection_for_noninteractive(
        suite_root,
        args,
        prior,
        environment=environment,
        host_check=host_check,
    )
    return _customize_selection(
        suite_root,
        base,
        discovered_git=discovered_git,
        input_stream=input_stream,
        output=output,
        require_profile=True,
    )


def _selection_for_noninteractive(
    suite_root: Path,
    args: argparse.Namespace,
    prior: Mapping[str, Any] | None,
    *,
    environment: Mapping[str, str] | None,
    host_check: Mapping[str, Any] | None,
) -> dict[str, Any]:
    discovered_git = (
        host_check["requirements"]["git"]["executable"]
        if host_check is not None
        and host_check["requirements"]["git"]["state"] == "ready"
        else None
    )
    custom = any(
        value is not None
        for value in (
            args.workspace,
            args.profile_config,
            args.state_root,
            args.java_home,
            args.git_executable,
        )
    )
    if prior is not None:
        selection = _selection_from_record(prior)
        if not custom and not args.repair:
            return selection
    else:
        selection = _new_selection(
            suite_root,
            workspace=Path.cwd(),
            state_root=default_runtime_state_root(
                suite_root,
                environment=environment,
            ),
            profile_config=None,
            java_home=None,
            git_executable=discovered_git,
        )
    # Explicit arguments are intentional replacements, except that omitting a
    # profile never clears a saved profile during repair/check.
    if args.workspace is not None:
        selection["workspace"] = str(_workspace(args.workspace))
    if args.state_root is not None:
        replacement_state = _state_root(args.state_root)
        retained_state = _absolute(selection["state_root"])
        if os.path.normcase(str(replacement_state)) != os.path.normcase(
            str(retained_state)
        ):
            # A managed Java home is derived from its state root and is only
            # authoritative together with the receipt below that root.  An
            # explicit root replacement must inspect/reuse or provision the
            # compatible runtime in the replacement; it cannot retain the old
            # root's derived execution path.
            selection["managed_java_home"] = None
        selection["state_root"] = str(replacement_state)
    if args.profile_config is not None:
        selected_profile = str(_configuration_path(suite_root, args.profile_config))
        if selection.get("profile_config") != selected_profile:
            selection["profile_selection_digest"] = None
            selection["managed_java_home"] = None
        selection["profile_config"] = selected_profile
    if args.java_home is not None:
        selection["java_home"] = str(_absolute(args.java_home))
        selection["managed_java_home"] = None
    if args.git_executable is not None:
        selection["git_executable"] = str(_absolute(args.git_executable))
    elif args.repair and discovered_git is not None:
        selection["git_executable"] = discovered_git
    return selection


def _create_reviewed_workspace(
    plan: Mapping[str, Any],
    selection: Mapping[str, Any],
    created: list[tuple[Path, int, int]],
) -> None:
    action = next(
        (row for row in plan["actions"] if row["id"] == "create-workspace"),
        None,
    )
    if action is None:
        return
    workspace = _absolute(selection["workspace"])
    if action["destination"] != str(workspace):
        raise SetupError("workspace creation action differs from the reviewed selection")
    try:
        workspace.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SetupError(f"workspace destination is unavailable: {workspace}") from exc
    else:
        raise SetupError("workspace destination changed after review")
    missing: list[Path] = []
    cursor = workspace
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise SetupError("workspace has no available parent directory")
            cursor = parent
            continue
        except OSError as exc:
            raise SetupError(f"workspace destination is unavailable: {cursor}") from exc
        if cursor == workspace:
            raise SetupError("workspace destination changed after review")
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SetupError("workspace destination changed after review")
        break
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError as exc:
            raise SetupError("workspace destination changed after review") from exc
        else:
            info = directory.lstat()
            created.append((directory, info.st_dev, info.st_ino))
    for directory, expected_device, expected_inode in created:
        info = directory.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_dev != expected_device
            or info.st_ino != expected_inode
        ):
            raise SetupError("workspace destination changed after review")
    if _workspace(workspace) != workspace:
        raise SetupError("workspace destination changed after review")


def _remove_empty_created_directories(created: Sequence[tuple[Path, int, int]]) -> None:
    for directory, expected_device, expected_inode in reversed(created):
        try:
            info = directory.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_dev != expected_device
                or info.st_ino != expected_inode
            ):
                continue
            directory.rmdir()
        except (FileNotFoundError, OSError):
            # A non-empty or replaced directory is no longer disposable setup
            # residue and must be left alone.
            continue


def _finish_setup_apply(
    suite_root: Path,
    plan: Mapping[str, Any],
    selection: dict[str, Any],
    *,
    record_path: Path,
    environment: Mapping[str, str] | None = None,
    created_workspace: bool = False,
) -> dict[str, Any]:
    reviewed_state_root = _absolute(selection["state_root"])
    observed_state_root = _state_root(reviewed_state_root)
    if not _same_physical_path(reviewed_state_root, observed_state_root):
        raise SetupError("state root destination changed after review")
    selection["state_root"] = str(observed_state_root)
    installed: list[dict[str, Any]] = []
    if any(action["id"] == "install-managed-java" for action in plan["actions"]):
        configured = selection.get("profile_config")
        if configured is None:
            raise SetupError("managed Java action lacks an explicit runtime profile")
        try:
            configuration = load_workbench_configuration(suite_root, configured)
            result = ensure_java_runtime(
                suite_root,
                configuration=configuration,
                state_root=selection["state_root"],
                candidates=(),
            )
        except (WorkbenchConfigurationError, JavaRuntimeError) as exc:
            raise SetupError(f"managed Java installation failed: {exc}") from exc
        if result["source"] == "managed":
            reviewed_action = next(
                row
                for row in plan["actions"]
                if row["id"] == "install-managed-java"
            )
            execution = result["receipt"].get("execution")
            portability = result["receipt"].get("portability")
            if not isinstance(execution, dict):
                raise SetupError("managed Java receipt lacks its reviewed execution path")
            if not isinstance(portability, dict) or not isinstance(
                portability.get("cds"), dict
            ):
                raise SetupError("managed Java receipt lacks its portability record")
            observed_execution = {
                "kind": execution.get("kind"),
                "java_home": str(_file_uri_path(execution.get("java_home_uri"))),
                "java": str(_file_uri_path(execution.get("java_uri"))),
            }
            reviewed_execution = reviewed_action.get("execution")
            observed_plan_execution = {
                "kind": observed_execution["kind"],
                "portability_transform_id": portability["cds"].get(
                    "transform_id"
                ),
            }
            if isinstance(reviewed_execution, dict) and set(reviewed_execution) == {
                "kind",
                "portability_transform_id",
            }:
                execution_matches = observed_plan_execution == reviewed_execution
            elif isinstance(reviewed_execution, dict) and set(reviewed_execution) == {
                "kind"
            }:
                execution_matches = (
                    observed_execution.get("kind") == reviewed_execution.get("kind")
                )
            else:
                execution_matches = observed_execution == reviewed_execution
            if not execution_matches:
                raise SetupError(
                    "managed Java execution path differs from the reviewed setup plan"
                )
            java_home = _file_uri_path(result["receipt"]["target"]["java_home_uri"])
            runtime_id = result["receipt"]["runtime_id"]
        else:
            java_home = _absolute(result["runtime"]["probe"]["java_home"])
            runtime_id = None
        selection["managed_java_home"] = str(java_home)
        installed.append(
            {
                "component": "java",
                "outcome": result["outcome"],
                "source": result["source"],
                "runtime_id": runtime_id,
                "java_home": str(java_home),
            }
        )
    git_requirement = next(
        (
            bool(row["required"])
            for row in plan["dependencies"]
            if row.get("id") == "git"
        ),
        False,
    )
    verification = inspect_setup(
        suite_root,
        selection,
        record_path=record_path,
        record_present=False,
        environment=environment,
        require_git=git_requirement,
    )
    if verification["state"] != "ready":
        unresolved = verification["blockers"] + verification["managed_installs_available"]
        raise SetupError(
            "setup verification is not ready after apply: "
            + (", ".join(unresolved) if unresolved else verification["state"])
        )
    if any(action["id"] == "save-user-setup" for action in plan["actions"]):
        if plan.get("format") != PLAN_FORMAT_V3 or "expected_record_id" not in plan:
            raise SetupError("setup record write requires a compare-and-swap plan")
        expected_record_id = plan["expected_record_id"]
        if expected_record_id is not None and type(expected_record_id) is not str:
            raise SetupError("setup plan expected record identity is invalid")
        record = _write_setup_record_compare_and_swap(
            record_path,
            selection,
            expected_record_id=expected_record_id,
        )
        outcome = "configured"
    else:
        record = load_setup_record(record_path)
        if record is None or record["selection"] != selection:
            raise SetupError("saved setup changed after its reuse plan was reviewed")
        outcome = "reused"
    v2 = any(
        action["id"] == "create-workspace" for action in plan["actions"]
    )
    response = {
        "format": RESULT_FORMAT_V2 if v2 else RESULT_FORMAT,
        "schema_version": 2 if v2 else SCHEMA_VERSION,
        "outcome": outcome,
        "applied_plan_id": plan["plan_id"],
        "record": record,
        "installed": installed,
        "verification": {
            "state": verification["state"],
            "dependencies": verification["dependencies"],
        },
        "next_commands": [
            ["workbench", "setup", "--check"],
            ["workbench", "open", selection["workspace"]],
            ["workbench", "review", "recipes", "--help"],
        ],
    }
    if v2:
        response["created"] = (
            [{"component": "workspace", "path": selection["workspace"]}]
            if created_workspace
            else []
        )
    return response


def _apply_plan(
    suite_root: Path,
    plan: Mapping[str, Any],
    *,
    record_path: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if plan["blockers"]:
        raise SetupError("setup plan has manual blockers: " + ", ".join(plan["blockers"]))
    selection = dict(plan["selection"])
    created: list[tuple[Path, int, int]] = []
    try:
        _create_reviewed_workspace(plan, selection, created)
        return _finish_setup_apply(
            suite_root,
            plan,
            selection,
            record_path=record_path,
            environment=environment,
            created_workspace=any(
                directory == _absolute(selection["workspace"])
                for directory, _device, _inode in created
            ),
        )
    except BaseException:
        _remove_empty_created_directories(created)
        raise


def _render_result(
    result: Mapping[str, Any],
    *,
    presentation: HumanPresentation | None = None,
) -> str:
    view = human_presentation() if presentation is None else presentation
    installed = result["installed"]
    review_only = result["record"]["selection"].get("profile_config") is None
    lines = [
        "Workbench setup · Saved",
        "Status: "
        + _status_label(
            "review-only" if review_only else "ready",
            presentation=view,
        ),
        f"Workspace: {result['record']['selection']['workspace']}",
    ]
    if installed:
        for row in installed:
            lines.append(f"Java: {row['outcome']} at {row['java_home']}")
    lines.extend(
        (
            "",
            "Next: "
            + human_command(
                ["workbench", "open", result["record"]["selection"]["workspace"]]
            ),
        )
    )
    return "\n".join(lines) + "\n"


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
    """Run setup with injectable streams and locations for native clients/tests."""

    parser = _parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    stdout_view = human_presentation(stdout, environment=environment)
    suite_root = _absolute(root)
    selected_record = _absolute(
        record_path
        if record_path is not None
        else default_setup_record_path(environment=environment)
    )
    try:
        if args.list_java:
            from .java_inventory import inspect_java_inventory, render_java_inventory
            policy = load_java_runtime_policy(suite_root, config_path=args.profile_config) if args.profile_config else None
            inventory = inspect_java_inventory(policy=policy, environment=environment,
                                               homes=[args.java_home] if args.java_home else [])
            stdout.write(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n" if args.json else render_java_inventory(inventory))
            return 0
        prior = load_setup_record(selected_record)
        if args.repair and prior is None:
            raise SetupError(
                "there is no saved setup to repair; choose Full developer or Review-only"
            )
        interactive = bool(getattr(stdin, "isatty", lambda: False)()) and bool(
            getattr(stdout, "isatty", lambda: False)()
        )
        explicit_operation = args.check or args.plan or args.apply is not None
        host_check = (
            inspect_host_requirements(
                environment=environment,
                configured_git=(
                    prior["selection"].get("git_executable")
                    if prior is not None and args.repair
                    else None
                ),
                explicit_git=args.git_executable,
            )
            if prior is None or args.repair
            else None
        )
        if args.json and not explicit_operation:
            raise SetupError("--json requires --check, --plan, or --apply PLAN_ID")
        if not explicit_operation and not interactive:
            raise SetupError(
                "interactive setup requires a TTY; use --check, --plan, or review "
                "--plan --json and pass its exact plan_id with --apply"
            )
        if explicit_operation:
            selection = _selection_for_noninteractive(
                suite_root,
                args,
                prior,
                environment=environment,
                host_check=host_check,
            )
        else:
            selection = _interactive_selection(
                suite_root,
                args,
                prior,
                environment=environment,
                host_check=host_check,
                input_stream=stdin,
                output=stdout,
                presentation=stdout_view,
            )

        def inspect_selection(selected: Mapping[str, Any]) -> dict[str, Any]:
            _require_external_record(
                selected_record,
                workspace=Path(selected["workspace"]),
                suite_root=suite_root,
            )
            observed = inspect_setup(
                suite_root,
                selected,
                record_path=selected_record,
                record_present=prior is not None,
                environment=environment,
                require_git=args.require_git or prior is None,
                host_requirements_check=host_check,
            )
            if (
                observed["selection"].get("java_home") is not None
                and observed["selection"].get("profile_config") is None
            ):
                raise SetupError("a Java home requires an explicit profile configuration")
            return observed

        check = inspect_selection(selection)
        if args.check:
            if args.json:
                stdout.write(json.dumps(check, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            else:
                stdout.write(_render_check(check, presentation=stdout_view))
            return 0 if check["state"] == "ready" and check["configured"] else 1
        plan = build_setup_plan(check, current_record=prior)
        if args.plan:
            if args.json:
                stdout.write(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            else:
                stdout.write(_render_check(check, presentation=stdout_view))
                stdout.write("\n" + _render_plan(plan, presentation=stdout_view))
            return 0
        if args.apply is not None:
            if args.apply != plan["plan_id"]:
                raise SetupError(
                    "--apply does not match the current plan; rerun --plan and review it"
                )
        else:
            while True:
                stdout.write(
                    _render_check(
                        check,
                        presentation=stdout_view,
                        include_record_path=False,
                    )
                )
                stdout.write(
                    "\n"
                    + _render_plan(
                        plan,
                        presentation=stdout_view,
                        include_plan_id=False,
                    )
                )
                if plan["blockers"]:
                    return 1
                answer = _prompt(
                    stdin,
                    stdout,
                    "Apply this setup? [Y/n] ",
                ).strip().casefold()
                if answer in {"", "y", "yes"}:
                    break
                if answer not in {"n", "no"}:
                    stdout.write("Please answer y or n.\n")
                    continue
                discovered_git = (
                    host_check["requirements"]["git"]["executable"]
                    if host_check is not None
                    and host_check["requirements"]["git"]["state"] == "ready"
                    else None
                )
                selection = _customize_selection(
                    suite_root,
                    selection,
                    discovered_git=discovered_git,
                    input_stream=stdin,
                    output=stdout,
                    require_profile=True,
                )
                check = inspect_selection(selection)
                plan = build_setup_plan(check, current_record=prior)
                stdout.write("\nRevised setup\n")
        result = _apply_plan(
            suite_root,
            plan,
            record_path=selected_record,
            environment=environment,
        )
        if args.json:
            stdout.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        else:
            stdout.write(_render_result(result, presentation=stdout_view))
        return 0
    except SetupCancelled:
        stdout.write("\nSetup cancelled; nothing was changed.\n")
        return 0
    except KeyboardInterrupt:
        stdout.write("\nSetup cancelled; nothing was changed.\n")
        return 130
    except (OSError, SetupError, ValueError) as exc:
        stderr.write(f"Workbench setup failed: {exc}\n")
        return 2


__all__ = [
    "CHECK_FORMAT",
    "CHECK_FORMAT_V2",
    "PLAN_FORMAT",
    "PLAN_FORMAT_V2",
    "PLAN_FORMAT_V3",
    "RECORD_FORMAT",
    "RESULT_FORMAT",
    "RESULT_FORMAT_V2",
    "SetupError",
    "apply_setup_environment_defaults",
    "build_setup_plan",
    "default_setup_record_path",
    "inspect_setup",
    "load_setup_record",
    "main",
    "replace_setup_git_binding",
    "setup_record_lock",
]
