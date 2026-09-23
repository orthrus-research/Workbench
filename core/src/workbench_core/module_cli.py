"""Explicit local-wheel lifecycle; never installs from an implicit index."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import configparser
from dataclasses import dataclass
import email.parser
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

from workbench_api import ModuleError
from workbench_api.state_paths import default_runtime_state_root
from workbench_api.profiles import profile_scope, profile_status
from .modules import discover
from .dependencies import dependency_errors, reverse_dependency_errors
from .package_ownership import validate_wheel_ownership
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version


def configuration_path(state_root: Path, kind: str) -> Path:
    if kind not in {"modules", "profiles"}:
        raise ModuleError("unknown package kind")
    from .package_guard import environment_id
    return state_root / "environments" / environment_id() / kind / "disabled.json"


def disabled_components(state_root: Path, kind: str) -> tuple[str, ...]:
    path = configuration_path(state_root, kind)
    if any(parent.is_symlink() for parent in path.parents):
        raise ModuleError("module configuration directory must not traverse symlinks")
    if not path.exists() and not path.is_symlink():
        return ()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ModuleError("module configuration is not a bounded ordinary file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or set(value) != {"disabled", "schema_version"} or value["schema_version"] != 1:
        raise ModuleError("invalid module configuration")
    ids = value["disabled"]
    if type(ids) is not list or any(type(v) is not str or not re.fullmatch(r"[a-z][a-z0-9.-]*", v) for v in ids) or len(ids) != len(set(ids)):
        raise ModuleError("invalid disabled module identifiers")
    return tuple(ids)


def disabled_modules(state_root: Path) -> tuple[str, ...]:
    return disabled_components(state_root, "modules")


def disabled_profiles(state_root: Path) -> tuple[str, ...]:
    return disabled_components(state_root, "profiles")


@contextmanager
def profile_admission_scope(state_root: Path):
    """Core binds module availability; API remains independent of its host."""
    modules = discover(disabled=disabled_modules(state_root))
    unavailable = {row.distribution for row in modules if row.state != "available"}
    with profile_scope(disabled=disabled_profiles(state_root), unavailable_distributions=unavailable):
        yield modules


def _profile_status(state_root: Path, *, disabled=None):
    with profile_admission_scope(state_root):
        return profile_status(disabled=disabled)


def _set_disabled(state_root: Path, module_id: str, disabled: bool, *, kind: str = "modules") -> None:
    # Reuse Core's cross-process setup lock for compare/read/write exclusion.
    from .setup_cli import setup_record_lock
    path = configuration_path(state_root, kind)
    if any(parent.is_symlink() for parent in (path.parent, *path.parent.parents)):
        raise ModuleError("module configuration directory must not traverse symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    with setup_record_lock(path):
        ids = set(disabled_components(state_root, kind))
        if disabled:
            ids.add(module_id)
        else:
            ids.discard(module_id)
        raw = json.dumps({"schema_version": 1, "disabled": sorted(ids)}, sort_keys=True) + "\n"
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".disabled-", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class WheelCandidate:
    path: Path
    distribution: str
    version: str
    modules: tuple[str, ...]
    requirements: tuple[str, ...]
    profiles: tuple[str, ...] = ()


@contextmanager
def _snapshot_wheel(path: Path):
    """Validate and install the same private copy of the selected wheel."""
    selected = path.expanduser().absolute()
    if selected.suffix != ".whl" or selected.is_symlink():
        raise ModuleError("select one ordinary local wheel")
    descriptor = os.open(selected, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source, tempfile.TemporaryDirectory(prefix="workbench-wheel-") as temporary:
        limit = 256 * 1024 * 1024
        observation = os.fstat(source.fileno())
        if not stat.S_ISREG(observation.st_mode) or not 0 < observation.st_size <= limit:
            raise ModuleError("wheel must be a bounded ordinary file (maximum 256 MiB)")
        snapshot = Path(temporary) / selected.name
        copied = 0
        with snapshot.open("xb") as target:
            while block := source.read(1024 * 1024):
                copied += len(block)
                if copied > limit:
                    raise ModuleError("wheel grew beyond its size limit")
                target.write(block)
        try:
            candidate = _wheel(snapshot)
        except (zipfile.BadZipFile, configparser.Error, UnicodeError, ValueError) as exc:
            raise ModuleError(f"invalid wheel: {exc}") from exc
        yield candidate


def _wheel(path: Path) -> WheelCandidate:
    selected = path.expanduser().absolute()
    if selected.is_symlink() or not selected.is_file() or selected.suffix != ".whl":
        raise ModuleError("install and update require one ordinary local wheel")
    with zipfile.ZipFile(selected) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1 or archive.getinfo(names[0]).file_size > 1024 * 1024:
            raise ModuleError("wheel must have exactly one bounded distribution metadata record")
        message = email.parser.BytesParser().parsebytes(archive.read(names[0]))
        for field in ("Name", "Version"):
            if len(message.get_all(field, [])) != 1:
                raise ModuleError(f"wheel must declare one {field}")
        name = canonicalize_name(message["Name"])
        version = Version(message["Version"])
        wheel_name, wheel_version, _, tags = parse_wheel_filename(selected.name)
        if name != wheel_name or version != wheel_version or not tags.intersection(sys_tags()):
            raise ModuleError("wheel identity or Python/platform compatibility does not match")
        if not SpecifierSet(message.get("Requires-Python", "")).contains(".".join(map(str, sys.version_info[:3]))):
            raise ModuleError("wheel requires an incompatible Python version")
        if name in {"workbench-core", "workbench-api"}:
            raise ModuleError("module installation cannot replace Core or its API")
        entry = names[0].rsplit("/", 1)[0] + "/entry_points.txt"
        if entry not in archive.namelist() or archive.getinfo(entry).file_size > 65536:
            raise ModuleError("wheel does not declare a Workbench module")
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(archive.read(entry).decode("utf-8"))
        module_ids = tuple(parser["workbench.modules"]) if parser.has_section("workbench.modules") else ()
        profile_ids = tuple(parser["workbench.profiles"]) if parser.has_section("workbench.profiles") else ()
        ids = (*module_ids, *profile_ids)
        if not ids or any(not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", value) for value in ids):
            raise ModuleError("wheel has invalid or empty module/profile declarations")
    return WheelCandidate(selected, name, str(version), module_ids, tuple(message.get_all("Requires-Dist", [])), profile_ids)


def _install(candidate: WheelCandidate, *, update: bool, state: Path) -> int:
    failures = dependency_errors(candidate.requirements, replacements={candidate.distribution: candidate.version})
    failures += reverse_dependency_errors(candidate.distribution, candidate.version)
    for group, ids in (("modules", candidate.modules), ("profiles", candidate.profiles)):
        for entry in metadata.entry_points(group=f"workbench.{group}"):
            if entry.name in ids and (entry.dist is None or canonicalize_name(entry.dist.metadata["Name"]) != candidate.distribution):
                failures.append(f"{group} ID already belongs to another distribution: {entry.name}")
    if failures:
        raise ModuleError("package preflight failed: " + "; ".join(failures))
    validate_wheel_ownership(candidate.path, candidate.distribution)
    command = ["install", "--no-index", "--no-deps", *(["--upgrade"] if update else []), str(candidate.path)]
    result = _pip(command)
    if result:
        return result
    if metadata.version(candidate.distribution) != candidate.version:
        raise ModuleError("package changed but installed version verification failed; no automatic rollback was attempted")
    observed = discover(disabled=disabled_modules(state))
    for module_id in candidate.modules:
        rows = [row for row in observed if row.id == module_id and canonicalize_name(row.distribution) == candidate.distribution]
        if len(rows) != 1 or rows[0].state not in {"available", "disabled"}:
            raise ModuleError(f"package changed but {module_id} failed admission; repair the package explicitly (no automatic rollback)")
    observed_profiles = _profile_status(state)
    for profile_id in candidate.profiles:
        rows = [row for row in observed_profiles if row.id == profile_id and canonicalize_name(row.distribution) == candidate.distribution]
        if len(rows) != 1 or rows[0].state not in {"available", "disabled"}:
            raise ModuleError(f"package changed but profile {profile_id} failed admission; repair explicitly (no automatic rollback)")
    print("Package verified. Workspace data and retained resources were not removed.")
    return 0


def _pip(arguments: list[str]) -> int:
    # Python isolation prevents cwd/PYTHONPATH/user-site pip injection. Pip's
    # isolation ignores option environment variables and user configuration,
    # while its explicit null configuration sentinel also disables global and
    # per-environment files. Neither can redirect the leased installation.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "PIP_"))}
    environment["PIP_CONFIG_FILE"] = os.devnull
    return subprocess.run(
        [sys.executable, "-I", "-m", "pip", "--isolated", *arguments],
        env=environment, check=False,
    ).returncode


def main(argv, *, root: Path, kind: str = "modules") -> int:
    if kind not in {"modules", "profiles"}:
        raise ModuleError("unknown package kind")
    parser = argparse.ArgumentParser(prog=f"workbench {kind}")
    commands = parser.add_subparsers(dest="action", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    for name in ("enable", "disable", "remove"):
        command = commands.add_parser(name)
        command.add_argument("component_id")
    for name in ("install", "update"):
        command = commands.add_parser(name)
        command.add_argument("wheel", type=Path)
    args = parser.parse_args(argv)
    package_change = args.action in {"install", "update", "remove"}
    if package_change and sys.prefix == sys.base_prefix:
        raise ModuleError("package changes require a dedicated Workbench virtual environment")
    from .package_guard import PackageActivity, package_change as mutation_guard
    # Enable/disable affect running code just as installation does.
    with (PackageActivity() if args.action == "list" else mutation_guard()):
        return _execute(args, root=root, kind=kind)


def _execute(args, *, root: Path, kind: str = "modules") -> int:
    state = default_runtime_state_root(root)
    # Do not import the old plugin into this process before replacing its wheel.
    modules = () if args.action in {"install", "update"} else (
        discover(disabled=disabled_modules(state)) if kind == "modules" else _profile_status(state))
    if args.action == "list":
        print(json.dumps([m.record() for m in modules], indent=2, sort_keys=True))
        return 0
    if args.action in {"enable", "disable", "remove"}:
        matches = [row for row in modules if row.id == args.component_id]
        if len(matches) != 1:
            raise ModuleError(f"select exactly one installed {kind} ID")
        if args.action in {"enable", "disable"}:
            if args.action == "enable":
                disabled = set(disabled_components(state, kind)) - {args.component_id}
                proposed = discover(disabled=disabled) if kind == "modules" else _profile_status(state, disabled=disabled)
                if not any(row.id == args.component_id and row.state == "available" for row in proposed):
                    raise ModuleError("component cannot be enabled until admission failures are repaired")
            _set_disabled(state, args.component_id, args.action == "disable", kind=kind)
            return 0
        distribution = matches[0].distribution
        if re.sub(r"[-_.]+", "-", distribution).lower() in {"workbench-core", "workbench-api"}:
            raise ModuleError("module removal cannot remove Core or its API")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", distribution):
            raise ModuleError("invalid installed distribution name")
        dependents = [row.id for row in modules if getattr(row, "module", None) and args.component_id in row.module.requires]
        if dependents:
            raise ModuleError("remove dependent modules first: " + ", ".join(dependents))
        failures = reverse_dependency_errors(distribution, None)
        if failures:
            raise ModuleError("package removal would break installed consumers: " + "; ".join(failures))
        command = ["uninstall", "--yes", distribution]
    else:
        with _snapshot_wheel(args.wheel) as wheel:
            if not getattr(wheel, kind):
                raise ModuleError(f"selected wheel does not declare {kind}")
            return _install(wheel, update=args.action == "update", state=state)
    # pip is optional so a Core-only installation has no installer dependency.
    # Wheel installation executes trusted local code on subsequent discovery.
    result = _pip(command)
    if result == 0:
        print("Package change complete. Workspace data and retained resources were not removed.")
    return result
