"""Exact source-location interchange contract; no parser or product imports.

Coordinates retain the established half-open UTF-8 byte / one-based UTF-16
editor representation. Portable path validation is shared with source producers.
"""

from __future__ import annotations
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath
import re
import unicodedata
from typing import Any

SOURCE_LOCATION_API_VERSION = 1
MAX_PORTABLE_PATH_BYTES = 4096
MAX_PORTABLE_COMPONENT_BYTES = 255
_WINDOWS_RESERVED = {
    "AUX", "CLOCK$", "CON", "NUL", "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class SourceLocationError(ValueError):
    """A source path, coordinate or byte binding violates the public contract."""


def portable_relative_path(
    value: Any, context: str, *, allow_directory_marker: bool = False
) -> PurePosixPath:
    """Admit one canonical path that has the same meaning on every host.

    Recipe Review persists POSIX logical paths but eventually joins them to a
    native candidate or materialized baseline. Rejecting native separators,
    drive-relative spellings, filesystem aliases, and non-portable components
    before that join prevents a profile or runConfig from changing meaning on
    Windows, macOS, or Linux.
    """

    if not isinstance(value, str) or not value:
        raise SourceLocationError(f"{context} must be a canonical portable relative path")
    rendered = value[:-1] if allow_directory_marker and value.endswith("/") else value
    try:
        raw = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise SourceLocationError(
            f"{context} must be a canonical portable relative path"
        ) from exc
    if (
        len(raw) > MAX_PORTABLE_PATH_BYTES
        or unicodedata.normalize("NFC", rendered) != rendered
        or "\x00" in rendered
        or "\\" in rendered
    ):
        raise SourceLocationError(f"{context} must be a canonical portable relative path")

    posix = PurePosixPath(rendered)
    windows = PureWindowsPath(rendered)
    parts = tuple(rendered.split("/"))
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(part in {"", ".", ".."} for part in parts)
        or rendered != "/".join(parts)
    ):
        raise SourceLocationError(f"{context} must be a canonical portable relative path")
    for part in parts:
        if not portable_path_component(part):
            raise SourceLocationError(f"{context} must be a canonical portable relative path")
    return posix


def portable_path_component(part: str) -> bool:
    reserved_stem = part.split(".", 1)[0].upper()
    return not (
        len(part.encode("utf-8", "strict")) > MAX_PORTABLE_COMPONENT_BYTES
        or part.casefold() == ".git"
        or part.endswith((" ", "."))
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        or any(character in '<>:"|?*' for character in part)
        or reserved_stem in _WINDOWS_RESERVED
    )



def _path(value):
    return portable_relative_path(value, "source location")


def validate_location_shape(location):
    if (
        not isinstance(location, dict)
        or set(location)
        != {
            "path",
            "sha256",
            "byte_start",
            "byte_end",
            "start",
            "end",
            "coordinate_system",
            "interval",
        }
        or location["coordinate_system"] != "one-based-utf16"
        or location["interval"] != "half-open"
        or not isinstance(location["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", location["sha256"]) is None
        or type(location["byte_start"]) is not int
        or type(location["byte_end"]) is not int
        or not 0 <= location["byte_start"] <= location["byte_end"] <= 64 * 1024 * 1024
    ):
        raise SourceLocationError("invalid source location contract")
    _path(location["path"])
    for key in ("start", "end"):
        point = location[key]
        if (
            not isinstance(point, dict)
            or set(point) != {"line", "column"}
            or any(type(value) is not int or value < 1 for value in point.values())
        ):
            raise SourceLocationError("invalid editor position")
    if tuple(location["start"][key] for key in ("line", "column")) > tuple(
        location["end"][key] for key in ("line", "column")
    ):
        raise SourceLocationError("source location is reversed")
    if (location["byte_start"] == location["byte_end"]) != (location["start"] == location["end"]):
        raise SourceLocationError("point locations require matching byte and editor positions")
    return location


def source_location(raw: bytes, path: str, start: int, end: int) -> dict[str, Any]:
    """Half-open bytes and UTF-16 coordinates, including insertion/EOF points."""
    _path(path)
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start <= end <= len(raw)
    ):
        raise SourceLocationError("source location is outside its exact file bytes")
    try:
        prefix = raw[:start].decode("utf-8")
        selected = raw[start:end].decode("utf-8")
    except UnicodeError as exc:
        raise SourceLocationError("source location splits a UTF-8 code point") from exc

    def position(text):
        return {
            "line": text.count("\n") + 1,
            "column": len(text.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2 + 1,
        }

    return {
        "path": path,
        "sha256": sha256(raw).hexdigest(),
        "byte_start": start,
        "byte_end": end,
        "start": position(prefix),
        "end": position(prefix + selected),
        "coordinate_system": "one-based-utf16",
        "interval": "half-open",
    }


def character_location(raw: bytes, path: str, start: int, end: int) -> dict[str, Any]:
    text = raw.decode("utf-8")
    if not 0 <= start < end <= len(text):
        raise SourceLocationError("invalid character span")
    return source_location(
        raw, path, len(text[:start].encode()), len(text[:end].encode())
    )


def verify_source_location(raw: bytes, location: dict[str, Any]) -> dict[str, Any]:
    validate_location_shape(location)
    current = source_location(
        raw, location["path"], location["byte_start"], location["byte_end"]
    )
    if current != location:
        raise SourceLocationError("selected source bytes or location changed")
    return current
