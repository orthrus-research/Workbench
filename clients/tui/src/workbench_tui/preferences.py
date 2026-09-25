"""Portable, client-owned presentation choices outside an installed wheel."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping
from uuid import uuid4


FORMAT = "workbench-tui-settings-v1"
_THEME_NAME = re.compile(r"[a-z][a-z0-9-]{0,79}\Z")
_MAX_BYTES = 16 * 1024


class PreferencesError(ValueError):
    """A client preference cannot be read or changed safely."""


@dataclass(frozen=True)
class TuiPreferences:
    theme: str = "workbench-dark"
    show_clock: bool = True
    record_id: str | None = None


def config_home(*, environment: Mapping[str, str] | None = None) -> Path:
    """Use a stable client dotfile home without importing Core."""
    values = os.environ if environment is None else environment
    explicit = values.get("WORKBENCH_CONFIG_HOME")
    if explicit:
        selected = Path(explicit).expanduser()
        if not selected.is_absolute():
            raise PreferencesError("WORKBENCH_CONFIG_HOME must be an absolute directory")
        return selected.absolute()
    home = values.get("USERPROFILE") if os.name == "nt" else values.get("HOME")
    return (Path(home).expanduser().absolute() if home else Path.home().absolute()) / ".workbench"


def preferences_path(*, environment: Mapping[str, str] | None = None) -> Path:
    return config_home(environment=environment) / "tui.json"


def _record_id(theme: str, show_clock: bool) -> str:
    body = {"format": FORMAT, "schema_version": 1, "theme": theme, "show_clock": show_clock}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "workbench-tui-settings:sha256:" + sha256(raw).hexdigest()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PreferencesError(f"duplicate TUI preference key: {key}")
        value[key] = item
    return value


def load_preferences(path: Path | None = None, *, environment: Mapping[str, str] | None = None) -> TuiPreferences:
    selected = preferences_path(environment=environment) if path is None else path
    try:
        info = selected.lstat()
    except FileNotFoundError:
        return TuiPreferences()
    except OSError as exc:
        raise PreferencesError(f"cannot inspect TUI preferences: {selected}") from exc
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_BYTES:
        raise PreferencesError(f"TUI preferences must be a bounded regular file: {selected}")
    try:
        value = json.loads(selected.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreferencesError(f"TUI preferences are not valid UTF-8 JSON: {selected}") from exc
    if type(value) is not dict or set(value) != {"format", "schema_version", "theme", "show_clock", "record_id"}:
        raise PreferencesError("unsupported TUI preference fields")
    if value["format"] != FORMAT or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise PreferencesError("this Workbench TUI cannot read the saved preference schema")
    theme = value["theme"]
    clock = value["show_clock"]
    if type(theme) is not str or _THEME_NAME.fullmatch(theme) is None or type(clock) is not bool:
        raise PreferencesError("TUI preferences contain invalid appearance choices")
    if value["record_id"] != _record_id(theme, clock):
        raise PreferencesError("TUI preference identity does not match the saved choices")
    return TuiPreferences(theme, clock, value["record_id"])


@contextmanager
def _preference_lock(path: Path):
    """Serialize readers that compare and replace a TUI preference record."""
    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise PreferencesError(f"cannot open TUI preference lock: {lock_path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PreferencesError(f"TUI preference lock is not a regular file: {lock_path}")
        if os.name == "nt":  # pragma: no cover - exercised on native Windows
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
        raise PreferencesError(f"cannot lock TUI preferences: {lock_path}") from exc
    finally:
        os.close(descriptor)


def save_preferences(
    preferences: TuiPreferences,
    path: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> TuiPreferences:
    """Atomically save after checking that another instance has not changed it."""
    if _THEME_NAME.fullmatch(preferences.theme) is None or type(preferences.show_clock) is not bool:
        raise PreferencesError("TUI preferences contain invalid appearance choices")
    selected = preferences_path(environment=environment) if path is None else path
    try:
        for ancestor in (selected.parent, *selected.parent.parents):
            if ancestor.is_symlink():
                raise PreferencesError(f"TUI preference directory is a symlink: {ancestor}")
        selected.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _preference_lock(selected):
            current = load_preferences(selected)
            if current.record_id != preferences.record_id:
                raise PreferencesError("TUI preferences changed in another session")
            result = replace(preferences, record_id=_record_id(preferences.theme, preferences.show_clock))
            value = {
                "format": FORMAT,
                "schema_version": 1,
                "theme": result.theme,
                "show_clock": result.show_clock,
                "record_id": result.record_id,
            }
            temporary = selected.with_name(f".{selected.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    if os.name != "nt":
                        os.fchmod(stream.fileno(), 0o600)
                    json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, selected)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise PreferencesError(f"cannot save TUI preferences: {selected}") from exc
    return result
