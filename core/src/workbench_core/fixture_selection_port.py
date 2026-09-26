"""Core host for per-user recipe fixture locations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .fixture_selection import register_recipe_fixture, resolve_recipe_fixture
from .user_config_home import default_user_config_home, default_user_record_path


class CoreFixtureSelections:
    def __init__(self, *, configuration_home: Path):
        home = Path(configuration_home)
        if not home.is_absolute():
            raise ValueError("fixture selections require an absolute Core configuration home")
        self.configuration_home = home

    def _record(self, name: str) -> Path:
        # Keep the legacy-import guard for the ordinary per-user home. Explicit
        # Core test or configured homes use their own stable records.
        if self.configuration_home == default_user_config_home():
            return default_user_record_path(name)
        return self.configuration_home / name

    def register(
        self, profile: str, workspace: Path | str, runtime: Path | str,
        java_home: Path | str,
    ) -> dict[str, Any]:
        return register_recipe_fixture(
            profile, workspace, runtime, java_home,
            path=self._record("recipe-fixtures-v1.json"),
        )

    def resolve(
        self, profile: str, workspace: Path | str | None = None, *,
        runtime: Path | str | None = None,
        java_home: Path | str | None = None,
    ) -> dict[str, Any]:
        if workspace is not None and runtime is not None and java_home is not None:
            # An exact operation override can recover from a damaged or
            # unmigrated user registry without reading either record.
            return resolve_recipe_fixture(
                profile, workspace, runtime=runtime, java_home=java_home,
            )
        return resolve_recipe_fixture(
            profile, workspace, runtime=runtime, java_home=java_home,
            registry_path=self._record("recipe-fixtures-v1.json"),
            setup_path=self._record("setup-v1.json"),
        )


__all__ = ["CoreFixtureSelections"]
