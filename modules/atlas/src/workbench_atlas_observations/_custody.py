"""File witnesses for a process-local verified view; these are not write locks."""

from __future__ import annotations

from pathlib import Path
import stat

from workbench_atlas_categorical_graph.bundle import AtlasCategoricalGraphError, _load_manifest


class GraphWitness:
    """Detect path replacement and ordinary mutation without rereading graph bytes."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if ".." in self.root.parts:
            raise AtlasCategoricalGraphError("observation graph path cannot contain parent traversal")
        self._directories: dict[Path, tuple[int, ...]] = {}
        self._files: dict[Path, tuple[int, ...]] = {}
        self._add_file(self.root / "manifest.json")
        self.manifest = _load_manifest(self.root)
        for partition in self.manifest["partitions"]:
            for kind in ("nodes", "edges"):
                self._add_file(self.root / partition[kind]["file"])
        descriptor = self.manifest["query_index"]
        if descriptor is None:
            raise AtlasCategoricalGraphError("categorical graph query index is missing; explicit rebuild is required")
        try:
            self._add_file(self.root / descriptor["file"])
        except AtlasCategoricalGraphError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                raise AtlasCategoricalGraphError(
                    "categorical graph query index is missing; explicit rebuild is required"
                ) from error
            raise
        self.check()

    @staticmethod
    def _directory(path: Path) -> tuple[int, ...]:
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode):
            raise AtlasCategoricalGraphError("observation graph cannot traverse a symlink or non-directory")
        # Sibling activity changes directory timestamps, but not its identity.
        return value.st_dev, value.st_ino, value.st_mode

    @staticmethod
    def _file(path: Path) -> tuple[int, ...]:
        value = path.lstat()
        if not stat.S_ISREG(value.st_mode):
            raise AtlasCategoricalGraphError("observation graph requires ordinary files without symlinks")
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)

    def _add_file(self, path: Path) -> None:
        try:
            for parent in reversed(path.parents):
                if parent not in self._directories:
                    self._directories[parent] = self._directory(parent)
            self._files[path] = self._file(path)
        except OSError as error:
            raise AtlasCategoricalGraphError("observation graph file is missing or unavailable") from error

    def check(self) -> None:
        try:
            for path, expected in self._directories.items():
                if self._directory(path) != expected:
                    raise AtlasCategoricalGraphError("observation graph directory changed")
            for path, expected in self._files.items():
                if self._file(path) != expected:
                    raise AtlasCategoricalGraphError("observation graph file changed")
        except (OSError, AtlasCategoricalGraphError) as error:
            raise AtlasCategoricalGraphError(
                "observation graph changed or became unavailable; reopen and verify it"
            ) from error
