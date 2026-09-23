"""Explicit workspace selection for Shell commands."""
from pathlib import Path
import os

def _configured_workspace_default(fallback: Path) -> Path:
    configured = os.environ.get("WORKBENCH_WORKSPACE")
    return Path(configured).expanduser() if configured else fallback
