"""Expose native source manifests through Python's distribution discovery API.

This opt-in development loader uses the same metadata and entry points as
installed wheels. It does not contain a product command or module registry.
"""
from __future__ import annotations

from importlib import metadata
from pathlib import Path
import sys
import tomllib

from packaging.utils import canonicalize_name


class SourceDistribution(metadata.Distribution):
    def __init__(self, manifest: Path) -> None:
        self.manifest = manifest
        self.project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]

    def read_text(self, filename: str) -> str | None:
        if filename in {"METADATA", "PKG-INFO"}:
            fields = ["Metadata-Version: 2.1", f"Name: {self.project['name']}", f"Version: {self.project['version']}"]
            fields.extend(f"Requires-Dist: {value}" for value in self.project.get("dependencies", ()))
            return "\n".join(fields) + "\n"
        if filename == "entry_points.txt":
            return "\n".join(
                f"[{group}]\n" + "\n".join(f"{name} = {handler}" for name, handler in entries.items())
                for group, entries in self.project.get("entry-points", {}).items()
            )
        return None

    def locate_file(self, path):
        return self.manifest.parent / path


class SourceFinder(metadata.DistributionFinder):
    def __init__(self, root: Path, distributions: tuple[SourceDistribution, ...]) -> None:
        self.root = root
        self.distributions = distributions

    def find_spec(self, fullname, path=None, target=None):
        return None

    def find_distributions(self, context=metadata.DistributionFinder.Context()):
        return (
            distribution for distribution in self.distributions
            if context.name is None
            or canonicalize_name(context.name) == canonicalize_name(distribution.project["name"])
        )


def enable_source_checkout(root: Path) -> None:
    root = root.resolve()
    if any(isinstance(finder, SourceFinder) and finder.root == root for finder in sys.meta_path):
        return
    manifests = [root / "api/pyproject.toml", root / "core/pyproject.toml"]
    for pattern in ("modules/*/pyproject.toml", "profiles/*/*/pyproject.toml"):
        manifests.extend(sorted(root.glob(pattern)))
    distributions = tuple(SourceDistribution(path) for path in manifests)
    for distribution in distributions:
        source = str(distribution.manifest.parent / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
    sys.meta_path.insert(0, SourceFinder(root, distributions))
