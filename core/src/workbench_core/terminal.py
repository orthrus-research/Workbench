"""Deterministic terminal setup and interruption handling."""
from __future__ import annotations
import errno
import os
import sys

def _configure_utf8_terminal_streams() -> None:
    """Make the CLI transport deterministic when Windows redirects output.

    Python otherwise selects the active Windows ANSI code page for a pipe.  The
    Workbench human and JSON surfaces are UTF-8 contracts and legitimately use
    non-ASCII source names and review notation, so a redirected invocation must
    not depend on the caller's locale.  In-memory streams used by tests and
    embedding callers intentionally remain untouched.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            # A caller may supply an already-detached or otherwise immutable
            # stream.  Let the owning transport retain responsibility for it.
            continue

def _silence_broken_stdout() -> None:
    """Prevent interpreter shutdown from reporting the same closed pipe."""

    try:
        stdout_descriptor = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return
    try:
        replacement = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(replacement, stdout_descriptor)
    except OSError:
        pass
    finally:
        os.close(replacement)
