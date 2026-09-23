"""Small, dependency-free helpers for accessible terminal status labels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
import shlex
import subprocess
import sys
from typing import Literal, TextIO


Tone = Literal["good", "attention", "blocked"]

_ANSI_BY_TONE = {
    "good": "\x1b[32m",
    "attention": "\x1b[33m",
    "blocked": "\x1b[31m",
}
_ANSI_RESET = "\x1b[0m"


def _supports_color(
    stream: TextIO,
    *,
    environment: Mapping[str, str],
) -> bool:
    """Return whether ANSI color is safe and permitted for this stream."""

    if "NO_COLOR" in environment or environment.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class HumanPresentation:
    """Render fixed status words with optional semantic color."""

    color: bool

    def label(self, text: str, tone: Tone) -> str:
        visible = f"[{text.upper()}]"
        if not self.color:
            return visible
        return f"{_ANSI_BY_TONE[tone]}{visible}{_ANSI_RESET}"


def human_presentation(
    stream: TextIO | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> HumanPresentation:
    """Build presentation policy from the actual destination and environment."""

    destination = sys.stdout if stream is None else stream
    values = os.environ if environment is None else environment
    return HumanPresentation(
        color=_supports_color(destination, environment=values),
    )


def human_command(
    argv: Sequence[object],
    *,
    platform_name: str | None = None,
) -> str:
    """Render argv in the quoting syntax of the current command shell family."""

    arguments = [str(item) for item in argv]
    selected_platform = os.name if platform_name is None else platform_name
    if selected_platform == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


__all__ = [
    "HumanPresentation",
    "Tone",
    "human_command",
    "human_presentation",
]
