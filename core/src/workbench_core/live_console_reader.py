"""Core-hosted exact lookup for retained live-console session readers."""

from __future__ import annotations

from pathlib import Path

from workbench_api.sessions import SessionError

from .sessions import _exact_session_directory, _read_exact_manifest


class CoreLiveConsoleReader:
    def resolve(self, workspace: Path, session_id: str) -> Path:
        selected = Path(workspace).expanduser()
        if not selected.is_absolute():
            raise SessionError("selected session workspace must be absolute")
        directory = _exact_session_directory(selected, session_id)
        _read_exact_manifest(directory)
        return directory


HOST = CoreLiveConsoleReader()


__all__ = ["CoreLiveConsoleReader", "HOST"]
