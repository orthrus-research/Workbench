"""Strict source-checkout identity for the public Workbench Core component."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib


CORE_COMPONENT_ID = "workbench-core"
COMPONENT_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:(?:a|b|rc)[1-9][0-9]*)?"
)


class CoreComponentIdentityError(ValueError):
    """The source checkout cannot prove its public Core identity."""


def load_core_component_identity(repository_root: Path) -> tuple[str, str]:
    """Read the Core owner's exact source identity from ``core/pyproject.toml``.

    Runtime package versions come from installed distribution metadata. This
    function is only for source-bound registry records and never treats the
    repository tooling manifest as a distribution authority.
    """

    if (
        not isinstance(repository_root, Path)
        or not repository_root.is_absolute()
        or not repository_root.is_dir()
        or repository_root.is_symlink()
    ):
        raise CoreComponentIdentityError(
            "Core identity requires an absolute ordinary repository root"
        )
    root = repository_root.resolve()
    source = root / "core/pyproject.toml"
    if (
        not source.is_file()
        or source.is_symlink()
        or source.parent.is_symlink()
        or not source.resolve().is_relative_to(root)
    ):
        raise CoreComponentIdentityError(
            "Core identity requires an ordinary core/pyproject.toml"
        )
    try:
        value = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CoreComponentIdentityError(
            "Core identity core/pyproject.toml is unreadable or invalid"
        ) from exc
    project = value.get("project") if type(value) is dict else None
    name = project.get("name") if type(project) is dict else None
    version = project.get("version") if type(project) is dict else None
    if (
        name != CORE_COMPONENT_ID
        or type(version) is not str
        or COMPONENT_VERSION.fullmatch(version) is None
    ):
        raise CoreComponentIdentityError(
            "Core identity core/pyproject.toml has an unsupported name or version"
        )
    return name, version


__all__ = [
    "CORE_COMPONENT_ID",
    "CoreComponentIdentityError",
    "load_core_component_identity",
]
