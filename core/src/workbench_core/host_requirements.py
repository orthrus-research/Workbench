"""Host-aware discovery for Workbench's baseline system requirements.

This owner is intentionally small.  It observes an existing Git installation
on ``PATH`` or in bounded, conventional locations and, when Git is absent,
selects one native package-manager command that a separate consent boundary
may run.  It never changes the process environment or the user's machine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import threading
from typing import Any
import unicodedata


CHECK_FORMAT = "workbench-host-requirements-check-v1"
SCHEMA_VERSION = 1
MAX_OS_RELEASE_BYTES = 64 * 1024
MAX_MANAGER_BYTES = 512 * 1024 * 1024
MAX_GIT_PROBE_OUTPUT_BYTES = 64 * 1024
_WINDOWS_DESCRIPTOR_PATH_MODE_MAY_DIFFER = os.name == "nt"
_EXECUTABLE_DESCRIPTOR_CUSTODY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_size",
    "st_mtime_ns",
)
_EXECUTABLE_CROSS_VIEW_CUSTODY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
)


def _host_family(system_name: str) -> str:
    normalized = system_name.strip().casefold()
    if normalized == "linux":
        return "linux"
    if normalized == "windows" or normalized.startswith("win"):
        return "windows"
    if normalized in {"darwin", "mac", "macos"}:
        return "macos"
    return "other"


def _environment_value(
    environment: Mapping[str, str],
    key: str,
    *,
    case_insensitive: bool = False,
) -> str | None:
    value = environment.get(key)
    if value is not None or not case_insensitive:
        return value
    folded = key.casefold()
    return next(
        (candidate for name, candidate in environment.items() if name.casefold() == folded),
        None,
    )


def _read_linux_distribution(path: Path) -> dict[str, str | None]:
    result: dict[str, str | None] = {"id": None, "id_like": None}
    try:
        selected = path.resolve(strict=True)
        info = selected.stat()
    except OSError:
        return result
    if not selected.is_file() or not 1 <= info.st_size <= MAX_OS_RELEASE_BYTES:
        return result
    try:
        lines = selected.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return result
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in {"ID", "ID_LIKE"}:
            values[key] = value.casefold()
    result["id"] = values.get("ID")
    result["id_like"] = values.get("ID_LIKE")
    return result


def inspect_host(
    *,
    system_name: str | None = None,
    machine: str | None = None,
    os_release_path: Path | str = "/etc/os-release",
) -> dict[str, Any]:
    """Return the small host identity needed for requirement repair policy."""

    observed_system = platform.system() if system_name is None else system_name
    family = _host_family(observed_system)
    distribution = (
        _read_linux_distribution(Path(os_release_path))
        if family == "linux"
        else {"id": None, "id_like": None}
    )
    return {
        "family": family,
        "system": observed_system,
        "machine": platform.machine() if machine is None else machine,
        # This is a routing fact, not a release-support claim.  Whether an
        # automatic repair can actually run is reported by the requirement's
        # repair state after a fixed package-manager executable is found.
        "repair_family_recognized": family in {"linux", "windows"},
        "distribution": distribution,
    }


def _candidate(
    rows: list[dict[str, str]],
    seen: set[str],
    path: Path | str | None,
    source: str,
) -> None:
    if path is None or not os.fspath(path):
        return
    absolute = os.path.abspath(os.path.expanduser(os.fspath(path)))
    key = os.path.normcase(absolute)
    if key in seen:
        return
    seen.add(key)
    rows.append({"path": absolute, "source": source})


def _safe_path_roots(
    environment: Mapping[str, str],
    *,
    case_insensitive: bool = False,
) -> tuple[str, ...]:
    """Discard empty and relative PATH entries before executable lookup."""

    roots: list[str] = []
    seen: set[str] = set()
    path_value = _environment_value(
        environment,
        "PATH",
        case_insensitive=case_insensitive,
    ) or ""
    for raw in path_value.split(os.pathsep):
        if not raw:
            continue
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            continue
        absolute = os.path.abspath(expanded)
        key = os.path.normcase(absolute)
        if key not in seen:
            seen.add(key)
            roots.append(absolute)
    return tuple(roots)


def _which_in_safe_path(
    name: str,
    environment: Mapping[str, str],
    *,
    which: Callable[..., str | None],
    case_insensitive: bool = False,
) -> str | None:
    roots = _safe_path_roots(environment, case_insensitive=case_insensitive)
    if not roots:
        return None
    bounded_path = os.pathsep.join(roots)
    try:
        selected = which(name, path=bounded_path)
    except TypeError:
        selected = which(name, bounded_path)
    if not selected:
        return None
    absolute = os.path.abspath(os.path.expanduser(selected))
    for root in roots:
        try:
            if os.path.commonpath((absolute, root)) == root:
                return absolute
        except ValueError:
            continue
    return None


def git_candidates(
    environment: Mapping[str, str],
    host: Mapping[str, Any],
    *,
    configured_git: Path | str | None = None,
    explicit_git: Path | str | None = None,
    which: Callable[..., str | None] = shutil.which,
) -> list[dict[str, str]]:
    """Return an ordered, bounded list of plausible Git executables."""

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    windows = host["family"] == "windows"
    if explicit_git is not None:
        _candidate(rows, seen, explicit_git, "explicit")
        return rows
    _candidate(rows, seen, configured_git, "saved-setup")
    _candidate(
        rows,
        seen,
        _environment_value(
            environment,
            "WORKBENCH_GIT_EXECUTABLE",
            case_insensitive=windows,
        ),
        "environment",
    )
    family = host["family"]
    executable_name = "git.exe" if family == "windows" else "git"
    path_git = _which_in_safe_path(
        "git",
        environment,
        which=which,
        case_insensitive=windows,
    )
    if family == "linux":
        _candidate(rows, seen, path_git, "PATH")
        try:
            user_git: Path | None = Path.home() / ".local/bin/git"
        except (OSError, RuntimeError):
            user_git = None
        for path in (
            user_git,
            Path("/usr/local/bin/git"),
            Path("/usr/bin/git"),
            Path("/bin/git"),
        ):
            _candidate(rows, seen, path, "linux-common-directory")
    elif family == "windows":
        program_roots: list[Path] = []
        for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            value = _environment_value(environment, key, case_insensitive=True)
            if value and Path(value).expanduser().is_absolute():
                program_roots.append(Path(value).expanduser())
        if not program_roots:
            system_drive = _environment_value(
                environment, "SystemDrive", case_insensitive=True
            ) or "C:"
            program_roots.extend(
                (Path(system_drive + "\\Program Files"), Path(system_drive + "\\Program Files (x86)"))
            )
        for root in program_roots:
            _candidate(rows, seen, root / "Git/cmd/git.exe", "windows-program-files")
            _candidate(rows, seen, root / "Git/bin/git.exe", "windows-program-files")
        local = _environment_value(environment, "LOCALAPPDATA", case_insensitive=True)
        if local and Path(local).expanduser().is_absolute():
            _candidate(
                rows,
                seen,
                Path(local) / "Programs/Git/cmd/git.exe",
                "windows-local-app-data",
            )
            _candidate(
                rows,
                seen,
                Path(local) / "Programs/Git/bin/git.exe",
                "windows-local-app-data",
            )
        chocolatey = _environment_value(
            environment, "ChocolateyInstall", case_insensitive=True
        )
        if chocolatey and Path(chocolatey).expanduser().is_absolute():
            _candidate(
                rows,
                seen,
                Path(chocolatey) / "bin/git.exe",
                "windows-chocolatey",
            )
        user_profile = _environment_value(
            environment, "USERPROFILE", case_insensitive=True
        )
        if user_profile and Path(user_profile).expanduser().is_absolute():
            _candidate(
                rows,
                seen,
                Path(user_profile) / "scoop/apps/git/current/cmd/git.exe",
                "windows-scoop",
            )
        # The packaged Pixi runtime may prepend its own Git to PATH.  A normal
        # Windows installation is the host dependency that setup and repair
        # must report when it exists, so bounded conventional locations are
        # checked before the inherited PATH fallback.
        _candidate(rows, seen, path_git, "PATH")
    else:
        _candidate(rows, seen, path_git, "PATH")

    runtime_executable = Path(sys.executable)
    if sys.executable and runtime_executable.is_absolute():
        _candidate(
            rows,
            seen,
            runtime_executable.parent / executable_name,
            "workbench-runtime",
        )
    return rows


def _bounded_command_output(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> tuple[int | None, bytes, bool, str | None]:
    """Drain child output while retaining only a fixed in-memory prefix."""

    try:
        process = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return None, b"", False, type(exc).__name__
    retained = bytearray()
    overflow = False
    guard = threading.Lock()

    def drain() -> None:
        nonlocal overflow
        stream = process.stdout
        if stream is None:
            return
        try:
            while True:
                block = stream.read(8192)
                if not block:
                    break
                with guard:
                    available = MAX_GIT_PROBE_OUTPUT_BYTES - len(retained)
                    if available > 0:
                        retained.extend(block[:available])
                    if len(block) > available:
                        overflow = True
        except (OSError, ValueError):
            return

    reader = threading.Thread(target=drain, name="workbench-git-probe", daemon=True)
    reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait()
    reader.join(timeout=1)
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
        was_truncated = overflow
    return (
        returncode,
        output,
        was_truncated,
        "TimeoutExpired" if timed_out else None,
    )


def probe_git_executable(path: Path | str) -> dict[str, Any]:
    """Content-bind and execute one Git candidate with bounded output."""

    selected = Path(path)
    try:
        if not selected.is_file():
            return {"state": "missing"}
        identity = measure_executable(selected)
    except OSError as exc:
        return {"state": "incompatible", "detail": type(exc).__name__}
    except ValueError as exc:
        return {"state": "incompatible", "detail": str(exc)}
    returncode, raw, truncated, error = _bounded_command_output(
        [identity["path"], "--version"],
        timeout_seconds=5,
    )
    if error is not None:
        return {"state": "incompatible", "detail": error}
    if truncated:
        return {"state": "incompatible", "detail": "output exceeded 65536 bytes"}
    try:
        output = raw.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return {"state": "incompatible", "detail": "output is not UTF-8"}
    if (
        returncode != 0
        or len(output.encode("utf-8")) > 512
        or "\n" in output
        or "\r" in output
        or any(unicodedata.category(character).startswith("C") for character in output)
        or not output.casefold().startswith("git version ")
    ):
        return {
            "state": "incompatible",
            "detail": f"exit {returncode}: {output[:128] or 'no Git version'}",
        }
    try:
        after = measure_executable(identity["path"])
    except ValueError as exc:
        return {"state": "incompatible", "detail": str(exc)}
    if after != identity:
        return {"state": "incompatible", "detail": "Git changed during its probe"}
    return {
        "state": "ready",
        "version": output,
        "executable_identity": identity,
    }


def _is_windows_winget_alias_candidate(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return (
        path.name.casefold() == "winget.exe"
        and len(parts) >= 3
        and parts[-3:] == ("microsoft", "windowsapps", "winget.exe")
    )


def _find_fixed_executable(
    common_paths: Sequence[Path],
    *,
    allow_windows_winget_alias: bool = False,
) -> str | None:
    for path in common_paths:
        if not path.is_absolute():
            continue
        if allow_windows_winget_alias and _is_windows_winget_alias_candidate(path):
            try:
                # Windows app-execution aliases are fixed zero-byte reparse
                # entries.  They are launchable even though there are no
                # executable bytes at this path to hash.
                observed = path.lstat()
                if (
                    observed.st_size == 0
                    and not stat.S_ISDIR(observed.st_mode)
                    and os.access(path, os.F_OK)
                ):
                    return str(path.absolute())
            except OSError:
                pass
        try:
            resolved = path.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return str(resolved)
        except (OSError, RuntimeError):
            continue
    return None


def _same_stat_fields(
    left: os.stat_result,
    right: os.stat_result,
    fields: tuple[str, ...],
) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _stable_executable_custody(
    before: os.stat_result,
    after: os.stat_result,
    current: os.stat_result,
) -> bool:
    """Compare stable handle custody with the final pathname view.

    Git for Windows can report different permission bits for ``fstat()`` and
    pathname ``lstat()`` views of the same executable.  Preserve raw mode
    equality within the descriptor view and all other identity fields across
    views; the caller independently requires every view to be a regular file.
    """

    cross_view_fields = _EXECUTABLE_CROSS_VIEW_CUSTODY_FIELDS
    if not _WINDOWS_DESCRIPTOR_PATH_MODE_MAY_DIFFER:
        cross_view_fields += ("st_mode",)
    return _same_stat_fields(
        before,
        after,
        _EXECUTABLE_DESCRIPTOR_CUSTODY_FIELDS,
    ) and _same_stat_fields(after, current, cross_view_fields)


def _descriptor_is_executable(path: Path, observed: os.stat_result) -> bool:
    """Decide executability from the opened object, never a second pathname lookup."""

    if os.name == "nt":
        # Windows does not expose an execute mode bit through fstat().  The
        # supported repair tools are native CreateProcess images; content and
        # path custody are checked around the read and the tool is probed again
        # before any repair is authorized.
        return path.suffix.casefold() in {".com", ".exe"}
    return bool(stat.S_IMODE(observed.st_mode) & 0o111)


def measure_executable(
    path: Path | str,
    *,
    allow_windows_winget_alias: bool = False,
) -> dict[str, Any]:
    """Content-measure one fixed repair executable for plan/apply freshness."""

    selected = Path(path)
    if not selected.is_absolute():
        raise ValueError("repair executable must be an absolute path")
    if allow_windows_winget_alias and _is_windows_winget_alias_candidate(selected):
        try:
            before = selected.lstat()
            if (
                before.st_size == 0
                and not stat.S_ISDIR(before.st_mode)
                and os.access(selected, os.F_OK)
            ):
                after = selected.lstat()
                identity_fields = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_size",
                    "st_mtime_ns",
                )
                if any(
                    getattr(before, field) != getattr(after, field)
                    for field in identity_fields
                ):
                    raise ValueError(
                        "Windows app-execution alias changed while it was measured"
                    )
                return {
                    "path": str(selected.absolute()),
                    "size_bytes": 0,
                    "sha256": sha256(b"").hexdigest(),
                    "device": before.st_dev,
                    "inode": before.st_ino,
                    "mode": stat.S_IMODE(before.st_mode),
                    "mtime_ns": before.st_mtime_ns,
                }
        except OSError as exc:
            raise ValueError("Windows app-execution alias could not be measured") from exc
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("repair executable is unavailable") from exc
    descriptor: int | None = None
    digest = sha256()
    consumed = 0
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(resolved, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not _descriptor_is_executable(resolved, before)
            or not 1 <= before.st_size <= MAX_MANAGER_BYTES
        ):
            raise ValueError("repair executable is not a bounded regular executable")
        while True:
            block = os.read(
                descriptor,
                min(1024 * 1024, MAX_MANAGER_BYTES - consumed + 1),
            )
            if not block:
                break
            consumed += len(block)
            if consumed > MAX_MANAGER_BYTES:
                raise ValueError("repair executable exceeds its byte bound")
            digest.update(block)
        after = os.fstat(descriptor)
        current = resolved.lstat()
    except OSError as exc:
        raise ValueError("repair executable could not be measured") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        consumed != before.st_size
        or not stat.S_ISREG(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or not _stable_executable_custody(before, after, current)
    ):
        raise ValueError("repair executable changed while it was measured")
    return {
        "path": str(resolved),
        "size_bytes": consumed,
        "sha256": digest.hexdigest(),
        "device": before.st_dev,
        "inode": before.st_ino,
        "mode": stat.S_IMODE(before.st_mode),
        "mtime_ns": before.st_mtime_ns,
    }


def _linux_manager_order(host: Mapping[str, Any]) -> tuple[str, ...]:
    distribution = host.get("distribution", {})
    labels = " ".join(
        value
        for value in (distribution.get("id"), distribution.get("id_like"))
        if isinstance(value, str)
    )
    if any(token in labels.split() for token in ("debian", "ubuntu")):
        return ("apt-get", "dnf", "yum", "zypper", "pacman")
    if any(token in labels.split() for token in ("fedora", "rhel", "centos")):
        return ("dnf", "yum", "apt-get", "zypper", "pacman")
    if any(token in labels.split() for token in ("suse", "opensuse")):
        return ("zypper", "dnf", "apt-get", "yum", "pacman")
    if "arch" in labels.split():
        return ("pacman", "dnf", "apt-get", "zypper", "yum")
    return ("apt-get", "dnf", "yum", "zypper", "pacman")


def _windows_process_is_elevated() -> bool:
    if os.name != "nt":
        return False
    try:  # pragma: no cover - exercised by native Windows CI
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def git_install_strategy(
    host: Mapping[str, Any],
    environment: Mapping[str, str],
    *,
    effective_uid: int | None = None,
    manager_paths: Mapping[str, Sequence[Path]] | None = None,
    sudo_paths: Sequence[Path] | None = None,
    windows_is_elevated: bool | None = None,
) -> dict[str, Any]:
    """Choose an exact native install command without executing it."""

    family = host["family"]
    if family == "linux":
        selected_manager_paths = dict(
            {
                "apt-get": (Path("/usr/bin/apt-get"),),
                "dnf": (Path("/usr/bin/dnf"), Path("/bin/dnf")),
                "yum": (Path("/usr/bin/yum"), Path("/bin/yum")),
                "zypper": (Path("/usr/bin/zypper"),),
                "pacman": (Path("/usr/bin/pacman"),),
            }
            if manager_paths is None
            else manager_paths
        )
        manager: str | None = None
        manager_path: str | None = None
        for name in _linux_manager_order(host):
            selected = _find_fixed_executable(selected_manager_paths.get(name, ()))
            if selected is not None:
                manager, manager_path = name, selected
                break
        if manager is None or manager_path is None:
            return {
                "state": "manual",
                "detail": "No supported Linux package manager was found.",
                "command": None,
            }
        arguments = {
            "apt-get": [manager_path, "install", "--yes", "git"],
            "dnf": [manager_path, "install", "--assumeyes", "git"],
            "yum": [manager_path, "install", "--assumeyes", "git"],
            "zypper": [manager_path, "--non-interactive", "install", "git"],
            "pacman": [manager_path, "--sync", "--needed", "--noconfirm", "git"],
        }[manager]
        uid = (
            os.geteuid()
            if effective_uid is None and hasattr(os, "geteuid")
            else 0
            if effective_uid is None
            else effective_uid
        )
        requires_elevation = uid != 0
        executable_identities: list[dict[str, Any]] = []
        if requires_elevation:
            sudo = _find_fixed_executable(
                tuple(
                    (Path("/usr/bin/sudo"), Path("/bin/sudo"))
                    if sudo_paths is None
                    else sudo_paths
                )
            )
            if sudo is None:
                return {
                    "state": "manual",
                    "detail": (
                        f"{manager} can install Git, but Workbench could not find sudo. "
                        "Run the package-manager command as an administrator."
                    ),
                    "command": arguments,
                }
            arguments = [sudo, "--", *arguments]
            try:
                executable_identities.append(measure_executable(sudo))
            except ValueError as exc:
                return {
                    "state": "manual",
                    "detail": f"sudo could not be bound safely: {exc}",
                    "command": arguments,
                }
        try:
            executable_identities.append(measure_executable(manager_path))
        except ValueError as exc:
            return {
                "state": "manual",
                "detail": f"{manager} could not be bound safely: {exc}",
                "command": arguments,
            }
        return {
            "state": "available",
            "manager": manager,
            "requires_elevation": requires_elevation,
            "command": arguments,
            "executable_identities": executable_identities,
            "detail": f"Install Git with the detected {manager} package manager.",
        }
    if family == "windows":
        machine = str(host.get("machine", "")).strip().casefold()
        if machine in {"arm64", "aarch64"}:
            return {
                "state": "manual",
                "detail": (
                    "Automatic Git installation is not qualified on Windows ARM64. "
                    "Install a native ARM64 Git, then rerun workbench repair."
                ),
                "command": None,
            }
        local = _environment_value(environment, "LOCALAPPDATA", case_insensitive=True)
        default_winget = (
            (Path(local) / "Microsoft/WindowsApps/winget.exe",) if local else ()
        )
        program_data = _environment_value(environment, "ProgramData", case_insensitive=True)
        default_chocolatey = (
            (Path(program_data) / "chocolatey/bin/choco.exe",)
            if program_data
            else (Path(r"C:\ProgramData\chocolatey\bin\choco.exe"),)
        )
        selected_manager_paths = dict(
            {"winget": default_winget, "chocolatey": default_chocolatey}
            if manager_paths is None
            else manager_paths
        )
        winget = _find_fixed_executable(
            selected_manager_paths.get("winget", ()),
            allow_windows_winget_alias=True,
        )
        if winget is not None:
            try:
                identities = [
                    measure_executable(
                        winget,
                        allow_windows_winget_alias=True,
                    )
                ]
            except ValueError as exc:
                return {
                    "state": "manual",
                    "detail": f"Winget could not be bound safely: {exc}",
                    "command": None,
                }
            return {
                "state": "available",
                "manager": "winget",
                "requires_elevation": False,
                "command": [
                    winget,
                    "install",
                    "--id",
                    "Git.Git",
                    "--exact",
                    "--source",
                    "winget",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
                "executable_identities": identities,
                "detail": "Install Git for Windows with Winget.",
            }
        choco = _find_fixed_executable(selected_manager_paths.get("chocolatey", ()))
        if choco is not None:
            elevated = (
                _windows_process_is_elevated()
                if windows_is_elevated is None
                else windows_is_elevated
            )
            if not elevated:
                return {
                    "state": "manual",
                    "detail": (
                        "Chocolatey was found, but this Workbench process is not "
                        "elevated. Rerun from an administrator terminal or install "
                        "Git for Windows manually."
                    ),
                    "command": None,
                }
            try:
                identities = [measure_executable(choco)]
            except ValueError as exc:
                return {
                    "state": "manual",
                    "detail": f"Chocolatey could not be bound safely: {exc}",
                    "command": None,
                }
            return {
                "state": "available",
                "manager": "chocolatey",
                "requires_elevation": True,
                "command": [choco, "install", "git", "--yes", "--no-progress"],
                "executable_identities": identities,
                "detail": "Install Git for Windows with Chocolatey.",
            }
        return {
            "state": "manual",
            "detail": "Install Git for Windows, then rerun workbench repair.",
            "command": None,
        }
    return {
        "state": "manual",
        "detail": (
            f"Automatic Git repair is not implemented for {host['system']}; "
            "install Git and rerun setup."
        ),
        "command": None,
    }


def inspect_host_requirements(
    *,
    environment: Mapping[str, str] | None = None,
    configured_git: Path | str | None = None,
    explicit_git: Path | str | None = None,
    system_name: str | None = None,
    machine: str | None = None,
    os_release_path: Path | str = "/etc/os-release",
    which: Callable[..., str | None] = shutil.which,
    effective_uid: int | None = None,
) -> dict[str, Any]:
    """Inspect Git and the host-native recovery route without mutation."""

    values = dict(os.environ if environment is None else environment)
    host = inspect_host(
        system_name=system_name,
        machine=machine,
        os_release_path=os_release_path,
    )
    candidates = git_candidates(
        values,
        host,
        configured_git=configured_git,
        explicit_git=explicit_git,
        which=which,
    )
    checked: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    first_incompatible: dict[str, Any] | None = None
    for candidate in candidates:
        probe = probe_git_executable(Path(candidate["path"]))
        row = {**candidate, **probe}
        if probe["state"] == "ready":
            row["path"] = probe["executable_identity"]["path"]
        checked.append(row)
        if probe["state"] == "ready":
            selected = row
            break
        if probe["state"] == "incompatible" and first_incompatible is None:
            first_incompatible = row
    if selected is not None:
        git = {
            "required": True,
            "state": "ready",
            "executable": selected["path"],
            "version": selected["version"],
            "discovery": selected["source"],
            "executable_identity": selected["executable_identity"],
            "candidates_checked": checked,
            "repair": {"state": "not-needed", "command": None},
        }
        state = "ready"
    else:
        if explicit_git is not None:
            repair = {
                "state": "manual",
                "detail": (
                    "The explicit Git executable is not usable. Choose a working "
                    "path or omit --git-executable to resume bounded discovery."
                ),
                "command": None,
            }
        else:
            repair = git_install_strategy(
                host,
                values,
                effective_uid=effective_uid,
            )
        git = {
            "required": True,
            "state": "incompatible" if first_incompatible is not None else "missing",
            "executable": None,
            "version": None,
            "discovery": None,
            "executable_identity": None,
            "candidates_checked": checked,
            "detail": (
                "Candidate Git executables were not usable."
                if first_incompatible is not None
                else "Git was not found on PATH or in standard host directories."
            ),
            "repair": repair,
        }
        state = "repairable" if repair["state"] == "available" else "attention"
    return {
        "format": CHECK_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        "state": state,
        "host": host,
        "requirements": {"git": git},
    }


__all__ = [
    "CHECK_FORMAT",
    "git_candidates",
    "git_install_strategy",
    "inspect_host",
    "inspect_host_requirements",
    "measure_executable",
    "probe_git_executable",
]
