"""Fail closed when Pixi can merge project-local configuration.

Pixi 0.75.0 documents exactly one project-local configuration path:
``<project>/.pixi/config.toml``.  ``--no-config`` disables system and user
configuration, but deliberately continues to load that project-local file.
Exact Workbench setup and package operations therefore require it to be absent.
"""

from __future__ import annotations

from pathlib import Path
import stat


PROJECT_LOCAL_CONFIG_PATHS = (Path(".pixi/config.toml"),)


class PixiProjectConfigError(RuntimeError):
    """Pixi project-local configuration is present or cannot be inspected."""


def require_project_local_config_absent(project_root: Path) -> None:
    """Require every Pixi-supported project-local configuration path to be absent.

    ``lstat`` is intentional: an existing broken symlink must be rejected rather
    than treated as an absent file.  Every existing filesystem type is rejected;
    Workbench never deletes or broad-scans project-local Pixi state.
    """

    root = Path(project_root)
    for relative in PROJECT_LOCAL_CONFIG_PATHS:
        candidate = root / relative
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise PixiProjectConfigError(
                f"Pixi project-local configuration cannot be inspected: {candidate}"
            ) from error

        if stat.S_ISLNK(info.st_mode):
            kind = "symbolic link"
        elif stat.S_ISREG(info.st_mode):
            kind = "regular file"
        else:
            kind = "non-regular filesystem entry"
        raise PixiProjectConfigError(
            "exact Pixi materialization requires project-local configuration "
            f"to be absent; remove or relocate {candidate} ({kind})"
        )
