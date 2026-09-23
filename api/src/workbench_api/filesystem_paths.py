"""Portable path identities with Windows extended-length access at the IO edge."""

import os
from pathlib import Path, PureWindowsPath


def _windows_access_path(value: str) -> str:
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("filesystem inputs must use ordinary paths, not device namespaces")
    parsed = PureWindowsPath(value)
    if not parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError("native Windows access requires an absolute normalized path")
    if len(value) < 240:
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def native_path(path: Path) -> Path:
    """Spell an ordinary absolute path for local filesystem calls only."""
    selected = Path(path)
    if os.name != "nt":
        return selected
    return Path(_windows_access_path(os.path.abspath(selected)))


def resolved_path(path: Path, *, strict: bool = False) -> Path:
    """Resolve through native IO; return an ordinary identity for provenance."""
    resolved = native_path(path).resolve(strict=strict)
    if os.name != "nt":
        return resolved
    value = str(resolved)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)
