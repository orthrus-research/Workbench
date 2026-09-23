"""Locate package-owned read-only resources without modifying import paths."""

from importlib import metadata
from pathlib import Path

from .modules import ModuleError


def repository_root(source_file: str) -> Path:
    """Resource layout for source development or installed module distributions.

    Each wheel owns only its subtree below the shared namespace. The namespace
    is read-only input, never a workspace or cleanup root.
    """
    for parent in Path(source_file).resolve().parents:
        relative = Path(source_file).resolve().relative_to(parent)
        source_owned = relative.parts[0] in {"modules", "profiles", "api", "core", "tools"}
        if source_owned and (parent / "workbench.toml").is_file() and (parent / "modules").is_dir():
            return parent
    for distribution in metadata.distributions():
        files = distribution.files or ()
        if any(str(path).startswith("workbench_resources/") for path in files):
            root = Path(distribution.locate_file("workbench_resources")).resolve()
            if root.is_dir():
                return root
    raise ModuleError("this operation requires its installed resource package or an explicit source workspace")


def module_root(source_file: str, module: str) -> Path:
    for parent in Path(source_file).resolve().parents:
        if parent.name == module and (parent / "src").is_dir():
            return parent
    return repository_root(source_file) / "modules" / module
