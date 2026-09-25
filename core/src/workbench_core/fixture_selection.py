"""Core-owned, per-user fixture locations for profile-specific operations.

Locations are hints, not evidence of compatibility.  An operation's selected
profile must inspect the bytes before it uses a registered or overridden path.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .host_filesystem import file_lease, fsync_directory, secure_private_path
from .setup_cli import default_setup_record_path, load_setup_record
from .user_config_home import default_user_record_path


FORMAT = "workbench-recipe-fixture-locations-v1"
_MAX_BYTES = 256 * 1024
_PROFILE = re.compile(r"[a-z][a-z0-9._:-]{0,159}\Z")


class FixtureSelectionError(ValueError):
    """A user fixture location is absent, ambiguous, or malformed."""


def default_fixture_registry_path() -> Path:
    return default_user_record_path("recipe-fixtures-v1.json")


def _absolute(value: Path | str, label: str) -> Path:
    if not isinstance(value, (Path, str)) or not str(value):
        raise FixtureSelectionError(f"{label} must be an absolute directory")
    path = Path(value).expanduser().absolute()
    if not path.is_absolute():
        raise FixtureSelectionError(f"{label} must be an absolute directory")
    return path


def _profile(value: str) -> str:
    if type(value) is not str or _PROFILE.fullmatch(value) is None:
        raise FixtureSelectionError("select a stable recipe profile ID")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record(entries: list[dict[str, str]]) -> dict[str, Any]:
    body = {"format": FORMAT, "schema_version": 1, "entries": entries}
    return {**body, "id": "recipe-fixtures:sha256:" + sha256(_canonical(body)).hexdigest()}


def load_fixture_registry(path: Path | str | None = None) -> dict[str, Any]:
    selected = default_fixture_registry_path() if path is None else _absolute(path, "fixture registry")
    if not selected.exists() and not selected.is_symlink():
        return _record([])
    if selected.is_symlink() or not selected.is_file() or selected.stat().st_size > _MAX_BYTES:
        raise FixtureSelectionError("recipe fixture registry is not an ordinary bounded file")
    try:
        raw = selected.read_bytes()
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise FixtureSelectionError("recipe fixture registry has duplicate keys")
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FixtureSelectionError("recipe fixture registry is not valid JSON") from exc
    if type(value) is not dict or set(value) != {"format", "schema_version", "entries", "id"}:
        raise FixtureSelectionError("recipe fixture registry has unsupported fields")
    entries = value["entries"]
    if type(entries) is not list or len(entries) > 256:
        raise FixtureSelectionError("recipe fixture registry has too many entries")
    keys = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"profile", "workspace", "runtime", "java_home"}:
            raise FixtureSelectionError("recipe fixture entry has unsupported fields")
        _profile(entry["profile"])
        for field in ("workspace", "runtime", "java_home"):
            if type(entry[field]) is not str or not Path(entry[field]).is_absolute():
                raise FixtureSelectionError(f"recipe fixture {field} must be an absolute path")
        key = (entry["profile"], entry["workspace"])
        if key in keys:
            raise FixtureSelectionError("recipe fixture registry has duplicate selections")
        keys.add(key)
    if value != _record(entries):
        raise FixtureSelectionError("recipe fixture registry identity changed")
    return value


def register_recipe_fixture(profile: str, workspace: Path | str, runtime: Path | str,
                            java_home: Path | str, *, path: Path | str | None = None) -> dict[str, Any]:
    """Save user locations; profile validation happens when an operation plans."""
    selected = default_fixture_registry_path() if path is None else _absolute(path, "fixture registry")
    entry = {"profile": _profile(profile), "workspace": str(_absolute(workspace, "workspace")),
             "runtime": str(_absolute(runtime, "runtime")),
             "java_home": str(_absolute(java_home, "Java home"))}
    selected.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_private_path(selected.parent, directory=True)
    lock = selected.with_suffix(selected.suffix + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise FixtureSelectionError("recipe fixture registry lock must be an independent file")
        secure_private_path(lock, directory=False)
        with file_lease(descriptor, exclusive=True):
            previous = load_fixture_registry(selected)
            entries = [row for row in previous["entries"]
                       if (row["profile"], row["workspace"]) != (entry["profile"], entry["workspace"])]
            entries.append(entry)
            result = _record(sorted(entries, key=lambda row: (row["profile"], row["workspace"])))
            temporary = selected.with_name("." + selected.name + "." + uuid4().hex + ".tmp")
            try:
                with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                    stream.write(_canonical(result) + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, selected)
                secure_private_path(selected, directory=False)
                fsync_directory(selected.parent)
            finally:
                temporary.unlink(missing_ok=True)
            return result
    finally:
        os.close(descriptor)


def resolve_recipe_fixture(profile: str, workspace: Path | str | None = None, *,
                           runtime: Path | str | None = None, java_home: Path | str | None = None,
                           registry_path: Path | str | None = None,
                           setup_path: Path | str | None = None) -> dict[str, Any]:
    """Resolve overrides, then user registration, then a matching Setup JDK."""
    if workspace is not None and runtime is not None and java_home is not None:
        return {"profile": _profile(profile), "workspace": str(_absolute(workspace, "workspace")),
                "runtime": str(_absolute(runtime, "runtime")),
                "java_home": str(_absolute(java_home, "Java home")),
                "runtime_source": "override", "java_source": "override", "registry_id": None}
    selected_setup = default_setup_record_path() if setup_path is None else Path(setup_path)
    setup = load_setup_record(selected_setup) if workspace is None else None
    selected_workspace = _absolute(
        workspace if workspace is not None else setup["selection"]["workspace"] if setup
        else os.environ.get("WORKBENCH_WORKSPACE") or Path.cwd(),
        "workspace")
    registry = load_fixture_registry(registry_path)
    entry = next((row for row in registry["entries"] if row["profile"] == _profile(profile)
                  and row["workspace"] == str(selected_workspace)), None)
    if setup is None and java_home is None and entry is None:
        setup = load_setup_record(selected_setup)
    setup_java = None
    if setup and setup["selection"]["workspace"] == str(selected_workspace):
        setup_java = setup["selection"].get("java_home") or setup["selection"].get("managed_java_home")
    selected_runtime = runtime if runtime is not None else entry["runtime"] if entry else None
    selected_java = java_home if java_home is not None else entry["java_home"] if entry else setup_java
    if selected_runtime is None or selected_java is None:
        missing = "runtime and Java" if selected_runtime is None and selected_java is None else (
            "runtime" if selected_runtime is None else "Java")
        raise FixtureSelectionError(
            f"No {missing} selected for profile {profile} and workspace {selected_workspace}. "
            "Register locations with 'workbench capture recipes fixtures set' or supply "
            "--runtime and --java-home overrides. No pack files were changed.")
    return {"profile": _profile(profile), "workspace": str(selected_workspace),
            "runtime": str(_absolute(selected_runtime, "runtime")),
            "java_home": str(_absolute(selected_java, "Java home")),
            "runtime_source": "override" if runtime is not None else "user-registry",
            "java_source": "override" if java_home is not None else "user-registry" if entry else "core-setup",
            "registry_id": registry["id"]}


def find_artifact_by_digest(runtime: Path | str, digest: str, *, suffix: str = ".jar") -> Path:
    """Find one profile-pinned file without assuming a pack's directory layout."""
    root = _absolute(runtime, "runtime")
    if not root.is_dir() or root.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise FixtureSelectionError("select an ordinary runtime and a profile artifact digest")
    matches: list[Path] = []
    count = 0
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = [name for name in children if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() != suffix or path.is_symlink():
                continue
            count += 1
            if count > 4096:
                raise FixtureSelectionError("runtime has too many candidate libraries; select an exact override")
            actual = sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    actual.update(block)
            if actual.hexdigest() == digest:
                matches.append(path)
    if len(matches) != 1:
        raise FixtureSelectionError(
            f"profile artifact {digest} has {len(matches)} matches in selected runtime; "
            "choose a compatible runtime or supply an exact library override")
    return matches[0]


def resolve_java_library_fixture(profile: str, digest: str, *, workspace: Path | str | None = None,
                                 java_executable: Path | str | None = None,
                                 library: Path | str | None = None) -> tuple[Path, Path]:
    """Resolve a synthetic Java test classpath through Core or exact overrides."""
    explicit_java = Path(java_executable).expanduser().absolute() if java_executable else None
    explicit_library = Path(library).expanduser().absolute() if library else None
    selection = resolve_recipe_fixture(
        profile, Path.cwd() if workspace is None and explicit_java and explicit_library else workspace,
        java_home=explicit_java.parent.parent if explicit_java else None,
        runtime=explicit_library.parent if explicit_library else None,
    )
    java = explicit_java or Path(selection["java_home"]) / "bin" / (
        "java.exe" if os.name == "nt" else "java")
    javac = java.with_name("javac.exe" if os.name == "nt" else "javac")
    if not java.is_file() or not javac.is_file():
        raise FixtureSelectionError("selected Java is not a JDK with java and javac; use a JDK override")
    selected_library = explicit_library or find_artifact_by_digest(selection["runtime"], digest)
    if not selected_library.is_file() or selected_library.is_symlink():
        raise FixtureSelectionError("selected library is not an ordinary file")
    actual = sha256()
    with selected_library.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            actual.update(block)
    if actual.hexdigest() != digest:
        raise FixtureSelectionError("selected library does not match the profile artifact digest")
    return java, selected_library


__all__ = ["FixtureSelectionError", "default_fixture_registry_path", "load_fixture_registry",
           "register_recipe_fixture", "resolve_recipe_fixture", "find_artifact_by_digest",
           "resolve_java_library_fixture"]
