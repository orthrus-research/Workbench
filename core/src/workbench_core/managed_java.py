"""Bind Core's exact JDK acquisition to one resolved operation selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workbench_api.modules import EnvironmentSelection

from .runtime_java import JavaRuntimeError, ensure_java_runtime, host_platform, probe_java


class CoreManagedJava:
    def __init__(self, *, state_root: Path, selection: EnvironmentSelection):
        self.state_root = state_root
        self.selection = selection

    def ensure(self, suite_root: Path, *, config_path: Path) -> dict[str, Any]:
        candidate = self.selection.java_home
        feature = self.selection.managed_java_feature
        if candidate is not None and feature is not None:
            raise JavaRuntimeError("choose a Java path or managed Java release")
        if candidate is not None and not candidate.is_absolute():
            raise JavaRuntimeError("selected Java home must be an absolute local path")
        if candidate is not None:
            # User paths are intentionally passed through without inventory,
            # version/vendor probing, or profile compatibility admission.
            return {
                "format": "workbench-java-runtime-result-v3",
                "schema_version": 3,
                "outcome": "selected",
                "source": "user-path",
                "runtime": {
                    "origin": self.selection.java_source,
                    "java_home_uri": candidate.as_uri(),
                    "state": "unverified",
                },
                "limitations": [
                    "User-supplied Java was not inspected or verified; the consuming operation may fail."
                ],
            }
        return ensure_java_runtime(
            suite_root,
            config_path=config_path,
            state_root=self.state_root,
            candidates=(),
            managed_feature_version=feature,
        )

    def for_execution(self, suite_root: Path, *, config_path: Path) -> dict[str, Any]:
        """Resolve only the selected Java when an operation actually needs it."""

        choice = self.ensure(suite_root, config_path=config_path)
        if choice["source"] != "user-path":
            return choice
        host = host_platform()
        home = self.selection.java_home
        assert home is not None
        executable = home / "bin" / ("java.exe" if host["os"] == "windows" else "java")
        probe = probe_java(executable)
        return {
            **choice,
            "outcome": "observed",
            "host": host,
            "runtime": {
                "origin": self.selection.java_source,
                "java_home_uri": home.as_uri(),
                "java_uri": executable.as_uri(),
                "state": "observed",
                "probe": probe,
            },
        }


__all__ = ["CoreManagedJava"]
